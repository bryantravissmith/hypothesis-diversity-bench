# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Diversity, accuracy and pass@k from labeled results. Generates the report.

    uv run analyze.py results/round-001 --report RESULTS.md

Writes summary.json and passk.svg into the results directory and, with
--report, a generated Markdown report. Never edit those outputs by hand.

Every planned trial counts. A trial without a valid answer is a miss, not a
smaller denominator.

- none tier: the true cause is not identifiable, so the prompt is the same for
  every truth. pass@k averages over the four equally likely true causes.
- partial and full tiers: pass@k is against the case's true cause.

pass@k uses the unbiased estimator 1 - C(n-c, k) / C(n, k) within each case,
then averages over cases. Intervals are 95% percentile bootstraps over cases.
"""

import argparse
import collections
import json
import math
import os
import random
from pathlib import Path

import grade

CAUSES = ("release", "mix", "tracking", "policy")
KS = {"none": (1, 2, 3, 5, 10), "partial": (1, 2, 3, 5), "full": (1, 2)}
BOOTSTRAP = 2000
COLORS = ["#1f4e9c", "#6b8fd6", "#c2602c", "#2f8f5b", "#8a5cc2", "#b5323c", "#5d6b7a"]


def pass_at_k(n, c, k):
    if k > n:
        raise ValueError("k exceeds trials")
    return 1.0 if n - c < k else 1 - math.comb(n - c, k) / math.comb(n, k)


def effective(counts):
    total = sum(counts.values())
    if not total:
        return 0.0
    return 2 ** -sum(v / total * math.log2(v / total) for v in counts.values() if v)


def case_metrics(case, labels, ks):
    """Per-case metrics from the list of labels for one condition."""
    n = len(labels)
    counts = collections.Counter(labels)
    valid = {k: v for k, v in counts.items() if k != "invalid"}
    m = dict(n=n, counts=dict(counts), effective=effective(valid))
    if case["truth"] is None:
        m["passk"] = {k: sum(pass_at_k(n, counts[c], k) for c in CAUSES) / len(CAUSES) for k in ks if k <= n}
        m["modal_share"] = max(valid.values()) / n if valid else 0.0
    else:
        hits = counts[case["truth"]]
        ruled_out = [c for c in CAUSES if c not in case["candidates"]]
        m["passk"] = {k: pass_at_k(n, hits, k) for k in ks if k <= n}
        m["ruled_out_rate"] = sum(counts[c] for c in ruled_out) / n
    return m


def bootstrap(values, seed):
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(values) for _ in values) / len(values) for _ in range(BOOTSTRAP))
    return means[int(0.025 * BOOTSTRAP)], means[int(0.975 * BOOTSTRAP) - 1]


def summarize(results):
    plan, rows = grade.labels(results)
    cases = {c["id"]: c for c in plan["cases"]}
    grouped = collections.defaultdict(list)
    for r in rows:
        grouped[(r["condition"], r["case"])].append(r["label"])
    out = {"plan_seed": plan["seed"], "repeats": plan["repeats"], "conditions": [], "label_sources": {}}
    out["label_sources"] = dict(collections.Counter(r.get("source") for r in rows))
    for cond in plan["conditions"]:
        entry = dict(id=cond["id"], label=cond["label"], model=cond["model"], thinking=cond["thinking"], tiers={})
        for tier in ("none", "partial", "full"):
            tier_cases = [c for c in plan["cases"] if c["tier"] == tier]
            per_case = {c["id"]: case_metrics(c, grouped[(cond["id"], c["id"])], KS[tier]) for c in tier_cases if grouped[(cond["id"], c["id"])]}
            if not per_case:
                continue
            ks = sorted(set.intersection(*(set(m["passk"]) for m in per_case.values())))
            t = dict(cases=len(per_case), trials=sum(m["n"] for m in per_case.values()))
            t["invalid"] = sum(m["counts"].get("invalid", 0) for m in per_case.values())
            t["passk"] = {}
            for k in ks:
                values = [m["passk"][k] for m in per_case.values()]
                lo, hi = bootstrap(values, f"{cond['id']}-{tier}-{k}")
                t["passk"][str(k)] = dict(mean=sum(values) / len(values), low=lo, high=hi)
            t["effective_causes"] = sum(m["effective"] for m in per_case.values()) / len(per_case)
            labels_all = collections.Counter()
            for m in per_case.values():
                labels_all.update(m["counts"])
            t["labels"] = dict(labels_all)
            if tier == "none":
                t["modal_share"] = sum(m["modal_share"] for m in per_case.values()) / len(per_case)
            else:
                t["ruled_out_rate"] = sum(m["ruled_out_rate"] for m in per_case.values()) / len(per_case)
                by_truth = collections.defaultdict(list)
                for cid, m in per_case.items():
                    by_truth[cases[cid]["truth"]].append(m["passk"][1])
                t["accuracy_by_truth"] = {c: sum(v) / len(v) for c, v in sorted(by_truth.items())}
            entry["tiers"][tier] = t
        out["conditions"].append(entry)
    return plan, out


def svg_chart(summary, tier, title):
    width, height, left, right, top, bottom = 640, 360, 56, 190, 40, 44
    conds = [c for c in summary["conditions"] if tier in c["tiers"]]
    ks = sorted({int(k) for c in conds for k in c["tiers"][tier]["passk"]})
    if not ks:
        return ""
    x0, x1 = left, width - right
    y0, y1 = height - bottom, top

    def x(k):
        return x0 + (x1 - x0) * (k - ks[0]) / max(1, ks[-1] - ks[0])

    def y(v):
        return y0 - (y0 - y1) * v

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" font-family="Helvetica, Arial, sans-serif" font-size="12">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="22" font-size="14" font-weight="bold" fill="#1d1d1f">{title}</text>',
    ]
    for v in (0, 0.25, 0.5, 0.75, 1.0):
        parts.append(f'<line x1="{x0}" y1="{y(v):.1f}" x2="{x1}" y2="{y(v):.1f}" stroke="#e3e3e8"/>')
        parts.append(f'<text x="{x0 - 8}" y="{y(v) + 4:.1f}" text-anchor="end" fill="#6e6e73">{v:.2f}</text>')
    for k in ks:
        parts.append(f'<text x="{x(k):.1f}" y="{y0 + 18}" text-anchor="middle" fill="#6e6e73">{k}</text>')
    parts.append(f'<text x="{(x0 + x1) / 2:.1f}" y="{height - 8}" text-anchor="middle" fill="#1d1d1f">Independent runs (k)</text>')
    parts.append(f'<text transform="translate(14 {(y0 + y1) / 2:.1f}) rotate(-90)" text-anchor="middle" fill="#1d1d1f">Chance the true cause appears (pass@k)</text>')
    for i, c in enumerate(conds):
        color = COLORS[i % len(COLORS)]
        pk = c["tiers"][tier]["passk"]
        points = " ".join(f"{x(int(k)):.1f},{y(v['mean']):.1f}" for k, v in sorted(pk.items(), key=lambda kv: int(kv[0])))
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        for k, v in pk.items():
            parts.append(f'<circle cx="{x(int(k)):.1f}" cy="{y(v["mean"]):.1f}" r="3.5" fill="{color}"/>')
        ly = top + 14 + i * 20
        parts.append(f'<line x1="{x1 + 16}" y1="{ly - 4}" x2="{x1 + 36}" y2="{ly - 4}" stroke="{color}" stroke-width="2.5"/>')
        parts.append(f'<text x="{x1 + 42}" y="{ly}" fill="#1d1d1f">{c["label"]}</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def fmt(v):
    return f"{v['mean']:.2f} [{v['low']:.2f}, {v['high']:.2f}]"


def report(results, plan, summary, report_path):
    name = results.name
    figures = Path(os.path.relpath(results, Path(report_path).resolve().parent)).as_posix()
    lines = [
        "# Results",
        "",
        "<!-- Generated by analyze.py. Do not edit by hand. -->",
        "",
        f"Round `{name}`, seed `{plan['seed']}`, state **{plan.get('state', 'unknown')}**"
        f" ({plan.get('unfinished_jobs', 0)} unfinished jobs). Trials per case: "
        + ", ".join(f"{t} {n}" for t, n in plan["repeats"].items()) + ".",
        "",
        "Every planned trial is in the denominator; trials without a valid answer count as misses. "
        "Brackets are 95% bootstrap intervals over cases.",
        "",
        "## No evidence: does running again explore other causes?",
        "",
        "The true cause cannot be identified from the prompt. pass@k averages over the four equally likely true causes, so any single fixed answer scores 0.25 at every k.",
        "",
        f"![pass@k with no evidence]({figures}/passk-none.svg)",
        "",
        "| Condition | Effective causes | Modal share | " + " | ".join(f"pass@{k}" for k in KS["none"]) + " | Labels |",
        "|---|---:|---:|" + "---:|" * len(KS["none"]) + "---|",
    ]
    for c in summary["conditions"]:
        t = c["tiers"].get("none")
        if not t:
            continue
        cells = [fmt(t["passk"][str(k)]) if str(k) in t["passk"] else "n/a" for k in KS["none"]]
        labels = ", ".join(f"{k} {v}" for k, v in sorted(t["labels"].items(), key=lambda kv: -kv[1]))
        lines.append(f"| {c['label']} | {t['effective_causes']:.2f} | {t['modal_share']:.2f} | " + " | ".join(cells) + f" | {labels} |")
    for tier, heading, blurb in (
        ("partial", "Partial evidence: two causes ruled out", "Diagnostics rule out two causes and leave the true cause and one alternative. A model that picks at random between the two scores 0.50 at k=1. Ruled-out rate is the share of answers naming a cause the evidence excluded."),
        ("full", "Full evidence: the cause is identifiable", "Diagnostics for all four causes are shown and only the true cause shows its signature. This is the control: it checks that models read the evidence."),
    ):
        lines += ["", f"## {heading}", "", blurb, ""]
        if tier == "partial":
            lines += [f"![pass@k with partial evidence]({figures}/passk-partial.svg)", ""]
        ks = KS[tier]
        lines += [
            "| Condition | " + " | ".join(f"pass@{k}" for k in ks) + " | Ruled-out rate | Accuracy by true cause (pass@1) |",
            "|---|" + "---:|" * len(ks) + "---:|---|",
        ]
        for c in summary["conditions"]:
            t = c["tiers"].get(tier)
            if not t:
                continue
            cells = [fmt(t["passk"][str(k)]) if str(k) in t["passk"] else "n/a" for k in ks]
            acc = ", ".join(f"{k} {v:.2f}" for k, v in t["accuracy_by_truth"].items())
            lines.append(f"| {c['label']} | " + " | ".join(cells) + f" | {t['ruled_out_rate']:.2f} | {acc} |")
    lines += [
        "",
        "## Labels",
        "",
        "Label sources: " + ", ".join(f"{k} {v}" for k, v in sorted(summary["label_sources"].items(), key=lambda kv: str(kv[0]))) + ".",
        "Pattern labels come from each scenario's change patterns; review labels were assigned from a blinded list that shows only the scenario and hypothesis text.",
        "",
    ]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results")
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    results = Path(args.results).resolve()
    plan, summary = summarize(results)
    (results / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    (results / "passk-none.svg").write_text(svg_chart(summary, "none", "No evidence: pass@k over four equally likely causes"))
    (results / "passk-partial.svg").write_text(svg_chart(summary, "partial", "Partial evidence: pass@k against the true cause"))
    if args.report:
        Path(args.report).write_text(report(results, plan, summary, args.report))
    print(json.dumps({c["id"]: {t: {k: round(v["mean"], 2) for k, v in d["passk"].items()} for t, d in c["tiers"].items()} for c in summary["conditions"]}, indent=1))


if __name__ == "__main__":
    main()
