# Confidence intervals and scenario sensitivity

The analysis reports conservative confidence intervals for pass@k, direct
condition comparisons and sensitivity to the six scenario templates.

## Target and assumptions

The estimand is the equal-case mean score of k fresh planned-job outcomes
on the **same fixed prompts, condition, runner, retry policy and grading
rules**. Planned-job outcomes include invalid answers as misses. Retries are
part of their planned job.

The guarantee requires:

- Independent outcomes across planned jobs, including across cases.
- An identical response distribution across repetitions within each case
  and condition. Different cases may have different distributions.
- A fixed number of cases and repetitions and fixed scoring rules. The
  present formula requires equal repetitions within a tier.

Shared outages, provider drift, correlated retries or adaptive stopping can
violate these assumptions. The target is conditional on the chosen prompts,
runner and grading rules; label uncertainty and population-level scenario
variation are outside these CIs.

Incomplete rounds retain descriptive missing-as-miss estimates, but no
inferential bounds. Unreviewed ambiguous labels also suppress bounds.
Malformed schedules, stale saved labels and incompatible case records fail
validation. Round identity is retained throughout.

## Bounded U-statistic construction

Let m be the number of cases, n the planned repetitions per case, and k no
larger than n. For case i, let h_i be the symmetric score of a k-answer
subset. Partial/full scores are 1 if the assigned true cause appears and
0 otherwise. No-evidence scores are the number of distinct scored categories
divided by four. Invalid, other and multiple labels contribute no category.

The estimator U_i averages h_i over every k-subset. For a truth
category appearing c times, its component is `1 - C(n-c,k)/C(n,k)`.
The reported estimate is `s = sum_i U_i / m`.

Use the known range `[0,R]`, with `R=1` for partial/full and
`R=min(k,4)/4` for no evidence. Write `Z=s/R`, and let theta be its
expectation. Define `r=floor(n/k)` and `B=m*r`.

Here is the finite-sample argument for this stratified application:

1. For each case, permute its n observations uniformly and form r disjoint
   k-blocks. Discard any remainder for this representation only. Each block
   is a uniformly selected k-subset, so the expectation over permutations
   of its normalized average score equals U_i/R. Every observation remains
   in the actual estimator.
2. Across all m cases, let V be the average of these B block scores. Conditional
   on the observed answers, `E_permutations[V]=Z`. Convexity gives
   `exp(t B Z) <= E_permutations[exp(t B V)]` for every real t.
3. For any fixed permutations, the B block scores are independent, bounded
   in `[0,1]`. Each case's r blocks have the same expectation theta_i.
   For a bounded score Y with mean a, the chord bound is
   `E[exp(tY)] <= 1-a+a*exp(t)`, also for negative t.
4. Multiplication of these moment bounds and concavity of
   `log(1-a+a*exp(t))` in a give
   `E[exp(t B Z)] <= (1-theta+theta*exp(t))^B`, where
   `theta=sum_i theta_i/m`. This step allows heterogeneous cases.
5. Chernoff optimization yields
   `P(Z >= z) <= exp(-B*KL(z||theta))` for `z>theta`, with the analogous
   lower-tail inequality for `z<theta`. Invert the two tails, allocating
   alpha/2 to each, to obtain coverage at least `1-alpha`.

The normalized interval consists of all q satisfying
`B*KL(Z||q) <= log(2/alpha)`, where
`KL(z||q)=z*log(z/q)+(1-z)*log((1-z)/(1-q))` with endpoint limits.
Multiply both endpoints by R. B is the concentration parameter from the
disjoint-block representation above.

