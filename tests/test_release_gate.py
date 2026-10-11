"""Real external files/Git and CLI negative controls; fixtures are NOT releases.

Run this file directly for an append-only external test report and execution
log. No mocks, package installation, service operation or production writes.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import struct
import subprocess
import sys
import unittest
from uuid import uuid4
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import release_gate as gate

PEERS = {
    "tools/release_app.py": Path("/data/dev/opensim-release-v1/tools/release_app.py"),
    "tools/browser_audit.mjs": Path("/data/dev/opensim-audit-v1/tools/browser_audit.mjs"),
    "tools/browser_performance.mjs": Path("/data/dev/opensim-perf-v1/tools/browser_performance.mjs"),
}
BRIDGE_DEPENDENCIES = {
    "obligations": {
        "path": "/data/dev/opensim-authorized-obligations-20261010/tools/release_obligations.py",
        "sha256": "9c1b0d9a464c958d6d32c760ba39173da6134ad771436ddc7a36466ab3a8b4bf",
    },
    "trust": {
        "path": "/data/dev/opensim-authorized-trust-20261010/tools/release_trust.py",
        "sha256": "ce085ad4062327c6d95b6d274f66897fbb2440ab2170404dd03b07986bd59639",
    },
}

NATIVE_ADAPTER_TREE = Path("/data/dev/opensim-release-native-adapter-main-v1")
NATIVE_ASSESSMENT_DEPENDENCIES = {
    NATIVE_ADAPTER_TREE / "tools/release_native_adapter.py":
        "4c998f3d0587a4d09064ced6a76171da37e95cb8bddd24c4d912416fe95b5b00",
    NATIVE_ADAPTER_TREE / "tests/test_release_native_adapter.py":
        "1cd8296076cd2ed2198a4d8fa41f653039ec3c4ebb23e9385ea52a132eaa085c",
    NATIVE_ADAPTER_TREE / "tools/workbench_acceptance.py":
        "633a3b2296a9bf5531c2c311fdd39b83b36a95a5713be3f655865bfaed7552d9",
    NATIVE_ADAPTER_TREE / "tests/test_workbench_acceptance.py":
        "fad6811eafb9573cc954d62d481623e17ba48d63d2ba4199db1b809c505cc316",
    NATIVE_ADAPTER_TREE / "docs/deploy/RELEASE_NATIVE_ADAPTER.md":
        "e210d116920e011d4f9028f4920010faa4d834a8869471f69dc517eed65322dc",
}


class NativeAssessmentTests(unittest.TestCase):
    out = None

    @classmethod
    def setUpClass(cls):
        if cls.out is None:
            cls.out = gate.OUTPUT_ROOT / ("native-assessment-discovery-" + uuid4().hex)
            cls.out.mkdir(parents=True, exist_ok=False)
        for path, pin in NATIVE_ASSESSMENT_DEPENDENCIES.items():
            gate.require(gate.sha(gate.read_file(path)) == pin,
                         "Native assessment fixture dependency drift")
        location = NATIVE_ADAPTER_TREE / "tests/test_release_native_adapter.py"
        spec = importlib.util.spec_from_file_location("assessment_original_fixture", location)
        cls.original = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.original)
        cls.original.AdapterTests.out = cls.out.resolve()
        cls.original.AdapterTests.children = []
        cls.original.AdapterTests.setUpClass()
        cls.support = cls.original.AdapterTests(
            "test_partial_actual_cli_preserves_open_and_original_rc")

    @classmethod
    def tearDownClass(cls):
        cls.original.AdapterTests.tearDownClass()

    def observation(self, *, as_pass=False, failure=False):
        path = self.support.terminal(as_pass=as_pass, failure=failure,
                                     audit_links=None if failure else "pair")
        process = self.support.collected(path)
        output = self.support.cli(path, process, 1 if failure else 3)
        adapter = self.support.adapter
        request = {"schema": "opensim-release-native-assessment-request-v1",
                   "adapter": {"path": str(NATIVE_ADAPTER_TREE / "tools/release_native_adapter.py"),
                               "sha256": NATIVE_ASSESSMENT_DEPENDENCIES[
                                   NATIVE_ADAPTER_TREE / "tools/release_native_adapter.py"]},
                   "observation": {"path": str(output / "adapter.json"),
                                   "sha256": adapter.sha((output / "adapter.json").read_bytes())}}
        directory = self.support.directory("assessment-input")
        manifest = directory / "request.json"
        put(manifest, gate.encode(request))
        return path, process, output, manifest

    def cli(self, manifest, *, extra=(), output=None, stdout=None, late=None):
        directory = self.support.directory("assessment-cli")
        output = output or directory / "assessment.json"
        tool = Path(__file__) if late else ROOT / "tools/release_gate.py"
        argv = [sys.executable, "-B", str(tool),
                "--source-root", str(self.support.root), "--commit", self.support.commit,
                "--native-assessment", str(manifest), "--out", str(output), *extra]
        if late:
            argv += ["--assessment-publication-negative", str(late)]
        child = subprocess.run(argv, cwd=ROOT,
                               stdout=subprocess.PIPE if stdout is None else stdout,
                               stderr=subprocess.PIPE)
        put(directory / "stdout.log", child.stdout if stdout is None else b"")
        put(directory / "stderr.log", child.stderr)
        Fixture.child_records.append({"argv": child.args, "rc": child.returncode,
                                      "stdout": {"path": str(directory / "stdout.log"),
                                                 "captured": stdout is None,
                                                 "bytes": len(child.stdout or b""),
                                                 "sha256": gate.sha(child.stdout or b"")},
                                      "stderr": {"path": str(directory / "stderr.log"),
                                                 "bytes": len(child.stderr), "sha256": gate.sha(child.stderr)},
                                      "report_path": str(output)})
        return child, output

    def test_partial_pair_reaches_real_assessment_without_human_claim(self):
        path, process, output, manifest = self.observation()
        originals = {item: (item.read_bytes(), self.support.adapter.stamp(item.stat()))
                     for item in (path, path.parent / "audit/report.json",
                                  path.parent / "audit/report.candidate.json", process)}
        child, report_path = self.cli(manifest)
        self.assertEqual(child.returncode, 3, child.stderr.decode())
        report = gate.decode(report_path.read_bytes())
        self.assertEqual(report["state"], "OPEN_OBLIGATIONS")
        self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")
        self.assertEqual(report["assessment"]["read_observation_calls"], 2)
        self.assertEqual(report["assessment"]["native_g3_status"], "PARTIAL")
        self.assertEqual(report["assessment"]["closed_obligations"], [])
        self.assertTrue(all(row["status"] == "NOT_TESTED"
                            for row in report["assessment"]["human"]))
        self.assertEqual(report["assessment"]["trust"]["state"], "TRUST_UNAVAILABLE")
        for item, original in originals.items():
            self.assertEqual((item.read_bytes(), self.support.adapter.stamp(item.stat())), original)
        self.assertFalse(report["production_changes"])
        self.assertEqual(report["assessment"]["launcher_scope"], self.support.adapter.FIXTURE_SCOPE)
        log = report_path.with_suffix(".log").read_bytes()
        self.assertEqual(report["execution_log"]["sha256"], gate.sha(log))
        self.assertEqual(report["execution_log"]["path"], str(report_path.with_suffix(".log")))
        self.assertIn(report["invocation_id"].encode(), log)
        self.assertIn(b"finalization=PROVISIONAL", log)
        self.assertIn(("report_sha256=" + gate.sha(report_path.read_bytes())).encode(), child.stdout)
        self.assertIn(("invocation_id=" + report["invocation_id"]).encode(), child.stdout)
        self.assertIn(b"state=OPEN_OBLIGATIONS rc=3 ", child.stdout)
        self.assertEqual(report["finalization"], "FINAL_PROCESS_RC_REQUIRED")

    def reject(self, manifest, reason, *, expected=2, **kwargs):
        child, output = self.cli(manifest, **kwargs)
        self.assertEqual(child.returncode, expected, child.stderr.decode())
        report = gate.decode(output.read_bytes())
        self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")
        self.assertIn(reason, report["failure"]["reason"])
        self.assertIn(reason.encode(), child.stderr)
        return report

    def test_as_pass_pair_is_observed_without_semantic_or_human_closure(self):
        path, _, _, manifest = self.observation(as_pass=True)
        original = path.read_bytes()
        child, output = self.cli(manifest)
        self.assertEqual(child.returncode, 3, child.stderr.decode())
        report = gate.decode(output.read_bytes())
        self.assertEqual(report["assessment"]["native_g3_status"], "AUTOMATED_SCOPE_PASS")
        self.assertEqual(report["assessment"]["read_observation_calls"], 2)
        self.assertEqual(report["assessment"]["coverage"]["state"], "OPEN")
        self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")
        self.assertTrue(all(row["status"] == "NOT_TESTED" for row in report["assessment"]["human"]))
        self.assertEqual(report["assessment"]["public_g5"], {"status": "NOT_TESTED", "waivable": False})
        self.assertEqual(path.read_bytes(), original)

    def test_original_fail_keeps_rc_and_first_cause_in_report_and_log(self):
        _, _, _, manifest = self.observation(failure=True)
        child, output = self.cli(manifest)
        self.assertEqual(child.returncode, 1, child.stderr.decode())
        report = gate.decode(output.read_bytes())
        self.assertEqual(report["state"], "ORIGINAL_PRODUCER_FAILURE_VETO")
        self.assertEqual(report["first_cause"]["message"], "FILE_FIXTURE_FAILURE")
        self.assertEqual(report["assessment"]["read_observation_calls"], 2)
        self.assertIn(b"FILE_FIXTURE_FAILURE", output.with_suffix(".log").read_bytes())
        self.assertIn(b"FILE_FIXTURE_FAILURE", child.stderr)

    def test_failure_sidecars_veto_actual_read_observation(self):
        for original in (False, True):
            with self.subTest(original=original):
                path, _, output, manifest = self.observation(failure=original)
                marker = (path.parent / "persistence-failure-fixture.json" if original else
                          output / "adapter-failure-fixture.json")
                put(marker, b'{"scope":"FILE_ONLY_FAILURE_MARKER"}\n')
                report = self.reject(manifest, "failure")
                self.assertEqual(report["read_observation_calls"], 1)
                if original:
                    self.assertEqual(report["native_observation_diagnostic"]["first_cause"]["message"],
                                     "FILE_FIXTURE_FAILURE")

    def test_wrong_launcher_argv_and_rc_are_rejected_not_report_literals(self):
        for key, value in (("argv", ["not-the-waited-cli"]), ("actual_rc", True)):
            with self.subTest(key=key):
                _, process, output, manifest = self.observation(failure=True)
                body = gate.decode(process.read_bytes())
                body[key] = value
                put(process, gate.encode(body))
                observation = gate.decode((output / "adapter.json").read_bytes())
                observation["process_rc_source"] = self.support.adapter.regular(process)[1]
                observation["launcher"] = body
                put(output / "adapter.json", gate.encode(observation))
                request = gate.decode(manifest.read_bytes())
                request["observation"]["sha256"] = gate.sha((output / "adapter.json").read_bytes())
                put(manifest, gate.encode(request))
                report = self.reject(manifest, "Launcher candidate/report/argv/rc binding differs")
                self.assertEqual(report["read_observation_calls"], 1)

    def test_unknown_third_audit_link_rejected_and_preserved(self):
        path, _, _, manifest = self.observation()
        final = path.parent / "audit/report.json"
        unknown = final.with_name("unknown-third-link.json")
        os.link(final, unknown)
        body = unknown.read_bytes()
        self.reject(manifest, "Workbench producer completion dependency changed")
        self.assertEqual(unknown.read_bytes(), body)
        self.assertEqual(final.stat().st_nlink, 3)

    def test_source_pin_request_version_and_observation_hash_are_strict(self):
        _, _, _, manifest = self.observation(failure=True)
        original = gate.decode(manifest.read_bytes())
        variants = (
            ("schema", "opensim-release-evidence-v2", "Invalid versioned"),
            ("adapter", {**original["adapter"], "sha256": "0" * 64}, "Unapproved native"),
            ("observation", {**original["observation"], "sha256": "0" * 64}, "SHA differs"))
        for key, value, cause in variants:
            with self.subTest(key=key):
                put(manifest, gate.encode({**original, key: value}))
                self.reject(manifest, cause)

    def test_wrong_candidate_and_approval_do_not_open_a_release_path(self):
        _, _, _, manifest = self.observation(failure=True)
        report = self.reject(manifest, "candidate/root mismatch", extra=("--commit", "0" * 40))
        self.assertEqual(report["read_observation_calls"], 1)
        report = self.reject(manifest, "forbids approval", extra=("--approval-path", "/no/approval",
                                                                "--approval-sha256", "0" * 64))
        self.assertEqual(report["read_observation_calls"], 0)

    def test_output_conflict_protects_unknown_bytes_and_inode(self):
        _, _, _, manifest = self.observation(failure=True)
        output = self.support.directory("conflict") / "assessment.json"
        put(output, b"UNKNOWN_PREEXISTING_REPORT\n")
        before = (output.read_bytes(), self.support.adapter.stamp(output.stat()))
        child, _ = self.cli(manifest, output=output)
        self.assertEqual(child.returncode, 1)
        self.assertIn(b"Existing output protected", child.stderr)
        self.assertEqual((output.read_bytes(), self.support.adapter.stamp(output.stat())), before)
        self.assertFalse(output.with_suffix(".log").exists())

    def test_late_raw_stream_and_sidecar_drift_revokes_exact_owned_report(self):
        causes = {"raw": "Invalid JSON report", "stream": "Launcher stream inode/bytes/hash differs",
                  "sidecar": "Adapter failure sidecar present before assessment publication"}
        for kind in ("raw", "stream", "sidecar"):
            with self.subTest(kind=kind):
                path, process, adapter_out, manifest = self.observation(failure=True)
                target = (path if kind == "raw" else
                          Path(gate.decode(process.read_bytes())["stdout"]["path"]) if kind == "stream"
                          else adapter_out / "adapter-failure-late.json")
                child, output = self.cli(manifest, late=target)
                self.assertEqual(child.returncode, 1, child.stderr.decode())
                report = gate.decode(output.read_bytes())
                self.assertEqual(report["finalization"], "FAILED")
                self.assertEqual(report["action_result"]["assessment"]["read_observation_calls"], 2)
                self.assertEqual(report["first_cause"]["message"], "FILE_FIXTURE_FAILURE")
                self.assertIn(b"FILE_FIXTURE_FAILURE", child.stderr)
                self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")
                self.assertIn(causes[kind], report["evidence_failure"]["reason"])
                self.assertEqual(target.read_bytes(), b"LATE_FILE_ONLY_DRIFT\n")

    def test_z_late_dirty_candidate_is_rejected_without_cleaning_unknown_file(self):
        _, _, _, manifest = self.observation(failure=True)
        target = self.support.root / "unknown-late-candidate.txt"
        before = self.support.reader.inventory(self.support.root)
        child, output = self.cli(manifest, late=target)
        self.assertEqual(child.returncode, 1, child.stderr.decode())
        report = gate.decode(output.read_bytes())
        self.assertEqual(report["finalization"], "FAILED")
        self.assertIn("Publication native/source dependency drift", report["evidence_failure"]["reason"])
        self.assertEqual(report["first_cause"]["message"], "FILE_FIXTURE_FAILURE")
        self.assertEqual(target.read_bytes(), b"LATE_FILE_ONLY_DRIFT\n")
        self.assertNotEqual(self.support.reader.inventory(self.support.root), before)
        self.assertEqual(report["action_result"]["assessment"]["read_observation_calls"], 2)

    def test_actual_stdout_enospc_preserves_original_failure_and_nonzero(self):
        _, _, _, manifest = self.observation(failure=True)
        with open("/dev/full", "wb", buffering=0) as full:
            child, output = self.cli(manifest, stdout=full)
        self.assertEqual(child.returncode, 1, child.stderr.decode())
        report = gate.decode(output.read_bytes())
        self.assertEqual(report["finalization"], "FAILED")
        self.assertEqual(report["evidence_failure"]["errno"], 28)
        self.assertEqual(report["first_cause"]["message"], "FILE_FIXTURE_FAILURE")
        self.assertIn(b"FILE_FIXTURE_FAILURE", child.stderr)


def assessment_publication_negative(args):
    """Real API caller: mutate file after report write, before terminal publication."""
    guards = []
    result = gate.assess_native(args.source_root, args.commit, args.native_assessment,
                                publication_guards=guards)
    gate.require(result["assessment"]["read_observation_calls"] == 2 and len(guards) == 1,
                 "Publication negative did not reach real validated assessment")
    target = gate.external(args.assessment_publication_negative)
    calls = 0

    def verify():
        nonlocal calls
        calls += 1
        if calls == 2:
            gate.require(args.out.is_file(), "Late negative did not reach written candidate report")
            put(target, b"LATE_FILE_ONLY_DRIFT\n")
        guards[0]()

    return gate.finalize_result(result, args.out, status_key="state",
                                failed_status="INVALID_NATIVE_ASSESSMENT",
                                label="OPENSIM_NATIVE_ASSESSMENT", verify=verify,
                                emit=gate.native_assessment_stdout)



def png(width: int = 1, height: int = 1) -> bytes:
    def chunk(kind, body):
        return struct.pack("!I", len(body)) + kind + body + struct.pack("!I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(b"\0" * ((width * 3 + 1) * height))) + chunk(b"IEND", b""))


def put(path: Path, body: bytes) -> None:
    gate.external(path, gate.OUTPUT_ROOT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    if path.read_bytes() != body:
        raise AssertionError(f"Fixture readback failed: {path}")


def command(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                                     OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1"))
    if result.returncode:
        raise AssertionError(f"Fixture command rc={result.returncode}: {argv}: {result.stderr}")
    return result


def native_assets(directory: Path, report: dict, expected: dict, collected: str) -> None:
    """Real files for the native contract, explicitly synthetic and never product evidence."""
    report["matrix"], report["screenshots"], report["injections"] = [], [], []
    for width, height in ((320, 568), (768, 1024), (1064, 768), (1440, 900)):
        report["matrix"].append({"width": width, "height": height})
        body = png(width, height)
        for state in ("loading-empty", "ready", "error", "empty", "long",
                      "error-recovery", "empty-recovery", "long-recovery"):
            phase = f"{width}-{state}"
            for name in (phase, phase + "-tile-000"):
                put(directory / (name + ".png"), body)
                report["screenshots"].append({"file": name + ".png", "sha256": gate.sha(body),
                    "width": width, "height": height, "url": report["url"], "state": name})
            put(directory / (phase + ".coverage.json"), gate.encode({
                "status": "PASS", "scale": 1, "horizontal_coverage": "COMPLETE",
                "document": {"width": width, "height": height}, "vertical_coverage": height,
                "tiles": [{"file": phase + "-tile-000.png", "document_y": 0,
                           "viewport": {"width": width, "height": height}}]}))
        for mode in ("hold", "error", "empty", "long"):
            phase = f"{width}-loading-empty" if mode == "hold" else f"{width}-{mode}"
            report["injections"].append({"mode": mode, "phase": phase,
                "request_id": "FIXTURE-" + phase, "url": report["url"].rstrip("/") + "/api/solve",
                "status": 503 if mode == "error" else 200, "body_sha256": "c" * 64,
                "real_mesh_nodes": 1, "injected_warning_length": 100})
    report["coverage_counts"] = {"matrix": 4, "screenshots": len(report["screenshots"]),
                                 "checks": len(report["checks"]), "injected_requests": 16}
    # The digest is a CDP metadata fixture, not a raw HTTP response rehash.
    url = report["url"].rstrip("/") + "/api/solve"
    post = {"url": url, "status": 200, "injection": None, "bytes": 100,
            "body_sha256": "c" * 64, "test_fixture": "SYNTHETIC_NOT_HTTP_OR_FEM",
            "payload": {"mesh": {"nodes_mm": [[0, 0]]}, "fields": {"velocity_m_s": [0]},
                        "reproducibility": {"source_sha256": expected["source_sha256"],
                            "solver_sha256": expected["source_sha256"]["solver.py"], "computed_at": collected}}}
    put(directory / "http-solve-000.json", gate.encode(post))
    report["post_source_checks"] = [{"artifact": "http-solve-000.json", "url": url,
        "source_sha256": expected["source_sha256"], "body_sha256": "c" * 64, "verdict": "PASS"}]
    for index, status, injection in ((1, 503, "error"), (2, 200, "empty")):
        put(directory / f"http-solve-{index:03}.json", gate.encode({
            "url": url, "status": status, "injection": injection, "payload": None,
            "bytes": 0 if injection == "empty" else 10, "body_sha256": "d" * 64}))


class Fixture:
    base = gate.OUTPUT_ROOT
    child_records = []

    def __init__(self, approval: dict | None = None, *, bridge: bool = False):
        self.directory = self.base / ("fixture-" + uuid4().hex)
        self.repo = self.directory / "repo"
        self.repo.mkdir(parents=True)
        self.now = datetime.now(timezone.utc)
        self.time = self.now.isoformat()
        fixture_files = set((*gate.SOURCES, *gate.TOOLS, "requirements.txt",
                             *(("tools/ui_contract_test.mjs",) if bridge else ())))
        for name in fixture_files:
            if name == "tools/workbench_acceptance.py":
                put(self.repo / name, gate.git(Path("/data/dev/opensim"), "show",
                    "75beb17b230f86c016621234c3b9df96809ec04f:" + name))
                continue
            source = ROOT / name
            if not source.exists():
                source = PEERS[name]
            put(self.repo / name, source.read_bytes())
        if approval:
            put(self.repo / "docs/approvals.json", gate.encode({
                "schema": "opensim-user-approvals-v1", "records": [approval]}))
        command(["git", "init", "--initial-branch=fixture"], self.repo)
        command(["git", "add", "--", *sorted(fixture_files),
                 *(["docs/approvals.json"] if approval else [])], self.repo)
        command(["git", "-c", "user.name=Gate Test Fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "core.hooksPath=/dev/null", "commit", "-m", "FIXTURE_NOT_RELEASE"], self.repo)
        self.commit = command(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        self.identity = gate.identity(self.repo, self.commit)
        self.approval_pin = None
        if approval:
            self.approval_pin = {"path": "docs/approvals.json",
                                 "sha256": gate.sha((self.repo / "docs/approvals.json").read_bytes())}
        self.ledger_path = self.directory / "ledger.json"
        self.reports = {}
        edited = datetime.fromtimestamp(int(max((self.repo / n).stat().st_mtime for n in gate.SOURCES)),
                                        timezone(timedelta(hours=8))).isoformat()
        html = f'<html><footer>{self.identity["page_build"]}</footer></html>'.encode()
        put(self.directory / "page/index-v1.html", html)
        page_check = {"verdict": "PASS", "html_sha256": gate.sha(html),
                      "source_sha256_prefix": self.identity["page_build"], "edited_at": edited}
        self.store("page-check", page_check)
        page = {"schema": "opensim-page-evidence-v1", "commit": self.commit,
                "source_sha256": self.identity["source_sha256"],
                "page_build": self.identity["page_build"],
                "timestamp_origin": "SOURCE_MTIME", "source_epoch": None,
                "html": {"path": str(self.directory / "page/index-v1.html"), "sha256": gate.sha(html)},
                "page_check": self.pin("page-check")}
        local = {"verdict": "PASS", "collected_at": self.time,
                 "source_sha256": {**self.identity["source_sha256"], "tools/validate_numerics.py":
                                  self.identity["tool_sha256"]["tools/validate_numerics.py"]},
                 "steps": [{"argv": ["fixture", name], "rc": 0, "stdout": "Ran 7 tests\nOK\n",
                            "stderr": ""} for name in ("unittest", "node", "numeric", "page", "repeat")]}
        numerics = {"state": "COMPLETE", "verdict": "PASS", "collected_at": self.time,
                    "solver": {"sha256": self.identity["source_sha256"]["solver.py"]},
                    "checks_executed": 3, "cases": [{"verdict": "PASS"}],
                    "rejections": [{"verdict": "PASS"}],
                    "convergence": {n: {"verdict": "PASS", "resolutions": [8, 16, 32],
                                       "relative_flow_error": {"errors": [0.1, 0.05, 0.01]}}
                                    for n in ("circle", "rectangle", "square")}}
        browser = self.common("tools/browser_check.mjs")
        browser.update({"mode": "OPENSIM_ACCEPTANCE", "verdict": "AUTOMATED_PASS",
                        "release_verdict": "NOT_CERTIFIED",
                        "page_build": {"build_text": f'fixture build {self.identity["page_build"]}',
                                       "edited_at": edited},
                        "matrix": [{"width": w, "height": h, "status": "PASS"} for w, h in gate.VIEWPORTS],
                        "checks": [{"name": name, "status": "PASS", "rule": rule} for name, rule in
                                   (("structure", "G1-structure"), ("layout", "G2-layout"),
                                    ("keyboard", "G4-keyboard"))]})
        # Deliberately use native browser schema without a top-level source map.
        browser.pop("source_sha256")
        audit = self.common("tools/browser_audit.mjs")
        audit.update({"mode": "G3_G6_AUTOMATIC_EXTENSION_NOT_RELEASE", "verdict": "AUTOMATED_SCOPE_PASS",
                      "schema_version": 1, "tool": "opensim-browser-audit",
                      "release_verdict": "NOT_CERTIFIED", "coverage": [
                          {"rule": rule, "status": "NOT_TESTED"} for rule in
                          ("WEB-A11Y-009", "PHYSICAL-TOUCH", "SCREENSHOT-REVIEW",
                           "FULL-STANDARD-MATRIX", "G5-performance")],
                      "url": gate.PUBLIC_URL + "/",
                      "page_build": {"id": self.identity["page_build"],
                                     "footer": "FIXTURE build " + self.identity["page_build"]},
                      "gates": {name: {"status": "PARTIAL" if name == "G3-accessibility" else "PASS",
                                       "release_status": "NOT_CERTIFIED"}
                                for name in ("G3-accessibility", "G6-content")},
                      "checks": []})
        observed = {"url": audit["url"], "verdict": "PASS", "fixture_asset_override": False,
                    "footer": audit["page_build"]["footer"],
                    "local_source_sha256": self.identity["source_sha256"],
                    "committed_source_sha256": self.identity["source_sha256"],
                    "health": {"status": "ok", "source_sha256": self.identity["source_sha256"],
                               "page_build": self.identity["page_build"]},
                    "served_byte_sha256": {"health": "a" * 64, "/": "b" * 64,
                        "/web/app.js": self.identity["source_sha256"]["web/app.js"],
                        "/web/app.css": self.identity["source_sha256"]["web/app.css"]}}
        audit["identity_checks"] = [{**copy.deepcopy(observed), "stage": stage}
                                    for stage in ("before", "after")]
        perf = self.common("tools/browser_performance.mjs")
        perf.update({"scope": "PUBLIC_LAB", "verdict": "PUBLIC_G5_PASS", "g5_certified": True,
                     "target_url": gate.PUBLIC_URL, "baseline_commands_acknowledged": True,
                     "baseline": {"id": "opensim-mobile-cold-v1", "width": 360, "height": 800,
                                  "device_scale_factor": 1, "mobile": True, "touch": True,
                                  "latency_ms": 150, "download_bytes_s": 200000, "upload_bytes_s": 93750,
                                  "cpu_rate": 4, "observation_ms": 8000, "cache_disabled": True,
                                  "service_worker_bypassed": True, "samples": 1, "timeout_ms": 30000,
                                  "retries": 0}, "browser": {"product": "Chrome/fixture"},
                     "public_dns": [{"address": "8.8.8.8"}],
                     "samples": [{"target_url": gate.PUBLIC_URL,
                                  "source_identity": {"bound": True, "assets_bound": True,
                                                      "local_source_sha256": self.identity["source_sha256"],
                                                      "committed_source_sha256": self.identity["source_sha256"],
                                                      "solve_source_sha256": self.identity["source_sha256"],
                                                      "health": {"source_sha256": self.identity["source_sha256"],
                                                                 "page_build": self.identity["page_build"]}},
                                  "phase": "ONE_COLD_NAVIGATION_PLUS_THREE_CORE_TOUCHES",
                                  "raw_metrics": {"lcp": [{"startTime": 100}], "shifts": [],
                                                  "supported": ["layout-shift"], "observer_errors": []},
                                  "interactions": [{"selector": selector, "click_count": 1,
                                                    "latency_ms": 10, "event_timing": [],
                                                    "paints": [{"type": "click", "trusted": True,
                                                                "start_ms": 1, "end_ms": 11, "bound_ms": 10}]}
                                                   for selector in ("#view-mesh", "#view-field", "#solve-button")],
                                  "metrics": {"lcp_ms": 100, "interaction_latency_ms": 10, "cls": 0,
                                              "lcp_count": 1, "interaction_count": 3, "cls_observed": True},
                                  "fonts": {"ready": True, "errors": [], "preloads": 0,
                                            "fallback_verified": True, "faces": []},
                                  "third_party": [], "isolation": "BLOCK_ALL_OFF_ORIGIN_BEFORE_REQUEST",
                                  "redirects": [], "network_errors": [], "runtime_errors": [], "images": [],
                                  "content": {"ready": True, "text_length": 101, "canvas_colors": 4,
                                              "solve_count": 2, "nodes": 11, "triangles": 11, "max_velocity": 1},
                                  "resources": [{"url": gate.PUBLIC_URL + "/fixture", "type": name,
                                                 "transfer_bytes": 32, "finished": True, "cached": False,
                                                 "mime": "text/javascript", "decoded_body_bytes": 1024,
                                                 "encoding": "gzip", "fingerprinted": True,
                                                 "body_sha256": "a" * 64, "fingerprint_sha256": "a" * 64,
                                                 "cache_control": "public, max-age=31536000, immutable",
                                                 "remote_ip": "8.8.8.8", "status": 200}
                                                for name in ("Document", "Stylesheet", "Script")]}],
                     "checks": [{"name": f"perf-{i}", "rule": f"WEB-PERF-{i:03}", "status": "PASS"}
                                for i in range(1, 7)]})
        public = {"verdict": "PASS", "scope": "PUBLIC_CODE_AND_FEM", "url": gate.PUBLIC_URL,
                  "desired_git_head": self.commit, "source_sha256": self.identity["source_sha256"],
                  "page_build": self.identity["page_build"], "collected_at": self.time,
                  "dns_addresses": ["8.8.8.8"],
                  "checks": [{"name": name, "verdict": "PASS"} for name in
                             ("runtime_identity", "page_and_assets", "real_fem_circle", "real_fem_rectangle")]}
        for row in public["checks"]:
            if row["name"].startswith("real_fem_"):
                row.update({"config_sha256": "a" * 64, "solver_sha256": self.identity["source_sha256"]["solver.py"],
                            "node_count": 11, "flow_rate_m3_s": 0.01, "relative_residual": 1e-9,
                            "computed_at": self.time})
        for kind, report in (("page", page), ("local", local), ("numerics", numerics),
                             ("browser", browser), ("audit", audit), ("performance", perf), ("public", public)):
            if kind == "browser":
                report["screenshots"] = [self.screenshot(kind)]
            if kind == "audit":
                native_assets(self.directory / kind, report, self.identity, self.time)
            self.store(kind, report)
        self.ledger = {"schema": "opensim-release-evidence-v1", "identity": self.identity,
                       "evidence": {k: self.pin(k) for k in
                                    ("page", "local", "numerics", "browser", "audit", "performance", "public")},
                       "executions": {}, "gates": {}, "manual": {}, "exceptions": []}
        for kind, tool in {
            "local": "tools/acceptance.py", "numerics": "tools/validate_numerics.py",
            "browser": "tools/browser_check.mjs", "audit": "tools/browser_audit.mjs",
            "performance": "tools/browser_performance.mjs", "public": "tools/public_gate.py",
            "page": "tools/render_page.py",
        }.items():
            self.launch(kind, tool)
        for name, kind, chosen in (
            ("G1-structure", "browser", "structure"), ("G2-layout", "browser", "layout"),
            ("G3-accessibility", "audit", "accessibility"), ("G4-keyboard", "browser", "keyboard"),
            ("G5-performance", "performance", "perf-1"), ("G6-content", "audit", "content"),
        ):
            self.ledger["gates"][name] = {"evidence": kind, "rc": 0, "status": "PASS", "checks": [chosen]}
            selected = []
            for rule in sorted(gate.REQUIRED_RULES[name]):
                check_name = name + ":" + rule
                self.reports[kind]["checks"].append({
                    "name": check_name, "rule": rule, "status": "PASS",
                    "url": gate.PUBLIC_URL, "viewport": [320, 568],
                    "reproduce": "FIXTURE_NOT_BROWSER_CHECK"})
                if kind == "audit":
                    self.reports[kind]["checks"][-1].update(
                        url=audit["url"], viewport={"width": 320, "height": 568}, state="320-ready",
                        reproduce=["FIXTURE_NOT_BROWSER_CHECK"])
                    self.reports[kind]["checks"][-1].pop("name")
                selected.append(check_name)
            self.ledger["gates"][name]["checks"] = (
                [{"pointer": f"/checks/{i}", "sha256": gate.sha(gate.encode(row))}
                 for i, row in enumerate(self.reports[kind]["checks"])
                 if row.get("rule") in gate.REQUIRED_RULES[name]] if kind == "audit" else selected)
            self.change(kind)
        for rule in gate.MANUAL:
            report = {"schema": "opensim-human-check-v1", "rule_id": rule, "status": "PASS",
                      "commit": self.commit, "source_sha256": self.identity["source_sha256"],
                      "page_build": self.identity["page_build"], "mode": "REAL_DEVICE_HUMAN",
                      "owner": "FIXTURE_NOT_HUMAN", "device": "FIXTURE", "combination_version": "FIXTURE",
                      "url": gate.PUBLIC_URL, "reproduce": "FIXTURE_NOT_RELEASE", "collected_at": self.time,
                      "checks": [{"status": "PASS"}], "screenshots": [self.screenshot(rule)]}
            self.store(rule, report)
            self.ledger["manual"][rule] = self.pin(rule)
        self.save()

    def common(self, tool: str) -> dict:
        return {"commit": self.commit, "source_sha256": self.identity["source_sha256"],
                "page_build": self.identity["page_build"], "tool_sha256": self.identity["tool_sha256"][tool],
                "standard": "shared-r41", "exit_code": 0, "finished_at": self.time}

    def screenshot(self, kind: str) -> dict:
        put(self.directory / kind / "fixture.png", png())
        return {"file": "fixture.png", "sha256": gate.sha(png())}

    def store(self, kind: str, report: dict) -> None:
        if kind == "audit":
            report["coverage_counts"]["checks"] = len(report["checks"])
            final = self.directory / "audit/report.json"
            line = "AUDIT candidate_verdict={} candidate_rc={} report={}\n".format(
                report["verdict"], report.get("exit_code", 0), final).encode()
            report["completion"] = {"protocol": "durable-final-v2", "status": "FINAL",
                                    "receipt": "completion.json", "log": "logs/audit.log",
                                    "log_sha256": gate.sha(line)}
            put(final.parent / "logs/audit.log", line)
            put(final.parent / "completion.json", gate.encode({
                "protocol": "durable-final-v2", "status": "COMPLETE", "exit_code": report.get("exit_code", 0),
                "report": "report.json", "report_sha256": gate.sha(gate.encode(report)),
                "log_sha256": gate.sha(line)}))
        self.reports[kind] = report
        put(self.directory / kind / "report.json", gate.encode(report))

    def pin(self, kind: str) -> dict:
        path = self.directory / kind / "report.json"
        return {"path": str(path), "sha256": gate.sha(path.read_bytes())}

    def change(self, kind: str, **updates) -> None:
        self.store(kind, {**self.reports[kind], **updates})
        self.ledger["evidence"][kind] = self.pin(kind)
        old = self.reports.get("launcher-" + kind)
        if old:
            self.launch(kind, old["tool"])
        self.save()

    def launch(self, kind: str, tool: str) -> None:
        stdout = self.directory / kind / "launcher.stdout"
        stderr = self.directory / kind / "launcher.stderr"
        put(stdout, b"FIXTURE_NOT_PRODUCER_RUN\n")
        put(stderr, b"FIXTURE_NO_REAL_RELEASE_EVIDENCE\n")
        report = {"schema": "opensim-producer-execution-v1", "identity": self.identity,
                  "tool": tool, "argv": ["fixture-not-producer", str(self.repo / tool)], "rc": 0,
                  "report": self.pin(kind), "collected_at": self.time,
                  "stdout": {"path": str(stdout), "sha256": gate.sha(stdout.read_bytes())},
                  "stderr": {"path": str(stderr), "sha256": gate.sha(stderr.read_bytes())}}
        self.store("launcher-" + kind, report)
        self.ledger["executions"][kind] = self.pin("launcher-" + kind)

    def save(self) -> None:
        put(self.ledger_path, gate.encode(self.ledger))

    def evaluate(self) -> dict:
        return gate.evaluate(self.repo, self.commit, self.ledger_path, self.approval_pin)

    def cli(self) -> tuple[subprocess.CompletedProcess, dict]:
        out = self.directory / ("cli-" + uuid4().hex + ".json")
        argv = [sys.executable, "-B", str(ROOT / "tools/release_gate.py"),
                "--source-root", str(self.repo), "--commit", self.commit,
                "--ledger", str(self.ledger_path), "--out", str(out)]
        if self.approval_pin:
            argv += ["--approval-path", self.approval_pin["path"],
                     "--approval-sha256", self.approval_pin["sha256"]]
        actual = subprocess.run(argv, cwd=ROOT, capture_output=True,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        streams = {}
        for label, body in (("stdout", actual.stdout), ("stderr", actual.stderr)):
            path = out.with_suffix("." + label)
            put(path, body)
            streams[label] = {"path": str(path), "bytes": len(body), "sha256": gate.sha(body)}
        self.child_records.append({"argv": argv, "actual_rc": actual.returncode,
                                   "report": {"path": str(out), "sha256": gate.sha(out.read_bytes())},
                                   **streams, "fixture": str(self.directory),
                                   "scope": "ACTUAL_GATE_CLI_FILE_ONLY_NOT_PRODUCT"})
        result = subprocess.CompletedProcess(argv, actual.returncode,
                                             actual.stdout.decode(), actual.stderr.decode())
        put(out.with_suffix(".subprocess.log"),
            (f"rc={result.returncode}\n" + result.stdout + result.stderr).encode())
        return result, gate.decode(out.read_bytes())


def approval(rule: str = "PHYSICAL-TOUCH", expired: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    return {"id": "EXPLICIT-FIXTURE-APPROVAL", "rule_id": rule, "owner": "fixture-owner",
            "expires_at": (now + timedelta(days=-1 if expired else 1)).isoformat(),
            "reason": "FIXTURE_NOT_USER_APPROVAL", "scope": gate.PUBLIC_URL,
            "removal_condition": "fixture condition", "status": "APPROVED", "approved_by": "user",
            "approved_at": (now - timedelta(days=2)).isoformat(), "approval_record_id": "fixture-record"}


class BridgeTests(unittest.TestCase):
    def prepare(self, status="PARTIAL", approval_record=None):
        fixture = Fixture(approval_record, bridge=True)
        fixture.reports["audit"].update(
            findings={"failure_count": 0, "failure_rules": []},
            focus_samples=[], page_capabilities={"dialogs": 0, "mobile_menus": 0})
        fixture.reports["audit"]["gates"]["G3-accessibility"]["status"] = status
        fixture.change("audit")
        fixture.ledger["schema"] = "opensim-release-evidence-v2"
        fixture.ledger["gates"]["G3-accessibility"]["status"] = status
        mapped = gate.native_obligations(fixture.pin("audit"), fixture.identity, 0, fixture.repo,
                                        BRIDGE_DEPENDENCIES, fixture.now)
        fixture.store("native-closure", copy.deepcopy(mapped))
        fixture.ledger["bridge"] = {
            "implementations": copy.deepcopy(BRIDGE_DEPENDENCIES),
            "native_closure": fixture.pin("native-closure"), "canonical": None,
            "provenance": {"human": {}, "exceptions": {}}}
        fixture.save()
        return fixture

    def blocked(self, fixture, needle):
        before = gate.encode(fixture.reports["audit"])
        identity = gate.identity(fixture.repo, fixture.commit)
        child, report = fixture.cli()
        self.assertEqual(child.returncode, 1, child.stderr)
        self.assertIn(needle, report["failure"]["reason"])
        self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")
        self.assertFalse(report["production_changes"])
        self.assertEqual(gate.identity(fixture.repo, fixture.commit), identity)
        self.assertEqual((fixture.directory / "audit/report.json").read_bytes(), before)
        return report

    def test_native_partial_advances_past_raw_pass_contradiction(self):
        fixture = self.prepare()
        report = self.blocked(fixture, "TRUST_UNAVAILABLE")
        self.assertTrue(report["bridge"]["closure"]["native"]["open"])
        self.assertEqual(report["bridge"]["trust"]["production"]["state"], "TRUST_UNAVAILABLE")
        self.assertEqual({row["state"] for row in report["bridge"]["trust"]["materials"]},
                         {"MISSING_PROVENANCE"})
        self.assertEqual(fixture.reports["audit"]["gates"]["G3-accessibility"]["status"], "PARTIAL")

    def test_native_automated_scope_advances_past_raw_pass_contradiction(self):
        fixture = self.prepare("AUTOMATED_SCOPE_PASS")
        report = self.blocked(fixture, "TRUST_UNAVAILABLE")
        self.assertEqual(report["bridge"]["closure"]["native"]["first_cause"]["code"],
                         "NATIVE_G3_ADAPTER_UNSUPPORTED")
        self.assertEqual(fixture.reports["audit"]["gates"]["G3-accessibility"]["status"],
                         "AUTOMATED_SCOPE_PASS")

    def test_raw_pass_is_still_rejected_by_original_reader(self):
        fixture = self.prepare()
        fixture.reports["audit"]["gates"]["G3-accessibility"]["status"] = "PASS"
        fixture.change("audit")
        self.blocked(fixture, "Native audit G3 cannot claim full PASS")

    def test_native_failure_cannot_be_covered_by_claimed_canonical_closed(self):
        fixture = self.prepare()
        fixture.ledger["bridge"]["canonical"] = {"verdict": "AUTOMATION_SCOPE_CLOSED"}
        fixture.reports["audit"]["checks"][0]["status"] = "FAIL"
        fixture.change("audit")
        self.blocked(fixture, "Audit failed/unknown checks")

    def test_native_coordinate_closure_must_equal_actual_mapping(self):
        fixture = self.prepare()
        fixture.reports["native-closure"]["open"][0]["coordinate"]["viewport"] = [321, 568]
        fixture.store("native-closure", fixture.reports["native-closure"])
        fixture.ledger["bridge"]["native_closure"] = fixture.pin("native-closure")
        fixture.save()
        self.blocked(fixture, "Native closure differs from original coordinates/source binding")

    def test_native_closure_source_binding_cannot_change(self):
        fixture = self.prepare()
        fixture.reports["native-closure"]["candidate"]["commit"] = "0" * 40
        fixture.store("native-closure", fixture.reports["native-closure"])
        fixture.ledger["bridge"]["native_closure"] = fixture.pin("native-closure")
        fixture.save()
        self.blocked(fixture, "Native closure differs from original coordinates/source binding")

    def test_missing_closure_material_is_fail_loud(self):
        fixture = self.prepare()
        fixture.ledger["bridge"]["native_closure"]["path"] = str(fixture.directory / "missing.json")
        fixture.save()
        self.blocked(fixture, "No such file")

    def test_dependency_hash_mismatch_no_fallback(self):
        fixture = self.prepare()
        fixture.ledger["bridge"]["implementations"]["obligations"]["sha256"] = "0" * 64
        fixture.save()
        self.blocked(fixture, "Bridge dependency source hash mismatch")

    def test_manual_missing_and_stale_still_refuse(self):
        fixture = self.prepare()
        fixture.ledger["manual"] = {}
        fixture.save()
        self.blocked(fixture, "Missing real device/manual gate records")
        fixture = self.prepare()
        rule = gate.MANUAL[0]
        fixture.store(rule, {**fixture.reports[rule],
                            "collected_at": (fixture.now - timedelta(days=2)).isoformat()})
        fixture.ledger["manual"][rule] = fixture.pin(rule)
        fixture.save()
        self.blocked(fixture, "Stale/future evidence")

    def test_editable_proof_never_creates_production_enrollment(self):
        fixture = self.prepare()
        fixture.store("envelope", {"test_fixture": "FILE_ONLY_NOT_HUMAN_PROVENANCE"})
        fixture.store("revocations", {"test_fixture": "FILE_ONLY_NOT_TRUST"})
        fixture.ledger["bridge"]["provenance"]["human"][gate.MANUAL[0]] = {
            "envelope": fixture.pin("envelope"), "revocations": fixture.pin("revocations")}
        fixture.save()
        report = self.blocked(fixture, "TRUST_UNAVAILABLE")
        material = next(row for row in report["bridge"]["trust"]["materials"]
                        if row["id"] == gate.MANUAL[0] and row["role"] == "human")
        self.assertEqual(material["state"], "TRUST_UNAVAILABLE")

    def test_fabricated_trusted_state_rejected(self):
        fixture = self.prepare()
        fixture.ledger["bridge"]["provenance"]["human"][gate.MANUAL[0]] = {"state": "TRUSTED"}
        fixture.save()
        self.blocked(fixture, "Provenance requires pinned envelope/revocations")

    def test_exact_exception_requires_separate_user_provenance(self):
        fixture = self.prepare(approval_record=approval())
        fixture.ledger["exceptions"] = ["EXPLICIT-FIXTURE-APPROVAL"]
        fixture.ledger["manual"]["PHYSICAL-TOUCH"] = {"status": "EXCEPTION"}
        fixture.save()
        report = self.blocked(fixture, "TRUST_UNAVAILABLE")
        self.assertIn({"role": "user-approval", "id": "EXPLICIT-FIXTURE-APPROVAL",
                       "material_sha256": gate.sha(gate.encode(approval_record := gate.approvals(
                           fixture.repo, fixture.commit, fixture.approval_pin, fixture.now)[
                               "EXPLICIT-FIXTURE-APPROVAL"])),
                       "state": "MISSING_PROVENANCE"}, report["bridge"]["trust"]["materials"])
        self.assertEqual(approval_record["rule_id"], "PHYSICAL-TOUCH")

    def test_expired_exception_and_public_g5_nonwaivable(self):
        fixture = Fixture(approval(expired=True), bridge=True)
        fixture.ledger["schema"] = "opensim-release-evidence-v2"
        fixture.ledger["bridge"] = {}
        fixture.save()
        self.blocked(fixture, "Expired/future exception")
        fixture = self.prepare(approval_record=approval("G5-performance"))
        fixture.ledger["exceptions"] = ["EXPLICIT-FIXTURE-APPROVAL"]
        fixture.save()
        self.blocked(fixture, "Missing public G5 cannot be waived")

    def test_local_public_budget_cannot_be_waived_by_bridge(self):
        fixture = self.prepare()
        fixture.change("performance", scope="LOCAL_PREFLIGHT")
        self.blocked(fixture, "G5 requires PUBLIC_LAB")

    def test_gate_record_cannot_upgrade_native_partial(self):
        fixture = self.prepare()
        fixture.ledger["gates"]["G3-accessibility"]["status"] = "PASS"
        fixture.save()
        self.blocked(fixture, "Bridge gate must retain original native status")

    def canonical(self, fixture, *, untested=False):
        obligations, _ = gate.bridge_algorithms(BRIDGE_DEPENDENCIES, fixture.repo, fixture.identity)
        identity = obligations.candidate(fixture.repo, fixture.commit)
        policy = obligations.build_policy(identity, [{"url": gate.PUBLIC_URL,
            "objects": [{"id": "FILE_ONLY_" + kind, "kind": kind} for kind in obligations.KINDS]}])
        coordinates, unsupported = obligations.coordinates(policy)
        self.assertFalse(unsupported)
        report = {"schema": obligations.OBSERVATION, "mode": "AUTOMATED_PROTOCOL_OBSERVATION",
                  "candidate": identity, "tool": {"path": "tools/browser_audit.mjs",
                      "sha256": fixture.identity["tool_sha256"]["tools/browser_audit.mjs"]},
                  "exit_code": 0, "execution_source": "FILE_ONLY_NOT_PRODUCT_EXECUTION",
                  "release_verdict": "NOT_CERTIFIED", "scans": obligations.scans_expected(policy),
                  "checks": [{"coordinate": row, "status": "PASS",
                              "detail": {"scope": "FILE_PROTOCOL_ONLY_NOT_REAL_BROWSER"}}
                             for row in coordinates], "failure": None}
        if untested:
            report["checks"][0]["status"] = "NOT_TESTED"
        fixture.store("canonical-policy", policy)
        fixture.store("canonical-original", report)
        supplements = []
        if untested:
            supplement = copy.deepcopy(report)
            supplement["checks"] = [{**report["checks"][0], "status": "PASS"}]
            fixture.store("canonical-supplement", supplement)
            supplements.append(fixture.pin("canonical-supplement"))
        fixture.store("canonical-ledger", {"schema": obligations.SCHEMA,
                      "policy_sha256": fixture.pin("canonical-policy")["sha256"],
                      "baseline": fixture.pin("canonical-original"), "supplements": supplements})
        fixture.ledger["bridge"]["canonical"] = {
            "policy": fixture.pin("canonical-policy"), "ledger": fixture.pin("canonical-ledger")}
        fixture.save()
        return report

    def test_canonical_closed_does_not_close_native_or_human(self):
        fixture = self.prepare()
        self.canonical(fixture, untested=True)
        report = self.blocked(fixture, "TRUST_UNAVAILABLE")
        closure = report["bridge"]["closure"]
        self.assertEqual(closure["canonical"]["verdict"], "AUTOMATION_SCOPE_CLOSED")
        self.assertEqual(closure["canonical"]["open"], [])
        self.assertEqual(closure["canonical"]["original_observations"][0]["checks"][0]["status"],
                         "NOT_TESTED")
        self.assertTrue(closure["native"]["open"])
        self.assertEqual(closure["native"]["original_observations"][0]["gates"]
                         ["G3-accessibility"]["status"], "PARTIAL")
        self.assertEqual(closure["canonical_authority"], "PROTOCOL_ONLY_NOT_NATIVE_CERTIFICATION")

    def test_canonical_wrong_original_status_source_and_marker_rejected(self):
        fixture = self.prepare()
        original = self.canonical(fixture)
        for variant, reason in (("status", "Nonzero rc or explicit producer failure"),
                                ("source", "Candidate/tool/build differs"),
                                ("marker", "Producer failure sidecar present")):
            with self.subTest(variant=variant):
                changed = copy.deepcopy(original)
                if variant == "status":
                    changed["exit_code"] = 1
                elif variant == "source":
                    changed["candidate"]["source_sha256"]["solver.py"] = "0" * 64
                fixture.store("canonical-original", changed)
                fixture.reports["canonical-ledger"]["baseline"] = fixture.pin("canonical-original")
                fixture.store("canonical-ledger", fixture.reports["canonical-ledger"])
                fixture.ledger["bridge"]["canonical"]["ledger"] = fixture.pin("canonical-ledger")
                if variant == "marker":
                    put(fixture.directory / "canonical-original/report.failure.json",
                        gate.encode({"test_fixture": "ORIGINAL_FAILURE_NOT_COVERABLE"}))
                fixture.save()
                self.blocked(fixture, reason)

    def test_real_crypto_rejects_wrong_role_material_and_expired_without_certifying(self):
        fixture = self.prepare()
        _, trust = gate.bridge_algorithms(BRIDGE_DEPENDENCIES, fixture.repo, fixture.identity)
        import base64
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        current = int(fixture.now.timestamp())
        source = {"record_id": "FILE_ONLY_SYNTHETIC_SOURCE", "sha256": trust.sha(b"FILE_ONLY")}
        keys, private = [], {}
        for role, kind in trust.ROLES.items():
            secret = Ed25519PrivateKey.generate()
            public = secret.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            private[role] = secret
            keys.append({"key_id": trust.sha(public), "public_key": base64.b64encode(public).decode(),
                         "role": role, "actor_kind": kind, "principal_id": "FILE_ONLY_" + role,
                         "not_before": current - 1000, "expires_at": current + 7200, "source": source})
        root = {"schema": trust.TEST_ROOT, "mode": "TEST_ONLY", "domain": trust.DOMAIN,
                "policy": trust.POLICY, "source": source, "keys": keys,
                "minimum_revocation_sequence": 1, "revocation_not_before": current - 60}
        material = fixture.pin(gate.MANUAL[0])["sha256"]
        payload = {"domain": trust.DOMAIN, "policy": trust.POLICY, "role": "human",
                   "record_id": "FILE_ONLY_RECORD", "candidate": fixture.commit,
                   "material_sha256": material, "issuer_id": "FILE_ONLY_human", "source": source,
                   "issued_at": current - 10, "not_before": current - 10, "expires_at": current + 600}
        snapshot = {"domain": trust.DOMAIN, "policy": trust.POLICY, "source": source,
                    "sequence": 1, "issued_at": current - 10, "expires_at": current + 120,
                    "revoked_keys": [], "revoked_records": []}

        def signed(schema, value, role):
            frame = {"schema": schema, "algorithm": "Ed25519",
                     "key_id": next(key["key_id"] for key in keys if key["role"] == role),
                     "payload": value}
            frame["signature"] = base64.b64encode(
                private[role].sign(trust.signing_bytes(frame, schema))).decode()
            return trust.canonical(frame)

        revoked = signed(trust.REVOCATIONS, snapshot, "revocation")
        args = {"expected_candidate": fixture.commit, "expected_material": material,
                "expected_role": "human", "root": trust.canonical(root), "now": current,
                "test_only": True}
        positive = trust.verify(signed(trust.ENVELOPE, payload, "human"), revoked, **args)
        self.assertEqual(positive["scope"], "TEST_ONLY")
        self.assertEqual(positive["release_verdict"], "NOT_CERTIFIED")
        for variant, code in (({**payload, "material_sha256": "0" * 64},
                               "CANDIDATE_OR_MATERIAL_MISMATCH"),
                              ({**payload, "expires_at": current}, "EXPIRED_OR_FUTURE"),
                              ({**payload, "role": "deployment"}, "ROLE_MISMATCH")):
            with self.subTest(code=code):
                with self.assertRaisesRegex(trust.TrustError, code):
                    trust.verify(signed(trust.ENVELOPE, variant, "human"), revoked, **args)
        with self.assertRaisesRegex(trust.TrustError, "TRUST_UNAVAILABLE"):
            trust.verify(signed(trust.ENVELOPE, payload, "human"), revoked,
                         **{key: value for key, value in args.items() if key != "test_only"})
        put(fixture.directory / "signed-envelope.json", signed(trust.ENVELOPE, payload, "human"))
        put(fixture.directory / "signed-revocations.json", revoked)
        proof = {label: {"path": str(fixture.directory / ("signed-" + filename + ".json")),
                         "sha256": gate.sha((fixture.directory / ("signed-" + filename + ".json")).read_bytes())}
                 for label, filename in (("envelope", "envelope"), ("revocations", "revocations"))}
        fixture.ledger["bridge"]["provenance"]["human"][gate.MANUAL[0]] = proof
        fixture.save()
        self.blocked(fixture, "TRUST_UNAVAILABLE")

    def test_real_v4_original_mapping_read_only(self):
        path = gate.ARTIFACT_ROOT / "workbench-integrated-v4/workbench.json"
        body = gate.read_file(path)
        self.assertEqual(gate.sha(body),
                         "1d9abf07f544d56e8751db480f82daaa29356a33478eba93956033d433526f64")
        workbench = gate.decode(body)
        self.assertEqual(workbench["exit_code"], 3)
        self.assertEqual(workbench["verdict"], "LOCAL_AUTOMATION_PARTIAL_NOT_RELEASE")
        directory = gate.OUTPUT_ROOT / ("v4-bridge-readback-" + uuid4().hex)
        directory.mkdir()
        root = directory / "candidate"
        command(["git", "clone", "--no-hardlinks", "--no-checkout", "/data/dev/opensim", str(root)],
                directory)
        command(["git", "checkout", "--detach", "97477ba9255c1cdb9f8a15d9145a00e61a63188d"], root)
        expected = gate.identity(root, "97477ba9255c1cdb9f8a15d9145a00e61a63188d")
        original = workbench["producers"]["audit"]["report"]
        pin = {key: original[key] for key in ("path", "sha256")}
        mapped = gate.native_obligations(pin, expected, 0, root, BRIDGE_DEPENDENCIES,
                                        datetime.fromisoformat(workbench["finished_at"]))
        self.assertTrue(mapped["open"])
        self.assertTrue(mapped["closed"])
        self.assertFalse(mapped["object_inventory"]["complete"])
        audit, _ = gate.audit_reference(pin)
        obligations, _ = gate.bridge_algorithms(BRIDGE_DEPENDENCIES, root, expected)
        for closure in mapped["closed"]:
            for evidence in closure["evidence"]:
                row = audit[evidence["section"]][evidence["index"]]
                self.assertEqual(evidence["row_sha256"], obligations.digest(row))
                self.assertEqual(evidence["sha256"], pin["sha256"])
        self.assertEqual(gate.read_file(path), body)
        self.assertEqual(gate.identity(root, expected["commit"]), expected)
        put(directory / "readback.json", gate.encode({
            "scope": "HISTORICAL_V4_NATIVE_AUDIT_READBACK_NOT_NEW_CANDIDATE_RELEASE",
            "workbench": {"path": str(path), "sha256": gate.sha(body), "exit_code": 3},
            "candidate": expected, "audit": pin, "source_stable": True,
            "counts": {"closed": len(mapped["closed"]), "open": len(mapped["open"]),
                       "required": mapped["required_automated_coordinates"]},
            "original_statuses_not_promoted": True, "release_verdict": "NOT_CERTIFIED"}))


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def blocked(self, needle: str) -> None:
        result, report = self.f.cli()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(report["verdict"], "BLOCKED")
        self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")
        self.assertIn(needle, report["failure"]["reason"])
        self.assertFalse(report["production_changes"])

    def test_complete_fixture_schema_pass_is_not_deployment(self):
        self.blocked("PARTIAL audit cannot certify G3-accessibility")
        self.assertEqual(self.f.reports["browser"]["release_verdict"], "NOT_CERTIFIED")

    def test_empty_evidence(self):
        self.f.ledger["evidence"] = {}
        self.f.save()
        self.blocked("evidence missing")

    def test_old_hash(self):
        put(self.f.directory / "audit/report.json", b"{}\n")
        self.blocked("Evidence hash drift")

    def test_dirty_tree(self):
        put(self.f.repo / "untracked.txt", b"other asset\n")
        self.blocked("Dirty checkout")
        self.assertEqual((self.f.repo / "untracked.txt").read_bytes(), b"other asset\n")

    def test_modified_tracked_tree(self):
        put(self.f.repo / "web/app.js", b"other developer changes\n")
        self.blocked("Dirty checkout")

    def test_old_tool_hash(self):
        self.f.change("browser", tool_sha256="0" * 64)
        self.blocked("Old/unknown tool")

    def test_wrong_commit(self):
        self.f.change("browser", commit="0" * 40)
        self.blocked("Report commit mismatch")

    def test_automated_only_cannot_certify(self):
        self.f.ledger["manual"] = {}
        self.f.save()
        self.blocked("PARTIAL audit cannot certify")

    def test_partial_audit(self):
        self.f.reports["audit"]["gates"]["G3-accessibility"]["status"] = "PARTIAL"
        self.f.change("audit")
        self.blocked("PARTIAL audit")

    def test_partial_content_gate_never_promoted(self):
        self.f.reports["audit"]["gates"]["G6-content"]["status"] = "PARTIAL"
        self.f.change("audit")
        self.blocked("PARTIAL audit cannot certify")
        self.assertEqual(self.f.reports["audit"]["gates"]["G6-content"]["status"], "PARTIAL")

    def test_missing_g6_record(self):
        self.f.ledger["gates"].pop("G6-content")
        self.f.save()
        self.blocked("Missing G1..G6")

    def test_local_rc_without_completed_tests_not_evidence(self):
        for step in self.f.reports["local"]["steps"]:
            step["stdout"] = ""
        self.f.change("local")
        self.blocked("without actual tests")

    def test_binding_and_performance_positive_controls(self):
        for kind, tool in (("browser", "tools/browser_check.mjs"),
                           ("audit", "tools/browser_audit.mjs"),
                           ("performance", "tools/browser_performance.mjs")):
            gate.binding(self.f.reports[kind], self.f.identity, tool, datetime.now(timezone.utc))
        gate.performance(self.f.reports["performance"])
        self.assertEqual(gate.identity(self.f.repo, self.f.commit), self.f.identity)

    def test_local_perf_not_g5(self):
        self.f.change("performance", scope="LOCAL_PREFLIGHT", verdict="LOCAL_PREFLIGHT_PASS", g5_certified=False)
        self.blocked("LOCAL_PREFLIGHT")

    def test_loopback_disguised_public_performance(self):
        self.f.change("performance", target_url="http://127.0.0.1:9999")
        self.blocked("PUBLIC_LAB")

    def test_loopback_dns(self):
        self.f.change("public", dns_addresses=["127.0.0.1"])
        self.blocked("Loopback/private DNS")

    def test_stale_public(self):
        self.f.change("public", collected_at=(self.f.now - timedelta(minutes=11)).isoformat())
        self.blocked("Stale/future")

    def test_missing_real_fem(self):
        self.f.change("public", checks=[{"name": "runtime_identity", "verdict": "PASS"}])
        self.blocked("Missing real public computations")

    def test_missing_screenshot(self):
        self.f.change("browser", screenshots=[])
        self.blocked("Missing actual screenshot")

    def test_empty_selected_gate_checks(self):
        self.f.ledger["gates"]["G2-layout"]["checks"] = []
        self.f.save()
        self.blocked("Empty/duplicate gate")

    def test_missing_viewport(self):
        self.f.change("browser", matrix=self.f.reports["browser"]["matrix"][:-1])
        self.blocked("matrix missing")

    def test_wrong_build(self):
        self.f.change("browser", page_build={"build_text": "wrong", "edited_at":
                                           self.f.reports["browser"]["page_build"]["edited_at"]})
        self.blocked("build_text differs")

    def test_page_timestamp_origin_not_invented(self):
        self.f.change("page", source_epoch=1)
        self.blocked("Local edited_at")

    def test_unapproved_exception(self):
        self.f.ledger["exceptions"] = ["NOT_APPROVED"]
        self.f.save()
        self.blocked("Unapproved/unknown")

    def test_expired_exception(self):
        self.f = Fixture(approval(expired=True))
        self.f.ledger["exceptions"] = ["EXPLICIT-FIXTURE-APPROVAL"]
        self.f.save()
        self.blocked("Expired/future exception")

    def test_editable_json_user_approval_cannot_self_certify(self):
        self.f = Fixture(approval())
        self.f.ledger["exceptions"] = ["EXPLICIT-FIXTURE-APPROVAL"]
        self.f.ledger["manual"]["PHYSICAL-TOUCH"] = {"status": "EXCEPTION"}
        self.f.save()
        self.blocked("no trusted user-approval verifier")

    def test_native_browser_no_exit_code_uses_launcher(self):
        report = self.f.reports["browser"]
        report.pop("exit_code")
        self.f.change("browser")
        self.blocked("PARTIAL audit cannot certify")

    def test_wrong_launcher_report(self):
        launch = self.f.reports["launcher-browser"]
        launch["report"] = self.f.pin("audit")
        self.f.store("launcher-browser", launch)
        self.f.ledger["executions"]["browser"] = self.f.pin("launcher-browser")
        self.f.save()
        self.blocked("Launcher does not bind")

    def test_empty_launcher_stderr_is_real_empty_file_not_missing(self):
        path = self.f.directory / "browser/launcher.stderr"
        put(path, b"")
        report = self.f.reports["launcher-browser"]
        report["stderr"]["sha256"] = gate.sha(b"")
        self.f.store("launcher-browser", report)
        self.f.ledger["executions"]["browser"] = self.f.pin("launcher-browser")
        self.f.save()
        self.blocked("PARTIAL audit cannot certify")

    def test_assume_unchanged_cannot_fake_clean(self):
        command(["git", "update-index", "--assume-unchanged", "web/app.js"], self.f.repo)
        self.blocked("assume-unchanged")

    def test_sparse_rule_coverage_blocks_gate(self):
        self.f.ledger["gates"]["G1-structure"]["checks"] = ["structure"]
        self.f.save()
        self.blocked("missing required rule")

    def test_loopback_remote_connection_cannot_fake_public(self):
        self.f.reports["performance"]["samples"][0]["resources"][0]["remote_ip"] = "127.0.0.1"
        self.f.change("performance")
        self.blocked("connection/DNS not public")

    def test_perf_raw_summary_mismatch(self):
        self.f.reports["performance"]["samples"][0]["raw_metrics"]["lcp"][0]["startTime"] = 101
        self.f.change("performance")
        self.blocked("raw LCP/CLS")

    def test_perf_cache_self_report_not_enough(self):
        self.f.reports["performance"]["samples"][0]["resources"][1]["cache_control"] = "no-store"
        self.f.change("performance")
        self.blocked("no-store")

    def test_public_g5_never_waived(self):
        self.f = Fixture(approval("G5-performance"))
        self.f.ledger["exceptions"] = ["EXPLICIT-FIXTURE-APPROVAL"]
        self.f.save()
        self.blocked("public G5 cannot be waived")

    def test_duplicate_json_key(self):
        put(self.f.ledger_path, b'{"schema":1,"schema":2}\n')
        self.blocked("Duplicate JSON")

    def test_symlink_evidence(self):
        alias = self.f.directory / "alias.json"
        alias.symlink_to(self.f.directory / "browser/report.json")
        self.f.ledger["evidence"]["browser"]["path"] = str(alias)
        self.f.save()
        self.blocked("symlink")

    def test_hardlink_evidence(self):
        alias = self.f.directory / "hard.json"
        os.link(self.f.directory / "browser/report.json", alias)
        self.blocked("hardlinked")


class NativeAuditTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.report = copy.deepcopy(self.f.reports["audit"])
        self.report.update(schema_version=1, tool="opensim-browser-audit", release_verdict="NOT_CERTIFIED",
                           test_fixture="SYNTHETIC_FILE_CONTRACT_NOT_PRODUCT")
        self.report["checks"] = [{"rule": "WEB-INPUT-005", "status": "PASS", "url": gate.PUBLIC_URL,
                                 "viewport": {"width": 320, "height": 568}, "state": "320-ready",
                                 "reproduce": ["FIXTURE_NOT_BROWSER"]}]
        self.report["gates"] = {
            "G3-accessibility": {"status": "PARTIAL", "release_status": "NOT_CERTIFIED"},
            "G6-content": {"status": "PASS", "release_status": "NOT_CERTIFIED"}}
        self.report["coverage"] = [{"rule": "WEB-A11Y-009", "status": "NOT_TESTED"},
                                   {"rule": "WEB-A11Y-008", "status": "NOT_APPLICABLE", "actual_count": 0},
                                   *[{"rule": rule, "status": "NOT_TESTED"} for rule in
                                     ("PHYSICAL-TOUCH", "SCREENSHOT-REVIEW", "FULL-STANDARD-MATRIX", "G5-performance")]]
        self.report["page_capabilities"] = {"dialogs": 0, "mobile_menus": 0}
        self.report["checks"][0]["url"] = self.report["url"]
        identity = {"url": self.report["url"], "verdict": "PASS", "fixture_asset_override": False,
                    "footer": self.report["page_build"]["footer"],
                    "local_source_sha256": self.f.identity["source_sha256"],
                    "committed_source_sha256": self.f.identity["source_sha256"],
                    "health": {"status": "ok", "source_sha256": self.f.identity["source_sha256"],
                               "page_build": self.f.identity["page_build"]},
                    "served_byte_sha256": {"health": "a" * 64, "/": "b" * 64,
                        "/web/app.js": self.f.identity["source_sha256"]["web/app.js"],
                        "/web/app.css": self.f.identity["source_sha256"]["web/app.css"]}}
        self.report["identity_checks"] = [{**copy.deepcopy(identity), "stage": stage}
                                          for stage in ("before", "after")]
        native_assets(self.f.directory / "native-audit", self.report, self.f.identity, self.f.time)
        self.persist()

    def persist(self):
        self.path = self.f.directory / "native-audit/report.json"
        self.report["coverage_counts"]["checks"] = len(self.report["checks"])
        line = "AUDIT candidate_verdict={} candidate_rc={} report={}\n".format(
            self.report["verdict"], self.report.get("exit_code", 0), self.path)
        body = line.encode()
        self.report["completion"] = {"protocol": "durable-final-v2", "status": "FINAL",
                                     "receipt": "completion.json", "log": "logs/audit.log",
                                     "log_sha256": gate.sha(body)}
        put(self.path, gate.encode(self.report))
        put(self.path.parent / "logs/audit.log", body)
        self.pin = {"path": str(self.path), "sha256": gate.sha(self.path.read_bytes())}
        put(self.path.parent / "completion.json", gate.encode({
            "protocol": "durable-final-v2", "status": "COMPLETE", "exit_code": self.report.get("exit_code", 0),
            "report": "report.json", "report_sha256": self.pin["sha256"], "log_sha256": gate.sha(body)}))

    def read(self, rc=0):
        return gate.read_audit_scope(self.pin, self.f.identity, rc, source_root=self.f.repo)

    def test_native_scope_preserves_partial_na_and_original_bytes(self):
        before = self.path.read_bytes()
        result = self.read()
        self.assertEqual(result["coverage_status"], "AUTOMATED_PARTIAL")
        self.assertEqual(result["release_verdict"], "NOT_CERTIFIED")
        self.assertEqual(result["native_gates"]["G3-accessibility"]["status"], "PARTIAL")
        self.assertEqual(result["remaining"][0]["status"], "NOT_TESTED")
        self.assertEqual(len(result["not_applicable"]), 1)
        self.assertEqual(before, self.path.read_bytes())

    def test_native_build_and_all_five_sources_required(self):
        for page in ("old-string", {}, {"id": "0" * 12, "footer": self.report["page_build"]["footer"]}):
            with self.subTest(page=page):
                value = {**self.report, "page_build": page}
                with self.assertRaises(gate.GateError):
                    gate.binding(value, self.f.identity, "tools/browser_audit.mjs", self.f.now)
        for sources in (None, {}, {**self.f.identity["source_sha256"], "solver.py": "0" * 64}):
            with self.subTest(sources=sources):
                with self.assertRaisesRegex(gate.GateError, "all five sources"):
                    gate.binding({**self.report, "source_sha256": sources}, self.f.identity,
                                 "tools/browser_audit.mjs", self.f.now)

    def test_native_actual_identity_drift_rejected(self):
        for field in ("local_source_sha256", "committed_source_sha256"):
            self.report["identity_checks"][0][field] = {}
            self.persist()
            with self.assertRaisesRegex(gate.GateError, "five-source"):
                self.read()
            self.report["identity_checks"][0][field] = self.f.identity["source_sha256"]

    def test_native_coordinates_and_pinned_selection(self):
        row = self.report["checks"][0]
        pin = {"pointer": "/checks/0", "sha256": gate.sha(gate.encode(row))}
        self.assertEqual(gate.selected_checks(self.report, [pin], "audit"), [row])
        for selection in ([{**pin, "sha256": "0" * 64}], [pin, pin],
                          [{**pin, "pointer": "/checks/10"}], ["invented-name"]):
            with self.subTest(selection=selection):
                with self.assertRaises(gate.GateError):
                    gate.selected_checks(self.report, selection, "audit")
        for field, value in (("viewport", [320, 568]), ("viewport", {"width": True, "height": 568}),
                             ("reproduce", "invented"), ("reproduce", []), ("url", "http://wrong/")):
            with self.subTest(field=field, value=value):
                with self.assertRaises(gate.GateError):
                    gate.audit_coordinates({**row, field: value}, self.report)

    def test_native_rc_and_fabricated_pass_never_accepted(self):
        for rc in (1, 2, 3, True, None):
            with self.subTest(rc=rc):
                with self.assertRaisesRegex(gate.GateError, "rc mismatch"):
                    self.read(rc)
        for field, value in (("exit_code", 1), ("exit_code", False), ("verdict", "PASS"),
                             ("verdict", "AUTOMATED_PASS"), ("release_verdict", "CERTIFIED")):
            original = self.report[field]
            self.report[field] = value
            self.persist()
            with self.subTest(field=field, value=value):
                with self.assertRaises(gate.GateError):
                    self.read()
            self.report[field] = original
        self.report["gates"]["G3-accessibility"]["status"] = "PASS"
        self.persist()
        with self.assertRaisesRegex(gate.GateError, "cannot claim full PASS"):
            self.read()

    def test_native_na_is_not_permission_and_wrong_na_rejected(self):
        self.report["page_capabilities"]["dialogs"] = 1
        self.persist()
        with self.assertRaisesRegex(gate.GateError, "NA cannot turn green"):
            self.read()
        self.report["page_capabilities"]["dialogs"] = 0
        self.report["coverage"][1]["rule"] = "WEB-A11Y-009"
        self.persist()
        with self.assertRaisesRegex(gate.GateError, "PASS or NA"):
            self.read()

    def test_native_final_candidate_pair_only_not_arbitrary_hardlinks(self):
        candidate = self.path.with_name("report.candidate.json")
        os.link(self.path, candidate)
        self.assertEqual(self.read()["release_verdict"], "NOT_CERTIFIED")
        with self.assertRaisesRegex(gate.GateError, "hardlinked"):
            gate.read_file(self.path)
        os.link(self.path, self.path.with_name("unknown-alias.json"))
        with self.assertRaisesRegex(gate.GateError, "link count"):
            self.read()

    def test_native_receipt_log_failure_marker_reject_unchanged_report(self):
        before = self.path.read_bytes()
        log = self.path.parent / "logs/audit.log"
        put(log, b"wrong terminal\n")
        with self.assertRaisesRegex(gate.GateError, "receipt/report/log"):
            self.read()
        self.persist()
        put(self.path.parent / "report.failure.json", gate.encode({"first_failure": "REAL_FILE_NEGATIVE"}))
        with self.assertRaisesRegex(gate.GateError, "failure sidecar"):
            self.read()
        self.assertEqual(before, self.path.read_bytes())

    def test_native_receipt_required_and_wrong_pin_rejected(self):
        receipt_path = self.path.parent / "completion.json"
        for field, value in (("exit_code", 1), ("report_sha256", "0" * 64),
                             ("log_sha256", "0" * 64), ("status", "PROVISIONAL")):
            receipt = gate.decode(receipt_path.read_bytes())
            receipt[field] = value
            put(receipt_path, gate.encode(receipt))
            with self.subTest(field=field):
                with self.assertRaisesRegex(gate.GateError, "receipt/report/log"):
                    self.read()
            self.persist()
        receipt_path.rename(receipt_path.with_name("missing-receipt.json"))
        with self.assertRaises(FileNotFoundError):
            self.read()

    def test_native_partial_and_not_tested_never_promoted(self):
        self.report["checks"][0]["status"] = "NOT_TESTED"
        self.report["coverage"].append({"rule": "WEB-A11Y-001", "status": "PARTIAL"})
        self.persist()
        result = self.read()
        self.assertEqual(result["checks"][0]["status"], "NOT_TESTED")
        self.assertIn("PARTIAL", [row["status"] for row in result["remaining"]])
        self.assertEqual(result["coverage_status"], "AUTOMATED_PARTIAL")
        self.assertEqual(result["release_verdict"], "NOT_CERTIFIED")

    def test_native_manual_public_or_matrix_fabricated_pass_rejected(self):
        for index, row in enumerate(self.report["coverage"]):
            if row["status"] != "NOT_TESTED":
                continue
            for value in ("PASS", "NOT_APPLICABLE"):
                with self.subTest(rule=row["rule"], value=value):
                    self.report["coverage"][index]["status"] = value
                    self.persist()
                    with self.assertRaisesRegex(gate.GateError, "PASS or NA"):
                        self.read()
            self.report["coverage"][index]["status"] = "NOT_TESTED"

    def test_native_narrow_pair_wrong_sibling_or_provisional_rejected(self):
        other = self.path.with_name("wrong-candidate.json")
        os.link(self.path, other)
        with self.assertRaises(FileNotFoundError):
            self.read()
        other.rename(self.path.with_name("report.candidate.json"))
        self.report["completion"]["status"] = "PROVISIONAL"
        put(self.path, gate.encode(self.report))
        self.pin["sha256"] = gate.sha(self.path.read_bytes())
        with self.assertRaisesRegex(gate.GateError, "protocol missing"):
            self.read()

    def test_native_helper_actual_subprocess_positive_and_negative(self):
        expected = self.f.directory / "expected.json"
        put(expected, gate.encode(self.f.identity))
        for rc, expected_rc in ((0, 0), (3, 1)):
            argv = [sys.executable, "-B", str(Path(__file__).resolve()), "--audit-child",
                    "--audit-report", str(self.path), "--audit-sha256", self.pin["sha256"],
                    "--expected", str(expected), "--producer-rc", str(rc),
                    "--source-root", str(self.f.repo)]
            child = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT)
            put(self.f.directory / f"child-{rc}.stdout", child.stdout.encode())
            put(self.f.directory / f"child-{rc}.stderr", child.stderr.encode())
            self.assertEqual(child.returncode, expected_rc, child.stderr)
            if rc == 0:
                self.assertIn("NOT_CERTIFIED", child.stdout)
            else:
                self.assertIn("rc mismatch", child.stderr)

    def test_evaluate_calls_scope_reader_not_just_binding(self):
        self.f.change("audit", identity_checks=[])
        child, report = self.f.cli()
        self.assertEqual(child.returncode, 1)
        self.assertIn("before/after actual identity", report["failure"]["reason"])
        self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")

    def test_evaluate_narrow_pair_and_partial_without_name_dead_path(self):
        path = self.f.directory / "audit/report.json"
        os.link(path, path.with_name("report.candidate.json"))
        result = gate.execution(self.f.pin("launcher-audit"), self.f.pin("audit"),
                                self.f.identity, "tools/browser_audit.mjs", self.f.now)
        self.assertEqual(result["rc"], 0)
        child, report = self.f.cli()
        self.assertEqual(child.returncode, 1)
        self.assertIn("PARTIAL audit cannot certify", report["failure"]["reason"])
        self.assertNotIn("hardlinked", report["failure"]["reason"])
        self.assertFalse(report["production_changes"])

    def test_evaluate_uses_native_selected_hash_and_final_sidecar(self):
        self.f.ledger["gates"]["G3-accessibility"]["checks"][0]["sha256"] = "0" * 64
        self.f.save()
        child, report = self.f.cli()
        self.assertEqual(child.returncode, 1)
        self.assertIn("Native audit check hash drift", report["failure"]["reason"])
        put(self.f.directory / "audit/report.failure.json", gate.encode({"first_failure": "LATE_IO"}))
        child, report = self.f.cli()
        self.assertEqual(child.returncode, 1)
        self.assertIn("failure sidecar", report["failure"]["reason"])

    def test_evaluate_fabricated_full_g3_pass_remains_closed(self):
        self.f.reports["audit"]["gates"]["G3-accessibility"]["status"] = "PASS"
        self.f.change("audit")
        child, report = self.f.cli()
        self.assertEqual(child.returncode, 1)
        self.assertIn("cannot claim full PASS", report["failure"]["reason"])

    def test_native_dependencies_require_pinned_same_checkout_validator(self):
        for value in (None, "old", "0" * 64):
            expected = copy.deepcopy(self.f.identity)
            expected["tool_sha256"]["tools/workbench_acceptance.py"] = value
            with self.subTest(value=value):
                with self.assertRaisesRegex(gate.GateError, "identity|validator hash"):
                    gate.read_audit_scope(self.pin, expected, 0, source_root=self.f.repo)
        empty = self.f.directory / "no-validator"
        empty.mkdir()
        with self.assertRaises(FileNotFoundError):
            gate.read_audit_scope(self.pin, self.f.identity, 0, source_root=empty)

    def test_native_post_artifacts_complete_actual_source_and_cdp_digest(self):
        post_path = self.path.parent / "http-solve-000.json"
        original = post_path.read_bytes()
        for field, value, reason in (("body_sha256", "0" * 64, "CDP digest"),
                                     ("status", 503, "successful actual solve"),
                                     ("url", "http://wrong/api/solve", "successful actual solve")):
            post = gate.decode(original)
            post[field] = value
            put(post_path, gate.encode(post))
            with self.subTest(field=field):
                with self.assertRaisesRegex(gate.GateError, reason):
                    self.read()
        post = gate.decode(original)
        post["payload"]["reproducibility"]["source_sha256"]["solver.py"] = "0" * 64
        put(post_path, gate.encode(post))
        with self.assertRaisesRegex(gate.GateError, "Source identity differs"):
            self.read()
        put(post_path, original)
        put(self.path.parent / "http-solve-999.json", original)
        with self.assertRaisesRegex(gate.GateError, "successful POST inventory differs"):
            self.read()

    def test_native_missing_successful_post_rejected(self):
        post = self.path.parent / "http-solve-000.json"
        post.rename(post.with_name("retained-missing-post.json"))
        with self.assertRaises(FileNotFoundError):
            self.read()

    def test_native_logs_ancestor_symlink_rejected_preserves_target(self):
        logs = self.path.parent / "logs"
        retained = self.path.parent / "retained-logs"
        logs.rename(retained)
        before = (retained / "audit.log").read_bytes()
        logs.symlink_to(retained, target_is_directory=True)
        with self.assertRaisesRegex(gate.GateError, "Evidence symlink rejected"):
            self.read()
        self.assertEqual((retained / "audit.log").read_bytes(), before)

    def test_native_png_dimensions_state_and_tile_set_rejected(self):
        pictures = copy.deepcopy(self.report["screenshots"])
        for field, value, reason in (("width", 1, "required viewport/state screenshot missing"),
                                     ("url", "http://wrong/", "screenshot missing"),
                                     ("state", "UNKNOWN", "screenshot missing")):
            self.report["screenshots"] = copy.deepcopy(pictures)
            self.report["screenshots"][0][field] = value
            self.persist()
            with self.subTest(field=field):
                with self.assertRaisesRegex(gate.GateError, reason):
                    self.read()
        self.report["screenshots"] = pictures[:1]
        self.report["coverage_counts"]["screenshots"] = 1
        self.persist()
        with self.assertRaisesRegex(gate.GateError, "tile inventory/coverage gap"):
            self.read()

    def test_native_tile_gap_and_injections_rejected(self):
        cover = self.path.parent / "320-ready.coverage.json"
        value = gate.decode(cover.read_bytes())
        value["tiles"][0]["document_y"] = 1
        put(cover, gate.encode(value))
        with self.assertRaisesRegex(gate.GateError, "tile inventory/coverage gap"):
            self.read()
        value["tiles"][0]["document_y"] = 0
        put(cover, gate.encode(value))
        self.report["injections"].pop()
        self.persist()
        with self.assertRaisesRegex(gate.GateError, "incomplete injection inventory"):
            self.read()


def audit_child(args) -> int:
    try:
        expected = gate.decode(gate.read_file(gate.external(args.expected)))
        result = gate.read_audit_scope({"path": str(args.audit_report), "sha256": args.audit_sha256},
                                      expected, args.producer_rc, source_root=args.source_root)
        print(gate.encode(result).decode(), end="", flush=True)
        return 0
    except Exception as exc:
        print(f"AUDIT_SCOPE_REFUSED {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def audit_readback(args) -> int:
    """Re-read existing launcher records, not re-run or impersonate their producer."""
    out = gate.external(args.out, gate.OUTPUT_ROOT)
    out.mkdir(parents=True, exist_ok=False)
    result = {"scope": "HISTORICAL_AUDIT_READBACK_NOT_RELEASE", "verdict": "FAIL",
              "release_verdict": "NOT_CERTIFIED", "production_changes": False,
              "collected_at": datetime.now(timezone.utc).isoformat(), "exit_code": 1}
    try:
        workbench, workbench_path = gate.reference({"path": str(args.audit_readback),
                                                    "sha256": args.workbench_sha256})
        workbench_body = gate.read_file(workbench_path)
        gate.require(workbench.get("tool") == "opensim-workbench-acceptance" and
                     workbench.get("schema_version") == 1 and
                     workbench.get("scope") == "LOCAL_AUTOMATION_NOT_RELEASE" and
                     workbench.get("release_verdict") == "NOT_CERTIFIED" and
                     workbench["before"]["commit"] == workbench["after"]["commit"] == args.commit,
                     "Workbench historical candidate mismatch")
        names = (*gate.SOURCES, "tools/browser_audit.mjs", "tools/workbench_acceptance.py")
        before = {name: gate.read_file(args.source_root / name) for name in names}
        bodies = {name: gate.committed(args.source_root, args.commit, name) for name in names}
        fingerprint = hashlib.sha256()
        for name in gate.SOURCES:
            fingerprint.update(name.encode() + b"\0" + bodies[name] + b"\0")
        expected = {"commit": args.commit, "source_sha256": {n: gate.sha(bodies[n]) for n in gate.SOURCES},
                    "page_build": fingerprint.hexdigest()[:12], "tool_sha256": {
                        n: gate.sha(bodies[n]) for n in
                        ("tools/browser_audit.mjs", "tools/workbench_acceptance.py")}}
        producer = workbench["producers"]["audit"]
        step, = [row for row in workbench["steps"] if row["name"] == "audit"]
        gate.require(producer["candidate_commit"] == args.commit and producer["tool_sha256"] ==
                     expected["tool_sha256"]["tools/browser_audit.mjs"] and
                     type(step["rc"]) is int and step["rc"] == 0 and
                     producer["terminal"] == {"rc": 0, "exit_code": 0, "verdict": "AUTOMATED_SCOPE_PASS"} and
                     str(args.source_root / "tools/browser_audit.mjs") in step["argv"],
                     "Existing actual audit launcher rc/tool mismatch")
        pin = {key: producer["report"][key] for key in ("path", "sha256")}
        report, path = gate.audit_reference(pin)
        info = path.stat()
        gate.require(str(info.st_dev) == producer["report"]["dev"] and
                     str(info.st_ino) == producer["report"]["ino"] and
                     info.st_nlink == producer["report"]["nlink"] and
                     info.st_size == producer["report"]["bytes"], "Historical audit artifact identity drift")
        for key in ("stdout", "stderr"):
            log = {field: step[key][field] for field in ("path", "sha256")}
            gate.log_artifact(log)
            gate.require(Path(log["path"]).stat().st_size == step[key]["bytes"], "Actual launcher log size drift")
        stdout = gate.read_file(gate.external(Path(step["stdout"]["path"]))).decode()
        terminal = "AUDIT verdict=AUTOMATED_SCOPE_PASS rc=0 report="
        lines = [line for line in stdout.splitlines() if line.startswith(terminal)]
        gate.require(len(lines) == 1 and gate.external(Path(lines[0][len(terminal):])).resolve() == path.resolve(),
                     "Actual audit stdout lacks exact final report marker")
        durable = workbench["durability"]
        gate.require(durable["state"] == "FINAL_AFTER_DURABLE_LOG", "Historical workbench not final")
        gate.log_artifact({key: durable["log"][key] for key in ("path", "sha256")})
        put(out / "expected.json", gate.encode(expected))
        argv = [sys.executable, "-B", str(Path(__file__).resolve()), "--audit-child",
                "--audit-report", str(path), "--audit-sha256", pin["sha256"],
                "--expected", str(out / "expected.json"), "--producer-rc", str(step["rc"]),
                "--source-root", str(args.source_root)]
        child = subprocess.run(argv, cwd=ROOT, capture_output=True)
        gate.write_new(out / "reader.stdout", child.stdout)
        put(out / "reader.stderr", child.stderr)
        result["reader_process"] = {"argv": child.args, "rc": child.returncode,
                                    "stdout_sha256": gate.sha(child.stdout),
                                    "stderr_sha256": gate.sha(child.stderr)}
        gate.require(child.returncode == 0, "Actual readback child failed: " + child.stderr.decode())
        observation = gate.decode(child.stdout)
        gate.require(observation["release_verdict"] == "NOT_CERTIFIED" and
                     observation["coverage_status"] == "AUTOMATED_PARTIAL", "Readback incorrectly upgraded scope")
        gate.require(all(gate.read_file(args.source_root / n) == before[n] for n in names) and
                     gate.read_file(workbench_path) == workbench_body and
                     gate.audit_reference(pin)[0] == report, "Readback changed source/report identity")
        result.update(verdict="PASS", exit_code=0, observation=observation,
                      expected=expected, source_bytes_unchanged=True, original_audit_unchanged=True,
                      workbench={"path": str(workbench_path), "sha256": args.workbench_sha256,
                                 "verdict": workbench["verdict"], "exit_code": workbench["exit_code"],
                                 "failure": workbench.get("failure")},
                      existing_launcher={"recorded_process_rc": step["rc"], "step": step,
                                         "source": "PINNED_HISTORICAL_LAUNCHER_RECORD_NOT_USER_APPROVAL"},
                      counts={"checks": len(report["checks"]), "screenshots": len(report["screenshots"]),
                              "not_applicable": len(observation["not_applicable"]),
                              "remaining": len(observation["remaining"])})
    except Exception as exc:
        result["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
    return gate.finalize_result(result, out / "report.json", status_key="verdict", failed_status="FAIL",
                                label="OPENSIM_AUDIT_READBACK")


def run_suite(suite: unittest.TestSuite, out: Path, label: str) -> int:
    gate.external(out, gate.OUTPUT_ROOT)
    out.mkdir(parents=True, exist_ok=False)
    Fixture.base = out / "fixtures"
    Fixture.child_records = []
    paths = {Path(__file__).resolve(), Path(gate.__file__).resolve(),
             ROOT / "docs/deploy/SYNC_RELEASE.md",
             *(Path(pin["path"]) for pin in BRIDGE_DEPENDENCIES.values())}
    if label.startswith("OPENSIM_NATIVE_ASSESSMENT"):
        paths.update(NATIVE_ASSESSMENT_DEPENDENCIES)
        NativeAssessmentTests.out = out
    for pin in BRIDGE_DEPENDENCIES.values():
        gate.require(gate.sha(gate.read_file(Path(pin["path"]))) == pin["sha256"],
                     "Frozen bridge dependency drift")
    for name in ("sync_release", "__main__"):
        module = sys.modules.get(name)
        filename = getattr(module, "__file__", None)
        if filename:
            paths.add(Path(filename).resolve())
    before = {str(path): gate.sha(gate.read_file(path)) for path in sorted(paths)}
    started_at = datetime.now(timezone.utc).isoformat()
    gate.write_new(out / "run-start.json", gate.encode({
        "state": "RUNNING_NOT_FINAL", "started_at": started_at,
        "source_sha256": before, "scope": "FIXTURE_NOT_RELEASE"}))
    progress = out / "progress.log"
    with progress.open("x", encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        stream.flush()
        os.fsync(stream.fileno())
    after = {str(path): gate.sha(gate.read_file(path)) for path in sorted(paths)}
    stable = before == after
    rc = 0 if result.wasSuccessful() and result.testsRun > 0 and stable else 1
    line = (f"{label} verdict={'PASS' if rc == 0 else 'FAIL'} tests={result.testsRun} "
            f"failures={len(result.failures)} errors={len(result.errors)} rc={rc} "
            f"source_stable={stable} scope=FIXTURE_NOT_RELEASE "
            f"collected_at={datetime.now(timezone.utc).isoformat()} out={out}")
    log = gate.read_file(progress)
    gate.write_new(out / "execution.log", log + ("\n" + line + "\n").encode())
    gate.write_new(out / "report.json", gate.encode({
        "verdict": "PASS" if rc == 0 else "FAIL", "tests": result.testsRun, "exit_code": rc,
        "failures": len(result.failures), "errors": len(result.errors),
        "started_at": started_at, "source_before": before, "source_after": after,
        "source_stable": stable,
        "child_invocations": Fixture.child_records,
        "original_fixture_child_invocations": (
            NativeAssessmentTests.original.AdapterTests.children
            if label.startswith("OPENSIM_NATIVE_ASSESSMENT") else []),
        "scope": "FIXTURE_NOT_RELEASE", "production_changes": False,
        "line": line, "finished_at": datetime.now(timezone.utc).isoformat()}))
    print(line)
    if rc:
        print(log.decode("utf-8"), file=sys.stderr)
        if not stable:
            print("SOURCE_DRIFT: test/tool files changed during execution", file=sys.stderr)
    return rc


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--native-audit", action="store_true")
    parser.add_argument("--bridge-red", action="store_true")
    parser.add_argument("--bridge", action="store_true")
    parser.add_argument("--bridge-core", action="store_true")
    parser.add_argument("--native-assessment-tests", action="store_true")
    parser.add_argument("--native-assessment", type=Path)
    parser.add_argument("--assessment-publication-negative", type=Path)
    parser.add_argument("--native-assessment-only", nargs="+")
    parser.add_argument("--native-assessment-module", action="store_true")
    parser.add_argument("--s1-negatives", action="store_true")
    parser.add_argument("--audit-child", action="store_true")
    parser.add_argument("--audit-report", type=Path)
    parser.add_argument("--audit-sha256")
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--producer-rc", type=int)
    parser.add_argument("--audit-readback", type=Path)
    parser.add_argument("--workbench-sha256")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--commit")
    args = parser.parse_args()
    if args.audit_child:
        raise SystemExit(audit_child(args))
    if args.audit_readback:
        raise SystemExit(audit_readback(args))
    if args.out is None:
        parser.error("--out required")
    if args.assessment_publication_negative:
        raise SystemExit(assessment_publication_negative(args))
    if args.native_assessment_only:
        suite = unittest.TestSuite(NativeAssessmentTests(name) for name in args.native_assessment_only)
        raise SystemExit(run_suite(suite, args.out, "OPENSIM_NATIVE_ASSESSMENT_FOCUSED"))
    if args.native_assessment_module:
        suite = unittest.TestSuite((
            unittest.defaultTestLoader.loadTestsFromTestCase(NativeAssessmentTests),
            unittest.TestSuite(BridgeTests(name) for name in (
                "test_native_partial_advances_past_raw_pass_contradiction",
                "test_native_automated_scope_advances_past_raw_pass_contradiction",
                "test_fabricated_trusted_state_rejected",
                "test_raw_pass_is_still_rejected_by_original_reader"))))
        raise SystemExit(run_suite(suite, args.out, "OPENSIM_NATIVE_ASSESSMENT_MODULE"))
    if args.native_assessment_tests:
        raise SystemExit(run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(NativeAssessmentTests),
                                   args.out, "OPENSIM_NATIVE_ASSESSMENT_TEST"))
    if args.bridge_core:
        suite = unittest.TestSuite(BridgeTests(name) for name in (
            "test_native_partial_advances_past_raw_pass_contradiction",
            "test_canonical_closed_does_not_close_native_or_human",
            "test_canonical_wrong_original_status_source_and_marker_rejected",
            "test_real_crypto_rejects_wrong_role_material_and_expired_without_certifying"))
        raise SystemExit(run_suite(suite, args.out, "OPENSIM_RELEASE_BRIDGE_CORE_TEST"))
    if args.bridge or args.bridge_red:
        raise SystemExit(run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(BridgeTests),
                                   args.out, "OPENSIM_RELEASE_BRIDGE_TEST"))
    if args.s1_negatives:
        suite = unittest.TestSuite(NativeAuditTests(name) for name in (
            "test_native_dependencies_require_pinned_same_checkout_validator",
            "test_native_post_artifacts_complete_actual_source_and_cdp_digest",
            "test_native_missing_successful_post_rejected",
            "test_native_logs_ancestor_symlink_rejected_preserves_target",
            "test_native_png_dimensions_state_and_tile_set_rejected",
            "test_native_tile_gap_and_injections_rejected"))
        raise SystemExit(run_suite(suite, args.out, "OPENSIM_RELEASE_GATE_S1_NEGATIVES"))
    classes = [NativeAuditTests] if args.native_audit else [ReleaseGateTests, NativeAuditTests, BridgeTests]
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in classes)
    if args.native_audit:
        suite.addTests(ReleaseGateTests(name) for name in (
            "test_binding_and_performance_positive_controls", "test_partial_audit",
            "test_editable_json_user_approval_cannot_self_certify", "test_public_g5_never_waived",
            "test_hardlink_evidence", "test_native_browser_no_exit_code_uses_launcher"))
    raise SystemExit(run_suite(suite, args.out, "OPENSIM_RELEASE_GATE_TEST"))
