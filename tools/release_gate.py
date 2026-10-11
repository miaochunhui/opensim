"""Fail-closed shared-r41 evidence gate. This tool never deploys or approves.

The ledger pins actual external reports; it is not a replacement for running
their producers. Editable user-approval JSON alone never authorizes an exception.
Filesystem test fixtures prove gate behaviour, never real release acceptance.
"""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import subprocess
import sys
import types
import zlib

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1")
OUTPUT_ROOT = ARTIFACT_ROOT / "seat-sync"
PUBLIC_URL = "https://opensim.ai4everyone.top"
SOURCES = ("tools/render_page.py", "web/app.js", "web/app.css", "solver.py", "server.py")
TOOLS = (
    "tools/acceptance.py", "tools/validate_numerics.py", "tools/render_page.py",
    "tools/browser_check.mjs", "tools/browser_audit.mjs",
    "tools/browser_performance.mjs", "tools/public_gate.py",
    "tools/release_app.py", "tools/release_gate.py", "tools/sync_release.py",
    "tools/workbench_acceptance.py",
)
GATES = ("G1-structure", "G2-layout", "G3-accessibility", "G4-keyboard",
         "G5-performance", "G6-content")
MANUAL = ("PHYSICAL-TOUCH", "SOFT-KEYBOARD", "SCREEN-READER-DESKTOP",
          "SCREEN-READER-MOBILE", "FAULT-ISOLATION", "SCREENSHOT-REVIEW")
REQUIRED_RULES = {
    "G1-structure": {"WEB-BASE-001", "WEB-BASE-002", "WEB-BASE-003", "WEB-BASE-005",
                     "WEB-ADAPT-001", "WEB-ADAPT-002", "WEB-NAV-001", "WEB-NAV-002",
                     "WEB-INPUT-003", "WEB-A11Y-006", "WEB-A11Y-007"},
    "G2-layout": {"WEB-BASE-004", "WEB-RWD-001", "WEB-RWD-002", "WEB-RWD-003",
                  "WEB-RWD-005", "WEB-ADAPT-003", "WEB-ADAPT-004", "WEB-ADAPT-005",
                  "WEB-ADAPT-006", "WEB-TYPE-001", "WEB-TYPE-002", "WEB-TYPE-003",
                  "WEB-TYPE-004", "WEB-A11Y-004"},
    "G3-accessibility": {"WEB-A11Y-001", "WEB-A11Y-002", "WEB-A11Y-005",
                         "WEB-A11Y-006", "WEB-A11Y-007", "WEB-A11Y-008", "WEB-INPUT-001",
                         "WEB-INPUT-002", "WEB-INPUT-003", "WEB-INPUT-004", "WEB-INPUT-005"},
    "G4-keyboard": {"WEB-A11Y-003", "WEB-A11Y-004", "WEB-A11Y-008", "WEB-NAV-004",
                    "WEB-NAV-005", "WEB-INPUT-006", "WEB-MOTION-001", "WEB-MOTION-002",
                    "WEB-MOTION-003"},
    "G5-performance": {f"WEB-PERF-{i:03}" for i in range(1, 7)},
    "G6-content": {"WEB-TYPE-005", "WEB-TYPE-006", "WEB-CONTENT-001", "WEB-CONTENT-002",
                   "WEB-CONTENT-003", "WEB-CONTENT-004", "WEB-CONTENT-005",
                   "WEB-CONTENT-006", "WEB-CONTENT-007", "WEB-INPUT-004",
                   "WEB-INPUT-005", "WEB-ADAPT-002"},
}
VIEWPORTS = ((320, 568), (360, 800), (390, 844), (768, 1024),
             (1024, 768), (1064, 768), (1280, 800), (1440, 900))
LIMIT = 16_000_000
BRIDGE_IMPLEMENTATIONS = {
    "release_obligations.py": {
        "path": "/data/dev/opensim-authorized-obligations-20261010/tools/release_obligations.py",
        "sha256": "9c1b0d9a464c958d6d32c760ba39173da6134ad771436ddc7a36466ab3a8b4bf",
    },
    "release_trust.py": {
        "path": "/data/dev/opensim-authorized-trust-20261010/tools/release_trust.py",
        "sha256": "ce085ad4062327c6d95b6d274f66897fbb2440ab2170404dd03b07986bd59639",
    },
}
NATIVE_ASSESSMENT_SCHEMA = "opensim-release-native-assessment-request-v1"
CLOSURE_TRUST_IMPLEMENTATION = {
    "path": "/data/dev/opensim-production-trust-20261011/tools/release_trust.py",
    "sha256": "1bc76e30b0a80a92efa555b014c70afb237a178b71f6311c55f1059f588358af",
}
NATIVE_ADAPTER = {
    "path": "/data/dev/opensim-release-native-adapter-main-v1/tools/release_native_adapter.py",
    "sha256": "4c998f3d0587a4d09064ced6a76171da37e95cb8bddd24c4d912416fe95b5b00",
}


class GateError(RuntimeError):
    pass


class BridgeBlocked(GateError):
    def __init__(self, reason: str, observation: dict):
        super().__init__(reason)
        self.observation = observation


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise GateError(reason)


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def encode(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("ascii")


def decode(body: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def nonfinite(value):
        raise GateError(f"Nonfinite JSON: {value}")

    value = json.loads(body, object_pairs_hook=unique, parse_constant=nonfinite)
    require(isinstance(value, dict) and bool(value), "Empty/non-object evidence")
    encode(value)
    return value


def external(path: Path, base: Path = ARTIFACT_ROOT) -> Path:
    require(path.is_absolute() and ".." not in path.parts, "Absolute external path required")
    entry = base if path.is_relative_to(base) else base.resolve()
    require(path.is_relative_to(entry) and path != entry, f"Path outside evidence root: {path}")
    # The host's mount alias is trusted, but no descendant symlink is.
    cursor = entry
    for part in path.relative_to(entry).parts:
        cursor /= part
        require(not cursor.is_symlink(), f"Evidence symlink rejected: {cursor}")
    require(path.resolve().is_relative_to(base.resolve()), "Evidence escaped physical root")
    return path


def read_file(path: Path) -> bytes:
    require(path.is_absolute(), "Absolute file path required")
    require(not path.is_symlink(), f"Symlink file rejected: {path}")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                f"Nonregular/hardlinked evidence: {path}")
        body = stream.read(LIMIT + 1)
    require(0 < len(body) <= LIMIT, f"Empty/oversized evidence: {path}")
    return body


def write_new(path: Path, body: bytes, *, owned: dict | None = None) -> None:
    external(path, OUTPUT_ROOT)
    require(bool(body), "Empty output rejected")
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                           0o644), "wb") as stream:
        if owned is not None:
            info = os.fstat(stream.fileno())
            owned[path] = (info.st_dev, info.st_ino)
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    require(read_file(path) == body, f"Output readback differs: {path}")
    sync_directory(path.parent)


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def new_result_paths(out: Path) -> None:
    external(out, OUTPUT_ROOT)
    require(out.suffix == ".json", "Result output must have .json suffix")
    for path in (out, out.with_suffix(".log")):
        require(not os.path.lexists(path), f"Existing output protected: {path}")


def revoke_exact(path: Path, body: bytes, owned: dict) -> None:
    """Revoke only this invocation's exact bytes and inode, never old output."""
    require(path in owned, f"No ownership claim for evidence revocation: {path}")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                (info.st_dev, info.st_ino) == owned[path] and
                stream.read(LIMIT + 1) == body, f"Exact evidence revocation drift: {path}")
        current = path.lstat()
        require((current.st_dev, current.st_ino) == owned[path] and current.st_nlink == 1,
                f"Evidence replaced before revocation: {path}")
        path.unlink()
    sync_directory(path.parent)


def finalize_result(report: dict, out: Path, *, status_key: str,
                    failed_status: str, label: str, verify=None, emit=None) -> int:
    """Action results are provisional until log/report readback and process rc."""
    from uuid import uuid4

    new_result_paths(out)
    if emit is None:
        def emit(line):
            print(line, flush=True)
    original = dict(report)
    report = dict(report, invocation_id=uuid4().hex,
                  finalization="FINAL_PROCESS_RC_REQUIRED")
    owned: dict = {}
    log = out.with_suffix(".log")
    cause = (original.get("failure", {}).get("reason") or
             (original.get("first_cause") or {}).get("message", ""))
    log_body = (
        f"{label} action={report[status_key]} action_rc={report['exit_code']} "
        f"finalization=PROVISIONAL invocation_id={report['invocation_id']}\n" +
        cause + "\n"
    ).encode("utf-8")
    attempted_body = None
    failed_path = log
    try:
        write_new(log, log_body, owned=owned)
        report["execution_log"] = {"path": str(log), "sha256": sha(log_body)}
        if verify is not None:
            verify()
        failed_path = out
        attempted_body = encode(report)
        write_new(out, attempted_body, owned=owned)
        require(read_file(log) == log_body and read_file(out) == attempted_body,
                "Final result/log drift before process completion")
        if verify is not None:
            verify()
        emit(f"{label} {status_key}={report[status_key]} rc={report['exit_code']} "
             f"finalization=FINAL_PROCESS_RC_REQUIRED invocation_id={report['invocation_id']} "
             f"report_sha256={sha(attempted_body)} collected_at={report['collected_at']} report={out}")
    except Exception as exc:
        evidence_failure = {"type": type(exc).__name__, "reason": str(exc),
                            "errno": getattr(exc, "errno", None), "path": str(failed_path)}
        report.update({status_key: failed_status, "exit_code": 1, "finalization": "FAILED",
                       "action_result": original, "evidence_failure": evidence_failure})
        if not original.get("failure"):
            report["failure"] = evidence_failure
        if "release_verdict" in report:
            report["release_verdict"] = "NOT_CERTIFIED"
        # A post-write fsync/readback failure can leave a complete success JSON.
        # An O_EXCL failure never establishes ownership, even for identical bytes.
        if out in owned and attempted_body is not None:
            try:
                revoke_exact(out, attempted_body, owned)
            except Exception as recovery:
                print(f"Exact evidence revocation failed: {recovery}; "
                      "reject all artifacts from this failed process", file=sys.stderr)
        if not os.path.lexists(out):
            try:
                write_new(out, encode(report), owned=owned)
            except Exception as recovery:
                print(f"Failure report persistence failed: {recovery}", file=sys.stderr)
        print(f"Evidence cause: {exc}", file=sys.stderr)
        if cause or report.get("failure"):
            print("First cause: " + (cause or report["failure"]["reason"]), file=sys.stderr)
        try:
            emit(f"{label} {status_key}={failed_status} rc=1 finalization=FAILED "
                 f"invocation_id={report['invocation_id']} report={out}")
        except Exception as recovery:
            print(f"Failure marker output failed: {recovery}", file=sys.stderr)
        return 1
    if cause:
        print("First cause: " + cause, file=sys.stderr)
    return report["exit_code"]


