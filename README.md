# Hypothesis Diversity Bench

This benchmark measures how much cause coverage repeated model calls provide
when asked for the single most likely explanation of a business metric drop.
It runs six synthetic business scenarios with no, partial or full diagnostic
evidence and scores the cause category named in each answer.

**[Results and interpretation](RESULTS_NOTES.md)** · [Full results](RESULTS.md) · [Confidence intervals](docs/INTERVALS.md) · [Run it](#run-a-round)

## Findings

Rounds 001-004 contain 21 model/effort/prompt conditions and 54 base cases.
Collection on November 1-2, 2026 (America/Los_Angeles) produced 6,048 valid
final answers from 6,060 physical attempts, including 12 operational failures.

**High concentration with no evidence.** GPT-6 Sol at max effort selected the
same category in all 20 answers for each of the six prompts. Its pass@10
and pass@1 both equal 0.25. Across the tested GPT conditions with medium/max
effort and Opus, pass@10 ranges from 0.25 to 0.37; GPT-4o and the two DeepSeek
conditions score approximately 0.48-0.50.

![No-evidence pass@k](results/figures/passk-none.svg)

| Selected plain condition | Effective causes per case | pass@1 | pass@10 |
| --- | ---: | ---: | ---: |
| DeepSeek R1-0528 | 1.77 | 0.25 | 0.50 |
| GPT-4o | 1.84 | 0.25 | 0.49 |
| DeepSeek V3.2, thinking off | 1.74 | 0.25 | 0.48 |
| Claude Opus 5.5, medium | 1.47 | 0.25 | 0.37 |
| GPT-6.1 Terra, medium | 1.35 | 0.25 | 0.35 |
| GPT-6.1 Sol, medium | 1.11 | 0.25 | 0.31 |
| GPT-6 Sol, max | 1.00 | 0.25 | 0.25 |
| GPT-6 Astra, medium | 1.00 | 0.25 | 0.25 |

The no-evidence score averages coverage over four equally weighted causes.
Every single-category answer scores 0.25 at k=1. Independent uniform choices
across all four causes reach 0.9437 at k=10. The figure shows point estimates;
[the tables](RESULTS.md) include 95% confidence intervals.

**Effort effects vary.** Max effort has a lower no-evidence pass@10 estimate
in five of six plain-prompt comparisons; Astra rises from 0.250 to 0.292.
All six adjusted difference intervals include zero. The six sampling-prefix
comparisons also have intervals spanning zero. Those bounds treat each
condition separately. A paired permutation test on the same fixed prompts
finds a pooled effort effect (mean -0.030, two-sided p < 0.001): Opus and
Terra drop, Astra rises, and the three Sol versions are inconclusive. These
comparisons were chosen after seeing the data, so a
[preregistered confirmation round](docs/CONFIRMATION.md) retests effort and the
meta prompt on 24 new prompts.

**Cause preferences differ.** Opus primarily selects tracking, while the GPT
conditions with medium/max effort primarily select releases.

**Evidence compliance improves with decisive diagnostics.** Under partial
evidence, GPT conditions with medium/max effort and Opus select excluded
causes in approximately 0-6% of answers, compared with 19% for GPT-4o and 38%
for DeepSeek V3.2. With full evidence, every GPT condition with medium/max
effort, Opus and DeepSeek R1 scores 48/48; GPT-4o scores 44/48 and V3.2 scores
42/48. A score of 48/48 has a pass@1 95% CI of [0.926, 1.000].

## Design

Each prompt describes a metric drop and four changes: a release, traffic mix,
tracking, and pricing/policy. The six settings are an online store, SaaS,
mobile app, food delivery, vacation rentals and news subscriptions. The model
returns one hypothesis and one diagnostic check. Scoring uses the hypothesis's
cause category.

| Evidence tier | Cases | Trials per case and condition | Trials per condition |
| --- | ---: | ---: | ---: |
| None: headline drop and change log | 6 | 20 | 120 |
| Partial: diagnostics exclude two causes | 24 | 5 | 120 |
| Full: diagnostics identify one cause | 24 | 2 | 48 |

Partial and full tiers assign every scenario each of the four truths. The
partial tier selects one alternative per truth. This produces imbalanced
remaining pairs: a fixed preference for release, then mix, tracking and
policy scores 16/24 on these cases. The [pair counts](RESULTS_NOTES.md#partial-evidence)
describe the resulting distribution.

Diagnostic numbers encode the assigned truth with small jitter. Change and
diagnostic order are shuffled once per case and held fixed across repeats.
Dates and wording vary by cause type.

For a case with n trials and c correct answers, **pass@k** is
`1 - C(n-c, k) / C(n, k)`, averaged equally over cases. With no evidence,
scoring also averages over four hypothetical equally likely truths.
**Effective causes** is the exponential of label entropy, averaged over cases.

### Conditions

| Condition | Runner | Round |
| --- | --- | --- |
| Claude Opus 5.5, medium/max | Claude Code CLI, subscription | 001 |
| GPT-4o | Pi, OpenRouter | 001 |
| DeepSeek V3.2, thinking off | Pi, OpenRouter | 001 |
| DeepSeek R1-0528 | Pi, OpenRouter | 001 |
| GPT-5.6 Sol, GPT-6 Sol, GPT-6.1 Sol, medium/max | Pi, ChatGPT subscription | 002 |
| GPT-6.1 Terra, medium/max | Pi, ChatGPT subscription | 003 |
| GPT-6 Astra, medium/max | Pi, ChatGPT subscription | 004 |
| GPT-6 Astra, GPT-6.1 Sol and Terra, medium/max, sampling prefix | Pi, ChatGPT subscription | 004 |

All rounds use seed `84381f5ac1434825` and identical base cases. Sampling-prefix
conditions prepend a sentence describing the answer as one observation in a
random sample. Rounds 001-003 overlap in time; round 004 was collected later.
Results retain each condition's round identity.

Every call starts a fresh session, with system prompt `You are a helpful
assistant.`, working directory `/`, and tools disabled. Native CLI context,
provider routing and decoding settings are part of the tested configuration.
The harness checks the model identity reported by the runner.

### Grading and attempt accounting

Per-scenario patterns identify the named cause. Answers matching zero or
several changes receive a review from a blinded list containing the scenario
and hypothesis. Decisions and reasons are saved in each round's `reviews.json`.
There are 5,559 pattern labels and 489 review labels.

Every planned trial stays in the denominator. Its last recorded attempt is
scored; invalid or missing final answers count as misses. Operational failures
may be retried within the frozen bound. Completed invalid responses are
terminal, and all attempted calls are retained.

The parser accepts an extractable JSON object with a nonempty `hypothesis`.
All 6,048 final answers contain a nonempty check; 623 also have surrounding
text or formatting. Check quality is outside the scoring rubric.

### Scope and uncertainty

The CIs target repeated calls to the fixed prompts, assuming independent jobs
and stable response distributions within each case and condition. The
[method](docs/INTERVALS.md) specifies these assumptions and the adjustment for
condition comparisons. [Scenario sensitivity](results/figures/UNCERTAINTY.md)
reports variation across the six templates.

The benchmark-building agent reviewed ambiguous answers and a 60-answer
pattern-label sample from rounds 001-003, with 60 agreements. The
[saved spot check](results/label-spot-check.json) records that review. An
independent label audit and more distinct, balanced scenarios are priorities
for further validation.

This is a companion to [Random Number Bench](https://github.com/bryantravissmith/random-number-bench),
which measures diversity in number sampling.

## Run a round

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), and authenticated
[Claude Code](https://code.claude.com) or [Pi](https://github.com/earendil-works/pi)
CLIs for the selected conditions. Pi uses OpenRouter and the `openai` provider
with a ChatGPT subscription.

```sh
# Freeze the seed, cases, prompts, conditions and schedule.
uv run bench.py plan runs/new-round --conditions opus-5.5-medium gpt-6.1-sol-medium

# Run, inspect or resume with the frozen source snapshot.
uv run bench.py run runs/new-round
uv run bench.py status runs/new-round
uv run runs/new-round/source/bench.py run runs/new-round

# Verify evidence hashes, export public fields and grade.
uv run bench.py export runs/new-round results/new-round
uv run grade.py review results/new-round
uv run grade.py labels results/new-round

# Rebuild the published analysis of the four completed rounds.
uv run analyze.py results/round-001 results/round-002 results/round-003 results/round-004 \
  --figures results/figures --comparisons analysis-spec.json --report RESULTS.md
```

`RESULTS.md`, summaries, labels and SVG figures are generated with the scripts.
The [results index](results/README.md) lists the artifacts. Maintained prose
lives in `RESULTS_NOTES.md`.

Preview a case without calling a model:

```sh
uv run cases.py --seed preview --case ecommerce-checkout--partial--mix
```

## Offline tests and interval validation

```sh
uv run --no-project python -m unittest discover -s tests -v
uv run validate_intervals.py --output results/validation
```

Tests cover case construction, parsing, scoring, retry/resume behavior,
evidence hashes, confidence bounds and the full fake-CLI pipeline. The
[coverage report](results/validation/VALIDATION.md) uses 10,000 simulated
datasets per setting.

## License

MIT
