"""Numerical bounds and independently enumerated scenario sensitivity."""

import itertools
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import intervals


class IntervalTest(unittest.TestCase):
    def test_boundaries_and_actual_repeat_counts(self):
        for k, lower in ((1, 0.9260272146527198), (2, 0.8575264022774743)):
            ci = intervals.fixed_prompt_interval(1, 24, 2, k, "full")
            self.assertAlmostEqual(ci["low"], lower)
            self.assertEqual(ci["high"], 1)
        ci = intervals.fixed_prompt_interval(.25, 6, 20, 10, "none")
        self.assertAlmostEqual(ci["low"], .03423668334)
        self.assertAlmostEqual(ci["high"], .63519001869)
        self.assertEqual(ci["block_parameter"], 12)
        ci = intervals.fixed_prompt_interval(.25, 6, 20, 1, "none")
        self.assertAlmostEqual(ci["low"], .242431756856)
        self.assertEqual(ci["high"], .25)

    def test_inversion_extremes_symmetry_and_monotonicity(self):
        for mean in (0, 1e-10, .01, .25, .5, .99, 1 - 1e-10, 1):
            for blocks in (1, 12, 48, 120, 100000):
                lo, hi = intervals.kl_bounds(mean, blocks)
                reverse = intervals.kl_bounds(1 - mean, blocks)
                self.assertLessEqual(lo, mean)
                self.assertGreaterEqual(hi, mean)
                self.assertAlmostEqual(lo, 1 - reverse[1])
                self.assertAlmostEqual(hi, 1 - reverse[0])
                for endpoint in (lo, hi):
                    if 1e-8 < endpoint < 1 - 1e-8:
                        self.assertAlmostEqual(blocks * intervals.bernoulli_kl(mean, endpoint), math.log(40), places=5)
                narrower = intervals.kl_bounds(mean, blocks * 2)
                self.assertGreaterEqual(narrower[0], lo)
                self.assertLessEqual(narrower[1], hi)
                adjusted = intervals.kl_bounds(mean, blocks, .05 / 12)
                self.assertLessEqual(adjusted[0], lo)
                self.assertGreaterEqual(adjusted[1], hi)

    def test_scale_and_nondivisible_blocks(self):
        ci = intervals.fixed_prompt_interval(.3, 6, 7, 3, "none")
        raw = intervals.kl_bounds(.4, 12)
        self.assertEqual(ci["block_parameter"], 12)
        self.assertEqual(ci["score_range"], [0, .75])
        self.assertAlmostEqual(ci["low"], raw[0] * .75)
        self.assertAlmostEqual(ci["high"], raw[1] * .75)
        zero = intervals.fixed_prompt_interval(0, 24, 5, 5, "partial")
        self.assertEqual(zero["low"], 0)
        self.assertGreater(zero["high"], 0)

    def test_invalid_parameters(self):
        for args in ((math.nan, 12), (.2, 0), (.2, True), (.2, 2.5), (.2, 12, 0), (.2, 12, 1)):
            with self.assertRaises(ValueError):
                intervals.kl_bounds(*args)
        for args in ((.3, 6, 20, 1, "none"), (.5, 6, 2, 3, "full"), (.5, 0, 5, 1, "partial")):
            with self.assertRaises(ValueError):
                intervals.fixed_prompt_interval(*args)

    def test_exact_coverage_for_binomial_and_small_heterogeneous_ustat(self):
        for n in (1, 12, 48, 120):
            for probability in (.001, .01, .1, .5, .9, .99, .999):
                coverage = 0.0
                for hits in range(n + 1):
                    lo, hi = intervals.kl_bounds(hits / n, n)
                    if lo <= probability <= hi:
                        coverage += math.comb(n, hits) * probability ** hits * (1 - probability) ** (n - hits)
                self.assertGreaterEqual(coverage, .95 - 1e-12)
        # Enumerate every possible dataset, not bootstrap draws: two different
        # prompt distributions, 3 repeats each, k=2, two categories plus invalid.
        probabilities = ((.7, .2, .1), (.1, .2, .7))
        truth = sum(1 - (1 - p) ** 2 for probs in probabilities for p in probs[:2]) / 8
        coverage = 0.0
        for data in itertools.product(range(3), repeat=6):
            weight = math.prod(probabilities[i // 3][category] for i, category in enumerate(data))
            score = 0.0
            for case in (data[:3], data[3:]):
                score += sum(len(set(pair) - {2}) / 4 for pair in itertools.combinations(case, 2)) / 6
            ci = intervals.fixed_prompt_interval(score, 2, 3, 2, "none")
            if ci["low"] <= truth <= ci["high"]:
                coverage += weight
        self.assertGreaterEqual(coverage, .95 - 1e-12)

    def test_exact_cluster_bootstrap_against_ordered_enumeration(self):
        groups = {"a": [0, .2], "b": [.7], "c": [.4, .5, .9]}
        result = intervals.scenario_sensitivity(groups)
        samples = []
        for keys in itertools.product(groups, repeat=3):
            values = [v for key in keys for v in groups[key]]
            samples.append(sum(values) / len(values))
        samples.sort()
        self.assertEqual(result["bootstrap"]["resamples"], 27)
        for key, fraction in (("low", .025), ("high", .975)):
            self.assertAlmostEqual(result["bootstrap"][key], samples[math.ceil(fraction * 27) - 1])
        self.assertAlmostEqual(result["mean"], 2.7 / 6)
        self.assertAlmostEqual(result["leave_one_out"]["c"], .9 / 3)
        self.assertEqual(result, intervals.scenario_sensitivity(groups))

    def test_six_clusters_and_single_cluster(self):
        result = intervals.scenario_sensitivity({str(i): [.25] * (i + 1) for i in range(6)})
        self.assertEqual(result["bootstrap"]["resamples"], 46656)
        self.assertEqual(result["bootstrap"]["low"], .25)
        self.assertEqual(result["bootstrap"]["high"], .25)
        self.assertIsNone(intervals.scenario_sensitivity({"a": [.5]})["leave_one_out_range"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
