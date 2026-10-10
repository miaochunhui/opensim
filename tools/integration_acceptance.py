"""Run bounded local integration with immutable external logs and evidence.

This is not a release certifier. Full-browser mode checks the existing complete
viewport/keyboard run; public, accessibility, performance and manual gates remain
separate. Children run serially so their real FEM requests cannot collide.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1")
VIEWPORTS = {
    "mobile-narrow", "mobile-common", "mobile-wide", "tablet-portrait",
    "desktop-small", "desktop-browser-common", "desktop-standard", "desktop-wide",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
    )
    if result.returncode:
        raise RuntimeError(f"git {args[0]} rc={result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def identity(root: Path) -> dict:
    files = git(root, "ls-files", "-z").split("\0")
    sources = {}
    for relative in files:
        if not relative:
            continue
        file = root / relative
        if file.is_symlink() or not file.is_file():
            raise ValueError(f"Tracked file missing, nonregular or symlink: {relative}")
        sources[relative] = digest(file.read_bytes())
    if not sources:
        raise ValueError("Empty tracked source inventory")
    return {
        "commit": git(root, "rev-parse", "HEAD"),
        "status": git(root, "status", "--porcelain"),
        "tracked_sha256": sources,
    }


def output_directory(path: Path) -> Path:
    external = EXTERNAL.resolve(strict=True)
    resolved = path.resolve()
    if external not in resolved.parents:
        raise ValueError("Use a new directory below the OpenSim external artifact root")
    # Permit the host's known /hf3fs-jd mount mapping, not child symlinks.
    logical = path.absolute()
    base = EXTERNAL.absolute()
    if base in logical.parents:
        child = base
        for part in logical.relative_to(base).parts:
            child = child / part
            if child.is_symlink():
                raise ValueError("Output may not traverse a child symlink")
    ancestor = resolved
    while not ancestor.exists():
        ancestor = ancestor.parent
    if ancestor.is_symlink():
        raise ValueError("Output may not traverse a symlink")
    path = resolved
    path.mkdir(parents=True, exist_ok=False)
    probe = path / "write-probe"
    with probe.open("xb") as stream:
        stream.write(b"opensim-integration-write-probe\n")
    if probe.read_bytes() != b"opensim-integration-write-probe\n":
        raise OSError("External artifact write/read probe failed")
    return path


def read_report(path: Path, output: Path) -> tuple[dict, dict]:
    resolved = path.resolve(strict=True)
    if output.resolve() not in resolved.parents or path.is_symlink():
        raise ValueError(f"Child report escaped its output directory: {path}")
    content = resolved.read_bytes()
    value = json.loads(content)
    if not isinstance(value, dict) or not value:
        raise ValueError(f"Empty or non-object child report: {path}")
    return value, {"path": str(resolved), "bytes": len(content), "sha256": digest(content)}


def validate_local(value: dict, root: Path) -> None:
    if value.get("verdict") != "PASS":
        raise ValueError("Local acceptance did not PASS")
    steps = value.get("steps")
    if not isinstance(steps, list) or len(steps) != 5:
        raise ValueError("Local acceptance lacks all five stages")
    if any(not isinstance(step, dict) or step.get("rc") != 0 for step in steps):
        raise ValueError("Local stage failed or lacks its exit code")
    source_paths = {
        "solver.py", "server.py", "web/app.js", "web/app.css",
        "tools/render_page.py", "tools/validate_numerics.py",
    }
    hashes = value.get("source_sha256")
    if not isinstance(hashes, dict) or set(hashes) != source_paths:
        raise ValueError("Local source inventory is incomplete")
    for relative in source_paths:
        if hashes[relative] != digest((root / relative).read_bytes()):
            raise ValueError(f"Stale local evidence: {relative}")
    match = re.search(r"Ran ([1-9][0-9]*) tests", steps[0].get("stderr", ""))
    if match is None or "\nOK" not in steps[0].get("stderr", ""):
        raise ValueError("Unit runner did not emit a nonempty completion marker")


def validate_browser(value: dict, root: Path, commit: str) -> None:
    if value.get("mode") != "OPENSIM_ACCEPTANCE" or value.get("verdict") != "AUTOMATED_PASS":
        raise ValueError("Expected a complete real OpenSim browser run, not a probe/selftest")
    if value.get("commit") != commit:
        raise ValueError("Browser evidence commit differs from the integration candidate")
    if value.get("tool_sha256") != digest((root / "tools/browser_check.mjs").read_bytes()):
        raise ValueError("Browser tool identity mismatch")
    checks = value.get("checks")
    matrix = value.get("matrix")
    screenshots = value.get("screenshots")
    if not isinstance(checks, list) or not checks or any(
        not isinstance(row, dict) or row.get("status") != "PASS" for row in checks
    ):
        raise ValueError("Browser checks empty, failed or incomplete")
    if not isinstance(matrix, list) or not matrix:
        raise ValueError("Browser viewport matrix empty")
    names = {row.get("name") for row in matrix if isinstance(row, dict)}
    if not VIEWPORTS.issubset(names):
        raise ValueError(f"Browser baseline viewports missing: {sorted(VIEWPORTS - names)}")
    if any(row.get("status") != "PASS" for row in matrix):
        raise ValueError("Browser viewport matrix has unfinished or failed rows")
    if not isinstance(screenshots, list) or not screenshots:
        raise ValueError("Browser screenshot evidence empty")


def execute(argv: list[str], cwd: Path, output: Path, name: str) -> dict:
    start = timestamp()
    env = dict(
        os.environ, PYTHONDONTWRITEBYTECODE="1",
        OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1",
    )
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    stdout = output / f"{name}.stdout.log"
    stderr = output / f"{name}.stderr.log"
    stdout.write_text(result.stdout, encoding="utf-8")
    stderr.write_text(result.stderr, encoding="utf-8")
    return {
        "name": name, "argv": argv, "started_at": start, "finished_at": timestamp(),
        "rc": result.returncode,
        "stdout": {"path": str(stdout), "sha256": digest(stdout.read_bytes())},
        "stderr": {"path": str(stderr), "sha256": digest(stderr.read_bytes())},
    }


def run(root: Path, output: Path, full_browser: bool, local_url: str) -> dict:
    report = {
        "schema_version": 1, "scope": "LOCAL_INTEGRATION_NOT_RELEASE",
        "started_at": timestamp(), "verdict": "RUNNING", "steps": [], "artifacts": {},
        "release_verdict": "NOT_CERTIFIED",
        "unverified": [
            "Public DNS/TLS and code/FEM gate",
            "G3 accessibility and G5 public performance",
            "Real touch/soft keyboard and desktop/mobile screen readers",
            "Production apply/revert and persistent automatic deployment",
        ],
    }
    try:
        before = identity(root)
        report["before"] = before
        if before["status"]:
            raise ValueError("Integration requires a clean committed candidate")
        local = output / "local"
        step = execute(
            [sys.executable, "-B", str(root / "tools/acceptance.py"), "--out", str(local)],
            root, output, "local",
        )
        report["steps"].append(step)
        if step["rc"]:
            raise RuntimeError(f"local acceptance failed rc={step['rc']}; see {step['stderr']['path']}")
        value, artifact = read_report(local / "acceptance.json", output)
        validate_local(value, root)
        report["artifacts"]["local"] = artifact
        if full_browser:
            step = execute(
                ["node", str(root / "tools/browser_check.mjs"), "--url", local_url,
                 "--out", str(output / "browser")],
                root, output, "browser",
            )
            report["steps"].append(step)
            if step["rc"]:
                raise RuntimeError(f"full browser failed rc={step['rc']}; see {step['stderr']['path']}")
            reports = list((output / "browser").glob("run-*/report.json"))
            if len(reports) != 1:
                raise ValueError(f"Expected exactly one browser report, got {len(reports)}")
            value, artifact = read_report(reports[0], output)
            validate_browser(value, root, before["commit"])
            report["artifacts"]["browser"] = artifact
        after = identity(root)
        report["after"] = after
        if after != before:
            raise ValueError("Source tree, Git commit or status changed during integration")
        report["verdict"] = "LOCAL_INTEGRATION_PASS"
    except Exception as exc:
        report["verdict"] = "FAIL"
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    report["finished_at"] = timestamp()
    report["tool_sha256"] = digest(Path(__file__).read_bytes())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--full-browser", action="store_true")
    parser.add_argument("--local-url", default="http://127.0.0.1:8776/")
    args = parser.parse_args()
    url = urlsplit(args.local_url)
    if (
        url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1")
        or url.username or url.password or url.query or url.fragment or url.path != "/"
    ):
        parser.error("Only a credential-free loopback HTTP root URL is accepted")
    output = output_directory(args.out)
    report = run(ROOT, output, args.full_browser, args.local_url)
    file = output / "integration.json"
    with file.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    reread = json.loads(file.read_bytes())
    if reread != report:
        raise OSError("Integration report readback mismatch")
    line = (
        f"OPENSIM_INTEGRATION verdict={report['verdict']} "
        f"steps={len(report['steps'])} collected_at={report['finished_at']} report={file}"
    )
    with (output / "integration.log").open("x", encoding="utf-8") as stream:
        stream.write(line + "\n")
    print(line)
    if "failure" in report:
        print(json.dumps(report["failure"], ensure_ascii=False), file=sys.stderr)
    return 0 if report["verdict"] == "LOCAL_INTEGRATION_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
