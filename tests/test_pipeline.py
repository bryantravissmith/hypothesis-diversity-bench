"""Answer parsing, pass@k and a full plan-run-export-grade-analyze cycle against fake CLIs."""

import contextlib
import io
import json
import math
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import analyze
import bench
import grade

FAKE_CLAUDE = r"""#!{python}
import json, sys
args = sys.argv[1:]
if "--version" in args:
    print("9.9.9 (Claude Code)"); sys.exit(0)
model = args[args.index("--model") + 1]
prompt = args[-1]
print(json.dumps({"type": "system", "subtype": "init", "model": model, "tools": [], "session_id": "s"}))
answer = {"hypothesis": "The tag manager migration broke order tracking.", "check": "Compare with the order database."}
if "online store" not in prompt:
    answer["hypothesis"] = "The new release introduced a bug."
text = json.dumps(answer)
print(json.dumps({"type": "assistant", "message": {"model": model, "content": [{"type": "text", "text": text}]}}))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text,
                  "usage": {"output_tokens": 40, "output_tokens_details": {"thinking_tokens": 0}}, "total_cost_usd": 0.01}))
"""

FAKE_PI = r"""#!{python}
import json, os, sys
args = sys.argv[1:]
if "--version" in args:
    print("1.3.0"); sys.exit(0)
model = args[args.index("--model") + 1]
prompt = args[-1]
state = os.environ["FAKE_STATE"]
if "online store" in prompt and not os.path.exists(state):
    open(state, "w").write("failed once")
    sys.exit(1)
if "software-as-a-service" in prompt:
    text = "Probably the price change."
else:
    text = "```json\n" + json.dumps({"hypothesis": "The paid social campaign brought low-intent traffic.", "check": "Split by source."}) + "\n```"
msg = {"role": "assistant", "content": [{"type": "text", "text": text}], "model": model, "stopReason": "stop",
       "usage": {"input": 10, "output": 20, "reasoning": 0, "cost": {"total": 0.001}}}
print(json.dumps({"type": "message_end", "message": msg}))
"""