def verify_process_result(observation: dict, label: str,
                          process: subprocess.CompletedProcess) -> dict:
    """Bind launcher-observed rc to final bytes; this is never user approval."""
    require(set(observation) == {"schema", "argv", "rc", "report", "stdout", "stderr"} and
            observation["schema"] == "opensim-process-observation-v1" and
            type(observation["rc"]) is int, "Missing actual process observation")
    require(isinstance(observation["argv"], list) and observation["argv"] and
            all(isinstance(item, str) for item in observation["argv"]),
            "Missing actual process argv")
    require(isinstance(process, subprocess.CompletedProcess) and
            observation["argv"] == process.args and observation["rc"] == process.returncode,
            "Actual launcher argv/rc differs from observation")
    report, _ = reference(observation["report"])
    stdout, _ = artifact(observation["stdout"])
    log_artifact(observation["stderr"])
    actual_stdout = process.stdout.encode("utf-8") if isinstance(process.stdout, str) else process.stdout
    actual_stderr = process.stderr.encode("utf-8") if isinstance(process.stderr, str) else process.stderr
    require(isinstance(actual_stdout, bytes) and isinstance(actual_stderr, bytes) and
            stdout == actual_stdout and sha(actual_stderr) == observation["stderr"]["sha256"],
            "Actual launcher stdout/stderr differs from artifacts")
    require(type(report.get("exit_code")) is int and
            report["exit_code"] == observation["rc"] == 0 and
            report.get("finalization") == "FINAL_PROCESS_RC_REQUIRED",
            "Process rc/final report disagreement or unsuccessful finalization")
    identifier = report.get("invocation_id")
    require(isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{32}", identifier),
            "Missing invocation identity")
    log_body, _ = artifact(report.get("execution_log"))
    require(f"invocation_id={identifier}".encode() in log_body and
            b"finalization=PROVISIONAL" in log_body, "Result/log invocation drift")
    lines = stdout.decode("utf-8").splitlines()
    require(any(line.startswith(label + " ") and "rc=0 " in line and
                f"invocation_id={identifier} " in line and
                f"report_sha256={observation['report']['sha256']} " in line and
                "finalization=FINAL_PROCESS_RC_REQUIRED " in line for line in lines),
            "Missing exact process final marker/report hash")
    return report


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["/usr/bin/git", "-c", "core.fsmonitor=false", *args], cwd=root,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "GIT_OPTIONAL_LOCKS": "0"},
        capture_output=True,
    )
    require(result.returncode == 0,
            f"git rc={result.returncode} args={args!r}: {result.stderr.decode(errors='replace')}")
    return result.stdout


def relative(name: str) -> str:
    require(isinstance(name, str) and bool(name), "Empty relative file name")
    path = PurePosixPath(name)
    require(not path.is_absolute() and ".." not in path.parts and str(path) == name,
            f"Unsafe relative file name: {name}")
    return name


def committed(root: Path, commit: str, name: str) -> bytes:
    relative(name)
    record = git(root, "ls-tree", commit, "--", name).decode().split()
    require(len(record) == 4 and record[0:2] == ["100644", "blob"],
            f"Required committed regular file: {name}")
    cursor = root
    for part in PurePosixPath(name).parts:
        cursor /= part
        require(not cursor.is_symlink(), f"Source symlink rejected: {cursor}")
    body = git(root, "cat-file", "blob", record[2])
    require(read_file(cursor) == body, f"Worktree bytes differ from commit: {name}")
    return body


def identity(root: Path, commit: str) -> dict:
    require(re.fullmatch(r"[a-f0-9]{40}", commit or "") is not None, "Full clean commit required")
    require(root.is_absolute() and root.is_dir() and not root.is_symlink(), "Invalid source root")
    require(Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() == root.resolve(),
            "Source root must be checkout root")
    require(git(root, "rev-parse", "HEAD").decode().strip() == commit, "HEAD/commit mismatch")
    require(not git(root, "status", "--porcelain=v1", "--untracked-files=all",
                    "--ignored=matching").strip(),
            "Dirty checkout (including untracked files)")
    require(all(row.startswith(b"H ") for row in git(root, "ls-files", "-v", "-z").split(b"\0") if row),
            "Skip-worktree/assume-unchanged files cannot prove clean commit")
    bodies = {name: committed(root, commit, name) for name in dict.fromkeys((*SOURCES, *TOOLS))}
    fingerprint = hashlib.sha256()
    for name in SOURCES:
        fingerprint.update(name.encode("ascii") + b"\0" + bodies[name] + b"\0")
    return {"commit": commit, "source_sha256": {name: sha(bodies[name]) for name in SOURCES},
            "page_build": fingerprint.hexdigest()[:12],
            "tool_sha256": {name: sha(bodies[name]) for name in TOOLS}}


def time_value(value: str) -> datetime:
    require(isinstance(value, str), "Timezone timestamp required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "Naive timestamp rejected")
    return result


def fresh(report: dict, now: datetime, seconds: int = 86400) -> None:
    collected = report.get("finished_at") or report.get("collected_at")
    age = (now - time_value(collected)).total_seconds()
    require(0 <= age <= seconds, f"Stale/future evidence: {collected}")


def reference(record: dict) -> tuple[dict, Path]:
    require(isinstance(record, dict) and set(record) == {"path", "sha256"},
            "Evidence reference requires exactly path/sha256")
    require(re.fullmatch(r"[0-9a-f]{64}", record["sha256"] or "") is not None,
            "Full evidence SHA256 required")
    path = external(Path(record["path"]))
    body = read_file(path)
    require(sha(body) == record["sha256"], f"Evidence hash drift: {path}")
    return decode(body), path


def artifact(record: dict) -> tuple[bytes, Path]:
    require(isinstance(record, dict) and set(record) == {"path", "sha256"},
            "Actual artifact requires path/sha256")
    path = external(Path(record["path"]))
    body = read_file(path)
    require(re.fullmatch(r"[a-f0-9]{64}", record["sha256"] or "") and
            sha(body) == record["sha256"], f"Artifact hash drift: {path}")
    return body, path


def log_artifact(record: dict) -> None:
    require(isinstance(record, dict) and set(record) == {"path", "sha256"},
            "Actual launcher log requires path/sha256")
    path = external(Path(record["path"]))
    require(path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1,
            "Launcher log must be a real regular file")
    # Empty stderr is legitimate; its actual file and hash remain mandatory.
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Unsafe launcher log")
        body = stream.read(LIMIT + 1)
    require(len(body) <= LIMIT and sha(body) == record["sha256"], "Launcher log hash drift")


def check_rows(rows: object, label: str) -> list[dict]:
    require(isinstance(rows, list) and bool(rows), f"Empty checks: {label}")
    require(all(isinstance(row, dict) and
                (row.get("status") or row.get("verdict")) == "PASS" for row in rows),
            f"Incomplete/failed checks: {label}")
    return rows


def screenshots(report: dict, path: Path) -> None:
    rows = report.get("screenshots")
    require(isinstance(rows, list) and bool(rows), "Missing actual screenshot evidence")
    for row in rows:
        require(isinstance(row, dict) and re.fullmatch(r"[a-f0-9]{64}", row.get("sha256", "")),
                "Missing screenshot hash")
        name = relative(row.get("file", ""))
        body = read_file(external(path.parent / name))
        require(sha(body) == row["sha256"] and body.startswith(b"\x89PNG\r\n\x1a\n"),
                f"Missing/corrupt PNG: {name}")
        require(len(body) > 32 and body[12:16] == b"IHDR" and
                int.from_bytes(body[16:20], "big") > 0 and
                int.from_bytes(body[20:24], "big") > 0 and b"IEND" in body[-12:],
                f"Empty/truncated screenshot PNG: {name}")


def binding(report: dict, expected: dict, tool: str, now: datetime) -> None:
    require(report.get("commit") == expected["commit"], f"Report commit mismatch: {tool}")
    require(report.get("tool_sha256") == expected["tool_sha256"][tool],
            f"Old/unknown tool hash: {tool}")
    sources = report.get("source_sha256")
    # Native performance producer binds health, committed source and solve separately.
    if tool == "tools/browser_performance.mjs":
        sample = report.get("samples", [{}])[0]
        provenance = sample.get("source_identity", {})
        require(provenance.get("bound") is True and provenance.get("assets_bound") is True,
                "Performance identity not bound")
        for key in ("local_source_sha256", "committed_source_sha256", "solve_source_sha256"):
            require(provenance.get(key) == expected["source_sha256"], f"Performance {key} differs")
        health = provenance.get("health", {})
        sources = health.get("source_sha256")
        page = health.get("page_build")
    elif tool == "tools/browser_check.mjs":
        page_record = report.get("page_build", {})
        require(isinstance(page_record, dict), "Browser page_build must contain DOM observation")
        text = page_record.get("build_text", "")
        require(isinstance(text, str) and
                re.search(rf"(?<![a-f0-9]){expected['page_build']}(?![a-f0-9])", text),
                "Browser DOM build_text differs from five-source byte build")
        require(time_value(page_record.get("edited_at")).utcoffset().total_seconds() == 28800,
                "Browser edited_at must preserve precise +08:00 source timestamp")
        if sources is None:
            # Native browser_check records the five-source fingerprint in the DOM.
            sources = expected["source_sha256"]
        page = expected["page_build"]
    elif tool == "tools/browser_audit.mjs":
        page_record = report.get("page_build")
        require(isinstance(page_record, dict) and set(page_record) == {"id", "footer"},
                "Audit page_build must contain native id/footer")
        page = page_record["id"]
        require(isinstance(page_record["footer"], str) and
                re.search(r"(?<![a-f0-9]){}(?![a-f0-9])".format(expected["page_build"]),
                          page_record["footer"]), "Audit observed footer/build differs")
    else:
        page = report.get("page_build")
    require(sources == expected["source_sha256"], f"Report must bind all five sources: {tool}")
    require(page == expected["page_build"], f"Report page_build mismatch: {tool}")
    fresh(report, now)


def audit_coordinates(row: dict, report: dict) -> dict:
    """Read native coordinates without adding fields to the producer report."""
    url, viewport, steps = row.get("url"), row.get("viewport"), row.get("reproduce")
    require(isinstance(url, str) and url == report.get("url"), "Audit check URL differs")
    require(isinstance(viewport, dict) and set(viewport) == {"width", "height"} and
            all(type(viewport[k]) is int and viewport[k] > 0 for k in viewport),
            "Audit check missing native viewport")
    require(isinstance(steps, list) and steps and
            all(isinstance(step, str) and step.strip() for step in steps) and
            isinstance(row.get("state"), str) and row["state"].strip(),
            "Audit check missing native reproduction/state")
    return {"url": url, "viewport": dict(viewport), "state": row["state"], "reproduce": list(steps)}


def selected_checks(report: dict, chosen: object, kind: str) -> list[dict]:
    require(isinstance(chosen, list) and bool(chosen), "Empty/duplicate gate check selection")
    rows = report.get("checks", [])
    require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows),
            "Invalid producer check rows")
    if kind == "audit" and all(isinstance(pin, dict) for pin in chosen):
        indices, result = set(), []
        for pin in chosen:
            require(set(pin) == {"pointer", "sha256"} and
                    isinstance(pin["pointer"], str) and
                    re.fullmatch(r"/checks/(0|[1-9][0-9]*)", pin["pointer"]),
                    "Invalid native audit check pointer")
            index = int(pin["pointer"].rsplit("/", 1)[1])
            require(index < len(rows) and index not in indices, "Unknown/duplicate audit check pointer")
            row = rows[index]
            require(sha(encode(row)) == pin["sha256"], "Native audit check hash drift")
            audit_coordinates(row, report)
            indices.add(index)
            result.append(row)
        return result
    require(all(isinstance(name, str) and name for name in chosen) and
            len(set(chosen)) == len(chosen), "Empty/duplicate gate check selection")
    result = []
    for name in chosen:
        matches = [row for row in rows if row.get("name") == name]
        require(len(matches) == 1, "Unknown/ambiguous gate checks")
        result.append(matches[0])
    return result


def _audit_file(path: Path, links: int, *, base: Path = ARTIFACT_ROOT) -> tuple[bytes, tuple]:
    external(path, base)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == links,
                "Unsafe native audit file/link count")
        body = stream.read(LIMIT + 1)
        after = os.fstat(stream.fileno())
    def stamp(s):
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)
    require(0 < len(body) <= LIMIT and stamp(before) == stamp(after) == stamp(path.lstat()),
            "Native audit file changed during readback")
    return body, stamp(after)


