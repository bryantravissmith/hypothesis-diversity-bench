# Hypothesis Diversity Bench

When you run an analysis agent several times, do you get several analyses?

Teams scale agent analysis by running it again: parallel investigators, a
second opinion, self-consistency, best-of-k with a check. All of these assume
that independent runs explore different explanations. This benchmark measures
whether they do, on root-cause questions where an analyst would normally
consider several causes.

It is a companion to [Random Number Bench](https://github.com/bryantravissmith/random-number-bench),
which found that current models answer "pick a random number from 1 to 10"
with the same number almost every time, while older models such as GPT-4o and
DeepSeek V3.2 spread their answers. This benchmark asks whether that collapse
carries over to analysis, and what it costs.

**[Results](RESULTS.md)** · [Design](#design) · [Run it](#run-a-round)

## The question

Each case describes a metric that dropped and four changes that landed around
the same time, always one of each type:

| Cause type | Example (online store) |
| --- | --- |
| Release | A new site release to 90% of visitors, with a random 10% holdout |
| Traffic mix | A new paid social campaign |
| Tracking | A new tag manager configuration |
| Pricing or policy | Free shipping limited to orders of $50 or more |

The model names the single most likely cause and one check that would confirm
or rule it out. Every run is a fresh session with the same prompt.

## Design

Six scenarios (online store, SaaS, mobile app, food delivery, vacation rentals,
news subscriptions) appear in three evidence tiers.

- **None.** Only the headline drop and the change log. The true cause cannot be
  identified, so a model that always gives the same answer finds the true
  cause in 25% of cases however many times it runs. A model that spreads its
  answers finds it more often as runs are added. This is the coverage test.
- **Partial.** Diagnostic breakdowns rule out two causes and leave the true
  cause plus one alternative. This tests whether models use evidence, and
  whether repeated runs cover the two that remain.
- **Full.** Breakdowns for all four causes, where only the true cause shows its
  signature (for example, the order database holds steady while analytics
  orders fall). This is the control: it checks that every model can read the
  evidence.

True causes are balanced: every scenario appears with each of the four causes
in the partial and full tiers, so a model with a favorite story cannot win on
average. All numbers are generated from the true cause and are consistent with
it. Change-log and breakdown order are shuffled per case.

The headline metric is **pass@k**: the chance that at least one of k
independent runs names the true cause. With no evidence it averages over the
four equally likely causes. A collapsed model scores the same at k=1 and k=10.

### Grading

Each hypothesis is labeled with the change it names, using per-scenario
patterns. Answers that match no change or several go to a blinded review list
that shows only the scenario and the hypothesis text, never the model. Review
decisions and reasons are published with the results. Every planned trial
stays in the denominator: an invalid or missing answer is a miss.

### Conditions

| Condition | Runner | Notes |
| --- | --- | --- |
| Claude Opus 5.5, medium effort | Claude Code CLI | Subscription login, tools off, one turn |
| Claude Opus 5.5, max effort | Claude Code CLI | Same, max effort |
| GPT-4o | Pi via OpenRouter | Provider default decoding, no reasoning |
| DeepSeek V3.2, thinking off | Pi via OpenRouter | Provider default decoding |
| DeepSeek R1-0528 | Pi via OpenRouter | Reasoning model |

The same system prompt (`You are a helpful assistant.`) and working directory
(`/`) are used for every condition. Native CLIs add their own context, and
OpenRouter routes to a provider of its choosing, so treat the runner as part
of each condition. Model identity is checked against what each runner reports.

## Limits

- Six synthetic scenarios with a fixed structure. They are built so that the
  answer is knowable, not to match the messiness of real incidents.
- The none tier rewards spread by construction. Its point is to measure how
  much spread each model has, not to claim spread is always good: when the
  evidence identifies the cause, consistency is correct, which is what the
  full tier checks.
- Pattern labels can miss unusual phrasings; review covers answers that match
  zero or several changes, and a spot check of the rest is recorded in the
  round notes.
- Opus runs through a subscription CLI whose decoding settings are not exposed.
  GPT-4o and DeepSeek use OpenRouter defaults.

## Run a round

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/), an authenticated
[Claude Code](https://code.claude.com) CLI for the Claude conditions and
[Pi](https://github.com/earendil-works/pi) logged in to OpenRouter for the
others.

```sh
# Freeze seed, cases, prompts, conditions and schedule. No inference.
uv run bench.py plan runs/round-002

# Run with bounded concurrency per service. Ctrl-C pauses; rerun to resume.
uv run bench.py run runs/round-002
uv run bench.py status runs/round-002

# Verify raw evidence hashes and export explicit public fields.
uv run bench.py export runs/round-002 results/round-002

# Label, review the blinded list, then build the report.
uv run grade.py review results/round-002
uv run grade.py labels results/round-002
uv run analyze.py results/round-002 --report RESULTS.md
```

Preview any case prompt without calling a model:

```sh
uv run cases.py --seed preview --case ecommerce-checkout--partial--mix
```

## Test without calling a model

```sh
uv run tests/test_cases.py
uv run tests/test_pipeline.py
```

The tests check that cases are balanced and consistent with their true cause,
that only the true cause's breakdown shows its signature, answer parsing,
pass@k math, retries, resume, tamper detection and a full
plan-run-export-grade-report cycle against fake CLIs.

## License

MIT
