"""Real file/Git negative controls; fixture success is NOT product acceptance.

Direct execution retains files, logs and unittest counts in a new external root.
No producer is mocked into a production PASS, and no dependencies are installed.
"""

from __future__ import annotations

import copy
import io
import json
import logging
import os
import struct
import subprocess
import sys
import threading
import unittest
import uuid
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import workbench_acceptance as gate


def png(width: int = 3, height: int = 2) -> bytes:
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(
            ">I", zlib.crc32(kind + payload) & 0xffffffff)
    rows = b"".join(b"\0" + bytes([17, 107, 96]) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def command(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Fixture git {args[0]} rc={result.returncode}: {result.stderr}")
    return result.stdout.strip()


class LogConflictStore(gate.DurableStore):
    """Cause an actual exclusive-create failure after the provisional write."""

    def create(self, path, data):
        if path.name == "workbench.log":
            path.mkdir()
        super().create(path, data)


class LateReadFailureStore(gate.DurableStore):
    """Temporarily withdraw an owned pathname: actual open raises ENOENT."""

    def __init__(self, target):
        super().__init__()
        self.target = target
        self.reads = 0

    def verify(self, path, expected):
        if path.name == self.target:
            self.reads += 1
            if self.reads == 2:
                preserved = path.parent / ("io-fault-held-" + path.name)
                path.rename(preserved)
                try:
                    super().verify(path, expected)
                finally:
                    preserved.rename(path)
                return
        super().verify(path, expected)


class FinalSyncFailureStore(gate.DurableStore):
    """Perform fsync on a real closed fd at final directory durability."""

    def sync_directory(self, path):
        if path / "workbench.json" in self.owned:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            os.close(fd)
            os.fsync(fd)
        super().sync_directory(path)


class ChangedReportStore(gate.DurableStore):
    """Replace or modify actual final bytes, never alter a verdict in memory."""

    def __init__(self, replace):
        super().__init__()
        self.replace = replace
        self.changed = None

    def verify(self, path, expected):
        if path.name == "workbench.json" and self.changed is None:
            self.changed = path
            if self.replace:
                path.rename(path.parent / "preserved-original-final.json")
                path.write_bytes(b'{"owner":"other-file","verdict":"UNKNOWN"}\n')
            else:
                with path.open("ab") as stream:
                    stream.write(b"\nEXTERNAL_CHANGE\n")
        super().verify(path, expected)


class WorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = gate.new_output(gate.EXTERNAL / ("workbench-files-" + uuid.uuid4().hex))
        cls.root = cls.out / "candidate"
        cls.root.mkdir()
        for relative in (*gate.LOCAL_SOURCES, "tools/integration_acceptance.py",
                         *(f"tools/{item[0]}" for item in gate.PRODUCERS.values())):
            path = cls.root / relative
            path.parent.mkdir(exist_ok=True)
            path.write_text(f"# FILE_PARSER_FIXTURE_NOT_PRODUCT {relative}\n", encoding="ascii")
        command(cls.root, "init", "-b", "codex/file-fixture")
        command(cls.root, "add", "--", *gate.LOCAL_SOURCES, "tools/integration_acceptance.py",
                *(f"tools/{item[0]}" for item in gate.PRODUCERS.values()))
        command(cls.root, "-c", "user.name=OpenSim file fixture", "-c", "user.email=fixture@invalid",
                "-c", "core.hooksPath=/dev/null", "commit", "-m", "File fixture, not production acceptance")
        cls.candidate = gate.inventory(cls.root)
        gate.clean(cls.candidate)
        cls.url = "http://127.0.0.1:12345/"
        cls.fixture_records = []

    @classmethod
    def tearDownClass(cls):
        gate.write_json(cls.out / "fixture-manifest.json", {
            "scope": "FILE_FIXTURES_NOT_PRODUCT_OR_RELEASE", "files": cls.fixture_records,
            "candidate": cls.candidate, "collected_at": gate.now()})

    def directory(self):
        directory = self.out / (self._testMethodName + "-" + uuid.uuid4().hex)
        directory.mkdir()
        return directory

    def save(self, directory, value, filename="report.json"):
        path = directory / filename
        path.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
        readback, record = gate.read_report(path, directory)
        self.assertEqual(readback, value)
        self.assertEqual(record["sha256"], gate.sha(path.read_bytes()))
        self.fixture_records.append(record)
        return readback

    def browser(self, directory):
        image = png()
        (directory / "fixture.png").write_bytes(image)
        sources = gate.source_map(self.candidate)
        self.save(directory, {"status": 200, "result": {
            "mesh": {"nodes_mm": [[0, 0], [1, 0], [0, 1]]},
            "fields": {"velocity_m_s": [0, 1, 0]},
            "reproducibility": {"source_sha256": sources, "solver_sha256": sources["solver.py"],
                                "computed_at": "2026-10-10T00:00:00Z"}}}, "solve-001.json")
        return {
            "schema_version": 1, "tool": "opensim-browser-check",
            "verdict": "AUTOMATED_PASS", "exit_code": 0,
            "started_at": "2026-10-10T00:00:00Z", "finished_at": "2026-10-10T00:01:00Z",
            "mode": "OPENSIM_ACCEPTANCE", "release_verdict": "NOT_CERTIFIED",
            "commit": self.candidate["commit"],
            "tool_sha256": gate.sha((self.root / "tools/browser_check.mjs").read_bytes()),
            "target_url": self.url, "checks": [{"status": "PASS"}],
            "matrix": [{"name": name, "status": "PASS"} for name in sorted(gate.VIEWPORTS)],
            "result_count": 1, "page_build": {"build_text": gate.page_build(self.root)},
            "screenshots": [{"file": "fixture.png", "sha256": gate.sha(image), "bytes": len(image),
                             "width": 3, "height": 2}],
        }

    def validate(self, value, directory, rc=0, name="browser"):
        value = self.save(directory, value)
        return gate.validate_producer(name, value, rc, self.root, self.candidate, directory, self.url)

    def local(self):
        return {"verdict": "PASS", "steps": [{"rc": 0, "stderr": "\nRan 2 tests in 0.1s\n\nOK\n"}] +
                [{"rc": 0} for _ in range(4)],
                "source_sha256": {name: self.candidate["tracked_sha256"][name] for name in gate.LOCAL_SOURCES}}

    def test_valid_real_file_parser_fixture_is_not_product_claim(self):
        directory = self.directory()
        result = self.validate(self.browser(directory), directory)
        self.assertEqual((result["checks"], result["solves"], len(result["pngs"])), (1, 1, 1))
        self.assertEqual(result["commit"], self.candidate["commit"])
        self.assertEqual(gate.inventory(self.root), self.candidate)

    def test_empty_missing_and_nonobject_report_rejected(self):
        directory = self.directory()
        path = directory / "empty.json"
        for payload in (b"", b"{}", b"[]", b"null", b"{"):
            with self.subTest(payload=payload):
                path.write_bytes(payload)
                with self.assertRaises((gate.EvidenceError, json.JSONDecodeError)):
                    gate.read_report(path, directory)
        with self.assertRaises(gate.EvidenceError):
            gate.read_report(directory / "missing.json", directory)

    def test_duplicate_and_nonfinite_report_rejected(self):
        directory = self.directory()
        for index, payload in enumerate((b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}')):
            path = directory / f"bad-{index}.json"
            path.write_bytes(payload)
            with self.assertRaises(gate.EvidenceError):
                gate.read_report(path, directory)

    def test_empty_checks_rejected(self):
        directory = self.directory()
        value = self.browser(directory)
        value["checks"] = []
        with self.assertRaisesRegex(gate.EvidenceError, "empty checks"):
            self.validate(value, directory)

    def test_old_commit_rejected(self):
        directory = self.directory()
        value = self.browser(directory)
        value["commit"] = "0" * 40
        with self.assertRaisesRegex(gate.EvidenceError, "stale commit"):
            self.validate(value, directory)

    def test_old_tool_rejected(self):
        directory = self.directory()
        value = self.browser(directory)
        value["tool_sha256"] = "0" * 64
        with self.assertRaisesRegex(gate.EvidenceError, "tool hash"):
            self.validate(value, directory)

    def test_old_page_build_rejected(self):
        directory = self.directory()
        value = self.browser(directory)
        value["page_build"] = {"build_text": "old"}
        with self.assertRaisesRegex(gate.EvidenceError, "Stale browser build"):
            self.validate(value, directory)

    def test_nonzero_process_rejected_even_with_pass_report(self):
        directory = self.directory()
        with self.assertRaisesRegex(gate.EvidenceError, "process rc=9"):
            self.validate(self.browser(directory), directory, 9)

    def test_missing_mismatched_or_bool_exit_code_rejected(self):
        for code in (None, 1, False):
            directory = self.directory()
            value = self.browser(directory)
            value["exit_code"] = code
            with self.assertRaisesRegex(gate.EvidenceError, "exit_code"):
                self.validate(value, directory)

    def test_running_terminal_rejected(self):
        directory = self.directory()
        value = self.browser(directory)
        value["verdict"] = "RUNNING"
        with self.assertRaisesRegex(gate.EvidenceError, "terminal verdict"):
            self.validate(value, directory)

    def test_invalid_time_rejected(self):
        directory = self.directory()
        value = self.browser(directory)
        value["finished_at"] = "2026-10-09T00:00:00Z"
        with self.assertRaisesRegex(gate.EvidenceError, "time interval"):
            self.validate(value, directory)

    def test_forged_scope_and_release_rejected(self):
        for key, bad in (("mode", "INPUT_PROBE_NOT_FULL_ACCEPTANCE"), ("release_verdict", "CERTIFIED"),
                         ("target_url", "https://opensim.ai4everyone.top/")):
            directory = self.directory()
            value = self.browser(directory)
            value[key] = bad
            with self.assertRaises(gate.EvidenceError):
                self.validate(value, directory)

    def test_partial_terminal_never_becomes_pass(self):
        directory = self.directory()
        value = self.browser(directory)
        value["verdict"] = "PARTIAL"
        with self.assertRaisesRegex(gate.EvidenceError, "PARTIAL"):
            self.validate(value, directory)

    def test_first_producer_cause_preserved_ahead_of_process_rc(self):
        directory = self.directory()
        value = self.browser(directory)
        value["verdict"] = "FAIL"
        value["exit_code"] = 2
        value["first_failure"] = {"rule": "ORIGINAL_CAUSE", "message": "first real failure"}
        value["cleanup_failure"] = {"message": "secondary"}
        with self.assertRaisesRegex(gate.EvidenceError, "ORIGINAL_CAUSE.*process_rc=2"):
            self.validate(value, directory, 2)
        self.assertEqual(gate.compact_cause({"message": "first", "traceback": "full internal stack"}),
                         {"message": "first"})

    def test_png_hash_dimensions_and_crc_rejected(self):
        for variant in ("hash", "dimensions", "crc", "truncated", "fake-header"):
            directory = self.directory()
            value = self.browser(directory)
            image = directory / "fixture.png"
            if variant == "hash":
                value["screenshots"][0]["sha256"] = "0" * 64
            elif variant == "dimensions":
                value["screenshots"][0]["width"] = 99
            else:
                raw = image.read_bytes()
                raw = raw[:-1] if variant == "truncated" else (
                    raw[:40] + b"x" + raw[41:] if variant == "crc" else raw[:33] + b"x" * 100)
                image.write_bytes(raw)
                value["screenshots"][0].update(sha256=gate.sha(raw), bytes=len(raw))
            with self.subTest(variant=variant), self.assertRaises(gate.EvidenceError):
                self.validate(value, directory)

    def test_png_zlib_and_scanlines_rejected_despite_valid_crc(self):
        directory = self.directory()
        raw = png()
        length = int.from_bytes(raw[33:37], "big")
        payload = b"x" * length
        raw = raw[:41] + payload + struct.pack(">I", zlib.crc32(b"IDAT" + payload) & 0xffffffff) + raw[45 + length:]
        path = directory / "bad.png"
        path.write_bytes(raw)
        with self.assertRaises((gate.EvidenceError, zlib.error)):
            gate.png_record({"file": path.name, "sha256": gate.sha(raw), "width": 3, "height": 2}, directory)

    def test_missing_png_or_missing_solve_rejected(self):
        for missing in ("fixture.png", "solve-001.json"):
            directory = self.directory()
            value = self.browser(directory)
            # Rename owned fixture rather than remove evidence.
            (directory / missing).rename(directory / ("preserved-" + missing))
            with self.assertRaises(gate.EvidenceError):
                self.validate(value, directory)

    def test_stale_real_post_source_rejected(self):
        directory = self.directory()
        value = self.browser(directory)
        path = directory / "solve-001.json"
        call = json.loads(path.read_bytes())
        call["result"]["reproducibility"]["source_sha256"]["solver.py"] = "0" * 64
        path.write_text(json.dumps(call), encoding="utf-8")
        with self.assertRaisesRegex(gate.EvidenceError, "Source identity"):
            self.validate(value, directory)

    def test_local_source_count_and_rc_rejected(self):
        for variant in ("source", "count", "rc", "empty-tests"):
            value = self.local()
            if variant == "source":
                value["source_sha256"]["solver.py"] = "0" * 64
            elif variant == "count":
                value["steps"].pop()
            elif variant == "rc":
                value["steps"][1]["rc"] = 1
            else:
                value["steps"][0]["stderr"] = "Ran 0 tests\nOK"
            value = self.save(self.directory(), value)
            with self.assertRaises(gate.EvidenceError):
                gate.validate_local(value, self.candidate)

    def test_dirty_untracked_and_changed_inventory_is_real_git(self):
        directory = self.directory()
        for name, content in (("new-untracked.txt", b"untracked\n"), ("solver.py", b"modified\n")):
            relative = self.root / name
            original = relative.read_bytes() if relative.exists() else None
            relative.write_bytes(content)
            current = gate.inventory(self.root)
            self.assertIn(name, current["tracked_sha256"])
            self.assertNotEqual(current, self.candidate)
            with self.assertRaisesRegex(gate.EvidenceError, "dirty/untracked"):
                gate.clean(current)
            if original is None:
                relative.rename(directory / name)
            else:
                relative.write_bytes(original)
        self.assertEqual(gate.inventory(self.root), self.candidate)

    def test_missing_tools_fail_before_local_or_server_with_first_cause(self):
        directory = self.directory()
        root = directory / "missing-candidate"
        root.mkdir()
        (root / "README.md").write_text("not a production candidate\n", encoding="ascii")
        command(root, "init", "-b", "codex/missing-fixture")
        command(root, "add", "--", "README.md")
        command(root, "-c", "user.name=fixture", "-c", "user.email=fixture@invalid",
                "-c", "core.hooksPath=/dev/null", "commit", "-m", "missing tool fixture")
        result = gate.run(root, directory)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertEqual(result["first_cause"]["coordinate"], "preflight")
        self.assertIn("Missing producer tools", result["first_cause"]["message"])
        self.assertEqual(result["steps"], [])
        self.assertNotIn("owned_server", result)
        gate.write_json(directory / "result.json", result)

    def test_external_root_reuse_and_symlink_rejected(self):
        directory = self.directory()
        with self.assertRaises(FileExistsError):
            gate.new_output(directory)
        link = directory / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(gate.EvidenceError, "symlink"):
            gate.read_report(link / "solver.py", directory)
        with self.assertRaisesRegex(gate.EvidenceError, "outside external"):
            gate.bounded(ROOT / "unexpected-output")

    def test_empty_artifact_and_explicit_negative_input_distinguished(self):
        directory = self.directory()
        (directory / "bad-empty.json").write_bytes(b"")
        (directory / "diagnostic.log").write_bytes(b"")
        rows = gate.artifacts(directory, "ui")
        self.assertEqual({row["kind"] for row in rows}, {"NEGATIVE_INPUT", "EMPTY_DIAGNOSTIC"})
        with self.assertRaisesRegex(gate.EvidenceError, "Empty evidence"):
            gate.artifacts(directory, "audit")

    def test_chromium_internal_symlinks_are_not_acceptance_artifacts(self):
        directory = self.directory()
        profile = directory / "chromium-profile"
        profile.mkdir()
        (profile / "SingletonLock").symlink_to("owned-runtime-only")
        (directory / "report.json").write_text('{"scope":"fixture"}', encoding="ascii")
        rows = gate.artifacts(directory, "browser")
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["path"].endswith("report.json"))

    def test_output_roots_and_real_cli_fields_are_exact(self):
        self.assertEqual({name: spec[1] for name, spec in gate.PRODUCERS.items()},
                         {"browser": "browser", "audit": "seat-audit", "perf": "seat-perf", "ui": "seat-ui"})
        text = (ROOT / "tools/workbench_acceptance.py").read_text(encoding="utf-8")
        self.assertIn('"--url", url,\n                    "--out"', text)
        self.assertNotIn('"--output"', text)

    def test_durable_snapshots_do_not_promote_running_or_overwrite_report(self):
        directory = self.directory()
        value = {"verdict": "RUNNING", "steps": []}
        gate.checkpoint(directory, value, "producer-start")
        value["steps"].append({"rc": 7})
        value["verdict"] = "FAIL"
        gate.checkpoint(directory, value, "terminal")
        first, first_record = gate.read_report(directory / "state-001.json", directory)
        final, _ = gate.read_report(directory / "state-002.json", directory)
        self.assertEqual(first["report"]["verdict"], "PROVISIONAL_NOT_FINAL")
        self.assertEqual(first["report"]["candidate_verdict"], "RUNNING")
        self.assertEqual(first["report"]["steps"], [])
        self.assertEqual(final["report"]["candidate_verdict"], "FAIL")
        path = directory / "workbench.json"
        gate.write_json(path, value)
        with self.assertRaises(FileExistsError):
            gate.write_json(path, {"verdict": "PASS"})
        self.assertEqual(gate.read_report(directory / "state-001.json", directory)[1], first_record)

    def terminal_fixture(self, failure=False):
        report = {"verdict": "FAIL" if failure else gate.PASS, "exit_code": 1 if failure else 0,
                  "counts": {"producers_validated": 4, "checks": 4, "pngs": 4},
                  "finished_at": gate.now(), "scope": "PERSISTENCE_FILE_FIXTURE_NOT_PRODUCT"}
        if failure:
            report["first_cause"] = {"rule": "ORIGINAL_RUN_CAUSE", "message": "original producer failure"}
        return report

    def test_terminal_published_only_after_durable_log_and_exact_readback(self):
        directory = self.directory()
        value = gate.persist_result(directory, self.terminal_fixture())
        provisional, _ = gate.read_report(directory / "workbench.provisional.json", directory)
        final, _ = gate.read_report(directory / "workbench.json", directory)
        self.assertEqual(provisional["verdict"], "PROVISIONAL_NOT_FINAL")
        self.assertIsNone(provisional["exit_code"])
        self.assertEqual(final, value)
        self.assertEqual(final["durability"]["state"], "FINAL_AFTER_DURABLE_LOG")
        log = (directory / "workbench.log").read_bytes()
        self.assertTrue(log.startswith(b"PROVISIONAL_LOG_NOT_TERMINAL\n"))
        self.assertEqual(final["durability"]["log"]["sha256"], gate.sha(log))
        self.assertEqual(gate.read_final(directory), final)

    def test_real_log_creation_failure_never_leaves_terminal_pass(self):
        directory = self.directory()
        with self.assertRaises(gate.PersistenceError) as raised:
            gate.persist_result(directory, self.terminal_fixture(), LogConflictStore())
        self.assertFalse((directory / "workbench.json").exists())
        self.assertTrue((directory / "workbench.log").is_dir())
        provisional, _ = gate.read_report(directory / "workbench.provisional.json", directory)
        self.assertEqual(provisional["verdict"], "PROVISIONAL_NOT_FINAL")
        self.assertEqual(raised.exception.report["exit_code"], 2)
        marker, _ = gate.read_report(raised.exception.path, directory)
        self.assertEqual(marker["verdict"], "FAIL")

    def test_real_late_final_and_log_read_errors_revoke_exact_owned_pass(self):
        for target in ("workbench.json", "workbench.log"):
            directory = self.directory()
            with self.assertRaises(gate.PersistenceError) as raised:
                gate.persist_result(directory, self.terminal_fixture(), LateReadFailureStore(target))
            self.assertFalse((directory / "workbench.json").exists())
            report = raised.exception.report
            self.assertEqual(report["persistence_failure"]["type"], "FileNotFoundError")
            self.assertEqual(report["verdict"], "FAIL")
            self.assertEqual(report["exit_code"], 2)
            self.assertEqual(len(report["revocations"]), 1)
            revoked = report["revocations"][0]
            self.assertEqual(gate.sha(Path(revoked["preserved"]).read_bytes()), revoked["sha256"])
            self.assertNotIn("revocation_failure", report)
            marker, _ = gate.read_report(raised.exception.path, directory)
            self.assertEqual(marker["durability"]["state"], "FAILED_NOT_CONSUMABLE")

    def test_real_fsync_error_revokes_final_and_preserves_run_first_cause(self):
        directory = self.directory()
        with self.assertRaises(gate.PersistenceError) as raised:
            gate.persist_result(directory, self.terminal_fixture(True), FinalSyncFailureStore())
        self.assertFalse((directory / "workbench.json").exists())
        report = raised.exception.report
        self.assertEqual(report["first_cause"]["rule"], "ORIGINAL_RUN_CAUSE")
        self.assertEqual(report["persistence_failure"]["type"], "OSError")
        self.assertEqual(len(report["revocations"]), 1)
        self.assertEqual(report["exit_code"], 2)

    def test_unknown_inode_or_changed_bytes_are_never_revoked_or_overwritten(self):
        for replace in (True, False):
            directory = self.directory()
            with self.assertRaises(gate.PersistenceError) as raised:
                gate.persist_result(directory, self.terminal_fixture(), ChangedReportStore(replace))
            current = (directory / "workbench.json").read_bytes()
            self.assertIn("revocation_failure", raised.exception.report)
            self.assertEqual(raised.exception.report["revocations"], [])
            self.assertEqual(raised.exception.report["exit_code"], 2)
            if replace:
                self.assertEqual(current, b'{"owner":"other-file","verdict":"UNKNOWN"}\n')
            else:
                self.assertTrue(current.endswith(b"\nEXTERNAL_CHANGE\n"))

    def test_preexisting_terminal_and_output_conflicts_preserve_original_bytes(self):
        for name in ("workbench.json", "workbench.log"):
            directory = self.directory()
            old = directory / name
            old.write_bytes(b"OLD_UNKNOWN_ASSET\n")
            original = old.stat()
            with self.assertRaises(gate.PersistenceError) as raised:
                gate.persist_result(directory, self.terminal_fixture())
            self.assertEqual(old.read_bytes(), b"OLD_UNKNOWN_ASSET\n")
            self.assertEqual(old.stat().st_ino, original.st_ino)
            self.assertEqual(raised.exception.report["revocations"], [])
            self.assertFalse((directory / "workbench.provisional.json").exists())
            with self.assertRaises(FileExistsError):
                gate.new_output(directory)

    def test_final_consumer_rejects_marker_log_drift_and_provisional(self):
        for variant in ("marker", "log", "provisional"):
            directory = self.directory()
            gate.persist_result(directory, self.terminal_fixture())
            if variant == "marker":
                gate.write_json(directory / "persistence-failure-fixture.json", {"verdict": "FAIL"})
            elif variant == "log":
                with (directory / "workbench.log").open("ab") as stream:
                    stream.write(b"external drift\n")
            else:
                path = directory / "workbench.json"
                path.rename(directory / "preserved-final.json")
                gate.write_json(path, {"verdict": "PROVISIONAL_NOT_FINAL"})
            with self.assertRaises(gate.EvidenceError):
                gate.read_final(directory)

    def audit(self, directory):
        browser = self.browser(directory)
        sources = gate.source_map(self.candidate)
        ident = {"verdict": "PASS", "url": self.url, "fixture_asset_override": False,
                 "local_source_sha256": sources, "committed_source_sha256": sources,
                 "health": {"source_sha256": sources, "page_build": gate.page_build(self.root)},
                 "served_byte_sha256": {"health": "1" * 64, "/": "2" * 64}}
        post = {"payload": {"reproducibility": {"source_sha256": sources}},
                "body_sha256": "3" * 64, "bytes": 10}
        self.save(directory, post, "http-solve-000.json")
        return {**browser, "tool": "opensim-browser-audit", "url": self.url,
                "tool_sha256": gate.sha((self.root / "tools/browser_audit.mjs").read_bytes()),
                "mode": "G3_G6_AUTOMATIC_EXTENSION_NOT_RELEASE", "verdict": "AUTOMATED_SCOPE_PASS",
                "source_sha256": sources, "page_build": {"id": gate.page_build(self.root)},
                "identity_checks": [{"stage": "before", **ident}, {"stage": "after", **ident}],
                "post_source_checks": [{"artifact": "http-solve-000.json", "source_sha256": sources,
                                        "body_sha256": "3" * 64, "verdict": "PASS"}],
                "matrix": [{"width": width, "height": height}
                           for width, height in ((320, 568), (768, 1024), (1064, 768), (1440, 900))],
                "coverage": [{"rule": "WEB-A11Y-009", "status": "NOT_TESTED"},
                             {"rule": "WEB-A11Y-001", "status": "PARTIAL"}],
                "gates": {"G3-accessibility": {"status": "PARTIAL"},
                          "G6-content": {"status": "PASS"}}}

    def test_audit_real_report_partial_produces_nonzero_not_release(self):
        directory = self.directory()
        result = self.validate(self.audit(directory), directory, name="audit")
        self.assertEqual(len(result["remaining"]), 3)
        verdict, rc = gate.automation_verdict(result["remaining"])
        self.assertEqual((verdict, rc), ("LOCAL_AUTOMATION_PARTIAL_NOT_RELEASE", 3))
        self.assertNotEqual(rc, 0)
        self.assertEqual(gate.automation_verdict([]), ("LOCAL_AUTOMATION_PASS_NOT_RELEASE", 0))

    def test_audit_smoke_source_and_failed_coverage_rejected(self):
        for variant in ("smoke", "source", "coverage", "post"):
            directory = self.directory()
            value = self.audit(directory)
            if variant == "smoke":
                value["mode"] = "AUDIT_SMOKE_NOT_ACCEPTANCE"
            elif variant == "source":
                value["source_sha256"] = {"solver.py": "0" * 64}
            elif variant == "coverage":
                value["coverage"][0]["status"] = "FAIL"
            else:
                value["post_source_checks"][0]["artifact"] = "missing.json"
            with self.subTest(variant=variant), self.assertRaises(gate.EvidenceError):
                self.validate(value, directory, name="audit")

    def test_perf_local_scope_and_fixed_png_without_schema_dimensions(self):
        directory = self.directory()
        browser = self.browser(directory)
        image = png(360, 800)
        (directory / "result.png").write_bytes(image)
        sources = gate.source_map(self.candidate)
        value = {**browser, "tool": "opensim-browser-performance", "standard": "shared-r41",
                 "gate": "G5-performance", "scope": "LOCAL_PREFLIGHT", "g5_certified": False,
                 "tool_sha256": gate.sha((self.root / "tools/browser_performance.mjs").read_bytes()),
                 "verdict": "LOCAL_PREFLIGHT_PASS",
                 "baseline": {"width": 360, "height": 800, "samples": 1, "retries": 0},
                 "samples": [{"phase": "ONE_COLD_NAVIGATION_PLUS_THREE_CORE_TOUCHES",
                              "target_url": self.url, "content": {"solve_count": 2},
                              "source_identity": {
                                  "local_source_sha256": sources, "committed_source_sha256": sources,
                                  "solve_source_sha256": sources, "bound": True, "assets_bound": True,
                                  "page_build_seen": True,
                                  "health": {"source_sha256": sources, "page_build": gate.page_build(self.root)}},
                              "screenshot": {"file": "result.png", "sha256": gate.sha(image), "bytes": len(image)}}]}
        result = self.validate(value, directory, name="perf")
        self.assertEqual((result["pngs"][0]["width"], result["pngs"][0]["height"]), (360, 800))
        for field, bad in (("scope", "PUBLIC_LAB"), ("g5_certified", True)):
            altered = copy.deepcopy(value)
            altered[field] = bad
            with self.assertRaisesRegex(gate.EvidenceError, "scope"):
                self.validate(altered, directory, name="perf")

    def test_ui_missing_commit_is_explicit_inventory_binding_not_fabrication(self):
        directory = self.directory()
        value = self.browser(directory)
        del value["commit"]
        value.update(tool="opensim-ui-contract", mode="REAL_DOM_CDP_CONTRACT", verdict="PASS",
                     tool_sha256=gate.sha((self.root / "tools/ui_contract_test.mjs").read_bytes()),
                     sources=gate.source_map(self.candidate), actual_solver_calls=1,
                     checks=[{"verdict": "PASS"}], browser_failures=[])
        result = self.validate(value, directory, name="ui")
        self.assertEqual(result["commit_binding"], "CLEAN_BEFORE_AFTER_INVENTORY")
        self.assertNotIn("commit", value)
        for variant in ("minimal", "source"):
            altered = copy.deepcopy(value)
            if variant == "minimal":
                altered["mode"] = "MINIMAL_REGRESSION_NOT_FULL_ACCEPTANCE"
            else:
                altered["sources"]["web/app.js"] = "0" * 64
            with self.assertRaises(gate.EvidenceError):
                self.validate(altered, directory, name="ui")

    def numerics(self, directory):
        raw = {"mesh": {"nodes_mm": [[0, 0], [1, 0], [0, 1]]},
               "fields": {"velocity_m_s": [0, 1, 0]}}
        self.save(directory, raw, "raw-circle.json")
        return {"verdict": "PASS", "state": "COMPLETE", "exit_code": 0,
                "solver_root": str(self.root),
                "solver": {"sha256": self.candidate["tracked_sha256"]["solver.py"]},
                "validator_sha256": self.candidate["tracked_sha256"]["tools/validate_numerics.py"],
                "checks_executed": 5,
                "cases": [{"verdict": "PASS", "result_path": str(directory / "raw-circle.json"),
                           "result_sha256": gate.sha((directory / "raw-circle.json").read_bytes())}],
                "rejections": [{"verdict": "PASS"}],
                "convergence": {name: {"verdict": "PASS"} for name in ("circle", "rectangle", "square")}}

    def test_numerics_raw_files_source_validator_and_terminal_rejected(self):
        for variant in ("valid", "old-solver", "old-validator", "running", "failed", "empty", "raw-hash"):
            directory = self.directory()
            value = self.numerics(directory)
            if variant == "old-solver":
                value["solver"]["sha256"] = "0" * 64
            elif variant == "old-validator":
                value["validator_sha256"] = "0" * 64
            elif variant == "running":
                value["state"] = "RUNNING"
            elif variant == "failed":
                value["exit_code"] = 1
            elif variant == "empty":
                value["cases"] = []
            elif variant == "raw-hash":
                value["cases"][0]["result_sha256"] = "0" * 64
            value = self.save(directory, value)
            if variant == "valid":
                result = gate.validate_numerics(value, self.root, self.candidate)
                self.assertEqual(result["cases"], 1)
                self.assertEqual(len(result["raw_artifacts"]), 1)
            else:
                with self.subTest(variant=variant), self.assertRaises(gate.EvidenceError):
                    gate.validate_numerics(value, self.root, self.candidate)

    def integration(self, directory):
        local_dir, browser_dir = directory / "local", directory / "browser"
        local_dir.mkdir()
        browser_dir.mkdir()
        local = self.local()
        self.save(local_dir, local, "acceptance.json")
        browser = self.browser(browser_dir)
        self.save(browser_dir, browser)
        paths = {"local": local_dir / "acceptance.json", "browser": browser_dir / "report.json"}
        steps = []
        for name in ("local", "browser"):
            stdout, stderr = directory / f"{name}.stdout.log", directory / f"{name}.stderr.log"
            stdout.write_text("file fixture completed\n", encoding="ascii")
            stderr.write_bytes(b"")
            steps.append({"name": name, "rc": 0,
                          "stdout": gate.file_record(stdout, directory),
                          "stderr": gate.file_record(stderr, directory, True)})
        return {"schema_version": 1, "scope": "LOCAL_INTEGRATION_NOT_RELEASE",
                "release_verdict": "NOT_CERTIFIED", "verdict": "LOCAL_INTEGRATION_PASS",
                "before": self.candidate, "after": self.candidate,
                "tool_sha256": self.candidate["tracked_sha256"]["tools/integration_acceptance.py"],
                "steps": steps,
                "artifacts": {name: gate.file_record(path, directory) for name, path in paths.items()}}

    def test_integration_real_file_reuse_and_stale_scope_rc_hash_rejected(self):
        for variant in ("valid", "stale", "scope", "rc", "hash", "partial", "local-only"):
            directory = self.directory()
            value = self.integration(directory)
            if variant == "stale":
                value["after"] = {**self.candidate, "commit": "0" * 40}
            elif variant == "scope":
                value["scope"] = "PUBLIC_RELEASE"
            elif variant == "rc":
                value["steps"][1]["rc"] = 1
            elif variant == "hash":
                value["artifacts"]["browser"]["sha256"] = "0" * 64
            elif variant == "partial":
                value["verdict"] = "LOCAL_AUTOMATION_PARTIAL_NOT_RELEASE"
            elif variant == "local-only":
                value["steps"].pop()
            self.save(directory, value, "integration.json")
            report = {"counts": {}, "producers": {}}
            if variant == "valid":
                path = gate.reuse_integration(directory / "integration.json", self.root, self.candidate, report)
                self.assertEqual(path, directory / "local" / "numerics.json")
                self.assertTrue(report["producers"]["browser"]["reused"])
                self.assertEqual(report["counts"]["unit_tests"], 2)
            else:
                with self.subTest(variant=variant), self.assertRaises(gate.EvidenceError):
                    gate.reuse_integration(directory / "integration.json", self.root, self.candidate, report)

    def test_owned_runtime_real_http_identity_and_shutdown(self):
        from server import ApplicationServer
        candidate = gate.inventory(ROOT)
        directory = self.directory()
        app = ApplicationServer(("127.0.0.1", 0))
        thread = threading.Thread(target=app.serve_forever, daemon=True)
        handler = logging.FileHandler(directory / "server.log", encoding="utf-8")
        logging.getLogger().addHandler(handler)
        thread.start()
        try:
            url = f"http://127.0.0.1:{app.server_port}/"
            result = gate.server_identity(url, ROOT, candidate, directory, "fixture")
            self.assertEqual(result["health"]["source_sha256"], gate.source_map(candidate))
            self.assertEqual(len(result["artifacts"]), 4)
        finally:
            app.shutdown()
            app.server_close()
            thread.join(10)
            logging.getLogger().removeHandler(handler)
            handler.close()
        self.assertFalse(thread.is_alive())
        self.assertEqual(gate.inventory(ROOT), candidate)


def main():
    parser = gate.argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regression", action="store_true")
    parser.add_argument("--cli-smoke", choices=("dirty", "missing"))
    args = parser.parse_args()
    output = gate.new_output(gate.EXTERNAL / ("workbench-tests-" + uuid.uuid4().hex))
    if args.regression or args.cli_smoke:
        argv = ([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"]
                if args.regression else [sys.executable, "-B", "tools/workbench_acceptance.py",
                                         "--out", str(output / "cli")])
        step = gate.execute(argv, ROOT, output, "regression" if args.regression else "cli-smoke")
        if args.regression:
            text = Path(step["stderr"]["path"]).read_text(encoding="utf-8")
            match = gate.re.search(r"Ran ([1-9][0-9]*) tests", text)
            passed = step["rc"] == 0 and match is not None and "\nOK" in text
            detail = {"tests": int(match[1]) if match else None}
        else:
            report, record = gate.read_report(output / "cli" / "workbench.json", output)
            expected = "Candidate has dirty/untracked" if args.cli_smoke == "dirty" else "Missing producer tools"
            passed = (step["rc"] == 1 and report["verdict"] == "FAIL" and
                      expected in report["first_cause"]["message"] and not report["steps"] and
                      "owned_server" not in report)
            detail = {"expected_failure": expected, "report": record, "first_cause": report["first_cause"]}
        summary = {"verdict": "PASS" if passed else "FAIL", "collected_at": gate.now(),
                   "scope": "REGRESSION_OR_NEGATIVE_CLI_NOT_FULL_WORKBENCH", "step": step, **detail}
        gate.write_json(output / "summary.json", summary)
        print(f"WORKBENCH_VERIFICATION verdict={summary['verdict']} child_rc={step['rc']} "
              f"collected_at={summary['collected_at']} report={output / 'summary.json'}")
        if not passed:
            print("FIRST_CAUSE " + Path(step["stderr"]["path"]).read_text(encoding="utf-8")[-4000:])
        return 0 if passed else 1
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(WorkbenchTests))
    with (output / "unittest.log").open("x", encoding="utf-8") as log:
        log.write(stream.getvalue())
    summary = {"scope": "FILE_AND_HTTP_SELFTEST_NOT_FULL_WORKBENCH_OR_RELEASE",
               "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
               "skipped": len(result.skipped), "fixture_root": str(WorkbenchTests.out),
               "verdict": "PASS" if result.wasSuccessful() and result.testsRun > 0 else "FAIL",
               "collected_at": gate.now(),
               "log": gate.file_record(output / "unittest.log", output)}
    gate.write_json(output / "summary.json", summary)
    print(f"WORKBENCH_SELFTEST verdict={summary['verdict']} tests={result.testsRun} "
          f"failures={summary['failures']} errors={summary['errors']} "
          f"collected_at={summary['collected_at']} report={output / 'summary.json'}")
    if result.failures or result.errors:
        print("FIRST_CAUSE " + (result.failures + result.errors)[0][1])
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
