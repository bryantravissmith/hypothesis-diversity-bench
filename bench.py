# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Freeze, run and export a hypothesis-diversity round.

    uv run bench.py plan runs/round-001
    uv run bench.py run runs/round-001
    uv run bench.py status runs/round-001
    uv run bench.py export runs/round-001 results/round-001

Planning freezes the seed, every case prompt, the conditions, repeat counts,
CLI versions, source hashes and a shuffled schedule before any inference.
Every physical attempt is recorded. Operational failures are retried with a
fresh session up to the frozen bound; completed invalid responses are terminal
and never replaced.
"""

import argparse
import contextlib
import hashlib
import json
import os
import random
import secrets
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import cases as case_builder

ROOT = Path(__file__).resolve().parent
SOURCES = ("bench.py", "cases.py", "scenarios.json", "conditions.json")
SCHEMA = "hypothesis-diversity-plan-v1"
DEFAULT_REPEATS = {"none": 20, "partial": 5, "full": 2}
TERMINAL = {"valid", "invalid_response", "protocol_error"}
RETRYABLE = {"error", "timeout", "rate_limit"}
FATAL_MARKERS = ("authentication", "unauthorized", "invalid api key", "insufficient credits", "401", "402")
# Variables that would route a CLI away from the user's own stored login or
# override the frozen model and effort settings.
STRIP_ENV = {
    "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL",
    "ANTHROPIC_CUSTOM_HEADERS", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_EFFORT_LEVEL", "MAX_THINKING_TOKENS", "CLAUDE_CODE_EXTRA_BODY",
    "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT", "CLAUDE_CODE_SAFE_MODE",
    "CLAUDE_CODE_AGENT_ID", "CLAUDE_CODE_PARENT_SESSION_ID", "CLAUDE_AGENT_SDK_VERSION",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, obj):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def read_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


def extract_answer(text):
    """Return (answer, strict) where answer is the first JSON object with a
    non-empty string "hypothesis", and strict says the whole text was that
    object with nothing around it."""
    if not isinstance(text, str):
        return None, False
    stripped = text.strip()
    decoder = json.JSONDecoder()
    for start in [i for i, ch in enumerate(stripped) if ch == "{"]:
        try:
            obj, end = decoder.raw_decode(stripped, start)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("hypothesis"), str) and obj["hypothesis"].strip():
            return obj, start == 0 and end == len(stripped)
    return None, False


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------


def prompt_for(condition, case):
    """The case prompt, after the condition's frozen prefix when it has one."""
    prefix = condition.get("prompt_prefix")
    return f"{prefix}\n\n{case['prompt']}" if prefix else case["prompt"]


def argv_for(condition, system_prompt, prompt):
    if condition["runner"] == "claude":
        return [
            "claude", "--print", "--safe-mode", "--tools", "", "--mcp-config", '{"mcpServers":{}}',
            "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence",
            "--permission-mode", "dontAsk", "--disable-slash-commands", "--no-chrome",
            "--max-turns", "1", "--output-format", "stream-json", "--verbose",
            "--model", condition["model"], "--effort", condition["thinking"],
            "--system-prompt", system_prompt, "--", prompt,
        ]
    if condition["runner"] == "pi":
        return [
            "pi", "--offline", "--print", "--mode", "json", "--provider", condition["provider"],
            "--model", condition["model"], "--thinking", condition["thinking"], "--no-tools",
            "--no-mcp", "--no-extensions", "--no-skills", "--no-prompt-templates",
            "--no-themes", "--no-context-files", "--no-approve", "--no-session",
            "--system-prompt", system_prompt, "--", prompt,
        ]
    raise ValueError(f"Unknown runner {condition['runner']!r}")


def _events(stdout):
    out = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            out.append(event)
    return out


def parse_claude(events, condition):
    r = dict(text=None, observed_model=None, reasoning_tokens=None, output_tokens=None, cost_usd=None, problem=None)
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    r["observed_model"] = init.get("model")
    if init.get("tools"):
        r["problem"] = ("protocol_error", "tools were available")
        return r
    for e in events:
        if e.get("type") == "rate_limit_event":
            status = (e.get("rate_limit_info") or {}).get("status")
            if status not in ("allowed", "allowed_warning"):
                r["problem"] = ("rate_limit", f"subscription limit {status}")
                return r
        if e.get("type") == "system" and e.get("subtype") == "api_retry":
            r["problem"] = ("error", f"CLI internal retry {e.get('error_status')}")
            return r
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if result is None:
        r["problem"] = ("error", "no result event")
        return r
    usage = result.get("usage") or {}
    r["output_tokens"] = usage.get("output_tokens")
    r["reasoning_tokens"] = (usage.get("output_tokens_details") or {}).get("thinking_tokens")
    r["cost_usd"] = result.get("total_cost_usd")
    if result.get("is_error") or result.get("subtype") != "success":
        r["problem"] = ("error", f"result {result.get('subtype')}: {str(result.get('result'))[:200]}")
        return r
    if r["observed_model"] != condition["model"]:
        r["problem"] = ("protocol_error", f"model {r['observed_model']!r} != {condition['model']!r}")
        return r
    r["text"] = result.get("result")
    return r


