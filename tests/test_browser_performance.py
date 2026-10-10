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
import math
import os
import re
import stat
import struct
import subprocess
import sys
import time
import unittest
import zlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/browser_performance.mjs"
EVIDENCE = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-perf")
REVIEW_EVIDENCE = EVIDENCE / "review-v1-20261010"
DEFAULT_REVIEW_EVIDENCE = REVIEW_EVIDENCE
TASK_EVIDENCE = EVIDENCE.parent
ORIGINAL_REPORT = COMBINATION_STATE = COMBINATION_REPORT = None
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


def read_exact(path):
    """Read regular evidence through a held inode without modifying producer files."""
    evidence_path(path, TASK_EVIDENCE)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 16 * 1024 * 1024:
            raise ValueError(f"Nonregular/empty/oversized evidence: {path}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(16 * 1024 * 1024 + 1)
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)
        if stamp(before) != stamp(os.fstat(fd)) or stamp(before) != stamp(path.lstat()):
            raise ValueError(f"Evidence inode/content changed during read: {path}")
        if len(data) != before.st_size or before.st_nlink != 1:
            raise ValueError(f"Short or multiply linked evidence: {path}")
        return data, stamp(before)
    finally:
        os.close(fd)


def evidence_path(path, boundary):
    """Keep the approved logical path while checking its physical boundary."""
    if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(boundary):
        raise ValueError(f"Evidence path must be absolute and inside {boundary}: {path}")
    relative = path.relative_to(boundary)
    current = boundary
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Evidence path has a symlink below its boundary: {current}")
    if not path.resolve().is_relative_to(boundary.resolve()):
        raise ValueError(f"Evidence physical path escapes {boundary}: {path}")
    return path


def validate_review_args(parser, args):
    originals = (args.original_report, args.combination_state, args.combination_report)
    if any(path is not None for path in originals) and not all(path is not None for path in originals):
        parser.error("Original review requires original report plus combination state and report")
    if args.health_review_only and (args.real_browser or args.preflight):
        parser.error("Health parse review cannot launch browser or preflight")
    try:
        allowed_tools = {TOOL.resolve(), Path("/data/dev/opensim/tools/browser_performance.mjs").resolve()}
        if (not args.tool.is_absolute() or ".." in args.tool.parts or args.tool.is_symlink() or
                args.tool.resolve(strict=True) not in allowed_tools or not args.tool.is_file()):
            raise ValueError("Tool must be the review or main repository performance tool")
        output = evidence_path(args.evidence_root, EVIDENCE)
        relative = output.relative_to(EVIDENCE)
        if (output != DEFAULT_REVIEW_EVIDENCE and
                (not relative.parts or not re.fullmatch(r"review-integrated-v2-[A-Za-z0-9_-]+", relative.parts[0]))):
            raise ValueError("Evidence output must be in a dedicated review-integrated-v2-* directory")
        if all(path is not None for path in originals):
            for path in originals:
                evidence_path(path, TASK_EVIDENCE)
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise ValueError(f"Original input must be a regular file: {path}")
            original, state, combination = originals
            if not original.is_relative_to(EVIDENCE) or original.name != "report.json":
                raise ValueError("Original report must be a seat-perf report.json")
            if (state.parent != combination.parent or combination.name != "workbench.json" or
                    not re.fullmatch(r"state-[0-9]{3}\.json", state.name) or
                    state.parent.parent != TASK_EVIDENCE or
                    not state.parent.name.startswith("workbench-integrated-")):
                raise ValueError("Combination inputs must be same-run state-NNN.json and workbench.json")
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return args.tool.resolve(strict=True), output, originals