The general bounded-U-statistic argument is grounded in
[Hoeffding (1963)](https://doi.org/10.1080/01621459.1963.10500830);
[Andrews et al., Appendix Q](https://economics.mit.edu/sites/default/files/inline-files/Theory_Transfer_07.24.pdf)
also describes inverting U-statistic concentration bounds. The equal-weight
stratified derivation above specifies the application used here.

## Numerical behavior

The standard-library implementation uses logarithms and `log1p` for KL,
`expm1` for the zero endpoint, and 64 bisection iterations with outward
bracketing for interior values. At normalized score 1 the lower endpoint is
`exp(-log(2/alpha)/B)`; at score 0 the upper endpoint is
`-expm1(-log(2/alpha)/B)`.

For pointwise 95% coverage at the current repeat counts:

| Observation | Estimate | Fixed-prompt bound |
| --- | ---: | --- |
| Full pass@1, 48/48 correct, 24 cases | 1.000 | [0.926, 1.000] |
| Full pass@2, same data | 1.000 | [0.858, 1.000] |
| None pass@10, same category 20/20 within each of 6 cases | 0.250 | [0.034, 0.635] |
| None pass@1, every answer names one scored category | 0.250 | [0.242, 0.250] |

The last line includes invalid-answer uncertainty in the planned-job target.
Conditional on every answer naming a scored category, no-evidence pass@1
equals 0.25.

Intervals may widen as k rises because B decreases. These conservative
marginal bounds are particularly broad at k=10 with 20 repeats.

## Scenario sensitivity and contrasts

Case variants are grouped by their scenario template. The analysis reports
each scenario's score, every leave-one-scenario-out mean, and a cluster
bootstrap percentile range. For six scenarios, count-vector enumeration
with multinomial multiplicities evaluates all `6^6=46,656` ordered resamples
exactly. Each selected scenario contributes all of its cases with their
original case weights. Quantiles use the inverse empirical CDF. A seeded
10,000-resample fallback exists only for larger numbers of scenarios.

These ranges describe sensitivity to the observed templates and are reported
separately from the fixed-prompt CIs. They have zero width when all scenario
scores agree.

[analysis-spec.json](../analysis-spec.json) declares two exploratory families:
six plain-prompt max-minus-medium no-evidence pass@10 contrasts, and six
meta-minus-plain contrasts at the same metric. The comparison families were
selected after observing the results.

Within each family, alpha=0.05 is divided among its 12 unique endpoint
conditions. Difference bounds are `[L_left-U_right, U_left-L_right]`.
By the union bound, all configured differences within that family are covered
simultaneously with probability at least 95%, under the assumptions above.
This projection permits dependence between contrast endpoints. Each endpoint's
fixed-prompt assumptions still apply. Missing endpoints retain their allocated
error budget. Simultaneous coverage applies within each family; individual
score intervals across k and tiers are pointwise.

Scenario differences use the same case and scenario on each side and the
same scenario multiplicities. Explicit round IDs identify both endpoints. Astra
plain/meta conditions were in round 004; Sol/Terra meta contrasts cross
rounds and may include collection-time effects.

## Validation and reproduction

The [validation report](../results/validation/VALIDATION.md) evaluates coverage
on 10,000 complete synthetic experiments per setting. Settings include exact
boundaries, rare alternatives, heterogeneous
case distributions, invalid responses, actual 20/5/2 repeats, non-divisible
n/k, six-contrast families, and deliberately invalid dependence/drift cases.
Monte Carlo frequency intervals and widths are reported. The tests also
enumerate exact coverage in small models, verify numerical limits, and
exercise the CLI on incomplete, malformed and mismatched inputs.

## Paired permutation tests

A contrast family may also declare a `test` in its spec. The
[paired permutation test](../permutation.py) asks whether two conditions draw
labels from the same distribution on each fixed prompt. Each draw shuffles the
pooled labels of every case between the two conditions, keeping group sizes,
and recomputes the equal-case mean pass@k difference. Unlike the difference
bounds above, it pairs conditions on the same cases and uses every answer, so
it has far more power, but it only tests equality on these prompts.

Only within-round pairs are tested, because exchangeability fails when
collection time differs. The family's pooled statistic is the mean of its pair
statistics, permuted jointly. Pair p-values are Holm-adjusted within the
family. With 20,000 draws, the Monte Carlo p-value
`(1 + extreme draws) / (1 + draws)` has standard error about 0.0015 near
0.05. Seeds derive from family IDs, so outputs are reproducible.

```sh
uv run --no-project python -m unittest discover -s tests -v
uv run validate_intervals.py --output results/validation
uv run analyze.py results/round-001 results/round-002 results/round-003 results/round-004 \
  --figures results/figures --comparisons analysis-spec.json --report RESULTS.md
```

Analysis uses only the Python standard library; simulations additionally use
Python 3.12 and the pinned NumPy dependency in their script header. JSON outputs record
Python/NumPy versions, method, alpha, counts, inference availability and
source/input SHA-256 hashes. `--figures` writes each round's summary alongside
its inputs, plus combined results in the selected directory. `--output`
writes all analysis artifacts to a separate directory.
