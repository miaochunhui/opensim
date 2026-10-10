#!/usr/bin/env python3
"""Independent solver tests; missing solver is an error, never a skip/pass.

  sudo -n -u infra env OPENBLAS_NUM_THREADS=1 python3 -B tests/test_solver.py \
    --solver-root /data/dev/opensim-seat-solver

--selftest runs ONLY validator/oracle negative controls, not solver acceptance.
Normal unittest discovery also works, using OPENSIM_SOLVER_ROOT or this tree.
Use --select ExactTestName --out NEW_EXTERNAL_DIR for a source-bound focused run.
"""

import argparse
import copy
import importlib.util
import math
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location(
    "opensim_validate_numerics", ROOT / "tools" / "validate_numerics.py")
numerics = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = numerics
spec.loader.exec_module(numerics)
SOLVER_ROOT = Path(os.environ.get("OPENSIM_SOLVER_ROOT", str(ROOT)))
TEST_ARTIFACT_ROOT = numerics.external_path(Path(os.environ.get(
    "OPENSIM_NUMERICS_TEST_ARTIFACT_ROOT", str(numerics.ARTIFACT_ROOT))))


class OracleAndGateTests(unittest.TestCase):
    def test_circle_poiseuille_center_wall_and_flow(self):
        config = numerics.base_config()
        velocity = numerics.reference_velocity(config, [[0, 0], [0.002, 0]])
        self.assertAlmostEqual(velocity[0], 0.01, places=14)
        self.assertEqual(velocity[1], 0.0)
        self.assertAlmostEqual(numerics.reference_flow(config),
                               math.pi * 0.002 ** 4 * 10000 / 8, places=20)

    def test_rectangle_series_integrals_agree(self):
        for width, height in ((0.004, 0.002), (0.004, 0.004), (0.001, 0.006)):
            with self.subTest(width=width, height=height):
                single = numerics.rectangular_flow(width, height, 10000)
                double = numerics.rectangular_flow_double(width, height, 10000)
                self.assertLess(abs(single / double - 1), 2e-7)

    def test_square_known_dimensionless_flow(self):
        # Independent dimensionless square conductance, Q/(g*a^4/mu).
        coefficient = numerics.rectangular_flow(1.0, 1.0, 1.0)
        self.assertAlmostEqual(coefficient, 0.0351442537388, places=12)

    def test_rectangle_series_walls_and_symmetry(self):
        points = np.array([[0, 0], [0.002, 0], [0, 0.001],
                           [0.0003, 0.0004], [-0.0003, -0.0004]])
        velocity = numerics.rectangular_velocity(points, 0.004, 0.002, 10000)
        self.assertGreater(velocity[0], velocity[3])
        self.assertLess(max(abs(velocity[1]), abs(velocity[2])), 1e-16)
        self.assertAlmostEqual(velocity[3], velocity[4], places=14)

    def test_rectangle_series_solves_forcing(self):
        point, h = np.array([[0.00031, 0.00027]]), 1e-5
        def value(offset):
            return float(numerics.rectangular_velocity(point + offset, 0.004, 0.002,
                                                       10000, terms=256)[0])
        laplacian = (value([h, 0]) + value([-h, 0]) + value([0, h])
                     + value([0, -h]) - 4 * value([0, 0])) / h ** 2
        self.assertLess(abs(laplacian / -10000 - 1), 0.015)

    def test_rectangle_rotation_and_scale(self):
        points = np.array([[0.0002, 0.0003], [-0.0005, 0.0004]])
        a = numerics.rectangular_velocity(points, 0.004, 0.002, 10000)
        b = numerics.rectangular_velocity(points[:, ::-1], 0.002, 0.004, 20000)
        np.testing.assert_allclose(b, 2 * a, rtol=1e-12)

    def test_empty_and_partial_convergence_fail(self):
        for records in ([], [{}], [{}, {}]):
            with self.assertRaises(numerics.AcceptanceError):
                numerics.convergence(records)

    def test_fabricated_nonconvergence_fails(self):
        records = [{"config": {"resolution": grid},
                    "independent": {"quadrature_relative_l2_error": 0.1,
                                    "relative_flow_error": 0.1, "node_count": grid ** 2}}
                   for grid in numerics.GRIDS]
        with self.assertRaises(numerics.AcceptanceError):
            numerics.convergence(records)

    def test_missing_and_empty_result_fail(self):
        for result in ({}, None, {"schema_version": 1}):
            with self.assertRaises(numerics.AcceptanceError):
                numerics.audit_result(result, numerics.base_config())

    def test_pass_report_cannot_be_empty(self):
        with self.assertRaises(numerics.AcceptanceError):
            numerics.report_summary({"verdict": "PASS", "cases": [], "convergence": {},
                                     "rejections": []})

    def failed_readback_report(self, exit_code):
        """Diagnostic-only report, never numerical acceptance evidence."""
        return {
            "verdict": "FAIL", "state": "FAILED", "collected_at": numerics.timestamp(),
            "cases": [], "convergence": {}, "rejections": [], "checks_executed": 0,
            "exit_code": exit_code,
            "failure": {"type": "DiagnosticFixture", "message": "FIRST_FAILURE"},
        }

    def test_fail_summary_requires_portable_nonzero_integer_exit(self):
        for code in (0, False, True, None, 1.0, "1", -1, 256, 512):
            with self.subTest(exit_code=code):
                with self.assertRaisesRegex(numerics.AcceptanceError, "integer exit_code in 1..255"):
                    numerics.report_summary(self.failed_readback_report(code))
        report = self.failed_readback_report(1)
        del report["exit_code"]
        with self.assertRaisesRegex(numerics.AcceptanceError, "integer exit_code in 1..255"):
            numerics.report_summary(report)

    def test_fail_readback_cli_cannot_exit_successfully(self):
        for code in (0, 256):
            with self.subTest(exit_code=code):
                directory = TEST_ARTIFACT_ROOT / f"fail-rc-{uuid.uuid4().hex}"
                directory.mkdir()
                report = self.failed_readback_report(code)
                path = directory / "report.json"
                source = numerics.json_bytes(report)
                with path.open("xb") as stream:
                    stream.write(source)
                with (directory / "stdout.log").open("xb") as out:
                    with (directory / "stderr.log").open("xb") as err:
                        result = subprocess.run(
                            [sys.executable, "-B", str(ROOT / "tools/validate_numerics.py"),
                             "--read-report", str(path)], stdout=out, stderr=err)
                with (directory / "launcher.json").open("xb") as stream:
                    stream.write(numerics.json_bytes({"rc": result.returncode,
                                                     "report_sha256": numerics.digest(source)}))
                self.assertEqual(path.read_bytes(), source)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("integer exit_code in 1..255",
                              (directory / "stderr.log").read_text())

    def test_fail_readback_preserves_nonzero_and_running_diagnostics(self):
        for state, code in (("FAILED", 1), ("FAILED", 2), ("FAILED", 255), ("RUNNING", 2)):
            with self.subTest(state=state, exit_code=code):
                report = self.failed_readback_report(code)
                report["state"] = state
                path = TEST_ARTIFACT_ROOT / f"fail-diagnostic-{uuid.uuid4().hex}.json"
                source = numerics.json_bytes(report)
                with path.open("xb") as stream:
                    stream.write(source)
                self.assertIn("NUMERICS verdict=FAIL", numerics.report_summary(report))
                self.assertEqual(numerics.read_report(path), code)
                self.assertEqual(path.read_bytes(), source)

    def test_raw_json_rejects_nan(self):
        with self.assertRaises(ValueError):
            numerics.json_bytes({"velocity": [float("nan")]})

    def test_output_cannot_enter_worktree(self):
        with self.assertRaises(ValueError):
            numerics.external_path(ROOT / "numerics.json")

    def test_missing_solver_is_not_fallback(self):
        with self.assertRaises(FileNotFoundError):
            numerics.load_solver(ROOT / "nonexistent-solver-root")

    def test_case_output_has_page_contract(self):
        report = {"verdict": "FAIL", "collected_at": numerics.timestamp(),
                  "cases": [], "convergence": {}, "rejections": [],
                  "checks_executed": 0, "exit_code": 2}
        out = TEST_ARTIFACT_ROOT / f"gate-selftest-{uuid.uuid4().hex}.json"
        writer = numerics.ReportWriter(out, report)
        writer.checkpoint(stage="initial")
        raw = {"metrics": {"flow_rate_m3_s": 1}}
        records = {"circle_8": {
            "name": "circle_8", "geometry": "circle", "resolution": 8,
            "metrics": raw["metrics"], "verdict": "RUNNING",
            "result": raw, "result_sha256": numerics.digest(numerics.json_bytes(raw)),
        }}
        writer.checkpoint(records, "raw")
        decoded = numerics.json.loads(out.read_bytes())
        case = decoded["cases"][0]
        self.assertEqual(case["geometry"], "circle")
        self.assertEqual(case["resolution"], 8)
        self.assertEqual(case["metrics"], raw["metrics"])
        self.assertNotIn("result", case)
        self.assertTrue(Path(case["result_path"]).is_file())
        self.assertEqual(len(list(writer.parts.glob("checkpoint-*.json"))), 2)
        self.assertEqual(numerics.read_report(out), 2)

    def test_report_writer_rejects_existing_file(self):
        report = {"verdict": "FAIL", "collected_at": numerics.timestamp(),
                  "cases": [], "convergence": {}, "rejections": [], "checks_executed": 0,
                  "exit_code": 2}
        out = TEST_ARTIFACT_ROOT / f"gate-existing-{uuid.uuid4().hex}.json"
        first = numerics.ReportWriter(out, copy.deepcopy(report))
        first.checkpoint()
        original = out.read_bytes()
        second = numerics.ReportWriter(out, copy.deepcopy(report))
        with self.assertRaises(FileExistsError):
            second.checkpoint()
        self.assertEqual(out.read_bytes(), original)

    def test_missing_solver_report_preserves_initial_checkpoint(self):
        out = TEST_ARTIFACT_ROOT / f"gate-missing-{uuid.uuid4().hex}.json"
        exit_code = numerics.main([
            "--solver-root", str(ROOT / "nonexistent-solver-root"), "--out", str(out),
            "--log-dir", str(TEST_ARTIFACT_ROOT / "logs")])
        report = numerics.json.loads(out.read_bytes())
        self.assertEqual(exit_code, 2)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(report["state"], "FAILED")
        self.assertEqual(report["cases"], [])
        self.assertEqual(report["failure"]["type"], "FileNotFoundError")
        self.assertEqual(len(list(Path(report["parts_dir"]).glob("checkpoint-*.json"))), 2)

    def test_exact_loaded_bytes_and_source_drift(self):
        directory = TEST_ARTIFACT_ROOT / f"source-binding-{uuid.uuid4().hex}"
        directory.mkdir()
        source = (ROOT / "solver.py").read_bytes()
        path = directory / "solver.py"
        with path.open("xb") as stream:
            stream.write(source)
        module, fingerprint = numerics.load_solver(directory)
        self.assertEqual(fingerprint["sha256"], numerics.digest(source))
        self.assertEqual(module.solve_case.__code__.co_filename, str(path.resolve()))
        with path.open("ab") as stream:
            stream.write(b"\n# Deliberate isolated source-drift negative control.\n")
        with self.assertRaisesRegex(numerics.AcceptanceError, "source changed"):
            numerics.assert_source_unchanged(fingerprint)
        self.assertEqual(fingerprint["sha256"], numerics.digest(source))


class SolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validation = numerics.ValidationRun(SOLVER_ROOT)

    @classmethod
    def tearDownClass(cls):
        numerics.assert_source_unchanged(cls.validation.fingerprint)

    def test_validate_config_does_not_mutate(self):
        config = numerics.base_config()
        original = copy.deepcopy(config)
        normalized = self.validation.solver.validate_config(config)
        self.assertEqual(config, original)
        self.assertEqual(normalized, original)

    def test_solve_does_not_mutate_config(self):
        config = numerics.base_config()
        original = copy.deepcopy(config)
        numerics.audit_result(self.validation.solver.solve_case(config), config)
        self.assertEqual(config, original)

    def test_rectangular_aspect_extremes_are_valid(self):
        for width, height in ((1.0, 10.0), (10.0, 1.0)):
            with self.subTest(width=width, height=height):
                config = {**numerics.base_config("rectangle", 8),
                          "width_mm": width, "height_mm": height}
                numerics.audit_result(self.validation.solver.solve_case(config), config)

    def test_wrong_velocity_detected_even_with_selfreported_green_metrics(self):
        result = copy.deepcopy(self.validation.case("rectangle")["result"])
        result["fields"]["velocity_m_s"] = [
            value * 1.05 for value in result["fields"]["velocity_m_s"]]
        result["metrics"]["relative_residual"] = 0.0
        result["metrics"]["relative_l2_error"] = 0.0
        with self.assertRaisesRegex(numerics.AcceptanceError, "weak-form residual"):
            numerics.audit_result(result, numerics.base_config("rectangle"))

    def test_nonzero_wall_detected(self):
        result = copy.deepcopy(self.validation.case("circle")["result"])
        index = result["mesh"]["boundary_nodes"][0]
        result["fields"]["velocity_m_s"][index] = 0.001
        with self.assertRaisesRegex(numerics.AcceptanceError, "nonzero wall"):
            numerics.audit_result(result, numerics.base_config())

    def test_fake_flow_metric_detected(self):
        result = copy.deepcopy(self.validation.case("rectangle")["result"])
        result["metrics"]["flow_rate_m3_s"] *= 1.05
        with self.assertRaisesRegex(numerics.AcceptanceError, "flow_rate"):
            numerics.audit_result(result, numerics.base_config("rectangle"))

    def test_empty_mesh_detected(self):
        result = copy.deepcopy(self.validation.case("circle")["result"])
        result["mesh"]["triangles"] = []
        with self.assertRaises(numerics.AcceptanceError):
            numerics.audit_result(result, numerics.base_config())

    def test_bad_boundary_and_missing_metric_detected(self):
        source = self.validation.case("circle")["result"]
        result = copy.deepcopy(source)
        result["mesh"]["boundary_nodes"] = result["mesh"]["boundary_nodes"][1:]
        with self.assertRaisesRegex(numerics.AcceptanceError, "boundary"):
            numerics.audit_result(result, numerics.base_config())
        result = copy.deepcopy(source)
        del result["metrics"]["relative_residual"]
        with self.assertRaisesRegex(numerics.AcceptanceError, "metrics"):
            numerics.audit_result(result, numerics.base_config())

    def test_reynolds_cannot_report_only_numerical_mean(self):
        result = copy.deepcopy(self.validation.case("circle")["result"])
        metrics = result["metrics"]
        metrics["reynolds"] = (
            result["config"]["density_kg_m3"] * metrics["mean_velocity_m_s"]
            * metrics["hydraulic_diameter_mm"] / 1000
            / result["config"]["viscosity_pa_s"])
        with self.assertRaisesRegex(numerics.AcceptanceError, "conservative reynolds"):
            numerics.audit_result(result, numerics.base_config())

    def test_tiny_nonzero_wall_is_rejected(self):
        result = copy.deepcopy(self.validation.case("circle")["result"])
        index = result["mesh"]["boundary_nodes"][0]
        result["fields"]["velocity_m_s"][index] = 1e-16
        with self.assertRaisesRegex(numerics.AcceptanceError, "nonzero wall"):
            numerics.audit_result(result, numerics.base_config())

    def readback_fixture(self):
        """Protocol-only fixture with a real raw solve, not convergence evidence."""
        row = copy.deepcopy(self.validation.case("circle", 8))
        out = TEST_ARTIFACT_ROOT / f"readback-{uuid.uuid4().hex}.json"
        report = {
            "verdict": "FAIL", "state": "RUNNING", "collected_at": numerics.timestamp(),
            "cases": [], "convergence": {}, "rejections": [],
            "checks_executed": 1, "exit_code": 2,
            "solver": dict(self.validation.fingerprint),
        }
        writer = numerics.ReportWriter(out, report)
        writer.checkpoint({row["name"]: row})
        report.update(verdict="PASS", state="COMPLETE", exit_code=0,
                      convergence={"circle": numerics.convergence([
                          self.validation.case("circle", grid) for grid in numerics.GRIDS])},
                      rejections=[{"verdict": "PASS"}])
        writer.checkpoint(stage="fixture_complete")
        return out, report

    def test_complete_readback_fixture_is_valid(self):
        out, _ = self.readback_fixture()
        self.assertEqual(numerics.read_report(out), 0)

    def test_pass_readback_rejects_running_and_failed_records(self):
        for variant in ("running", "exit_code", "case", "rejection", "convergence", "raw"):
            with self.subTest(variant=variant):
                out, report = self.readback_fixture()
                if variant == "running":
                    report["state"] = "RUNNING"
                elif variant == "exit_code":
                    report["exit_code"] = 2
                elif variant == "case":
                    report["cases"][0]["verdict"] = "RUNNING"
                elif variant == "rejection":
                    report["rejections"][0]["verdict"] = "FAIL"
                elif variant == "convergence":
                    report["convergence"]["circle"]["verdict"] = "FAIL"
                else:
                    del report["cases"][0]["result_path"]
                corrupted = out.with_name(f"corrupted-{out.name}")
                with corrupted.open("xb") as stream:
                    stream.write(numerics.json_bytes(report))
                with self.assertRaises(numerics.AcceptanceError):
                    numerics.read_report(corrupted)

    def test_pass_readback_rejects_stale_solver_binding(self):
        out, report = self.readback_fixture()
        report["solver"]["sha256"] = "0" * 64
        corrupted = out.with_name(f"corrupted-{out.name}")
        with corrupted.open("xb") as stream:
            stream.write(numerics.json_bytes(report))
        with self.assertRaisesRegex(numerics.AcceptanceError, "source changed"):
            numerics.read_report(corrupted)

    def test_analytic_nodal_fill_is_not_a_fem_solution(self):
        result = copy.deepcopy(self.validation.case("circle", 8)["result"])
        result["fields"]["velocity_m_s"] = result["reference"]["velocity_m_s"][:]
        result["metrics"]["relative_residual"] = 0.0
        result["metrics"]["relative_l2_error"] = 0.0
        with self.assertRaisesRegex(numerics.AcceptanceError, "weak-form residual"):
            numerics.audit_result(result, numerics.base_config("circle", 8))

    def test_length_and_section_units_scale_independently(self):
        for group in ("circle", "rectangle"):
            with self.subTest(group=group):
                base = self.validation.case(group, 8)["result"]
                config = base["config"]
                longer = self.validation.solver.solve_case({
                    **config, "length_mm": config["length_mm"] * 2})
                numerics.audit_result(longer, longer["config"])
                numerics.audit_scale(base, longer, 0.5)
                larger = self.validation.solver.solve_case({
                    **config, "width_mm": config["width_mm"] * 2,
                    "height_mm": config["height_mm"] * 2})
                measured = numerics.audit_result(larger, larger["config"])
                np.testing.assert_allclose(larger["mesh"]["nodes_mm"],
                                           2 * np.asarray(base["mesh"]["nodes_mm"]),
                                           rtol=1e-13, atol=0)
                np.testing.assert_allclose(larger["fields"]["velocity_m_s"],
                                           4 * np.asarray(base["fields"]["velocity_m_s"]),
                                           rtol=1e-11, atol=0)
                self.assertAlmostEqual(measured["flow_rate_m3_s"] /
                                       base["metrics"]["flow_rate_m3_s"], 16, places=10)
                self.assertAlmostEqual(larger["metrics"]["reynolds"] /
                                       base["metrics"]["reynolds"], 8, places=10)

    def test_density_changes_mass_flow_and_re_not_velocity(self):
        for group in ("circle", "rectangle"):
            with self.subTest(group=group):
                base = self.validation.case(group, 8)["result"]
                config = {**base["config"], "density_kg_m3":
                          base["config"]["density_kg_m3"] * 2}
                changed = self.validation.solver.solve_case(config)
                measured = numerics.audit_result(changed, config)
                numerics.audit_scale(base, changed, 1.0)
                self.assertAlmostEqual(changed["metrics"]["reynolds"] /
                                       base["metrics"]["reynolds"], 2, places=11)
                nodes = np.asarray(changed["mesh"]["nodes_mm"]) / 1000
                cells = np.asarray(changed["mesh"]["triangles"])
                points = nodes[cells]
                first, second = points[:, 1] - points[:, 0], points[:, 2] - points[:, 0]
                areas = (first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0]) / 2
                centroids = np.asarray(changed["fields"]["velocity_m_s"])[cells].mean(axis=1)
                # P1 is exactly integrated at cell centroids; density is not PDE forcing.
                mass_flow = math.fsum((areas * centroids * config["density_kg_m3"]).tolist())
                self.assertAlmostEqual(mass_flow / (
                    measured["flow_rate_m3_s"] * config["density_kg_m3"]), 1, places=13)
                self.assertAlmostEqual(mass_flow / (
                    base["metrics"]["flow_rate_m3_s"] * base["config"]["density_kg_m3"]),
                    2, places=13)

    def test_underestimated_reference_cannot_bypass_numerical_rejection(self):
        solver = self.validation.solver
        for group in ("circle", "rectangle"):
            config = numerics.near_re_config(group, 32, 2010)
            exact = numerics.reference_flow(config)
            with self.subTest(group=group):
                with patch.object(solver, "_reference_flow_rate", return_value=exact * 0.95):
                    solver.validate_config(config)
                    with self.assertRaisesRegex(solver.InputError, "Reynolds"):
                        solver.solve_case(config)

    def test_fem_field_does_not_use_reference_velocity_as_solution(self):
        solver = self.validation.solver
        for group in ("circle", "rectangle"):
            base = self.validation.case(group, 8)["result"]
            original = solver._reference_velocity
            with self.subTest(group=group):
                with patch.object(solver, "_reference_velocity",
                                  side_effect=lambda *args: original(*args) * 1.1):
                    changed = solver.solve_case(base["config"])
                np.testing.assert_array_equal(changed["fields"]["velocity_m_s"],
                                              base["fields"]["velocity_m_s"])
                self.assertEqual(changed["metrics"]["flow_rate_m3_s"],
                                 base["metrics"]["flow_rate_m3_s"])
                with self.assertRaisesRegex(numerics.AcceptanceError, "reference disagrees"):
                    numerics.audit_result(changed, base["config"])