def audit_reference(pin: dict) -> tuple[dict, Path]:
    """Only the native final/candidate pair may have two links; generic pins stay strict."""
    require(isinstance(pin, dict) and set(pin) == {"path", "sha256"}, "Invalid audit report pin")
    require(isinstance(pin["sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", pin["sha256"]),
            "Full audit SHA256 required")
    path = external(Path(pin["path"]))
    require(path.name == "report.json", "Native audit final filename required")
    require(not os.path.lexists(path.parent / "report.failure.json"), "Audit failure sidecar present")
    if path.lstat().st_nlink == 1:
        return reference(pin)
    body, stamp = _audit_file(path, 2)
    candidate, other = _audit_file(path.with_name("report.candidate.json"), 2)
    require(stamp == other and body == candidate and sha(body) == pin["sha256"],
            "Native audit final/candidate identity/hash mismatch")
    value = decode(body)
    completion = value.get("completion", {})
    require(completion.get("protocol") == "durable-final-v2" and completion.get("status") == "FINAL",
            "Native audit final/candidate protocol missing")
    return value, path


def audit_dependencies(report: dict, path: Path, expected: dict, source_root: Path) -> dict:
    """Reuse pinned workbench pure validators, never its runner or network entrypoints."""
    name = "tools/workbench_acceptance.py"
    require(source_root.is_absolute() and source_root.is_dir() and not source_root.is_symlink(),
            "Invalid explicit audit validator source root")
    source = source_root / name
    require(not (source_root / "tools").is_symlink(), "Audit validator ancestor symlink")
    body = read_file(source)
    require(sha(body) == expected.get("tool_sha256", {}).get(name),
            "Old/unknown pure audit validator hash")
    module = types.ModuleType("opensim_pinned_audit_validation")
    # Compile only the pinned pure predicates; no imports, runner or module-level actions.
    pure = {"EvidenceError", "need", "matching", "positive", "decode", "unique_object",
            "finite_tree", "validate_audit_payload", "validate_audit_coverage", "png_record"}
    tree = ast.parse(body, filename=str(source))
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
             and node.name in pure]
    require({node.name for node in nodes} == pure, "Pinned pure audit validator API missing")
    def producer_file(directory, relative_name, label):
        file = external(directory / relative(relative_name))
        data, stamp = _audit_file(file, 1)
        return data, {"path": str(file), "bytes": len(data), "sha256": sha(data),
                      "dev": str(stamp[0]), "ino": str(stamp[1]), "nlink": stamp[-1]}
    def file_record(file, directory):
        require(file.is_relative_to(directory), "Pure validator file escaped run")
        return producer_file(directory, str(file.relative_to(directory)), "audit")[1]
    module.__dict__.update(json=json, math=math, re=re, struct=struct, zlib=zlib,
                           datetime=datetime, Path=Path, producer_file=producer_file,
                           file_record=file_record)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), module.__dict__)
    directory, url = path.parent, report.get("url")
    matrix = report.get("matrix", [])
    viewports = {(320, 568), (768, 1024), (1064, 768), (1440, 900)}
    require(isinstance(matrix, list) and len(matrix) == 4 and
            all(isinstance(row, dict) for row in matrix) and
            {(row.get("width"), row.get("height")) for row in matrix} == viewports,
            "Incomplete native audit matrix")
    pictures = report.get("screenshots", [])
    require(isinstance(pictures, list) and pictures and all(isinstance(row, dict) for row in pictures),
            "Missing native audit screenshots")
    files = [relative(row.get("file", "")) for row in pictures]
    require(len(set(files)) == len(files), "Audit duplicate screenshot file")
    posts = sorted(directory.glob("http-solve-*.json"))
    covers = sorted(directory.glob("*.coverage.json"))
    dependencies = set(posts + covers + [directory / name for name in files])
    before = {file: _audit_file(external(file), 1) for file in dependencies}
    try:
        payloads = module.validate_audit_payload(report, directory, expected["source_sha256"], url)
        coverage = module.validate_audit_coverage(report, directory, url)
        pngs = [module.png_record(row, directory) for row in pictures]
    except module.EvidenceError as exc:
        raise GateError(f"Audit dependency {exc.coordinate}: {exc}") from exc
    by_file = {row["file"]: row for row in pictures}
    covered_tiles = set()
    for cover in covers:
        value = decode(before[cover][0])
        phase = cover.name.removesuffix(".coverage.json")
        main = by_file.get(phase + ".png", {})
        require(main.get("url") == url and main.get("state") == phase and
                (main.get("width"), main.get("height")) in viewports,
                "Audit coverage state/viewport differs")
        width, height = main["width"], main["height"]
        document, tiles = value.get("document", {}), value.get("tiles", [])
        require(value.get("status") == "PASS" and value.get("scale") == 1 and
                value.get("horizontal_coverage") == "COMPLETE" and
                type(document.get("height")) is int and document["height"] > 0 and
                type(document.get("width")) is int and 0 < document["width"] <= width and
                isinstance(tiles, list) and tiles, "Audit incomplete native tile coverage")
        covered = 0
        for index, tile in enumerate(tiles):
            name = f"{phase}-tile-{index:03}.png"
            picture = by_file.get(name, {})
            y = tile.get("document_y")
            require(tile.get("file") == name and name not in covered_tiles and
                    type(y) is int and 0 <= y <= covered and y <= max(0, document["height"] - height) and
                    tile.get("viewport") == {"width": width, "height": height} and
                    picture.get("state") == name.removesuffix(".png") and picture.get("url") == url and
                    (picture.get("width"), picture.get("height")) == (width, height),
                    "Audit tile inventory/state/coverage gap differs")
            covered_tiles.add(name)
            covered = max(covered, y + height)
        require(covered >= document["height"] and value.get("vertical_coverage") == covered,
                "Audit incomplete native vertical coverage")
    require(covered_tiles == {name for name in files if "-tile-" in name},
            "Audit tile set differs from coverage inventory")
    for row in pictures:
        require(row.get("url") == url and row.get("state") == row["file"].removesuffix(".png") and
                (row.get("width"), row.get("height")) in viewports,
                "Audit screenshot URL/state/viewport differs")
    require(set(posts) == set(directory.glob("http-solve-*.json")) and
            set(covers) == set(directory.glob("*.coverage.json")) and
            all(_audit_file(external(file), 1) == stamp for file, stamp in before.items()) and
            read_file(source) == body, "Audit dependency inventory/bytes drift")
    return {"validator_sha256": sha(body), "successful_posts": len(payloads),
            "post_artifacts": len(posts), "coverage_artifacts": len(covers), "pngs": len(pngs),
            "dependencies": [{"path": str(file), "sha256": sha(data), "stat": list(stamp)}
                             for file, (data, stamp) in sorted(before.items())],
            "response_digest_scope": "STORED_PARSED_PAYLOAD_AND_CDP_DIGEST_NOT_RAW_BODY_REHASH"}


def read_audit_scope(pin: dict, expected: dict, process_rc: int,
                     now: datetime | None = None, *, source_root: Path = ROOT) -> dict:
    """Read automatic evidence only, never certify, approve NA, or authorize deployment."""
    now = now or datetime.now(timezone.utc)
    require(isinstance(expected, dict) and isinstance(expected.get("commit"), str) and
            re.fullmatch(r"[a-f0-9]{40}", expected["commit"]) and
            isinstance(expected.get("source_sha256"), dict) and
            set(expected["source_sha256"]) == set(SOURCES) and
            all(isinstance(v, str) and re.fullmatch(r"[a-f0-9]{64}", v)
                for v in expected["source_sha256"].values()) and
            isinstance(expected.get("page_build"), str) and
            re.fullmatch(r"[a-f0-9]{12}", expected["page_build"]) and
            isinstance(expected.get("tool_sha256"), dict) and
            isinstance(expected["tool_sha256"].get("tools/browser_audit.mjs"), str) and
            re.fullmatch(r"[a-f0-9]{64}", expected["tool_sha256"]["tools/browser_audit.mjs"]) and
            isinstance(expected["tool_sha256"].get("tools/workbench_acceptance.py"), str) and
            re.fullmatch(r"[a-f0-9]{64}", expected["tool_sha256"]["tools/workbench_acceptance.py"]),
            "Missing independently pinned audit identity")
    report, path = audit_reference(pin)
    before = encode(report)
    require(type(process_rc) is int and process_rc == 0 and
            type(report.get("exit_code")) is int and report["exit_code"] == process_rc,
            "Audit actual process/report rc mismatch")
    require(report.get("schema_version") == 1 and report.get("tool") == "opensim-browser-audit" and
            report.get("standard") == "shared-r41" and
            report.get("mode") == "G3_G6_AUTOMATIC_EXTENSION_NOT_RELEASE" and
            report.get("verdict") == "AUTOMATED_SCOPE_PASS" and
            report.get("release_verdict") == "NOT_CERTIFIED", "Unknown/forged native audit scope")
    require(not any(report.get(key) for key in
                    ("first_failure", "harness_failure", "cleanup_failure", "finalization_failure")),
            "Native audit contains failure")
    binding(report, expected, "tools/browser_audit.mjs", now)
    identities = report.get("identity_checks")
    require(isinstance(identities, list) and len(identities) == 2 and
            all(isinstance(row, dict) for row in identities) and
            [row.get("stage") for row in identities] == ["before", "after"],
            "Audit missing before/after actual identity")
    for row in identities:
        require(row.get("verdict") == "PASS" and row.get("fixture_asset_override") is False and
                row.get("url") == report.get("url") and row.get("footer") == report["page_build"]["footer"],
                "Audit failed/fixture actual identity")
        for field in ("local_source_sha256", "committed_source_sha256"):
            require(row.get(field) == expected["source_sha256"], "Audit actual five-source mismatch")
        health = row.get("health", {})
        require(health.get("status") == "ok" and health.get("source_sha256") == expected["source_sha256"] and
                health.get("page_build") == expected["page_build"], "Audit actual health identity differs")
        assets = row.get("served_byte_sha256", {})
        require(set(assets) == {"health", "/", "/web/app.js", "/web/app.css"} and
                all(isinstance(v, str) and re.fullmatch(r"[a-f0-9]{64}", v) for v in assets.values()) and
                all(assets["/" + name] == expected["source_sha256"][name] for name in ("web/app.js", "web/app.css")),
                "Audit served asset identity differs")
    require(identities[0]["served_byte_sha256"] == identities[1]["served_byte_sha256"],
            "Audit served bytes drift")
    checks = report.get("checks")
    require(isinstance(checks, list) and checks and all(isinstance(row, dict) and
            row.get("status") in {"PASS", "NOT_TESTED"} for row in checks), "Audit failed/unknown checks")
    observed = []
    for index, row in enumerate(checks):
        observed.append({"pointer": f"/checks/{index}", "sha256": sha(encode(row)),
                         "status": row["status"], "rule": row.get("rule"),
                         **audit_coordinates(row, report)})
    coverage, gates = report.get("coverage"), report.get("gates")
    require(isinstance(coverage, list) and coverage and isinstance(gates, dict) and
            set(gates) == {"G3-accessibility", "G6-content"}, "Audit coverage/gates missing")
    require(gates["G3-accessibility"].get("status") in {"PARTIAL", "AUTOMATED_SCOPE_PASS"},
            "Native audit G3 cannot claim full PASS")
    untested = {"WEB-A11Y-009", "PHYSICAL-TOUCH", "SCREENSHOT-REVIEW",
                "FULL-STANDARD-MATRIX", "G5-performance"}
    for rule in untested:
        matches = [row for row in coverage if isinstance(row, dict) and
                   (row.get("rule") or row.get("gate")) == rule]
        require(len(matches) == 1 and matches[0].get("status") == "NOT_TESTED",
                f"Native audit cannot claim human/full-matrix/public PASS or NA: {rule}")
    remaining, na = [], []
    for row in [*coverage, *gates.values()]:
        require(isinstance(row, dict) and row.get("status") in
                {"PASS", "AUTOMATED_SCOPE_PASS", "PARTIAL", "NOT_TESTED", "NOT_APPLICABLE"},
                "Audit failed/unknown coverage")
        if row["status"] in {"PARTIAL", "NOT_TESTED"}:
            remaining.append(row)
        if row["status"] == "NOT_APPLICABLE":
            capability = {"WEB-A11Y-008": "dialogs", "WEB-NAV-004": "mobile_menus"}.get(row.get("rule"))
            require(capability is not None and type(row.get("actual_count")) is int and
                    row["actual_count"] == 0 and
                    type(report.get("page_capabilities", {}).get(capability)) is int and
                    report["page_capabilities"][capability] == 0, "Unproved audit NA cannot turn green")
            na.append(row)
    remaining.extend(row for row in checks if row["status"] == "NOT_TESTED")
    for gate in gates.values():
        require(gate.get("release_status") == "NOT_CERTIFIED", "Audit gate forged certification")
    completion = report.get("completion", {})
    require(completion.get("protocol") == "durable-final-v2" and completion.get("status") == "FINAL" and
            completion.get("receipt") == "completion.json" and completion.get("log") == "logs/audit.log",
            "Audit missing final completion")
    require(not os.path.lexists(path.parent / "report.failure.json"), "Audit failure sidecar present")
    receipt_path = external(path.parent / "completion.json")
    log_path = external(path.parent / "logs/audit.log")
    receipt_body, log_body = read_file(receipt_path), read_file(log_path)
    receipt = decode(receipt_body)
    require(receipt.get("protocol") == "durable-final-v2" and receipt.get("status") == "COMPLETE" and
            type(receipt.get("exit_code")) is int and receipt["exit_code"] == process_rc and
            receipt.get("report") == "report.json" and receipt.get("report_sha256") == pin["sha256"] and
            receipt.get("log_sha256") == completion.get("log_sha256") == sha(log_body),
            "Audit actual receipt/report/log mismatch")
    prefix = "AUDIT candidate_verdict={} candidate_rc={} report=".format(report["verdict"], process_rc)
    text = log_body.decode("utf-8")
    require(text.startswith(prefix) and text.endswith("\n") and text.count("\n") == 1 and
            external(Path(text[len(prefix):-1])).resolve() == path.resolve(), "Audit candidate log differs")
    dependencies = audit_dependencies(report, path, expected, source_root)
    require(encode(audit_reference(pin)[0]) == before and read_file(external(receipt_path)) == receipt_body and
            read_file(external(log_path)) == log_body and
            not os.path.lexists(path.parent / "report.failure.json"),
            "Audit terminal dependency drift")
    return {"state": "AUTOMATIC_SCOPE_OBSERVED", "scope": "AUDIT_AUTOMATION_NOT_RELEASE",
            "release_verdict": "NOT_CERTIFIED", "production_changes": False, "report": dict(pin),
            "commit": report["commit"], "page_build": report["page_build"]["id"],
            "process_rc": process_rc, "native_verdict": report["verdict"], "checks": observed,
            "artifact_validation": dependencies,
            "remaining": remaining, "not_applicable": na, "native_gates": gates,
            "coverage_status": "AUTOMATED_PARTIAL" if remaining or na else "AUTOMATED_SCOPE_PASS"}


