"""Real external files/Git and CLI negative controls; fixtures are NOT releases.

Run this file directly for an append-only external test report and execution
log. No mocks, package installation, service operation or production writes.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import io
import os
from pathlib import Path
import struct
import subprocess
import sys
import unittest
from uuid import uuid4
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import release_gate as gate

PEERS = {
    "tools/release_app.py": Path("/data/dev/opensim-release-v1/tools/release_app.py"),
    "tools/browser_audit.mjs": Path("/data/dev/opensim-audit-v1/tools/browser_audit.mjs"),
    "tools/browser_performance.mjs": Path("/data/dev/opensim-perf-v1/tools/browser_performance.mjs"),
}


def png() -> bytes:
    def chunk(kind, body):
        return struct.pack("!I", len(body)) + kind + body + struct.pack("!I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(b"\0\0\0\0")) + chunk(b"IEND", b""))


def put(path: Path, body: bytes) -> None:
    gate.external(path, gate.OUTPUT_ROOT)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    if path.read_bytes() != body:
        raise AssertionError(f"Fixture readback failed: {path}")


def command(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                                     OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1"))
    if result.returncode:
        raise AssertionError(f"Fixture command rc={result.returncode}: {argv}: {result.stderr}")
    return result


class Fixture:
    def __init__(self, approval: dict | None = None):
        self.directory = gate.OUTPUT_ROOT / ("fixture-" + uuid4().hex)
        self.repo = self.directory / "repo"
        self.repo.mkdir(parents=True)
        self.now = datetime.now(timezone.utc)
        self.time = self.now.isoformat()
        for name in set((*gate.SOURCES, *gate.TOOLS, "requirements.txt")):
            source = ROOT / name
            if not source.exists():
                source = PEERS[name]
            put(self.repo / name, source.read_bytes())
        if approval:
            put(self.repo / "docs/approvals.json", gate.encode({
                "schema": "opensim-user-approvals-v1", "records": [approval]}))
        command(["git", "init", "--initial-branch=fixture"], self.repo)
        command(["git", "add", "--", *sorted(set((*gate.SOURCES, *gate.TOOLS, "requirements.txt"))),
                 *(["docs/approvals.json"] if approval else [])], self.repo)
        command(["git", "-c", "user.name=Gate Test Fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "core.hooksPath=/dev/null", "commit", "-m", "FIXTURE_NOT_RELEASE"], self.repo)
        self.commit = command(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        self.identity = gate.identity(self.repo, self.commit)
        self.approval_pin = None
        if approval:
            self.approval_pin = {"path": "docs/approvals.json",
                                 "sha256": gate.sha((self.repo / "docs/approvals.json").read_bytes())}
        self.ledger_path = self.directory / "ledger.json"
        self.reports = {}
        edited = datetime.fromtimestamp(int(max((self.repo / n).stat().st_mtime for n in gate.SOURCES)),
                                        timezone(timedelta(hours=8))).isoformat()
        html = f'<html><footer>{self.identity["page_build"]}</footer></html>'.encode()
        put(self.directory / "page/index-v1.html", html)
        page_check = {"verdict": "PASS", "html_sha256": gate.sha(html),
                      "source_sha256_prefix": self.identity["page_build"], "edited_at": edited}
        self.store("page-check", page_check)
        page = {"schema": "opensim-page-evidence-v1", "commit": self.commit,
                "source_sha256": self.identity["source_sha256"],
                "page_build": self.identity["page_build"],
                "timestamp_origin": "SOURCE_MTIME", "source_epoch": None,
                "html": {"path": str(self.directory / "page/index-v1.html"), "sha256": gate.sha(html)},
                "page_check": self.pin("page-check")}
        local = {"verdict": "PASS", "collected_at": self.time,
                 "source_sha256": {**self.identity["source_sha256"], "tools/validate_numerics.py":
                                  self.identity["tool_sha256"]["tools/validate_numerics.py"]},
                 "steps": [{"argv": ["fixture", name], "rc": 0, "stdout": "Ran 7 tests\nOK\n",
                            "stderr": ""} for name in ("unittest", "node", "numeric", "page", "repeat")]}
        numerics = {"state": "COMPLETE", "verdict": "PASS", "collected_at": self.time,
                    "solver": {"sha256": self.identity["source_sha256"]["solver.py"]},
                    "checks_executed": 3, "cases": [{"verdict": "PASS"}],
                    "rejections": [{"verdict": "PASS"}],
                    "convergence": {n: {"verdict": "PASS", "resolutions": [8, 16, 32],
                                       "relative_flow_error": {"errors": [0.1, 0.05, 0.01]}}
                                    for n in ("circle", "rectangle", "square")}}
        browser = self.common("tools/browser_check.mjs")
        browser.update({"mode": "OPENSIM_ACCEPTANCE", "verdict": "AUTOMATED_PASS",
                        "release_verdict": "NOT_CERTIFIED",
                        "page_build": {"build_text": f'fixture build {self.identity["page_build"]}',
                                       "edited_at": edited},
                        "matrix": [{"width": w, "height": h, "status": "PASS"} for w, h in gate.VIEWPORTS],
                        "checks": [{"name": name, "status": "PASS", "rule": rule} for name, rule in
                                   (("structure", "G1-structure"), ("layout", "G2-layout"),
                                    ("keyboard", "G4-keyboard"))]})
        # Deliberately use native browser schema without a top-level source map.
        browser.pop("source_sha256")
        audit = self.common("tools/browser_audit.mjs")
        audit.update({"mode": "G3_G6_AUTOMATIC_EXTENSION_NOT_RELEASE", "verdict": "AUTOMATED_SCOPE_PASS",
                      "gates": {name: {"status": "PASS"} for name in ("G3-accessibility", "G6-content")},
                      "checks": [{"name": "accessibility", "status": "PASS"},
                                 {"name": "content", "status": "PASS"}]})
        perf = self.common("tools/browser_performance.mjs")
        perf.update({"scope": "PUBLIC_LAB", "verdict": "PUBLIC_G5_PASS", "g5_certified": True,
                     "target_url": gate.PUBLIC_URL, "baseline_commands_acknowledged": True,
                     "baseline": {"id": "opensim-mobile-cold-v1", "width": 360, "height": 800,
                                  "device_scale_factor": 1, "mobile": True, "touch": True,
                                  "latency_ms": 150, "download_bytes_s": 200000, "upload_bytes_s": 93750,
                                  "cpu_rate": 4, "observation_ms": 8000, "cache_disabled": True,
                                  "service_worker_bypassed": True, "samples": 1, "timeout_ms": 30000,
                                  "retries": 0}, "browser": {"product": "Chrome/fixture"},
                     "public_dns": [{"address": "8.8.8.8"}],
                     "samples": [{"target_url": gate.PUBLIC_URL,
                                  "source_identity": {"bound": True, "assets_bound": True,
                                                      "local_source_sha256": self.identity["source_sha256"],
                                                      "committed_source_sha256": self.identity["source_sha256"],
                                                      "solve_source_sha256": self.identity["source_sha256"],
                                                      "health": {"source_sha256": self.identity["source_sha256"],
                                                                 "page_build": self.identity["page_build"]}},
                                  "phase": "ONE_COLD_NAVIGATION_PLUS_THREE_CORE_TOUCHES",
                                  "raw_metrics": {"lcp": [{"startTime": 100}], "shifts": [],
                                                  "supported": ["layout-shift"], "observer_errors": []},
                                  "interactions": [{"selector": selector, "click_count": 1,
                                                    "latency_ms": 10, "event_timing": [],
                                                    "paints": [{"type": "click", "trusted": True,
                                                                "start_ms": 1, "end_ms": 11, "bound_ms": 10}]}
                                                   for selector in ("#view-mesh", "#view-field", "#solve-button")],
                                  "metrics": {"lcp_ms": 100, "interaction_latency_ms": 10, "cls": 0,
                                              "lcp_count": 1, "interaction_count": 3, "cls_observed": True},
                                  "fonts": {"ready": True, "errors": [], "preloads": 0,
                                            "fallback_verified": True, "faces": []},
                                  "third_party": [], "isolation": "BLOCK_ALL_OFF_ORIGIN_BEFORE_REQUEST",
                                  "redirects": [], "network_errors": [], "runtime_errors": [], "images": [],
                                  "content": {"ready": True, "text_length": 101, "canvas_colors": 4,
                                              "solve_count": 2, "nodes": 11, "triangles": 11, "max_velocity": 1},
                                  "resources": [{"url": gate.PUBLIC_URL + "/fixture", "type": name,
                                                 "transfer_bytes": 32, "finished": True, "cached": False,
                                                 "mime": "text/javascript", "decoded_body_bytes": 1024,
                                                 "encoding": "gzip", "fingerprinted": True,
                                                 "body_sha256": "a" * 64, "fingerprint_sha256": "a" * 64,
                                                 "cache_control": "public, max-age=31536000, immutable",
                                                 "remote_ip": "8.8.8.8", "status": 200}
                                                for name in ("Document", "Stylesheet", "Script")]}],
                     "checks": [{"name": f"perf-{i}", "rule": f"WEB-PERF-{i:03}", "status": "PASS"}
                                for i in range(1, 7)]})
        public = {"verdict": "PASS", "scope": "PUBLIC_CODE_AND_FEM", "url": gate.PUBLIC_URL,
                  "desired_git_head": self.commit, "source_sha256": self.identity["source_sha256"],
                  "page_build": self.identity["page_build"], "collected_at": self.time,
                  "dns_addresses": ["8.8.8.8"],
                  "checks": [{"name": name, "verdict": "PASS"} for name in
                             ("runtime_identity", "page_and_assets", "real_fem_circle", "real_fem_rectangle")]}
        for row in public["checks"]:
            if row["name"].startswith("real_fem_"):
                row.update({"config_sha256": "a" * 64, "solver_sha256": self.identity["source_sha256"]["solver.py"],
                            "node_count": 11, "flow_rate_m3_s": 0.01, "relative_residual": 1e-9,
                            "computed_at": self.time})
        for kind, report in (("page", page), ("local", local), ("numerics", numerics),
                             ("browser", browser), ("audit", audit), ("performance", perf), ("public", public)):
            if kind in {"browser", "audit"}:
                report["screenshots"] = [self.screenshot(kind)]
            self.store(kind, report)
        self.ledger = {"schema": "opensim-release-evidence-v1", "identity": self.identity,
                       "evidence": {k: self.pin(k) for k in
                                    ("page", "local", "numerics", "browser", "audit", "performance", "public")},
                       "executions": {}, "gates": {}, "manual": {}, "exceptions": []}
        for kind, tool in {
            "local": "tools/acceptance.py", "numerics": "tools/validate_numerics.py",
            "browser": "tools/browser_check.mjs", "audit": "tools/browser_audit.mjs",
            "performance": "tools/browser_performance.mjs", "public": "tools/public_gate.py",
            "page": "tools/render_page.py",
        }.items():
            self.launch(kind, tool)
        for name, kind, chosen in (
            ("G1-structure", "browser", "structure"), ("G2-layout", "browser", "layout"),
            ("G3-accessibility", "audit", "accessibility"), ("G4-keyboard", "browser", "keyboard"),
            ("G5-performance", "performance", "perf-1"), ("G6-content", "audit", "content"),
        ):
            self.ledger["gates"][name] = {"evidence": kind, "rc": 0, "status": "PASS", "checks": [chosen]}
            selected = []
            for rule in sorted(gate.REQUIRED_RULES[name]):
                check_name = name + ":" + rule
                self.reports[kind]["checks"].append({
                    "name": check_name, "rule": rule, "status": "PASS",
                    "url": gate.PUBLIC_URL, "viewport": [320, 568],
                    "reproduce": "FIXTURE_NOT_BROWSER_CHECK"})
                selected.append(check_name)
            self.ledger["gates"][name]["checks"] = selected
            self.change(kind)
        for rule in gate.MANUAL:
            report = {"schema": "opensim-human-check-v1", "rule_id": rule, "status": "PASS",
                      "commit": self.commit, "source_sha256": self.identity["source_sha256"],
                      "page_build": self.identity["page_build"], "mode": "REAL_DEVICE_HUMAN",
                      "owner": "FIXTURE_NOT_HUMAN", "device": "FIXTURE", "combination_version": "FIXTURE",
                      "url": gate.PUBLIC_URL, "reproduce": "FIXTURE_NOT_RELEASE", "collected_at": self.time,
                      "checks": [{"status": "PASS"}], "screenshots": [self.screenshot(rule)]}
            self.store(rule, report)
            self.ledger["manual"][rule] = self.pin(rule)
        self.save()

    def common(self, tool: str) -> dict:
        return {"commit": self.commit, "source_sha256": self.identity["source_sha256"],
                "page_build": self.identity["page_build"], "tool_sha256": self.identity["tool_sha256"][tool],
                "standard": "shared-r41", "exit_code": 0, "finished_at": self.time}

    def screenshot(self, kind: str) -> dict:
        put(self.directory / kind / "fixture.png", png())
        return {"file": "fixture.png", "sha256": gate.sha(png())}

    def store(self, kind: str, report: dict) -> None:
        self.reports[kind] = report
        put(self.directory / kind / "report.json", gate.encode(report))

    def pin(self, kind: str) -> dict:
        path = self.directory / kind / "report.json"
        return {"path": str(path), "sha256": gate.sha(path.read_bytes())}

    def change(self, kind: str, **updates) -> None:
        self.store(kind, {**self.reports[kind], **updates})
        self.ledger["evidence"][kind] = self.pin(kind)
        old = self.reports.get("launcher-" + kind)
        if old:
            self.launch(kind, old["tool"])
        self.save()

    def launch(self, kind: str, tool: str) -> None:
        stdout = self.directory / kind / "launcher.stdout"
        stderr = self.directory / kind / "launcher.stderr"
        put(stdout, b"FIXTURE_NOT_PRODUCER_RUN\n")
        put(stderr, b"FIXTURE_NO_REAL_RELEASE_EVIDENCE\n")
        report = {"schema": "opensim-producer-execution-v1", "identity": self.identity,
                  "tool": tool, "argv": ["fixture-not-producer", str(self.repo / tool)], "rc": 0,
                  "report": self.pin(kind), "collected_at": self.time,
                  "stdout": {"path": str(stdout), "sha256": gate.sha(stdout.read_bytes())},
                  "stderr": {"path": str(stderr), "sha256": gate.sha(stderr.read_bytes())}}
        self.store("launcher-" + kind, report)
        self.ledger["executions"][kind] = self.pin("launcher-" + kind)

    def save(self) -> None:
        put(self.ledger_path, gate.encode(self.ledger))

    def evaluate(self) -> dict:
        return gate.evaluate(self.repo, self.commit, self.ledger_path, self.approval_pin)

    def cli(self) -> tuple[subprocess.CompletedProcess, dict]:
        out = self.directory / ("cli-" + uuid4().hex + ".json")
        argv = [sys.executable, "-B", str(ROOT / "tools/release_gate.py"),
                "--source-root", str(self.repo), "--commit", self.commit,
                "--ledger", str(self.ledger_path), "--out", str(out)]
        if self.approval_pin:
            argv += ["--approval-path", self.approval_pin["path"],
                     "--approval-sha256", self.approval_pin["sha256"]]
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        put(out.with_suffix(".subprocess.log"),
            (f"rc={result.returncode}\n" + result.stdout + result.stderr).encode())
        return result, gate.decode(out.read_bytes())


def approval(rule: str = "PHYSICAL-TOUCH", expired: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    return {"id": "EXPLICIT-FIXTURE-APPROVAL", "rule_id": rule, "owner": "fixture-owner",
            "expires_at": (now + timedelta(days=-1 if expired else 1)).isoformat(),
            "reason": "FIXTURE_NOT_USER_APPROVAL", "scope": gate.PUBLIC_URL,
            "removal_condition": "fixture condition", "status": "APPROVED", "approved_by": "user",
            "approved_at": (now - timedelta(days=2)).isoformat(), "approval_record_id": "fixture-record"}


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()

    def blocked(self, needle: str) -> None:
        result, report = self.f.cli()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(report["verdict"], "BLOCKED")
        self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")
        self.assertIn(needle, report["failure"]["reason"])
        self.assertFalse(report["production_changes"])

    def test_complete_fixture_schema_pass_is_not_deployment(self):
        self.blocked("trusted human-acceptance verifier unavailable")
        self.assertEqual(self.f.reports["browser"]["release_verdict"], "NOT_CERTIFIED")

    def test_empty_evidence(self):
        self.f.ledger["evidence"] = {}
        self.f.save()
        self.blocked("evidence missing")

    def test_old_hash(self):
        put(self.f.directory / "audit/report.json", b"{}\n")
        self.blocked("Evidence hash drift")

    def test_dirty_tree(self):
        put(self.f.repo / "untracked.txt", b"other asset\n")
        self.blocked("Dirty checkout")
        self.assertEqual((self.f.repo / "untracked.txt").read_bytes(), b"other asset\n")

    def test_modified_tracked_tree(self):
        put(self.f.repo / "web/app.js", b"other developer changes\n")
        self.blocked("Dirty checkout")

    def test_old_tool_hash(self):
        self.f.change("browser", tool_sha256="0" * 64)
        self.blocked("Old/unknown tool")

    def test_wrong_commit(self):
        self.f.change("browser", commit="0" * 40)
        self.blocked("Report commit mismatch")

    def test_automated_only_cannot_certify(self):
        self.f.ledger["manual"] = {}
        self.f.save()
        self.blocked("Missing real device")

    def test_partial_audit(self):
        self.f.reports["audit"]["gates"]["G3-accessibility"]["status"] = "PARTIAL"
        self.f.change("audit")
        self.blocked("PARTIAL audit")

    def test_partial_content_gate_never_promoted(self):
        self.f.reports["audit"]["gates"]["G6-content"]["status"] = "PARTIAL"
        self.f.change("audit")
        self.blocked("PARTIAL audit cannot certify G6-content")

    def test_missing_g6_record(self):
        self.f.ledger["gates"].pop("G6-content")
        self.f.save()
        self.blocked("Missing G1..G6")

    def test_local_rc_without_completed_tests_not_evidence(self):
        for step in self.f.reports["local"]["steps"]:
            step["stdout"] = ""
        self.f.change("local")
        self.blocked("without actual tests")

    def test_binding_and_performance_positive_controls(self):
        for kind, tool in (("browser", "tools/browser_check.mjs"),
                           ("audit", "tools/browser_audit.mjs"),
                           ("performance", "tools/browser_performance.mjs")):
            gate.binding(self.f.reports[kind], self.f.identity, tool, datetime.now(timezone.utc))
        gate.performance(self.f.reports["performance"])
        self.assertEqual(gate.identity(self.f.repo, self.f.commit), self.f.identity)

    def test_local_perf_not_g5(self):
        self.f.change("performance", scope="LOCAL_PREFLIGHT", verdict="LOCAL_PREFLIGHT_PASS", g5_certified=False)
        self.blocked("LOCAL_PREFLIGHT")

    def test_loopback_disguised_public_performance(self):
        self.f.change("performance", target_url="http://127.0.0.1:9999")
        self.blocked("PUBLIC_LAB")

    def test_loopback_dns(self):
        self.f.change("public", dns_addresses=["127.0.0.1"])
        self.blocked("Loopback/private DNS")

    def test_stale_public(self):
        self.f.change("public", collected_at=(self.f.now - timedelta(minutes=11)).isoformat())
        self.blocked("Stale/future")

    def test_missing_real_fem(self):
        self.f.change("public", checks=[{"name": "runtime_identity", "verdict": "PASS"}])
        self.blocked("Missing real public computations")

    def test_missing_screenshot(self):
        self.f.change("browser", screenshots=[])
        self.blocked("Missing actual screenshot")

    def test_empty_selected_gate_checks(self):
        self.f.ledger["gates"]["G2-layout"]["checks"] = []
        self.f.save()
        self.blocked("Empty/duplicate gate")

    def test_missing_viewport(self):
        self.f.change("browser", matrix=self.f.reports["browser"]["matrix"][:-1])
        self.blocked("matrix missing")

    def test_wrong_build(self):
        self.f.change("browser", page_build={"build_text": "wrong", "edited_at":
                                           self.f.reports["browser"]["page_build"]["edited_at"]})
        self.blocked("build_text differs")

    def test_page_timestamp_origin_not_invented(self):
        self.f.change("page", source_epoch=1)
        self.blocked("Local edited_at")

    def test_unapproved_exception(self):
        self.f.ledger["exceptions"] = ["NOT_APPROVED"]
        self.f.save()
        self.blocked("Unapproved/unknown")

    def test_expired_exception(self):
        self.f = Fixture(approval(expired=True))
        self.f.ledger["exceptions"] = ["EXPLICIT-FIXTURE-APPROVAL"]
        self.f.save()
        self.blocked("Expired/future exception")

    def test_editable_json_user_approval_cannot_self_certify(self):
        self.f = Fixture(approval())
        self.f.ledger["exceptions"] = ["EXPLICIT-FIXTURE-APPROVAL"]
        self.f.ledger["manual"]["PHYSICAL-TOUCH"] = {"status": "EXCEPTION"}
        self.f.save()
        self.blocked("no trusted user-approval verifier")

    def test_native_browser_no_exit_code_uses_launcher(self):
        report = self.f.reports["browser"]
        report.pop("exit_code")
        self.f.change("browser")
        self.blocked("trusted human-acceptance verifier unavailable")

    def test_wrong_launcher_report(self):
        launch = self.f.reports["launcher-browser"]
        launch["report"] = self.f.pin("audit")
        self.f.store("launcher-browser", launch)
        self.f.ledger["executions"]["browser"] = self.f.pin("launcher-browser")
        self.f.save()
        self.blocked("Launcher does not bind")

    def test_empty_launcher_stderr_is_real_empty_file_not_missing(self):
        path = self.f.directory / "browser/launcher.stderr"
        put(path, b"")
        report = self.f.reports["launcher-browser"]
        report["stderr"]["sha256"] = gate.sha(b"")
        self.f.store("launcher-browser", report)
        self.f.ledger["executions"]["browser"] = self.f.pin("launcher-browser")
        self.f.save()
        self.blocked("trusted human-acceptance verifier unavailable")

    def test_assume_unchanged_cannot_fake_clean(self):
        command(["git", "update-index", "--assume-unchanged", "web/app.js"], self.f.repo)
        self.blocked("assume-unchanged")

    def test_sparse_rule_coverage_blocks_gate(self):
        self.f.ledger["gates"]["G1-structure"]["checks"] = ["structure"]
        self.f.save()
        self.blocked("missing required rule")

    def test_loopback_remote_connection_cannot_fake_public(self):
        self.f.reports["performance"]["samples"][0]["resources"][0]["remote_ip"] = "127.0.0.1"
        self.f.change("performance")
        self.blocked("connection/DNS not public")

    def test_perf_raw_summary_mismatch(self):
        self.f.reports["performance"]["samples"][0]["raw_metrics"]["lcp"][0]["startTime"] = 101
        self.f.change("performance")
        self.blocked("raw LCP/CLS")

    def test_perf_cache_self_report_not_enough(self):
        self.f.reports["performance"]["samples"][0]["resources"][1]["cache_control"] = "no-store"
        self.f.change("performance")
        self.blocked("no-store")

    def test_public_g5_never_waived(self):
        self.f = Fixture(approval("G5-performance"))
        self.f.ledger["exceptions"] = ["EXPLICIT-FIXTURE-APPROVAL"]
        self.f.save()
        self.blocked("public G5 cannot be waived")

    def test_duplicate_json_key(self):
        put(self.f.ledger_path, b'{"schema":1,"schema":2}\n')
        self.blocked("Duplicate JSON")

    def test_symlink_evidence(self):
        alias = self.f.directory / "alias.json"
        alias.symlink_to(self.f.directory / "browser/report.json")
        self.f.ledger["evidence"]["browser"]["path"] = str(alias)
        self.f.save()
        self.blocked("symlink")

    def test_hardlink_evidence(self):
        alias = self.f.directory / "hard.json"
        os.link(self.f.directory / "browser/report.json", alias)
        self.blocked("hardlinked")


def run_suite(suite: unittest.TestSuite, out: Path, label: str) -> int:
    gate.external(out, gate.OUTPUT_ROOT)
    out.mkdir(parents=True, exist_ok=False)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    rc = 0 if result.wasSuccessful() and result.testsRun > 0 else 1
    line = (f"{label} verdict={'PASS' if rc == 0 else 'FAIL'} tests={result.testsRun} "
            f"failures={len(result.failures)} errors={len(result.errors)} rc={rc} "
            f"scope=FIXTURE_NOT_RELEASE collected_at={datetime.now(timezone.utc).isoformat()} out={out}")
    gate.write_new(out / "execution.log", (stream.getvalue() + "\n" + line + "\n").encode())
    gate.write_new(out / "report.json", gate.encode({
        "verdict": "PASS" if rc == 0 else "FAIL", "tests": result.testsRun, "exit_code": rc,
        "failures": len(result.failures), "errors": len(result.errors),
        "scope": "FIXTURE_NOT_RELEASE", "production_changes": False,
        "line": line, "finished_at": datetime.now(timezone.utc).isoformat()}))
    print(line)
    if rc:
        print(stream.getvalue(), file=sys.stderr)
    return rc


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(ReleaseGateTests),
                               args.out, "OPENSIM_RELEASE_GATE_TEST"))
