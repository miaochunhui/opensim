"""Real CLI/FS negative controls for desired releases; never call production.

The peer API is read-only. Its existing immutable test bundles are inspected,
not installed or modified. All new test files and logs remain under seat-sync.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import errno
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import release_gate as gate
import sync_release as sync
from test_release_gate import Fixture, put, run_suite


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.api = self.f.repo / "tools/release_app.py"
        self.desired_path = self.f.directory / "desired-release.json"
        receipt = {"schema": "opensim-release-gate-v1", "verdict": "PASS",
                   "release_verdict": "CERTIFIED", "scope": "EVIDENCE_VALIDATED_NOT_DEPLOYED",
                   "identity": self.f.identity, "ledger_sha256": gate.sha(self.f.ledger_path.read_bytes()),
                   "approval_pin": None, "exceptions": [], "gates": self.f.ledger["gates"],
                   "evidence": self.f.ledger["evidence"], "exit_code": 0,
                   "collected_at": datetime.now(timezone.utc).isoformat()}
        self.f.store("gate-receipt", receipt)
        epoch = int(gate.git(self.f.repo, "show", "-s", "--format=%ct", self.f.commit).strip())
        digest = "a" * 64
        self.desired = {"schema": "opensim-desired-release-v1", "request_id": "FIXTURE_NOT_RELEASE",
                        "action": "INSTALL_FIXED_RELEASE", "commit": self.f.commit,
                        "ledger": {"path": str(self.f.ledger_path),
                                   "sha256": gate.sha(self.f.ledger_path.read_bytes())},
                        "gate_receipt": self.f.pin("gate-receipt"), "approval_pin": None,
                        "bundle": str(self.f.directory / "nonexistent-bundle"), "manifest_sha256": digest,
                        "source_epoch": epoch, "release": f"/opt/opensim/releases/{self.f.commit}-{digest}",
                        "before_receipt": None}
        self.save()

    def save(self):
        put(self.desired_path, gate.encode(self.desired))

    def cli(self, apply=False, digest=None):
        out = self.f.directory / ("sync-cli-" + uuid4().hex + ".json")
        argv = [sys.executable, "-B", str(ROOT / "tools/sync_release.py"),
                "--source-root", str(self.f.repo), "--desired", str(self.desired_path),
                "--desired-sha256", digest or gate.sha(self.desired_path.read_bytes()),
                "--out", str(out)]
        if apply:
            argv.append("--apply")
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        put(out.with_suffix(".subprocess.log"),
            (f"rc={result.returncode}\n" + result.stdout + result.stderr).encode())
        report = gate.decode(out.read_bytes())
        self.assertFalse(report["production_changes"])
        self.assertEqual(report["state"], "REFUSED")
        self.assertEqual(result.returncode, 1, result.stderr)
        return report

    def test_unknown_desired_field(self):
        self.desired["postcommit"] = True
        self.save()
        self.assertIn("Unknown desired-release", self.cli()["failure"]["reason"])

    def test_unknown_desired_action(self):
        self.desired["action"] = "PUBLISH_ENTIRE_TREE"
        self.save()
        self.assertIn("Unknown desired-release action", self.cli()["failure"]["reason"])

    def test_request_bytes_changed(self):
        self.assertIn("request hash mismatch", self.cli(digest="0" * 64)["failure"]["reason"])

    def test_wrong_gate_receipt(self):
        self.f.store("gate-receipt", {"schema": 1, "verdict": "AUTOMATED_PASS", "exit_code": 0})
        self.desired["gate_receipt"] = self.f.pin("gate-receipt")
        self.save()
        self.assertIn("Wrong release-gate receipt", self.cli()["failure"]["reason"])

    def test_wrong_gate_ledger_hash(self):
        self.f.reports["gate-receipt"]["ledger_sha256"] = "0" * 64
        self.f.store("gate-receipt", self.f.reports["gate-receipt"])
        self.desired["gate_receipt"] = self.f.pin("gate-receipt")
        self.save()
        self.assertIn("does not bind", self.cli()["failure"]["reason"])

    def test_gate_rechecked_not_cached(self):
        put(self.f.repo / "other-owner.txt", b"OTHER OWNER MUST REMAIN\n")
        self.assertIn("Dirty checkout", self.cli()["failure"]["reason"])
        self.assertEqual((self.f.repo / "other-owner.txt").read_bytes(), b"OTHER OWNER MUST REMAIN\n")

    def test_evidence_drift_after_gate(self):
        put(self.f.directory / "browser/report.json", b"{}\n")
        self.assertIn("Evidence hash drift", self.cli()["failure"]["reason"])

    def test_missing_controller_refused(self):
        report = self.cli(apply=True)
        self.assertIn("controller", report["failure"]["reason"].lower())
        self.assertFalse((self.f.directory / "nonexistent-bundle").exists())

    def test_api_pin_and_signature_actual_peer(self):
        api = sync.load_api(self.api, self.f.identity["tool_sha256"]["tools/release_app.py"])
        self.assertEqual(api.Layout().release(self.f.commit, "a" * 64),
                         Path(self.desired["release"]))
        self.assertEqual(tuple(api.SOURCES), gate.SOURCES)
        self.assertEqual(set(api.PAYLOAD), sync.PAYLOAD)

    def test_api_old_hash(self):
        with self.assertRaisesRegex(gate.GateError, "API bytes differ"):
            sync.load_api(self.api, "0" * 64)

    def test_prepare_output_conflict_refused_before_writes(self):
        out = self.f.directory / "never-prepared"
        with self.assertRaisesRegex(gate.GateError, "PARTIAL audit cannot certify G3-accessibility"):
            sync.prepare_guard(self.f.repo, self.f.commit, self.f.ledger_path, out)
        self.assertFalse(out.exists())

    def test_prepare_api_root_conflict_independent_negative(self):
        api = sync.load_api(self.api, self.f.identity["tool_sha256"]["tools/release_app.py"])
        out = self.f.directory / "output-conflict"
        with self.assertRaisesRegex(gate.GateError, "Path outside evidence root"):
            sync.prepare_output_guard(api, out)
        self.assertFalse(out.exists())

    def test_prepare_actual_peer_root_allowed_without_mutation(self):
        api = sync.load_api(self.api, self.f.identity["tool_sha256"]["tools/release_app.py"])
        before = api.ARTIFACTS
        bundle = sync.PREPARE_ROOT / ("sync-guard-" + uuid4().hex) / "bundle"
        self.assertEqual(sync.prepare_output_guard(api, bundle), bundle.resolve())
        self.assertEqual(api.ARTIFACTS, before)
        self.assertFalse(bundle.exists())

    def test_prepare_unknown_api_root_refused(self):
        bundle = sync.PREPARE_ROOT / ("sync-guard-" + uuid4().hex) / "bundle"
        for root in (gate.OUTPUT_ROOT, gate.ARTIFACT_ROOT, Path("/tmp")):
            with self.subTest(root=root):
                with self.assertRaisesRegex(gate.GateError, "fixed allowed seat-release root"):
                    sync.prepare_output_guard(SimpleNamespace(ARTIFACTS=root), bundle)
                self.assertFalse(bundle.exists())

    def test_missing_bundle_preserves_request(self):
        before = self.desired_path.read_bytes()
        report = self.cli()
        self.assertIn("PARTIAL audit cannot certify G3-accessibility", report["failure"]["reason"])
        self.assertEqual(self.desired_path.read_bytes(), before)

    def test_forged_certified_receipt_cannot_authorize_sync(self):
        self.assertIn("PARTIAL audit cannot certify G3-accessibility", self.cli()["failure"]["reason"])

    def test_release_api_bundle_missing_negative_control(self):
        api = sync.load_api(self.api, self.f.identity["tool_sha256"]["tools/release_app.py"])
        with self.assertRaises(FileNotFoundError):
            api.validate_bundle((self.f.directory / "missing-bundle").resolve(),
                                "a" * 64, self.f.commit)

    def test_exact_manifest_fields_epoch_sources_layout(self):
        manifest = {"schema": 1, "commit": self.f.commit, "source_epoch": self.desired["source_epoch"],
                    "source_sha256": self.f.identity["source_sha256"],
                    "page_build": self.f.identity["page_build"], "files": dict.fromkeys(sync.PAYLOAD, {})}
        sync.match_manifest(manifest, self.desired, self.f.identity)
        manifest["source_epoch"] += 1
        with self.assertRaisesRegex(gate.GateError, "manifest/gate/epoch mismatch"):
            sync.match_manifest(manifest, self.desired, self.f.identity)

    def test_before_receipt_wrong_release_path(self):
        api = sync.load_api(self.api, self.f.identity["tool_sha256"]["tools/release_app.py"])
        self.f.store("before", {"schema": 1, "state": "FILES_INSTALLED", "commit": self.f.commit,
                               "manifest_sha256": "a" * 64, "release": "/opt/opensim/other-owner"})
        self.desired["before_receipt"] = self.f.pin("before")
        with self.assertRaisesRegex(gate.GateError, "escaped release layout"):
            sync.validate_before(api, self.desired)

    def test_real_peer_bundle_validate_and_bad_pin(self):
        peer_root = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-release")
        # Select only a bundle the peer explicitly produced as BUNDLE_PREPARED.
        candidates = sorted(peer_root.glob("fixtures-*/bundle-v1/manifest.json"))
        self.assertTrue(candidates, "No actual peer API bundle available; integration unverified")
        manifest_path = candidates[-1]
        raw = manifest_path.read_bytes()
        manifest = gate.decode(raw)
        api = sync.load_api(self.api, self.f.identity["tool_sha256"]["tools/release_app.py"])
        value = api.validate_bundle(manifest_path.parent.resolve(), gate.sha(raw), manifest["commit"])
        self.assertEqual(value, manifest)
        self.assertEqual(manifest_path.read_bytes(), raw)
        with self.assertRaisesRegex(api.ReleaseError, "manifest SHA drift"):
            api.validate_bundle(manifest_path.parent.resolve(), "0" * 64, manifest["commit"])

    def test_unit_templates_have_no_publication_privilege(self):
        service = (ROOT / "deploy/opensim-sync.service").read_text()
        path = (ROOT / "deploy/opensim-sync.path").read_text()
        self.assertIn("User=infra", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertNotIn("--apply", service)
        self.assertNotIn("sudo", service)
        self.assertNotIn(".git", path)
        self.assertIn("PathChanged=/var/lib/opensim-sync/desired-release.json", path)

    def test_dns_applied_self_report_not_accepted(self):
        self.desired["dns_receipt"] = {"state": "APPLIED", "authorized": True}
        self.save()
        self.assertIn("Unknown desired-release", self.cli()["failure"]["reason"])


class PreparePinTests(unittest.TestCase):
    """Isolate the post-gate handoff, never certification or production."""

    def setUp(self):
        self.directory = gate.OUTPUT_ROOT / ("prepare-pin-" + uuid4().hex)
        self.directory.mkdir(parents=True)
        self.ledger = self.directory / "ledger.json"
        self.numerics = self.directory / "numerics.json"
        self.body = gate.encode({"scope": "UNIT_FIXTURE_NOT_NUMERICS", "value": 1})
        gate.write_new(self.numerics, self.body)
        self.pin = {"path": str(self.numerics), "sha256": gate.sha(self.body)}
        self.ledger_body = gate.encode({"evidence": {"numerics": self.pin}})
        gate.write_new(self.ledger, self.ledger_body)
        self.commit = "a" * 40
        self.identity = {"source_sha256": {}, "page_build": "123456789abc",
                         "tool_sha256": {"tools/release_app.py": "b" * 64}}
        self.result = {"identity": self.identity, "ledger_sha256": gate.sha(self.ledger_body)}
        self.bundle = self.directory / "bundle-not-created"
        self.calls = []

        def prepare(root, commit, evidence, bundle):
            self.calls.append(str(evidence))
            return {"state": "BUNDLE_PREPARED", "scope": "SOURCE_AND_NUMERICS_ONLY",
                    "bundle": str(bundle), "manifest_sha256": "c" * 64,
                    "commit": commit, "page_build": self.identity["page_build"]}

        self.manifest = {"schema": 1, "commit": self.commit, "source_epoch": 1,
                         "source_sha256": {}, "page_build": self.identity["page_build"],
                         "files": dict.fromkeys(sync.PAYLOAD, {})}
        self.manifest["files"]["evidence/numerics.json"] = {"sha256": self.pin["sha256"]}
        self.api = SimpleNamespace(prepare=prepare, validate_bundle=lambda *args: self.manifest)

    def invoke(self, evaluate=None):
        # Inject only the handoff, leaving the real certification gate closed.
        with patch.object(gate, "evaluate", side_effect=evaluate or (lambda *args: self.result)), \
                patch.object(sync, "load_api", return_value=self.api), \
                patch.object(sync, "prepare_output_guard", return_value=self.bundle), \
                patch.object(gate, "identity", return_value=self.identity), \
                patch.object(gate, "git", return_value=b"1\n"):
            return sync.prepare_guard(ROOT, self.commit, self.ledger, self.bundle)

    def test_unchanged_handoff_positive_control(self):
        self.assertEqual(self.invoke()["state"], "BUNDLE_PREPARED")
        self.assertEqual(self.calls, [str(self.numerics)])
        self.assertEqual(self.ledger.read_bytes(), self.ledger_body)
        self.assertFalse(self.bundle.exists())

    def test_ledger_changed_after_gate_refused_before_prepare(self):
        def changed(*args):
            put(self.ledger, gate.encode({"evidence": {"numerics": self.pin}, "changed": True}))
            return self.result

        with self.assertRaisesRegex(gate.GateError, "Ledger changed during gated prepare"):
            self.invoke(changed)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.bundle.exists())

    def test_bundle_numerics_must_match_gated_pin(self):
        self.manifest["files"]["evidence/numerics.json"] = {"sha256": "d" * 64}
        with self.assertRaisesRegex(gate.GateError, "Bundle numerics differ from gated evidence"):
            self.invoke()
        self.assertEqual(self.calls, [str(self.numerics)])
        self.assertFalse(self.bundle.exists())

    def test_ledger_changed_during_prepare_cannot_return_candidate(self):
        original = self.api.prepare

        def changed(*args):
            receipt = original(*args)
            put(self.ledger, gate.encode({"evidence": {"numerics": self.pin}, "changed": True}))
            return receipt

        self.api.prepare = changed
        with self.assertRaisesRegex(gate.GateError, "Ledger changed during gated prepare"):
            self.invoke()
        self.assertFalse(self.bundle.exists())


def run_prepare_pin(out: Path) -> int:
    """Reuse run_suite in a real child; retain launcher rc and source hashes."""
    gate.external(out)
    out.mkdir(parents=True, exist_ok=False)
    gate.OUTPUT_ROOT = out
    names = ("tools/sync_release.py", "tests/test_sync_release.py", "tools/release_gate.py",
             "tests/test_release_gate.py")
    before = {name: gate.sha(gate.read_file(ROOT / name)) for name in names}
    result = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), "--prepare-pin-child",
         "--out", str(out / "tests")], cwd=ROOT, capture_output=True, text=True,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    put(out / "launcher.stdout", result.stdout.encode())
    put(out / "launcher.stderr", result.stderr.encode())
    after = {name: gate.sha(gate.read_file(ROOT / name)) for name in names}
    report = gate.decode(gate.read_file(out / "tests/report.json"))
    passed = (result.returncode == report["exit_code"] == 0 and report["tests"] == 4 and
              report["failures"] == report["errors"] == 0 and before == after and
              "Ran 4 tests" in (out / "tests/execution.log").read_text() and
              report["line"] in result.stdout)
    gate.write_new(out / "summary.json", gate.encode({
        "scope": "INJECTED_HANDOFF_UNIT_NOT_RELEASE_INTEGRATION",
        "verdict": "PASS" if passed else "FAIL", "launcher_rc": result.returncode,
        "argv": result.args, "tests": report["tests"], "failures": report["failures"],
        "errors": report["errors"], "source_before": before, "source_after": after,
        "source_stable": before == after, "production_changes": False,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "stdout_sha256": gate.sha(result.stdout.encode()),
        "stderr_sha256": gate.sha(result.stderr.encode()),
        "report_sha256": gate.sha(gate.read_file(out / "tests/report.json"))}))
    print(f"OPENSIM_SYNC_PREPARE_PIN verdict={'PASS' if passed else 'FAIL'} "
          f"tests={report['tests']} failures={report['failures']} errors={report['errors']} "
          f"launcher_rc={result.returncode} source_stable={before == after} out={out}")
    return 0 if passed else 1


def persistence_child(out: Path, tool: str, fault: str, entry: str) -> int:
    """Subprocess I/O faults only; never substitute a release gate decision."""
    original = gate.write_new
    if fault == "final-readback":
        original_read = gate.read_file
        injected = False

        def failing_read(path):
            nonlocal injected
            body = original_read(path)
            if path == out.with_suffix(".log") and out.exists() and not injected:
                injected = True
                raise OSError(errno.EIO, "late final actual log readback failure")
            return body

        gate.read_file = failing_read
    if fault == "directory-fsync":
        original_sync = gate.sync_directory
        injected = False

        def failing_sync(path):
            nonlocal injected
            original_sync(path)
            if out.exists() and not injected:
                injected = True
                raise OSError(errno.EIO, "late actual directory fsync failure")

        gate.sync_directory = failing_sync

    def failing_write(path, body, *, owned=None):
        selected = ((fault in {"log-full", "late-log"} and path.suffix == ".log") or
                    (fault in {"late-report", "foreign-report", "foreign-identical"} and
                     path.suffix == ".json"))
        if not selected:
            return original(path, body, owned=owned)
        if fault == "log-full":
            descriptor = os.open("/dev/full", os.O_WRONLY)
            try:
                os.write(descriptor, body)
            finally:
                os.close(descriptor)
            raise AssertionError("/dev/full did not produce ENOSPC")
        if fault == "foreign-identical":
            original(path, body)
        original(path, body, owned=owned)
        if fault == "foreign-report":
            path.unlink()
            put(path, gate.encode({"owner": "OTHER_OWNER_MUST_REMAIN"}))
        raise OSError(errno.ENOSPC, "late persistence after actual write/readback")

    gate.write_new = failing_write
    if fault == "stdout-full":
        original_stdout = sys.stdout
        full = io.TextIOWrapper(io.FileIO("/dev/full", "w"), write_through=True)
        sys.stdout = full
        try:
            return persistence_child(out, tool, "none", entry)
        finally:
            sys.stdout = original_stdout
            full.close()
    if entry == "main":
        if tool == "sync":
            return sync.main(["--desired", str(out.parent / "missing-request.json"),
                              "--desired-sha256", "0" * 64, "--out", str(out)])
        return gate.main(["--commit", "0" * 40, "--ledger",
                          str(out.parent / "missing-ledger.json"), "--out", str(out)])
    status_key, success, failed, label = (
        ("state", "FIXTURE_IO_ONLY", "REFUSED", "OPENSIM_SYNC_IO_FIXTURE") if tool == "sync" else
        ("verdict", "PASS", "BLOCKED", "OPENSIM_GATE_IO_FIXTURE"))
    report = {status_key: success, "exit_code": 0,
              "scope": "PERSISTENCE_FIXTURE_NOT_RELEASE", "production_changes": False,
              "collected_at": datetime.now(timezone.utc).isoformat()}
    return gate.finalize_result(report, out, status_key=status_key,
                                failed_status=failed, label=label)


class PersistenceTests(unittest.TestCase):
    def child(self, tool: str, fault: str, entry="finalize", before=None):
        directory = gate.OUTPUT_ROOT / ("persistence-" + uuid4().hex)
        directory.mkdir()
        out = directory / "report.json"
        if before is not None:
            put(out, before)
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--fault-child",
             fault, "--tool", tool, "--entry", entry, "--out", str(out)],
            cwd=ROOT, capture_output=True, text=True,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        put(directory / "launcher.log",
            (f"rc={result.returncode}\n" + result.stdout + result.stderr).encode())
        put(directory / "stdout.log", result.stdout.encode())
        put(directory / "stderr.log", result.stderr.encode())
        gate.write_new(directory / "observation.json", gate.encode({
            "schema": "opensim-process-observation-v1", "argv": result.args,
            "rc": result.returncode, "report_present": out.exists(),
            "tool": tool, "fault": fault, "entry": entry,
            "scope": "REAL_PROCESS_IO_FIXTURE_NOT_RELEASE",
            "stdout": {"path": str(directory / "stdout.log"),
                       "sha256": gate.sha(result.stdout.encode())},
            "stderr": {"path": str(directory / "stderr.log"),
                       "sha256": gate.sha(result.stderr.encode())},
            "production_changes": False}))
        return out, result

    def test_real_dev_full_log_enospc_cannot_leave_report_zero(self):
        for tool in ("sync", "gate"):
            with self.subTest(tool=tool):
                out, result = self.child(tool, "log-full")
                report = gate.decode(out.read_bytes())
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(report["exit_code"], 1)
                self.assertEqual(report["evidence_failure"]["errno"], errno.ENOSPC)
                self.assertEqual(report["action_result"]["exit_code"], 0)
                self.assertEqual(report["finalization"], "FAILED")

    def test_late_log_persistence_does_not_publish_success(self):
        for tool in ("sync", "gate"):
            with self.subTest(tool=tool):
                out, result = self.child(tool, "late-log")
                report = gate.decode(out.read_bytes())
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(report["exit_code"], 1)
                self.assertIn("PROVISIONAL", out.with_suffix(".log").read_text())

    def test_late_report_persistence_revokes_exact_success(self):
        for tool in ("sync", "gate"):
            with self.subTest(tool=tool):
                out, result = self.child(tool, "late-report")
                report = gate.decode(out.read_bytes())
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(report["exit_code"], 1)
                self.assertEqual(report["finalization"], "FAILED")
                self.assertEqual(report["action_result"]["exit_code"], 0)
                self.assertNotIn("Exact evidence revocation failed", result.stderr)

    def test_actual_directory_fsync_late_failure_revokes_report(self):
        for tool in ("sync", "gate"):
            with self.subTest(tool=tool):
                out, result = self.child(tool, "directory-fsync")
                report = gate.decode(out.read_bytes())
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(report["exit_code"], 1)
                self.assertEqual(report["evidence_failure"]["errno"], errno.EIO)
                self.assertEqual(report["action_result"]["exit_code"], 0)
                self.assertNotIn("Exact evidence revocation failed", result.stderr)

    def test_late_final_readback_failure_revokes_report(self):
        for tool in ("sync", "gate"):
            with self.subTest(tool=tool):
                out, result = self.child(tool, "final-readback")
                report = gate.decode(out.read_bytes())
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(report["exit_code"], 1)
                self.assertEqual(report["evidence_failure"]["errno"], errno.EIO)
                self.assertEqual(report["action_result"]["exit_code"], 0)
                self.assertNotIn("Exact evidence revocation failed", result.stderr)

    def test_actual_stdout_enospc_revokes_report_and_keeps_first_cause(self):
        for tool in ("sync", "gate"):
            for entry in ("finalize", "main"):
                with self.subTest(tool=tool, entry=entry):
                    out, result = self.child(tool, "stdout-full", entry=entry)
                    report = gate.decode(out.read_bytes())
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertEqual(report["exit_code"], 1)
                    self.assertEqual(report["evidence_failure"]["errno"], errno.ENOSPC)
                    self.assertIn("Evidence cause:", result.stderr)
                    self.assertIn("First cause:", result.stderr)
                    self.assertIn("Failure marker output failed:", result.stderr)
                    self.assertNotIn("rc=0 ", result.stdout)
                    if entry == "main":
                        self.assertIn("missing-request" if tool == "sync" else "HEAD/commit mismatch",
                                      report["failure"]["reason"])

    def test_foreign_replacement_is_never_revoked(self):
        out, result = self.child("sync", "foreign-report")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(gate.decode(out.read_bytes()), {"owner": "OTHER_OWNER_MUST_REMAIN"})
        self.assertIn("Exact evidence revocation failed", result.stderr)

    def test_identical_foreign_report_is_not_owned_or_revoked(self):
        out, result = self.child("sync", "foreign-identical")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(gate.decode(out.read_bytes())["exit_code"], 0)
        self.assertIn("Evidence cause", result.stderr)
        self.assertNotIn("Exact evidence revocation failed", result.stderr)

    def test_main_first_cause_survives_enospc(self):
        for tool in ("sync", "gate"):
            with self.subTest(tool=tool):
                out, result = self.child(tool, "log-full", entry="main")
                report = gate.decode(out.read_bytes())
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(report["evidence_failure"]["errno"], errno.ENOSPC)
                self.assertIn("missing-request" if tool == "sync" else "HEAD/commit mismatch",
                              report["failure"]["reason"])
                self.assertEqual(report["failure"], report["action_result"]["failure"])

    def test_existing_out_protected_before_action(self):
        before = gate.encode({"exit_code": 0, "owner": "OTHER_OWNER_MUST_REMAIN"})
        for tool in ("sync", "gate"):
            with self.subTest(tool=tool):
                out, result = self.child(tool, "none", entry="main", before=before)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(out.read_bytes(), before)
                self.assertFalse(out.with_suffix(".log").exists())
                self.assertIn("Existing output protected", result.stderr)

    def test_existing_log_protected_before_action(self):
        directory = gate.OUTPUT_ROOT / ("persistence-existing-log-" + uuid4().hex)
        directory.mkdir()
        out = directory / "report.json"
        log = out.with_suffix(".log")
        put(log, b"OTHER_OWNER_LOG\n")
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--fault-child",
             "none", "--entry", "main", "--out", str(out)],
            cwd=ROOT, capture_output=True, text=True,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        put(directory / "launcher.log",
            (f"rc={result.returncode}\n" + result.stdout + result.stderr).encode())
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(out.exists())
        self.assertEqual(log.read_bytes(), b"OTHER_OWNER_LOG\n")
        self.assertIn("Existing output protected", result.stderr)

    def test_success_requires_matching_process_and_log_hash(self):
        out, result = self.child("sync", "none")
        report = gate.decode(out.read_bytes())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(report["exit_code"], result.returncode)
        self.assertEqual(report["finalization"], "FINAL_PROCESS_RC_REQUIRED")
        self.assertEqual(report["execution_log"]["sha256"], gate.sha(out.with_suffix(".log").read_bytes()))
        self.assertIn(gate.sha(out.read_bytes()), result.stdout)
        def pin(name):
            path = out.parent / name
            return {"path": str(path), "sha256": gate.sha(path.read_bytes())}
        observation = {"schema": "opensim-process-observation-v1", "argv": result.args,
                       "rc": result.returncode, "report": pin("report.json"),
                       "stdout": pin("stdout.log"), "stderr": pin("stderr.log")}
        self.assertEqual(gate.verify_process_result(observation, "OPENSIM_SYNC_IO_FIXTURE", result), report)
        observation["rc"] = 1
        with self.assertRaisesRegex(gate.GateError, "Actual launcher argv/rc"):
            gate.verify_process_result(observation, "OPENSIM_SYNC_IO_FIXTURE", result)
        observation["rc"] = 0
        with self.assertRaisesRegex(gate.GateError, "Actual launcher argv/rc"):
            gate.verify_process_result(observation, "OPENSIM_SYNC_IO_FIXTURE", None)
        put(out.parent / "stdout.log", b"rc=0 SELF_REPORTED_ONLY\n")
        observation["stdout"] = pin("stdout.log")
        with self.assertRaisesRegex(gate.GateError, "Actual launcher stdout/stderr"):
            gate.verify_process_result(observation, "OPENSIM_SYNC_IO_FIXTURE", result)


def prepare_api_child(args) -> int:
    """Real pinned API preparation only; never bypass or certify release gates."""
    gate.new_result_paths(args.out)
    report = {"state": "FAILED", "scope": "OFFLINE_API_INTEGRATION_NOT_CERTIFIED",
              "production_changes": False, "collected_at": datetime.now(timezone.utc).isoformat(),
              "exit_code": 1}
    try:
        root = args.source_root
        api = sync.load_api(args.release_api, args.api_sha256)
        api_root = api.ARTIFACTS
        bundle = sync.prepare_output_guard(api, args.bundle)
        commit = gate.git(root, "rev-parse", "HEAD").decode().strip()
        gate.require(commit == args.commit, "Actual prepare source commit changed before call")
        status = gate.git(root, "status", "--porcelain=v1", "--untracked-files=all",
                          "--ignored=matching")
        gate.require(not status, "Actual prepare source must be clean")
        sources = {name: gate.committed(root, commit, name) for name in api.EXPORTS}
        api_body = gate.read_file(args.release_api)
        prepared = api.prepare(root, commit, args.evidence, bundle)
        gate.require(set(prepared) == {"state", "scope", "bundle", "manifest_sha256",
                                      "commit", "page_build"} and
                     prepared["state"] == "BUNDLE_PREPARED" and
                     prepared["scope"] == "SOURCE_AND_NUMERICS_ONLY" and
                     Path(prepared["bundle"]) == bundle and prepared["commit"] == commit,
                     "Actual prepare receipt/schema differs")
        manifest = api.validate_bundle(bundle, prepared["manifest_sha256"], commit)
        epoch = int(gate.git(root, "show", "-s", "--format=%ct", commit).strip())
        fingerprint = hashlib.sha256()
        for name in gate.SOURCES:
            fingerprint.update(name.encode("ascii") + b"\0" + sources[name] + b"\0")
        expected = {"source_sha256": {name: gate.sha(sources[name]) for name in gate.SOURCES},
                    "page_build": fingerprint.hexdigest()[:12]}
        sync.match_manifest(manifest, {"commit": commit, "source_epoch": epoch}, expected)
        gate.require(prepared["page_build"] == expected["page_build"],
                     "Actual prepare page build mismatch")
        page = gate.decode(gate.read_file(bundle / "build/page-check.json"))
        gate.require(gate.time_value(page["edited_at"]).timestamp() == epoch,
                     "Prepared edited_at must equal honest committed source epoch")
        gate.require(gate.sha(gate.read_file(args.evidence)) ==
                     manifest["files"]["evidence/numerics.json"]["sha256"],
                     "Prepared numerics bytes differ from actual input")
        gate.require(api.COMMANDS and all(row["rc"] == 0 for row in api.COMMANDS),
                     "Actual prepare command failure or missing subprocess evidence")
        gate.require(api.ARTIFACTS == api_root == sync.PREPARE_ROOT and
                     gate.read_file(args.release_api) == api_body,
                     "Actual prepare changed peer API/root")
        gate.require(gate.git(root, "rev-parse", "HEAD").decode().strip() == commit and
                     gate.git(root, "status", "--porcelain=v1", "--untracked-files=all",
                              "--ignored=matching") == status and
                     all(gate.committed(root, commit, name) == body for name, body in sources.items()),
                     "Actual prepare source changed during call")
        report.update(prepared, exit_code=0, api_sha256=gate.sha(api_body),
                      source_before_sha256={name: gate.sha(body) for name, body in sources.items()},
                      source_after_sha256={name: gate.sha(gate.read_file(root / name)) for name in sources},
                      source_status_before=status.decode(), source_status_after=status.decode(),
                      manifest=manifest, commands=api.COMMANDS,
                      release_verdict="NOT_CERTIFIED",
                      checks={"fixed_expected_root": "PASS", "prepare_receipt": "PASS",
                              "validate_bundle": "PASS", "source_unchanged": "PASS",
                              "api_unchanged": "PASS", "manifest_source_epoch": "PASS",
                              "page_build_bytes": "PASS", "edited_at_commit_epoch": "PASS",
                              "numerics_bytes": "PASS", "actual_commands_rc": "PASS"})
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
    return gate.finalize_result(report, args.out, status_key="state", failed_status="FAILED",
                                label="OPENSIM_SYNC_PREPARE_API")


class MainApiTests(unittest.TestCase):
    def real_prepare(self, api_path: Path):
        original_root = Path("/data/dev/opensim")
        commit = gate.git(original_root, "rev-parse", "HEAD").decode().strip()
        original_status = gate.git(original_root, "status", "--porcelain=v1",
                                   "--untracked-files=all", "--ignored=matching")
        api_bytes = gate.read_file(api_path)
        directory = gate.OUTPUT_ROOT / ("real-prepare-" + uuid4().hex)
        directory.mkdir()
        root = directory / "clean-source"
        original_sources = {name: gate.committed(original_root, commit, name)
                            for name in (*gate.SOURCES, "requirements.txt")}
        source_commands = []
        for command in (
                ["/usr/bin/git", "clone", "--no-hardlinks", "--no-checkout", "--",
                 str(original_root), str(root)],
                ["/usr/bin/git", "-c", "core.hooksPath=/dev/null", "-C", str(root),
                 "checkout", "--detach", commit]):
            result = subprocess.run(command, capture_output=True, text=True,
                                    env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                                         "GIT_OPTIONAL_LOCKS": "0"})
            source_commands.append({"argv": result.args, "rc": result.returncode,
                                    "stdout": result.stdout, "stderr": result.stderr})
            self.assertEqual(result.returncode, 0, result.stderr)
        gate.write_new(directory / "source-snapshot.json", gate.encode({
            "scope": "LOCAL_REAL_COMMIT_SNAPSHOT_NO_NETWORK", "commit": commit,
            "original_root": str(original_root), "snapshot_root": str(root),
            "original_status": original_status.decode(), "commands": source_commands,
            "source_sha256": {name: gate.sha(body) for name, body in original_sources.items()}}))
        self.assertEqual(gate.git(root, "status", "--porcelain=v1", "--untracked-files=all",
                                  "--ignored=matching"), b"")
        self.assertEqual({name: gate.committed(root, commit, name) for name in original_sources},
                         original_sources)
        bundle = sync.PREPARE_ROOT / ("sync-prepare-" + uuid4().hex) / "bundle"
        out = directory / "report.json"
        argv = [sys.executable, "-B", str(Path(__file__).resolve()), "--prepare-api-child",
                "--source-root", str(root), "--commit", commit, "--release-api", str(api_path),
                "--api-sha256", gate.sha(api_bytes),
                "--evidence", str(gate.ARTIFACT_ROOT / "local-v8/numerics.json"),
                "--bundle", str(bundle), "--out", str(out)]
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                                         OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1"))
        gate.write_new(directory / "stdout.log", result.stdout.encode())
        put(directory / "stderr.log", result.stderr.encode())
        def pin(path):
            return {"path": str(path), "sha256": gate.sha(path.read_bytes())}
        observation = {"schema": "opensim-process-observation-v1", "argv": result.args,
                       "rc": result.returncode, "report": pin(out),
                       "stdout": pin(directory / "stdout.log"), "stderr": pin(directory / "stderr.log")}
        gate.write_new(directory / "observation.json", gate.encode(observation))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = gate.verify_process_result(observation, "OPENSIM_SYNC_PREPARE_API", result)
        self.assertEqual(report["state"], "BUNDLE_PREPARED")
        self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")
        self.assertFalse(report["production_changes"])
        self.assertEqual(len(report["checks"]), 10)
        self.assertEqual(set(report["checks"].values()), {"PASS"})
        self.assertEqual(report["source_before_sha256"], report["source_after_sha256"])
        api = sync.load_api(api_path, gate.sha(api_bytes))
        self.assertEqual(api.validate_bundle(Path(report["bundle"]), report["manifest_sha256"], commit),
                         report["manifest"])
        self.assertEqual(gate.read_file(api_path), api_bytes)
        self.assertEqual(gate.git(original_root, "rev-parse", "HEAD").decode().strip(), commit)
        self.assertEqual(gate.git(original_root, "status", "--porcelain=v1",
                                  "--untracked-files=all", "--ignored=matching"), original_status)
        self.assertEqual({name: gate.committed(original_root, commit, name) for name in original_sources},
                         original_sources)

    def test_actual_main_prepare_success_validate_and_unchanged_sources(self):
        self.real_prepare(Path("/data/dev/opensim/tools/release_app.py"))

    def test_actual_peer_prepare_success_validate_and_unchanged_sources(self):
        self.real_prepare(Path("/data/dev/opensim-release-v1/tools/release_app.py"))

    def test_main_committed_api_real_cli_output_root_conflict(self):
        root = Path("/data/dev/opensim")
        commit = gate.git(root, "rev-parse", "HEAD").decode().strip()
        api_path = root / "tools/release_app.py"
        api_bytes = gate.committed(root, commit, "tools/release_app.py")
        api = sync.load_api(api_path, gate.sha(api_bytes))
        self.assertEqual(api.ARTIFACTS, gate.ARTIFACT_ROOT / "seat-release")
        directory = gate.OUTPUT_ROOT / ("main-api-" + uuid4().hex)
        directory.mkdir()
        bundle, api_out = directory / "bundle", directory / "api-out"
        argv = ["/usr/bin/python3", "-I", "-B", str(api_path), "--prepare",
                "--source-root", str(root), "--commit", commit,
                "--evidence", str(gate.ARTIFACT_ROOT / "local-v8/numerics.json"),
                "--bundle", str(bundle), "--out", str(api_out)]
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        gate.write_new(directory / "stdout.log", result.stdout.encode())
        gate.write_new(directory / "stderr.log", result.stderr.encode())
        gate.write_new(directory / "observation.json", gate.encode({
            "scope": "REAL_MAIN_API_REFUSAL_NOT_BUNDLE_PREPARED",
            "argv": argv, "rc": result.returncode, "main_commit": commit,
            "api_sha256": gate.sha(api_bytes), "api_report_present": api_out.exists(),
            "bundle_present": bundle.exists(), "production_changes": False,
            "stdout": {"path": str(directory / "stdout.log"),
                       "sha256": gate.sha(result.stdout.encode())},
            "stderr": {"path": str(directory / "stderr.log"),
                       "sha256": gate.sha(result.stderr.encode())}}))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Output must be below seat-release external root", result.stderr)
        self.assertIn("OPENSIM_APP_RELEASE state=FAILED rc=1", result.stdout)
        self.assertFalse(api_out.exists())
        self.assertFalse(bundle.exists())
        self.assertEqual(gate.read_file(api_path), api_bytes)
        self.assertEqual(gate.git(root, "rev-parse", "HEAD").decode().strip(), commit)


def scan(out: Path) -> int:
    """Point-name review using existing credential in memory, never output it."""
    import re
    names = ("tools/release_gate.py", "tools/sync_release.py", "tests/test_release_gate.py",
             "tests/test_sync_release.py", "deploy/opensim-sync.service",
             "deploy/opensim-sync.path", "docs/deploy/SYNC_RELEASE.md")
    token = Path("/opt/notify/relay.token").read_bytes().strip()
    if len(token) < 16:
        raise RuntimeError("Exact credential scan unavailable")
    shapes = re.compile(rb"(?i)\b(?:password|passwd|api[_-]?key|secret|authorization)"
                        rb"[\"']?\s*[:=]\s*[\"'][^\"']{6,}[\"']")
    pem = re.compile(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")
    rows = []
    for name in names:
        body = (ROOT / name).read_bytes()
        rows.append({"path": name, "bytes": len(body), "sha256": gate.sha(body),
                     "exact_hits": body.count(token), "shape_hits": len(shapes.findall(body)),
                     "pem_hits": len(pem.findall(body))})
    diff = subprocess.run(["git", "diff", "--check", "--", *names], cwd=ROOT,
                          capture_output=True, text=True)
    passed = diff.returncode == 0 and all(row["bytes"] > 0 and
                                        not any(row[k] for k in ("exact_hits", "shape_hits", "pem_hits"))
                                        for row in rows)
    report = {"verdict": "PASS" if passed else "FAIL", "files": rows, "diff_rc": diff.returncode,
              "scope": "EXPLICIT_SEVEN_PATHS", "collected_at": datetime.now(timezone.utc).isoformat()}
    gate.write_new(out, gate.encode(report))
    line = f"OPENSIM_SYNC_SCAN verdict={report['verdict']} files={len(rows)} report={out}"
    gate.write_new(out.with_suffix(".log"), (line + "\n" + diff.stdout + diff.stderr).encode())
    print(line)
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--persistence-only", action="store_true")
    parser.add_argument("--main-api-only", action="store_true")
    parser.add_argument("--prepare-pin-only", action="store_true")
    parser.add_argument("--prepare-pin-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--fault-child",
                        choices=("none", "log-full", "late-log", "late-report", "foreign-report",
                                 "foreign-identical", "directory-fsync", "final-readback",
                                 "stdout-full"))
    parser.add_argument("--tool", choices=("sync", "gate"), default="sync")
    parser.add_argument("--entry", choices=("main", "finalize"), default="finalize")
    parser.add_argument("--prepare-api-child", action="store_true")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--release-api", type=Path)
    parser.add_argument("--api-sha256")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--bundle", type=Path)
    args = parser.parse_args()
    if args.prepare_api_child:
        raise SystemExit(prepare_api_child(args))
    if args.fault_child:
        raise SystemExit(persistence_child(args.out, args.tool, args.fault_child, args.entry))
    if args.prepare_pin_only:
        raise SystemExit(run_prepare_pin(args.out))
    if args.prepare_pin_child:
        gate.external(args.out)
        gate.OUTPUT_ROOT = args.out.parent
        raise SystemExit(run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(PreparePinTests),
                                   args.out, "OPENSIM_SYNC_PREPARE_PIN_UNIT_TEST"))
    suite = unittest.TestSuite()
    if not args.persistence_only and not args.main_api_only:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(PreparePinTests))
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(SyncTests))
    if not args.main_api_only:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(PersistenceTests))
    if not args.persistence_only:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(MainApiTests))
    raise SystemExit(scan(args.out) if args.scan else
                     run_suite(suite, args.out, "OPENSIM_SYNC_RELEASE_TEST"))
