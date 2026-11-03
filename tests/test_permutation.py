"""Paired within-case permutation tests, offline."""

import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import permutation

CAUSES = ("release", "mix", "tracking", "policy")


class PermutationTest(unittest.TestCase):
    def test_holm_matches_hand_computation(self):
        self.assertEqual(permutation.holm([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06])
        self.assertEqual(permutation.holm([0.5, 0.9]), [1.0, 1.0])
        self.assertEqual(permutation.holm([0.2, 0.9]), [0.4, 0.9])

    def test_pass_table_matches_unbiased_estimator(self):
        table = permutation.pass_table(20, 10)
        self.assertEqual(table[0], 0)
        self.assertEqual(table[11], 1)
        self.assertAlmostEqual(table[1], 0.5)

    def test_identical_groups_are_never_extreme_two_sided_only_at_zero(self):
        labels = ["release"] * 10 + ["mix"] * 10
        out = permutation.family_test({"same": [(labels, labels, None)] * 3}, CAUSES, 10, "two-sided", 200, "s")
        self.assertEqual(out["pairs"]["same"]["statistic"], 0)
        self.assertEqual(out["pairs"]["same"]["p"], 1)

    def test_clear_drop_is_detected_and_pooled(self):
        collapsed = ["release"] * 20
        spread = ["release", "mix", "tracking", "policy"] * 5
        pairs = {"a": [(collapsed, spread, None)] * 6, "b": [(collapsed, spread, None)] * 6}
        out = permutation.family_test(pairs, CAUSES, 10, "less", 500, "s")
        self.assertLess(out["pairs"]["a"]["statistic"], -0.5)
        self.assertEqual(out["pairs"]["a"]["p"], 1 / 501)
        self.assertEqual(out["pooled"]["p"], 1 / 501)
        self.assertEqual(permutation.family_test(pairs, CAUSES, 10, "greater", 500, "s")["pairs"]["a"]["p"], 1)

    def test_seeded_and_invalid_answers_score_as_misses(self):
        left = ["release"] * 18 + ["invalid", "invalid"]
        right = ["release"] * 20
        cases = {"x": [(left, right, "release")]}
        first = permutation.family_test(cases, CAUSES, 1, "two-sided", 300, "seed")
        self.assertEqual(first, permutation.family_test(cases, CAUSES, 1, "two-sided", 300, "seed"))
        self.assertAlmostEqual(first["pairs"]["x"]["statistic"], -0.1)

    def test_rejects_bad_parameters(self):
        with self.assertRaises(ValueError):
            permutation.family_test({"x": [(["mix"] * 5, ["mix"] * 5, None)]}, CAUSES, 10, "less", 10, "s")
        with self.assertRaises(ValueError):
            permutation.family_test({"x": [(["mix"] * 20, ["mix"] * 20, None)]}, CAUSES, 10, "up", 10, "s")
        with self.assertRaises(ValueError):
            permutation.family_test({}, CAUSES, 10, "less", 10, "s")

    def test_null_rejection_rate_stays_near_alpha(self):
        rng = random.Random(7)
        weights = (0.7, 0.2, 0.1, 0.0)
        rejections, datasets = 0, 120
        for i in range(datasets):
            cases = [(rng.choices(CAUSES, weights, k=20), rng.choices(CAUSES, weights, k=20), None) for _ in range(6)]
            out = permutation.family_test({"x": cases}, CAUSES, 10, "less", 199, f"null-{i}")
            rejections += out["pairs"]["x"]["p"] <= 0.05
        self.assertLessEqual(rejections / datasets, 0.1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
