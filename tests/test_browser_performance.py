"""File-fixture and actual Chromium negative tests, with external evidence only.

Run with --real-browser to include four fresh Chromium profiles on a new loopback
fixture server. Fixture pages are not OpenSim/FEM or production certificates.
All subprocess output and the counted unittest result are preserved externally.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/browser_performance.mjs"
EVIDENCE = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-perf")
BASELINE = {
    "id": "opensim-mobile-cold-v1", "width": 360, "height": 800,
    "device_scale_factor": 1, "mobile": True, "touch": True,
    "latency_ms": 150, "download_bytes_s": 200000, "upload_bytes_s": 93750,
    "cpu_rate": 4, "observation_ms": 8000, "cache_disabled": True,
    "service_worker_bypassed": True, "samples": 1, "timeout_ms": 30000,
    "retries": 0,
}
SOURCES = ["tools/render_page.py", "web/app.js", "web/app.css", "solver.py", "server.py"]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def sample_report():
    """An explicitly synthetic parser ruler; not a browser or solver proof."""
    sources = {name: hashlib.sha256(name.encode()).hexdigest() for name in SOURCES}
    target = "http://127.0.0.1:12345/"
    resources = []
    for kind, ext in [("Document", "html"), ("Script", "js"), ("Stylesheet", "css")]:
        digest = sources.get(f"web/app.{ext}", "a" * 64)
        resources.append({
            "url": target if kind == "Document" else f"{target}web/app.{digest}.{ext}",
            "type": kind, "transfer_bytes": 1000, "decoded_body_bytes": 3000,
            "finished": True, "cached": False, "status": 200,
            "mime": {"html": "text/html", "js": "text/javascript", "css": "text/css"}[ext],
            "encoding": "gzip", "fingerprinted": kind != "Document",
            "fingerprint_sha256": digest if kind != "Document" else None,
            "body_sha256": digest,
            "cache_control": "no-store" if kind == "Document" else "public, max-age=31536000, immutable",
            "etag": digest, "last_modified": "",
        })
    interactions = []
    for index, selector in enumerate(["#view-mesh", "#view-field", "#solve-button"]):
        start = 8100 + index * 100
        interactions.append({
            "selector": selector, "click_count": 1, "latency_ms": 40,
            "paints": [{"type": "click", "trusted": True, "target": selector[1:],
                        "start_ms": start, "end_ms": start + 40, "bound_ms": 40}],
            "event_timing": [{"duration": 40}],
        })
    sample = {
        "phase": "ONE_COLD_NAVIGATION_PLUS_THREE_CORE_TOUCHES", "target_url": target,
        "metrics": {"lcp_ms": 1000, "lcp_count": 1, "cls": 0, "cls_observed": True,
                    "interaction_latency_ms": 40, "interaction_count": 3,
                    "metric_name": "EVENT_TO_PAINT_UPPER_BOUND_NOT_INP", "inp_ms": None},
        "raw_metrics": {"lcp": [{"startTime": 1000}], "shifts": [],
                        "supported": ["layout-shift", "largest-contentful-paint", "event"],
                        "observer_errors": []},
        "interactions": interactions, "resources": resources,
        "content": {"ready": True, "text_length": 500, "canvas_colors": 10,
                    "solve_count": 2, "nodes": 100, "triangles": 150, "max_velocity": 0.1},
        "fonts": {"ready": True, "errors": [], "preloads": 0, "faces": [],
                  "fallback_verified": True},
        "images": [], "third_party": [], "redirects": [], "network_errors": [],
        "runtime_errors": [], "isolation": "BLOCK_ALL_OFF_ORIGIN_BEFORE_REQUEST",
        "source_identity": {"bound": True, "local_source_sha256": sources,
                            "committed_source_sha256": sources,
                            "health": {"source_sha256": sources, "page_build": "b" * 12},
                            "solve_source_sha256": sources, "assets_bound": True,
                            "page_build_seen": True, "document_sha256": "a" * 64},
    }
    return {"schema_version": 1, "tool": "opensim-browser-performance", "standard": "shared-r41",
            "gate": "G5-performance", "baseline": BASELINE, "baseline_commands_acknowledged": True,
            "browser": {"product": "Chrome/140.0.7339.16"}, "samples": [sample],
            "commit": "a" * 40, "tool_sha256": "b" * 64, "target_url": target,
            "scope": "LOCAL_PREFLIGHT", "g5_certified": False, "release_verdict": "NOT_CERTIFIED",
            "fixture_only": True}


def set_metric(report, name, value):
    sample = report["samples"][0]
    sample["metrics"][name] = value
    if name == "lcp_ms":
        sample["raw_metrics"]["lcp"][0]["startTime"] = value
    elif name == "cls":
        sample["raw_metrics"]["shifts"] = [
            {"startTime": 1200, "value": value, "hadRecentInput": False}]
    else:
        row = sample["interactions"][0]
        row["latency_ms"] = value
        row["paints"][0]["bound_ms"] = value
        row["paints"][0]["end_ms"] = row["paints"][0]["start_ms"] + value
        row["event_timing"][0]["duration"] = value


def fixture_page(kind):
    """Generate standalone, file-backed browser rulers with defined mutations."""
    action = (
        "const until=performance.now()+350;while(performance.now()<until){};"
        if kind == "interaction" else ""
    )
    delayed = (
        "setTimeout(()=>{document.querySelector('h1').textContent="
        "'Delayed largest content fixture: this is not OpenSim or a solver';},3000);"
        if kind == "lcp" else ""
    )
    shift = (
        "setTimeout(()=>{document.getElementById('spacer').style.height='300px';},1200);"
        if kind == "cls" else ""
    )
    heading = "" if kind == "lcp" else "Real browser fixture only, not OpenSim or FEM"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,"><title>Performance ruler only: {kind}</title>
<style>body{{margin:0;padding:16px;font:16px/1.5 serif}}h1{{font-size:32px}}
button{{min-width:180px;min-height:48px}}canvas{{width:200px;height:100px}}</style>
</head><body><div id="spacer"></div><h1>{heading}</h1>
<p>This page proves that a real Chromium observer detects performance failures.
It does not compute fluid flow and cannot certify a production application.</p>
<button id="solve-button">Real input fixture</button>
<canvas id="flow-canvas" width="200" height="100" aria-label="Fixture drawing"></canvas>
<p id="status">Waiting for actual trusted input.</p>
<script>document.getElementById('solve-button').addEventListener('click',()=>{{
{action}document.getElementById('status').textContent='Actual event handled';
const ctx=document.getElementById('flow-canvas').getContext('2d');
ctx.fillStyle='#095';ctx.fillRect(0,0,200,100);}});{delayed}{shift}</script></body></html>"""