def parse_pi(events, condition):
    r = dict(text=None, observed_model=None, reasoning_tokens=None, output_tokens=None, cost_usd=None, problem=None)
    final = None
    for e in events:
        message = e.get("message") or {}
        if e.get("type") == "message_end" and message.get("role") == "assistant":
            final = message
    if final is None:
        r["problem"] = ("error", "no assistant message")
        return r
    usage = final.get("usage") or {}
    r["observed_model"] = final.get("model")
    r["reasoning_tokens"] = usage.get("reasoning")
    r["output_tokens"] = usage.get("output")
    r["cost_usd"] = (usage.get("cost") or {}).get("total")
    stop = final.get("stopReason")
    if stop == "error" or final.get("errorMessage"):
        message = str(final.get("errorMessage", ""))[:200]
        kind = "rate_limit" if "429" in message or "rate" in message.lower() else "error"
        r["problem"] = (kind, f"provider error: {message}")
        return r
    if r["observed_model"] != condition["model"]:
        r["problem"] = ("protocol_error", f"model {r['observed_model']!r} != {condition['model']!r}")
        return r
    r["text"] = "".join(c.get("text", "") for c in final.get("content") or [] if c.get("type") == "text")
    if stop == "length":
        r["problem"] = ("invalid_response", "truncated at output limit")
    return r


def classify(parsed):
    if parsed["problem"]:
        return parsed["problem"]
    answer, _ = extract_answer(parsed["text"])
    if answer is None:
        return ("invalid_response", "no JSON object with a hypothesis")
    return ("valid", None)


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def _version(binary):
    try:
        done = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        return (done.stdout or done.stderr).strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return None


def plan(args):
    out = Path(args.out)
    if out.exists():
        raise SystemExit(f"{out} exists; choose a new run ID")
    repeats = dict(DEFAULT_REPEATS)
    for item in args.repeats or []:
        tier, count = item.split("=")
        if tier not in repeats or int(count) < 1:
            raise SystemExit(f"Bad --repeats {item!r}")
        repeats[tier] = int(count)
    spec = read_json(ROOT / "conditions.json")
    conditions = [c for c in spec["conditions"] if not args.conditions or c["id"] in args.conditions]
    if args.conditions and len(conditions) != len(set(args.conditions)):
        raise SystemExit("Unknown condition in --conditions")
    seed = args.seed or secrets.token_hex(8)
    built = case_builder.build(seed, tiers=args.tiers, none_variants=args.none_variants)
    if args.cases:
        built = [c for c in built if c["id"] in args.cases]
    jobs = [
        dict(case=c["id"], condition=cond["id"], trial=t)
        for c in built
        for cond in conditions
        for t in range(1, repeats[c["tier"]] + 1)
    ]
    random.Random(seed).shuffle(jobs)
    for i, job in enumerate(jobs, 1):
        job["job"] = f"{i:05d}"
    runners = sorted({c["runner"] for c in conditions})
    out.mkdir(parents=True)
    snapshot = out / "source"
    snapshot.mkdir()
    for name in SOURCES:
        shutil.copy2(ROOT / name, snapshot / name)
    write_json(
        out / "plan.json",
        dict(
            schema=SCHEMA,
            created_at=now(),
            seed=seed,
            repeats=repeats,
            case_options=dict(tiers=list(args.tiers), none_variants=args.none_variants),
            system_prompt=spec["system_prompt"],
            conditions=conditions,
            pools={k: v for k, v in spec["pools"].items() if any(c["pool"] == k for c in conditions)},
            max_attempts=args.max_attempts,
            timeout_seconds=args.timeout,
            max_hours=args.max_hours,
            cli_versions={r: _version(r) for r in runners},
            source_sha256={name: sha256_file(ROOT / name) for name in SOURCES},
            cases=built,
            schedule=jobs,
        ),
    )
    print(f"Froze {len(jobs)} jobs over {len(built)} cases and {len(conditions)} conditions (seed {seed}); no inference yet")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