class UnitTest(unittest.TestCase):
    def test_extract_answer(self):
        obj = {"hypothesis": "x", "check": "y"}
        self.assertEqual(bench.extract_answer(json.dumps(obj)), (obj, True))
        self.assertEqual(bench.extract_answer("```json\n" + json.dumps(obj) + "\n```"), (obj, False))
        self.assertEqual(bench.extract_answer('Sure: {"a": 1} then ' + json.dumps(obj))[0], obj)
        self.assertEqual(bench.extract_answer('{"hypothesis": ""}'), (None, False))
        self.assertEqual(bench.extract_answer(None), (None, False))

    def test_pi_uses_each_condition_provider(self):
        spec = json.loads((ROOT / "conditions.json").read_text())
        for cond in spec["conditions"]:
            argv = bench.argv_for(cond, "sys", "prompt")
            if cond["runner"] == "pi":
                self.assertEqual(argv[argv.index("--provider") + 1], cond["provider"])
                self.assertIn("--no-tools", argv)
            self.assertEqual(argv[argv.index("--model") + 1], cond["model"])
            self.assertEqual(argv[-1], "prompt")
            self.assertIn(cond["pool"], spec["pools"])
        gpt = [c for c in spec["conditions"] if c["model"].startswith("gpt-")]
        self.assertTrue(gpt and all(c["provider"] == "openai" for c in gpt))

    def test_pass_at_k(self):
        self.assertEqual(analyze.pass_at_k(20, 0, 10), 0.0)
        self.assertEqual(analyze.pass_at_k(20, 20, 1), 1.0)
        self.assertAlmostEqual(analyze.pass_at_k(20, 5, 1), 0.25)
        self.assertAlmostEqual(analyze.pass_at_k(20, 5, 3), 1 - math.comb(15, 3) / math.comb(20, 3))
        with self.assertRaises(ValueError):
            analyze.pass_at_k(5, 1, 6)

    def test_fixed_answer_scores_a_quarter_without_evidence(self):
        case = {"truth": None, "candidates": list(analyze.CAUSES)}
        m = analyze.case_metrics(case, ["tracking"] * 20, analyze.KS["none"])
        self.assertTrue(all(abs(v - 0.25) < 1e-12 for v in m["passk"].values()))
        self.assertEqual(m["effective"], 1.0)
        spread = analyze.case_metrics(case, ["release", "mix", "tracking", "policy"] * 5, analyze.KS["none"])
        self.assertAlmostEqual(spread["passk"][1], 0.25)
        self.assertAlmostEqual(spread["passk"][5], 1 - math.comb(15, 5) / math.comb(20, 5))
        self.assertAlmostEqual(spread["effective"], 4.0)

    def test_invalid_trials_stay_in_the_denominator(self):
        case = {"truth": "mix", "candidates": ["mix", "policy"]}
        m = analyze.case_metrics(case, ["mix", "invalid", "invalid", "invalid"], (1,))
        self.assertAlmostEqual(m["passk"][1], 0.25)

    def test_pi_errors_and_model_mismatch(self):
        cond = {"model": "openai/gpt-4o"}
        err = [{"type": "message_end", "message": {"role": "assistant", "stopReason": "error", "errorMessage": "429 Too Many Requests", "model": "openai/gpt-4o"}}]
        self.assertEqual(bench.parse_pi(err, cond)["problem"][0], "rate_limit")
        other = [{"type": "message_end", "message": {"role": "assistant", "stopReason": "stop", "model": "openai/gpt-4o-mini", "content": []}}]
        self.assertEqual(bench.parse_pi(other, cond)["problem"][0], "protocol_error")

    def test_claude_limit_and_errors(self):
        cond = {"model": "claude-opus-5-5"}
        init = {"type": "system", "subtype": "init", "model": "claude-opus-5-5", "tools": []}
        limited = [init, {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}}]
        self.assertEqual(bench.parse_claude(limited, cond)["problem"][0], "rate_limit")
        failed = [init, {"type": "result", "subtype": "error_during_execution", "is_error": True}]
        self.assertEqual(bench.parse_claude(failed, cond)["problem"][0], "error")
        tools = [dict(init, tools=["Bash"])]
        self.assertEqual(bench.parse_claude(tools, cond)["problem"][0], "protocol_error")


