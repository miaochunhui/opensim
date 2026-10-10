"""OpenSim-only edge delta engine; production writes are deliberately blocked.

Public API: plan_append(live, transaction), plan_revert(live, receipt, backup),
apply_fixture(root, transaction, expected_sha), revert_fixture(root, receipt).
The filesystem fixture is not a production transport or a TLS release gate.
Production integration requires verified common-owner locking or an explicit
exclusive publication window. Neither is authorized/verified here.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timezone

DOMAIN = "opensim.ai4everyone.top"
EDGES = ("192.168.1.183", "192.168.1.184")
CONFIG = Path("/etc/gitlab-edge/Caddyfile")
CADDY = "/usr/local/bin/caddy-edge"
ENVFILE = "/opt/docs-site/runtime/caddy.env"
EVIDENCE = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-edge")
BLOCK = (
    f"{DOMAIN}:{{$EDGE_PORT:18445}} {{\n"
    "    bind 127.0.0.1\n"
    "    tls /opt/docs-site/runtime/tls/opensim.crt /opt/docs-site/runtime/tls/opensim.key\n"
    "    encode gzip\n"
    "    reverse_proxy http://192.168.1.30:8772\n"
    "}\n"
).encode("ascii")
ISSUE = {
    "id": "EDGE-SHARED-LOCK-01",
    "state": "BLOCKED_NOT_PRODUCTION_READY",
    "reason": "Neither shared-writer lock protocol nor authorized exclusive release window is verified.",
    "close_when": "Verify common-owner lock OR approved exclusive writer window, plus sequential "
                  "restart/recovery authorization and all-domain TLS gate.",
    "owner": "release coordinator and shared edge owner",
}


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def require_edge(edge: str) -> None:
    if edge not in EDGES:
        raise ValueError("Only 192.168.1.183 and 192.168.1.184 are allowed")


def transaction_block(transaction: str) -> bytes:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", transaction):
        raise ValueError("Invalid transaction ID")
    return (f"# >>> opensim-release:{transaction} BEGIN >>>\n".encode()
            + BLOCK + f"# <<< opensim-release:{transaction} END <<<\n".encode())


def existing_domains(body: bytes) -> tuple[str, ...]:
    """Conservative inventory for the observed explicit-domain Caddyfile shape."""
    text = body.decode("utf-8", errors="strict")
    if re.search(r"(?m)^\s*import\b", text):
        raise ValueError("Imported domains need actual Caddy inventory; refusing partial inventory")
    found = re.findall(
        r"(?m)^([a-z0-9.-]+):(?:\{\$EDGE_PORT:18445\}|18445)\s*\{\s*$", text)
    if not found or len(found) != len(set(found)):
        raise ValueError("Empty or duplicate baseline domain inventory")
    # Fail closed rather than skip unsupported address syntax.
    for line in text.splitlines():
        header = line.split("#", 1)[0].strip()
        if ".ai4everyone.top" in header and header.endswith("{"):
            if not re.fullmatch(
                    r"[a-z0-9.-]+:(?:\{\$EDGE_PORT:18445\}|18445)\s*\{", header):
                raise ValueError("Unsupported domain header; refusing incomplete inventory")
    return tuple(sorted(found))


def plan_append(live: bytes, transaction: str) -> tuple[bytes, dict]:
    domains = existing_domains(live)
    if DOMAIN in domains or b"opensim-release:" in live:
        raise ValueError("Pre-existing OpenSim block/marker; refusing to adopt or overwrite")
    block = transaction_block(transaction)
    delta = (b"" if live.endswith(b"\n") else b"\n") + b"\n" + block
    candidate = live + delta
    receipt = {
        "schema": 1, "domain": DOMAIN, "transaction": transaction,
        "before_sha256": digest(live), "installed_sha256": digest(candidate),
        "delta": delta.decode("ascii"), "existing_domains": list(domains),
    }
    return candidate, receipt


def plan_revert(live: bytes, receipt: dict, backup: bytes) -> bytes:
    if receipt.get("schema") != 1 or receipt.get("domain") != DOMAIN:
        raise ValueError("Invalid OpenSim receipt")
    if digest(backup) != receipt.get("before_sha256"):
        raise ValueError("Backup hash mismatch")
    candidate, expected = plan_append(backup, receipt.get("transaction", ""))
    if expected != receipt:
        raise ValueError("Receipt does not describe the exact original delta")
    if digest(live) == receipt["installed_sha256"]:
        if live != candidate:
            raise ValueError("Installed bytes do not match receipt")
        return backup
    block = transaction_block(receipt["transaction"])
    if live.count(b"opensim-release:") != 2 or live.count(block) != 1:
        raise ValueError("Own block drift or ambiguous markers; refusing revert")
    delta = receipt["delta"].encode("ascii")
    if live.count(delta) != 1:
        raise ValueError("Own delta drift; refusing revert")
    reverted = live.replace(delta, b"", 1)
    if DOMAIN in existing_domains(reverted):
        raise ValueError("Unowned OpenSim domain remains; refusing revert")
    return reverted


def read_regular(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"Not a regular file: {path}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(fd)


def write_new(path: Path, body: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    if read_regular(path) != body:
        raise RuntimeError(f"Write verification failed: {path}")


def compare_replace(path: Path, before: bytes, after: bytes) -> None:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("Refusing symlink/non-regular live file")
    temp = path.with_name(path.name + ".candidate-" + uuid.uuid4().hex)
    write_new(temp, after)
    os.chmod(temp, stat.S_IMODE(metadata.st_mode))
    try:
        installed_metadata = temp.stat()
        if (installed_metadata.st_uid, installed_metadata.st_gid) != (metadata.st_uid, metadata.st_gid):
            raise PermissionError("Atomic replacement would change live ownership")
        if read_regular(path) != before:
            raise RuntimeError("Live hash drift immediately before replace")
        os.replace(temp, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        if read_regular(path) != after:
            raise RuntimeError("Installed bytes mismatch")
    finally:
        if temp.exists():
            temp.unlink()


def fixture_root(root: Path) -> Path:
    resolved = root.resolve(strict=True)
    if EVIDENCE.resolve() not in resolved.parents or not resolved.name.startswith("fixture-"):
        raise ValueError("Fixture must be a fixture-* directory under seat-edge evidence")
    if read_regular(resolved / "FIXTURE_ONLY") != b"OPENSIM_FILE_FIXTURE\n":
        raise ValueError("Missing fixture identity; production paths are forbidden")
    return resolved


@contextmanager
def existing_lock(path: Path):
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Lock is not a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def apply_fixture(root: Path, transaction: str, expected_sha: str,
                  validate=None) -> dict:
    root = fixture_root(root)
    with existing_lock(root / "edge.lock"):
        live = read_regular(root / "Caddyfile")
        metadata = (root / "Caddyfile").stat()
        if metadata.st_uid != os.geteuid():
            raise PermissionError("Fixture live ownership differs from executing identity")
        if digest(live) != expected_sha:
            raise RuntimeError("Live SHA differs from inspected baseline")
        candidate, receipt = plan_append(live, transaction)
        # A fixture validator is never labelled actual Caddy/TLS acceptance.
        if validate is not None:
            validate(candidate)
        write_new(root / f"{transaction}.before", live)
        write_new(root / f"{transaction}.receipt.json",
                  (json.dumps(receipt, indent=2) + "\n").encode())
        compare_replace(root / "Caddyfile", live, candidate)
        return receipt


def revert_fixture(root: Path, transaction: str) -> dict:
    root = fixture_root(root)
    transaction_block(transaction)
    with existing_lock(root / "edge.lock"):
        receipt = json.loads(read_regular(root / f"{transaction}.receipt.json"))
        live = read_regular(root / "Caddyfile")
        before = read_regular(root / f"{transaction}.before")
        after = plan_revert(live, receipt, before)
        compare_replace(root / "Caddyfile", live, after)
        return {"state": "FIXTURE_REVERTED", "sha256": digest(after)}


def validate_candidate(candidate: Path) -> dict:
    """Invoke the real installed edge binary; do not store expanded config/env."""
    result = subprocess.run(
        [CADDY, "validate", "--config", str(candidate), "--adapter", "caddyfile",
         "--envfile", ENVFILE], capture_output=True)
    if result.returncode:
        # Validator diagnostics can contain expanded secrets; retain them only
        # in caller-controlled memory, never public evidence.
        error = RuntimeError(f"Caddy validate failed rc={result.returncode} candidate={candidate}")
        error.returncode = result.returncode
        error.private_stderr = result.stderr
        raise error
    return {"validator": CADDY, "adapter": "caddyfile", "rc": 0}


def refuse_production(edge: str) -> None:
    require_edge(edge)
    raise PermissionError(f"{ISSUE['id']}: {ISSUE['reason']} Production mutation disabled.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("action", choices=("inspect", "dryrun", "apply", "revert"))
    parser.add_argument("--edge", required=True, choices=EDGES)
    parser.add_argument("--transaction", required=True)
    parser.add_argument("--expected-sha")
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if EVIDENCE.resolve() not in out.parents:
        parser.error("--out must be a new directory under seat-edge")
    out.mkdir(parents=True, exist_ok=False)
    report = {"action": args.action, "edge": args.edge, "verdict": "FAIL",
              "collected_at": datetime.now(timezone.utc).isoformat(), "issue": ISSUE}
    try:
        transaction_block(args.transaction)
        if args.fixture_root:
            root = fixture_root(args.fixture_root)
            report["scope"] = "FILESYSTEM_FIXTURE_NOT_CADDY_OR_TLS"
            if args.action == "apply":
                report["receipt"] = apply_fixture(root, args.transaction, args.expected_sha)
            elif args.action == "revert":
                report.update(revert_fixture(root, args.transaction))
            else:
                candidate, receipt = plan_append(read_regular(root / "Caddyfile"), args.transaction)
                report.update(receipt=receipt, candidate_sha256=digest(candidate))
            report["verdict"] = "FIXTURE_PASS"
        elif args.action in ("apply", "revert"):
            refuse_production(args.edge)
        else:
            addresses = subprocess.run(["hostname", "-I"], capture_output=True, text=True, check=True)
            if args.edge not in addresses.stdout.split() or os.geteuid() != 0:
                raise PermissionError("Production inspect must run locally on the selected edge as root")
            live = read_regular(CONFIG)
            candidate, receipt = plan_append(live, args.transaction)
            report.update(receipt=receipt, scope="READ_ONLY_PLAN", candidate_sha256=digest(candidate),
                          caddy_validation="NOT_TESTED", tls_canary="NOT_TESTED",
                          production_ready=False)
            if args.action == "dryrun":
                if not args.candidate or read_regular(args.candidate) != candidate:
                    raise ValueError("dryrun requires the exact pre-staged candidate; no fake validate")
                report["caddy_validation"] = validate_candidate(args.candidate)
            report["verdict"] = "PLAN_ONLY"
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
    write_new(out / "report.json", (json.dumps(report, indent=2) + "\n").encode())
    line = f"OPENSIM_EDGE verdict={report['verdict']} action={args.action} report={out / 'report.json'}"
    write_new(out / "execution.log", (line + "\n" + str(report.get("failure", "")) + "\n").encode())
    print(line)
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
