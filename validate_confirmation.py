# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Calibration and power of the round-005 confirmatory tests, by simulation.

    uv run validate_confirmation.py --output results/validation

No model calls. Simulated answers come from the no-evidence label
distributions observed in rounds 001-004. Truth smoothing adds a pseudo count
to each cause: zero reproduces the observed, often single-cause distributions,
the hardest case for variance estimation; larger values give unseen causes
some probability. Each round-005 condition borrows the closest
observed condition. Every scenario's distribution is reused for all of its
prompt variants.

Calibration uses three score nulls: identical distributions, equal scores with
the favorite cause relabeled on one side, and rare-label distributions. Power
shrinks each historical pair effect toward zero, because effects chosen after
looking at data are biased upward. Shrinking mixes the two distributions, with
the weight found by bisection so the true score difference is the stated share
of the historical one; score is not linear in the mixing weight. All variance
estimates in coverage_test.py are compared. Effort is the one confirmatory
family; meta-prompt rows show what an exploratory estimate could detect.
"""

import argparse
import hashlib
import json
import random
from pathlib import Path

import coverage_test

ROOT = Path(__file__).resolve().parent
CAUSES = ("release", "mix", "tracking", "policy")
MISS = "invalid"
K, REPEATS, ALPHA = 10, 20, 0.05
HISTORY = {
    "opus-5.5-medium": ("round-001", "opus-5.5-medium"),
    "opus-5.5-max": ("round-001", "opus-5.5-max"),
    "gpt-6.1-terra-medium": ("round-003", "gpt-6.1-terra-medium"),
    "gpt-6.1-terra-max": ("round-003", "gpt-6.1-terra-max"),
    "gpt-6.1-terra-medium-meta": ("round-004", "gpt-6.1-terra-medium-meta"),
    "gpt-6.1-terra-max-meta": ("round-004", "gpt-6.1-terra-max-meta"),
    "gpt-6-astra-medium": ("round-004", "gpt-6-astra-medium"),
    "gpt-6-astra-max": ("round-004", "gpt-6-astra-max"),
    "gpt-6-astra-medium-meta": ("round-004", "gpt-6-astra-medium-meta"),
    "gpt-6-astra-max-meta": ("round-004", "gpt-6-astra-max-meta"),
}
EFFORT = [("opus-5.5-max", "opus-5.5-medium"), ("gpt-6.1-terra-max", "gpt-6.1-terra-medium"),
          ("gpt-6-astra-max", "gpt-6-astra-medium")]
META = [(f"{base}-meta", base) for pair in EFFORT[1:] for base in pair]


def history(pseudo):
    """Smoothed per-scenario label probabilities for each round-005 condition."""
    out = {}
    for cond, (rnd, source) in HISTORY.items():
        counts = {}
        for line in (ROOT / "results" / rnd / "labels.jsonl").read_text().splitlines():
            row = json.loads(line)
            if row["condition"] == source and row["case"].endswith("--none"):
                scenario = row["case"].removesuffix("--none")
                label = row["label"] if row["label"] in CAUSES else MISS
                counts.setdefault(scenario, dict.fromkeys(CAUSES + (MISS,), 0))[label] += 1
        out[cond] = {}
        for scenario, c in counts.items():
            weights = {label: c[label] + (pseudo if label in CAUSES else 0) for label in c}
            total = sum(weights.values())
            out[cond][scenario] = {label: w / total for label, w in weights.items()}
    return out


def score(p):
    return sum(1 - (1 - p[c]) ** K for c in CAUSES) / len(CAUSES)


def mix(base, other, weight):
    return {label: base[label] + weight * (other[label] - base[label]) for label in base}


def relabel(p, rng):
    order = rng.sample(CAUSES, len(CAUSES))
    return {**{new: p[old] for old, new in zip(CAUSES, order)}, MISS: p[MISS]}


def draw(p, rng):
    labels = list(p)
    return rng.choices(labels, [p[label] for label in labels], k=REPEATS)


def pair_effect(left, right, scenarios):
    return sum(score(left[s]) - score(right[s]) for s in scenarios) / len(scenarios)


def shrink(left, right, scenarios, share):
    """Left distributions mixed toward right so the pair's true effect is share of its full effect."""
    full = pair_effect(left, right, scenarios)
    low, high = 0.0, 1.0
    for _ in range(60):
        mid = (low + high) / 2
        mixed = {s: mix(right[s], left[s], mid) for s in scenarios}
        if (pair_effect(mixed, right, scenarios) - share * full) * full < 0:
            low = mid
        else:
            high = mid
    return {s: mix(right[s], left[s], high) for s in scenarios}


