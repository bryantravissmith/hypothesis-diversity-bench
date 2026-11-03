"""Generate root-cause cases with a known true cause and graded evidence.

Every scenario has four candidate changes, one of each cause type. Three
evidence tiers share the same story:

- none: no diagnostics. The true cause cannot be identified, so the prompt is
  identical for all four truths and there is one case per scenario.
- partial: diagnostics for two non-true causes rule them out, leaving the true
  cause and one randomly chosen alternative.
- full: diagnostics for all four causes; only the true cause shows its
  signature.

Numbers are generated from the true cause so that every table is consistent
with it. Effect sizes are balanced: whichever change is true, it needs a
moderate effect (roughly 20-30%) on the people it touches to explain the drop,
so no cause wins on arithmetic alone. Change-log and diagnostic order are shuffled per case.
"""

import hashlib
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCENARIOS = ROOT / "scenarios.json"
TIERS = ("none", "partial", "full")
QUESTION = (
    "What is the single most likely cause of the drop? Reply with only a JSON object "
    'with two keys: "hypothesis" (one cause, in one sentence) and "check" (one query '
    "or analysis that would confirm or rule it out)."
)
ROLLOUT = 0.9  # Share of users on the new release; the rest is a random holdout.
MIX_SOURCE_RATE = 0.3  # New-source rate relative to baseline when mix is true.
MIX_DECOY_SHARE = 0.05  # New-source volume share when mix is not the cause.
BASE_VOLUME = 100_000
NOISE = 0.025


def load_scenarios(path=SCENARIOS):
    data = json.loads(Path(path).read_text())
    if data["schema"] != "hypothesis-diversity-scenarios-v1":
        raise ValueError("Unknown scenario schema")
    for s in data["scenarios"]:
        if set(s["causes"]) != set(data["causes"]):
            raise ValueError(f"{s['id']} must define every cause type")
        if not 0 < s["after"] < s["before"]:
            raise ValueError(f"{s['id']} must show a drop")
    return data


