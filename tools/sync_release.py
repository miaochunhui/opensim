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
    gate.require(set(value) == REQUEST_FIELDS and value["schema"] == "opensim-desired-release-v1",
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


def plan(root: Path, desired_path: Path, desired_sha: str, api_path: Path | None = None) -> dict:
    gate.require(os.geteuid() != 0, "Sync must run as infra, never root")
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
            "gate_receipt": desired["gate_receipt"], "production_changes": False,
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
    result = gate.evaluate(root, commit, ledger_path, approval_pin)
    api = load_api(api_path or root / "tools/release_app.py",
                   result["identity"]["tool_sha256"]["tools/release_app.py"])
    prepare_output_guard(api, bundle)
    ledger = gate.decode(gate.read_file(ledger_path))
    evidence = gate.reference(ledger["evidence"]["numerics"])[1]
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
    try:
        # Parse the exact request before either refusal or gate evaluation.
        request(args.desired, args.desired_sha256)
        if args.apply:
            apply_guard()
        report = plan(args.source_root, args.desired, args.desired_sha256, args.release_api)
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
    rc = 0 if report["state"] == "FIXED_RELEASE_PLAN" else 1
    report["exit_code"] = rc
    try:
        return gate.finalize_result(report, args.out, status_key="state",
                                    failed_status="REFUSED", label="OPENSIM_SYNC_RELEASE")
    except Exception as exc:
        print(f"Sync finalization refused: {exc}; first result={report}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
