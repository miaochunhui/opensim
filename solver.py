"""Bounded P1 FEM for fully developed, steady Newtonian flow in straight pipes.

The only solved field is axial velocity on a two-dimensional cross-section:
``-mu * Laplacian(u) = pressure_pa / length_m``, with zero wall velocity.
All assembly, integration and reference calculations use SI units. Coordinates
are converted to millimetres only at the JSON boundary. Analytical references
never supply the finite-element solution.

Run ``python3 -B solver.py --smoke --out <absolute external JSON path>`` for
reproducible kernel smoke checks without modifying the repository or installing
dependencies. The independent numerical report belongs to
``tools/validate_numerics.py``, not this smoke entry point.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from numbers import Integral, Real
from pathlib import Path
import warnings as python_warnings

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import MatrixRankWarning, spsolve


class InputError(ValueError):
    """Invalid complete input, including a flow outside the laminar model.

    ``field`` is optional metadata for HTTP clients; the message always states
    the offending constraint. No missing value is replaced with a default.
    """

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


class SolverError(RuntimeError):
    """Mesh, assembly, linear solve or independent numerical check failed."""


CONFIG = {
    "geometry": "circle",
    "width_mm": 4.0,
    "height_mm": 4.0,
    "length_mm": 100.0,
    "pressure_pa": 1.0,
    "viscosity_pa_s": 0.001,
    "density_kg_m3": 998.0,
    "resolution": 16,
}

_RANGES = {
    "width_mm": (0.1, 100.0),
    "height_mm": (0.1, 100.0),
    "length_mm": (1.0, 10000.0),
    "pressure_pa": (0.0001, 100000.0),
    "viscosity_pa_s": (0.00001, 10.0),
    "density_kg_m3": (0.1, 20000.0),
}
_INPUT_KEYS = frozenset((*_RANGES, "geometry", "resolution"))
_SERIES_ODD = np.arange(1, 512, 2, dtype=float)
_RE_LIMIT = 2000.0

# Seven-point Dunavant quadrature integrates degree-five polynomials exactly.
_BARYCENTRIC = np.array(
    [
        [1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0],
        [0.059715871789770, 0.470142064105115, 0.470142064105115],
        [0.470142064105115, 0.059715871789770, 0.470142064105115],
        [0.470142064105115, 0.470142064105115, 0.059715871789770],
        [0.797426985353087, 0.101286507323456, 0.101286507323456],
        [0.101286507323456, 0.797426985353087, 0.101286507323456],
        [0.101286507323456, 0.101286507323456, 0.797426985353087],
    ]
)
_QUADRATURE_WEIGHTS = np.array(
    [0.225, *([0.132394152788506] * 3), *([0.125939180544827] * 3)]
)


def validate_config(config: dict) -> dict:
    """Return a fresh, JSON-native complete configuration or raise InputError.

    Dimensions are millimetres, pressure is a positive inlet-to-outlet drop,
    dynamic viscosity is Pa s and density is kg/m3. Booleans, strings used as
    numbers, nonfinite values, extra/missing keys and nonintegral resolutions
    are invalid. A reference-flow Reynolds check is mesh-independent, so a
    coarse mesh cannot make an inadmissible turbulent case appear laminar.
    ``solve_case`` additionally checks the larger reference/numerical Reynolds
    number after solving.
    """
    if not isinstance(config, dict):
        raise InputError("config must be a dict containing all required fields")
    missing = _INPUT_KEYS.difference(config)
    extra = set(config).difference(_INPUT_KEYS)
    if missing or extra:
        details = []
        if missing:
            details.append("missing fields: " + ", ".join(sorted(missing)))
        if extra:
            details.append(
                "unknown fields: " + ", ".join(sorted(repr(key) for key in extra))
            )
        raise InputError("; ".join(details))

    geometry = config["geometry"]
    if not isinstance(geometry, str) or geometry not in ("circle", "rectangle"):
        raise InputError("geometry must be 'circle' or 'rectangle'", "geometry")
    normalized = {"geometry": str(geometry)}
    for field, (lower, upper) in _RANGES.items():
        value = config[field]
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
            raise InputError(f"{field} must be a finite real number, not bool", field)
        try:
            number = float(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise InputError(f"{field} cannot be represented as a finite number", field) from exc
        if not math.isfinite(number):
            raise InputError(f"{field} must be finite", field)
        if not lower <= number <= upper:
            raise InputError(f"{field} must be in [{lower:g}, {upper:g}]", field)
        normalized[field] = number

    resolution = config["resolution"]
    if isinstance(resolution, (bool, np.bool_)) or not isinstance(resolution, Integral):
        raise InputError("resolution must be an integer in [8, 48], not bool", "resolution")
    if not 8 <= resolution <= 48:
        raise InputError("resolution must be an integer in [8, 48]", "resolution")
    normalized["resolution"] = int(resolution)
    width, height = normalized["width_mm"], normalized["height_mm"]
    if geometry == "circle" and width != height:
        raise InputError("circle height_mm must equal diameter width_mm", "height_mm")
    if geometry == "rectangle" and not 0.1 <= width / height <= 10.0:
        raise InputError("rectangle width_mm / height_mm must be in [0.1, 10]", "height_mm")
    _checked_reynolds(normalized)
    return normalized


def _physical_parameters(config: dict) -> tuple[float, float, float, float]:
    """Return width, height, axial driving gradient and viscosity in SI units."""
    return (
        config["width_mm"] * 1.0e-3,
        config["height_mm"] * 1.0e-3,
        config["pressure_pa"] / (config["length_mm"] * 1.0e-3),
        config["viscosity_pa_s"],
    )


def _section(config: dict) -> tuple[float, float]:
    """Return ideal cross-section area (m2) and hydraulic diameter (m)."""
    width, height, _, _ = _physical_parameters(config)
    if config["geometry"] == "circle":
        return math.pi * width**2 / 4.0, width
    return width * height, 2.0 * width * height / (width + height)


def _reference_flow_rate(config: dict) -> float:
    """Integrate the ideal-domain reference, independently of any FEM mesh.

    The circle uses Hagen-Poiseuille flow. For a rectangle, a is the longer
    side, b the shorter side and the odd Fourier series is
    ``Q = G*a*b**3/(12*mu) *
    (1 - 192*b/(pi**5*a)*sum(tanh(n*pi*a/(2*b))/n**5))``.
    Keeping 256 odd terms bounds the omitted correction below 3e-12 relative
    throughout the allowed aspect ratios. The series is not a fitted formula.
    """
    width, height, gradient, viscosity = _physical_parameters(config)
    if config["geometry"] == "circle":
        return math.pi * gradient * (width / 2.0) ** 4 / (8.0 * viscosity)
    long_side, short_side = max(width, height), min(width, height)
    terms = np.tanh(_SERIES_ODD * math.pi * long_side / (2.0 * short_side))
    correction = (
        192.0
        * short_side
        / (math.pi**5 * long_side)
        * float(np.sum(terms / _SERIES_ODD**5))
    )
    return gradient * long_side * short_side**3 / (12.0 * viscosity) * (1.0 - correction)


def _checked_reynolds(config: dict, numerical_mean: float | None = None) -> float:
    """Reject Re > 2000 using the greater ideal-reference and FEM mean speed."""
    area, diameter = _section(config)
    mean = _reference_flow_rate(config) / area
    if numerical_mean is not None:
        if not math.isfinite(numerical_mean) or numerical_mean <= 0.0:
            raise SolverError("numerical mean velocity is nonfinite or nonpositive")
        mean = max(mean, numerical_mean)
    reynolds = config["density_kg_m3"] * mean * diameter / config["viscosity_pa_s"]
    if not math.isfinite(reynolds):
        raise SolverError("Reynolds calculation produced a nonfinite value")
    if reynolds > _RE_LIMIT:
        raise InputError(
            f"Reynolds number {reynolds:.12g} exceeds 2000; "
            "fully developed laminar model is not applicable",
            "pressure_pa",
        )
    return reynolds


def _circle_mesh(diameter: float, resolution: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build a disk polygon from rings with 6*k nodes, avoiding a singular fan.

    Each ring has approximately the same edge length in radial and tangential
    directions. Adjacent rings are triangulated by monotonically advancing
    their angular indices; no Delaunay tolerance or random mesher is involved.
    The boundary has 6*resolution vertices on the exact circle. Its chords
    approximate the curved wall and the domain error decreases under refinement.
    """
    radius = diameter / 2.0
    nodes = [(0.0, 0.0)]
    rings = []
    for ring in range(1, resolution + 1):
        count = 6 * ring
        start = len(nodes)
        indices = np.arange(start, start + count, dtype=np.int64)
        rings.append(indices)
        angles = 2.0 * math.pi * np.arange(count) / count
        coordinates = radius * ring / resolution * np.column_stack((np.cos(angles), np.sin(angles)))
        nodes.extend(map(tuple, coordinates))

    triangles = [(0, int(rings[0][i]), int(rings[0][(i + 1) % 6])) for i in range(6)]
    for inner, outer in zip(rings[:-1], rings[1:]):
        inner_count, outer_count = len(inner), len(outer)
        i = j = 0
        while i < inner_count or j < outer_count:
            if i < inner_count and (
                j == outer_count or (i + 1) * outer_count <= (j + 1) * inner_count
            ):
                triangles.append(
                    (int(inner[i % inner_count]), int(outer[j % outer_count]), int(inner[(i + 1) % inner_count]))
                )
                i += 1
            else:
                triangles.append(
                    (int(inner[i % inner_count]), int(outer[j % outer_count]), int(outer[(j + 1) % outer_count]))
                )
                j += 1
    return np.asarray(nodes, dtype=float), np.asarray(triangles, dtype=np.int64), rings[-1]


