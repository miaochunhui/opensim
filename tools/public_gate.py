"""Verify public OpenSim code identity and real FEM, never deploy or retry.

CLI always checks the fixed public HTTPS domain with normal TLS verification.
Loopback is allowed only through check_endpoint() for independent test fixtures;
its scope is LOCAL_TEST, never a public delivery verdict. All CLI evidence is
append-only under the OpenSim artifact root. This gate does not certify browser,
accessibility, performance, or human release gates.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import ipaddress
import json
import math
import secrets
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from solver import solve_case
from tools.render_page import SOURCE_PATHS, _source_metadata, render_page

PUBLIC_URL = "https://opensim.ai4everyone.top"
ARTIFACT_ROOT = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1")
MAX_RESPONSE = 2_000_000


class GateError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GateError(f"Unexpected HTTP redirect status={code}")


def strict_json(body: bytes) -> object:
    def constant(value):
        raise GateError(f"Non-finite JSON constant: {value}")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise GateError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(body, parse_constant=constant, object_pairs_hook=unique)


def source_hashes() -> dict[str, str]:
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in SOURCE_PATHS}


def fetch(base: str, path: str, payload: dict | None = None) -> tuple[bytes, dict]:
    headers = {"Accept-Encoding": "gzip", "Cache-Control": "no-cache"}
    data = None
    if payload is not None:
        data = json.dumps(payload, allow_nan=False).encode("utf-8")
        headers.update({"Content-Type": "application/json", "Origin": base})
    # A proxy or redirect must not silently turn an external probe into a local one.
    request = Request(base + path, data=data, headers=headers)
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=20) as response:
        if response.status != 200 or response.geturl() != base + path:
            raise GateError(f"Unexpected HTTP response for {path}: {response.status}")
        body = response.read(MAX_RESPONSE + 1)
        if len(body) > MAX_RESPONSE:
            raise GateError(f"Response exceeds limit: {path}")
        result_headers = dict(response.headers.items())
        if response.headers.get("Content-Encoding") == "gzip":
            import io
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                body = stream.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                raise GateError(f"Decompressed response exceeds limit: {path}")
        return body, result_headers


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GateError(message)


class AssetReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script":
            self.references.append(("js", values.get("src")))
        elif tag == "link" and values.get("rel") == "stylesheet":
            self.references.append(("css", values.get("href")))


def page_assets(html: str) -> list[tuple[str, str]]:
    """Check the assets browsers actually load, not unrelated legacy aliases."""
    actual = AssetReferences()
    actual.feed(html)
    desired = AssetReferences()
    desired.feed(render_page())
    require(len(desired.references) == 2
            and {kind for kind, _ in desired.references} == {"js", "css"},
            "Local generated asset contract is incomplete")
    require(actual.references == desired.references,
            "Page asset references differ from the desired generated page")
    result = []
    for kind, relative in actual.references:
        require(isinstance(relative, str), "Page asset reference missing")
        parsed = urlsplit(relative)
        require(not parsed.scheme and not parsed.netloc and not parsed.query
                and not parsed.fragment and relative.startswith("web/app.")
                and relative.endswith("." + kind) and ".." not in relative,
                "Page asset reference is not a bounded same-origin resource")
        result.append((f"web/app.{kind}", "/" + relative))
    return result


def compare_solution(actual: object, config: dict, sources: dict, started: float) -> dict:
    require(isinstance(actual, dict), "Solve response must be an object")
    expected = solve_case(config)
    require(actual.get("schema_version") == 1, "Wrong solve schema")
    require(actual.get("config") == expected["config"], "Wrong or stale solve config")
    require(actual.get("mesh") == expected["mesh"], "Mesh differs from local FEM")
    provenance = actual.get("reproducibility")
    require(isinstance(provenance, dict), "Missing solve provenance")
    require(provenance.get("source_sha256") == sources, "Solve sources differ")
    require(provenance.get("solver_sha256") == sources["solver.py"], "Solver differs")
    canonical = json.dumps(expected["config"], sort_keys=True, allow_nan=False,
                           separators=(",", ":")).encode("utf-8")
    require(provenance.get("config_sha256") == hashlib.sha256(canonical).hexdigest(),
            "Config identity differs")
    computed = datetime.fromisoformat(provenance["computed_at"])
    require(computed.tzinfo is not None, "Compute time must include timezone")
    require(started - 120 <= computed.timestamp() <= time.time() + 120,
            "Compute time is stale or clock skew exceeds 120 seconds")
    fields = actual.get("fields")
    require(isinstance(fields, dict), "Missing finite element field")
    velocity = fields.get("velocity_m_s")
    expected_velocity = expected["fields"]["velocity_m_s"]
    require(isinstance(velocity, list) and len(velocity) == len(expected_velocity),
            "Incomplete finite element field")
    for observed, reference in zip(velocity, expected_velocity):
        require(type(observed) in (float, int) and math.isfinite(observed)
                and math.isclose(observed, reference, rel_tol=1e-9, abs_tol=1e-14),
                "Finite element field differs")
    metrics = actual.get("metrics")
    require(isinstance(metrics, dict), "Missing solve metrics")
    for key, reference in expected["metrics"].items():
        observed = metrics.get(key)
        require(type(observed) in (float, int) and math.isfinite(observed)
                and math.isclose(observed, reference, rel_tol=1e-9, abs_tol=1e-14),
                f"Solve metric differs: {key}")
    return {"config_sha256": provenance["config_sha256"],
            "solver_sha256": provenance["solver_sha256"],
            "node_count": metrics["node_count"],
            "flow_rate_m3_s": metrics["flow_rate_m3_s"],
            "relative_residual": metrics["relative_residual"],
            "computed_at": provenance["computed_at"]}


def check_endpoint(base: str = PUBLIC_URL, *, public: bool = True) -> dict:
    started = time.time()
    report = {
        "schema": 1, "scope": "PUBLIC_CODE_AND_FEM" if public else "LOCAL_TEST",
        "url": base, "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdict": "FAIL", "checks": [], "release_certification": "NOT_ASSESSED",
    }
    try:
        if public:
            require(base == PUBLIC_URL, "Only the fixed OpenSim public HTTPS URL is allowed")
            addresses = sorted({item[4][0] for item in
                                socket.getaddrinfo(urlsplit(base).hostname, 443)})
            require(bool(addresses), "DNS returned no addresses")
            require(all(ipaddress.ip_address(ip).is_global for ip in addresses),
                    "Public hostname resolved to a non-public address")
            report["dns_addresses"] = addresses
        else:
            parsed = urlsplit(base)
            require(parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                    and not parsed.username and not parsed.password
                    and parsed.path == "" and not parsed.query and not parsed.fragment,
                    "Local test endpoint must be a loopback HTTP origin")
        sources = source_hashes()
        report["source_sha256"] = sources
        report["page_build"] = _source_metadata()[2]
        health = strict_json(fetch(base, "/api/health")[0])
        require(isinstance(health, dict) and health.get("status") == "ok"
                and health.get("schema_version") == 1, "Invalid health response")
        require(health.get("source_sha256") == sources, "Running sources differ from desired sources")
        require(health.get("page_build") == report["page_build"], "Running page build differs")
        report["checks"].append({"name": "runtime_identity", "verdict": "PASS"})
        html = fetch(base, "/")[0].decode("utf-8")
        require(report["page_build"] in html and 'id="case-form"' in html
                and 'id="workspace"' in html, "Page is missing the desired workbench")
        references = page_assets(html)
        for relative, asset_path in references:
            asset = fetch(base, asset_path)[0]
            require(hashlib.sha256(asset).hexdigest() == sources[relative],
                    f"Wrong asset: {relative}")
        report["checks"].append({"name": "page_and_assets", "verdict": "PASS",
                                 "references": dict(references)})
        # A new pressure for every observation makes an old canned result detectable.
        pressure = 0.5 + secrets.randbelow(500_000) / 1_000_000
        for geometry, height in (("circle", 4.0), ("rectangle", 2.0)):
            config = {
                "geometry": geometry, "width_mm": 4.0, "height_mm": height,
                "length_mm": 100.0, "pressure_pa": pressure,
                "viscosity_pa_s": 0.001, "density_kg_m3": 998.0, "resolution": 8,
            }
            actual = strict_json(fetch(base, "/api/solve", config)[0])
            result = compare_solution(actual, config, sources, started)
            report["checks"].append({"name": f"real_fem_{geometry}",
                                     "verdict": "PASS", **result})
        require(strict_json(fetch(base, "/api/health")[0]) == health,
                "Runtime changed during public verification")
        require(source_hashes() == sources, "Desired sources changed during verification")
        report["verdict"] = "PASS"
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
    report["duration_s"] = round(time.time() - started, 3)
    return report


def write_evidence(report: dict, directory: Path) -> Path:
    destination = directory.resolve()
    require(ARTIFACT_ROOT.resolve() in destination.parents,
            "Evidence must be under the OpenSim external artifact root")
    destination.mkdir(parents=True, exist_ok=True)
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex
    path = destination / (name + ".json")
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    with (destination / (name + ".log")).open("x", encoding="utf-8") as stream:
        stream.write(verdict_line(report, path) + "\n")
    return path


def verdict_line(report: dict, path: Path) -> str:
    failure = report.get("failure", {}).get("type", "none")
    return (f"OPENSIM_PUBLIC_GATE verdict={report['verdict']} scope={report['scope']} "
            f"collected_at={report['collected_at']} checks={len(report['checks'])}/4 "
            f"failure={failure} report={path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ARTIFACT_ROOT / "public-gate")
    args = parser.parse_args()
    report = check_endpoint()
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True, check=True)
    report["desired_git_head"] = result.stdout.strip()
    path = write_evidence(report, args.out_dir)
    print(verdict_line(report, path))
    if report["verdict"] != "PASS":
        print("First cause: " + report["failure"]["reason"])
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
