# Round-005 confirmation plan

Written and committed before any round-005 inference. The machine-readable
version is [confirmation-spec.json](../confirmation-spec.json).

## Why

Rounds 001-004 show a consistent direction: max effort and the sampling meta
prompt usually lower no-evidence diversity. The conservative fixed-prompt
bounds include zero for every comparison. A paired within-case permutation
test on the same data finds a pooled effort effect, but those comparisons were
chosen after seeing the results, cover only six prompts and, for most meta
comparisons, span rounds. Round-005 tests the two claims on fresh data.

## Design

- **Cases:** no-evidence tier only. Seed `2f6412b4e27271a5`, four cases per
  scenario (24 in all). Each scenario's four change orders form a Latin
  square, so every change appears in every list position once. These prompts
  have not been run before.
- **Conditions (12):** Opus 5.5, GPT-6.1 Terra and GPT-6 Astra, each at medium
  and max effort, each plain and with the meta prompt. All run in one round
  with one shuffled schedule, so every comparison is within round.
- **Repeats:** 20 per case and condition, 5,760 jobs.
- **Runners:** Claude Code with the Claude subscription; Pi with the ChatGPT
  subscription. Pools and retry limits as in earlier rounds.

## Decision rules

1. **Effort.** Plain max minus medium, no-evidence pass@10, pooled over the
   three models. One-sided paired permutation test; confirmed if p <= 0.05.
2. **Meta prompt.** Meta minus plain, no-evidence pass@10, pooled over all six
   model-effort pairs. Two-sided, because earlier evidence points both ways
   (the random-number benchmark found increases); confirmed if p <= 0.05, with
   the sign of the pooled estimate giving the direction.

Per-model results are secondary, Holm-adjusted within each family. Every
planned job counts and invalid answers are misses. If the round stops early,
the confirmatory tests are not reported. The analysis reports the
conservative bounds and scenario sensitivity alongside.

## Limits

The permutation test conditions on the frozen prompts. Four orderings of six
scenarios widen the prompt set but do not sample new business situations, so
the result generalizes to these scenarios, not to analytics questions at large.

## Commands

```sh
uv run bench.py plan runs/round-005 --seed 2f6412b4e27271a5 --tiers none \
  --none-variants 4 --max-hours 18 --conditions \
  opus-5.5-medium opus-5.5-max opus-5.5-medium-meta opus-5.5-max-meta \
  gpt-6.1-terra-medium gpt-6.1-terra-max gpt-6.1-terra-medium-meta gpt-6.1-terra-max-meta \
  gpt-6-astra-medium gpt-6-astra-max gpt-6-astra-medium-meta gpt-6-astra-max-meta
uv run bench.py run runs/round-005
uv run bench.py export runs/round-005 results/round-005
uv run grade.py review results/round-005
uv run grade.py labels results/round-005
uv run analyze.py results/round-005 --output results/confirmation \
  --comparisons confirmation-spec.json
```
