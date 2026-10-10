"""Real Chromium tests and owned ephemeral product server. No handwritten HTML.

sudo -n -u infra python3 -B tests/test_browser_audit.py --product-smoke
Evidence, subprocess output and server logs stay outside the working tree.
This smoke is neither product G3/G6 acceptance nor a screen-reader session.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import ApplicationServer, Handler, RUNTIME_SOURCES
from tools.render_page import render_page

OUTPUT = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-audit")
EVIDENCE = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/local-v8/numerics.json")

FIXTURE_CSS = """
/* Test-only contrast ruler, never served by the product server. */
*,*::before,*::after {
 color:#111 !important; background-color:#fff !important;
 background-image:none !important; border-color:#111 !important;
 outline-color:#111 !important; box-shadow:none !important;
 text-shadow:none !important;
}
.intro p {background-image:linear-gradient(rgb(255,255,255),rgba(245,245,245,0.5)),none !important}
"""
NEGATIVE_CSS = """
#result-summary {color:#eee !important; white-space:nowrap; overflow:hidden; max-height:24px}
#solve-button {border-color:#fff !important; background-color:#fff !important}
*:focus-visible,[tabindex="-1"]:focus {outline:3px solid #eee !important}
#view-field:focus-visible {outline:none !important}
.workspace-grid > .parameter-panel {order:2}
.workspace-grid > .canvas-panel {order:1}
#error {display:none !important}
"""
FIXTURE_JS = """
document.addEventListener('DOMContentLoaded', () => {
 document.body.dataset.auditFixture='true';
});
"""


class FixtureHandler(Handler):
    """Use actual renderer and app/solver; change only test HTTP responses."""

    def do_GET(self):
        if self.path == "/api/health" and self.server.identity_bad:
            self.json_response(200, {
                "status": "ok", "schema_version": 1,
                "source_sha256": dict(RUNTIME_SOURCES, **{"solver.py": "0" * 64}),
                "page_build": "bad-identity-fixture",
            })
        elif self.path in ("/", "/index-v1.html"):
            html = render_page(evidence=self.server.evidence)
            self.respond(200, html.encode(), "text/html; charset=utf-8")
        elif self.path == "/web/app.css":
            css = (ROOT / "web/app.css").read_text() + FIXTURE_CSS
            if self.server.negative:
                css += NEGATIVE_CSS
            self.respond(200, css.encode(), "text/css; charset=utf-8")
        elif self.path == "/web/app.js":
            script = (ROOT / "web/app.js").read_text() + FIXTURE_JS
            self.respond(200, script.encode(), "application/javascript; charset=utf-8")
        else:
            super().do_GET()


def run_browser(evidence: Path = EVIDENCE, *, smoke=True, fixture=False, negative=False, identity_bad=False) -> dict:
    """Start only this worktree's server, attest real evidence, then stop it."""
    evidence = evidence.resolve(strict=True)
    if not evidence.is_relative_to(Path("/hf3fs-jd/data/tmp/opensim-fluid-v1").resolve()):
        raise ValueError("Numerical evidence must be on the authorized external mount")
    numerical = json.loads(evidence.read_text())
    if numerical["solver"]["sha256"] != RUNTIME_SOURCES["solver.py"]:
        raise ValueError("Numerical evidence does not match this worktree solver")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    mode = "fixture-identity-negative" if identity_bad else "fixture-negative" if negative else "fixture-positive" if fixture else "product-smoke" if smoke else "product-audit"
    run = OUTPUT / f"{mode}-{time.time_ns()}"
    logs = run / "logs"
    logs.mkdir(parents=True)
    handler = logging.FileHandler(logs / "server.log")
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    app = ApplicationServer(("127.0.0.1", 0), evidence=evidence)
    if fixture:
        app.RequestHandlerClass = FixtureHandler
        app.negative = negative
        app.identity_bad = identity_bad
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{app.server_port}/"
    summary = {
        "scope": "HARNESS_FIXTURE_NOT_PRODUCT" if fixture else "PRODUCT_AUTOMATIC_SCOPE_NOT_RELEASE",
        "mode": mode,
        "source_root": str(ROOT), "source_sha256": RUNTIME_SOURCES,
        "evidence_source": str(evidence),
        "evidence_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
        "url": url, "started_at": time.time(), "verdict": "RUNNING",
    }
    try:
        with urlopen(url + "api/health", timeout=10) as response:
            health = json.load(response)
        if not identity_bad and health.get("source_sha256") != RUNTIME_SOURCES:
            raise AssertionError("Ephemeral server identity is not this worktree's source")
        summary["observed_health"] = health
        command = ["node", str(ROOT / "tools/browser_audit.mjs"), "--url", url, "--out", str(run)]
        if smoke:
            command.append("--smoke")
        if fixture:
            command.append("--fixture")
        summary["command"] = command
        with (logs / "browser.stdout.log").open("w") as stdout, (logs / "browser.stderr.log").open("w") as stderr:
            process = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr, timeout=600)
        summary["exit_code"] = process.returncode
        reports = list(run.glob("run-*/report.json"))
        if len(reports) != 1:
            raise AssertionError(f"Expected exactly one report, found {reports}")
        report = json.loads(reports[0].read_text())
        summary["browser_report"] = str(reports[0])
        expected = {2} if identity_bad else {1} if negative else {0, 1} if not fixture and not smoke else {0}
        if process.returncode not in expected:
            raise AssertionError(f"Browser failed: expected rc={expected}, actual={process.returncode} report={reports[0]}")
        if identity_bad:
            if "Served health source drift" not in report["first_failure"]["message"]:
                raise AssertionError("False served source identity was not rejected")
            summary["verdict"] = "EXPECTED_IDENTITY_NEGATIVE_PASS"
            return summary
        if not report.get("source_sha256") or len(report.get("identity_checks", [])) != 2:
            raise AssertionError("Missing actual before/after served identity checks")
        if not report.get("post_source_checks") or report["source_sha256"] != RUNTIME_SOURCES:
            raise AssertionError("Missing or mismatched real POST source evidence")
        if report["identity_checks"][0]["served_byte_sha256"] != report["identity_checks"][1]["served_byte_sha256"]:
            raise AssertionError("Served bytes changed between identity checks")
        if negative:
            failures = {item["rule"] for item in report["checks"] if item["status"] == "FAIL"}
            required = {"WEB-A11Y-001", "WEB-A11Y-002", "WEB-A11Y-004", "WEB-A11Y-005",
                        "WEB-INPUT-004", "WEB-TYPE-006", "G6-content"}
            if not required <= failures or report["mode"] != "HARNESS_FIXTURE_NOT_PRODUCT":
                raise AssertionError(f"Negative fixture missed rules: {required - failures}")
            if not any(item["rule"] == "WEB-A11Y-002" and "focus contrast" in item["message"]
                       for item in report["checks"] if item["status"] == "FAIL"):
                raise AssertionError("Negative fixture did not catch actual low-contrast focus")
        elif not smoke:
            if len(report["matrix"]) != 4 or len(report["injections"]) != 16:
                raise AssertionError("Missing actual viewports or fault injections")
            if fixture and report["findings"]["failure_count"] != 0:
                raise AssertionError("Positive fixture has audit failures")
            if fixture and not negative and not report.get("contrast_totals", {}).get("gradient_samples"):
                raise AssertionError("Positive fixture did not inspect actual gradient-backed text")
        shot = report["screenshots"][0]
        data = (reports[0].parent / shot["file"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != shot["sha256"] or shot["width"] != 320:
            raise AssertionError("Actual screenshot content/dimensions disagree")
        if not smoke:
            modes = {item["mode"] for item in report["injections"]}
            if modes != {"hold", "error", "empty", "long"}:
                raise AssertionError(f"Actual injected modes missing: {modes}")
            if not report["focus_samples"] or not report["hover_samples"]:
                raise AssertionError("No real focus/hover observations")
            widths = {item["width"] for item in report["screenshots"]}
            if widths != {320, 768, 1064, 1440}:
                raise AssertionError("Actual screenshot widths missing")
            for item in report["screenshots"]:
                payload = (reports[0].parent / item["file"]).read_bytes()
                if hashlib.sha256(payload).hexdigest() != item["sha256"]:
                    raise AssertionError(f"Screenshot digest mismatch: {item['file']}")
        summary["verdict"] = "EXPECTED_NEGATIVE_PASS" if negative else "SMOKE_PASS" if smoke else (
            "PRODUCT_AUDIT_FAIL" if process.returncode else "AUTOMATED_SCOPE_PASS")
        summary["first_failure"] = report.get("first_failure")
        summary["findings"] = report.get("findings")
        summary["coverage_counts"] = report.get("coverage_counts")
    except BaseException as error:
        summary["verdict"] = "FAIL"
        summary["first_failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        app.shutdown()
        thread.join(timeout=10)
        app.server_close()
        logging.getLogger().removeHandler(handler)
        handler.close()
        summary["owned_server_stopped"] = not thread.is_alive()
        summary["finished_at"] = time.time()
        (run / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"BROWSER_TEST mode={mode} verdict={summary['verdict']} summary={run / 'summary.json'}", flush=True)
    return summary


class BrowserSmokeTests(unittest.TestCase):
    def test_false_served_health_identity_rejected(self):
        summary = run_browser(fixture=True, identity_bad=True)
        self.assertEqual(summary["verdict"], "EXPECTED_IDENTITY_NEGATIVE_PASS")
        self.assertTrue(summary["owned_server_stopped"])

    def test_invalid_url_and_output_fail_loud(self):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        logs = OUTPUT / f"cli-tests-{time.time_ns()}"
        logs.mkdir()
        invalid = [
            ["--url", "https://evil.invalid/", "--out", str(logs)],
            ["--url", "https://opensim.ai4everyone.top.evil.invalid/", "--out", str(logs)],
            ["--url", "https://opensim.ai4everyone.top:444/", "--out", str(logs)],
            ["--url", "http://127.0.0.1/", "--out", str(ROOT)],
            ["--url", "http://127.0.0.1/"],
            ["--url", "http://127.0.0.1/", "--out", str(logs), "--unknown"],
        ]
        for index, arguments in enumerate(invalid):
            with self.subTest(arguments=arguments):
                process = subprocess.run(["node", str(ROOT / "tools/browser_audit.mjs"), *arguments],
                                         cwd=ROOT, capture_output=True, text=True, timeout=15)
                (logs / f"{index}.stdout.log").write_text(process.stdout)
                (logs / f"{index}.stderr.log").write_text(process.stderr)
                self.assertEqual(process.returncode, 2)
                self.assertIn("AUDIT FAIL", process.stderr)

    def test_actual_browser_and_rendered_product(self):
        summary = run_browser()
        self.assertEqual(summary["verdict"], "SMOKE_PASS")
        self.assertTrue(summary["owned_server_stopped"])

    def test_positive_real_browser_audit_fixture(self):
        summary = run_browser(smoke=False, fixture=True)
        self.assertEqual(summary["verdict"], "AUTOMATED_SCOPE_PASS")
        self.assertTrue(summary["owned_server_stopped"])

    def test_negative_real_browser_audit_fixture(self):
        summary = run_browser(smoke=False, fixture=True, negative=True)
        self.assertEqual(summary["verdict"], "EXPECTED_NEGATIVE_PASS")
        self.assertTrue(summary["owned_server_stopped"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-smoke", action="store_true")
    parser.add_argument("--product-audit", action="store_true")
    parser.add_argument("--fixture-positive", action="store_true")
    parser.add_argument("--fixture-negative", action="store_true")
    parser.add_argument("--evidence", type=Path, default=EVIDENCE)
    args = parser.parse_args()
    if sum([args.product_smoke, args.product_audit, args.fixture_positive, args.fixture_negative]) > 1:
        parser.error("Choose exactly one run mode")
    if args.product_smoke or args.product_audit or args.fixture_positive or args.fixture_negative:
        result = run_browser(args.evidence, smoke=args.product_smoke,
                             fixture=args.fixture_positive or args.fixture_negative, negative=args.fixture_negative)
        if result["verdict"] == "PRODUCT_AUDIT_FAIL":
            sys.exit(1)
    else:
        unittest.main(argv=[sys.argv[0]], verbosity=2)
