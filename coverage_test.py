"""Tests of the no-evidence pass@k score difference itself.

The paired permutation test asks whether two conditions have the same label
distribution on each prompt. That is stronger than equal scores: two models
that each favor a different single cause have equal coverage but differ in
distribution. This module tests the score functional directly.

For a cell (one condition on one case) with n = 2k independent runs, the
reported estimate U is the unbiased pass@k U-statistic: the mean, over every
k-run subset, of the number of distinct causes in the subset divided by four.
Split the runs into two disjoint k-run batches with batch scores b1 and b2.
Both variance estimates below average exactly over every split, using the
cell's label counts, so neither depends on an arbitrary split.

- unbiased: the two batches are disjoint, so E[b1 * b2] is the squared true
  score, and U^2 - mean(b1 * b2) is unbiased for Var(U). A single cell can be
  negative; sums over many cells are what the test uses.
- bound: U is the average of the two-batch mean over all splits, so
  Var(U) <= Var(h) / 2, and mean((b1 - b2)^2) / 4 is unbiased for that bound.
- smoothed: the exact variance of U under a multinomial whose cause
  probabilities are the observed counts plus a pseudo count. Split-based
  estimates are zero whenever a cell shows one cause, and nearly zero when a
  rare cause appears a few times, so they understate uncertainty when only a
  few cells vary. Smoothing keeps unseen causes possible.

Cells are independent, so a difference of equal-case means has an estimated
variance equal to the sum of its cell variances over the squared number of
terms. If an unbiased sum is not positive, the bound is used instead. The z
statistic uses the normal reference. validate_confirmation.py checks
calibration by simulation, including equal-score nulls in which the two
conditions favor different causes, rare labels, and a single varying scenario.
The bound is the default: it held the nominal rate in every simulated null,
while the unbiased estimate rejected too often with few varying cells and the
smoothed estimate was too conservative to detect plausible effects.
"""

import math
from functools import lru_cache
from statistics import NormalDist

from permutation import holm

METHOD = "equal-case mean pass@k difference, exact split-batch variance, normal reference"
ALTERNATIVES = ("two-sided", "less", "greater")
VARIANCES = ("smoothed", "unbiased", "bound")
PSEUDO = 0.5


@lru_cache(maxsize=None)
def cell_stats(cause_counts, misses, k):
    """(U, unbiased variance, variance bound) for one cell. cause_counts is a sorted
    tuple of the four cause counts; misses counts invalid, other and multiple labels."""
    n = sum(cause_counts) + misses
    if n != 2 * k:
        raise ValueError(f"Split-batch variance needs exactly {2 * k} runs per cell, got {n}")
    total = math.comb(n, k)
    u = sum(1 - math.comb(n - c, k) / total for c in cause_counts) / len(cause_counts)
    squared = product = 0.0

    def walk(i, taken, weight, seen_first, seen_second):
        nonlocal squared, product
        if i == len(cause_counts):
            rest = k - taken
            if 0 <= rest <= misses:
                w = weight * math.comb(misses, rest)
                b1, b2 = seen_first / len(cause_counts), seen_second / len(cause_counts)
                squared += w * (b1 - b2) ** 2
                product += w * b1 * b2
            return
        c = cause_counts[i]
        for x in range(0, min(c, k - taken) + 1):
            walk(i + 1, taken + x, weight * math.comb(c, x),
                 seen_first + (x > 0), seen_second + (c - x > 0))

    walk(0, 0, 1, 0, 0)
    return u, u * u - product / total, squared / total / 4