def approvals(root: Path, commit: str, record: dict | None, now: datetime) -> dict:
    if record is None:
        return {}
    require(isinstance(record, dict) and set(record) == {"path", "sha256"},
            "Approval registry must be explicitly pinned")
    name = relative(record["path"])
    body = committed(root, commit, name)
    require(sha(body) == record["sha256"], "Approval registry hash mismatch")
    value = decode(body)
    require(value.get("schema") == "opensim-user-approvals-v1" and
            isinstance(value.get("records"), list), "Unknown approval registry")
    result = {}
    for item in value["records"]:
        fields = {"id", "rule_id", "owner", "expires_at", "reason", "scope",
                  "removal_condition", "status", "approved_by", "approved_at", "approval_record_id"}
        require(isinstance(item, dict) and set(item) == fields, "Incomplete user approval")
        require(all(isinstance(item[key], str) and item[key].strip() for key in fields),
                "Empty approval field")
        require(item["id"] not in result and item["rule_id"] in {*GATES, *MANUAL},
                "Duplicate/unknown exception ID or rule")
        require(item["status"] == "APPROVED" and item["approved_by"] == "user",
                "Exception not explicitly user-approved")
        require(time_value(item["approved_at"]) <= now < time_value(item["expires_at"]),
                f"Expired/future exception: {item['id']}")
        require(item["scope"] == PUBLIC_URL, "Exception scope is not exact OpenSim release target")
        result[item["id"]] = item
    return result


def execution(pin: dict, report_pin: dict, expected: dict, tool: str, now: datetime) -> dict:
    value, _ = reference(pin)
    require(set(value) == {"schema", "identity", "tool", "argv", "rc", "report",
                           "stdout", "stderr", "collected_at"},
            "Missing launcher execution receipt fields")
    require(value["schema"] == "opensim-producer-execution-v1" and
            value["identity"] == expected and value["tool"] == tool and
            value["report"] == report_pin and type(value["rc"]) is int and value["rc"] == 0,
            "Launcher does not bind producer rc/report/clean identity")
    argv = value["argv"]
    require(isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv) and
            any(a.endswith("/" + tool) or a == tool for a in argv),
            "Launcher argv does not identify required producer")
    log_artifact(value["stdout"])
    log_artifact(value["stderr"])
    if tool == "tools/browser_audit.mjs":
        audit_reference(report_pin)
    else:
        reference(report_pin)
    fresh(value, now)
    return value


def performance(report: dict) -> None:
    require(report.get("scope") == "PUBLIC_LAB" and report.get("verdict") == "PUBLIC_G5_PASS"
            and report.get("g5_certified") is True and
            report.get("target_url", "").rstrip("/") == PUBLIC_URL,
            "G5 requires PUBLIC_LAB; LOCAL_PREFLIGHT is not public PASS")
    samples = report.get("samples")
    require(isinstance(samples, list) and len(samples) == 1, "Exactly one cold public sample required")
    sample = samples[0]
    require(sample.get("target_url", "").rstrip("/") == PUBLIC_URL,
            "Loopback/unknown sample cannot be public")
    metrics = sample.get("metrics", {})
    for key, limit in (("lcp_ms", 2500), ("interaction_latency_ms", 200), ("cls", 0.1)):
        value = metrics.get(key)
        require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= limit,
                f"G5 budget/missing measurement: {key}")
    require(metrics.get("lcp_count", 0) > 0 and metrics.get("interaction_count", 0) >= 3 and
            metrics.get("cls_observed") is True, "G5 measurements cannot be defaults")
    rows = check_rows(report.get("checks"), "G5")
    require({f"WEB-PERF-{i:03}" for i in range(1, 7)} <= {r.get("rule") for r in rows},
            "G5 missing WEB-PERF-001..006")
    resources = sample.get("resources")
    require(isinstance(resources, list) and resources and
            all(isinstance(r, dict) and type(r.get("transfer_bytes")) is int and
                r["transfer_bytes"] > 0 and r.get("finished") is True and
                r.get("cached") is False and r.get("status") == 200 for r in resources),
            "G5 missing actual cold transfer evidence")
    require({r.get("type") for r in resources} >= {"Document", "Stylesheet", "Script"},
            "G5 resource set incomplete")
    dns = report.get("public_dns")
    require(isinstance(dns, list) and dns and
            all(isinstance(row, dict) and ipaddress.ip_address(row.get("address", "")).is_global
                for row in dns) and
            all(ipaddress.ip_address(row.get("remote_ip", "")).is_global for row in resources),
            "G5 actual browser connection/DNS not public")
    for types, limit in (({"Document", "Stylesheet", "Script"}, 200000),
                         (None, 1000000), ({"Script"}, 300000), ({"Font"}, 150000)):
        require(sum(r["transfer_bytes"] for r in resources if types is None or r["type"] in types)
                <= limit, "G5 transfer budget exceeded")
    texts = [r for r in resources if re.search(r"html|css|javascript|json|text/", r.get("mime", ""), re.I)]
    require(texts and all(type(r.get("decoded_body_bytes")) in (int, float) and
                         (r["decoded_body_bytes"] <= 512 or r.get("encoding") in {"gzip", "br"})
                         for r in texts), "G5 text resources not actually compressed")
    assets = [r for r in resources if r["type"] in {"Stylesheet", "Script", "Font", "Image"}]
    require(assets, "G5 static resource set empty")
    for row in assets:
        policy = row.get("cache_control", "")
        require(isinstance(policy, str) and not re.search(r"no-store", policy, re.I),
                "G5 static resource no-store")
        age = re.search(r"(?:^|,)\s*max-age=(\d+)", policy, re.I)
        if row["type"] in {"Stylesheet", "Script"} or row.get("fingerprinted"):
            require(row.get("fingerprinted") is True and age and int(age[1]) >= 31536000 and
                    re.search(r"immutable", policy, re.I) and
                    row.get("fingerprint_sha256") == row.get("body_sha256") and
                    re.fullmatch(r"[a-f0-9]{64}", row.get("body_sha256", "")),
                    "G5 fingerprint/cache bytes mismatch")
        else:
            require((row.get("etag") or row.get("last_modified")) and
                    re.search(r"must-revalidate|no-cache", policy, re.I), "G5 asset cache lacks validator")
    fonts = sample.get("fonts", {})
    require(fonts.get("ready") is True and fonts.get("errors") == [] and
            type(fonts.get("preloads")) is int and 0 <= fonts["preloads"] <= 1 and
            fonts.get("fallback_verified") is True and isinstance(fonts.get("faces"), list) and
            all(face.get("display") in {"swap", "optional"} and face.get("subset") is True
                for face in fonts["faces"]), "G5 font/fallback policy missing")
    require(sample.get("third_party") == [] and
            sample.get("isolation") == "BLOCK_ALL_OFF_ORIGIN_BEFORE_REQUEST" and
            sample.get("redirects") == [] and sample.get("network_errors") == [] and
            sample.get("runtime_errors") == [], "G5 isolation/redirect/runtime failure")
    content = sample.get("content", {})
    require(content.get("ready") is True and content.get("text_length", 0) > 100 and
            content.get("canvas_colors", 0) > 3 and content.get("solve_count", 0) >= 2 and
            content.get("nodes", 0) > 10 and content.get("triangles", 0) > 10 and
            content.get("max_velocity", 0) > 0, "G5 real content/compute evidence missing")
    require(isinstance(sample.get("images"), list) and
            all(image.get("width", 0) > 0 and image.get("height", 0) > 0 and
                image.get("intrinsic_width", float("inf")) <= image["width"] * 2 and
                image.get("mime") in {"image/avif", "image/webp", "image/svg+xml"}
                for image in sample["images"]), "G5 mobile image budget unverified")
    baseline = {"id": "opensim-mobile-cold-v1", "width": 360, "height": 800,
                "device_scale_factor": 1, "mobile": True, "touch": True,
                "latency_ms": 150, "download_bytes_s": 200000, "upload_bytes_s": 93750,
                "cpu_rate": 4, "observation_ms": 8000, "cache_disabled": True,
                "service_worker_bypassed": True, "samples": 1, "timeout_ms": 30000, "retries": 0}
    require(report.get("baseline_commands_acknowledged") is True and
            report.get("baseline") == baseline and
            report.get("browser", {}).get("product", "").startswith("Chrome/"),
            "G5 lacks browser/network/CPU baseline")
    raw = sample.get("raw_metrics")
    require(isinstance(raw, dict) and isinstance(raw.get("lcp"), list) and raw["lcp"] and
            isinstance(raw.get("shifts"), list) and "layout-shift" in raw.get("supported", []) and
            raw.get("observer_errors") == [] and
            metrics["lcp_ms"] == raw["lcp"][-1].get("startTime") and
            metrics["lcp_count"] == len(raw["lcp"]), "G5 raw LCP/CLS observations missing/differ")
    maximum, current, start, last = 0.0, 0.0, None, None
    for shift in sorted(raw["shifts"], key=lambda r: r["startTime"]):
        moment, value = shift["startTime"], shift["value"]
        require(type(moment) in (int, float) and math.isfinite(moment) and moment >= 0 and
                type(value) in (int, float) and math.isfinite(value) and value >= 0 and
                type(shift.get("hadRecentInput")) is bool, "G5 invalid raw shift observation")
        if shift["hadRecentInput"]:
            continue
        if start is None or moment - last >= 1000 or moment - start >= 5000:
            current, start = 0.0, moment
        current += value
        last = moment
        maximum = max(maximum, current)
    require(metrics["cls"] == maximum, "G5 CLS summary differs from raw session windows")
    interactions = sample.get("interactions")
    require(sample.get("phase") == "ONE_COLD_NAVIGATION_PLUS_THREE_CORE_TOUCHES" and
            isinstance(interactions, list) and len(interactions) == 3 and
            {row.get("selector") for row in interactions} == {"#view-mesh", "#view-field", "#solve-button"},
            "G5 missing three actual core interactions")
    for row in interactions:
        paints = row.get("paints")
        events = row.get("event_timing")
        require(isinstance(paints, list) and paints and isinstance(events, list) and
                all(p.get("trusted") is True and type(p.get("start_ms")) in (int, float) and
                    type(p.get("end_ms")) in (int, float) and
                    math.isfinite(p["start_ms"]) and math.isfinite(p["end_ms"]) and
                    p["end_ms"] >= p["start_ms"] and
                    p.get("bound_ms") == p["end_ms"] - p["start_ms"] for p in paints) and
                row.get("click_count") == 1 == sum(p.get("type") == "click" for p in paints),
                "G5 missing trusted raw interaction")
        durations = [event.get("duration") for event in events]
        require(all(type(d) in (int, float) and math.isfinite(d) and d >= 0 for d in durations) and
                row.get("latency_ms") == max([p["bound_ms"] for p in paints] + durations),
                "G5 interaction summary differs from raw timings")
    require(metrics["interaction_latency_ms"] == max(row["latency_ms"] for row in interactions),
            "G5 interaction aggregate differs")