def simulate(pairs, scenarios, variants, alternative, variance, reps, seed):
    """pairs: list of (left dist by scenario, right dist by scenario). Returns rejection rates."""
    rng = random.Random(seed)
    pooled = per_pair = 0
    for _ in range(reps):
        data = {}
        for i, (left, right) in enumerate(pairs):
            data[str(i)] = [(draw(left[s], rng), draw(right[s], rng)) for s in scenarios for _ in range(variants)]
        out = coverage_test.family_test(data, CAUSES, K, alternative, variance)
        pooled += out["pooled"]["p"] <= ALPHA
        per_pair += sum(r["p_holm"] <= ALPHA for r in out["pairs"].values()) / len(pairs)
    return dict(pooled=pooled / reps, mean_pair_holm=per_pair / reps)


def true_effect(pairs, scenarios):
    return sum(score(left[s]) - score(right[s]) for left, right in pairs for s in scenarios) / (len(pairs) * len(scenarios))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", required=True)
    parser.add_argument("--reps", type=int, default=2000)
    args = parser.parse_args(argv)
    rows = []
    for pseudo in (0.0, 0.1, 0.5):
        dist = history(pseudo)
        scenarios = sorted(dist["opus-5.5-medium"])
        rare = {s: {"release": .9, "mix": .05, "tracking": .03, "policy": .02, MISS: 0.0} for s in scenarios}
        one_varying = {s: {"release": 1.0, "mix": 0.0, "tracking": 0.0, "policy": 0.0, MISS: 0.0} for s in scenarios}
        one_varying[scenarios[0]] = {"release": .85, "mix": 0.0, "tracking": 0.0, "policy": .15, MISS: 0.0}
        rng = random.Random(f"relabel-{pseudo}")
        relabeled = {cond: {s: relabel(p, rng) for s, p in by_s.items()} for cond, by_s in dist.items()}
        designs = []
        for family, pairs, alternative in (("effort", EFFORT, "less"), ("meta", META, "two-sided")):
            designs += [
                (family, "null: identical", [(dist[r], dist[r]) for _, r in pairs], alternative),
                (family, "null: equal score, other favorite cause", [(relabeled[r], dist[r]) for _, r in pairs], alternative),
                (family, "null: rare labels, relabeled", [({s: relabel(rare[s], rng) for s in scenarios}, rare) for _ in pairs], alternative),
                (family, "null: one varying scenario", [(one_varying, one_varying) for _ in pairs], alternative),
            ]
            for share in (0.5, 0.75, 1.0):
                shrunk = [(shrink(dist[lft], dist[r], scenarios, share), dist[r]) for lft, r in pairs]
                designs.append((family, f"effect: {share:.0%} of history", shrunk, alternative))
        for family, name, pairs, alternative in designs:
            for variance in coverage_test.VARIANCES:
                for variants in (4, 8, 12):
                    seed = int(hashlib.sha256(f"{pseudo}|{family}|{name}|{variants}".encode()).hexdigest()[:12], 16)
                    rates = simulate(pairs, scenarios, variants, alternative, variance, args.reps, seed)
                    rows.append(dict(pseudo_count=pseudo, family=family, design=name, variance=variance,
                                     cases=variants * len(scenarios),
                                     true_effect=round(true_effect(pairs, scenarios), 4), **rates))
                    print(json.dumps(rows[-1]), flush=True)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "confirmation-power.json").write_text(json.dumps(dict(
        reps=args.reps, alpha=ALPHA, k=K, repeats=REPEATS, history=HISTORY,
        source_sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                       for name in ("validate_confirmation.py", "coverage_test.py")},
        rows=rows), indent=1) + "\n")
    lines = ["# Round-005 calibration and power", "", "<!-- Generated by validate_confirmation.py. Do not edit by hand. -->", "",
             f"{args.reps:,} simulated rounds per row, alpha {ALPHA} per family, k={K}, {REPEATS} runs per cell. "
             "Pooled is the primary family test. Mean pair (Holm) is the average share of pairs rejected after Holm adjustment. "
             "Null rows should reject at or below alpha. Truth smoothing is the pseudo count added to each cause "
             "of the simulated truth; it is separate from the smoothed variance estimate's own pseudo count.", "",
             "| Truth smoothing | Family | Design | Variance | Cases | True effect | Pooled rejection | Mean pair rejection (Holm) |",
             "|---:|---|---|---|---:|---:|---:|---:|"]
    for r in rows:
        lines.append(f"| {r['pseudo_count']} | {r['family']} | {r['design']} | {r['variance']} | {r['cases']} | {r['true_effect']:+.4f} | "
                     f"{r['pooled']:.3f} | {r['mean_pair_holm']:.3f} |")
    (out / "CONFIRMATION_POWER.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
