"""Handle only a caller-pinned desired release; no hooks, broker or root dispatch.

Plans validate the gate again before validating fixed release data. Production
apply is deliberately REFUSED until a separately authorized narrow controller
protocol exists. A root-owned file's existence is not authorization.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import inspect
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_gate as gate

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = Path("/usr/local/libexec/opensim-release-app.py")
PREPARE_ROOT = gate.ARTIFACT_ROOT / "seat-release"
REQUEST_FIELDS = {"schema", "request_id", "action", "commit", "ledger", "gate_receipt",
                  "approval_pin", "bundle", "manifest_sha256", "source_epoch",
                  "release", "before_receipt"}
CLOSURE_REQUEST_FIELDS = REQUEST_FIELDS - {"ledger", "gate_receipt"} | {"closure_request"}
PAYLOAD = {f"source/{name}" for name in (*gate.SOURCES, "requirements.txt")} | {
    "evidence/numerics.json", "build/index-v1.html", "build/page-check.json"}


class Refused(gate.GateError):
    pass


def pinned(record: dict) -> tuple[dict, Path, bytes]:
    value, path = gate.reference(record)
    return value, path, gate.read_file(path)


def request(path: Path, digest: str) -> tuple[dict, bytes]:
    raw = gate.read_file(gate.external(path))
    gate.require(re.fullmatch(r"[a-f0-9]{64}", digest or "") and gate.sha(raw) == digest,
                 "Desired-release request hash mismatch")
    value = gate.decode(raw)
    gate.require((set(value) == REQUEST_FIELDS and value["schema"] == "opensim-desired-release-v1") or
                 (set(value) == CLOSURE_REQUEST_FIELDS and value["schema"] == "opensim-desired-release-v2"),
                 "Unknown desired-release schema/fields")
    gate.require(isinstance(value["request_id"], str) and
                 re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", value["request_id"]),
                 "Explicit request ID required")
    gate.require(value["action"] == "INSTALL_FIXED_RELEASE", "Unknown desired-release action")
    gate.require(re.fullmatch(r"[a-f0-9]{40}", value["commit"] or "") and
                 re.fullmatch(r"[a-f0-9]{64}", value["manifest_sha256"] or ""),
                 "Full fixed commit/manifest pins required")
    gate.require(type(value["source_epoch"]) is int and value["source_epoch"] > 0,
                 "Explicit source commit epoch required")
    return value, raw


def load_api(path: Path, expected_sha: str):
    gate.require(os.geteuid() != 0, "Never import checkout release API as root")
    gate.require(path.is_absolute() and not path.is_symlink(), "Invalid release API path")
    body = gate.read_file(path)
    gate.require(gate.sha(body) == expected_sha, "Release API bytes differ from gate tool pin")
    spec = importlib.util.spec_from_file_location("_opensim_sync_api_" + uuid4().hex, path)
    gate.require(spec is not None and spec.loader is not None, "Missing release API loader")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    # Execute exactly the bytes just verified, avoiding stale bytecode.
    exec(compile(body, str(path), "exec"), module.__dict__)
    for name, parameters in (("prepare", ("root", "commit", "evidence", "bundle")),
                             ("validate_bundle", ("bundle", "digest", "commit"))):
        gate.require(callable(getattr(module, name, None)) and
                     tuple(inspect.signature(getattr(module, name)).parameters) == parameters,
                     f"Release API signature drift: {name}")
    gate.require(tuple(module.SOURCES) == gate.SOURCES and
                 tuple(module.EXPORTS) == (*gate.SOURCES, "requirements.txt") and
                 set(module.PAYLOAD) == PAYLOAD and module.CONTROLLER == CONTROLLER,
                 "Release exports/payload/controller API drift")
    return module


def match_manifest(manifest: dict, desired: dict, expected: dict) -> None:
    gate.require(set(manifest) == {"schema", "commit", "source_epoch", "source_sha256",
                                  "page_build", "files"} and manifest["schema"] == 1,
                 "Release manifest schema drift")
    gate.require(manifest["commit"] == desired["commit"] and
                 manifest["source_epoch"] == desired["source_epoch"] and
                 manifest["source_sha256"] == expected["source_sha256"] and
                 manifest["page_build"] == expected["page_build"] and
                 set(manifest["files"]) == PAYLOAD, "Release manifest/gate/epoch mismatch")


def validate_before(api, desired: dict) -> None:
    pin = desired["before_receipt"]
    # Null means an explicitly requested first install, not a missing default.
    if pin is None:
        return
    receipt, _, _ = pinned(pin)
    gate.require(receipt.get("schema") == 1 and receipt.get("state") in
                 {"FILES_INSTALLED", "SERVICE_RESTARTED_UNVERIFIED"},
                 "Wrong/interrupted previous receipt")
    layout = api.Layout()
    target = layout.release(receipt.get("commit", ""), receipt.get("manifest_sha256", ""))
    gate.require(receipt.get("release") == str(target), "Previous receipt escaped release layout")
    api.validate_live(layout, receipt)


def closure_stream(path: Path, body: bytes) -> dict:
    """Keep actual empty streams too; the original gate writer forbids empties."""
    gate.external(path, gate.OUTPUT_ROOT)
    gate.require(type(body) is bytes and len(body) <= gate.LIMIT, "Closure stream missing/oversized")
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                           0o644), "wb") as stream:
        gate.require(stream.write(body) == len(body), "Closure stream short write")
        stream.flush()
        os.fsync(stream.fileno())
    gate.log_artifact({"path": str(path), "sha256": gate.sha(body)})
    gate.sync_directory(path.parent)
    return {"path": str(path), "sha256": gate.sha(body)}


def closure_stream_stamp(pin: dict) -> tuple:
    path = gate.external(Path(pin["path"]))
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        before = os.fstat(stream.fileno())
        gate.require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
                     "Unsafe closure stream")
        body = stream.read(gate.LIMIT + 1)
        after = os.fstat(stream.fileno())
    def stamp(value):
        return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
                value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    gate.require(stamp(before) == stamp(after) == stamp(path.lstat()) and
                 len(body) <= gate.LIMIT and gate.sha(body) == pin["sha256"],
                 "Closure stream identity/bytes drift")
    return stamp(after)


def closure_execution(root: Path, desired: dict, directory: Path, context: dict, guards: list) -> dict:
    """Use the actual candidate-pinned gate, never an editable certification receipt."""
    closure, closure_path, closure_raw = pinned(desired["closure_request"])
    identity = gate.identity(root, desired["commit"])
    executing = Path(gate.__file__).resolve()
    tool_bytes = gate.read_file(executing)
    gate.require(tool_bytes == gate.committed(root, desired["commit"], "tools/release_gate.py"),
                 "Executing closure gate differs from candidate")
    gate.require(gate.read_file(Path(__file__).resolve()) ==
                 gate.committed(root, desired["commit"], "tools/sync_release.py"),
                 "Executing closure sync differs from candidate")
    raw_stamp = gate._audit_file(closure_path, 1)
    gate.require(raw_stamp[0] == closure_raw, "Closure request changed before dispatch")
    source_paths = (Path(__file__).resolve(), executing,
                    executing.with_name("release_obligations.py"))
    source_stamps = {path: gate._audit_file(path, 1, base=Path("/data/dev")) for path in source_paths}
    argv = [sys.executable, "-B", str(executing), "--source-root", str(root),
            "--commit", desired["commit"], "--closure-request", str(closure_path),
            "--out", str(directory / "gate.json")]
    approval = desired["approval_pin"]
    if approval is not None:
        gate.require(type(approval) is dict and set(approval) == {"path", "sha256"},
                     "Exact closure approval pin required")
        argv += ["--approval-path", approval["path"], "--approval-sha256", approval["sha256"]]
    directory.mkdir(exist_ok=False)
    execution = {"argv": list(argv), "actual_child_rc": None,
                 "scope": "ACTUAL_CLOSURE_PROCESS_NOT_RELEASE_AUTHORITY",
                 "candidate": identity, "request": desired["closure_request"],
                 "material_pins": {}, "source_before": {
                     str(path): gate.sha(record[0]) for path, record in source_stamps.items()},
                 "final_source_stable": False}
    context["closure_execution"] = execution
    child = subprocess.run(argv, cwd=root, capture_output=True,
                           env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                                "PYTHONDONTWRITEBYTECODE": "1"})
    execution["actual_child_rc"] = child.returncode
    stdout = closure_stream(directory / "stdout.bin", child.stdout)
    stderr = closure_stream(directory / "stderr.bin", child.stderr)
    report_path = directory / "gate.json"
    report_raw = gate.read_file(report_path)
    report = gate.decode(report_raw)
    report_pin = {"path": str(report_path), "sha256": gate.sha(report_raw)}
    observation = {"schema": "opensim-process-observation-v1", "argv": list(child.args),
                   "rc": child.returncode, "report": report_pin, "stdout": stdout, "stderr": stderr}
    gate.write_new(directory / "observation.json", gate.encode(observation))
    report_stamp = gate._audit_file(report_path, 1)
    log_path = gate.external(Path(report["execution_log"]["path"]))
    log_stamp = gate._audit_file(log_path, 1)
    stream_stamps = {name: closure_stream_stamp(pin) for name, pin in
                     (("stdout", stdout), ("stderr", stderr))}
    execution.update(observation=observation, report=report, execution_log=report.get("execution_log"),
                     stream_bytes={"stdout": len(child.stdout), "stderr": len(child.stderr)})
    original, _ = gate.reference(closure["original_ledger"])
    # This is a pin inventory, not another verifier. Original evaluator owns
    # all semantic checks and narrow native-audit hardlink admission.
    def pins(value):
        if isinstance(value, dict):
            if set(value) == {"path", "sha256"}:
                return [value]
            return [record for item in value.values() for record in pins(item)]
        if isinstance(value, list):
            return [record for item in value for record in pins(item)]
        return []
    execution["material_pins"] = {"closure_request": desired["closure_request"],
                                  "declared": pins(closure) + pins(original),
                                  "validated_measured": report.get("bridge", {}).get(
                                      "closure", {}).get("materials", [])}

    def verify_process():
        gate.require(type(child.returncode) is int and child.returncode in (0, 1),
                     "Unexpected closure gate process exit code")
        gate.require(gate._audit_file(report_path, 1) == report_stamp and
                     gate._audit_file(log_path, 1) == log_stamp and
                     all(closure_stream_stamp(observation[name]) == stamp
                         for name, stamp in stream_stamps.items()),
                     "Closure final report/log/stream identity drift")
        gate.require(type(report.get("exit_code")) is int and report["exit_code"] == child.returncode and
                     report.get("schema") == "opensim-release-gate-v1" and
                     report.get("finalization") == "FINAL_PROCESS_RC_REQUIRED",
                     "Closure process rc/final report disagreement")
        gate.require(not os.path.lexists(report_path.with_name("report.failure.json")) and
                     not os.path.lexists(report_path.with_suffix(".failure.json")),
                     "Closure failure sidecar present")
        if child.returncode == 0:
            gate.verify_process_result(observation, "OPENSIM_RELEASE_GATE", child)
            gate.require(report.get("verdict") == "PASS" and
                         report.get("release_verdict") in {"CERTIFIED", "APPROVED_EXCEPTION"} and
                         report.get("scope") == "EVIDENCE_VALIDATED_NOT_DEPLOYED",
                         "Automated/test-only closure is not certification")
        else:
            identifier = report.get("invocation_id")
            gate.require(type(identifier) is str and re.fullmatch(r"[a-f0-9]{32}", identifier) and
                         report.get("verdict") == "BLOCKED" and
                         report.get("release_verdict") == "NOT_CERTIFIED",
                         "Invalid blocked closure final result")
            expected_line = (
                f"OPENSIM_RELEASE_GATE verdict=BLOCKED rc=1 finalization=FINAL_PROCESS_RC_REQUIRED "
                f"invocation_id={identifier} report_sha256={report_pin['sha256']} "
                f"collected_at={report['collected_at']} report={report_path}\n").encode()
            gate.require(child.stdout == expected_line, "Blocked closure final marker/report mismatch")
            reason = report.get("failure", {}).get("reason")
            gate.require(type(reason) is str and bool(reason) and
                         child.stderr == ("First cause: " + reason + "\n").encode(),
                         "Blocked closure stderr/first cause mismatch")
            log, _ = gate.artifact(report["execution_log"])
            expected_log = (f"OPENSIM_RELEASE_GATE action=BLOCKED action_rc=1 "
                            f"finalization=PROVISIONAL invocation_id={identifier}\n{reason}\n").encode()
            gate.require(log == expected_log, "Blocked closure provisional log mismatch")
        gate.require(gate.identity(root, desired["commit"]) == identity and
                     gate._audit_file(closure_path, 1) == raw_stamp and
                     all(gate._audit_file(path, 1, base=Path("/data/dev")) == stamp
                         for path, stamp in source_stamps.items()),
                     "Closure final source/request drift")
        execution["source_after"] = {str(path): gate.sha(gate.read_file(path)) for path in source_paths}
        execution["final_source_stable"] = True

    verify_process()
    guards.append(verify_process)
    # Reuse the original final readback algorithm. Its publication guards also
    # cover complete measured/provenance/original dependencies when reachable.
    fresh_guards = []
    try:
        verified = gate.evaluate_closure(root, desired["commit"], closure_path, approval,
                                         publication_guards=fresh_guards)
    except Exception as first:
        gate.require(child.returncode == 1 and
                     report.get("failure") == {"type": type(first).__name__, "reason": str(first)},
                     f"Closure process/fresh evaluator first cause changed: {first}")
        guards.extend(fresh_guards)
        context.update(state="BLOCKED", release_verdict="NOT_CERTIFIED")
        raise
    guards.extend(fresh_guards)
    gate.require(child.returncode == 0 and
                 all(report.get(key) == verified.get(key) for key in (
                     "identity", "ledger_sha256", "approval_pin", "exceptions", "gates",
                     "evidence", "release_verdict", "scope", "closure")),
                 "Closure process/fresh certified evidence differs")
    return verified


def plan(root: Path, desired_path: Path, desired_sha: str, api_path: Path | None = None,
         *, closure_directory: Path | None = None, context: dict | None = None,
         publication_guards: list | None = None) -> dict:
    gate.require(os.geteuid() != 0, "Sync must run as infra, never root")
    desired, raw = request(desired_path, desired_sha)
    if desired["schema"] == "opensim-desired-release-v2":
        gate.require(closure_directory is not None and context is not None and publication_guards is not None,
                     "Closure plan requires actual process evidence output")
        verified = closure_execution(root, desired, closure_directory, context, publication_guards)
        closure, _, _ = pinned(desired["closure_request"])
        ledger, _, _ = pinned(closure["original_ledger"])
        receipt = verified
    else:
        return legacy_plan(root, desired_path, desired_sha, api_path)
    return fixed_plan(root, desired_path, desired_sha, api_path, desired, raw, ledger, receipt, verified)


def legacy_plan(root: Path, desired_path: Path, desired_sha: str, api_path: Path | None = None) -> dict:
    desired, raw = request(desired_path, desired_sha)
    ledger, ledger_path, _ = pinned(desired["ledger"])
    receipt, _, _ = pinned(desired["gate_receipt"])
    gate.require(receipt.get("schema") == "opensim-release-gate-v1" and
                 receipt.get("verdict") == "PASS" and receipt.get("exit_code") == 0 and
                 receipt.get("release_verdict") in {"CERTIFIED", "APPROVED_EXCEPTION"} and
                 receipt.get("scope") == "EVIDENCE_VALIDATED_NOT_DEPLOYED",
                 "Wrong release-gate receipt; automated PASS is not certification")
    gate.require(receipt.get("ledger_sha256") == desired["ledger"]["sha256"] and
                 receipt.get("identity", {}).get("commit") == desired["commit"],
                 "Gate receipt does not bind this desired ledger/release")
    verified = gate.evaluate(root, desired["commit"], ledger_path, desired["approval_pin"])
    gate.require(receipt.get("identity") == verified["identity"] and
                 receipt.get("ledger_sha256") == desired["ledger"]["sha256"] and
                 receipt.get("approval_pin") == verified["approval_pin"] and
                 receipt.get("exceptions") == verified["exceptions"] and
                 receipt.get("gates") == verified["gates"] and
                 receipt.get("evidence") == verified["evidence"] and
                 receipt.get("release_verdict") == verified["release_verdict"],
                 "Gate receipt does not bind this desired ledger/release")
    gate.fresh(receipt, datetime.now(timezone.utc), 600)
    return fixed_plan(root, desired_path, desired_sha, api_path, desired, raw, ledger, receipt, verified)


def fixed_plan(root, desired_path, desired_sha, api_path, desired, raw, ledger, receipt, verified):
    api = load_api(api_path or root / "tools/release_app.py",
                   verified["identity"]["tool_sha256"]["tools/release_app.py"])
    bundle = prepare_output_guard(api, Path(desired["bundle"]))
    manifest = api.validate_bundle(bundle.resolve(), desired["manifest_sha256"], desired["commit"])
    match_manifest(manifest, desired, verified["identity"])
    epoch = int(gate.git(root, "show", "-s", "--format=%ct", desired["commit"]).strip())
    gate.require(epoch == desired["source_epoch"], "source_epoch must equal actual commit time")
    target = api.Layout().release(desired["commit"], desired["manifest_sha256"])
    gate.require(desired["release"] == str(target), "Desired release escaped fixed layout")
    validate_before(api, desired)
    gate.require(manifest["files"]["evidence/numerics.json"]["sha256"] ==
                 ledger["evidence"]["numerics"]["sha256"], "Bundle numerics differ from gated evidence")
    page_check = gate.decode(gate.read_file(bundle / "build/page-check.json"))
    gate.require(gate.time_value(page_check.get("edited_at")).timestamp() == epoch,
                 "Bundle edited_at must honestly represent committed source epoch")
    gate.require(gate.identity(root, desired["commit"]) == verified["identity"] and
                 gate.read_file(desired_path) == raw, "Desired/source drift during plan")
    api.validate_bundle(bundle.resolve(), desired["manifest_sha256"], desired["commit"])
    return {"schema": "opensim-sync-result-v1", "state": "FIXED_RELEASE_PLAN",
            "scope": "VALIDATED_PLAN_NOT_DEPLOYED", "request_id": desired["request_id"],
            "request_sha256": desired_sha, "commit": desired["commit"],
            "manifest_sha256": desired["manifest_sha256"], "release": str(target),
            "page_build": manifest["page_build"], "source_epoch": epoch,
            **({"gate_receipt": desired["gate_receipt"]} if desired["schema"] == "opensim-desired-release-v1"
               else {"closure_request": desired["closure_request"]}),
            "production_changes": False,
            "controller": "NOT_AUTHORIZED", "collected_at": datetime.now(timezone.utc).isoformat()}


def apply_guard() -> None:
    gate.require(os.geteuid() != 0, "Sync must never run as root")
    if not CONTROLLER.is_file():
        raise Refused(f"Missing authorized root controller: {CONTROLLER}")
    raise Refused("Root controller authorization/protocol not accepted; no broker or sudo dispatch")


def prepare_output_guard(api, bundle: Path) -> Path:
    """Only bundles use the explicitly authorized, fixed peer artifact root."""
    gate.require(isinstance(api.ARTIFACTS, Path) and api.ARTIFACTS == PREPARE_ROOT,
                 "REFUSED: prepare API output root differs from fixed allowed seat-release root")
    gate.external(bundle, PREPARE_ROOT)
    resolved = api.external(bundle)
    gate.require(resolved == bundle.resolve(), "Prepare API physical output root drift")
    return resolved


def prepare_guard(root: Path, commit: str, ledger_path: Path, bundle: Path,
                  approval_pin: dict | None = None, api_path: Path | None = None) -> dict:
    """Gate first; peer bundle root is separate from seat-sync result output."""
    raw = gate.read_file(gate.external(ledger_path))
    result = gate.evaluate(root, commit, ledger_path, approval_pin)
    gate.require(result["ledger_sha256"] == gate.sha(raw) and
                 gate.read_file(ledger_path) == raw, "Ledger changed during gated prepare")
    api = load_api(api_path or root / "tools/release_app.py",
                   result["identity"]["tool_sha256"]["tools/release_app.py"])
    prepare_output_guard(api, bundle)
    ledger = gate.decode(raw)
    evidence = gate.reference(ledger["evidence"]["numerics"])[1]
    gate.require(gate.read_file(ledger_path) == raw, "Ledger changed during gated prepare")
    prepared = api.prepare(root, commit, evidence, bundle)
    gate.require(set(prepared) == {"state", "scope", "bundle", "manifest_sha256", "commit", "page_build"}
                 and prepared["state"] == "BUNDLE_PREPARED" and
                 prepared["scope"] == "SOURCE_AND_NUMERICS_ONLY" and
                 Path(prepared["bundle"]).resolve() == bundle.resolve() and
                 prepared["commit"] == commit and prepared["page_build"] == result["identity"]["page_build"],
                 "Prepare receipt does not match precise release API")
    manifest = api.validate_bundle(Path(prepared["bundle"]), prepared["manifest_sha256"], commit)
    epoch = int(gate.git(root, "show", "-s", "--format=%ct", commit).strip())
    match_manifest(manifest, {"commit": commit, "source_epoch": epoch}, result["identity"])
    gate.require(manifest["files"]["evidence/numerics.json"]["sha256"] ==
                 ledger["evidence"]["numerics"]["sha256"],
                 "Bundle numerics differ from gated evidence")
    gate.require(gate.read_file(ledger_path) == raw, "Ledger changed during gated prepare")
    gate.require(gate.identity(root, commit) == result["identity"],
                 "Sources changed during gated prepare")
    return prepared


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desired", type=Path, required=True)
    parser.add_argument("--desired-sha256", required=True)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--release-api", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        gate.new_result_paths(args.out)
    except Exception as exc:
        print(f"Output admission refused: {exc}", file=sys.stderr)
        return 1
    report = {"schema": "opensim-sync-result-v1", "state": "REFUSED",
              "scope": "NOT_DEPLOYED", "production_changes": False,
              "collected_at": datetime.now(timezone.utc).isoformat()}
    guards = []
    try:
        # Parse the exact request before either refusal or gate evaluation.
        desired, desired_raw = request(args.desired, args.desired_sha256)
        if args.apply:
            apply_guard()
        if desired["schema"] == "opensim-desired-release-v2":
            stamp = gate._audit_file(args.desired, 1)
            def verify_desired():
                gate.require(gate._audit_file(args.desired, 1) == stamp and stamp[0] == desired_raw,
                             "Desired closure request drift during plan")
            guards.append(verify_desired)
        result = plan(args.source_root, args.desired, args.desired_sha256, args.release_api,
                      closure_directory=args.out.parent / ("closure-gate-" + uuid4().hex),
                      context=report, publication_guards=guards)
        report.update(result)
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
    rc = 0 if report["state"] == "FIXED_RELEASE_PLAN" else 1
    report["exit_code"] = rc
    try:
        return gate.finalize_result(report, args.out, status_key="state",
                                    failed_status="REFUSED", label="OPENSIM_SYNC_RELEASE",
                                    verify=(lambda: [guard() for guard in guards]) if guards else None,
                                    emit=gate.native_assessment_stdout if guards else None)
    except Exception as exc:
        print(f"Sync finalization refused: {exc}; first result={report}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
