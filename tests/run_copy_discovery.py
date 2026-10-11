"""Bounded original copy discover runner; artifacts are external, never release.

Run --out a new child of the fixed S3 evidence root. It launches the original
unittest discovery entry, records actual child/log/source bytes and refuses an
empty or failed suite. It never injects an output directory into the tests.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
BASE = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/authorized-s3-copy-discovery-fix-20261011")
SOURCES = ("tests/test_copy_control.py", "tests/run_copy_discovery.py",
           "tools/render_page.py", "web/app.js", "web/app.css", "solver.py",
           "server.py", "tools/browser_audit.mjs", "docs/testing/COPY_CONTROL.md")


def snapshot():
    def identity(info):
        return [info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
                info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
    values = {}
    for name in SOURCES:
        path = ROOT / name
        info = path.lstat()
        raw = path.read_bytes()
        if identity(path.lstat()) != identity(info):
            raise RuntimeError("SOURCE_READ_DRIFT")
        values[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "identity": identity(info)}
    return values


def write(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        if stream.write(raw) != len(raw):
            raise OSError("SHORT_EVIDENCE_WRITE")
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != raw:
        raise OSError("EVIDENCE_READBACK_DRIFT")


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--entry", choices=("discover", "cli"), default="discover")
    parser.add_argument("--missing-evidence-child", action="store_true")
    parser.add_argument("--negative", choices=("missing-evidence", "invalid-out"))
    args = parser.parse_args()
    out = args.out.absolute()
    if os.geteuid() == 0 or ".." in args.out.parts or not out.is_relative_to(BASE) or out == BASE:
        parser.error("NONROOT_FRESH_FIXED_OUTPUT_REQUIRED")
    out.mkdir(parents=True, exist_ok=False)
    if args.missing_evidence_child:
        import unittest
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_copy_control.py")
        module = sys.modules["test_copy_control"]
        module.EVIDENCE = out / "absent-numerics.json"
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    before = snapshot()
    started = time.monotonic()
    command = [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests",
               "-p", "test_copy_control.py", "-v"]
    if args.entry == "cli":
        command = [sys.executable, "-B", str(ROOT / "tests/test_copy_control.py"),
                   "--out", str(out / "cli-tests")]
    if args.negative == "missing-evidence":
        command = [sys.executable, "-B", str(Path(__file__).resolve()),
                   "--missing-evidence-child", "--out", str(out / "negative-child")]
    if args.negative == "invalid-out":
        command = [sys.executable, "-B", str(ROOT / "tests/test_copy_control.py"),
                   "--out", str(ROOT / "forbidden-copy-evidence")]
    write(out / "run-start.json", encode({"argv": command, "cwd": str(ROOT),
          "source_before": before, "started_at": datetime.now(timezone.utc).isoformat()}))
    with (out / "stdout.log").open("xb") as stdout, (out / "stderr.log").open("xb") as stderr:
        child = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
    after = snapshot()
    raw = (out / "stderr.log").read_bytes()
    stdout_raw = (out / "stdout.log").read_bytes()
    outputs = re.findall(rb"^COPY_CONTROL_OUTPUT=(.+)$", stdout_raw, re.MULTILINE)
    lifecycle = None
    tests_run = None
    cli_report = None
    if not child.returncode:
        if len(outputs) != 1:
            raise RuntimeError("EXACT_COPY_OUTPUT_READBACK_REQUIRED")
        evidence = Path(outputs[0].decode())
        if not evidence.is_relative_to(BASE) or evidence == BASE:
            raise RuntimeError("COPY_OUTPUT_OUTSIDE_FIXED_ROOT")
        lifecycle = json.loads((evidence / "lifecycle-readback.json").read_bytes())
        if args.entry == "cli":
            cli_report = json.loads((evidence / "report.json").read_bytes())
            tests_run = cli_report["tests"]
    if args.entry == "discover":
        count = re.search(rb"^Ran (\d+) tests? in ", raw, re.MULTILINE)
        tests_run = int(count[1]) if count else None
    suite_ok = tests_run == 4 and raw.rstrip().endswith(b"OK") if args.entry == "discover" else \
        cli_report is not None and tests_run == 4 and cli_report["actual_runner_rc"] == 0 and \
        cli_report["failures"] == cli_report["errors"] == cli_report["skipped"] == 0 and cli_report["source_stable"]
    rc = child.returncode if child.returncode else 0 if before == after and suite_ok and lifecycle["source_stable"] else 1
    report = {"schema": "opensim-copy-discovery-run-v1", "actual_child_rc": child.returncode,
              "actual_runner_rc": rc, "source_before": before, "source_after": after,
              "source_stable": before == after, "seconds": time.monotonic() - started,
              "stderr_sha256": hashlib.sha256(raw).hexdigest(), "argv": command,
              "entry": args.entry, "negative": args.negative, "lifecycle": lifecycle,
              "tests": tests_run, "cli_report": cli_report,
              "copy_outputs": [value.decode() for value in outputs],
              "finished_at": datetime.now(timezone.utc).isoformat(), "certified": False}
    write(out / "report.json", encode(report))
    print("COPY_DISCOVERY_TERMINAL child_rc=" + str(child.returncode) + " runner_rc=" + str(rc) +
          " source_stable=" + str(before == after) + " report=" + str(out / "report.json"))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
