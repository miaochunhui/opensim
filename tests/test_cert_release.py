"""OpenSSL-generated public fixtures; synthetic private keys exist only in RAM."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import cert_release as cert
from tools import edge_release as edge

RUN_ROOT = edge.EVIDENCE / ("cert-tests-" + uuid.uuid4().hex)


@contextmanager
def memory_file(body: bytes):
    fd = os.memfd_create("opensim-synthetic-test-only", 0)
    try:
        if os.write(fd, body) != len(body):
            raise RuntimeError("Short memfd write")
        os.lseek(fd, 0, os.SEEK_SET)
        yield fd
    finally:
        os.close(fd)


def run_openssl(args: list[str], descriptors=()) -> bytes:
    result = subprocess.run(["openssl", *args], capture_output=True, pass_fds=descriptors)
    if result.returncode:
        raise RuntimeError(f"Synthetic OpenSSL fixture failed command={args[0]} rc={result.returncode}")
    return result.stdout


class CertReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        RUN_ROOT.mkdir(parents=True, exist_ok=True)
        cls.ca_key = cert.openssl("genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256")
        cls.leaf_key = cert.openssl("genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256")
        with memory_file(cls.ca_key) as key:
            cls.ca = run_openssl([
                "req", "-new", "-x509", "-key", f"/proc/self/fd/{key}",
                "-days", "365", "-subj", "/CN=OpenSim Synthetic Test CA",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign"], (key,))
        cls.trust = RUN_ROOT / "synthetic-public-ca.crt"
        edge.write_new(cls.trust, cls.ca)
        cls.public = cert.openssl("pkey", "-pubout", "-outform", "DER", data=cls.leaf_key)
        cls.good = cls.signed("DNS:" + edge.DOMAIN)
        edge.write_new(RUN_ROOT / "synthetic-public-chain.crt", cls.good)

    @classmethod
    def tearDownClass(cls):
        cls.ca_key = cls.leaf_key = b""

    @classmethod
    def signed(cls, san: str, days: int = 30) -> bytes:
        with memory_file(cls.leaf_key) as key:
            csr = run_openssl([
                "req", "-new", "-key", f"/proc/self/fd/{key}", "-subj", "/CN=" + edge.DOMAIN,
                "-addext", "subjectAltName=" + san,
                "-addext", "extendedKeyUsage=serverAuth"], (key,))
        with memory_file(csr) as csrfd, memory_file(cls.ca) as ca, memory_file(cls.ca_key) as cakey:
            leaf = run_openssl([
                "x509", "-req", "-in", f"/proc/self/fd/{csrfd}",
                "-CA", f"/proc/self/fd/{ca}", "-CAkey", f"/proc/self/fd/{cakey}",
                "-set_serial", "1", "-days", str(days), "-copy_extensions", "copy"], (csrfd, ca, cakey))
        return leaf + cls.ca

    def validate(self, pem=None, public=None, minimum=7 * 86400, now=None):
        return cert.validate_certificate(
            self.good if pem is None else pem, self.public if public is None else public,
            minimum, trust_file=self.trust, now=now)

    def test_actual_chain_san_dates_key_match(self):
        report = self.validate()
        self.assertEqual(report["sans"], [edge.DOMAIN])
        self.assertEqual(report["chain"], "PASS")
        self.assertEqual(report["key_match"], "PASS")
        self.assertEqual(len(report["certificate_sha256"]), 64)
        self.assertNotIn("PRIVATE KEY", json.dumps(report))

    def test_wrong_domain_chain_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "chain verification"):
            self.validate(pem=self.signed("DNS:other.example.com"))

    def test_extra_domain_rejected(self):
        with self.assertRaisesRegex(ValueError, "only the exact"):
            self.validate(pem=self.signed("DNS:" + edge.DOMAIN + ",DNS:other.example.com"))

    def test_non_dns_san_rejected(self):
        with self.assertRaisesRegex(ValueError, "Non-DNS"):
            self.validate(pem=self.signed("DNS:" + edge.DOMAIN + ",IP:127.0.0.1"))

    def test_missing_chain_and_empty_pem_rejected(self):
        leaf = self.good[:self.good.index(b"-----END CERTIFICATE-----")
                         + len(b"-----END CERTIFICATE-----")] + b"\n"
        for pem in (b"", leaf, b"not a certificate"):
            with self.subTest(length=len(pem)):
                with self.assertRaisesRegex(ValueError, "leaf plus chain"):
                    self.validate(pem=pem)

    def test_pem_extra_content_rejected(self):
        with self.assertRaisesRegex(ValueError, "extra content"):
            self.validate(pem=self.good + b"untrusted trailer")

    def test_public_key_mismatch_and_empty_rejected(self):
        wrong = cert.openssl("pkey", "-pubout", "-outform", "DER", data=self.ca_key)
        for public in (wrong, b""):
            with self.subTest(length=len(public)):
                with self.assertRaisesRegex(ValueError, "mismatch or empty"):
                    self.validate(public=public)

    def test_expiry_policy_and_expired_date_rejected(self):
        with self.assertRaisesRegex(ValueError, "expiry"):
            self.validate(minimum=31 * 86400)
        with self.assertRaisesRegex(ValueError, "expired"):
            self.validate(now=datetime.now(timezone.utc) + timedelta(days=40))

    def test_not_yet_valid_rejected(self):
        with self.assertRaisesRegex(ValueError, "not yet valid"):
            self.validate(now=datetime.now(timezone.utc) - timedelta(days=2))

    def test_system_trust_rejects_synthetic_ca(self):
        with self.assertRaisesRegex(RuntimeError, "chain verification"):
            cert.validate_certificate(self.good, self.public)

    def test_metadata_negative_chain(self):
        now = datetime.now(timezone.utc)
        with self.assertRaisesRegex(ValueError, "chain verification"):
            cert.validate_metadata([edge.DOMAIN], now - timedelta(days=1),
                                   now + timedelta(days=30), self.public, self.public, False, now)

    def test_empty_and_other_domain_false_green_refused(self):
        for domain in ("", "ai4everyone.top", "other.ai4everyone.top", edge.DOMAIN + " "):
            with self.subTest(domain=domain):
                with self.assertRaises(ValueError):
                    cert.require_domain(domain)

    def test_mutating_callbacks_fail_loud_before_credentials(self):
        for action in ("apply", "renew", "revert"):
            out = RUN_ROOT / ("cli-" + action + "-" + uuid.uuid4().hex)
            with patch.object(cert, "openssl", side_effect=AssertionError("must not read key")), \
                    patch.object(cert, "inspect_local", side_effect=AssertionError("must not read account")):
                self.assertEqual(cert.main([action, "--domain", edge.DOMAIN, "--out", str(out)]), 1)
            report = json.loads(edge.read_regular(out / "report.json"))
            self.assertEqual(report["verdict"], "FAIL")
            self.assertIn(cert.ISSUE["id"], report["failure"]["reason"])

    def test_key_or_pem_never_written_to_evidence(self):
        # This checks real generated files, not a self-reported safety flag.
        for path in RUN_ROOT.rglob("*"):
            if path.is_file():
                self.assertNotIn(b"PRIVATE KEY", path.read_bytes(), str(path))

    def test_unprivileged_check_refuses_before_key_read(self):
        with patch.object(cert.os, "geteuid", return_value=1000), \
                patch.object(cert, "read_regular", side_effect=AssertionError("must not read")):
            with self.assertRaises(PermissionError):
                cert.check_certificate(cert.TLS / "opensim.crt", cert.TLS / "opensim.key")


def main() -> int:
    global RUN_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    RUN_ROOT = args.out.resolve()
    if edge.EVIDENCE.resolve() not in RUN_ROOT.parents:
        parser.error("Output must be under seat-edge")
    RUN_ROOT.mkdir(parents=True, exist_ok=False)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(CertReleaseTests))
    report = {"verdict": "PASS" if result.wasSuccessful() else "FAIL", "tests": result.testsRun,
              "scope": "ACTUAL_OPENSSL_SYNTHETIC_CERTIFICATES_NO_PRIVATE_KEY_FILES",
              "production_ready": False, "collected_at": datetime.now(timezone.utc).isoformat()}
    edge.write_new(RUN_ROOT / "report.json", (json.dumps(report, indent=2) + "\n").encode())
    edge.write_new(RUN_ROOT / "execution.log", stream.getvalue().encode())
    print(f"OPENSIM_CERT_TESTS verdict={report['verdict']} tests={result.testsRun} "
          f"report={RUN_ROOT / 'report.json'}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
