"""Negative controls for local integration evidence; never run production."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.integration_acceptance import (
    EXTERNAL, ROOT, VIEWPORTS, identity, read_report, validate_browser, validate_local,
)
from tools.render_page import _source_metadata


class IntegrationEvidenceTests(unittest.TestCase):
    def local(self):
        sources = (
            "solver.py", "server.py", "web/app.js", "web/app.css",
            "tools/render_page.py", "tools/validate_numerics.py",
        )
        return {
            "verdict": "PASS",
            "steps": [{"rc": 0, "stderr": "\nRan 2 tests in 0.01s\n\nOK\n"}]
            + [{"rc": 0} for _ in range(4)],
            "source_sha256": {
                relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
                for relative in sources
            },
        }

    def browser(self, directory):
        # This parser fixture is not rendered-product or valid PNG acceptance.
        content = (
            b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR"
            + (320).to_bytes(4, "big") + (568).to_bytes(4, "big") + b"x" * 100
        )
        (directory / "parser-fixture.png").write_bytes(content)
        return {
            "mode": "OPENSIM_ACCEPTANCE", "verdict": "AUTOMATED_PASS",
            "commit": "candidate",
            "tool_sha256": hashlib.sha256((ROOT / "tools/browser_check.mjs").read_bytes()).hexdigest(),
            "checks": [{"status": "PASS"}],
            "matrix": [{"name": name, "status": "PASS"} for name in VIEWPORTS],
            "result_count": 1,
            "page_build": {"build_text": _source_metadata()[2]},
            "screenshots": [{"file": "parser-fixture.png", "width": 320, "height": 568,
                             "sha256": hashlib.sha256(content).hexdigest()}],
        }

    def test_local_accepts_complete_source_bound_report(self):
        validate_local(self.local(), ROOT)

    def test_local_rejects_failed_missing_empty_or_stale_evidence(self):
        variants = []
        value = self.local()
        value["steps"][1]["rc"] = 1
        variants.append(value)
        value = self.local()
        value["steps"].pop()
        variants.append(value)
        value = self.local()
        value["steps"][0]["stderr"] = "Ran 0 tests\nOK"
        variants.append(value)
        value = self.local()
        value["source_sha256"]["solver.py"] = "0" * 64
        variants.append(value)
        value = self.local()
        value["source_sha256"].pop("server.py")
        variants.append(value)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_local(value, ROOT)

    def test_browser_requires_full_matrix_commit_tool_and_terminal_checks(self):
        with tempfile.TemporaryDirectory(prefix="integration-browser-", dir=EXTERNAL) as temporary:
            directory = Path(temporary)
            good = self.browser(directory)
            validate_browser(good, ROOT, "candidate", directory)
            for key, value in [
                ("commit", "old"), ("tool_sha256", "0" * 64),
                ("mode", "OPENSIM_INPUT_PROBE"), ("checks", []),
                ("matrix", good["matrix"][:-1]), ("screenshots", []),
                ("checks", [{"status": "FAIL"}]), ("result_count", 0),
                ("page_build", {"build_text": "old"}),
            ]:
                bad = copy.deepcopy(good)
                bad[key] = value
                with self.subTest(key=key), self.assertRaises(ValueError):
                    validate_browser(bad, ROOT, "candidate", directory)
            (directory / "parser-fixture.png").write_bytes(b"not-a-screenshot")
            with self.assertRaises(ValueError):
                validate_browser(good, ROOT, "candidate", directory)

    def test_report_readback_has_exact_nonempty_artifact_hash(self):
        with tempfile.TemporaryDirectory(prefix="integration-test-", dir=EXTERNAL) as directory:
            output = Path(directory)
            file = output / "report.json"
            file.write_text(json.dumps({"verdict": "FAIL"}), encoding="utf-8")
            value, artifact = read_report(file, output)
            self.assertEqual(value["verdict"], "FAIL")
            self.assertEqual(artifact["sha256"], hashlib.sha256(file.read_bytes()).hexdigest())
            file.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_report(file, output)
            with self.assertRaises(ValueError):
                read_report(ROOT / "requirements.txt", output)

    def test_identity_is_real_nonempty_tracked_inventory(self):
        value = identity(ROOT)
        self.assertEqual(len(value["commit"]), 40)
        self.assertIn("solver.py", value["tracked_sha256"])
        self.assertEqual(
            value["tracked_sha256"]["solver.py"],
            hashlib.sha256((ROOT / "solver.py").read_bytes()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