def numerical_test(group, grid):
    def test(self):
        record = self.validation.case(group, grid)
        self.assertEqual(record["verdict"], "PASS")
    return test


def convergence_test(group):
    def test(self):
        result = numerics.convergence([self.validation.case(group, grid) for grid in numerics.GRIDS])
        self.assertEqual(result["verdict"], "PASS")
    return test


def scaling_test(group, scale, multiplier):
    def test(self):
        base = self.validation.case(group)["result"]
        changed = self.validation.case(group, scale=scale)["result"]
        numerics.audit_scale(base, changed, multiplier)
    return test


def rejection_test(name, config):
    def test(self):
        result = numerics.check_rejection(self.validation.solver, copy.deepcopy(config), name)
        self.assertEqual(result["verdict"], "PASS")
    return test


def near_re_test(group, grid):
    def test(self):
        config = numerics.near_re_config(group, grid, 1999.9)
        result = self.validation.solver.solve_case(config)
        numerics.audit_result(result, config)
        self.assertAlmostEqual(result["metrics"]["reynolds"], 1999.9, places=7)
    return test


for group in numerics.GEOMETRIES:
    for grid in numerics.GRIDS:
        setattr(SolverTests, f"test_numerical_{group}_{grid:02d}", numerical_test(group, grid))
        setattr(SolverTests, f"test_reynolds_near_limit_{group}_{grid:02d}",
                near_re_test(group, grid))
    setattr(SolverTests, f"test_convergence_{group}", convergence_test(group))
    for scale, multiplier in (("pressure_x2", 2.0), ("viscosity_x2", 0.5)):
        setattr(SolverTests, f"test_scaling_{group}_{scale}",
                scaling_test(group, scale, multiplier))
