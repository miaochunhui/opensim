#!/usr/bin/env python3
"""Independent solver tests; missing solver is an error, never a skip/pass.

  sudo -n -u infra env OPENBLAS_NUM_THREADS=1 python3 -B tests/test_solver.py \
    --solver-root /data/dev/opensim-seat-solver

--selftest runs ONLY validator/oracle negative controls, not solver acceptance.
Normal unittest discovery also works, using OPENSIM_SOLVER_ROOT or this tree.
"""

import argparse
import copy
import importlib.util
import math
import os
from pathlib import Path
import sys
import unittest
import uuid

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location(
    "opensim_validate_numerics", ROOT / "tools" / "validate_numerics.py")
numerics = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = numerics
spec.loader.exec_module(numerics)
SOLVER_ROOT = Path(os.environ.get("OPENSIM_SOLVER_ROOT", str(ROOT)))


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
        out = numerics.ARTIFACT_ROOT / f"gate-selftest-{uuid.uuid4().hex}.json"
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
                  "cases": [], "convergence": {}, "rejections": [], "checks_executed": 0}
        out = numerics.ARTIFACT_ROOT / f"gate-existing-{uuid.uuid4().hex}.json"
        first = numerics.ReportWriter(out, copy.deepcopy(report))
        first.checkpoint()
        original = out.read_bytes()
        second = numerics.ReportWriter(out, copy.deepcopy(report))
        with self.assertRaises(FileExistsError):
            second.checkpoint()
        self.assertEqual(out.read_bytes(), original)

    def test_missing_solver_report_preserves_initial_checkpoint(self):
        out = numerics.ARTIFACT_ROOT / f"gate-missing-{uuid.uuid4().hex}.json"
        exit_code = numerics.main([
            "--solver-root", str(ROOT / "nonexistent-solver-root"), "--out", str(out)])
        report = numerics.json.loads(out.read_bytes())
        self.assertEqual(exit_code, 2)
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(report["state"], "FAILED")
        self.assertEqual(report["cases"], [])
        self.assertEqual(report["failure"]["type"], "FileNotFoundError")
        self.assertEqual(len(list(Path(report["parts_dir"]).glob("checkpoint-*.json"))), 2)


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
    args = parser.parse_args(argv)
    SOLVER_ROOT = args.solver_root
    loader = unittest.TestLoader()
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


if __name__ == "__main__":
    sys.exit(main())
