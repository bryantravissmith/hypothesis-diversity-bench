# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Label each hypothesis with the change it names.

    uv run grade.py review results/round-001     # blinded list of answers needing review
    uv run grade.py labels results/round-001     # write labels.jsonl

Labels: one of the cause types (release, mix, tracking, policy), "other" when
no listed change is named, "multiple" when several changes are named and a
reviewer has not resolved it, or "invalid" when no valid answer exists.

Automatic labels use each scenario's change patterns. Answers that match no
pattern or more than one go to a blinded review list that shows only the
scenario and the hypothesis text, never the model or condition. Reviewer
decisions live in reviews.json next to the results, keyed "job:attempt", each
with a label and a short reason.

Review rule: the label is the one listed change the answer presents as the
cause. Changes mentioned only to rule them out or as a contrast do not count.
An answer that names two or more changes as joint causes with no primary one
is "multiple"; one that names no listed change is "other". Both are misses.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LABELS = ("release", "mix", "tracking", "policy", "other", "multiple", "invalid")
REVIEW_LABELS = LABELS[:6]


def scenario_patterns(scenarios_path):
    data = json.loads(Path(scenarios_path).read_text())
    return {
        s["id"]: {cause: re.compile(spec["pattern"], re.I) for cause, spec in s["causes"].items()}
        for s in data["scenarios"]
    }


def auto_label(hypothesis, patterns):
    hits = [cause for cause, pattern in patterns.items() if pattern.search(hypothesis)]
    if len(hits) == 1:
        return hits[0], hits
    return ("other" if not hits else "multiple"), hits


def final_rows(results):
    """The last recorded attempt for every planned job, in schedule order."""
    plan = json.loads((results / "plan.json").read_text())
    by_job = {}
    for line in (results / "attempts.jsonl").read_text().splitlines():
        row = json.loads(line)
        by_job.setdefault(row["job"], []).append(row)
    return plan, [(job, by_job.get(job["job"], [])) for job in plan["schedule"]]


def load_reviews(results):
    path = results / "reviews.json"
    if not path.exists():
        return {}
    reviews = json.loads(path.read_text())
    for key, entry in reviews.items():
        if entry.get("label") not in REVIEW_LABELS or not entry.get("reason"):
            raise SystemExit(f"Review {key} needs a label among {REVIEW_LABELS} and a reason")
    return reviews


def labels(results):
    plan, jobs = final_rows(results)
    patterns = scenario_patterns(results / "source" / "scenarios.json")
    reviews = load_reviews(results)
    out = []
    for job, rows in jobs:
        final = rows[-1] if rows else None
        entry = dict(job=job["job"], case=job["case"], condition=job["condition"], trial=job["trial"])
        if not final or final["outcome"] != "valid":
            entry.update(label="invalid", source="outcome", outcome=final["outcome"] if final else "missing")
        else:
            key = f"{final['job']}:{final['attempt']}"
            label, hits = auto_label(final["hypothesis"], patterns[final["scenario"]])
            if key in reviews:
                entry.update(label=reviews[key]["label"], source="review", auto=label, hits=hits)
            else:
                entry.update(label=label, source="pattern", hits=hits)
            entry["key"] = key
        out.append(entry)
    unused = set(load_reviews(results)) - {e.get("key") for e in out}
    if unused:
        raise SystemExit(f"Reviews for unknown answers: {sorted(unused)}")
    return plan, out


def cmd_review(args):
    results = Path(args.results)
    _, rows = labels(results)
    plan, jobs = final_rows(results)
    finals = {job["job"]: rs[-1] for job, rs in jobs if rs}
    pending = [r for r in rows if r.get("source") == "pattern" and r["label"] in ("other", "multiple")]
    if args.all_unreviewed:
        pending = [r for r in rows if r.get("source") == "pattern"]
    pending.sort(key=lambda r: r["key"])
    scenarios = {s["id"]: s for s in json.loads((results / "source" / "scenarios.json").read_text())["scenarios"]}
    for r in pending:
        final = finals[r["job"]]
        changes = {c: scenarios[final["scenario"]]["causes"][c]["change"] for c in ("release", "mix", "tracking", "policy")}
        print(json.dumps(dict(key=r["key"], auto=r["label"], hits=r["hits"], scenario=final["scenario"],
                              hypothesis=final["hypothesis"], changes=changes), ensure_ascii=False))
    print(f"{len(pending)} answers need review", file=sys.stderr)


def cmd_labels(args):
    results = Path(args.results)
    _, rows = labels(results)
    unresolved = [r for r in rows if r["source"] == "pattern" and r["label"] == "multiple"]
    with open(results / "labels.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Wrote {len(rows)} labels; {len(unresolved)} unresolved 'multiple'")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("review")
    p.add_argument("results")
    p.add_argument("--all-unreviewed", action="store_true", help="List every pattern label, for spot checks")
    p.set_defaults(func=cmd_review)
    p = sub.add_parser("labels")
    p.add_argument("results")
    p.set_defaults(func=cmd_labels)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
