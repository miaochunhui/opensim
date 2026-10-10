"""Reject stale and incomplete numerical evidence without touching live reports."""

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tools import render_page as page_module
from tools.render_page import LOCAL_ZONE, SOURCE_PATHS, _source_metadata, check_page, render_page
from tools.snapshot import read_report

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1")


class PageMetadataTests(unittest.TestCase):
    def setUp(self):
        external = ARTIFACTS / "seat-server"
        external.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="page-metadata-", dir=external)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for index, relative in enumerate(SOURCE_PATHS):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
            timestamp = (1_700_000_000 + index) * 1_000_000_000 + 987_654_321
            os.utime(target, ns=(timestamp, timestamp))
        self.scope = patch.object(page_module, "__file__", str(self.root / "tools/render_page.py"))
        self.scope.start()
        self.addCleanup(self.scope.stop)

    def test_required_sources_exact_metadata_and_repeatable_bytes(self):
        from datetime import datetime
        expected = datetime.fromtimestamp(1_700_000_004, LOCAL_ZONE)
        fingerprint = hashlib.sha256()
        for relative in SOURCE_PATHS:
            fingerprint.update(relative.encode("ascii") + b"\0" +
                               (self.root / relative).read_bytes() + b"\0")
        self.assertEqual(_source_metadata(), (
            expected.isoformat(timespec="seconds"),
            expected.strftime("%Y-%m-%d %H:%M:%S +08:00"),
            fingerprint.hexdigest()[:12],
        ))
        first = render_page()
        self.assertEqual(first.encode("utf-8"), render_page().encode("utf-8"))
        self.assertGreater(check_page(first)["checks"], 0)
        self.assertIn('rel="icon" type="image/svg+xml" href="data:image/svg+xml,', first)
        self.assertEqual(SOURCE_PATHS, ("tools/render_page.py", "web/app.js", "web/app.css",
                                       "solver.py", "server.py"))
        (self.root / "web/app.css").unlink()
        with self.assertRaises(FileNotFoundError):
            render_page()

    def test_asset_content_not_mtime_drives_resource_url(self):
        first = render_page()
        edited = check_page(first)["edited_at"]
        source = self.root / "web/app.css"
        before = source.stat()
        body = source.read_bytes() + b"\n/* source-change fixture only */\n"
        source.write_bytes(body)
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        changed = render_page()
        old_path = re.search(r'href="(web/app\.[a-f0-9]{64}\.css)"', first)
        self.assertIsNotNone(old_path)
        path = f"web/app.{hashlib.sha256(body).hexdigest()}.css"
        self.assertIn('href="' + path + '"', changed)
        self.assertNotIn(old_path.group(1), changed)
        self.assertEqual(edited, check_page(changed)["edited_at"])

    def test_validator_rejects_unversioned_and_wrong_content_hash(self):
        page = render_page()
        for extension in ("js", "css"):
            path = f'web/app.{hashlib.sha256((self.root / f"web/app.{extension}").read_bytes()).hexdigest()}.{extension}'
            self.assertIn(path, page)
            for wrong in (f"web/app.{extension}", f"web/app.{'0' * 64}.{extension}"):
                with self.subTest(extension=extension, wrong=wrong), self.assertRaises(ValueError):
                    check_page(page.replace(path, wrong))


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
