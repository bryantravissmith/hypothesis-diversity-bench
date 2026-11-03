"""Conservative fixed-prompt bounds and exploratory scenario sensitivity.

The KL bound is for independent job outcomes, identically distributed within
each fixed case. Cases may have different distributions. See docs/INTERVALS.md
for the stratified U-statistic derivation, scope and multiplicity policy.
"""

import functools
import math
import random

METHOD = "stratified-bounded-ustat-kl-v1"
CONFIDENCE = 0.95


def bernoulli_kl(observed, candidate):
    """KL(Bernoulli(observed) || Bernoulli(candidate)), including endpoints."""
    if not (0 <= observed <= 1 and 0 <= candidate <= 1):
        raise ValueError("Probabilities must be finite and in [0, 1]")
    if observed == candidate:
        return 0.0
    if candidate in (0.0, 1.0):
        return math.inf
    if observed == 0:
        return -math.log1p(-candidate)
    if observed == 1:
        return -math.log(candidate)
    value = observed * (math.log(observed) - math.log(candidate))
    value += (1 - observed) * (
        math.log1p(-observed) - math.log1p(-candidate)
    )
    return max(0.0, value)  # Roundoff close to the minimum can be negative.


@functools.lru_cache(maxsize=32768)
def kl_bounds(observed, blocks, alpha=0.05):
    """Invert both Chernoff tails, allocating alpha/2 to each tail."""
    if not 0 <= observed <= 1 or not math.isfinite(observed):
        raise ValueError("Observed mean must be in [0, 1]")
    if not isinstance(blocks, int) or isinstance(blocks, bool) or blocks < 1:
        raise ValueError("The block parameter must be a positive integer")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)")
    threshold = math.log(2 / alpha) / blocks
    if observed == 0:
        return 0.0, -math.expm1(-threshold)
    if observed == 1:
        return math.exp(-threshold), 1.0
    left, right = 0.0, observed
    for _ in range(64):
        middle = (left + right) / 2
        if bernoulli_kl(observed, middle) > threshold:
            left = middle
        else:
            right = middle
    low = left  # Outward rounding preserves a conservative bracket.
    left, right = observed, 1.0
    for _ in range(64):
        middle = (left + right) / 2
        if bernoulli_kl(observed, middle) > threshold:
            right = middle
        else:
            left = middle
    return low, right


def fixed_prompt_interval(mean, cases, repeats, k, tier, alpha=0.05):
    """Equal-case-weight bound with equal repeats, as in the frozen rounds."""
    for value in (cases, repeats, k):
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("Cases, repeats and k must be positive integers")
    if k > repeats:
        raise ValueError("k exceeds repeats")
    if tier not in ("none", "partial", "full"):
        raise ValueError(f"Unknown tier: {tier}")
    scale = min(k, 4) / 4 if tier == "none" else 1.0
    if not math.isfinite(mean) or not -1e-12 <= mean <= scale + 1e-12:
        raise ValueError("Mean exceeds the known score range")
    blocks = cases * (repeats // k)
    low, high = kl_bounds(min(1.0, max(0.0, mean / scale)), blocks, alpha)
    return dict(
        low=low * scale, high=high * scale, method=METHOD,
        confidence=1 - alpha, alpha=alpha, block_parameter=blocks,
        score_range=[0.0, scale], cases=cases, repeats=repeats, k=k,
    )


@functools.lru_cache(maxsize=16)
def _resample_counts(n):
    """Count vectors and multiplicities for all n**n ordered resamples."""
    def compositions(total, length, prefix=()):
        if length == 1:
            yield prefix + (total,)
        else:
            for count in range(total + 1):
                yield from compositions(total - count, length - 1, prefix + (count,))

    return tuple(
        (counts, math.factorial(n) // math.prod(math.factorial(c) for c in counts))
        for counts in compositions(n, n)
    )


def scenario_sensitivity(groups, seed="scenario-sensitivity-v1"):
    """Resample whole scenarios, preserving case weights within each cluster.

    These percentiles describe the observed clusters only. They are not the
    fixed-prompt CI and are not calibrated claims about a new task population.
    """
    if not groups or any(not values for values in groups.values()):
        raise ValueError("At least one nonempty scenario is required")
    names = sorted(groups)
    totals = [math.fsum(groups[name]) for name in names]
    sizes = [len(groups[name]) for name in names]
    if not all(math.isfinite(x) for x in totals):
        raise ValueError("Scenario scores must be finite")
    n = len(names)
    total, size = math.fsum(totals), sum(sizes)
    scenarios = {
        name: dict(cases=count, mean=value / count)
        for name, value, count in zip(names, totals, sizes)
    }
    leave_out = {
        name: (total - value) / (size - count)
        for name, value, count in zip(names, totals, sizes) if size > count
    }
    if n ** n <= 1_000_000:
        distribution = sorted(
            (math.fsum(c * v for c, v in zip(counts, totals))
             / sum(c * v for c, v in zip(counts, sizes)), weight)
            for counts, weight in _resample_counts(n)
        )
        samples, method = n ** n, "exact-ordered-scenario-bootstrap"
    else:
        rng = random.Random(seed)
        distribution = []
        for _ in range(10000):
            indices = [rng.randrange(n) for _ in names]
            distribution.append((
                math.fsum(totals[i] for i in indices) / sum(sizes[i] for i in indices), 1,
            ))
        distribution.sort()
        samples, method = 10000, "seeded-scenario-bootstrap"

    def quantile(fraction):
        rank = max(1, math.ceil(fraction * samples))
        cumulative = 0
        for value, weight in distribution:
            cumulative += weight
            if cumulative >= rank:
                return value
        raise AssertionError("Incomplete resampling distribution")

    return dict(
        interpretation="exploratory sensitivity to observed scenarios; not a population CI",
        mean=total / size, scenarios=scenarios, scenario_count=n,
        leave_one_out=leave_out,
        leave_one_out_range=[min(leave_out.values()), max(leave_out.values())] if leave_out else None,
        bootstrap=dict(method=method, resamples=samples, seed=seed if samples == 10000 and n ** n > 1_000_000 else None,
                       percentile_mass=0.95, low=quantile(0.025), high=quantile(0.975)),
    )
