"""Real browser_check selftest finalization and actual filesystem fault tests.

Run: sudo -n -u infra python3 -B tests/test_browser_finalize.py
Only browser machinery, not fullbrowser/product/FEM/release acceptance.
Each subprocess uses its owned Chromium and loopback selftest server.
"""

import hashlib
import json
from pathlib import Path
import subprocess
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/browser")
FAULTS = ("log-directory", "log-enospc", "log-fsync", "post-publish-fsync",
          "same-inode", "replaced-directory")


class BrowserFinalizationTests(unittest.TestCase):
    case_summaries = []

    def run_case(self, fault=None, prior=False):
        folder = OUTPUT / f"finalize-test-{time.time_ns()}"
        folder.mkdir(parents=True)
        command = ["node", str(ROOT / "tools/browser_check.mjs"), "--selftest", "--out", str(folder)]
        if fault:
            command.extend(["--finalize-fixture", fault])
        if prior:
            command.extend(["--chrome", str(folder / "missing-chromium")])
        with (folder / "stdout.log").open("w") as out, (folder / "stderr.log").open("w") as err:
            process = subprocess.run(command, cwd=ROOT, stdout=out, stderr=err, timeout=180)
        runs = list(folder.glob("run-*"))
        self.assertEqual(len(runs), 1)
        run = runs[0]
        report_path = run / ("report.failure.json" if fault else "report.json")
        report = json.loads(report_path.read_text())
        summary = {"scope": "REAL_BROWSER_SELFTEST_NOT_PRODUCT", "command": command,
                   "report": str(report_path), "exit_code": process.returncode,
                   "tool_sha256": hashlib.sha256((ROOT / "tools/browser_check.mjs").read_bytes()).hexdigest(),
                   "fault": fault, "started_browser": bool(report.get("browser")),
                   "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                   "checks": len(report["checks"]), "screenshots": len(report["screenshots"]),
                   "browser": report.get("browser"),
                   "verdict": "RUNNING"}
        try:
            self.assertEqual(report["schema_version"], 1)
            self.assertEqual(report["mode"], "HARNESS_SELFTEST_NOT_OPENSIM")
            self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")
            provisional = json.loads((run / "report.provisional.json").read_text())
            self.assertEqual(provisional["verdict"], "PROVISIONAL_NOT_FINAL")
            self.assertEqual(provisional["exit_code"], 2)
            if not prior:
                self.assertEqual(len(report["checks"]), 10)
                self.assertTrue(all(row["status"] == "PASS" for row in report["checks"]))
                self.assertGreater(len(report["screenshots"]), 0)
                for shot in report["screenshots"]:
                    self.assertEqual(hashlib.sha256((run / shot["file"]).read_bytes()).hexdigest(), shot["sha256"])
                self.assertTrue(report.get("fixture_url", "").startswith("http://127.0.0.1:"))
            if fault:
                self.assertEqual(process.returncode, 2)
                self.assertEqual(report["exit_code"], 2)
                self.assertEqual(report["verdict"], "FAIL")
                self.assertEqual(report["durability"]["state"], "FAILED_NOT_CONSUMABLE")
                cause = report["persistence_failure"]
                expected = {"log-directory": "EISDIR", "log-enospc": "ENOSPC", "log-fsync": "EINVAL",
                            "post-publish-fsync": "EINVAL", "same-inode": "ERR_ASSERTION",
                            "replaced-directory": "ERR_ASSERTION"}[fault]
                self.assertEqual(cause["code"], expected)
                self.assertIn("BROWSER_CHECK FAIL stage=", (folder / "stderr.log").read_text())
                if prior:
                    self.assertIn("ENOENT", report["first_failure"]["message"])
                else:
                    self.assertEqual(report["first_failure"], cause)
                if fault == "same-inode":
                    proof = report["same_inode_fixture"]
                    self.assertEqual(proof["before_ino"], proof["after_ino"])
                    self.assertEqual(hashlib.sha256((run / "report.json").read_bytes()).hexdigest(), proof["preserved_sha256"])
                    self.assertEqual(report["durability"]["revocations"][0]["status"], "UNKNOWN_OR_CHANGED_NOT_REMOVED")
                elif fault == "replaced-directory":
                    self.assertEqual((run / "report.json/unrelated.txt").read_text(), "unknown bytes preserved\n")
                    self.assertEqual(report["durability"]["revocations"][0]["status"], "UNKNOWN_OR_CHANGED_NOT_REMOVED")
                else:
                    self.assertFalse((run / "report.json").exists())
                    if fault == "post-publish-fsync":
                        revoke = report["durability"]["revocations"][0]
                        self.assertEqual(revoke["status"], "EXACT_OWNED_REVOKED")
                        self.assertEqual(hashlib.sha256(Path(revoke["preserved"]).read_bytes()).hexdigest(), revoke["sha256"])
                summary["persistence_failure"] = cause
                summary["revocations"] = report["durability"]["revocations"]
                summary["first_failure"] = report["first_failure"]
            else:
                self.assertEqual(process.returncode, 0)
                self.assertEqual(report["verdict"], "SELFTEST_PASS")
                self.assertEqual(report["durability"]["state"], "FINAL_AFTER_DURABLE_LOG")
                log = (run / "run.log").read_bytes()
                self.assertEqual(hashlib.sha256(log).hexdigest(), report["durability"]["log"]["sha256"])
                self.assertEqual(len(log), report["durability"]["log"]["bytes"])
            summary["verdict"] = "PASS"
        except BaseException as error:
            summary["verdict"] = "FAIL"
            summary["failure"] = {"type": type(error).__name__, "message": str(error)}
            raise
        finally:
            (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            self.case_summaries.append({"summary": str(folder / "summary.json"),
                                        "verdict": summary["verdict"], "fault": fault})
            print("BROWSER_FINALIZE_TEST verdict={} fault={} summary={}".format(summary["verdict"], fault, folder / "summary.json"), flush=True)

    def test_real_chromium_selftest_positive(self):
        self.run_case()

    def test_actual_io_failures_and_unknown_bytes(self):
        for fault in FAULTS:
            with self.subTest(fault=fault):
                self.run_case(fault)

    def test_browser_first_cause_survives_later_io_fault(self):
        self.run_case("post-publish-fsync", prior=True)


if __name__ == "__main__":
    output = OUTPUT / f"finalize-suite-{time.time_ns()}"
    output.mkdir(parents=True)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(BrowserFinalizationTests)
    with (output / "unittest.log").open("w") as log:
        outcome = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    summary = {"verdict": "PASS" if outcome.wasSuccessful() else "FAIL", "tests": outcome.testsRun,
               "failures": len(outcome.failures), "errors": len(outcome.errors), "finished_at": time.time(),
               "scope": "REAL_BROWSER_SELFTEST_NOT_PRODUCT", "cases": BrowserFinalizationTests.case_summaries,
               "case_count": len(BrowserFinalizationTests.case_summaries),
               "tool_sha256": hashlib.sha256((ROOT / "tools/browser_check.mjs").read_bytes()).hexdigest()}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("BROWSER_FINALIZE_SUITE verdict={} tests={} summary={}".format(summary["verdict"], outcome.testsRun, output / "summary.json"), flush=True)
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
