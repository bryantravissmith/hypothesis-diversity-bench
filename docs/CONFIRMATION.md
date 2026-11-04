# Effort confirmation plan

## Version 3: round-005 Opus and round-006 GPT

**Outcome: not completed.** Round-006 started at 06:01 PT on November 4 while
the subscription limit was still in effect. It recorded 36 usage-limit
attempts and no answers, and was cancelled at 06:52 PT for diminishing
returns. The preregistered pooled test was never run, so the effort claim is
unconfirmed. The only fresh-prompt effort result is the complete round-005
Opus pair below, which shows no drop.

Committed before any round-006 inference and before any round-005 result was
exported or analyzed. The machine-readable version is
[confirmation-v3-spec.json](../confirmation-v3-spec.json).

**What happened to round-005.** The ChatGPT subscription reached its usage
limit at 01:19 PT on November 4, after about 9.5 hours. From then on every GPT
call failed within seconds, and the runner counted those failures toward its
three-attempt retry bound. 856 GPT jobs (99-115 per condition) ended as
retry-exhausted misses before the run was stopped by hand; about 480 more
never ran. Under the frozen rules those misses cannot be replaced, so the GPT
conditions would carry an outage-driven loss of about 11% of their answers.
Both Opus conditions had finished all 960 planned jobs each with valid
answers before the limit.

**What changes.**

- **Opus pair kept.** The confirmatory pairs compare max with medium for the
  same model within one round, so the finished Opus pair still answers its
  question. The analysis requires every listed round-005 condition to be
  complete; the stopped GPT conditions are reported descriptively only.
- **GPT reruns as round-006.** Same eight GPT conditions, 48 new no-evidence
  prompts (seed `4e53d8dbf4871807`), 20 runs per cell, 7,680 jobs. No prompt
  repeats rounds 001-005.
- **Order and dates both balanced.** Earlier rounds tied each cause type to a
  date: mix October 12, release and tracking October 13, policy October 14,
  with the drop "in the week of October 13". Round-006 reassigns that set of
  dates across causes from Latin squares, so each cause carries each date slot
  equally often, independently of its list position. Round-005 Opus prompts
  keep the fixed dates; each pair is still compared on identical prompts.
- **Usage limits pause instead of failing.** A subscription usage-limit
  response is recorded with outcome `quota`, pauses that service for 10
  minutes before the next probe, and does not use up the retry bound.
- **Decision rules unchanged.** The pooled one-sided score test over the three
  plain max-minus-medium pairs is the single confirmatory test at alpha 0.05;
  per-model claims are gated and Holm-adjusted; the meta family is
  exploratory. The pooled average now combines effects measured on two prompt
  sets from the same six scenarios. The power analysis below still applies,
  since every pair keeps 48 cases and 20 runs.

**Round-005 as published.** [results/round-005](../results/round-005) is the
stopped export: 10,835 attempts, every one kept. Both Opus conditions are
complete and keep confidence bounds; the eight GPT conditions are stopped and
descriptive only. Blinded review resolved 76 ambiguous answers (68 rental
answers naming the analytics migration, which the pattern missed, were
labeled tracking).

Opus effort result, computed after this plan was committed. It was planned as
one input to the pooled test, which was not run, so it stands as a secondary
result without the gating that plan gave it:

| Opus 5.5, no evidence | Medium | Max |
|---|---:|---:|
| pass@10 | 0.308 [0.192, 0.443] | 0.318 [0.200, 0.454] |
| Effective causes | 1.18 | 1.19 |
| Tracking answers | 750 / 960 | 827 / 960 |

Max minus medium is +0.010. One-sided score test for a drop: raw p 0.905,
Holm p 0.905 (23 of 96 cells vary). Two-sided distribution test: raw p 0.331,
Holm p 0.331. The -0.083 Opus drop seen on the six round 001-004 prompts does
not appear on 48 new prompts.

**Limits added by this change.** The Opus effect is measured on fixed-date
prompts and the GPT effects on date-balanced prompts, about a day apart. A
timing cue could change how strongly effort concentrates answers, so a
difference between Opus and GPT effects is not interpretable as a model
difference alone. Release wording still mentions a random holdout in every
scenario, which makes release the easiest cause to check.

```sh
uv run bench.py plan runs/round-006 --tiers none --none-variants 8 --randomize-dates \
  --max-hours 72 --novel-against results/round-001/plan.json results/round-002/plan.json \
  results/round-003/plan.json results/round-004/plan.json runs/round-005/plan.json \
  --conditions gpt-6.1-terra-medium gpt-6.1-terra-max gpt-6.1-terra-medium-meta \
  gpt-6.1-terra-max-meta gpt-6-astra-medium gpt-6-astra-max gpt-6-astra-medium-meta \
  gpt-6-astra-max-meta
uv run bench.py run runs/round-006
uv run bench.py export runs/round-005 results/round-005
uv run bench.py export runs/round-006 results/round-006
uv run grade.py review results/round-005   # and round-006
uv run grade.py labels results/round-005   # and round-006
uv run analyze.py results/round-005 results/round-006 --output results/confirmation \
  --comparisons confirmation-v3-spec.json
```

