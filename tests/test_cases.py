"""Generated cases are balanced, consistent with their true cause and frozen by seed."""

import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cases

SCENARIOS = cases.load_scenarios()["scenarios"]
CAUSES = cases.load_scenarios()["causes"]


def signature(cause, values):
    """True when a diagnostic table shows its cause's signature."""
    after = values["after"]
    if cause == "mix":
        # Existing sources hold their rate; the new source converts far lower.
        return after[1] < 0.6 * after[0] and values["volume_after"][1] > 0.1 * values["volume_after"][0]
    # Two rows: the affected row falls well below the unaffected one.
    return after[0] < 0.9 * after[1] if cause != "tracking" else after[1] > 1.1 * after[0]


class CaseTest(unittest.TestCase):
    def setUp(self):
        self.built = cases.build("test-seed")

    def test_balanced_case_set(self):
        self.assertEqual(len(self.built), len(SCENARIOS) * 9)
        for tier in ("partial", "full"):
            truths = [c["truth"] for c in self.built if c["tier"] == tier]
            self.assertEqual(sorted(set(truths)), sorted(CAUSES))
            self.assertTrue(all(truths.count(t) == len(SCENARIOS) for t in CAUSES))
        self.assertEqual(len({c["id"] for c in self.built}), len(self.built))

    def test_none_tier_hides_the_truth(self):
        for c in self.built:
            if c["tier"] == "none":
                self.assertIsNone(c["truth"])
                self.assertEqual(c["diagnostics"], [])
                self.assertNotIn("breakdowns", c["prompt"])

    def test_partial_tier_leaves_truth_and_one_alternative(self):
        for c in self.built:
            if c["tier"] == "partial":
                self.assertEqual(len(c["candidates"]), 2)
                self.assertIn(c["truth"], c["candidates"])
                self.assertEqual(set(c["diagnostics"]), set(CAUSES) - set(c["candidates"]))

    def test_only_the_true_cause_shows_its_signature(self):
        for scenario in SCENARIOS:
            for truth in CAUSES:
                for trial in range(20):
                    rng = random.Random(trial)
                    for cause in CAUSES:
                        _, _, values = cases.diagnostic(scenario, cause, truth, rng)
                        self.assertEqual(
                            signature(cause, values), cause == truth,
                            f"{scenario['id']} truth={truth} diagnostic={cause}",
                        )

    def test_every_prompt_lists_all_changes_and_asks_one_question(self):
        for c in self.built:
            scenario = next(s for s in SCENARIOS if s["id"] == c["scenario"])
            for cause in CAUSES:
                self.assertEqual(c["prompt"].count(scenario["causes"][cause]["change"]), 1)
            self.assertEqual(sorted(c["change_order"]), sorted(CAUSES))
            self.assertTrue(c["prompt"].endswith(cases.QUESTION))

    def test_seed_freezes_cases(self):
        self.assertEqual(self.built, cases.build("test-seed"))
        self.assertNotEqual(
            [c["change_order"] for c in self.built],
            [c["change_order"] for c in cases.build("other-seed")],
        )

    def test_published_rounds_rebuild_unchanged(self):
        import json

        for name in ("round-001", "round-004"):
            plan = json.loads((ROOT / "results" / name / "plan.json").read_text())
            self.assertEqual(cases.build(plan["seed"]), plan["cases"])

    def test_none_variants_balance_change_positions(self):
        built = cases.build("test-seed", tiers=("none",), none_variants=len(CAUSES))
        self.assertEqual(len(built), len(SCENARIOS) * len(CAUSES))
        self.assertEqual({c["tier"] for c in built}, {"none"})
        for s in SCENARIOS:
            orders = [c["change_order"] for c in built if c["scenario"] == s["id"]]
            self.assertEqual(len({tuple(o) for o in orders}), len(CAUSES))
            for position in range(len(CAUSES)):
                self.assertEqual(sorted(o[position] for o in orders), sorted(CAUSES))
        self.assertEqual(built, cases.build("test-seed", tiers=("none",), none_variants=len(CAUSES)))
        with self.assertRaises(ValueError):
            cases.build("test-seed", none_variants=2)
        published = {c["scenario"]: [c["change_order"]] for c in cases.build("test-seed") if c["tier"] == "none"}
        eight = cases.build("test-seed", tiers=("none",), none_variants=8, exclude_orders=published)
        self.assertEqual(len(eight), len(SCENARIOS) * 8)
        for s in SCENARIOS:
            orders = [tuple(c["change_order"]) for c in eight if c["scenario"] == s["id"]]
            self.assertEqual(len(set(orders)), 8)
            self.assertNotIn(tuple(published[s["id"]][0]), orders)
            for position in range(len(CAUSES)):
                self.assertEqual(sorted(o[position] for o in orders), sorted(CAUSES * 2))
        with self.assertRaises(ValueError):
            cases.build("test-seed", tiers=("sometimes",))

    def test_randomized_dates_balance_cause_and_date(self):
        import collections

        built = cases.build("test-seed", tiers=("none",), none_variants=8, randomize_dates=True)
        self.assertEqual(built, cases.build("test-seed", tiers=("none",), none_variants=8, randomize_dates=True))
        plain = cases.build("test-seed", tiers=("none",), none_variants=8)
        self.assertEqual([c["change_order"] for c in built], [c["change_order"] for c in plain])
        for s in SCENARIOS:
            variants = [c for c in built if c["scenario"] == s["id"]]
            slots = collections.Counter(cases.DATE.search(s["causes"][c]["change"]).group() for c in CAUSES)
            for cause in CAUSES:
                # Every cause carries each date in proportion to the scenario's
                # own date slots: October 13 twice as often as 12 or 14.
                got = collections.Counter(v["change_dates"][cause] for v in variants)
                self.assertEqual(got, collections.Counter({d: n * 2 for d, n in slots.items()}))
            for v in variants:
                self.assertEqual(collections.Counter(v["change_dates"].values()), slots)
                for cause in CAUSES:
                    line = cases.change_text(s, cause, v["change_dates"][cause])
                    self.assertIn(f"  - {line}", v["prompt"])
                    self.assertEqual(cases.DATE.findall(line), [v["change_dates"][cause]])
                self.assertIn("in the week of October 13", v["prompt"])
        self.assertTrue(all("change_dates" not in c for c in plain))
        with self.assertRaises(ValueError):
            cases.build("test-seed", randomize_dates=True)
        with self.assertRaises(ValueError):
            cases.build("test-seed", tiers=("none",), randomize_dates=True)

    def test_patterns_name_their_own_change(self):
        import grade

        patterns = grade.scenario_patterns(cases.SCENARIOS)
        for s in SCENARIOS:
            for cause in CAUSES:
                label, hits = grade.auto_label(s["causes"][cause]["change"], patterns[s["id"]])
                self.assertEqual(hits, [cause], f"{s['id']} {cause} change must match only its own pattern")


if __name__ == "__main__":
    unittest.main(verbosity=2)
