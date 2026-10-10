"""Fixed OpenSim releases: non-root preparation/probing, data-only installation.

Default invocation is a read-only plan. Production mutation requires --apply or
--revert and a reviewed root-owned controller, never a writable checkout script.
Filesystem fixture operations cannot call systemctl or use production paths.
"""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

ARTIFACTS = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-release")
UNIT = "opensim-web.service"
CONTROLLER = Path("/usr/local/libexec/opensim-release-app.py")
SOURCES = ("tools/render_page.py", "web/app.js", "web/app.css", "solver.py", "server.py")
EXPORTS = (*SOURCES, "requirements.txt")
PAYLOAD = {f"source/{name}" for name in EXPORTS} | {
    "evidence/numerics.json", "build/index-v1.html", "build/page-check.json"}
MAX_FILE = 2_000_000
COMMANDS: list[dict] = []


class ReleaseError(RuntimeError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ReleaseError(reason)


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def encode(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=True,
                       allow_nan=False, indent=2) + "\n").encode("ascii")


def decode(body: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ReleaseError(f"Nonfinite JSON: {value}")

    result = json.loads(body, object_pairs_hook=unique, parse_constant=constant)
    require(isinstance(result, dict) and bool(result), "Empty/non-object JSON")
    # This also rejects overflow floats such as 1e999.
    encode(result)
    return result


def no_symlinks(path: Path) -> None:
    require(path.is_absolute(), f"Absolute path required: {path}")
    for item in (path, *path.parents):
        require(not item.is_symlink(), f"Symlink rejected: {item}")


def read_file(path: Path) -> bytes:
    no_symlinks(path)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                f"Nonregular/hardlinked file: {path}")
        body = stream.read(MAX_FILE + 1)
    require(len(body) <= MAX_FILE, f"Oversized file: {path}")
    return body


def write_new(path: Path, body: bytes, mode: int = 0o444) -> None:
    no_symlinks(path)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                           os.O_NOFOLLOW, mode), "wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, mode)
    require(read_file(path) == body, f"Write/readback differs: {path}")
    sync_directory(path.parent)


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def optional_file(path: Path) -> bytes | None:
    no_symlinks(path)
    return read_file(path) if path.exists() else None


def replace_guarded(path: Path, before: bytes | None, after: bytes | None,
                    mode: int) -> None:
    require(optional_file(path) == before, f"Before-byte drift: {path}")
    if after is None:
        require(before is not None, f"Cannot remove absent file: {path}")
        path.unlink()
    else:
        candidate = path.parent / (".opensim-" + uuid4().hex)
        write_new(candidate, after, mode)
        require(optional_file(path) == before, f"Before-byte drift: {path}")
        os.replace(candidate, path)
    sync_directory(path.parent)
    require(optional_file(path) == after, f"After-byte identity failed: {path}")


def execute(argv: list[str], *, cwd: Path | None = None) -> str:
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
           "PYTHONDONTWRITEBYTECODE": "1", "OPENBLAS_NUM_THREADS": "1",
           "OMP_NUM_THREADS": "1"}
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    COMMANDS.append({"argv": argv, "rc": result.returncode,
                     "stdout": result.stdout, "stderr": result.stderr})
    require(result.returncode == 0,
            f"Command rc={result.returncode}: {argv!r}: {result.stderr.strip()}")
    return result.stdout.strip()


def git(root: Path, *args: str) -> str:
    return execute(["/usr/bin/git", "-c", "core.fsmonitor=false",
                    "-c", "core.hooksPath=/dev/null", *args], cwd=root)


def external(path: Path) -> Path:
    path = path.absolute()
    # The known 3FS root itself can resolve through the host mount mapping.
    base = ARTIFACTS.resolve()
    entry = ARTIFACTS if path.is_relative_to(ARTIFACTS) else base
    require(path.is_relative_to(entry) and path != entry,
            "Output must be below seat-release external root")
    cursor = entry
    for part in path.relative_to(entry).parts:
        cursor = cursor / part
        require(not cursor.is_symlink(), f"Symlink rejected below evidence root: {cursor}")
    resolved = path.resolve()
    require(base in resolved.parents, "Output must be below seat-release external root")
    no_symlinks(resolved)
    return resolved