class Round:
    def __init__(self, out, inference=False):
        self.out = Path(out)
        self.plan = read_json(self.out / "plan.json")
        # Only inference must run the frozen source. Status and export read
        # recorded evidence, which the frozen snapshot already describes.
        if inference:
            for name, digest in self.plan["source_sha256"].items():
                if sha256_file(ROOT / name) != digest:
                    raise SystemExit(
                        f"{name} changed since planning; resume with {self.out / 'source' / 'bench.py'}"
                    )
        self.cases = {c["id"]: c for c in self.plan["cases"]}
        self.conditions = {c["id"]: c for c in self.plan["conditions"]}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.attempts_path = self.out / "attempts.jsonl"

    def history(self):
        by_job = {}
        for row in read_jsonl(self.attempts_path):
            by_job.setdefault(row["job"], []).append(row)
        return by_job

    def done(self, rows):
        return any(r["outcome"] in TERMINAL for r in rows) or len(rows) >= self.plan["max_attempts"]

    def record(self, row):
        with self.lock:
            with open(self.attempts_path, "a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def attempt(self, job, number):
        case = self.cases[job["case"]]
        condition = self.conditions[job["condition"]]
        folder = self.out / "raw" / job["job"] / f"{number:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        prompt = prompt_for(condition, case)
        argv = argv_for(condition, self.plan["system_prompt"], prompt)
        env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
        env.update(DISABLE_TELEMETRY="1", NO_COLOR="1")
        started = now()
        clock = time.monotonic()
        timed_out = False
        try:
            done = subprocess.run(
                argv, cwd="/", env=env, stdin=subprocess.DEVNULL, capture_output=True,
                text=True, timeout=self.plan["timeout_seconds"],
            )
            stdout, stderr, code = done.stdout, done.stderr, done.returncode
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr, code = "timeout", None
        elapsed = round(time.monotonic() - clock, 3)
        (folder / "events.jsonl").write_text(stdout)
        (folder / "stderr.log").write_text(stderr or "")
        events = _events(stdout)
        parser = parse_claude if condition["runner"] == "claude" else parse_pi
        parsed = parser(events, condition)
        if timed_out:
            outcome, detail = "timeout", "process timeout"
        elif code != 0 and not parsed["problem"]:
            outcome, detail = "error", f"exit {code}"
        else:
            outcome, detail = classify(parsed)
        if outcome in RETRYABLE and any(m in (stderr or "").lower() + str(detail).lower() for m in FATAL_MARKERS):
            outcome = "fatal_error"
        answer, strict = extract_answer(parsed["text"])
        return dict(
            job=job["job"], case=job["case"], tier=case["tier"], truth=case["truth"],
            scenario=case["scenario"], condition=job["condition"], trial=job["trial"],
            attempt=number, outcome=outcome, detail=detail, text=parsed["text"],
            hypothesis=(answer or {}).get("hypothesis"), check=(answer or {}).get("check"),
            strict_json=strict, observed_model=parsed["observed_model"],
            reasoning_tokens=parsed["reasoning_tokens"], output_tokens=parsed["output_tokens"],
            reported_cost_usd=parsed["cost_usd"], started_at=started, finished_at=now(),
            elapsed_seconds=elapsed, returncode=code,
            raw_events_sha256=sha256_text(stdout), prompt_sha256=sha256_text(prompt),
        )

    def work(self, job, prior):
        number = len(prior)
        while not self.stop.is_set() and number < self.plan["max_attempts"]:
            if time.time() > self.deadline:
                self.stop.set()
                return
            number += 1
            row = self.attempt(job, number)
            self.record(row)
            print(f"{job['job']} {job['condition']:16} {job['case']:40} #{number} {row['outcome']}", flush=True)
            if row["outcome"] == "fatal_error":
                print(f"Fatal error, stopping: {row['detail']}", file=sys.stderr, flush=True)
                self.stop.set()
                return
            if row["outcome"] in TERMINAL:
                return
            base = float(os.environ.get("HDB_BACKOFF_SECONDS", 15))
            time.sleep(min(240, base * 2 ** (number - 1)) * random.uniform(0.8, 1.2))

    def run(self):
        meta_path = self.out / "run-state.json"
        state = read_json(meta_path) if meta_path.exists() else {"first_started_at": time.time()}
        write_json(meta_path, state)
        self.deadline = state["first_started_at"] + self.plan["max_hours"] * 3600
        history = self.history()
        pending = [j for j in self.plan["schedule"] if not self.done(history.get(j["job"], []))]
        print(f"{len(pending)} of {len(self.plan['schedule'])} jobs pending", flush=True)
        # One executor per service, sized to its concurrency limit, so a slow
        # service never holds workers that a fast one could use. Each service
        # still takes its jobs in frozen schedule order.
        with contextlib.ExitStack() as stack:
            executors = {
                name: stack.enter_context(ThreadPoolExecutor(size, thread_name_prefix=name))
                for name, size in self.plan["pools"].items()
            }
            futures = [
                executors[self.conditions[j["condition"]]["pool"]].submit(self.work, j, history.get(j["job"], []))
                for j in pending
            ]
            try:
                for f in futures:
                    f.result()
            except KeyboardInterrupt:
                self.stop.set()
                print("Interrupted; in-flight attempts finish, then the round pauses", file=sys.stderr)
                raise


def run(args):
    Round(args.out, inference=True).run()
    status(args)


def status(args):
    r = Round(args.out)
    history = r.history()
    table = {}
    for job in r.plan["schedule"]:
        rows = history.get(job["job"], [])
        key = (job["condition"], r.cases[job["case"]]["tier"])
        entry = table.setdefault(key, {"planned": 0, "valid": 0, "invalid": 0, "unfinished": 0, "attempts": 0})
        entry["planned"] += 1
        entry["attempts"] += len(rows)
        final = rows[-1]["outcome"] if rows else None
        if final == "valid":
            entry["valid"] += 1
        elif final in ("invalid_response", "protocol_error") or (rows and r.done(rows)):
            entry["invalid"] += 1
        else:
            entry["unfinished"] += 1
    print(f"{'condition':18} {'tier':8} planned valid invalid unfinished attempts")
    for (cond, tier), e in sorted(table.items()):
        print(f"{cond:18} {tier:8} {e['planned']:7} {e['valid']:5} {e['invalid']:7} {e['unfinished']:10} {e['attempts']:8}")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

PUBLIC_FIELDS = (
    "job", "case", "tier", "truth", "scenario", "condition", "trial", "attempt", "outcome",
    "detail", "text", "hypothesis", "check", "strict_json", "observed_model",
    "reasoning_tokens", "output_tokens", "reported_cost_usd", "started_at", "finished_at",
    "elapsed_seconds", "raw_events_sha256", "prompt_sha256",
)


def export(args):
    src, dest = Path(args.out), Path(args.dest)
    if dest.exists():
        raise SystemExit(f"{dest} exists; exports never overwrite")
    r = Round(src)
    history = r.history()
    rows = [row for job in r.plan["schedule"] for row in history.get(job["job"], [])]
    for row in rows:
        raw = (src / "raw" / row["job"] / f"{row['attempt']:02d}" / "events.jsonl").read_text()
        if sha256_text(raw) != row["raw_events_sha256"]:
            raise SystemExit(f"Raw evidence hash mismatch for job {row['job']} attempt {row['attempt']}")
    unfinished = sum(1 for j in r.plan["schedule"] if not r.done(history.get(j["job"], [])))
    tmp = dest.with_name(dest.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    public_plan = {k: v for k, v in r.plan.items()}
    public_plan["state"] = "complete" if unfinished == 0 else "stopped"
    public_plan["unfinished_jobs"] = unfinished
    write_json(tmp / "plan.json", public_plan)
    with open(tmp / "attempts.jsonl", "w") as f:
        for row in rows:
            f.write(json.dumps({k: row.get(k) for k in PUBLIC_FIELDS}, ensure_ascii=False) + "\n")
    shutil.copytree(src / "source", tmp / "source")
    os.replace(tmp, dest)
    print(f"Exported {len(rows)} attempts ({public_plan['state']}) to {dest}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("out")
    p.add_argument("--seed")
    p.add_argument("--repeats", nargs="*", help="tier=count, e.g. none=20 partial=5 full=2")
    p.add_argument("--conditions", nargs="*")
    p.add_argument("--cases", nargs="*", help="Restrict to these case IDs (smoke checks)")
    p.add_argument("--tiers", nargs="+", default=list(case_builder.TIERS), choices=case_builder.TIERS)
    p.add_argument("--none-variants", type=int, default=1, help="No-evidence cases per scenario: 1, or 4 Latin-square change orders")
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--max-hours", type=float, default=8)
    p.set_defaults(func=plan)
    for name, func in (("run", run), ("status", status)):
        p = sub.add_parser(name)
        p.add_argument("out")
        p.set_defaults(func=func)
    p = sub.add_parser("export")
    p.add_argument("out")
    p.add_argument("dest")
    p.set_defaults(func=export)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
