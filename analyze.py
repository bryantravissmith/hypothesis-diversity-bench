# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Diversity, accuracy and pass@k from labeled results. Generates the report.

    uv run analyze.py results/round-001 results/round-002 \\
        --figures results/figures --report RESULTS.md

Writes summary.json into each round, the pass@k figures (and a combined
summary when several rounds are given) into --figures and, with --report, a
generated Markdown report. Rounds must have identical cases and are reported
side by side, never pooled. Never edit those outputs by hand.

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
        entry = dict(
            id=cond["id"], label=cond["label"], model=cond["model"], thinking=cond["thinking"],
            variant="meta" if cond.get("prompt_prefix") else "plain", base=cond.get("base"), tiers={},
        )
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


FAMILY_COLORS = (
    ("claude-", "#c2602c"),
    ("gpt-6.1-terra", "#7b4fc9"),
    ("gpt-6.1", "#1f4e9c"),
    ("gpt-6-", "#4f7fd1"),
    ("gpt-5.6", "#8fb0e8"),
    ("openai/gpt-4o", "#5d6b7a"),
    ("deepseek/deepseek-r1", "#2f8f5b"),
    ("deepseek/", "#8bc4a0"),
)


def style(condition):
    color = next((c for prefix, c in FAMILY_COLORS if condition["model"].startswith(prefix)), "#8a5cc2")
    dash = ' stroke-dasharray="6 4"' if condition["thinking"] == "max" else ""
    return color, dash