@lru_cache(maxsize=None)
def smoothed_variance(cause_counts, misses, k, pseudo):
    """Exact Var(U) when cause probabilities are (count + pseudo) / (n + 4 * pseudo)."""
    n = sum(cause_counts) + misses
    total = n + pseudo * len(cause_counts)
    probs = [(c + pseudo) / total for c in cause_counts]
    missed = [math.comb(n - c, k) / math.comb(n, k) for c in range(n + 1)]

    def binomial(q):
        return [math.comb(n, c) * q ** c * (1 - q) ** (n - c) for c in range(n + 1)]

    marginals = [binomial(q) for q in probs]
    means = [sum(b[c] * missed[c] for c in range(n + 1)) for b in marginals]
    variance = 0.0
    for i, qi in enumerate(probs):
        for j, qj in enumerate(probs):
            if i == j:
                joint = sum(marginals[i][c] * missed[c] ** 2 for c in range(n + 1))
            else:
                rest = max(0.0, 1 - qi - qj)
                joint = sum(
                    math.comb(n, a) * math.comb(n - a, b) * qi ** a * qj ** b * rest ** (n - a - b) * missed[a] * missed[b]
                    for a in range(n + 1) for b in range(n - a + 1)
                )
            variance += joint - means[i] * means[j]
    return variance / len(probs) ** 2


def cell_from_labels(labels, causes, k):
    counts = tuple(sorted(labels.count(c) for c in causes))
    return cell_stats(counts, len(labels) - sum(counts), k)


def _p_value(z, alternative):
    normal = NormalDist()
    if alternative == "less":
        return normal.cdf(z)
    if alternative == "greater":
        return 1 - normal.cdf(z)
    return 2 * (1 - normal.cdf(abs(z)))


def _z(estimate, variance):
    if variance > 0:
        return estimate / math.sqrt(variance)
    return 0.0 if estimate == 0 else math.copysign(math.inf, estimate)


def _variance(smoothed, unbiased, bound, choice):
    if choice == "smoothed":
        return smoothed
    return unbiased if choice == "unbiased" and unbiased > 0 else bound


def _cell(labels, causes, k, pseudo):
    counts = tuple(sorted(labels.count(c) for c in causes))
    u, unbiased, bound = cell_stats(counts, len(labels) - sum(counts), k)
    return u, smoothed_variance(counts, len(labels) - sum(counts), k, pseudo), unbiased, bound


def family_test(pairs, causes, k, alternative, variance="bound", pseudo=PSEUDO):
    """pairs maps pair ID to a list of (left labels, right labels) per case."""
    if alternative not in ALTERNATIVES:
        raise ValueError(f"alternative must be one of {ALTERNATIVES}")
    if variance not in VARIANCES:
        raise ValueError(f"variance must be one of {VARIANCES}")
    if not pairs or any(not cases for cases in pairs.values()):
        raise ValueError("Score test needs at least one case per pair")
    results, sums = {}, {}
    for pid, cases in pairs.items():
        estimate, totals, varying = 0.0, [0.0, 0.0, 0.0], 0
        for left, right in cases:
            (ul, *vl), (ur, *vr) = _cell(left, causes, k, pseudo), _cell(right, causes, k, pseudo)
            estimate += ul - ur
            totals = [t + a + b for t, a, b in zip(totals, vl, vr)]
            varying += (vl[2] > 0) + (vr[2] > 0)
        n = len(cases)
        sums[pid] = tuple(t / n ** 2 for t in totals)
        estimate /= n
        v = _variance(*sums[pid], variance)
        z = _z(estimate, v)
        results[pid] = dict(statistic=estimate, se=math.sqrt(v), z=z, p=_p_value(z, alternative),
                            varying_cells=varying, cells=2 * n)
    adjusted = holm([r["p"] for r in results.values()])
    for r, p in zip(results.values(), adjusted):
        r["p_holm"] = p
    estimate = sum(r["statistic"] for r in results.values()) / len(results)
    v = _variance(*(sum(x[i] for x in sums.values()) / len(results) ** 2 for i in range(3)), variance)
    z = _z(estimate, v)
    return dict(method=METHOD, variance=variance, pseudo=pseudo if variance == "smoothed" else None,
                alternative=alternative, pairs=results,
                pooled=dict(pairs=list(results), statistic=estimate, se=math.sqrt(v), z=z,
                            p=_p_value(z, alternative)))
