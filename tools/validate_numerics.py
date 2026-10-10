#!/usr/bin/env python3
"""Independent OpenSim P1 numerical acceptance, without solver reference helpers.

Run as infra:
  python3 -B tools/validate_numerics.py --solver-root /path/to/worktree \
    --out /hf3fs-jd/data/tmp/opensim-fluid-v1/numerics.json

The rectangular oracle is a double sine expansion of the constant forcing.
Its integral is cross-checked against a separately evaluated single series.
Reported residuals are reconstructed from the exported P1 mesh in SI units.
No solver oracle or reported error is used as an acceptance oracle. Existing
reports are never overwritten. Exit 1 means failed acceptance; exit 2 means
collection/setup failed. Logs and raw solver results stay outside worktrees.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import traceback
import uuid

import numpy as np


ARTIFACT_ROOT = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1")
GRIDS = (8, 16, 32)
GEOMETRIES = {
    "circle": ("circle", 4.0, 4.0),
    "rectangle": ("rectangle", 4.0, 2.0),
    "square": ("rectangle", 4.0, 4.0),
}
NUMERIC_FIELDS = (
    "width_mm", "height_mm", "length_mm", "pressure_pa",
    "viscosity_pa_s", "density_kg_m3", "resolution",
)
METRIC_KEYS = {
    "node_count", "element_count", "dof_count", "max_velocity_m_s",
    "mean_velocity_m_s", "flow_rate_m3_s", "reynolds",
    "hydraulic_diameter_mm", "relative_residual", "relative_l2_error",
    "reference_flow_rate_m3_s", "relative_flow_error",
    "force_balance_relative_error",
}
# Fixed before seeing solver results; P1 boundary geometry also converges.
LIMITS = {
    "relative_residual": 1e-9,
    "wall_velocity_m_s": 0.0,
    "fine_l2_error": 0.025,
    "fine_flow_error": 0.025,
    "minimum_order": 1.25,
    "metric_relative_difference": 2e-8,
    "scale_relative_difference": 2e-9,
}


class AcceptanceError(AssertionError):
    """An independently evaluated acceptance condition failed."""


def require(condition, message):
    if not condition:
        raise AcceptanceError(message)


def timestamp():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def json_bytes(value):
    return json.dumps(value, sort_keys=True, allow_nan=False,
                      separators=(",", ":"), ensure_ascii=True).encode("ascii")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def base_config(group="circle", resolution=16):
    geometry, width, height = GEOMETRIES[group]
    return {
        "geometry": geometry, "width_mm": width, "height_mm": height,
        "length_mm": 100.0, "pressure_pa": 1.0,
        "viscosity_pa_s": 0.001, "density_kg_m3": 998.0,
        "resolution": resolution,
    }


def rectangular_velocity(points_m, width_m, height_m, forcing_over_mu,
                         terms=64):
    """Solve -Delta u = forcing_over_mu on a centered rectangle by separation."""
    points = np.asarray(points_m, dtype=float)
    odd = np.arange(1, 2 * terms, 2, dtype=float)
    m, n = odd[:, None], odd[None, :]
    coefficients = 16.0 * forcing_over_mu / (
        math.pi ** 4 * m * n * ((m / width_m) ** 2 + (n / height_m) ** 2))
    values = np.empty(len(points))
    for start in range(0, len(points), 1024):
        chunk = points[start:start + 1024]
        sx = np.sin(math.pi * (chunk[:, 0, None] / width_m + 0.5) * odd)
        sy = np.sin(math.pi * (chunk[:, 1, None] / height_m + 0.5) * odd)
        values[start:start + len(chunk)] = np.sum((sx @ coefficients) * sy, axis=1)
    return values


def rectangular_flow(width_m, height_m, forcing_over_mu, terms=512):
    """Independent integrated single-series oracle, with the shorter side cubed."""
    width, height = max(width_m, height_m), min(width_m, height_m)
    correction = math.fsum(
        math.tanh(k * math.pi * width / (2.0 * height)) / k ** 5
        for k in range(1, 2 * terms, 2))
    return forcing_over_mu * width * height ** 3 / 12.0 * (
        1.0 - 192.0 * height * correction / (math.pi ** 5 * width))


def rectangular_flow_double(width_m, height_m, forcing_over_mu, terms=256):
    odd = np.arange(1, 2 * terms, 2, dtype=float)
    m, n = odd[:, None], odd[None, :]
    return float(64.0 * forcing_over_mu * width_m * height_m / math.pi ** 6
                 * np.sum(1.0 / (
                     m ** 2 * n ** 2
                     * ((m / width_m) ** 2 + (n / height_m) ** 2))))


def reference_velocity(config, points_m):
    width, height = config["width_mm"] / 1000.0, config["height_mm"] / 1000.0
    forcing = config["pressure_pa"] / (
        config["length_mm"] / 1000.0 * config["viscosity_pa_s"])
    points = np.asarray(points_m, dtype=float)
    if config["geometry"] == "circle":
        return forcing / 4.0 * ((width / 2.0) ** 2 - np.sum(points ** 2, axis=1))
    return rectangular_velocity(points, width, height, forcing)


def reference_flow(config):
    width, height = config["width_mm"] / 1000.0, config["height_mm"] / 1000.0
    forcing = config["pressure_pa"] / (
        config["length_mm"] / 1000.0 * config["viscosity_pa_s"])
    if config["geometry"] == "circle":
        return math.pi * (width / 2.0) ** 4 * forcing / 8.0
    return rectangular_flow(width, height, forcing)


def load_solver(root):
    """Compile the exact fingerprinted bytes; never copy/write the target tree."""
    root = Path(root).resolve(strict=True)
    path = root / "solver.py"
    source = path.read_bytes()
    require(bool(source.strip()), f"empty solver source: {path}")
    name = "_opensim_acceptance_solver_" + digest(source)[:16]
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.dont_write_bytecode = True
    sys.modules[name] = module
    exec(compile(source, str(path), "exec"), module.__dict__)
    for function in ("solve_case", "validate_config"):
        require(callable(getattr(module, function, None)), f"missing solver.{function}")
    for name, parent in (("InputError", ValueError), ("SolverError", RuntimeError)):
        error = getattr(module, name, None)
        require(isinstance(error, type) and issubclass(error, parent),
                f"missing/invalid solver.{name}")
    return module, {"path": str(path), "sha256": digest(source), "bytes": len(source)}


def assert_source_unchanged(fingerprint):
    require(digest(Path(fingerprint["path"]).read_bytes()) == fingerprint["sha256"],
            "solver source changed during collection; rerun only after it is stable")


def close_metric(actual, expected, name, relative=None):
    tolerance = LIMITS["metric_relative_difference"] if relative is None else relative
    require(math.isclose(actual, expected, rel_tol=tolerance, abs_tol=1e-25),
            f"{name}: reported={actual!r} independently_measured={expected!r}")


def audit_result(result, config):
    """Check geometry/topology, integrate P1 u, and reconstruct free-DOF Ku-f."""
    from scipy.sparse import coo_matrix

    require(isinstance(result, dict), "result must be a dict")
    keys = {"schema_version", "config", "model", "mesh", "fields",
            "metrics", "reference", "warnings"}
    require(set(result) == keys, f"result keys mismatch: {set(result) ^ keys}")
    require(type(result["schema_version"]) is int and result["schema_version"] == 1,
            "schema_version must be integer 1")
    require(result["config"] == config, "returned config differs from requested config")
    json_bytes(result)  # Recursive finite JSON gate, including metadata/reference.
    model = result["model"]
    require(isinstance(model, dict) and set(model) == {
        "name", "discretization", "assumptions"}, "model contract mismatch")
    require(all(isinstance(model[k], str) and model[k]
                for k in ("name", "discretization")), "empty model description")
    require(isinstance(model["assumptions"], list) and model["assumptions"]
            and all(isinstance(x, str) and x for x in model["assumptions"]),
            "missing model assumptions")
    require(isinstance(result["warnings"], list)
            and all(isinstance(x, str) for x in result["warnings"]), "invalid warnings")
    mesh = result["mesh"]
    require(isinstance(mesh, dict) and set(mesh) == {
        "nodes_mm", "triangles", "boundary_nodes"}, "mesh contract mismatch")
    require(isinstance(result["fields"], dict)
            and set(result["fields"]) == {"velocity_m_s"}, "fields contract mismatch")
    require(isinstance(result["reference"], dict)
            and set(result["reference"]) == {"velocity_m_s", "method"},
            "reference contract mismatch")
    require(isinstance(result["reference"]["method"], str)
            and result["reference"]["method"], "missing reference method")
    metrics = result["metrics"]
    require(isinstance(metrics, dict) and set(metrics) == METRIC_KEYS,
            "metrics contract mismatch")
    for name, value in metrics.items():
        require(type(value) in (int, float) and math.isfinite(value),
                f"nonfinite/nonnumeric metric: {name}")
    nodes = np.asarray(mesh["nodes_mm"], dtype=float) / 1000.0
    velocity = np.asarray(result["fields"]["velocity_m_s"], dtype=float)
    require(nodes.ndim == 2 and nodes.shape[1:] == (2,) and len(nodes) > 3,
            "empty/malformed nodes")
    require(velocity.shape == (len(nodes),) and np.isfinite(velocity).all(),
            "empty/malformed velocity")
    require(np.isfinite(nodes).all(), "nonfinite nodes")
    require(len(np.unique(nodes, axis=0)) == len(nodes), "duplicate mesh nodes")
    reported_reference = np.asarray(result["reference"]["velocity_m_s"], dtype=float)
    require(reported_reference.shape == velocity.shape
            and np.isfinite(reported_reference).all(), "malformed reference array")
    triangles_raw, boundary_raw = mesh["triangles"], mesh["boundary_nodes"]
    require(isinstance(triangles_raw, list) and triangles_raw,
            "empty/malformed triangles")
    require(all(isinstance(t, list) and len(t) == 3
                and all(type(i) is int for i in t) for t in triangles_raw),
            "triangle indices must be integer triples (not bool)")
    require(isinstance(boundary_raw, list) and boundary_raw
            and all(type(i) is int for i in boundary_raw), "empty/invalid boundary")
    triangles, boundary = np.asarray(triangles_raw), np.asarray(boundary_raw)
    require(triangles.min() >= 0 and triangles.max() < len(nodes)
            and boundary.min() >= 0 and boundary.max() < len(nodes),
            "mesh index out of range")
    require(len(set(boundary_raw)) == len(boundary_raw), "duplicate boundary indices")
    require(len({tuple(sorted(t)) for t in triangles_raw}) == len(triangles),
            "duplicate triangles")
    require(set(triangles.ravel()) == set(range(len(nodes))), "unused mesh nodes")
    edges = Counter(tuple(sorted((int(t[i]), int(t[(i + 1) % 3]))))
                    for t in triangles for i in range(3))
    require(all(count in (1, 2) for count in edges.values()), "nonmanifold mesh")
    topological_wall = {i for edge, count in edges.items() if count == 1 for i in edge}
    require(set(boundary_raw) == topological_wall, "boundary omits/adds topological nodes")
    require(len(nodes) - len(edges) + len(triangles) == 1, "mesh has holes/disconnection")
    width, height = config["width_mm"] / 1000.0, config["height_mm"] / 1000.0
    if config["geometry"] == "circle":
        radius = width / 2.0
        require(np.max(np.linalg.norm(nodes, axis=1)) <= radius * (1.0 + 1e-9),
                "circle mesh extends outside pipe")
        require(np.allclose(np.linalg.norm(nodes[boundary], axis=1), radius,
                            rtol=1e-8, atol=1e-14), "circle wall is off geometry")
        exact_area, hydraulic_diameter = math.pi * radius ** 2, width
    else:
        scaled = np.abs(nodes) / np.array([width / 2.0, height / 2.0])
        require(np.max(scaled) <= 1.0 + 1e-9, "rectangle mesh extends outside pipe")
        require(np.allclose(np.max(scaled[boundary], axis=1), 1.0, atol=1e-8),
                "rectangle wall is off geometry")
        exact_area = width * height
        hydraulic_diameter = 2.0 * width * height / (width + height)
    points = nodes[triangles]
    a, b = points[:, 1] - points[:, 0], points[:, 2] - points[:, 0]
    twice_signed_area = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
    area = np.abs(twice_signed_area) / 2.0
    require(np.all(area > 0.0), "degenerate triangles")
    mesh_area = float(np.sum(area))
    require(abs(mesh_area / exact_area - 1.0) <= (
        0.06 if config["geometry"] == "circle" else 1e-8), "mesh area mismatch")
    # Local gradients from coordinates, not solver assembly or its diagnostics.
    gradients = np.stack((
        np.stack((points[:, 1, 1] - points[:, 2, 1],
                  points[:, 2, 0] - points[:, 1, 0]), axis=1),
        np.stack((points[:, 2, 1] - points[:, 0, 1],
                  points[:, 0, 0] - points[:, 2, 0]), axis=1),
        np.stack((points[:, 0, 1] - points[:, 1, 1],
                  points[:, 1, 0] - points[:, 0, 0]), axis=1)), axis=1)
    gradients /= twice_signed_area[:, None, None]
    stiffness = config["viscosity_pa_s"] * area[:, None, None] * np.einsum(
        "tik,tjk->tij", gradients, gradients)
    rows = np.repeat(triangles, 3, axis=1).ravel()
    columns = np.tile(triangles, (1, 3)).ravel()
    matrix = coo_matrix((stiffness.ravel(), (rows, columns)),
                        shape=(len(nodes), len(nodes))).tocsr()
    forcing = config["pressure_pa"] / (config["length_mm"] / 1000.0)
    load = np.bincount(triangles.ravel(),
                       weights=np.repeat(forcing * area / 3.0, 3),
                       minlength=len(nodes))
    free = np.ones(len(nodes), dtype=bool)
    free[boundary] = False
    require(np.any(free), "empty free-DOF set")
    imbalance = matrix @ velocity - load
    residual = float(np.linalg.norm(imbalance[free]) / np.linalg.norm(load[free]))
    wall_speed = float(np.max(np.abs(velocity[boundary])))
    require(wall_speed <= LIMITS["wall_velocity_m_s"], f"nonzero wall speed: {wall_speed}")
    require(residual <= LIMITS["relative_residual"], f"weak-form residual: {residual}")
    require(float(np.min(velocity)) >= -1e-14 and np.max(velocity) > 0.0,
            "nonphysical velocity sign or zero solution")
    flow = float(np.sum(area * np.mean(velocity[triangles], axis=1)))
    mean = flow / exact_area
    exact_flow = reference_flow(config)
    flow_error = abs(flow / exact_flow - 1.0)
    reaction = -float(np.sum(imbalance[boundary]))
    force_error = abs(reaction / (forcing * mesh_area) - 1.0)
    reynolds = config["density_kg_m3"] * mean * hydraulic_diameter / config["viscosity_pa_s"]
    # Three degree-two barycentric points measure interpolated P1 u, not just nodes.
    barycentric = np.array([[2/3, 1/6, 1/6], [1/6, 2/3, 1/6], [1/6, 1/6, 2/3]])
    quadrature_points = np.einsum("qi,tij->tqj", barycentric, points)
    exact = reference_velocity(config, quadrature_points.reshape(-1, 2)).reshape(-1, 3)
    numerical = velocity[triangles] @ barycentric.T
    l2_error = math.sqrt(float(np.sum(area[:, None] * (numerical - exact) ** 2)
                               / np.sum(area[:, None] * exact ** 2)))
    nodal_exact = reference_velocity(config, nodes)
    require(np.max(np.abs(reported_reference - nodal_exact))
            <= 2e-4 * np.max(nodal_exact), "solver reference disagrees with independent series")
    independent = {
        "node_count": len(nodes), "element_count": len(triangles),
        "dof_count": int(np.sum(free)), "mesh_area_m2": mesh_area,
        "exact_area_m2": exact_area, "max_velocity_m_s": float(np.max(velocity)),
        "mean_velocity_m_s": mean, "flow_rate_m3_s": flow, "reynolds": reynolds,
        "hydraulic_diameter_mm": hydraulic_diameter * 1000.0,
        "relative_residual": residual, "quadrature_relative_l2_error": l2_error,
        "reference_flow_rate_m3_s": exact_flow, "relative_flow_error": flow_error,
        "wall_velocity_m_s": wall_speed, "force_balance_relative_error": force_error,
    }
    for name in ("node_count", "element_count", "dof_count"):
        require(type(metrics[name]) is int and metrics[name] == independent[name],
                f"incorrect {name}")
    for name in ("flow_rate_m3_s", "max_velocity_m_s", "hydraulic_diameter_mm",
                 "reference_flow_rate_m3_s", "relative_flow_error"):
        close_metric(metrics[name], independent[name], name)
    # Polygon area or true area conventions differ for a circular boundary.
    require(any(math.isclose(metrics["mean_velocity_m_s"], candidate, rel_tol=2e-8)
                for candidate in (mean, flow / mesh_area)), "incorrect mean velocity")
    numerical_reynolds = config["density_kg_m3"] * metrics["mean_velocity_m_s"] * (
        hydraulic_diameter / config["viscosity_pa_s"])
    reference_reynolds = config["density_kg_m3"] * exact_flow / exact_area * (
        hydraulic_diameter / config["viscosity_pa_s"])
    # Chair-confirmed conservative convention: max(reference mean, FEM mean).
    close_metric(metrics["reynolds"], max(numerical_reynolds, reference_reynolds),
                 "conservative reynolds")
    independent["numerical_reynolds"] = numerical_reynolds
    independent["reference_reynolds"] = reference_reynolds
    for name in ("relative_residual", "force_balance_relative_error"):
        require(0.0 <= metrics[name] <= LIMITS["relative_residual"], f"reported {name} too large")
    require(force_error <= LIMITS["relative_residual"], f"force balance: {force_error}")
    require(0.0 <= metrics["relative_l2_error"] <= 0.25, "invalid reported L2 error")
    require(reynolds <= 2000.0, "result exceeds laminar Re limit")
    if config["resolution"] == 32:
        require(l2_error <= LIMITS["fine_l2_error"], f"fine-grid L2 error: {l2_error}")
        require(flow_error <= LIMITS["fine_flow_error"], f"fine-grid flow error: {flow_error}")
    return independent


def convergence(records):
    require(len(records) == 3, "convergence requires exactly 8/16/32 records, not empty/partial")
    require([r["config"]["resolution"] for r in records] == list(GRIDS),
            "convergence grid sequence must be 8/16/32")
    values = {}
    for name in ("quadrature_relative_l2_error", "relative_flow_error"):
        errors = [r["independent"][name] for r in records]
        require(all(math.isfinite(x) and x > 0.0 for x in errors), f"invalid {name} errors")
        orders = [math.log(errors[i] / errors[i + 1], 2.0) for i in (0, 1)]
        require(all(p >= LIMITS["minimum_order"] for p in orders),
                f"{name} fails convergence: errors={errors}, orders={orders}")
        values[name] = {"errors": errors, "observed_orders": orders}
    counts = [r["independent"]["node_count"] for r in records]
    require(counts[0] < counts[1] < counts[2], "resolution does not refine mesh")
    return {"resolutions": list(GRIDS), "node_counts": counts, **values, "verdict": "PASS"}


def audit_scale(base, changed, multiplier):
    require(base["mesh"] == changed["mesh"], "pressure/viscosity scaling changed mesh")
    a, b = np.asarray(base["fields"]["velocity_m_s"]), np.asarray(changed["fields"]["velocity_m_s"])
    require(np.allclose(b, a * multiplier, rtol=LIMITS["scale_relative_difference"],
                        atol=1e-14), "velocity violates pressure/viscosity linear scaling")
    for name in ("flow_rate_m3_s", "mean_velocity_m_s", "max_velocity_m_s"):
        close_metric(changed["metrics"][name], base["metrics"][name] * multiplier,
                     f"scaled {name}", LIMITS["scale_relative_difference"])


def invalid_configs():
    """Concrete rejection cases; both public entry points must raise InputError."""
    result = {}
    base = base_config()
    for field in base:
        changed = dict(base)
        del changed[field]
        result[f"missing_{field}"] = changed
    result.update({"empty": {}, "not_dict_none": None, "not_dict_list": [],
                   "not_dict_string": "{}",
                   "unknown_key": {**base, "unexpected": 1},
                   "unknown_geometry": {**base, "geometry": "triangle"},
                   "bool_geometry": {**base, "geometry": True},
                   "list_geometry": {**base, "geometry": ["circle"]},
                   "none_geometry": {**base, "geometry": None},
                   "nan_geometry": {**base, "geometry": float("nan")},
                   "circle_height_mismatch": {**base, "height_mm": 2.0},
                   "aspect_ratio_high": {**base, "geometry": "rectangle",
                                         "width_mm": 100.0, "height_mm": 0.1},
                   "aspect_ratio_low": {**base, "geometry": "rectangle",
                                        "width_mm": 0.1, "height_mm": 100.0},
                   "super_re": {**base, "pressure_pa": 1000.0}})
    bad_values = {
        "nan": float("nan"), "posinf": float("inf"), "neginf": -float("inf"),
        "bool_true": True, "bool_false": False, "zero": 0, "negative": -1,
        "none": None, "list": [1], "dict": {}, "nan_string": "NaN",
        "malformed": "1.0.0", "overflow_string": "1e9999",
        "numeric_string": "1.0", "huge_integer": 10 ** 400,
    }
    for field in NUMERIC_FIELDS:
        for label, value in bad_values.items():
            result[f"{field}_{label}"] = {**base, field: value}
    ranges = {
        "width_mm": (0.099, 100.001), "height_mm": (0.099, 100.001),
        "length_mm": (0.999, 10000.001), "pressure_pa": (0.000099, 100000.001),
        "viscosity_pa_s": (0.0000099, 10.001), "density_kg_m3": (0.099, 20000.001),
        "resolution": (7, 49),
    }
    for field, (low, high) in ranges.items():
        for label, value in (("below_min", low), ("above_max", high)):
            result[f"{field}_{label}"] = {**base, field: value}
    result["resolution_fraction"] = {**base, "resolution": 16.5}
    result["resolution_integral_float"] = {**base, "resolution": 16.0}
    for group in GEOMETRIES:
        for grid in GRIDS:
            result[f"super_re_{group}_{grid}"] = near_re_config(group, grid, 2000.1)
    require(bool(result), "empty rejection case set")
    return result


def near_re_config(group, grid, reynolds):
    config = base_config(group, grid)
    width, height = config["width_mm"] / 1000.0, config["height_mm"] / 1000.0
    area = math.pi * width ** 2 / 4 if config["geometry"] == "circle" else width * height
    diameter = width if config["geometry"] == "circle" else 2 * width * height / (width + height)
    reference_re = config["density_kg_m3"] * reference_flow(config) / area * (
        diameter / config["viscosity_pa_s"])
    config["pressure_pa"] *= reynolds / reference_re
    return config


def check_rejection(solver, config, name):
    messages = {}
    for entry in ("validate_config", "solve_case"):
        try:
            getattr(solver, entry)(config)
        except solver.InputError as exc:
            require(bool(str(exc).strip()), f"{name}/{entry}: empty InputError")
            messages[entry] = str(exc)
        else:
            raise AcceptanceError(f"{name}/{entry}: bad input accepted")
    return {"name": name, "verdict": "PASS", "exceptions": messages}


class ValidationRun:
    def __init__(self, root, log=None, checkpoint=None):
        self.solver, self.fingerprint = load_solver(root)
        self.records = {}
        self.log = log or (lambda message: None)
        self.checkpoint = checkpoint or (lambda records, stage: None)

    def case(self, group, resolution=16, scale=None):
        name = f"{group}_{resolution}" + (f"_{scale}" if scale else "")
        if name not in self.records:
            config = base_config(group, resolution)
            if scale == "pressure_x2":
                config["pressure_pa"] *= 2.0
            elif scale == "viscosity_x2":
                config["viscosity_pa_s"] *= 2.0
            elif scale is not None:
                raise AcceptanceError(f"unknown scaling case: {scale}")
            self.log(f"CASE_START name={name} collected_at={timestamp()}")
            data = {
                "name": name, "geometry": config["geometry"], "group": group,
                "resolution": resolution, "config": config,
                "collected_at": timestamp(), "verdict": "RUNNING",
            }
            self.records[name] = data
            self.checkpoint(self.records, f"case_start:{name}")
            result = self.solver.solve_case(config)
            encoded = json_bytes(result)
            data.update({"result_sha256": digest(encoded), "result": result,
                         "metrics": result.get("metrics", {})})
            self.checkpoint(self.records, f"case_raw:{name}")
            try:
                data["independent"] = audit_result(result, config)
            except Exception as exc:
                data["verdict"] = "FAIL"
                data["failure"] = {"type": type(exc).__name__, "message": str(exc)}
                self.checkpoint(self.records, f"case_fail:{name}")
                raise
            data["verdict"] = "PASS"
            self.checkpoint(self.records, f"case_pass:{name}")
            self.log(f"CASE_END name={name} verdict=PASS")
        record = self.records[name]
        require(record.get("verdict") == "PASS", f"case previously failed: {name}")
        return record


def external_path(path):
    path = Path(path).expanduser().resolve()
    path.relative_to(ARTIFACT_ROOT.resolve())
    return path


def report_summary(report):
    require(isinstance(report, dict) and report.get("verdict") in ("PASS", "FAIL"),
            "invalid report")
    if report["verdict"] == "PASS":
        require(bool(report.get("cases")) and bool(report.get("convergence"))
                and bool(report.get("rejections")), "empty PASS report")
    return (f"NUMERICS verdict={report['verdict']} collected_at={report['collected_at']} "
            f"cases={len(report['cases'])} convergence={len(report['convergence'])} "
            f"rejections={len(report['rejections'])} checks={report['checks_executed']} "
            f"solver_sha256={report.get('solver', {}).get('sha256', 'NA')}")


class ReportWriter:
    """Persist immutable chunks and replace only this invocation's own report."""
    def __init__(self, out, report):
        self.out, self.report = out, report
        self.previous_sha = None
        self.sequence = 0
        self.raw_files = {}
        self.parts = out.parent / f"{out.stem}-parts-{uuid.uuid4().hex}"
        self.parts.mkdir(parents=True, exist_ok=False)
        report["parts_dir"] = str(self.parts)

    def checkpoint(self, records=None, stage="checkpoint"):
        if records is not None:
            public = []
            for name, record in records.items():
                row = {key: value for key, value in record.items() if key != "result"}
                if "result" in record:
                    if name not in self.raw_files:
                        path = self.parts / f"raw-{name}.json"
                        encoded = json_bytes(record["result"])
                        with path.open("xb") as output:
                            output.write(encoded)
                        require(digest(path.read_bytes()) == record["result_sha256"],
                                f"raw case read-back differs: {name}")
                        self.raw_files[name] = str(path)
                    row["result_path"] = self.raw_files[name]
                public.append(row)
            self.report["cases"] = public
        self.report["updated_at"] = timestamp()
        self.report["stage"] = stage
        self.report["verdict_line"] = report_summary(self.report)
        encoded = json_bytes(self.report) + b"\n"
        chunk = self.parts / f"checkpoint-{self.sequence:04d}.json"
        with chunk.open("xb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        require(chunk.read_bytes() == encoded, "checkpoint read-back bytes differ")
        if self.previous_sha is None:
            with self.out.open("xb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
        else:
            require(digest(self.out.read_bytes()) == self.previous_sha,
                    f"report changed by another writer: {self.out}")
            staging = self.parts / f"publish-{self.sequence:04d}.json"
            with staging.open("xb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(staging, self.out)
        require(self.out.read_bytes() == encoded, "report read-back bytes differ")
        self.previous_sha = digest(encoded)
        self.sequence += 1


def collect(root, report, log, writer):
    run = ValidationRun(root, log, writer.checkpoint)
    report["solver"] = run.fingerprint
    writer.checkpoint(stage="solver_loaded")
    try:
        for group in GEOMETRIES:
            records = [run.case(group, grid) for grid in GRIDS]
            report["convergence"][group] = convergence(records)
            report["checks_executed"] += len(records) + 1
            writer.checkpoint(stage=f"convergence:{group}")
            for scale, multiplier in (("pressure_x2", 2.0), ("viscosity_x2", 0.5)):
                changed = run.case(group, 16, scale)
                audit_scale(records[1]["result"], changed["result"], multiplier)
                report["scaling"].append({"name": changed["name"], "verdict": "PASS",
                                          "expected_multiplier": multiplier})
                report["checks_executed"] += 1
                writer.checkpoint(stage=f"scaling:{changed['name']}")
        for name, config in invalid_configs().items():
            report["rejections"].append(check_rejection(run.solver, config, name))
            report["checks_executed"] += 1
            if len(report["rejections"]) % 20 == 0:
                writer.checkpoint(stage=f"rejections:{len(report['rejections'])}")
        require(run.records and report["convergence"] and report["rejections"],
                "empty numerical acceptance collection")
        assert_source_unchanged(run.fingerprint)
    finally:
        writer.checkpoint(run.records, "cases_collected")


def read_report(path):
    data = Path(path).read_bytes()
    report = json.loads(data)
    json_bytes(report)
    for row in report["cases"]:
        if "result_path" in row:
            raw = Path(row["result_path"]).read_bytes()
            require(digest(raw) == row["result_sha256"],
                    f"raw artifact changed: {row['result_path']}")
    print(report_summary(report))
    for group, row in report["convergence"].items():
        print(f"CONVERGENCE group={group} grids={row['resolutions']} "
              f"l2={row['quadrature_relative_l2_error']['errors']} "
              f"flow={row['relative_flow_error']['errors']}")
    if report.get("failure"):
        print(f"FAILURE type={report['failure']['type']} message={report['failure']['message']}")
    print(f"REPORT sha256={digest(data)} bytes={len(data)} path={Path(path).resolve()}")
    return 0 if report["verdict"] == "PASS" else report["exit_code"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solver-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--log-dir", type=Path, default=ARTIFACT_ROOT / "logs")
    parser.add_argument("--read-report", type=Path)
    args = parser.parse_args(argv)
    if args.read_report:
        return read_report(args.read_report)
    if args.out is None:
        parser.error("--out is required for collection")
    out = external_path(args.out)
    require(not out.exists(), f"refusing to overwrite report: {out}")
    log_dir = external_path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"numerics-{datetime.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}.log"
    report = {
        "schema_version": 1, "collected_at": timestamp(), "owner": "OpenSim-seat-2",
        "solver_root": str(args.solver_root.resolve()), "verdict": "FAIL",
        "state": "RUNNING",
        "limits": dict(LIMITS), "cases": [], "convergence": {}, "scaling": [],
        "rejections": [], "checks_executed": 0, "exit_code": 2,
        "validator_sha256": digest(Path(__file__).read_bytes()),
        "log_path": str(log_path),
    }
    writer = ReportWriter(out, report)
    with log_path.open("x", encoding="utf-8") as stream:
        def log(message):
            stream.write(message + "\n")
            stream.flush()

        log(f"COLLECTION_START collected_at={report['collected_at']} root={args.solver_root}")
        writer.checkpoint(stage="collection_start")
        try:
            collect(args.solver_root, report, log, writer)
            report["verdict"] = "PASS"
            report["exit_code"] = 0
            report["state"] = "COMPLETE"
        except Exception as exc:
            report["exit_code"] = 1 if isinstance(exc, AcceptanceError) else 2
            report["state"] = "FAILED"
            report["verdict"] = "FAIL"
            report["failure"] = {"type": type(exc).__name__, "message": str(exc),
                                 "traceback": traceback.format_exc()}
            log(report["failure"]["traceback"])
        report["finished_at"] = timestamp()
        writer.checkpoint(stage="collection_finished")
        encoded = out.read_bytes()
        log(report["verdict_line"])
        print(report["verdict_line"])
        if report.get("failure"):
            print(f"FAILURE {report['failure']['type']}: {report['failure']['message']}",
                  file=sys.stderr)
        print(f"REPORT sha256={digest(encoded)} bytes={len(encoded)} path={out} log={log_path}")
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