def validate_evidence(body: bytes, solver_sha: str) -> dict:
    report = decode(body)
    require(report.get("verdict") == "PASS" and report.get("state") == "COMPLETE",
            "Evidence is not COMPLETE/PASS")
    require(report.get("solver", {}).get("sha256") == solver_sha,
            "Evidence solver SHA does not match committed export")
    require(type(report.get("checks_executed")) is int and
            report["checks_executed"] > 0, "Missing positive numerical check count")
    for key in ("cases", "rejections"):
        rows = report.get(key)
        require(isinstance(rows, list) and bool(rows) and
                all(isinstance(row, dict) and row.get("verdict") == "PASS" for row in rows),
                f"Empty/incomplete numerical {key}")
    groups = report.get("convergence")
    require(isinstance(groups, dict) and set(groups) == {"circle", "rectangle", "square"},
            "Missing convergence groups")
    for name, row in groups.items():
        require(isinstance(row, dict) and row.get("verdict") == "PASS" and
                row.get("resolutions") == [8, 16, 32], f"Invalid convergence: {name}")
        errors = row.get("relative_flow_error", {}).get("errors")
        require(isinstance(errors, list) and len(errors) == 3 and
                all(type(x) in (int, float) and math.isfinite(x) and x > 0 for x in errors)
                and errors[0] > errors[1] > errors[2], f"Invalid convergence errors: {name}")
    return report


