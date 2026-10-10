"""Read-only OpenSim certificate validators; signing/transfer/renewal are blocked.

No account.conf, credential, or private key contents are read by this process.
OpenSSL may read an already-installed key on the same root-controlled edge for
public-key comparison. Never transport a key through infra, worktrees or 3FS.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys

try:
    from .edge_release import DOMAIN, EDGES, EVIDENCE, existing_lock, read_regular, write_new
except ImportError:
    from edge_release import DOMAIN, EDGES, EVIDENCE, existing_lock, read_regular, write_new

TLS = Path("/opt/docs-site/runtime/tls")
ACME = Path("/root/.acme.sh/acme.sh")
ACME_LOCK = Path("/var/lock/dsh-acme.lock")
ISSUE = {
    "id": "CERT-ROOT-BROKER-01", "state": "BLOCKED_NOT_PRODUCTION_READY",
    "reason": "Root-only peer broker and all-writer ACME locking are unverified.",
    "owner": "release coordinator and certificate owner",
    "close_when": "Verify existing root-only broker, common lock, atomic pair rollback, "
                  "renewal restart authorization and per-edge existing-domain TLS canaries.",
}


def require_domain(domain: str) -> None:
    if domain != DOMAIN:
        raise ValueError("Exactly opensim.ai4everyone.top is allowed; empty domains are invalid")


def openssl(*args: str, data: bytes | None = None, descriptors=()) -> bytes:
    result = subprocess.run(["openssl", *args], input=data, capture_output=True, pass_fds=descriptors)
    if result.returncode:
        error = RuntimeError(f"OpenSSL {args[0]} failed rc={result.returncode}")
        error.returncode = result.returncode
        error.private_stderr = result.stderr
        raise error
    return result.stdout


def validate_metadata(sans: list[str], not_before: datetime, not_after: datetime,
                      cert_public: bytes, key_public: bytes, chain_ok: bool,
                      now: datetime, minimum_seconds: int = 7 * 86400) -> dict:
    if sans != [DOMAIN]:
        raise ValueError("SAN must contain only the exact OpenSim domain")
    if minimum_seconds < 1 or any(value.tzinfo is None for value in (now, not_before, not_after)):
        raise ValueError("Invalid expiry policy or timezone")
    if not_before > now or (not_after - now).total_seconds() < minimum_seconds:
        raise ValueError("Certificate is not yet valid, expired, or too near expiry")
    if not chain_ok:
        raise ValueError("Certificate chain verification failed")
    if not cert_public or not key_public or cert_public != key_public:
        raise ValueError("Certificate/private-key public keys mismatch or empty")
    return {"domain": DOMAIN, "sans": sans, "not_before": not_before.isoformat(),
            "not_after": not_after.isoformat(), "chain": "PASS", "key_match": "PASS",
            "public_key_sha256": hashlib.sha256(cert_public).hexdigest()}


def validate_certificate(pem: bytes, key_public: bytes, minimum_seconds: int = 7 * 86400,
                         *, trust_file: Path | None = None, now: datetime | None = None) -> dict:
    """Validate actual PEM and DER public keys. trust_file is for synthetic CA tests only."""
    certificates = re.findall(
        rb"-----BEGIN CERTIFICATE-----\s+.*?-----END CERTIFICATE-----", pem, re.S)
    if len(certificates) < 2 or re.sub(
            rb"-----BEGIN CERTIFICATE-----\s+.*?-----END CERTIFICATE-----", b"", pem, flags=re.S).strip():
        raise ValueError("Expected a nonempty leaf plus chain PEM, no extra content")
    leaf = certificates[0] + b"\n"
    san_text = openssl("x509", "-noout", "-ext", "subjectAltName", data=leaf).decode("ascii")
    entries = san_text.splitlines()[1:]
    sans = [part.strip()[4:] for part in ",".join(entries).split(",") if part.strip().startswith("DNS:")]
    if any(part.strip() and not part.strip().startswith("DNS:")
           for part in ",".join(entries).split(",")):
        raise ValueError("Non-DNS SAN entries are forbidden")
    dates = openssl("x509", "-noout", "-dates", data=leaf).decode("ascii").splitlines()
    parsed = {}
    for line in dates:
        name, value = line.split("=", 1)
        parsed[name] = datetime.strptime(value, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    cert_public = openssl("pkey", "-pubin", "-outform", "DER",
                          data=openssl("x509", "-pubkey", "-noout", data=leaf))
    # OpenSSL verifies using system CA trust; never trust the presented root.
    chain = b"\n".join(certificates[1:]) + b"\n"
    fd = os.memfd_create("opensim-public-chain", 0)
    try:
        os.write(fd, chain)
        os.lseek(fd, 0, os.SEEK_SET)
        argv = ["openssl", "verify", "-purpose", "sslserver", "-verify_hostname", DOMAIN,
                "-untrusted", f"/proc/self/fd/{fd}"]
        if trust_file is not None:
            argv += ["-CAfile", str(trust_file), "-no-CApath", "-no-CAstore"]
        result = subprocess.run(argv, input=leaf, capture_output=True, pass_fds=(fd,))
        if result.returncode:
            raise RuntimeError(f"Certificate chain verification failed rc={result.returncode}")
    finally:
        os.close(fd)
    metadata = validate_metadata(sans, parsed["notBefore"], parsed["notAfter"],
                                 cert_public, key_public, True, now or datetime.now(timezone.utc),
                                 minimum_seconds)
    metadata["certificate_sha256"] = hashlib.sha256(pem).hexdigest()
    metadata["scope"] = "INSTALLED_CERTIFICATE_ONLY_NOT_RUNTIME_TLS_OR_RENEWAL"
    return metadata


def check_certificate(cert: Path, key: Path, minimum_seconds: int = 7 * 86400) -> dict:
    if os.geteuid() != 0:
        raise PermissionError("Installed certificate/key check requires established local root chain")
    if cert != TLS / "opensim.crt" or key != TLS / "opensim.key":
        raise ValueError("Only installed OpenSim certificate/key paths are accepted")
    fd = os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        key_stat = os.fstat(fd)
        owners = (0, pwd.getpwnam("docsweb").pw_uid)
        if (not stat.S_ISREG(key_stat.st_mode) or key_stat.st_uid not in owners
                or stat.S_IMODE(key_stat.st_mode) not in (0o400, 0o600)):
            raise PermissionError("Installed key must be root/docsweb-owned regular file, mode 0400/0600")
        pem = read_regular(cert)
        key_public = openssl("pkey", "-in", f"/proc/self/fd/{fd}", "-pubout", "-outform", "DER",
                             descriptors=(fd,))
    finally:
        os.close(fd)
    return validate_certificate(pem, key_public, minimum_seconds)


def refuse_mutation(action: str, domain: str) -> None:
    require_domain(domain)
    raise PermissionError(f"{ISSUE['id']}: {ISSUE['reason']} {action} disabled; no credential/key access.")


def inspect_local(domain: str) -> dict:
    require_domain(domain)
    addresses = subprocess.run(["hostname", "-I"], capture_output=True, text=True, check=True)
    if os.geteuid() != 0 or EDGES[0] not in addresses.stdout.split():
        raise PermissionError("ACME inspection must run as root locally on .183")
    if not ACME.is_file() or not os.access(ACME, os.X_OK):
        raise FileNotFoundError("Existing .183 acme executable missing")
    with existing_lock(ACME_LOCK):
        return {"acme": str(ACME), "acme_lock": str(ACME_LOCK), "lock_acquired": True,
                "domain": domain, "production_ready": False,
                "all_writers_lock_verified": False, "root_broker_verified": False,
                "credentials_accessed": False, "issue": ISSUE}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("action", choices=("inspect", "check", "apply", "renew", "revert"))
    parser.add_argument("--domain", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if EVIDENCE.resolve() not in out.parents:
        parser.error("--out must be a new directory under seat-edge")
    out.mkdir(parents=True, exist_ok=False)
    report = {"action": args.action, "verdict": "FAIL", "issue": ISSUE,
              "collected_at": datetime.now(timezone.utc).isoformat()}
    try:
        require_domain(args.domain)
        if args.action == "inspect":
            report.update(inspect_local(args.domain), verdict="INSPECT_ONLY")
        elif args.action == "check":
            addresses = subprocess.run(["hostname", "-I"], capture_output=True, text=True, check=True)
            if not set(EDGES).intersection(addresses.stdout.split()):
                raise PermissionError("Installed certificate check must run on .183/.184")
            with existing_lock(ACME_LOCK):
                report.update(check_certificate(TLS / "opensim.crt", TLS / "opensim.key"),
                              verdict="CHECK_ONLY", production_ready=False)
        else:
            refuse_mutation(args.action, args.domain)
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
    write_new(out / "report.json", (json.dumps(report, indent=2) + "\n").encode())
    line = f"OPENSIM_CERT verdict={report['verdict']} action={args.action} report={out / 'report.json'}"
    write_new(out / "execution.log", (line + "\n" + str(report.get("failure", "")) + "\n").encode())
    print(line)
    return 1 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
