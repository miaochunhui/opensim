"""Actual external file and subprocess controls; no product/production proof."""

from __future__ import annotations

import argparse
import base64
import copy
from datetime import datetime, timezone
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import release_gate as gate
import release_obligations as obligations

EXTERNAL = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/authorized-s1-release-closure-implementation-20261011")


class ClosureTests(unittest.TestCase):
    output = None
    children = []

    @classmethod
    def setUpClass(cls):
        if cls.output is None:
            cls.output = EXTERNAL / ("discovery-" + uuid4().hex)
            cls.output.mkdir(parents=True, exist_ok=False)
        cls.files = gate.OUTPUT_ROOT / ("s1-closure-fixtures-" + uuid4().hex)
        cls.files.mkdir(parents=True, exist_ok=False)

    def directory(self, name):
        path = self.files / (name + "-" + uuid4().hex)
        path.mkdir()
        return path

    def put(self, directory, name, value):
        path = directory / name
        path.write_bytes(gate.encode(value))
        return {"path": str(path), "sha256": gate.sha(path.read_bytes())}

    def cli(self, argv, directory, *, stdout=None):
        argv = list(argv)
        directory = directory / ("child-" + uuid4().hex)
        directory.mkdir()
        child = subprocess.run(argv, cwd=ROOT, capture_output=stdout is None,
                               stdout=stdout, stderr=subprocess.PIPE if stdout else None)
        streams = {}
        for name, body in (("stdout", child.stdout), ("stderr", child.stderr)):
            path = directory / (name + ".log")
            if body is not None:
                path.write_bytes(body)
                streams[name] = {"path": str(path), "sha256": gate.sha(body), "bytes": len(body), "captured": True}
            else:
                streams[name] = {"captured": False}
        self.children.append({"argv": argv, "actual_rc": child.returncode, **streams})
        return child

    def fixture(self):
        location = ROOT / "tests/test_release_gate.py"
        spec = importlib.util.spec_from_file_location("s1_original_file_fixture", location)
        original = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(original)
        original.Fixture.base = self.files
        # The legacy fixture adds a hooks bypass even for its empty test repo.
        # Strip it before executing actual Git; never change a verdict/reader.
        original_command = original.command
        def command(argv, cwd):
            argv = list(argv)
            if "core.hooksPath=/dev/null" in argv:
                index = argv.index("core.hooksPath=/dev/null")
                del argv[index - 1:index + 1]
            return original_command(argv, cwd)
        original.command = command
        f = original.Fixture(bridge=True)
        path = f.repo / "tools/release_obligations.py"
        path.write_bytes((ROOT / "tools/release_obligations.py").read_bytes())
        for argv in (["git", "add", "--", "tools/release_obligations.py"],
                     ["git", "-c", "user.name=Closure Fixture", "-c", "user.email=fixture@example.invalid",
                      "commit", "-m", "TEST_ONLY measured module"]):
            command(argv, f.repo)
        # Rebuild reports after the fixture commit changes. This fixture has no
        # actual public/human testing; all material remains explicitly TEST_ONLY.
        old, new = f.commit, command(["git", "rev-parse", "HEAD"], f.repo).stdout.strip()
        f.commit = new
        f.identity = gate.identity(f.repo, new)
        def rebind(value):
            if isinstance(value, dict):
                return {k: rebind(v) for k, v in value.items()}
            if isinstance(value, list):
                return [rebind(v) for v in value]
            return new if value == old else value
        f.ledger = rebind(f.ledger)
        f.ledger["identity"] = f.identity
        for kind in ("page", "local", "numerics", "browser", "audit", "performance", "public", *gate.MANUAL):
            f.store(kind, rebind(f.reports[kind]))
            if kind in f.ledger["evidence"]:
                f.ledger["evidence"][kind] = f.pin(kind)
            else:
                f.ledger["manual"][kind] = f.pin(kind)
        for kind, tool in (("local", "tools/acceptance.py"), ("numerics", "tools/validate_numerics.py"),
                           ("browser", "tools/browser_check.mjs"), ("audit", "tools/browser_audit.mjs"),
                           ("performance", "tools/browser_performance.mjs"), ("public", "tools/public_gate.py"),
                           ("page", "tools/render_page.py")):
            f.launch(kind, tool)
        f.reports["audit"].update(findings={"failure_count": 0, "failure_rules": []},
                                  focus_samples=[], page_capabilities={"dialogs": 0, "mobile_menus": 0})
        f.change("audit")
        f.ledger["schema"] = "opensim-release-evidence-v2"
        f.ledger["gates"]["G3-accessibility"]["status"] = "PARTIAL"
        mapped = gate.native_obligations(f.pin("audit"), f.identity, 0, f.repo,
                                        original.BRIDGE_DEPENDENCIES, f.now)
        f.store("native-closure", mapped)
        f.ledger["bridge"] = {"implementations": original.BRIDGE_DEPENDENCIES,
                              "native_closure": f.pin("native-closure"), "canonical": None,
                              "provenance": {"human": {}, "exceptions": {}}}
        f.save()
        return f

    def measurement(self, rule, state):
        pair = [[[0, 0, 0], [255, 255, 255]]]
        values = {
            "WEB-A11Y-001": {"pairs": pair, "sampled_pixels": 1, "paint_method": "COMPOSITED_PIXEL_PAIRS", "unresolved_paint": 0, "color_only": False, "font_px": 16, "font_weight": 400},
            "WEB-A11Y-002": {"pairs": pair, "sampled_pixels": 1, "paint_method": "COMPOSITED_PIXEL_PAIRS", "unresolved_paint": 0, "color_only": False},
            "WEB-A11Y-004": {"rect": [1, 1, 44, 44], "viewport": [320, 568], "indicator_pairs": pair, "uncovered": True, "focus_visible": True},
            "WEB-A11Y-005": {"dom_order": ["main", "form"], "visual_order": ["main", "form"], "unresolved_groups": 0},
            "WEB-A11Y-006": {"tag": "BUTTON", "role": "button", "keyboard_paths": ["Tab", "activation"], "state_exposed": True},
            "WEB-A11Y-007": {"lang": "zh-CN", "title": "TEST_ONLY", "names": ["TEST_ONLY control"], "unresolved_names": 0},
            "WEB-A11Y-008": {"opened_focus": True, "trap_forward": True, "trap_reverse": True, "background_inert": True, "return_focus": True, "name": "TEST_ONLY dialog"},
            "WEB-INPUT-001": {"width": 44, "height": 44},
            "WEB-INPUT-002": {"independent_pairs": 1, "gaps": [8], "overlaps": 0},
            "WEB-INPUT-003": {"labels": ["TEST_ONLY label"], "controls": 1, "persistent_visible": True, "programmatic": True},
            "WEB-INPUT-004": {"error_tested": True, "focus_target": "#error", "invalid_associated": True, "location": "field", "reason": "bad value", "repair": "reset", "recovered": True},
            "WEB-INPUT-005": {"busy": state == "loading", "live": "polite", "focus_before": "#input", "focus_after": "#input", "destructive_actions": 0, "confirmed_or_undoable": False},
            "WEB-TYPE-005": {"payload_sha256": "a" * 64, "rendered_sha256": "a" * 64, "clipped": False, "horizontal_overflow": False, "expanded": False, "unique_information_hidden": False},
            "WEB-TYPE-006": {"payload_sha256": "a" * 64, "rendered_sha256": "a" * 64, "clipped": False, "horizontal_overflow": False, "expanded": False, "unique_information_hidden": False},
            "WEB-CONTENT-001": {"tag": "TABLE", "caption": "TEST_ONLY", "headers": ["col"], "programmatic_associations": True},
            "WEB-CONTENT-002": {"page_scroll_width": 320, "page_client_width": 320, "local_scroll": True, "focusable": True, "name": "TEST_ONLY"},
            "WEB-CONTENT-003": {"strategy": "horizontal-scroll", "header_value_associations": True},
            "WEB-CONTENT-004": {"pre_code": True, "format_preserved": True, "local_scroll_or_wrap": True, "page_overflow": False, "copy_name": "TEST_ONLY", "copy_feedback": True},
            "WEB-CONTENT-005": {"content_width": 200, "container_width": 300, "aspect_reserved": True, "distorted": False},
            "WEB-CONTENT-006": {"phase": "ready" if state in {"ready", "long", "recovery"} else "working" if state == "loading" else "failed", "navigation": 1, "current_navigation": 1, "solve_sequence": 2, "latest_sequence": 2, "result_config_sha256": "b" * 64, "current_config_sha256": "b" * 64, "result_nonempty": state in {"ready", "long", "recovery"}, "no_result": state not in {"ready", "long", "recovery"}, "metrics_uncomputed": state not in {"ready", "long", "recovery"}, "export_disabled": state not in {"ready", "long", "recovery"}, "adjacent_summary": "TEST_ONLY current data", "color_only": False},
            "WEB-CONTENT-007": {"audible_autoplay": False, "moving": False, "pause_available": False, "video": False, "captions": False, "audio": False, "transcript": False},
            "WEB-ADAPT-002": {"profile": "application", "shell_matches": 1, "main_matches": 1, "main_selector": "main", "shell_selector": "body"},
        }
        return copy.deepcopy(values[rule])

    def ledger(self, f, *, absent=False):
        directory = self.directory("measurements")
        p = obligations.policy(f.identity, [{"url": gate.PUBLIC_URL, "objects": [
            {"id": "object-" + kind, "kind": kind} for kind in obligations.KINDS
            if not absent or kind != "dialog"]}], gate.sha((ROOT / "tools/release_obligations.py").read_bytes()))
        ppin = self.put(directory, "policy.json", p)
        checks = [{"coordinate": c, "status": "NA" if c["object"].startswith("absent:") else "PASS",
                   "measurements": {"absence_scan": True} if c["object"].startswith("absent:") else self.measurement(c["rule"], c["state"])} for c in obligations.coordinates(p)]
        baseline = {"schema": obligations.OBSERVATION, "candidate": f.identity,
                    "tool": {"path": "tools/browser_audit.mjs", "sha256": f.identity["tool_sha256"]["tools/browser_audit.mjs"]},
                    "scope": "UNATTESTED_MEASUREMENTS_NOT_CERTIFICATION", "exit_code": 0,
                    "failure": None, "scans": obligations.scans(p), "checks": checks}
        bpin = self.put(directory, "baseline.json", baseline)
        ledger = {"schema": obligations.SCHEMA, "policy": ppin, "baseline": bpin, "supplements": []}
        lpin = self.put(directory, "ledger.json", ledger)
        return directory, p, ppin, baseline, ledger, lpin

    def measured(self, f, ppin, lpin):
        return obligations.evaluate(ppin, lpin, f.identity, gate.sha((ROOT / "tools/release_obligations.py").read_bytes()))

    def signed_materials(self, commit, targets):
        """Real signed public files; all synthetic private keys remain in RAM."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        trust, source = gate.closure_trust()
        directory = self.directory("crypto")
        channel = directory / "authority-channel"
        channel.mkdir()
        current = int(time.time())
        authority = Ed25519PrivateKey.generate()
        public = authority.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        authorization = {"schema": trust.AUTHORIZATION, "channel": trust.FIXTURE_CHANNEL,
            "record_id": "FIXTURE_ONLY_AUTHORIZATION", "domain": trust.DOMAIN, "policy": trust.POLICY,
            "authority_key_id": trust.sha(public), "approved_roles": sorted(trust.ACTORS),
            "issued_at": current - 100, "expires_at": current + 1800}
        receipt = trust.canonical(authorization)
        origin = {"record_id": authorization["record_id"], "sha256": trust.sha(receipt)}
        keys, private = [], {}
        for role in trust.ACTORS:
            key = Ed25519PrivateKey.generate()
            raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            private[role] = key
            keys.append({"key_id": trust.sha(raw), "public_key": base64.b64encode(raw).decode("ascii"),
                "role": role, "actor_kind": trust.ACTORS[role], "principal_id": "FIXTURE_ONLY_" + role,
                "not_before": current - 1000, "expires_at": current + 7200, "source": origin})
        anchor = {"schema": trust.AUTHORITY, "channel": trust.FIXTURE_CHANNEL,
            "domain": trust.DOMAIN, "policy": trust.POLICY, "key_id": trust.sha(public),
            "public_key": base64.b64encode(public).decode("ascii"), "authorization": origin,
            "minimum_enrollment_sequence": 1, "minimum_revocation_sequence": 1,
            "revocation_not_before": current - 60, "not_before": current - 1000, "expires_at": current + 1800}
        registry = {"schema": trust.REGISTRY, "mode": "ENROLLED", "domain": trust.DOMAIN,
            "policy": trust.POLICY, "source": origin, "keys": keys, "sequence": 1,
            "issued_at": current - 10, "expires_at": current + 900, "authority_key_id": trust.sha(public)}
        enrollment = {"schema": trust.ENROLLMENT, "algorithm": "Ed25519",
                      "key_id": trust.sha(public), "payload": registry}
        enrollment["signature"] = base64.b64encode(authority.sign(
            trust.ENROLLMENT_PREFIX + trust.canonical(enrollment))).decode("ascii")
        for name, body in (("authority.json", trust.canonical(anchor)),
                           ("authorization.json", receipt), ("enrollment.json", trust.canonical(enrollment))):
            (channel / name).write_bytes(body)
        snapshot = {"domain": trust.DOMAIN, "policy": trust.POLICY, "source": origin,
            "sequence": 1, "issued_at": current - 10, "expires_at": current + 240,
            "revoked_keys": [], "revoked_records": []}
        def sign(schema, payload, role):
            frame = {"schema": schema, "algorithm": "Ed25519",
                     "key_id": next(k["key_id"] for k in keys if k["role"] == role), "payload": payload}
            frame["signature"] = base64.b64encode(private[role].sign(trust.signing_bytes(frame, schema))).decode("ascii")
            return trust.canonical(frame)
        def write_frame(name, body):
            path = directory / name
            path.write_bytes(body)
            return {"path": str(path), "sha256": trust.sha(body)}
        rpin = write_frame("revocations.json", sign(trust.REVOCATIONS, snapshot, "revocation"))
        proofs = {}
        for index, (role, identifier, digest) in enumerate(targets):
            record_id = role + ":" + identifier
            payload = {"domain": trust.DOMAIN, "policy": trust.POLICY, "role": role,
                "record_id": record_id, "candidate": commit, "material_sha256": digest,
                "issuer_id": "FIXTURE_ONLY_" + role,
                "source": {"record_id": record_id, "sha256": digest},
                "issued_at": current - 10, "not_before": current - 10, "expires_at": current + 600}
            proofs[record_id] = {"envelope": write_frame("envelope-" + str(index) + ".json",
                sign(trust.ENVELOPE, payload, role)), "revocations": rpin}
        self.assertEqual(gate._audit_file(source[0], 1, base=Path("/data/dev")), source[1:])
        return trust, channel, proofs, snapshot, sign, write_frame

    def test_new_request_cli_is_accepted_but_missing_human_stays_blocked(self):
        directory = self.directory("invalid-request")
        request = directory / "request.json"
        request.write_bytes(gate.encode({"schema": "opensim-release-closure-request-v1"}))
        argv = [sys.executable, "-B", str(ROOT / "tools/release_gate.py"),
                "--source-root", str(ROOT), "--commit", "37d8ef6544a26a22ab49aa365ad02adf9e5d5cac",
                "--closure-request", str(request), "--out", str(directory / "gate.json")]
        child = self.cli(argv, directory)
        self.assertNotIn(b"unrecognized arguments", child.stderr)
        self.assertNotEqual(child.returncode, 0)
        report = gate.decode((directory / "gate.json").read_bytes())
        self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")

    def test_all_measured_rules_and_na_compute_not_claim_pass(self):
        f = self.fixture()
        for absent in (False, True):
            _, _, ppin, _, _, lpin = self.ledger(f, absent=absent)
            value, _ = self.measured(f, ppin, lpin)
            self.assertEqual(value["verdict"], "MEASURED_PREDICATES_CLOSED")
            self.assertEqual(value["open"], [])
            self.assertEqual(value["release_verdict"], "NOT_CERTIFIED")

    def test_mutated_measurements_fail_with_real_resealed_bytes(self):
        f = self.fixture()
        mutations = (("WEB-A11Y-001", "pairs", [[[150, 150, 150], [160, 160, 160]]]),
                     ("WEB-INPUT-002", "gaps", [7.99]),
                     ("WEB-A11Y-005", "visual_order", ["form", "main"]),
                     ("WEB-CONTENT-006", "result_nonempty", False))
        directory, _, ppin, baseline, ledger, _ = self.ledger(f)
        for rule, key, value in mutations:
            with self.subTest(rule=rule):
                changed = copy.deepcopy(baseline)
                row = next(r for r in changed["checks"] if r["coordinate"]["rule"] == rule and r["coordinate"]["state"] == "ready")
                row["measurements"][key] = value
                pin = self.put(directory, "mutant.json", changed)
                lpin = self.put(directory, "mutant-ledger.json", {**ledger, "baseline": pin})
                with self.assertRaisesRegex(gate.GateError, "Measured predicate failure"):
                    self.measured(f, ppin, lpin)

    def test_original_failure_not_tested_and_supplement_boundaries(self):
        f = self.fixture()
        directory, _, ppin, baseline, ledger, _ = self.ledger(f)
        original = copy.deepcopy(baseline)
        original["checks"][0]["status"] = "NOT_TESTED"
        pin = self.put(directory, "partial.json", original)
        lpin = self.put(directory, "partial-ledger.json", {**ledger, "baseline": pin})
        value, _ = self.measured(f, ppin, lpin)
        self.assertEqual(len(value["open"]), 1)
        supplement = {**baseline, "checks": [baseline["checks"][0]]}
        spin = self.put(directory, "supplement.json", supplement)
        lpin = self.put(directory, "closed-ledger.json", {**ledger, "baseline": pin, "supplements": [spin]})
        closed, _ = self.measured(f, ppin, lpin)
        self.assertEqual(closed["open"], [])
        self.assertEqual(closed["originals"][0]["checks"][0]["status"], "NOT_TESTED")
        original["checks"][0]["status"] = "FAIL"
        pin = self.put(directory, "failed.json", original)
        lpin = self.put(directory, "failed-ledger.json", {**ledger, "baseline": pin, "supplements": [spin]})
        with self.assertRaisesRegex(gate.GateError, "failure veto"):
            self.measured(f, ppin, lpin)

    def test_source_coordinate_scan_sidecar_and_late_inode_rejected(self):
        f = self.fixture()
        directory, _, ppin, baseline, ledger, lpin = self.ledger(f)
        for mutation in ("source", "coordinate", "scan", "duplicate", "rc", "na"):
            with self.subTest(mutation=mutation):
                changed = copy.deepcopy(baseline)
                if mutation == "source": changed["candidate"]["commit"] = "0" * 40
                elif mutation == "coordinate": changed["checks"][0]["coordinate"]["object"] = "foreign"
                elif mutation == "scan": changed["scans"].pop()
                elif mutation == "duplicate": changed["checks"].append(changed["checks"][0])
                elif mutation == "rc": changed["exit_code"] = True
                else:
                    row = next(r for r in changed["checks"] if r["coordinate"]["rule"] == "WEB-A11Y-008")
                    row.update(status="NA", measurements={"absence_scan": True})
                pin = self.put(directory, "changed.json", changed)
                cp = self.put(directory, "changed-ledger.json", {**ledger, "baseline": pin})
                with self.assertRaises(gate.GateError): self.measured(f, ppin, cp)
        _, records = self.measured(f, ppin, lpin)
        target = Path(ledger["baseline"]["path"])
        body = target.read_bytes()
        replacement = directory / "replacement.json"
        replacement.write_bytes(body)
        os.replace(replacement, target)
        with self.assertRaisesRegex(gate.GateError, "late inode/bytes drift"):
            obligations.verify_records(records)
        marker = directory / "report.failure.json"
        marker.write_bytes(b"TEST_ONLY FAILURE\n")
        with self.assertRaisesRegex(gate.GateError, "failure sidecar"):
            self.measured(f, ppin, lpin)

    def test_complete_actual_file_ledger_reaches_missing_trust_terminal(self):
        f = self.fixture()
        directory, _, ppin, _, _, lpin = self.ledger(f)
        request = self.put(directory, "request.json", {"schema": "opensim-release-closure-request-v1",
            "original_ledger": {"path": str(f.ledger_path), "sha256": gate.sha(f.ledger_path.read_bytes())},
            "policy": ppin, "measurements": lpin, "provenance": {}})
        argv = [sys.executable, "-B", str(ROOT / "tools/release_gate.py"), "--source-root", str(f.repo),
                "--commit", f.commit, "--closure-request", request["path"], "--out", str(directory / "gate.json")]
        child = self.cli(argv, directory)
        self.assertEqual(child.returncode, 1, child.stderr.decode())
        report = gate.decode((directory / "gate.json").read_bytes())
        self.assertEqual(report["bridge"]["closure"]["verdict"], "MEASURED_PREDICATES_CLOSED")
        self.assertIn("trusted human/producer provenance", report["failure"]["reason"])
        self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")

    def test_original_fail_and_missing_human_cannot_be_covered_by_measurements(self):
        f = self.fixture()
        directory, _, ppin, baseline, ledger, lpin = self.ledger(f)
        original = copy.deepcopy(f.ledger)
        for name in ("missing-human", "original-fail", "measured-fail"):
            with self.subTest(name=name):
                f.ledger = copy.deepcopy(original)
                measurement_pin = lpin
                if name == "missing-human":
                    f.ledger["manual"].pop("SCREEN-READER-DESKTOP")
                    reason = "Missing real device/manual"
                elif name == "original-fail":
                    f.ledger["gates"]["G1-structure"]["status"] = "FAIL"
                    reason = "Required gate incomplete"
                else:
                    changed = copy.deepcopy(baseline)
                    changed["checks"][0]["status"] = "FAIL"
                    bpin = self.put(directory, "failed-baseline.json", changed)
                    measurement_pin = self.put(directory, "failed-measurements.json", {**ledger, "baseline": bpin})
                    reason = "Measured producer failure veto"
                f.save()
                request = self.put(directory, name + "-request.json", {"schema": "opensim-release-closure-request-v1",
                    "original_ledger": {"path": str(f.ledger_path), "sha256": gate.sha(f.ledger_path.read_bytes())},
                    "policy": ppin, "measurements": measurement_pin, "provenance": {}})
                out = directory / (name + "-gate.json")
                child = self.cli([sys.executable, "-B", str(ROOT / "tools/release_gate.py"),
                    "--source-root", str(f.repo), "--commit", f.commit,
                    "--closure-request", request["path"], "--out", str(out)], directory)
                self.assertEqual(child.returncode, 1, child.stderr.decode())
                value = gate.decode(out.read_bytes())
                self.assertIn(reason, value["failure"]["reason"])
                self.assertEqual(value["release_verdict"], "NOT_CERTIFIED")
                self.assertNotIn("bridge", value)

    def test_test_only_terminal_success_and_wrong_trust_materials(self):
        f = self.fixture()
        _, _, ppin, _, _, lpin = self.ledger(f)
        closure, _ = self.measured(f, ppin, lpin)
        pin = {"path": str(f.ledger_path), "sha256": gate.sha(f.ledger_path.read_bytes())}
        targets = [("human", rule, f.ledger["manual"][rule]["sha256"]) for rule in gate.MANUAL]
        targets.extend(("deployment", str(i), material["sha256"]) for i, material in enumerate([pin, *closure["materials"]]))
        module, channel, provenance, _, _, _ = self.signed_materials(f.commit, targets)
        context = gate.evaluate(f.repo, f.commit, f.ledger_path, measured_context=True)
        self.assertEqual(context["scope"], "ORIGINAL_INPUTS_VALIDATED_NOT_CERTIFIED")
        trust, records, unavailable = gate.verify_closure_provenance(
            module, provenance, targets, f.commit, fixture_channel=channel)
        self.assertEqual(unavailable, [])
        self.assertEqual(len(records), 2 * len(targets))
        value = gate.certified_terminal(f.identity, f.ledger, pin, closure, trust, None, test_only=True)
        self.assertEqual(value["verdict"], "PASS")
        self.assertEqual(value["scope"], "TEST_ONLY_TERMINAL_CONTRACT")
        self.assertEqual(value["release_verdict"], "NOT_CERTIFIED")
        saved = self.directory("crypto-terminal")
        self.put(saved, "contract-result.json", value)
        with self.assertRaisesRegex(gate.GateError, "production trust unavailable"):
            gate.certified_terminal(f.identity, f.ledger, pin, closure, trust, None)
        for key, replacement in (("candidate", "0" * 40), ("role", "user-approval"), ("material_sha256", "0" * 64)):
            bad = copy.deepcopy(trust)
            bad["materials"][0][key] = replacement
            with self.assertRaises(gate.GateError):
                gate.certified_terminal(f.identity, f.ledger, pin, closure, bad, None, test_only=True)

    def test_real_crypto_source_candidate_time_and_revocation_negatives(self):
        targets = [("human", "SCREEN-READER-DESKTOP", "a" * 64)]
        module, channel, provenance, snapshot, sign, write_frame = self.signed_materials("9" * 40, targets)
        key = "human:SCREEN-READER-DESKTOP"
        original = module.parse(Path(provenance[key]["envelope"]["path"]).read_bytes())["payload"]
        for label in ("source", "candidate", "expired", "revoked", "role", "signature"):
            with self.subTest(label=label):
                changed = copy.deepcopy(provenance)
                payload, revoked = copy.deepcopy(original), copy.deepcopy(snapshot)
                if label == "source": payload["source"]["sha256"] = "b" * 64
                elif label == "candidate": payload["candidate"] = "8" * 40
                elif label == "expired": payload["expires_at"] = int(time.time()) - 1
                elif label == "revoked": revoked["revoked_records"] = [{"record_id": key, "revoked_at": revoked["issued_at"]}]
                elif label == "role": payload["role"] = "deployment"
                frame = sign(module.ENVELOPE, payload, "deployment" if label == "role" else "human")
                if label == "signature":
                    parsed = module.parse(frame)
                    parsed["signature"] = base64.b64encode(bytes(64)).decode("ascii")
                    frame = module.canonical(parsed)
                changed[key]["envelope"] = write_frame(label + "-envelope.json", frame)
                changed[key]["revocations"] = write_frame(label + "-revocations.json", sign(module.REVOCATIONS, revoked, "revocation"))
                with self.assertRaises(gate.GateError):
                    gate.verify_closure_provenance(module, changed, targets, "9" * 40, fixture_channel=channel)
        default, _, unavailable = gate.verify_closure_provenance(module, provenance, targets, "9" * 40)
        self.assertEqual(default["production"], "UNAVAILABLE")
        self.assertEqual(unavailable[0]["code"], "TRUST_UNAVAILABLE")

    def test_actual_measured_cli_positive_and_stdout_enospc(self):
        f = self.fixture()
        directory, _, ppin, _, _, lpin = self.ledger(f)
        argv = [sys.executable, "-B", str(ROOT / "tools/release_obligations.py"), "--source-root", str(f.repo),
                "--commit", f.commit, "--policy", ppin["path"], "--policy-sha256", ppin["sha256"],
                "--ledger", lpin["path"], "--ledger-sha256", lpin["sha256"], "--out", str(directory / "closed.json")]
        child = self.cli(argv, directory)
        self.assertEqual(child.returncode, 0, child.stderr.decode())
        report = gate.decode((directory / "closed.json").read_bytes())
        self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")
        self.assertIn(("report_sha256=" + gate.sha((directory / "closed.json").read_bytes())).encode(), child.stdout)
        argv[-1] = str(directory / "stdout-failed.json")
        self.assertEqual(child.args[-1], str(directory / "closed.json"))
        self.assertEqual(self.children[-1]["argv"][-1], str(directory / "closed.json"))
        with open("/dev/full", "wb") as full:
            failed = self.cli(argv, directory, stdout=full)
        self.assertEqual(failed.returncode, 1, failed.stderr.decode())
        report = gate.decode((directory / "stdout-failed.json").read_bytes())
        self.assertEqual(report["finalization"], "FAILED")
        self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")

    def test_invalid_measurement_types_and_contradictory_canvas_rejected(self):
        samples = (("WEB-INPUT-005", "focus_before", 42),
                   ("WEB-CONTENT-006", "adjacent_summary", True),
                   ("WEB-CONTENT-001", "caption", []),
                   ("WEB-CONTENT-002", "name", 42),
                   ("WEB-CONTENT-004", "copy_name", True),
                   ("WEB-ADAPT-002", "main_selector", True),
                   ("WEB-CONTENT-006", "no_result", True))
        for rule, key, value in samples:
            with self.subTest(rule=rule, key=key):
                measured = self.measurement(rule, "ready")
                measured[key] = value
                if key == "focus_before":
                    measured["focus_after"] = value
                try:
                    accepted = obligations.predicate(rule, measured, "ready")
                except gate.GateError:
                    accepted = False
                self.assertFalse(accepted)


def sources():
    paths = [ROOT / "tools/release_gate.py", Path(__file__), ROOT / "docs/deploy/RELEASE_CLOSURE.md",
             ROOT / "tests/test_release_gate.py", Path(gate.CLOSURE_TRUST_IMPLEMENTATION["path"])]
    paths.extend(Path(pin["path"]) for pin in gate.BRIDGE_IMPLEMENTATIONS.values())
    optional = ROOT / "tools/release_obligations.py"
    if optional.exists():
        paths.append(optional)
    return {str(path): gate.sha(path.read_bytes()) for path in paths}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--method", action="append")
    args = parser.parse_args()
    output = gate.external(args.out, EXTERNAL)
    output.mkdir(parents=True, exist_ok=False)
    ClosureTests.output = output
    before = sources()
    stream = io.StringIO()
    suite = (unittest.TestSuite(ClosureTests(name) for name in args.method) if args.method
             else unittest.defaultTestLoader.loadTestsFromTestCase(ClosureTests))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    after = sources()
    rc = 0 if result.wasSuccessful() and before == after else 1
    report = {"verdict": "PASS" if rc == 0 else "FAIL", "scope": "TEST_ONLY_NOT_PRODUCT_NOT_RELEASE",
              "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "actual_runner_rc": rc, "source_before": before, "source_after": after,
              "source_stable": before == after, "child_invocations": ClosureTests.children,
              "finished_at": datetime.now(timezone.utc).isoformat()}
    (output / "tests.log").write_text(stream.getvalue(), encoding="utf-8")
    (output / "report.json").write_bytes(gate.encode(report))
    verdict = report["verdict"]
    print(f"S1_CLOSURE_TEST verdict={verdict} tests={result.testsRun} rc={rc} out={output}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
