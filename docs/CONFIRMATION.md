# Round-005 confirmation plan

Written and committed before any round-005 inference. The machine-readable
version is [confirmation-spec.json](../confirmation-spec.json). Version 1 of
this plan was frozen but never run; an external review (GPT-6 Astra, xhigh
effort) found that its primary test targeted the wrong null, that two of its
"new" prompts had already been run, and that multiplicity and completeness
rules were loose. Version 2 fixes those.

## Why

Rounds 001-004 show max effort usually lowering no-evidence diversity. The
conservative fixed-prompt bounds include zero for every comparison. Paired
tests on the same data find a pooled drop, but those comparisons were chosen
after seeing the results, cover six prompts, and several rest on one or two
prompts. Round-005 tests the effort claim on fresh prompts and samples.

## Design

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

## Decision rules

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

## Power

The [simulation report](../results/validation/CONFIRMATION_POWER.md) uses
the observed distributions from rounds 001-004 and shrinks the historical
effect, since effects chosen after seeing data are biased upward. Power
depends mostly on how often a model gives a cause it was never seen giving in
20 runs. If such causes are truly absent, 48 cases detect even half the
historical effect. If every cause keeps a few percent probability, power at
the full historical effect falls to about half. The meta-prompt effect, about
-0.01, is too small to confirm in any affordable round, which is why it is
exploratory.

## Limits

The tests condition on the frozen prompts. Eight orderings of six scenarios
widen the prompt set but do not sample new business situations, so a result
covers these scenarios, not analytics questions at large. The score treats
the four causes as equally likely; it measures coverage, not reasoning
quality.

## Commands

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
