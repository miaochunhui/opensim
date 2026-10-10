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
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/browser_performance.mjs"
EVIDENCE = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-perf")
REVIEW_EVIDENCE = EVIDENCE / "review-v1-20261010"
BASELINE = {
    "id": "opensim-mobile-cold-v1", "width": 360, "height": 800,
    "device_scale_factor": 1, "mobile": True, "touch": True,
    "latency_ms": 150, "download_bytes_s": 200000, "upload_bytes_s": 93750,
    "cpu_rate": 4, "observation_ms": 8000, "cache_disabled": True,
    "service_worker_bypassed": True, "samples": 1, "timeout_ms": 30000,
    "retries": 0,
}
SOURCES = ["tools/render_page.py", "web/app.js", "web/app.css", "solver.py", "server.py"]
CONFIG = {"geometry": "circle", "width_mm": 4, "height_mm": 4, "length_mm": 100,
          "pressure_pa": 1, "viscosity_pa_s": 0.001, "density_kg_m3": 998, "resolution": 16}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def sample_report():
    """An explicitly synthetic parser ruler; not a browser or solver proof."""
    sources = {name: hashlib.sha256(name.encode()).hexdigest() for name in SOURCES}
    target = "http://127.0.0.1:12345/"
    page = "<p>Explicit synthetic page</p><code>" + "b" * 12 + "</code>"
    page_digest = hashlib.sha256(page.encode()).hexdigest()
    resources = []
    for kind, ext in [("Document", "html"), ("Script", "js"), ("Stylesheet", "css")]:
        digest = sources.get(f"web/app.{ext}", page_digest)
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
                        "observer_errors": [], "cold_boundary": {
                            "cutoff_ms": 8000, "ready": True, "ready_at_ms": 900}},
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
                            "page_build_seen": True, "document_sha256": page_digest,
                            "document_body": page},
    }
    identity = sample["source_identity"]
    nodes = [[index / 100, 0] for index in range(100)]
    solve = {
        "schema_version": 1, "config": CONFIG,
        "mesh": {"nodes_mm": nodes, "triangles": [[0, i, i + 1] for i in range(1, 99)]},
        "fields": {"velocity_m_s": [0.1] * len(nodes)},
        "metrics": {"max_velocity_m_s": 0.1},
        "reproducibility": {"source_sha256": dict(sources), "solver_sha256": sources["solver.py"],
                            "config_sha256": "a" * 64, "computed_at": "2026-10-10T08:00:00Z"},
    }
    identity["response_resources"] = []
    for key, result, pathname, method in [
        ("solve_responses", solve, "/api/solve", "POST"),
        ("health_responses", identity["health"] | {"status": "ok", "schema_version": 1},
         "/api/health", "GET"),
    ]:
        identity[key] = []
        for index in range(2):
            text = json.dumps(result)
            digest = hashlib.sha256(text.encode()).hexdigest()
            request_id = f"{key}-{index}"
            identity[key].append({"result": json.loads(text), "response_body": text,
                                  "response_sha256": digest, "request_id": request_id})
            identity["response_resources"].append({
                "request_id": request_id, "url": target.rstrip("/") + pathname, "method": method,
                "status": 200, "finished": True, "body_sha256": digest})
    snapshot = {"commit": "a" * 40, "tool_sha256": "b" * 64,
                "committed_tool_sha256": "b" * 64, "page_build": "b" * 12,
                "source_sha256": sources, "committed_source_sha256": sources}
    report = {"schema_version": 1, "tool": "opensim-browser-performance", "standard": "shared-r41",
            "gate": "G5-performance", "baseline": BASELINE, "baseline_commands_acknowledged": True,
            "browser": {"product": "Chrome/140.0.7339.16"}, "samples": [sample],
            "commit": "a" * 40, "tool_sha256": "b" * 64, "target_url": target,
            "scope": "LOCAL_PREFLIGHT", "g5_certified": False, "release_verdict": "NOT_CERTIFIED",
            "fixture_only": True}
    report["identity_before"] = json.loads(json.dumps(snapshot))
    report["identity_after"] = json.loads(json.dumps(snapshot))
    return report


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
    add("first-solve-identity-mismatch", "FAIL", "G5-IDENTITY", lambda r:
        r["samples"][0]["source_identity"]["solve_responses"][0]["result"]
        ["reproducibility"]["source_sha256"].update({"solver.py": "c" * 64}))
    add("commit-drift", "FAIL", "G5-IDENTITY", lambda r:
        r["identity_after"].update(commit="c" * 40))
    add("loaded-tool-not-committed", "FAIL", "G5-IDENTITY", lambda r:
        r["identity_before"].update(committed_tool_sha256="c" * 64))
    add("tool-drift", "FAIL", "G5-IDENTITY", lambda r:
        r["identity_after"].update(tool_sha256="c" * 64))
    add("source-drift", "FAIL", "G5-IDENTITY", lambda r:
        r["identity_after"]["source_sha256"].update({"solver.py": "c" * 64}))
    add("first-health-identity-mismatch", "FAIL", "G5-IDENTITY", lambda r:
        r["samples"][0]["source_identity"]["health_responses"][0]["result"]
        ["source_sha256"].update({"solver.py": "c" * 64}))
    add("missing-first-solve", "FAIL", "G5-IDENTITY", lambda r:
        r["samples"][0]["source_identity"]["solve_responses"].pop(0))
    add("empty-first-solve-velocity", "FAIL", "G5-IDENTITY", lambda r:
        r["samples"][0]["source_identity"]["solve_responses"][0]["result"]["fields"]
        .update(velocity_m_s=[]))
    add("fake-page-build-substring", "FAIL", "G5-IDENTITY", lambda r:
        r["samples"][0]["source_identity"].update(document_body="b" * 12))
    add("original-error-cannot-parse-green", "FAIL", "EVIDENCE", lambda r:
        r.update(failure={"rule": "HARNESS", "message": "Original retained failure"}))
    add("cleanup-error-cannot-parse-green", "FAIL", "EVIDENCE", lambda r:
        r.update(cleanup_failure={"message": "Chrome evidence persistence failed"}))
    add("missed-fixed-boundary", "FAIL", "G5-CONTENT", lambda r:
        r["samples"][0]["raw_metrics"]["cold_boundary"].update(ready=False, ready_at_ms=8500))
    add("lcp-after-observation-cutoff", "ERROR", "REPORT", lambda r: set_metric(r, "lcp_ms", 9000))
    add("inp-claim", "ERROR", "REPORT", lambda r:
        r["samples"][0]["metrics"].update(inp_ms=40))
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
        REVIEW_EVIDENCE.mkdir(parents=True, exist_ok=True)
        cls.evidence_run = REVIEW_EVIDENCE / ("python-tests-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                              + "-" + uuid4().hex[:8])
        cls.evidence_run.mkdir()
        cls.fixtures = cls.evidence_run / "fixtures"
        cls.cases = create_fixtures(cls.fixtures)
        cls.invocations = []

    @classmethod
    def tearDownClass(cls):
        write_json(cls.evidence_run / "invocations.json", cls.invocations)

    def invoke(self, *args):
        command = ["node", str(TOOL), *args, "--out", str(self.evidence_run / "cli")]
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

    def test_late_log_failure_cannot_leave_green_report(self):
        """An actual filesystem collision after START exercises CLI finalization."""
        for fixture, collision in [
            ("positive-synthetic-ruler.json", "run.log"),
            ("lcp_ms.json", "run.log"),
            ("positive-synthetic-ruler.json", "report-candidate.json"),
        ]:
            command = ["node", str(TOOL), "--parse", str(self.fixtures / fixture),
                       "--out", str(self.evidence_run / "late-write")]
            process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            try:
                first = process.stdout.readline()
                self.assertIn(" run=", first)
                run = Path(first.strip().split(" run=")[-1])
                (run / collision).mkdir()
                stdout, stderr = process.communicate(timeout=30)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            log = first + stdout + stderr
            log_name = f"late-write-{collision}-{fixture}.log"
            (self.evidence_run / log_name).write_text(log, encoding="utf-8")
            self.invocations.append({"command": command, "exit_code": process.returncode,
                                     "log": log_name, "negative": collision})
            report = json.loads((run / "report.json").read_text(encoding="utf-8"))
            self.assertNotEqual(process.returncode, 0, log)
            self.assertEqual(report["verdict"], "FAIL", report)
            self.assertEqual(report["exit_code"], 2, report)
            self.assertFalse(report["g5_certified"])
            self.assertIn("persistence_failure", report)
            expected_rule = "EVIDENCE" if fixture.startswith("positive") else "WEB-PERF-001"
            self.assertEqual(report["failure"]["rule"], expected_rule, report)


def verify_product_artifacts(report, directory):
    """Independently read the actual files, not the tool's terminal claim."""
    if report["verdict"] != "LOCAL_PREFLIGHT_PASS" or report["exit_code"] != 0:
        raise ValueError(f"Preflight failed: {report.get('failure')}")
    if report["g5_certified"] or report["release_verdict"] != "NOT_CERTIFIED":
        raise ValueError("Loopback evidence incorrectly claims certification")
    before, after = report["identity_before"], report["identity_after"]
    if before != after or report["commit"] != before["commit"]:
        raise ValueError("Source/commit/tool changed during preflight")
    committed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.strip()
    if report["commit"] != committed or report["tool_sha256"] != hashlib.sha256(TOOL.read_bytes()).hexdigest():
        raise ValueError("Preflight does not belong to current commit/tool")
    if not report["checks"] or any(row["status"] != "PASS" for row in report["checks"]):
        raise ValueError("Preflight has missing or failed checks")
    sample = report["samples"][0]
    identity = sample["source_identity"]
    png = (directory / sample["screenshot"]["file"]).read_bytes()
    if (not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) != sample["screenshot"]["bytes"]
            or hashlib.sha256(png).hexdigest() != sample["screenshot"]["sha256"]):
        raise ValueError("Actual PNG missing or inconsistent")
    counts = {}
    for kind in ("health", "solve"):
        responses = identity[kind + "_responses"]
        if len(responses) != 2:
            raise ValueError(f"Expected exactly two full {kind} responses")
        for index, row in enumerate(responses, 1):
            raw = (directory / f"{kind}-{index}.json").read_bytes()
            if json.loads(raw) != row["result"]:
                raise ValueError(f"Actual {kind} file differs from browser response")
            if kind == "health" and hashlib.sha256(raw).hexdigest() != row["response_sha256"]:
                raise ValueError("Health raw response digest differs")
            if kind == "solve":
                result = row["result"]
                if len(result["fields"]["velocity_m_s"]) != len(result["mesh"]["nodes_mm"]):
                    raise ValueError("Full FEM field does not cover mesh nodes")
        counts[kind] = len(responses)
    response_files = list(directory.glob("response-*.txt"))
    expected_digests = {identity["document_sha256"],
                        before["source_sha256"]["web/app.js"], before["source_sha256"]["web/app.css"]}
    actual_digests = {hashlib.sha256(file.read_bytes()).hexdigest() for file in response_files}
    if actual_digests != expected_digests or len(response_files) != 3:
        raise ValueError("Page/assets raw source files do not match full identity")
    return {"checks": len(report["checks"]), "solves": counts["solve"], "health": counts["health"],
            "page_assets": len(response_files), "screenshots": 1, "commit": committed,
            "tool_sha256": report["tool_sha256"], "metrics": sample["metrics"],
            "critical_bytes": sum(row["transfer_bytes"] for row in sample["resources"]
                                  if row["type"] in ("Document", "Stylesheet", "Script")),
            "initial_bytes": sum(row["transfer_bytes"] for row in sample["resources"])}


