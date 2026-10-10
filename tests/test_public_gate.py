"""Independent loopback positive/negative controls for the public delivery gate."""

import copy
import json
import threading
import time
import unittest
from unittest.mock import patch

from server import ApplicationServer
from tools.public_gate import (
    GateError, PUBLIC_URL, check_endpoint, compare_solution, source_hashes, strict_json,
)
from solver import solve_case
from tools.snapshot import read_public_report

CONFIG = {
    "geometry": "circle", "width_mm": 4.0, "height_mm": 4.0,
    "length_mm": 100.0, "pressure_pa": 0.78, "viscosity_pa_s": 0.001,
    "density_kg_m3": 998.0, "resolution": 8,
}


class PublicGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ApplicationServer(("127.0.0.1", 0))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def test_real_fixture_is_local_not_public(self):
        report = check_endpoint(self.url, public=False)
        self.assertEqual(report["verdict"], "PASS", report)
        self.assertEqual(report["scope"], "LOCAL_TEST")
        self.assertEqual(len(report["checks"]), 4)
        self.assertEqual([item["name"] for item in report["checks"][-2:]],
                         ["real_fem_circle", "real_fem_rectangle"])

    def test_local_cannot_be_public(self):
        report = check_endpoint(self.url)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("fixed OpenSim", report["failure"]["reason"])

    def test_private_dns_is_blocked_before_http(self):
        with patch("tools.public_gate.socket.getaddrinfo",
                   return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with patch("tools.public_gate.fetch") as fetch:
                report = check_endpoint(PUBLIC_URL)
                self.assertEqual(report["verdict"], "FAIL")
                fetch.assert_not_called()

    def test_runtime_mismatch_is_detected(self):
        with patch("tools.public_gate.source_hashes", return_value={"solver.py": "0" * 64}):
            report = check_endpoint(self.url, public=False)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("sources differ", report["failure"]["reason"])

    def test_missing_assets_are_not_skipped(self):
        from tools.public_gate import fetch as real_fetch

        def fetch(base, path, payload=None):
            if path == "/web/app.css":
                return b"", {}
            return real_fetch(base, path, payload)

        with patch("tools.public_gate.fetch", side_effect=fetch):
            report = check_endpoint(self.url, public=False)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("Wrong asset", report["failure"]["reason"])

    def test_field_metric_and_provenance_mutants(self):
        from datetime import datetime, timezone
        import hashlib
        sources = source_hashes()
        result = solve_case(CONFIG)
        canonical = json.dumps(result["config"], sort_keys=True, allow_nan=False,
                               separators=(",", ":")).encode("utf-8")
        result["reproducibility"] = {
            "source_sha256": sources, "solver_sha256": sources["solver.py"],
            "config_sha256": hashlib.sha256(canonical).hexdigest(),
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }
        self.assertGreater(compare_solution(result, CONFIG, sources, time.time())["node_count"], 0)
        variants = []
        for kind in ("field", "metric", "solver", "config", "clock", "mesh", "missing"):
            mutant = copy.deepcopy(result)
            if kind == "field":
                mutant["fields"]["velocity_m_s"] = []
            elif kind == "metric":
                mutant["metrics"]["flow_rate_m3_s"] *= 2
            elif kind == "solver":
                mutant["reproducibility"]["solver_sha256"] = "0" * 64
            elif kind == "config":
                mutant["config"]["pressure_pa"] = 999
            elif kind == "clock":
                mutant["reproducibility"]["computed_at"] = "2020-01-01T00:00:00+00:00"
            elif kind == "mesh":
                mutant["mesh"]["triangles"] = []
            else:
                del mutant["reproducibility"]
            variants.append((kind, mutant))
        for kind, mutant in variants:
            with self.subTest(kind=kind), self.assertRaises(GateError):
                compare_solution(mutant, CONFIG, sources, time.time())

    def test_json_nonfinite_and_duplicates_fail(self):
        for raw in (b'{"x":NaN}', b'{"x":1,"x":2}', b'{"x":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(GateError):
                strict_json(raw)

    def test_local_scope_cannot_read_arbitrary_origins(self):
        for url in ("https://other.invalid", "http://localhost:8772",
                    "http://127.0.0.1:8772/subpath"):
            with self.subTest(url=url):
                self.assertEqual(check_endpoint(url, public=False)["verdict"], "FAIL")

    def test_snapshot_rejects_fake_scope_incomplete_checks_and_old_pass(self):
        import tempfile
        from datetime import datetime, timezone
        from pathlib import Path
        with tempfile.TemporaryDirectory(
            prefix="public-snapshot-negative-",
            dir="/hf3fs-jd/data/tmp/opensim-fluid-v1",
        ) as directory:
            path = Path(directory) / "synthetic.json"
            base = {
                "scope": "PUBLIC_CODE_AND_FEM", "url": PUBLIC_URL, "verdict": "PASS",
                "source_sha256": source_hashes(),
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "checks": [{"name": name, "verdict": "PASS"} for name in
                           ("runtime_identity", "page_and_assets",
                            "real_fem_circle", "real_fem_rectangle")],
            }
            for changes in ({"scope": "LOCAL_TEST"}, {"checks": []},
                            {"collected_at": "2020-01-01T00:00:00+00:00"},
                            {"source_sha256": {}},
                            {"checks": [{"name": "runtime_identity", "verdict": "PASS"}] * 4}):
                path.write_text(json.dumps({**base, **changes}), encoding="utf-8")
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    read_public_report(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
