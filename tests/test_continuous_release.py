"""Non-root real-child/file controls, never production or human certification.

Use --original to reproduce against the fixed read-only S3 draft. Default
tests consume only the sibling candidate. Fixture admission is explicitly
not a successful release_gate observation; its public data comes from the
actual loopback ApplicationServer and public_gate.
"""

from __future__ import annotations

import argparse
import ast
import copy
from datetime import datetime, timezone
import errno
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from uuid import uuid4

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
from test_release_gate import run_suite
import release_gate as gate

ORIGINAL = Path("/data/dev/opensim-s3-continuous-release-20261011/tools/continuous_release.py")
ORIGINAL_SHA = "131f4b167562ce1acffbb51ef0547ef3415a78d9b264ef07ef5d210714116184"
TEST_ROOT = gate.ARTIFACT_ROOT / "authorized-s5r-consumer-main-integration-20261011"
TARGET = ROOT / "tools/continuous_release.py"
FINITE_SELECTORS = ("test_finite_collector_main_abi_and_real_cli",
                    "test_finite_collector_offline_failure_and_late_veto")
MAIN_PINS = {
    "tools/render_page.py": "054e2cf5c92c196dabe3e144a013caf763e80549b36bb91db9e9d61556ee63aa",
    "web/app.js": "1caba19259f38921fb280f737fd9142660f9198de55b9b3768dc530272214ffc",
    "web/app.css": "fed209cf9fcca81ae68b4766beb4f0550b7fa3f94e805fe862b2d60754afce14",
    "solver.py": "71e8933f1f883f1f2f4d15a986e0230df2a9372e4efccf136feac5dc2b3e351a",
    "server.py": "668380b300e7f34bab596fa6da52bf2a4d7a6817490517b2748c6b0c86e7b036",
    "tools/sync_release.py": "c1b483654bee51984ebf00b79faabc4f014e75843299f3b06a8eaf94aeecbd14",
    "tools/release_gate.py": "55dc52edc95115e5077add86dc4d48446d48567054e4a8bab7ad69e587653ae3",
}
FINITE_MODES = ("normal", "missing-source", "pin", "commit", "rc", "late-inode")
SCENARIOS = (
    "control", "timestamp", "stdout", "fsync", "public-metrics",
    "late-public", "late-terminal", "late-admission", "late-intent",
    "late-receipt", "late-public-inode", "late-unknown", "producer-stderr",
    "producer-rc", "stop-failure", "revert-failure", "missing-executor",
    "request-drift", "build-drift", "late-closure", "during-output", "fsync-veto-failure",
    "fsync-veto-stop-failure",
    "owner-output-same", "owner-output-different", "fsync-unlink-veto-stop-failure",
    "pending-release-failure",
    "late-raw-report", "late-raw-log", "late-raw-inode", "late-raw-sidecar",
)


def source_snapshot(path: Path) -> dict:
    info = path.lstat()
    return {
        "sha256": gate.sha(gate.read_file(path)), "device": info.st_dev,
        "inode": info.st_ino, "mode": info.st_mode, "uid": info.st_uid,
        "gid": info.st_gid, "links": info.st_nlink, "bytes": info.st_size,
        "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
    }


def fixture_directory(path: Path) -> None:
    path.mkdir(mode=0o700)
    # 3FS inherits setgid; only this newly created, fixture-owned directory changes.
    os.chmod(path, 0o700, follow_symlinks=False)


def capture(path: Path, body: bytes) -> None:
    gate.external(path, TEST_ROOT)
    with path.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != body:
        raise AssertionError("CAPTURE_READBACK_FAILED")


def observe_source(path: Path) -> dict:
    snapshot = source_snapshot(path)
    return {"path": str(path), "sha256": snapshot["sha256"], "metadata": [
        snapshot[key] for key in ("device", "inode", "mode", "uid", "gid", "links",
                                 "bytes", "mtime_ns", "ctime_ns")]}


def load_target(original: bool):
    path = ORIGINAL if original else TARGET
    body = gate.read_file(path)
    if original:
        gate.require(gate.sha(body) == ORIGINAL_SHA, "ORIGINAL_DRAFT_SHA_DRIFT")
    spec = importlib.util.spec_from_file_location("_continuous_fixture_" + uuid4().hex, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(body, str(path), "exec"), module.__dict__)
    gate.require(Path(module.__file__) == path and os.geteuid() != 0,
                 "FIXTURE_MUST_LOAD_REAL_NONROOT_TARGET")
    return module


def deny_fixture_network(event, args):
    if event.startswith("socket."):
        raise AssertionError("OFFLINE_FIXTURE_NETWORK_FORBIDDEN")


def finite_producer(directory: Path, mode: str) -> int:
    """Original CLI writer with file-only protocol data, not a FEM/public test."""
    sys.addaudithook(deny_fixture_network)
    from tools import public_gate

    sources = {name: gate.sha(gate.read_file(ROOT / name)) for name in gate.SOURCES}
    rows = [{"name": name, "verdict": "PASS"} for name in
            ("runtime_identity", "page_and_assets", "real_fem_circle", "real_fem_rectangle")]
    for row in rows[2:]:
        row.update(config_sha256="a" * 64, solver_sha256=sources["solver.py"],
                   node_count=11, flow_rate_m3_s=1.0, relative_residual=0.0,
                   computed_at=datetime.now(timezone.utc).isoformat())
    report = {"schema": 1, "verdict": "PASS", "scope": "LOCAL_TEST",
              "fixture_scope": "FILE_ONLY_PROTOCOL_NOT_FEM_NOT_PUBLIC",
              "url": "http://127.0.0.1:1", "source_sha256": sources,
              "page_build": "FILE_ONLY_FIXTURE", "checks": rows,
              "collected_at": datetime.now(timezone.utc).isoformat()}
    public_gate.check_endpoint = lambda: report
    sys.argv = [str(ROOT / "tools/public_gate.py"), "--out-dir", str(directory)]
    rc = public_gate.main()
    return 7 if mode == "rc" else rc


class FiniteRunner:
    """Only map the fixed producer argv to an offline original-writer child."""

    def __init__(self, mode):
        self.mode, self.calls = mode, []

    def launch(self, argv):
        gate.require(argv[:5] == ["/usr/bin/python3", "-I", "-B",
                                 str(ROOT / "tools/public_gate.py"), "--out-dir"] and
                     len(argv) == 6, "FINITE_FIXED_PRODUCER_ARGV")
        actual = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                  "--finite-producer", self.mode, "--public-cli-out", argv[-1]]
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                       "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
        self.calls.append({"fixed": argv, "actual": actual, "environment": environment})
        return subprocess.Popen(actual, cwd=ROOT, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=environment)


def finite_child(out: Path, mode: str) -> int:
    sys.addaudithook(deny_fixture_network)
    gate.external(out, TEST_ROOT)
    fixture_directory(out)
    module = load_target(False)
    gate.require(module.gate is gate and Path(module.sync.__file__) == ROOT / "tools/sync_release.py"
                 and module.APPROVED_EXECUTOR is None, "MAIN_SIBLING_BINDING_REQUIRED")
    gate.require(not os.path.lexists(ROOT / "deploy/opensim-sync.timer"), "MAIN_TIMER_MUST_REMAIN_ABSENT")
    before = {name: source_snapshot(ROOT / name) for name in MAIN_PINS}
    gate.require({name: value["sha256"] for name, value in before.items()} == MAIN_PINS,
                 "APPROVED_MAIN_BYTES_CHANGED_NO_REPIN")
    pins = module.source_set(ROOT)
    head = subprocess.run(["git", "--no-optional-locks", "rev-parse", "HEAD"], cwd=ROOT,
                          stdin=subprocess.DEVNULL, capture_output=True, check=True).stdout.decode().strip()
    root = ROOT
    if mode == "missing-source":
        root = (out / "missing-sources").resolve()
        fixture_directory(root)
    runner = FiniteRunner(mode)
    argv = ["--collect-public", "--source-root", str(root), "--out", str(out / "collection"),
            "--expect-source-set-sha256", "0" * 64 if mode == "pin" else module.source_set_sha256(pins),
            "--expect-commit", "0" * 40 if mode == "commit" else head]
    flags, real_stdout = [], sys.stdout

    class Output:
        def write(self, text):
            return real_stdout.write(text)

        def flush(self):
            real_stdout.flush()
            if mode == "late-inode" and not flags:
                physical = module.collector_libraries()[1].output_path(out / "collection")
                path = next((physical / "producer").glob("*.json"))
                replacement = out / "owned-replacement.json"
                capture(replacement, path.read_bytes())
                os.replace(replacement, path)
                flags.append("SAME_BYTES_NEW_INODE_AFTER_PROVISIONAL_STDOUT")

    sys.stdout = Output()
    try:
        rc = module.main(argv, runner=runner)
    finally:
        sys.stdout = real_stdout
    after = {name: source_snapshot(ROOT / name) for name in MAIN_PINS}
    physical = module.collector_libraries()[1].output_path(out / "collection")
    result = gate.decode(gate.read_file(physical / "collection.json"))
    late = physical / "report.failure.json"
    capture(out / "fixture.json", gate.encode({
        "actual_rc": rc, "argv": [str(TARGET), *argv], "calls": runner.calls,
        "uid": os.geteuid(), "scope": "FILE_ONLY_PROTOCOL_NOT_FEM_NOT_PUBLIC",
        "source_before": before, "source_after": after, "source_stable": before == after,
        "timer_absent": not os.path.lexists(ROOT / "deploy/opensim-sync.timer"),
        "executor_absent": module.APPROVED_EXECUTOR is None, "flags": flags,
        "collection": result, "late_failure": gate.decode(gate.read_file(late)) if late.exists() else None,
        "production_operations": 0, "network_operations": 0}))
    return rc


