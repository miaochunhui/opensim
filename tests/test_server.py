"""Real loopback HTTP checks; no production service is touched."""

import gzip
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server as server_module
from server import ApplicationServer, RUNTIME_SOURCES
from tools import render_page as page_module

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

    def raw_request(self, target, method="GET", headers=(), body=b""):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        self.addCleanup(connection.close)
        connection.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
        for key, value in headers:
            connection.putheader(key, value)
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, response.headers, response.read()

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

    def test_fingerprinted_resources_and_cache_headers(self):
        page = self.request("/")[2].decode("utf-8")
        self.assertEqual(self.request("/")[1]["Cache-Control"], "no-store")
        self.assertEqual(self.request("/api/health")[1]["Cache-Control"], "no-store")
        for extension, mime in (("js", "text/javascript"), ("css", "text/css")):
            relative = f"web/app.{extension}"
            body = (server_module.ROOT / relative).read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            target = f"/web/app.{digest}.{extension}"
            self.assertIn(f'"{target.lstrip("/")}"', page)
            for path in (target, "/" + relative):
                status, headers, raw = self.request(path)
                self.assertEqual(status, 200)
                self.assertEqual(raw, body)
                self.assertEqual(headers["Content-Type"], mime + "; charset=utf-8")
                self.assertEqual(headers["Vary"], "Accept-Encoding")
                self.assertEqual(headers["ETag"], '"' + digest + '-identity"')
                cache = ("public, max-age=31536000, immutable" if path == target
                         else "public, max-age=0, must-revalidate")
                self.assertEqual(headers["Cache-Control"], cache)
                status, revalidated, raw = self.request(
                    path, headers={"If-None-Match": f'"other", W/{headers["ETag"]}'})
                self.assertEqual(status, 304)
                self.assertEqual(raw, b"")
                self.assertNotIn("Content-Length", revalidated)
                self.assertEqual(revalidated["Content-Type"], headers["Content-Type"])
                self.assertEqual(revalidated["Cache-Control"], cache)
                self.assertEqual(revalidated["Vary"], "Accept-Encoding")
            self.assertEqual(self.request(
                f"/web/app.{'0' * 64}.{extension}")[0], 404)

    def test_gzip_quality_and_representation_validators(self):
        target = "/web/app.js"
        identity = self.request(target)
        for value, compressed in (("gzip; q=0", False), ("gzip;q=0.000", False),
                                  ("GZIP;Q=0.5", True), ("gzip;q=0.5", True),
                                  ("gzip;q=1.0", True), ("gzip;q=bogus", False),
                                  ("*;q=1, gzip;q=0", False), ("*;q=0.5", True)):
            with self.subTest(accept_encoding=value):
                status, headers, body = self.request(target, headers={"Accept-Encoding": value})
                self.assertEqual(status, 200)
                self.assertEqual(headers.get("Content-Encoding") == "gzip", compressed)
                self.assertEqual(headers["Vary"], "Accept-Encoding")
                self.assertEqual(gzip.decompress(body) if compressed else body, identity[2])
                if compressed:
                    self.assertNotEqual(headers["ETag"], identity[1]["ETag"])
                    repeated = self.request(target, headers={"Accept-Encoding": value})
                    self.assertEqual(body, repeated[2])
                    self.assertEqual(self.request(target, headers={
                        "Accept-Encoding": value, "If-None-Match": identity[1]["ETag"],
                    })[0], 200)
                    status, conditional, empty = self.request(target, headers={
                        "Accept-Encoding": value, "If-None-Match": headers["ETag"],
                    })
                    self.assertEqual(status, 304)
                    self.assertEqual(empty, b"")
                    self.assertEqual(conditional["ETag"], headers["ETag"])
                    self.assertEqual(conditional["Content-Encoding"], "gzip")
                    self.assertNotIn("Content-Length", conditional)
                status, head, empty = self.request(
                    target, headers={"Accept-Encoding": value}, method="HEAD")
                self.assertEqual(status, 200)
                self.assertEqual(empty, b"")
                self.assertEqual(head["Content-Length"], str(len(body)))
                self.assertEqual(head["ETag"], headers["ETag"])
        self.assertEqual(self.request(target, headers={"If-None-Match": "*"})[0], 304)

    def test_malformed_request_targets_fail_loudly(self):
        host = [("Host", "127.0.0.1")]
        for target in ("http://[/", "http://evil.invalid/api/health",
                       "/api/health#fragment", "/api/health%GG", "/api/health%",
                       "/api/health\\suffix"):
            with self.subTest(target=target):
                status, headers, body = self.raw_request(target, headers=host)
                self.assertEqual(status, 400)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertEqual(json.loads(body)["error"]["code"], "BAD_PATH")
        self.assertEqual(self.request("/api/health?probe=1")[0], 200)

    def test_host_origin_and_forwarded_header_boundaries(self):
        body = json.dumps(CONFIG).encode("utf-8")
        common = [("Content-Type", "application/json"), ("Content-Length", str(len(body)))]
        for host, origin, expected in (
            ("opensim.ai4everyone.top", "https://opensim.ai4everyone.top", 200),
            ("OPENSIM.ai4everyone.top", "https://opensim.ai4everyone.top:443", 200),
            ("opensim.ai4everyone.top:443", "https://opensim.ai4everyone.top", 200),
            ("opensim.ai4everyone.top:8080", "http://opensim.ai4everyone.top:8080", 200),
            ("[::1]:8080", "http://[::1]:8080", 200),
            ("opensim.ai4everyone.top", "https://evil.invalid", 403),
            ("opensim.ai4everyone.top", "https://opensim.ai4everyone.top/path", 403),
            ("opensim.ai4everyone.top", "https://opensim.ai4everyone.top?x=1", 403),
            ("opensim.ai4everyone.top", "https://opensim.ai4everyone.top#x", 403),
            ("opensim.ai4everyone.top", "https://opensim.ai4everyone.top:444", 403),
            ("opensim.ai4everyone.top", "null", 403),
            ("opensim.ai4everyone.top", "https://user@opensim.ai4everyone.top", 403),
        ):
            with self.subTest(host=host, origin=origin):
                status, _, _ = self.raw_request("/api/solve", "POST",
                                               common + [("Host", host), ("Origin", origin)], body)
                self.assertEqual(status, expected)
        for hosts in ([], [("Host", "a"), ("Host", "a")],
                      [("Host", "example.invalid/path")], [("Host", "[::1")],
                      [("Host", "example.invalid:99999")], [("Host", "user@example.invalid")]):
            with self.subTest(hosts=hosts):
                status, _, raw = self.raw_request("/api/health", headers=hosts)
                self.assertEqual(status, 400)
                self.assertEqual(json.loads(raw)["error"]["code"], "BAD_HOST")
        for extra in (
            [("Origin", "https://evil.invalid"), ("X-Forwarded-Host", "evil.invalid")],
            [("Origin", "https://evil.invalid"), ("Forwarded", "host=evil.invalid;proto=https")],
            [("Origin", "http://127.0.0.1"), ("Origin", "http://127.0.0.1")],
        ):
            self.assertEqual(self.raw_request(
                "/api/solve", "POST", common + [("Host", "127.0.0.1")] + extra, body)[0], 403)
        self.assertEqual(self.raw_request("/api/solve", "POST", common + [
            ("Host", "opensim.ai4everyone.top"),
            ("Origin", "https://opensim.ai4everyone.top"),
            ("X-Forwarded-Proto", "http"), ("X-Forwarded-Host", "evil.invalid"),
        ], body)[0], 200)

    def test_framing_and_bounded_admission(self):
        for extra, expected in (
            ([], 400), ([("Content-Length", "0")], 413),
            ([("Content-Length", "4"), ("Content-Length", "4")], 400),
            ([("Content-Length", "-1")], 400),
            ([("Content-Length", "4"), ("Transfer-Encoding", "chunked")], 400),
        ):
            with self.subTest(extra=extra):
                self.assertEqual(self.raw_request("/api/solve", "POST", [
                    ("Host", "127.0.0.1"), ("Content-Type", "application/json"),
                ] + extra, b"null")[0], expected)
        app = ApplicationServer(("127.0.0.1", 0))
        for _ in range(24):
            self.assertTrue(app.http_slots.acquire(blocking=False))
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", app.server_port, timeout=5)
            try:
                connection.request("GET", "/api/health")
                response = connection.getresponse()
                self.assertEqual(response.status, 503)
                self.assertEqual(response.read(), b"")
            finally:
                connection.close()
        finally:
            for _ in range(24):
                app.http_slots.release()
            app.shutdown()
            thread.join()
            app.server_close()
        self.assertEqual(self.request("/api/health")[0], 200)

    def test_real_source_change_stale_fingerprint_never_labels_new_bytes(self):
        external = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-server")
        external.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="source-change-", dir=external) as directory:
            root = Path(directory)
            for relative in page_module.SOURCE_PATHS:
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(server_module.ROOT / relative, target)
            with patch.object(server_module, "ROOT", root), patch.object(
                page_module, "__file__", str(root / "tools/render_page.py"),
            ), patch.dict(RUNTIME_SOURCES, RUNTIME_SOURCES.copy(), clear=True):
                old_hash = RUNTIME_SOURCES["web/app.js"]
                old_path = f"/web/app.{old_hash}.js"
                self.assertEqual(self.request(old_path)[0], 200)
                source = root / "web/app.js"
                changed = source.read_bytes() + b"\n// source-change fixture only\n"
                source.write_bytes(changed)
                for path in ("/api/health", "/", "/web/app.js", old_path):
                    self.assertEqual(self.request(path)[0], 503)
                RUNTIME_SOURCES["web/app.js"] = hashlib.sha256(changed).hexdigest()
                status, headers, body = self.request(old_path, headers={
                    "If-None-Match": '"' + old_hash + '-identity"',
                })
                self.assertEqual(status, 404)
                self.assertEqual(headers["Cache-Control"], "no-store")
                self.assertNotIn("ETag", headers)
                self.assertNotEqual(body, changed)
                new_path = f'/web/app.{RUNTIME_SOURCES["web/app.js"]}.js'
                self.assertEqual(self.request(new_path)[2], changed)
                self.assertEqual(self.request("/web/app.js")[2], changed)
                self.assertIn(new_path.lstrip("/"), self.request("/")[2].decode())
                self.assertEqual(json.loads(self.request("/api/health")[2])["source_sha256"],
                                 RUNTIME_SOURCES)
        self.assertEqual(self.request("/api/health")[0], 200)

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
