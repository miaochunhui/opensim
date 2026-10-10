"""Real file/Git negative controls; fixture success is NOT product acceptance.

Direct execution retains files, logs and unittest counts in a new external root.
No producer is mocked into a production PASS, and no dependencies are installed.
"""

from __future__ import annotations

import copy
import importlib.util
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
RELEASE_SOURCE = ROOT / "tools/release_gate.py"
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
    artifact_base = gate.EXTERNAL

    @classmethod
    def setUpClass(cls):
        cls.out = gate.new_output(cls.artifact_base / ("workbench-files-" + uuid.uuid4().hex))
        cls.root = cls.out / "candidate"
        cls.root.mkdir()
        for relative in (*gate.LOCAL_SOURCES, "tools/integration_acceptance.py", "tools/workbench_acceptance.py",
                         *(f"tools/{item[0]}" for item in gate.PRODUCERS.values())):
            path = cls.root / relative
            path.parent.mkdir(exist_ok=True)
            if relative == "tools/workbench_acceptance.py":
                path.write_bytes((ROOT / relative).read_bytes())
            else:
                path.write_text(f"# FILE_PARSER_FIXTURE_NOT_PRODUCT {relative}\n", encoding="ascii")
        command(cls.root, "init", "-b", "codex/file-fixture")
        command(cls.root, "add", "--", *gate.LOCAL_SOURCES, "tools/integration_acceptance.py", "tools/workbench_acceptance.py",
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

    def browser(self, directory, core=True):
        image = png()
        (directory / "fixture.png").write_bytes(image)
        sources = gate.source_map(self.candidate)
        self.save(directory, {"status": 200, "result": {
            "mesh": {"nodes_mm": [[0, 0], [1, 0], [0, 1]]},
            "fields": {"velocity_m_s": [0, 1, 0]},
            "reproducibility": {"source_sha256": sources, "solver_sha256": sources["solver.py"],
                                "computed_at": "2026-10-10T00:00:00Z"}}}, "solve-001.json")
        log = (f"2026-10-10T00:01:00Z BROWSER_CHECK candidate_verdict=AUTOMATED_PASS "
               f"candidate_rc=0 report={directory / 'report.json'}\n").encode()
        (directory / "run.log").write_bytes(log)
        value = {
            "schema_version": 1, "tool": "opensim-browser-check",
            "verdict": "AUTOMATED_PASS", "exit_code": 0,
            "started_at": "2026-10-10T00:00:00Z", "finished_at": "2026-10-10T00:01:00Z",
            "mode": "OPENSIM_ACCEPTANCE", "release_verdict": "NOT_CERTIFIED",
            "commit": self.candidate["commit"],
            "tool_sha256": gate.sha((self.root / "tools/browser_check.mjs").read_bytes()),
            "target_url": self.url, "checks": [{"status": "PASS"}],
            "matrix": [{"name": name, "status": "PASS", "width": dimensions[0],
                        "height": dimensions[1], "touch": dimensions[2]}
                       for name, dimensions in sorted(gate.VIEWPORT_MATRIX.items())],
            "result_count": 1, "page_build": {"build_text": gate.page_build(self.root)},
            "screenshots": [{"file": "fixture.png", "sha256": gate.sha(image), "bytes": len(image),
                             "width": 3, "height": 2}],
            "durability": {"protocol": "browser-durable-final-v1", "state": "FINAL_AFTER_DURABLE_LOG",
                           "log": {"file": "run.log", "bytes": len(log), "sha256": gate.sha(log)}},
        }
        self.capture_fixture(directory, value)
        if core:
            (directory / "fixture.png").rename(directory / "preserved-single-image.bin")
            (directory / "fixture-capture.json").rename(directory / "preserved-single-capture.bin")
            value["screenshots"], value["captures"] = [], []
            flows = {name: dimensions[:2] for name, dimensions in gate.VIEWPORT_MATRIX.items()}
            flows.update({"keyboard-desktop": (1280, 800), "keyboard-mobile": (320, 568),
                          "reduced-motion": (360, 800), "forced-colors": (360, 800),
                          "text-200": (360, 800), "page-400": (320, 200)})
            for name, (width, height) in flows.items():
                if not name.startswith("keyboard-"):
                    self.save(directory, {"viewport": {"width": width, "height": height}, "client_width": width - 15},
                              name + "-layout.json")
                for suffix, view in (("mesh", "mesh"), ("field", "field"), ("bad-input", "field")):
                    self.capture_fixture(directory, value, name=name + "-" + suffix,
                                         viewport={"width": width - 15, "height": height}, view=view, append=True,
                                         dpr=4 if name == "page-400" else 1)
                captures = {row["name"]: row for row in value["captures"]}
                value["checks"].append({"status": "PASS", "coordinate": name,
                                        "detail": {view: captures[name + "-" + view]["canvas"] for view in ("field", "mesh")}})
            value["source_audit"], value["breakpoints"] = {"media_widths": []}, [576, 768, 1024, 1280]
            for point in value["breakpoints"]:
                for delta in (-1, 0, 1):
                    name = f"breakpoint-{point + delta}"
                    self.capture_fixture(directory, value, name=name,
                                         viewport={"width": point + delta - 15, "height": 800}, append=True)
                    self.save(directory, {"viewport": {"width": point + delta, "height": 800},
                                          "client_width": point + delta - 15}, name + "-layout.json")
                    canvas = value["captures"][-1]["canvas"]
                    value["checks"].append({"status": "PASS", "rule": "WEB-RWD-002", "coordinate": name,
                                            "detail": {"field": {"selected_view": "field", "canvas": canvas},
                                                       "mesh": {"selected_view": "mesh", "canvas": {**canvas, "pixel_hash": "2"}}}})
            self.capture_fixture(directory, value, name="rectangle",
                                 viewport={"width": 1280, "height": 800}, append=True)
        return value

    def capture_fixture(self, directory, value, segmented=False, name="fixture", viewport=None,
                        view="field", append=False, dpr=1):
        clips = [{"x": 0, "y": 0, "width": 3, "height": 2, "scale": 1}]
        if segmented:
            clips.append({**clips[0], "y": 2})
        pictures = []
        for index, clip in enumerate(clips):
            filename = name + ".png" if not segmented else f"{name}-tile-{index + 1:03d}.png"
            image = png(3 * dpr, 2 * dpr)
            (directory / filename).write_bytes(image)
            canvas = {"width": 3, "height": 4, "nontransparent": 11, "distinct_colors": 3,
                      "pixel_hash": "1" if view == "field" else "2"}
            pictures.append({"file": filename, "capture": name, "coordinate": "FILE_FIXTURE",
                             "selected_view": view, "canvas": canvas, "clip": clip,
                             "width": 3 * dpr, "height": 2 * dpr, "sha256": gate.sha(image), "bytes": len(image)})
        if segmented and (directory / "fixture.png").exists():
            (directory / "fixture.png").rename(directory / "preserved-single-image.bin")
        height = len(clips) * 2
        coverage = {"status": "PASS", "source_css_pixels": 3 * height, "tiles": len(clips),
                    "scaled": False, "gaps": 0, "overlaps": 0}
        manifest = {"name": name, "coordinate": "FILE_FIXTURE", "selected_view": view,
                    "canvas": canvas, "content": {"x": 0, "y": 0, "width": 3, "height": height},
                    "width": 3, "height": height, "dpr": dpr,
                    "viewport": viewport or {"width": 3, "height": 2}, "tiles": clips,
                    "artifacts": pictures, "coverage": coverage}
        self.save(directory, manifest, name + "-capture.json")
        capture = {"name": name, "manifest": name + "-capture.json", "width": 3,
                   "height": height, "dpr": dpr, "selected_view": view, "canvas": canvas, **coverage}
        if append:
            value["screenshots"].extend(pictures)
            value["captures"].append(capture)
        else:
            value["screenshots"] = pictures + [row for row in value.get("screenshots", []) if row.get("capture") not in (None, name)]
            value["captures"] = [capture] + [row for row in value.get("captures", []) if row["name"] != name]

    def validate(self, value, directory, rc=0, name="browser"):
        if name != "browser":
            self.seal_producer_fixture(name, value, directory)
        value = self.save(directory, value)
        return gate.validate_producer(name, value, rc, self.root, self.candidate, directory, self.url)

    def seal_producer_fixture(self, name, value, directory):
        """Actual schema, actual files; this never executes or certifies a producer."""
        if name == "browser":
            return
        if name == "audit":
            (directory / "logs").mkdir(exist_ok=True)
            log = (f"AUDIT candidate_verdict={value['verdict']} candidate_rc={value['exit_code']} "
                   f"report={directory / 'report.json'}\n").encode()
            (directory / "logs/audit.log").write_bytes(log)
            value["completion"] = {"protocol": "durable-final-v2", "status": "FINAL",
                                   "receipt": "completion.json", "log": "logs/audit.log",
                                   "log_sha256": gate.sha(log)}
            self.save(directory, value)
            self.save(directory, {"protocol": "durable-final-v2", "status": "COMPLETE",
                                 "exit_code": value["exit_code"], "report": "report.json",
                                 "report_sha256": gate.sha((directory / "report.json").read_bytes()),
                                 "log_sha256": gate.sha(log)}, "completion.json")
        elif name == "perf":
            value["persistence_protocol"] = copy.deepcopy(gate.PERF_PERSISTENCE)
            (directory / "run.log").write_text(
                f"{value['started_at']} START\n{value['finished_at']} EVIDENCE_READY "
                f"scope={value['scope']} terminal_authority={directory / 'report.json'}\n", encoding="utf-8")
            self.save(directory, {**value, "verdict": "EVIDENCE_PENDING",
                                  "g5_certified": False, "exit_code": 2}, "report-candidate.json")
        else:
            log = (f"{value['started_at']} START mode=PROVISIONAL\n{value['finished_at']} "
                   f"CANDIDATE verdict={value['verdict']} rc={value['exit_code']} "
                   f"checks={len(value['checks'])}\n").encode()
            (directory / "run.log").write_bytes(log)
            (directory / "downloads").mkdir(exist_ok=True)
            files = [directory / row["file"] for row in value["screenshots"]]
            files += sorted(directory.glob("solve-*.json"))
            files += [directory / "downloads" / row["guid"] for row in value["downloads"]]
            evidence = {"state": "COMPLETE", "run_log_sha256": gate.sha(log),
                        "files": [{"file": path.relative_to(directory).as_posix(),
                                   "sha256": gate.sha(path.read_bytes())} for path in files],
                        "actual_solver_calls": value["actual_solver_calls"], "sources": value["sources"],
                        "collected_at": gate.now()}
            self.save(directory, evidence, "evidence.json")
            value.update(state="FINAL", evidence_sha256=gate.sha((directory / "evidence.json").read_bytes()),
                         run_log_sha256=gate.sha(log))
            operations = []
            for path in [directory / "run.log", directory / "evidence.json", *files]:
                info = path.stat()
                for operation, key in (("fsync-file", "file"), ("exact-readback", "file")):
                    operations.append({"operation": operation, key: str(path), "dev": str(info.st_dev),
                                       "ino": str(info.st_ino), "sha256": gate.sha(path.read_bytes())})
                parent = path.parent.stat()
                operations.append({"operation": "fsync-directory", "path": str(path.parent),
                                   "dev": str(parent.st_dev), "ino": str(parent.st_ino)})
            value["durability"] = {"state": gate.UI_DURABILITY, "protocol": gate.UI_PROTOCOL,
                                   "operations": operations}

    def prepared(self, name):
        directory = self.directory()
        value = getattr(self, name)(directory)
        self.seal_producer_fixture(name, value, directory)
        self.save(directory, value)
        return directory, value

    def consume_prepared(self, name, directory, value, rc=0):
        # Do not reseal: mutations must be observed by the production consumer.
        return gate.validate_producer(name, value, rc, self.root, self.candidate, directory, self.url)

    def local(self):
        return {"verdict": "PASS", "steps": [{"rc": 0, "stderr": "\nRan 2 tests in 0.1s\n\nOK\n"}] +
                [{"rc": 0} for _ in range(4)],
                "source_sha256": {name: self.candidate["tracked_sha256"][name] for name in gate.LOCAL_SOURCES}}

    def test_valid_real_file_parser_fixture_is_not_product_claim(self):
        directory = self.directory()
        result = self.validate(self.browser(directory), directory)
        self.assertEqual((result["checks"], result["solves"], len(result["pngs"])), (27, 1, 55))
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
            image = directory / value["screenshots"][0]["file"]
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
        for missing in ("mobile-narrow-mesh.png", "solve-001.json"):
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
        # Persistence alone is not a complete four-producer automation run.
        with self.assertRaisesRegex(gate.EvidenceError, "terminal scope"):
            gate.read_final(directory)

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
        browser = self.browser(directory, core=False)
        (directory / "fixture.png").rename(directory / "preserved-browser-fixture.bin")
        sources = gate.source_map(self.candidate)
        ident = {"verdict": "PASS", "url": self.url, "fixture_asset_override": False,
                 "local_source_sha256": sources, "committed_source_sha256": sources,
                 "health": {"source_sha256": sources, "page_build": gate.page_build(self.root)},
                 "served_byte_sha256": {"health": "1" * 64, "/": "2" * 64}}
        post = {"payload": json.loads((directory / "solve-001.json").read_bytes())["result"],
                "body_sha256": "3" * 64, "bytes": 10, "url": self.url + "api/solve",
                "status": 200, "injection": None}
        self.save(directory, post, "http-solve-000.json")
        pictures, injections = [], []
        for width, height in ((320, 568), (768, 1024), (1064, 768), (1440, 900)):
            image = png(width, height)
            for state in ("loading-empty", "ready", "error", "empty", "long",
                          "error-recovery", "empty-recovery", "long-recovery",
                          "mesh-selected", "field-unselected", "details-open-0"):
                phase = f"{width}-{state}"
                for label in (phase, phase + "-tile-000"):
                    filename = label + ".png"
                    (directory / filename).write_bytes(image)
                    pictures.append({"file": filename, "width": width, "height": height,
                                     "sha256": gate.sha(image), "url": self.url, "state": label})
                self.save(directory, {"status": "PASS", "scale": 1, "horizontal_coverage": "COMPLETE",
                                      "document": {"width": width, "height": height},
                                      "vertical_coverage": height,
                                      "tiles": [{"file": phase + "-tile-000.png", "document_y": 0,
                                                 "viewport": {"width": width, "height": height}}]},
                          phase + ".coverage.json")
            for mode in ("hold", "error", "empty", "long"):
                injections.append({"mode": mode,
                                   "phase": f"{width}-loading-empty" if mode == "hold" else f"{width}-{mode}",
                                   "url": self.url + "api/solve", "request_id": f"fixture-{width}-{mode}",
                                   "status": 503 if mode == "error" else 200, "body_sha256": "4" * 64,
                                   "real_mesh_nodes": 3, "injected_warning_length": 50})
        return {**browser, "tool": "opensim-browser-audit", "url": self.url,
                "tool_sha256": gate.sha((self.root / "tools/browser_audit.mjs").read_bytes()),
                "mode": "G3_G6_AUTOMATIC_EXTENSION_NOT_RELEASE", "verdict": "AUTOMATED_SCOPE_PASS",
                "source_sha256": sources, "page_build": {"id": gate.page_build(self.root)},
                "identity_checks": [{"stage": "before", **ident}, {"stage": "after", **ident}],
                "post_source_checks": [{"artifact": "http-solve-000.json", "source_sha256": sources,
                                        "body_sha256": "3" * 64, "verdict": "PASS",
                                        "url": self.url + "api/solve"}],
                "screenshots": pictures, "injections": injections,
                "coverage_counts": {"matrix": 4, "checks": 1, "screenshots": len(pictures),
                                    "injected_requests": len(injections)},
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

    def perf(self, directory):
        browser = self.browser(directory, core=False)
        image = png(360, 800)
        (directory / "result.png").write_bytes(image)
        sources = gate.source_map(self.candidate)
        return {**browser, "tool": "opensim-browser-performance", "standard": "shared-r41",
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
    def test_perf_local_scope_and_fixed_png_without_schema_dimensions(self):
        directory = self.directory()
        value = self.perf(directory)
        result = self.validate(value, directory, name="perf")
        self.assertEqual((result["pngs"][0]["width"], result["pngs"][0]["height"]), (360, 800))
        for field, bad in (("scope", "PUBLIC_LAB"), ("g5_certified", True)):
            altered = copy.deepcopy(value)
            altered[field] = bad
            with self.assertRaisesRegex(gate.EvidenceError, "scope"):
                self.validate(altered, directory, name="perf")

    def ui(self, directory):
        value = self.browser(directory, core=False)
        del value["commit"]
        value.update(tool="opensim-ui-contract", mode="REAL_DOM_CDP_CONTRACT", verdict="PASS",
                     tool_sha256=gate.sha((self.root / "tools/ui_contract_test.mjs").read_bytes()),
                     sources=gate.source_map(self.candidate), actual_solver_calls=1,
                     checks=[{"verdict": "PASS"}], browser_failures=[],
                     downloads=[{"guid": "fixture-export", "state": "completed"}])
        (directory / "downloads").mkdir()
        self.save(directory / "downloads", json.loads((directory / "solve-001.json").read_bytes())["result"],
                  "fixture-export")
        return value

    def test_ui_missing_commit_is_explicit_inventory_binding_not_fabrication(self):
        directory = self.directory()
        value = self.ui(directory)
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

    def test_audit_receipt_missing_empty_incomplete_rc_and_report_sha_rejected(self):
        for variant in ("missing", "empty", "incomplete", "rc", "report-sha", "old-protocol"):
            directory, value = self.prepared("audit")
            receipt_path = directory / "completion.json"
            receipt = json.loads(receipt_path.read_bytes())
            if variant == "missing":
                receipt_path.rename(directory / "preserved-receipt.json")
            elif variant == "empty":
                receipt_path.write_bytes(b"")
            else:
                if variant == "incomplete":
                    receipt["status"] = "PENDING"
                elif variant == "rc":
                    receipt["exit_code"] = False
                elif variant == "report-sha":
                    receipt["report_sha256"] = "0" * 64
                else:
                    receipt["protocol"] = "durable-final-v1"
                self.save(directory, receipt, "completion.json")
            with self.subTest(variant=variant), self.assertRaises((gate.EvidenceError, OSError)):
                self.consume_prepared("audit", directory, value)

    def test_audit_log_sha_actual_bytes_and_candidate_termination_rejected(self):
        for variant in ("log-bytes", "receipt-sha", "report-sha", "termination"):
            directory, value = self.prepared("audit")
            receipt = json.loads((directory / "completion.json").read_bytes())
            if variant == "log-bytes":
                with (directory / "logs/audit.log").open("ab") as stream:
                    stream.write(b"late actual drift\n")
            elif variant == "receipt-sha":
                receipt["log_sha256"] = "0" * 64
            elif variant == "report-sha":
                value["completion"]["log_sha256"] = "0" * 64
            else:
                log = b"AUDIT candidate_verdict=PASS candidate_rc=0 report=/unknown/report.json\n"
                (directory / "logs/audit.log").write_bytes(log)
                value["completion"]["log_sha256"] = receipt["log_sha256"] = gate.sha(log)
            self.save(directory, value)
            receipt["report_sha256"] = gate.sha((directory / "report.json").read_bytes())
            self.save(directory, receipt, "completion.json")
            with self.subTest(variant=variant), self.assertRaises(gate.EvidenceError):
                self.consume_prepared("audit", directory, value)

    def test_audit_post_status_url_solver_time_empty_mesh_and_velocity_rejected(self):
        for variant in ("status", "url", "solver", "time", "mesh", "velocity", "sha"):
            directory, value = self.prepared("audit")
            post = json.loads((directory / "http-solve-000.json").read_bytes())
            if variant == "status":
                post["status"] = 503
            elif variant == "url":
                post["url"] = "http://127.0.0.1:9/api/solve"
            elif variant == "solver":
                post["payload"]["reproducibility"]["solver_sha256"] = "0" * 64
            elif variant == "time":
                post["payload"]["reproducibility"]["computed_at"] = ""
            elif variant == "mesh":
                post["payload"]["mesh"]["nodes_mm"] = []
            elif variant == "velocity":
                post["payload"]["fields"]["velocity_m_s"] = []
            else:
                post["body_sha256"] = "not-a-sha"
            self.save(directory, post, "http-solve-000.json")
            with self.subTest(variant=variant), self.assertRaises((gate.EvidenceError, ValueError)):
                self.consume_prepared("audit", directory, value)

    def test_audit_required_states_injections_and_tile_coverage_rejected(self):
        for variant in ("state", "injection", "count", "gap", "tile", "scale"):
            directory, value = self.prepared("audit")
            if variant == "state":
                value["screenshots"] = [row for row in value["screenshots"] if row["state"] != "320-error-recovery"]
            elif variant == "injection":
                value["injections"][0]["mode"] = "none"
            elif variant == "count":
                value["coverage_counts"]["screenshots"] = 1
            else:
                coverage = json.loads((directory / "320-long.coverage.json").read_bytes())
                if variant == "gap":
                    coverage["tiles"][0]["document_y"] = 1
                elif variant == "tile":
                    coverage["tiles"][0]["file"] = "missing.png"
                else:
                    coverage["scale"] = 0.5
                self.save(directory, coverage, "320-long.coverage.json")
            self.seal_producer_fixture("audit", value, directory)
            self.save(directory, value)
            with self.subTest(variant=variant), self.assertRaises(gate.EvidenceError):
                self.consume_prepared("audit", directory, value)

    def test_perf_protocol_log_pending_candidate_and_failure_marker_rejected(self):
        for variant in ("protocol", "log", "authority", "pending", "candidate", "hardlink", "marker"):
            directory, value = self.prepared("perf")
            if variant == "protocol":
                value["persistence_protocol"]["version"] = 1
                self.save(directory, value)
            elif variant == "log":
                with (directory / "run.log").open("ab") as stream:
                    stream.write(b"late actual drift\n")
            elif variant == "authority":
                path = directory / "run.log"
                path.write_text(path.read_text().replace(str(directory / "report.json"), "/unknown/report.json"))
            elif variant in ("pending", "candidate"):
                pending = json.loads((directory / "report-candidate.json").read_bytes())
                pending["verdict" if variant == "pending" else "commit"] = "forged"
                self.save(directory, pending, "report-candidate.json")
            elif variant == "hardlink":
                os.link(directory / "run.log", directory / "extra-link.log")
            else:
                (directory / "report-failure-real-file.json").write_bytes(b"")
            with self.subTest(variant=variant), self.assertRaises(gate.EvidenceError):
                self.consume_prepared("perf", directory, value)

    def test_ui_legacy_manifest_state_and_operation_field_rejected(self):
        for variant in ("legacy", "state", "protocol", "op-key", "inode", "sha", "directory"):
            directory, value = self.prepared("ui")
            if variant == "legacy":
                del value["durability"]
            elif variant == "state":
                value["state"] = "PROVISIONAL"
            elif variant == "protocol":
                value["durability"]["protocol"] = "unknown"
            else:
                operation = value["durability"]["operations"][0]
                if variant == "op-key":
                    operation["path"] = operation.pop("file")
                elif variant == "inode":
                    operation["ino"] = "0"
                elif variant == "sha":
                    operation["sha256"] = "0" * 64
                else:
                    value["durability"]["operations"] = [
                        row for row in value["durability"]["operations"] if row["operation"] != "fsync-directory"]
            self.save(directory, value)
            with self.subTest(variant=variant), self.assertRaises(gate.EvidenceError):
                self.consume_prepared("ui", directory, value)

    def test_ui_manifest_hash_log_bytes_and_artifact_sha_rejected(self):
        for variant in ("manifest-bytes", "log-bytes", "artifact-bytes", "report-sha"):
            directory, value = self.prepared("ui")
            if variant == "report-sha":
                value["evidence_sha256"] = "0" * 64
                self.save(directory, value)
            else:
                path = directory / {"manifest-bytes": "evidence.json", "log-bytes": "run.log",
                                    "artifact-bytes": "solve-001.json"}[variant]
                with path.open("ab") as stream:
                    stream.write(b"\n")
            with self.subTest(variant=variant), self.assertRaises(gate.EvidenceError):
                self.consume_prepared("ui", directory, value)

    def test_ui_manifest_incomplete_duplicate_omitted_extra_escape_source_count_rejected(self):
        for variant in ("state", "duplicate", "omit", "extra", "escape", "source", "count"):
            directory, value = self.prepared("ui")
            evidence = json.loads((directory / "evidence.json").read_bytes())
            if variant == "state":
                evidence["state"] = "PENDING"
            elif variant == "duplicate":
                evidence["files"].append(evidence["files"][0])
            elif variant == "omit":
                evidence["files"].pop()
            elif variant == "extra":
                evidence["files"].append({"file": "run.log", "sha256": value["run_log_sha256"]})
            elif variant == "escape":
                evidence["files"][0]["file"] = "../report.json"
            elif variant == "source":
                evidence["sources"]["solver.py"] = "0" * 64
            else:
                evidence["actual_solver_calls"] = False
            self.save(directory, evidence, "evidence.json")
            value["evidence_sha256"] = gate.sha((directory / "evidence.json").read_bytes())
            self.save(directory, value)
            with self.subTest(variant=variant), self.assertRaises(gate.EvidenceError):
                self.consume_prepared("ui", directory, value)

    def test_ui_actual_downloads_manifest_sources_and_inventory(self):
        directory = self.directory()
        value = self.ui(directory)
        self.assertTrue((directory / "downloads").is_dir())
        result = json.loads((directory / "solve-001.json").read_bytes())["result"]
        self.save(directory / "downloads", result, "actual-guid")
        value["downloads"].append({"guid": "actual-guid", "state": "completed"})
        self.validate(value, directory, name="ui")
        (directory / "downloads" / "unlisted-file").write_bytes(b"unlisted actual download\n")
        with self.assertRaisesRegex(gate.EvidenceError, "download inventory"):
            self.consume_prepared("ui", directory, value)

    def test_actual_failure_sidecars_read_before_unknown_report_preserve_first_cause(self):
        for name, filename in (("browser", "report.failure.json"), ("audit", "report.failure.json"),
                               ("ui", "finalization-failure.json"),
                               ("perf", "report-failure-file.json")):
            output = self.directory()
            run = output / "run-fixture"
            run.mkdir()
            (run / "report.json").mkdir()
            failure = {"schema_version": 1, "tool": gate.PRODUCERS[name][2], "verdict": "FAIL",
                       "first_failure": {"rule": "ORIGINAL_CAUSE", "message": "real producer error"},
                       "finalization_failure": {"stage": "directory-fsync", "code": "EBADF",
                                                "syscall": "fsync", "path": str(run)},
                       "revocations": [{"status": "UNKNOWN_PROTECTED"}]}
            self.save(run, failure, filename)
            value, record, failures = gate.load_producer_terminal(output, name)
            self.assertEqual(value, failure)
            self.assertTrue(record["path"].endswith(filename))
            self.assertEqual(failures[0]["value"]["revocations"], failure["revocations"])
            self.assertTrue((run / "report.json").is_dir())
            with self.assertRaisesRegex(gate.EvidenceError, "ORIGINAL_CAUSE"):
                gate.validate_durable_producer(name, value, 2, run)
        self.assertEqual(gate.compact_cause(failure["finalization_failure"]), failure["finalization_failure"])

    def test_new_late_failure_fields_keep_original_failed_check_first(self):
        for field in ("finalization_failure", "persistence_failure", "persistence_recovery_failure",
                      "failure_log_failure", "revocation_failure"):
            value = {field: {"message": "late I/O failure", "code": "EBADF"}}
            self.assertEqual(gate.first_failure(value), value[field])
            value["checks"] = [{"status": "FAIL", "rule": "FIRST_CHECK"}]
            self.assertEqual(gate.first_failure(value)["rule"], "FIRST_CHECK")

    def test_all_actual_failure_markers_veto_unchanged_success_report(self):
        for name, marker in (("browser", "report.failure.json"), ("audit", "report.failure.json"),
                             ("ui", "finalization-failure.json"),
                             ("perf", "report-failure-sidecar.json")):
            directory, value = self.prepared(name)
            self.save(directory, {"verdict": "FAIL", "finalization_failure": {"code": "EBADF"}}, marker)
            with self.assertRaisesRegex(gate.EvidenceError, "failure marker"):
                gate.validate_durable_producer(name, value, 0, directory)
            self.assertEqual(json.loads((directory / "report.json").read_bytes()), value)

    def test_approved_logical_physical_paths_read_same_protocol(self):
        for name in ("audit", "perf", "ui"):
            directory, value = self.prepared(name)
            logical = gate.EXTERNAL / directory.relative_to(gate.EXTERNAL.resolve())
            if name in ("audit", "perf"):
                path = directory / ("logs/audit.log" if name == "audit" else "run.log")
                path.write_text(path.read_text().replace(str(directory / "report.json"), str(logical / "report.json")))
                if name == "audit":
                    value["completion"]["log_sha256"] = gate.sha(path.read_bytes())
                    self.save(directory, value)
                    receipt = json.loads((directory / "completion.json").read_bytes())
                    receipt.update(report_sha256=gate.sha((directory / "report.json").read_bytes()),
                                   log_sha256=gate.sha(path.read_bytes()))
                    self.save(directory, receipt, "completion.json")
            else:
                for row in value["durability"]["operations"]:
                    key = "path" if row["operation"] == "fsync-directory" else "file"
                    row[key] = str(gate.EXTERNAL / Path(row[key]).relative_to(gate.EXTERNAL.resolve()))
                self.save(directory, value)
            self.consume_prepared(name, directory, value)
            gate.validate_durable_producer(name, value, 0, logical)

    def test_perf_offline_readback_also_requires_durable_completion(self):
        directory = self.directory()
        value = self.perf(directory)
        original = {"sha256": "1" * 64}
        value.update(scope="OFFLINE_PARSE_NOT_G5", verdict="OFFLINE_PARSE_PASS",
                     parsed_source={"sha256": original["sha256"]}, samples=[])
        self.seal_producer_fixture("perf", value, directory)
        self.save(directory, value)
        result = gate.validate_perf_readback(value, 0, directory, original, value["tool_sha256"])
        self.assertEqual(len(result["artifacts"]), 3)
        with (directory / "run.log").open("ab") as stream:
            stream.write(b"late actual parser log drift\n")
        with self.assertRaises(gate.EvidenceError):
            gate.validate_perf_readback(value, 0, directory, original, value["tool_sha256"])

    def test_browser_actual_log_bytes_sha_candidate_and_durable_state_rejected(self):
        for variant in ("legacy", "state", "protocol", "file", "bytes", "sha", "log", "candidate"):
            directory = self.directory()
            value = self.browser(directory)
            if variant == "legacy":
                del value["durability"]
            elif variant == "state":
                value["durability"]["state"] = "PROVISIONAL"
            elif variant == "protocol":
                value["durability"]["protocol"] = "unknown"
            elif variant in ("file", "bytes", "sha"):
                key = "sha256" if variant == "sha" else variant
                value["durability"]["log"][key] = {"file": "../run.log", "bytes": False,
                                                 "sha": "0" * 64}[variant]
            else:
                log = (b"late actual log drift\n" if variant == "log" else
                       b"BROWSER candidate_verdict=AUTOMATED_PASS candidate_rc=2\n")
                (directory / "run.log").write_bytes(log)
                if variant == "candidate":
                    value["durability"]["log"].update(bytes=len(log), sha256=gate.sha(log))
            with self.subTest(variant=variant), self.assertRaises(gate.EvidenceError):
                self.validate(value, directory)

    def test_browser_complete_log_binding_and_approved_path_alias(self):
        for variant in ("time", "tool", "path", "suffix", "missing-newline", "logical-path"):
            directory = self.directory()
            value = self.browser(directory)
            path = directory / "run.log"
            text = path.read_text()
            if variant == "time":
                text = text.replace(value["finished_at"], "2026-10-09T00:01:00Z")
            elif variant == "tool":
                text = text.replace("BROWSER_CHECK", "UNKNOWN_TOOL")
            elif variant == "path":
                text = text.replace(str(directory / "report.json"), "/unknown/report.json")
            elif variant == "suffix":
                text = text.rstrip("\n") + " unbound-extra-field\n"
            elif variant == "missing-newline":
                text = text.rstrip("\n")
            else:
                logical = gate.EXTERNAL / directory.relative_to(gate.EXTERNAL.resolve())
                text = text.replace(str(directory / "report.json"), str(logical / "report.json"))
            path.write_text(text)
            value["durability"]["log"].update(bytes=len(path.read_bytes()), sha256=gate.sha(path.read_bytes()))
            self.save(directory, value)
            if variant == "logical-path":
                result = self.consume_prepared("browser", directory, value)
                self.assertEqual(result["durable_completion"]["protocol"], "browser-durable-final-v1")
            else:
                with self.subTest(variant=variant), self.assertRaisesRegex(gate.EvidenceError, "candidate termination"):
                    self.consume_prepared("browser", directory, value)

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


    def test_resume_browser_segmented_manifest_mutations_rejected(self):
        for variant in ("missing-manifest", "missing-captures", "missing-tile", "extra-tile",
                        "dimensions", "gap", "overlap", "orphan", "view", "canvas", "dpr"):
            with self.subTest(variant=variant):
                directory = self.directory()
                value = self.browser(directory, core=False)
                self.capture_fixture(directory, value, segmented=True)
                self.save(directory, value)
                gate.validate_browser_captures(value, directory)
                path = directory / "fixture-capture.json"
                manifest = json.loads(path.read_bytes())
                if variant == "missing-manifest":
                    path.rename(directory / "preserved-capture.json")
                elif variant == "missing-captures":
                    del value["captures"]
                elif variant == "orphan":
                    (directory / "unlisted.png").write_bytes(png())
                else:
                    if variant == "missing-tile":
                        manifest["artifacts"].pop()
                    elif variant == "extra-tile":
                        manifest["artifacts"].append(manifest["artifacts"][0])
                    elif variant == "dimensions":
                        manifest["artifacts"][0]["width"] = 1
                    elif variant in ("gap", "overlap"):
                        manifest["tiles"][1]["y"] = 3 if variant == "gap" else 1
                        manifest["artifacts"][1]["clip"] = manifest["tiles"][1]
                        value["screenshots"][1]["clip"] = manifest["tiles"][1]
                    elif variant == "view":
                        manifest["selected_view"] = "mesh"
                    elif variant == "canvas":
                        manifest["artifacts"][0]["canvas"] = {"pixel_hash": "stale"}
                    else:
                        manifest["dpr"] = 2
                    self.save(directory, manifest, path.name)
                self.save(directory, value)
                with self.assertRaises(gate.EvidenceError):
                    gate.validate_browser_captures(value, directory)

    def test_resume_audit_orphan_tile_and_extra_segment_rejected(self):
        for variant in ("orphan", "extra-segment", "wrong-name"):
            with self.subTest(variant=variant):
                directory, value = self.prepared("audit")
                picture = copy.deepcopy(value["screenshots"][1])
                picture.update(file="320-loading-empty-tile-999.png", state="320-loading-empty-tile-999")
                (directory / picture["file"]).write_bytes((directory / value["screenshots"][1]["file"]).read_bytes())
                value["screenshots"].append(picture)
                value["coverage_counts"]["screenshots"] += 1
                if variant != "orphan":
                    coverage = json.loads((directory / "320-loading-empty.coverage.json").read_bytes())
                    if variant == "extra-segment":
                        coverage["tiles"].append({"file": picture["file"], "document_y": 0,
                                                  "viewport": {"width": 320, "height": 568}})
                    else:
                        coverage["tiles"][0]["file"] = picture["file"]
                    self.save(directory, coverage, "320-loading-empty.coverage.json")
                self.seal_producer_fixture("audit", value, directory)
                self.save(directory, value)
                with self.assertRaises(gate.EvidenceError):
                    self.consume_prepared("audit", directory, value)

    def test_resume_ui_empty_download_set_rejected_even_when_resealed(self):
        directory, value = self.prepared("ui")
        (directory / "downloads").rename(directory / "preserved-downloads")
        value["downloads"] = []
        self.seal_producer_fixture("ui", value, directory)
        self.save(directory, value)
        with self.assertRaisesRegex(gate.EvidenceError, "download inventory"):
            self.consume_prepared("ui", directory, value)

    def test_resume_terminal_success_without_closed_inventory_rejected(self):
        directory = self.directory()
        gate.persist_result(directory, self.terminal_fixture())
        with self.assertRaises(gate.EvidenceError):
            gate.read_final(directory)

    def extra_audit_capture(self, directory, value):
        phase, width, height = "320-details-open-1", 320, 568
        for label in (phase, phase + "-tile-000"):
            image = png(width, height)
            (directory / (label + ".png")).write_bytes(image)
            value["screenshots"].append({"file": label + ".png", "state": label, "url": self.url,
                                          "width": width, "height": height, "sha256": gate.sha(image)})
        self.save(directory, {"status": "PASS", "scale": 1, "horizontal_coverage": "COMPLETE",
                              "document": {"width": width, "height": height}, "vertical_coverage": height,
                              "tiles": [{"file": phase + "-tile-000.png", "document_y": 0,
                                         "viewport": {"width": width, "height": height}}]}, phase + ".coverage.json")
        value["coverage_counts"]["screenshots"] = len(value["screenshots"])

    def test_resume_audit_extra_whole_capture_is_consumed_and_missing_coverage_rejected(self):
        for variant in ("missing", "gap", "orphan-whole", "dimensions"):
            with self.subTest(variant=variant):
                directory, value = self.prepared("audit")
                self.extra_audit_capture(directory, value)
                self.seal_producer_fixture("audit", value, directory)
                self.save(directory, value)
                result = self.consume_prepared("audit", directory, value)
                self.assertEqual(len(result["pngs"]), 90)
                path = directory / "320-details-open-1.coverage.json"
                if variant == "missing":
                    path.rename(directory / "preserved-extra-coverage.json")
                elif variant == "orphan-whole":
                    (directory / "unlisted-whole.png").write_bytes(png())
                elif variant == "dimensions":
                    value["screenshots"][-2]["width"] = 1
                else:
                    coverage = json.loads(path.read_bytes())
                    coverage["tiles"][0]["document_y"] = 1
                    self.save(directory, coverage, path.name)
                self.seal_producer_fixture("audit", value, directory)
                self.save(directory, value)
                with self.assertRaises(gate.EvidenceError):
                    self.consume_prepared("audit", directory, value)

    def test_resume_browser_single_png_missing_core_and_matrix_geometry_rejected(self):
        for variant in ("single", "matrix", "touch", "core-view", "core-canvas", "viewport"):
            with self.subTest(variant=variant):
                directory = self.directory()
                value = self.browser(directory, core=variant != "single")
                if variant == "matrix":
                    value["matrix"][0]["width"] = 3
                elif variant == "touch":
                    value["matrix"][0]["touch"] = not value["matrix"][0]["touch"]
                elif variant in ("core-view", "core-canvas", "viewport"):
                    row = value["captures"][0]
                    path = directory / row["manifest"]
                    manifest = json.loads(path.read_bytes())
                    if variant == "core-view":
                        row["selected_view"] = manifest["selected_view"] = "field"
                        for artifact in manifest["artifacts"]:
                            artifact["selected_view"] = "field"
                        value["screenshots"][0]["selected_view"] = "field"
                    elif variant == "core-canvas":
                        row["canvas"] = manifest["canvas"] = None
                        manifest["artifacts"][0]["canvas"] = value["screenshots"][0]["canvas"] = None
                    else:
                        manifest["viewport"]["width"] = 3
                    self.save(directory, manifest, path.name)
                self.save(directory, value)
                with self.assertRaises(gate.EvidenceError):
                    self.consume_prepared("browser", directory, value)

    def complete_fixture(self, directory):
        """File protocol fixture; git subprocesses are not product producers."""
        report = {"schema_version": 1, "tool": "opensim-workbench-acceptance",
                  "scope": "LOCAL_AUTOMATION_NOT_RELEASE", "release_verdict": "NOT_CERTIFIED",
                  "before": self.candidate, "after": self.candidate, "producers": {}, "remaining": [],
                  "owned_server": {"url": self.url, "stopped": True}, "steps": [], "counts": {},
                  "finished_at": gate.now(), "verdict": gate.PARTIAL, "exit_code": 3}
        for name in [*(f"local-{index}" for index in range(1, 6)), "browser", "audit", "perf", "perf-readback", "ui"]:
            report["steps"].append(gate.execute(["git", "--version"], self.root, directory, name))
        for name in gate.PRODUCERS:
            child_dir = directory / name
            child_dir.mkdir()
            value = getattr(self, name)(child_dir)
            validation = self.validate(value, child_dir, name=name)
            entry = {"report": gate.file_record(child_dir / "report.json", directory),
                     "terminal": {"rc": 0, "exit_code": 0, "verdict": value["verdict"]}, "validation": validation}
            report["producers"][name] = entry
            report["remaining"].extend({"producer": name, **row} for row in validation["remaining"])
            if name == "perf":
                parsed_dir = child_dir / "readback"
                parsed_dir.mkdir()
                parsed = {**value, "scope": "OFFLINE_PARSE_NOT_G5", "verdict": "OFFLINE_PARSE_PASS",
                          "parsed_source": entry["report"]}
                self.seal_producer_fixture("perf", parsed, parsed_dir)
                self.save(parsed_dir, parsed)
                entry["raw_metric_readback"] = gate.file_record(parsed_dir / "report.json", directory)
                entry["readback_durable_completion"] = gate.validate_perf_readback(
                    parsed, 0, parsed_dir, entry["report"], value["tool_sha256"])
        for key, value in (("producers_validated", 4),
                           ("checks", sum(row["validation"]["checks"] for row in report["producers"].values())),
                           ("solves", sum(row["validation"]["solves"] for row in report["producers"].values())),
                           ("pngs", sum(len(row["validation"]["pngs"]) for row in report["producers"].values()))):
            report["counts"][key] = value
        health = {"status": "ok", "source_sha256": gate.source_map(self.candidate),
                  "page_build": gate.page_build(self.root)}
        for stage in ("before", "after"):
            records = {}
            for endpoint in ("api/health", "", "web/app.js", "web/app.css"):
                path = directory / f"server-{stage}-{endpoint.replace(chr(47), chr(45)) or chr(112)}.bin"
                path.write_bytes(gate.json_bytes(health) if endpoint == "api/health" else
                                 (self.root / endpoint).read_bytes() if endpoint else b"FILE_SERVER_FIXTURE")
                records[endpoint] = gate.file_record(path, directory)
            report["server_" + stage] = {"health": health, "artifacts": records}
        report["first_cause"] = {"coordinate": "coverage", "message": "Producer coverage remains PARTIAL/NOT_TESTED",
                                 "detail": report["remaining"][0]}
        return report

    def test_resume_complete_terminal_rejects_inventory_rc_log_and_preserves_partial(self):
        directory = self.directory()
        report = self.complete_fixture(directory)
        gate.validate_complete_run(report)
        gate.persist_result(directory, report)
        self.assertEqual(gate.read_final(directory)["exit_code"], 3)
        for variant in ("producer", "receipt", "rc", "step", "server-after", "download", "parse", "counts", "promote"):
            value = copy.deepcopy(report)
            if variant == "producer":
                del value["producers"]["ui"]
            elif variant == "receipt":
                value["producers"]["audit"]["validation"]["durable_completion"]["artifacts"] = []
            elif variant == "rc":
                value["producers"]["browser"]["terminal"]["exit_code"] = 1
            elif variant == "step":
                value["steps"].pop()
            elif variant == "server-after":
                del value["server_after"]["artifacts"]["web/app.js"]
            elif variant == "download":
                value["producers"]["ui"]["validation"]["downloads"] = 0
            elif variant == "parse":
                del value["producers"]["perf"]["raw_metric_readback"]
            elif variant == "counts":
                value["counts"]["pngs"] = 1
            else:
                value.update(verdict=gate.PASS, exit_code=0)
            with self.subTest(variant=variant), self.assertRaises(gate.EvidenceError):
                gate.validate_complete_run(value)
        with (directory / "logs/browser.stdout.log").open("ab") as stream:
            stream.write(b"actual late log drift\n")
        with self.assertRaisesRegex(gate.EvidenceError, "subprocess log"):
            gate.read_final(directory)

    def test_resume_synchronized_canvas_change_and_fixed_plan_omission_rejected(self):
        for variant in ("stale-hash", "drawing-check", "keyboard", "breakpoint", "rectangle"):
            with self.subTest(variant=variant):
                directory = self.directory()
                value = self.browser(directory)
                if variant in ("keyboard", "breakpoint", "rectangle"):
                    selected = {"keyboard": "keyboard-mobile-mesh", "breakpoint": "breakpoint-575",
                                "rectangle": "rectangle"}[variant]
                    row = next(row for row in value["captures"] if row["name"] == selected)
                    manifest = json.loads((directory / row["manifest"]).read_bytes())
                    for artifact in manifest["artifacts"]:
                        (directory / artifact["file"]).rename(directory / ("preserved-" + artifact["file"] + ".bin"))
                    (directory / row["manifest"]).rename(directory / ("preserved-" + row["manifest"] + ".bin"))
                    value["captures"] = [row for row in value["captures"] if row["name"] != selected]
                    value["screenshots"] = [row for row in value["screenshots"] if row["capture"] != selected]
                else:
                    selected = "mobile-narrow-mesh"
                    row = next(row for row in value["captures"] if row["name"] == selected)
                    manifest = json.loads((directory / row["manifest"]).read_bytes())
                    canvas = {**row["canvas"], "pixel_hash": "1" if variant == "stale-hash" else "3"}
                    row["canvas"] = manifest["canvas"] = canvas
                    for artifact in manifest["artifacts"]:
                        artifact["canvas"] = canvas
                    next(row for row in value["screenshots"] if row["capture"] == selected)["canvas"] = canvas
                    self.save(directory, manifest, row["manifest"])
                self.save(directory, value)
                with self.assertRaises(gate.EvidenceError):
                    self.consume_prepared("browser", directory, value)

    def test_resume_pinned_release_gate_consumes_same_pure_audit_reader(self):
        source = RELEASE_SOURCE
        frozen = source.read_bytes()
        spec = importlib.util.spec_from_file_location("resume_pinned_gate", source)
        release = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(release)
        directory, value = self.prepared("audit")
        self.extra_audit_capture(directory, value)
        expected = {"source_sha256": gate.source_map(self.candidate),
                    "tool_sha256": {"tools/workbench_acceptance.py": gate.sha((ROOT / "tools/workbench_acceptance.py").read_bytes())}}
        result = release.audit_dependencies(value, directory / "report.json", expected, self.root)
        self.assertEqual((result["successful_posts"], result["coverage_artifacts"], result["pngs"]), (1, 45, 90))
        path = directory / "320-details-open-1.coverage.json"
        path.rename(directory / "preserved-extra-coverage.json")
        with self.assertRaises(release.GateError):
            release.audit_dependencies(value, directory / "report.json", expected, self.root)
        self.assertEqual(source.read_bytes(), frozen)
        self.fixture_records.append({"scope": "READONLY_CURRENT_RELEASE_GATE_NOT_AUTHORIZATION",
                                     "path": str(source), "sha256": gate.sha(frozen),
                                     "source_stable": True, "validated_artifacts": result})

    def test_resume_audit_required_view_and_details_group_cannot_be_omitted(self):
        for suffix in ("mesh-selected", "field-unselected", "details-open-0"):
            directory, value = self.prepared("audit")
            phase = "320-" + suffix
            value["screenshots"] = [row for row in value["screenshots"] if not row["state"].startswith(phase)]
            for path in (directory / (phase + ".png"), directory / (phase + "-tile-000.png"),
                         directory / (phase + ".coverage.json")):
                path.rename(directory / ("preserved-" + path.name + ".bin"))
            value["coverage_counts"]["screenshots"] = len(value["screenshots"])
            self.seal_producer_fixture("audit", value, directory)
            self.save(directory, value)
            with self.subTest(suffix=suffix), self.assertRaisesRegex(gate.EvidenceError, "required viewport/state"):
                self.consume_prepared("audit", directory, value)

    def test_resume_client_viewport_binding_and_cleared_bad_input_canvas(self):
        directory = self.directory()
        value = self.browser(directory)
        # No FEM result remains after terminal input rejection; not a ready canvas.
        capture = next(row for row in value["captures"] if row["name"] == "mobile-narrow-bad-input")
        canvas = {**capture["canvas"], "nontransparent": 0, "distinct_colors": 1}
        capture["canvas"] = canvas
        manifest = json.loads((directory / capture["manifest"]).read_bytes())
        manifest["canvas"] = manifest["artifacts"][0]["canvas"] = canvas
        next(row for row in value["screenshots"] if row["capture"] == capture["name"])["canvas"] = canvas
        self.save(directory, manifest, capture["manifest"])
        result = self.validate(value, directory)
        self.assertEqual(len(result["pngs"]), 55)
        # A real layout records innerWidth=320 and clientWidth=305 independently.
        path = directory / "mobile-narrow-layout.json"
        original = json.loads(path.read_bytes())
        self.assertEqual((original["viewport"]["width"], original["client_width"]), (320, 305))
        for key in ("viewport", "client_width"):
            altered = copy.deepcopy(original)
            if key == "viewport":
                altered[key]["width"] = 305
            else:
                altered[key] = 320
            self.save(directory, altered, path.name)
            with self.subTest(key=key), self.assertRaisesRegex(gate.EvidenceError, "viewport"):
                self.consume_prepared("browser", directory, value)
            self.save(directory, original, path.name)


class ProgressResult(unittest.TextTestResult):
    def startTest(self, test):
        super().startTest(test)
        self.stream.write(f"PROGRESS collected_at={gate.now()} test={test.id()} state=RUNNING\n")
        self.stream.flush()

    def stopTest(self, test):
        super().stopTest(test)
        self.stream.write(f"PROGRESS collected_at={gate.now()} tests={self.testsRun} "
                          f"failures={len(self.failures)} errors={len(self.errors)}\n")
        self.stream.flush()


def main():
    global RELEASE_SOURCE
    parser = gate.argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regression", action="store_true")
    parser.add_argument("--cli-smoke", choices=("dirty", "missing"))
    parser.add_argument("--producer-readback", nargs=2, metavar=("PRODUCER", "REPORT"))
    parser.add_argument("--producer-rc", type=int, default=0)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--focused", action="store_true")
    parser.add_argument("--release-source", type=Path, default=RELEASE_SOURCE)
    parser.add_argument("--suite-child", action="store_true", help=gate.argparse.SUPPRESS)
    args = parser.parse_args()
    RELEASE_SOURCE = args.release_source
    if args.suite_child:
        WorkbenchTests.artifact_base = args.out
        suite = unittest.TestSuite()
        suite.addTests(WorkbenchTests(name) for name in unittest.defaultTestLoader.getTestCaseNames(WorkbenchTests)
                       if name.startswith("test_resume_"))
        if not args.focused:
            suite.addTests(WorkbenchTests(name) for name in unittest.defaultTestLoader.getTestCaseNames(WorkbenchTests)
                           if not name.startswith("test_resume_"))
        with (args.out / "progress.log").open("x", encoding="utf-8", buffering=1) as stream:
            result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=ProgressResult).run(suite)
        summary = {"tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
                   "skipped": len(result.skipped), "fixture_roots": [str(WorkbenchTests.out)],
                   "verdict": "PASS" if result.wasSuccessful() and result.testsRun > 0 else "FAIL",
                   "first_cause": (result.failures + result.errors)[0][1] if result.failures or result.errors else None,
                   "collected_at": gate.now()}
        gate.write_json(args.out / "unit-result.json", summary)
        print("WORKBENCH_UNIT verdict={verdict} tests={tests}".format(**summary))
        return 0 if summary["verdict"] == "PASS" else 1
    output = gate.new_output(args.out or gate.EXTERNAL / ("workbench-tests-" + uuid.uuid4().hex))
    if not (args.producer_readback or args.regression or args.cli_smoke):
        before = gate.inventory(ROOT)
        gate.write_json(output / "run-start.json", {"state": "RUNNING", "source_before": before,
                        "collected_at": gate.now(), "focused": args.focused})
        argv = [sys.executable, "-B", str(Path(__file__).resolve()), "--suite-child", "--out", str(output),
                "--release-source", str(RELEASE_SOURCE)]
        if args.focused:
            argv.append("--focused")
        step = gate.execute(argv, ROOT, output, "unittest")
        result, _ = gate.read_report(output / "unit-result.json", output)
        after = gate.inventory(ROOT)
        summary = {"scope": "FILE_AND_HTTP_SELFTEST_NOT_FULL_WORKBENCH_OR_RELEASE", **result,
                   "step": step, "launcher_rc": step["rc"], "source_before": before,
                   "source_after": after, "source_stable": before == after,
                   "verdict": "PASS" if result["verdict"] == "PASS" and step["rc"] == 0 and before == after else "FAIL",
                   "collected_at": gate.now(), "log": gate.file_record(output / "progress.log", output)}
        gate.write_json(output / "summary.json", summary)
        print("WORKBENCH_SELFTEST verdict={verdict} tests={tests} failures={failures} errors={errors} "
              "child_rc={launcher_rc} source_stable={source_stable} report=".format(**summary) +
              str(output / "summary.json"))
        if summary["first_cause"]:
            print("FIRST_CAUSE " + summary["first_cause"])
        return 0 if summary["verdict"] == "PASS" else 1
    if args.producer_readback:
        before = gate.inventory(ROOT)
        gate.write_json(output / "run-start.json", {"state": "RUNNING", "source_before": before,
                                                   "collected_at": gate.now()})
        name, filename = args.producer_readback
        result = {}
        try:
            gate.need(name in ("browser", "audit", "perf", "ui"), "Unknown protocol readback producer")
            path = gate.bounded(Path(filename))
            value, original = gate.read_report(path, gate.EXTERNAL)
            result = gate.validate_durable_producer(name, value, args.producer_rc, path.parent)
            if name in ("browser", "audit"):
                result["observed_pngs"] = [gate.png_record(row, path.parent) for row in value["screenshots"]]
                result["png_count"] = len(result["observed_pngs"])
            if name == "browser":
                result["observed_segment_manifests"] = gate.validate_browser_captures(value, path.parent)
                result["capture_count"] = len(result["observed_segment_manifests"])
                result["observed_captures"] = gate.validate_browser_core(value, path.parent, ROOT)
            if name == "audit":
                result["observed_post_files"] = gate.validate_audit_payload(
                    value, path.parent, value["source_sha256"], value["url"])
                result["observed_state_coverages"] = gate.validate_audit_coverage(value, path.parent, value["url"])
                result["post_count"] = len(result["observed_post_files"])
                result["coverage_count"] = len(result["observed_state_coverages"])
            summary = {"verdict": "PASS", "scope": "EXISTING_PRODUCER_PROTOCOL_READBACK_NOT_PRODUCT",
                       "producer": name, "observed_cli_rc": args.producer_rc, "original": original,
                       "validation": result, "collected_at": gate.now()}
        except Exception as exc:
            summary = {"verdict": "FAIL", "scope": "EXISTING_PRODUCER_PROTOCOL_READBACK_NOT_PRODUCT",
                       "producer": name, "first_cause": str(exc), "validation_before_failure": result,
                       "observed_cli_rc": args.producer_rc, "collected_at": gate.now()}
        after = gate.inventory(ROOT)
        summary.update(source_before=before, source_after=after, source_stable=before == after)
        if before != after:
            summary.update(verdict="FAIL", first_cause="Source changed during native readback")
        with (output / "readback.log").open("x", encoding="utf-8") as log:
            log.write("NATIVE_READBACK " + json.dumps({key: item for key, item in summary.items()
                      if key not in ("source_before", "source_after", "validation", "validation_before_failure")}) + "\n")
        gate.write_json(output / "summary.json", summary)
        print(f"WORKBENCH_PROTOCOL_READBACK verdict={summary['verdict']} producer={name} "
              f"collected_at={summary['collected_at']} report={output / 'summary.json'}")
        if summary["verdict"] == "FAIL":
            print("FIRST_CAUSE " + summary["first_cause"])
        return 0 if summary["verdict"] == "PASS" else 1
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


if __name__ == "__main__":
    raise SystemExit(main())