def public_child(origin: str, mode: str, directory: Path | None = None) -> int:
    from tools import public_gate

    if directory is not None:
        original = public_gate.check_endpoint
        # Only transport is loopback; execute the original CLI/report/log writer.
        public_gate.check_endpoint = lambda: original(origin, public=False)
        sys.argv = [str(ROOT / "tools/public_gate.py"), "--out-dir", str(directory)]
        rc = public_gate.main()
        if mode == "stderr":
            sys.stderr.write("FIXTURE_POST_STDOUT_DIAGNOSTIC\n")
            sys.stderr.flush()
        return 7 if mode == "rc" else rc

    before = {str(ROOT / name): source_snapshot(ROOT / name)
              for name in (*gate.SOURCES, "tools/public_gate.py")}
    value = public_gate.check_endpoint(origin, public=False)
    after = {str(ROOT / name): source_snapshot(ROOT / name) for name in
             (*gate.SOURCES, "tools/public_gate.py")}
    sys.stdout.buffer.write(gate.encode(value))
    sys.stdout.buffer.flush()
    if mode == "stderr":
        sys.stderr.write("FIXTURE_POST_STDOUT_DIAGNOSTIC\n")
        sys.stderr.flush()
    if mode == "rc":
        return 7
    return 0 if value["verdict"] == "PASS" and before == after else 1


class PublicRunner:
    """Map only the fixed original CLI to its real non-root loopback transport."""

    def __init__(self, origin, mode="normal"):
        self.origin, self.mode = origin, mode
        self.calls = []

    def launch(self, argv):
        gate.require(argv[:5] == ["/usr/bin/python3", "-I", "-B",
                                 str(ROOT / "tools/public_gate.py"), "--out-dir"] and
                     len(argv) == 6, "FIXTURE_FIXED_PUBLIC_ARGV")
        actual = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                  "--public-fixture", self.origin, "--public-mode", self.mode,
                  "--public-cli-out", argv[-1]]
        self.calls.append({"fixed": argv, "actual": actual})
        return subprocess.Popen(actual, cwd=ROOT, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                                     "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"})


def loopback_collection(module, out, mode="normal"):
    from server import ApplicationServer

    server = ApplicationServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        runner = PublicRunner(f"http://127.0.0.1:{server.server_port}", mode)
        pins = module.source_set(ROOT)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, check=True).stdout.decode("ascii").strip()
        value = module.collect_public(ROOT, out, module.source_set_sha256(pins), head,
                                      runner=runner)
        gate.write_new(out.with_suffix(".mapping.json"), gate.encode(runner.calls))
        return value
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def collector_child(out, mode):
    from server import ApplicationServer

    gate.external(out, TEST_ROOT)
    gate.OUTPUT_ROOT = TEST_ROOT
    module = load_target(False)
    fixture_directory(out)
    server = ApplicationServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_loader, native_read = module.collector_libraries, os.read
    real_stdout = sys.stdout
    loaded = original_loader()
    original_write = loaded[1].write_new
    native_capture = loaded[0].capture
    flags = []

    def storage_write(path, body):
        if mode == "fsync" and path.name == "collection.json":
            original_write(path, body)
            flags.append("ACTUAL_COLLECTION_BYTES_WRITTEN_BEFORE_FSYNC_FAILURE")
            raise OSError(errno.ENOSPC, "FIXTURE_POST_WRITE_FSYNC")
        return original_write(path, body)

    loaded[1].write_new = storage_write
    module.collector_libraries = lambda: loaded

    class Output:
        def write(self, text):
            if mode == "stdout":
                with open("/dev/full", "w", encoding="ascii", buffering=1) as full:
                    full.write(text)
                    full.flush()
            return real_stdout.write(text)

        def flush(self):
            real_stdout.flush()
            if mode == "late-inode":
                path = next((out / "collection" / "producer").glob("*.json"))
                replacement = out / "replacement-owned"
                replacement.write_bytes(path.read_bytes())
                os.replace(replacement, path)
                flags.append("ACTUAL_SAME_BYTES_NEW_INODE_AFTER_OUTPUT")

    if mode in ("stdout", "late-inode"):
        sys.stdout = Output()
    if mode == "stream":
        def fail_read(fd, size):
            if not flags:
                flags.append("ACTUAL_PIPE_READ_FAILED")
                raise OSError(errno.EIO, "FIXTURE_PIPE_READ")
            return native_read(fd, size)
        def failed_capture(child):
            module.os.read = fail_read
            try:
                return native_capture(child)
            finally:
                module.os.read = native_read
        loaded[0].capture = failed_capture
    runner = PublicRunner(f"http://127.0.0.1:{server.server_port}",
                          "stderr" if mode == "stderr" else "rc" if mode == "rc" else "normal")
    pins = module.source_set(ROOT)
    digest = module.source_set_sha256(pins)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                          capture_output=True, check=True).stdout.decode("ascii").strip()
    try:
        rc = module.main(["--collect-public", "--source-root", str(ROOT), "--out",
                          str(out / "collection"), "--expect-source-set-sha256",
                          "0" * 64 if mode == "pin" else digest, "--expect-commit", head],
                         runner=runner)
    finally:
        module.collector_libraries = original_loader
        loaded[1].write_new = original_write
        loaded[0].capture = native_capture
        module.os.read = native_read
        sys.stdout = real_stdout
        server.shutdown()
        thread.join()
        server.server_close()
    gate.write_new(out / "fixture.json", gate.encode({"actual_rc": rc, "flags": flags,
        "calls": runner.calls, "uid": os.geteuid(), "production_operations": 0}))
    return rc


def loopback_report(module, out: Path, scenario: str) -> dict:
    if hasattr(module, "collect_public"):
        mode = {"producer-stderr": "stderr", "producer-rc": "rc"}.get(scenario, "normal")
        value = loopback_collection(module, out / "public-cli", mode)
        observation = value["observation"]
        gate.require(observation is not None and observation["report"]["scope"] == "LOCAL_TEST"
                     and len(observation["report"]["checks"]) == 4 and
                     observation["actual_rc"] == (7 if mode == "rc" else 0),
                     "REAL_ORIGINAL_CLI_FIXTURE_NOT_VALID")
        return observation
    from server import ApplicationServer

    server = ApplicationServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        pins = [observe_source(ROOT / name)
                for name in (*gate.SOURCES, "tools/public_gate.py")]
        mode = {"producer-stderr": "stderr", "producer-rc": "rc"}.get(scenario, "normal")
        child = subprocess.run(
            [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
             "--public-fixture", f"http://127.0.0.1:{server.server_port}", "--public-mode", mode],
            cwd=ROOT, capture_output=True)
        capture(out / "public-child.stdout", child.stdout)
        capture(out / "public-child.stderr", child.stderr)
        report = json.loads(child.stdout)
        gate.require(report["verdict"] == "PASS" and report["scope"] == "LOCAL_TEST"
                     and len(report["checks"]) == 4
                     and all(row["node_count"] > 10 for row in report["checks"][-2:])
                     and child.returncode == (7 if mode == "rc" else 0)
                     and bool(child.stderr) == (mode == "stderr"),
                     "REAL_LOOPBACK_FIXTURE_NOT_VALID")
        return {"report": report, "actual_rc": child.returncode,
                "stderr_bytes": len(child.stderr), "source_before": pins,
                "source_after": [observe_source(ROOT / name)
                                 for name in (*gate.SOURCES, "tools/public_gate.py")],
                "producer": str(ROOT / "tools/public_gate.py"),
                "stdout_sha256": gate.sha(child.stdout)}
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


class FixtureExecutor:
    """Only creates local receipt files; no root, network or service operation."""

    def __init__(self, module, scenario):
        self.module = module
        self.calls = []
        self.scenario = scenario

    def action(self, name, context, run):
        self.calls.append(name)
        if (name == "stop" and self.scenario in (
                "stop-failure", "fsync-veto-stop-failure", "fsync-unlink-veto-stop-failure")) or (
                name == "revert" and self.scenario == "revert-failure"):
            raise self.module.Refused("FIXTURE_" + name.upper() + "_FAILED")
        receipt = {
            "schema": "opensim-continuous-executor-v1", "operation": name,
            **{key: context[key] for key in
               ("generation_id", "request_sha256", "commit", "manifest_sha256")},
            "actual_rc": 0,
        }
        path = run / (name + "-fixture-receipt.json")
        self.module.create(path, receipt)
        return {"actual_rc": 0, "receipt": {"path": str(path), "sha256": gate.sha(gate.encode(receipt))}}

    def deploy(self, context, run):
        return self.action("deploy", context, run)

    def stop(self, context, run):
        return self.action("stop", context, run)

    def revert(self, context, run):
        return self.action("revert", context, run)


