"""Run the real UI contract against an owned ephemeral loopback FEM server.

unittest retains immutable external logs/reports. CLI --scan checks only the
three finalizer delta files; --verify-browser rereads full-matrix PNG/solve evidence.
No production, dependency installation, shared process cleanup or credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import unittest
from datetime import datetime, timezone
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import ApplicationServer
from tools.render_page import SOURCE_PATHS

OUT = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-ui")
WRITE_SET = ("web/app.js", "web/app.css", "tools/ui_contract_test.mjs",
             "tests/test_ui_contract.py", "docs/testing/UI_CONTRACT.md")
DELTA_SET = ("tools/ui_contract_test.mjs", "tests/test_ui_contract.py", "docs/testing/UI_CONTRACT.md")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_report(destination, report):
    destination = Path(destination).resolve()
    if OUT.resolve() not in destination.parents or destination.exists():
        raise ValueError("Use a new external seat-ui report")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return destination


def run_contract(destination, app, *options):
    command = ["node", str(ROOT / "tools/ui_contract_test.mjs"),
               "--url", f"http://127.0.0.1:{app.server_port}/",
               "--out", str(destination), *options]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                                     OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1"))
    write_report(destination / "execution.json", {
        "argv": command, "rc": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    })
    return result


class UIContractTests(unittest.TestCase):
    def test_minimal_durable_smoke(self):
        destination = OUT / ("durable-smoke-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                             + "-" + uuid4().hex[:8])
        app = ApplicationServer(("127.0.0.1", 0))
        worker = threading.Thread(target=app.serve_forever, daemon=True)
        worker.start()
        try:
            result = run_contract(destination, app, "--minimal")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            reports = list(destination.glob("run-*/report.json"))
            self.assertEqual(len(reports), 1)
            report = json.loads(reports[0].read_text())
            self.assertEqual((report["verdict"], report["state"], report["exit_code"]),
                             ("PASS", "FINAL", result.returncode))
            self.assertEqual(report["actual_solver_calls"], 3)
            self.assertEqual(report["durability"]["state"], "FINAL_AFTER_FSYNC_AND_INODE_READBACK")
            operations = report["durability"]["operations"]
            for file in ("report.provisional.json", "run.log", "evidence.json"):
                self.assertTrue(any(row["operation"] == "fsync-file" and
                                    Path(row["file"]).name == file for row in operations))
                self.assertTrue(any(row["operation"] == "exact-readback" and
                                    Path(row["file"]).name == file for row in operations))
            self.assertTrue(any(row["operation"] == "fsync-directory" for row in operations))
            self.assertEqual(sha((reports[0].parent / "evidence.json").read_bytes()),
                             report["evidence_sha256"])
        finally:
            app.shutdown()
            app.server_close()
            worker.join(5)
            self.assertFalse(worker.is_alive())

    def test_real_dom_and_fem_contract(self):
        destination = OUT / ("unittest-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                             + "-" + uuid4().hex[:8])
        destination.mkdir(parents=True)
        app = ApplicationServer(("127.0.0.1", 0))
        worker = threading.Thread(target=app.serve_forever, daemon=True)
        worker.start()
        try:
            command = ["node", str(ROOT / "tools/ui_contract_test.mjs"),
                       "--url", f"http://127.0.0.1:{app.server_port}/",
                       "--out", str(destination)]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                                             OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1"))
            write_report(destination / "execution.json", {
                "argv": command, "rc": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr,
                "collected_at": datetime.now(timezone.utc).isoformat(),
            })
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            reports = list(destination.glob("run-*/report.json"))
            self.assertEqual(len(reports), 1)
            report = json.loads(reports[0].read_text())
            self.assertEqual(report["exit_code"], result.returncode)
            self.assertEqual(report["state"], "FINAL")
            self.assertEqual(report["verdict"], "PASS")
            self.assertEqual(report["mode"], "REAL_DOM_CDP_CONTRACT")
            self.assertGreaterEqual(len(report["checks"]), 43)
            self.assertGreaterEqual(report["actual_solver_calls"], 61)
            self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")
            self.assertEqual(list(report["sources"]), list(SOURCE_PATHS))
            self.assertEqual(report["sources"], {
                source: sha((ROOT / source).read_bytes()) for source in SOURCE_PATHS})
            for image in report["screenshots"]:
                self.assertEqual(sha((reports[0].parent / image["file"]).read_bytes()), image["sha256"])
            evidence_bytes = (reports[0].parent / "evidence.json").read_bytes()
            self.assertEqual(sha(evidence_bytes), report["evidence_sha256"])
            evidence = json.loads(evidence_bytes)
            self.assertEqual(evidence["state"], "COMPLETE")
            self.assertEqual(evidence["actual_solver_calls"], report["actual_solver_calls"])
            self.assertEqual(sha((reports[0].parent / "run.log").read_bytes()), report["run_log_sha256"])
            self.assertEqual(evidence["run_log_sha256"], report["run_log_sha256"])
            for file in evidence["files"]:
                self.assertEqual(sha((reports[0].parent / file["file"]).read_bytes()), file["sha256"])
        finally:
            app.shutdown()
            app.server_close()
            worker.join(5)
            self.assertFalse(worker.is_alive(), "Owned HTTP test thread did not stop")

    def test_finalization_fault_simulations_revoke_pass_and_match_process(self):
        destination = OUT / ("finalization-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                             + "-" + uuid4().hex[:8])
        destination.mkdir(parents=True)
        app = ApplicationServer(("127.0.0.1", 0))
        worker = threading.Thread(target=app.serve_forever, daemon=True)
        worker.start()
        rows = []
        try:
            for stage in ("log-write", "log-readback", "evidence-write", "evidence-readback",
                          "final-write", "final-readback"):
                with self.subTest(stage=stage):
                    command = ["node", str(ROOT / "tools/ui_contract_test.mjs"),
                               "--url", f"http://127.0.0.1:{app.server_port}/",
                               "--out", str(destination / stage), "--minimal",
                               "--finalize-fault", stage]
                    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                                                     OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1"))
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    reports = list((destination / stage).glob("run-*/report.json"))
                    self.assertEqual(len(reports), 1)
                    report = json.loads(reports[0].read_text())
                    self.assertEqual(report["exit_code"], result.returncode)
                    self.assertEqual(report["verdict"], "FAIL")
                    self.assertEqual(report["state"], "FINALIZATION_FAILED")
                    self.assertEqual(report["finalization_failure"]["candidate_verdict"], "PASS")
                    self.assertIn(stage, report["finalization_failure"]["message"])
                    self.assertEqual(report["finalization_failure"]["code"], "SIMULATED_EIO")
                    self.assertEqual(report["finalization_failure"]["method"], "FAULT_SIMULATION")
                    self.assertEqual(report["actual_solver_calls"], 3)
                    self.assertIn("verdict=FAIL rc=2", result.stdout)
                    self.assertNotIn("UI_CONTRACT verdict=PASS", result.stdout)
                    for file in reports[0].parent.glob("report*.json"):
                        self.assertNotEqual(json.loads(file.read_text())["verdict"], "PASS")
                    rows.append({"stage": stage, "rc": result.returncode, "report": str(reports[0]),
                                 "report_sha256": sha(reports[0].read_bytes()),
                                 "stdout": result.stdout, "stderr": result.stderr})
            write_report(destination / "execution.json", {
                "verdict": "PASS", "checks": rows, "failures_retained": True,
                "method": "FAULT_SIMULATION_NOT_REAL_IO_ERRNO",
                "collected_at": datetime.now(timezone.utc).isoformat(),
            })
        finally:
            app.shutdown()
            app.server_close()
            worker.join(5)
            self.assertFalse(worker.is_alive(), "Owned fault-test HTTP thread did not stop")

    def test_real_filesystem_errno_and_unknown_inode_protection(self):
        destination = OUT / ("filesystem-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                             + "-" + uuid4().hex[:8])
        app = ApplicationServer(("127.0.0.1", 0))
        worker = threading.Thread(target=app.serve_forever, daemon=True)
        worker.start()
        cases = {
            "directory-evidence": "EEXIST", "dev-full-log": "ENOSPC",
            "closed-fd-file-sync": "EBADF", "closed-fd-directory-sync": "EBADF",
            "same-bytes-provisional-replacement": None, "same-bytes-final-replacement": None,
            "symlink-final-replacement": "ELOOP", "late-evidence-directory": "EISDIR",
        }
        rows = []
        try:
            for name, errno in cases.items():
                with self.subTest(case=name):
                    result = run_contract(destination / name, app, "--minimal", "--finalize-io", name)
                    runs = list((destination / name).glob("run-*"))
                    self.assertEqual(len(runs), 1, result.stdout + result.stderr)
                    run = runs[0]
                    markers = list(run.glob("finalization-failure.json"))
                    self.assertEqual(len(markers), 1, result.stdout + result.stderr)
                    report = json.loads(markers[0].read_text())
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertEqual(report["exit_code"], result.returncode)
                    self.assertEqual((report["verdict"], report["state"]), ("FAIL", "FINALIZATION_FAILED"))
                    failure = report["finalization_failure"]
                    self.assertEqual(failure["candidate_verdict"], "PASS")
                    self.assertEqual(failure["method"], "REAL_FILESYSTEM_OR_READBACK")
                    self.assertEqual(report["actual_solver_calls"], 3)
                    if errno:
                        self.assertEqual(failure["code"], errno, failure)
                        self.assertTrue(failure["syscall"], failure)
                    else:
                        self.assertIn("inode changed", failure["message"])
                    self.assertIn("verdict=FAIL rc=2", result.stdout)
                    self.assertNotIn("UI_CONTRACT verdict=PASS", result.stdout)
                    unknown = run / "unknown-replacement.json"
                    if unknown.exists():
                        saved = json.loads(unknown.read_text())
                        file = Path(saved["file"])
                        stat = file.lstat()
                        self.assertEqual({"dev": str(stat.st_dev), "ino": str(stat.st_ino)},
                                         saved["replacement"])
                        self.assertNotEqual(saved["original"], saved["replacement"])
                        self.assertEqual(sha(file.read_bytes()), saved["sha256"])
                        self.assertEqual(file.is_symlink(), saved["symlink"])
                        if file.name == "report.json":
                            self.assertIn("revocation_failure", report)
                            self.assertIn(str(markers[0]), result.stdout)
                    terminal = run / "report.json"
                    if terminal.exists() and name not in {
                            "same-bytes-final-replacement", "symlink-final-replacement"}:
                        final = json.loads(terminal.read_text())
                        self.assertEqual((final["verdict"], final["exit_code"]), ("FAIL", 2))
                    if name == "late-evidence-directory":
                        self.assertTrue(report["revocation"]["preserved"])
                        self.assertFalse(report["revocation"]["consumable"])
                    rows.append({"case": name, "kernel_errno": failure["code"], "syscall": failure["syscall"],
                                 "process_rc": result.returncode, "report_rc": report["exit_code"],
                                 "report": str(markers[0]), "sha256": sha(markers[0].read_bytes()),
                                 "actual_solver_calls": report["actual_solver_calls"],
                                 "unknown_inode_protected": unknown.exists()})
            write_report(destination / "summary.json", {
                "verdict": "PASS", "scope": "REAL_FILESYSTEM_NEGATIVES_NOT_RELEASE",
                "cases": rows, "collected_at": datetime.now(timezone.utc).isoformat(),
            })
        finally:
            app.shutdown()
            app.server_close()
            worker.join(5)
            self.assertFalse(worker.is_alive(), "Owned filesystem-test HTTP thread did not stop")

    def test_scan_scope_is_exact(self):
        self.assertEqual(set(WRITE_SET), {"web/app.js", "web/app.css",
                         "tools/ui_contract_test.mjs", "tests/test_ui_contract.py",
                         "docs/testing/UI_CONTRACT.md"})
        self.assertEqual(set(DELTA_SET), {"tools/ui_contract_test.mjs",
                         "tests/test_ui_contract.py", "docs/testing/UI_CONTRACT.md"})


def scan(destination):
    secret = re.compile(
        rb"(?i)\b(?:password|passwd|api[_-]?key|secret|authorization)"
        rb"[\"']?\s*[:=]\s*[\"'][^\"']{6,}[\"']")
    private = re.compile(rb"-----BEGIN " + rb"(?:[A-Z]+ )?PRIVATE KEY-----")
    assert secret.search(b"api_" + b'key="fixture-only-not-a-credential"')
    assert not secret.search(b"no credential assignment")
    assert private.search(b"-----BEGIN " + b"PRIVATE KEY-----")
    rows = []
    for name in DELTA_SET:
        content = (ROOT / name).read_bytes()
        rows.append({"path": name, "bytes": len(content), "sha256": sha(content),
                     "credential_shape_hits": len(secret.findall(content)),
                     "private_key_hits": len(private.findall(content))})
    diff = subprocess.run(["git", "diff", "--check", "--", *DELTA_SET], cwd=ROOT,
                          capture_output=True, text=True)
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    outside = [line[3:] for line in status.stdout.splitlines() if line[3:] not in DELTA_SET]
    passed = diff.returncode == 0 and not outside and all(
        row["bytes"] > 0 and not row["credential_shape_hits"] and not row["private_key_hits"]
        for row in rows)
    report = {"verdict": "PASS" if passed else "FAIL", "files": rows,
              "diff_check_rc": diff.returncode, "diff_check_stderr": diff.stderr,
              "outside_write_set": outside, "selftest": "PASS",
              "scope": "credential shapes/private keys; no secret values read or printed",
              "collected_at": datetime.now(timezone.utc).isoformat()}
    output = write_report(destination, report)
    print(f"UI_CHANGE_SCAN verdict={report['verdict']} files={len(rows)} report={output}")
    return 0 if passed else 1


def verify_browser(filename, destination):
    filename = Path(filename).resolve()
    root = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/browser").resolve()
    if root not in filename.parents:
        raise ValueError("Full browser evidence must be inside its fixed root")
    report = json.loads(filename.read_text())
    if (report["verdict"] != "AUTOMATED_PASS" or report["mode"] != "OPENSIM_ACCEPTANCE"
            or report["release_verdict"] != "NOT_CERTIFIED"):
        raise ValueError("Not a complete automated run, or falsely certified")
    if len(report["matrix"]) != 8 or any(row["status"] != "PASS" for row in report["matrix"]):
        raise ValueError("Incomplete 8 viewport matrix")
    breakpoint_rows = [row for row in report["checks"] if row["coordinate"].startswith("breakpoint-")]
    if len(breakpoint_rows) != 12 or any(row["status"] != "PASS" for row in report["checks"]):
        raise ValueError("Incomplete 12 breakpoint checks or failed checks")
    for coordinate in ("keyboard-desktop", "keyboard-mobile"):
        if not any(row["coordinate"] == coordinate for row in report["checks"]):
            raise ValueError(f"Missing {coordinate}")
    for row in report["screenshots"]:
        content = (filename.parent / row["file"]).read_bytes()
        if (len(content) <= 100 or content[:8] != b"\x89PNG\r\n\x1a\n" or
                sha(content) != row["sha256"] or
                int.from_bytes(content[16:20], "big") != row["width"] or
                int.from_bytes(content[20:24], "big") != row["height"]):
            raise ValueError(f"PNG/hash/dimension mismatch: {row['file']}")
    solves = list(filename.parent.glob("solve-*.json"))
    if len(solves) != report["result_count"] or len(solves) < 64:
        raise ValueError("Actual solve artifacts missing")
    expected = {source: sha((ROOT / source).read_bytes()) for source in SOURCE_PATHS}
    for file in solves:
        actual = json.loads(file.read_text())
        stamp = actual["result"]["reproducibility"]
        if actual["status"] != 200 or stamp["source_sha256"] != expected:
            raise ValueError(f"Actual solve source/status mismatch: {file.name}")
    output = write_report(destination, {
        "verdict": "PASS", "mode": report["mode"], "browser_report": str(filename),
        "browser_report_sha256": sha(filename.read_bytes()), "source_sha256": expected,
        "screenshots": len(report["screenshots"]), "actual_solves": len(solves),
        "viewports": 8, "breakpoints": len(breakpoint_rows),
        "release_verdict": "NOT_CERTIFIED", "collected_at": datetime.now(timezone.utc).isoformat(),
    })
    print(f"UI_BROWSER_ARTIFACTS verdict=PASS viewports=8 breakpoints=12 "
          f"actual_solves={len(solves)} png={len(report['screenshots'])} report={output}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan", type=Path)
    parser.add_argument("--verify-browser", type=Path)
    parser.add_argument("--out", type=Path)
    options = parser.parse_args()
    if options.scan:
        sys.exit(scan(options.scan))
    if options.verify_browser and options.out:
        sys.exit(verify_browser(options.verify_browser, options.out))
    parser.error("Use unittest, --scan FILE, or --verify-browser REPORT --out FILE")
