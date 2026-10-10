"""Offline tests and actual subprocess tc-api fixture for fixed OpenSim DNS.

Run this file with --out NEW_EXTERNAL_DIRECTORY to preserve a JSON report and
all fake API/ledger evidence. Fixture code lives here, not generated shell code.
The fixture refuses non-DNSPod actions, unexpected payloads and real credentials.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.util
import io
import json
import os
import re
import socket
import subprocess
import sys
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import dns_release as dns

OUT = dns.ARTIFACTS / "seat-dns"
RUN = None


def dump(path: Path, value):
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, sort_keys=True, indent=2)
        stream.write("\n")


def fake_signer() -> int:
    """DNSPod signer protocol, with filesystem-only state and fail-loud guards."""
    if (sys.argv[1:2] != ["dnspod"] or len(sys.argv) != 6
            or sys.argv[3:] != [dns.VERSION, "-d", "-"]
            or os.environ.get(dns.ENV_ID) != "OFFLINE_FIXTURE_ID"
            or os.environ.get(dns.ENV_KEY) != "OFFLINE_FIXTURE_KEY"):
        return 91
    directory = Path(os.environ["OPENSIM_DNS_FIXTURE_DIR"])
    action = sys.argv[2]
    payload = dns.strict_json(sys.stdin.read())
    state = dns.strict_json((directory / "state.json").read_text())
    expected = {
        "DescribeRecordList": {"Domain": dns.ZONE, "Offset": 0, "Limit": dns.LIMIT},
        "CreateRecord": {"Domain": dns.ZONE, "SubDomain": "opensim", "RecordType": "A",
                         "RecordLine": dns.DESIRED["Line"], "Value": "43.135.70.136", "TTL": 600},
        "DeleteRecord": {"Domain": dns.ZONE, "RecordId": state.get("created_id", 1001)},
    }
    if action not in expected or payload != expected[action]:
        return 92
    with (directory / "calls.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"action": action, "payload": payload}) + "\n")
    records = state["records"]
    variant = state.get("variant", "normal")
    response = {"RequestId": str(uuid.uuid4())}
    if variant == "api_failure":
        # These deliberately untrusted streams must not enter tool evidence.
        print(os.environ[dns.ENV_KEY] + " Authorization: fixture-sensitive", file=sys.stderr)
        print(os.environ[dns.ENV_ID], file=sys.stdout)
        return 17
    if action == "DescribeRecordList":
        state["reads"] = state.get("reads", 0) + 1
        response.update({"RecordList": records,
                         "RecordCountInfo": {"TotalCount": len(records)}})
        if variant == "missing_list":
            response.pop("RecordList")
        elif variant == "null_list":
            response["RecordList"] = None
        elif variant == "object_list":
            response["RecordList"] = {}
        elif variant == "wrong_count":
            response["RecordCountInfo"]["TotalCount"] = len(records) + 1
        elif variant == "limit":
            response["RecordCountInfo"]["TotalCount"] = dns.LIMIT
        elif variant == "error_object":
            response["Error"] = {"Code": "fixture-sensitive", "Message": os.environ[dns.ENV_KEY]}
        elif variant == "missing_request":
            response.pop("RequestId")
        elif variant == "invalid_request":
            response["RequestId"] = "not-a-uuid"
        elif variant == "missing_count":
            response.pop("RecordCountInfo")
        elif variant == "bool_count":
            response["RecordCountInfo"]["TotalCount"] = False
        elif variant == "postcreate_read_failure" and state.get("created"):
            dump(directory / "state.json", state)
            return 18
        elif variant == "precreate_drift" and state["reads"] == 2:
            response["RecordList"] = [{"RecordId": 4001, **dns.DESIRED}]
            response["RecordCountInfo"]["TotalCount"] = 1
        elif variant == "nochange_drift" and state["reads"] == 2:
            response["RecordList"] = [{**r, "TTL": 601} for r in records]
        elif variant == "revert_predelete_drift" and state["reads"] == 5:
            response["RecordList"] = [{**r, "TTL": 601} for r in records]
    elif action == "CreateRecord":
        rid = state.get("created_id", 1001)
        added = {"RecordId": rid, **dns.DESIRED}
        if variant == "created_ttl_drift":
            added["TTL"] = 601
        records.append(added)
        state["created"] = True
        response["RecordId"] = rid
        if variant == "create_missing_id":
            response.pop("RecordId")
        elif variant == "create_missing_request":
            response.pop("RequestId")
    else:
        if variant != "delete_no_effect":
            state["records"] = [r for r in records if r["RecordId"] != payload["RecordId"]]
    dump(directory / "state.json", state)
    print(json.dumps(response, ensure_ascii=True))
    return 0


class DNSReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global RUN
        if RUN is None:
            RUN = dns.external(OUT / ("discovery-" + uuid.uuid4().hex) / "report.json").parent
            RUN.mkdir(parents=True, exist_ok=False)

    def setUp(self):
        self.directory = RUN / self._testMethodName
        self.directory.mkdir()
        self.ledger = self.directory / "receipt.jsonl"
        self.state = self.directory / "state.json"
        dump(self.state, {"records": []})
        self.procs = []

    def arrange(self, records=None, variant="normal"):
        dump(self.state, {"records": records or [], "variant": variant})

    def run_cli(self, mode="apply", expect_rc=0, extra=()):
        argv = [sys.executable, "-B", str(ROOT / "tools/dns_release.py"), mode,
                "--release-id", "seat-dns-test", "--ledger", str(self.ledger),
                "--fixture-dir", str(self.directory), *extra]
        env = os.environ.copy()
        env.pop(dns.ENV_ID, None)
        env.pop(dns.ENV_KEY, None)
        proc = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=20)
        self.procs.append({"mode": mode, "rc": proc.returncode, "stdout": proc.stdout,
                           "stderr": proc.stderr})
        dump(self.directory / "processes.json", self.procs)
        self.assertEqual(proc.returncode, expect_rc, proc.stdout + proc.stderr)
        self.assertEqual(proc.stderr, "")
        receipt = dns.strict_json(proc.stdout)
        self.assertNotIn("OFFLINE_FIXTURE_KEY", proc.stdout)
        self.assertNotIn("OFFLINE_FIXTURE_ID", proc.stdout)
        return receipt

    def calls(self):
        path = self.directory / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def mutations(self):
        return [c for c in self.calls() if c["action"] != "DescribeRecordList"]

    def events(self):
        return [json.loads(line) for line in self.ledger.read_text().splitlines()]

    def test_dryrun_is_not_applied(self):
        receipt = self.run_cli("dryrun")
        self.assertEqual(receipt["outcome"], "PLANNED_CREATE")
        self.assertIsNone(receipt["created_record_id"])
        self.assertIsNone(receipt["after"])
        self.assertEqual(self.mutations(), [])
        self.assertEqual(len(self.calls()), 1)

    def test_apply_and_revert_preserve_other_record_and_ledger(self):
        foreign = {"RecordId": 9, **dns.DESIRED, "Name": "opencad"}
        self.arrange([foreign])
        receipt = self.run_cli()
        self.assertEqual(receipt["outcome"], "APPLIED")
        self.assertEqual(receipt["before"], [])
        self.assertEqual(receipt["after"], [{"RecordId": 1001, **dns.DESIRED}])
        self.assertEqual(receipt["created_record_id"], 1001)
        prefix = self.ledger.read_bytes()
        reverted = self.run_cli("revert")
        self.assertEqual(reverted["outcome"], "REVERTED")
        self.assertEqual(reverted["after"], [])
        self.assertTrue(self.ledger.read_bytes().startswith(prefix))
        self.assertEqual(json.loads(self.state.read_text())["records"], [foreign])
        self.assertEqual([c["action"] for c in self.mutations()], ["CreateRecord", "DeleteRecord"])
        self.assertEqual(self.run_cli("revert")["outcome"], "NO_CHANGE_REVERT")

    def test_exact_existing_is_no_change_not_owned(self):
        self.arrange([{"RecordId": 55, **dns.DESIRED}])
        receipt = self.run_cli()
        self.assertEqual(receipt["outcome"], "NO_CHANGE")
        self.assertIsNone(receipt["created_record_id"])
        self.assertEqual(receipt["verified_record_id"], 55)
        self.assertEqual(len(self.calls()), 2)
        refused = self.run_cli("revert", 2)
        self.assertEqual(refused["failure"]["reason"], "revert_has_no_owned_create")
        self.assertEqual(self.mutations(), [])

    def test_missing_list_fails_loud_without_write(self):
        self.arrange(variant="missing_list")
        receipt = self.run_cli(expect_rc=2)
        self.assertEqual(receipt["failure"]["reason"], "record_list_or_count_missing_or_wrong_type")
        self.assertEqual(self.mutations(), [])
        self.assertEqual(self.events()[-1]["outcome"], "FAILED")

    def test_bad_complete_view_variants(self):
        for variant in ("null_list", "object_list", "wrong_count", "limit",
                        "error_object", "missing_request", "invalid_request",
                        "missing_count", "bool_count"):
            with self.subTest(variant=variant):
                self.arrange(variant=variant)
                receipt = self.run_cli(expect_rc=2)
                self.assertEqual(receipt["outcome"], "FAILED")
        self.assertEqual(self.mutations(), [])

    def test_duplicate_records_refuse(self):
        self.arrange([{"RecordId": rid, **dns.DESIRED} for rid in (10, 11)])
        self.assertEqual(self.run_cli(expect_rc=2)["failure"]["reason"],
                         "duplicate_or_conflicting_opensim_records")
        self.assertEqual(self.mutations(), [])

    def test_duplicate_record_ids_refuse(self):
        self.arrange([{"RecordId": 10, **dns.DESIRED},
                      {"RecordId": 10, **dns.DESIRED, "Name": "opencad"}])
        self.assertEqual(self.run_cli(expect_rc=2)["failure"]["reason"], "duplicate_record_id")
        self.assertEqual(self.mutations(), [])

    def test_incomplete_or_wrong_record_types_refuse(self):
        incomplete = {"RecordId": 10, **dns.DESIRED}
        incomplete.pop("Status")
        for item in (incomplete, {"RecordId": "10", **dns.DESIRED},
                     {"RecordId": True, **dns.DESIRED},
                     {"RecordId": 10, **dns.DESIRED, "TTL": True},
                     {"RecordId": 10, **dns.DESIRED, "Status": "unknown"},
                     {"RecordId": 10, **dns.DESIRED, "LineId": 0},
                     None):
            with self.subTest(item=item):
                self.arrange([item])
                self.assertEqual(self.run_cli(expect_rc=2)["outcome"], "FAILED")
        self.assertEqual(self.mutations(), [])

    def test_conflicting_fields_refuse(self):
        for changes in ({"Type": "CNAME"}, {"Value": "43.135.70.137"},
                        {"TTL": 601}, {"Status": "DISABLE"}, {"Line": "other"},
                        {"LineId": "1"}, {"Name": "OpenSim"}):
            with self.subTest(changes=changes):
                self.arrange([{"RecordId": 10, **dns.DESIRED, **changes}])
                self.assertEqual(self.run_cli(expect_rc=2)["failure"]["reason"],
                                 "opensim_fields_or_status_conflict")
        self.assertEqual(self.mutations(), [])

    def test_api_failure_preserves_rc_without_streams(self):
        self.arrange(variant="api_failure")
        receipt = self.run_cli(expect_rc=2)
        self.assertEqual(receipt["failure"], {"reason": "subprocess_failed",
                                             "action": "DescribeRecordList", "rc": 17})
        self.assertNotIn("fixture-sensitive", self.ledger.read_text())
        self.assertNotIn("OFFLINE_FIXTURE", self.ledger.read_text())
        self.assertEqual(self.mutations(), [])

    def test_precreate_drift_prevents_mutation(self):
        self.arrange(variant="precreate_drift")
        self.assertEqual(self.run_cli(expect_rc=2)["failure"]["reason"], "apply_precreate_drift")
        self.assertEqual(self.mutations(), [])

    def test_no_change_must_be_reverified(self):
        self.arrange([{"RecordId": 10, **dns.DESIRED}], "nochange_drift")
        self.assertEqual(self.run_cli(expect_rc=2)["failure"]["reason"],
                         "no_change_verification_drift")
        self.assertEqual(self.mutations(), [])

    def test_ambiguous_create_preserved_and_never_recreated(self):
        self.arrange(variant="create_missing_id")
        receipt = self.run_cli(expect_rc=2)
        self.assertEqual(receipt["failure"]["reason"],
                         "create_record_id_missing_invalid_or_preexisting")
        self.assertEqual(len(self.mutations()), 1)
        prefix = self.ledger.read_bytes()
        refused = self.run_cli(expect_rc=2)
        self.assertEqual(refused["failure"]["reason"], "create_ownership_unknown_or_ambiguous")
        self.assertEqual(len(self.mutations()), 1)
        self.assertTrue(self.ledger.read_bytes().startswith(prefix))
        self.run_cli("revert", 2)
        self.assertEqual(len(self.mutations()), 1)

    def test_ack_survives_failed_readback_and_allows_verified_revert(self):
        self.arrange(variant="postcreate_read_failure")
        receipt = self.run_cli(expect_rc=2)
        self.assertEqual(receipt["created_record_id"], 1001)
        self.assertEqual(receipt["failure"]["rc"], 18)
        state = json.loads(self.state.read_text())
        state["variant"] = "normal"
        dump(self.state, state)
        self.assertEqual(self.run_cli("revert")["outcome"], "REVERTED")
        self.assertTrue(any(e["outcome"] == "FAILED" for e in self.events() if e["step"] == "FINAL"))

    def test_missing_create_request_id_has_no_owned_ack(self):
        self.arrange(variant="create_missing_request")
        self.assertEqual(self.run_cli(expect_rc=2)["failure"]["reason"], "response_request_id_missing")
        self.assertFalse(any(e["step"] == "CREATE_ACK" for e in self.events()))
        self.assertEqual(self.run_cli("revert", 2)["failure"]["reason"],
                         "create_ownership_unknown_or_ambiguous")
        self.assertEqual(len(self.mutations()), 1)

    def test_postcreate_field_drift_preserves_id_and_refuses_revert(self):
        self.arrange(variant="created_ttl_drift")
        receipt = self.run_cli(expect_rc=2)
        self.assertEqual(receipt["failure"]["reason"], "apply_postcreate_verification_failed")
        self.assertEqual(receipt["created_record_id"], 1001)
        self.run_cli("revert", 2)
        self.assertEqual(len(self.mutations()), 1)

    def test_revert_drift_refuses_without_deleting(self):
        self.run_cli()
        state = json.loads(self.state.read_text())
        state["records"][0]["TTL"] = 601
        dump(self.state, state)
        self.run_cli("revert", 2)
        self.assertEqual([c["action"] for c in self.mutations()], ["CreateRecord"])
        self.assertEqual(json.loads(self.state.read_text())["records"][0]["TTL"], 601)

    def test_revert_foreign_id_refuses(self):
        self.run_cli()
        state = json.loads(self.state.read_text())
        state["records"][0]["RecordId"] = 2002
        dump(self.state, state)
        self.assertEqual(self.run_cli("revert", 2)["failure"]["reason"], "revert_owned_id_drift")
        self.assertEqual(len(self.mutations()), 1)

    def test_revert_no_effect_fails_verification(self):
        self.run_cli()
        state = json.loads(self.state.read_text())
        state["variant"] = "delete_no_effect"
        dump(self.state, state)
        self.assertEqual(self.run_cli("revert", 2)["failure"]["reason"],
                         "revert_postdelete_verification_failed")

    def test_revert_predelete_drift_prevents_delete(self):
        self.arrange(variant="revert_predelete_drift")
        self.run_cli()
        self.assertEqual(self.run_cli("revert", 2)["failure"]["reason"], "revert_predelete_drift")
        self.assertEqual(len(self.mutations()), 1)

    def test_missing_owned_record_is_not_fictive_revert(self):
        self.run_cli()
        state = json.loads(self.state.read_text())
        state["records"] = []
        dump(self.state, state)
        self.assertEqual(self.run_cli("revert", 2)["failure"]["reason"],
                         "owned_record_missing_without_delete_ack")
        self.assertEqual(len(self.mutations()), 1)

    def test_ledger_truncation_is_preserved_without_api_call(self):
        self.ledger.write_bytes(b'{"schema":"partial"')
        original = self.ledger.read_bytes()
        self.assertEqual(self.run_cli(expect_rc=2)["failure"]["reason"], "ledger_empty_or_truncated")
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.ledger.read_bytes(), original)

    def test_other_release_ledger_refuses_before_api(self):
        ledger = dns.Ledger(self.ledger, "other-release")
        ledger.append("dryrun", "BEGIN")
        original = self.ledger.read_bytes()
        self.assertEqual(self.run_cli(expect_rc=2)["failure"]["reason"], "ledger_schema_or_release_mismatch")
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.ledger.read_bytes(), original)

    def test_fixture_receipt_cannot_authorize_production_revert(self):
        self.run_cli()
        original = self.ledger.read_bytes()
        output = io.StringIO()
        with patch.object(dns.subprocess, "run") as proc, patch.object(dns.sys, "stdout", output):
            rc = dns.main(["revert", "--release-id", "seat-dns-test", "--ledger", str(self.ledger),
                           "--authorized"])
        self.assertEqual(rc, 2)
        proc.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["failure"]["reason"],
                         "ledger_schema_or_release_mismatch")
        self.assertEqual(self.ledger.read_bytes(), original)

    def test_ledger_write_failure_prevents_api_and_keeps_errno(self):
        output = io.StringIO()
        with patch.object(dns.os, "open", side_effect=OSError(errno.ENOSPC, "untrusted-detail")), \
                patch.object(dns.subprocess, "run") as proc, patch.object(dns.sys, "stdout", output):
            rc = dns.main(["apply", "--release-id", "seat-dns-test", "--ledger", str(self.ledger),
                           "--fixture-dir", str(self.directory)])
        self.assertEqual(rc, 2)
        proc.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["failure"]["errno"], errno.ENOSPC)
        self.assertNotIn("untrusted-detail", output.getvalue())

    def test_missing_authorization_prevents_ssh(self):
        output = io.StringIO()
        with patch.object(dns.subprocess, "run") as proc, patch.object(dns.sys, "stdout", output):
            rc = dns.main(["apply", "--release-id", "seat-dns-test", "--ledger", str(self.ledger)])
        self.assertEqual(rc, 2)
        proc.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["failure"]["reason"],
                         "production_mutation_not_authorized")

    def test_ssh_failure_drops_raw_streams(self):
        fake = subprocess.CompletedProcess([], 23, "credential-sensitive",
                                           "Authorization: credential-sensitive")
        with patch.object(dns.subprocess, "run", return_value=fake) as proc:
            with self.assertRaises(dns.Refuse) as raised:
                dns.credential_env(None)
        self.assertEqual(raised.exception.failure,
                         {"reason": "subprocess_failed", "action": "read_credentials", "rc": 23})
        argv = proc.call_args.args[0]
        self.assertIn("infra@192.168.1.183", argv)
        self.assertIn("/home/infra/.ssh/id_ed25519", argv)
        self.assertNotIn("StrictHostKeyChecking=no", argv)
        self.assertNotIn("credential-sensitive", str(raised.exception))

    def test_credentials_only_in_child_env_and_no_env_fallback(self):
        conf = (dns.SAVED + "SecretId='process-only-id'\n"
                + dns.SAVED + "SecretKey='process-only-key'\n")
        fake = subprocess.CompletedProcess([], 0, conf, "")
        with patch.object(dns.subprocess, "run", return_value=fake) as proc:
            env = dns.credential_env(None)
        self.assertEqual(env[dns.ENV_ID], "process-only-id")
        self.assertEqual(env[dns.ENV_KEY], "process-only-key")
        self.assertNotIn("process-only", json.dumps(proc.call_args.args))
        child_env = proc.call_args.kwargs["env"]
        self.assertNotIn(dns.ENV_ID, child_env)
        self.assertNotIn(dns.ENV_KEY, child_env)

    def test_fixture_denies_network_and_actual_protocol_is_used(self):
        with patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
            self.assertEqual(self.run_cli("dryrun")["outcome"], "PLANNED_CREATE")
        self.assertEqual(self.calls(), [{"action": "DescribeRecordList",
                                        "payload": {"Domain": dns.ZONE, "Offset": 0, "Limit": 3000}}])
        self.assertEqual(json.loads(self.state.read_text())["records"], [])

    def test_duplicate_json_keys_rejected(self):
        with self.assertRaises(dns.Refuse):
            dns.strict_json('{"RecordList":[],"RecordList":[]}')

    def test_external_path_refuses_worktree(self):
        with self.assertRaises(dns.Refuse):
            dns.external(ROOT / "receipt.jsonl")

    def test_secret_shape_scan_and_redaction_controls(self):
        scanner = Path("/data/dev/infra/ops/dsh/scan-secrets-precommit.py")
        spec = importlib.util.spec_from_file_location("infra_secret_scan", scanner)
        engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(engine)
        paths = [ROOT / "tools/dns_release.py", ROOT / "tests/test_dns_release.py",
                 ROOT / "docs/deploy/DNS_RELEASE.md"]
        texts = {str(p): p.read_text(encoding="utf-8") for p in paths}
        patterns = {
            "quoted_password": re.compile(
                r"""(?:password|passwd|pwd)['"]?\s*[:=]\s*['"][^'"]{6,}['"]""", re.I),
            "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
            "tencent_id": re.compile(r"AKID[A-Za-z0-9]{24,}"),
            "saved_assignment": re.compile(r"SAVED_Tencent_Secret(?:Id|Key)\s*="),
        }
        matches = [{"file": path, "pattern": name}
                   for path, text in texts.items() for name, rx in patterns.items() if rx.search(text)]
        self.assertEqual(matches, [])
        needles = []
        for name in (dns.ENV_ID, dns.ENV_KEY):
            value = os.environ.get(name)
            if value:
                needles.append(value)
        hits, _ = engine.scan_literal([str(p) for p in paths], needles)
        self.assertEqual(hits, 0)
        canary = self.directory / "scan-canary.txt"
        canary.write_text("fixture-value-only\n")
        count, rendered = engine.scan_literal([str(canary)], ["fixture-value-only"])
        self.assertEqual(count, 1)
        self.assertNotIn("fixture-value-only", "\n".join(rendered))
        dump(self.directory / "secret-scan.json", {
            "verdict": "PASS", "files": len(paths), "shape_hits": len(matches),
            "environment_literal_hits": hits, "environment_needles": len(needles),
            "live_credentials_read": False, "redaction_negative_control": "PASS"})