def reusable(store) -> tuple[bool, str | None]:
    try:
        with store.locked():
            store.current()
        return True, None
    except Exception as exc:
        return False, str(exc)


def closure_child(out: Path, mode: str) -> int:
    """Run real frozen S4 fixture/CLI, never fake trusted release success."""
    gate.external(out, TEST_ROOT)
    fixture_directory(out)
    module = load_target(False)
    before = source_snapshot(TARGET)
    with module.closure_api() as (owner, owner_sources):
        location = module.CLOSURE_ROOT.parent / "tests/test_sync_release.py"
        expected = "7017b6ccfedca0e6a92b8f0e5d26837749c93385c3a292ae036f89daa1688302"
        body = gate.read_file(location)
        gate.require(gate.sha(body) == expected, "FROZEN_S4_FIXTURE_SHA")
        # Import only original file fixtures. Their tools/ROOT are S4 bytes,
        # not the local v1 gate loaded by the consumer suite.
        names = ("sync_release", "test_release_gate", "test_release_closure")
        previous = {name: sys.modules.get(name) for name in names}
        saved_path = list(sys.path)
        sys.modules["sync_release"] = owner
        sys.path.insert(0, str(location.parent))
        for name in names[1:]:
            sys.modules.pop(name, None)
        name = "_closure_consumer_fixture_" + uuid4().hex
        spec = importlib.util.spec_from_file_location(name, location)
        support_module = importlib.util.module_from_spec(spec)
        sys.modules[name] = support_module
        try:
            exec(compile(body, str(location), "exec"), support_module.__dict__)
            support_module.SyncClosureTests.setUpClass()
            f = support_module.SyncClosureTests()
            f.setUp()
            if mode != "complete":
                f.f.ledger["manual"].pop("SCREEN-READER-DESKTOP")
                f.f.save()
                f.closure["original_ledger"]["sha256"] = gate.sha(f.f.ledger_path.read_bytes())
                f.save()
            if mode == "wrong-pin":
                f.desired["closure_request"]["sha256"] = "0" * 64
                support_module.put(f.desired_path, gate.encode(f.desired))
            if mode == "fallback":
                f.desired["gate_receipt"] = f.closure["original_ledger"]
                support_module.put(f.desired_path, gate.encode(f.desired))
            inspect_out = module.EVIDENCE_ROOT / ("s5-closure-inspect-" + uuid4().hex)
            argv = [sys.executable, "-I", "-B", str(TARGET), "--inspect-candidate",
                    "--source-root", str(f.f.repo), "--desired", str(f.desired_path),
                    "--desired-sha256", gate.sha(f.desired_path.read_bytes()),
                    "--out", str(inspect_out)]
            if mode in ("stdout", "fsync", "late-output-inode", "late-output-sidecar"):
                argv = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                        "--inspect-boundary", mode, *argv[4:]]
            child = subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL,
                                   capture_output=True,
                                   env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
            capture(out / "stdout.bin", child.stdout)
            capture(out / "stderr.bin", child.stderr)
            capture(out / "inner-launcher.json", gate.encode({"argv": argv,
                "actual_rc": child.returncode, "stdout_bytes": len(child.stdout),
                "stderr_bytes": len(child.stderr), "production_operations": 0}))
            gate.require((inspect_out / "report.json").is_file(),
                         "INNER_CONSUMER_REPORT_MISSING_RC_" + str(child.returncode))
            report = gate.decode(gate.read_file(inspect_out / "report.json"))
            execution = report.get("closure_execution")
            original_execution = execution.get("owner", {}).get("closure_execution") if execution else None
            drift = None
            if mode == "late-inode" and original_execution:
                path = Path(original_execution["observation"]["report"]["path"])
                replacement = out / "replacement-owned.json"
                capture(replacement, path.read_bytes())
                os.replace(replacement, path)
                try:
                    for pin in module.closure_dependencies(execution):
                        module.verify_dependency(pin)
                except Exception as exc:
                    drift = module.failure(exc)
            capture(out / "result.json", gate.encode({
                "actual_child_rc": child.returncode, "argv": argv, "uid": os.geteuid(),
                "report": report, "drift": drift, "source_stable": before == source_snapshot(TARGET),
                "failure_report": gate.decode((inspect_out / "report.failure.json").read_bytes())
                    if (inspect_out / "report.failure.json").exists() else None,
                "owner_sources": owner_sources, "production_operations": 0,
                "original_audit_status": f.f.reports["audit"]["gates"]["G3-accessibility"]["status"]}))
            return child.returncode
        finally:
            sys.path[:] = saved_path
            for key, value in previous.items():
                if value is None:
                    sys.modules.pop(key, None)
                else:
                    sys.modules[key] = value
            sys.modules.pop(name, None)


def inspect_boundary(args):
    module = load_target(False)
    gate.external(args.out, module.EVIDENCE_ROOT)
    native_create, native_stdout = module.create, sys.stdout
    triggered = []

    def create(path, value):
        result = native_create(path, value)
        if args.inspect_boundary == "fsync" and path == args.out / "report.json":
            triggered.append("ACTUAL_REPORT_BYTES_WRITTEN_BEFORE_FSYNC_FAILURE")
            raise OSError(errno.ENOSPC, "FIXTURE_POST_REPORT_FSYNC")
        return result

    class Output:
        def write(self, text):
            if args.inspect_boundary == "stdout":
                with open("/dev/full", "w", encoding="ascii", buffering=1) as full:
                    full.write(text)
                    full.flush()
            return native_stdout.write(text)

        def flush(self):
            native_stdout.flush()
            if args.inspect_boundary.startswith("late-output") and not triggered:
                saved = gate.decode((args.out / "report.json").read_bytes())
                execution = saved["closure_execution"]["owner"]["closure_execution"]
                if args.inspect_boundary == "late-output-inode":
                    path = Path(execution["observation"]["report"]["path"])
                    replacement = args.out / "fixture-replacement-owned"
                    replacement.write_bytes(path.read_bytes())
                    os.replace(replacement, path)
                    triggered.append("ACTUAL_SAME_BYTES_NEW_GATE_INODE_AFTER_OUTPUT")
                else:
                    pins = saved["closure_execution"]["inputs"]
                    path = Path(next(pin["path"] for pin in pins
                                     if pin["path"].endswith("baseline.json")))
                    (path.parent / "finalization-failure.json").write_bytes(b"UNKNOWN_PRESERVE\n")
                    triggered.append("ACTUAL_MEASURED_SIDECAR_AFTER_OUTPUT")

    module.create, sys.stdout = create, Output()
    try:
        rc = module.main(["--inspect-candidate", "--source-root", str(args.source_root),
                          "--desired", str(args.desired), "--desired-sha256", args.desired_sha256,
                          "--out", str(args.out)])
    finally:
        module.create, sys.stdout = native_create, native_stdout
    native_create(args.out / "boundary-fixture.json", {"actual_rc": rc,
        "triggered": triggered, "uid": os.geteuid(), "production_operations": 0})
    return rc


