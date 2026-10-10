"""Strict, fixed-target OpenSim DNS release; credentials never leave subprocess memory.

Only dryrun/apply/revert are supported. Production uses the established infra
SSH/account.conf and the read-only infra tc-api signer. --fixture-dir selects
the in-repository, network-free subprocess fixture, never a production signer.
JSONL evidence is external, append-only and fsynced before each mutation.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1")
SIGNER = Path("/data/dev/infra/ops/tencent-cloud/tc_api.py")
FIXTURE = ROOT / "tests/test_dns_release.py"
ZONE = "ai4everyone.top"
LIMIT = 3000
VERSION = "2021-03-23"
ENV_ID = "TENCENTCLOUD_SECRET_ID"
ENV_KEY = "TENCENTCLOUD_SECRET_KEY"
SAVED = "SAVED_" + "Tencent_"
FIELDS = ("RecordId", "Name", "Type", "Value", "Line", "LineId", "TTL", "Status")
DESIRED = {"Name": "opensim", "Type": "A", "Value": "43.135.70.136",
           "Line": "\u9ed8\u8ba4", "LineId": "0", "TTL": 600, "Status": "ENABLE"}
TARGET = {"Domain": ZONE, **DESIRED}
SCHEMA = "opensim.dns-release.v1"
RELEASE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")


class Refuse(Exception):
    """Only fixed labels/numeric diagnostics are safe to include in receipts."""

    def __init__(self, reason: str, *, action: str | None = None,
                 rc: int | None = None, os_errno: int | None = None):
        self.failure = {"reason": reason}
        if action is not None:
            self.failure["action"] = action
        if rc is not None:
            self.failure["rc"] = rc
        if os_errno is not None:
            self.failure["errno"] = os_errno
        super().__init__(reason)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def strict_json(text: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Refuse("duplicate_json_key")
            result[key] = value
        return result

    def constant(_):
        raise Refuse("nonfinite_json")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError):
        raise Refuse("invalid_json") from None


def positive_int(value) -> bool:
    return type(value) is int and value > 0


def record(raw: dict) -> dict:
    if type(raw) is not dict or any(key not in raw for key in FIELDS):
        raise Refuse("incomplete_record")
    if not positive_int(raw["RecordId"]) or not positive_int(raw["TTL"]):
        raise Refuse("invalid_record_integer")
    if any(type(raw[key]) is not str or not raw[key]
           for key in ("Name", "Type", "Value", "Line", "LineId", "Status")):
        raise Refuse("invalid_record_string")
    if (raw["Type"] not in {"A", "AAAA", "CNAME", "MX", "TXT", "NS", "SRV",
                           "CAA", "PTR", "HTTPS", "SVCB", "DNSKEY", "DS", "TLSA"}
            or raw["Status"] not in {"ENABLE", "DISABLE"}):
        raise Refuse("invalid_record_type_or_status")
    return {key: raw[key] for key in FIELDS}


def exact(item: dict) -> bool:
    return all(item[key] == value for key, value in DESIRED.items())


def targets(records: list[dict]) -> list[dict]:
    return [item for item in records if item["Name"].lower() == DESIRED["Name"]]


def classify(records: list[dict]) -> list[dict]:
    hits = targets(records)
    if len(hits) > 1:
        raise Refuse("duplicate_or_conflicting_opensim_records")
    if hits and not exact(hits[0]):
        raise Refuse("opensim_fields_or_status_conflict")
    return hits


def same_zone(left: list[dict], right: list[dict]) -> bool:
    return sorted(left, key=lambda item: item["RecordId"]) == sorted(
        right, key=lambda item: item["RecordId"])


def external(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise Refuse("ledger_not_external_regular_path")
    resolved = path.resolve()
    if ARTIFACTS.resolve() not in resolved.parents or ROOT in resolved.parents:
        raise Refuse("ledger_outside_opensim_evidence")
    return resolved


class Ledger:
    def __init__(self, path: Path, release_id: str, backend: str = "production"):
        self.path = external(path)
        self.release_id = release_id
        self.backend = backend
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events = self.read()
        self.before = None
        self.after = None
        self.created_record_id = None
        self.verified_record_id = None

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        if not self.path.is_file() or self.path.stat().st_size > 16 * 1024 * 1024:
            raise Refuse("ledger_invalid_file_or_size")
        text = self.path.read_text(encoding="utf-8")
        if not text or not text.endswith("\n"):
            raise Refuse("ledger_empty_or_truncated")
        events = []
        for seq, line in enumerate(text.splitlines(), 1):
            entry = strict_json(line)
            if (type(entry) is not dict or entry.get("schema") != SCHEMA
                    or entry.get("seq") != seq or type(entry.get("seq")) is not int
                    or entry.get("release_id") != self.release_id
                    or entry.get("backend") != self.backend
                    or entry.get("target") != TARGET
                    or entry.get("mode") not in {"dryrun", "apply", "revert"}
                    or entry.get("step") not in {
                        "BEGIN", "BEFORE", "CREATE_INTENT", "CREATE_ACK",
                        "DELETE_INTENT", "DELETE_ACK", "AFTER", "FINAL"}
                    or type(entry.get("at")) is not str):
                raise Refuse("ledger_schema_or_release_mismatch")
            for field in ("before", "after"):
                snapshot = entry.get(field)
                if snapshot is not None:
                    if type(snapshot) is not list or len(snapshot) > LIMIT:
                        raise Refuse("ledger_invalid_snapshot")
                    for item in snapshot:
                        record(item)
            rid = entry.get("created_record_id")
            if rid is not None and not positive_int(rid):
                raise Refuse("ledger_invalid_owned_id")
            verified = entry.get("verified_record_id")
            if verified is not None and not positive_int(verified):
                raise Refuse("ledger_invalid_verified_id")
            if entry["step"] == "FINAL" and entry.get("outcome") not in {
                    "PLANNED_CREATE", "NO_CHANGE", "APPLIED", "REVERTED",
                    "NO_CHANGE_REVERT", "FAILED"}:
                raise Refuse("ledger_invalid_final_outcome")
            events.append(entry)
        return events

    def append(self, mode: str, step: str, **extra) -> dict:
        entry = {"schema": SCHEMA, "seq": len(self.events) + 1, "at": now(),
                 "release_id": self.release_id, "target": TARGET, "mode": mode,
                 "backend": self.backend, "step": step,
                 "before": self.before, "after": self.after,
                 "created_record_id": self.created_record_id,
                 "verified_record_id": self.verified_record_id, **extra}
        body = (json.dumps(entry, ensure_ascii=True, sort_keys=True) + "\n").encode()
        # A separate exclusive lease serializes this fixed target, not an I/O
        # flock held across external commands. Never repair/truncate old evidence.
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o666)
        try:
            with os.fdopen(fd, "ab") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            self.events.append(entry)
        except OSError as exc:
            raise Refuse("ledger_append_failed", os_errno=exc.errno) from None
        return entry

    def ownership(self) -> int | None:
        intents = [e for e in self.events if e["step"] == "CREATE_INTENT"]
        acknowledgements = [e for e in self.events if e["step"] == "CREATE_ACK"]
        if not intents and not acknowledgements:
            return None
        if (len(intents) != 1 or len(acknowledgements) != 1
                or intents[0]["mode"] != "apply"
                or acknowledgements[0]["mode"] != "apply"
                or intents[0]["before"] != [] or acknowledgements[0]["before"] != []
                or intents[0]["seq"] >= acknowledgements[0]["seq"]
                or not positive_int(acknowledgements[0]["created_record_id"])):
            raise Refuse("create_ownership_unknown_or_ambiguous")
        return acknowledgements[0]["created_record_id"]


class Lease:
    def __init__(self):
        self.path = ARTIFACTS / "opensim-dns-release.active"
        self.token = uuid.uuid4().hex

    def __enter__(self):
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("x", encoding="ascii") as stream:
                stream.write(self.token + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            raise Refuse("opensim_dns_release_busy_or_stale_lease") from None
        return self

    def __exit__(self, *unused):
        if self.path.read_text(encoding="ascii") != self.token + "\n":
            raise Refuse("lease_drift_refusing_cleanup")
        self.path.unlink()


def child(argv: list[str], *, env: dict, action: str, body: str | None = None):
    try:
        result = subprocess.run(argv, input=body, capture_output=True, text=True,
                                env=env, timeout=45)
    except subprocess.TimeoutExpired:
        raise Refuse("subprocess_timeout_no_retry", action=action) from None
    except (OSError, UnicodeError) as exc:
        raise Refuse("subprocess_io_failed", action=action,
                     os_errno=getattr(exc, "errno", None)) from None
    if result.returncode:
        # Neither stdout nor stderr is trustworthy: signer tracebacks can carry
        # headers and SSH output can carry account.conf. Keep only rc and action.
        raise Refuse("subprocess_failed", action=action, rc=result.returncode)
    return result.stdout


def credential_env(fixture_dir: Path | None) -> dict:
    env = os.environ.copy()
    for key in (ENV_ID, ENV_KEY, "OPENSIM_DNS_FIXTURE_DIR"):
        env.pop(key, None)
    if fixture_dir is not None:
        fixture_dir = external(fixture_dir / "state.json").parent
        if not (fixture_dir / "state.json").is_file():
            raise Refuse("fixture_state_missing")
        env.update({ENV_ID: "OFFLINE_FIXTURE_ID", ENV_KEY: "OFFLINE_FIXTURE_KEY",
                    "OPENSIM_DNS_FIXTURE_DIR": str(fixture_dir)})
        return env
    remote = ("sudo -n grep -E " + shlex.quote("^" + SAVED + "(SecretId|SecretKey)=")
              + " /root/.acme.sh/account.conf")
    text = child(["ssh", "-i", "/home/infra/.ssh/id_ed25519",
                  "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                  "infra@192.168.1.183", remote], env=env.copy(), action="read_credentials")
    found = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key in {SAVED + "SecretId", SAVED + "SecretKey"}:
            try:
                values = shlex.split(value)
            except ValueError:
                raise Refuse("invalid_account_conf_value") from None
            if key in found or len(values) != 1 or not values[0]:
                raise Refuse("ambiguous_or_empty_account_conf_credentials")
            found[key] = values[0]
    if set(found) != {SAVED + "SecretId", SAVED + "SecretKey"}:
        raise Refuse("account_conf_credentials_missing")
    env[ENV_ID], env[ENV_KEY] = found[SAVED + "SecretId"], found[SAVED + "SecretKey"]
    return env


class API:
    def __init__(self, fixture_dir: Path | None):
        self.signer = FIXTURE if fixture_dir is not None else SIGNER
        if not self.signer.is_file():
            raise Refuse("signer_missing")
        self.env = credential_env(fixture_dir)

    def call(self, action: str, payload: dict) -> dict:
        output = child([sys.executable, "-B", str(self.signer), "dnspod", action,
                        VERSION, "-d", "-"], env=self.env, action=action,
                       body=json.dumps(payload, ensure_ascii=True))
        try:
            response = strict_json(output)
            if type(response) is not dict:
                raise Refuse("response_not_object")
            if "Error" in response:
                raise Refuse("api_error_response")
            request_id = response.get("RequestId")
            if type(request_id) is not str:
                raise Refuse("response_request_id_missing")
            try:
                uuid.UUID(request_id)
            except ValueError:
                raise Refuse("response_request_id_invalid") from None
        except Refuse as exc:
            raise Refuse(exc.failure["reason"], action=action) from None
        return response

    def describe(self) -> list[dict]:
        response = self.call("DescribeRecordList", {"Domain": ZONE, "Offset": 0, "Limit": LIMIT})
        records = response.get("RecordList")
        count = response.get("RecordCountInfo")
        if type(records) is not list or type(count) is not dict:
            raise Refuse("record_list_or_count_missing_or_wrong_type")
        total = count.get("TotalCount")
        if type(total) is not int or total < 0:
            raise Refuse("total_count_missing_or_wrong_type")
        if total >= LIMIT or len(records) >= LIMIT:
            raise Refuse("record_limit_incomplete_view")
        if total != len(records):
            raise Refuse("record_count_incomplete_view")
        result = [record(item) for item in records]
        ids = [item["RecordId"] for item in result]
        if len(ids) != len(set(ids)):
            raise Refuse("duplicate_record_id")
        return result


def execute(args, ledger: Ledger) -> str:
    mode = args.mode
    ledger.created_record_id = ledger.ownership()
    api = API(args.fixture_dir)
    before = api.describe()
    ledger.before = targets(before)
    ledger.append(mode, "BEFORE")
    hits = classify(before)
    owned = ledger.created_record_id
    if mode == "revert":
        if owned is None:
            raise Refuse("revert_has_no_owned_create")
        if not hits:
            if not any(e["step"] == "DELETE_ACK" and e["created_record_id"] == owned
                       for e in ledger.events):
                raise Refuse("owned_record_missing_without_delete_ack")
            after = api.describe()
            ledger.after = targets(after)
            ledger.append(mode, "AFTER")
            if not same_zone(before, after) or any(r["RecordId"] == owned for r in after):
                raise Refuse("revert_absence_verification_drift")
            return "NO_CHANGE_REVERT"
        if hits[0]["RecordId"] != owned:
            raise Refuse("revert_owned_id_drift")
        ledger.verified_record_id = owned
        # Re-read immediately before deletion; no blind delete or field update.
        checked = api.describe()
        if not same_zone(before, checked):
            raise Refuse("revert_predelete_drift")
        ledger.append(mode, "DELETE_INTENT")
        api.call("DeleteRecord", {"Domain": ZONE, "RecordId": owned})
        ledger.append(mode, "DELETE_ACK")
        after = api.describe()
        ledger.after = targets(after)
        ledger.append(mode, "AFTER")
        if ledger.after or not same_zone([r for r in before if r["RecordId"] != owned], after):
            raise Refuse("revert_postdelete_verification_failed")
        return "REVERTED"
    if owned is not None and (not hits or hits[0]["RecordId"] != owned):
        raise Refuse("owned_record_drift")
    if hits:
        ledger.verified_record_id = hits[0]["RecordId"]
        after = api.describe()
        ledger.after = targets(after)
        ledger.append(mode, "AFTER")
        if not same_zone(before, after):
            raise Refuse("no_change_verification_drift")
        return "NO_CHANGE"
    if mode == "dryrun":
        return "PLANNED_CREATE"
    checked = api.describe()
    if not same_zone(before, checked):
        raise Refuse("apply_precreate_drift")
    ledger.append(mode, "CREATE_INTENT")
    response = api.call("CreateRecord", {"Domain": ZONE, "SubDomain": DESIRED["Name"],
                                        "RecordType": "A", "RecordLine": DESIRED["Line"],
                                        "Value": DESIRED["Value"], "TTL": 600})
    rid = response.get("RecordId")
    if not positive_int(rid) or any(r["RecordId"] == rid for r in before):
        raise Refuse("create_record_id_missing_invalid_or_preexisting")
    ledger.created_record_id = rid
    ledger.append(mode, "CREATE_ACK")
    after = api.describe()
    ledger.after = targets(after)
    ledger.append(mode, "AFTER")
    if not same_zone(before + [{"RecordId": rid, **DESIRED}], after):
        raise Refuse("apply_postcreate_verification_failed")
    ledger.verified_record_id = rid
    return "APPLIED"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("dryrun", "apply", "revert"))
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--authorized", action="store_true",
                        help="Confirm separate production authorization for this fixed DNS operation")
    parser.add_argument("--fixture-dir", type=Path,
                        help="Offline only: use in-repository fake signer, never SSH/network")
    args = parser.parse_args(argv)
    receipt = {"schema": SCHEMA, "mode": args.mode, "outcome": "FAILED",
               "release_id": None, "target": TARGET, "collected_at": now(),
               "before": None, "after": None, "created_record_id": None,
               "verified_record_id": None}
    try:
        if not RELEASE_RE.fullmatch(args.release_id):
            raise Refuse("invalid_release_id")
        receipt["release_id"] = args.release_id
        if pwd.getpwuid(os.geteuid()).pw_name != "infra":
            raise Refuse("requires_established_infra_identity")
        if args.mode != "dryrun" and args.fixture_dir is None and not args.authorized:
            raise Refuse("production_mutation_not_authorized")
        external(args.ledger)
        backend = ("fixture:" + str(external(args.fixture_dir / "state.json").parent)
                   if args.fixture_dir is not None else "production")
        receipt["backend"] = backend
        with Lease():
            ledger = Ledger(args.ledger, args.release_id, backend)
            ledger.append(args.mode, "BEGIN")
            try:
                outcome = execute(args, ledger)
                final = ledger.append(args.mode, "FINAL", outcome=outcome)
                receipt.update(final)
            except (Refuse, OSError, UnicodeError) as exc:
                failure = (exc.failure if isinstance(exc, Refuse)
                           else {"reason": "local_io_failed",
                                 "errno": getattr(exc, "errno", None)})
                receipt.update({"failure": failure, "before": ledger.before,
                                "after": ledger.after,
                                "created_record_id": ledger.created_record_id,
                                "verified_record_id": ledger.verified_record_id})
                try:
                    ledger.append(args.mode, "FINAL", outcome="FAILED", failure=failure)
                except (Refuse, OSError) as append_error:
                    receipt["evidence_failure"] = (
                        append_error.failure if isinstance(append_error, Refuse)
                        else {"reason": "failure_receipt_append_failed",
                              "errno": append_error.errno})
    except (Refuse, OSError, UnicodeError) as exc:
        receipt["outcome"] = "FAILED"
        failure = (exc.failure if isinstance(exc, Refuse)
                   else {"reason": "local_io_failed", "errno": getattr(exc, "errno", None)})
        if "failure" in receipt:
            receipt["cleanup_failure"] = failure
        else:
            receipt["failure"] = failure
    receipt["ledger"] = str(args.ledger)
    receipt["collected_at"] = now()
    print(json.dumps(receipt, ensure_ascii=True, sort_keys=True))
    return 2 if receipt["outcome"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