def owned_preflight(directory):
    """Own a new loopback process; never attach to or stop another seat's service."""
    log_dir = directory / "server-logs"
    log_dir.mkdir()
    console = directory / "server-console.log"
    command = ["python3", "-B", str(ROOT / "server.py"), "--host", "127.0.0.1", "--port", "0",
               "--log-dir", str(log_dir)]
    result = {"status": "RUNNING", "production_changes": 0, "command": command, "browser_runs": []}
    try:
        with console.open("w", encoding="utf-8") as log:
            server = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 30
                port = None
                while time.monotonic() < deadline:
                    if server.poll() is not None:
                        raise ValueError(f"Owned server exited: {server.returncode}; {console}")
                    text = console.read_text(encoding="utf-8")
                    for line in text.splitlines():
                        if "OPENSIM_SERVER_READY host=127.0.0.1 port=" in line:
                            port = int(line.split(" port=")[1].split()[0])
                    if port:
                        break
                    time.sleep(0.05)
                if not port:
                    raise ValueError(f"Owned server readiness absent; {console}")
                result.update({"server_pid": server.pid, "url": f"http://127.0.0.1:{port}/"})
                for name, negative in [("late-browser-evidence", True), ("product", False)]:
                    browser_command = ["node", str(TOOL), "--url", result["url"],
                                       "--out", str(directory / name)]
                    process = subprocess.Popen(browser_command, cwd=ROOT, stdout=subprocess.PIPE,
                                               stderr=subprocess.PIPE, text=True)
                    try:
                        first = process.stdout.readline()
                        if " run=" not in first:
                            raise ValueError("Browser failed before evidence directory allocation")
                        run = Path(first.strip().split(" run=")[-1])
                        if negative:
                            (run / "chromium.log").mkdir()
                        stdout, stderr = process.communicate(timeout=90)
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait()
                    (directory / f"{name}.log").write_text(first + stdout + stderr, encoding="utf-8")
                    report = json.loads((run / "report.json").read_text(encoding="utf-8"))
                    result["browser_runs"].append({"command": browser_command, "exit_code": process.returncode,
                                                   "report": str(run / "report.json")})
                    if negative:
                        if (process.returncode != 2 or report["verdict"] != "FAIL"
                                or report["g5_certified"] or "cleanup_failure" not in report
                                or not report.get("failure")):
                            raise ValueError(f"Late browser evidence failure left a false terminal: {run}")
                        result["late_browser_negative"] = {
                            "status": "PASS", "report": str(run / "report.json"),
                            "process_exit_code": process.returncode, "failure": report["failure"]}
                    else:
                        result["actual"] = verify_product_artifacts(report, run)
                        result["report"] = str(run / "report.json")
                        result["status"] = "LOCAL_PREFLIGHT_PASS"
            finally:
                if server.poll() is None:
                    server.terminate()
                    server.wait(timeout=10)
                result["owned_server_stopped"] = server.poll() is not None
                result["owned_server_exit_code"] = server.returncode
    except Exception as error:
        result["status"] = "FAIL"
        result["failure"] = {"type": type(error).__name__, "message": str(error)}
    write_json(directory / "preflight.json", result)
    return result


def run_main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-browser", action="store_true")
    parser.add_argument("--preflight", action="store_true",
                        help="Start/stop an owned new loopback server and verify actual product evidence")
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
                   "--out", str(directory / "real-browser")]
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
    if args.preflight and report["verdict"] == "PASS":
        report["preflight"] = owned_preflight(directory)
        if report["preflight"]["status"] != "LOCAL_PREFLIGHT_PASS":
            report["verdict"] = "FAIL"
    report["cli_invocations"] = len(PerformanceTests.invocations)
    report["all_cli_invocations"] = (report["cli_invocations"] +
                                     int("real_browser_exit_code" in report) +
                                     len(report.get("preflight", {}).get("browser_runs", [])))
    report["collected_at"] = datetime.now(timezone.utc).isoformat()
    write_json(directory / "summary.json", report)
    print(f"PERFORMANCE_TESTS verdict={report['verdict']} tests={report['tests']} "
          f"fixtures={report['fixture_cases']} browser={report['real_browser']} "
          f"report={directory / 'summary.json'}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(run_main())