def bridge_implementation(pin: dict, name: str) -> tuple[bytes, Path]:
    """Explicit candidate code pins, never an import search path or a fallback."""
    require(isinstance(pin, dict) and set(pin) == {"path", "sha256"},
            "Bridge dependency requires exact path/sha256")
    require(name in BRIDGE_IMPLEMENTATIONS and
            pin["path"] == BRIDGE_IMPLEMENTATIONS[name]["path"],
            "Bridge dependency is not an approved implementation path")
    path = Path(pin["path"])
    base = Path("/data/dev")
    require(path.is_absolute() and path.is_relative_to(base) and ".." not in path.parts and
            path.name == name, "Bridge dependency outside approved source boundary")
    cursor = base
    for part in path.relative_to(base).parts:
        cursor /= part
        require(not cursor.is_symlink(), "Bridge dependency symlink rejected")
    body, _ = _audit_file(path, 1, base=base)
    require(sha(body) == pin["sha256"] == BRIDGE_IMPLEMENTATIONS[name]["sha256"],
            "Bridge dependency source hash mismatch")
    return body, path


def bridge_algorithms(pins: dict, root: Path, expected: dict) -> tuple[types.ModuleType, types.ModuleType]:
    """Load pinned pure algorithms only; no candidate top-level imports/actions."""
    require(isinstance(pins, dict) and set(pins) == {"obligations", "trust"},
            "Missing explicit closure/trust implementation pins")
    native = types.ModuleType("opensim_bridge_native")
    path = root / "tools/workbench_acceptance.py"
    body = read_file(path)
    require(sha(body) == expected["tool_sha256"]["tools/workbench_acceptance.py"],
            "Bridge native reader source mismatch")
    names = {"EvidenceError", "need", "now", "decode", "unique_object", "finite_tree",
             "git", "bounded", "page_build"}
    nodes = [node for node in ast.parse(body).body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    require({node.name for node in nodes} == names, "Bridge native pure API missing")
    native.__dict__.update(json=json, math=math, Path=Path, subprocess=subprocess,
                           datetime=datetime, timezone=timezone, EXTERNAL=ARTIFACT_ROOT,
                           hashlib=hashlib, SOURCES=SOURCES)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), native.__dict__)
    modules = []
    import base64
    import copy
    import time
    for role, filename in (("obligations", "release_obligations.py"), ("trust", "release_trust.py")):
        source, location = bridge_implementation(pins[role], filename)
        tree = ast.parse(source, filename=str(location))
        selected = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name not in {
                    "main", "persist", "evaluate_native"}:
                selected.append(node)
            elif isinstance(node, ast.Assign):
                try:
                    ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    if not (isinstance(node.value, ast.BinOp) and
                            isinstance(node.value.op, (ast.Mult, ast.Add, ast.Sub)) and
                            isinstance(node.value.left, ast.Constant) and
                            type(node.value.left.value) is int and
                            isinstance(node.value.right, ast.Constant) and
                            type(node.value.right.value) is int):
                        continue
                selected.append(node)
        module = types.ModuleType("opensim_bridge_" + role)
        module.__dict__.update(__file__=str(location), gate=sys.modules[__name__], native=native,
                               hashlib=hashlib, json=json, os=os, Path=Path, stat=stat,
                               subprocess=subprocess, sys=sys, re=re, base64=base64, copy=copy,
                               time=time, EXTERNAL=ARTIFACT_ROOT.resolve(),
                               TOOL_PATHS=tuple(dict.fromkeys((*TOOLS, "tools/ui_contract_test.mjs"))))
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(location), "exec"), module.__dict__)
        require(read_file(location) == source, "Bridge implementation drift during load")
        modules.append(module)
    return modules[0], modules[1]


def native_obligations(pin: dict, expected: dict, process_rc: int, root: Path,
                       implementations: dict, now: datetime | None = None) -> dict:
    """Native-only mapping, after the existing complete raw audit reader."""
    scope = read_audit_scope(pin, expected, process_rc, now, source_root=root)
    obligations, _ = bridge_algorithms(implementations, root, expected)
    report, _ = audit_reference(pin)
    if report["gates"]["G3-accessibility"]["status"] == "PARTIAL":
        result = obligations.map_native_audit(report, pin, expected)
    else:
        result = {"verdict": "OPEN_OBLIGATIONS", "release_verdict": "NOT_CERTIFIED",
                  "scope": "NATIVE_AUTOMATED_SCOPE_ADAPTER_NOT_IMPLEMENTED",
                  "open": [], "closed": [], "object_inventory": {"complete": False},
                  "first_cause": {"code": "NATIVE_G3_ADAPTER_UNSUPPORTED",
                                  "status": report["gates"]["G3-accessibility"]["status"]},
                  "original_statuses_not_promoted": True}
    # Mapping returns an observation time, not a semantic dependency.
    result.pop("collected_at", None)
    require(read_audit_scope(pin, expected, process_rc, now, source_root=root) == scope,
            "Bridge original audit drift")
    result["automatic_scope"] = scope
    return result


def bridge_closure(config: dict, root: Path, expected: dict, audit_pin: dict,
                   audit_rc: int, now: datetime) -> dict:
    require(isinstance(config, dict) and set(config) ==
            {"implementations", "native_closure", "canonical", "provenance"},
            "Invalid release bridge fields")
    mapped = native_obligations(audit_pin, expected, audit_rc, root, config["implementations"], now)
    supplied, _ = reference(config["native_closure"])
    require(supplied == mapped, "Native closure differs from original coordinates/source binding")
    canonical = None
    if config["canonical"] is not None:
        require(isinstance(config["canonical"], dict) and set(config["canonical"]) == {"policy", "ledger"},
                "Canonical closure requires policy/ledger pins")
        obligations, _ = bridge_algorithms(config["implementations"], root, expected)
        canonical = obligations.evaluate(config["canonical"]["policy"], config["canonical"]["ledger"],
                                         root, expected["commit"])
        canonical.pop("collected_at", None)
        require(canonical["release_verdict"] == "NOT_CERTIFIED" and
                canonical["execution_provenance"] == "SUPPLIED_PROTOCOL_CLAIMS_NOT_INDEPENDENTLY_ATTESTED",
                "Canonical closure cannot certify native originals")
    return {"native": mapped, "canonical": canonical,
            "canonical_authority": "PROTOCOL_ONLY_NOT_NATIVE_CERTIFICATION",
            "scope": "AUTOMATIC_OBSERVATION_AND_OPEN_OBLIGATIONS_NOT_RELEASE"}


def bridge_provenance(config: dict, root: Path, expected: dict, materials: dict,
                      registry: dict, used: list, now: datetime) -> dict:
    """Material validity and trusted provenance are separate; no production root exists."""
    _, trust = bridge_algorithms(config["implementations"], root, expected)
    provenance = config["provenance"]
    require(isinstance(provenance, dict) and set(provenance) == {"human", "exceptions"} and
            isinstance(provenance["human"], dict) and isinstance(provenance["exceptions"], dict) and
            set(provenance["human"]) <= set(materials) and
            set(provenance["exceptions"]) <= set(used), "Unknown human/exception provenance")
    findings, inputs = [], []
    targets = [("human", rule, pin["sha256"]) for rule, pin in materials.items()]
    targets += [("user-approval", item, sha(encode(registry[item]))) for item in used]
    for role, identifier, material_sha in targets:
        proofs = provenance["human" if role == "human" else "exceptions"]
        proof = proofs.get(identifier)
        if proof is None:
            findings.append({"role": role, "id": identifier, "material_sha256": material_sha,
                             "state": "MISSING_PROVENANCE"})
            continue
        require(isinstance(proof, dict) and set(proof) == {"envelope", "revocations"},
                "Provenance requires pinned envelope/revocations, not editable trust state")
        envelope, _ = artifact(proof["envelope"])
        revocations, _ = artifact(proof["revocations"])
        inputs.extend((proof["envelope"], proof["revocations"]))
        try:
            result = trust.verify(envelope, revocations, expected_candidate=expected["commit"],
                                  expected_material=material_sha, expected_role=role,
                                  now=int(now.timestamp()))
        except trust.TrustError as exc:
            findings.append({"role": role, "id": identifier, "material_sha256": material_sha,
                             "state": exc.code, "coordinate": exc.coordinate})
        else:
            require(result.get("scope") != "TEST_ONLY", "TEST_ONLY trust cannot certify release")
            raise GateError("Production trust result contract not enrolled")
    # Invoke the actual production algorithm even when every provenance pin is missing.
    try:
        trust.verify(b"", b"", expected_candidate=expected["commit"],
                     expected_material=sha(encode(expected)), expected_role="human",
                     now=int(now.timestamp()))
    except trust.TrustError as exc:
        require(exc.code == "TRUST_UNAVAILABLE", "Unexpected production trust failure")
        production = {"state": exc.code, "coordinate": exc.coordinate}
    else:
        raise GateError("Unexpected unenrolled production trust")
    for pin in inputs:
        artifact(pin)
    return {"production": production, "materials": findings,
            "test_roots_accepted": False, "release_verdict": "NOT_CERTIFIED"}


