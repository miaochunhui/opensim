"""Real CLI/FS negative controls for desired releases; never call production.

The peer API is read-only. Its existing immutable test bundles are inspected,
not installed or modified. All new test files and logs remain under seat-sync.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))
import release_gate as gate
import sync_release as sync
from test_release_gate import Fixture, put, run_suite


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.api = self.f.repo / "tools/release_app.py"
        self.desired_path = self.f.directory / "desired-release.json"
        receipt = {"schema": "opensim-release-gate-v1", "verdict": "PASS",
                   "release_verdict": "CERTIFIED", "scope": "EVIDENCE_VALIDATED_NOT_DEPLOYED",
                   "identity": self.f.identity, "ledger_sha256": gate.sha(self.f.ledger_path.read_bytes()),
                   "approval_pin": None, "exceptions": [], "gates": self.f.ledger["gates"],
                   "evidence": self.f.ledger["evidence"], "exit_code": 0,
                   "collected_at": datetime.now(timezone.utc).isoformat()}
        self.f.store("gate-receipt", receipt)
        epoch = int(gate.git(self.f.repo, "show", "-s", "--format=%ct", self.f.commit).strip())
        digest = "a" * 64
        self.desired = {"schema": "opensim-desired-release-v1", "request_id": "FIXTURE_NOT_RELEASE",
                        "action": "INSTALL_FIXED_RELEASE", "commit": self.f.commit,
                        "ledger": {"path": str(self.f.ledger_path),
                                   "sha256": gate.sha(self.f.ledger_path.read_bytes())},
                        "gate_receipt": self.f.pin("gate-receipt"), "approval_pin": None,
                        "bundle": str(self.f.directory / "nonexistent-bundle"), "manifest_sha256": digest,
                        "source_epoch": epoch, "release": f"/opt/opensim/releases/{self.f.commit}-{digest}",
                        "before_receipt": None}
        self.save()

    def save(self):
        put(self.desired_path, gate.encode(self.desired))

    def cli(self, apply=False, digest=None):
        out = self.f.directory / ("sync-cli-" + uuid4().hex + ".json")
        argv = [sys.executable, "-B", str(ROOT / "tools/sync_release.py"),
                "--source-root", str(self.f.repo), "--desired", str(self.desired_path),
                "--desired-sha256", digest or gate.sha(self.desired_path.read_bytes()),
                "--out", str(out)]
        if apply:
            argv.append("--apply")
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        put(out.with_suffix(".subprocess.log"),
            (f"rc={result.returncode}\n" + result.stdout + result.stderr).encode())
        report = gate.decode(out.read_bytes())
        self.assertFalse(report["production_changes"])
        self.assertEqual(report["state"], "REFUSED")
        self.assertEqual(result.returncode, 1, result.stderr)
        return report

    def test_unknown_desired_field(self):
        self.desired["postcommit"] = True
        self.save()
        self.assertIn("Unknown desired-release", self.cli()["failure"]["reason"])

    def test_unknown_desired_action(self):
        self.desired["action"] = "PUBLISH_ENTIRE_TREE"
        self.save()
        self.assertIn("Unknown desired-release action", self.cli()["failure"]["reason"])

    def test_request_bytes_changed(self):
        self.assertIn("request hash mismatch", self.cli(digest="0" * 64)["failure"]["reason"])

    def test_wrong_gate_receipt(self):
        self.f.store("gate-receipt", {"schema": 1, "verdict": "AUTOMATED_PASS", "exit_code": 0})
        self.desired["gate_receipt"] = self.f.pin("gate-receipt")
        self.save()
        self.assertIn("Wrong release-gate receipt", self.cli()["failure"]["reason"])

    def test_wrong_gate_ledger_hash(self):
        self.f.reports["gate-receipt"]["ledger_sha256"] = "0" * 64
        self.f.store("gate-receipt", self.f.reports["gate-receipt"])
        self.desired["gate_receipt"] = self.f.pin("gate-receipt")
        self.save()
        self.assertIn("does not bind", self.cli()["failure"]["reason"])

    def test_gate_rechecked_not_cached(self):
        put(self.f.repo / "other-owner.txt", b"OTHER OWNER MUST REMAIN\n")
        self.assertIn("Dirty checkout", self.cli()["failure"]["reason"])
        self.assertEqual((self.f.repo / "other-owner.txt").read_bytes(), b"OTHER OWNER MUST REMAIN\n")

    def test_evidence_drift_after_gate(self):
        put(self.f.directory / "browser/report.json", b"{}\n")
        self.assertIn("Evidence hash drift", self.cli()["failure"]["reason"])

    def test_missing_controller_refused(self):
        report = self.cli(apply=True)
        self.assertIn("controller", report["failure"]["reason"].lower())
        self.assertFalse((self.f.directory / "nonexistent-bundle").exists())

    def test_api_pin_and_signature_actual_peer(self):
        api = sync.load_api(self.api, self.f.identity["tool_sha256"]["tools/release_app.py"])
        self.assertEqual(api.Layout().release(self.f.commit, "a" * 64),
                         Path(self.desired["release"]))
        self.assertEqual(tuple(api.SOURCES), gate.SOURCES)
        self.assertEqual(set(api.PAYLOAD), sync.PAYLOAD)

    def test_api_old_hash(self):
        with self.assertRaisesRegex(gate.GateError, "API bytes differ"):
            sync.load_api(self.api, "0" * 64)

    def test_prepare_output_conflict_refused_before_writes(self):
        out = self.f.directory / "never-prepared"
        with self.assertRaisesRegex(gate.GateError, "trusted human-acceptance"):
            sync.prepare_guard(self.f.repo, self.f.commit, self.f.ledger_path, out)
        self.assertFalse(out.exists())

    def test_prepare_api_root_conflict_independent_negative(self):
        api = sync.load_api(self.api, self.f.identity["tool_sha256"]["tools/release_app.py"])
        out = self.f.directory / "output-conflict"
        with self.assertRaisesRegex(gate.GateError, "output root conflicts"):
            sync.prepare_output_guard(api, out)
        self.assertFalse(out.exists())

    def test_missing_bundle_preserves_request(self):
        before = self.desired_path.read_bytes()
        report = self.cli()
        self.assertIn("trusted human-acceptance", report["failure"]["reason"])
        self.assertEqual(self.desired_path.read_bytes(), before)

    def test_forged_certified_receipt_cannot_authorize_sync(self):
        self.assertIn("trusted human-acceptance", self.cli()["failure"]["reason"])

    def test_release_api_bundle_missing_negative_control(self):
        api = sync.load_api(self.api, self.f.identity["tool_sha256"]["tools/release_app.py"])
        with self.assertRaises(FileNotFoundError):
            api.validate_bundle((self.f.directory / "missing-bundle").resolve(),
                                "a" * 64, self.f.commit)

    def test_exact_manifest_fields_epoch_sources_layout(self):
        manifest = {"schema": 1, "commit": self.f.commit, "source_epoch": self.desired["source_epoch"],
                    "source_sha256": self.f.identity["source_sha256"],
                    "page_build": self.f.identity["page_build"], "files": dict.fromkeys(sync.PAYLOAD, {})}
        sync.match_manifest(manifest, self.desired, self.f.identity)
        manifest["source_epoch"] += 1
        with self.assertRaisesRegex(gate.GateError, "manifest/gate/epoch mismatch"):
            sync.match_manifest(manifest, self.desired, self.f.identity)

    def test_before_receipt_wrong_release_path(self):
        api = sync.load_api(self.api, self.f.identity["tool_sha256"]["tools/release_app.py"])
        self.f.store("before", {"schema": 1, "state": "FILES_INSTALLED", "commit": self.f.commit,
                               "manifest_sha256": "a" * 64, "release": "/opt/opensim/other-owner"})
        self.desired["before_receipt"] = self.f.pin("before")
        with self.assertRaisesRegex(gate.GateError, "escaped release layout"):
            sync.validate_before(api, self.desired)

    def test_real_peer_bundle_validate_and_bad_pin(self):
        peer_root = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-release")
        # Select only a bundle the peer explicitly produced as BUNDLE_PREPARED.
        candidates = sorted(peer_root.glob("fixtures-*/bundle-v1/manifest.json"))
        self.assertTrue(candidates, "No actual peer API bundle available; integration unverified")
        manifest_path = candidates[-1]
        raw = manifest_path.read_bytes()
        manifest = gate.decode(raw)
        api = sync.load_api(self.api, self.f.identity["tool_sha256"]["tools/release_app.py"])
        value = api.validate_bundle(manifest_path.parent.resolve(), gate.sha(raw), manifest["commit"])
        self.assertEqual(value, manifest)
        self.assertEqual(manifest_path.read_bytes(), raw)
        with self.assertRaisesRegex(api.ReleaseError, "manifest SHA drift"):
            api.validate_bundle(manifest_path.parent.resolve(), "0" * 64, manifest["commit"])

    def test_unit_templates_have_no_publication_privilege(self):
        service = (ROOT / "deploy/opensim-sync.service").read_text()
        path = (ROOT / "deploy/opensim-sync.path").read_text()
        self.assertIn("User=infra", service)
        self.assertIn("NoNewPrivileges=true", service)
        self.assertNotIn("--apply", service)
        self.assertNotIn("sudo", service)
        self.assertNotIn(".git", path)
        self.assertIn("PathChanged=/var/lib/opensim-sync/desired-release.json", path)

    def test_dns_applied_self_report_not_accepted(self):
        self.desired["dns_receipt"] = {"state": "APPLIED", "authorized": True}
        self.save()
        self.assertIn("Unknown desired-release", self.cli()["failure"]["reason"])


def scan(out: Path) -> int:
    """Point-name review using existing credential in memory, never output it."""
    import re
    names = ("tools/release_gate.py", "tools/sync_release.py", "tests/test_release_gate.py",
             "tests/test_sync_release.py", "deploy/opensim-sync.service",
             "deploy/opensim-sync.path", "docs/deploy/SYNC_RELEASE.md")
    token = Path("/opt/notify/relay.token").read_bytes().strip()
    if len(token) < 16:
        raise RuntimeError("Exact credential scan unavailable")
    shapes = re.compile(rb"(?i)\b(?:password|passwd|api[_-]?key|secret|authorization)"
                        rb"[\"']?\s*[:=]\s*[\"'][^\"']{6,}[\"']")
    pem = re.compile(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")
    rows = []
    for name in names:
        body = (ROOT / name).read_bytes()
        rows.append({"path": name, "bytes": len(body), "sha256": gate.sha(body),
                     "exact_hits": body.count(token), "shape_hits": len(shapes.findall(body)),
                     "pem_hits": len(pem.findall(body))})
    diff = subprocess.run(["git", "diff", "--check", "--", *names], cwd=ROOT,
                          capture_output=True, text=True)
    passed = diff.returncode == 0 and all(row["bytes"] > 0 and
                                        not any(row[k] for k in ("exact_hits", "shape_hits", "pem_hits"))
                                        for row in rows)
    report = {"verdict": "PASS" if passed else "FAIL", "files": rows, "diff_rc": diff.returncode,
              "scope": "EXPLICIT_SEVEN_PATHS", "collected_at": datetime.now(timezone.utc).isoformat()}
    gate.write_new(out, gate.encode(report))
    line = f"OPENSIM_SYNC_SCAN verdict={report['verdict']} files={len(rows)} report={out}"
    gate.write_new(out.with_suffix(".log"), (line + "\n" + diff.stdout + diff.stderr).encode())
    print(line)
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scan", action="store_true")
    args = parser.parse_args()
    raise SystemExit(scan(args.out) if args.scan else
                     run_suite(unittest.defaultTestLoader.loadTestsFromTestCase(SyncTests),
                               args.out, "OPENSIM_SYNC_RELEASE_TEST"))