def fixture_child(out: Path, original: bool, scenario: str) -> int:
    gate.external(out, TEST_ROOT)
    gate.OUTPUT_ROOT = TEST_ROOT
    fixture_directory(out)
    module = load_target(original)
    before = source_snapshot(Path(module.__file__))
    public_observation = loopback_report(module, out, scenario)
    public = public_observation["report"]
    root = out / "state"
    fixture_directory(root)
    store = module.Store(root)
    with store.locked(initialize=True):
        store.current()
    executor = FixtureExecutor(module, scenario)
    context = {
        "request_sha256": "a" * 64, "commit": "b" * 40,
        "manifest_sha256": "c" * 64,
        "identity": {"source_sha256": public["source_sha256"], "page_build": public["page_build"],
                     "tool_sha256": {"tools/public_gate.py":
                                    source_snapshot(ROOT / "tools/public_gate.py")["sha256"]}},
        "plan": {"schema": "opensim-sync-result-v1", "state": "FIXED_RELEASE_PLAN",
                 "scope": "VALIDATED_PLAN_NOT_DEPLOYED",
                 "request_id": "FIXTURE-NOT-GATE-ADMISSION", "request_sha256": "a" * 64,
                 "commit": "b" * 40, "manifest_sha256": "c" * 64,
                 "release": "/fixture/not-installed", "page_build": public["page_build"],
                 "source_epoch": 1, "gate_receipt": {"path": "/fixture/not-gate", "sha256": "d" * 64},
                 "production_changes": False, "controller": "NOT_AUTHORIZED",
                 "collected_at": "2026-10-11T00:00:00+00:00"},
    }
    admissions = []

    def admission():
        value = copy.deepcopy(context)
        if scenario == "timestamp":
            # This field is regenerated by the real sync.plan return contract.
            value["plan"]["collected_at"] = datetime.now(timezone.utc).isoformat()
        if scenario == "request-drift" and admissions:
            value["request_sha256"] = "e" * 64
        admissions.append(value)
        return value

    def public_check(context, run):
        value = copy.deepcopy(public_observation)
        if scenario in ("public-metrics", "stop-failure", "revert-failure"):
            for row in value["report"]["checks"][-2:]:
                row.clear()
            value["report"]["checks"][-2:] = [
                {"name": "real_fem_circle", "verdict": "PASS"},
                {"name": "real_fem_rectangle", "verdict": "PASS"},
            ]
            value["stdout_sha256"] = gate.sha(gate.encode(value["report"]))
        if scenario == "build-drift":
            value["report"]["page_build"] = "0" * 12
            value["stdout_sha256"] = gate.sha(gate.encode(value["report"]))
        return value

    native_fsync = module.os.fsync
    root_syncs = []

    def injected_fsync(fd):
        info = os.fstat(fd)
        target = root.stat()
        if (info.st_dev, info.st_ino) == (target.st_dev, target.st_ino):
            root_syncs.append(len(root_syncs) + 1)
            owner = gate.decode(gate.read_file(root / "owner.json"))
            if owner["report_sha256"] is not None:
                raise OSError(errno.ENOSPC, "FIXTURE_FINAL_OWNER_FSYNC")
        return native_fsync(fd)

    if scenario in ("fsync", "fsync-veto-failure", "fsync-veto-stop-failure",
                    "fsync-unlink-veto-stop-failure"):
        module.os.fsync = injected_fsync
    original_create = module.create

    def veto_write_failure(path, value):
        if path.name == "finalization-veto.json":
            raise OSError(errno.ENOSPC, "FIXTURE_VETO_WRITE_FAILED")
        return original_create(path, value)

    if scenario in ("fsync-veto-failure", "fsync-veto-stop-failure",
                    "fsync-unlink-veto-stop-failure"):
        module.create = veto_write_failure
    native_unlink = module.os.unlink
    unlink_failures = []

    def injected_unlink(path, *args, **kwargs):
        if Path(path) == store.owner and scenario == "fsync-unlink-veto-stop-failure":
            unlink_failures.append("FIXTURE_OWNER_UNLINK_FAILED")
            raise OSError(errno.EACCES, "FIXTURE_OWNER_UNLINK_FAILED")
        if Path(path).name == "admission-pending.json" and scenario == "pending-release-failure":
            unlink_failures.append("FIXTURE_PENDING_UNLINK_FAILED")
            raise OSError(errno.EACCES, "FIXTURE_PENDING_UNLINK_FAILED")
        return native_unlink(path, *args, **kwargs)

    if scenario in ("fsync-unlink-veto-stop-failure", "pending-release-failure"):
        module.os.unlink = injected_unlink
    real_stdout = sys.stdout
    foreign_owner = None
    foreign_identity = None

    class DriftOnFlush:
        def write(self, text):
            return real_stdout.write(text)

        def flush(self):
            real_stdout.flush()
            for path in (root / "runs").iterdir():
                target = path / "public.json"
                if target.exists():
                    target.write_bytes(target.read_bytes() + b"\n")

    class OwnerDriftOnFlush:
        def write(self, text):
            return real_stdout.write(text)

        def flush(self):
            nonlocal foreign_owner, foreign_identity
            real_stdout.flush()
            if foreign_owner is None:
                foreign_owner = (store.owner.read_bytes() if scenario == "owner-output-same"
                                 else b"FOREIGN_OWNER_NOT_JSON\n")
                replacement = out / "foreign-owner-replacement"
                with replacement.open("xb") as stream:
                    stream.write(foreign_owner)
                os.chmod(replacement, 0o600)
                info = replacement.stat()
                foreign_identity = (info.st_dev, info.st_ino)
                os.replace(replacement, store.owner)

    if scenario == "during-output":
        sys.stdout = DriftOnFlush()
    if scenario in ("owner-output-same", "owner-output-different"):
        sys.stdout = OwnerDriftOnFlush()
    rc = 4
    failure = None
    report = None
    chosen_executor = None if scenario == "missing-executor" else executor
    try:
        if scenario == "stdout":
            previous = sys.stdout
            with open("/dev/full", "w", encoding="ascii", buffering=1) as stream:
                sys.stdout = stream
                try:
                    report = module.run_once(store, admission, chosen_executor, public_check,
                                             lambda value: "FIXTURE_ALERT_RECORDED", fixture=True)
                    rc = report["exit_code"]
                finally:
                    sys.stdout = previous
                    # Close of a failed buffered /dev/full stream can fail again.
                    try:
                        stream.flush()
                    except OSError:
                        pass
        else:
            report = module.run_once(store, admission, chosen_executor, public_check,
                                     lambda value: "FIXTURE_ALERT_RECORDED", fixture=True)
            rc = report["exit_code"]
    except Exception as exc:
        failure = module.failure(exc) if hasattr(module, "failure") else {
            "type": type(exc).__name__, "errno": getattr(exc, "errno", None)}
        rc = 4
    finally:
        module.os.fsync = native_fsync
        module.os.unlink = native_unlink
        module.create = original_create
        sys.stdout = real_stdout
    if scenario.startswith("late-"):
        gate.require(report is not None and report["state"] == "FIXTURE_SYNCHRONIZED",
                     "LATE_DRIFT_REQUIRES_ACTUAL_SUCCESS_CONTROL")
        run = root / "runs" / report["generation_id"]
        files = {
            "late-public": run / "public.json", "late-terminal": run / "terminal.json",
            "late-admission": run / "admission.json", "late-intent": run / "intent.json",
            "late-receipt": Path(report["operations"]["deploy"]["receipt"]["path"]),
            "late-public-inode": run / "public.json",
            "late-unknown": run / "foreign-asset.json",
            "late-closure": run / "closure.json",
            "late-raw-report": Path(public_observation["artifacts"]["report"]["path"]),
            "late-raw-log": Path(public_observation["artifacts"]["log"]["path"]),
            "late-raw-inode": Path(public_observation["artifacts"]["report"]["path"]),
            "late-raw-sidecar": Path(public_observation["directory"]["path"]) / "failure.json",
        }
        path = files[scenario]
        if scenario in ("late-public-inode", "late-closure", "late-raw-inode"):
            replacement = run / "fixture-owned-replacement"
            replacement.write_bytes(path.read_bytes())
            os.replace(replacement, path)
        elif scenario in ("late-unknown", "late-raw-sidecar"):
            path.write_bytes(b"FOREIGN_FIXTURE_DO_NOT_DELETE\n")
        else:
            path.write_bytes(path.read_bytes() + b"\n")
    can_reuse, veto = reusable(store)
    next_generation_blocked = None
    if scenario in ("owner-output-same", "owner-output-different",
                    "fsync-unlink-veto-stop-failure", "pending-release-failure"):
        calls_before, admissions_before = list(executor.calls), len(admissions)
        runs_before = set(path.name for path in store.runs.iterdir())
        try:
            module.run_once(store, admission, executor, public_check,
                            lambda value: "FIXTURE_ALERT_RECORDED", fixture=True)
        except Exception:
            next_generation_blocked = (
                executor.calls == calls_before and len(admissions) == admissions_before and
                set(path.name for path in store.runs.iterdir()) == runs_before)
        else:
            next_generation_blocked = False
    if report is not None and failure is None:
        failure = report["first_cause"]
    after = source_snapshot(Path(module.__file__))
    observation = {
        "scope": "REAL_NONROOT_FIXTURE_NOT_RELEASE", "scenario": scenario,
        "process_rc": rc, "first_cause": failure, "state_reusable": can_reuse,
        "state_veto": veto, "state": report["state"] if report else None,
        "calls": executor.calls, "admissions": admissions,
        "loopback_scope": public["scope"], "loopback_checks": len(public["checks"]),
        "source_before": before, "source_after": after, "source_stable": before == after,
        "production_operations": 0, "certified": False, "uid": os.geteuid(),
        "final_owner_fsync_attempts": len(root_syncs),
        "unknown_preserved": (not scenario == "late-unknown" or
                              path.read_bytes() == b"FOREIGN_FIXTURE_DO_NOT_DELETE\n"),
        "producer_rc": public_observation["actual_rc"],
        "producer_stderr_bytes": public_observation["stderr_bytes"],
        "owner_unlink_failures": unlink_failures,
        "foreign_owner_preserved": foreign_owner is None or (
            store.owner.read_bytes() == foreign_owner and
            (store.owner.stat().st_dev, store.owner.stat().st_ino) == foreign_identity),
        "admission_pending": any((root / "runs").glob("*/admission-pending.json")),
        "next_generation_blocked_before_admission": next_generation_blocked,
    }
    gate.write_new(out / "observation.json", gate.encode(observation))
    return rc if before == after else 5


