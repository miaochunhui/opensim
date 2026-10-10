"""Offline OpenSim certificate protocol, NOT a production root controller.

All cryptographic private material is synthetic RAM/memfd input. There is no
SSH executor, installed-key reader, persistent recipient or deployment path.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time

DOMAIN = "opensim.ai4everyone.top"
SOURCE = "192.168.1.183"
TARGET = "192.168.1.184"
EVIDENCE = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-cert-transport")
MAX_BYTES = 256 * 1024
MAX_TTL = 120
REQUEST_FIELDS = {
    "version", "action", "domain", "source", "target", "nonce", "issued",
    "expires", "recipient", "recipient_pin", "source_pin",
}
PEM_CERT = re.compile(
    rb"-----" + rb"BEGIN CERTIFICATE" + rb"-----\s+.*?-----END CERTIFICATE-----", re.S)
SEALS = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
ISSUE = "CERT_TRANSPORT_BOOTSTRAP_REQUIRED"


class ProtocolError(ValueError):
    """Only public operation/reason codes may escape to logs."""


class CryptoError(ProtocolError):
    def __init__(self, operation: str, returncode: int):
        self.operation = operation
        self.returncode = returncode
        super().__init__(f"OPENSSL_FAILED operation={operation} rc={returncode}")


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def encode(body: bytes) -> str:
    return base64.b64encode(body).decode("ascii")


def decode(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > MAX_BYTES * 2:
        raise ProtocolError("INVALID_BASE64")
    try:
        body = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise ProtocolError("INVALID_BASE64") from None
    if not body or len(body) > MAX_BYTES or encode(body) != value:
        raise ProtocolError("INVALID_BASE64")
    return body


def parse(body: bytes, fields: set[str]) -> dict:
    if not isinstance(body, bytes) or not body or len(body) > MAX_BYTES:
        raise ProtocolError("INVALID_FRAME_SIZE")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProtocolError("DUPLICATE_FIELD")
            result[key] = value
        return result

    try:
        value = json.loads(body, object_pairs_hook=pairs)
    except (ValueError, UnicodeError):
        raise ProtocolError("INVALID_JSON") from None
    if not isinstance(value, dict) or set(value) != fields:
        raise ProtocolError("UNKNOWN_OR_MISSING_FIELD")
    if canonical(value) != body:
        raise ProtocolError("NONCANONICAL_FRAME")
    return value


def clock(now: int | None) -> int:
    value = int(time.time()) if now is None else now
    if type(value) is not int or value < 1:
        raise ProtocolError("INVALID_CLOCK")
    return value


def require_pin(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ProtocolError("INVALID_PIN")


def validate_request(body: bytes, now: int | None = None) -> dict:
    request = parse(body, REQUEST_FIELDS)
    if (type(request["version"]) is not int or request["version"] != 1
            or request["action"] != "transfer" or request["domain"] != DOMAIN
            or request["source"] != SOURCE or request["target"] != TARGET):
        raise ProtocolError("UNKNOWN_REQUEST_OR_SCOPE")
    if (not isinstance(request["nonce"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", request["nonce"])):
        raise ProtocolError("INVALID_NONCE")
    issued, expires = request["issued"], request["expires"]
    if (type(issued) is not int or type(expires) is not int
            or issued < 1 or not 0 < expires - issued <= MAX_TTL):
        raise ProtocolError("INVALID_LIFETIME")
    current = clock(now)
    if not issued <= current < expires:
        raise ProtocolError("EXPIRED_OR_FUTURE_REQUEST")
    require_pin(request["source_pin"])
    require_pin(request["recipient_pin"])
    recipient = decode(request["recipient"])
    if not hmac.compare_digest(digest(recipient), request["recipient_pin"]):
        raise ProtocolError("RECIPIENT_PIN_MISMATCH")
    return request


@contextmanager
def memory_file(body: bytes):
    if not isinstance(body, bytes) or len(body) > MAX_BYTES:
        raise ProtocolError("INVALID_MEMORY_INPUT")
    fd = os.memfd_create("opensim-offline-protocol", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        if os.write(fd, body) != len(body):
            raise ProtocolError("SHORT_MEMORY_WRITE")
        os.lseek(fd, 0, os.SEEK_SET)
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, SEALS)
        yield fd
    finally:
        os.close(fd)


def check_key_metadata(metadata: os.stat_result, *, owner: int = 0) -> None:
    """Production controller must apply this to its already opened source fd."""
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != owner
            or stat.S_IMODE(metadata.st_mode) not in (0o400, 0o600)
            or metadata.st_nlink != 1):
        raise ProtocolError("SOURCE_KEY_OWNERSHIP_OR_MODE")


def require_synthetic_fd(fd: int) -> None:
    """Memfd validation is not isolation from another process with the same UID."""
    if os.geteuid() == 0:
        raise ProtocolError("ROOT_MUTABLE_TREE_EXECUTION_FORBIDDEN")
    metadata = os.fstat(fd)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
            or metadata.st_size < 1 or metadata.st_size > MAX_BYTES
            or not os.readlink(f"/proc/self/fd/{fd}").startswith("/memfd:")
            or fcntl.fcntl(fd, fcntl.F_GET_SEALS) & SEALS != SEALS):
        raise ProtocolError("SYNTHETIC_SEALED_MEMFD_REQUIRED")


def openssl(operation: str, args: list[str], *, data: bytes | None = None,
            descriptors=()) -> bytes:
    result = subprocess.run(["openssl", *args], input=data, capture_output=True,
                            pass_fds=descriptors)
    if result.returncode:
        # Never attach private stdout/stderr or input to a public exception.
        raise CryptoError(operation, result.returncode)
    return result.stdout


def fd_path(fd: int) -> str:
    return f"/proc/self/fd/{fd}"


def require_der_frame(body: bytes) -> None:
    """OpenSSL may accept trailing input; require exactly one definite DER sequence."""
    if len(body) < 2 or body[0] != 0x30:
        raise ProtocolError("INVALID_DER_FRAME")
    size = body[1]
    header = 2
    if size & 0x80:
        count = size & 0x7f
        if not 1 <= count <= 4 or len(body) < 2 + count or body[2] == 0:
            raise ProtocolError("INVALID_DER_FRAME")
        size = int.from_bytes(body[2:2 + count], "big")
        header += count
        if size < 128:
            raise ProtocolError("NONCANONICAL_DER_FRAME")
    if header + size != len(body):
        raise ProtocolError("TRUNCATED_OR_TRAILING_DER_FRAME")


def der_items(body: bytes) -> list[tuple[int, bytes]]:
    """Read only the bounded, definite-length DER subset used by this protocol."""
    items, offset = [], 0
    while offset < len(body):
        if len(items) >= 16 or offset + 2 > len(body):
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        tag, size = body[offset], body[offset + 1]
        offset += 2
        if tag & 0x1f == 0x1f:
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        if size & 0x80:
            count = size & 0x7f
            if not 1 <= count <= 4 or offset + count > len(body) or body[offset] == 0:
                raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
            size = int.from_bytes(body[offset:offset + count], "big")
            offset += count
            if size < 128:
                raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        if offset + size > len(body):
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        items.append((tag, body[offset:offset + size]))
        offset += size
    return items


def validate_cipher_suite(body: bytes) -> None:
    """Reject implicit SHA1 defaults and multi-recipient/algorithm substitutions."""
    oid_sha256 = bytes.fromhex("608648016503040201")
    oid_oaep = bytes.fromhex("2a864886f70d010107")
    oid_mgf1 = bytes.fromhex("2a864886f70d010108")
    oid_pspecified = bytes.fromhex("2a864886f70d010109")

    def sequence(raw: bytes) -> list[tuple[int, bytes]]:
        items = der_items(raw)
        if len(items) != 1 or items[0][0] != 0x30:
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        return der_items(items[0][1])

    def sha256_algorithm(raw: bytes) -> bool:
        return sequence(raw) in ([(6, oid_sha256)], [(6, oid_sha256), (5, b"")])

    try:
        outer = sequence(body)
        if (len(outer) != 2 or outer[0] != (6, bytes.fromhex("2a864886f70d0109100117"))
                or outer[1][0] != 0xa0):
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        envelope = sequence(outer[1][1])
        if (len(envelope) != 4 or [tag for tag, _ in envelope] != [2, 0x31, 0x30, 4]
                or envelope[0][1] != b"\0"):
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        recipients = der_items(envelope[1][1])
        if len(recipients) != 1 or recipients[0][0] != 0x30:
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        recipient = der_items(recipients[0][1])
        if (len(recipient) != 4 or [tag for tag, _ in recipient] != [2, 0x30, 0x30, 4]
                or recipient[0][1] != b"\0"):
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        algorithm = der_items(recipient[2][1])
        if len(algorithm) != 2 or algorithm[0] != (6, oid_oaep) or algorithm[1][0] != 0x30:
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        parameters = der_items(algorithm[1][1])
        if len(parameters) not in (2, 3) or [tag for tag, _ in parameters[:2]] != [0xa0, 0xa1]:
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        if not sha256_algorithm(parameters[0][1]):
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        mask = sequence(parameters[1][1])
        if len(mask) != 2 or mask[0] != (6, oid_mgf1) or mask[1][0] != 0x30:
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        if der_items(mask[1][1]) not in ([(6, oid_sha256)], [(6, oid_sha256), (5, b"")]):
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        if len(parameters) == 3 and (parameters[2][0] != 0xa2 or
                sequence(parameters[2][1]) != [(6, oid_pspecified), (4, b"")]):
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        content = der_items(envelope[2][1])
        if (len(content) != 3 or content[0] != (6, bytes.fromhex("2a864886f70d010701"))
                or content[1][0] != 0x30 or content[2][0] != 0x80):
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        cipher = der_items(content[1][1])
        if (len(cipher) != 2 or cipher[0] != (6, bytes.fromhex("60864801650304012e"))
                or cipher[1][0] != 0x30):
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
        gcm = der_items(cipher[1][1])
        if (len(gcm) != 2 or gcm[0][0] != 4 or len(gcm[0][1]) != 12
                or gcm[1] != (2, b"\x10") or len(envelope[3][1]) != 16):
            raise ProtocolError("UNSUPPORTED_CIPHER_SUITE")
    except (IndexError, ValueError, TypeError):
        raise ProtocolError("UNSUPPORTED_CIPHER_SUITE") from None


def leaf_and_chain(bundle: bytes) -> tuple[bytes, bytes]:
    certificates = PEM_CERT.findall(bundle)
    if len(certificates) < 2 or PEM_CERT.sub(b"", bundle).strip():
        raise ProtocolError("LEAF_AND_CHAIN_REQUIRED")
    return certificates[0] + b"\n", b"\n".join(certificates[1:]) + b"\n"


def validate_pair(bundle: bytes, key_fd: int, trust: bytes, *, now: int,
                  minimum_seconds: int = 7 * 86400) -> dict:
    """Offline validation with explicit synthetic trust, never presented-root trust."""
    require_synthetic_fd(key_fd)
    if type(minimum_seconds) is not int or minimum_seconds < 1 or not trust:
        raise ProtocolError("INVALID_CERTIFICATE_POLICY")
    now = clock(now)
    leaf, chain = leaf_and_chain(bundle)
    san = openssl("certificate_san", ["x509", "-noout", "-ext", "subjectAltName"],
                  data=leaf).decode("ascii")
    if [item.strip() for item in ",".join(san.splitlines()[1:]).split(",")] != [
            "DNS:" + DOMAIN]:
        raise ProtocolError("EXACT_SOLE_OPENSIM_SAN_REQUIRED")
    with memory_file(chain) as chain_fd, memory_file(trust) as trust_fd:
        openssl("certificate_chain", [
            "verify", "-purpose", "sslserver", "-verify_hostname", DOMAIN,
            "-attime", str(now), "-CAfile", fd_path(trust_fd), "-no-CApath", "-no-CAstore",
            "-untrusted", fd_path(chain_fd)], data=leaf,
            descriptors=(chain_fd, trust_fd))
        openssl("certificate_expiry_margin", [
            "verify", "-purpose", "sslserver", "-verify_hostname", DOMAIN,
            "-attime", str(now + minimum_seconds), "-CAfile", fd_path(trust_fd),
            "-no-CApath", "-no-CAstore", "-untrusted", fd_path(chain_fd)],
            data=leaf, descriptors=(chain_fd, trust_fd))
    public_pem = openssl("certificate_public", ["x509", "-pubkey", "-noout"], data=leaf)
    cert_public = openssl("certificate_public_der", ["pkey", "-pubin", "-outform", "DER"],
                          data=public_pem)
    key_public = openssl("key_public_der", [
        "pkey", "-in", fd_path(key_fd), "-pubout", "-outform", "DER"], descriptors=(key_fd,))
    if not cert_public or not hmac.compare_digest(cert_public, key_public):
        raise ProtocolError("CERTIFICATE_KEY_MISMATCH")
    return {"domain": DOMAIN, "chain": "PASS", "san": "PASS", "key_match": "PASS",
            "certificate_sha256": digest(bundle)}


class ReplayGuard:
    """Source controller lifetime state; never instantiate per incoming message."""

    def __init__(self):
        self._lock = threading.Lock()
        self._used: set[str] = set()

    def claim(self, nonce: str) -> None:
        with self._lock:
            if nonce in self._used:
                raise ProtocolError("SOURCE_REQUEST_ALREADY_CONSUMED")
            self._used.add(nonce)


def seal(request_body: bytes, source_key_fd: int, certificate: bytes, *, replay: ReplayGuard,
         request_pin: str, recipient_pin: str, now: int | None = None) -> bytes:
    """Called ONLY inside the synthetic source endpoint, never by the relay."""
    request = validate_request(request_body, now)
    require_pin(request_pin)
    require_pin(recipient_pin)
    if (not hmac.compare_digest(digest(request_body), request_pin)
            or not hmac.compare_digest(request["recipient_pin"], recipient_pin)):
        raise ProtocolError("TRUSTED_CONTEXT_PIN_MISMATCH")
    if not hmac.compare_digest(digest(certificate), request["source_pin"]):
        raise ProtocolError("SOURCE_CERTIFICATE_PIN_MISMATCH")
    require_synthetic_fd(source_key_fd)
    leaf_and_chain(certificate)
    replay.claim(request["nonce"])
    private = os.pread(source_key_fd, MAX_BYTES + 1, 0)
    if not private or len(private) > MAX_BYTES:
        raise ProtocolError("INVALID_SOURCE_MEMORY")
    content = canonical({"request": encode(request_body), "certificate": encode(certificate),
                         "key": encode(private)})
    signature = openssl("source_signature", [
        "dgst", "-sha256", "-sign", fd_path(source_key_fd)],
        data=content, descriptors=(source_key_fd,))
    payload = canonical({"content": encode(content), "signature": encode(signature)})
    with memory_file(decode(request["recipient"])) as recipient_fd:
        encrypted = openssl("authenticated_encrypt", [
            "cms", "-encrypt", "-binary", "-outform", "DER", "-aes-256-gcm",
            "-recip", fd_path(recipient_fd), "-keyopt", "rsa_padding_mode:oaep",
            "-keyopt", "rsa_oaep_md:sha256", "-keyopt", "rsa_mgf1_md:sha256"],
            data=payload, descriptors=(recipient_fd,))
    validate_request(request_body, now)
    frame = canonical({"version": 1, "request_pin": request_pin, "ciphertext": encode(encrypted)})
    if len(frame) > MAX_BYTES:
        raise ProtocolError("INVALID_FRAME_SIZE")
    return frame


class RecipientSession:
    """One-process synthetic recipient; no restore/persistence/retry operation."""

    def __init__(self, source_pin: str, *, now: int | None = None, ttl: int = MAX_TTL):
        if os.geteuid() == 0:
            raise ProtocolError("ROOT_MUTABLE_TREE_EXECUTION_FORBIDDEN")
        require_pin(source_pin)
        current = clock(now)
        if type(ttl) is not int or not 0 < ttl <= MAX_TTL:
            raise ProtocolError("INVALID_LIFETIME")
        self._lock = threading.Lock()
        self._state = "OPEN"
        self._deadline = time.monotonic() + ttl
        self._key = openssl("recipient_key", [
            "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072"])
        with memory_file(self._key) as key_fd:
            recipient = openssl("recipient_certificate", [
                "req", "-new", "-x509", "-key", fd_path(key_fd), "-days", "1",
                "-subj", "/CN=OpenSim ephemeral encryption recipient",
                "-addext", "keyUsage=critical,keyEncipherment"], descriptors=(key_fd,))
        self._request = canonical({
            "version": 1, "action": "transfer", "domain": DOMAIN, "source": SOURCE,
            "target": TARGET, "nonce": os.urandom(32).hex(), "issued": current,
            "expires": current + ttl, "recipient": encode(recipient),
            "recipient_pin": digest(recipient), "source_pin": source_pin,
        })

    @property
    def state(self) -> str:
        return self._state

    def offer(self) -> tuple[bytes, str, str]:
        """Pins must reach the source via a trusted controller, NOT relay assertions."""
        with self._lock:
            if self._state != "OPEN":
                raise ProtocolError("SESSION_ALREADY_CONSUMED")
            if time.monotonic() >= self._deadline:
                self._key, self._state = b"", "CLOSED"
                raise ProtocolError("EXPIRED_MONOTONIC_SESSION")
            try:
                request = validate_request(self._request)
            except ProtocolError:
                self._key, self._state = b"", "CLOSED"
                raise
            return self._request, digest(self._request), request["recipient_pin"]

    def close(self) -> None:
        with self._lock:
            self._key = b""
            self._state = "CLOSED"

    def accept(self, frame: bytes, trust: bytes, *, now: int | None = None,
               minimum_seconds: int = 7 * 86400) -> dict:
        # Burn the request before parsing/decryption: failed attempts cannot be retried.
        with self._lock:
            if self._state != "OPEN":
                raise ProtocolError("SESSION_ALREADY_CONSUMED")
            self._state = "CONSUMED"
            key, self._key = self._key, b""
        try:
            if time.monotonic() >= self._deadline:
                raise ProtocolError("EXPIRED_MONOTONIC_SESSION")
            request = validate_request(self._request, now)
            outer = parse(frame, {"version", "request_pin", "ciphertext"})
            if (type(outer["version"]) is not int or outer["version"] != 1
                    or outer["request_pin"] != digest(self._request)):
                raise ProtocolError("ENVELOPE_REQUEST_MISMATCH")
            encrypted = decode(outer["ciphertext"])
            require_der_frame(encrypted)
            validate_cipher_suite(encrypted)
            with memory_file(key) as key_fd:
                plaintext = openssl("authenticated_decrypt", [
                    "cms", "-decrypt", "-binary", "-inform", "DER", "-inkey", fd_path(key_fd)],
                    data=encrypted, descriptors=(key_fd,))
            signed = parse(plaintext, {"content", "signature"})
            content_body = decode(signed["content"])
            content = parse(content_body, {"request", "certificate", "key"})
            if decode(content["request"]) != self._request:
                raise ProtocolError("INNER_REQUEST_MISMATCH")
            certificate = decode(content["certificate"])
            if digest(certificate) != request["source_pin"]:
                raise ProtocolError("SOURCE_CERTIFICATE_PIN_MISMATCH")
            leaf, _ = leaf_and_chain(certificate)
            public = openssl("source_public", ["x509", "-pubkey", "-noout"], data=leaf)
            with memory_file(public) as public_fd, memory_file(decode(signed["signature"])) as sig_fd:
                openssl("source_signature_verify", [
                    "dgst", "-sha256", "-verify", fd_path(public_fd),
                    "-signature", fd_path(sig_fd)], data=content_body,
                    descriptors=(public_fd, sig_fd))
            with memory_file(decode(content["key"])) as source_fd:
                report = validate_pair(certificate, source_fd, trust, now=clock(now),
                                       minimum_seconds=minimum_seconds)
            validate_request(self._request, now)
            if time.monotonic() >= self._deadline:
                raise ProtocolError("EXPIRED_MONOTONIC_SESSION")
            return {**report, "verdict": "OFFLINE_PROTOCOL_PASS", "production_ready": False,
                    "private_output": "NONE", "session": "CONSUMED"}
        finally:
            key = b""


def ssh_plan(edge: str, controller: str) -> tuple[str, ...]:
    """A fixed reviewed-controller invocation plan; never executes SSH."""
    if edge not in (SOURCE, TARGET) or controller != "/usr/local/libexec/opensim-cert-controller":
        raise ProtocolError("FIXED_CONTROLLER_AND_EDGE_REQUIRED")
    return ("ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "UpdateHostKeys=no", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes",
            "-o", "RequestTTY=no", "infra@" + edge, "sudo", "-n", "--", controller)


def refuse_production(action: str) -> None:
    raise ProtocolError(
        f"{ISSUE}: action={action}; reviewed root-owned bootstrap, explicit owner window "
        "and operation authorization missing; production transport disabled")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("action", choices=("prepare", "transfer", "apply", "renew"))
    args = parser.parse_args(argv)
    try:
        refuse_production(args.action)
    except ProtocolError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    raise AssertionError("Production refusal must never return")


if __name__ == "__main__":
    sys.exit(main())
