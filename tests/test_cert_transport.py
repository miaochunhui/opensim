"""Real offline OpenSSL protocol acceptance; no persisted private fixtures."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import io
import json
import multiprocessing
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import cert_transport as transport

NOW = int(time.time())
FAILURE_COORDINATES = []


def crypto(operation, args, *, data=None, descriptors=()):
    return transport.openssl(operation, args, data=data, descriptors=descriptors)


def generate_key() -> bytes:
    return crypto("synthetic_key", [
        "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"])


def generate_ca(key: bytes) -> bytes:
    with transport.memory_file(key) as fd:
        return crypto("synthetic_ca", [
            "req", "-new", "-x509", "-key", transport.fd_path(fd), "-days", "365",
            "-subj", "/CN=OpenSim offline synthetic CA",
            "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign"], descriptors=(fd,))


def signed_bundle(key: bytes, ca: bytes, ca_key: bytes, *, san=None, days=30) -> bytes:
    san = "DNS:" + transport.DOMAIN if san is None else san
    with transport.memory_file(key) as fd:
        csr = crypto("synthetic_csr", [
            "req", "-new", "-key", transport.fd_path(fd),
            "-subj", "/CN=" + transport.DOMAIN, "-addext", "subjectAltName=" + san,
            "-addext", "extendedKeyUsage=serverAuth"], descriptors=(fd,))
    with transport.memory_file(csr) as csr_fd, transport.memory_file(ca) as ca_fd, \
            transport.memory_file(ca_key) as key_fd:
        leaf = crypto("synthetic_certificate", [
            "x509", "-req", "-in", transport.fd_path(csr_fd),
            "-CA", transport.fd_path(ca_fd), "-CAkey", transport.fd_path(key_fd),
            "-set_serial", "1", "-days", str(days), "-copy_extensions", "copy"],
            descriptors=(csr_fd, ca_fd, key_fd))
    return leaf + ca


def private_pem_present(body: bytes) -> bool:
    return b"-----" + b"BEGIN " in body and b"PRIVATE KEY" in body


def scan_evidence(root: Path) -> tuple[int, int]:
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise RuntimeError("EMPTY_EVIDENCE_SCAN")
    positive = b"-----" + b"BEGIN " + b"EC PRIVATE KEY" + b"-----"
    if not private_pem_present(positive) or private_pem_present(b"public report"):
        raise RuntimeError("PRIVATE_PEM_DETECTOR_CONTROL_FAILED")
    hits = sum(private_pem_present(path.read_bytes()) for path in files)
    return len(files), hits


def isolated_source(connection):
    """Generate the transported key only in child RAM; never send it to relay."""
    try:
        ca_key = generate_key()
        ca = generate_ca(ca_key)
        source_key = generate_key()
        certificate = signed_bundle(source_key, ca, ca_key)
        connection.send_bytes(transport.canonical({
            "certificate": transport.encode(certificate), "trust": transport.encode(ca)}))
        offer = transport.parse(connection.recv_bytes(), {"request", "pin", "recipient_pin"})
        with transport.memory_file(source_key) as source_fd:
            frame = transport.seal(
                transport.decode(offer["request"]), source_fd, certificate,
                replay=transport.ReplayGuard(),
                request_pin=offer["pin"], recipient_pin=offer["recipient_pin"])
        if source_key in frame or private_pem_present(frame):
            raise RuntimeError("PRIVATE_SOURCE_OUTPUT")
        connection.send_bytes(frame)
        source_key = ca_key = b""
    except BaseException:
        # Child failure cannot ship exception values or private tracebacks to infra.
        connection.send_bytes(b'{"failure":"SOURCE_CHILD_FAILED"}')
        raise
    finally:
        connection.close()


class CertTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global NOW
        cls.ca_key = generate_key()
        cls.ca = generate_ca(cls.ca_key)
        cls.key = generate_key()
        cls.bundle = signed_bundle(cls.key, cls.ca, cls.ca_key)
        cls.wrong_key = generate_key()
        NOW = int(time.time())

    @classmethod
    def tearDownClass(cls):
        cls.ca_key = cls.key = cls.wrong_key = b""

    def setUp(self):
        self.session = transport.RecipientSession(transport.digest(self.bundle), now=NOW)
        self.addCleanup(self.session.close)
        self.request, self.pin, self.recipient_pin = self.session.offer()
        self.replay = transport.ReplayGuard()

    def seal(self, *, request=None, key=None, bundle=None, pin=None, recipient_pin=None, now=None):
        with transport.memory_file(self.key if key is None else key) as fd:
            return transport.seal(
                self.request if request is None else request, fd,
                self.bundle if bundle is None else bundle,
                replay=self.replay,
                request_pin=self.pin if pin is None else pin,
                recipient_pin=self.recipient_pin if recipient_pin is None else recipient_pin,
                now=NOW if now is None else now)

    def frame_with_payload(self, content: bytes, signature: bytes, cipher="-aes-256-gcm") -> bytes:
        payload = transport.canonical({"content": transport.encode(content),
                                       "signature": transport.encode(signature)})
        request = transport.validate_request(self.request, NOW)
        with transport.memory_file(transport.decode(request["recipient"])) as recipient_fd:
            encrypted = crypto("negative_authenticated_encrypt", [
                "cms", "-encrypt", "-binary", "-outform", "DER", cipher,
                "-recip", transport.fd_path(recipient_fd), "-keyopt", "rsa_padding_mode:oaep",
                "-keyopt", "rsa_oaep_md:sha256"], data=payload, descriptors=(recipient_fd,))
        return transport.canonical({"version": 1, "request_pin": self.pin,
                                    "ciphertext": transport.encode(encrypted)})

    def content(self, *, request=None, key=None, bundle=None) -> bytes:
        return transport.canonical({
            "request": transport.encode(self.request if request is None else request),
            "certificate": transport.encode(self.bundle if bundle is None else bundle),
            "key": transport.encode(self.key if key is None else key)})

    def sign_content(self, content: bytes) -> bytes:
        with transport.memory_file(self.key) as fd:
            return crypto("negative_signature", [
                "dgst", "-sha256", "-sign", transport.fd_path(fd)],
                data=content, descriptors=(fd,))

    def test_real_openssl_roundtrip(self):
        frame = self.seal()
        report = self.session.accept(frame, self.ca, now=NOW)
        self.assertEqual(report["verdict"], "OFFLINE_PROTOCOL_PASS")
        self.assertEqual(report["chain"], "PASS")
        self.assertEqual(report["san"], "PASS")
        self.assertEqual(report["key_match"], "PASS")
        self.assertFalse(report["production_ready"])
        self.assertEqual(report["private_output"], "NONE")
        self.assertFalse(private_pem_present(frame))
        self.assertFalse(self.key in frame)
        outer = transport.parse(frame, {"version", "request_pin", "ciphertext"})
        cms = transport.decode(outer["ciphertext"])
        details = crypto("public_cms_inspect", [
            "cms", "-cmsout", "-print", "-inform", "DER"], data=cms)
        self.assertTrue(b"id-smime-ct-authEnvelopedData" in details)
        self.assertTrue(b"aes-256-gcm" in details)
        self.assertTrue(b"rsaesOaep" in details)

    def test_replay_after_success(self):
        frame = self.seal()
        self.session.accept(frame, self.ca, now=NOW)
        with self.assertRaisesRegex(transport.ProtocolError, "ALREADY_CONSUMED"):
            self.session.accept(frame, self.ca, now=NOW)

    def test_source_replay_rejected_before_plaintext_read(self):
        self.seal()
        with patch.object(transport.os, "pread", side_effect=AssertionError("NO_REPLAY_READ")):
            with self.assertRaisesRegex(transport.ProtocolError, "SOURCE_REQUEST_ALREADY"):
                self.seal()

    def test_source_failed_crypto_burns_nonce(self):
        with patch.object(transport, "openssl", side_effect=transport.CryptoError("source_signature", 7)):
            with self.assertRaises(transport.CryptoError):
                self.seal()
        with self.assertRaisesRegex(transport.ProtocolError, "SOURCE_REQUEST_ALREADY"):
            self.seal()

    def test_source_expires_during_encryption(self):
        with patch.object(transport.time, "time", side_effect=[NOW, NOW + transport.MAX_TTL]), \
                transport.memory_file(self.key) as fd:
            with self.assertRaisesRegex(transport.ProtocolError, "EXPIRED_OR_FUTURE"):
                transport.seal(self.request, fd, self.bundle, replay=self.replay,
                               request_pin=self.pin, recipient_pin=self.recipient_pin)

    def test_target_expires_during_validation(self):
        frame = self.seal()
        with patch.object(transport.time, "time",
                          side_effect=[NOW, NOW, NOW + transport.MAX_TTL]):
            with self.assertRaisesRegex(transport.ProtocolError, "EXPIRED_OR_FUTURE"):
                self.session.accept(frame, self.ca)

    def test_unsigned_cipher_downgrade_rejected_before_decrypt(self):
        content = self.content()
        frame = self.frame_with_payload(content, self.sign_content(content), cipher="-aes-256-cbc")
        with self.assertRaisesRegex(transport.ProtocolError, "UNSUPPORTED_CIPHER_SUITE"):
            self.session.accept(frame, self.ca, now=NOW)

    def test_ciphertext_tamper_burns_session(self):
        frame = self.seal()
        outer = transport.parse(frame, {"version", "request_pin", "ciphertext"})
        encrypted = bytearray(transport.decode(outer["ciphertext"]))
        encrypted[-1] ^= 1
        outer["ciphertext"] = transport.encode(bytes(encrypted))
        with self.assertRaisesRegex(transport.CryptoError, "authenticated_decrypt"):
            self.session.accept(transport.canonical(outer), self.ca, now=NOW)
        with self.assertRaisesRegex(transport.ProtocolError, "ALREADY_CONSUMED"):
            self.session.accept(frame, self.ca, now=NOW)

    def test_ciphertext_trailing_garbage_and_truncation(self):
        outer = transport.parse(self.seal(), {"version", "request_pin", "ciphertext"})
        body = transport.decode(outer["ciphertext"])
        for tampered in (body + b"unapproved", body[:-1]):
            with self.assertRaisesRegex(transport.ProtocolError, "DER_FRAME"):
                transport.require_der_frame(tampered)
        outer["ciphertext"] = transport.encode(body + b"unapproved")
        with self.assertRaisesRegex(transport.ProtocolError, "DER_FRAME"):
            self.session.accept(transport.canonical(outer), self.ca, now=NOW)

    def test_invalid_response_burns_target_session(self):
        with self.assertRaises(transport.ProtocolError):
            self.session.accept(b"", self.ca, now=NOW)
        with self.assertRaisesRegex(transport.ProtocolError, "ALREADY_CONSUMED"):
            self.session.accept(self.seal(), self.ca, now=NOW)

    def test_wrong_recipient_session(self):
        other = transport.RecipientSession(transport.digest(self.bundle), now=NOW)
        self.addCleanup(other.close)
        with self.assertRaisesRegex(transport.ProtocolError, "ENVELOPE_REQUEST"):
            other.accept(self.seal(), self.ca, now=NOW)

    def test_recipient_key_cannot_decrypt_other_ciphertext(self):
        other = transport.RecipientSession(transport.digest(self.bundle), now=NOW)
        self.addCleanup(other.close)
        outer = transport.parse(self.seal(), {"version", "request_pin", "ciphertext"})
        other_request, other_pin, _ = other.offer()
        outer["request_pin"] = other_pin
        with self.assertRaisesRegex(transport.CryptoError, "authenticated_decrypt"):
            other.accept(transport.canonical(outer), self.ca, now=NOW)
        self.assertTrue(other_request != self.request)

    def test_request_pin_substitution_rejected_before_source_read(self):
        request = transport.validate_request(self.request, NOW)
        request["nonce"] = os.urandom(32).hex()
        with patch.object(transport.os, "pread", side_effect=AssertionError("SOURCE_READ_FORBIDDEN")):
            with self.assertRaisesRegex(transport.ProtocolError, "TRUSTED_CONTEXT"):
                self.seal(request=transport.canonical(request))

    def test_recipient_public_key_pin_substitution(self):
        with self.assertRaisesRegex(transport.ProtocolError, "TRUSTED_CONTEXT"):
            self.seal(recipient_pin=transport.digest(b"wrong pin"))
        request = transport.validate_request(self.request, NOW)
        request["recipient_pin"] = transport.digest(b"wrong public key")
        with self.assertRaisesRegex(transport.ProtocolError, "RECIPIENT_PIN_MISMATCH"):
            self.seal(request=transport.canonical(request))

    def test_wrong_source_certificate_pin(self):
        with self.assertRaisesRegex(transport.ProtocolError, "SOURCE_CERTIFICATE_PIN"):
            self.seal(bundle=signed_bundle(self.key, self.ca, self.ca_key, days=29))

    def test_expiry_at_exact_boundary(self):
        frame = self.seal()
        with self.assertRaisesRegex(transport.ProtocolError, "EXPIRED_OR_FUTURE"):
            self.session.accept(frame, self.ca, now=NOW + transport.MAX_TTL)

    def test_source_refuses_expiry_before_plaintext_read(self):
        with patch.object(transport.os, "pread", side_effect=AssertionError("SOURCE_READ_FORBIDDEN")):
            with self.assertRaisesRegex(transport.ProtocolError, "EXPIRED_OR_FUTURE"):
                self.seal(now=NOW + transport.MAX_TTL)

    def test_monotonic_expiry_survives_wall_clock_rollback(self):
        frame = self.seal()
        with patch.object(transport.time, "monotonic", return_value=self.session._deadline + 1):
            with self.assertRaisesRegex(transport.ProtocolError, "EXPIRED_MONOTONIC"):
                self.session.accept(frame, self.ca, now=NOW)

    def test_future_request(self):
        with self.assertRaisesRegex(transport.ProtocolError, "EXPIRED_OR_FUTURE"):
            transport.validate_request(self.request, NOW - 1)

    def test_unknown_action_domain_and_endpoints(self):
        changes = {
            "action": "renew", "domain": "other.ai4everyone.top",
            "source": transport.TARGET, "target": "192.168.1.185", "version": True,
        }
        for name, value in changes.items():
            with self.subTest(field=name):
                request = transport.validate_request(self.request, NOW)
                request[name] = value
                with self.assertRaisesRegex(transport.ProtocolError, "UNKNOWN_REQUEST"):
                    transport.validate_request(transport.canonical(request), NOW)

    def test_empty_invalid_oversized_duplicate_noncanonical_frames(self):
        for body in (b"", b"{", b"[]", b"x" * (transport.MAX_BYTES + 1),
                     b'{"x":1,"x":1}', b'{ "x":1}', b'{"unknown":1}'):
            with self.subTest(size=len(body)):
                with self.assertRaises(transport.ProtocolError):
                    transport.parse(body, {"x"})

    def test_missing_extra_request_fields(self):
        for action in ("missing", "extra"):
            request = transport.validate_request(self.request, NOW)
            if action == "missing":
                del request["nonce"]
            else:
                request["path"] = "/root/unapproved"
            with self.assertRaisesRegex(transport.ProtocolError, "UNKNOWN_OR_MISSING"):
                transport.validate_request(transport.canonical(request), NOW)

    def test_nonce_pins_and_lifetime_shapes(self):
        for field, value in (
                ("nonce", ""), ("nonce", "1"), ("source_pin", ""), ("recipient_pin", 1),
                ("issued", True), ("expires", NOW), ("expires", NOW + transport.MAX_TTL + 1)):
            with self.subTest(field=field):
                request = transport.validate_request(self.request, NOW)
                request[field] = value
                with self.assertRaises(transport.ProtocolError):
                    transport.validate_request(transport.canonical(request), NOW)
        for ttl in (0, -1, True, transport.MAX_TTL + 1):
            with self.assertRaisesRegex(transport.ProtocolError, "INVALID_LIFETIME"):
                transport.RecipientSession(transport.digest(self.bundle), now=NOW, ttl=ttl)

    def test_invalid_base64_and_empty_ciphertext(self):
        for value in ("", "?", "Zg", "Zg==\n", 1):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaisesRegex(transport.ProtocolError, "BASE64"):
                    transport.decode(value)

    def test_signed_inner_request_mismatch(self):
        request = transport.validate_request(self.request, NOW)
        request["nonce"] = os.urandom(32).hex()
        content = self.content(request=transport.canonical(request))
        frame = self.frame_with_payload(content, self.sign_content(content))
        with self.assertRaisesRegex(transport.ProtocolError, "INNER_REQUEST"):
            self.session.accept(frame, self.ca, now=NOW)

    def test_invalid_source_signature_even_with_valid_encryption(self):
        content = self.content()
        signature = bytearray(self.sign_content(content))
        signature[-1] ^= 1
        frame = self.frame_with_payload(content, bytes(signature))
        with self.assertRaisesRegex(transport.CryptoError, "source_signature_verify"):
            self.session.accept(frame, self.ca, now=NOW)

    def test_modified_plaintext_with_old_signature(self):
        original = self.content()
        changed = self.content(key=self.wrong_key)
        frame = self.frame_with_payload(changed, self.sign_content(original))
        with self.assertRaisesRegex(transport.CryptoError, "source_signature_verify"):
            self.session.accept(frame, self.ca, now=NOW)

    def test_correct_signature_wrong_keymatch(self):
        content = self.content(key=self.wrong_key)
        frame = self.frame_with_payload(content, self.sign_content(content))
        with self.assertRaisesRegex(transport.ProtocolError, "CERTIFICATE_KEY_MISMATCH"):
            self.session.accept(frame, self.ca, now=NOW)

    def test_wrong_domain_chain(self):
        bundle = signed_bundle(self.key, self.ca, self.ca_key, san="DNS:other.example.com")
        with transport.memory_file(self.key) as fd:
            with self.assertRaisesRegex(transport.ProtocolError, "EXACT_SOLE"):
                transport.validate_pair(bundle, fd, self.ca, now=NOW)

    def test_end_to_end_wrong_domain_certificate(self):
        bundle = signed_bundle(self.key, self.ca, self.ca_key, san="DNS:other.example.com")
        target = transport.RecipientSession(transport.digest(bundle), now=NOW)
        self.addCleanup(target.close)
        request, pin, recipient_pin = target.offer()
        frame = self.seal(request=request, pin=pin, recipient_pin=recipient_pin, bundle=bundle)
        with self.assertRaisesRegex(transport.ProtocolError, "EXACT_SOLE"):
            target.accept(frame, self.ca, now=NOW)

    def test_extra_wildcard_ip_or_duplicate_san(self):
        for san in ("DNS:" + transport.DOMAIN + ",DNS:other.example.com",
                    "DNS:*.ai4everyone.top", "DNS:" + transport.DOMAIN + ",IP:127.0.0.1",
                    "DNS:" + transport.DOMAIN + ",DNS:" + transport.DOMAIN):
            bundle = signed_bundle(self.key, self.ca, self.ca_key, san=san)
            with transport.memory_file(self.key) as fd:
                with self.assertRaisesRegex(transport.ProtocolError, "EXACT_SOLE"):
                    transport.validate_pair(bundle, fd, self.ca, now=NOW)

    def test_chain_uses_independent_trust_not_presented_root(self):
        untrusted_ca = generate_ca(self.wrong_key)
        with transport.memory_file(self.key) as fd:
            with self.assertRaisesRegex(transport.CryptoError, "certificate_chain"):
                transport.validate_pair(self.bundle, fd, untrusted_ca, now=NOW)

    def test_missing_malformed_chain_and_empty_key(self):
        leaf, _ = transport.leaf_and_chain(self.bundle)
        for bundle in (b"", leaf, self.bundle + b"extra"):
            with self.assertRaisesRegex(transport.ProtocolError, "LEAF_AND_CHAIN"):
                transport.leaf_and_chain(bundle)
        with transport.memory_file(b"") as fd:
            with self.assertRaisesRegex(transport.ProtocolError, "SYNTHETIC_SEALED"):
                transport.validate_pair(self.bundle, fd, self.ca, now=NOW)

    def test_certificate_near_expiry(self):
        with transport.memory_file(self.key) as fd:
            with self.assertRaisesRegex(transport.CryptoError, "certificate_expiry_margin"):
                transport.validate_pair(self.bundle, fd, self.ca, now=NOW,
                                        minimum_seconds=31 * 86400)

    def test_certificate_expired(self):
        with transport.memory_file(self.key) as fd:
            with self.assertRaisesRegex(transport.CryptoError, "certificate_chain"):
                transport.validate_pair(self.bundle, fd, self.ca, now=NOW + 40 * 86400)

    def test_certificate_not_yet_valid(self):
        with transport.memory_file(self.key) as fd:
            with self.assertRaisesRegex(transport.CryptoError, "certificate_chain"):
                transport.validate_pair(self.bundle, fd, self.ca, now=NOW - 86400)

    def test_source_key_ownership_policy(self):
        def metadata(mode=0o600, uid=0, links=1):
            return os.stat_result((stat.S_IFREG | mode, 0, 0, links, uid, 0, 1, 0, 0, 0))
        for mode in (0o400, 0o600):
            transport.check_key_metadata(metadata(mode))
        for item in (metadata(0o640), metadata(0o666), metadata(uid=os.geteuid()),
                     metadata(links=2), os.stat_result((stat.S_IFLNK | 0o600, 0, 0, 1, 0, 0,
                                                       1, 0, 0, 0))):
            with self.assertRaisesRegex(transport.ProtocolError, "OWNERSHIP_OR_MODE"):
                transport.check_key_metadata(item)

    def test_unsealed_memfd_and_regular_file_refused_before_read(self):
        fd = os.memfd_create("unsealed-public-negative", os.MFD_CLOEXEC)
        self.addCleanup(os.close, fd)
        os.write(fd, b"not private")
        with patch.object(transport.os, "pread", side_effect=AssertionError("NO_PRIVATE_READ")):
            with self.assertRaisesRegex(transport.ProtocolError, "SYNTHETIC_SEALED"):
                transport.seal(self.request, fd, self.bundle,
                               replay=self.replay,
                               request_pin=self.pin, recipient_pin=self.recipient_pin, now=NOW)
            with Path(__file__).open("rb") as source:
                with self.assertRaisesRegex(transport.ProtocolError, "SYNTHETIC_SEALED"):
                    transport.require_synthetic_fd(source.fileno())

    def test_root_execution_rejected_without_read_or_crypto(self):
        with patch.object(transport.os, "geteuid", return_value=0), \
                patch.object(transport, "openssl", side_effect=AssertionError("ROOT_CRYPTO_FORBIDDEN")):
            with self.assertRaisesRegex(transport.ProtocolError, "ROOT_MUTABLE_TREE"):
                transport.RecipientSession(transport.digest(self.bundle), now=NOW)

    def test_closed_session_and_concurrent_replay(self):
        self.session.close()
        with self.assertRaisesRegex(transport.ProtocolError, "ALREADY_CONSUMED"):
            self.session.offer()
        other = transport.RecipientSession(transport.digest(self.bundle), now=NOW)
        self.addCleanup(other.close)
        request, pin, recipient_pin = other.offer()
        frame = self.seal(request=request, pin=pin, recipient_pin=recipient_pin)

        def attempt():
            try:
                other.accept(frame, self.ca, now=NOW)
                return "PASS"
            except transport.ProtocolError:
                return "REJECTED"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: attempt(), range(2)))
        self.assertEqual(results.count("PASS"), 1)
        self.assertEqual(results.count("REJECTED"), 1)

    def test_private_material_not_in_openssl_argv_env_or_path_writes(self):
        real_run = subprocess.run
        observations = []

        def observed_run(argv, **kwargs):
            if self.key in repr(argv).encode() or self.key in repr(kwargs.get("env")).encode():
                raise AssertionError("PRIVATE_ARGV_ENV")
            for fd in kwargs.get("pass_fds", ()):
                self.assertTrue(os.readlink(transport.fd_path(fd)).startswith("/memfd:"))
            observations.append(argv[1])
            return real_run(argv, **kwargs)

        with patch.object(transport.subprocess, "run", side_effect=observed_run), \
                patch("builtins.open", side_effect=AssertionError("NO_PATH_IO")):
            frame = self.seal()
            self.session.accept(frame, self.ca, now=NOW)
        self.assertGreater(len(observations), 5)

    def test_source_plaintext_never_delivered_to_relay_process(self):
        parent, child = multiprocessing.get_context("fork").Pipe()
        process = multiprocessing.get_context("fork").Process(target=isolated_source, args=(child,))
        process.start()
        child.close()
        try:
            public = transport.parse(parent.recv_bytes(), {"certificate", "trust"})
            bundle = transport.decode(public["certificate"])
            trust = transport.decode(public["trust"])
            target = transport.RecipientSession(transport.digest(bundle))
            self.addCleanup(target.close)
            request, pin, recipient_pin = target.offer()
            # Relay has no source key fd and must not read any source plaintext.
            with patch.object(transport.os, "pread", side_effect=AssertionError("RELAY_PLAINTEXT_READ")):
                parent.send_bytes(transport.canonical({
                    "request": transport.encode(request), "pin": pin,
                    "recipient_pin": recipient_pin}))
                frame = parent.recv_bytes()
            self.assertFalse(private_pem_present(frame))
            self.assertEqual(set(transport.parse(
                frame, {"version", "request_pin", "ciphertext"})),
                {"version", "request_pin", "ciphertext"})
            report = target.accept(frame, trust)
            self.assertEqual(report["verdict"], "OFFLINE_PROTOCOL_PASS")
        finally:
            parent.close()
            process.join()
        self.assertEqual(process.exitcode, 0)

    def test_fixed_ssh_identity_and_strict_host_keys_plan_only(self):
        with patch.object(transport.subprocess, "run", side_effect=AssertionError("NO_SSH_EXECUTION")):
            argv = transport.ssh_plan(
                transport.TARGET, "/usr/local/libexec/opensim-cert-controller")
        self.assertTrue("infra@" + transport.TARGET in argv)
        self.assertTrue("StrictHostKeyChecking=yes" in argv)
        self.assertTrue("ForwardAgent=no" in argv)
        self.assertEqual(argv[-4:], ("sudo", "-n", "--", "/usr/local/libexec/opensim-cert-controller"))
        for edge, controller in ((transport.SOURCE, "/data/dev/mutable"),
                                 ("other", "/usr/local/libexec/opensim-cert-controller")):
            with self.assertRaisesRegex(transport.ProtocolError, "FIXED_CONTROLLER"):
                transport.ssh_plan(edge, controller)

    def test_missing_bootstrap_owner_window_all_production_actions_refused(self):
        for action in ("prepare", "transfer", "apply", "renew"):
            with patch.object(transport, "openssl", side_effect=AssertionError("NO_PRIVATE_ACCESS")), \
                    patch.object(transport.os, "open", side_effect=AssertionError("NO_PRODUCTION_IO")), \
                    patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(transport.main([action]), 1)

    def test_crypto_failure_does_not_expose_private_diagnostics(self):
        with patch.object(transport.subprocess, "run", return_value=subprocess.CompletedProcess(
                ["openssl"], 7, stdout=self.key, stderr=self.key)):
            with self.assertRaises(transport.CryptoError) as caught:
                transport.openssl("safe_operation", ["pkey"])
        self.assertEqual(caught.exception.returncode, 7)
        self.assertFalse(private_pem_present(str(caught.exception).encode()))
        self.assertFalse(hasattr(caught.exception, "private_stderr"))

    def test_detector_positive_and_negative(self):
        self.assertTrue(private_pem_present(self.key))
        self.assertFalse(private_pem_present(self.bundle))
        self.assertFalse(private_pem_present(self.request))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = args.out.resolve()
    if transport.EVIDENCE.resolve() not in root.parents:
        parser.error("Evidence must be a new directory below seat-cert-transport")
    root.mkdir(parents=True, exist_ok=False)
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CertTransportTests)
    actual_openssl = transport.openssl

    def recorded_openssl(operation, args, **kwargs):
        try:
            return actual_openssl(operation, args, **kwargs)
        except transport.CryptoError as exc:
            FAILURE_COORDINATES.append({"operation": exc.operation, "rc": exc.returncode})
            raise

    with patch.object(transport, "openssl", side_effect=recorded_openssl):
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    verdict = "PASS" if result.testsRun > 0 and result.wasSuccessful() else "FAIL"
    log = stream.getvalue().encode()
    if private_pem_present(log):
        raise RuntimeError("PRIVATE_PEM_IN_TEST_LOG_REFUSED")
    report = {
        "verdict": verdict, "tests": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors),
        "scope": "OFFLINE_REAL_OPENSSL_SYNTHETIC_ONLY",
        "production_ready": False, "independent_security_review": "PENDING",
        "production_debt": ["reviewed_root_owned_bootstrap", "trusted_pin_authorization",
                            "owner_window", "root_only_session_lifecycle",
                            "atomic_pair_recovery", "renewal_restart_authorization"],
        "expected_negative_openssl_failures": FAILURE_COORDINATES,
        "implementation_sha256": transport.digest(Path(transport.__file__).read_bytes()),
    }
    report_body = (json.dumps(report, indent=2) + "\n").encode()
    if private_pem_present(report_body):
        raise RuntimeError("PRIVATE_PEM_IN_REPORT_REFUSED")
    (root / "execution.log").write_bytes(log)
    (root / "report.json").write_bytes(report_body)
    count, hits = scan_evidence(root)
    line = (f"OPENSIM_CERT_TRANSPORT verdict={verdict} tests={result.testsRun} "
            f"failures={len(result.failures)} errors={len(result.errors)} "
            f"private_pem_hits={hits} evidence_files={count} production_ready=false")
    (root / "verdict.log").write_bytes((line + "\n").encode())
    _, final_hits = scan_evidence(root)
    if final_hits:
        raise RuntimeError("PRIVATE_PEM_IN_PERSISTED_EVIDENCE")
    print(line)
    print(f"report={root / 'report.json'}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