def svg_chart(summary, tier, title, reference=None):
    """Line chart of pass@k. The legend is ordered by the last point so it
    reads top to bottom like the lines; `reference` draws a labeled dashed
    line, such as the score of a model that always gives one answer."""
    conds = [c for c in summary["conditions"] if tier in c["tiers"] and c.get("variant", "plain") == "plain"]
    ks = sorted({int(k) for c in conds for k in c["tiers"][tier]["passk"]})
    if not ks:
        return ""
    last = str(ks[-1])
    conds.sort(key=lambda c: -c["tiers"][tier]["passk"].get(last, {"mean": 0})["mean"])
    top_value = max(v["mean"] for c in conds for v in c["tiers"][tier]["passk"].values())
    y_max = min(1.0, math.ceil((top_value + 0.05) * 4) / 4)
    width, left, right, top, bottom = 780, 56, 250, 40, 44
    height = max(360, top + 30 + 20 * (len(conds) + (reference is not None)) + bottom)
    x0, x1 = left, width - right
    y0, y1 = height - bottom, top

    def x(k):
        return x0 + (x1 - x0) * (k - ks[0]) / max(1, ks[-1] - ks[0])

    def y(v):
        return y0 - (y0 - y1) * v / y_max

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" font-family="Helvetica, Arial, sans-serif" font-size="12">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="22" font-size="14" font-weight="bold" fill="#1d1d1f">{title}</text>',
    ]
    for v in [i / 4 for i in range(int(y_max * 4) + 1)]:
        parts.append(f'<line x1="{x0}" y1="{y(v):.1f}" x2="{x1}" y2="{y(v):.1f}" stroke="#e3e3e8"/>')
        parts.append(f'<text x="{x0 - 8}" y="{y(v) + 4:.1f}" text-anchor="end" fill="#6e6e73">{v:.2f}</text>')
    for k in ks:
        parts.append(f'<text x="{x(k):.1f}" y="{y0 + 18}" text-anchor="middle" fill="#6e6e73">{k}</text>')
    parts.append(f'<text x="{(x0 + x1) / 2:.1f}" y="{height - 8}" text-anchor="middle" fill="#1d1d1f">Independent runs (k)</text>')
    parts.append(f'<text transform="translate(14 {(y0 + y1) / 2:.1f}) rotate(-90)" text-anchor="middle" fill="#1d1d1f">Chance the true cause appears (pass@k)</text>')
    if reference is not None:
        value, label = reference
        parts.append(f'<line x1="{x0}" y1="{y(value):.1f}" x2="{x1}" y2="{y(value):.1f}" stroke="#6e6e73" stroke-width="1.5" stroke-dasharray="2 3"/>')
        ly = top + 14 + len(conds) * 20
        parts.append(f'<line x1="{x1 + 16}" y1="{ly - 4}" x2="{x1 + 40}" y2="{ly - 4}" stroke="#6e6e73" stroke-width="1.5" stroke-dasharray="2 3"/>')
        parts.append(f'<text x="{x1 + 46}" y="{ly}" fill="#6e6e73">{label}</text>')
    for i, c in enumerate(conds):
        color, dash = style(c)
        pk = c["tiers"][tier]["passk"]
        points = " ".join(f"{x(int(k)):.1f},{y(v['mean']):.1f}" for k, v in sorted(pk.items(), key=lambda kv: int(kv[0])))
        parts.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.5"{dash}/>')
        for k, v in pk.items():
            parts.append(f'<circle cx="{x(int(k)):.1f}" cy="{y(v["mean"]):.1f}" r="3.5" fill="{color}"/>')
        ly = top + 14 + i * 20
        parts.append(f'<line x1="{x1 + 16}" y1="{ly - 4}" x2="{x1 + 40}" y2="{ly - 4}" stroke="{color}" stroke-width="2.5"{dash}/>')
        parts.append(f'<text x="{x1 + 46}" y="{ly}" fill="#1d1d1f">{c["label"]}</text>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def fmt(v):
    return f"{v['mean']:.2f} [{v['low']:.2f}, {v['high']:.2f}]"


def report(combined, figures):
    lines = [
        "# Results",
        "",
        "<!-- Generated by analyze.py. Do not edit by hand. -->",
        "",
        "Rounds share one frozen seed and identical case prompts; they ran separately and are never pooled. "
        "The charts show plain-prompt conditions; meta-prompt conditions are compared in their own section. "
        "Every planned trial is in the denominator; trials without a valid answer count as misses. "
        "Brackets are 95% bootstrap intervals over cases.",
        "",
        "| Round | Seed | State | Unfinished jobs | Trials per case | Label sources |",
        "|---|---|---|---:|---|---|",
    ]
    for r in combined["rounds"]:
        trials = ", ".join(f"{t} {n}" for t, n in r["repeats"].items())
        sources = ", ".join(f"{k} {v}" for k, v in sorted(r["label_sources"].items(), key=lambda kv: str(kv[0])))
        lines.append(f"| `{r['name']}` | `{r['seed']}` | {r['state']} | {r['unfinished_jobs']} | {trials} | {sources} |")
    lines += [
        "",
        "## No evidence: does running again explore other causes?",
        "",
        "The true cause cannot be identified from the prompt. pass@k averages over the four equally likely true causes, so any single fixed answer scores 0.25 at every k.",
        "",
        f"![pass@k with no evidence]({figures}/passk-none.svg)",
        "",
        "| Condition | Round | Effective causes | Modal share | " + " | ".join(f"pass@{k}" for k in KS["none"]) + " | Labels |",
        "|---|---|---:|---:|" + "---:|" * len(KS["none"]) + "---|",
    ]
    for c in combined["conditions"]:
        t = c["tiers"].get("none")
        if not t:
            continue
        cells = [fmt(t["passk"][str(k)]) if str(k) in t["passk"] else "n/a" for k in KS["none"]]
        labels = ", ".join(f"{k} {v}" for k, v in sorted(t["labels"].items(), key=lambda kv: -kv[1]))
        lines.append(f"| {c['label']} | `{c['round']}` | {t['effective_causes']:.2f} | {t['modal_share']:.2f} | " + " | ".join(cells) + f" | {labels} |")
    for tier, heading, blurb in (
        ("partial", "Partial evidence: two causes ruled out", "Diagnostics rule out two causes and leave the true cause and one alternative. A model that picks at random between the two scores 0.50 at k=1. Ruled-out rate is the share of answers naming a cause the evidence excluded."),
        ("full", "Full evidence: the cause is identifiable", "Diagnostics for all four causes are shown and only the true cause shows its signature. This is the control: it checks that models read the evidence."),
    ):
        lines += ["", f"## {heading}", "", blurb, ""]
        if tier == "partial":
            lines += [f"![pass@k with partial evidence]({figures}/passk-partial.svg)", ""]
        ks = KS[tier]
        lines += [
            "| Condition | Round | " + " | ".join(f"pass@{k}" for k in ks) + " | Ruled-out rate | Accuracy by true cause (pass@1) |",
            "|---|---|" + "---:|" * len(ks) + "---:|---|",
        ]
        for c in combined["conditions"]:
            t = c["tiers"].get(tier)
            if not t:
                continue
            cells = [fmt(t["passk"][str(k)]) if str(k) in t["passk"] else "n/a" for k in ks]
            acc = ", ".join(f"{k} {v:.2f}" for k, v in t["accuracy_by_truth"].items())
            lines.append(f"| {c['label']} | `{c['round']}` | " + " | ".join(cells) + f" | {t['ruled_out_rate']:.2f} | {acc} |")
    lines += meta_section(combined)
    lines += [
        "",
        "## Labels",
        "",
        "Pattern labels come from each scenario's change patterns. Review labels were assigned from a blinded list that shows only the scenario and hypothesis text, never the model; decisions and reasons are in each round's `reviews.json`.",
        "",
    ]
    return "\n".join(lines)


def meta_section(combined):
    """Pair each meta-prompt condition with its plain counterpart."""
    plain = {c["id"]: c for c in combined["conditions"] if c.get("variant", "plain") == "plain"}
    pairs = [(plain[c["base"]], c) for c in combined["conditions"] if c.get("variant") == "meta" and c.get("base") in plain]
    if not pairs:
        return []
    lines = [
        "",
        "## Meta prompt: telling the model it is one of many samples",
        "",
        "Meta conditions put the Random Number Bench sampling sentence (\"Your response will be one observation in a random sample collected from many independent LLM sessions.\") before the unchanged case prompt. Each row compares a meta condition with the plain condition for the same model and effort; they ran in separate rounds on identical cases.",
        "",
        "| Model and effort | Plain round | Meta round | No evidence: effective causes, plain / meta | No evidence: pass@10, plain / meta | Partial: pass@1, plain / meta | Partial: ruled-out rate, plain / meta | Full: pass@1, plain / meta |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]

    def pk(c, tier, k):
        return c["tiers"].get(tier, {}).get("passk", {}).get(str(k), {}).get("mean")

    def pair(a, b, digits=2):
        return " / ".join("n/a" if v is None else f"{v:.{digits}f}" for v in (a, b))

    for base, meta in pairs:
        name = base["label"]
        lines.append(
            f"| {name} | `{base['round']}` | `{meta['round']}` | "
            f"{pair(base['tiers'].get('none', {}).get('effective_causes'), meta['tiers'].get('none', {}).get('effective_causes'))} | "
            f"{pair(pk(base, 'none', 10), pk(meta, 'none', 10))} | "
            f"{pair(pk(base, 'partial', 1), pk(meta, 'partial', 1))} | "
            f"{pair(base['tiers'].get('partial', {}).get('ruled_out_rate'), meta['tiers'].get('partial', {}).get('ruled_out_rate'))} | "
            f"{pair(pk(base, 'full', 1), pk(meta, 'full', 1))} |"
        )
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", nargs="+", help="One or more exported rounds with identical cases")
    parser.add_argument("--figures", help="Directory for figures and the combined summary (default: the first round)")
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    dirs = [Path(d).resolve() for d in args.results]
    combined = {"rounds": [], "conditions": []}
    reference = None
    for results in dirs:
        plan, summary = summarize(results)
        prompts = [(c["id"], c["prompt"]) for c in plan["cases"]]
        if reference is not None and prompts != reference:
            raise SystemExit(f"{results.name} has different cases; rounds can only be reported together with identical cases")
        reference = prompts
        (results / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
        combined["rounds"].append(dict(
            name=results.name, seed=plan["seed"], state=plan.get("state", "unknown"),
            unfinished_jobs=plan.get("unfinished_jobs", 0), repeats=plan["repeats"],
            label_sources=summary["label_sources"],
        ))
        combined["conditions"] += [dict(c, round=results.name) for c in summary["conditions"]]
    figures = Path(args.figures).resolve() if args.figures else dirs[0]
    figures.mkdir(parents=True, exist_ok=True)
    if len(dirs) > 1:
        (figures / "summary.json").write_text(json.dumps(combined, indent=1) + "\n")
    (figures / "passk-none.svg").write_text(svg_chart(combined, "none", "No evidence: pass@k over four equally likely causes", (0.25, "Always the same answer")))
    (figures / "passk-partial.svg").write_text(svg_chart(combined, "partial", "Partial evidence: pass@k against the true cause", (0.5, "Random pick of the two remaining")))
    if args.report:
        rel = Path(os.path.relpath(figures, Path(args.report).resolve().parent)).as_posix()
        Path(args.report).write_text(report(combined, rel))
    print(json.dumps({f"{c['round']}/{c['id']}": {t: {k: round(v["mean"], 2) for k, v in d["passk"].items()} for t, d in c["tiers"].items()} for c in combined["conditions"]}, indent=1))


if __name__ == "__main__":
    main()