class PipelineTest(unittest.TestCase):
    def test_full_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bin_dir = tmp / "bin"
            bin_dir.mkdir()
            for name, body in (("claude", FAKE_CLAUDE), ("pi", FAKE_PI)):
                path = bin_dir / name
                path.write_text(body.replace("{python}", sys.executable))
                path.chmod(0o755)
            env = {
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                "FAKE_STATE": str(tmp / "state"),
                "HDB_BACKOFF_SECONDS": "0",
                "ANTHROPIC_BASE_URL": "http://should-be-stripped.invalid",
            }
            raw, public = tmp / "raw", tmp / "public"
            cases = ["ecommerce-checkout--none", "saas-paid-start--partial--mix", "ecommerce-checkout--full--tracking"]
            quiet = contextlib.redirect_stdout(io.StringIO())
            with unittest.mock.patch.dict(os.environ, env), quiet:
                bench.main(["plan", str(raw), "--seed", "s1", "--conditions", "opus-5.5-medium", "gpt-4o",
                            "--cases", *cases, "--repeats", "none=3", "partial=2", "full=1"])
                with self.assertRaises(SystemExit):
                    bench.main(["plan", str(raw)])
                bench.main(["run", str(raw)])
                bench.main(["run", str(raw)])  # resume is a no-op once complete
                bench.main(["export", str(raw), str(public)])
                with self.assertRaises(SystemExit):
                    bench.main(["export", str(raw), str(public)])

            plan = json.loads((public / "plan.json").read_text())
            self.assertEqual(plan["state"], "complete")
            self.assertEqual(len(plan["schedule"]), 2 * (3 + 2 + 1))
            rows = [json.loads(line) for line in (public / "attempts.jsonl").read_text().splitlines()]
            # One failed then retried attempt for the first GPT-4o call on the online store.
            self.assertEqual(sum(r["outcome"] == "error" for r in rows), 1)
            self.assertEqual(len(rows), 13)
            invalid = [r for r in rows if r["outcome"] == "invalid_response"]
            self.assertEqual(len(invalid), 2)
            self.assertTrue(all(r["condition"] == "gpt-4o" and r["scenario"] == "saas-paid-start" for r in invalid))
            self.assertTrue(all(r["observed_model"] for r in rows if r["outcome"] == "valid"))

            # Tampered raw evidence blocks export.
            first = rows[0]
            events = raw / "raw" / first["job"] / f"{first['attempt']:02d}" / "events.jsonl"
            events.write_text(events.read_text() + "\n")
            with self.assertRaises(SystemExit), quiet:
                bench.main(["export", str(raw), str(tmp / "public2")])

            _, labels = grade.labels(public)
            by = {(r["condition"], r["case"]): r["label"] for r in labels}
            self.assertEqual(by[("opus-5.5-medium", "ecommerce-checkout--none")], "tracking")
            self.assertEqual(by[("gpt-4o", "ecommerce-checkout--none")], "mix")
            self.assertEqual(by[("gpt-4o", "saas-paid-start--partial--mix")], "invalid")

            # Reviews must name a real answer and carry a reason.
            (public / "reviews.json").write_text(json.dumps({"99999:1": {"label": "mix", "reason": "x"}}))
            with self.assertRaises(SystemExit):
                grade.labels(public)
            (public / "reviews.json").write_text(json.dumps({f"{first['job']}:{first['attempt']}": {"label": "mix"}}))
            with self.assertRaises(SystemExit):
                grade.labels(public)
            (public / "reviews.json").unlink()

            with quiet:
                analyze.main([str(public), "--report", str(tmp / "RESULTS.md")])
            summary = json.loads((public / "summary.json").read_text())
            opus = next(c for c in summary["conditions"] if c["id"] == "opus-5.5-medium")
            self.assertAlmostEqual(opus["tiers"]["none"]["passk"]["1"]["mean"], 0.25)
            self.assertAlmostEqual(opus["tiers"]["full"]["passk"]["1"]["mean"], 1.0)
            report = (tmp / "RESULTS.md").read_text()
            self.assertIn("public/passk-none.svg", report)
            self.assertTrue((public / "passk-none.svg").read_text().startswith("<svg"))

            # A second round with the same seed is reported beside the first;
            # a round with different cases is refused.
            def round_with(name, seed):
                raw_n, public_n = tmp / f"raw-{name}", tmp / name
                with unittest.mock.patch.dict(os.environ, env), quiet:
                    bench.main(["plan", str(raw_n), "--seed", seed, "--conditions", "gpt-6.1-sol-max",
                                "--cases", *cases, "--repeats", "none=3", "partial=2", "full=1"])
                    bench.main(["run", str(raw_n)])
                    bench.main(["export", str(raw_n), str(public_n)])
                return public_n

            second = round_with("second", "s1")
            with quiet:
                analyze.main([str(public), str(second), "--figures", str(tmp / "figures"), "--report", str(tmp / "BOTH.md")])
            both = json.loads((tmp / "figures" / "summary.json").read_text())
            self.assertEqual([r["name"] for r in both["rounds"]], ["public", "second"])
            self.assertEqual({c["round"] for c in both["conditions"]}, {"public", "second"})
            self.assertIn("figures/passk-none.svg", (tmp / "BOTH.md").read_text())
            other = round_with("other", "s2")
            with self.assertRaises(SystemExit), quiet:
                analyze.main([str(public), str(other), "--figures", str(tmp / "figures2")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
