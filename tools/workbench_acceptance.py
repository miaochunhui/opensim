"""Serial candidate-only OpenSim automation; never a release certifier.

Use --out NEW_EXTERNAL_DIR, optionally --integration-report SAME_CANDIDATE_JSON.
No installation, download, production target, exception approval or retry exists.
Producer schemas and output boundaries are documented in WORKBENCH_ACCEPTANCE.md.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import logging
import math
import os
import re
import struct
import stat
import subprocess
import sys
import threading
import traceback
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1")
SOURCES = ("tools/render_page.py", "web/app.js", "web/app.css", "solver.py", "server.py")
LOCAL_SOURCES = (*SOURCES, "tools/validate_numerics.py")
PRODUCERS = {
    "browser": ("browser_check.mjs", "browser", "opensim-browser-check"),
    "audit": ("browser_audit.mjs", "seat-audit", "opensim-browser-audit"),
    "perf": ("browser_performance.mjs", "seat-perf", "opensim-browser-performance"),
    "ui": ("ui_contract_test.mjs", "seat-ui", "opensim-ui-contract"),
}
VIEWPORTS = {
    "mobile-narrow", "mobile-common", "mobile-wide", "tablet-portrait",
    "desktop-small", "desktop-browser-common", "desktop-standard", "desktop-wide",
}
PARTIAL = "LOCAL_AUTOMATION_PARTIAL_NOT_RELEASE"
PASS = "LOCAL_AUTOMATION_PASS_NOT_RELEASE"


class EvidenceError(ValueError):
    """An evidence failure with a stable, machine-readable coordinate."""

    def __init__(self, message: str, coordinate: str = "evidence"):
        super().__init__(message)
        self.coordinate = coordinate


def need(condition: bool, message: str, coordinate: str = "evidence") -> None:
    if not condition:
        raise EvidenceError(message, coordinate)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        need(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def finite_tree(value: object) -> None:
    if isinstance(value, float):
        need(math.isfinite(value), "Non-finite JSON number")
    elif isinstance(value, dict):
        for child in value.values():
            finite_tree(child)
    elif isinstance(value, list):
        for child in value:
            finite_tree(child)


def decode(data: bytes) -> dict:
    value = json.loads(data, object_pairs_hook=unique_object)
    need(isinstance(value, dict) and bool(value), "Empty or non-object report")
    finite_tree(value)
    return value


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    need(result.returncode == 0,
         f"git {args[0]} rc={result.returncode}: {result.stderr.strip()}", "git")
    return result.stdout.rstrip("\n")


def inventory(root: Path) -> dict:
    paths = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    files = {}
    for relative in sorted(set(paths.split("\0")) - {""}):
        path = root / relative
        need(path.is_file() and not path.is_symlink(), f"Nonregular source: {relative}", "source")
        files[relative] = sha(path.read_bytes())
    need(bool(files), "Empty source inventory", "source")
    return {
        "commit": git(root, "rev-parse", "HEAD"),
        "status": git(root, "status", "--porcelain", "--untracked-files=all"),
        "tracked_sha256": files,
    }


def clean(value: dict) -> None:
    need(not value["status"], "Candidate has dirty/untracked files", "source")


def bounded(path: Path, base: Path = EXTERNAL) -> Path:
    logical = path.absolute()
    base = base.absolute()
    physical_base = base.resolve(strict=True)
    if base not in logical.parents and physical_base in logical.parents:
        base = physical_base
    need(base in logical.parents, f"Path outside external root: {path}", "path")
    child = base
    for part in logical.relative_to(base).parts:
        child /= part
        need(not child.is_symlink(), f"Child symlink forbidden: {child}", "path")
    actual = path.resolve()
    need(base.resolve(strict=True) in actual.parents, f"Resolved path escaped root: {path}", "path")
    return actual


def new_output(path: Path) -> Path:
    path = bounded(path)
    path.mkdir(parents=True, exist_ok=False)
    probe = path / "write-probe.txt"
    with probe.open("xb") as stream:
        stream.write(b"opensim workbench external write/read probe\n")
    need(probe.read_bytes() == b"opensim workbench external write/read probe\n",
         "External write/read probe differs", "output")
    return path


def file_record(path: Path, base: Path, allow_empty: bool = False) -> dict:
    path = bounded(path, base)
    need(path.is_file(), f"Missing evidence file: {path}", "artifact")
    content = path.read_bytes()
    need(bool(content) or allow_empty, f"Empty evidence file: {path}", "artifact")
    return {"path": str(path), "bytes": len(content), "sha256": sha(content)}


def read_report(path: Path, base: Path) -> tuple[dict, dict]:
    record = file_record(path, base)
    return decode(Path(record["path"]).read_bytes()), record


def json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


class DurableStore:
    """Exclusive owned-file I/O, with exact inode/byte checks before revocation."""

    def __init__(self):
        self.owned = {}
        self.revocations = []

    def sync_directory(self, path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def verify(self, path: Path, expected: bytes) -> None:
        owned = self.owned[path]
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            need((info.st_dev, info.st_ino) == owned["inode"] and stat.S_ISREG(info.st_mode),
                 f"Owned file inode changed: {path}", "persistence")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                actual = stream.read()
            need(actual == expected, f"Owned file exact bytes differ: {path}", "persistence")
        finally:
            os.close(fd)
        current = path.lstat()
        need((current.st_dev, current.st_ino) == owned["inode"] and stat.S_ISREG(current.st_mode),
             f"Owned file replaced during readback: {path}", "persistence")

    def create(self, path: Path, data: bytes) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        info = os.fstat(fd)
        self.owned[path] = {"inode": (info.st_dev, info.st_ino), "bytes": b""}
        try:
            offset = 0
            while offset < len(data):
                count = os.write(fd, data[offset:])
                need(count > 0, f"Zero-byte persistence write: {path}", "persistence")
                offset += count
                self.owned[path]["bytes"] = data[:offset]
            os.fsync(fd)
        finally:
            os.close(fd)
        self.sync_directory(path.parent)
        self.verify(path, data)

    def revoke(self, path: Path) -> dict:
        need(path in self.owned, f"Refusing to revoke unknown file: {path}", "persistence")
        owned = self.owned[path]
        # Use the base verifier, not a test fault injector, for the ownership fence.
        DurableStore.verify(self, path, owned["bytes"])
        preserved = path.parent / ("revoked-owned-" + uuid.uuid4().hex + "-" + path.name)
        need(not preserved.exists(), "Revocation destination conflict", "persistence")
        current = path.lstat()
        need((current.st_dev, current.st_ino) == owned["inode"] and stat.S_ISREG(current.st_mode),
             "Refusing to revoke replaced/unknown report", "persistence")
        path.rename(preserved)
        record = {"original": str(path), "preserved": str(preserved),
                  "inode": list(owned["inode"]), "bytes": len(owned["bytes"]), "sha256": sha(owned["bytes"])}
        self.revocations.append(record)
        # Report any persistence error; do not undo a safe revocation.
        self.sync_directory(path.parent)
        return record


def write_json(path: Path, value: dict) -> None:
    store = DurableStore()
    try:
        store.create(path, json_bytes(value))
    except Exception as exc:
        if path in store.owned:
            try:
                store.revoke(path)
            except Exception as revoke_error:
                raise EvidenceError(f"{exc}; exact-own revocation refused/failed: {revoke_error}",
                                    "persistence") from exc
        raise


def checkpoint(output: Path, report: dict, stage: str) -> None:
    sequence = len(list(output.glob("state-*.json"))) + 1
    snapshot = copy.deepcopy(report)
    snapshot["candidate_verdict"] = snapshot["verdict"]
    snapshot["verdict"] = "PROVISIONAL_NOT_FINAL"
    snapshot["exit_code"] = None
    write_json(output / f"state-{sequence:03d}.json", {
        "stage": stage, "collected_at": now(), "report": snapshot})


def source_map(candidate: dict) -> dict:
    return {name: candidate["tracked_sha256"][name] for name in SOURCES}


def page_build(root: Path) -> str:
    digest = hashlib.sha256()
    for name in SOURCES:
        digest.update(name.encode("ascii") + b"\0" + (root / name).read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def matching(actual: object, expected: dict, coordinate: str) -> None:
    need(actual == expected, f"Source identity differs: {coordinate}", coordinate)


def positive(value: object, coordinate: str) -> int:
    need(type(value) is int and value > 0, f"Missing positive count: {coordinate}", coordinate)
    return value


def first_failure(value: dict) -> object | None:
    for name in ("first_failure", "failure", "harness_failure", "cleanup_failure"):
        if value.get(name):
            return value[name]
    for row in value.get("checks", []):
        if isinstance(row, dict) and (row.get("status") == "FAIL" or row.get("verdict") == "FAIL"):
            return row
    return None


def compact_cause(cause: object) -> object:
    if isinstance(cause, dict):
        return {key: cause[key] for key in ("type", "rule", "coordinate", "phase", "message", "name")
                if key in cause}
    return str(cause)[:2000]


def terminal(value: dict, rc: int, name: str, verdict: str) -> None:
    cause = first_failure(value)
    need(cause is None,
         f"{name} first cause: {json.dumps(compact_cause(cause), ensure_ascii=False)}; process_rc={rc}", name)
    need(type(rc) is int and rc == 0, f"{name} process rc={rc}, report verdict={value.get('verdict')}", name)
    need(type(value.get("exit_code")) is int and value["exit_code"] == rc,
         f"{name} missing/mismatched terminal exit_code", name)
    need(value.get("verdict") == verdict, f"{name} terminal verdict={value.get('verdict')}", name)
    for key in ("started_at", "finished_at"):
        need(isinstance(value.get(key), str), f"{name} missing {key}", name)
    start = datetime.fromisoformat(value["started_at"].replace("Z", "+00:00"))
    finish = datetime.fromisoformat(value["finished_at"].replace("Z", "+00:00"))
    need(start.tzinfo is not None and finish.tzinfo is not None and finish >= start,
         f"{name} invalid terminal time interval", name)
    need(value.get("release_verdict") == "NOT_CERTIFIED", f"{name} forged release scope", name)


def png_record(row: dict, directory: Path, dimensions: tuple | None = None) -> dict:
    need(isinstance(row, dict) and isinstance(row.get("file"), str), "PNG record missing file", "png")
    need(Path(row["file"]).name == row["file"], "PNG reference escaped run", "png")
    record = file_record(directory / row["file"], directory)
    data = Path(record["path"]).read_bytes()
    need(record["sha256"] == row.get("sha256"), "PNG hash differs", "png")
    if "bytes" in row:
        need(type(row["bytes"]) is int and record["bytes"] == row["bytes"], "PNG bytes differ", "png")
    need(data[:8] == b"\x89PNG\r\n\x1a\n", "Corrupt PNG signature", "png")
    offset, idat, header, ended = 8, [], None, False
    while offset < len(data):
        need(offset + 12 <= len(data), "Truncated PNG chunk", "png")
        length = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        need(end <= len(data), "Truncated PNG payload", "png")
        payload = data[offset + 8:end - 4]
        need(zlib.crc32(kind + payload) & 0xffffffff == int.from_bytes(data[end - 4:end], "big"),
             "Corrupt PNG CRC", "png")
        if offset == 8:
            need(kind == b"IHDR" and length == 13, "PNG missing first IHDR", "png")
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IHDR":
            raise EvidenceError("Duplicate PNG IHDR", "png")
        if kind == b"IDAT":
            idat.append(payload)
        if kind == b"IEND":
            need(length == 0 and end == len(data), "PNG invalid IEND/trailing bytes", "png")
            ended = True
        offset = end
    need(header is not None and ended and bool(idat), "Incomplete PNG", "png")
    width, height, depth, color, compression, filtering, interlace = header
    need(width > 0 and height > 0 and width * height <= 100_000_000 and
         depth == 8 and color in (2, 6) and (compression, filtering, interlace) == (0, 0, 0),
         "Unsupported/unbounded PNG dimensions or encoding", "png")
    expected = dimensions if dimensions is not None else (row.get("width"), row.get("height"))
    need(all(type(item) is int for item in expected) and (width, height) == expected,
         "PNG dimensions differ", "png")
    stride = width * (3 if color == 2 else 4) + 1
    decompressor = zlib.decompressobj()
    pixels = decompressor.decompress(b"".join(idat), stride * height + 1)
    need(decompressor.eof and not decompressor.unused_data and not decompressor.unconsumed_tail and
         len(pixels) == stride * height, "Corrupt/incomplete PNG scanlines", "png")
    need(all(pixels[index] <= 4 for index in range(0, len(pixels), stride)), "Invalid PNG filter", "png")
    return {**record, "width": width, "height": height}


def artifacts(directory: Path, name: str) -> list[dict]:
    records = []
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory)
        if "chromium-profile" in relative.parts:
            continue
        need(not path.is_symlink(), f"Artifact symlink: {relative}", name)
        if path.is_dir():
            continue
        # Empty logs are measured diagnostics; this UI input deliberately tests empty import.
        negative_input = name == "ui" and relative.name == "bad-empty.json"
        record = file_record(path, directory, path.suffix == ".log" or negative_input)
        record["kind"] = "NEGATIVE_INPUT" if negative_input else (
            "EMPTY_DIAGNOSTIC" if record["bytes"] == 0 else "ARTIFACT")
        records.append(record)
    need(bool(records), "Producer emitted no artifacts", name)
    return records


def validate_local(value: dict, candidate: dict) -> int:
    need(value.get("verdict") == "PASS" and isinstance(value.get("steps"), list) and
         len(value["steps"]) == 5, "Incomplete local acceptance", "local")
    need(all(type(step.get("rc")) is int and step["rc"] == 0 for step in value["steps"]),
         "Local stage has nonzero/missing rc", "local")
    matching(value.get("source_sha256"),
             {key: candidate["tracked_sha256"][key] for key in LOCAL_SOURCES}, "local")
    stderr = value["steps"][0].get("stderr", "")
    match = re.search(r"Ran ([1-9][0-9]*) tests", stderr)
    need(match is not None and "\nOK" in stderr, "Unit completion/count missing", "local")
    return int(match[1])


def validate_numerics(value: dict, root: Path, candidate: dict) -> dict:
    need(value.get("verdict") == "PASS" and value.get("state") == "COMPLETE" and
         type(value.get("exit_code")) is int and value["exit_code"] == 0,
         "Numerics incomplete/failed", "numerics")
    need(value.get("solver", {}).get("sha256") == candidate["tracked_sha256"]["solver.py"] and
         value.get("validator_sha256") == candidate["tracked_sha256"]["tools/validate_numerics.py"],
         "Stale numerics solver/validator", "numerics")
    need(Path(value["solver_root"]).resolve() == root.resolve(), "Numerics candidate root differs", "numerics")
    need(set(value.get("convergence", {})) == {"circle", "rectangle", "square"},
         "Missing numerics convergence groups", "numerics")
    need(bool(value.get("cases")) and bool(value.get("rejections")), "Empty numerics evidence", "numerics")
    for rows in (value["cases"], value["rejections"], list(value["convergence"].values())):
        need(all(isinstance(row, dict) and row.get("verdict") == "PASS" for row in rows),
             "Numerics has failed/unfinished records", "numerics")
    raw_records = []
    for row in value["cases"]:
        record = file_record(Path(row["result_path"]), EXTERNAL)
        need(record["sha256"] == row.get("result_sha256"), "Numerics raw case hash differs", "numerics")
        raw = decode(Path(record["path"]).read_bytes())
        need(bool(raw.get("mesh", {}).get("nodes_mm")) and bool(raw.get("fields", {}).get("velocity_m_s")),
             "Numerics raw FEM result empty", "numerics")
        raw_records.append(record)
    return {"checks": positive(value.get("checks_executed"), "numerics"),
            "cases": len(value["cases"]), "convergence": len(value["convergence"]),
            "rejections": len(value["rejections"]), "raw_artifacts": raw_records}


def solve_artifacts(directory: Path, sources: dict, expected: int, name: str) -> list[dict]:
    files = sorted(directory.glob("solve-*.json"))
    need(len(files) == expected, f"{name} actual solve artifact count differs", name)
    records = []
    successful = 0
    for path in files:
        call, record = read_report(path, directory)
        result = call.get("result", {})
        if call.get("status") == 200:
            stamp = result.get("reproducibility", {})
            matching(stamp.get("source_sha256"), sources, name)
            need(stamp.get("solver_sha256") == sources["solver.py"] and bool(stamp.get("computed_at")),
                 f"{name} POST solver/time missing or stale", name)
            need(bool(result.get("mesh", {}).get("nodes_mm")) and
                 bool(result.get("fields", {}).get("velocity_m_s")), f"{name} empty POST FEM result", name)
            successful += 1
        records.append(record)
    need(successful > 0, f"{name} no successful actual FEM artifact", name)
    return records


def validate_producer(name: str, value: dict, rc: int, root: Path,
                      candidate: dict, directory: Path, url: str) -> dict:
    tool_file, _, tool_name = PRODUCERS[name]
    need(value.get("schema_version") == 1 and value.get("tool") == tool_name,
         f"Unknown {name} report schema/tool", name)
    verdict = {"browser": "AUTOMATED_PASS", "audit": "AUTOMATED_SCOPE_PASS",
               "perf": "LOCAL_PREFLIGHT_PASS", "ui": "PASS"}[name]
    terminal(value, rc, name, verdict)
    need(value.get("tool_sha256") == sha((root / "tools" / tool_file).read_bytes()),
         f"{name} tool hash differs", name)
    # UI v1 has no commit field: bind it through the caller's unchanged Git inventory.
    if name != "ui" or "commit" in value:
        need(value.get("commit") == candidate["commit"], f"{name} stale commit", name)
    need(value.get("url" if name == "audit" else "target_url") == url,
         f"{name} target differs from owned server", name)
    checks = value.get("checks")
    need(isinstance(checks, list) and bool(checks), f"{name} empty checks", name)
    key = "verdict" if name == "ui" else "status"
    allowed = {"PASS", "NOT_TESTED"} if name == "audit" else {"PASS"}
    need(all(isinstance(row, dict) and row.get(key) in allowed for row in checks),
         f"{name} unfinished/failed checks", name)
    sources, build = source_map(candidate), page_build(root)
    remaining = []
    if name == "browser":
        need(value.get("mode") == "OPENSIM_ACCEPTANCE", "Browser probe/selftest is not full scope", name)
        matrix = value.get("matrix", [])
        need(len(matrix) == len(VIEWPORTS) and {row.get("name") for row in matrix} == VIEWPORTS and
             all(row.get("status") == "PASS" for row in matrix), "Incomplete browser matrix", name)
        need(build in value.get("page_build", {}).get("build_text", ""), "Stale browser build", name)
        solves = positive(value.get("result_count"), name)
        solve_artifacts(directory, sources, solves, name)
        pictures = value.get("screenshots")
    elif name == "audit":
        need(value.get("mode") == "G3_G6_AUTOMATIC_EXTENSION_NOT_RELEASE", "Audit smoke/fixture scope", name)
        matching(value.get("source_sha256"), sources, name)
        need(value.get("page_build", {}).get("id") == build, "Stale audit page_build", name)
        identities = value.get("identity_checks", [])
        need(len(identities) == 2 and [row.get("stage") for row in identities] == ["before", "after"],
             "Audit lacks before/after runtime identity", name)
        for row in identities:
            need(row.get("verdict") == "PASS" and row.get("url") == url and
                 row.get("fixture_asset_override") is False, "Audit fixture/failed identity", name)
            for field in ("local_source_sha256", "committed_source_sha256"):
                matching(row.get(field), sources, name)
            matching(row.get("health", {}).get("source_sha256"), sources, name)
            need(row["health"].get("page_build") == build, "Audit health build differs", name)
        need(identities[0].get("served_byte_sha256") == identities[1].get("served_byte_sha256") and
             bool(identities[0].get("served_byte_sha256")), "Audit served bytes drift/empty", name)
        posts = value.get("post_source_checks", [])
        solves = positive(len(posts), name)
        for row in posts:
            matching(row.get("source_sha256"), sources, name)
            need(row.get("verdict") == "PASS", "Audit POST source check failed", name)
            post, _ = read_report(directory / row["artifact"], directory)
            matching(post.get("payload", {}).get("reproducibility", {}).get("source_sha256"), sources, name)
            need(post.get("body_sha256") == row.get("body_sha256") and bool(post.get("bytes")),
                 "Audit POST artifact differs/empty", name)
        matrix = value.get("matrix", [])
        need({(row.get("width"), row.get("height")) for row in matrix} ==
             {(320, 568), (768, 1024), (1064, 768), (1440, 900)} and len(matrix) == 4,
             "Incomplete audit matrix", name)
        coverage = value.get("coverage")
        need(isinstance(coverage, list) and bool(coverage) and bool(value.get("gates")),
             "Audit coverage/gates missing", name)
        for row in [*coverage, *value["gates"].values()]:
            need(row.get("status") in {"PASS", "AUTOMATED_SCOPE_PASS", "PARTIAL", "NOT_TESTED", "NOT_APPLICABLE"},
                 "Audit failed/unknown coverage", name)
            if row["status"] in {"PARTIAL", "NOT_TESTED"}:
                remaining.append(row)
        remaining.extend(row for row in checks if row["status"] == "NOT_TESTED")
        pictures = value.get("screenshots")
    elif name == "perf":
        need(value.get("scope") == "LOCAL_PREFLIGHT" and value.get("g5_certified") is False and
             value.get("standard") == "shared-r41" and value.get("gate") == "G5-performance",
             "Performance forged/local scope differs", name)
        samples = value.get("samples", [])
        need(len(samples) == 1, "Performance requires one cold sample", name)
        sample = samples[0]
        need(sample.get("phase") == "ONE_COLD_NAVIGATION_PLUS_THREE_CORE_TOUCHES" and
             sample.get("target_url") == url, "Performance partial/foreign sample", name)
        ident = sample.get("source_identity", {})
        for field in ("local_source_sha256", "committed_source_sha256", "solve_source_sha256"):
            matching(ident.get(field), sources, name)
        matching(ident.get("health", {}).get("source_sha256"), sources, name)
        need(ident.get("bound") is True and ident.get("assets_bound") is True and
             ident.get("page_build_seen") is True and ident["health"].get("page_build") == build,
             "Performance page/runtime identity differs", name)
        solves = positive(sample.get("content", {}).get("solve_count"), name)
        baseline = value.get("baseline", {})
        need(baseline.get("width") == 360 and baseline.get("height") == 800 and
             baseline.get("samples") == 1 and baseline.get("retries") == 0,
             "Performance baseline differs", name)
        pictures = [sample.get("screenshot")]
    else:
        need(value.get("mode") == "REAL_DOM_CDP_CONTRACT", "UI minimal/fixture scope", name)
        matching(value.get("sources"), sources, name)
        solves = positive(value.get("actual_solver_calls"), name)
        need(not value.get("browser_failures"), "UI browser failures", name)
        solve_artifacts(directory, sources, solves, name)
        pictures = value.get("screenshots")
    need(isinstance(pictures, list) and bool(pictures), f"{name} empty PNG evidence", name)
    pngs = [png_record(row, directory, (360, 800) if name == "perf" else None) for row in pictures]
    return {"checks": len(checks), "solves": solves, "pngs": pngs, "remaining": remaining,
            "commit": candidate["commit"], "source_sha256": sources, "page_build": build,
            "commit_binding": "CLEAN_BEFORE_AFTER_INVENTORY" if name == "ui" else "PRODUCER_AND_INVENTORY"}


def execute(argv: list[str], root: Path, output: Path, name: str) -> dict:
    logs = output / "logs"
    logs.mkdir(exist_ok=True)
    stdout, stderr = logs / f"{name}.stdout.log", logs / f"{name}.stderr.log"
    step = {"name": name, "argv": argv, "started_at": now(), "rc": None}
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    with stdout.open("xb") as out, stderr.open("xb") as err:
        result = subprocess.run(argv, cwd=root, env=env, stdout=out, stderr=err)
    step.update(rc=result.returncode, finished_at=now(),
                stdout=file_record(stdout, output, True), stderr=file_record(stderr, output, True))
    return step


def check_step(step: dict) -> None:
    if step["rc"] != 0:
        message = Path(step["stderr"]["path"]).read_text(encoding="utf-8", errors="replace")
        raise EvidenceError(f"{step['name']} rc={step['rc']}: {message.strip()[:2000]}; "
                            f"log={step['stderr']['path']}", step["name"])


def collect_local(root: Path, output: Path, candidate: dict, report: dict) -> Path:
    local = output / "local"
    local.mkdir()
    commands = [
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
        ["node", "--check", "web/app.js"],
        [sys.executable, "-B", "tools/validate_numerics.py", "--out", str(local / "numerics.json"),
         "--solver-root", str(root), "--log-dir", str(local / "logs")],
        [sys.executable, "-B", "tools/render_page.py", "--out", str(local / "gen"),
         "--evidence", str(local / "numerics.json"), "--selftest"],
    ]
    commands.append(commands[-1])
    steps = []
    for index, argv in enumerate(commands):
        report["active"] = {"name": f"local-{index + 1}", "argv": argv, "state": "RUNNING"}
        checkpoint(output, report, f"local-{index + 1}-start")
        step = execute(argv, root, output, f"local-{index + 1}")
        report["steps"].append(step)
        report["active"] = {"name": step["name"], "state": "EXITED", "rc": step["rc"]}
        checkpoint(output, report, f"local-{index + 1}-exit")
        check_step(step)
        steps.append({**step, "stderr": Path(step["stderr"]["path"]).read_text(encoding="utf-8")})
    value = {"verdict": "PASS", "steps": steps, "collected_at": now(),
             "source_sha256": {key: candidate["tracked_sha256"][key] for key in LOCAL_SOURCES}}
    write_json(local / "acceptance.json", value)
    report["counts"]["unit_tests"] = validate_local(value, candidate)
    report["local_artifacts"] = artifacts(local, "local")
    return local / "numerics.json"


def reuse_integration(path: Path, root: Path, candidate: dict, report: dict) -> Path:
    value, record = read_report(path, EXTERNAL)
    need(value.get("schema_version") == 1 and value.get("scope") == "LOCAL_INTEGRATION_NOT_RELEASE" and
         value.get("release_verdict") == "NOT_CERTIFIED" and value.get("verdict") == "LOCAL_INTEGRATION_PASS",
         "Integration scope/terminal verdict differs", "integration")
    matching(value.get("before"), candidate, "integration-before")
    matching(value.get("after"), candidate, "integration-after")
    need(value.get("tool_sha256") == candidate["tracked_sha256"]["tools/integration_acceptance.py"],
         "Stale integration tool", "integration")
    steps = value.get("steps", [])
    need(len(steps) == 2 and [row.get("name") for row in steps] == ["local", "browser"] and
         all(type(row.get("rc")) is int and row["rc"] == 0 for row in steps),
         "Integration lacks local+fullbrowser process success", "integration")
    for step in steps:
        for stream in ("stdout", "stderr"):
            saved = step[stream]
            actual = file_record(Path(saved["path"]), EXTERNAL, True)
            need(actual["sha256"] == saved.get("sha256"), "Integration log hash differs", "integration")
    producer_reports = {}
    for name in ("local", "browser"):
        saved = value.get("artifacts", {}).get(name, {})
        child, actual = read_report(Path(saved["path"]), EXTERNAL)
        need(actual == saved, "Integration artifact metadata differs", "integration")
        producer_reports[name] = (child, Path(actual["path"]))
    local, local_path = producer_reports["local"]
    report["counts"]["unit_tests"] = validate_local(local, candidate)
    report["local_artifacts"] = artifacts(local_path.parent, "local")
    browser, browser_path = producer_reports["browser"]
    from urllib.parse import urlsplit
    old_url = urlsplit(browser.get("target_url", ""))
    need(old_url.scheme == "http" and old_url.hostname == "127.0.0.1" and
         not old_url.username and not old_url.password and old_url.path == "/" and
         not old_url.query and not old_url.fragment, "Integration browser is not loopback root", "integration")
    result = validate_producer("browser", browser, 0, root, candidate, browser_path.parent,
                               browser.get("target_url"))
    report["producers"]["browser"] = {"report": value["artifacts"]["browser"], "validation": result,
                                      "artifacts": artifacts(browser_path.parent, "browser"), "reused": True}
    report["integration"] = record
    return local_path.parent / "numerics.json"


def server_identity(url: str, root: Path, candidate: dict, output: Path, stage: str) -> dict:
    opener = build_opener(ProxyHandler({}))
    records = {}
    for endpoint in ("api/health", "", "web/app.js", "web/app.css"):
        with opener.open(url + endpoint, timeout=15) as response:
            need(response.status == 200 and response.url == url + endpoint, "Owned server HTTP/redirect failure", "server")
            data = response.read()
        filename = output / f"server-{stage}-{endpoint.replace('/', '-') or 'page'}.bin"
        with filename.open("xb") as stream:
            stream.write(data)
        records[endpoint] = file_record(filename, output)
    health = decode(Path(records["api/health"]["path"]).read_bytes())
    matching(health.get("source_sha256"), source_map(candidate), "server")
    need(health.get("status") == "ok" and health.get("page_build") == page_build(root),
         "Owned server health/build differs", "server")
    need(page_build(root).encode("ascii") in Path(records[""]["path"]).read_bytes(),
         "Owned server page build missing", "server")
    for name in ("web/app.js", "web/app.css"):
        need(records[name]["sha256"] == candidate["tracked_sha256"][name],
             "Owned server actual asset differs", "server")
    return {"health": health, "artifacts": records}


def automation_verdict(remaining: list) -> tuple[str, int]:
    return (PARTIAL, 3) if remaining else (PASS, 0)


class PersistenceError(OSError):
    def __init__(self, report: dict, path: Path | None):
        super().__init__(report["persistence_failure"]["message"])
        self.report = report
        self.path = path


def verdict_line(report: dict, path: Path) -> str:
    line = (f"OPENSIM_WORKBENCH verdict={report['verdict']} rc={report['exit_code']} "
            f"producers={report['counts']['producers_validated']}/4 "
            f"checks={report['counts']['checks']} pngs={report['counts']['pngs']} "
            f"collected_at={report['finished_at']} report={path}")
    if report.get("first_cause"):
        line += "\nFIRST_CAUSE " + json.dumps(compact_cause(report["first_cause"]), ensure_ascii=False)
    return line


def persist_result(output: Path, report: dict, store: DurableStore | None = None) -> dict:
    """Publish terminal evidence only after durable provisional and log readback.

    On failure preserve the run's first cause, invalidate only exact-owned bytes,
    and write a uniquely named FAIL marker. Unknown/old reports are never edited.
    """
    store = store if store is not None else DurableStore()
    path, log_path = output / "workbench.json", output / "workbench.log"
    result = copy.deepcopy(report)
    provisional = copy.deepcopy(report)
    provisional.update(verdict="PROVISIONAL_NOT_FINAL", exit_code=None,
                       candidate_verdict=report["verdict"])
    try:
        # Do not even prepare a new result over a pre-existing terminal destination.
        need(not os.path.lexists(path) and not os.path.lexists(log_path),
             "Terminal report/log destination conflict", "persistence")
        store.create(output / "workbench.provisional.json", json_bytes(provisional))
        log = ("PROVISIONAL_LOG_NOT_TERMINAL\n" + verdict_line(report, path) + "\n").encode("utf-8")
        store.create(log_path, log)
        result["durability"] = {
            "state": "FINAL_AFTER_DURABLE_LOG",
            "log": {"path": str(log_path), "bytes": len(log), "sha256": sha(log)},
            "protocol": "exclusive-create/fsync-file/fsync-directory/exact-inode-and-bytes-readback",
        }
        final = json_bytes(result)
        store.create(path, final)
        # A late final/log readback failure must revoke our terminal bytes.
        store.verify(log_path, log)
        store.verify(path, final)
        return result
    except Exception as exc:
        failure = copy.deepcopy(report)
        failure.update(verdict="FAIL", exit_code=2)
        failure["persistence_failure"] = {
            "type": type(exc).__name__, "coordinate": "persistence", "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        failure.setdefault("first_cause", failure["persistence_failure"])
        if path in store.owned:
            try:
                store.revoke(path)
            except Exception as revoke_error:
                failure["revocation_failure"] = str(revoke_error)
        failure["revocations"] = store.revocations
        failure["durability"] = {"state": "FAILED_NOT_CONSUMABLE"}
        marker = output / ("persistence-failure-" + uuid.uuid4().hex + ".json")
        try:
            write_json(marker, failure)
        except Exception as marker_error:
            failure["failure_marker_error"] = str(marker_error)
            marker = None
        raise PersistenceError(failure, marker) from exc


def read_final(output: Path) -> dict:
    """Fail closed on incomplete publication, failure markers or log drift."""
    need(not list(output.glob("persistence-failure-*.json")),
         "Persistence failure marker present; result not consumable", "persistence")
    value, _ = read_report(output / "workbench.json", output)
    durability = value.get("durability", {})
    need(durability.get("state") == "FINAL_AFTER_DURABLE_LOG",
         "Provisional/unknown terminal durability", "persistence")
    expected = durability.get("log", {})
    actual = file_record(output / "workbench.log", output)
    need(actual == expected, "Final log path/bytes/hash drift", "persistence")
    need(value.get("verdict") in {PASS, PARTIAL, "FAIL"} and
         type(value.get("exit_code")) is int and
         value["exit_code"] == (0 if value["verdict"] == PASS else
                                3 if value["verdict"] == PARTIAL else 1),
         "Final verdict/exit-code mismatch", "persistence")
    return value


def run(root: Path, output: Path, integration: Path | None = None) -> dict:
    report = {"schema_version": 1, "tool": "opensim-workbench-acceptance",
              "scope": "LOCAL_AUTOMATION_NOT_RELEASE", "release_verdict": "NOT_CERTIFIED",
              "started_at": now(), "verdict": "RUNNING", "steps": [], "producers": {},
              "counts": {}, "remaining": [], "unverified": [
                  "Public same-version DNS/TLS/page/assets/FEM and public G5",
                  "Physical touch/soft keyboard, desktop/mobile screen readers and human screenshot review",
                  "Production deployment/revert, authorization and exception decisions",
              ]}
    server = thread = handler = before = None
    try:
        checkpoint(output, report, "start")
        before = inventory(root)
        report["before"] = before
        clean(before)
        missing = [str(root / "tools" / item[0]) for item in PRODUCERS.values()
                   if not (root / "tools" / item[0]).is_file()]
        need(not missing, f"Missing producer tools: {', '.join(missing)}", "preflight")
        checkpoint(output, report, "preflight-complete")
        numerics = reuse_integration(integration, root, before, report) if integration else (
            collect_local(root, output, before, report))
        need(inventory(root) == before, "Candidate drifted during local/reused evidence checks", "local-after")
        value, record = read_report(numerics, EXTERNAL)
        report["counts"]["numerics"] = validate_numerics(value, root, before)
        report["numerics"] = record
        sys.path.insert(0, str(root))
        from server import ApplicationServer, RUNTIME_SOURCES
        matching(RUNTIME_SOURCES, source_map(before), "server-import")
        handler = logging.FileHandler(output / "server.log", encoding="utf-8")
        logging.getLogger().addHandler(handler)
        server = ApplicationServer(("127.0.0.1", 0), evidence=numerics)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/"
        report["owned_server"] = {"url": url, "source_root": str(root), "numerics": record}
        report["server_before"] = server_identity(url, root, before, output, "before")
        checkpoint(output, report, "owned-server-ready")
        token = "workbench-" + uuid.uuid4().hex
        for name, (tool_file, out_root, _) in PRODUCERS.items():
            if name == "browser" and integration:
                continue
            clean(inventory(root))
            need(inventory(root) == before, "Source drift before producer", name)
            child_output = EXTERNAL / out_root / token
            need(not child_output.exists(), "Producer root already exists", name)
            bounded(child_output)
            entry = {"output": str(child_output), "candidate_commit": before["commit"],
                     "tool_sha256": sha((root / "tools" / tool_file).read_bytes())}
            report["producers"][name] = entry
            argv = ["node", str(root / "tools" / tool_file), "--url", url,
                    "--out", str(child_output)]
            report["active"] = {"name": name, "argv": argv, "state": "RUNNING"}
            checkpoint(output, report, name + "-start")
            step = execute(argv, root, output, name)
            report["steps"].append(step)
            report["active"] = {"name": name, "state": "EXITED", "rc": step["rc"]}
            checkpoint(output, report, name + "-exit")
            reports = sorted(child_output.glob("run-*/report.json"))
            if len(reports) != 1:
                check_step(step)
                raise EvidenceError(f"{name} expected exactly one report, got {len(reports)}", name)
            child, child_record = read_report(reports[0], child_output)
            entry["report"] = child_record
            entry["terminal"] = {"rc": step["rc"], "exit_code": child.get("exit_code"),
                                 "verdict": child.get("verdict")}
            if first_failure(child) is not None:
                entry["producer_first_cause"] = first_failure(child)
            artifact_error = None
            try:
                entry["artifacts"] = artifacts(reports[0].parent, name)
            except Exception as exc:
                artifact_error = exc
                entry["artifact_failure"] = str(exc)
            # Preserve producer first cause even if later artifact readback also fails.
            result = validate_producer(name, child, step["rc"], root, before, reports[0].parent, url)
            if artifact_error is not None:
                raise artifact_error
            entry["validation"] = result
            report["remaining"].extend({"producer": name, **row} for row in result["remaining"])
            checkpoint(output, report, name + "-artifacts-validated")
            if name == "perf":
                # Recompute raw metrics/budgets with the actual producer's exported parser CLI.
                parse_output = child_output / "readback"
                report["active"] = {"name": "perf-readback", "state": "RUNNING"}
                checkpoint(output, report, "perf-readback-start")
                parsed_step = execute(["node", str(root / "tools" / tool_file), "--parse", str(reports[0]),
                                      "--out", str(parse_output)], root, output, "perf-readback")
                report["steps"].append(parsed_step)
                check_step(parsed_step)
                parsed_reports = list(parse_output.glob("run-*/report.json"))
                need(len(parsed_reports) == 1, "Performance parser report missing/ambiguous", name)
                parsed, parsed_record = read_report(parsed_reports[0], parse_output)
                need(parsed.get("verdict") == "OFFLINE_PARSE_PASS" and parsed.get("exit_code") == 0 and
                     parsed.get("scope") == "OFFLINE_PARSE_NOT_G5" and
                     parsed.get("parsed_source", {}).get("sha256") == child_record["sha256"] and
                     parsed.get("tool_sha256") == entry["tool_sha256"] and
                     not first_failure(parsed), "Performance raw readback failed/mismatched", name)
                entry["raw_metric_readback"] = parsed_record
                entry["readback_artifacts"] = artifacts(parsed_reports[0].parent, name)
                report["active"] = {"name": "perf-readback", "state": "EXITED", "rc": parsed_step["rc"]}
                checkpoint(output, report, "perf-readback-complete")
        report["server_after"] = server_identity(url, root, before, output, "after")
        matching({key: row["sha256"] for key, row in report["server_before"]["artifacts"].items()},
                 {key: row["sha256"] for key, row in report["server_after"]["artifacts"].items()},
                 "server-before-after")
        report["verdict"], _ = automation_verdict(report["remaining"])
        if report["remaining"]:
            report["first_cause"] = {"coordinate": "coverage", "message": "Producer coverage remains PARTIAL/NOT_TESTED",
                                     "detail": report["remaining"][0]}
    except Exception as exc:
        report["verdict"] = "FAIL"
        report["failure"] = {"type": type(exc).__name__, "coordinate": getattr(exc, "coordinate", "orchestration"),
                             "message": str(exc), "traceback": traceback.format_exc()}
        report["first_cause"] = report["failure"]
    finally:
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
                if thread is not None:
                    thread.join(10)
                    need(not thread.is_alive(), "Owned server did not stop", "cleanup")
                report["owned_server"]["stopped"] = True
            except Exception as exc:
                report["cleanup_failure"] = str(exc)
                report["first_cause"] = report.get("first_cause", {"coordinate": "cleanup", "message": str(exc)})
                report["verdict"] = "FAIL"
        if handler is not None:
            logging.getLogger().removeHandler(handler)
            handler.close()
        if before is not None:
            try:
                report["after"] = inventory(root)
                need(report["after"] == before, "Source inventory/commit/status drifted during run", "source-after")
                clean(report["after"])
            except Exception as exc:
                report["source_failure"] = str(exc)
                report["first_cause"] = report.get("first_cause", {"coordinate": "source-after", "message": str(exc)})
                report["verdict"] = "FAIL"
    report["counts"]["producers_validated"] = sum("validation" in row for row in report["producers"].values())
    report["counts"]["checks"] = sum(row.get("validation", {}).get("checks", 0) for row in report["producers"].values())
    report["counts"]["pngs"] = sum(len(row.get("validation", {}).get("pngs", [])) for row in report["producers"].values())
    report["counts"]["solves"] = sum(row.get("validation", {}).get("solves", 0) for row in report["producers"].values())
    report["finished_at"] = now()
    report["exit_code"] = 0 if report["verdict"] == PASS else (3 if report["verdict"] == PARTIAL else 1)
    report["tool_sha256"] = sha(Path(__file__).read_bytes())
    report["active"] = {"state": "COMPLETE"}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--integration-report", type=Path)
    args = parser.parse_args()
    try:
        output = new_output(args.out)
        report = run(ROOT, output, args.integration_report)
        path = output / "workbench.json"
        report = persist_result(output, report)
        print(verdict_line(report, path))
        return report["exit_code"]
    except PersistenceError as exc:
        print(verdict_line(exc.report, exc.path if exc.path is not None else args.out))
        print("PERSISTENCE_FAILURE " + json.dumps(compact_cause(exc.report["persistence_failure"]),
                                                  ensure_ascii=False))
        return 2
    except Exception as exc:
        print(f"OPENSIM_WORKBENCH verdict=FAIL rc=2 collected_at={now()}")
        print(f"FIRST_CAUSE type={type(exc).__name__} message={exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