def _rng(*parts):
    digest = hashlib.sha256(json.dumps(parts).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _fmt(value, decimals):
    return f"{value:.{decimals}f}%"


def _table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def diagnostic(scenario, cause, truth, rng):
    """Render one diagnostic table consistent with the true cause.

    Returns (title, table, values) where values are the unrounded numbers, used
    by tests to check the signature.
    """
    b, a = scenario["before"], scenario["after"]
    drop = 1 - a / b
    dec = scenario["decimals"]
    short = scenario["short"]
    rows = scenario["causes"][cause]["rows"]

    def jitter(x):
        return x * (1 + rng.uniform(-NOISE, NOISE))

    before = "Six weeks before"
    after = "Since October 13"
    if cause == "release":
        new, old = (b * (1 - drop / ROLLOUT), b) if truth == "release" else (a, a)
        values = {"before": [jitter(b), jitter(b)], "after": [jitter(new), jitter(old)]}
        title = f"{short} by release group (random {1 - ROLLOUT:.0%} holdout since October 13)"
        table = _table(
            ["Group", before, after],
            [[rows[i], _fmt(values["before"][i], dec), _fmt(values["after"][i], dec)] for i in range(2)],
        )
    elif cause == "mix":
        existing_rate = b if truth == "mix" else a
        if truth == "mix":
            source_rate = b * MIX_SOURCE_RATE
            volume = BASE_VOLUME * (b - a) / (a - source_rate)
        else:
            source_rate, volume = a, BASE_VOLUME * MIX_DECOY_SHARE
        values = {
            "volume_before": [jitter(BASE_VOLUME), 0],
            "volume_after": [jitter(BASE_VOLUME), jitter(volume)],
            "before": [jitter(b), None],
            "after": [jitter(existing_rate), jitter(source_rate)],
        }
        title = f"{scenario['volume']} and {short.lower()} by source"
        table = _table(
            ["Source", "Volume, six weeks before", "Volume, since October 13", f"{short}, six weeks before", f"{short}, since October 13"],
            [
                [rows[0], f"{values['volume_before'][0]:,.0f}", f"{values['volume_after'][0]:,.0f}", _fmt(values["before"][0], dec), _fmt(values["after"][0], dec)],
                [rows[1], "0", f"{values['volume_after'][1]:,.0f}", "n/a", _fmt(values["after"][1], dec)],
            ],
        )
    elif cause == "tracking":
        analytics, record = (a, b) if truth == "tracking" else (a, a)
        values = {"before": [jitter(b), jitter(b)], "after": [jitter(analytics), jitter(record)]}
        title = f"{short} computed from two data sources"
        table = _table(
            ["Source", before, after],
            [[rows[i], _fmt(values["before"][i], dec), _fmt(values["after"][i], dec)] for i in range(2)],
        )
    elif cause == "policy":
        share = scenario["causes"]["policy"]["share"]
        affected, other = (b * (1 - drop / share), b) if truth == "policy" else (a, a)
        values = {"before": [jitter(b), jitter(b)], "after": [jitter(affected), jitter(other)]}
        title = f"{short} by segment"
        table = _table(
            ["Segment", before, after],
            [[rows[i], _fmt(values["before"][i], dec), _fmt(values["after"][i], dec)] for i in range(2)],
        )
    else:
        raise ValueError(cause)
    return title, table, values


def prompt_for(scenario, changes, diagnostics):
    dec = scenario["decimals"]
    lines = [
        f"You are helping the analytics team at {scenario['business']}. Here is what we know:",
        "",
        f"- {scenario['metric']} was steady at about {_fmt(scenario['before'], dec)} for six weeks, "
        f"then fell to {_fmt(scenario['after'], dec)} in the week of October 13 and has stayed there since.",
        "- No outages were reported.",
        "- Changes around that time:",
    ]
    lines += [f"  - {scenario['causes'][c]['change']}" for c in changes]
    if diagnostics:
        lines += ["", "The team has already pulled these breakdowns:"]
        for title, table in diagnostics:
            lines += ["", title, "", table]
    lines += ["", QUESTION]
    return "\n".join(lines)


def latin_orders(items, rng):
    """len(items) orderings in which every item takes every position once."""
    n = len(items)
    rows, cols, symbols = rng.sample(range(n), n), rng.sample(range(n), n), rng.sample(list(items), n)
    return [[symbols[(rows[r] + cols[c]) % n] for c in range(n)] for r in range(n)]


def build(seed, scenarios_path=SCENARIOS, tiers=TIERS, none_variants=1):
    """Build the case set. With none_variants > 1, each scenario gets that many
    no-evidence cases whose change orders come from a Latin square, so every
    change appears in every list position equally often."""
    data = load_scenarios(scenarios_path)
    causes = data["causes"]
    if none_variants not in (1, len(causes)):
        raise ValueError(f"none_variants must be 1 or {len(causes)}")
    if not tiers or set(tiers) - set(TIERS):
        raise ValueError(f"tiers must be a non-empty subset of {TIERS}")
    cases = []
    for scenario in data["scenarios"]:
        sid = scenario["id"]
        if "none" not in tiers:
            orders = []
        elif none_variants == 1:
            orders = [(f"{sid}--none", _rng(seed, sid, "none").sample(causes, len(causes)))]
        else:
            square = latin_orders(causes, _rng(seed, sid, "none", "latin"))
            orders = [(f"{sid}--none--v{i}", order) for i, order in enumerate(square, 1)]
        for case_id, changes in orders:
            cases.append(
                dict(
                    id=case_id,
                    scenario=sid,
                    tier="none",
                    truth=None,
                    candidates=list(causes),
                    diagnostics=[],
                    change_order=changes,
                    prompt=prompt_for(scenario, changes, []),
                )
            )
        for tier in [t for t in ("partial", "full") if t in tiers]:
            for truth in causes:
                rng = _rng(seed, sid, tier, truth)
                changes = rng.sample(causes, len(causes))
                if tier == "full":
                    shown, candidates = list(causes), [truth]
                else:
                    alternative = rng.choice([c for c in causes if c != truth])
                    candidates = [truth, alternative]
                    shown = [c for c in causes if c not in candidates]
                shown = rng.sample(shown, len(shown))
                rendered = [diagnostic(scenario, c, truth, rng)[:2] for c in shown]
                cases.append(
                    dict(
                        id=f"{sid}--{tier}--{truth}",
                        scenario=sid,
                        tier=tier,
                        truth=truth,
                        candidates=sorted(candidates, key=causes.index),
                        diagnostics=shown,
                        change_order=changes,
                        prompt=prompt_for(scenario, changes, rendered),
                    )
                )
    return cases


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", default="preview")
    parser.add_argument("--case", help="Print one case prompt")
    args = parser.parse_args()
    built = build(args.seed)
    if args.case:
        print(next(c for c in built if c["id"] == args.case)["prompt"])
    else:
        for c in built:
            print(c["id"], "candidates:", c["candidates"], "diagnostics:", c["diagnostics"])
