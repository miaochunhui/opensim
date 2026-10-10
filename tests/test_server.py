"""Real loopback HTTP checks; no production service is touched."""

import gzip
import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import ApplicationServer, RUNTIME_SOURCES

CONFIG = {
    "geometry": "circle", "width_mm": 4.0, "height_mm": 4.0,
    "length_mm": 100.0, "pressure_pa": 1.0, "viscosity_pa_s": 0.001,
    "density_kg_m3": 998.0, "resolution": 16,
}


class HTTPTests(unittest.TestCase):
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

    def request(self, path="/api/solve", body=None, headers=None, method=None):
        if body is not None and not isinstance(body, bytes):
            body = json.dumps(body).encode()
        hdr = {"Content-Type": "application/json"} if body is not None else {}
        hdr.update(headers or {})
        request = Request(self.url + path, data=body, headers=hdr, method=method)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    def test_health_and_real_compute(self):
        status, _, body = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")
        status, _, body = self.request(body=CONFIG)
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertGreater(result["metrics"]["node_count"], 10)
        self.assertGreater(result["metrics"]["max_velocity_m_s"], 0)
        self.assertLess(result["metrics"]["relative_residual"], 1e-8)
        self.assertEqual(len(result["reproducibility"]["config_sha256"]), 64)
        self.assertEqual(len(result["reproducibility"]["solver_sha256"]), 64)

    def test_page_assets_and_headers(self):
        for path, needle in [("/", b"workspace"), ("/index-v1.html", b"case-form"),
                             ("/web/app.css", b"canvas"), ("/web/app.js", b"solve")]:
            with self.subTest(path=path):
                status, headers, body = self.request(path, headers={"Accept-Encoding": "gzip"})
                self.assertEqual(status, 200)
                if headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                self.assertIn(needle, body)
                self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_reject_json_and_config(self):
        for body, expected in [(b"{", 400), (b'{"x":NaN}', 400),
                               (b'{"x":1,"x":2}', 400), ({}, 422),
                               ({"geometry": "circle"}, 422), ([], 422),
                               (b"[" * 1500 + b"0" + b"]" * 1500, 400),
                               (b'{"x":"\xff"}', 400)]:
            with self.subTest(body=body):
                status, _, raw = self.request(body=body)
                self.assertEqual(status, expected)
                self.assertIn("message", json.loads(raw)["error"])
        status, _, _ = self.request(body=b"x" * 17000)
        self.assertEqual(status, 413)
        status, _, _ = self.request(body=CONFIG, headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)

    def test_isolation_and_busy(self):
        changed = dict(CONFIG, pressure_pa=2.0)
        first = json.loads(self.request(body=CONFIG)[2])
        second = json.loads(self.request(body=changed)[2])
        self.assertAlmostEqual(second["metrics"]["flow_rate_m3_s"],
                               2 * first["metrics"]["flow_rate_m3_s"], places=12)
        self.server.solve_slot.acquire()
        try:
            status, _, body = self.request(body=CONFIG)
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body)["error"]["code"], "BUSY")
        finally:
            self.server.solve_slot.release()
        self.assertEqual(self.request(body=CONFIG)[0], 200)

    def test_no_cross_origin_or_file_access(self):
        status, _, _ = self.request(body=CONFIG, headers={"Origin": "https://evil.invalid"})
        self.assertEqual(status, 403)
        status, _, _ = self.request(body=CONFIG, headers={"Origin": "http://["})
        self.assertEqual(status, 403)
        for path in ["/solver.py", "/web/../solver.py", "/web/%2e%2e/solver.py", "/nope"]:
            self.assertEqual(self.request(path)[0], 404)

    def test_etag_revalidation_and_head(self):
        status, headers, _ = self.request("/web/app.css")
        self.assertEqual(status, 200)
        status, _, body = self.request("/web/app.css", headers={"If-None-Match": headers["ETag"]})
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")
        status, _, body = self.request("/api/health", method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")

    def test_source_drift_rejects_old_runtime_identity(self):
        with patch.dict(RUNTIME_SOURCES, {"solver.py": "0" * 64}):
            for path, body in [("/api/health", None), ("/", None), ("/api/solve", CONFIG)]:
                with self.subTest(path=path):
                    status, _, raw = self.request(path, body=body)
                    self.assertEqual(status, 503)
                    self.assertEqual(json.loads(raw)["error"]["code"], "SOURCE_CHANGED")
        self.assertEqual(self.request(body=CONFIG)[0], 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
