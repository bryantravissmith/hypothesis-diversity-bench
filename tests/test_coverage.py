"""Score-difference test for no-evidence coverage, offline."""

import itertools
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import coverage_test

CAUSES = ("release", "mix", "tracking", "policy")


def brute_force(labels, k):
    scores, squares, products = [], [], []
    for first in itertools.combinations(range(len(labels)), k):
        second = [i for i in range(len(labels)) if i not in first]
        b1 = len({labels[i] for i in first} & set(CAUSES)) / 4
        b2 = len({labels[i] for i in second} & set(CAUSES)) / 4
        scores.append(b1)
        squares.append((b1 - b2) ** 2 / 4)
        products.append(b1 * b2)
    u = sum(scores) / len(scores)
    return u, u * u - sum(products) / len(products), sum(squares) / len(squares)


class CoverageTest(unittest.TestCase):
    def test_exact_split_variance_matches_enumeration(self):
        rng = random.Random(3)
        for _ in range(20):
            labels = rng.choices(CAUSES + ("invalid",), k=8)
            for got, expected in zip(coverage_test.cell_from_labels(labels, CAUSES, 4), brute_force(labels, 4)):
                self.assertAlmostEqual(got, expected)

    def test_unbiased_variance_matches_sampling_variance(self):
        rng = random.Random(5)
        weights = (0.6, 0.25, 0.1, 0.05)
        cells = [coverage_test.cell_from_labels(rng.choices(CAUSES, weights, k=20), CAUSES, 10) for _ in range(4000)]
        mean = sum(c[0] for c in cells) / len(cells)
        sampling = sum((c[0] - mean) ** 2 for c in cells) / (len(cells) - 1)
        self.assertAlmostEqual(sum(c[1] for c in cells) / len(cells), sampling, delta=0.15 * sampling)
        self.assertGreater(sum(c[2] for c in cells) / len(cells), sampling)

    def test_collapsed_cells_and_requirements(self):
        self.assertEqual(coverage_test.cell_from_labels(["mix"] * 20, CAUSES, 10), (0.25, 0.0, 0.0))
        with self.assertRaises(ValueError):
            coverage_test.family_test({"x": [(["mix"] * 20, ["mix"] * 20)]}, CAUSES, 10, "less", "exact")
        with self.assertRaisesRegex(ValueError, "exactly 20"):
            coverage_test.cell_from_labels(["mix"] * 19, CAUSES, 10)
        with self.assertRaises(ValueError):
            coverage_test.family_test({"x": [(["mix"] * 20, ["mix"] * 20)]}, CAUSES, 10, "up")

    def test_equal_scores_with_different_preferences_are_not_a_difference(self):
        left = ["release"] * 20
        right = ["mix"] * 20
        out = coverage_test.family_test({"x": [(left, right)] * 6}, CAUSES, 10, "two-sided")
        self.assertEqual(out["pairs"]["x"]["statistic"], 0)
        self.assertEqual(out["pairs"]["x"]["p"], 1)

    def test_clear_drop_and_holm(self):
        spread = ["release", "mix", "tracking", "policy"] * 5
        pairs = {"a": [(["release"] * 20, spread)] * 6, "b": [(spread, spread)] * 6}
        out = coverage_test.family_test(pairs, CAUSES, 10, "less")
        self.assertLess(out["pairs"]["a"]["p"], 1e-6)
        self.assertEqual(out["pairs"]["a"]["p_holm"], min(1, 2 * out["pairs"]["a"]["p"]))
        self.assertGreater(out["pairs"]["b"]["p"], 0.4)
        self.assertLess(out["pooled"]["p"], 0.001)

    def test_null_rejection_rate_with_relabeled_preferences(self):
        # Equal coverage, different favorite cause: the score null holds, distributions differ.
        rng = random.Random(11)
        weights = (0.75, 0.15, 0.07, 0.03)
        rejections, datasets = dict(unbiased=0, bound=0), 400
        for _ in range(datasets):
            cases = []
            for _ in range(24):
                swapped = rng.sample(CAUSES, 4)
                cases.append((rng.choices(CAUSES, weights, k=20), rng.choices(swapped, weights, k=20)))
            for variance in rejections:
                out = coverage_test.family_test({"x": cases}, CAUSES, 10, "less", variance)
                rejections[variance] += out["pairs"]["x"]["p"] <= 0.05
        self.assertLessEqual(rejections["unbiased"] / datasets, 0.075)
        self.assertLessEqual(rejections["bound"], rejections["unbiased"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