for name, config in numerics.invalid_configs().items():
    setattr(SolverTests, "test_reject_" + name, rejection_test(name, config))


def main(argv=None):
    global SOLVER_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solver-root", type=Path, default=SOLVER_ROOT)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--select", nargs="+", help="Exact unittest names/classes; partial scope")
    parser.add_argument("--out", type=Path, help="New external launcher directory and receipts")
    args = parser.parse_args(argv)
    SOLVER_ROOT = args.solver_root
    if args.out:
        return logged_tests(args)
    loader = unittest.TestLoader()
    if args.select:
        suite = loader.loadTestsFromNames(args.select, sys.modules[__name__])
    else:
        suite = loader.loadTestsFromTestCase(OracleAndGateTests)
        if not args.selftest:
            suite.addTests(loader.loadTestsFromTestCase(SolverTests))
    discovered = suite.countTestCases()
    numerics.require(discovered > 0, "empty unittest suite")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    passed = (result.wasSuccessful() and not result.skipped and result.testsRun == discovered)
    kind = "VALIDATOR_SELFTEST" if args.selftest else "SOLVER_UNITTEST"
    print(f"{kind} verdict={'PASS' if passed else 'FAIL'} collected_at={numerics.timestamp()} "
          f"discovered={discovered} tests_run={result.testsRun} failures={len(result.failures)} "
          f"errors={len(result.errors)} skipped={len(result.skipped)}")
    if not args.selftest and hasattr(SolverTests, "validation"):
        print(f"SOLVER_SOURCE sha256={SolverTests.validation.fingerprint['sha256']} "
              f"path={SolverTests.validation.fingerprint['path']}")
    return 0 if passed else 1