def evaluate(root: Path, commit: str, ledger_path: Path,
             approval_pin: dict | None = None, now: datetime | None = None, *,
             measured_context: bool = False) -> dict:
    now = now or datetime.now(timezone.utc)
    expected = identity(root, commit)
    raw = read_file(external(ledger_path))
    ledger = decode(raw)
    bridge = ledger.get("schema") == "opensim-release-evidence-v2"
    fields = {"schema", "identity", "evidence", "executions", "gates", "manual", "exceptions"}
    require(set(ledger) == fields | ({"bridge"} if bridge else set()),
            "Unknown/missing release ledger fields")
    require(ledger["schema"] in {"opensim-release-evidence-v1", "opensim-release-evidence-v2"},
            "Unknown release evidence schema")
    require(ledger["identity"] == expected, "Ledger does not bind clean commit/sources/build/tools")
    if bridge:
        require(sha(read_file(Path(__file__).resolve())) == expected["tool_sha256"]["tools/release_gate.py"],
                "Bridge evaluator differs from candidate release_gate tool")
    registry = approvals(root, commit, approval_pin, now)
    used = ledger["exceptions"]
    require(isinstance(used, list) and all(isinstance(item, str) for item in used) and
            len(set(used)) == len(used) and
            all(item in registry for item in used), "Unapproved/unknown exception")
    waived = {registry[item]["rule_id"] for item in used}
    require("G5-performance" not in waived, "Missing public G5 cannot be waived to PASS")
    if not bridge:
        require(not used, "BLOCKED: no trusted user-approval verifier; editable JSON is not approval")
    evidence = ledger["evidence"]
    require(isinstance(evidence, dict) and set(evidence) ==
            {"local", "numerics", "browser", "audit", "performance", "public", "page"},
            "Required actual local/browser/audit/perf/public evidence missing")
    reports = {kind: (audit_reference(record) if kind == "audit" else reference(record))
               for kind, record in evidence.items()}
    executions = ledger["executions"]
    producer_tools = {"local": "tools/acceptance.py", "numerics": "tools/validate_numerics.py",
                      "browser": "tools/browser_check.mjs", "audit": "tools/browser_audit.mjs",
                      "performance": "tools/browser_performance.mjs", "public": "tools/public_gate.py",
                      "page": "tools/render_page.py"}
    require(isinstance(executions, dict) and set(executions) == set(producer_tools),
            "Missing required producer launcher receipts")
    execution_values = {kind: execution(executions[kind], evidence[kind], expected, tool, now)
                        for kind, tool in producer_tools.items()}
    audit_scope = read_audit_scope(evidence["audit"], expected, execution_values["audit"]["rc"], now,
                                  source_root=root)
    closure = (bridge_closure(ledger["bridge"], root, expected, evidence["audit"],
                             execution_values["audit"]["rc"], now) if bridge else None)
    page = reports["page"][0]
    require(set(page) == {"schema", "commit", "source_sha256", "page_build", "html",
                         "page_check", "timestamp_origin", "source_epoch"},
            "Missing actual generated-page binding")
    require(page["schema"] == "opensim-page-evidence-v1" and page["commit"] == commit and
            page["source_sha256"] == expected["source_sha256"] and
            page["page_build"] == expected["page_build"], "Generated page identity mismatch")
    html, _ = artifact(page["html"])
    page_check, _ = reference(page["page_check"])
    require(page_check.get("verdict") == "PASS" and
            page_check.get("html_sha256") == sha(html) and
            page_check.get("source_sha256_prefix") == expected["page_build"] and
            expected["page_build"].encode() in html,
            "Actual generated page/check/source mismatch")
    edited_at = time_value(page_check.get("edited_at"))
    require(edited_at.utcoffset().total_seconds() == 28800 and
            page["timestamp_origin"] in {"SOURCE_MTIME", "COMMIT_EPOCH"},
            "Generated page must declare truthful edit timestamp origin")
    if page["timestamp_origin"] == "COMMIT_EPOCH":
        epoch = int(git(root, "show", "-s", "--format=%ct", commit).strip())
        require(type(page["source_epoch"]) is int and page["source_epoch"] == epoch and
                edited_at.timestamp() == epoch, "Fixed page epoch differs from commit time")
    else:
        require(page["source_epoch"] is None and edited_at.timestamp() ==
                int(max((root / name).stat().st_mtime for name in SOURCES)),
                "Local edited_at must reflect actual source mtime, not deployment time")
    local = reports["local"][0]
    require(local.get("verdict") == "PASS" and local.get("source_sha256") ==
            {**expected["source_sha256"], "tools/validate_numerics.py":
             expected["tool_sha256"]["tools/validate_numerics.py"]}, "Stale/incomplete local evidence")
    fresh(local, now)
    steps = local.get("steps")
    require(isinstance(steps, list) and len(steps) == 5 and
            all(isinstance(s, dict) and type(s.get("rc")) is int and s["rc"] == 0 and
                isinstance(s.get("argv"), list) and s["argv"] and
                isinstance(s.get("stdout"), str) and isinstance(s.get("stderr"), str)
                for s in steps), "Missing complete local subprocess evidence")
    require(any(re.search(r"Ran [1-9][0-9]* tests", s["stdout"] + s["stderr"]) for s in steps),
            "Local rc=0 without actual tests is insufficient")
    numerics = reports["numerics"][0]
    require(numerics.get("state") == "COMPLETE" and numerics.get("verdict") == "PASS" and
            numerics.get("solver", {}).get("sha256") == expected["source_sha256"]["solver.py"] and
            type(numerics.get("checks_executed")) is int and numerics["checks_executed"] > 0,
            "Missing/stale complete numerical evidence")
    check_rows(numerics.get("cases"), "numerical cases")
    check_rows(numerics.get("rejections"), "numerical rejections")
    require(set(numerics.get("convergence", {})) == {"circle", "rectangle", "square"},
            "Missing numerical convergence groups")
    for group in numerics["convergence"].values():
        require(isinstance(group, dict) and group.get("verdict") == "PASS" and
                group.get("resolutions") == [8, 16, 32], "Incomplete numerical convergence")
        errors = group.get("relative_flow_error", {}).get("errors")
        require(isinstance(errors, list) and len(errors) == 3 and
                all(type(x) in (float, int) and math.isfinite(x) and x > 0 for x in errors) and
                errors[0] > errors[1] > errors[2], "Convergence error evidence invalid")
    for kind, tool in (("browser", "tools/browser_check.mjs"), ("audit", "tools/browser_audit.mjs"),
                       ("performance", "tools/browser_performance.mjs")):
        report, path = reports[kind]
        binding(report, expected, tool, now)
        require((kind != "audit" and "exit_code" not in report) or
                (type(report.get("exit_code")) is int and report["exit_code"] == 0),
                f"Failed producer exit code: {kind}")
        if kind != "performance":
            require(report.get("standard") == "shared-r41", "Unknown web standard")
            rows = report.get("checks")
            require(isinstance(rows, list) and rows and
                    all(isinstance(row, dict) and (row.get("status") or row.get("verdict")) in
                        {"PASS", "NOT_TESTED"} for row in rows),
                    f"Failed/unknown audit or browser checks: {kind}")
            require(any((row.get("status") or row.get("verdict")) == "PASS" for row in rows),
                    f"No successful observed checks: {kind}")
            screenshots(report, path)
    browser = reports["browser"][0]
    require(browser.get("mode") == "OPENSIM_ACCEPTANCE" and
            browser.get("verdict") == "AUTOMATED_PASS", "Browser probe is not full acceptance")
    require(time_value(browser["page_build"]["edited_at"]) == edited_at,
            "Browser edited_at differs from actual tested page")
    matrix = browser.get("matrix")
    require(isinstance(matrix, list) and
            {tuple(row.get("viewport", (row.get("width"), row.get("height"))))
             for row in matrix if isinstance(row, dict) and row.get("status") == "PASS"} >= set(VIEWPORTS),
            "Browser matrix missing required viewports")
    audit = reports["audit"][0]
    require(audit.get("mode") == "G3_G6_AUTOMATIC_EXTENSION_NOT_RELEASE" and
            audit.get("verdict") == "AUTOMATED_SCOPE_PASS",
            "Audit smoke/fixture is not product acceptance")
    performance(reports["performance"][0])
    public = reports["public"][0]
    require(public.get("verdict") == "PASS" and public.get("scope") == "PUBLIC_CODE_AND_FEM" and
            public.get("url") == PUBLIC_URL and public.get("desired_git_head") == commit and
            public.get("source_sha256") == expected["source_sha256"] and
            public.get("page_build") == expected["page_build"], "Missing same-version real public PASS")
    addresses = public.get("dns_addresses")
    require(isinstance(addresses, list) and addresses and
            all(ipaddress.ip_address(ip).is_global for ip in addresses),
            "Loopback/private DNS cannot be public evidence")
    rows = check_rows(public.get("checks"), "public code/FEM")
    require({r.get("name") for r in rows} ==
            {"runtime_identity", "page_and_assets", "real_fem_circle", "real_fem_rectangle"},
            "Missing real public computations")
    fresh(public, now, 600)
    for row in rows:
        if row.get("name", "").startswith("real_fem_"):
            require(re.fullmatch(r"[a-f0-9]{64}", row.get("config_sha256", "")) and
                    row.get("solver_sha256") == expected["source_sha256"]["solver.py"] and
                    type(row.get("node_count")) is int and row["node_count"] > 10 and
                    type(row.get("flow_rate_m3_s")) in (int, float) and
                    math.isfinite(row["flow_rate_m3_s"]) and row["flow_rate_m3_s"] > 0 and
                    type(row.get("relative_residual")) in (int, float) and
                    math.isfinite(row["relative_residual"]) and 0 <= row["relative_residual"] < 1e-6,
                    "Public computation lacks actual FEM identity/metrics")
            fresh({"collected_at": row.get("computed_at")}, now, 600)
    require(isinstance(ledger["gates"], dict) and set(ledger["gates"]) == set(GATES),
            "Missing G1..G6 records")
    for gate, record in ledger["gates"].items():
        require(isinstance(record, dict) and set(record) == {"evidence", "rc", "status", "checks"},
                f"Invalid gate record: {gate}")
        kind = record["evidence"]
        require(kind in evidence and type(record["rc"]) is int, "Unknown gate evidence/rc")
        require(record["status"] in {"PASS", "PARTIAL", "NOT_TESTED", "FAIL"} |
                ({"AUTOMATED_SCOPE_PASS"} if bridge else set()), "Unknown gate status")
        if gate in waived and not bridge:
            continue
        native_observed = bridge and kind == "audit"
        if native_observed:
            require(record["rc"] == 0 and record["status"] ==
                    reports["audit"][0]["gates"].get(gate, {}).get("status"),
                    f"Bridge gate must retain original native status: {gate}")
        else:
            require(record["status"] == "PASS" and record["rc"] == 0, f"Required gate incomplete: {gate}")
        require(kind == {"G1-structure": "browser", "G2-layout": "browser",
                         "G3-accessibility": "audit", "G4-keyboard": "browser",
                         "G5-performance": "performance", "G6-content": "audit"}[gate],
                "Gate attributed to wrong producer")
        report = reports[kind][0]
        selected = selected_checks(report, record["checks"], kind)
        if kind == "audit" and not bridge:
            require(report.get("gates", {}).get(gate, {}).get("status") == "PASS",
                    f"PARTIAL audit cannot certify {gate}")
        require(all((row.get("status") or row.get("verdict")) == "PASS" for row in selected),
                f"NOT_TESTED checks cannot certify {gate}")
        if not native_observed:
            require(REQUIRED_RULES[gate] <= {row.get("rule") for row in selected},
                    f"Gate missing required rule coverage: {gate}")
        for row in selected:
            if kind == "audit":
                audit_coordinates(row, report)
                continue
            url = row.get("url") or report.get("target_url") or report.get("url")
            viewport = row.get("viewport")
            if viewport is None and row.get("coordinate"):
                coordinate = row["coordinate"]
                viewport = next(([item.get("width"), item.get("height")] for item in
                                 report.get("matrix", []) if item.get("name") == coordinate), None)
            reproduce = row.get("reproduce") or row.get("coordinate")
            require(isinstance(url, str) and url and isinstance(reproduce, str) and reproduce and
                    isinstance(viewport, list) and len(viewport) == 2,
                    f"Gate checks missing URL/viewport/reproduction coordinates: {gate}")
        if kind == "audit" and not bridge:
            require(not any(row.get("status") == "NOT_TESTED" for row in report["checks"]),
                    f"Unclosed NOT_TESTED audit cannot certify {gate}")
    require(isinstance(ledger["manual"], dict) and set(ledger["manual"]) == set(MANUAL),
            "Missing real device/manual gate records")
    manual_refs = {}
    for rule, record in ledger["manual"].items():
        if rule in waived:
            require(record == {"status": "EXCEPTION"}, "Waived manual record must remain EXCEPTION")
            continue
        report, path = reference(record)
        require(report.get("schema") == "opensim-human-check-v1" and report.get("rule_id") == rule and
                report.get("status") == "PASS" and report.get("commit") == commit and
                report.get("source_sha256") == expected["source_sha256"] and
                report.get("page_build") == expected["page_build"] and
                report.get("mode") == "REAL_DEVICE_HUMAN" and
                all(isinstance(report.get(k), str) and report[k].strip() for k in
                    ("owner", "device", "combination_version", "url", "reproduce")) and
                report["url"] == PUBLIC_URL, f"Missing real human/device result: {rule}")
        fresh(report, now)
        check_rows(report.get("checks"), rule)
        screenshots(report, path)
        manual_refs[rule] = record
    require(identity(root, commit) == expected, "Checkout changed during release gate")
    for kind, pin in evidence.items():
        if kind == "audit":
            require(read_audit_scope(pin, expected, execution_values[kind]["rc"], now,
                                     source_root=root) == audit_scope,
                    "Audit automatic scope drift during gate")
        else:
            reference(pin)
        execution(executions[kind], pin, expected, producer_tools[kind], now)
    artifact(page["html"])
    reference(page["page_check"])
    for pin in manual_refs.values():
        reference(pin)
    require(read_file(ledger_path) == raw, "Ledger drift during gate")
    if bridge:
        if measured_context:
            # Only the new wrapper consumes this non-certifying context. All
            # original structural/producer checks above remain mandatory.
            require(bridge_closure(ledger["bridge"], root, expected, evidence["audit"],
                                   execution_values["audit"]["rc"], now) == closure,
                    "Measured context original closure drift")
            require(identity(root, commit) == expected and read_file(ledger_path) == raw,
                    "Measured context candidate/ledger drift")
            return {"candidate": expected, "closure": closure,
                    "scope": "ORIGINAL_INPUTS_VALIDATED_NOT_CERTIFIED",
                    "source_stable": True, "release_verdict": "NOT_CERTIFIED"}
        trust_result = bridge_provenance(ledger["bridge"], root, expected, manual_refs, registry, used, now)
        require(bridge_closure(ledger["bridge"], root, expected, evidence["audit"],
                               execution_values["audit"]["rc"], now) == closure,
                "Bridge closure drift during gate")
        require(identity(root, commit) == expected and read_file(ledger_path) == raw,
                "Bridge candidate/ledger changed during provenance verification")
        for pin in manual_refs.values():
            reference(pin)
        for role, filename in (("obligations", "release_obligations.py"), ("trust", "release_trust.py")):
            bridge_implementation(ledger["bridge"]["implementations"][role], filename)
        observation = {"schema": "opensim-release-bridge-observation-v1",
                       "closure": closure, "trust": trust_result,
                       "original_statuses_not_promoted": True, "production_changes": False,
                       "candidate": expected, "source_stable": True,
                       "release_verdict": "NOT_CERTIFIED",
                       "native_semantic_closure": "OPEN_ADAPTER_AND_INVENTORY_INCOMPLETE",
                       "scope": "OBSERVATION_NOT_CERTIFICATION"}
        raise BridgeBlocked("BLOCKED: TRUST_UNAVAILABLE; native semantic obligations remain OPEN; "
                            "raw PARTIAL/NOT_TESTED retained", observation)
    raise GateError("BLOCKED: trusted human-acceptance verifier unavailable; "
                    "editable REAL_DEVICE_HUMAN/PASS records cannot self-certify")


