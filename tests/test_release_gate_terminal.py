"""Real FILE_ONLY protocol regressions; no human, public or release certification.

Only the unchanged gate CLI and its pinned pure reader execute. All producer,
public and manual records are explicit synthetic files, not producer launches.
Run directly with --out pointing to a new external seat-sync directory.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_release_gate import Fixture, command, gate, png, put, run_suite

SCOPE = "FILE_ONLY_PROTOCOL_CONTRADICTION_NOT_RELEASE"
G3 = "G3-accessibility"
CAUSE = "PARTIAL audit cannot certify G3-accessibility"


class CurrentFixture(Fixture):
    """Reuse the file builder, then pin current tools before any consumption."""

    def __init__(self):
        super().__init__()
        name = "tools/workbench_acceptance.py"
        old_commit = self.commit
        old_hash = self.identity["tool_sha256"][name]
        body = gate.read_file(ROOT / name)
        put(self.repo / name, body)
        command(["git", "add", "--", name], self.repo)
        command(["git", "-c", "user.name=Gate Test Fixture", "-c",
                 "user.email=fixture@example.invalid", "-c", "core.hooksPath=/dev/null",
                 "commit", "-m", "CURRENT_CANDIDATE_FILE_ONLY_NOT_RELEASE"], self.repo)
        self.commit = command(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        self.identity = gate.identity(self.repo, self.commit)

        def rebind(value):
            if isinstance(value, dict):
                return {key: rebind(item) for key, item in value.items()}
            if isinstance(value, list):
                return [rebind(item) for item in value]
            if value == old_commit:
                return self.commit
            if value == old_hash:
                return gate.sha(body)
            return value

        self.reports = rebind(self.reports)
        self.ledger = rebind(self.ledger)
        audit = self.reports["audit"]
        directory = self.directory / "audit"
        for width, height in ((320, 568), (768, 1024), (1064, 768), (1440, 900)):
            image = png(width, height)
            for state in ("mesh-selected", "field-unselected", "details-open-0"):
                phase = f"{width}-{state}"
                for stem in (phase, phase + "-tile-000"):
                    put(directory / (stem + ".png"), image)
                    audit["screenshots"].append({"file": stem + ".png",
                        "sha256": gate.sha(image), "width": width, "height": height,
                        "url": audit["url"], "state": stem})
                put(directory / (phase + ".coverage.json"), gate.encode({
                    "status": "PASS", "scale": 1, "horizontal_coverage": "COMPLETE",
                    "document": {"width": width, "height": height}, "vertical_coverage": height,
                    "tiles": [{"file": phase + "-tile-000.png", "document_y": 0,
                               "viewport": {"width": width, "height": height}}]}))
        audit["coverage_counts"]["screenshots"] = len(audit["screenshots"])
        for kind in tuple(self.reports):
            if not kind.startswith("launcher-"):
                self.store(kind, self.reports[kind])
        for kind in self.ledger["evidence"]:
            self.ledger["evidence"][kind] = self.pin(kind)
        for rule in self.ledger["manual"]:
            self.ledger["manual"][rule] = self.pin(rule)
        for kind in tuple(self.ledger["executions"]):
            self.launch(kind, self.reports["launcher-" + kind]["tool"])
        self.save()


def inventory(directory: Path) -> dict:
    """Observe all fixture inputs, including receipts, PNG and launcher files."""
    result = {}
    for path in sorted(directory.rglob("*")):
        if ".git" in path.relative_to(directory).parts or path.is_dir():
            continue
        body = gate.read_file(path)
        info = path.stat()
        result[str(path.relative_to(directory))] = {
            "sha256": gate.sha(body), "bytes": len(body), "dev": str(info.st_dev),
            "ino": str(info.st_ino), "mtime_ns": info.st_mtime_ns}
    return result


class FileOnlyTerminalTests(unittest.TestCase):
    evidence_root = gate.OUTPUT_ROOT / ("terminal-cases-" + uuid4().hex)

    def setUp(self):
        self.case = self.evidence_root / self._testMethodName
        self.case.mkdir(parents=True, exist_ok=False)
        self.f = CurrentFixture()
        expected = gate.identity(self.f.repo, self.f.commit)
        self.assertEqual(expected, self.f.identity)
        for name in dict.fromkeys((*gate.SOURCES, *gate.TOOLS)):
            self.assertEqual(gate.read_file(self.f.repo / name), gate.read_file(ROOT / name), name)
        self.assertEqual(set(self.f.ledger["evidence"]),
                         {"local", "numerics", "browser", "audit", "performance", "public", "page"})
        self.assertEqual(set(self.f.ledger["executions"]), set(self.f.ledger["evidence"]))
        self.assertEqual(set(self.f.ledger["gates"]), set(gate.GATES))
        self.assertEqual(set(self.f.ledger["manual"]), set(gate.MANUAL))
        self.assertEqual(self.f.ledger["exceptions"], [])
        self.assertIsNone(self.f.approval_pin)
        self.assertEqual(command(["git", "status", "--porcelain"], self.f.repo).stdout, "")
        gate.write_new(self.case / "fixture.json", gate.encode({
            "scope": SCOPE, "fixture": str(self.f.directory), "identity": expected,
            "candidate_head": command(["git", "rev-parse", "HEAD"], ROOT).stdout.strip(),
            "producer_executions": "SYNTHETIC_NOT_PRODUCER_RUN",
            "public": "SYNTHETIC_NOT_PUBLIC_EXECUTION", "human": "FIXTURE_NOT_HUMAN"}))

    def prepare(self, status: str) -> dict:
        self.f.reports["audit"]["gates"][G3]["status"] = status
        self.f.change("audit")
        before = inventory(self.f.directory)
        gate.write_new(self.case / "inputs-before.json", gate.encode(before))
        return before

    def stable(self, before: dict):
        after = inventory(self.f.directory)
        gate.write_new(self.case / "inputs-after.json", gate.encode(after))
        self.assertEqual(after, before, "Gate/reader changed fixture inputs or created side effects")
        self.assertEqual(gate.identity(self.f.repo, self.f.commit), self.f.identity)

    def test_automated_scope_reader_full_file_protocol_positive_control(self):
        before = self.prepare("AUTOMATED_SCOPE_PASS")
        observation = gate.read_audit_scope(self.f.pin("audit"), self.f.identity, 0,
                                            source_root=self.f.repo)
        gate.write_new(self.case / "reader.json", gate.encode(observation))
        self.assertEqual(observation["state"], "AUTOMATIC_SCOPE_OBSERVED")
        self.assertEqual(observation["release_verdict"], "NOT_CERTIFIED")
        self.assertFalse(observation["production_changes"])
        self.assertEqual(observation["native_gates"][G3]["status"], "AUTOMATED_SCOPE_PASS")
        dependencies = observation["artifact_validation"]
        self.assertEqual(dependencies["validator_sha256"],
                         gate.sha(gate.read_file(ROOT / "tools/workbench_acceptance.py")))
        self.assertEqual((dependencies["successful_posts"], dependencies["post_artifacts"],
                          dependencies["coverage_artifacts"], dependencies["pngs"]), (1, 3, 44, 88))
        self.assertEqual(len(dependencies["dependencies"]), 135)
        self.assertTrue(observation["remaining"])
        self.stable(before)

    def test_automated_scope_cli_reaches_g3_certification_rejection(self):
        before = self.prepare("AUTOMATED_SCOPE_PASS")
        control = gate.read_audit_scope(self.f.pin("audit"), self.f.identity, 0,
                                        source_root=self.f.repo)
        gate.write_new(self.case / "reader-positive-control.json", gate.encode(control))
        self.assertEqual(control["state"], "AUTOMATIC_SCOPE_OBSERVED")
        self.assertEqual(control["native_gates"][G3]["status"], "AUTOMATED_SCOPE_PASS")
        self.assertEqual(control["release_verdict"], "NOT_CERTIFIED")
        out = self.case / "gate.json"
        argv = [sys.executable, "-B", str(self.f.repo / "tools/release_gate.py"),
                "--source-root", str(self.f.repo), "--commit", self.f.commit,
                "--ledger", str(self.f.ledger_path), "--out", str(out)]
        process = subprocess.run(argv, cwd=self.f.repo, capture_output=True, text=True,
                                 env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        put(self.case / "launcher.stdout", process.stdout.encode())
        put(self.case / "launcher.stderr", process.stderr.encode())
        report = gate.decode(gate.read_file(out))
        gate.write_new(self.case / "launcher.json", gate.encode({
            "scope": SCOPE, "argv": argv, "rc": process.returncode,
            "stdout_sha256": gate.sha(process.stdout.encode()), "stderr_sha256": gate.sha(process.stderr.encode()),
            "report_sha256": gate.sha(gate.read_file(out)), "first_cause": report.get("failure"),
            "final_human_sentinel": "NOT_TESTED", "collected_at": datetime.now(timezone.utc).isoformat()}))
        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["verdict"], "BLOCKED")
        self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")
        self.assertEqual(report["failure"], {"type": "GateError", "reason": CAUSE})
        self.assertFalse(report["production_changes"])
        self.assertEqual(report["finalization"], "FINAL_PROCESS_RC_REQUIRED")
        log = gate.read_file(out.with_suffix(".log"))
        self.assertEqual(report["execution_log"]["sha256"], gate.sha(log))
        self.assertIn(CAUSE.encode(), log)
        self.assertIn("verdict=BLOCKED rc=1", process.stdout)
        self.assertNotIn("trusted human-acceptance verifier unavailable", log.decode())
        self.assertFalse(list(self.case.glob("*.failure-*.json")))
        self.assertEqual(set(path.name for path in self.case.iterdir()), {
            "fixture.json", "inputs-before.json", "gate.json", "gate.log", "launcher.stdout",
            "launcher.stderr", "launcher.json",
            "reader-positive-control.json"})
        self.stable(before)


def sources() -> dict:
    names = set((*gate.SOURCES, *gate.TOOLS, "tests/test_release_gate.py",
                 "tests/test_release_gate_terminal.py", "docs/testing/RELEASE_GATE_TERMINAL.md"))
    return {name: gate.sha(gate.read_file(ROOT / name)) for name in sorted(names)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    before = sources()
    head_before = command(["git", "rev-parse", "HEAD"], ROOT).stdout.strip()
    FileOnlyTerminalTests.evidence_root = args.out / "cases"
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(FileOnlyTerminalTests)
    rc = run_suite(suite, args.out, "OPENSIM_FILE_ONLY_TERMINAL_TEST")
    after = sources()
    stable = before == after and head_before == command(["git", "rev-parse", "HEAD"], ROOT).stdout.strip()
    report = gate.decode(gate.read_file(args.out / "report.json"))
    gate.write_new(args.out / "source-binding.json", gate.encode({
        "scope": SCOPE, "candidate_head": head_before, "source_before": before,
        "source_after": after, "source_stable": stable, "suite_rc": rc,
        "tests": report["tests"], "failures": report["failures"], "errors": report["errors"],
        "final_human_sentinel": "NOT_TESTED", "original_goal": "UNSATISFIABLE_FINAL_PATH",
        "production_changes": False, "collected_at": datetime.now(timezone.utc).isoformat()}))
    if not stable:
        print("SOURCE_DRIFT: candidate or protocol files changed", file=sys.stderr)
        return 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
