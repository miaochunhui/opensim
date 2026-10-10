"""Read real peer handoff documents; fixtures never send or sign a response.

Use --out for the existing append-only run_suite logger. No local integration,
solver, browser, remote command or peer repository is executed or modified.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from test_release_gate import gate, run_suite

DIRECTORY = Path("/data/dev/infra/docs/handoff")
CHECKER = Path("/data/dev/infra/ops/opensim-coordination/check_handoff.py")
INDEX = "20261010-opensim-coordination.md"
REQUESTS = {
    "OpenCAD": ("20261010-opensim-to-opencad-requests.md",
                ["OSIM-CAD-001", "OSIM-CAD-002", "OSIM-CAD-003"]),
    "openopt": ("20261010-opensim-to-openopt-requests.md",
                ["OSIM-OPT-001", "OSIM-OPT-002", "OSIM-OPT-003"]),
}
STATUS = ROOT / "docs/integration/HANDOFF_STATUS_20261011.md"
ARTIFACTS = gate.ARTIFACT_ROOT / "authorized-s5-integration-handoff-20261011"
OUT = None

spec = importlib.util.spec_from_file_location("integration_handoff_checker", CHECKER)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def observation(path: Path) -> dict:
    if not os.path.lexists(path):
        return {"state": "ABSENT"}
    body = gate.read_file(path)
    info = path.lstat()
    return {"state": "PRESENT", "sha256": gate.sha(body), "bytes": len(body),
            "dev": info.st_dev, "ino": info.st_ino, "mtime_ns": info.st_mtime_ns,
            "nlink": info.st_nlink}


def inventory() -> dict:
    counts = checker.validate(DIRECTORY)
    rows = []
    for owner, (name, ids) in REQUESTS.items():
        metadata, _ = checker.read_document(DIRECTORY / name)
        reply = DIRECTORY / metadata["response_file"]
        decisions = {}
        if reply.exists():
            response, _ = checker.read_document(reply)
            decisions = {item["id"]: item["decision"] for item in response["decisions"]}
        for request_id in ids:
            rows.append({"id": request_id, "recipient": owner,
                         "request_status": metadata["status"],
                         "response_path": str(reply),
                         "decision": decisions.get(request_id, "NOT_RECEIVED"),
                         "agreement": "NOT_VERIFIED", "implementation": "NOT_VERIFIED",
                         "joint_acceptance": "NOT_VERIFIED"})
    return {**counts, "items": rows, "scope": "DOCUMENT_INTEGRITY_ONLY"}


def source_snapshot() -> dict:
    paths = {Path(__file__).resolve(), STATUS, CHECKER,
             Path(sys.modules["test_release_gate"].__file__).resolve(),
             Path(gate.__file__).resolve(), ROOT / "tools/integration_acceptance.py",
             ROOT / "docs/INTEGRATION.md", ROOT / "docs/CONTRACT.md", DIRECTORY / INDEX}
    for name, _ in REQUESTS.values():
        metadata, _ = checker.read_document(DIRECTORY / name)
        paths.add(DIRECTORY / name)
        paths.add(DIRECTORY / metadata["response_file"])
    return {str(path): observation(path) for path in sorted(paths)}


class IntegrationHandoffTests(unittest.TestCase):
    def setUp(self):
        self.storage_root = gate.OUTPUT_ROOT
        gate.OUTPUT_ROOT = ARTIFACTS
        self.addCleanup(setattr, gate, "OUTPUT_ROOT", self.storage_root)

    def fixture(self) -> Path:
        base = OUT if OUT is not None else gate.OUTPUT_ROOT
        directory = base / ("handoff-fixture-" + uuid4().hex)
        directory.mkdir(parents=True, exist_ok=False)
        for name in (INDEX, *(row[0] for row in REQUESTS.values())):
            body = gate.read_file(DIRECTORY / name)
            gate.write_new(directory / name, body)
            self.assertEqual((directory / name).read_bytes(), body)
            text = body.decode("utf-8")
            for target in checker.LINK.findall(text):
                linked = directory / target.split("#", 1)[0]
                if not linked.exists() and linked.name != INDEX and linked.name not in {
                    row[0] for row in REQUESTS.values()
                }:
                    gate.write_new(linked, b"TEST_ONLY local link target; not historical evidence.\n")
        return directory

    def child(self, directory: Path, expected_rc: int) -> subprocess.CompletedProcess:
        self.assertNotEqual(os.geteuid(), 0, "Tests must run as nonroot infra")
        result = subprocess.run(
            [sys.executable, "-B", str(CHECKER), "--handoff-dir", str(directory)],
            stdin=subprocess.DEVNULL, capture_output=True, cwd=ROOT,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
        base = OUT if OUT is not None else gate.OUTPUT_ROOT
        log = base / ("handoff-child-" + uuid4().hex + ".json")
        gate.write_new(log, gate.encode({"actual_rc": result.returncode,
                       "stdout": result.stdout.decode("utf-8"),
                       "stderr": result.stderr.decode("utf-8"),
                       "handoff_dir": str(directory), "checker": observation(CHECKER)}))
        self.assertEqual(result.returncode, expected_rc, str(log))
        return result

    def test_real_three_documents_and_six_requests(self):
        metadata, _ = checker.read_document(DIRECTORY / INDEX)
        self.assertEqual(metadata["request_files"], [row[0] for row in REQUESTS.values()])
        for owner, (name, ids) in REQUESTS.items():
            value, body = checker.read_document(DIRECTORY / name)
            self.assertEqual(value["recipient"], owner)
            self.assertEqual(value["requests"], ids)
            self.assertEqual(re.findall(r"^## (OSIM-[A-Z]+-\d{3})$", body, re.M), ids)
            self.assertEqual(value["status"], "PROPOSED")
        value = inventory()
        self.assertEqual((value["documents"], value["requests"]), (3, 6))
        self.assertEqual(len(value["items"]), 6)
        self.assertEqual(value["unanswered"], sum(
            row["decision"] == "NOT_RECEIVED" for row in value["items"]))
        self.assertEqual(value["receipts"], sum(
            state == "RECEIVED_NOT_VERIFIED" for state in value["responses"].values()))

    def test_real_checker_cli_and_receipt_inventory(self):
        before = source_snapshot()
        value = inventory()
        result = self.child(DIRECTORY, 0)
        text = result.stdout.decode("utf-8")
        for key in ("documents", "requests", "receipts", "unanswered"):
            self.assertIn(f"{key}={value[key]}", text)
        self.assertIn("agreement=NOT_VERIFIED implementation=NOT_VERIFIED", text)
        for owner, state in value["responses"].items():
            self.assertIn(f"recipient={owner} state={state}", text)
        self.assertEqual(source_snapshot(), before)

    def test_real_partial_receipt_never_implementation(self):
        directory = self.fixture()
        metadata, _ = checker.read_document(directory / REQUESTS["OpenCAD"][0])
        response = {"kind": "response", "version": 1, "owner": "OpenCAD",
                    "decisions": [{"id": "OSIM-CAD-001", "decision": "ACCEPT"}]}
        body = "<!-- opensim-coordination\n" + json.dumps(response) + "\n-->\n"
        gate.write_new(directory / metadata["response_file"], body.encode())
        value = checker.validate(directory)
        self.assertEqual((value["receipts"], value["unanswered"]), (1, 5))
        self.assertEqual(value["responses"]["OpenCAD"], "RECEIVED_NOT_VERIFIED")
        result = self.child(directory, 0)
        self.assertIn(b"agreement=NOT_VERIFIED implementation=NOT_VERIFIED", result.stdout)
        self.assertIn(b"receipts=1 unanswered=5", result.stdout)

    def test_real_cli_rejects_invalid_receipt(self):
        for field, bad in (("owner", "openopt"), ("id", "OSIM-OPT-001"),
                           ("decision", "VERIFIED")):
            with self.subTest(field=field):
                directory = self.fixture()
                metadata, _ = checker.read_document(directory / REQUESTS["OpenCAD"][0])
                response = {"kind": "response", "version": 1, "owner": "OpenCAD",
                            "decisions": [{"id": "OSIM-CAD-001", "decision": "ACCEPT"}]}
                if field == "owner":
                    response[field] = bad
                else:
                    response["decisions"][0][field] = bad
                body = "<!-- opensim-coordination\n" + json.dumps(response) + "\n-->\n"
                gate.write_new(directory / metadata["response_file"], body.encode())
                result = self.child(directory, 1)
                self.assertIn(b"OPENSIM_HANDOFF verdict=FAIL", result.stderr)
                self.assertNotIn(b"verdict=PASS", result.stdout)

    def test_real_cli_rejects_request_damage(self):
        for field, bad in (("version", 2), ("requests", []), ("status", "VERIFIED")):
            with self.subTest(field=field):
                directory = self.fixture()
                path = directory / REQUESTS["OpenCAD"][0]
                metadata, body = checker.read_document(path)
                metadata[field] = bad
                text = checker.MARKER.sub(
                    lambda _: "<!-- opensim-coordination\n" + json.dumps(metadata) + "\n-->",
                    body, count=1)
                # Rewrite only this test-owned copied file; never the live handoff.
                path.write_text(text, encoding="utf-8")
                self.assertEqual(path.read_text(encoding="utf-8"), text)
                self.assertIn(b"OPENSIM_HANDOFF verdict=FAIL", self.child(directory, 1).stderr)

    def test_v1_scope_and_local_integration_are_not_peer_protocols(self):
        text = gate.read_file(ROOT / "docs/INTEGRATION.md").decode("utf-8")
        expanded = re.sub(r"(OSIM-[A-Z]+-)(\d{3})/(\d{3})",
                          lambda match: match[1] + match[2] + " " + match[1] + match[3], text)
        for request_id in [item for row in REQUESTS.values() for item in row[1]]:
            self.assertIn(request_id, expanded)
        tool = gate.read_file(ROOT / "tools/integration_acceptance.py").decode("utf-8")
        self.assertIn("LOCAL_INTEGRATION_NOT_RELEASE", tool)
        self.assertIn("NOT_CERTIFIED", tool)
        self.assertIn("tools/acceptance.py", tool)
        self.assertNotIn("opensim-coordination", tool)

    def test_status_document_is_bounded_executable(self):
        text = gate.read_file(STATUS).decode("utf-8")
        blocks = re.findall(r"<!-- opensim-handoff-status\s*\n(.*?)\n-->", text, re.S)
        self.assertEqual(len(blocks), 1)
        value = json.loads(blocks[0])
        self.assertEqual(value["schema"], "opensim-handoff-status-v1")
        self.assertEqual(value["scope"], "FILE_HANDOFF_NOT_AGREEMENT")
        self.assertEqual(value["snapshot_counts"],
                         {"documents": 3, "requests": 6, "receipts": 0, "unanswered": 6})
        self.assertEqual(value["request_ids"], [item for row in REQUESTS.values() for item in row[1]])
        self.assertEqual(value["linear_system_status"], "NOT_REQUESTED_NOT_AGREED")
        self.assertEqual(value["source_sha256"], {
            name: gate.sha(gate.read_file(DIRECTORY / name))
            for name in (INDEX, *(row[0] for row in REQUESTS.values()))})
        for marker in ("check_handoff.py", "test_integration_handoff.py", "NOT_VERIFIED",
                       "A x = b", "LP/QP", "nodes_mm", "boundary_nodes"):
            self.assertIn(marker, text)


def main() -> int:
    global OUT
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--select", action="append")
    args = parser.parse_args()
    # Configure only this test process; never edit the shared gate implementation.
    gate.OUTPUT_ROOT = ARTIFACTS
    OUT = gate.external(args.out, gate.OUTPUT_ROOT)
    loader = unittest.defaultTestLoader
    if args.select:
        allowed = loader.getTestCaseNames(IntegrationHandoffTests)
        if any(name not in allowed for name in args.select) or len(set(args.select)) != len(args.select):
            parser.error("Unknown or duplicate test selector")
        suite = unittest.TestSuite(IntegrationHandoffTests(name) for name in args.select)
    else:
        suite = loader.loadTestsFromTestCase(IntegrationHandoffTests)
    before = source_snapshot()
    current = inventory()
    rc = run_suite(suite, OUT, "INTEGRATION_HANDOFF_TESTS")
    after = source_snapshot()
    stable = before == after
    gate.write_new(OUT / "handoff-inventory.json", gate.encode({
        "collected_at": datetime.now(timezone.utc).isoformat(), "inventory": current,
        "source_before": before, "source_after": after, "source_stable": stable,
        "actual_suite_rc": rc, "exit_code": rc if stable else 1,
        "agreement": "NOT_VERIFIED", "implementation": "NOT_VERIFIED",
        "joint_acceptance": "NOT_VERIFIED", "production_changes": False}))
    print(f"HANDOFF_READBACK source_stable={stable} actual_suite_rc={rc} out={OUT}")
    return rc if stable else 1


if __name__ == "__main__":
    raise SystemExit(main())