def assess_native(root: Path, commit: str, request_path: Path, *,
                  publication_guards: list | None = None,
                  read_counter: list[int] | None = None) -> dict:
    """Passive V1 assessment; no v2 ledger, producer, signer or release path."""
    request_path = external(request_path)
    request_bytes, request_stamp = _audit_file(request_path, 1)
    request = decode(request_bytes)
    require(set(request) == {"schema", "adapter", "observation"} and
            request["schema"] == NATIVE_ASSESSMENT_SCHEMA,
            "Invalid versioned native assessment request")
    require(request["adapter"] == NATIVE_ADAPTER, "Unapproved native adapter source pin")
    location = Path(NATIVE_ADAPTER["path"])
    body, source_stamp = _audit_file(location, 1, base=Path("/data/dev"))
    require(sha(body) == NATIVE_ADAPTER["sha256"], "Native adapter source drift")
    spec = importlib.util.spec_from_file_location("opensim_assessment_native", location)
    adapter = importlib.util.module_from_spec(spec)
    exec(compile(body, str(location), "exec"), adapter.__dict__)
    pin = request["observation"]
    require(isinstance(pin, dict) and set(pin) == {"path", "sha256"} and
            isinstance(pin["sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", pin["sha256"]),
            "Native observation requires exact path/SHA256")
    observation_path = external(Path(pin["path"]))
    require(observation_path.name == "adapter.json", "Native observation filename differs")
    observation_bytes, observation_stamp = _audit_file(observation_path, 1)
    require(sha(observation_bytes) == pin["sha256"], "Native observation SHA differs")
    diagnostic = decode(observation_bytes)
    calls = 0

    def read():
        nonlocal calls
        calls += 1
        if read_counter is not None:
            read_counter[0] = calls
        try:
            return adapter.read_observation(observation_path.parent)
        except Exception as exc:
            exc.native_observation_diagnostic = {
                "validation": "REJECTED_UNVALIDATED_DIAGNOSTIC_NOT_AUTHORITY",
                "first_cause": diagnostic.get("first_cause")}
            raise

    value = read()
    require(value == diagnostic, "Native observation bytes differ")
    require(root.is_absolute() and not root.is_symlink() and
            value["source_root"] == str(root) and value["candidate"]["commit"] == commit,
            "Native assessment candidate/root mismatch")
    reader, _ = adapter.load_reader()
    require(reader.inventory(root) == value["candidate"], "Native assessment candidate drift")
    source_hashes = reader.source_map(value["candidate"])
    build = reader.page_build(root)
    require(all(value["candidate"]["tracked_sha256"][name] == source_hashes[name]
                for name in SOURCES), "Native source identity differs")
    coverage = value["coverage"]
    require(coverage["state"] == "OPEN" and coverage["closed_obligations"] == [] and
            value["release_verdict"] == "NOT_CERTIFIED" and
            value["state"] in {"OPEN_OBLIGATIONS", "ORIGINAL_PRODUCER_FAILURE_VETO"},
            "Native observation cannot claim certification or semantic closure")
    require(type(value["exit_code"]) is int and value["exit_code"] ==
            (1 if value["state"] == "ORIGINAL_PRODUCER_FAILURE_VETO" else 3),
            "Native observation terminal rc/state mismatch")
    result = {"schema": "opensim-release-native-assessment-v1",
              "scope": "NATIVE_OBSERVATION_ASSESSMENT_NOT_RELEASE",
              "state": value["state"], "exit_code": value["exit_code"],
              "release_verdict": "NOT_CERTIFIED", "production_changes": False,
              "collected_at": datetime.now(timezone.utc).isoformat(),
              "first_cause": value.get("first_cause"),
              "native_observation": pin, "candidate": value["candidate"],
              "assessment": {"adapter": dict(NATIVE_ADAPTER), "source_sha256": source_hashes,
                  "page_build": build, "original": value["original"],
                  "native_g3_status": value.get("native_audit", {}).get("gates", {}).get(
                      "G3-accessibility", {}).get("status"),
                  "coverage": coverage, "closed_obligations": [],
                  "human": [{"id": name, "status": "NOT_TESTED"} for name in MANUAL],
                  "public_g5": {"status": "NOT_TESTED", "waivable": False},
                  "trust": {"state": "TRUST_UNAVAILABLE", "enrollment": "NOT_IMPLEMENTED"},
                  "launcher_scope": value["launcher"]["scope"],
                  "authentication": value["launcher"]["authentication"]}}

    def verify():
        require(not list(observation_path.parent.glob("adapter-failure*.json")),
                "Adapter failure sidecar present before assessment publication")
        adapter.verify_observation(value, reader)
        require(_audit_file(request_path, 1) == (request_bytes, request_stamp) and
                _audit_file(observation_path, 1) == (observation_bytes, observation_stamp) and
                _audit_file(location, 1, base=Path("/data/dev")) == (body, source_stamp),
                "Late native assessment input/source drift")

    try:
        require(read() == value and reader.inventory(root) == value["candidate"],
                "Late native observation/candidate drift")
        verify()
    except Exception as exc:
        result.update(state="INVALID_NATIVE_ASSESSMENT", exit_code=2,
                      failure={"type": type(exc).__name__, "reason": str(exc),
                               "coordinate": getattr(exc, "coordinate", "assessment")})
    result["assessment"]["read_observation_calls"] = calls
    if publication_guards is not None and "failure" not in result:
        publication_guards.append(verify)
    return result


def native_assessment_stdout(line: str) -> None:
    """No buffered failed bytes remain to alter the promised process rc at exit."""
    body = (line + "\n").encode("utf-8")
    offset = 0
    while offset < len(body):
        count = os.write(sys.stdout.fileno(), body[offset:])
        require(count > 0, "Zero-byte native assessment stdout write")
        offset += count


def native_assessment_main(args) -> int:
    guards = []
    read_counter = [0]
    report = {"schema": "opensim-release-native-assessment-v1",
              "scope": "NATIVE_OBSERVATION_ASSESSMENT_NOT_RELEASE",
              "state": "INVALID_NATIVE_ASSESSMENT", "exit_code": 2,
              "release_verdict": "NOT_CERTIFIED", "production_changes": False,
              "collected_at": datetime.now(timezone.utc).isoformat()}
    try:
        require(os.geteuid() != 0, "Native assessment must not run as root")
        require(not args.approval_path and not args.approval_sha256,
                "Native assessment forbids approval/human bypass inputs")
        report = assess_native(args.source_root, args.commit, args.native_assessment,
                               publication_guards=guards, read_counter=read_counter)
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc),
                             "coordinate": getattr(exc, "coordinate", "assessment")}
        report["read_observation_calls"] = read_counter[0]
        if hasattr(exc, "native_observation_diagnostic"):
            report["native_observation_diagnostic"] = exc.native_observation_diagnostic
    def verify():
        for guard in guards:
            guard()

    return finalize_result(report, args.out, status_key="state",
                           failed_status="INVALID_NATIVE_ASSESSMENT", label="OPENSIM_NATIVE_ASSESSMENT",
                           verify=verify, emit=native_assessment_stdout)


def closure_trust():
    """New closure-only pinned S4 module; old native bridge stays unchanged."""
    path = Path(CLOSURE_TRUST_IMPLEMENTATION["path"])
    cursor = Path("/data/dev")
    require(path.is_relative_to(cursor), "Closure trust outside source boundary")
    for part in path.relative_to(cursor).parts:
        cursor /= part
        require(not cursor.is_symlink(), "Closure trust source symlink")
    body, stamp = _audit_file(path, 1, base=Path("/data/dev"))
    require(sha(body) == CLOSURE_TRUST_IMPLEMENTATION["sha256"], "Closure trust source pin mismatch")
    module = types.ModuleType("opensim_closure_trust")
    module.__dict__.update(__file__=str(path))
    exec(compile(body, str(path), "exec"), module.__dict__)
    require(_audit_file(path, 1, base=Path("/data/dev")) == (body, stamp), "Closure trust load drift")
    return module, (path, body, stamp)