def run_tests(argv=None) -> int:
    global RUN
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUT / ("test-" + uuid.uuid4().hex))
    args = parser.parse_args(argv)
    RUN = dns.external(args.out / "report.json").parent
    RUN.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    timer = time.monotonic()
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(DNSReleaseTests))
    report = {"schema": "opensim.dns-tests.v1", "verdict": "PASS" if result.wasSuccessful() else "FAIL",
              "started_at": started, "collected_at": datetime.now(timezone.utc).isoformat(),
              "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "elapsed_seconds": round(time.monotonic() - timer, 3),
              "scope": "OFFLINE_SUBPROCESS_ONLY", "production_mutations": 0,
              "source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in (ROOT / "tools/dns_release.py",
                                             ROOT / "tests/test_dns_release.py",
                                             ROOT / "docs/deploy/DNS_RELEASE.md")}}
    dump(RUN / "report.json", report)
    (RUN / "execution.log").write_text(stream.getvalue(), encoding="utf-8")
    print(json.dumps({**report, "report": str(RUN / "report.json")}, sort_keys=True))
    if not result.wasSuccessful():
        print(stream.getvalue())
    return 0 if result.wasSuccessful() else 2


if __name__ == "__main__":
    if sys.argv[1:2] == ["dnspod"]:
        # Hard network denial is applied inside the actual fixture subprocess,
        # not merely in the unittest parent.
        with patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
            raise SystemExit(fake_signer())
    raise SystemExit(run_tests())