## Version 2: round-005 (stopped)

Written and committed before any round-005 inference. The machine-readable
version is [confirmation-spec.json](../confirmation-spec.json). Version 1 of
this plan was frozen but never run; an external review (GPT-6 Astra, xhigh
effort) found that its primary test targeted the wrong null, that two of its
"new" prompts had already been run, and that multiplicity and completeness
rules were loose. Version 2 fixes those.

### Why

Rounds 001-004 show max effort usually lowering no-evidence diversity. The
conservative fixed-prompt bounds include zero for every comparison. Paired
tests on the same data find a pooled drop, but those comparisons were chosen
after seeing the results, cover six prompts, and several rest on one or two
prompts. Round-005 tests the effort claim on fresh prompts and samples.

### Design

- **Cases:** no-evidence tier only, 48 prompts (8 per scenario), seed
  `0b919a6c8400878d`. Each scenario's change orders come from two Latin
  squares, so every change appears in every list position twice. Planning
  checks every prompt against rounds 001-004; none has been run before.
- **Conditions (10):** Opus 5.5, GPT-6.1 Terra and GPT-6 Astra at medium and
  max effort, plus Terra and Astra with the meta prompt. One shuffled schedule,
  so every comparison is within round. Opus with the meta prompt is left out:
  at max effort it would add about 13 hours for an exploratory estimate.
- **Repeats:** 20 per case and condition, 9,600 jobs.
- **Execution:** each job is a fresh CLI session with no tools. Claude Code
  uses the Claude subscription; Pi uses the ChatGPT subscription. Operational
  failures retry up to the frozen bound with a fresh session; completed
  invalid answers are final. Every attempt, model ID and timestamp is kept.

### Decision rules

1. **Confirmatory (effort).** Plain max minus medium no-evidence pass@10,
   averaged equally over the three models, by the one-sided
   [score test](INTERVALS.md#paired-tests) with the conservative variance.
   Confirmed if p <= 0.05.
2. **Per model (secondary).** Claimed only if rule 1 passes and the
   Holm-adjusted one-sided p is <= 0.05. Raw and Holm p are reported together.
3. **Meta prompt (exploratory).** Four within-round pairs, two-sided, reported
   with raw and Holm p. No confirmatory claim.
4. **Distribution test.** Reported for every pair. A rejection means the
   answer distributions differ, not that scores differ.
5. **Completeness.** Every planned job counts and invalid answers are misses.
   Unless all 10 conditions, 48 cases and 20 runs per cell are present, every
   test is withheld. An aborted round is published as stopped, and this
   specification is not reused for a rerun.

### Power

The [simulation report](../results/validation/CONFIRMATION_POWER.md) uses
the observed distributions from rounds 001-004 and shrinks the historical
effect, since effects chosen after seeing data are biased upward. With the
conservative variance at alpha 0.05, the worst simulated null rejected 5.5% of
the time (2,000 rounds per row, Monte Carlo SE about 0.5 points).

Power depends mostly on "truth smoothing": how much probability a model keeps
for causes it was never seen giving in 20 runs. Pooled effort power with 48
cases:

| Truth smoothing | 50% of historical effect | 75% | 100% |
|---|---:|---:|---:|
| 0 (unseen causes absent) | 0.99 | 1.00 | 1.00 |
| 0.1 | 0.59 | 0.90 | 0.99 |
| 0.5 (every cause about 2% or more) | 0.21 | 0.37 | 0.58 |

The meta-prompt effect, about -0.01 to -0.016, is too small to confirm
reliably unless unseen causes are truly absent, which is why it is
exploratory. This section was completed after inference started; the
decision rules above were committed before it.

### Limits

The tests condition on the frozen prompts. Eight orderings of six scenarios
widen the prompt set but do not sample new business situations, so a result
covers these scenarios, not analytics questions at large. The score treats
the four causes as equally likely; it measures coverage, not reasoning
quality.

### Commands

```sh
uv run bench.py plan runs/round-005 --seed 0b919a6c8400878d --tiers none \
  --none-variants 8 --max-hours 24 \
  --novel-against results/round-001/plan.json results/round-002/plan.json \
  results/round-003/plan.json results/round-004/plan.json --conditions \
  opus-5.5-medium opus-5.5-max gpt-6.1-terra-medium gpt-6.1-terra-max \
  gpt-6.1-terra-medium-meta gpt-6.1-terra-max-meta \
  gpt-6-astra-medium gpt-6-astra-max gpt-6-astra-medium-meta gpt-6-astra-max-meta
uv run bench.py run runs/round-005
uv run bench.py export runs/round-005 results/round-005
uv run grade.py review results/round-005
uv run grade.py labels results/round-005
uv run analyze.py results/round-005 --output results/confirmation \
  --comparisons confirmation-spec.json
```
