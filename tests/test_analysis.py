"""Versioned analysis through the CLI on synthetic public exports, offline."""

import contextlib
import copy
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import analyze
import grade


def fixture(path):
    path.mkdir()
    (path / "source").mkdir()
    shutil.copy(ROOT / "scenarios.json", path / "source")
    frozen = json.loads((ROOT / "results/round-001/plan.json").read_text())
    selected = [c for c in frozen["cases"] if c["scenario"] == "ecommerce-checkout"]
    conditions = [dict(id=name, label=name, model="synthetic", thinking=name) for name in ("a", "b")]
    repeats = dict(none=20, partial=5, full=2)
    schedule, attempts = [], []
    scenario = json.loads((ROOT / "scenarios.json").read_text())["scenarios"][0]
    for cond in conditions:
        for case in selected:
            for trial in range(1, repeats[case["tier"]] + 1):
                job = dict(job=str(len(schedule)), case=case["id"], condition=cond["id"], trial=trial)
                schedule.append(job)
                cause = case["truth"] or ("release" if cond["id"] == "a" else ("mix" if trial <= 10 else "tracking"))
                attempts.append(dict(job, attempt=1, outcome="valid", scenario=case["scenario"],
                                     hypothesis=scenario["causes"][cause]["change"]))
    plan = dict(seed="test", cases=selected, conditions=conditions, repeats=repeats,
                schedule=schedule, state="complete", unfinished_jobs=0)
    (path / "plan.json").write_text(json.dumps(plan))
    (path / "attempts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in attempts))
    return plan


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.input = self.root / "round-a"
        self.plan = fixture(self.input)

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()):
            analyze.main([str(self.input), *map(str, args)])

    def test_versioned_outputs_leave_inputs_unchanged_and_are_reproducible(self):
        before = {str(p): p.read_bytes() for p in self.input.rglob("*") if p.is_file()}
        output = self.root / "v2"
        self.run_cli("--output", output)
        after = {str(p): p.read_bytes() for p in self.input.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        generated = {str(p): p.read_bytes() for p in output.rglob("*") if p.is_file()}
        self.run_cli("--output", output)
        self.assertEqual(generated, {str(p): p.read_bytes() for p in output.rglob("*") if p.is_file()})
        summary = json.loads((output / "summary.json").read_text())
        full = summary["conditions"][0]["tiers"]["full"]["passk"]["1"]
        self.assertEqual(full["mean"], 1)
        self.assertLess(full["low"], 1)
        self.assertEqual(full["confidence"], .95)
        self.assertNotIn("legacy_case_bootstrap", full)
        self.assertNotIn("Original case bootstrap", (output / "UNCERTAINTY.md").read_text())
        self.assertIn("Random once, then repeat", (output / "passk-partial.svg").read_text())

    def test_incomplete_round_keeps_denominator_but_suppresses_ci(self):
        self.plan.update(state="stopped", unfinished_jobs=1)
        (self.input / "plan.json").write_text(json.dumps(self.plan))
        rows = (self.input / "attempts.jsonl").read_text().splitlines()
        (self.input / "attempts.jsonl").write_text("\n".join(rows[1:]) + "\n")
        self.run_cli("--output", self.root / "out")
        s = json.loads((self.root / "out/summary.json").read_text())
        tier = s["conditions"][0]["tiers"]["none"]
        self.assertEqual(tier["trials"], 20)
        self.assertEqual(tier["invalid"], 1)
        self.assertAlmostEqual(tier["passk"]["1"]["mean"], .95 / 4)
        self.assertIsNone(tier["passk"]["1"]["low"])
        self.assertIn("unavailable", (self.root / "out/RESULTS.md").read_text())

    def test_pending_review_and_stale_labels(self):
        rows = [json.loads(x) for x in (self.input / "attempts.jsonl").read_text().splitlines()]
        rows[0]["hypothesis"] = "Something inexplicable happened."
        (self.input / "attempts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        _, s = analyze.summarize(self.input)
        self.assertEqual(s["analysis"]["unavailable_reason"], "unreviewed ambiguous labels")
        _, labels = grade.labels(self.input)
        labels[0]["label"] = "mix"
        (self.input / "labels.jsonl").write_text("".join(json.dumps(r) + "\n" for r in labels))
        with self.assertRaisesRegex(ValueError, "saved labels differ"):
            analyze.summarize(self.input)

    def test_malformed_schedule_and_case_mismatch_fail_before_writes(self):
        self.plan["schedule"].pop()
        (self.input / "plan.json").write_text(json.dumps(self.plan))
        with self.assertRaisesRegex(ValueError, "Schedule does not match"):
            self.run_cli("--output", self.root / "bad")
        self.assertFalse((self.root / "bad").exists())
        # Restore, then change truth while retaining prompt and case identity.
        shutil.rmtree(self.input)
        fixture(self.input)
        second = self.root / "round-b"
        second_plan = fixture(second)
        second_plan["cases"][1]["truth"] = "tracking"
        (second / "plan.json").write_text(json.dumps(second_plan))
        with self.assertRaisesRegex(SystemExit, "different cases"):
            self.run_cli(second, "--output", self.root / "bad")
        self.assertFalse((self.root / "bad").exists())
        self.assertFalse((self.input / "summary.json").exists())

    def test_round_qualified_pairs_and_family_adjustment(self):
        _, s = analyze.summarize(self.input)
        combined = dict(conditions=[dict(c, round="round-a") for c in s["conditions"]])
        combined["conditions"] += [dict(c, round="round-b") for c in s["conditions"]]
        spec = dict(families=[dict(id="test", tier="none", k=10, alpha=.05, pairs=[
            dict(id="within", left=["round-a", "b"], right=["round-a", "a"]),
            dict(id="across", left=["round-b", "a"], right=["round-a", "a"]),
            dict(id="absent", left=["round-c", "a"], right=["round-a", "a"]),
        ])])
        f = analyze.contrasts(combined, spec)[0]
        self.assertEqual(f["endpoint_count"], 4)
        self.assertEqual(f["endpoint_alpha"], .05 / 4)
        within, across, absent = f["pairs"]
        self.assertGreater(within["mean"], 0)
        self.assertEqual(across["mean"], 0)
        self.assertEqual(absent["status"], "unavailable")
        self.assertAlmostEqual(within["mean"], within["scenario_sensitivity"]["mean"])
        self.assertEqual(within["low"], within["endpoints"][0]["low"] - within["endpoints"][1]["high"])
        changed = copy.deepcopy(combined)
        changed["conditions"][0]["tiers"]["none"]["per_case"].clear()
        with self.assertRaisesRegex(ValueError, "identical cases"):
            analyze.contrasts(changed, spec)

    def test_label_permutation_preserves_none_score(self):
        case = dict(truth=None, candidates=analyze.CAUSES)
        first = analyze.case_metrics(case, ["release"] * 17 + ["mix", "tracking", "invalid"], (1, 5, 10))
        permuted = analyze.case_metrics(case, ["policy"] * 17 + ["tracking", "mix", "invalid"], (1, 5, 10))
        self.assertEqual(first["passk"], permuted["passk"])


class FrozenResultsTest(unittest.TestCase):
    def test_published_point_estimates_and_trial_counts(self):
        points = []
        for name in ("round-001", "round-002", "round-003", "round-004"):
            _, summary = analyze.summarize(ROOT / "results" / name)
            for c in summary["conditions"]:
                for tier, t in c["tiers"].items():
                    self.assertEqual(t["trials"], 48 if tier == "full" else 120)
                    points.extend((name, c["id"], tier, k, v["mean"]) for k, v in t["passk"].items())
        self.assertEqual(len(points), 231)
        digest = hashlib.sha256(json.dumps(sorted(points), separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(digest, "049fbe643147cc0ffe67c026b3c8e5a705fca7b53fa6f0d79067a281f6739b79")


if __name__ == "__main__":
    unittest.main(verbosity=2)
