"""Paired within-case permutation tests for configured contrasts.

The null hypothesis is that, on each fixed prompt, the two conditions draw
labels from the same distribution. Under that null the labels of one case are
exchangeable between the two conditions, so each permutation shuffles the
pooled labels of every case and splits them back into the original group
sizes. The statistic is the equal-case mean of the per-case score difference,
left minus right, using the same pass@k score as the report. Invalid answers
stay in the pools and score as misses.

Exchangeability needs both conditions collected together. Pairs from
different rounds are not tested, because collection time is confounded with
condition. A family's pooled statistic is the mean of its tested pair
statistics, permuted jointly with them, so it answers one question: is the
average effect across the family different from zero? Pair p-values are
Holm-adjusted within the family.

p-values are Monte Carlo estimates, (1 + extreme draws) / (1 + draws), from a
seed derived from the family ID, so regenerated reports are identical.
"""

import hashlib
import math
import random

METHOD = "paired within-case label permutation, Monte Carlo, Holm within family"
ALTERNATIVES = ("two-sided", "less", "greater")
TOLERANCE = 1e-12


def pass_table(n, k):
    """pass@k for every possible hit count c out of n."""
    return [1.0 if n - c < k else 1 - math.comb(n - c, k) / math.comb(n, k) for c in range(n + 1)]


def case_score(labels, truth, causes, table):
    if truth is None:
        return sum(table[labels.count(c)] for c in causes) / len(causes)
    return table[labels.count(truth)]


def _extreme(simulated, observed, alternative):
    if alternative == "less":
        return simulated <= observed + TOLERANCE
    if alternative == "greater":
        return simulated >= observed - TOLERANCE
    return abs(simulated) >= abs(observed) - TOLERANCE


def holm(p_values):
    """Holm step-down adjusted p-values, in input order."""
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    adjusted, running = [0.0] * len(p_values), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(p_values) - rank) * p_values[i]))
        adjusted[i] = running
    return adjusted


def family_test(pairs, causes, k, alternative, permutations, seed):
    """pairs maps pair ID to a list of (left labels, right labels, truth) per case."""
    if alternative not in ALTERNATIVES:
        raise ValueError(f"alternative must be one of {ALTERNATIVES}")
    if permutations < 1 or not pairs or any(not cases for cases in pairs.values()):
        raise ValueError("Permutation test needs draws and at least one case per pair")
    if any(len(left) < k or len(right) < k for cases in pairs.values() for left, right, _ in cases):
        raise ValueError("k exceeds trials")
    rng = random.Random(int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big"))
    tables = {}

    def statistic(cases):
        total = 0.0
        for left, right, truth in cases:
            for side in (len(left), len(right)):
                if side not in tables:
                    tables[side] = pass_table(side, k)
            total += (case_score(left, truth, causes, tables[len(left)])
                      - case_score(right, truth, causes, tables[len(right)]))
        return total / len(cases)

    ids = list(pairs)
    observed = {pid: statistic(pairs[pid]) for pid in ids}
    pooled = sum(observed.values()) / len(ids)
    pools = {pid: [(list(left) + list(right), len(left), truth) for left, right, truth in pairs[pid]] for pid in ids}
    hits = dict.fromkeys(ids, 0)
    pooled_hits = 0
    for _ in range(permutations):
        draws = {}
        for pid in ids:
            shuffled = []
            for pool, size, truth in pools[pid]:
                rng.shuffle(pool)
                shuffled.append((pool[:size], pool[size:], truth))
            draws[pid] = statistic(shuffled)
            hits[pid] += _extreme(draws[pid], observed[pid], alternative)
        pooled_hits += _extreme(sum(draws.values()) / len(ids), pooled, alternative)
    raw = [(hits[pid] + 1) / (permutations + 1) for pid in ids]
    adjusted = holm(raw)
    return dict(
        method=METHOD, alternative=alternative, permutations=permutations, seed=seed,
        pairs={pid: dict(statistic=observed[pid], p=raw[i], p_holm=adjusted[i]) for i, pid in enumerate(ids)},
        pooled=dict(pairs=ids, statistic=pooled, p=(pooled_hits + 1) / (permutations + 1)),
    )