def logged_tests(args):
    """Reuse workbench execution and durable stores; run no other producers."""
    import re

    spec = importlib.util.spec_from_file_location(
        "opensim_workbench_launcher", ROOT / "tools" / "workbench_acceptance.py")
    workbench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(workbench)
    output = workbench.new_output(args.out)
    before = workbench.inventory(ROOT)
    workbench.write_json(output / "source-before.json", before)
    (output / "test-artifacts").mkdir()
    os.environ["OPENSIM_NUMERICS_TEST_ARTIFACT_ROOT"] = str(output / "test-artifacts")
    argv = [sys.executable, "-B", str(Path(__file__).resolve()),
            "--solver-root", str(args.solver_root)]
    if args.selftest:
        argv.append("--selftest")
    if args.select:
        argv.extend(["--select", *args.select])
    start = {"owner": "S5", "state": "RUNNING", "argv": argv, "started_at": workbench.now(),
             "scope": "ISOLATED_NUMERICS_NO_PRODUCTION_NO_NESTED_NO_FLASH"}
    workbench.write_json(output / "START.json", start)
    step = workbench.execute(argv, ROOT, output, "solver-tests")
    workbench.write_json(output / "execution.json", step)
    after = workbench.inventory(ROOT)
    workbench.write_json(output / "source-after.json", after)
    text = Path(step["stdout"]["path"]).read_text(encoding="utf-8")
    markers = re.findall(
        r"(?:SOLVER_UNITTEST|VALIDATOR_SELFTEST) verdict=(PASS|FAIL) .*?"
        r"discovered=(\d+) tests_run=(\d+) failures=(\d+) errors=(\d+) skipped=(\d+)", text)
    counts, verdict = None, None
    if len(markers) == 1:
        verdict, *numbers = markers[0]
        counts = dict(zip(("discovered", "tests_run", "failures", "errors", "skipped"),
                          map(int, numbers)))
    passed = (step["rc"] == 0 and before == after and counts is not None and
              verdict == "PASS" and counts["discovered"] == counts["tests_run"] > 0 and
              counts["failures"] == counts["errors"] == counts["skipped"] == 0)
    summary = {"schema": "opensim-s5-numerics-tests-v1",
               "verdict": "PASS" if passed else "FAIL", "state": "COMPLETE",
               "scope": ("SELECTED_NUMERICS_TESTS" if args.select else
                         "VALIDATOR_SELFTEST" if args.selftest else "SOLVER_TEST_SUITE"),
               "collected_at": workbench.now(), "launcher_rc": step["rc"],
               "source_unchanged": before == after, "counts": counts,
               "execution": step, "selected": args.select,
               "solver": text.split("SOLVER_SOURCE ")[-1].strip() if "SOLVER_SOURCE " in text else None}
    if not passed:
        summary["first_cause_log"] = step["stderr"]
    workbench.write_json(output / "summary.json", summary)
    line = (f"S5_NUMERICS_TESTS verdict={summary['verdict']} launcher_rc={step['rc']} "
            f"counts={counts} source_unchanged={before == after} collected_at={summary['collected_at']}")
    workbench.DurableStore().create(output / "progress.log", (line + "\n").encode("ascii"))
    print(line)
    print(f"SUMMARY path={output / 'summary.json'}")
    return 0 if passed else (step["rc"] or 1)


if __name__ == "__main__":
    sys.exit(main())
