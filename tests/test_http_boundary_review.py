"""Focused raw-socket HTTP review; no existing service is used.

Reuse test_server's private 127.0.0.1:0 lifecycle and workbench's evidence
executor. --out records the current candidate, or ordinary unittest discovery
creates a unique external evidence directory. --regression runs only the
unchanged test_server module. Current candidates bind their full inventory
and loaded runtime; --historical-dependency-baseline explicitly selects the
legacy four-dependency constraint, never recertifying old evidence. One real FEM positive
control is not a numerical matrix, production test or release certification.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
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
IMPORT_CANDIDATE = workbench.inventory(ROOT)
import test_server as server_tests
import server
import solver
from tools import render_page

BASELINE = "2a86febdf472ee1429395b8b073e408cf20dcc38"
SCOPE = "PRIVATE_RAW_LOOPBACK_CANDIDATE_BYTES_NOT_PUBLIC_NOT_RELEASE"
ARTIFACTS = None
HISTORICAL_DEPENDENCY_BASELINE = None
ITERATION_BASE = "97477ba9255c1cdb9f8a15d9145a00e61a63188d"
FIXTURE_COMMENT = b"\n# HTTP_ITERATION_FIXTURE_ONLY: no solver behavior change.\n"


def prepare_iteration_fixture(args):
    """Preserve a real legacy startup rejection, not product acceptance."""
    output = workbench.new_output(args.out)
    workbench.write_json(output / "START.json", {
        "state": "RUNNING", "started_at": workbench.now(),
        "scope": "COMMENT_ONLY_GIT_FIXTURE_NOT_PRODUCT", "base": ITERATION_BASE,
    })
    candidate = output / "candidate"
    clone = workbench.execute(
        ["git", "clone", "--local", "--no-hardlinks", "--no-checkout", str(ROOT), str(candidate)],
        ROOT, output, "fixture-clone")
    workbench.check_step(clone)
    workbench.git(candidate, "checkout", "-b", "codex/http-iteration-fixture", ITERATION_BASE)
    before = workbench.inventory(candidate)
    workbench.clean(before)
    workbench.write_json(output / "base-inventory.json", before)
    solver = candidate / "solver.py"
    solver.write_bytes(solver.read_bytes() + FIXTURE_COMMENT)
    workbench.git(candidate, "add", "--", "solver.py")
    workbench.git(candidate, "-c", "user.name=OpenSim HTTP fixture",
                  "-c", "user.email=fixture@invalid", "commit", "-m",
                  "HTTP comment-only iteration fixture; not product change", "--", "solver.py")
    frozen = workbench.inventory(candidate)
    workbench.clean(frozen)
    changed = {name for name in before["tracked_sha256"]
               if before["tracked_sha256"][name] != frozen["tracked_sha256"][name]}
    workbench.need(changed == {"solver.py"}, "Iteration fixture changed unexpected sources", "fixture")
    workbench.write_json(output / "source-before.json", frozen)
    legacy = workbench.execute(
        [sys.executable, "-B", str(candidate / "tests/test_http_boundary_review.py"),
         "--regression", "--out", str(output / "legacy-regression-red-v1")],
        candidate, output, "legacy-regression-red")
    stderr = Path(legacy["stderr"]["path"]).read_text(encoding="utf-8")
    after = workbench.inventory(candidate)
    workbench.write_json(output / "source-after.json", after)
    observed = (legacy["rc"] == 1 and frozen == after and
                "Pinned dependency differs from old baseline: solver.py" in stderr)
    summary = {
        "verdict": "EXPECTED_LEGACY_STARTUP_REJECTION" if observed else "FAIL",
        "scope": "COMMENT_ONLY_GIT_FIXTURE_NOT_PRODUCT", "candidate_root": str(candidate),
        "base": ITERATION_BASE, "historical_dependency_baseline": BASELINE,
        "changed_runtime_paths": sorted(changed), "legacy_execution": legacy,
        "legacy_launcher_rc": legacy["rc"], "legacy_tests_run": 0,
        "source_before": frozen, "source_after": after,
        "source_unchanged": frozen == after, "finished_at": workbench.now(),
        "exit_code": 0 if observed else 1,
    }
    workbench.write_json(output / "summary.json", summary)
    print("HTTP_ITERATION_LEGACY verdict={} legacy_rc={} tests_run=0 "
          "source_unchanged={} candidate={}".format(
              summary["verdict"], legacy["rc"], frozen == after, candidate), flush=True)
    return summary["exit_code"]


def candidate_sources(candidate=None):
    candidate = workbench.inventory(ROOT) if candidate is None else candidate
    if candidate != IMPORT_CANDIDATE:
        raise ValueError("Candidate/source changed since runtime import")
    sources = {name: candidate["tracked_sha256"][name] for name in workbench.SOURCES}
    for module, relative in ((workbench, "tools/workbench_acceptance.py"),
                             (server_tests, "tests/test_server.py"), (server, "server.py"),
                             (solver, "solver.py"), (render_page, "tools/render_page.py")):
        if Path(module.__file__).resolve() != ROOT / relative:
            raise ValueError(f"Runtime module loaded outside candidate: {relative}")
    if server.RUNTIME_SOURCES != sources or tuple(render_page.SOURCE_PATHS) != workbench.SOURCES:
        raise ValueError("Loaded runtime five-source identity differs from candidate")
    server.assert_runtime_sources()
    if HISTORICAL_DEPENDENCY_BASELINE is not None:
        for name in workbench.SOURCES:
            frozen = subprocess.run(
                ["git", "show", f"{HISTORICAL_DEPENDENCY_BASELINE}:{name}"], cwd=ROOT,
                capture_output=True, check=True,
            ).stdout
            if name != "server.py" and sources[name] != hashlib.sha256(frozen).hexdigest():
                raise ValueError(f"Pinned dependency differs from old baseline: {name}")
    return sources


def source_binding():
    return {
        "candidate_mode": "CURRENT_RUNTIME" if HISTORICAL_DEPENDENCY_BASELINE is None
                          else "EXPLICIT_HISTORICAL_FOUR_DEPENDENCIES_SERVER_EXEMPT",
        "dependency_baseline": HISTORICAL_DEPENDENCY_BASELINE,
        "historical_evidence_baseline": BASELINE,
        "historical_evidence_recertified": False,
    }


def drift_suite(output):
    """Only explicit fixture CLI collects this destructive external control."""
    class RuntimeDriftControl(unittest.TestCase):
        def test_running_candidate_source_drift(self):
            workbench.bounded(ROOT)
            before = workbench.inventory(ROOT)
            workbench.clean(before)
            type(self).candidate = before
            type(self).sources = candidate_sources(before)
            workbench.write_json(output / "source-before.json", before)
            target = ROOT / "solver.py"
            original = target.read_bytes()
            workbench.need(original.endswith(FIXTURE_COMMENT),
                           "Running drift requires the owned comment-only fixture", "fixture")
            changed = original + b"\n# HTTP_RUNNING_DRIFT_FIXTURE_ONLY\n"
            server_tests.HTTPTests.setUpClass.__func__(type(self))
            self.assertTrue(self.thread.is_alive())
            try:
                target.write_bytes(changed)
                after = workbench.inventory(ROOT)
                workbench.write_json(output / "source-after.json", after)
                self.assertNotEqual(before, after)
                with self.assertRaisesRegex(ValueError, "^Candidate/source changed since runtime import$") as caught:
                    HTTPBoundaryReviewTests.check_sources.__func__(type(self))
                workbench.write_json(output / "first-cause.json", {
                    "type": type(caught.exception).__name__, "message": str(caught.exception),
                    "listener_alive": self.thread.is_alive(), "changed_path": "solver.py",
                })
                with self.assertRaisesRegex(RuntimeError, "Runtime source changed: solver.py"):
                    server.assert_runtime_sources()
                connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
                try:
                    connection.request("GET", "/api/health")
                    response = connection.getresponse()
                    raw = response.read()
                    workbench.write_json(output / "drift-response.json", {
                        "status": response.status, "response_hex": raw.hex(),
                        "runtime_sources": self.sources, "source_after": after,
                        "scope": "REAL_EXTERNAL_RUNNING_DRIFT_NOT_PRODUCT",
                    })
                    self.assertEqual(response.status, 503)
                    self.assertEqual(json.loads(raw)["error"]["code"], "SOURCE_CHANGED")
                finally:
                    connection.close()
            finally:
                try:
                    workbench.need(target.read_bytes() == changed,
                                   "Owned drift fixture changed unexpectedly; refusing restoration", "fixture")
                    target.write_bytes(original)
                    restored = workbench.inventory(ROOT)
                    workbench.write_json(output / "source-restored.json", restored)
                    self.assertEqual(before, restored)
                    candidate_sources(restored)
                finally:
                    server_tests.HTTPTests.tearDownClass.__func__(type(self))
                    workbench.write_json(output / "listener-stopped.json", {
                        "thread_alive": self.thread.is_alive(), "socket_fileno": self.server.socket.fileno(),
                    })
            self.assertFalse(self.thread.is_alive())
            self.assertEqual(self.server.socket.fileno(), -1)
    return unittest.defaultTestLoader.loadTestsFromTestCase(RuntimeDriftControl)


def verify_iteration_fixture(args):
    """Run actual candidate subprocesses; never rewrite the legacy red tree."""
    output = workbench.new_output(args.out)
    harness_before = workbench.inventory(ROOT)
    workbench.write_json(output / "harness-before.json", harness_before)
    legacy_root = workbench.bounded(args.fixture_root)
    legacy = workbench.inventory(legacy_root)
    workbench.clean(legacy)
    workbench.write_json(output / "START.json", {
        "state": "RUNNING", "started_at": workbench.now(),
        "scope": "COMMENT_ONLY_GIT_FIXTURE_NOT_PRODUCT", "legacy_root": str(legacy_root),
    })
    candidate = output / "candidate"
    clone = workbench.execute(
        ["git", "clone", "--local", "--no-hardlinks", str(legacy_root), str(candidate)],
        ROOT, output, "candidate-clone")
    workbench.check_step(clone)
    paths = ("tests/test_http_boundary_review.py", "docs/testing/HTTP_BOUNDARY_REVIEW.md")
    for relative in paths:
        (candidate / relative).write_bytes((ROOT / relative).read_bytes())
    workbench.git(candidate, "add", "--", *paths)
    workbench.git(candidate, "-c", "user.name=OpenSim HTTP fixture",
                  "-c", "user.email=fixture@invalid", "commit", "-m",
                  "HTTP current-reader fixture; not product change", "--", *paths)
    before = workbench.inventory(candidate)
    workbench.clean(before)
    workbench.write_json(output / "source-before.json", before)
    script = str(candidate / paths[0])
    invalid_options = (
        ["--prepare-iteration-fixture", "--regression"],
        ["--prepare-iteration-fixture", "--historical-dependency-baseline", BASELINE],
        ["--verify-iteration-fixture", "--drift-child", str(output / "unused-drift")],
        ["--fixture-root", str(legacy_root)],
        ["--out", str(output / "unused-out"), "--child-root", str(output / "unused-child")],
        ["--out", str(output / "unused-regression"), "--regression", "--select", "test_controls"],
    )
    for index, options in enumerate(invalid_options):
        step = workbench.execute([sys.executable, "-B", script, *options], candidate,
                                 output, f"invalid-options-{index + 1}")
        workbench.write_json(output / f"invalid-options-{index + 1}-execution.json", step)
        error = Path(step["stderr"]["path"]).read_text(encoding="utf-8")
        workbench.need(step["rc"] == 2 and "error:" in error,
                       "Invalid CLI combination was not rejected", "cli")
    historical = workbench.execute(
        [sys.executable, "-B", script, "--out", str(output / "explicit-historical-red-v1"),
         "--regression", "--historical-dependency-baseline", BASELINE],
        candidate, output, "explicit-historical-red-v1")
    workbench.write_json(output / "explicit-historical-red-v1-execution.json", historical)
    workbench.need(historical["rc"] == 1 and "Pinned dependency differs from old baseline: solver.py"
                   in Path(historical["stderr"]["path"]).read_text(encoding="utf-8"),
                   "Explicit historical regression did not enforce its parent pin", "historical")
    commands = [
        ("current-candidate-green-v1", [sys.executable, "-B", script, "--out",
                                      str(output / "current-candidate-green-v1")], 5),
        ("server-regression-v1", [sys.executable, "-B", script, "--regression", "--out",
                                 str(output / "server-regression-v1")], 14),
        ("ordinary-discover-v1", [sys.executable, "-B", "-m", "unittest", "discover",
                                  "-s", "tests", "-p", "test_http_boundary_review.py", "-v"], 5),
    ]
    executions = []
    for name, argv, count in commands:
        step = workbench.execute(argv, candidate, output, name)
        executions.append(step)
        workbench.write_json(output / (name + "-execution.json"), step)
        workbench.check_step(step)
        if name == "ordinary-discover-v1":
            stderr = Path(step["stderr"]["path"]).read_text(encoding="utf-8")
            workbench.need(re.search(r"\nRan 5 tests in [0-9.]+s\n\nOK\n", stderr) is not None,
                           "Ordinary discovery count/terminal differs", "discovery")
        else:
            report = json.loads((output / name / "workbench.json").read_bytes())
            workbench.need(report["verdict"] == "PASS" and report["launcher_rc"] == 0 and
                           report["source_unchanged"] and report["unittest_counts"] == {
                               "discovered": count, "tests_run": count, "failures": 0,
                               "errors": 0, "skipped": 0}, "Candidate suite receipt differs", name)
    after = workbench.inventory(candidate)
    workbench.write_json(output / "source-after.json", after)
    workbench.need(before == after, "Candidate sources drifted during positive suites", "source")
    # Drift mutates only its owned copy and restores its exact original bytes.
    drift_root = output / "drift-candidate"
    step = workbench.execute(["git", "clone", "--local", "--no-hardlinks", str(candidate),
                              str(drift_root)], ROOT, output, "drift-clone")
    workbench.check_step(step)
    drift = workbench.execute([sys.executable, "-B", str(drift_root / paths[0]),
                               "--drift-child", str(output / "drift-rejection-v1")],
                              drift_root, output, "drift-rejection-v1")
    executions.append(drift)
    workbench.write_json(output / "drift-rejection-v1-execution.json", drift)
    workbench.check_step(drift)
    stdout = Path(drift["stdout"]["path"]).read_text(encoding="utf-8")
    workbench.need("HTTP_BOUNDARY_UNITTEST verdict=PASS discovered=1 tests_run=1 failures=0 errors=0 skipped=0"
                   in stdout, "Drift control did not complete", "drift")
    workbench.need(workbench.inventory(legacy_root) == legacy,
                   "Historical red candidate was modified", "historical")
    harness_after = workbench.inventory(ROOT)
    workbench.write_json(output / "harness-after.json", harness_after)
    workbench.need(harness_before == harness_after, "Harness source changed during suites", "source")
    summary = {
        "verdict": "PASS", "exit_code": 0, "scope": "COMMENT_ONLY_GIT_FIXTURE_NOT_PRODUCT",
        "finished_at": workbench.now(), "source_before": before, "source_after": after,
        "source_unchanged": before == after, "legacy_source_unchanged": True,
        "harness_source_unchanged": harness_before == harness_after,
        "invalid_cli_rejections": len(invalid_options), "explicit_historical_execution": historical,
        "source_sha256": {name: before["tracked_sha256"][name] for name in workbench.SOURCES},
        "tests": {"current_http": 5, "server_regression": 14, "ordinary_discover": 5,
                  "running_drift": 1}, "executions": executions,
    }
    workbench.write_json(output / "summary.json", summary)
    print(f"HTTP_ITERATION_CURRENT verdict=PASS rc=0 tests=5+14+5+1 source_unchanged=True out={output}",
          flush=True)
    return 0


class HTTPBoundaryReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.artifacts = ARTIFACTS if ARTIFACTS is not None else workbench.new_output(
            workbench.EXTERNAL / "resume-s3-http-current-20261010" /
            ("discover-" + uuid4().hex))
        cls.candidate = workbench.inventory(ROOT)
        cls.sources = candidate_sources(cls.candidate)
        workbench.write_json(cls.artifacts / "source-before.json", cls.candidate)
        workbench.write_json(cls.artifacts / "START.json", {
            "state": "RUNNING", "started_at": workbench.now(), "scope": SCOPE,
            "candidate_commit": cls.candidate["commit"], "source_sha256": cls.sources,
            **source_binding(),
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
            **source_binding(), "source_sha256": cls.sources,
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
            "name": name, **source_binding(), "source_sha256": self.sources,
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
    if HISTORICAL_DEPENDENCY_BASELINE is not None:
        argv.extend(["--historical-dependency-baseline", HISTORICAL_DEPENDENCY_BASELINE])
    if args.regression:
        argv = [sys.executable, "-B", "-m", "unittest", "discover",
                "-s", str(ROOT / "tests"), "-p", "test_server.py", "-v"]
    elif args.select:
        argv.extend(["--select", *args.select])
    workbench.write_json(output / "START.json", {
        "state": "RUNNING", "started_at": workbench.now(), "argv": argv, "scope": SCOPE,
        **source_binding(), "candidate_commit": before["commit"],
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
        "scope": SCOPE, **source_binding(),
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
    global ARTIFACTS, HISTORICAL_DEPENDENCY_BASELINE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--select", nargs="+")
    parser.add_argument("--child-root", type=Path)
    parser.add_argument("--regression", action="store_true")
    parser.add_argument("--prepare-iteration-fixture", action="store_true")
    parser.add_argument("--historical-dependency-baseline", choices=[BASELINE],
                        help="Explicit legacy four-source constraint; server remains exempt")
    parser.add_argument("--verify-iteration-fixture", action="store_true")
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument("--drift-child", type=Path)
    args = parser.parse_args()
    modes = (args.prepare_iteration_fixture, args.verify_iteration_fixture,
             args.drift_child is not None)
    if sum(modes) > 1:
        parser.error("Fixture preparation, verification and running drift are exclusive")
    if any(modes) and (args.select or args.regression or args.child_root or
                       args.historical_dependency_baseline is not None):
        parser.error("Fixture modes cannot combine with selection, regression, child-root or historical pin")
    if args.prepare_iteration_fixture:
        if args.out is None or args.fixture_root is not None:
            parser.error("--prepare-iteration-fixture requires only --out")
        return prepare_iteration_fixture(args)
    HISTORICAL_DEPENDENCY_BASELINE = args.historical_dependency_baseline
    if args.verify_iteration_fixture:
        if args.out is None or args.fixture_root is None or args.select or args.regression or args.child_root:
            parser.error("--verify-iteration-fixture requires --out and --fixture-root")
        return verify_iteration_fixture(args)
    if args.fixture_root is not None:
        parser.error("--fixture-root requires --verify-iteration-fixture")
    if args.drift_child is not None and (args.out or args.select or args.regression or args.child_root):
        parser.error("--drift-child is an isolated external fixture control")
    if args.out:
        if args.child_root is not None or (args.regression and args.select):
            parser.error("--out cannot combine with --child-root; regression cannot select HTTP methods")
        return logged_suite(args)
    if args.regression:
        parser.error("--regression requires --out")
    if args.child_root is not None:
        ARTIFACTS = workbench.bounded(args.child_root)
    loader = unittest.defaultTestLoader
    suite = (drift_suite(workbench.new_output(args.drift_child)) if args.drift_child is not None else
             loader.loadTestsFromNames(args.select, sys.modules[__name__]) if args.select
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