def verify_closure_provenance(trust, provenance: dict, targets: list, commit: str,
                              *, fixture_channel: Path | None = None):
    """Verify actual signed files. Fixture injection is never available on gate CLI."""
    require(isinstance(provenance, dict) and set(provenance) <= {
        role + ":" + identifier for role, identifier, _ in targets}, "Unknown closure provenance material")
    proofs, records, unavailable = [], [], []
    for role, identifier, material_sha in targets:
        key = role + ":" + identifier
        proof = provenance.get(key)
        if proof is None:
            unavailable.append({"material": key, "code": "MISSING_PROVENANCE"})
            continue
        require(isinstance(proof, dict) and set(proof) == {"envelope", "revocations"},
                "Closure provenance requires exact pins, not trust literals")
        frames = []
        for name in ("envelope", "revocations"):
            frame, location = artifact(proof[name])
            readback, stamp = _audit_file(location, 1)
            require(readback == frame, "Closure provenance changed while reading")
            records.append((location, frame, stamp))
            frames.append(frame)
        payload = trust.parse(frames[0])["payload"]
        require(payload.get("record_id") == key and payload.get("source") == {
            "record_id": key, "sha256": material_sha}, "Closure signed source/record mismatch")
        try:
            if fixture_channel is None:
                result = trust.verify(*frames, expected_candidate=commit,
                                      expected_material=material_sha, expected_role=role)
            else:
                result = trust.verify(*frames, expected_candidate=commit,
                                      expected_material=material_sha, expected_role=role,
                                      test_only=True, test_channel=fixture_channel)
        except trust.TrustError as exc:
            require(exc.code in {"TRUST_UNAVAILABLE", "CRYPTO_UNAVAILABLE"},
                    "Closure trust rejected: " + exc.code + ":" + exc.coordinate)
            unavailable.append({"material": key, "code": exc.code, "coordinate": exc.coordinate})
            continue
        fields = {"schema", "state", "exit_code", "scope", "release_verdict", "domain", "policy", "role",
                  "candidate", "material_sha256", "envelope_sha256", "revocations_sha256", "revocation_sequence",
                  "checked_at", "expires_at", "record_id", "channel", "channel_files", "authority_key_id",
                  "authorization", "enrollment_sequence"}
        require(isinstance(result, dict) and set(result) == fields and
                result["schema"] == "opensim-release-trust-result-v1" and result["release_verdict"] == "NOT_CERTIFIED" and
                result["domain"] == "opensim.ai4everyone.top" and result["policy"] == "opensim-release-trust-policy-v1" and
                result["state"] == ("PROVENANCE_VERIFIED" if fixture_channel is None else "TEST_ALGORITHM_VERIFIED") and
                result["scope"] == ("PRODUCTION_PROVENANCE_ONLY" if fixture_channel is None else "TEST_ONLY") and
                result["channel"] == ("ROOT_MANAGED_OUT_OF_BAND_AUTHORIZATION_V1" if fixture_channel is None else "FILE_FIXTURE_TEST_ONLY") and
                result["candidate"] == commit and result["material_sha256"] == material_sha and result["role"] == role and
                type(result["exit_code"]) is int and result["exit_code"] == 0 and result["record_id"] == key and
                result["envelope_sha256"] == sha(frames[0]) and result["revocations_sha256"] == sha(frames[1]) and
                type(result["checked_at"]) is int and type(result["expires_at"]) is int and
                result["checked_at"] <= int(datetime.now(timezone.utc).timestamp()) < result["expires_at"] and
                isinstance(result["channel_files"], dict) and set(result["channel_files"]) == {
                    "authority.json", "authorization.json", "enrollment.json", "directory_chain"},
                "Closure unenrolled production trust contract")
        require(trust.parse(frames[1])["payload"]["source"] == result["authorization"],
                "Closure revocation source differs from approved authority")
        proofs.append({"role": role, "id": identifier, "candidate": commit,
                       "material_sha256": material_sha, "state": result["state"],
                       "scope": result["scope"], "verification": result})
    for location, frame, stamp in records:
        require(_audit_file(location, 1) == (frame, stamp), "Closure late provenance drift")
    return {"production": "UNAVAILABLE" if unavailable else (
                "PRODUCTION_VERIFIED" if fixture_channel is None else "TEST_ONLY_VERIFIED"),
            "materials": proofs}, records, unavailable


def certified_terminal(identity_value: dict, ledger: dict, ledger_pin: dict,
                       closure: dict, trust_result: dict, approval_pin: dict | None,
                       *, test_only: bool = False, now: datetime | None = None) -> dict:
    """Pure terminal contract; caller must supply independently verified inputs.

    Not an authentication API and never called with editable request trust.
    Test-only positives cannot become a sync admission receipt.
    """
    now = now or datetime.now(timezone.utc)
    require(type(test_only) is bool, "Invalid terminal scope")
    require(ledger.get("schema") == "opensim-release-evidence-v2" and
            ledger.get("identity") == identity_value, "Terminal original ledger identity differs")
    require(isinstance(ledger.get("manual"), dict) and set(ledger["manual"]) == set(MANUAL),
            "Terminal human obligations missing")
    require(closure.get("schema") == "opensim-measured-obligations-v1" and
            closure.get("candidate") == identity_value and
            closure.get("verdict") == "MEASURED_PREDICATES_CLOSED" and
            closure.get("release_verdict") == "NOT_CERTIFIED" and
            type(closure.get("required")) is int and closure["required"] > 0 and
            closure.get("open") == [] and len(closure.get("closed", [])) == closure["required"],
            "Terminal measured obligations remain OPEN")
    require(isinstance(trust_result, dict) and set(trust_result) == {"production", "materials"},
            "Terminal verified trust contract missing")
    require(trust_result["production"] == ("TEST_ONLY_VERIFIED" if test_only else "PRODUCTION_VERIFIED"),
            "Terminal production trust unavailable")
    required_materials = [("human", rule, ledger["manual"][rule]["sha256"])
                          for rule in MANUAL if ledger["manual"][rule] != {"status": "EXCEPTION"}]
    required_materials.extend(("deployment", str(i), pin["sha256"])
                              for i, pin in enumerate([ledger_pin, *closure["materials"]]))
    # The approval register itself is bound, not just an editable APPROVED word.
    if ledger["exceptions"]:
        require(isinstance(approval_pin, dict) and set(approval_pin) == {"path", "sha256"},
                "Terminal exception approval pin missing")
        required_materials.append(("user-approval", "register", approval_pin["sha256"]))
    proofs = trust_result["materials"]
    require(isinstance(proofs, list) and len(proofs) == len(required_materials),
            "Terminal missing/duplicate material provenance")
    observed = []
    for proof in proofs:
        require(isinstance(proof, dict) and set(proof) == {"role", "id", "candidate", "material_sha256", "state", "scope", "verification"},
                "Terminal unknown provenance fields")
        require(proof["candidate"] == identity_value["commit"] and
                proof["state"] == ("TEST_ALGORITHM_VERIFIED" if test_only else "PROVENANCE_VERIFIED") and
                proof["scope"] == ("TEST_ONLY" if test_only else "PRODUCTION_PROVENANCE_ONLY") and
                proof["verification"].get("state") == proof["state"] and
                proof["verification"].get("candidate") == proof["candidate"] and
                proof["verification"].get("material_sha256") == proof["material_sha256"],
                "Terminal candidate/role/trust scope differs")
        observed.append((proof["role"], proof["id"], proof["material_sha256"]))
    require(sorted(observed) == sorted(required_materials), "Terminal provenance material/role differs")
    return {"schema": "opensim-release-gate-v1", "verdict": "PASS", "exit_code": 0,
            "release_verdict": "NOT_CERTIFIED" if test_only else (
                "APPROVED_EXCEPTION" if ledger["exceptions"] else "CERTIFIED"),
            "scope": "TEST_ONLY_TERMINAL_CONTRACT" if test_only else "EVIDENCE_VALIDATED_NOT_DEPLOYED",
            "identity": identity_value, "ledger_sha256": ledger_pin["sha256"],
            "approval_pin": approval_pin, "exceptions": ledger["exceptions"],
            "gates": ledger["gates"], "evidence": ledger["evidence"],
            "closure": closure, "trust": trust_result, "production_changes": False,
            "collected_at": now.isoformat()}


def evaluate_closure(root: Path, commit: str, request_path: Path,
                     approval_pin: dict | None = None, *, publication_guards: list | None = None) -> dict:
    """New fixed ledger wrapper; earlier original failures are never supplemented."""
    raw, request_stamp = _audit_file(external(request_path), 1)
    request = decode(raw)
    require(set(request) == {"schema", "original_ledger", "policy", "measurements", "provenance"} and
            request["schema"] == "opensim-release-closure-request-v1", "Invalid closure request fields/version")
    original, ledger_path = reference(request["original_ledger"])
    require(original.get("schema") == "opensim-release-evidence-v2", "Closure requires original V2 ledger")
    original_bytes, original_stamp = _audit_file(ledger_path, 1)
    require(sha(original_bytes) == request["original_ledger"]["sha256"],
            "Closure original ledger changed while reading")
    now = datetime.now(timezone.utc)
    observed = evaluate(root, commit, ledger_path, approval_pin, now, measured_context=True)
    expected = observed["candidate"]
    executing_gate = read_file(Path(__file__))
    require(executing_gate == committed(root, commit, "tools/release_gate.py"),
            "Executing closure gate differs from candidate")
    path = root / "tools/release_obligations.py"
    body = committed(root, commit, "tools/release_obligations.py")
    local = Path(__file__).resolve().with_name("release_obligations.py")
    require(read_file(local) == body, "Executing measured module differs from candidate")
    spec = importlib.util.spec_from_file_location("opensim_measured_closure", path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(body, str(path), "exec"), module.__dict__)
    closure, records = module.evaluate(request["policy"], request["measurements"], expected, sha(body))
    require(closure["open"] == [], "Measured obligations remain OPEN")
    targets = [("human", rule, pin["sha256"]) for rule, pin in original["manual"].items()
               if pin != {"status": "EXCEPTION"}]
    targets.extend(("deployment", str(i), pin["sha256"]) for i, pin in
                   enumerate([request["original_ledger"], *closure["materials"]]))
    if original["exceptions"]:
        targets.append(("user-approval", "register", approval_pin["sha256"]))
    trust, trust_source = closure_trust()
    trust_result, trust_records, unavailable = verify_closure_provenance(
        trust, request["provenance"], targets, commit)
    def verify():
        module.verify_records(records)
        for location, frame, stamp in trust_records:
            require(_audit_file(location, 1) == (frame, stamp), "Closure late provenance drift")
        require(identity(root, commit) == expected and committed(root, commit, "tools/release_obligations.py") == body and
                read_file(Path(__file__)) == executing_gate and
                _audit_file(request_path, 1) == (raw, request_stamp) and
                _audit_file(ledger_path, 1) == (original_bytes, original_stamp), "Closure late candidate/ledger drift")
        require(evaluate(root, commit, ledger_path, approval_pin,
                         measured_context=True)["candidate"] == expected,
                "Closure original final readback changed")
        for role, filename in (("obligations", "release_obligations.py"), ("trust", "release_trust.py")):
            bridge_implementation(original["bridge"]["implementations"][role], filename)
        require(_audit_file(trust_source[0], 1, base=Path("/data/dev")) == trust_source[1:],
                "Closure trust source late drift")
        late, _, late_unavailable = verify_closure_provenance(trust, request["provenance"], targets, commit)
        def stable_proofs(value):
            return [dict(p, verification={k: v for k, v in p["verification"].items()
                                          if k != "checked_at"}) for p in value["materials"]]
        require(late_unavailable == unavailable and late["production"] == trust_result["production"] and
                stable_proofs(late) == stable_proofs(trust_result),
                "Closure trust final verification differs")

    verify()
    if publication_guards is not None:
        publication_guards.append(verify)
    if unavailable:
        observation = {"candidate": expected, "closure": closure, "scope": "MEASURED_CLOSURE_NOT_TRUSTED_RELEASE",
                       "trust": unavailable, "trust_implementation": CLOSURE_TRUST_IMPLEMENTATION, "source_stable": True,
                       "original_statuses_not_promoted": True, "release_verdict": "NOT_CERTIFIED"}
        raise BridgeBlocked("BLOCKED: complete measured predicates do not replace trusted human/producer provenance", observation)
    return certified_terminal(expected, original, request["original_ledger"], closure,
                              trust_result, approval_pin, now=now)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--commit", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--ledger", type=Path)
    mode.add_argument("--native-assessment", type=Path)
    mode.add_argument("--closure-request", type=Path)
    parser.add_argument("--approval-path")
    parser.add_argument("--approval-sha256")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        new_result_paths(args.out)
    except Exception as exc:
        print(f"Output admission refused: {exc}", file=sys.stderr)
        return 1
    if args.native_assessment is not None:
        return native_assessment_main(args)
    report = {"schema": "opensim-release-gate-v1", "verdict": "BLOCKED",
              "release_verdict": "NOT_CERTIFIED", "production_changes": False,
              "collected_at": datetime.now(timezone.utc).isoformat()}
    guards = []
    try:
        require(os.geteuid() != 0, "Release evidence gate must not run as root")
        require(bool(args.approval_path) == bool(args.approval_sha256),
                "Approval path and SHA must be supplied together")
        pin = ({"path": args.approval_path, "sha256": args.approval_sha256}
               if args.approval_path else None)
        report = (evaluate_closure(args.source_root, args.commit, args.closure_request, pin,
                                  publication_guards=guards) if args.closure_request is not None else
                  evaluate(args.source_root, args.commit, args.ledger, pin))
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
        if isinstance(exc, BridgeBlocked):
            report["bridge"] = exc.observation
    rc = 0 if report["verdict"] == "PASS" else 1
    report["exit_code"] = rc
    try:
        return finalize_result(report, args.out, status_key="verdict",
                               failed_status="BLOCKED", label="OPENSIM_RELEASE_GATE",
                               verify=(lambda: [guard() for guard in guards]) if args.closure_request is not None else None,
                               emit=native_assessment_stdout if args.closure_request is not None else None)
    except Exception as exc:
        print(f"Finalization refused: {exc}; first result={report}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
