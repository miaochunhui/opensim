"""Focused raw-socket HTTP review; no existing service is used.

Reuse test_server's private 127.0.0.1:0 lifecycle and workbench's evidence
executor. --out records the current candidate, or ordinary unittest discovery
creates a unique external evidence directory. --regression runs only the
unchanged test_server module. Old baseline evidence is never overwritten;
solver and other runtime dependencies stay pinned. One real FEM positive
control is not a numerical matrix, production test or release certification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
import re
import socket
import subprocess
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from tools import workbench_acceptance as workbench
import test_server as server_tests
import server

BASELINE = "2a86febdf472ee1429395b8b073e408cf20dcc38"
SCOPE = "PRIVATE_RAW_LOOPBACK_CANDIDATE_BYTES_NOT_PUBLIC_NOT_RELEASE"
ARTIFACTS = None


def candidate_sources():
    sources = {}
    for name in workbench.SOURCES:
        frozen = subprocess.run(
            ["git", "show", f"{BASELINE}:{name}"], cwd=ROOT,
            capture_output=True, check=True,
        ).stdout
        expected = hashlib.sha256(frozen).hexdigest()
        actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        if name != "server.py" and actual != expected:
            raise ValueError(f"Pinned dependency differs from old baseline: {name}")
        sources[name] = actual
    return sources


class HTTPBoundaryReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.artifacts = ARTIFACTS if ARTIFACTS is not None else workbench.new_output(
            workbench.EXTERNAL / "resume-s6-core-precheck-20261010" /
            ("discover-" + uuid4().hex))
        cls.candidate = workbench.inventory(ROOT)
        cls.sources = candidate_sources()
        workbench.write_json(cls.artifacts / "source-before.json", cls.candidate)
        workbench.write_json(cls.artifacts / "START.json", {
            "state": "RUNNING", "started_at": workbench.now(), "scope": SCOPE,
            "candidate_commit": cls.candidate["commit"], "source_sha256": cls.sources,
        })
        cls.log = logging.FileHandler(cls.artifacts / "server.log", encoding="utf-8")
        logging.getLogger().addHandler(cls.log)
        cls.addClassCleanup(cls.log.close)
        cls.addClassCleanup(logging.getLogger().removeHandler, cls.log)
        cls.addClassCleanup(cls.record_sources)
        server_tests.HTTPTests.setUpClass.__func__(cls)
        cls.addClassCleanup(cls.record_stopped)
        if (cls.server.server_address[0] != "127.0.0.1"
                or not 0 < cls.server.server_port <= 65535
                or server.RUNTIME_SOURCES != cls.sources):
            raise ValueError("Private listener/runtime identity differs")
        workbench.write_json(cls.artifacts / "listener.json", {
            "dependency_baseline": BASELINE, "source_sha256": cls.sources,
            "candidate_commit": cls.candidate["commit"],
            "address": list(cls.server.server_address), "scope": SCOPE,
            "lifecycle": "test_server.HTTPTests private ephemeral listener",
        })

    @classmethod
    def check_sources(cls):
        if candidate_sources() != cls.sources or workbench.inventory(ROOT) != cls.candidate:
            raise ValueError("Candidate/source changed during raw requests")

    @classmethod
    def record_sources(cls):
        after = workbench.inventory(ROOT)
        workbench.write_json(cls.artifacts / "source-after.json", after)
        cls.check_sources()
        print(f"HTTP_BOUNDARY_SOURCE_STABLE commit={after['commit']} "
              f"scope={SCOPE} out={cls.artifacts}", flush=True)

    @classmethod
    def record_stopped(cls):
        server_tests.HTTPTests.tearDownClass.__func__(cls)
        workbench.write_json(cls.artifacts / "listener-stopped.json", {
            "thread_alive": cls.thread.is_alive(),
            "socket_fileno": cls.server.socket.fileno(), "scope": SCOPE,
        })
        if cls.thread.is_alive() or cls.server.socket.fileno() != -1:
            raise ValueError("Private listener did not stop")

    def exchange(self, name, target="/api/solve", headers=(), body=b"null",
                 method="POST", expected_status=400, expected_code="BODY_LENGTH",
                 raw_request=None, json_response=True):
        self.check_sources()
        request = (f"{method} {target} HTTP/1.1\r\n"
                   + "".join(f"{key}: {value}\r\n" for key, value in headers)
                   + "\r\n").encode("ascii") + body
        if raw_request is not None:
            request = raw_request
        response = bytearray()
        row = {
            "name": name, "dependency_baseline": BASELINE, "source_sha256": self.sources,
            "candidate_commit": self.candidate["commit"],
            "scope": SCOPE, "request_hex": request.hex(),
            "request_sha256": hashlib.sha256(request).hexdigest(),
            "expected_status": expected_status, "expected_code": expected_code,
            "peer_eof": False, "client_closed": False, "started_at": workbench.now(),
        }
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
                connection.settimeout(5)
                connection.connect(("127.0.0.1", self.server.server_port))
                row["peer"] = list(connection.getpeername())
                connection.sendall(request)
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        row["peer_eof"] = True
                        break
                    response.extend(chunk)
                    if len(response) > 1024 * 1024:
                        raise ValueError("Unexpected oversized diagnostic response")
            row["client_closed"] = True
            head, raw = bytes(response).split(b"\r\n\r\n", 1)
            lines = head.split(b"\r\n")
            row["status_line"] = lines[0].decode("ascii")
            row["status"] = int(lines[0].split()[1])
            lengths = [line.split(b":", 1)[1].strip() for line in lines[1:]
                       if line.lower().startswith(b"content-length:")]
            if len(lengths) != 1 or int(lengths[0]) != len(raw):
                raise ValueError("Response framing differs from body")
            value = json.loads(raw) if json_response else {}
            row["response_json"] = value
            if not json_response:
                row["response_text"] = raw.decode("utf-8")
                self.assertIn("Bad request syntax", row["response_text"])
            row["error_code"] = value.get("error", {}).get("code")
            self.assertTrue(row["peer_eof"], "Server did not close the connection")
            self.assertEqual(self.sources, server.RUNTIME_SOURCES)
            self.assertEqual(row["status"], expected_status, name)
            self.assertEqual(row["error_code"], expected_code, name)
            if expected_status == 200:
                if target == "/api/solve":
                    self.assertEqual(value["reproducibility"]["source_sha256"], self.sources)
                    self.assertGreater(value["metrics"]["max_velocity_m_s"], 0)
                    self.assertGreater(value["metrics"]["node_count"], 10)
                    self.assertLess(value["metrics"]["relative_residual"], 1e-8)
                else:
                    self.assertEqual(value["source_sha256"], self.sources)
        except Exception as exc:
            row["first_failure"] = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            row.update(response_hex=bytes(response).hex(),
                       response_sha256=hashlib.sha256(response).hexdigest(),
                       finished_at=workbench.now())
            workbench.write_json(self.artifacts / f"{name}.json", row)

    def post_headers(self, *extra, content_type="application/json"):
        return [("Host", "127.0.0.1"), ("Content-Type", content_type),
                ("Content-Length", "4"), *extra]

    def test_controls(self):
        self.exchange("health-control", "/api/health", [("Host", "127.0.0.1")],
                      b"", "GET", 200, None)
        self.exchange("null-control", headers=self.post_headers(),
                      expected_status=422, expected_code="INVALID_CONFIG")
        self.exchange("chunked-control",
                      headers=self.post_headers(("Transfer-Encoding", "chunked")))
        self.exchange("plain-control", headers=self.post_headers(content_type="text/plain"),
                      expected_status=415, expected_code="CONTENT_TYPE")
        body = json.dumps(server_tests.CONFIG).encode("utf-8")
        self.exchange("fem-control", headers=[
            ("Host", "127.0.0.1"), ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
        ], body=body, expected_status=200, expected_code=None)

    def test_transfer_encoding_presence_and_duplicates(self):
        for name, extra in (
            ("te-empty", [("Transfer-Encoding", "")]),
            ("te-empty-first", [("Transfer-Encoding", ""), ("Transfer-Encoding", "chunked")]),
            ("te-nonempty-duplicate", [("Transfer-Encoding", "chunked"),
                                       ("Transfer-Encoding", "chunked")]),
        ):
            with self.subTest(name=name):
                self.exchange(name, headers=self.post_headers(*extra))

    def test_duplicate_content_type_rejected_in_both_orders(self):
        for name, first, second in (
            ("ct-json-first", "application/json", "text/plain"),
            ("ct-plain-first", "text/plain", "application/json"),
        ):
            with self.subTest(name=name):
                self.exchange(name, headers=[
                    ("Host", "127.0.0.1"), ("Content-Type", first),
                    ("Content-Type", second), ("Content-Length", "4"),
                ], expected_status=415, expected_code="CONTENT_TYPE")
        for name, types in (
            ("ct-missing", []), ("ct-empty", [("Content-Type", "")]),
            ("ct-json-duplicate", [("Content-Type", "application/json"),
                                   ("Content-Type", "application/json")]),
        ):
            with self.subTest(name=name):
                self.exchange(name, headers=[
                    ("Host", "127.0.0.1"), ("Content-Length", "4"), *types,
                ], expected_status=415, expected_code="CONTENT_TYPE")

    def test_leading_double_slash_rejected_before_normalization(self):
        self.exchange("leading-double-slash", "//api/health",
                      [("Host", "127.0.0.1")], b"", "GET", 400, "BAD_PATH")
        self.exchange("leading-double-slash-post", "//api/solve",
                      self.post_headers(), expected_status=400, expected_code="BAD_PATH")

    def test_standard_parser_error_keeps_first_cause(self):
        self.exchange("parser-syntax-control", expected_status=400, expected_code=None,
                      raw_request=b"GET //api/health EXTRA HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
                      json_response=False)


def logged_suite(args):
    output = workbench.new_output(args.out)
    before = workbench.inventory(ROOT)
    candidate_sources()
    workbench.write_json(output / "source-before.json", before)
    child_root = output / "requests"
    child_root.mkdir()
    argv = [sys.executable, "-B", str(Path(__file__).resolve()),
            "--child-root", str(child_root)]
    if args.regression:
        argv = [sys.executable, "-B", "-m", "unittest", "discover",
                "-s", str(ROOT / "tests"), "-p", "test_server.py", "-v"]
    elif args.select:
        argv.extend(["--select", *args.select])
    workbench.write_json(output / "START.json", {
        "state": "RUNNING", "started_at": workbench.now(), "argv": argv, "scope": SCOPE,
        "dependency_baseline": BASELINE, "candidate_commit": before["commit"],
        "source_sha256": candidate_sources(),
    })
    step = workbench.execute(argv, ROOT, output, "http-boundary-tests")
    after = workbench.inventory(ROOT)
    workbench.write_json(output / "source-after.json", after)
    workbench.write_json(output / "execution.json", step)
    stdout = Path(step["stdout"]["path"]).read_text(encoding="utf-8")
    markers = re.findall(
        r"HTTP_BOUNDARY_UNITTEST verdict=(PASS|FAIL) discovered=(\d+) tests_run=(\d+) "
        r"failures=(\d+) errors=(\d+) skipped=(\d+)", stdout,
    )
    counts = None
    verdict = None
    if len(markers) == 1:
        verdict, *numbers = markers[0]
        counts = dict(zip(("discovered", "tests_run", "failures", "errors", "skipped"),
                          map(int, numbers)))
    if args.regression:
        stderr = Path(step["stderr"]["path"]).read_text(encoding="utf-8")
        completed = re.findall(r"\nRan ([1-9][0-9]*) tests in [0-9.]+s\n", stderr)
        if len(completed) == 1 and re.search(r"\nOK\n", stderr):
            count = int(completed[0])
            counts = {"discovered": count, "tests_run": count, "failures": 0,
                      "errors": 0, "skipped": 0}
            verdict = "PASS"
    passed = (counts is not None and step["rc"] == 0 and before == after
              and verdict == "PASS" and counts["discovered"] == counts["tests_run"] > 0
              and counts["failures"] == counts["errors"] == counts["skipped"] == 0)
    report = {
        "scope": SCOPE, "dependency_baseline": BASELINE,
        "candidate_commit": before["commit"], "source_sha256": candidate_sources(),
        "verdict": "PASS" if passed else "FAIL", "exit_code": 0 if passed else 1,
        "release_verdict": "NOT_CERTIFIED", "production_changes": False,
        "finished_at": workbench.now(), "unittest_counts": counts,
        "counts": {"producers_validated": 0, "checks": counts["tests_run"] if counts else 0,
                   "pngs": 0}, "launcher_rc": step["rc"], "execution": step,
        "source_unchanged": before == after, "selected": args.select,
        "regression": args.regression,
    }
    if not passed:
        report["first_cause"] = {"type": "UnittestFailure", "path": step["stderr"]["path"]}
    final = workbench.persist_result(output, report)
    print(f"HTTP_BOUNDARY_LAUNCHER verdict={final['verdict']} rc={final['exit_code']} "
          f"child_rc={step['rc']} counts={counts} source_unchanged={before == after} "
          f"scope={SCOPE} report={output / 'workbench.json'}", flush=True)
    return final["exit_code"]


def main():
    global ARTIFACTS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--select", nargs="+")
    parser.add_argument("--child-root", type=Path)
    parser.add_argument("--regression", action="store_true")
    args = parser.parse_args()
    if args.out:
        return logged_suite(args)
    if args.regression:
        parser.error("--regression requires --out")
    if args.child_root is not None:
        ARTIFACTS = workbench.bounded(args.child_root)
    loader = unittest.defaultTestLoader
    suite = (loader.loadTestsFromNames(args.select, sys.modules[__name__]) if args.select
             else loader.loadTestsFromTestCase(HTTPBoundaryReviewTests))
    discovered = suite.countTestCases()
    if discovered < 1:
        raise ValueError("Empty boundary test selection")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    passed = (result.wasSuccessful() and result.testsRun == discovered and not result.skipped)
    print(f"HTTP_BOUNDARY_UNITTEST verdict={'PASS' if passed else 'FAIL'} "
          f"discovered={discovered} tests_run={result.testsRun} failures={len(result.failures)} "
          f"errors={len(result.errors)} skipped={len(result.skipped)}", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