def _rectangle_mesh(width: float, height: float, resolution: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split an exact rectangular grid with alternating diagonals for symmetry."""
    axis_x = np.linspace(-width / 2.0, width / 2.0, resolution + 1)
    axis_y = np.linspace(-height / 2.0, height / 2.0, resolution + 1)
    x, y = np.meshgrid(axis_x, axis_y)
    nodes = np.column_stack((x.ravel(), y.ravel()))
    triangles = []
    stride = resolution + 1
    for j in range(resolution):
        for i in range(resolution):
            lower_left = j * stride + i
            lower_right = lower_left + 1
            upper_left = lower_left + stride
            upper_right = upper_left + 1
            if (i + j) % 2:
                triangles.extend(
                    [(lower_left, lower_right, upper_left), (lower_right, upper_right, upper_left)]
                )
            else:
                triangles.extend(
                    [(lower_left, lower_right, upper_right), (lower_left, upper_right, upper_left)]
                )
    grid_ids = np.arange(len(nodes)).reshape(stride, stride)
    boundary = np.unique(np.concatenate((grid_ids[0], grid_ids[-1], grid_ids[:, 0], grid_ids[:, -1])))
    return nodes, np.asarray(triangles, dtype=np.int64), boundary


def _build_mesh(config: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    width, height, _, _ = _physical_parameters(config)
    if config["geometry"] == "circle":
        return _circle_mesh(width, config["resolution"])
    return _rectangle_mesh(width, height, config["resolution"])


def _validate_mesh(
    nodes: np.ndarray, triangles: np.ndarray, boundary: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Reject malformed/degenerate meshes before any linear algebra.

    A valid generated mesh is a connected, oriented triangulated disk with
    unique finite nodes, unique CCW cells, no unused nodes or nonmanifold edges,
    and an exact correspondence between exterior edges and Dirichlet nodes.
    Return positive element areas and physical P1 basis gradients.
    """
    if (
        not isinstance(nodes, np.ndarray)
        or nodes.ndim != 2
        or nodes.shape[1] != 2
        or len(nodes) < 4
        or nodes.dtype.kind not in "fi"
        or not np.isfinite(nodes).all()
    ):
        raise SolverError("mesh nodes must be a finite numeric (N, 2) array")
    if len(np.unique(nodes, axis=0)) != len(nodes):
        raise SolverError("mesh contains duplicate node coordinates")
    if (
        not isinstance(triangles, np.ndarray)
        or triangles.ndim != 2
        or triangles.shape[1] != 3
        or len(triangles) == 0
        or triangles.dtype.kind not in "iu"
    ):
        raise SolverError("mesh triangles must be a nonempty integer (T, 3) array")
    if np.any(triangles < 0) or np.any(triangles >= len(nodes)):
        raise SolverError("mesh triangle index lies outside the node array")
    if len(np.unique(np.sort(triangles, axis=1), axis=0)) != len(triangles):
        raise SolverError("mesh contains duplicate triangles")
    if len(np.unique(triangles)) != len(nodes):
        raise SolverError("mesh contains unused nodes")
    if (
        not isinstance(boundary, np.ndarray)
        or boundary.ndim != 1
        or boundary.dtype.kind not in "iu"
        or len(boundary) < 3
        or len(boundary) >= len(nodes)
        or np.any(boundary < 0)
        or np.any(boundary >= len(nodes))
        or len(np.unique(boundary)) != len(boundary)
    ):
        raise SolverError("mesh boundary must contain unique valid indices and leave interior DOFs")

    coordinates = nodes[triangles]
    first_edge = coordinates[:, 1] - coordinates[:, 0]
    second_edge = coordinates[:, 2] - coordinates[:, 0]
    determinants = first_edge[:, 0] * second_edge[:, 1] - first_edge[:, 1] * second_edge[:, 0]
    edge_scale = np.maximum(np.sum(first_edge**2, axis=1), np.sum(second_edge**2, axis=1))
    if np.any(determinants <= 128.0 * np.finfo(float).eps * edge_scale):
        bad = int(np.flatnonzero(determinants <= 128.0 * np.finfo(float).eps * edge_scale)[0])
        raise SolverError(f"mesh triangle {bad} is inverted, degenerate or numerically singular")

    all_edges = np.concatenate((triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]))
    edges, counts = np.unique(np.sort(all_edges, axis=1), axis=0, return_counts=True)
    if np.any(counts > 2):
        raise SolverError("mesh contains nonmanifold edges")
    exterior = edges[counts == 1]
    if not np.array_equal(np.unique(exterior), np.sort(boundary)):
        raise SolverError("Dirichlet boundary nodes do not match all exterior edges")
    _, boundary_degree = np.unique(exterior, return_counts=True)
    if np.any(boundary_degree != 2):
        raise SolverError("mesh exterior is not a closed nonbranching boundary")
    adjacency = coo_matrix(
        (np.ones(len(edges)), (edges[:, 0], edges[:, 1])), shape=(len(nodes), len(nodes))
    ).tocsr()
    if connected_components(adjacency, directed=False, return_labels=False) != 1:
        raise SolverError("mesh has disconnected components")
    if len(nodes) - len(edges) + len(triangles) != 1:
        raise SolverError("mesh topology is not a simply connected triangulated disk")

    x, y = coordinates[:, :, 0], coordinates[:, :, 1]
    gradients = np.stack(
        (
            np.column_stack((y[:, 1] - y[:, 2], y[:, 2] - y[:, 0], y[:, 0] - y[:, 1])),
            np.column_stack((x[:, 2] - x[:, 1], x[:, 0] - x[:, 2], x[:, 1] - x[:, 0])),
        ),
        axis=2,
    ) / determinants[:, None, None]
    if not np.isfinite(gradients).all():
        raise SolverError("mesh basis gradients are nonfinite")
    return determinants / 2.0, gradients