def prepare(root: Path, commit: str, evidence: Path, bundle: Path) -> dict:
    require(os.geteuid() != 0, "Never execute source generation as root")
    require(re.fullmatch(r"[0-9a-f]{40}", commit) is not None, "Full commit SHA required")
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", "HEAD") == commit, "HEAD must equal expected commit")
    require(not git(root, "status", "--porcelain=v1", "--untracked-files=all"),
            "Source checkout must be completely clean, including untracked files")
    epoch = int(git(root, "show", "-s", "--format=%ct", commit))
    listing = {}
    for line in git(root, "ls-tree", "-r", commit).splitlines():
        metadata, name = line.split("\t", 1)
        listing[name] = metadata.split()
    bodies = {}
    for name in EXPORTS:
        require(name in listing and listing[name][:2] == ["100644", "blob"],
                f"Export must be a committed regular non-executable file: {name}")
        # cat-file bypasses worktree content, filters and generated files.
        result = subprocess.run(["/usr/bin/git", "cat-file", "blob", listing[name][2]],
                                cwd=root, capture_output=True)
        require(result.returncode == 0,
                f"git cat-file rc={result.returncode} path={name}: {result.stderr!r}")
        require(0 < len(result.stdout) <= MAX_FILE, f"Empty/oversized source: {name}")
        bodies[f"source/{name}"] = result.stdout
    bodies["evidence/numerics.json"] = read_file(evidence.resolve(strict=True))
    validate_evidence(bodies["evidence/numerics.json"], sha(bodies["source/solver.py"]))
    bundle = external(bundle)
    bundle.mkdir(parents=True, exist_ok=False)
    for name, body in bodies.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        write_new(path, body)
        os.utime(path, (epoch, epoch))
    (bundle / "build").mkdir()
    execute(["/usr/bin/python3", "-I", "-B", str(bundle / "source/tools/render_page.py"),
             "--out", str(bundle / "build"),
             "--evidence", "../evidence/numerics.json"], cwd=bundle / "source")
    for name in ("build/index-v1.html", "build/page-check.json"):
        bodies[name] = read_file(bundle / name)
        os.chmod(bundle / name, 0o444)
        os.utime(bundle / name, (epoch, epoch))
    sources = {name: sha(bodies[f"source/{name}"]) for name in SOURCES}
    fingerprint = hashlib.sha256()
    for name in SOURCES:
        fingerprint.update(name.encode("ascii") + b"\0" + bodies[f"source/{name}"] + b"\0")
    manifest = {
        "schema": 1, "commit": commit, "source_epoch": epoch,
        "source_sha256": sources, "page_build": fingerprint.hexdigest()[:12],
        "files": {name: {"sha256": sha(body), "bytes": len(body)}
                  for name, body in sorted(bodies.items())},
    }
    require(git(root, "rev-parse", "HEAD") == commit and
            not git(root, "status", "--porcelain=v1", "--untracked-files=all"),
            "Checkout drifted while preparing release")
    write_new(bundle / "manifest.json", encode(manifest))
    for path in sorted(bundle.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            os.chmod(path, 0o555)
    os.chmod(bundle, 0o555)
    digest = sha(read_file(bundle / "manifest.json"))
    validate_bundle(bundle, digest, commit)
    return {"state": "BUNDLE_PREPARED", "scope": "SOURCE_AND_NUMERICS_ONLY",
            "bundle": str(bundle), "manifest_sha256": digest, "commit": commit,
            "page_build": manifest["page_build"]}


def validate_bundle(bundle: Path, digest: str, commit: str) -> dict:
    require(re.fullmatch(r"[0-9a-f]{64}", digest or "") is not None,
            "Caller-pinned full manifest SHA256 required")
    require(re.fullmatch(r"[0-9a-f]{40}", commit or "") is not None,
            "Caller-pinned full commit SHA required")
    no_symlinks(bundle)
    raw = read_file(bundle / "manifest.json")
    require(sha(raw) == digest, "Bundle manifest SHA drift")
    manifest = decode(raw)
    require(set(manifest) == {"schema", "commit", "source_epoch", "source_sha256",
                              "page_build", "files"} and manifest["schema"] == 1,
            "Unknown manifest schema/fields")
    require(manifest["commit"] == commit, "Bundle commit does not match caller pin")
    epoch = manifest["source_epoch"]
    require(type(epoch) is int and epoch > 0, "Invalid source commit timestamp")
    require(isinstance(manifest["files"], dict) and set(manifest["files"]) == PAYLOAD,
            "Unexpected/missing release payload paths")
    expected_directories = {"source", "source/tools", "source/web", "build", "evidence"}
    files, directories = set(), set()
    for path in bundle.rglob("*"):
        no_symlinks(path)
        relative = path.relative_to(bundle).as_posix()
        if path.is_dir():
            directories.add(relative)
        else:
            files.add(relative)
    require(files == PAYLOAD | {"manifest.json"} and directories == expected_directories,
            "Unknown/missing files or directories in bundle")
    bodies = {}
    for name, record in manifest["files"].items():
        require(isinstance(record, dict) and set(record) == {"sha256", "bytes"},
                f"Invalid file manifest: {name}")
        body = read_file(bundle / name)
        require(type(record["bytes"]) is int and len(body) == record["bytes"] and
                sha(body) == record["sha256"], f"Payload byte/hash drift: {name}")
        require(stat.S_IMODE((bundle / name).stat().st_mode) == 0o444,
                f"Release file is not read-only: {name}")
        require((bundle / name).stat().st_mtime_ns == epoch * 1_000_000_000,
                f"Commit timestamp drift: {name}")
        bodies[name] = body
    require(all(stat.S_IMODE(path.stat().st_mode) == 0o555 for path in
                [bundle, *(bundle / name for name in expected_directories)]),
            "Release directories are not read-only")
    sources = {name: sha(bodies[f"source/{name}"]) for name in SOURCES}
    require(manifest["source_sha256"] == sources, "Runtime sources differ from manifest")
    fingerprint = hashlib.sha256()
    for name in SOURCES:
        fingerprint.update(name.encode("ascii") + b"\0" + bodies[f"source/{name}"] + b"\0")
    require(manifest["page_build"] == fingerprint.hexdigest()[:12],
            "PAGE build does not match exported source")
    page = bodies["build/index-v1.html"].decode("utf-8")
    require(manifest["page_build"] in page and 'id="case-form"' in page,
            "Generated page does not identify exported source")
    page_check = decode(bodies["build/page-check.json"])
    require(page_check.get("verdict") == "PASS" and page_check.get("html_sha256") ==
            sha(bodies["build/index-v1.html"]) and page_check.get("source_sha256_prefix") ==
            manifest["page_build"], "PAGE check does not match exported page/source")
    validate_evidence(bodies["evidence/numerics.json"], sources["solver.py"])
    dependencies = dependency_pins(bodies["source/requirements.txt"])
    require(set(dependencies) == {"numpy", "scipy"}, "Both numeric dependencies must be pinned")
    return manifest


def dependency_pins(body: bytes) -> dict[str, str]:
    pins = {}
    for line in body.decode("ascii").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = re.fullmatch(r"(numpy|scipy)==([0-9]+(?:\.[0-9]+){2})", line)
        require(match is not None and match[1] not in pins,
                "Only exactly pinned numpy/scipy dependency lines are allowed")
        pins[match[1]] = match[2]
    require(set(pins) == {"numpy", "scipy"}, "Missing required numeric dependency pin")
    return pins


class Layout:
    def __init__(self, fixture: Path | None = None):
        self.fixture = fixture
        if fixture is None:
            self.releases = Path("/opt/opensim/releases")
            self.unit = Path("/etc/systemd/system") / UNIT
            self.state = Path("/var/lib/opensim-release-app/current.json")
            self.logs = external(ARTIFACTS / "app-logs")
        else:
            fixture = external(fixture)
            self.releases = fixture / "releases"
            self.unit = fixture / "systemd" / UNIT
            self.state = fixture / "state/current.json"
            self.logs = fixture / "logs"
        for path in (self.releases, self.unit, self.state, self.logs):
            no_symlinks(path)

    def release(self, commit: str, digest: str) -> Path:
        require(re.fullmatch(r"[0-9a-f]{40}", commit or "") is not None and
                re.fullmatch(r"[0-9a-f]{64}", digest or "") is not None, "Invalid release ID")
        return self.releases / (commit + "-" + digest)

    def create(self) -> None:
        for directory in (self.releases, self.unit.parent, self.state.parent, self.logs):
            directory.mkdir(parents=True, exist_ok=True, mode=0o755)
            no_symlinks(directory)


def root_owned(path: Path) -> None:
    no_symlinks(path)
    for item in (path, *path.parents):
        if item.exists():
            info = item.stat()
            require(info.st_uid == 0 and not info.st_mode & 0o022,
                    f"Root-owned, non-writable trusted path required: {item}")


def require_controller() -> None:
    require(os.geteuid() == 0, "Production mutation requires authorized root caller")
    require(Path(__file__).absolute() == CONTROLLER,
            "Refusing root execution of checkout controller; install reviewed trusted copy first")
    root_owned(CONTROLLER)
    require(stat.S_ISREG(CONTROLLER.stat().st_mode), "Controller is not a regular file")


def unit_bytes(layout: Layout, release: Path) -> bytes:
    # No unit template, command, user, host or port is accepted from the bundle.
    for path in (release, layout.logs):
        require(re.fullmatch(r"/[A-Za-z0-9_./-]+", str(path)) is not None,
                "Unsafe systemd path characters")
    return (
        "[Unit]\nDescription=OpenSim fixed-release Fluid FEM\nAfter=network.target\n\n"
        "[Service]\nType=simple\nUser=infra\nGroup=infra\n"
        f"WorkingDirectory={release}/source\n"
        f"ExecStart=/usr/bin/python3 -E -s -B {release}/source/server.py "
        "--host 192.168.1.30 --port 8772 --evidence ../evidence/numerics.json "
        f"--log-dir {layout.logs}\n"
        "Restart=on-failure\nRestartSec=5\nNoNewPrivileges=true\nPrivateTmp=true\n"
        "ProtectSystem=strict\nProtectHome=true\n"
        f"ReadWritePaths={layout.logs}\n"
        "Environment=OPENBLAS_NUM_THREADS=1\nEnvironment=OMP_NUM_THREADS=1\n"
        "MemoryMax=512M\nTasksMax=32\nUMask=0027\n"
    ).encode("ascii")


def active_receipt(receipt: dict | None) -> dict | None:
    for _ in range(16):
        if receipt is None:
            return None
        require(receipt.get("schema") == 1, "Unknown install receipt schema")
        if receipt.get("state") == "REVERTED":
            receipt = receipt.get("previous")
        else:
            require(receipt.get("state") in ("INSTALLING", "FILES_INSTALLED",
                                           "SERVICE_RESTARTED_UNVERIFIED"),
                    "Interrupted/unknown state requires inspection, not overwrite")
            return receipt
    raise ReleaseError("Receipt history too deep; explicit operator review required")


def validate_live(layout: Layout, receipt: dict) -> dict:
    require(receipt.get("schema") == 1, "Invalid install receipt")
    release = layout.release(receipt["commit"], receipt["manifest_sha256"])
    require(receipt.get("release") == str(release), "Receipt release path escaped layout")
    manifest = validate_bundle(release, receipt["manifest_sha256"], receipt["commit"])
    require(read_file(layout.unit) == unit_bytes(layout, release) and
            sha(read_file(layout.unit)) == receipt["unit_sha256"], "Live unit byte drift")
    require(stat.S_IMODE(layout.unit.stat().st_mode) == 0o644, "Live unit mode drift")
    if layout.fixture is None:
        for path in (layout.unit, layout.state, release, *release.rglob("*")):
            root_owned(path)
        validate_unit_locations(layout)
    return manifest


def validate_unit_locations(layout: Layout) -> None:
    """Reject alternate fragments/overrides before any privileged service action."""
    require(layout.fixture is None, "Systemd path checks are production-only")
    bases = (Path("/etc/systemd/system"), Path("/run/systemd/system"),
             Path("/usr/local/lib/systemd/system"), Path("/usr/lib/systemd/system"),
             Path("/lib/systemd/system"), Path("/run/systemd/transient"),
             Path("/run/systemd/generator"), Path("/run/systemd/generator.early"),
             Path("/run/systemd/generator.late"), Path("/etc/systemd/system.control"),
             Path("/run/systemd/system.control"))
    for directory in bases:
        fragment = directory / UNIT
        if fragment != layout.unit:
            require(not fragment.exists() and not fragment.is_symlink(),
                    f"Alternate OpenSim service fragment: {fragment}")
        for name in (UNIT + ".d", "service.d", "opensim-.service.d"):
            override = directory / name
            require(not override.exists() and not override.is_symlink(),
                    f"Unmanaged systemd override directory: {override}")


def validate_effective_unit(layout: Layout, absent: bool = False,
                            require_current: bool = False) -> None:
    validate_unit_locations(layout)
    result = execute(["/usr/bin/systemctl", "show", UNIT,
                      "--property=LoadState,FragmentPath,DropInPaths,NeedDaemonReload"])
    properties = dict(line.split("=", 1) for line in result.splitlines() if "=" in line)
    require(set(properties) == {"LoadState", "FragmentPath", "DropInPaths", "NeedDaemonReload"},
            "Incomplete effective systemd identity")
    require(not properties["DropInPaths"], "Unmanaged systemd drop-ins")
    if absent:
        require(properties["LoadState"] == "not-found" and not properties["FragmentPath"],
                "Unknown loaded OpenSim service; refusing install")
    else:
        require(properties["LoadState"] == "loaded" and
                properties["FragmentPath"] == str(layout.unit),
                "Effective systemd fragment is not this managed unit")
        if require_current:
            require(properties["NeedDaemonReload"] == "no",
                    "Effective systemd unit still needs daemon-reload")


def plan(layout: Layout, bundle: Path, digest: str, commit: str) -> dict:
    manifest = validate_bundle(bundle, digest, commit)
    before = optional_file(layout.state)
    previous = active_receipt(decode(before) if before is not None else None)
    old_unit = optional_file(layout.unit)
    if previous is None:
        require(old_unit is None, "Unknown existing opensim-web unit; refusing overwrite")
    else:
        require(previous["state"] != "INSTALLING", "Interrupted install; use guarded revert")
        validate_live(layout, previous)
    target = layout.release(commit, digest)
    if target.exists():
        validate_bundle(target, digest, commit)
    return {"state": "DRY_RUN", "scope": "FILES_ONLY_NO_SERVICE_OR_PUBLIC_PASS",
            "commit": commit, "manifest_sha256": digest, "release": str(target),
            "unit_sha256": sha(unit_bytes(layout, target)), "page_build": manifest["page_build"],
            "before_state_sha256": sha(before) if before is not None else None,
            "before_unit_sha256": sha(old_unit) if old_unit is not None else None}


@contextmanager
def locked(layout: Layout):
    lock = layout.state.parent / "install.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        require(stat.S_ISREG(os.fstat(descriptor).st_mode) and
                os.fstat(descriptor).st_nlink == 1, "Unsafe install lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def copy_release(bundle: Path, target: Path, digest: str, commit: str) -> None:
    if target.exists():
        validate_bundle(target, digest, commit)
        return
    candidate = target.parent / (".staging-" + uuid4().hex)
    candidate.mkdir(mode=0o755)
    manifest = validate_bundle(bundle, digest, commit)
    for name in sorted(PAYLOAD | {"manifest.json"}):
        path = candidate / name
        path.parent.mkdir(parents=True, exist_ok=True)
        body = read_file(bundle / name)
        expected = digest if name == "manifest.json" else manifest["files"][name]["sha256"]
        require(sha(body) == expected, f"Bundle drift before copy: {name}")
        write_new(path, body)
        if name != "manifest.json":
            os.utime(path, (manifest["source_epoch"], manifest["source_epoch"]))
    for path in sorted(candidate.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            os.chmod(path, 0o555)
    os.chmod(candidate, 0o555)
    validate_bundle(candidate, digest, commit)
    require(not target.exists(), "Concurrent release path creation")
    os.rename(candidate, target)
    sync_directory(target.parent)
    validate_bundle(target, digest, commit)


def service(action: str, layout: Layout) -> None:
    require(layout.fixture is None, "Fixtures must never operate systemd")
    require(action in ("restart", "stop", "reload"), "Forbidden service action")
    argv = (["/usr/bin/systemctl", "daemon-reload"] if action == "reload" else
            ["/usr/bin/systemctl", action, UNIT])
    execute(argv)


def install(layout: Layout, bundle: Path, digest: str, commit: str,
            authorize_restart: bool = False) -> dict:
    if layout.fixture is None:
        require_controller()
        for path in (layout.releases, layout.unit, layout.state):
            root_owned(path)
        import pwd
        require(layout.logs.is_dir() and
                layout.logs.stat().st_uid == pwd.getpwnam("infra").pw_uid,
                "Application log directory must already exist and be owned by infra")
    else:
        require(os.geteuid() != 0 and not authorize_restart,
                "Fixture must be non-root with no service authorization")
    initial = plan(layout, bundle, digest, commit)
    if layout.fixture is None:
        validate_effective_unit(layout, absent=initial["before_unit_sha256"] is None)
    layout.create()
    with locked(layout):
        current_plan = plan(layout, bundle, digest, commit)
        require(current_plan == initial, "Install plan drifted before lock")
        before_state = optional_file(layout.state)
        before_unit = optional_file(layout.unit)
        previous = active_receipt(decode(before_state) if before_state is not None else None)
        if previous is not None:
            require(previous["manifest_sha256"] != digest or previous["commit"] != commit,
                    "Release already installed; use --check")
        release = layout.release(commit, digest)
        copy_release(bundle, release, digest, commit)
        if layout.fixture is None:
            for path in (release, *release.rglob("*")):
                root_owned(path)
        receipt = {"schema": 1, "state": "INSTALLING", "commit": commit,
                   "manifest_sha256": digest, "release": str(release),
                   "unit_sha256": sha(unit_bytes(layout, release)), "previous": previous,
                   "before_unit_base64": (base64.b64encode(before_unit).decode("ascii")
                                          if before_unit is not None else None),
                   "restart_authorized": authorize_restart,
                   "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        raw = encode(receipt)
        replace_guarded(layout.state, before_state, raw, 0o644)
        require(optional_file(layout.state) == raw, "Receipt drift before unit write")
        if previous is not None:
            validate_live(layout, previous)
        replace_guarded(layout.unit, before_unit, unit_bytes(layout, release), 0o644)
        validate_live(layout, receipt)
        receipt["state"] = "FILES_INSTALLED"
        after = encode(receipt)
        replace_guarded(layout.state, raw, after, 0o644)
        if authorize_restart:
            service("reload", layout)
            validate_live(layout, receipt)
            validate_effective_unit(layout, require_current=True)
            require(optional_file(layout.state) == after, "Receipt drift before restart")
            service("restart", layout)
            receipt["state"] = "SERVICE_RESTARTED_UNVERIFIED"
            replace_guarded(layout.state, after, encode(receipt), 0o644)
        validate_live(layout, receipt)
        require(optional_file(layout.state) == encode(receipt), "Final receipt byte drift")
        return receipt


def revert(layout: Layout, authorize_restart: bool = False) -> dict:
    if layout.fixture is None:
        require_controller()
        root_owned(layout.state)
    else:
        require(os.geteuid() != 0 and not authorize_restart,
                "Fixture must be non-root with no service authorization")
    with locked(layout):
        raw = read_file(layout.state)
        receipt = decode(raw)
        require(receipt.get("state") in ("INSTALLING", "FILES_INSTALLED",
                                       "SERVICE_RESTARTED_UNVERIFIED"),
                "Receipt is not an installed/revertible release")
        previous = receipt["previous"]
        before = receipt["before_unit_base64"]
        restored = base64.b64decode(before, validate=True) if before is not None else None
        if previous is None:
            require(restored is None, "Unknown original unit cannot be restored")
        else:
            target = layout.release(previous["commit"], previous["manifest_sha256"])
            validate_bundle(target, previous["manifest_sha256"], previous["commit"])
            require(restored == unit_bytes(layout, target), "Previous unit snapshot mismatch")
        release = layout.release(receipt["commit"], receipt["manifest_sha256"])
        require(receipt["release"] == str(release), "Receipt release path escaped layout")
        validate_bundle(release, receipt["manifest_sha256"], receipt["commit"])
        interrupted_before_switch = (receipt["state"] == "INSTALLING" and
                                     optional_file(layout.unit) == restored)
        if not interrupted_before_switch:
            validate_live(layout, receipt)
        # A restarted service must not keep running a removed/reverted unit.
        require(not receipt["restart_authorized"] or authorize_restart,
                "Revert of restarted service requires fresh explicit service authorization")
        if authorize_restart and previous is None:
            validate_effective_unit(layout)
            service("stop", layout)
        require(read_file(layout.state) == raw, "Receipt drift before revert")
        if interrupted_before_switch:
            require(optional_file(layout.unit) == restored, "Interrupted install unit drift")
        else:
            validate_live(layout, receipt)
            replace_guarded(layout.unit, unit_bytes(layout, release), restored, 0o644)
        if authorize_restart:
            service("reload", layout)
            if previous is not None:
                validate_live(layout, previous)
                validate_effective_unit(layout, require_current=True)
                service("restart", layout)
        if previous is not None:
            validate_live(layout, previous)
        require(optional_file(layout.unit) == restored, "Revert byte identity failed")
        receipt["state"] = "REVERTED"
        replace_guarded(layout.state, raw, encode(receipt), 0o644)
        require(read_file(layout.state) == encode(receipt), "Reverted receipt drift")
        # Fixed releases and transaction evidence are retained, never deleted.
        return receipt


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ReleaseError(f"Probe redirect rejected: status={code}")


def fetch(base: str, path: str, payload: dict | None = None) -> bytes:
    data = encode(payload) if payload is not None else None
    request = Request(base + path, data=data,
                      headers={"Content-Type": "application/json", "Cache-Control": "no-cache"})
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=20) as response:
        require(response.status == 200 and response.geturl() == base + path,
                f"Probe HTTP identity/status failed: {path}")
        body = response.read(MAX_FILE + 1)
        require(len(body) <= MAX_FILE, f"Probe response too large: {path}")
        return body


def probe(layout: Layout, receipt: dict, base: str | None = None) -> dict:
    require(os.geteuid() != 0, "Never execute a released solver oracle as root")
    manifest = validate_live(layout, receipt)
    if layout.fixture is None:
        validate_effective_unit(layout, require_current=True)
        require(base in (None, "http://192.168.1.30:8772"), "Only fixed app endpoint allowed")
        base = "http://192.168.1.30:8772"
    else:
        require(base is not None and
                re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", base) is not None and
                0 < int(base.rsplit(":", 1)[1]) < 65536, "Fixture probe must be loopback")
    release = Path(receipt["release"])
    for package, version in dependency_pins(
            read_file(release / "source/requirements.txt")).items():
        require(importlib.metadata.version(package) == version, f"Dependency drift: {package}")
    started = time.time()
    before_state = read_file(layout.state)
    health = decode(fetch(base, "/api/health"))
    require(health.get("status") == "ok" and health.get("schema_version") == 1 and
            health.get("source_sha256") == manifest["source_sha256"] and
            health.get("page_build") == manifest["page_build"], "Probe runtime identity differs")
    require(fetch(base, "/") == read_file(release / "build/index-v1.html"),
            "Served PAGE bytes do not match fixed release")
    for name in ("web/app.js", "web/app.css"):
        require(fetch(base, "/" + name) == read_file(release / "source" / name),
                f"Served asset byte drift: {name}")
    spec = importlib.util.spec_from_file_location("_opensim_release_solver",
                                                  release / "source/solver.py")
    require(spec is not None and spec.loader is not None, "Cannot load committed solver oracle")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    # Compile the already-fingerprinted bytes; no stale pyc or worktree imports.
    exec(compile(read_file(release / "source/solver.py"), str(spec.origin), "exec"),
         module.__dict__)
    checks = []
    pressure = 0.5 + secrets.randbelow(500_000) / 1_000_000
    for geometry, height in (("circle", 4.0), ("rectangle", 2.0)):
        config = {"geometry": geometry, "width_mm": 4.0, "height_mm": height,
                  "length_mm": 100.0, "pressure_pa": pressure, "viscosity_pa_s": 0.001,
                  "density_kg_m3": 998.0, "resolution": 8}
        actual = decode(fetch(base, "/api/solve", config))
        expected = module.solve_case(config)
        require(actual.get("schema_version") == 1 and actual.get("config") == expected["config"]
                and actual.get("mesh") == expected["mesh"], "Probe FEM config/mesh differs")
        provenance = actual.get("reproducibility", {})
        canonical = json.dumps(expected["config"], sort_keys=True, allow_nan=False,
                               separators=(",", ":")).encode("utf-8")
        require(provenance.get("source_sha256") == manifest["source_sha256"] and
                provenance.get("solver_sha256") == manifest["source_sha256"]["solver.py"] and
                provenance.get("config_sha256") == sha(canonical), "Probe solver identity differs")
        computed = datetime.fromisoformat(provenance["computed_at"])
        require(computed.tzinfo is not None and started - 120 <= computed.timestamp() <=
                time.time() + 120, "Probe stale computation timestamp")
        actual_fields = actual.get("fields", {}).get("velocity_m_s")
        expected_fields = expected["fields"]["velocity_m_s"]
        require(isinstance(actual_fields, list) and len(actual_fields) == len(expected_fields),
                "Probe incomplete velocity field")
        for observed, reference in zip(actual_fields, expected_fields):
            require(type(observed) in (float, int) and math.isfinite(observed) and
                    math.isclose(observed, reference, rel_tol=1e-9, abs_tol=1e-14),
                    "Probe FEM velocity differs")
        for key, reference in expected["metrics"].items():
            observed = actual.get("metrics", {}).get(key)
            require(type(observed) in (float, int) and math.isfinite(observed) and
                    math.isclose(observed, reference, rel_tol=1e-9, abs_tol=1e-14),
                    f"Probe FEM metric differs: {key}")
        checks.append({"geometry": geometry, "config_sha256": sha(canonical),
                       "node_count": expected["metrics"]["node_count"],
                       "computed_at": provenance["computed_at"]})
    require(decode(fetch(base, "/api/health")) == health, "Runtime drift during probe")
    validate_live(layout, receipt)
    if layout.fixture is None:
        validate_effective_unit(layout, require_current=True)
    require(read_file(layout.state) == before_state, "Receipt drift during probe")
    return {"state": "LOCAL_APP_PASS", "scope": "LOCAL_TEST" if layout.fixture else
            "PRIVATE_APP_CODE_AND_FEM_NOT_PUBLIC", "checks": checks, "url": base,
            "page_build": manifest["page_build"]}


def check(layout: Layout, with_probe: bool = False, base: str | None = None) -> dict:
    receipt = active_receipt(decode(read_file(layout.state)))
    require(receipt is not None, "No installed app; initial release has been reverted")
    require(receipt["state"] != "INSTALLING", "Incomplete transaction is not installed")
    validate_live(layout, receipt)
    if with_probe:
        return probe(layout, receipt, base)
    return {"state": "FILES_VALIDATED", "scope": "NO_RUNTIME_OR_PUBLIC_PASS",
            "commit": receipt["commit"], "manifest_sha256": receipt["manifest_sha256"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--revert", action="store_true")
    modes.add_argument("--check", action="store_true")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--commit")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--fixture-url")
    parser.add_argument("--authorize-restart", choices=(UNIT,))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    COMMANDS.clear()
    report = {"state": "FAILED", "scope": "NOT_VERIFIED",
              "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    out = None
    try:
        require(not args.probe or args.check, "--probe requires --check")
        require(not args.fixture_url or (args.fixture_root and args.probe),
                "--fixture-url requires a fixture probe")
        require(not args.authorize_restart or (args.apply or args.revert),
                "Service authorization applies only to --apply/--revert")
        if os.geteuid() == 0:
            require_controller()
        out = external(args.out)
        out.mkdir(parents=True, exist_ok=False)
        if args.prepare:
            require(args.source_root is not None and args.evidence is not None and
                    args.bundle is not None, "Preparation requires source-root/evidence/bundle")
            report.update(prepare(args.source_root, args.commit or "",
                                  args.evidence, args.bundle))
        else:
            layout = Layout(args.fixture_root)
            if args.revert:
                report.update(revert(layout, bool(args.authorize_restart)))
            elif args.check:
                report.update(check(layout, args.probe, args.fixture_url))
            else:
                require(args.bundle is not None, "Plan/apply requires --bundle")
                report.update((install if args.apply else plan)(
                    layout, external(args.bundle), args.manifest_sha256 or "",
                    args.commit or "", **({"authorize_restart": bool(args.authorize_restart)}
                                         if args.apply else {})))
    except Exception as exc:
        report["state"] = "FAILED"
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
    report["commands"] = COMMANDS.copy()
    report["exit_code"] = 1 if report["state"] == "FAILED" else 0
    line = (f"OPENSIM_APP_RELEASE state={report['state']} rc={report['exit_code']} "
            f"collected_at={report['collected_at']} out={out or args.out}")
    if out is not None:
        try:
            write_new(out / "report.json", encode(report), 0o644)
            write_new(out / "execution.log",
                      (line + "\n" + report.get("failure", {}).get("reason", "") + "\n").
                      encode("utf-8"), 0o644)
        except Exception as exc:
            print(f"Evidence failure: {type(exc).__name__}: {exc}; first result={line}",
                  file=sys.stderr)
            return 1
    print(line)
    if report.get("failure"):
        print("First cause: " + report["failure"]["reason"], file=sys.stderr)
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
