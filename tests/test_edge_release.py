"""Real filesystem acceptance plus explicit read-only actual-Caddy negative gate.

Run directly with --out under seat-edge. --real-caddy-negative additionally
uses the already verified infra SSH chain; it never uploads files or restarts.
All generated evidence stays outside the worktree. No credentials are read.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import edge_release as edge

RUN_ROOT = edge.EVIDENCE / ("tests-" + uuid.uuid4().hex)
BASE = (b"{\n    admin off\n    auto_https off\n}\n"
        b"opencad.ai4everyone.top:{$EDGE_PORT:18445} {\n    respond \"CAD\" 200\n}\n"
        b"openmath.ai4everyone.top:{$EDGE_PORT:18445} {\n    respond \"MATH\" 200\n}\n")


def ssh_argv(host: str, *argv: str) -> list[str]:
    edge.require_edge(host)
    return ["sudo", "-n", "-u", "infra", "ssh", "-T", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=8", f"infra@{host}",
            shlex.join(argv)]


class EdgeReleaseTests(unittest.TestCase):
    def setUp(self):
        self.root = RUN_ROOT / ("fixture-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        edge.write_new(self.root / "FIXTURE_ONLY", b"OPENSIM_FILE_FIXTURE\n")
        edge.write_new(self.root / "edge.lock", b"")
        edge.write_new(self.root / "Caddyfile", BASE)
        self.sentinels = {"unit": b"UNIT-UNTOUCHED\n", "env": b"ENV-UNTOUCHED\n",
                          "binary": b"BINARY-UNTOUCHED\n", "frpc": b"FRPC-UNTOUCHED\n"}
        for name, body in self.sentinels.items():
            edge.write_new(self.root / name, body)

    def apply(self, validate=None):
        return edge.apply_fixture(self.root, "release1", edge.digest(BASE), validate)

    def test_minimal_diff_and_exact_revert(self):
        metadata = (self.root / "Caddyfile").stat()
        receipt = self.apply()
        installed = edge.read_regular(self.root / "Caddyfile")
        self.assertEqual(installed, BASE + receipt["delta"].encode())
        self.assertEqual(edge.read_regular(self.root / "release1.before"), BASE)
        self.assertEqual(installed.count(edge.DOMAIN.encode()), 1)
        edge.revert_fixture(self.root, "release1")
        self.assertEqual(edge.read_regular(self.root / "Caddyfile"), BASE)
        after = (self.root / "Caddyfile").stat()
        self.assertEqual((after.st_uid, after.st_gid, after.st_mode),
                         (metadata.st_uid, metadata.st_gid, metadata.st_mode))
        for name, body in self.sentinels.items():
            self.assertEqual(edge.read_regular(self.root / name), body)

    def test_revert_preserves_unrelated_drift(self):
        self.apply()
        changed = BASE.replace(b'"CAD"', b'"OTHER OWNER"')
        live = edge.read_regular(self.root / "Caddyfile").replace(BASE, changed)
        suffix = b"\nother.example.com:18445 {\n    respond \"OTHER\" 200\n}\n"
        (self.root / "Caddyfile").write_bytes(live + suffix)
        edge.revert_fixture(self.root, "release1")
        self.assertEqual(edge.read_regular(self.root / "Caddyfile"), changed + suffix)

    def test_own_block_drift_refuses_without_write(self):
        self.apply()
        live = edge.read_regular(self.root / "Caddyfile").replace(b":8772", b":9999")
        (self.root / "Caddyfile").write_bytes(live)
        with self.assertRaisesRegex(ValueError, "Own block drift"):
            edge.revert_fixture(self.root, "release1")
        self.assertEqual(edge.read_regular(self.root / "Caddyfile"), live)

    def test_backup_hash_mismatch_refuses(self):
        self.apply()
        (self.root / "release1.before").write_bytes(BASE + b"# altered backup\n")
        live = edge.read_regular(self.root / "Caddyfile")
        with self.assertRaisesRegex(ValueError, "Backup hash"):
            edge.revert_fixture(self.root, "release1")
        self.assertEqual(edge.read_regular(self.root / "Caddyfile"), live)

    def test_receipt_tamper_refuses(self):
        receipt = self.apply()
        receipt["delta"] = ""
        (self.root / "release1.receipt.json").write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError, "exact original delta"):
            edge.revert_fixture(self.root, "release1")

    def test_empty_domain_false_green_refused(self):
        for body in (b"", b"# comments only\n", b"{\nadmin off\n}\n"):
            with self.subTest(body=body):
                with self.assertRaisesRegex(ValueError, "Empty"):
                    edge.plan_append(body, "release1")

    def test_preexisting_and_import_refused(self):
        self.apply()
        live = edge.read_regular(self.root / "Caddyfile")
        with self.assertRaisesRegex(ValueError, "Pre-existing"):
            edge.plan_append(live, "release2")
        with self.assertRaisesRegex(ValueError, "Imported"):
            edge.plan_append(BASE + b"import extra.caddy\n", "release2")

    def test_invalid_transaction_no_files(self):
        with self.assertRaisesRegex(ValueError, "Invalid transaction"):
            edge.plan_append(BASE, "../outside")

    def test_wrong_sha_and_missing_lock_refuse(self):
        with self.assertRaisesRegex(RuntimeError, "SHA differs"):
            edge.apply_fixture(self.root, "release1", "0" * 64)
        (self.root / "edge.lock").rename(self.root / "saved.lock")
        with self.assertRaises(FileNotFoundError):
            self.apply()
        self.assertEqual(edge.read_regular(self.root / "Caddyfile"), BASE)

    def test_validation_failure_no_install(self):
        def reject(body):
            self.assertEqual(body.count(edge.DOMAIN.encode()), 1)
            raise RuntimeError("fixture validation deliberately failed")
        with self.assertRaisesRegex(RuntimeError, "validation deliberately"):
            self.apply(reject)
        self.assertEqual(edge.read_regular(self.root / "Caddyfile"), BASE)
        self.assertFalse((self.root / "release1.before").exists())

    def test_drift_during_validation_refused(self):
        changed = BASE + b"# concurrent owner\n"
        self.assertNotEqual(changed, BASE)
        with self.assertRaisesRegex(RuntimeError, "drift immediately"):
            self.apply(lambda body: (self.root / "Caddyfile").write_bytes(changed))
        self.assertEqual(edge.read_regular(self.root / "Caddyfile"), changed)

    def test_no_trailing_newline_exact_revert(self):
        baseline = BASE.rstrip(b"\n")
        candidate, receipt = edge.plan_append(baseline, "release1")
        self.assertEqual(edge.plan_revert(candidate, receipt, baseline), baseline)

    def test_symlink_live_refused(self):
        (self.root / "Caddyfile").rename(self.root / "real-config")
        (self.root / "Caddyfile").symlink_to(self.root / "real-config")
        with self.assertRaises(OSError):
            self.apply()
        self.assertEqual(edge.read_regular(self.root / "real-config"), BASE)

    def test_actual_validator_command_contract(self):
        with patch.object(edge.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 9, b"", b"PRIVATE VALIDATOR DIAGNOSTIC")) as run:
            with self.assertRaisesRegex(RuntimeError, "rc=9") as caught:
                edge.validate_candidate(self.root / "candidate")
        self.assertEqual(run.call_args.args[0], [
            edge.CADDY, "validate", "--config", str(self.root / "candidate"),
            "--adapter", "caddyfile", "--envfile", edge.ENVFILE])
        self.assertEqual(caught.exception.private_stderr, b"PRIVATE VALIDATOR DIAGNOSTIC")
        self.assertNotIn("PRIVATE", str(caught.exception))

    def test_production_refusal_precedes_any_side_effect(self):
        for host in edge.EDGES:
            with self.assertRaisesRegex(PermissionError, edge.ISSUE["id"]):
                edge.refuse_production(host)
        with self.assertRaises(ValueError):
            edge.refuse_production("192.168.1.30")
        out = RUN_ROOT / ("cli-" + uuid.uuid4().hex)
        self.assertEqual(edge.main(["apply", "--edge", edge.EDGES[0], "--transaction", "r1",
                                    "--out", str(out)]), 1)
        report = json.loads(edge.read_regular(out / "report.json"))
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn(edge.ISSUE["id"], report["failure"]["reason"])

    def test_remote_arguments_preserve_literal_metacharacters(self):
        arguments = ("curl", "--noproxy", "*", "-w", "%{http_code}",
                     "https://example.com/", "one two", "a|b;systemctl restart forbidden")
        command = ssh_argv(edge.EDGES[0], *arguments)
        self.assertEqual(shlex.split(command[-1]), list(arguments))
        self.assertEqual(command[-2], "infra@" + edge.EDGES[0])
        self.assertIn("'*'", command[-1])


def real_caddy_negative() -> list[dict]:
    """Both real validators must reject the exact missing-OpenSim-cert candidate."""
    baseline = edge.read_regular(Path("/data/dev/infra/ops/gitlab-ha/edge/Caddyfile"))
    candidate, receipt = edge.plan_append(baseline, "seat3-readonly-negative")
    evidence = []
    for host in edge.EDGES:
        sha = subprocess.run(ssh_argv(host, "sudo", "-n", "sha256sum", str(edge.CONFIG)),
                             capture_output=True, check=True, text=True).stdout.split()[0]
        if sha != edge.digest(baseline):
            raise RuntimeError(f"Actual edge baseline drift host={host} sha256={sha}")
        argv = ssh_argv(host, "sudo", "-n", edge.CADDY, "validate", "--config", "/dev/stdin",
                        "--adapter", "caddyfile", "--envfile", edge.ENVFILE)
        valid = subprocess.run(argv, input=baseline, capture_output=True)
        if valid.returncode:
            raise RuntimeError(f"Actual Caddy baseline validation failed host={host} rc={valid.returncode}")
        negative = subprocess.run(argv, input=candidate, capture_output=True)
        missing = (b"/opt/docs-site/runtime/tls/opensim.crt" in negative.stderr
                   or b"/opt/docs-site/runtime/tls/opensim.key" in negative.stderr)
        if negative.returncode == 0 or not missing:
            raise RuntimeError(f"Actual Caddy negative did not prove missing OpenSim TLS host={host} "
                               f"rc={negative.returncode}")
        canaries = {}
        for domain in ("opencad.ai4everyone.top", "openmath.ai4everyone.top"):
            response = subprocess.run(
                ssh_argv(host, "curl", "-q", "--noproxy", "*", "--proto", "=https", "-sS",
                         "--max-time", "20", "--resolve", f"{domain}:18445:127.0.0.1",
                         "-o", "/dev/null", "-w", "%{http_code}", f"https://{domain}:18445/"),
                capture_output=True, text=True)
            if response.returncode or response.stdout != "200":
                raise RuntimeError(f"TLS canary failed host={host} domain={domain} "
                                   f"rc={response.returncode} stdout_bytes={len(response.stdout.encode())} "
                                   f"stdout_sha256={edge.digest(response.stdout.encode())}")
            canaries[domain] = {"tls": "VERIFIED", "http": 200}
        after = subprocess.run(ssh_argv(host, "sudo", "-n", "sha256sum", str(edge.CONFIG)),
                               capture_output=True, check=True, text=True).stdout.split()[0]
        if after != sha:
            raise RuntimeError(f"Config changed during read-only probe host={host}")
        evidence.append({"host": host, "before_sha256": sha, "after_sha256": after,
                         "candidate_sha256": receipt["installed_sha256"],
                         "baseline_caddy_rc": valid.returncode, "candidate_caddy_rc": negative.returncode,
                         "candidate_failure": "MISSING_OPENSIM_TLS_FILE",
                         "canaries": canaries, "scope": "READ_ONLY_NO_RESTART",
                         "all_existing_domains_tls": "NOT_TESTED"})
    return evidence


def remote_metadata() -> list[dict]:
    """Literal-path discovery: metadata and approved callback tokens, no secret bodies."""
    facts = []
    for host in edge.EDGES:
        paths = ("/root/.ssh/config", "/root/.ssh/id_ed25519", "/root/.ssh/id_rsa",
                 "/root/.ssh/id_ecdsa", "/opt/cert-broker", "/usr/local/sbin/cert-broker",
                 "/usr/local/bin/cert-broker", "/usr/local/sbin/opensim-cert-broker",
                 "/usr/local/libexec/opensim-cert-broker", "/var/lock/gitlab-edge.lock",
                 "/var/lock/dsh-acme.lock", "/root/deploy-opensim-cert.sh")
        metadata = subprocess.run(ssh_argv(host, "sudo", "-n", "stat", "-c", "%a:%U:%G:%n", *paths),
                                  capture_output=True, text=True)
        units = subprocess.run(ssh_argv(
            host, "sudo", "-n", "systemctl", "show", "gitlab-edge.service", "docs-site.service",
            "--property=Id", "--property=ActiveState", "--property=ExecMainStartTimestamp",
            "--property=ActiveEnterTimestamp", "--property=CanReload"),
            capture_output=True, text=True, check=True)
        callbacks = subprocess.run(ssh_argv(
            host, "sudo", "-n", "grep", "-Eon",
            r"sudo -u infra|/tmp/[A-Za-z0-9_$./-]+|192\.168\.1\.184|"
            r"systemctl restart [A-Za-z0-9 .-]+",
            "/root/deploy-opencad-cert.sh", "/root/deploy-openmath-cert.sh"),
            capture_output=True, text=True)
        facts.append({"host": host, "stat_rc": metadata.returncode,
                      "metadata": metadata.stdout.splitlines(), "missing_paths": metadata.stderr.splitlines(),
                      "units": units.stdout.splitlines(), "callback_tokens_rc": callbacks.returncode,
                      "callback_operation_tokens": callbacks.stdout.splitlines(),
                      "callback_missing": callbacks.stderr.splitlines(), "secret_bodies_read": False})
    return facts


def main() -> int:
    global RUN_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--real-caddy-negative", action="store_true")
    parser.add_argument("--remote-metadata", action="store_true")
    args = parser.parse_args()
    RUN_ROOT = args.out.resolve()
    if edge.EVIDENCE.resolve() not in RUN_ROOT.parents:
        parser.error("Output must be under seat-edge")
    RUN_ROOT.mkdir(parents=True, exist_ok=False)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(EdgeReleaseTests))
    report = {"verdict": "PASS" if result.wasSuccessful() else "FAIL", "tests": result.testsRun,
              "scope": "FILESYSTEM_FIXTURE", "collected_at": datetime.now(timezone.utc).isoformat()}
    if args.real_caddy_negative and result.wasSuccessful():
        try:
            report["real_caddy"] = real_caddy_negative()
        except Exception as exc:
            report.update(verdict="FAIL", failure={"type": type(exc).__name__, "reason": str(exc)})
    if args.remote_metadata and report["verdict"] == "PASS":
        try:
            report["remote_metadata"] = remote_metadata()
        except Exception as exc:
            report.update(verdict="FAIL", failure={"type": type(exc).__name__, "reason": str(exc)})
    edge.write_new(RUN_ROOT / "report.json", (json.dumps(report, indent=2) + "\n").encode())
    edge.write_new(RUN_ROOT / "execution.log", stream.getvalue().encode())
    print(f"OPENSIM_EDGE_TESTS verdict={report['verdict']} tests={result.testsRun} "
          f"report={RUN_ROOT / 'report.json'}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
