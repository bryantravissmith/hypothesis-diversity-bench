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

Use --output to write to a separate analysis directory.
--comparisons selects explicit contrasts.

Every planned trial counts. A trial without a valid answer is a miss, not a
smaller denominator.

- none tier: the true cause is not identifiable, so the prompt is the same for
  every truth. pass@k averages over the four equally likely true causes.
- partial and full tiers: pass@k is against the case's true cause.

pass@k uses the unbiased estimator 1 - C(n-c, k) / C(n, k) within each case,
then averages over cases. Headline intervals are conservative fixed-prompt
bounds. Scenario sensitivity is reported separately.
"""

import argparse
import collections
import hashlib
import json
import math
import os
import sys
from pathlib import Path

import grade
import intervals

CAUSES = ("release", "mix", "tracking", "policy")
KS = {"none": (1, 2, 3, 5, 10), "partial": (1, 2, 3, 5), "full": (1, 2)}
ANALYSIS_VERSION = "fixed-prompt-ci-v2"
ROOT = Path(__file__).resolve().parent


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def provenance(results):
    inputs = ["plan.json", "attempts.jsonl", "reviews.json", "labels.jsonl"]
    inputs += [str(p.relative_to(results)) for p in sorted((results / "source").glob("*")) if p.is_file()]
    return dict(
        version=ANALYSIS_VERSION,
        python_version=sys.version.split()[0],
        source_sha256={name: file_hash(ROOT / name) for name in ("analyze.py", "grade.py", "intervals.py")},
        input_sha256={name: file_hash(results / name) for name in inputs if (results / name).exists()},
        interval_method=intervals.METHOD,
        confidence=intervals.CONFIDENCE,
        estimand="equal-case mean k-run score on the fixed frozen prompts",
        assumptions=[
            "independent planned-job outcomes",
            "stable distribution across repetitions of each prompt and condition",
            "fixed runner, retry policy and grading rules",
        ],
        exclusions=["new-scenario generalization", "label error", "provider drift", "dependent job outcomes"],
        adjustment="pointwise; configured contrasts have separate family adjustment",
    )


def validate_schedule(plan, rows):
    """A malformed schedule is not evidence of a smaller benchmark."""
    cases = {c["id"]: c for c in plan["cases"]}
    conditions = {c["id"] for c in plan["conditions"]}
    if len(cases) != len(plan["cases"]) or len(conditions) != len(plan["conditions"]):
        raise ValueError("Duplicate case or condition IDs")
    job_ids, trials = set(), set()
    for row in rows:
        if row["condition"] not in conditions or row["case"] not in cases:
            raise ValueError("Unknown case or condition in schedule")
        trial = (row["condition"], row["case"], row["trial"])
        if row["job"] in job_ids or trial in trials:
            raise ValueError("Duplicate job or trial in schedule")
        job_ids.add(row["job"])
        trials.add(trial)
    expected = {
        (condition, case["id"], trial)
        for condition in conditions for case in cases.values()
        for trial in range(1, plan["repeats"][case["tier"]] + 1)
    }
    if trials != expected:
        raise ValueError("Schedule does not match the frozen cases, conditions and repeat counts")


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


def summarize(results):
    plan, rows = grade.labels(results)
    validate_schedule(plan, rows)
    saved_labels = results / "labels.jsonl"
    if saved_labels.exists():
        saved = [json.loads(line) for line in saved_labels.read_text().splitlines() if line.strip()]
        if saved != rows:
            raise ValueError(f"{results.name}: saved labels differ from grading; rebuild labels explicitly")
    complete = (plan.get("state") == "complete" and plan.get("unfinished_jobs") == 0
                and all(row.get("outcome") != "missing" for row in rows))
    pending = any(row["source"] == "pattern" and row["label"] in ("other", "multiple") for row in rows)
    inferential = complete and not pending
    cases = {c["id"]: c for c in plan["cases"]}
    grouped = collections.defaultdict(list)
    for r in rows:
        grouped[(r["condition"], r["case"])].append(r["label"])
    out = {"plan_seed": plan["seed"], "repeats": plan["repeats"], "conditions": [], "label_sources": {}, "analysis": provenance(results)}
    out["analysis"]["inferential_status"] = "available" if inferential else "unavailable"
    out["analysis"]["unavailable_reason"] = None if inferential else (
        "incomplete round or missing final attempts" if not complete else "unreviewed ambiguous labels"
    )
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
            trial_counts = {m["n"] for m in per_case.values()}
            if len(trial_counts) != 1:
                raise ValueError("Fixed-prompt bounds require equal repeat counts within a tier")
            repeats = next(iter(trial_counts))
            t["repeats_per_case"] = repeats
            t["scenario_count"] = len({cases[cid]["scenario"] for cid in per_case})
            t["inferential_status"] = out["analysis"]["inferential_status"]
            t["unavailable_reason"] = out["analysis"]["unavailable_reason"]
            t["per_case"] = {
                cid: dict(m, scenario=cases[cid]["scenario"], truth=cases[cid]["truth"])
                for cid, m in per_case.items()
            }
            t["invalid"] = sum(m["counts"].get("invalid", 0) for m in per_case.values())
            t["passk"] = {}
            t["scenario_sensitivity"] = {}
            for k in ks:
                values = [m["passk"][k] for m in per_case.values()]
                mean = sum(values) / len(values)
                ci = intervals.fixed_prompt_interval(mean, len(values), repeats, k, tier) if inferential else dict(low=None, high=None)
                t["passk"][str(k)] = dict(mean=mean, **ci)
                groups = collections.defaultdict(list)
                for cid, m in per_case.items():
                    groups[cases[cid]["scenario"]].append(m["passk"][k])
                t["scenario_sensitivity"][str(k)] = intervals.scenario_sensitivity(groups)
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
    ("gpt-6-astra", "#1e9aa8"),
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


def svg_chart(summary, tier, title, references=()):
    """Line chart of pass@k. The legend is ordered by the last point so it
    reads top to bottom like the lines. `references` are (function of k,
    label, dash) curves drawn in gray, such as always giving the same answer
    or picking uniformly at random on every run."""
    conds = [c for c in summary["conditions"] if tier in c["tiers"] and c.get("variant", "plain") == "plain"]
    ks = sorted({int(k) for c in conds for k in c["tiers"][tier]["passk"]})
    if not ks:
        return ""
    last = str(ks[-1])
    conds.sort(key=lambda c: -c["tiers"][tier]["passk"].get(last, {"mean": 0})["mean"])
    top_value = max(
        [v["mean"] for c in conds for v in c["tiers"][tier]["passk"].values()]
        + [curve(k) for curve, _, _ in references for k in ks]
    )
    y_max = min(1.0, math.ceil((top_value + 0.05) * 4) / 4)
    width, left, right, top, bottom = 780, 56, 250, 40, 44
    height = max(360, top + 30 + 20 * (len(conds) + len(references)) + bottom)
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
    parts.append(f'<text x="{(x0 + x1) / 2:.1f}" y="{height - 8}" text-anchor="middle" fill="#1d1d1f">Runs (k)</text>')
    axis_label = "Expected category coverage / 4 (pass@k)" if tier == "none" else "Chance assigned truth appears (pass@k)"
    parts.append(f'<text transform="translate(14 {(y0 + y1) / 2:.1f}) rotate(-90)" text-anchor="middle" fill="#1d1d1f">{axis_label}</text>')
    for j, (curve, label, pattern) in enumerate(references):
        points = " ".join(f"{x(k):.1f},{y(curve(k)):.1f}" for k in range(ks[0], ks[-1] + 1))
        parts.append(f'<polyline points="{points}" fill="none" stroke="#6e6e73" stroke-width="1.5" stroke-dasharray="{pattern}"/>')
        ly = top + 14 + (len(conds) + j) * 20
        parts.append(f'<line x1="{x1 + 16}" y1="{ly - 4}" x2="{x1 + 40}" y2="{ly - 4}" stroke="#6e6e73" stroke-width="1.5" stroke-dasharray="{pattern}"/>')
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
    if v["low"] is None:
        return f"{v['mean']:.2f} [unavailable]"
    return f"{v['mean']:.2f} [{v['low']:.2f}, {v['high']:.2f}]"


def contrasts(combined, spec):
    """Explicit round-qualified pairs; never pool or pair trial indices."""
    by_key = {(c["round"], c["id"]): c for c in combined["conditions"]}
    output = []
    family_ids = [f["id"] for f in spec["families"]]
    if len(set(family_ids)) != len(family_ids):
        raise ValueError("Duplicate contrast family IDs")
    for family in spec["families"]:
        tier, k, alpha = family["tier"], family["k"], family["alpha"]
        endpoints = {tuple(pair[side]) for pair in family["pairs"] for side in ("left", "right")}
        if not endpoints or not 0 < alpha < 1 or tier not in KS or k not in KS[tier]:
            raise ValueError("Invalid contrast family")
        if any(len(key) != 2 for key in endpoints):
            raise ValueError("Contrast endpoints must be [round, condition]")
        names = [p["id"] for p in family["pairs"]]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate comparison IDs within family")
        per_endpoint = alpha / len(endpoints)
        result = dict(id=family["id"], tier=tier, k=k, alpha=alpha,
                      adjustment="Bonferroni across unique endpoint conditions within this family",
                      endpoint_count=len(endpoints), endpoint_alpha=per_endpoint, pairs=[])
        for pair in family["pairs"]:
            item = dict(pair, status="unavailable")
            result["pairs"].append(item)
            left, right = (by_key.get(tuple(pair[side])) for side in ("left", "right"))
            if left is None or right is None:
                item["reason"] = "configured endpoint absent from these rounds"
                continue
            lt, rt = (c["tiers"].get(tier) for c in (left, right))
            if not lt or not rt or any(str(k) not in t["passk"] for t in (lt, rt)):
                item["reason"] = "configured tier/k unavailable"
                continue
            lm, rm = (t["passk"][str(k)]["mean"] for t in (lt, rt))
            item["mean"] = lm - rm
            if lt["per_case"].keys() != rt["per_case"].keys():
                raise ValueError("Contrasts require identical cases")
            groups = collections.defaultdict(list)
            for cid, lc in lt["per_case"].items():
                rc = rt["per_case"][cid]
                if (lc["scenario"], lc["truth"]) != (rc["scenario"], rc["truth"]):
                    raise ValueError("Contrast case metadata differ")
                groups[lc["scenario"]].append(lc["passk"][k] - rc["passk"][k])
            item["scenario_sensitivity"] = intervals.scenario_sensitivity(groups)
            if any(t["inferential_status"] != "available" for t in (lt, rt)):
                item["reason"] = "at least one endpoint is not inferentially available"
                continue
            bounds = [intervals.fixed_prompt_interval(
                t["passk"][str(k)]["mean"], t["cases"], t["repeats_per_case"], k, tier, per_endpoint,
            ) for t in (lt, rt)]
            item.update(status="available", low=bounds[0]["low"] - bounds[1]["high"],
                        high=bounds[0]["high"] - bounds[1]["low"], endpoints=bounds)
        output.append(result)
    return output


def uncertainty_report(combined):
    lines = [
        "# Uncertainty and sensitivity", "",
        "<!-- Generated by analyze.py. Do not edit by hand. -->", "",
        "Conservative 95% confidence bounds target fresh planned-job outcomes on the fixed prompts. "
        "They assume independent jobs and a stable distribution within each prompt/condition. "
        "Coverage is pointwise for each score; comparison families receive a separate multiplicity adjustment.", "",
        "## Fixed-prompt confidence intervals", "",
        "| Round / condition | Tier / k | Estimate | 95% CI | Block parameter |",
        "|---|---|---:|---|---:|",
    ]
    for c in combined["conditions"]:
        for tier, k in (("none", 10), ("partial", 1), ("full", 1), ("full", 2)):
            v = c["tiers"].get(tier, {}).get("passk", {}).get(str(k))
            if v is None:
                continue
            bound = "unavailable" if v["low"] is None else f"[{v['low']:.3f}, {v['high']:.3f}]"
            lines.append(f"| {c['round']} / {c['label']} | {tier} / {k} | {v['mean']:.3f} | "
                         f"{bound} | {v.get('block_parameter', 'n/a')} |")
    lines += ["", "## Direct contrasts", "",
              "Comparisons were selected after observing these data. "
              "Each family has its own 5% error budget. "
              "Positive means left exceeds right. Cross-round differences can include collection-time effects. "
              "Paired scenario resampling matches cases. "
              "Scenario percentiles and leave-one-out ranges describe sensitivity to the six templates.", ""]
    for family in combined.get("contrasts", []):
        lines += [f"### {family['id']}: {family['tier']} pass@{family['k']}", "",
                  f"Family alpha {family['alpha']}; {family['endpoint_count']} unique endpoints; "
                  f"endpoint alpha {family['endpoint_alpha']:.6f}.", "",
                  "| Left minus right | Estimate | Family-adjusted bound | Scenario 95% percentile range | Leave-one-scenario-out range |",
                  "|---|---:|---|---|---|"]
        for pair in family["pairs"]:
            name = " / ".join(pair["left"]) + " minus " + " / ".join(pair["right"])
            if pair["status"] != "available":
                lines.append(f"| {name} | n/a | {pair['reason']} | n/a | n/a |")
                continue
            s = pair["scenario_sensitivity"]
            b, loo = s["bootstrap"], s["leave_one_out_range"]
            loo_text = f"[{loo[0]:.3f}, {loo[1]:.3f}]" if loo else "n/a"
            lines.append(f"| {name} | {pair['mean']:+.3f} | [{pair['low']:.3f}, {pair['high']:.3f}] | "
                         f"[{b['low']:.3f}, {b['high']:.3f}] | {loo_text} |")
        lines.append("")
    lines += ["The companion `summary.json` contains every k, individual scenario scores and paired effects "
              "and leave-one-out estimates, input/source hashes, assumptions and inference status. "
              "For six scenarios, the sensitivity calculation enumerates all 46,656 ordered cluster resamples exactly. "
              "It retains all case variants in a selected scenario and preserves their case weights.", ""]
    return "\n".join(lines)


def report(combined, figures):
    lines = [
        "# Results",
        "",
        "<!-- Generated by analyze.py. Do not edit by hand. -->",
        "",
        "Rounds have identical base case records; they ran separately and are never pooled. "
        "The charts show plain-prompt conditions; meta-prompt conditions are compared in their own section. "
        "Every planned trial is in the denominator; trials without a valid answer count as misses. "
        "Brackets are conservative, pointwise 95% fixed-prompt confidence bounds, assuming independent jobs "
        "and stable response distributions within each prompt/condition. "
        f"See [confidence intervals and scenario sensitivity]({figures}/UNCERTAINTY.md).",
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
        "## No evidence: cause coverage across repeated runs",
        "",
        "pass@k averages over four hypothetical equally likely truths. "
        "Any single fixed scored category scores 0.25 at every k, "
        "while picking one of four uniformly on every run scores 1 - (3/4)^k (0.94 at k=10). "
        "All valid single-category answers score 0.25 at pass@1. "
        "Charts show point estimates; tables give confidence intervals.",
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
        ("partial", "Partial evidence: two causes ruled out", "Diagnostics rule out two causes and leave the true cause and one alternative. Choosing once uniformly at random and repeating that choice has expected score 0.50 at every k; choosing independently on every run scores 1 - (1/2)^k. Remaining pairs are imbalanced in these frozen cases. Ruled-out rate is the share of answers naming a cause the evidence excluded."),
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
    counts = collections.Counter(c["id"] for c in combined["conditions"] if c.get("variant", "plain") == "plain")
    plain = {c["id"]: c for c in combined["conditions"] if c.get("variant", "plain") == "plain" and counts[c["id"]] == 1}
    pairs = [(plain[c["base"]], c) for c in combined["conditions"] if c.get("variant") == "meta" and c.get("base") in plain]
    if not pairs:
        return []
    lines = [
        "",
        "## Meta prompt: telling the model it is one of many samples",
        "",
        "Meta conditions put the Random Number Bench sampling sentence (\"Your response will be one observation in a random sample collected from many independent LLM sessions.\") before the unchanged case prompt. Each row compares a meta condition with the unique plain condition for the same model and effort on identical base cases. Round identities show whether collection occurred within or across rounds. Repeated plain IDs are omitted from this convenience table; configured contrasts always identify both rounds explicitly.",
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
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--figures", help="Directory for figures and combined summary; write round summaries in place")
    output_group.add_argument("--output", help="Separate output directory for all analysis artifacts")
    parser.add_argument("--comparisons", help="Explicit JSON comparison specification")
    parser.add_argument("--report")
    args = parser.parse_args(argv)
    dirs = [Path(d).resolve() for d in args.results]
    if len({d.name for d in dirs}) != len(dirs):
        raise SystemExit("Round directory names must be unique")
    combined = {"rounds": [], "conditions": [], "analysis_version": ANALYSIS_VERSION}
    summaries = []
    reference = None
    for results in dirs:
        plan, summary = summarize(results)
        cases = sorted(plan["cases"], key=lambda c: c["id"])
        if reference is not None and cases != reference:
            raise SystemExit(f"{results.name} has different cases; rounds can only be reported together with identical cases")
        reference = cases
        summaries.append((results, summary))
        combined["rounds"].append(dict(
            name=results.name, seed=plan["seed"], state=plan.get("state", "unknown"),
            unfinished_jobs=plan.get("unfinished_jobs", 0), repeats=plan["repeats"],
            label_sources=summary["label_sources"],
            analysis=summary["analysis"],
        ))
        combined["conditions"] += [dict(c, round=results.name) for c in summary["conditions"]]
    if args.comparisons:
        spec = json.loads(Path(args.comparisons).read_text())
        combined["comparison_spec"] = dict(spec, sha256=file_hash(args.comparisons))
        combined["contrasts"] = contrasts(combined, spec)
    else:
        combined["contrasts"] = []
    figures = Path(args.output or args.figures).resolve() if args.output or args.figures else dirs[0]
    if args.output and any(figures == d or figures in d.parents or d in figures.parents for d in dirs):
        raise SystemExit("Versioned output must be separate from input rounds")
    if args.report and any(Path(args.report).resolve().is_relative_to(d) for d in dirs) and args.output:
        raise SystemExit("Versioned report must be outside input rounds")
    # Validate all inputs and comparisons before writing any output.
    figures.mkdir(parents=True, exist_ok=True)
    for results, summary in summaries:
        destination = figures / "rounds" / results.name if args.output else results
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    if len(dirs) > 1 or args.output or figures != dirs[0]:
        (figures / "summary.json").write_text(json.dumps(combined, indent=1) + "\n")
    (figures / "UNCERTAINTY.md").write_text(uncertainty_report(combined))
    (figures / "passk-none.svg").write_text(svg_chart(combined, "none", "No evidence: pass@k over four equally likely causes", (
        (lambda k: 0.25, "Always the same answer", "2 3"),
        (lambda k: 1 - 0.75 ** k, "Random pick of 4 on every run", "8 3 2 3"),
    )))
    (figures / "passk-partial.svg").write_text(svg_chart(combined, "partial", "Partial evidence: pass@k against the true cause", (
        (lambda k: 0.5, "Random once, then repeat", "2 3"),
        (lambda k: 1 - 0.5 ** k, "Random pick of 2 on every run", "8 3 2 3"),
    )))
    if args.report:
        rel = Path(os.path.relpath(figures, Path(args.report).resolve().parent)).as_posix()
        Path(args.report).write_text(report(combined, rel))
    if args.output:
        (figures / "RESULTS.md").write_text(report(combined, "."))
    print(json.dumps({f"{c['round']}/{c['id']}": {t: {k: round(v["mean"], 2) for k, v in d["passk"].items()} for t, d in c["tiers"].items()} for c in combined["conditions"]}, indent=1))


if __name__ == "__main__":
    main()
