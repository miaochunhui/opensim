"""Real filesystem release transactions and true loopback compute, external only."""

import argparse
import copy
import errno
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import release_app as app


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.geteuid() == 0:
            raise RuntimeError("Tests must run as infra, never root")
        app.ARTIFACTS.mkdir(parents=True, exist_ok=True)
        cls.out = app.ARTIFACTS.resolve() / ("fixtures-" + uuid4().hex)
        cls.out.mkdir()
        cls.repo = cls.out / "committed-source"
        cls.repo.mkdir()
        cls.events = []
        for name in app.EXPORTS:
            path = cls.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / name).read_bytes())
        cls.command(["/usr/bin/git", "init", "--quiet"], cls.repo)
        cls.command(["/usr/bin/git", "add", "--", *app.EXPORTS], cls.repo)
        cls.command(["/usr/bin/git", "-c", "user.name=ReleaseFixture",
                     "-c", "user.email=fixture@invalid.example",
                     "-c", "commit.gpgsign=false", "commit", "--quiet",
                     "-m", "Filesystem fixture source"], cls.repo)
        cls.commit = cls.command(["/usr/bin/git", "rev-parse", "HEAD"], cls.repo).strip()
        cls.evidence = cls.out / "numerics.json"
        cls.command(["/usr/bin/python3", "-B", str(ROOT / "tools/validate_numerics.py"),
                     "--solver-root", str(cls.repo), "--out", str(cls.evidence),
                     "--log-dir", str(cls.out / "numeric-logs")], ROOT)
        cls.bundle = cls.out / "bundle-v1"
        cls.prepared = app.prepare(cls.repo, cls.commit, cls.evidence, cls.bundle)
        cls.digest = cls.prepared["manifest_sha256"]
        # A second actual committed release retains the same numerical solver.
        with (cls.repo / "web/app.css").open("ab") as stream:
            stream.write(b"\n/* Release fixture v2 identity */\n")
        cls.command(["/usr/bin/git", "add", "--", "web/app.css"], cls.repo)
        cls.command(["/usr/bin/git", "-c", "user.name=ReleaseFixture",
                     "-c", "user.email=fixture@invalid.example",
                     "-c", "commit.gpgsign=false", "commit", "--quiet",
                     "-m", "Filesystem fixture next release"], cls.repo)
        cls.commit2 = cls.command(["/usr/bin/git", "rev-parse", "HEAD"], cls.repo).strip()
        cls.bundle2 = cls.out / "bundle-v2"
        cls.prepared2 = app.prepare(cls.repo, cls.commit2, cls.evidence, cls.bundle2)
        cls.digest2 = cls.prepared2["manifest_sha256"]
        (cls.out / "source-events.json").write_bytes(app.encode({
            "scope": "REAL_FILESYSTEM_FIXTURE_NOT_PRODUCTION",
            "events": cls.events, "v1": cls.prepared, "v2": cls.prepared2}))

    @classmethod
    def command(cls, argv, cwd):
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                                     "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"})
        cls.events.append({"argv": argv, "rc": result.returncode,
                           "stdout": result.stdout, "stderr": result.stderr})
        if result.returncode:
            raise RuntimeError(f"Fixture command rc={result.returncode}: {argv}: {result.stderr}")
        return result.stdout

    def setUp(self):
        self.case = self.out / (self._testMethodName + "-" + uuid4().hex[:8])
        self.case.mkdir()
        self.layout = app.Layout(self.case / "host")

    def install(self):
        return app.install(self.layout, self.bundle, self.digest, self.commit)

    def mutable_bundle(self):
        destination = self.case / "mutable-bundle"
        shutil.copytree(self.bundle, destination)
        for path in [destination, *destination.rglob("*")]:
            os.chmod(path, 0o755 if path.is_dir() else 0o644)
        return destination

    def reseal(self, bundle):
        manifest = app.decode((bundle / "manifest.json").read_bytes())
        for name, row in manifest["files"].items():
            body = (bundle / name).read_bytes()
            row.update({"sha256": app.sha(body), "bytes": len(body)})
            os.utime(bundle / name, (manifest["source_epoch"], manifest["source_epoch"]))
        (bundle / "manifest.json").write_bytes(app.encode(manifest))
        for path in [bundle, *bundle.rglob("*")]:
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        return app.sha((bundle / "manifest.json").read_bytes())

    def test_export_committed_readonly_source_page_and_solver_evidence(self):
        manifest = app.validate_bundle(self.bundle, self.digest, self.commit)
        self.assertEqual(set(manifest["files"]), app.PAYLOAD)
        self.assertEqual((self.bundle / "source/solver.py").read_bytes(),
                         (ROOT / "solver.py").read_bytes())
        self.assertEqual(manifest["source_sha256"]["solver.py"],
                         app.decode(self.evidence.read_bytes())["solver"]["sha256"])
        self.assertIn(manifest["page_build"].encode(),
                      (self.bundle / "build/index-v1.html").read_bytes())
        self.assertNotEqual(self.digest, self.digest2)

    def test_dry_run_does_not_create_host_or_change_bundle(self):
        before = app.read_file(self.bundle / "manifest.json")
        report = app.plan(self.layout, self.bundle, self.digest, self.commit)
        self.assertEqual(report["state"], "DRY_RUN")
        self.assertFalse(self.layout.releases.parent.exists())
        self.assertEqual(app.read_file(self.bundle / "manifest.json"), before)

    def test_install_and_first_revert_exact_absence_unrelated_byte_identity(self):
        self.layout.create()
        sentinel = self.layout.unit.parent / "other-owner.service"
        sentinel.write_bytes(b"OTHER OWNER EXACT BYTES\n")
        before = sentinel.read_bytes()
        receipt = self.install()
        self.assertEqual(receipt["state"], "FILES_INSTALLED")
        release = Path(receipt["release"])
        self.assertEqual(self.layout.unit.read_bytes(), app.unit_bytes(self.layout, release))
        self.assertEqual(app.check(self.layout)["state"], "FILES_VALIDATED")
        for name in app.PAYLOAD | {"manifest.json"}:
            self.assertEqual((release / name).read_bytes(), (self.bundle / name).read_bytes())
        reverted = app.revert(self.layout)
        self.assertEqual(reverted["state"], "REVERTED")
        self.assertFalse(self.layout.unit.exists())
        self.assertEqual(sentinel.read_bytes(), before)
        self.assertTrue(release.is_dir())
        self.assertTrue(self.layout.state.is_file())
        (self.case / "byte-identity.json").write_bytes(app.encode({
            "unrelated_before_sha256": app.sha(before),
            "unrelated_after_sha256": app.sha(sentinel.read_bytes()),
            "unit_restored_to_exact_absence": not self.layout.unit.exists(),
            "fixed_release_retained": release.is_dir()}))

    def test_upgrade_revert_restores_prior_unit_and_source_byte_for_byte(self):
        first = self.install()
        before_unit = self.layout.unit.read_bytes()
        before_files = {name: (Path(first["release"]) / name).read_bytes()
                        for name in app.PAYLOAD | {"manifest.json"}}
        second = app.install(self.layout, self.bundle2, self.digest2, self.commit2)
        self.assertNotEqual(self.layout.unit.read_bytes(), before_unit)
        self.assertEqual(second["previous"], first)
        app.revert(self.layout)
        self.assertEqual(self.layout.unit.read_bytes(), before_unit)
        self.assertEqual(app.check(self.layout)["commit"], self.commit)
        for name, body in before_files.items():
            self.assertEqual((Path(first["release"]) / name).read_bytes(), body)
        (self.case / "upgrade-revert.json").write_bytes(app.encode({
            "before_unit_sha256": app.sha(before_unit),
            "after_unit_sha256": app.sha(self.layout.unit.read_bytes()),
            "restored_source_files": len(before_files), "retained_v2": second["release"]}))

    def test_unknown_unit_refused_without_receipt_or_overwrite(self):
        self.layout.create()
        self.layout.unit.write_bytes(b"UNKNOWN UNIT DO NOT OVERWRITE\n")
        before = self.layout.unit.read_bytes()
        with self.assertRaisesRegex(app.ReleaseError, "Unknown existing"):
            self.install()
        self.assertEqual(self.layout.unit.read_bytes(), before)
        self.assertFalse(self.layout.state.exists())

    def test_unit_drift_blocks_revert_and_upgrade_without_touching_owner(self):
        self.install()
        before_state = self.layout.state.read_bytes()
        self.layout.unit.write_bytes(b"CONCURRENT OWNER CHANGED LIVE\n")
        for operation in (
                lambda: app.revert(self.layout),
                lambda: app.install(self.layout, self.bundle2, self.digest2, self.commit2)):
            with self.assertRaisesRegex(app.ReleaseError, "unit byte drift"):
                operation()
        self.assertEqual(self.layout.unit.read_bytes(), b"CONCURRENT OWNER CHANGED LIVE\n")
        self.assertEqual(self.layout.state.read_bytes(), before_state)

    def test_release_content_drift_blocks_revert_even_if_unit_unchanged(self):
        receipt = self.install()
        path = Path(receipt["release"]) / "source/web/app.css"
        os.chmod(path, 0o644)
        path.write_bytes(b"CONCURRENT RELEASE DRIFT\n")
        before = self.layout.unit.read_bytes()
        with self.assertRaisesRegex(app.ReleaseError, "byte/hash drift"):
            app.revert(self.layout)
        self.assertEqual(self.layout.unit.read_bytes(), before)
        self.assertEqual(path.read_bytes(), b"CONCURRENT RELEASE DRIFT\n")

    def test_source_timestamp_drift_blocks_check(self):
        receipt = self.install()
        path = Path(receipt["release"]) / "source/server.py"
        os.utime(path, (1, 1))
        with self.assertRaisesRegex(app.ReleaseError, "timestamp drift"):
            app.check(self.layout)

    def test_unit_mode_drift_blocks_check(self):
        self.install()
        os.chmod(self.layout.unit, 0o666)
        with self.assertRaisesRegex(app.ReleaseError, "unit mode drift"):
            app.check(self.layout)

    def test_missing_non_directory_or_unwritable_logs_blocks_check(self):
        self.install()
        self.layout.logs.rmdir()
        with self.assertRaisesRegex(app.ReleaseError, "log directory"):
            app.check(self.layout)
        self.layout.logs.write_bytes(b"NOT A DIRECTORY\n")
        with self.assertRaisesRegex(app.ReleaseError, "log directory"):
            app.check(self.layout)
        self.layout.logs.unlink()
        self.layout.logs.mkdir(mode=0o500)
        with self.assertRaisesRegex(app.ReleaseError, "not writable"):
            app.check(self.layout)

    def test_same_release_not_reinstalled_or_receipt_overwritten(self):
        self.install()
        before = self.layout.state.read_bytes()
        with self.assertRaisesRegex(app.ReleaseError, "already installed"):
            self.install()
        self.assertEqual(self.layout.state.read_bytes(), before)

    def test_missing_wrong_commit_or_manifest_pin_refused(self):
        for digest, commit in (("", self.commit), ("0" * 64, self.commit),
                               (self.digest, "0" * 40)):
            with self.subTest(digest=digest, commit=commit), self.assertRaises(app.ReleaseError):
                app.plan(self.layout, self.bundle, digest, commit)
        self.assertFalse(self.layout.releases.exists())

    def test_unknown_payload_paths_and_symlink_are_refused(self):
        mutable = self.mutable_bundle()
        (mutable / "root-command.py").write_bytes(b"raise RuntimeError('must not run')\n")
        digest = self.reseal(mutable)
        with self.assertRaisesRegex(app.ReleaseError, "Unknown/missing"):
            app.validate_bundle(mutable, digest, self.commit)
        os.chmod(mutable, 0o755)
        (mutable / "root-command.py").unlink()
        path = mutable / "source/solver.py"
        os.chmod(path.parent, 0o755)
        path.unlink()
        path.symlink_to(ROOT / "solver.py")
        with self.assertRaisesRegex(app.ReleaseError, "Symlink"):
            app.validate_bundle(mutable, digest, self.commit)

    def test_evidence_wrong_solver_empty_or_noncomplete_rejected(self):
        good = app.decode(self.evidence.read_bytes())
        solver_sha = good["solver"]["sha256"]
        for changes in ({"solver": {"sha256": "0" * 64}}, {"cases": []},
                        {"checks_executed": 0}, {"state": "RUNNING"},
                        {"rejections": [{"verdict": "FAIL"}]}, {"convergence": {}}):
            with self.subTest(changes=changes), self.assertRaises(app.ReleaseError):
                app.validate_evidence(app.encode({**good, **changes}), solver_sha)

    def test_nonfinite_duplicate_and_overflow_json_rejected(self):
        for body in (b'{"x":NaN}', b'{"x":1,"x":2}', b'{"x":1e999}', b"{}"):
            with self.subTest(body=body), self.assertRaises((app.ReleaseError, ValueError)):
                app.decode(body)

    def test_dirty_untracked_source_and_wrong_head_refused_before_export(self):
        dirty = self.repo / "untracked-fixture"
        dirty.write_bytes(b"UNTRACKED SOURCE\n")
        try:
            with self.assertRaisesRegex(app.ReleaseError, "clean"):
                app.prepare(self.repo, self.commit2, self.evidence, self.case / "not-exported")
        finally:
            dirty.unlink()
        with self.assertRaisesRegex(app.ReleaseError, "HEAD"):
            app.prepare(self.repo, self.commit, self.evidence, self.case / "not-exported")
        self.assertFalse((self.case / "not-exported").exists())

    def test_root_must_not_execute_checkout_controller_or_prepare(self):
        with patch.object(app.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(app.ReleaseError, "checkout controller"):
                app.require_controller()
            with self.assertRaisesRegex(app.ReleaseError, "as root"):
                app.prepare(self.repo, self.commit2, self.evidence, self.case / "not-exported")
        self.assertFalse((self.case / "not-exported").exists())

    def test_fixture_cannot_restart_service_or_target_production(self):
        with patch.object(app, "service") as service:
            with self.assertRaisesRegex(app.ReleaseError, "Fixture"):
                app.install(self.layout, self.bundle, self.digest, self.commit,
                            authorize_restart=True)
            service.assert_not_called()
        with self.assertRaisesRegex(app.ReleaseError, "external root"):
            app.Layout(Path("/etc"))
        with self.assertRaisesRegex(app.ReleaseError, "Fixtures"):
            app.service("restart", self.layout)

    def test_apply_no_authorization_never_calls_service_or_package_code(self):
        with patch.object(app, "execute", side_effect=AssertionError("Must not execute code")), \
                patch.object(app, "service", side_effect=AssertionError("Must not restart")):
            receipt = self.install()
            app.revert(self.layout)
        self.assertEqual(receipt["state"], "FILES_INSTALLED")

    def test_interrupted_install_before_unit_write_has_guarded_revert(self):
        original = app.replace_guarded

        def fail_unit(path, before, after, mode):
            if path == self.layout.unit:
                raise OSError("Injected failure before unit switch")
            return original(path, before, after, mode)

        with patch.object(app, "replace_guarded", side_effect=fail_unit):
            with self.assertRaisesRegex(OSError, "Injected"):
                self.install()
        self.assertEqual(app.decode(self.layout.state.read_bytes())["state"], "INSTALLING")
        self.assertFalse(self.layout.unit.exists())
        self.assertEqual(app.revert(self.layout)["state"], "REVERTED")
        self.assertFalse(self.layout.unit.exists())

    def test_interrupted_install_after_unit_write_has_guarded_revert(self):
        with patch.object(app, "validate_live", side_effect=OSError("Injected after switch")):
            with self.assertRaisesRegex(OSError, "Injected"):
                self.install()
        self.assertTrue(self.layout.unit.exists())
        self.assertEqual(app.decode(self.layout.state.read_bytes())["state"], "INSTALLING")
        app.revert(self.layout)
        self.assertFalse(self.layout.unit.exists())

    def test_live_drift_between_plan_and_write_is_not_overwritten(self):
        self.layout.create()
        original = app.copy_release

        def drift(bundle, target, digest, commit):
            original(bundle, target, digest, commit)
            self.layout.unit.write_bytes(b"OWNER RACED INSTALL\n")

        with patch.object(app, "copy_release", side_effect=drift):
            with self.assertRaisesRegex(app.ReleaseError, "Before-byte drift"):
                self.install()
        self.assertEqual(self.layout.unit.read_bytes(), b"OWNER RACED INSTALL\n")
        with self.assertRaises(app.ReleaseError):
            app.revert(self.layout)
        self.assertEqual(self.layout.unit.read_bytes(), b"OWNER RACED INSTALL\n")

    def test_receipt_path_escape_and_symlink_parent_refused(self):
        receipt = self.install()
        receipt["release"] = "/etc"
        with self.assertRaisesRegex(app.ReleaseError, "escaped"):
            app.validate_live(self.layout, receipt)
        linked = self.case / "link"
        linked.symlink_to(self.case, target_is_directory=True)
        with self.assertRaisesRegex(app.ReleaseError, "Symlink"):
            app.no_symlinks(linked / "host")

    def test_unit_fixed_private_port_release_and_no_mutable_worktree(self):
        unit = app.unit_bytes(self.layout, self.layout.release(self.commit, self.digest))
        self.assertIn(b"--host 192.168.1.30 --port 8772", unit)
        self.assertIn(b"User=infra\nGroup=infra\n", unit)
        self.assertIn(b"/usr/bin/python3 -E -s -B", unit)
        self.assertNotIn(b"/data/dev/", unit)
        self.assertNotIn(b"0.0.0.0", unit)
        self.assertNotIn(b"ExecStartPre", unit)

    def test_effective_unit_rejects_unknown_fragment_dropins_and_stale_reload(self):
        layout = app.Layout()
        good = (f"LoadState=loaded\nFragmentPath={layout.unit}\nDropInPaths=\n"
                "NeedDaemonReload=no")
        with patch.object(app, "validate_unit_locations"), patch.object(app, "execute") as run:
            run.return_value = good
            app.validate_effective_unit(layout, require_current=True)
            for observed in (good.replace(str(layout.unit), "/etc/other.service"),
                             good.replace("DropInPaths=", "DropInPaths=/etc/override.conf"),
                             good.replace("NeedDaemonReload=no", "NeedDaemonReload=yes"),
                             "LoadState=loaded"):
                with self.subTest(observed=observed), self.assertRaises(app.ReleaseError):
                    run.return_value = observed
                    app.validate_effective_unit(layout, require_current=True)
            run.return_value = "LoadState=not-found\nFragmentPath=\nDropInPaths=\nNeedDaemonReload=no"
            app.validate_effective_unit(layout, absent=True)
            run.return_value = good
            with self.assertRaises(app.ReleaseError):
                app.validate_effective_unit(layout, absent=True)

    def test_service_command_allowlist_only_exact_app_never_enable_or_other_units(self):
        with patch.object(app, "execute") as run:
            production = app.Layout()
            app.service("reload", production)
            app.service("restart", production)
            app.service("stop", production)
            self.assertEqual([call.args[0] for call in run.call_args_list], [
                ["/usr/bin/systemctl", "daemon-reload"],
                ["/usr/bin/systemctl", "restart", "opensim-web.service"],
                ["/usr/bin/systemctl", "stop", "opensim-web.service"]])
            for action in ("enable", "disable", "kill", "restart gitlab-edge"):
                with self.assertRaises(app.ReleaseError):
                    app.service(action, production)

    def test_exact_dependency_pins_no_arbitrary_requirements(self):
        for body in (b"numpy==1.21.5\n", b"numpy>=1\nscipy==1.8.0\n",
                     b"numpy==1.21.5\nscipy==1.8.0\n-e /mutable\n",
                     b"numpy==1.21.5\nnumpy==1.21.5\nscipy==1.8.0\n"):
            with self.subTest(body=body), self.assertRaises(app.ReleaseError):
                app.dependency_pins(body)

    def test_cli_default_dry_run_and_failure_have_external_logs(self):
        out = self.case / "cli-dry"
        args = ["--bundle", str(self.bundle), "--commit", self.commit,
                "--manifest-sha256", self.digest, "--fixture-root", str(self.case / "host"),
                "--out", str(out)]
        self.assertEqual(app.main(args), 0)
        self.assertEqual(app.decode((out / "report.json").read_bytes())["state"], "DRY_RUN")
        self.assertIn("rc=0", (out / "execution.log").read_text())
        self.assertFalse(self.layout.releases.parent.exists())
        args[args.index("--manifest-sha256") + 1] = "0" * 64
        args[-1] = str(self.case / "cli-fail")
        self.assertEqual(app.main(args), 1)
        failed = app.decode((self.case / "cli-fail/report.json").read_bytes())
        self.assertEqual(failed["state"], "FAILED")
        self.assertIn("SHA drift", failed["failure"]["reason"])
        self.assertIn("rc=1", (self.case / "cli-fail/execution.log").read_text())

    def test_log_write_failure_never_leaves_success_report(self):
        for fail_action in (False, True):
            with self.subTest(fail_action=fail_action):
                out = self.case / ("log-fail-" + str(fail_action))
                args = ["--bundle", str(self.bundle), "--commit", self.commit,
                        "--manifest-sha256", "0" * 64 if fail_action else self.digest,
                        "--fixture-root", str(self.case / "host"), "--out", str(out)]
                original = app.write_new

                def failing_write(path, body, mode=0o444):
                    if path.name == "execution.log":
                        raise OSError(errno.ENOSPC, "injected log disk full")
                    return original(path, body, mode)

                with patch.object(app, "write_new", side_effect=failing_write):
                    self.assertEqual(app.main(args), 1)
                report = app.decode((out / "report.json").read_bytes())
                self.assertEqual((report["state"], report["exit_code"]), ("FAILED", 1))
                self.assertIn("injected log disk full", report["evidence_failure"]["reason"])
                self.assertEqual(report["action_result"]["exit_code"], int(fail_action))
                if fail_action:
                    self.assertIn("SHA drift", report["failure"]["reason"])

    def test_report_write_failure_does_not_print_final_success(self):
        out = self.case / "report-fail"
        args = ["--bundle", str(self.bundle), "--commit", self.commit,
                "--manifest-sha256", self.digest, "--fixture-root", str(self.case / "host"),
                "--out", str(out)]
        original = app.write_new

        def failing_write(path, body, mode=0o444):
            if path.name == "report.json":
                raise OSError(errno.ENOSPC, "injected report disk full")
            return original(path, body, mode)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(app, "write_new", side_effect=failing_write), \
                patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            self.assertEqual(app.main(args), 1)
        self.assertFalse((out / "report.json").exists())
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("injected report disk full", stderr.getvalue())
        self.assertIn("evidence_pending=1", (out / "execution.log").read_text())

    def test_existing_output_directory_is_not_written_into(self):
        out = self.case / "existing-output"
        out.mkdir()
        sentinel = out / "owner.txt"
        sentinel.write_bytes(b"OWNER BYTES\n")
        args = ["--bundle", str(self.bundle), "--commit", self.commit,
                "--manifest-sha256", self.digest, "--fixture-root", str(self.case / "host"),
                "--out", str(out)]
        self.assertEqual(app.main(args), 1)
        self.assertEqual(list(out.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b"OWNER BYTES\n")

    def test_late_report_persistence_failure_revokes_exact_success_bytes(self):
        out = self.case / "late-report-fail"
        args = ["--bundle", str(self.bundle), "--commit", self.commit,
                "--manifest-sha256", self.digest, "--fixture-root", str(self.case / "host"),
                "--out", str(out)]
        original = app.sync_directory
        injected = False

        def failing_sync(path):
            nonlocal injected
            original(path)
            if path == out and (out / "report.json").exists() and not injected:
                injected = True
                raise OSError(errno.EIO, "injected final report persistence failure")

        with patch.object(app, "sync_directory", side_effect=failing_sync):
            self.assertEqual(app.main(args), 1)
        self.assertTrue(injected)
        report = app.decode((out / "report.json").read_bytes())
        self.assertEqual((report["state"], report["exit_code"]), ("FAILED", 1))
        self.assertEqual(report["action_result"]["state"], "DRY_RUN")
        self.assertIn("injected final report", report["evidence_failure"]["reason"])

    def test_probe_rejects_health_only_canned_results_no_false_pass(self):
        self.install()
        manifest = app.validate_bundle(self.bundle, self.digest, self.commit)
        health = {"status": "ok", "schema_version": 1,
                  "source_sha256": manifest["source_sha256"], "page_build": manifest["page_build"]}

        def canned(base, path, payload=None):
            if path == "/api/health":
                return app.encode(health)
            if path == "/":
                return (self.bundle / "build/index-v1.html").read_bytes()
            if path.startswith("/web/"):
                return (self.bundle / "source" / path.lstrip("/")).read_bytes()
            return app.encode({"status": "ok"})

        with patch.object(app, "fetch", side_effect=canned):
            with self.assertRaisesRegex(app.ReleaseError, "FEM config/mesh"):
                app.check(self.layout, with_probe=True, base="http://127.0.0.1:8779")

    def test_true_fixed_release_page_assets_circle_rectangle_compute(self):
        import socket
        self.install()
        receipt = app.decode(self.layout.state.read_bytes())
        release = Path(receipt["release"])
        # Loopback fixtures never bind or touch the production private endpoint.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        log = self.case / "fixture-server.log"
        argv = ["/usr/bin/python3", "-E", "-s", "-B", str(release / "source/server.py"),
                "--host", "127.0.0.1", "--port", str(port),
                "--evidence", "../evidence/numerics.json",
                "--log-dir", str(self.layout.logs)]
        with log.open("xb") as stream:
            process = subprocess.Popen(argv, cwd=release / "source",
                                       stdout=stream, stderr=subprocess.STDOUT,
                                       env={**os.environ, "OPENBLAS_NUM_THREADS": "1",
                                            "OMP_NUM_THREADS": "1"})
            try:
                ready = False
                for _ in range(100):
                    if process.poll() is not None:
                        raise AssertionError(f"Fixture server exited rc={process.returncode}: {log}")
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            ready = True
                        break
                    except OSError:
                        time.sleep(0.05)
                self.assertTrue(ready, f"Fixture startup failed; see {log}")
                report = app.check(self.layout, with_probe=True, base=base)
                self.assertEqual(report["state"], "LOCAL_APP_PASS")
                self.assertEqual(report["scope"], "LOCAL_TEST")
                self.assertEqual([row["geometry"] for row in report["checks"]],
                                 ["circle", "rectangle"])
                self.assertTrue(all(row["node_count"] > 0 for row in report["checks"]))
                (self.case / "true-compute.json").write_bytes(app.encode(report))
            finally:
                # Only the subprocess owned by this test is stopped.
                process.terminate()
                process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    app.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    out = app.external(args.out)
    out.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReleaseTests)
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    explicit_paths = ("tools/release_app.py", "tests/test_release_app.py",
                      "docs/deploy/APP_RELEASE.md")
    patterns = {
        "credential_assignment": re.compile(
            rb"(?i)\b(?:password|passwd|api[_-]?key|secret|token|authorization)"
            rb"[\"']?\s*[:=]\s*[\"'][^\"']{6,}[\"']"),
        "private_key": re.compile(rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"),
        "cloud_access_id": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    }
    scan = []
    for relative in explicit_paths:
        body = (ROOT / relative).read_bytes()
        scan.append({"path": relative, "bytes": len(body), "sha256": app.sha(body),
                     "hits": {name: len(pattern.findall(body))
                              for name, pattern in patterns.items()}})
    scan_ok = all(row["bytes"] > 0 and not any(row["hits"].values()) for row in scan)
    diff = subprocess.run(["/usr/bin/git", "diff", "--check"], cwd=ROOT,
                          capture_output=True, text=True)
    review = {"scope": "THREE_EXPLICIT_FILES_SHAPE_SCAN_NO_CREDENTIAL_READ",
              "verdict": "PASS" if scan_ok and diff.returncode == 0 else "FAIL",
              "files": scan, "diff_check": {"rc": diff.returncode,
                                          "stdout": diff.stdout, "stderr": diff.stderr},
              "limitations": ["No existing credential was read; not an exact-secret-value scan"]}
    (out / "review.json").write_bytes(app.encode(review))
    passed = result.wasSuccessful() and review["verdict"] == "PASS"
    summary = {"collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "scope": "FILESYSTEM_AND_TRUE_LOOPBACK_COMPUTE_NOT_PRODUCTION",
               "verdict": "PASS" if passed else "FAIL",
               "tests": result.testsRun, "failures": len(result.failures),
               "errors": len(result.errors), "skipped": len(result.skipped),
               "duration_s": round(time.monotonic() - started, 3),
               "fixtures": str(getattr(ReleaseTests, "out", "SETUP_FAILED")),
               "source_sha256": app.sha((ROOT / "tools/release_app.py").read_bytes()),
               "test_sha256": app.sha(Path(__file__).read_bytes()),
               "review": str(out / "review.json"),
               "exit_code": 0 if passed else 1}
    line = (f"RELEASE_APP_TEST verdict={summary['verdict']} tests={summary['tests']} "
            f"failures={summary['failures']} errors={summary['errors']} "
            f"rc={summary['exit_code']} report={out / 'report.json'}")
    (out / "report.json").write_bytes(app.encode(summary))
    (out / "tests.log").write_text(stream.getvalue() + "\n" + line + "\n", encoding="utf-8")
    print(stream.getvalue())
    print(line)
    return summary["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