def inspect_original(path):
    """Independent arithmetic and file checks; never recertify an original FAIL."""
    data, stamp = read_exact(path)
    report = json.loads(data)
    sample = report["samples"][0]
    if (report["verdict"] != "FAIL" or report["exit_code"] != 1 or
            report["scope"] != "LOCAL_PREFLIGHT" or report["g5_certified"] or
            report["release_verdict"] != "NOT_CERTIFIED" or len(report["samples"]) != 1 or
            report["baseline"] != BASELINE or report["identity_before"] != report["identity_after"]):
        raise ValueError("Original terminal/baseline/source contract differs")
    state = json.loads(read_exact(COMBINATION_STATE)[0])
    workbench = json.loads(read_exact(COMBINATION_REPORT)[0])
    steps = [step for step in state["report"]["steps"] if step["name"] == "perf"]
    if len(steps) != 1 or steps[0]["rc"] != 1 or state["stage"] != "perf-exit":
        raise ValueError("Original perf rc not established by combination state")
    if any(step["name"] == "perf-readback" for step in workbench["steps"]):
        raise ValueError("Unexpected parse invocation after original failure")
    record = workbench["producers"]["perf"]["report"]
    digest = lambda b: hashlib.sha256(b).hexdigest()
    if (Path(record["path"]).resolve() != path.resolve() or record["sha256"] != digest(data) or
            record["dev"] != str(stamp[0]) or record["ino"] != str(stamp[1])):
        raise ValueError("Combination did not bind the current original inode/bytes")
    raw, metrics = sample["raw_metrics"], sample["metrics"]
    lcp = raw["lcp"][-1]["startTime"]
    maximum, value, start, last = 0, 0, None, None
    for shift in sorted(raw["shifts"], key=lambda row: row["startTime"]):
        if shift["hadRecentInput"]:
            continue
        moment = shift["startTime"]
        if start is None or moment - last >= 1000 or moment - start >= 5000:
            start, value = moment, 0
        value += shift["value"]
        maximum, last = max(maximum, value), moment
    latencies = []
    for row in sample["interactions"]:
        if row["click_count"] != 1 or any(not p["trusted"] for p in row["paints"]):
            raise ValueError("Untrusted/multiple original clicks")
        bounds = [p["end_ms"] - p["start_ms"] for p in row["paints"]]
        if bounds != [p["bound_ms"] for p in row["paints"]]:
            raise ValueError("Original raw paint subtraction differs")
        latency = max(bounds + [e["duration"] for e in row["event_timing"]])
        if latency != row["latency_ms"]:
            raise ValueError("Original interaction raw/summary mismatch")
        latencies.append(latency)
    measured = {"lcp_ms": lcp, "cls": maximum, "interaction_latency_ms": max(latencies)}
    if any(metrics[key] != value for key, value in measured.items()) or metrics["inp_ms"] is not None:
        raise ValueError("Original metrics differ from independent raw arithmetic")
    resources = sample["resources"]
    measured.update({"critical_bytes": sum(r["transfer_bytes"] for r in resources
                                           if r["type"] in {"Document", "Stylesheet", "Script"}),
                     "first_screen_bytes": sum(r["transfer_bytes"] for r in resources),
                     "javascript_bytes": sum(r["transfer_bytes"] for r in resources if r["type"] == "Script"),
                     "font_bytes": sum(r["transfer_bytes"] for r in resources if r["type"] == "Font")})
    limits = {"lcp_ms": 2500, "cls": 0.1, "interaction_latency_ms": 200, "critical_bytes": 200000,
              "first_screen_bytes": 1000000, "javascript_bytes": 300000, "font_bytes": 150000}
    if any(not math.isfinite(value) or value > limits[key] for key, value in measured.items()):
        raise ValueError("Original actually exceeded an unchanged budget")
    identity = sample["source_identity"]
    sources = report["identity_before"]["source_sha256"]
    if sources != report["identity_before"]["committed_source_sha256"]:
        raise ValueError("Original source identity is not committed")
    source_root = Path("/data/dev/opensim")
    commit = report["commit"]
    for name in [*SOURCES, "tools/browser_performance.mjs"]:
        committed = subprocess.run(["git", "show", f"{commit}:{name}"],
                                   cwd=source_root, capture_output=True, check=True).stdout
        expected = report["tool_sha256"] if name == "tools/browser_performance.mjs" else sources[name]
        if digest(committed) != expected:
            raise ValueError(f"Original commit bytes differ: {name}")
    healths = identity["health_responses"]
    health_phases = []
    for kind in ("health", "solve"):
        rows = identity[kind + "_responses"]
        ids = [row["request_id"] for row in rows]
        actual = [r for r in identity["response_resources"] if r["url"].endswith("/api/" + kind)]
        if len(ids) != len(set(ids)) or set(ids) != {r["request_id"] for r in actual}:
            raise ValueError(f"Original {kind} request inventory differs")
        for index, row in enumerate(rows, 1):
            wire = row["response_body"].encode()
            saved = read_exact(path.parent / f"{kind}-{index}.json")[0]
            resource = next(r for r in actual if r["request_id"] == row["request_id"])
            if (digest(wire) != row["response_sha256"] or resource["body_sha256"] != digest(wire) or
                    resource["status"] != 200 or not resource["finished"] or
                    json.loads(wire) != row["result"] or json.loads(saved) != row["result"]):
                raise ValueError(f"Original {kind} raw bytes/result differ")
            if kind == "health":
                if saved != wire or row["result"]["source_sha256"] != sources:
                    raise ValueError("Original health identity differs")
                health_phases.append(resource["phase"])
            elif (row["result"]["config"] != CONFIG or
                  row["result"]["reproducibility"]["source_sha256"] != sources or
                  len(row["result"]["fields"]["velocity_m_s"]) != len(row["result"]["mesh"]["nodes_mm"])):
                raise ValueError("Original full FEM/config identity differs")
    for resource in identity["response_resources"]:
        if resource["type"] in {"Document", "Script", "Stylesheet"}:
            saved = read_exact(path.parent / ("response-" + resource["request_id"].replace(".", "-") + ".txt"))[0]
            if digest(saved) != resource["body_sha256"]:
                raise ValueError("Original page/assets raw bytes differ")
    if "<code>" + report["identity_before"]["page_build"] + "</code>" not in identity["document_body"]:
        raise ValueError("Original exact page build element missing")
    screenshot_file = sample["screenshot"]["file"]
    if Path(screenshot_file).name != screenshot_file:
        raise ValueError("Original screenshot must be a run-local basename")
    png = read_exact(path.parent / screenshot_file)[0]
    if digest(png) != sample["screenshot"]["sha256"] or len(png) != sample["screenshot"]["bytes"]:
        raise ValueError("Original screenshot bytes/hash differ")
    if not png.startswith(b"\x89PNG\r\n\x1a\n") or struct.unpack(">II", png[16:24]) != (360, 800):
        raise ValueError("Original PNG size/signature differs")
    offset, chunks, ended = 8, [], False
    while offset < len(png):
        length = int.from_bytes(png[offset:offset + 4], "big")
        kind, chunk = png[offset + 4:offset + 8], png[offset + 8:offset + 8 + length]
        crc = int.from_bytes(png[offset + 8 + length:offset + 12 + length], "big")
        if zlib.crc32(kind + chunk) != crc:
            raise ValueError("Original PNG CRC differs")
        if kind == b"IDAT":
            chunks.append(chunk)
        offset += length + 12
        if kind == b"IEND":
            ended = True
            break
    channels = {2: 3, 6: 4}[png[25]]
    pixels = zlib.decompress(b"".join(chunks))
    if not ended or offset != len(png) or len(pixels) != (360 * channels + 1) * 800:
        raise ValueError("Original PNG incomplete")
    candidate = json.loads(read_exact(path.parent / "report-candidate.json")[0])
    if candidate != dict(report, verdict="EVIDENCE_PENDING", exit_code=2, g5_certified=False):
        raise ValueError("Original held-inode pending candidate differs")
    expected_log = (report["started_at"] + " START\n" + report["finished_at"] +
                    " EVIDENCE_READY scope=LOCAL_PREFLIGHT terminal_authority=")
    log = read_exact(path.parent / "run.log")[0].decode()
    if not log.startswith(expected_log) or Path(log[len(expected_log):].strip()).resolve() != path.resolve():
        raise ValueError("Original ready log differs")
    if list(path.parent.glob("report-failure-*.json")) or report.get("persistence_failure"):
        raise ValueError("Original unexpected persistence failure marker")
    return {"collected_at": datetime.now(timezone.utc).isoformat(), "verdict": "DIAGNOSED_HARNESS_BUG",
            "original_verdict": "FAIL", "original_rc_from_state": steps[0]["rc"], "parse_state": "NOT_RUN",
            "report": str(path), "report_sha256": digest(data), "dev": stamp[0], "ino": stamp[1],
            "started_at": report["started_at"], "finished_at": report["finished_at"],
            "measured": measured, "limits": limits, "budget_exceeded": False,
            "health_count": len(healths), "health_phases": health_phases, "solves": 2,
            "samples": 1, "retries": 0, "screenshot": sample["screenshot"],
            "source_before_after_equal": True, "scope": "LOCAL_PREFLIGHT", "g5_certified": False,
            "production_changes": 0}


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
            phase = (["cold-baseline", "core-interactions"] if key == "solve_responses" else
                     ["identity-before-interactions", "source-identity"])[index]
            identity[key].append({"phase": phase, "result": json.loads(text), "response_body": text,
                                  "response_sha256": digest, "request_id": request_id})
            identity["response_resources"].append({
                "request_id": request_id, "url": target.rstrip("/") + pathname, "method": method,
                "phase": phase, "status": 200, "finished": True, "body_sha256": digest})
    snapshot = {"commit": "a" * 40, "tool_sha256": "b" * 64,
                "identity_scope": "FIVE_APPLICATION_SOURCES_PLUS_PERFORMANCE_TOOL_NOT_FULL_TREE",
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


def add_startup_health(report, mutation=None):
    """Keep all three distinct responses, as the integrated page actually does."""
    identity = report["samples"][0]["source_identity"]
    row = json.loads(json.dumps(identity["health_responses"][0]))
    row.update(request_id="startup-health", phase="cold-baseline")
    resource = dict(identity["response_resources"][2], request_id=row["request_id"], phase=row["phase"])
    identity["health_responses"].insert(0, row)
    identity["response_resources"].append(resource)
    if mutation:
        mutation(identity, row, resource)


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
    add("health-three-valid", "PASS", mutation=add_startup_health)
    add("health-extra-wrong-source", "FAIL", "G5-IDENTITY", lambda r: add_startup_health(r,
        lambda identity, row, resource: row["result"]["source_sha256"].update({"solver.py": "c" * 64})))
    add("health-extra-wrong-body", "FAIL", "G5-IDENTITY", lambda r: add_startup_health(r,
        lambda identity, row, resource: row.update(response_body="{}")))
    add("health-extra-wrong-status", "FAIL", "G5-IDENTITY", lambda r: add_startup_health(r,
        lambda identity, row, resource: resource.update(status=503)))
    add("health-extra-duplicate-id", "FAIL", "G5-IDENTITY", lambda r: add_startup_health(r,
        lambda identity, row, resource: row.update(request_id=identity["health_responses"][1]["request_id"])))
    add("health-extra-missing-row", "FAIL", "G5-IDENTITY", lambda r: add_startup_health(r,
        lambda identity, row, resource: identity["health_responses"].remove(row)))
    add("health-extra-missing-resource", "FAIL", "G5-IDENTITY", lambda r: add_startup_health(r,
        lambda identity, row, resource: identity["response_resources"].remove(resource)))
    add("health-extra-duplicate-resource", "FAIL", "G5-IDENTITY", lambda r: add_startup_health(r,
        lambda identity, row, resource: identity["response_resources"].append(dict(resource))))
    add("health-missing-before-phase", "FAIL", "G5-IDENTITY", lambda r: add_startup_health(r,
        lambda identity, row, resource: identity["health_responses"][1].update(phase="cold-baseline")))
    add("health-missing-final-phase", "FAIL", "G5-IDENTITY", lambda r: add_startup_health(r,
        lambda identity, row, resource: identity["health_responses"][2].update(phase="cold-baseline")))
    add("health-extra-unknown-phase", "FAIL", "G5-IDENTITY", lambda r: add_startup_health(r,
        lambda identity, row, resource: (row.update(phase="unknown"), resource.update(phase="unknown"))))
    add("health-extra-phase-mismatch", "FAIL", "G5-IDENTITY", lambda r: add_startup_health(r,
        lambda identity, row, resource: resource.update(phase="source-identity")))
    add("health-extra-wrong-method", "FAIL", "G5-IDENTITY", lambda r: add_startup_health(r,
        lambda identity, row, resource: resource.update(method="POST")))
    add("health-extra-wrong-origin", "FAIL", "G5-IDENTITY", lambda r: add_startup_health(r,
        lambda identity, row, resource: resource.update(url="http://127.0.0.1:54321/api/health")))
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
        cls.argument_invocations = []

    @classmethod
    def tearDownClass(cls):
        write_json(cls.evidence_run / "invocations.json", cls.invocations)
        write_json(cls.evidence_run / "argument-invocations.json", cls.argument_invocations)

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

    def test_health_response_inventory(self):
        cases = [case for case in self.cases if case["file"].startswith("health-") or
                 case["file"] == "positive-synthetic-ruler.json"]
        self.assertEqual(len(cases), 15)
        for case in cases:
            with self.subTest(file=case["file"]):
                result, report = self.invoke("--parse", str(self.fixtures / case["file"]))
                self.assertEqual(result.returncode, 0 if case["expected"] == "PASS" else 1,
                                 result.stdout + result.stderr)
                self.assertFalse(report["g5_certified"])
                if case["expected"] == "FAIL":
                    self.assertTrue(any(row["rule"] == "G5-IDENTITY" and row["status"] == "FAIL"
                                        for row in report["checks"]))
        if ORIGINAL_REPORT:
            before = read_exact(ORIGINAL_REPORT)
            actual = inspect_original(ORIGINAL_REPORT)
            result, parsed = self.invoke("--parse", str(ORIGINAL_REPORT))
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(parsed["verdict"], "FAIL")
            self.assertEqual(parsed["input_failure"]["rule"], "G5-IDENTITY")
            self.assertEqual(parsed["parsed_source"]["sha256"], actual["report_sha256"])
            self.assertTrue(any(row["rule"] == "EVIDENCE" and row["status"] == "FAIL"
                                for row in parsed["checks"]))
            self.assertEqual(read_exact(ORIGINAL_REPORT), before)
            write_json(self.evidence_run / "original-review.json", actual)

    def test_review_cli_rejects_partial_inputs_and_unsafe_paths(self):
        """Reject bad admission before creating any child evidence or executing a tool."""
        original_args = ["--original-report", str(self.fixtures / "report.json")]
        state_args = ["--combination-state", str(TASK_EVIDENCE / "workbench-integrated-v2/state-021.json")]
        combination_args = ["--combination-report", str(TASK_EVIDENCE / "workbench-integrated-v2/workbench.json")]
        triples = [original_args, state_args, combination_args]
        cases = [([arg for index, pair in enumerate(triples) if mask & (1 << index) for arg in pair],
                  "Original review requires") for mask in range(1, 7)]
        cases += [(["--health-review-only", option], "cannot launch")
                  for option in ("--real-browser", "--preflight")]
        cases += [(["--evidence-root", str(path)], "Evidence") for path in (
            Path("/tmp/opensim-perf-unapproved"), EVIDENCE, EVIDENCE / "workbench-other-seat",
            Path("relative-evidence"), EVIDENCE / "review-integrated-v2-invalid/../escape")]
        cases += [(["--tool", "/bin/true"], "Tool must be")]
        link = self.evidence_run / "symlink-output"
        link.symlink_to(self.fixtures, target_is_directory=True)
        cases += [(["--evidence-root", str(link / "child")], "symlink")]
        cases += [(original_args + state_args + combination_args, "No such file")]
        outside = ["--original-report", "/etc/passwd"] + state_args + combination_args
        cases += [(outside, "Evidence path")]
        child_output = self.evidence_run / "argument-child"
        for index, (args, message) in enumerate(cases, 1):
            with self.subTest(args=args):
                command = [sys.executable, "-B", str(Path(__file__).resolve()),
                           "--evidence-root", str(child_output), *args]
                result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(message, result.stderr)
                self.assertNotIn("PERFORMANCE_TESTS", result.stdout)
                self.assertFalse(child_output.exists())
                log = f"argument-{index:03d}.log"
                (self.evidence_run / log).write_text(result.stdout + result.stderr, encoding="utf-8")
                self.argument_invocations.append({"command": command, "exit_code": result.returncode, "log": log})

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

    def test_real_inode_drift_and_persistence_io_controls(self):
        result, report = self.invoke("--persistence-selftest")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        actual = report["persistence_selftest"]
        self.assertEqual(actual["count"], 18)
        self.assertEqual(actual["status"], "PASS")
        self.assertEqual(len({row["name"] for row in actual["cases"]}), 18)
        self.assertEqual(sum(row["rc"] == 2 for row in actual["cases"]), 17)
        self.assertTrue(all(row["status"] == "PASS" for row in actual["cases"]))
        self.assertTrue(all(row["failure"]["code"] == "EBADF"
                            for row in actual["cases"] if "ebadf" in row["name"]))
        self.assertEqual(report["scope"], "PERSISTENCE_FIXTURES_NOT_G5")
        self.assertFalse(report["g5_certified"])


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
        if len(responses) < 2 or kind == "solve" and len(responses) != 2:
            raise ValueError(f"Missing required full {kind} responses")
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
    global TOOL, REVIEW_EVIDENCE, ORIGINAL_REPORT, COMBINATION_STATE, COMBINATION_REPORT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-browser", action="store_true")
    parser.add_argument("--preflight", action="store_true",
                        help="Start/stop an owned new loopback server and verify actual product evidence")
    parser.add_argument("--health-review-only", action="store_true",
                        help="Only parse health inventory fixtures; never launch Chromium/server")
    parser.add_argument("--tool", type=Path, default=TOOL)
    parser.add_argument("--evidence-root", type=Path, default=REVIEW_EVIDENCE)
    parser.add_argument("--original-report", type=Path)
    parser.add_argument("--combination-state", type=Path)
    parser.add_argument("--combination-report", type=Path)
    args = parser.parse_args()
    TOOL, REVIEW_EVIDENCE, originals = validate_review_args(parser, args)
    ORIGINAL_REPORT, COMBINATION_STATE, COMBINATION_REPORT = originals
    suite = (unittest.TestSuite([PerformanceTests("test_health_response_inventory")])
             if args.health_review_only else unittest.defaultTestLoader.loadTestsFromTestCase(PerformanceTests))
    test_log = io.StringIO()
    result = unittest.TextTestRunner(verbosity=2, stream=test_log).run(suite)
    directory = PerformanceTests.evidence_run
    (directory / "unittest.log").write_text(test_log.getvalue(), encoding="utf-8")
    report = {"collected_at": datetime.now(timezone.utc).isoformat(),
              "tests": result.testsRun, "fixture_cases": len(PerformanceTests.cases),
              "cli_invocations": len(PerformanceTests.invocations),
              "failures": len(result.failures), "errors": len(result.errors),
              "real_browser": "NOT_RUN", "verdict": "PASS" if result.wasSuccessful() else "FAIL"}
    report.update(tool_path=str(TOOL), tool_sha256=hashlib.sha256(TOOL.read_bytes()).hexdigest(),
                  health_review_only=args.health_review_only, production_changes=0,
                  original_review="original-review.json" if ORIGINAL_REPORT else None)
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
                                     len(PerformanceTests.argument_invocations) +
                                     int("real_browser_exit_code" in report) +
                                     len(report.get("preflight", {}).get("browser_runs", [])))
    report["argument_invocations"] = len(PerformanceTests.argument_invocations)
    report["collected_at"] = datetime.now(timezone.utc).isoformat()
    write_json(directory / "summary.json", report)
    print(f"PERFORMANCE_TESTS verdict={report['verdict']} tests={report['tests']} "
          f"fixtures={report['fixture_cases']} browser={report['real_browser']} "
          f"report={directory / 'summary.json'}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(run_main())