def create_fixtures(directory, real_browser=False):
    directory.mkdir()
    cases = []

    def add(name, expected, rule=None, mutation=None):
        report = sample_report()
        if mutation:
            mutation(report)
        filename = name + ".json"
        write_json(directory / filename, report)
        cases.append({"file": filename, "expected": expected, "rule": rule})

    add("positive-synthetic-ruler", "PASS")
    for name, value in [("lcp_ms", 2501), ("cls", 0.101), ("interaction_latency_ms", 201)]:
        add(name, "FAIL", "WEB-PERF-001", lambda r, n=name, v=value: set_metric(r, n, v))
    add("inclusive-boundaries", "PASS", mutation=lambda r: (
        set_metric(r, "lcp_ms", 2500), set_metric(r, "cls", 0.1),
        set_metric(r, "interaction_latency_ms", 200)))
    add("js-budget", "FAIL", "WEB-PERF-003",
        lambda r: r["samples"][0]["resources"][1].update(transfer_bytes=300001))
    add("critical-budget", "FAIL", "WEB-PERF-002",
        lambda r: r["samples"][0]["resources"][0].update(transfer_bytes=200001))
    add("first-screen-budget", "FAIL", "WEB-PERF-002", lambda r:
        r["samples"][0]["resources"].append(dict(
            r["samples"][0]["resources"][0], type="Other", transfer_bytes=1000001)))
    add("font-budget", "FAIL", "WEB-PERF-004", lambda r:
        r["samples"][0]["resources"].append(dict(
            r["samples"][0]["resources"][1], type="Font", transfer_bytes=150001)))
    add("uncompressed-text", "FAIL", "WEB-PERF-005",
        lambda r: r["samples"][0]["resources"][1].update(encoding=""))
    add("legacy-alias-not-performance-pass", "FAIL", "WEB-PERF-005",
        lambda r: r["samples"][0]["resources"][1].update(
            url="http://127.0.0.1:12345/web/app.js", fingerprinted=False,
            fingerprint_sha256=None, cache_control="public, max-age=0, must-revalidate"))
    add("fingerprint-not-immutable", "FAIL", "WEB-PERF-005",
        lambda r: r["samples"][0]["resources"][1].update(cache_control="public, max-age=31536000"))
    add("wrong-fingerprint", "FAIL", "WEB-PERF-005",
        lambda r: r["samples"][0]["resources"][1].update(fingerprint_sha256="c" * 64))
    add("fingerprint-url-forgery", "FAIL", "WEB-PERF-005",
        lambda r: r["samples"][0]["resources"][1].update(url="http://127.0.0.1:12345/web/app.js"))
    add("third-party-blocked-still-fail", "FAIL", "WEB-PERF-006",
        lambda r: r["samples"][0]["third_party"].append({"url": "https://unexpected.invalid/"}))
    add("redirect", "FAIL", "G5-NETWORK",
        lambda r: r["samples"][0]["redirects"].append({"status": 302}))
    add("offline-network-error", "FAIL", "G5-NETWORK",
        lambda r: r["samples"][0]["network_errors"].append({"error": "net::ERR_INTERNET_DISCONNECTED"}))
    add("favicon-404-preserved", "FAIL", "WEB-PERF-002",
        lambda r: r["samples"][0]["resources"][0].update(status=404))
    add("empty-content", "FAIL", "G5-CONTENT",
        lambda r: r["samples"][0]["content"].update(ready=False, nodes=0))
    add("font-fallback-unproven", "FAIL", "WEB-PERF-004",
        lambda r: r["samples"][0]["fonts"].update(fallback_verified=False))
    add("missing-sample", "ERROR", "REPORT", lambda r: r.update(samples=[]))
    add("altered-cpu", "ERROR", "REPORT", lambda r: r.update(baseline=dict(BASELINE, cpu_rate=1)))
    add("scope-forgery", "ERROR", "REPORT", lambda r: r.update(scope="PUBLIC_LAB"))
    add("raw-summary-forgery", "ERROR", "REPORT",
        lambda r: r["samples"][0]["metrics"].update(lcp_ms=1))
    add("missing-identity", "FAIL", "G5-IDENTITY",
        lambda r: r["samples"][0].update(source_identity=None))
    add("identity-forgery", "FAIL", "G5-IDENTITY", lambda r:
        r["samples"][0]["source_identity"].update(committed_source_sha256={}))
    (directory / "malformed.json").write_text("{", encoding="utf-8")
    cases.append({"file": "malformed.json", "expected": "ERROR"})
    manifest = {"cases": cases}
    if real_browser:
        browser_cases = []
        for name, metric, failure in [
            ("fast", "interaction_latency_ms", False),
            ("lcp", "lcp_ms", True), ("cls", "cls", True),
            ("interaction", "interaction_latency_ms", True),
        ]:
            filename = name + ".html"
            (directory / filename).write_text(fixture_page(name), encoding="utf-8")
            browser_cases.append({"file": filename, "metric": metric, "expected_failure": failure})
        manifest["browser_cases"] = browser_cases
    write_json(directory / "manifest.json", manifest)
    return cases


class PerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        cls.evidence_run = EVIDENCE / ("python-tests-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                              + "-" + uuid4().hex[:8])
        cls.evidence_run.mkdir()
        cls.fixtures = cls.evidence_run / "fixtures"
        cls.cases = create_fixtures(cls.fixtures)
        cls.invocations = []

    @classmethod
    def tearDownClass(cls):
        write_json(cls.evidence_run / "invocations.json", cls.invocations)

    def invoke(self, *args):
        command = ["node", str(TOOL), *args, "--out", str(EVIDENCE)]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=150)
        index = len(self.invocations) + 1
        (self.evidence_run / f"cli-{index:03d}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        self.invocations.append({"command": command, "exit_code": result.returncode,
                                 "log": f"cli-{index:03d}.log"})
        if "report=" not in result.stdout:
            return result, None
        filename = result.stdout.strip().split("report=")[-1]
        return result, json.loads(Path(filename).read_text(encoding="utf-8"))

    def test_file_fixture_selftest_counts_every_case(self):
        result, report = self.invoke("--selftest", "--fixtures", str(self.fixtures))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["verdict"], "SELFTEST_PASS")
        self.assertEqual(report["selftest"]["count"], len(self.cases))
        self.assertEqual(report["selftest"]["browser_count"], 0)
        self.assertFalse(report["g5_certified"])
        self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")

    def test_every_named_fixture_through_real_offline_cli(self):
        for case in self.cases:
            with self.subTest(file=case["file"]):
                result, report = self.invoke("--parse", str(self.fixtures / case["file"]))
                expected = {"PASS": 0, "FAIL": 1, "ERROR": 2}[case["expected"]]
                self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
                self.assertEqual(report["exit_code"], expected)
                self.assertFalse(report["g5_certified"])
                self.assertEqual(report["scope"], "OFFLINE_PARSE_NOT_G5")
                if case.get("rule"):
                    self.assertTrue(
                        report.get("failure", {}).get("rule") == case["rule"] or any(
                            check["rule"] == case["rule"] and check["status"] == "FAIL"
                            for check in report["checks"]), report)

    def test_empty_manifest_and_missing_fixture_fail_loud(self):
        for name, manifest in [("empty", {"cases": []}),
                               ("missing", {"cases": [
                                   {"file": f"absent-{i}.json", "expected": "PASS"}
                                   for i in range(6)]})]:
            directory = self.evidence_run / name
            directory.mkdir()
            write_json(directory / "manifest.json", manifest)
            result, report = self.invoke("--selftest", "--fixtures", str(directory))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(report["verdict"], "FAIL")

    def test_targets_and_baseline_cannot_be_loosened(self):
        for args in [
            ["--url", "https://example.com/"],
            ["--url", "https://opensim.ai4everyone.top.evil.invalid/"],
            ["--url", "https://opensim.ai4everyone.top:444/"],
            ["--url", "http://10.0.0.1/"],
            ["--url", "http://127.0.0.1:12345/", "--cpu-rate", "1"],
            ["--url", "http://127.0.0.1:12345/", "--threshold", "9999"],
            ["--url", "http://127.0.0.1:12345/", "--repeat", "3"],
        ]:
            with self.subTest(args=args):
                result, _ = self.invoke(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("rule=CLI", result.stderr)

    def test_tool_syntax_and_help(self):
        result = subprocess.run(["node", "--check", str(TOOL)], cwd=ROOT, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        result, _ = self.invoke("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("No configurable thresholds/retries", result.stdout)


def run_main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-browser", action="store_true")
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PerformanceTests)
    test_log = io.StringIO()
    result = unittest.TextTestRunner(verbosity=2, stream=test_log).run(suite)
    directory = PerformanceTests.evidence_run
    (directory / "unittest.log").write_text(test_log.getvalue(), encoding="utf-8")
    report = {"collected_at": datetime.now(timezone.utc).isoformat(),
              "tests": result.testsRun, "fixture_cases": len(PerformanceTests.cases),
              "cli_invocations": len(PerformanceTests.invocations),
              "failures": len(result.failures), "errors": len(result.errors),
              "real_browser": "NOT_RUN", "verdict": "PASS" if result.wasSuccessful() else "FAIL"}
    if args.real_browser and result.wasSuccessful():
        fixture_dir = directory / "real-browser-fixtures"
        create_fixtures(fixture_dir, real_browser=True)
        command = ["node", str(TOOL), "--selftest", "--fixtures", str(fixture_dir),
                   "--out", str(EVIDENCE)]
        browser = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=150)
        (directory / "real-browser.log").write_text(browser.stdout + browser.stderr, encoding="utf-8")
        report["real_browser_exit_code"] = browser.returncode
        report["real_browser"] = "PASS" if browser.returncode == 0 else "FAIL"
        if "report=" in browser.stdout:
            filename = browser.stdout.strip().split("report=")[-1]
            report["real_browser_report"] = filename
            actual = json.loads(Path(filename).read_text(encoding="utf-8"))
            report["real_browser_count"] = actual.get("selftest", {}).get("browser_count")
        if browser.returncode:
            report["verdict"] = "FAIL"
    report["collected_at"] = datetime.now(timezone.utc).isoformat()
    write_json(directory / "summary.json", report)
    print(f"PERFORMANCE_TESTS verdict={report['verdict']} tests={report['tests']} "
          f"fixtures={report['fixture_cases']} browser={report['real_browser']} "
          f"report={directory / 'summary.json'}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(run_main())
