"""Non-root release owner; no root dispatch or approved persistent executor yet.

Production admission reuses the complete gate and immutable bundle validator.
The transaction engine is exercised by explicitly labelled, non-root fixtures.
Neither fixtures, the timer, nor a writable CERTIFIED receipt authorizes release.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import importlib.util
import ipaddress
import math
import os
from pathlib import Path
import re
import stat
import sys
from uuid import uuid4
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_release as sync

gate = sync.gate
SCHEMA = "opensim-continuous-release-v1"
SOURCE_ROOT = Path("/opt/opensim-sync/candidate")
STATE_ROOT = Path("/var/lib/opensim-sync/continuous")
CONFIG = Path("/var/lib/opensim-sync/continuous-request.json")
EVIDENCE_ROOT = gate.ARTIFACT_ROOT / "continuous-release"
PUBLIC_GATE_SHA = "5fd9ed850e68a39cdfb303655cb1e6f1727d060b8ae9e520cf82b98d7c9b66e3"
COLLECTOR_LIBRARIES = (
    (Path("/data/dev/infra/wt-opensim-authorized-invoke-20261010/ops/opensim-deploy/invoke_runtime.py"),
     "a09d15d95a940bb1ce1a0b7cc4c9fb314204157b9027913346b4e90df5a9377a"),
    (Path("/data/dev/infra/ops/opensim-deploy/inspect_release.py"),
     "ec6a9b3754793b648e72a64a74baa15e1e8bac9f188730115e5c6d049a8c36de"),
)
PUBLIC_OBSERVATION_FIELDS = {
    "report", "actual_rc", "stderr_bytes", "source_before", "source_after",
    "producer", "stdout_sha256", "canonical_report_sha256", "capture",
    "artifacts", "directory", "collector_dependencies", "collection", "collection_root",
}
# Deliberately no user-selectable import, executable, sudo, or broker.
APPROVED_EXECUTOR = None
TERMINAL = {"BLOCKED", "PUBLISHED", "FAILED_REVERTED", "FIXTURE_SYNCHRONIZED"}
PLAN_FIELDS = {"schema", "state", "scope", "request_id", "request_sha256", "commit",
               "manifest_sha256", "release", "page_build", "source_epoch",
               "gate_receipt", "production_changes", "controller", "collected_at"}
CLOSURE_PLAN_FIELDS = PLAN_FIELDS - {"gate_receipt"} | {"closure_request"}
CLOSURE_ROOT = Path("/data/dev/opensim-s4-sync-closure-20261011/tools")
CLOSURE_SOURCES = {
    "release_gate.py": "55dc52edc95115e5077add86dc4d48446d48567054e4a8bab7ad69e587653ae3",
    "release_obligations.py": "36f6912c6bb98f0c6e338a73a478891c0d2528a41d6700dc47c5ff64e62c509a",
    "sync_release.py": "c1b483654bee51984ebf00b79faabc4f014e75843299f3b06a8eaf94aeecbd14",
}
CLOSURE_LIBRARIES = (
    (Path("/data/dev/opensim-production-trust-20261011/tools/release_trust.py"),
     "1bc76e30b0a80a92efa555b014c70afb237a178b71f6311c55f1059f588358af"),
    (Path("/data/dev/opensim-authorized-obligations-20261010/tools/release_obligations.py"),
     "9c1b0d9a464c958d6d32c760ba39173da6134ad771436ddc7a36466ab3a8b4bf"),
    (Path("/data/dev/opensim-authorized-trust-20261010/tools/release_trust.py"),
     "ce085ad4062327c6d95b6d274f66897fbb2440ab2170404dd03b07986bd59639"),
)


class Refused(RuntimeError):
    pass


def require(condition: bool, code: str) -> None:
    if not condition:
        raise Refused(code)


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def failure(exc: Exception) -> dict:
    # Internal refusal codes are public; arbitrary module exception messages
    # might contain private receipts or credentials and are never serialized.
    result = {"type": type(exc).__name__,
            "code": str(exc) if isinstance(exc, Refused) else
                    getattr(exc, "closure_refusal_code", "DEPENDENCY_FAILED"),
            "errno": getattr(exc, "errno", None)}
    if hasattr(exc, "revocation_cause"):
        result["revocation_cause"] = exc.revocation_cause
    if hasattr(exc, "finalization_secondary"):
        result["finalization_secondary"] = exc.finalization_secondary
    if hasattr(exc, "closure_readback_secondary"):
        result["closure_readback_secondary"] = exc.closure_readback_secondary
    return result


def metadata(info) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_owned(path: Path, mode: int = 0o600) -> tuple[bytes, tuple]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and
                before.st_uid == os.geteuid() and stat.S_IMODE(before.st_mode) == mode,
                "STATE_FILE_OWNER_MODE")
        raw = stream.read(gate.LIMIT + 1)
        require(len(raw) <= gate.LIMIT and metadata(before) == metadata(os.fstat(stream.fileno()))
                and metadata(before) == metadata(path.lstat()), "STATE_FILE_DRIFT")
    return raw, metadata(before)


def create(path: Path, value: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(gate.encode(value))
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def observe_file(path: Path, *, owned: bool = False) -> dict:
    if owned:
        raw, identity = read_owned(path)
    else:
        before = path.lstat()
        raw = gate.read_file(path)
        require(metadata(before) == metadata(path.lstat()) and before.st_nlink == 1,
                "SOURCE_OR_ARTIFACT_DRIFT")
        identity = metadata(before)
    return {"path": str(path), "sha256": gate.sha(raw), "metadata": list(identity)}


def verify_observation(pin: dict, *, owned: bool = False) -> None:
    require(isinstance(pin, dict) and set(pin) == {"path", "sha256", "metadata"},
            "OBSERVATION_SCHEMA")
    actual = (observe_stream(Path(pin["path"])) if not owned and pin["metadata"][6] == 0
              else observe_file(Path(pin["path"]), owned=owned))
    require(actual == pin,
            "SOURCE_OR_ARTIFACT_DRIFT")


def observe_directory(path: Path) -> dict:
    gate.external(path)
    before = path.lstat()
    require(stat.S_ISDIR(before.st_mode) and before.st_uid == os.geteuid(),
            "PUBLIC_DIRECTORY_OWNER")
    entries = sorted(item.name for item in path.iterdir())
    require(metadata(before) == metadata(path.lstat()), "PUBLIC_DIRECTORY_DRIFT")
    return {"path": str(path), "metadata": list(metadata(before)), "entries": entries}


def verify_dependency(pin: dict, *, owned: bool = False) -> None:
    if isinstance(pin, dict) and set(pin) == {"path", "sha256", "metadata", "native_pair"}:
        with closure_api() as (owner, _):
            require(closure_material(owner, {"path": pin["path"], "sha256": pin["sha256"]}) == pin,
                    "CLOSURE_NATIVE_PAIR_DRIFT")
    elif isinstance(pin, dict) and set(pin) == {"path", "metadata", "entries"}:
        require(observe_directory(Path(pin["path"])) == pin, "PUBLIC_DIRECTORY_DRIFT")
    else:
        verify_observation(pin, owned=owned)


def collector_libraries() -> tuple:
    modules, pins = [], []
    for path, digest in COLLECTOR_LIBRARIES:
        pin = observe_file(path)
        require(pin["sha256"] == digest, "COLLECTOR_LIBRARY_SHA_DRIFT")
        body = gate.read_file(path)
        require(gate.sha(body) == digest, "COLLECTOR_LIBRARY_SHA_DRIFT")
        name = "_public_capture_" + uuid4().hex
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        exec(compile(body, str(path), "exec"), module.__dict__)
        verify_observation(pin)
        modules.append(module)
        pins.append(pin)
    return modules[0], modules[1], pins


def source_set(root: Path) -> list:
    require(root.is_absolute() and ".." not in root.parts and
            all(not part.is_symlink() for part in (root, *root.parents)),
            "PUBLIC_SOURCE_ROOT_UNSAFE")
    return [observe_file(root / name) for name in (*gate.SOURCES, "tools/public_gate.py")]


def source_set_sha256(pins: list) -> str:
    root = Path(pins[-1]["path"]).parents[1]
    return gate.sha(gate.encode({name: next(pin["sha256"] for pin in pins
                                          if pin["path"] == str(root / name))
                                 for name in gate.SOURCES}))


def public_line(report: dict, path: Path) -> bytes:
    reason = report.get("failure", {}).get("type", "none")
    return ("OPENSIM_PUBLIC_GATE verdict=" + report["verdict"] + " scope=" + report["scope"] +
            " collected_at=" + report["collected_at"] + " checks=" +
            str(len(report["checks"])) + "/4 failure=" + reason + " report=" +
            str(path) + "\n").encode("utf-8")


def verify_public_artifacts(observation: dict) -> None:
    artifacts = observation["artifacts"]
    require(isinstance(artifacts, dict) and set(artifacts) ==
            {"report", "log", "stdout", "stderr"}, "PUBLIC_ARTIFACT_SET")
    directory = observation["directory"]
    report_path = Path(artifacts["report"]["path"])
    log_path = Path(artifacts["log"]["path"])
    require(report_path.parent == Path(directory["path"]) and
            re.fullmatch(r"[0-9]{8}T[0-9]{6}-[0-9a-f]{32}\.json", report_path.name) and
            log_path == report_path.with_suffix(".log") and
            directory["entries"] == sorted([report_path.name, log_path.name]),
            "PUBLIC_REPORT_LOG_INVENTORY")
    for pin in artifacts.values():
        gate.external(Path(pin["path"]))
        verify_observation(pin)
    verify_dependency(directory)
    report_raw = gate.read_file(report_path)
    report = gate.decode(report_raw)
    require(report == observation["report"] and
            observation["canonical_report_sha256"] == gate.sha(gate.encode(report)),
            "PUBLIC_RAW_REPORT_BINDING")
    line = public_line(report, report_path)
    require(gate.read_file(log_path) == line, "PUBLIC_LOG_VERDICT_BINDING")
    expected_stdout = line
    if report["verdict"] == "FAIL":
        expected_stdout += ("First cause: " + report["failure"]["reason"] + "\n").encode("utf-8")
    stdout = gate.read_file(Path(artifacts["stdout"]["path"]))
    require(stdout == expected_stdout and gate.sha(stdout) == observation["stdout_sha256"],
            "PUBLIC_PRODUCER_STDOUT_BINDING")
    row = observation["capture"]
    require(row["actual_rc"] == observation["actual_rc"] and
            row["argv"] == ["/usr/bin/python3", "-I", "-B", observation["producer"],
                            "--out-dir", directory["path"]] and
            row["capture_failure"] is None, "PUBLIC_CAPTURE_BINDING")
    for name in ("stdout", "stderr"):
        stream = row["streams"][name]
        pin = artifacts[name]
        require(stream == {"sha256": pin["sha256"], "bytes": pin["metadata"][6],
                           "complete": True, "oversized": False}, "PUBLIC_STREAM_BINDING")
    require(row["streams"]["stderr"]["bytes"] == observation["stderr_bytes"],
            "PUBLIC_STDERR_BINDING")


def verify_collection(observation: dict) -> None:
    collection = observation["collection"]
    verify_observation(collection)
    out = Path(collection["path"]).parent
    require(observation["collection_root"]["path"] == str(out), "PUBLIC_COLLECTION_ROOT")
    verify_dependency(observation["collection_root"])
    require(Path(observation["directory"]["path"]) == out / "producer" and
            {item.name for item in out.iterdir()} == {
                "START.json", "capture.json", "stdout.bin", "stderr.bin", "producer",
                "collection.json", "execution.log"}, "PUBLIC_COLLECTION_INVENTORY")
    for name in ("stdout", "stderr"):
        require(Path(observation["artifacts"][name]["path"]) == out / (name + ".bin"),
                "PUBLIC_STREAM_PATH_BINDING")
    saved = gate.decode(gate.read_file(Path(collection["path"])))
    require(saved["state"] == "COLLECTED_NOT_CERTIFIED" and saved["exit_code"] == 0 and
            saved["actual_rc"] == observation["actual_rc"] and saved["first_cause"] is None and
            saved["observation"] == {key: value for key, value in observation.items()
                                     if key not in {"collection", "collection_root"}},
            "PUBLIC_COLLECTION_TERMINAL")
    verify_public_artifacts(observation)
    for pin in public_dependencies(observation):
        verify_dependency(pin)


def public_dependencies(observation: dict) -> list:
    return [*observation["source_before"], *observation["collector_dependencies"],
            *observation["artifacts"].values(), observation["directory"],
            observation["collection"], observation["collection_root"]]


def collect_public(root: Path, out: Path, expected_sources: str, expected_commit: str,
                   *, runner=None) -> dict:
    """Fixed original CLI; the private runner seam only maps real test children."""
    require(os.geteuid() != 0, "NEVER_CHECKOUT_AS_ROOT")
    capture_module, storage, library_pins = collector_libraries()
    out = storage.output_path(out)
    out.mkdir(mode=0o700)
    result = {"schema": "opensim-public-cli-collection-v1", "state": "NOT_LAUNCHED",
              "exit_code": 2, "actual_rc": None, "first_cause": None, "secondary": [],
              "observation": None, "commands": [], "certified": False,
              "source_before": None, "source_after": None,
              "release_verdict": "NOT_CERTIFIED", "production_operations": 0,
              "started_at": stamp()}
    observation = None
    before = None
    try:
        require(isinstance(expected_sources, str) and
                re.fullmatch(r"[0-9a-f]{64}", expected_sources) and
                isinstance(expected_commit, str) and
                re.fullmatch(r"[0-9a-f]{40}", expected_commit), "PUBLIC_EXPLICIT_PINS_REQUIRED")
        before = source_set(root)
        result["source_before"] = before
        require(source_set_sha256(before) == expected_sources and
                before[-1]["sha256"] == PUBLIC_GATE_SHA, "PUBLIC_SOURCE_PIN_MISMATCH")
        dependencies = library_pins + [observe_file(Path(module.__file__))
                                      for module in (sys.modules[__name__], sync, gate)]
        storage.write_new(out / "START.json", gate.encode({"source_before": before,
            "dependencies": dependencies, "expected_commit": expected_commit,
            "expected_source_set_sha256": expected_sources, "production_operations": 0}))
        producer_out = out / "producer"
        producer_out.mkdir(mode=0o700)
        require(not list(producer_out.iterdir()), "PUBLIC_OUTPUT_NOT_EMPTY")
        argv = ["/usr/bin/python3", "-I", "-B", str(root / "tools/public_gate.py"),
                "--out-dir", str(producer_out)]
        active = runner if runner is not None else capture_module.Runner()
        row, bodies = capture_module.run_fixed(active, argv, result["commands"], "public-gate")
        result["actual_rc"] = row["actual_rc"]
        if row["actual_rc"] != 0:
            result["first_cause"] = {"code": "PUBLIC_PRODUCER_PROCESS_FAILED",
                                     "actual_rc": row["actual_rc"]}
        elif bodies["stderr"]:
            result["first_cause"] = {"code": "PUBLIC_PRODUCER_PROCESS_FAILED",
                                     "stderr_bytes": len(bodies["stderr"])}
        storage.write_new(out / "capture.json", gate.encode(row))
        if row["capture_failure"] is not None:
            result["first_cause"] = row["capture_failure"]
            raise Refused("PUBLIC_CAPTURE_FAILED")
        require(not any(item["oversized"] for item in row["streams"].values()),
                "PUBLIC_STREAM_OVERSIZED")
        storage.write_new(out / "stdout.bin", bodies["stdout"])
        storage.write_new(out / "stderr.bin", bodies["stderr"])
        directory = observe_directory(producer_out)
        paths = [producer_out / name for name in directory["entries"] if name.endswith(".json")]
        require(len(paths) == 1, "PUBLIC_REPORT_LOG_INVENTORY")
        path = paths[0]
        # The original writer closes but does not fsync its raw evidence.
        for raw_path in (path, path.with_suffix(".log")):
            fd = os.open(raw_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                        "PUBLIC_RAW_FILE_UNSAFE")
                os.fsync(fd)
            finally:
                os.close(fd)
        gate.sync_directory(producer_out)
        report = gate.decode(gate.read_file(path))
        observation = {"report": report, "actual_rc": row["actual_rc"],
            "stderr_bytes": len(bodies["stderr"]), "source_before": before,
            "source_after": source_set(root), "producer": str(root / "tools/public_gate.py"),
            "stdout_sha256": gate.sha(bodies["stdout"]),
            "canonical_report_sha256": gate.sha(gate.encode(report)), "capture": row,
            "artifacts": {"report": observe_file(path), "log": observe_file(path.with_suffix(".log")),
                          "stdout": observe_file(out / "stdout.bin"),
                          "stderr": observe_stream(out / "stderr.bin")},
            "directory": directory,
            "collector_dependencies": dependencies + [observe_file(out / "START.json"),
                                                        observe_file(out / "capture.json")]}
        require(report.get("desired_git_head") == expected_commit, "PUBLIC_COMMIT_MISMATCH")
        require(before == observation["source_after"], "PUBLIC_PRODUCER_SOURCE_DRIFT")
        verify_public_artifacts(observation)
        require(result["first_cause"] is None and report.get("verdict") == "PASS",
                "PUBLIC_PRODUCER_PROCESS_FAILED")
        context = {"commit": expected_commit, "identity": {
            "source_sha256": {name: next(pin["sha256"] for pin in before
                                       if pin["path"] == str(root / name))
                              for name in gate.SOURCES},
            "page_build": report.get("page_build")}}
        check_public(report, context, fixture=runner is not None)
        result["state"] = "COLLECTED_NOT_CERTIFIED"
        result["exit_code"] = 0
    except Exception as exc:
        if result["commands"] and result["actual_rc"] is None:
            result["actual_rc"] = result["commands"][-1]["actual_rc"]
        if result["first_cause"] is None:
            result["first_cause"] = failure(exc)
        else:
            result["secondary"].append(failure(exc))
        result["state"] = "COLLECTION_FAILED_NOT_CERTIFIED"
    finally:
        if before is not None:
            try:
                result["source_after"] = source_set(root)
                require(before == result["source_after"], "PUBLIC_PRODUCER_SOURCE_DRIFT")
                for pin in library_pins:
                    verify_observation(pin)
            except Exception as exc:
                if result["first_cause"] is None:
                    result["first_cause"] = failure(exc)
                else:
                    result["secondary"].append(failure(exc))
                result["state"], result["exit_code"] = "COLLECTION_FAILED_NOT_CERTIFIED", 2
    result["source_stable"] = before is not None and before == result["source_after"]
    result["finished_at"] = stamp()
    result["observation"] = observation
    try:
        storage.write_new(out / "execution.log", ("CONTINUOUS_PUBLIC state=" + result["state"] +
            " actual_public_rc=" + str(result["actual_rc"]) +
            " release_verdict=NOT_CERTIFIED\n").encode("ascii"))
        if observation is not None:
            observation["collector_dependencies"].append(observe_file(out / "execution.log"))
        storage.write_new(out / "collection.json", gate.encode(result))
        if observation is not None:
            observation["collection"] = observe_file(out / "collection.json")
            observation["collection_root"] = observe_directory(out)
        if result["exit_code"] == 0:
            verify_collection(observation)
    except Exception as exc:
        result["exit_code"], result["state"] = 3, "EVIDENCE_FAILED"
        if result["first_cause"] is None:
            result["first_cause"] = failure(exc)
        else:
            result["secondary"].append(failure(exc))
        try:
            storage.write_new(out / "collection.failure.json", gate.encode(result))
        except Exception as secondary:
            result["secondary"].append(failure(secondary))
            print("PUBLIC_COLLECTION_VETO_UNAVAILABLE type=" + type(secondary).__name__,
                  file=sys.stderr, flush=True)
        raise Refused("PUBLIC_COLLECTION_EVIDENCE_FAILED") from exc
    return result


def observe_stream(path: Path) -> dict:
    # stderr can legitimately be empty; ordinary evidence/source readers cannot.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, "PUBLIC_STREAM_UNSAFE")
        raw = stream.read(16385)
        require(len(raw) <= 16384 and metadata(before) == metadata(os.fstat(stream.fileno())) and
                metadata(before) == metadata(path.lstat()), "PUBLIC_STREAM_DRIFT")
    return {"path": str(path), "sha256": gate.sha(raw), "metadata": list(metadata(before))}


def binding(value: dict) -> dict:
    fields = {"request_sha256", "commit", "manifest_sha256", "identity", "plan"}
    require(isinstance(value, dict) and set(value) in (fields, fields | {"closure_evidence"}),
            "ADMISSION_FIELDS")
    plan = value["plan"]
    require(isinstance(plan, dict) and set(plan) in (PLAN_FIELDS, CLOSURE_PLAN_FIELDS), "PLAN_FIELDS")
    require("closure_evidence" not in value or set(plan) == CLOSURE_PLAN_FIELDS,
            "CLOSURE_EVIDENCE_REQUIRES_V2_PLAN")
    if "closure_evidence" in value:
        verify_closure_evidence(value["closure_evidence"])
    gate.time_value(plan["collected_at"])
    # Each real closure invocation has distinct raw output. Compare the desired
    # plan, while retaining every invocation as terminal dependencies separately.
    return {**{key: item for key, item in value.items() if key != "closure_evidence"},
            "plan": {key: item for key, item in plan.items() if key != "collected_at"}}


def veto(run: Path, exc: Exception, report: dict) -> None:
    """Presence vetoes replay even if its write/fsync subsequently fails."""
    path = run / "finalization-veto.json"
    if not os.path.lexists(path):
        create(path, {"schema": SCHEMA, "first_cause": report.get("first_cause") or failure(exc),
                      "finalization_cause": failure(exc), "state": "FAILED_FINALIZATION",
                      "secondary": report.get("secondary", []),
                      "operations": report.get("operations", {}),
                      "notification": report.get("notification", "NOT_ATTEMPTED")})


class Store:
    """Local single owner; a missing terminal report blocks further generations."""

    def __init__(self, root: Path):
        require(os.geteuid() != 0, "NEVER_CHECKOUT_AS_ROOT")
        require(root.is_absolute() and ".." not in root.parts, "ABSOLUTE_STATE_ROOT_REQUIRED")
        self.root = root
        info = root.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
                stat.S_IMODE(info.st_mode) == 0o700, "STATE_ROOT_OWNER_MODE")
        self.root_identity = (info.st_dev, info.st_ino, info.st_uid, info.st_mode)
        self.owner = root / "owner.json"
        self.runs = root / "runs"

    def verify_root(self) -> None:
        info = self.root.lstat()
        require((info.st_dev, info.st_ino, info.st_uid, info.st_mode) == self.root_identity,
                "STATE_PARENT_DRIFT")
        require(set(p.name for p in self.root.iterdir()) ==
                {"owner.lock", "owner.json", "runs"}, "UNKNOWN_STATE_ENTRY")
        info = self.runs.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
                stat.S_IMODE(info.st_mode) == 0o700, "RUNS_OWNER_MODE")
        require(all(str_uuid(path.name) for path in self.runs.iterdir()),
                "UNKNOWN_HISTORY_ENTRY")

    @contextmanager
    def locked(self, *, initialize: bool = False):
        names = set(p.name for p in self.root.iterdir())
        require(names == {"owner.lock", "owner.json", "runs"} or
                (initialize and not names), "STATE_UNINITIALIZED_OR_UNKNOWN")
        fd = os.open(self.root / "owner.lock",
                     os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT | os.O_EXCL if not names else 0),
                     0o600)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                    info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600,
                    "LOCK_OWNER_MODE")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise Refused("OWNER_BUSY_NO_REPLAY") from exc
            require(metadata(info) == metadata((self.root / "owner.lock").lstat()),
                    "LOCK_PATH_DRIFT")
            if not names:
                self.runs.mkdir(mode=0o700)
                create(self.owner, {"schema": SCHEMA, "generation": 0,
                                    "last_run": None, "report_sha256": None, "closure": None})
            self.verify_root()
            yield
        finally:
            os.close(fd)

    def current(self, *, terminal_only: bool = False, pending: dict | None = None) -> tuple[dict, tuple]:
        self.verify_root()
        for run in self.runs.iterdir():
            path = run / "admission-pending.json"
            if os.path.lexists(path):
                require(pending is not None and pending["path"] == str(path),
                        "ADMISSION_PENDING_NO_REPLAY")
                verify_observation(pending, owned=True)
        raw, identity = read_owned(self.owner)
        value = gate.decode(raw)
        require(set(value) == {"schema", "generation", "last_run", "report_sha256", "closure"} and
                value["schema"] == SCHEMA and type(value["generation"]) is int and
                value["generation"] >= 0, "OWNER_SCHEMA")
        if value["last_run"] is None:
            require(value["generation"] == 0 and value["report_sha256"] is None and
                    value["closure"] is None and not list(self.runs.iterdir()),
                    "UNKNOWN_INITIAL_HISTORY")
        else:
            require(isinstance(value["last_run"], str) and
                    str_uuid(value["last_run"]), "UNKNOWN_OWNER_RUN")
            require(isinstance(value["report_sha256"], str), "INTERRUPTED_OWNER_NO_REPLAY")
            run = self.runs / value["last_run"]
            info = run.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
                    stat.S_IMODE(info.st_mode) == 0o700, "RUN_OWNER_MODE")
            require(not os.path.lexists(run / "finalization-veto.json"),
                    "FAILED_FINALIZATION_NO_REPLAY")
            raw, _ = read_owned(run / "report.json")
            require(gate.sha(raw) == value["report_sha256"], "OWNER_REPORT_DRIFT")
            report = gate.decode(raw)
            allowed = TERMINAL | ({"FAILED_RECOVERY_REQUIRED"} if terminal_only else set())
            require(report["state"] in allowed and
                    report["generation_id"] == value["last_run"] and
                    report["generation"] == value["generation"] and
                    (terminal_only or report["notification"] != "FAILED"),
                    "UNRESOLVED_OWNER_NO_REPLAY")
            closure, _ = read_owned(run / "closure.json")
            require(value["closure"] == observe_file(run / "closure.json", owned=True),
                    "CLOSURE_OWNER_BINDING")
            closed = gate.decode(closure)
            require(set(closed) == {"report", "terminal", "dependencies"},
                    "CLOSURE_SCHEMA")
            terminal, _ = read_owned(run / "terminal.json")
            require(closed["report"] == observe_file(run / "report.json", owned=True) and
                    closed["terminal"] == observe_file(run / "terminal.json", owned=True) and
                    gate.decode(terminal) == {"state": report["state"],
                                             "exit_code": report["exit_code"],
                                             "finished_at": report["finished_at"]},
                    "TERMINAL_CLOSURE_DRIFT")
            require(closed["dependencies"] == report["dependencies"] and
                    set(p.name for p in run.iterdir()) ==
                    {Path(pin["path"]).name for pin in report["dependencies"]
                     if Path(pin["path"]).parent == run} |
                    {"report.json", "terminal.json", "closure.json"} |
                    ({"admission-pending.json"} if pending is not None else set()),
                    "RUN_INVENTORY_DRIFT")
            for pin in report["dependencies"]:
                verify_dependency(pin, owned=Path(pin["path"]).parent == run)
            public_path = run / "public-producer.json"
            if public_path.exists():
                observation = gate.decode(read_owned(public_path)[0])
                if report["public_evidence"] is not None and observation.get("collection") is not None:
                    verify_collection(observation)
        expected = read_owned(self.owner)
        require(expected[1] == identity and gate.decode(expected[0]) == value, "OWNER_READ_DRIFT")
        return value, expected

    def replace_owner(self, expected: tuple, value: dict) -> tuple:
        self.verify_root()
        require(read_owned(self.owner) == expected, "OWNER_CAS_DRIFT")
        pending = self.root / ("owner-" + uuid4().hex + ".pending")
        create(pending, value)
        info = pending.lstat()
        owned = (info.st_dev, info.st_ino)
        require(read_owned(self.owner) == expected, "OWNER_CAS_DRIFT")
        os.replace(pending, self.owner)
        try:
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except Exception as first:
            try:
                raw, observed = read_owned(self.owner)
                require(observed[:2] == owned and raw == gate.encode(value),
                        "OWNER_REPLACEMENT_NOT_OWNED_NO_REVOCATION")
                # Only this just-replaced inode is revoked; missing owner refuses replay.
                os.unlink(self.owner)
                fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except Exception as secondary:
                first.revocation_cause = failure(secondary)
            raise
        observed = read_owned(self.owner)
        require(observed[1][:2] == owned and observed[0] == gate.encode(value),
                "OWNER_CAS_DRIFT")
        return observed


def str_uuid(value: str) -> bool:
    from uuid import UUID
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


@contextmanager
def closure_api():
    """Load only the reviewed fixed v2 owner; leave legacy module bindings intact."""
    require(os.geteuid() != 0, "NEVER_CHECKOUT_AS_ROOT")
    pins = [observe_file(CLOSURE_ROOT / name) for name in CLOSURE_SOURCES]
    require(all(pin["sha256"] == CLOSURE_SOURCES[Path(pin["path"]).name]
                for pin in pins), "CLOSURE_OWNER_SOURCE_PIN")
    for path, digest in CLOSURE_LIBRARIES:
        pin = observe_file(path)
        require(pin["sha256"] == digest, "CLOSURE_LIBRARY_SOURCE_PIN")
        pins.append(pin)
    previous = sys.modules.get("release_gate")
    old_path = list(sys.path)
    names = []
    modules = []
    first = None
    try:
        for filename in ("release_gate.py", "sync_release.py"):
            path = CLOSURE_ROOT / filename
            body = gate.read_file(path)
            require(gate.sha(body) == CLOSURE_SOURCES[filename], "CLOSURE_OWNER_SOURCE_PIN")
            name = "_continuous_closure_" + uuid4().hex
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            names.append(name)
            exec(compile(body, str(path), "exec"), module.__dict__)
            modules.append(module)
            if filename == "release_gate.py":
                sys.modules["release_gate"] = module
        for pin in pins:
            verify_observation(pin)
        yield modules[1], pins
    except Exception as exc:
        first = exc
        if modules and isinstance(exc, modules[0].GateError):
            exc.closure_refusal_code = str(exc)
        raise
    finally:
        sys.path[:] = old_path
        if previous is None:
            sys.modules.pop("release_gate", None)
        else:
            sys.modules["release_gate"] = previous
        for name in names:
            sys.modules.pop(name, None)
        try:
            for pin in pins:
                verify_observation(pin)
        except Exception as exc:
            if first is None:
                raise
            first.closure_readback_secondary = [*getattr(first, "closure_readback_secondary", []),
                                                 failure(exc)]


def closure_material(owner, pin: dict) -> dict:
    path = Path(pin["path"])
    if path.lstat().st_nlink == 2:
        owner.gate.audit_reference(pin)
        before = path.lstat()
        other = path.with_name("report.candidate.json")
        twin = other.lstat()
        raw = owner.gate._audit_file(path, 2)[0]
        pair = owner.gate._audit_file(other, 2)[0]
        require(raw == pair and (before.st_dev, before.st_ino) == (twin.st_dev, twin.st_ino)
                and metadata(before) == metadata(path.lstat())
                and metadata(twin) == metadata(other.lstat()), "CLOSURE_NATIVE_PAIR_DRIFT")
        return {"path": str(path), "sha256": gate.sha(raw), "metadata": list(metadata(before)),
                "native_pair": {"path": str(other), "sha256": gate.sha(pair),
                                "metadata": list(metadata(twin))}}
    result = observe_stream(path) if path.lstat().st_size == 0 else observe_file(path)
    require(result["sha256"] == pin["sha256"], "CLOSURE_INPUT_PIN_DRIFT")
    return result


def closure_inputs(owner, desired: dict, root: Path) -> list:
    """Follow public pinned material documents, not arbitrary receipt paths."""
    records, directories = {}, set()
    def visit(value, parent=None):
        if isinstance(value, dict) and set(value) == {"path", "sha256"}:
            location = Path(value["path"])
            if not location.is_absolute():
                location = root / owner.gate.relative(value["path"])
            base = Path("/data/dev") if location.is_relative_to(Path("/data/dev")) else gate.ARTIFACT_ROOT
            owner.gate.external(location, base)
            pin = {"path": str(location), "sha256": value["sha256"]}
            require(isinstance(pin["sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", pin["sha256"]),
                    "CLOSURE_INPUT_PIN_SCHEMA")
            if str(location) in records:
                require(records[str(location)]["sha256"] == pin["sha256"], "CLOSURE_INPUT_PIN_CONFLICT")
                return
            records[str(location)] = closure_material(owner, pin)
            if location.is_relative_to(gate.ARTIFACT_ROOT):
                directories.add(location.parent)
            if location.suffix == ".json":
                raw = (owner.gate._audit_file(location, 2)[0]
                       if "native_pair" in records[str(location)] else owner.gate.read_file(location))
                require(gate.sha(raw) == pin["sha256"], "CLOSURE_INPUT_PIN_DRIFT")
                visit(owner.gate.decode(raw), location.parent)
                verify_dependency(records[str(location)])
        elif isinstance(value, dict) and parent is not None and set(value) == {"file", "sha256"}:
            visit({"path": str(parent / owner.gate.relative(value["file"])),
                   "sha256": value["sha256"]})
        elif isinstance(value, dict):
            for item in value.values():
                visit(item, parent)
        elif isinstance(value, list):
            for item in value:
                visit(item, parent)
    visit(desired["closure_request"])
    if desired["approval_pin"] is not None:
        visit(desired["approval_pin"])
    if desired.get("before_receipt") is not None:
        visit(desired["before_receipt"])
    return [*records.values(), *(observe_directory(path) for path in sorted(directories))]


def closure_dependencies(value: dict) -> list:
    require(isinstance(value, dict) and set(value) == {"owner", "inputs", "sources", "outputs"},
            "CLOSURE_EVIDENCE_FIELDS")
    return [*value["inputs"], *value["sources"], *value["outputs"]]


def verify_closure_evidence(value: dict) -> None:
    for pin in closure_dependencies(value):
        verify_dependency(pin)
    execution = value["owner"].get("closure_execution")
    require(isinstance(execution, dict) and execution.get("actual_child_rc") == 0
            and type(execution["actual_child_rc"]) is int and execution.get("final_source_stable") is True,
            "CLOSURE_SUCCESSFUL_PROCESS_REQUIRED")
    observation = execution["observation"]
    raw = gate.read_file(Path(observation["report"]["path"]))
    report = gate.decode(raw)
    require(report == execution["report"] and gate.sha(raw) == observation["report"]["sha256"]
            and report.get("verdict") == "PASS" and report.get("release_verdict") == "CERTIFIED",
            "CLOSURE_RAW_REPORT_BINDING")


def verify_closure_observation(value: dict) -> None:
    """Read back either real BLOCKED or successful material without promotion."""
    for pin in closure_dependencies(value):
        verify_dependency(pin)
    execution = value["owner"].get("closure_execution")
    if execution is None:
        return
    require(type(execution.get("actual_child_rc")) is int and
            execution["actual_child_rc"] in (0, 1) and
            execution.get("final_source_stable") is True, "CLOSURE_PROCESS_NOT_CLOSED")
    observation = execution["observation"]
    report_raw = gate.read_file(Path(observation["report"]["path"]))
    require(gate.sha(report_raw) == observation["report"]["sha256"] and
            gate.decode(report_raw) == execution["report"], "CLOSURE_RAW_REPORT_BINDING")
    require(gate.decode(gate.read_file(Path(observation["report"]["path"]).parent /
                                     "observation.json")) == observation,
            "CLOSURE_PROCESS_OBSERVATION_DRIFT")
    with closure_api() as (owner, _):
        for name in ("stdout", "stderr"):
            owner.closure_stream_stamp(observation[name])
    report = execution["report"]
    require(report.get("exit_code") == execution["actual_child_rc"] and
            report.get("finalization") == "FINAL_PROCESS_RC_REQUIRED",
            "CLOSURE_PROCESS_RESULT_DRIFT")


def closure_outputs(context: dict, directory: Path) -> list:
    execution = context.get("closure_execution", {})
    if "observation" not in execution:
        return []
    pins = execution["observation"]
    result = [observe_stream(Path(pins[name]["path"])) for name in ("stdout", "stderr")]
    result += [observe_file(Path(pins["report"]["path"])),
               observe_file(Path(execution["execution_log"]["path"])),
               observe_file(directory / "observation.json"), observe_directory(directory)]
    for name in ("stdout", "stderr", "report"):
        require(next(pin for pin in result if pin["path"] == pins[name]["path"])["sha256"] ==
                pins[name]["sha256"], "CLOSURE_OUTPUT_PIN_DRIFT")
    require(result[3]["sha256"] == execution["execution_log"]["sha256"],
            "CLOSURE_LOG_PIN_DRIFT")
    return result


def admit_closure(root: Path, desired_path: Path, digest: str, *, evidence: dict | None = None) -> dict:
    """Run the genuine closure CLI; a recorded observation never grants trust."""
    captured = {} if evidence is None else evidence
    context = {}
    captured.update(owner=context, inputs=[], sources=[], outputs=[])
    guards = []
    with closure_api() as (owner, sources):
        desired, raw = owner.request(desired_path, digest)
        require(desired["schema"] == "opensim-desired-release-v2", "CLOSURE_REQUIRES_DESIRED_V2")
        identity = owner.gate.identity(root, desired["commit"])
        request_pin = observe_file(desired_path)
        captured["sources"] = sources + [observe_file(root / name) for name in
            dict.fromkeys((*owner.gate.SOURCES, *owner.gate.TOOLS, "tools/release_obligations.py"))]
        captured["inputs"] = [request_pin, *closure_inputs(owner, desired, root)]
        directory = owner.gate.OUTPUT_ROOT / ("continuous-closure-" + uuid4().hex)
        first = None
        try:
            plan = owner.plan(root, desired_path, digest, closure_directory=directory,
                              context=context, publication_guards=guards)
            result = context["closure_execution"]["report"]
            require(result.get("release_verdict") == "CERTIFIED" and result.get("verdict") == "PASS"
                    and type(result.get("exit_code")) is int and result["exit_code"] == 0,
                    "COMPLETE_CERTIFIED_GATE_REQUIRED")
            require(gate.read_file(desired_path) == raw and
                    owner.gate.identity(root, desired["commit"]) == identity, "CANDIDATE_DRIFT")
            for verify in guards:
                verify()
            for pin in [request_pin, *sources]:
                verify_observation(pin)
            return {"request_sha256": digest, "commit": desired["commit"],
                    "manifest_sha256": desired["manifest_sha256"], "identity": identity,
                    "plan": plan, "closure_evidence": captured}
        except Exception as exc:
            # The caller persists the actual owner report and first cause, even
            # for missing trust. Never turn an observed BLOCKED into admission.
            exc.closure_evidence = captured
            first = exc
            raise
        finally:
            try:
                captured["outputs"] = closure_outputs(context, directory)
                for verify in guards:
                    verify()
                for pin in closure_dependencies(captured):
                    verify_dependency(pin)
            except Exception as exc:
                if first is None:
                    exc.closure_evidence = captured
                    raise
                first.closure_readback_secondary = [*getattr(first, "closure_readback_secondary", []),
                                                     failure(exc)]


def admit(root: Path, desired_path: Path, digest: str, *, evidence: dict | None = None) -> dict:
    """Do not accept APPROVED_EXCEPTION, cached reports, or editable human PASS."""
    value = gate.decode(gate.read_file(gate.external(desired_path)))
    if value.get("schema") == "opensim-desired-release-v2":
        return admit_closure(root, desired_path, digest, evidence=evidence)
    desired, raw = sync.request(desired_path, digest)
    identity = gate.identity(root, desired["commit"])
    receipt = gate.reference(desired["gate_receipt"])[0]
    require(receipt.get("release_verdict") == "CERTIFIED" and
            receipt.get("verdict") == "PASS" and type(receipt.get("exit_code")) is int and
            receipt["exit_code"] == 0, "COMPLETE_CERTIFIED_GATE_REQUIRED")
    # This reruns the original complete gate, producers, human/trust, and bundle.
    plan = sync.plan(root, desired_path, digest)
    require(gate.read_file(desired_path) == raw and
            gate.identity(root, desired["commit"]) == identity, "CANDIDATE_DRIFT")
    return {"request_sha256": digest, "commit": desired["commit"],
            "manifest_sha256": desired["manifest_sha256"], "identity": identity,
            "plan": plan}


def operation(executor, name: str, context: dict, run: Path) -> dict:
    """Proposed module contract; production registry remains empty."""
    value = getattr(executor, name)(context, run)
    require(isinstance(value, dict) and set(value) == {"actual_rc", "receipt"} and
            type(value["actual_rc"]) is int and value["actual_rc"] == 0,
            name.upper() + "_NOT_ACKNOWLEDGED")
    receipt, _ = gate.reference(value["receipt"])
    path = Path(value["receipt"]["path"])
    require(path.parent == run, name.upper() + "_RECEIPT_OWNED_RUN")
    require(set(receipt) == {"schema", "operation", "generation_id",
                            "request_sha256", "commit", "manifest_sha256", "actual_rc"} and
            receipt["schema"] == "opensim-continuous-executor-v1" and
            receipt["operation"] == name and type(receipt["actual_rc"]) is int and
            receipt["actual_rc"] == 0 and
            all(receipt[key] == context[key] for key in
                ("generation_id", "request_sha256", "commit", "manifest_sha256")),
            name.upper() + "_RECEIPT_BINDING")
    observed = observe_file(path, owned=True)
    require(observed["sha256"] == value["receipt"]["sha256"],
            name.upper() + "_RECEIPT_LATE_DRIFT")
    return {**value, "receipt_observation": observed}


def check_public(value: dict, context: dict, *, fixture: bool) -> None:
    require(isinstance(value, dict), "PUBLIC_REPORT_SCHEMA")
    require(value.get("schema") == 1 and value.get("verdict") == "PASS" and
            value.get("source_sha256") == context["identity"]["source_sha256"] and
            value.get("page_build") == context["identity"]["page_build"],
            "PUBLIC_SOURCE_OR_FEM_FAILED")
    expected = {"runtime_identity", "page_and_assets", "real_fem_circle", "real_fem_rectangle"}
    rows = value.get("checks")
    require(isinstance(rows, list) and len(rows) == 4 and all(isinstance(row, dict) for row in rows) and
            {row.get("name") for row in rows} == expected and
            all(row.get("verdict") == "PASS" for row in rows), "PUBLIC_FEM_CHECKSET_FAILED")
    if fixture:
        origin = urlsplit(value.get("url", ""))
        require(value.get("scope") == "LOCAL_TEST" and
                origin.scheme == "http" and origin.hostname == "127.0.0.1" and
                origin.port is not None and not origin.username and not origin.password and
                not origin.path and not origin.query and not origin.fragment,
                "FIXTURE_IS_NOT_PUBLIC_CERTIFICATION")
    else:
        require(value.get("scope") == "PUBLIC_CODE_AND_FEM" and
                value.get("url") == gate.PUBLIC_URL, "FIXED_PUBLIC_FEM_REQUIRED")
        addresses = value.get("dns_addresses")
        require(isinstance(addresses, list) and addresses and
                all(ipaddress.ip_address(ip).is_global for ip in addresses) and
                value.get("desired_git_head") == context["commit"], "PUBLIC_DNS_COMMIT_BINDING")
    gate.fresh(value, datetime.now(timezone.utc), 120)
    for row in rows:
        if row["name"].startswith("real_fem_"):
            require(isinstance(row.get("config_sha256"), str) and
                    re.fullmatch(r"[a-f0-9]{64}", row["config_sha256"]) and
                    row.get("solver_sha256") == context["identity"]["source_sha256"]["solver.py"] and
                    type(row.get("node_count")) is int and row["node_count"] > 10 and
                    type(row.get("flow_rate_m3_s")) in (int, float) and
                    math.isfinite(row["flow_rate_m3_s"]) and row["flow_rate_m3_s"] > 0 and
                    type(row.get("relative_residual")) in (int, float) and
                    math.isfinite(row["relative_residual"]) and
                    0 <= row["relative_residual"] < 1e-6,
                    "PUBLIC_FEM_METRICS_MISSING")
            gate.fresh({"collected_at": row.get("computed_at")}, datetime.now(timezone.utc), 120)


def check_producer(observation: dict, context: dict, *, fixture: bool) -> dict:
    require(isinstance(observation, dict) and set(observation) ==
            PUBLIC_OBSERVATION_FIELDS, "PUBLIC_PRODUCER_OBSERVATION_REQUIRED")
    require(type(observation["actual_rc"]) is int and observation["actual_rc"] == 0 and
            type(observation["stderr_bytes"]) is int and observation["stderr_bytes"] == 0,
            "PUBLIC_PRODUCER_PROCESS_FAILED")
    require(observation["source_before"] == observation["source_after"] and
            isinstance(observation["source_before"], list) and observation["source_before"] and
            all(isinstance(pin, dict) and set(pin) == {"path", "sha256", "metadata"}
                for pin in observation["source_before"]),
            "PUBLIC_PRODUCER_SOURCE_DRIFT")
    identity = context["identity"]
    producer = Path(observation["producer"])
    require(producer.is_absolute() and producer.name == "public_gate.py" and
            producer.parent.name == "tools" and
            (fixture or producer == SOURCE_ROOT / "tools/public_gate.py"),
            "PUBLIC_PRODUCER_FIXED_PATH")
    expected = {**identity["source_sha256"], "tools/public_gate.py":
                identity["tool_sha256"]["tools/public_gate.py"]}
    pins = observation["source_before"]
    require(len(pins) == len(expected) and
            {pin["path"] for pin in pins} == {
                str(Path(observation["producer"]).parents[1] / name) for name in expected},
            "PUBLIC_PRODUCER_SOURCE_SET")
    for name, digest in expected.items():
        path = Path(observation["producer"]).parents[1] / name
        pin = next(pin for pin in pins if pin["path"] == str(path))
        require(pin["sha256"] == digest, "PUBLIC_PRODUCER_CANDIDATE_MISMATCH")
        verify_observation(pin)
    require(expected["tools/public_gate.py"] == PUBLIC_GATE_SHA,
            "PUBLIC_PRODUCER_PROTOCOL_PIN")
    dependencies = observation["collector_dependencies"]
    for path, digest in COLLECTOR_LIBRARIES:
        require(any(pin["path"] == str(path) and pin["sha256"] == digest
                    for pin in dependencies), "PUBLIC_CAPTURE_LIBRARY_BINDING")
    verify_collection(observation)
    return observation["report"]


def run_once(store: Store, admission, executor, public_check, notify, *, fixture=False) -> dict:
    """Hooks are a Python test/module interface, never selectable by the CLI."""
    with store.locked():
        owner, snapshot = store.current()
        generation_id = str(uuid4())
        run = store.runs / generation_id
        run.mkdir(mode=0o700)
        report = {"schema": SCHEMA, "state": "BLOCKED", "generation_id": generation_id,
                  "generation": owner["generation"] + 1, "fixture": fixture,
                  "certified": False, "production_mutation_attempted": False,
                  "started_at": stamp(), "first_cause": None, "secondary": [],
                  "public_evidence": None, "operations": {}, "notification": "NOT_ATTEMPTED",
                  "finalization": "PROVISIONAL_PROCESS_RC_REQUIRED"}
        next_owner = {"schema": SCHEMA, "generation": report["generation"],
                      "last_run": generation_id, "report_sha256": None, "closure": None}
        create(run / "intent.json", report)
        create(run / "admission-pending.json", {
            "schema": SCHEMA, "generation_id": generation_id,
            "state": "PENDING_RELIABLE_TERMINAL"})
        pending = observe_file(run / "admission-pending.json", owned=True)
        dependencies = [observe_file(run / "intent.json", owned=True)]
        dependencies.extend(observe_file(Path(module.__file__))
                            for module in (sys.modules[__name__], sync, gate))
        provisional_owner = store.replace_owner(snapshot, next_owner)
        context = None
        mutation = False
        try:
            context = admission()
            if "closure_evidence" in context:
                dependencies.extend(closure_dependencies(context["closure_evidence"]))
            if executor is None:
                raise Refused("MISSING_REVIEWED_CONTINUOUS_EXECUTOR")
            require(fixture or executor is APPROVED_EXECUTOR,
                    "UNAPPROVED_EXECUTOR_NO_DISPATCH")
            context = {**context, "generation_id": generation_id}
            create(run / "admission.json", context)
            dependencies.append(observe_file(run / "admission.json", owned=True))
            # The final admission snapshot is taken before the deploy intent.
            first_binding = binding({key: value for key, value in context.items()
                                     if key != "generation_id"})
            second = admission()
            if "closure_evidence" in second:
                dependencies.extend(closure_dependencies(second["closure_evidence"]))
            require(binding(second) == first_binding, "PREDEPLOY_ADMISSION_DRIFT")
            create(run / "deploy-intent.json", {"generation_id": generation_id,
                                              "request_sha256": context["request_sha256"]})
            dependencies.append(observe_file(run / "deploy-intent.json", owned=True))
            mutation = True
            report["production_mutation_attempted"] = not fixture
            report["operations"]["deploy"] = operation(executor, "deploy", context, run)
            dependencies.append(report["operations"]["deploy"]["receipt_observation"])
            observation = public_check(context, run)
            create(run / "public-producer.json", observation)
            dependencies.append(observe_file(run / "public-producer.json", owned=True))
            if isinstance(observation, dict) and set(observation) == PUBLIC_OBSERVATION_FIELDS:
                dependencies.extend(public_dependencies(observation))
            evidence = check_producer(observation, context, fixture=fixture)
            create(run / "public.json", evidence)
            dependencies.append(observe_file(run / "public.json", owned=True))
            report["public_evidence"] = {"path": str(run / "public.json"),
                                        "sha256": gate.sha(gate.encode(evidence))}
            check_public(evidence, context, fixture=fixture)
            latest = admission()
            if "closure_evidence" in latest:
                dependencies.extend(closure_dependencies(latest["closure_evidence"]))
            require(binding(latest) == first_binding, "POSTDEPLOY_ADMISSION_DRIFT")
            report["state"] = "FIXTURE_SYNCHRONIZED" if fixture else "PUBLISHED"
        except Exception as exc:
            report["first_cause"] = failure(exc)
            if hasattr(exc, "closure_evidence"):
                report["closure_evidence"] = exc.closure_evidence
                dependencies.extend(closure_dependencies(exc.closure_evidence))
            if mutation:
                report["state"] = "FAILED_RECOVERY_REQUIRED"
                stopped = False
                for action in ("stop", "revert"):
                    if action == "revert" and not stopped:
                        report["secondary"].append({"code": "REVERT_REQUIRES_OWNED_STOP"})
                        continue
                    try:
                        report["operations"][action] = operation(executor, action, context, run)
                        dependencies.append(report["operations"][action]["receipt_observation"])
                        stopped = True
                        if action == "revert":
                            report["state"] = "FAILED_REVERTED"
                    except Exception as recovery:
                        report["secondary"].append(failure(recovery))
            try:
                report["notification"] = notify(report)
                require(report["notification"] in {"RELAY_SENT", "QUEUED_NOT_DELIVERY_VERIFIED",
                                                   "FIXTURE_ALERT_RECORDED"},
                        "ALERT_NOT_ACKNOWLEDGED")
            except Exception as alert:
                report["secondary"].append(failure(alert))
                report["notification"] = "FAILED"
        report["finished_at"] = stamp()
        report["exit_code"] = 0 if report["state"] in {"PUBLISHED", "FIXTURE_SYNCHRONIZED"} else (
            2 if report["state"] == "BLOCKED" else 3)
        try:
            report["dependencies"] = dependencies
            require(set(path.name for path in run.iterdir()) ==
                    {Path(pin["path"]).name for pin in dependencies
                     if Path(pin["path"]).parent == run} | {"admission-pending.json"},
                    "RUN_INVENTORY_DRIFT")
            for pin in dependencies:
                verify_dependency(pin, owned=Path(pin["path"]).parent == run)
            create(run / "report.json", report)
            create(run / "terminal.json", {"state": report["state"], "exit_code": report["exit_code"],
                                          "finished_at": report["finished_at"]})
            create(run / "closure.json", {
                "report": observe_file(run / "report.json", owned=True),
                "terminal": observe_file(run / "terminal.json", owned=True),
                "dependencies": report["dependencies"]})
            for pin in report["dependencies"]:
                verify_dependency(pin, owned=Path(pin["path"]).parent == run)
            require(emit(report) == report["exit_code"], "TERMINAL_OUTPUT_FAILED")
            next_owner["report_sha256"] = gate.sha(gate.encode(report))
            next_owner["closure"] = observe_file(run / "closure.json", owned=True)
            final_owner = store.replace_owner(provisional_owner, next_owner)
            _, checked_owner = store.current(terminal_only=True, pending=pending)
            require(checked_owner == final_owner, "OWNER_CAS_DRIFT")
            verify_observation(pending, owned=True)
            # Last admission operation: all terminal data is already durable.
            # No post-unlink fsync; an unpersisted deletion can only restore a
            # blocking marker after a crash, never admit an unclosed generation.
            os.unlink(run / "admission-pending.json")
        except Exception as exc:
            recovery = []
            if mutation and report["first_cause"] is None:
                stopped = "stop" in report["operations"]
                for action in ("stop", "revert"):
                    if action == "stop" and stopped:
                        continue
                    if action == "revert" and not stopped:
                        break
                    try:
                        report["operations"][action] = operation(executor, action, context, run)
                        stopped = True
                    except Exception as cause:
                        recovery.append(failure(cause))
                        break
            failed = {**report, "state": "FAILED_FINALIZATION",
                      "first_cause": report["first_cause"] or failure(exc),
                      "secondary": report["secondary"] + recovery}
            if report["first_cause"] is None:
                try:
                    failed["notification"] = notify(failed)
                    require(failed["notification"] in {"RELAY_SENT",
                            "QUEUED_NOT_DELIVERY_VERIFIED", "FIXTURE_ALERT_RECORDED"},
                            "ALERT_NOT_ACKNOWLEDGED")
                except Exception as cause:
                    failed["notification"] = "FAILED"
                    failed["secondary"].append(failure(cause))
            try:
                veto(run, exc, failed)
            except Exception as secondary:
                failed["secondary"].append(failure(secondary))
                print("CONTINUOUS_VETO_PERSIST_FAILED type=" + type(secondary).__name__,
                      file=sys.stderr, flush=True)
            exc.finalization_secondary = failed["secondary"]
            raise
        return report


def emit(report: dict) -> int:
    sys.stdout.write(gate.encode(report).decode("ascii"))
    sys.stdout.flush()
    return report["exit_code"]


def production_admission() -> dict:
    raw, identity = read_owned(CONFIG)
    config = gate.decode(raw)
    require(set(config) == {"schema", "source_root", "desired", "desired_sha256"} and
            config["schema"] == SCHEMA and config["source_root"] == str(SOURCE_ROOT),
            "FIXED_CANDIDATE_CONFIG_REQUIRED")
    desired = gate.external(Path(config["desired"]))
    result = admit(SOURCE_ROOT, desired, config["desired_sha256"])
    require(read_owned(CONFIG) == (raw, identity), "CONFIG_DRIFT")
    return result


def public_candidate(context: dict, run: Path) -> dict:
    digest = gate.sha(gate.encode(context["identity"]["source_sha256"]))
    result = collect_public(SOURCE_ROOT, EVIDENCE_ROOT / ("public-" + context["generation_id"]),
                            digest, context["commit"])
    require(result["exit_code"] == 0, "PUBLIC_PRODUCER_PROCESS_FAILED")
    return result["observation"]


def alert(report: dict) -> str:
    # Existing notification transport only; no new credentials or private body.
    from public_watch import send_notice
    return send_notice("OpenSim continuous release BLOCKED/FAILED " + report["generation_id"],
                       "state=" + report["state"] + "; code=" +
                       report["first_cause"]["code"])


def main(argv=None, *, runner=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--once", action="store_true")
    modes.add_argument("--init-state", action="store_true")
    modes.add_argument("--inspect-candidate", action="store_true")
    modes.add_argument("--collect-public", action="store_true")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--desired", type=Path)
    parser.add_argument("--desired-sha256")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--expect-source-set-sha256")
    parser.add_argument("--expect-commit")
    args = parser.parse_args(argv)
    inputs = (args.source_root, args.desired, args.desired_sha256, args.out)
    if args.collect_public:
        if (not all((args.source_root, args.out, args.expect_source_set_sha256,
                     args.expect_commit)) or args.desired or args.desired_sha256):
            parser.error("--collect-public requires source-root/out/source-set/commit only")
    elif args.expect_source_set_sha256 or args.expect_commit:
        parser.error("public collection pins require --collect-public")
    elif args.inspect_candidate:
        if not all(inputs):
            parser.error("--inspect-candidate requires all four explicit inputs")
    elif any(value is not None for value in inputs):
        parser.error("fixed production modes do not accept path or pin overrides")
    try:
        require(os.geteuid() != 0, "NEVER_CHECKOUT_AS_ROOT")
        if args.collect_public:
            result = collect_public(args.source_root, args.out, args.expect_source_set_sha256,
                                    args.expect_commit, runner=runner)
            _, storage, _ = collector_libraries()
            out = storage.output_path(args.out)
            line = "CONTINUOUS_PUBLIC state=" + result["state"] + " actual_public_rc=" + str(
                result["actual_rc"]) + " release_verdict=NOT_CERTIFIED"
            try:
                print(line + " provisional_actual_launcher_rc_required", flush=True)
                if result["exit_code"] == 0:
                    verify_collection(result["observation"])
            except Exception as exc:
                if not os.path.lexists(out / "report.failure.json"):
                    storage.write_new(out / "report.failure.json", gate.encode({
                        "state": "EVIDENCE_FAILED", "exit_code": 3,
                        "actual_rc": result["actual_rc"], "first_cause": failure(exc)}))
                return 3
            return result["exit_code"]
        if args.inspect_candidate:
            gate.external(args.out, EVIDENCE_ROOT)
            args.out.mkdir(mode=0o700, parents=True)
            report = {"schema": SCHEMA, "state": "BLOCKED", "exit_code": 2,
                      "certified": False, "production_mutation_attempted": False,
                      "first_cause": None, "finished_at": stamp()}
            closure_context = {}
            try:
                report["admission"] = admit(args.source_root, args.desired, args.desired_sha256,
                                            evidence=closure_context)
                report["state"] = "VALIDATED_NOT_DEPLOYED"
                report["exit_code"] = 0
            except Exception as exc:
                report["first_cause"] = failure(exc)
                if isinstance(exc, gate.GateError):
                    # Gate reasons are the existing public evidence diagnostics.
                    report["first_cause"]["code"] = str(exc)
            if closure_context:
                report["closure_execution"] = closure_context
            try:
                if closure_context:
                    verify_closure_observation(closure_context)
                create(args.out / "report.json", report)
                saved = observe_file(args.out / "report.json", owned=True)
                rc = emit(report)
                verify_observation(saved, owned=True)
                require({path.name for path in args.out.iterdir()} == {"report.json"},
                        "INSPECT_INVENTORY_DRIFT")
                if closure_context:
                    verify_closure_observation(closure_context)
                return rc
            except Exception as exc:
                report["state"], report["exit_code"] = "INSPECT_EVIDENCE_FAILED", 3
                report["evidence_failure"] = failure(exc)
                report["first_cause"] = report["first_cause"] or failure(exc)
                try:
                    create(args.out / "report.failure.json", report)
                except Exception as secondary:
                    print("CONTINUOUS_INSPECT_VETO_FAILED " + gate.encode(failure(secondary)).decode("ascii"),
                          file=sys.stderr, flush=True)
                print("CONTINUOUS_INSPECT_EVIDENCE_FAILED " + gate.encode(failure(exc)).decode("ascii"),
                      file=sys.stderr, flush=True)
                return 3
        store = Store(STATE_ROOT)
        if args.init_state:
            with store.locked(initialize=True):
                store.current()
            return emit({"schema": SCHEMA, "state": "OWNER_INITIALIZED_NOT_DEPLOYED",
                         "exit_code": 0, "certified": False})
        return run_once(store, production_admission, APPROVED_EXECUTOR,
                        public_candidate, alert)["exit_code"]
    except Exception as exc:
        print("CONTINUOUS_REFUSED " + gate.encode(failure(exc)).decode("ascii").strip(),
              file=sys.stderr, flush=True)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