def _solve_fem(
    nodes: np.ndarray,
    triangles: np.ndarray,
    boundary: np.ndarray,
    areas: np.ndarray,
    gradients: np.ndarray,
    driving_gradient: float,
    viscosity: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Assemble exact P1 stiffness/load integrals and eliminate wall DOFs."""
    local_stiffness = viscosity * areas[:, None, None] * np.einsum("tik,tjk->tij", gradients, gradients)
    stiffness = coo_matrix(
        (
            local_stiffness.ravel(),
            (np.repeat(triangles, 3, axis=1).ravel(), np.tile(triangles, (1, 3)).ravel()),
        ),
        shape=(len(nodes), len(nodes)),
    ).tocsr()
    load = np.zeros(len(nodes))
    np.add.at(load, triangles.ravel(), np.repeat(driving_gradient * areas / 3.0, 3))
    free_mask = np.ones(len(nodes), dtype=bool)
    free_mask[boundary] = False
    free = np.flatnonzero(free_mask)
    velocity = np.zeros(len(nodes))
    if not np.isfinite(stiffness.data).all() or not np.isfinite(load).all():
        raise SolverError("FEM assembly produced nonfinite stiffness or load")
    try:
        with python_warnings.catch_warnings():
            python_warnings.simplefilter("error", MatrixRankWarning)
            velocity[free] = spsolve(stiffness[free][:, free], load[free])
    except (MatrixRankWarning, RuntimeError, ValueError, FloatingPointError) as exc:
        raise SolverError(f"Dirichlet-eliminated sparse FEM solve failed: {exc}") from exc
    if not np.isfinite(velocity).all():
        raise SolverError("sparse FEM solve produced nonfinite velocity")
    if np.any(velocity[free] <= 0.0):
        raise SolverError("positive pressure drop produced a nonpositive interior FEM velocity")
    return velocity, free, load


def _independent_checks(
    nodes: np.ndarray,
    triangles: np.ndarray,
    boundary: np.ndarray,
    velocity: np.ndarray,
    free: np.ndarray,
    load: np.ndarray,
    driving_gradient: float,
    viscosity: float,
) -> tuple[float, float]:
    """Recompute element forces geometrically without the assembled matrix.

    Gradients are reconstructed from edge-normal vectors, then element stress
    contributions and the body load are scattered into a new nodal vector.
    The free-node residual tests the weak equations. Wall reactions include
    the constrained-node body load: their sum must cancel G*mesh_area. This is
    discrete FEM force balance, not an assertion that raw P1 wall derivatives
    exactly represent the smooth continuum wall shear.
    """
    coordinates = nodes[triangles]
    opposite_edges = coordinates[:, [2, 0, 1]] - coordinates[:, [1, 2, 0]]
    double_areas = (
        (coordinates[:, 1, 0] - coordinates[:, 0, 0]) * (coordinates[:, 2, 1] - coordinates[:, 0, 1])
        - (coordinates[:, 1, 1] - coordinates[:, 0, 1]) * (coordinates[:, 2, 0] - coordinates[:, 0, 0])
    )
    basis = np.stack((-opposite_edges[:, :, 1], opposite_edges[:, :, 0]), axis=2) / double_areas[:, None, None]
    velocity_gradient = np.sum(velocity[triangles][:, :, None] * basis, axis=1)
    element_force = (
        viscosity * double_areas[:, None] / 2.0
        * np.sum(basis * velocity_gradient[:, None, :], axis=2)
        - driving_gradient * double_areas[:, None] / 6.0
    )
    nodal_residual = np.zeros(len(nodes))
    np.add.at(nodal_residual, triangles.ravel(), element_force.ravel())
    residual = float(np.linalg.norm(nodal_residual[free]) / np.linalg.norm(load[free]))
    driving_force = driving_gradient * float(np.sum(double_areas)) / 2.0
    balance = abs(float(np.sum(nodal_residual[boundary])) + driving_force) / driving_force
    if not math.isfinite(residual) or residual > 1.0e-8:
        raise SolverError(f"independently reassembled relative residual is {residual:.12g}, limit 1e-8")
    if not math.isfinite(balance) or balance > 1.0e-8:
        raise SolverError(f"independent discrete force-balance error is {balance:.12g}, limit 1e-8")
    return residual, balance


def _reference_velocity(config: dict, points: np.ndarray) -> np.ndarray:
    """Evaluate circular parabolic or rectangular Fourier velocity at SI points.

    For the rectangle the short-side parabola is corrected by the exact odd
    sine/cosh series along the long side. The cosh quotient is evaluated as
    exponentials with nonpositive exponents, avoiding overflow for aspect 10.
    Long/short coordinates are swapped when needed. The 256 odd terms are
    fixed and bounded; no PDE solution is used in this reference.
    """
    width, height, gradient, viscosity = _physical_parameters(config)
    if config["geometry"] == "circle":
        return gradient / (4.0 * viscosity) * ((width / 2.0) ** 2 - np.sum(points**2, axis=-1))
    if width >= height:
        long_coordinate, short_coordinate = points[..., 0], points[..., 1]
        long_side, short_side = width, height
    else:
        long_coordinate, short_coordinate = points[..., 1], points[..., 0]
        long_side, short_side = height, width
    short_position = short_coordinate + short_side / 2.0
    velocity = gradient / (2.0 * viscosity) * short_position * (short_side - short_position)
    correction = np.zeros_like(velocity)
    for odd in _SERIES_ODD:
        exponent = odd * math.pi / short_side
        limit = exponent * long_side / 2.0
        argument = exponent * np.abs(long_coordinate)
        quotient = np.exp(argument - limit) * (1.0 + np.exp(-2.0 * argument)) / (1.0 + math.exp(-2.0 * limit))
        correction += quotient * np.sin(exponent * short_position) / odd**3
    return velocity - 4.0 * gradient * short_side**2 / (viscosity * math.pi**3) * correction


def _relative_l2_error(
    config: dict, nodes: np.ndarray, triangles: np.ndarray, areas: np.ndarray, velocity: np.ndarray
) -> float:
    """Integrate true P1-versus-analytic error, not just nodal sample error.

    Seven-point triangle quadrature includes intraelement interpolation error.
    Integration is over the FEM polygon; missing circular boundary slivers are
    accounted for separately by comparison with the ideal-domain flow rate.
    """
    points = np.einsum("qi,tij->tqj", _BARYCENTRIC, nodes[triangles])
    finite_element_values = velocity[triangles] @ _BARYCENTRIC.T
    reference = _reference_velocity(config, points)
    weights = areas[:, None] * _QUADRATURE_WEIGHTS[None, :]
    denominator = float(np.sum(weights * reference**2))
    if not math.isfinite(denominator) or denominator <= 0.0:
        raise SolverError("reference L2 norm is nonfinite or nonpositive")
    error = math.sqrt(float(np.sum(weights * (finite_element_values - reference) ** 2)) / denominator)
    if not math.isfinite(error):
        raise SolverError("quadrature L2 error is nonfinite")
    return error


def solve_case(config: dict) -> dict:
    """Solve the contract's laminar cross-section model with real sparse FEM.

    Flow rate integrates the P1 field exactly. Mean speed uses the actual mesh
    area, while hydraulic diameter and the analytic reference use the requested
    ideal section. The reported Reynolds number is conservatively the larger
    reference/numerical value, also near the 2000 cutoff. Every output is JSON
    native and finite, with wall velocity exactly zero.
    """
    normalized = validate_config(config)
    nodes, triangles, boundary = _build_mesh(normalized)
    areas, gradients = _validate_mesh(nodes, triangles, boundary)
    ideal_area, diameter = _section(normalized)
    mesh_area = float(np.sum(areas))
    resolution = normalized["resolution"]
    expected_area = ideal_area
    if normalized["geometry"] == "circle":
        angle = 2.0 * math.pi / (6 * resolution)
        expected_area *= math.sin(angle) / angle
    if not math.isclose(mesh_area, expected_area, rel_tol=1.0e-12, abs_tol=0.0):
        raise SolverError(f"mesh area {mesh_area:.12g} differs from expected section {expected_area:.12g}")
    _, _, driving_gradient, viscosity = _physical_parameters(normalized)
    velocity, free, load = _solve_fem(
        nodes, triangles, boundary, areas, gradients, driving_gradient, viscosity
    )
    residual, balance = _independent_checks(
        nodes, triangles, boundary, velocity, free, load, driving_gradient, viscosity
    )
    flow_rate = float(np.sum(areas * np.mean(velocity[triangles], axis=1)))
    mean_velocity = flow_rate / mesh_area
    reynolds = _checked_reynolds(normalized, mean_velocity)
    reference_flow = _reference_flow_rate(normalized)
    reference_velocity = _reference_velocity(normalized, nodes)
    reference_velocity[boundary] = 0.0
    messages = []
    if normalized["geometry"] == "circle":
        messages.append(
            f"The circular wall is approximated by a {6 * resolution}-edge inscribed polygon; "
            "L2 error is integrated over that polygon and flow error uses the ideal circle."
        )
    if reynolds >= 1800.0:
        messages.append(
            "Reynolds number is close to the 2000 model limit; "
            "the larger analytical-reference and numerical value is used."
        )
    result = {
        "schema_version": 1,
        "config": normalized,
        "model": {
            "name": "Fully developed straight-pipe laminar cross-section flow",
            "discretization": "P1 triangular FEM, exact element integrals, sparse Dirichlet elimination",
            "assumptions": [
                "Steady, incompressible Newtonian fluid; constant dynamic viscosity and density.",
                "Straight uniform pipe, fully developed axial velocity, stationary no-slip walls.",
                "Positive pressure drop; pressure varies linearly along the pipe by assumption.",
                "No entrance effects, bends, turbulence, transients or coupled physics; Re <= 2000.",
                "Assembly uses metres and SI units; exported node coordinates use millimetres.",
                "Reynolds number uses the larger reference and numerical cross-sectional mean.",
                "Force balance uses discrete Dirichlet wall reactions, including the wall-node load.",
            ],
        },
        "mesh": {
            "nodes_mm": (nodes * 1000.0).tolist(),
            "triangles": triangles.tolist(),
            "boundary_nodes": boundary.tolist(),
        },
        "fields": {"velocity_m_s": velocity.tolist()},
        "metrics": {
            "node_count": len(nodes),
            "element_count": len(triangles),
            "dof_count": len(free),
            "max_velocity_m_s": float(np.max(velocity)),
            "mean_velocity_m_s": mean_velocity,
            "flow_rate_m3_s": flow_rate,
            "reynolds": reynolds,
            "hydraulic_diameter_mm": diameter * 1000.0,
            "relative_residual": residual,
            "relative_l2_error": _relative_l2_error(normalized, nodes, triangles, areas, velocity),
            "reference_flow_rate_m3_s": reference_flow,
            "relative_flow_error": abs(flow_rate - reference_flow) / reference_flow,
            "force_balance_relative_error": balance,
        },
        "reference": {
            "velocity_m_s": reference_velocity.tolist(),
            "method": (
                "Hagen-Poiseuille analytic parabolic circle solution"
                if normalized["geometry"] == "circle"
                else "Rectangular Poisson sine/cosh Fourier series (256 odd terms); integrated flow series"
            ),
        },
        "warnings": messages,
    }
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SolverError(f"result violates finite JSON contract: {exc}") from exc
    return result


def _smoke() -> dict:
    """Exercise real solves and negative controls; fail with fixture coordinates."""
    from unittest.mock import patch

    rows = []
    results = {}
    checks = []

    def require(condition: bool, label: str) -> None:
        if not condition:
            raise SolverError(f"smoke check failed: {label}")
        checks.append(label)

    for geometry in ("circle", "rectangle"):
        previous_flow_error = previous_l2_error = math.inf
        for resolution in (8, 16, 32):
            config = {**CONFIG, "geometry": geometry, "resolution": resolution}
            result = solve_case(config)
            results[(geometry, resolution)] = result
            metrics = result["metrics"]
            require(metrics["relative_flow_error"] < previous_flow_error, f"{geometry}/{resolution}: flow convergence")
            require(metrics["relative_l2_error"] < previous_l2_error, f"{geometry}/{resolution}: L2 convergence")
            require(metrics["relative_residual"] < 1.0e-9, f"{geometry}/{resolution}: independent residual")
            require(metrics["force_balance_relative_error"] < 1.0e-9, f"{geometry}/{resolution}: discrete wall force")
            require(
                all(result["fields"]["velocity_m_s"][i] == 0.0 for i in result["mesh"]["boundary_nodes"]),
                f"{geometry}/{resolution}: exact Dirichlet",
            )
            require(json.loads(json.dumps(result, allow_nan=False)) == result, f"{geometry}/{resolution}: finite JSON roundtrip")
            rows.append({"geometry": geometry, "resolution": resolution, **metrics})
            previous_flow_error = metrics["relative_flow_error"]
            previous_l2_error = metrics["relative_l2_error"]

    circle = results[("circle", 32)]
    diameter = CONFIG["width_mm"] * 1.0e-3
    circular_flow = (
        CONFIG["pressure_pa"] * math.pi * diameter**4
        / (128.0 * CONFIG["viscosity_pa_s"] * CONFIG["length_mm"] * 1.0e-3)
    )
    require(
        math.isclose(circle["metrics"]["reference_flow_rate_m3_s"], circular_flow, rel_tol=1.0e-14),
        "SI Hagen-Poiseuille independent diameter-form flow",
    )
    for geometry in ("circle", "rectangle"):
        base = results[(geometry, 16)]
        scaled = solve_case({**base["config"], "pressure_pa": 2.0})
        require(
            np.allclose(scaled["fields"]["velocity_m_s"], 2.0 * np.array(base["fields"]["velocity_m_s"]), rtol=1.0e-11, atol=0.0),
            f"{geometry}: linear pressure scaling",
        )
        diluted = solve_case({**base["config"], "viscosity_pa_s": 0.002})
        require(
            math.isclose(diluted["metrics"]["flow_rate_m3_s"], base["metrics"]["flow_rate_m3_s"] / 2.0, rel_tol=1.0e-11),
            f"{geometry}: inverse viscosity scaling",
        )
        long_pipe = solve_case({**base["config"], "length_mm": 200.0})
        require(
            math.isclose(long_pipe["metrics"]["flow_rate_m3_s"], base["metrics"]["flow_rate_m3_s"] / 2.0, rel_tol=1.0e-11),
            f"{geometry}: inverse length scaling",
        )
        enlarged = solve_case({**base["config"], "width_mm": 8.0, "height_mm": 8.0})
        require(
            math.isclose(enlarged["metrics"]["flow_rate_m3_s"], 16.0 * base["metrics"]["flow_rate_m3_s"], rel_tol=1.0e-11),
            f"{geometry}: fourth-power length-unit scaling",
        )
        finest = solve_case({**base["config"], "resolution": 48})
        require(
            finest["metrics"]["relative_l2_error"] < results[(geometry, 32)]["metrics"]["relative_l2_error"],
            f"{geometry}: maximum resolution convergence",
        )
        rows.append({"geometry": geometry, "resolution": 48, **finest["metrics"]})

    for label, width, height in (
        ("rectangle-aspect-2", 4.0, 2.0),
        ("rectangle-aspect-10", 40.0, 4.0),
    ):
        previous_flow_error = previous_l2_error = math.inf
        for resolution in (8, 16, 32):
            result = solve_case({
                **CONFIG, "geometry": "rectangle", "width_mm": width,
                "height_mm": height, "resolution": resolution,
            })
            metrics = result["metrics"]
            require(metrics["relative_flow_error"] < previous_flow_error, f"{label}/{resolution}: flow convergence")
            require(metrics["relative_l2_error"] < previous_l2_error, f"{label}/{resolution}: L2 convergence")
            rows.append({"geometry": label, "resolution": resolution, **metrics})
            previous_flow_error = metrics["relative_flow_error"]
            previous_l2_error = metrics["relative_l2_error"]
    wide = solve_case({**CONFIG, "geometry": "rectangle", "width_mm": 40.0, "height_mm": 4.0})
    tall = solve_case({**wide["config"], "width_mm": 4.0, "height_mm": 40.0})
    require(
        math.isclose(wide["metrics"]["flow_rate_m3_s"], tall["metrics"]["flow_rate_m3_s"], rel_tol=1.0e-12),
        "rectangle: aspect-10 axis-swap symmetry",
    )
    require(
        math.isclose(wide["metrics"]["reference_flow_rate_m3_s"], tall["metrics"]["reference_flow_rate_m3_s"], rel_tol=1.0e-14),
        "rectangle: aspect-10 reference symmetry",
    )

    invalid = [
        ("not a dict", []),
        ("missing fields", {}),
        ("extra field", {**CONFIG, "extra": 1}),
        ("bad geometry", {**CONFIG, "geometry": []}),
        ("circle mismatch", {**CONFIG, "height_mm": 4.1}),
        ("aspect ratio", {**CONFIG, "geometry": "rectangle", "height_mm": 0.1}),
        ("float resolution", {**CONFIG, "resolution": 16.0}),
        ("bool resolution", {**CONFIG, "resolution": True}),
        ("small resolution", {**CONFIG, "resolution": 7}),
        ("large resolution", {**CONFIG, "resolution": 49}),
        ("NaN resolution", {**CONFIG, "resolution": float("nan")}),
        ("infinite resolution", {**CONFIG, "resolution": float("inf")}),
    ]
    for field, (lower, upper) in _RANGES.items():
        for label, value in (
            ("bool", True),
            ("NaN", float("nan")),
            ("infinity", float("inf")),
            ("numeric string", str(CONFIG[field])),
            ("below range", lower / 2.0),
            ("above range", upper * 2.0),
            ("unrepresentable integer", 10**1000),
        ):
            invalid.append((f"{field}: {label}", {**CONFIG, field: value}))
    for label, bad_config in invalid:
        try:
            validate_config(bad_config)
        except InputError as exc:
            require(bool(str(exc)), f"invalid {label}: explanatory InputError")
        else:
            raise SolverError(f"smoke accepted invalid fixture: {label}")

    threshold_pressure = (
        32.0 * CONFIG["viscosity_pa_s"] ** 2 * CONFIG["length_mm"] * 1.0e-3
        / (CONFIG["density_kg_m3"] * diameter**3)
    )
    for resolution in (8, 16, 32):
        near = solve_case({**CONFIG, "resolution": resolution, "pressure_pa": threshold_pressure * 1999.9})
        require(math.isclose(near["metrics"]["reynolds"], 1999.9, rel_tol=1.0e-12), f"circle/{resolution}: near-limit conservative Re")
        try:
            solve_case({**CONFIG, "resolution": resolution, "pressure_pa": threshold_pressure * 2000.1})
        except InputError as exc:
            require("Reynolds" in str(exc), f"circle/{resolution}: above-limit rejection")
        else:
            raise SolverError(f"smoke accepted Re > 2000 at resolution {resolution}")
    rectangle_config = {**CONFIG, "geometry": "rectangle", "height_mm": 2.0}
    reference_re = _checked_reynolds(rectangle_config)
    for resolution in (8, 16, 32):
        near = solve_case({
            **rectangle_config, "resolution": resolution,
            "pressure_pa": rectangle_config["pressure_pa"] * 1999.9 / reference_re,
        })
        require(
            math.isclose(near["metrics"]["reynolds"], 1999.9, rel_tol=1.0e-12),
            f"rectangle/{resolution}: near-limit conservative Re",
        )
        try:
            solve_case({
                **rectangle_config, "resolution": resolution,
                "pressure_pa": rectangle_config["pressure_pa"] * 2000.1 / reference_re,
            })
        except InputError as exc:
            require("Reynolds" in str(exc), f"rectangle/{resolution}: above-limit rejection")
        else:
            raise SolverError(f"smoke accepted rectangular Re > 2000 at resolution {resolution}")
    with patch(f"{__name__}._reference_flow_rate", return_value=circular_flow * 0.95):
        try:
            solve_case({**CONFIG, "resolution": 32, "pressure_pa": threshold_pressure * 2010.0})
        except InputError as exc:
            require("Reynolds" in str(exc), "numerical Re rejects deliberately underestimated reference")
        else:
            raise SolverError("numerical Reynolds safety check did not reject the negative control")

    valid_config = validate_config(CONFIG)
    nodes, triangles, boundary = _build_mesh(valid_config)
    broken = []
    bad_nodes = nodes.copy()
    bad_nodes[0, 0] = float("nan")
    broken.append(("NaN coordinate", bad_nodes, triangles, boundary))
    bad_nodes = nodes.copy()
    bad_nodes[1] = bad_nodes[0]
    broken.append(("duplicate node", bad_nodes, triangles, boundary))
    bad_triangles = triangles.copy()
    bad_triangles[0, 1] = bad_triangles[0, 0]
    broken.append(("zero-area triangle", nodes, bad_triangles, boundary))
    bad_triangles = triangles.copy()
    bad_triangles[0] = bad_triangles[0, ::-1]
    broken.append(("inverted triangle", nodes, bad_triangles, boundary))
    bad_triangles = triangles.copy()
    bad_triangles[0, 0] = len(nodes)
    broken.append(("out-of-range index", nodes, bad_triangles, boundary))
    broken.append(("float indices", nodes, triangles.astype(float), boundary))
    broken.append(("duplicate triangle", nodes, np.vstack((triangles, triangles[0])), boundary))
    broken.append(("missing wall node", nodes, triangles, boundary[1:]))
    broken.append(("interior marked wall", nodes, triangles, np.append(boundary, 0)))
    broken.append(("unused node", np.vstack((nodes, [0.1, 0.1])), triangles, boundary))
    for label, bad_nodes, bad_triangles, bad_boundary in broken:
        try:
            _validate_mesh(bad_nodes, bad_triangles, bad_boundary)
        except SolverError as exc:
            require(bool(str(exc)), f"bad mesh {label}: explanatory SolverError")
        else:
            raise SolverError(f"smoke accepted bad mesh: {label}")

    areas, gradients = _validate_mesh(nodes, triangles, boundary)
    _, _, driving_gradient, viscosity = _physical_parameters(valid_config)
    velocity, free, load = _solve_fem(nodes, triangles, boundary, areas, gradients, driving_gradient, viscosity)
    velocity[free[0]] *= 1.1
    try:
        _independent_checks(nodes, triangles, boundary, velocity, free, load, driving_gradient, viscosity)
    except SolverError as exc:
        require("residual" in str(exc), "independent residual detects deliberately corrupted FEM field")
    else:
        raise SolverError("independent residual accepted corrupted FEM field")

    for geometry in ("circle", "rectangle"):
        for label, overrides in (
            ("small physical scales", {
                "width_mm": 0.1, "height_mm": 0.1, "length_mm": 10000.0,
                "pressure_pa": 0.0001, "viscosity_pa_s": 10.0, "density_kg_m3": 0.1,
            }),
            ("large physical scales", {
                "width_mm": 100.0, "height_mm": 100.0, "length_mm": 1.0,
                "pressure_pa": 100000.0, "viscosity_pa_s": 10.0, "density_kg_m3": 0.1,
            }),
        ):
            result = solve_case({**CONFIG, **overrides, "geometry": geometry, "resolution": 8})
            require(
                json.loads(json.dumps(result, allow_nan=False)) == result,
                f"{geometry}: finite JSON at {label}",
            )

    return {
        "verdict": "PASS",
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_file": str(Path(__file__).resolve()),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": rows,
        "invalid_input_count": len(invalid),
        "invalid_mesh_count": len(broken),
        "check_count": len(checks),
        "checks": checks,
        "scope": "kernel smoke only; independent numerical/HTTP/browser acceptance not implied",
    }


def _main() -> int:
    """Write an exclusive external smoke artifact; preserve failures and exit 1."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="run actual FEM kernel smoke checks")
    parser.add_argument("--out", type=Path, help="new JSON file under /hf3fs-jd/data/tmp/opensim-fluid-v1/")
    args = parser.parse_args()
    if not args.smoke or args.out is None:
        parser.error("--smoke and --out are both required")
    root = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1")
    output = args.out
    if not output.is_absolute() or not output.resolve().is_relative_to(root.resolve()):
        parser.error("--out must be an absolute file under /hf3fs-jd/data/tmp/opensim-fluid-v1/")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as artifact:
        try:
            report = _smoke()
        except Exception as exc:
            json.dump(
                {
                    "verdict": "FAIL",
                    "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                artifact,
                allow_nan=False,
                indent=2,
            )
            artifact.write("\n")
            raise
        json.dump(report, artifact, allow_nan=False, indent=2)
        artifact.write("\n")
    if json.loads(output.read_text(encoding="utf-8")) != report:
        raise SolverError(f"smoke artifact read-back differs: {output}")
    for row in report["cases"]:
        print(
            f"SMOKE geometry={row['geometry']} resolution={row['resolution']} "
            f"nodes={row['node_count']} elements={row['element_count']} "
            f"Q={row['flow_rate_m3_s']:.12g} Qref={row['reference_flow_rate_m3_s']:.12g} "
            f"L2={row['relative_l2_error']:.6g} flow_error={row['relative_flow_error']:.6g} "
            f"residual={row['relative_residual']:.3g} force_error={row['force_balance_relative_error']:.3g}"
        )
    print(
        f"SOLVER_SMOKE verdict=PASS checks={report['check_count']} "
        f"invalid_inputs={report['invalid_input_count']} invalid_meshes={report['invalid_mesh_count']} "
        f"collected_at={report['collected_at']} artifact={output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
