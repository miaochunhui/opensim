"""Reject stale and incomplete numerical evidence without touching live reports."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.render_page import check_page, render_page
from tools.snapshot import read_report

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1")


class PageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="page-negative-", dir=ARTIFACTS)
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "synthetic.json"
        self.report = {
            "verdict": "PASS", "state": "COMPLETE", "checks_executed": 1,
            "collected_at": "SYNTHETIC_TEST_ONLY",
            "cases": ["fixture"], "rejections": ["fixture"],
            "solver": {"sha256": hashlib.sha256((ROOT / "solver.py").read_bytes()).hexdigest()},
            "convergence": {
                name: {"verdict": "PASS", "resolutions": [8, 16, 32],
                       "relative_flow_error": {"errors": [0.04, 0.01, 0.0025]}}
                for name in ("circle", "rectangle", "square")
            },
        }

    def write(self, report):
        self.file.write_text(json.dumps(report), encoding="utf-8")
        return self.file

    def test_complete_evidence_has_real_chart_and_matching_table(self):
        page = render_page(self.write(self.report))
        self.assertGreater(check_page(page)["checks"], 0)
        self.assertIn('id="convergence-chart-title"', page)
        self.assertEqual(page.count("<polyline "), 3)
        self.assertIn("4.000% → 1.000% → 0.250%", page)

    def test_failed_running_stale_and_empty_evidence_rejected(self):
        variants = [
            dict(self.report, verdict="FAIL"), dict(self.report, state="RUNNING"),
            dict(self.report, solver={"sha256": "0" * 64}),
            dict(self.report, convergence={}), dict(self.report, checks_executed=0),
            dict(self.report, checks_executed=True), dict(self.report, cases=[]),
            dict(self.report, rejections=[]),
        ]
        for variant in variants:
            with self.subTest(keys=variant):
                with self.assertRaises(ValueError):
                    render_page(self.write(variant))

    def test_bad_grid_or_nonconverging_error_rejected(self):
        for grids, errors in [([8, 16, 48], [0.04, 0.01, 0.0025]),
                              ([8, 16, 32], [0.04, 0.05, 0.06]),
                              ([8, 16, 32], [True, 0.01, 0.0025])]:
            variant = copy.deepcopy(self.report)
            variant["convergence"]["circle"].update(
                resolutions=grids, relative_flow_error={"errors": errors})
            with self.subTest(grids=grids, errors=errors):
                with self.assertRaises(ValueError):
                    render_page(self.write(variant))

    def test_snapshot_rejects_missing_stale_or_fixture_reports(self):
        with self.assertRaises(FileNotFoundError):
            read_report(self.file, "local")
        stale = {"verdict": "PASS", "source_sha256": {"solver.py": "0" * 64}}
        with self.assertRaisesRegex(ValueError, "stale"):
            read_report(self.write(stale), "local")
        with self.assertRaisesRegex(ValueError, "fixture"):
            read_report(self.write({"verdict": "SELFTEST_PASS",
                                    "mode": "HARNESS_SELFTEST_NOT_OPENSIM"}), "browser")
        with self.assertRaisesRegex(ValueError, "current page"):
            read_report(self.write({"verdict": "AUTOMATED_PASS", "mode": "OPENSIM_ACCEPTANCE",
                                    "page_build": {"build_text": "stale"}}), "browser")
        observed = read_report(self.write(dict(self.report, verdict="FAIL")), "numerics")
        self.assertEqual(observed["verdict"], "FAIL")
        self.assertEqual(len(observed["sha256"]), 64)