class ContinuousTests(unittest.TestCase):
    original = False
    output = None

    @classmethod
    def setUpClass(cls):
        if cls.output is None:
            TEST_ROOT.mkdir(parents=True, exist_ok=True)
            cls.output = TEST_ROOT / ("discover-" + uuid4().hex)
            fixture_directory(cls.output)
            gate.OUTPUT_ROOT = TEST_ROOT

    def finite_process(self, mode, expected):
        path = self.output / ("finite-" + mode + "-" + uuid4().hex)
        argv = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                "--finite-fixture", mode, "--out", str(path)]
        child = subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True,
                               env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                                    "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"})
        capture(path.with_suffix(".stdout.bin"), child.stdout)
        capture(path.with_suffix(".stderr.bin"), child.stderr)
        capture(path.with_suffix(".launcher.json"), gate.encode({
            "argv": argv, "actual_rc": child.returncode,
            "stdout_sha256": gate.sha(child.stdout), "stdout_bytes": len(child.stdout),
            "stderr_sha256": gate.sha(child.stderr), "stderr_bytes": len(child.stderr)}))
        self.assertEqual(child.returncode, expected, child.stderr.decode(errors="replace"))
        value = gate.decode(gate.read_file(path / "fixture.json"))
        self.assertEqual(value["actual_rc"], child.returncode)
        self.assertNotEqual(value["uid"], 0)
        self.assertTrue(value["source_stable"])
        self.assertTrue(value["timer_absent"])
        self.assertTrue(value["executor_absent"])
        self.assertEqual(value["production_operations"], 0)
        self.assertEqual(value["network_operations"], 0)
        self.assertEqual(value["scope"], "FILE_ONLY_PROTOCOL_NOT_FEM_NOT_PUBLIC")
        self.assertEqual({name: pin["sha256"] for name, pin in value["source_after"].items()}, MAIN_PINS)
        return child, value

    def test_finite_collector_main_abi_and_real_cli(self):
        module = load_target(False)
        self.assertIs(module.gate, gate)
        self.assertEqual(Path(module.sync.__file__), ROOT / "tools/sync_release.py")
        self.assertEqual(Path(module.gate.__file__), ROOT / "tools/release_gate.py")
        self.assertIsNone(module.APPROVED_EXECUTOR)
        self.assertEqual(module.CLOSURE_ROOT, Path("/data/dev/opensim-s4-sync-closure-20261011/tools"))
        self.assertEqual(module.CLOSURE_SOURCES["sync_release.py"], MAIN_PINS["tools/sync_release.py"])
        self.assertEqual(module.CLOSURE_SOURCES["release_gate.py"], MAIN_PINS["tools/release_gate.py"])
        child, value = self.finite_process("normal", 0)
        result = value["collection"]
        self.assertEqual(result["state"], "COLLECTED_NOT_CERTIFIED")
        self.assertFalse(result["certified"])
        self.assertEqual(result["release_verdict"], "NOT_CERTIFIED")
        self.assertEqual(result["actual_rc"], 0)
        self.assertIsNone(result["first_cause"])
        self.assertIsNone(value["late_failure"])
        self.assertEqual(len(value["calls"]), 1)
        self.assertEqual(len(result["commands"]), 1)
        self.assertEqual(value["calls"][0]["fixed"], result["commands"][0]["argv"])
        self.assertNotEqual(value["calls"][0]["actual"], value["calls"][0]["fixed"])
        self.assertEqual(set(value["calls"][0]["environment"]),
                         {"PATH", "LANG", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"})
        self.assertIn(b"provisional_actual_launcher_rc_required", child.stdout)
        observation = result["observation"]
        self.assertEqual(observation["report"]["fixture_scope"], value["scope"])
        self.assertNotEqual(observation["stdout_sha256"], observation["canonical_report_sha256"])
        for key in ("stdout", "stderr"):
            stream = result["commands"][0]["streams"][key]
            self.assertTrue(stream["complete"])
            self.assertFalse(stream["oversized"])
        self.assertEqual(result["commands"][0]["streams"]["stderr"]["bytes"], 0)
        with self.assertRaises(module.Refused):
            module.check_public(observation["report"], {"identity": {
                "source_sha256": observation["report"]["source_sha256"],
                "page_build": observation["report"]["page_build"]}}, fixture=False)

    def test_finite_collector_offline_failure_and_late_veto(self):
        for mode, expected in (("missing-source", 2), ("pin", 2), ("commit", 2),
                               ("rc", 2), ("late-inode", 3)):
            with self.subTest(mode=mode):
                _, value = self.finite_process(mode, expected)
                result = value["collection"]
                if mode == "late-inode":
                    self.assertEqual(result["exit_code"], 0, "PROVISIONAL_SUCCESS_IS_NOT_OUTER_RC")
                    self.assertEqual(value["flags"], ["SAME_BYTES_NEW_INODE_AFTER_PROVISIONAL_STDOUT"])
                    self.assertEqual(value["late_failure"]["state"], "EVIDENCE_FAILED")
                    self.assertEqual(value["late_failure"]["exit_code"], 3)
                    self.assertEqual(value["late_failure"]["first_cause"]["code"], "SOURCE_OR_ARTIFACT_DRIFT")
                    continue
                self.assertEqual(result["state"], "COLLECTION_FAILED_NOT_CERTIFIED")
                self.assertFalse(result["certified"])
                if mode in ("missing-source", "pin"):
                    self.assertEqual(value["calls"], [])
                    self.assertIsNone(result["actual_rc"])
                else:
                    self.assertEqual(len(value["calls"]), 1)
                if mode == "missing-source":
                    self.assertEqual(result["first_cause"]["type"], "FileNotFoundError")
                elif mode == "pin":
                    self.assertEqual(result["first_cause"]["code"], "PUBLIC_SOURCE_PIN_MISMATCH")
                elif mode == "commit":
                    self.assertEqual(result["first_cause"]["code"], "PUBLIC_COMMIT_MISMATCH")
                    self.assertEqual(result["actual_rc"], 0, "PRODUCER_ZERO_IS_NOT_COLLECTION_SUCCESS")
                else:
                    self.assertEqual(result["first_cause"], {"code": "PUBLIC_PRODUCER_PROCESS_FAILED", "actual_rc": 7})
                    self.assertEqual(result["actual_rc"], 7)

    def child(self, scenario):
        path = self.output / (scenario + "-" + uuid4().hex)
        argv = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                "--fixture-child", scenario, "--out", str(path)]
        if self.original:
            argv.append("--original")
        child = subprocess.run(argv, cwd=ROOT, capture_output=True,
                               env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                                    "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"})
        capture(path.with_suffix(".stdout.log"), child.stdout)
        capture(path.with_suffix(".stderr.log"), child.stderr)
        gate.write_new(path.with_suffix(".launcher.json"),
                       gate.encode({"argv": argv, "actual_rc": child.returncode}))
        value = gate.decode(gate.read_file(path / "observation.json"))
        self.assertEqual(value["process_rc"], child.returncode, value)
        self.assertNotEqual(value["uid"], 0)
        self.assertTrue(value["source_stable"])
        self.assertEqual(value["loopback_scope"], "LOCAL_TEST")
        return child, value

    def test_real_loopback_control_not_certified(self):
        child, value = self.child("control")
        self.assertEqual(child.returncode, 0, value)
        self.assertEqual(value["state"], "FIXTURE_SYNCHRONIZED")
        self.assertEqual(value["calls"], ["deploy"])
        self.assertTrue(value["state_reusable"], value)
        self.assertFalse(value["admission_pending"], value)

    def test_closure_v2_binding_exact_plan_preserves_v1(self):
        module = load_target(False)
        base = {"request_sha256": "a" * 64, "commit": "b" * 40,
                "manifest_sha256": "c" * 64, "identity": {"commit": "b" * 40},
                "plan": {name: None for name in module.PLAN_FIELDS}}
        base["plan"]["collected_at"] = "2026-10-11T00:00:00+00:00"
        module.binding(base)
        value = copy.deepcopy(base)
        value["plan"].pop("gate_receipt")
        value["plan"]["closure_request"] = {"path": "/file-only/closure.json", "sha256": "d" * 64}
        self.assertEqual(module.binding(value)["plan"]["closure_request"],
                         value["plan"]["closure_request"])
        for extra in ("ledger", "gate_receipt", "certified"):
            invalid = copy.deepcopy(value)
            invalid["plan"][extra] = True
            with self.subTest(extra=extra), self.assertRaises(module.Refused):
                module.binding(invalid)
        self.assertIsNone(module.APPROVED_EXECUTOR)

    def test_closure_v2_admission_has_no_unconditional_legacy_receipt(self):
        body = gate.read_file(TARGET)
        syntax = ast.parse(body)
        function = next(node for node in syntax.body if isinstance(node, ast.FunctionDef)
                        and node.name == "admit")
        branches = [node for node in function.body if isinstance(node, ast.If) and
                    "opensim-desired-release-v2" in ast.unparse(node.test)]
        self.assertEqual(len(branches), 1, "V2_HAS_NO_GATE_RECEIPT_LEGACY_ACCESS")
        branch = branches[0]
        self.assertTrue(any(isinstance(node, ast.Return) and
                            "admit_closure" in ast.unparse(node) for node in branch.body))
        legacy = [node for node in function.body if isinstance(node, ast.Assign) and
                  "gate_receipt" in ast.unparse(node)]
        self.assertTrue(all(node.lineno > branch.end_lineno for node in legacy))

    def closure_process(self, mode, expected=2):
        path = self.output / ("closure-" + mode + "-" + uuid4().hex)
        argv = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                "--closure-fixture", mode, "--out", str(path)]
        child = subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True,
                               env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        capture(path.with_suffix(".stdout.bin"), child.stdout)
        capture(path.with_suffix(".stderr.bin"), child.stderr)
        capture(path.with_suffix(".launcher.json"), gate.encode({"argv": argv, "actual_rc": child.returncode}))
        self.assertEqual(child.returncode, expected, child.stderr.decode("utf-8", errors="replace"))
        value = gate.decode(gate.read_file(path / "result.json"))
        self.assertEqual(value["actual_child_rc"], expected)
        self.assertNotEqual(value["uid"], 0)
        self.assertTrue(value["source_stable"])
        self.assertEqual(value["production_operations"], 0)
        self.assertFalse(value["report"]["certified"])
        self.assertNotIn("admission", value["report"])
        return value

    def test_closure_real_complete_untrusted_preserves_blocked(self):
        value = self.closure_process("complete")
        self.assertIn("trusted human/producer provenance", value["report"]["first_cause"]["code"])
        original = value["report"]["closure_execution"]["owner"]["closure_execution"]
        self.assertEqual(original["actual_child_rc"], 1)
        self.assertTrue(original["final_source_stable"])
        self.assertIn("--closure-request", original["argv"])
        self.assertNotIn("--ledger", original["argv"])
        self.assertEqual(original["report"]["verdict"], "BLOCKED")
        self.assertEqual(original["report"]["release_verdict"], "NOT_CERTIFIED")
        self.assertEqual(value["original_audit_status"], "PARTIAL")

    def test_closure_real_missing_human_and_wrong_pin_no_fallback(self):
        for mode, reason in (("human", "Missing real device/manual"),
                             ("wrong-pin", "CLOSURE_INPUT_PIN_DRIFT"),
                             ("fallback", "Unknown desired-release")):
            with self.subTest(mode=mode):
                value = self.closure_process(mode)
                self.assertIn(reason, value["report"]["first_cause"]["code"])

    def test_closure_real_raw_inode_dependency_drift_is_rejected(self):
        value = self.closure_process("late-inode")
        self.assertEqual(value["drift"]["code"], "SOURCE_OR_ARTIFACT_DRIFT")

    def test_closure_native_pair_inputs_exact_not_generic_links(self):
        module = load_target(False)
        directory = self.output / ("native-pair-" + uuid4().hex)
        fixture_directory(directory)
        path = directory / "report.json"
        body = gate.encode({"completion": {"protocol": "durable-final-v2", "status": "FINAL"},
                            "scope": "FILE_ONLY_NOT_AUDIT_ACCEPTANCE"})
        capture(path, body)
        os.link(path, directory / "report.candidate.json")
        request = directory / "request.json"
        capture(request, gate.encode({"audit": {"path": str(path), "sha256": gate.sha(body)}}))
        desired = {"closure_request": {"path": str(request), "sha256": gate.sha(request.read_bytes())},
                   "approval_pin": None}
        with module.closure_api() as (owner, _):
            records = module.closure_inputs(owner, desired, ROOT)
        self.assertEqual(len([record for record in records if "sha256" in record]), 2)
        for record in records:
            module.verify_dependency(record)
        self.assertIn("native_pair", records[1])
        os.link(path, directory / "third-link.json")
        with self.assertRaises((module.Refused, gate.GateError, RuntimeError)):
            module.verify_dependency(records[1])
        self.assertEqual(path.read_bytes(), body)

    def test_closure_actual_output_fsync_inode_sidecar_fail_closed(self):
        for mode in ("stdout", "fsync", "late-output-inode", "late-output-sidecar"):
            with self.subTest(mode=mode):
                value = self.closure_process(mode, 3)
                failed = value["failure_report"]
                self.assertEqual(failed["state"], "INSPECT_EVIDENCE_FAILED")
                self.assertEqual(failed["exit_code"], 3)
                self.assertIn("Missing real device/manual", failed["first_cause"]["code"])
                self.assertIn("evidence_failure", failed)
                path = Path(value["argv"][-1])
                fixture = gate.decode((path / "boundary-fixture.json").read_bytes())
                self.assertEqual(fixture["actual_rc"], 3)
                self.assertNotEqual(fixture["uid"], 0)
                if mode != "stdout":
                    self.assertEqual(len(fixture["triggered"]), 1)
                if mode == "late-output-sidecar":
                    pins = failed["closure_execution"]["inputs"]
                    baseline = Path(next(pin["path"] for pin in pins if pin["path"].endswith("baseline.json")))
                    self.assertEqual((baseline.parent / "finalization-failure.json").read_bytes(),
                                     b"UNKNOWN_PRESERVE\n")

    def test_real_public_cli_verdict_line_is_not_canonical_json(self):
        module = load_target(False)
        directory = self.output / ("real-cli-" + uuid4().hex)
        value = loopback_collection(module, directory)
        self.assertEqual(value["exit_code"], 0, value)
        observation = value["observation"]
        report = observation["report"]
        self.assertEqual(report["scope"], "LOCAL_TEST")
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(len(report["checks"]), 4)
        self.assertNotEqual(observation["stdout_sha256"], observation["canonical_report_sha256"])
        context = {"identity": {
            "source_sha256": report["source_sha256"], "page_build": report["page_build"],
            "tool_sha256": {"tools/public_gate.py": observation["source_before"][-1]["sha256"]}}}
        self.assertEqual(module.check_producer(observation, context, fixture=True), report)
        module.check_public(report, context, fixture=True)
        self.assertIsNone(module.APPROVED_EXECUTOR)
        self.assertFalse(value["certified"])

    def test_unchanged_binding_different_collection_time(self):
        child, value = self.child("timestamp")
        self.assertNotEqual(value["admissions"][0]["plan"]["collected_at"],
                            value["admissions"][1]["plan"]["collected_at"])
        self.assertEqual(child.returncode, 0, value)
        self.assertEqual(value["calls"], ["deploy"])
        self.assertTrue(value["state_reusable"], value)
        self.assertFalse(value["admission_pending"], value)

    def test_public_cli_real_stderr_nonzero_and_pin_fail_closed(self):
        for mode in ("stderr", "rc", "pin"):
            with self.subTest(mode=mode):
                child, path, fixture = self.collector_process(mode)
                self.assertEqual(child.returncode, 2, child.stderr)
                result = gate.decode((path / "collection" / "collection.json").read_bytes())
                self.assertEqual(result["actual_rc"], 7 if mode == "rc" else
                                 None if mode == "pin" else 0)
                self.assertEqual(len(fixture["calls"]), 0 if mode == "pin" else 1)
                self.assertFalse(result["certified"])
                self.assertNotEqual(result["state"], "COLLECTED_NOT_CERTIFIED")

    def collector_process(self, mode):
        path = self.output / ("collector-" + mode + "-" + uuid4().hex)
        argv = [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                "--collector-fixture", mode, "--out", str(path)]
        child = subprocess.run(argv, cwd=ROOT, capture_output=True,
                               env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                                    "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"})
        capture(path.with_suffix(".stdout.log"), child.stdout)
        capture(path.with_suffix(".stderr.log"), child.stderr)
        gate.write_new(path.with_suffix(".launcher.json"),
                       gate.encode({"argv": argv, "actual_rc": child.returncode}))
        fixture = gate.decode((path / "fixture.json").read_bytes())
        self.assertEqual(fixture["actual_rc"], child.returncode)
        self.assertNotEqual(fixture["uid"], 0)
        self.assertEqual(fixture["production_operations"], 0)
        return child, path, fixture

    def test_public_cli_late_stdout_and_fsync_real_failures_veto(self):
        for mode, expected in (("stdout", 3), ("late-inode", 3), ("fsync", 4), ("stream", 2)):
            with self.subTest(mode=mode):
                child, path, fixture = self.collector_process(mode)
                self.assertEqual(child.returncode, expected, child.stderr)
                self.assertEqual(len(fixture["calls"]), 1)
                if mode == "fsync":
                    self.assertIn("ACTUAL_COLLECTION_BYTES_WRITTEN_BEFORE_FSYNC_FAILURE", fixture["flags"])
                    self.assertTrue((path / "collection" / "collection.failure.json").exists())
                elif mode == "stream":
                    self.assertIn("ACTUAL_PIPE_READ_FAILED", fixture["flags"])
                    result = gate.decode((path / "collection" / "collection.json").read_bytes())
                    self.assertIsInstance(result["actual_rc"], int)
                    self.assertEqual(result["first_cause"]["code"], "STREAM_READ_FAILED")
                else:
                    self.assertTrue((path / "collection" / "report.failure.json").exists())

    def test_public_cli_raw_report_log_inode_sidecar_fail_late(self):
        module = load_target(False)
        for name in ("report", "log", "stdout", "stderr", "inode", "sidecar", "source"):
            with self.subTest(name=name):
                value = loopback_collection(module, self.output / ("raw-" + name + "-" + uuid4().hex))
                observation = value["observation"]
                self.assertEqual(value["exit_code"], 0, value)
                module.verify_collection(observation)
                if name == "sidecar":
                    path = Path(observation["directory"]["path"]) / "failure.json"
                    path.write_bytes(b"UNKNOWN_PRESERVE\n")
                elif name == "source":
                    path = self.output / ("source-pin-" + uuid4().hex)
                    capture(path, b"FIXTURE_SOURCE_ONLY\n")
                    observation["source_before"] = [module.observe_file(path)]
                    path.write_bytes(b"FIXTURE_SOURCE_CHANGED\n")
                    with self.assertRaisesRegex(module.Refused, "SOURCE_OR_ARTIFACT_DRIFT"):
                        module.verify_dependency(observation["source_before"][0])
                    continue
                else:
                    path = Path(observation["artifacts"]["report" if name == "inode" else name]["path"])
                    if name == "inode":
                        replacement = path.parent / "fixture-replacement"
                        replacement.write_bytes(path.read_bytes())
                        os.replace(replacement, path)
                    else:
                        path.write_bytes(path.read_bytes() + b"\n")
                with self.assertRaisesRegex(module.Refused, "SOURCE_OR_ARTIFACT_DRIFT|PUBLIC_DIRECTORY_DRIFT"):
                    module.verify_collection(observation)
                if name == "sidecar":
                    self.assertEqual(path.read_bytes(), b"UNKNOWN_PRESERVE\n")

    def test_stdout_failure_vetoes_next_generation(self):
        child, value = self.child("stdout")
        self.assertEqual(child.returncode, 4)
        self.assertFalse(value["state_reusable"], value)

    def test_final_owner_fsync_failure_vetoes_next_generation(self):
        child, value = self.child("fsync")
        self.assertEqual(child.returncode, 4)
        self.assertGreaterEqual(value["final_owner_fsync_attempts"], 2)
        self.assertFalse(value["state_reusable"], value)

    def test_final_owner_fsync_plus_veto_failure_cannot_leave_success(self):
        for scenario in ("fsync-veto-failure", "fsync-veto-stop-failure"):
            with self.subTest(scenario=scenario):
                child, value = self.child(scenario)
                self.assertEqual(child.returncode, 4, value)
                self.assertFalse(value["state_reusable"], value)

    def test_provisional_owner_replacement_refuses_exact_cas(self):
        for scenario in ("owner-output-same", "owner-output-different"):
            with self.subTest(scenario=scenario):
                child, value = self.child(scenario)
                self.assertEqual(child.returncode, 4, value)
                self.assertEqual(value["first_cause"]["code"], "OWNER_CAS_DRIFT")
                self.assertTrue(value["foreign_owner_preserved"], value)
                self.assertFalse(value["state_reusable"], value)
                self.assertEqual(value["calls"], ["deploy", "stop", "revert"])
                self.assertTrue(value["next_generation_blocked_before_admission"], value)

    def test_fsync_unlink_stop_veto_failure_keeps_durable_admission_pending(self):
        child, value = self.child("fsync-unlink-veto-stop-failure")
        self.assertEqual(child.returncode, 4, value)
        self.assertEqual(value["first_cause"]["errno"], errno.ENOSPC)
        self.assertEqual(value["first_cause"]["revocation_cause"]["errno"], errno.EACCES)
        self.assertEqual(value["owner_unlink_failures"], ["FIXTURE_OWNER_UNLINK_FAILED"])
        self.assertEqual(value["calls"], ["deploy", "stop"])
        self.assertFalse(value["state_reusable"], value)
        self.assertTrue(value["admission_pending"], value)
        self.assertTrue(value["next_generation_blocked_before_admission"], value)
        secondary = value["first_cause"]["finalization_secondary"]
        self.assertTrue(any(item["code"] == "FIXTURE_STOP_FAILED" for item in secondary))
        self.assertTrue(any(item["errno"] == errno.ENOSPC for item in secondary))
        self.assertIn(b"CONTINUOUS_VETO_PERSIST_FAILED", child.stderr)

    def test_pending_release_failure_preserves_blocking_state(self):
        child, value = self.child("pending-release-failure")
        self.assertEqual(child.returncode, 4, value)
        self.assertEqual(value["first_cause"]["errno"], errno.EACCES)
        self.assertEqual(value["owner_unlink_failures"], ["FIXTURE_PENDING_UNLINK_FAILED"])
        self.assertEqual(value["calls"], ["deploy", "stop", "revert"])
        self.assertTrue(value["admission_pending"], value)
        self.assertFalse(value["state_reusable"], value)
        self.assertTrue(value["next_generation_blocked_before_admission"], value)

    def test_public_real_fem_fields_cannot_be_removed(self):
        child, value = self.child("public-metrics")
        self.assertEqual(child.returncode, 3, value)
        self.assertEqual(value["calls"], ["deploy", "stop", "revert"])

    def test_each_terminal_dependency_drift_vetoes_next_generation(self):
        for scenario in SCENARIOS:
            if scenario.startswith("late-"):
                with self.subTest(scenario=scenario):
                    _, value = self.child(scenario)
                    self.assertFalse(value["state_reusable"], value)
                    self.assertTrue(value["unknown_preserved"])

    def test_original_observations_not_rebaselined_after_output(self):
        child, value = self.child("during-output")
        self.assertEqual(child.returncode, 4, value)
        self.assertFalse(value["state_reusable"], value)

    def test_actual_post_stdout_stderr_and_nonzero_rc_rejected(self):
        for scenario in ("producer-stderr", "producer-rc"):
            with self.subTest(scenario=scenario):
                child, value = self.child(scenario)
                self.assertEqual(child.returncode, 3, value)
                self.assertEqual(value["first_cause"]["code"], "PUBLIC_PRODUCER_PROCESS_FAILED")
                self.assertEqual(value["calls"], ["deploy", "stop", "revert"])
                self.assertTrue(value["producer_stderr_bytes"] or value["producer_rc"])

    def test_failed_stop_or_revert_blocks_later_generation(self):
        for scenario, calls in (("stop-failure", ["deploy", "stop"]),
                                ("revert-failure", ["deploy", "stop", "revert"])):
            with self.subTest(scenario=scenario):
                child, value = self.child(scenario)
                self.assertEqual(child.returncode, 3, value)
                self.assertEqual(value["state"], "FAILED_RECOVERY_REQUIRED")
                self.assertEqual(value["calls"], calls)
                self.assertFalse(value["state_reusable"], value)

    def test_missing_executor_blocked_with_zero_deploy(self):
        child, value = self.child("missing-executor")
        self.assertEqual(child.returncode, 2, value)
        self.assertEqual(value["first_cause"]["code"], "MISSING_REVIEWED_CONTINUOUS_EXECUTOR")
        self.assertEqual(value["calls"], [])

    def test_request_and_build_drift_are_not_timestamp_exclusions(self):
        for scenario, rc, calls in (
                ("request-drift", 2, []), ("build-drift", 3, ["deploy", "stop", "revert"])):
            with self.subTest(scenario=scenario):
                child, value = self.child(scenario)
                self.assertEqual(child.returncode, rc, value)
                self.assertEqual(value["calls"], calls)

    def test_same_bytes_inode_and_hardlink_not_equivalent(self):
        module = load_target(False)
        path = self.output / ("source-slice-" + uuid4().hex)
        capture(path, b"FIXTURE_SOURCE_ONLY\n")
        pin = module.observe_file(path)
        replacement = path.with_suffix(".replacement")
        capture(replacement, path.read_bytes())
        os.replace(replacement, path)
        with self.assertRaises(module.Refused):
            module.verify_observation(pin)
        alias = path.with_suffix(".hardlink")
        os.link(path, alias)
        with self.assertRaises((module.Refused, gate.GateError)):
            module.observe_file(path)

    def test_unknown_state_and_busy_lock_preserve_assets(self):
        module = load_target(False)
        root = self.output / ("owner-check-" + uuid4().hex)
        fixture_directory(root)
        store = module.Store(root)
        with store.locked(initialize=True):
            store.current()
            with self.assertRaisesRegex(module.Refused, "OWNER_BUSY_NO_REPLAY"):
                with store.locked():
                    pass
        capture(root / "foreign.asset", b"DO_NOT_REMOVE\n")
        with self.assertRaises(module.Refused):
            with store.locked():
                pass
        self.assertEqual((root / "foreign.asset").read_bytes(), b"DO_NOT_REMOVE\n")

    def test_failing_owner_sync_never_revokes_foreign_replacement(self):
        module = load_target(False)
        for same_bytes in (False, True):
            with self.subTest(same_bytes=same_bytes):
                root = self.output / ("foreign-owner-" + uuid4().hex)
                fixture_directory(root)
                store = module.Store(root)
                with store.locked(initialize=True):
                    value, expected = store.current()
                original = module.os.fsync
                replacement_identity = None
                foreign_body = None

                def replace_on_sync(fd):
                    nonlocal replacement_identity, foreign_body
                    observed = os.fstat(fd)
                    target = root.stat()
                    owner_info = store.owner.stat()
                    if ((observed.st_dev, observed.st_ino) == (target.st_dev, target.st_ino)
                            and (owner_info.st_dev, owner_info.st_ino) != expected[1][:2]):
                        foreign_body = (store.owner.read_bytes() if same_bytes
                                        else b"FOREIGN_OWNER_NOT_JSON\n")
                        replacement = root / "fixture-foreign.pending"
                        with replacement.open("xb") as stream:
                            stream.write(foreign_body)
                        os.chmod(replacement, 0o600)
                        info = replacement.stat()
                        replacement_identity = (info.st_dev, info.st_ino)
                        os.replace(replacement, store.owner)
                        raise OSError(errno.ENOSPC, "FIXTURE_FOREIGN_REPLACEMENT_SYNC")
                    return original(fd)

                module.os.fsync = replace_on_sync
                try:
                    with self.assertRaises(OSError) as raised:
                        store.replace_owner(expected, value)
                    self.assertEqual(raised.exception.errno, errno.ENOSPC)
                    self.assertEqual(raised.exception.revocation_cause["code"],
                                     "OWNER_REPLACEMENT_NOT_OWNED_NO_REVOCATION")
                finally:
                    module.os.fsync = original
                info = store.owner.stat()
                self.assertEqual((info.st_dev, info.st_ino), replacement_identity)
                self.assertEqual(store.owner.read_bytes(), foreign_body)

    def test_cli_fixed_modes_reject_paths_before_owner_access(self):
        for argv in (["--once", "--source-root", str(ROOT)],
                     ["--init-state", "--desired-sha256", "a" * 64],
                     ["--inspect-candidate"]):
            with self.subTest(argv=argv):
                child = subprocess.run([sys.executable, "-I", "-B", str(TARGET), *argv],
                                       capture_output=True, cwd=ROOT)
                self.assertEqual(child.returncode, 2)
                self.assertIn(b"error:", child.stderr)

    def test_service_templates_cannot_grant_production_or_renew_lease(self):
        service = (ROOT / "deploy/opensim-sync.service").read_text()
        timer = (ROOT / "deploy/opensim-sync.timer").read_text()
        self.assertIn("User=infra", service)
        self.assertIn("continuous_release.py --once", service)
        self.assertNotIn("[Install]", service + timer)
        self.assertNotIn("sudo", service + timer)
        self.assertNotIn("systemctl", service + timer)
        self.assertIn("Persistent=false", timer)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--public-fixture")
    parser.add_argument("--public-cli-out", type=Path)
    parser.add_argument("--finite-producer", choices=FINITE_MODES)
    parser.add_argument("--finite-fixture", choices=FINITE_MODES)
    parser.add_argument("--collector-fixture", choices=("normal", "stderr", "rc", "pin",
                                                      "stdout", "late-inode", "fsync", "stream"))
    parser.add_argument("--public-mode", choices=("normal", "stderr", "rc"), default="normal")
    parser.add_argument("--original", action="store_true")
    parser.add_argument("--select", action="append")
    parser.add_argument("--fixture-child", choices=SCENARIOS)
    parser.add_argument("--closure-fixture", choices=("complete", "human", "wrong-pin", "fallback", "late-inode",
                                                     "stdout", "fsync", "late-output-inode", "late-output-sidecar"))
    parser.add_argument("--inspect-boundary", choices=("stdout", "fsync", "late-output-inode", "late-output-sidecar"))
    parser.add_argument("--inspect-candidate", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--desired", type=Path)
    parser.add_argument("--desired-sha256")
    parser.add_argument("--focused", choices=("closure-module", "closure-contract", "closure-cli", "public-cli", "cas-pending", "fsync", "dual-failure", "binding", "publication", "dependencies", "public", "failure"))
    args = parser.parse_args(argv)
    if args.finite_producer:
        if not args.public_cli_out:
            parser.error("--finite-producer requires --public-cli-out")
        return finite_producer(args.public_cli_out, args.finite_producer)
    if args.public_fixture:
        return public_child(args.public_fixture, args.public_mode, args.public_cli_out)
    if not args.out:
        parser.error("--out is required for the fixture suite")
    if args.finite_fixture:
        return finite_child(args.out, args.finite_fixture)
    if args.inspect_boundary:
        return inspect_boundary(args)
    if args.collector_fixture:
        return collector_child(args.out, args.collector_fixture)
    if args.fixture_child:
        return fixture_child(args.out, args.original, args.fixture_child)
    if args.closure_fixture:
        return closure_child(args.out, args.closure_fixture)
    gate.external(args.out, TEST_ROOT)
    gate.OUTPUT_ROOT = TEST_ROOT
    ContinuousTests.output = args.out
    ContinuousTests.original = args.original
    path = ORIGINAL if args.original else TARGET
    before = source_snapshot(path)
    finite = bool(args.select) and all(name in FINITE_SELECTORS for name in args.select)
    if finite and (args.original or args.focused):
        parser.error("finite selectors require current sibling candidate only")
    inventory = [ROOT / name for name in (
        "tests/test_continuous_release.py", "deploy/opensim-sync.service",
        "deploy/opensim-sync.timer", "docs/deploy/CONTINUOUS_RELEASE.md",
        "tools/sync_release.py", "tools/release_gate.py", "tests/test_release_gate.py",
        "tools/public_gate.py", "tools/public_watch.py", *gate.SOURCES)]
    if finite:
        sys.addaudithook(deny_fixture_network)
        gate.require(not os.path.lexists(ROOT / "deploy/opensim-sync.timer"),
                     "MAIN_TIMER_MUST_REMAIN_ABSENT")
        inventory = [ROOT / name for name in (*MAIN_PINS, "tools/public_gate.py",
                    "tests/test_continuous_release.py", "docs/deploy/CONTINUOUS_RELEASE.md")]
    inventory.append(path)
    closure_sources = {
        "tools/sync_release.py", "tools/release_gate.py", "tools/release_obligations.py",
        "tests/test_sync_release.py", "tests/test_release_closure.py", "tests/test_release_gate.py",
        "docs/deploy/SYNC_RELEASE.md"}
    if not finite:
        inventory.extend(Path("/data/dev/opensim-s4-sync-closure-20261011") / name for name in closure_sources)
        inventory.extend(Path(path) for path in (
            "/data/dev/opensim-production-trust-20261011/tools/release_trust.py",
            "/data/dev/opensim-authorized-obligations-20261010/tools/release_obligations.py",
            "/data/dev/opensim-authorized-trust-20261010/tools/release_trust.py"))
    sources_before = {str(file): source_snapshot(file) for file in sorted(set(inventory))}
    selected = {
        "closure-contract": ("test_closure_v2_binding_exact_plan_preserves_v1",
                             "test_closure_v2_admission_has_no_unconditional_legacy_receipt"),
        "closure-cli": ("test_closure_real_complete_untrusted_preserves_blocked",
                        "test_closure_real_missing_human_and_wrong_pin_no_fallback",
                        "test_closure_real_raw_inode_dependency_drift_is_rejected"),
        "public-cli": ("test_real_public_cli_verdict_line_is_not_canonical_json",
                       "test_public_cli_real_stderr_nonzero_and_pin_fail_closed",
                       "test_public_cli_late_stdout_and_fsync_real_failures_veto",
                       "test_public_cli_raw_report_log_inode_sidecar_fail_late"),
        "cas-pending": ("test_provisional_owner_replacement_refuses_exact_cas",
                        "test_fsync_unlink_stop_veto_failure_keeps_durable_admission_pending",
                        "test_pending_release_failure_preserves_blocking_state"),
        "fsync": ("test_final_owner_fsync_failure_vetoes_next_generation",),
        "dual-failure": ("test_final_owner_fsync_plus_veto_failure_cannot_leave_success",),
        "binding": ("test_unchanged_binding_different_collection_time",),
        "publication": ("test_stdout_failure_vetoes_next_generation",
                        "test_final_owner_fsync_failure_vetoes_next_generation",
                        "test_original_observations_not_rebaselined_after_output"),
        "dependencies": ("test_each_terminal_dependency_drift_vetoes_next_generation",),
        "public": ("test_public_real_fem_fields_cannot_be_removed",
                   "test_real_loopback_control_not_certified"),
        "failure": ("test_actual_post_stdout_stderr_and_nonzero_rc_rejected",
                    "test_failed_stop_or_revert_blocks_later_generation",
                    "test_missing_executor_blocked_with_zero_deploy",
                    "test_request_and_build_drift_are_not_timestamp_exclusions",
                    "test_same_bytes_inode_and_hardlink_not_equivalent",
                    "test_unknown_state_and_busy_lock_preserve_assets",
                    "test_cli_fixed_modes_reject_paths_before_owner_access"),
    }
    selected["closure-module"] = (*selected["closure-contract"], *selected["closure-cli"],
        "test_closure_native_pair_inputs_exact_not_generic_links",
        "test_closure_actual_output_fsync_inode_sidecar_fail_closed",
        *selected["public-cli"], "test_unchanged_binding_different_collection_time",
        "test_missing_executor_blocked_with_zero_deploy")
    if args.select and (args.focused or any(not name.startswith("test_") or
            not hasattr(ContinuousTests, name) for name in args.select)):
        parser.error("--select requires existing test methods and no --focused")
    suite = (unittest.TestSuite(ContinuousTests(name) for name in args.select) if args.select else
             unittest.TestSuite(ContinuousTests(name) for name in selected[args.focused])
             if args.focused else unittest.defaultTestLoader.loadTestsFromTestCase(ContinuousTests))
    rc = run_suite(suite,
                   args.out, "CONTINUOUS_RELEASE")
    after = source_snapshot(path)
    sources_after = {str(file): source_snapshot(file) for file in sorted(set(inventory))}
    gate.write_new(args.out / "target-source.json", gate.encode({
        "path": str(path), "before": before, "after": after,
        "source_stable": sources_before == sources_after, "original_sha256": ORIGINAL_SHA,
        "all_source_before": sources_before, "all_source_after": sources_after,
        "production_operations": 0, "suite_rc": rc}))
    return rc if sources_before == sources_after else 5


if __name__ == "__main__":
    raise SystemExit(main())
