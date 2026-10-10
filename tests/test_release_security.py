"""Independent release counterexamples. All state is external; no live secrets.

CLI and unittest discovery freeze the current candidate; --baseline selects the
original reviewed commits. Run with --out NEW_SEAT_SECURITY_DIRECTORY.
The same reviewed file implements
the fixed offline DNS subprocess. Security expectations remain red on vulnerable
sources; a successful exploit is not relabelled as a passing security gate.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import ExitStack
import copy
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CURRENT_ROOT = Path("/data/dev/opensim")
DNS_SOURCE = CURRENT_ROOT / "tools/dns_release.py"
EXTERNAL = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-security")
WRITESET = ("tests/test_release_security.py", "docs/testing/RELEASE_SECURITY.md")
REAL_RUN = subprocess.run
RUN = None
dns = None
FROZEN = {}
APP_SOURCE_ROOT = CURRENT_ROOT
TRANSPORT_ROOT = CURRENT_ROOT
transport = None
TRANSPORT_FROZEN = {}
sys.path.insert(0, str(ROOT))
from tools import release_app as app


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def freeze_sources(out, app_revision, dns_revision, app_root, dns_source):
    global app, dns, FROZEN
    directory = out / "frozen-source"
    directory.mkdir()
    for label, repo, revision, relative in (
            ("app", app_root, app_revision, "tools/release_app.py"),
            ("dns", dns_source.parents[1], dns_revision, "tools/dns_release.py")):
        working = revision == "WORKTREE"
        commit = command(["/usr/bin/git", "rev-parse", "--verify",
                          ("HEAD" if working else revision) + "^{commit}"], repo).stdout.strip()
        source_path = dns_source if label == "dns" else repo / relative
        if working:
            before_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
            body = source_path.read_bytes()
        else:
            result = REAL_RUN(["/usr/bin/git", "show", commit + ":" + relative],
                              cwd=repo, capture_output=True)
            if result.returncode:
                raise RuntimeError(f"Freeze {label} git rc={result.returncode}")
            body = result.stdout
            before_sha = hashlib.sha256(body).hexdigest()
        path = directory / (label + "_release.py")
        with path.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, 0o444)
        if path.read_bytes() != body:
            raise RuntimeError("Frozen source byte readback differs")
        after_sha = hashlib.sha256(source_path.read_bytes()).hexdigest() if working else before_sha
        frozen_sha = hashlib.sha256(body).hexdigest()
        if before_sha != frozen_sha or after_sha != frozen_sha:
            raise RuntimeError(f"Source drift during freeze: {label}")
        FROZEN[label] = {"commit": commit, "path": str(path),
                         "sha256": frozen_sha, "origin": "WORKTREE" if working else "COMMIT",
                         "input_path": str(source_path),
                         "before_sha256": before_sha, "after_sha256": after_sha}
    app = load_source("security_app", Path(FROZEN["app"]["path"]))
    dns = load_source("security_dns", Path(FROZEN["dns"]["path"]))
    # Keep production path constants from the original location. Only executable
    # code bytes are frozen; the transport never executes the real signer.
    dns.ROOT = dns_source.parents[1]
    dns.FIXTURE = dns.ROOT / "tests/test_dns_release.py"
    export_directory = directory / "exports"
    export_directory.mkdir()
    exports = {}
    for name in app.EXPORTS:
        source = app_root / name
        if app_revision == "WORKTREE":
            before = app.sha(source.read_bytes())
            body = source.read_bytes()
        else:
            result = REAL_RUN(["/usr/bin/git", "show", FROZEN["app"]["commit"] + ":" + name],
                              cwd=app_root, capture_output=True)
            if result.returncode:
                raise RuntimeError(f"Freeze app export rc={result.returncode}: {name}")
            body = result.stdout
            before = app.sha(body)
        destination = export_directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(body)
        os.chmod(destination, 0o444)
        after = app.sha(source.read_bytes()) if app_revision == "WORKTREE" else before
        if before != app.sha(body) or after != before or destination.read_bytes() != body:
            raise RuntimeError(f"App export drift during freeze: {name}")
        exports[name] = {"path": str(destination), "sha256": before,
                         "input_path": str(source), "origin": FROZEN["app"]["origin"],
                         "before_sha256": before, "after_sha256": after}
    FROZEN["app"]["exports"] = exports
    references = {}
    if dns_revision == "WORKTREE":
        for name in ("tests/test_dns_release.py", "docs/deploy/DNS_RELEASE.md"):
            source = dns_source.parents[1] / name
            before = app.sha(source.read_bytes())
            body = source.read_bytes()
            destination = directory / "dns-refs" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(destination, 0o444)
            after = app.sha(source.read_bytes())
            if before != app.sha(body) or before != after or destination.read_bytes() != body:
                raise RuntimeError(f"DNS reference drift during freeze: {name}")
            references[name] = {"path": str(destination), "sha256": before,
                                "input_path": str(source), "origin": "WORKTREE",
                                "before_sha256": before, "after_sha256": after}
    FROZEN["dns"]["references"] = references
    dump(out / "frozen-source.json", FROZEN)


def freeze_transport(out, revision):
    global transport, TRANSPORT_FROZEN
    directory = out / "frozen-transport"
    directory.mkdir()
    working = revision == "WORKTREE"
    commit = command(["/usr/bin/git", "rev-parse", "--verify",
                      ("HEAD" if working else revision) + "^{commit}"],
                     TRANSPORT_ROOT).stdout.strip()
    rows = {}
    for relative in ("tools/cert_transport.py", "tests/test_cert_transport.py",
                     "docs/deploy/CERT_TRANSPORT.md"):
        source = TRANSPORT_ROOT / relative
        if working:
            before = app.sha(source.read_bytes())
            body = source.read_bytes()
        else:
            result = REAL_RUN(["/usr/bin/git", "show", commit + ":" + relative],
                              cwd=TRANSPORT_ROOT, capture_output=True)
            if result.returncode:
                raise RuntimeError(f"Freeze transport git rc={result.returncode}: {relative}")
            body = result.stdout
            before = app.sha(body)
        destination = directory / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(destination, 0o444)
        after = app.sha(source.read_bytes()) if working else before
        if destination.read_bytes() != body or before != app.sha(body) or before != after:
            raise RuntimeError("Frozen transport readback differs")
        rows[relative] = {"path": str(destination), "sha256": app.sha(body),
                          "input_path": str(source),
                          "before_sha256": before, "after_sha256": after}
    TRANSPORT_FROZEN = {"commit": commit, "files": rows,
                        "origin": "WORKTREE" if working else "COMMIT"}
    dump(out / "frozen-transport.json", TRANSPORT_FROZEN)
    transport = load_source("security_transport", directory / "tools/cert_transport.py")


def ensure_current_run():
    """Unittest discovery performs actual current-source tests, with no skips."""
    global RUN, APP_SOURCE_ROOT, TRANSPORT_ROOT
    if os.geteuid() == 0:
        raise RuntimeError("Security tests must run as infra, never root")
    if RUN is None:
        EXTERNAL.mkdir(parents=True, exist_ok=True)
        RUN = (EXTERNAL / ("discover-" + uuid4().hex)).resolve()
        RUN.mkdir()
        APP_SOURCE_ROOT = CURRENT_ROOT
        TRANSPORT_ROOT = CURRENT_ROOT
        freeze_sources(RUN, "WORKTREE", "WORKTREE", CURRENT_ROOT, DNS_SOURCE)
        freeze_transport(RUN, "WORKTREE")
        verify_frozen_inputs()


def input_observations():
    rows = dict(FROZEN)
    rows.update({"export:" + name: row
                 for name, row in FROZEN.get("app", {}).get("exports", {}).items()})
    rows.update({"dns-ref:" + name: row
                 for name, row in FROZEN.get("dns", {}).get("references", {}).items()})
    if TRANSPORT_FROZEN.get("origin") == "WORKTREE":
        rows.update({"transport:" + name: {**row, "origin": "WORKTREE"}
                     for name, row in TRANSPORT_FROZEN["files"].items()})
    observed = {}
    for name, row in rows.items():
        if row["origin"] != "WORKTREE":
            continue
        current = hashlib.sha256(Path(row["input_path"]).read_bytes()).hexdigest()
        observed[name] = {"sha256": current, "matches_frozen": current == row["sha256"]}
    return observed


def verify_frozen_inputs():
    observed = input_observations()
    dump(RUN / "input-before-tests.json", observed)
    if any(not row["matches_frozen"] for row in observed.values()):
        raise RuntimeError("Candidate changed during multi-file freeze; preserve and stop")


def dump(path, value):
    with path.open("xb") as stream:
        stream.write(app.encode(value))
        stream.flush()
        os.fsync(stream.fileno())


def rewrite(path, body):
    """Only mutate test-owned fixtures, never repository source."""
    with path.open("wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def command(argv, cwd=None):
    result = REAL_RUN(argv, cwd=cwd, capture_output=True, text=True,
                      env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                           "PYTHONDONTWRITEBYTECODE": "1",
                           "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"})
    if result.returncode:
        raise RuntimeError(f"Fixture command rc={result.returncode}: {argv}: {result.stderr}")
    return result


def offline_signer():
    directory = Path(os.environ["SECURITY_DNS_STATE"])
    if EXTERNAL.resolve() not in directory.resolve().parents:
        return 91
    if len(sys.argv) != 3 or sys.argv[1] != "--offline-dns":
        return 92
    action = sys.argv[2]
    request = dns.strict_json(sys.stdin.read())
    state = dns.strict_json((directory / "dns-state.json").read_text())
    expected = {
        "DescribeRecordList": {"Domain": dns.ZONE, "Offset": 0, "Limit": dns.LIMIT},
        "CreateRecord": {"Domain": dns.ZONE, "SubDomain": "opensim", "RecordType": "A",
                         "RecordLine": dns.DESIRED["Line"],
                         "Value": dns.DESIRED["Value"], "TTL": 600},
        "DeleteRecord": {"Domain": dns.ZONE, "RecordId": state["record_id"]},
    }
    if action not in expected or request != expected[action]:
        return 93
    with (directory / "dns-calls.jsonl").open("ab") as stream:
        stream.write((json.dumps({"action": action, "payload": request}) + "\n").encode())
        stream.flush()
        os.fsync(stream.fileno())
    reply = {"RequestId": str(uuid4())}
    if action == "DescribeRecordList":
        reply.update({"RecordList": state["records"],
                      "RecordCountInfo": {"TotalCount": len(state["records"])}})
        if state.get("variant") == "secret_field":
            reply["RecordList"] = [{"RecordId": 9001, **dns.DESIRED,
                                    "Value": "offline-" + "sensitive"}]
            reply["RecordCountInfo"]["TotalCount"] = 1
    elif action == "CreateRecord":
        state["records"].append({"RecordId": state["record_id"], **dns.DESIRED})
        reply["RecordId"] = state["record_id"]
    else:
        state["records"] = [r for r in state["records"]
                            if r["RecordId"] != request["RecordId"]]
    rewrite(directory / "dns-state.json", app.encode(state))
    print(json.dumps(reply, ensure_ascii=True))
    return 0


class OfflineTransport:
    """Hard allowlist: no provided argv ever executes SSH, signer or systemctl."""

    def __init__(self, directory):
        self.directory = directory
        self.ssh_calls = 0

    def __call__(self, argv, **kwargs):
        if argv[0] == "ssh":
            if argv[1:3] != ["-i", "/home/infra/.ssh/id_ed25519"]:
                raise AssertionError("Unexpected credential transport")
            if "infra@192.168.1.183" not in argv or kwargs.get("input") is not None:
                raise AssertionError("Unexpected credential target")
            self.ssh_calls += 1
            body = (dns.SAVED + "SecretId='offline-id'\n" +
                    dns.SAVED + "SecretKey='offline-key'\n")
            return subprocess.CompletedProcess(argv, 0, body, "")
        if len(argv) != 8 or argv[3] != "dnspod" or argv[5:] != [dns.VERSION, "-d", "-"]:
            raise AssertionError("Non-DNS subprocess forbidden")
        if Path(argv[2]) not in {dns.SIGNER, dns.FIXTURE}:
            raise AssertionError("Unexpected signer")
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
               "PYTHONDONTWRITEBYTECODE": "1", "SECURITY_DNS_STATE": str(self.directory),
               "SECURITY_DNS_SOURCE": FROZEN["dns"]["path"]}
        return REAL_RUN([sys.executable, "-B", str(Path(__file__).resolve()),
                         "--offline-dns", argv[4]], input=kwargs["input"],
                        capture_output=True, text=True, env=env, timeout=45)


class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_current_run()
        cls.out = RUN
        cls.approot = patch.object(app, "ARTIFACTS", EXTERNAL)
        cls.approot.start()
        cls.addClassCleanup(cls.approot.stop)
        cls.repo = RUN / "committed-source"
        cls.repo.mkdir()
        for name in app.EXPORTS:
            path = cls.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            frozen_source = FROZEN["app"]["exports"][name]
            dump_body = Path(frozen_source["path"]).read_bytes()
            if app.sha(dump_body) != frozen_source["sha256"]:
                raise RuntimeError(f"Frozen app export drift: {name}")
            with path.open("xb") as stream:
                stream.write(dump_body)
        events = []
        for argv in (
                ["/usr/bin/git", "init", "--quiet"],
                ["/usr/bin/git", "add", "--", *app.EXPORTS],
                ["/usr/bin/git", "-c", "user.name=SecurityFixture",
                 "-c", "user.email=security@invalid.example", "-c", "commit.gpgsign=false",
                 "commit", "--quiet", "-m", "Independent security fixture"]):
            result = command(argv, cls.repo)
            events.append({"argv": argv, "rc": result.returncode})
        cls.commit = command(["/usr/bin/git", "rev-parse", "HEAD"], cls.repo).stdout.strip()
        cls.evidence = RUN / "numerics.json"
        result = command(["/usr/bin/python3", "-B", str(ROOT / "tools/validate_numerics.py"),
                          "--solver-root", str(cls.repo), "--out", str(cls.evidence),
                          "--log-dir", str(RUN / "numeric-logs")], ROOT)
        cls.bundle = RUN / "bundle"
        cls.prepared = app.prepare(cls.repo, cls.commit, cls.evidence, cls.bundle)
        cls.digest = cls.prepared["manifest_sha256"]
        dump(RUN / "preparation.json", {"events": events, "numerics_rc": result.returncode,
                                       "prepared": cls.prepared,
                                       "source_commands": app.COMMANDS.copy()})

    def setUp(self):
        self.case = RUN / self._testMethodName
        self.case.mkdir()
        self.layout = app.Layout(self.case / "host")
        self.dns_root = patch.object(dns, "ARTIFACTS", self.case)
        self.dns_root.start()
        self.addCleanup(self.dns_root.stop)
        self.transport = OfflineTransport(self.case)
        dump(self.case / "dns-state.json", {"records": [], "record_id": 9001})
        dump(self.case / "state.json", {"scope": "OFFLINE_SENTINEL"})
        self.ledger = self.case / "receipt.jsonl"

    def install(self):
        return app.install(self.layout, self.bundle, self.digest, self.commit)

    def mutable_bundle(self):
        bundle = self.case / "mutable-bundle"
        shutil.copytree(self.bundle, bundle)
        for path in [bundle, *bundle.rglob("*")]:
            os.chmod(path, 0o755 if path.is_dir() else 0o644)
        return bundle

    def reseal(self, bundle, manifest):
        bodies = {name: (bundle / name).read_bytes() for name in app.PAYLOAD}
        manifest["source_sha256"] = {
            name: app.sha(bodies["source/" + name]) for name in app.SOURCES}
        fingerprint = hashlib.sha256()
        for name in app.SOURCES:
            fingerprint.update(name.encode() + b"\0" + bodies["source/" + name] + b"\0")
        old_build, manifest["page_build"] = manifest["page_build"], fingerprint.hexdigest()[:12]
        page = bodies["build/index-v1.html"].replace(old_build.encode(),
                                                     manifest["page_build"].encode())
        rewrite(bundle / "build/index-v1.html", page)
        page_check = app.decode(bodies["build/page-check.json"])
        page_check.update({"html_sha256": app.sha(page),
                          "source_sha256_prefix": manifest["page_build"]})
        rewrite(bundle / "build/page-check.json", app.encode(page_check))
        for name, row in manifest["files"].items():
            body = (bundle / name).read_bytes()
            row.update({"sha256": app.sha(body), "bytes": len(body)})
            os.utime(bundle / name, (manifest["source_epoch"], manifest["source_epoch"]))
        rewrite(bundle / "manifest.json", app.encode(manifest))
        for path in [bundle, *bundle.rglob("*")]:
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        return app.sha((bundle / "manifest.json").read_bytes())

    def dns_cli(self, mode, fixture=True):
        output = io.StringIO()
        argv = [mode, "--release-id", "security-review", "--ledger", str(self.ledger)]
        argv += ["--fixture-dir", str(self.case)] if fixture else ["--authorized"]
        with patch.object(dns.subprocess, "run", side_effect=self.transport), \
                patch.object(dns.sys, "stdout", output), \
                patch.object(socket, "socket", side_effect=AssertionError("Network forbidden")):
            rc = dns.main(argv)
        receipt = json.loads(output.getvalue())
        dump(self.case / (mode + "-" + uuid4().hex[:8] + ".json"),
             {"rc": rc, "receipt": receipt, "ssh_calls": self.transport.ssh_calls})
        return rc, receipt

    def calls(self):
        path = self.case / "dns-calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def events(self):
        return [json.loads(line) for line in self.ledger.read_text().splitlines()]

    def forged_ledger(self, backend="production", rid=9001, outcome="APPLIED"):
        ledger = dns.Ledger(self.ledger, "security-review", backend)
        ledger.before = []
        ledger.append("apply", "CREATE_INTENT")
        ledger.created_record_id = rid
        ledger.append("apply", "CREATE_ACK")
        ledger.append("apply", "FINAL", outcome=outcome)
        os.chmod(self.ledger, 0o666)

    def test_app_unchanged_bundle_true_filesystem_install_check_revert(self):
        receipt = self.install()
        self.assertEqual(app.check(self.layout)["state"], "FILES_VALIDATED")
        self.assertEqual(self.layout.unit.read_bytes(),
                         app.unit_bytes(self.layout, Path(receipt["release"])))
        self.assertEqual(app.revert(self.layout)["state"], "REVERTED")
        self.assertFalse(self.layout.unit.exists())
        self.assertTrue(Path(receipt["release"]).exists())

    def test_app_changed_pinned_manifest_is_refused(self):
        bundle = self.mutable_bundle()
        manifest = app.decode((bundle / "manifest.json").read_bytes())
        rewrite(bundle / "source/server.py", b"raise RuntimeError('injected')\n")
        self.reseal(bundle, manifest)
        with self.assertRaisesRegex(app.ReleaseError, "manifest SHA drift"):
            app.validate_bundle(bundle, self.digest, self.commit)
        self.assertFalse(self.layout.state.exists())

    def test_app_owner_can_reseal_but_new_digest_is_not_approval(self):
        bundle = self.mutable_bundle()
        manifest = app.decode((bundle / "manifest.json").read_bytes())
        rewrite(bundle / "source/server.py", b"raise RuntimeError('injected')\n")
        digest = self.reseal(bundle, manifest)
        accepted = app.validate_bundle(bundle, digest, self.commit)
        dump(self.case / "approval-boundary.json",
             {"changed_digest": digest != self.digest,
              "accepted_with_attacker_chosen_pin": accepted["commit"] == self.commit,
              "scope": "NEW_PIN_IS_NEW_APPROVAL_NOT_BYPASS_OF_EXISTING_PIN"})
        self.assertNotEqual(digest, self.digest)

    def test_app_before_snapshot_unknown_unit_injection_refused(self):
        receipt = self.install()
        before_unit = self.layout.unit.read_bytes()
        receipt["before_unit_base64"] = base64.b64encode(
            b"[Service]\nExecStart=/bin/false\n").decode()
        rewrite(self.layout.state, app.encode(receipt))
        with self.assertRaisesRegex(app.ReleaseError, "Unknown original unit"):
            app.revert(self.layout)
        self.assertEqual(self.layout.unit.read_bytes(), before_unit)

    def test_app_previous_snapshot_unknown_unit_injection_refused(self):
        receipt = self.install()
        receipt["previous"] = copy.deepcopy(receipt)
        receipt["before_unit_base64"] = base64.b64encode(b"UNKNOWN UNIT\n").decode()
        rewrite(self.layout.state, app.encode(receipt))
        before = self.layout.unit.read_bytes()
        with self.assertRaisesRegex(app.ReleaseError, "snapshot mismatch"):
            app.revert(self.layout)
        self.assertEqual(self.layout.unit.read_bytes(), before)

    def test_app_source_hardlink_and_symlink_refused(self):
        target = self.case / "plain"
        rewrite(target, b"owned fixture bytes")
        linked = self.case / "hardlinked"
        os.link(target, linked)
        with self.assertRaisesRegex(app.ReleaseError, "hardlinked"):
            app.read_file(linked)
        soft = self.case / "soft"
        soft.symlink_to(target)
        with self.assertRaisesRegex(app.ReleaseError, "Symlink"):
            app.read_file(soft)

    def test_app_copy_time_drift_is_not_installed(self):
        bundle = self.mutable_bundle()
        manifest = app.decode((bundle / "manifest.json").read_bytes())
        digest = self.reseal(bundle, manifest)
        original = app.read_file
        attacked = False

        def read_with_drift(path):
            nonlocal attacked
            if path == bundle / "source/server.py" and not attacked:
                attacked = True
                os.chmod(path, 0o644)
                rewrite(path, b"raise RuntimeError('drift at read')\n")
                os.chmod(path, 0o444)
            return original(path)

        with patch.object(app, "read_file", side_effect=read_with_drift):
            with self.assertRaisesRegex(app.ReleaseError, "byte/hash drift|before copy"):
                app.copy_release(bundle, self.case / "not-installed", digest, self.commit)
        self.assertTrue(attacked)
        self.assertFalse((self.case / "not-installed").exists())

    def test_app_forged_minimal_numerics_with_correct_solver_is_not_acceptance(self):
        evidence = {"state": "COMPLETE", "verdict": "PASS", "checks_executed": 1,
                    "solver": {"sha256": app.sha((ROOT / "solver.py").read_bytes())},
                    "cases": [{"verdict": "PASS"}], "rejections": [{"verdict": "PASS"}],
                    "convergence": {name: {"verdict": "PASS", "resolutions": [8, 16, 32],
                                           "relative_flow_error": {"errors": [3, 2, 1]}}
                                    for name in ("circle", "rectangle", "square")}}
        dump(self.case / "forged-numerics.json", evidence)
        # The full numerical/raw-artifact oracle is intentionally not invoked here.
        accepted = app.validate_evidence(app.encode(evidence), evidence["solver"]["sha256"])
        dump(self.case / "evidence-boundary.json",
             {"minimal_forgery_accepted": bool(accepted),
              "scope": "STRUCTURE_ONLY_EXTERNAL_NUMERICAL_GATE_REQUIRED"})
        self.assertEqual(accepted, evidence)

    def test_app_log_evidence_failure_returns_failure_after_install(self):
        out = self.case / "cli-output"
        original = app.write_new

        def evidence_failure(path, body, mode=0o444):
            if path == out / "execution.log":
                raise OSError(errno.ENOSPC, "offline evidence full")
            return original(path, body, mode)

        with patch.object(app, "write_new", side_effect=evidence_failure), \
                patch.object(app.sys, "stdout", io.StringIO()), \
                patch.object(app.sys, "stderr", io.StringIO()):
            rc = app.main(["--apply", "--bundle", str(self.bundle), "--commit", self.commit,
                           "--manifest-sha256", self.digest,
                           "--fixture-root", str(self.case / "host"), "--out", str(out)])
        report = app.decode((out / "report.json").read_bytes())
        dump(self.case / "partial-evidence.json",
             {"actual_rc": rc, "report_rc": report["exit_code"],
              "report_state": report["state"], "installed_unit": self.layout.unit.exists(),
              "execution_log_exists": (out / "execution.log").exists()})
        self.assertEqual(rc, 1)
        self.assertTrue(self.layout.unit.exists())
        self.assertEqual(report["exit_code"], rc,
                         "P2: report.json remains rc=0 despite evidence failure")
        self.assertEqual(report["state"], "FAILED")
        self.assertEqual(report["action_result"]["state"], "FILES_INSTALLED")
        self.assertEqual(report["evidence_failure"]["type"], "OSError")

    def test_app_missing_log_directory_is_not_files_validated(self):
        self.install()
        self.layout.logs.rmdir()
        try:
            observed = app.check(self.layout)
        except app.ReleaseError as exc:
            dump(self.case / "missing-log.json",
                 {"rejected": type(exc).__name__, "reason": str(exc),
                  "log_directory_exists": self.layout.logs.exists()})
            self.assertIn("log directory", str(exc))
            return
        dump(self.case / "missing-log.json",
             {"reported": observed, "log_directory_exists": self.layout.logs.exists()})
        self.assertNotEqual(observed["state"], "FILES_VALIDATED",
                            "P2: missing required writable log directory is not checked")

    def test_app_existing_output_directory_does_not_receive_report(self):
        out = self.case / "existing-out"
        out.mkdir()
        sentinel = out / "other-owner"
        rewrite(sentinel, b"KEEP EXACT\n")
        with patch.object(app.sys, "stdout", io.StringIO()), \
                patch.object(app.sys, "stderr", io.StringIO()):
            rc = app.main(["--bundle", str(self.bundle), "--commit", self.commit,
                           "--manifest-sha256", self.digest,
                           "--fixture-root", str(self.case / "host"), "--out", str(out)])
        dump(self.case / "existing-output.json",
             {"rc": rc, "entries": sorted(p.name for p in out.iterdir()),
              "sentinel_unchanged": sentinel.read_bytes() == b"KEEP EXACT\n"})
        self.assertEqual(rc, 1)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["other-owner"],
                         "P2: failed fresh-directory check writes into existing owner's directory")

    def test_app_action_first_cause_survives_evidence_write_failure(self):
        out = self.case / "cli-first-cause"
        original = app.write_new

        def evidence_failure(path, body, mode=0o444):
            if path == out / "execution.log":
                raise OSError(errno.ENOSPC, "offline evidence full")
            return original(path, body, mode)

        with patch.object(app, "write_new", side_effect=evidence_failure), \
                patch.object(app.sys, "stdout", io.StringIO()), \
                patch.object(app.sys, "stderr", io.StringIO()):
            rc = app.main(["--apply", "--bundle", str(self.bundle), "--commit", self.commit,
                           "--manifest-sha256", "0" * 64,
                           "--fixture-root", str(self.case / "host"), "--out", str(out)])
        report = app.decode((out / "report.json").read_bytes())
        dump(self.case / "first-cause.json", {"actual_rc": rc, "report": report,
                                             "unit_exists": self.layout.unit.exists()})
        self.assertEqual(rc, 1)
        self.assertEqual(report["exit_code"], 1)
        self.assertIn("manifest SHA drift", report["failure"]["reason"])
        self.assertEqual(report["evidence_failure"]["type"], "OSError")
        self.assertEqual(report["action_result"]["failure"], report["failure"])
        self.assertFalse(self.layout.unit.exists())

    def test_app_report_postwrite_fsync_failure_cannot_leave_success_json(self):
        out = self.case / "cli-tail-failure"
        original = app.sync_directory
        injected = False

        def failed_final_sync(path):
            nonlocal injected
            original(path)
            if path == out and (out / "report.json").exists() and not injected:
                injected = True
                raise OSError(errno.EIO, "offline report final fsync failure")

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(app, "sync_directory", side_effect=failed_final_sync), \
                patch.object(app.sys, "stdout", stdout), \
                patch.object(app.sys, "stderr", stderr):
            rc = app.main(["--apply", "--bundle", str(self.bundle), "--commit", self.commit,
                           "--manifest-sha256", self.digest,
                           "--fixture-root", str(self.case / "host"), "--out", str(out)])
        report_path = out / "report.json"
        report = app.decode(report_path.read_bytes()) if report_path.exists() else None
        log_path = out / "execution.log"
        log = log_path.read_text() if log_path.exists() else None
        dump(self.case / "tail-failure.json",
             {"actual_rc": rc, "report": report, "log": log,
              "stdout": stdout.getvalue(), "stderr": stderr.getvalue(),
              "fault_injected": injected, "unit_exists": self.layout.unit.exists()})
        self.assertTrue(injected, "Final report persistence fault was not reached")
        self.assertEqual(rc, 1)
        self.assertFalse(report is not None and report.get("exit_code") == 0,
                         "P2: fully written success report survives failed final persistence check")

    def test_app_report_complete_bytes_readback_failure_is_revoked(self):
        out = self.case / "cli-readback-failure"
        original = app.read_file
        injected = False

        def failed_readback(path):
            nonlocal injected
            if path == out / "report.json" and not injected:
                injected = True
                self.assertEqual(app.decode(original(path))["exit_code"], 0)
                raise OSError(errno.EIO, "offline completed report readback failure")
            return original(path)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(app, "read_file", side_effect=failed_readback), \
                patch.object(app.sys, "stdout", stdout), \
                patch.object(app.sys, "stderr", stderr):
            rc = app.main(["--apply", "--bundle", str(self.bundle), "--commit", self.commit,
                           "--manifest-sha256", self.digest,
                           "--fixture-root", str(self.case / "host"), "--out", str(out)])
        report_path = out / "report.json"
        report = app.decode(report_path.read_bytes()) if report_path.exists() else None
        dump(self.case / "readback-failure.json",
             {"rc": rc, "report": report, "fault_injected": injected,
              "unit_exists": self.layout.unit.exists(), "stdout": stdout.getvalue(),
              "stderr": stderr.getvalue()})
        self.assertTrue(injected)
        self.assertEqual(rc, 1)
        self.assertTrue(self.layout.unit.exists())
        self.assertEqual(stdout.getvalue(), "")
        self.assertFalse(report is not None and report.get("exit_code") == 0)

    def test_app_persistent_revocation_failure_requires_real_process_rc(self):
        out = self.case / "cli-persistent-readback"
        original = app.read_file
        readbacks = 0

        def unavailable_report(path):
            nonlocal readbacks
            if path == out / "report.json":
                readbacks += 1
                raise OSError(errno.EIO, "offline persistent report readback failure")
            return original(path)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(app, "read_file", side_effect=unavailable_report), \
                patch.object(app.sys, "stdout", stdout), \
                patch.object(app.sys, "stderr", stderr):
            rc = app.main(["--apply", "--bundle", str(self.bundle), "--commit", self.commit,
                           "--manifest-sha256", self.digest,
                           "--fixture-root", str(self.case / "host"), "--out", str(out)])
        report = app.decode((out / "report.json").read_bytes())
        # Persistent I/O failure cannot guarantee file revocation. A saved rc=0
        # is never sufficient authority; the caller must bind the real process rc.
        dump(self.case / "revocation-boundary.json",
             {"rc": rc, "file_report_rc": report["exit_code"], "readbacks": readbacks,
              "stdout": stdout.getvalue(), "stderr": stderr.getvalue(),
              "file_alone_is_not_acceptance": True})
        self.assertEqual(rc, 1)
        self.assertGreaterEqual(readbacks, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("invocation unverified", stderr.getvalue())
        self.assertTrue(self.layout.unit.exists())

    def test_root_controller_checkout_rejected_without_root_execution(self):
        with patch.object(app.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(app.ReleaseError, "checkout controller"):
                app.require_controller()
        self.assertEqual(os.geteuid(), self.layout.state.parent.stat().st_uid
                         if self.layout.state.parent.exists() else os.geteuid())

    def test_root_owned_trust_rejects_infra_owner_even_readonly(self):
        candidate = self.case / "controller.py"
        rewrite(candidate, b"raise RuntimeError('must not execute')\n")
        os.chmod(candidate, 0o444)
        with self.assertRaisesRegex(app.ReleaseError, "Root-owned"):
            app.root_owned(candidate)
        dump(self.case / "ownership.json",
             {"owner_uid": candidate.stat().st_uid,
              "mode": oct(stat.S_IMODE(candidate.stat().st_mode)),
              "root_owned_gate": "REJECTED", "execution": "NONE"})

    def test_app_production_install_denied_before_files_or_subprocess(self):
        layout = app.Layout()
        with patch.object(app.subprocess, "run", side_effect=AssertionError(
                "Production subprocess forbidden")):
            with self.assertRaisesRegex(app.ReleaseError, "authorized root"):
                app.install(layout, self.bundle, self.digest, self.commit)

    def test_app_install_never_executes_source_or_dependencies(self):
        with patch.object(app.subprocess, "run", side_effect=AssertionError(
                "Install must remain data-only")):
            self.install()
            self.assertEqual(app.check(self.layout)["state"], "FILES_VALIDATED")
            app.revert(self.layout)

    def test_app_dependency_drift_rejected_before_fetch_or_oracle(self):
        self.install()
        receipt = app.decode(self.layout.state.read_bytes())
        with patch.object(app.importlib.metadata, "version", return_value="0.0.0"), \
                patch.object(app, "fetch", side_effect=AssertionError("Must not fetch")):
            with self.assertRaisesRegex(app.ReleaseError, "Dependency drift"):
                app.probe(self.layout, receipt, "http://127.0.0.1:8779")

    def test_app_evidence_content_drift_refused_with_original_pin(self):
        receipt = self.install()
        path = Path(receipt["release"]) / "evidence/numerics.json"
        os.chmod(path, 0o644)
        rewrite(path, app.encode({"state": "RUNNING", "verdict": "PASS"}))
        os.chmod(path, 0o444)
        with self.assertRaisesRegex(app.ReleaseError, "byte/hash drift"):
            app.check(self.layout)

    def test_dns_real_subprocess_create_revert_preserves_foreign_record(self):
        foreign = {"RecordId": 7, **dns.DESIRED, "Name": "other-owner"}
        rewrite(self.case / "dns-state.json",
                app.encode({"records": [foreign], "record_id": 9001}))
        self.assertEqual(self.dns_cli("apply")[0], 0)
        self.assertEqual(self.dns_cli("revert")[0], 0)
        self.assertEqual(json.loads((self.case / "dns-state.json").read_text())["records"],
                         [foreign])
        self.assertEqual([c["action"] for c in self.calls()
                          if c["action"] != "DescribeRecordList"],
                         ["CreateRecord", "DeleteRecord"])

    def test_dns_existing_record_without_forged_ownership_not_deleted(self):
        rewrite(self.case / "dns-state.json",
                app.encode({"records": [{"RecordId": 9001, **dns.DESIRED}],
                            "record_id": 9001}))
        self.assertEqual(self.dns_cli("apply")[1]["outcome"], "NO_CHANGE")
        self.assertEqual(self.dns_cli("revert")[0], 2)
        self.assertFalse(any(c["action"] == "DeleteRecord" for c in self.calls()))

    def test_dns_forged_world_writable_production_ledger_cannot_delete_foreign(self):
        self.forged_ledger()
        rewrite(self.case / "dns-state.json",
                app.encode({"records": [{"RecordId": 9001, **dns.DESIRED}],
                            "record_id": 9001}))
        rc, receipt = self.dns_cli("revert", fixture=False)
        dump(self.case / "counterexample.json",
             {"rc": rc, "outcome": receipt["outcome"],
              "ledger_mode": oct(stat.S_IMODE(self.ledger.stat().st_mode)),
              "actual_subprocess_calls": self.calls(),
              "remaining_records": json.loads((self.case / "dns-state.json").read_text())["records"],
              "scope": "PRODUCTION_BRANCH_FAKE_TRANSPORT_NO_PRODUCTION_MUTATIONS"})
        self.assertFalse(any(c["action"] == "DeleteRecord" for c in self.calls()),
                         "P1: fabricated CREATE_ACK authorizes deleting preexisting RecordId")

    def test_dns_unchanged_fixture_backend_rejected_before_transport(self):
        self.assertEqual(self.dns_cli("apply")[0], 0)
        before = self.ledger.read_bytes()
        self.assertEqual(self.dns_cli("revert", fixture=False)[0], 2)
        self.assertEqual(self.transport.ssh_calls, 0)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_dns_relabelled_fixture_ledger_cannot_authorize_production(self):
        self.assertEqual(self.dns_cli("apply")[0], 0)
        events = self.events()
        for entry in events:
            entry["backend"] = "production"
        rewrite(self.ledger, b"".join(
            (json.dumps(e, ensure_ascii=True, sort_keys=True) + "\n").encode() for e in events))
        rc, receipt = self.dns_cli("revert", fixture=False)
        dump(self.case / "counterexample.json",
             {"rc": rc, "outcome": receipt["outcome"], "calls": self.calls(),
              "ssh_calls": self.transport.ssh_calls, "scope": "OFFLINE_ONLY"})
        self.assertFalse(any(c["action"] == "DeleteRecord" for c in self.calls()),
                         "P1: backend label mutation converts fixture ownership to production")

    def test_dns_unknown_outcome_is_refused_before_api(self):
        self.forged_ledger(backend="fixture:" + str(self.case), outcome="UNKNOWN")
        before = self.ledger.read_bytes()
        self.assertEqual(self.dns_cli("revert")[0], 2)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_dns_missing_record_without_ack_is_not_success(self):
        self.forged_ledger(backend="fixture:" + str(self.case))
        rc, receipt = self.dns_cli("revert")
        self.assertEqual(rc, 2)
        self.assertEqual(receipt["failure"]["reason"], "owned_record_missing_without_delete_ack")
        self.assertFalse(any(c["action"] == "DeleteRecord" for c in self.calls()))

    def test_dns_hardlinked_ledger_is_refused_without_append(self):
        self.forged_ledger(backend="fixture:" + str(self.case))
        other = self.case / "other-owner-ledger.jsonl"
        os.link(self.ledger, other)
        before = other.read_bytes()
        rc, _ = self.dns_cli("dryrun")
        dump(self.case / "counterexample.json",
             {"rc": rc, "other_owner_changed": other.read_bytes() != before,
              "link_count": other.stat().st_nlink})
        self.assertEqual(other.read_bytes(), before,
                         "P2: hardlinked ledger accepted and appended into other-owner inode")

    def test_dns_failed_child_secret_streams_not_in_receipt(self):
        marker = "offline-" + "sensitive"
        fake = subprocess.CompletedProcess([], 17, marker, marker)
        with patch.object(dns.subprocess, "run", return_value=fake):
            with self.assertRaises(dns.Refuse) as raised:
                dns.child(["offline"], env={}, action="DescribeRecordList")
        self.assertNotIn(marker, str(raised.exception.failure))
        self.assertEqual(raised.exception.failure["rc"], 17)

    def test_dns_successful_child_error_secret_is_not_in_receipt(self):
        marker = "offline-" + "sensitive"
        api = object.__new__(dns.API)
        api.signer, api.env = dns.FIXTURE, {}
        reply = json.dumps({"RequestId": str(uuid4()),
                            "Error": {"Code": marker, "Message": marker}})
        with patch.object(dns.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, reply, marker)):
            with self.assertRaises(dns.Refuse) as raised:
                api.call("DescribeRecordList", {"Domain": dns.ZONE})
        self.assertNotIn(marker, str(raised.exception.failure))

    def test_dns_successful_child_conflict_secret_not_persisted(self):
        rewrite(self.case / "dns-state.json",
                app.encode({"records": [], "record_id": 9001, "variant": "secret_field"}))
        rc, receipt = self.dns_cli("apply")
        marker = "offline-" + "sensitive"
        dump(self.case / "secret-field-summary.json",
             {"rc": rc, "ledger_contains_marker": marker in self.ledger.read_text(),
              "receipt_contains_marker": marker in json.dumps(receipt),
              "failure_reason": receipt["failure"]["reason"]})
        self.assertEqual(rc, 2)
        self.assertFalse(marker in self.ledger.read_text(),
                         "P2: rejected child-controlled DNS Value is persisted before validation")
        self.assertFalse(marker in json.dumps(receipt),
                         "P2: rejected child-controlled DNS Value appears in CLI receipt")

    def test_dns_symlink_ledger_rejected_and_target_unchanged(self):
        target = self.case / "other-owner"
        rewrite(target, b"OWNER BYTES\n")
        self.ledger.symlink_to(target)
        with self.assertRaises(dns.Refuse):
            dns.Ledger(self.ledger, "security-review")
        self.assertEqual(target.read_bytes(), b"OWNER BYTES\n")
        self.assertEqual(self.calls(), [])

    def test_dns_ledger_parent_swap_cannot_escape_frozen_path(self):
        directory = self.case / "ledger-parent"
        directory.mkdir()
        ledger = dns.Ledger(directory / "receipt.jsonl", "security-review")
        directory.rename(self.case / "ledger-original")
        outside = RUN / (self._testMethodName + "-outside")
        outside.mkdir()
        directory.symlink_to(outside, target_is_directory=True)
        caught = None
        try:
            ledger.append("dryrun", "BEGIN")
        except (dns.Refuse, OSError) as exc:
            caught = type(exc).__name__
        dump(self.case / "parent-swap.json",
             {"caught": caught, "outside_receipt_exists": (outside / "receipt.jsonl").exists(),
              "scope": "NONROOT_WRITABLE_LEDGER_PARENT_NOT_HOSTILE_ROOT"})
        self.assertFalse((outside / "receipt.jsonl").exists(),
                         "P2: parent symlink replacement bypasses O_NOFOLLOW on final basename")


class TransportSecurityTests(unittest.TestCase):
    """Only synthetic private material in memory; never persist keys or payload."""

    @classmethod
    def setUpClass(cls):
        ensure_current_run()
        if os.geteuid() == 0 or transport is None:
            raise RuntimeError("Frozen offline protocol and non-root caller required")
        cls.ca_key = cls.generate_key()
        cls.key = cls.generate_key()
        with transport.memory_file(cls.ca_key) as fd:
            cls.ca = transport.openssl("independent_synthetic_ca", [
                "req", "-new", "-x509", "-key", transport.fd_path(fd), "-days", "30",
                "-subj", "/CN=Independent offline CA",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign"], descriptors=(fd,))
        cls.bundle = cls.make_bundle(cls.key)
        cls.now = int(time.time())

    @classmethod
    def tearDownClass(cls):
        cls.key = cls.ca_key = b""

    @staticmethod
    def generate_key():
        return transport.openssl("independent_synthetic_key", [
            "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"])

    @classmethod
    def make_bundle(cls, key):
        with transport.memory_file(key) as fd:
            csr = transport.openssl("independent_synthetic_csr", [
                "req", "-new", "-key", transport.fd_path(fd),
                "-subj", "/CN=" + transport.DOMAIN,
                "-addext", "subjectAltName=DNS:" + transport.DOMAIN,
                "-addext", "extendedKeyUsage=serverAuth"], descriptors=(fd,))
        with transport.memory_file(csr) as csr_fd, \
                transport.memory_file(cls.ca) as ca_fd, \
                transport.memory_file(cls.ca_key) as ca_key_fd:
            leaf = transport.openssl("independent_synthetic_leaf", [
                "x509", "-req", "-in", transport.fd_path(csr_fd),
                "-CA", transport.fd_path(ca_fd), "-CAkey", transport.fd_path(ca_key_fd),
                "-set_serial", "2", "-days", "14", "-copy_extensions", "copy"],
                descriptors=(csr_fd, ca_fd, ca_key_fd))
        return leaf + cls.ca

    def setUp(self):
        self.case = RUN / self._testMethodName
        self.case.mkdir()
        self.session = transport.RecipientSession(transport.digest(self.bundle))
        self.addCleanup(self.session.close)
        self.request, self.pin, self.recipient_pin = self.session.offer()
        self.replay = transport.ReplayGuard()

    def seal(self, *, now=None):
        with transport.memory_file(self.key) as fd:
            return transport.seal(self.request, fd, self.bundle,
                                  replay=self.replay, request_pin=self.pin,
                                  recipient_pin=self.recipient_pin, now=now)

    def custom_frame(self, oaep_digest="sha256", mgf_digest="sha256", label=None,
                     extra_recipient=False):
        content = transport.canonical({
            "request": transport.encode(self.request),
            "certificate": transport.encode(self.bundle),
            "key": transport.encode(self.key)})
        with transport.memory_file(self.key) as fd:
            signature = transport.openssl("independent_source_signature", [
                "dgst", "-sha256", "-sign", transport.fd_path(fd)],
                data=content, descriptors=(fd,))
        payload = transport.canonical({"content": transport.encode(content),
                                       "signature": transport.encode(signature)})
        recipient = transport.validate_request(self.request)["recipient"]
        with ExitStack() as stack:
            fd = stack.enter_context(transport.memory_file(transport.decode(recipient)))
            options = (["-keyopt", "rsa_oaep_label:" + label] if label else [])
            if extra_recipient:
                other = transport.RecipientSession(transport.digest(self.bundle))
                stack.callback(other.close)
                other_request, _, _ = other.offer()
                other_certificate = transport.decode(
                    transport.validate_request(other_request)["recipient"])
                other_fd = stack.enter_context(transport.memory_file(other_certificate))
                options.extend(["-recip", transport.fd_path(other_fd),
                                "-keyopt", "rsa_padding_mode:oaep",
                                "-keyopt", "rsa_oaep_md:sha256",
                                "-keyopt", "rsa_mgf1_md:sha256"])
            descriptors = (fd, other_fd) if extra_recipient else (fd,)
            encrypted = transport.openssl("independent_custom_oaep", [
                "cms", "-encrypt", "-binary", "-outform", "DER", "-aes-256-gcm",
                "-recip", transport.fd_path(fd), "-keyopt", "rsa_padding_mode:oaep",
                "-keyopt", "rsa_oaep_md:" + oaep_digest,
                "-keyopt", "rsa_mgf1_md:" + mgf_digest, *options],
                data=payload, descriptors=descriptors)
        details = transport.openssl("independent_public_suite", [
            "cms", "-cmsout", "-print", "-inform", "DER"], data=encrypted)
        dump(self.case / ("suite-" + oaep_digest + "-" + mgf_digest + ".json"),
             {"oaep_digest": oaep_digest, "mgf_digest": mgf_digest,
              "nonempty_label": bool(label),
              "recipients": 2 if extra_recipient else 1,
              "public_cms_structure": details.decode("ascii"),
              "ciphertext_bytes": len(encrypted), "plaintext_persisted": False})
        return transport.canonical({"version": 1, "request_pin": self.pin,
                                    "ciphertext": transport.encode(encrypted)})

    def test_transport_valid_real_openssl_roundtrip(self):
        report = self.session.accept(self.seal(), self.ca)
        dump(self.case / "validation.json", report)
        self.assertEqual(report["verdict"], "OFFLINE_PROTOCOL_PASS")
        self.assertFalse(report["production_ready"])
        self.assertEqual(report["private_output"], "NONE")

    def test_transport_independent_fixed_suite_builder_valid_roundtrip(self):
        report = self.session.accept(self.custom_frame(), self.ca)
        dump(self.case / "builder-control.json", report)
        self.assertEqual(report["verdict"], "OFFLINE_PROTOCOL_PASS")
        self.assertFalse(report["production_ready"])

    def assert_suite_refused_before_decrypt(self, frame):
        original = transport.openssl
        attempted = False
        refused = None
        report = None

        def observe(operation, args, **kwargs):
            nonlocal attempted
            if operation == "authenticated_decrypt":
                attempted = True
            return original(operation, args, **kwargs)

        with patch.object(transport, "openssl", side_effect=observe):
            try:
                report = self.session.accept(frame, self.ca)
            except transport.ProtocolError as exc:
                refused = str(exc)
        dump(self.case / "suite-refusal.json",
             {"decryption_attempted": attempted, "refused": refused,
              "validation_report": report, "scope": "SYNTHETIC_KEYS_ONLY"})
        self.assertFalse(attempted, "P2: non-fixed OAEP parameters reach private decryption")
        self.assertIsNotNone(refused)

    def test_transport_oaep_sha512_nonfixed_suite_refused(self):
        self.assert_suite_refused_before_decrypt(self.custom_frame("sha512", "sha512"))

    def test_transport_nonempty_oaep_label_refused(self):
        self.assert_suite_refused_before_decrypt(self.custom_frame(label="01"))

    def test_transport_multiple_real_cms_recipients_refused(self):
        self.assert_suite_refused_before_decrypt(self.custom_frame(extra_recipient=True))

    def test_transport_child_private_diagnostics_do_not_escape(self):
        marker = b"offline-" + b"sensitive-diagnostic"
        fake = subprocess.CompletedProcess([], 23, marker, marker)
        with patch.object(transport.subprocess, "run", return_value=fake):
            with self.assertRaises(transport.CryptoError) as caught:
                transport.openssl("independent_negative_child", ["pkey"])
        self.assertEqual(caught.exception.returncode, 23)
        self.assertNotIn(marker.decode(), str(caught.exception))
        self.assertFalse(hasattr(caught.exception, "private_stderr"))

    def test_transport_oaep_sha1_downgrade_rejected_before_decrypt(self):
        frame = self.custom_frame("sha1", "sha1")
        attempted_decrypt = False
        original = transport.openssl

        def observe(operation, args, **kwargs):
            nonlocal attempted_decrypt
            if operation == "authenticated_decrypt":
                attempted_decrypt = True
            return original(operation, args, **kwargs)

        refused = None
        report = None
        with patch.object(transport, "openssl", side_effect=observe):
            try:
                report = self.session.accept(frame, self.ca)
            except transport.ProtocolError as exc:
                refused = str(exc)
        dump(self.case / "downgrade.json",
             {"refused": refused, "decryption_attempted": attempted_decrypt,
              "validation_report": report, "scope": "SYNTHETIC_KEYS_ONLY"})
        self.assertFalse(attempted_decrypt,
                         "P2: RSA-OAEP SHA1 package reaches decryption despite fixed SHA256 suite")
        self.assertIsNotNone(refused)

    def test_transport_mgf1_sha1_downgrade_rejected_before_decrypt(self):
        frame = self.custom_frame("sha256", "sha1")
        original = transport.openssl
        attempted_decrypt = False

        def observe(operation, args, **kwargs):
            nonlocal attempted_decrypt
            if operation == "authenticated_decrypt":
                attempted_decrypt = True
            return original(operation, args, **kwargs)

        refused = None
        report = None
        with patch.object(transport, "openssl", side_effect=observe):
            try:
                report = self.session.accept(frame, self.ca)
            except transport.ProtocolError as exc:
                refused = str(exc)
        dump(self.case / "downgrade.json",
             {"refused": refused, "decryption_attempted": attempted_decrypt,
              "validation_report": report, "scope": "SYNTHETIC_KEYS_ONLY"})
        self.assertFalse(attempted_decrypt,
                         "P2: MGF1 SHA1 parameters are not checked against SHA256")
        self.assertIsNotNone(refused)

    def test_transport_fixed_request_pin_rejects_recipient_substitution(self):
        other = transport.RecipientSession(transport.digest(self.bundle))
        self.addCleanup(other.close)
        request, _, recipient_pin = other.offer()
        with transport.memory_file(self.key) as fd, \
                patch.object(transport.os, "pread", side_effect=AssertionError(
                    "Source plaintext read before request authentication")):
            with self.assertRaisesRegex(transport.ProtocolError, "TRUSTED_CONTEXT"):
                transport.seal(request, fd, self.bundle, replay=self.replay,
                               request_pin=self.pin, recipient_pin=recipient_pin)

    def test_transport_source_replay_same_lifetime_denied(self):
        self.seal()
        with patch.object(transport.os, "pread", side_effect=AssertionError(
                "Source read on replay")):
            with self.assertRaisesRegex(transport.ProtocolError, "ALREADY_CONSUMED"):
                self.seal()

    def test_transport_source_restart_boundary_is_not_durable_replay_protection(self):
        first = self.seal()
        self.replay = transport.ReplayGuard()
        second = self.seal()
        dump(self.case / "replay-boundary.json",
             {"reissued_after_new_guard": bool(first and second),
              "scope": "DOCUMENTED_PROCESS_LOCAL_NOT_DURABLE",
              "production_authorization_required": True})
        self.assertTrue(first and second)

    def test_transport_expired_offer_cannot_accept_private_response(self):
        frame = self.seal()
        with patch.object(transport.time, "monotonic",
                          return_value=self.session._deadline + 1):
            refused = None
            exported = False
            try:
                self.session.offer()
                exported = True
            except transport.ProtocolError as exc:
                refused = str(exc)
            with self.assertRaisesRegex(transport.ProtocolError, "EXPIRED|CONSUMED"):
                self.session.accept(frame, self.ca)
        dump(self.case / "expired-offer.json",
             {"public_offer_exported": exported, "refused": refused,
              "session_state": self.session.state})
        self.assertIn(self.session.state, ("CONSUMED", "CLOSED"))

    def test_transport_source_expiry_during_crypto_denied(self):
        request = transport.validate_request(self.request)
        with patch.object(transport.time, "time",
                          side_effect=[request["issued"], request["expires"]]):
            with self.assertRaisesRegex(transport.ProtocolError, "EXPIRED"):
                self.seal()

    def test_transport_untrusted_source_pin_is_an_external_approval_boundary(self):
        other = transport.RecipientSession(transport.digest(self.bundle))
        self.addCleanup(other.close)
        request, pin, recipient_pin = other.offer()
        with transport.memory_file(self.key) as fd:
            frame = transport.seal(request, fd, self.bundle, replay=transport.ReplayGuard(),
                                   request_pin=pin, recipient_pin=recipient_pin)
        report = other.accept(frame, self.ca)
        dump(self.case / "trusted-pin-boundary.json",
             {"attacker_chosen_pins_accepted": report["verdict"] == "OFFLINE_PROTOCOL_PASS",
              "scope": "NEW_TRUSTED_APPROVAL_NOT_BYPASS_OF_PINNED_REQUEST",
              "production_ready": False})
        self.assertFalse(report["production_ready"])

    def test_transport_production_cli_always_denied_before_subprocess(self):
        with patch.object(transport.subprocess, "run", side_effect=AssertionError(
                "No production subprocess")), patch.object(sys, "stderr", io.StringIO()):
            for action in ("prepare", "transfer", "apply", "renew"):
                self.assertEqual(transport.main([action]), 1)


def scan_files(out):
    scanner = Path("/data/dev/infra/ops/dsh/scan-secrets-precommit.py")
    spec = importlib.util.spec_from_file_location("security_scan", scanner)
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    paths = []
    for args in (["ls-files", "-z", "--", *WRITESET],
                 ["ls-files", "-z", "--others", "--exclude-standard", "--", *WRITESET]):
        paths.extend(command(["/usr/bin/git", *args], ROOT).stdout.split("\0"))
    paths = sorted({p for p in paths if p})
    if set(paths) != set(WRITESET):
        raise RuntimeError("Explicit commit/scan file set is empty or incomplete")
    patterns = {
        "private_key": ("-----" + "BEGIN", "PRIVATE KEY" + "-----"),
        "saved_assignment": ("SAVED_" + "Tencent_", "SecretKey="),
    }
    rows = []
    for name in paths:
        body = (ROOT / name).read_bytes()
        text = body.decode("utf-8")
        hits = [rule for rule, parts in patterns.items()
                if any(all(part in line for part in parts) for line in text.splitlines())]
        rows.append({"path": name, "bytes": len(body), "sha256": app.sha(body), "hits": hits})
    canary = out / "scan-control.txt"
    canary_text = "offline-" + "scan-canary"
    with canary.open("x") as stream:
        stream.write(canary_text + "\n")
    count, rendered = engine.scan_literal([str(canary)], [canary_text])
    if count != 1 or canary_text in "\n".join(rendered):
        raise RuntimeError("Secret scan positive/redaction control failed")
    literal_hits, _ = engine.scan_literal([str(ROOT / p) for p in paths],
                                          [canary_text])
    sys.path.insert(0, "/data/dev/memory/tools")
    import redact_secrets
    positive = "a" * 64
    if not redact_secrets.sensitive_value(positive) or \
            redact_secrets.sensitive_value("plain explanation only"):
        raise RuntimeError("Memory secret engine positive/negative controls failed")
    content_hits = [
        {"path": name, "line": index, "reason": reason}
        for name in paths
        for index, line in enumerate((ROOT / name).read_text().splitlines(), 1)
        if (reason := redact_secrets.sensitive_value(line))]
    diff = command(["/usr/bin/git", "diff", "--check", "--", *WRITESET], ROOT)
    report = {"files": rows, "positive_control_hits": count, "literal_hits": literal_hits,
              "content_hits": content_hits,
              "actual_secret_reads": 0, "diff_rc": diff.returncode,
              "scope": "EXPLICIT_FILES_SHAPES_AND_SYNTHETIC_LITERAL_NOT_LIVE_SECRET_SCAN"}
    report["verdict"] = "PASS" if all(r["bytes"] and not r["hits"] for r in rows) \
        and literal_hits == 0 and not content_hits else "FAIL"
    dump(out / "scan.json", report)
    return report["verdict"] == "PASS"


def write_summary(result, started, *, initial_tests=0, initial_failures=0,
                  initial_errors=0, initial_skips=0, failure_rc=2, log=None):
    failures = [{"test": test.id(), "traceback": trace}
                for test, trace in result.failures[initial_failures:] +
                result.errors[initial_errors:]]
    passed = not failures and not result.unexpectedSuccesses
    report = {"scope": "REAL_EXTERNAL_FS_FIXED_OFFLINE_SUBPROCESS_SYNTHETIC_CRYPTO",
              "verdict": "PASS" if passed else "FAIL",
              "tests": result.testsRun - initial_tests,
              "failures": len(result.failures) - initial_failures,
              "errors": len(result.errors) - initial_errors,
              "skips": len(result.skipped) - initial_skips,
              "rc": 0 if passed else failure_rc,
              "elapsed_seconds": round(time.monotonic() - started, 3),
              "sources": FROZEN, "transport_sources": TRANSPORT_FROZEN,
              "source_commit": command(["/usr/bin/git", "rev-parse", "HEAD"], ROOT).stdout.strip(),
              "production_mutations": 0, "actual_secret_reads": 0, "details": failures,
              "input_after_tests": input_observations()}
    if report["tests"] < 1 or report["skips"]:
        raise RuntimeError("Security acceptance requires executed tests with no skips")
    dump(RUN / "report.json", report)
    with (RUN / "execution.log").open("x") as stream:
        stream.write(log if log is not None else json.dumps(report, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(f"RELEASE_SECURITY verdict={report['verdict']} tests={report['tests']} "
          f"failures={report['failures']} errors={report['errors']} rc={report['rc']} out={RUN}")
    for row in failures:
        print("FAILED " + row["test"])
    return report["rc"]


class DiscoveryEvidenceSuite(unittest.TestSuite):
    """Use the real discovery runner result, including setUpClass failures."""

    def run(self, result, debug=False):
        started = time.monotonic()
        initial = (result.testsRun, len(result.failures), len(result.errors), len(result.skipped))
        super().run(result, debug)
        if RUN is None:
            raise RuntimeError("Discovery did not execute current-source security setup")
        write_summary(result, started, initial_tests=initial[0], initial_failures=initial[1],
                      initial_errors=initial[2], initial_skips=initial[3], failure_rc=1)
        return result


def load_tests(loader, standard_tests, pattern):
    return DiscoveryEvidenceSuite([
        loader.loadTestsFromTestCase(SecurityTests),
        loader.loadTestsFromTestCase(TransportSecurityTests)])


def main():
    global RUN, APP_SOURCE_ROOT, TRANSPORT_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--scan-only", action="store_true")
    parser.add_argument("--baseline", action="store_true",
                        help="Explicit original app/DNS/transport commits; old red controls")
    parser.add_argument("--app-revision")
    parser.add_argument("--dns-revision")
    parser.add_argument("--app-root", type=Path)
    parser.add_argument("--dns-source", type=Path, default=DNS_SOURCE)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--transport-only", action="store_true")
    parser.add_argument("--all", action="store_true", help="Run both security classes")
    parser.add_argument("--transport-root", type=Path, default=CURRENT_ROOT)
    parser.add_argument("--transport-revision")
    args = parser.parse_args()
    if args.all and (args.transport_only or args.only):
        parser.error("--all cannot be combined with --transport-only/--only")
    if os.geteuid() == 0:
        raise RuntimeError("Security tests must run as infra, never root")
    app_revision = args.app_revision or ("b0ec932" if args.baseline else "WORKTREE")
    dns_revision = args.dns_revision or ("3b2ecbe" if args.baseline else "WORKTREE")
    transport_revision = args.transport_revision or ("466463b8" if args.baseline else "WORKTREE")
    app_root = args.app_root or (ROOT if args.baseline else CURRENT_ROOT)
    out = args.out.absolute()
    if EXTERNAL.resolve() not in out.resolve().parents or out.is_symlink():
        raise RuntimeError("Fresh output below seat-security required")
    out.mkdir(parents=True, exist_ok=False)
    if args.scan_only:
        passed = scan_files(out)
        print(f"SECURITY_SCAN verdict={'PASS' if passed else 'FAIL'} out={out}")
        return 0 if passed else 2
    RUN = out.resolve()
    APP_SOURCE_ROOT = app_root.resolve(strict=True)
    TRANSPORT_ROOT = args.transport_root.resolve(strict=True)
    if args.transport_only:
        freeze_transport(RUN, transport_revision)
    else:
        freeze_sources(RUN, app_revision, dns_revision, APP_SOURCE_ROOT,
                       args.dns_source.resolve(strict=True))
        if args.all:
            freeze_transport(RUN, transport_revision)
    verify_frozen_inputs()
    started = time.monotonic()
    stream = io.StringIO()
    loader = unittest.defaultTestLoader
    case_class = TransportSecurityTests if args.transport_only else SecurityTests
    if args.all:
        suite = unittest.TestSuite([loader.loadTestsFromTestCase(SecurityTests),
                                    loader.loadTestsFromTestCase(TransportSecurityTests)])
    else:
        suite = (unittest.TestSuite(case_class(name) for name in args.only) if args.only else
                 loader.loadTestsFromTestCase(case_class))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    return write_summary(result, started, log=stream.getvalue())


if __name__ == "__main__":
    if sys.argv[1:2] == ["--offline-dns"]:
        frozen_path = Path(os.environ["SECURITY_DNS_SOURCE"])
        if EXTERNAL.resolve() not in frozen_path.resolve().parents:
            raise RuntimeError("Offline subprocess requires frozen external source")
        dns = load_source("offline_dns", frozen_path)
        with patch.object(socket, "socket", side_effect=AssertionError("Network forbidden")):
            raise SystemExit(offline_signer())
    raise SystemExit(main())
