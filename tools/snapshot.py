"""Capture local source/evidence and public reachability without changing services."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.render_page import _source_metadata

DOMAIN = "opensim.ai4everyone.top"


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def read_report(file: Path, kind: str) -> dict:
    file = file.resolve(strict=True)
    content = file.read_bytes()
    report = json.loads(content)
    if not isinstance(report, dict) or not isinstance(report.get("verdict"), str):
        raise ValueError(f"{kind}: missing report verdict: {file}")
    verdict = report["verdict"]
    if kind == "browser" and report.get("mode") != "OPENSIM_ACCEPTANCE":
        raise ValueError("browser snapshot requires the full OpenSim run, not a fixture or probe")
    if verdict in ("PASS", "AUTOMATED_PASS"):
        if kind == "numerics":
            expected = hashlib.sha256((ROOT / "solver.py").read_bytes()).hexdigest()
            if report.get("state") != "COMPLETE" or report.get("solver", {}).get("sha256") != expected:
                raise ValueError("passing numerics are incomplete or do not match the current solver")
        elif kind == "local":
            sources = report.get("source_sha256")
            if not isinstance(sources, dict) or not sources:
                raise ValueError("local report has no source identities")
            for relative, expected in sources.items():
                source = (ROOT / relative).resolve()
                if ROOT not in source.parents:
                    raise ValueError(f"local source outside repository: {relative}")
                if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                    raise ValueError(f"local report is stale: {relative}")
        elif kind == "browser":
            if _source_metadata()[2] not in report.get("page_build", {}).get("build_text", ""):
                raise ValueError("browser report does not match the current page build")
            expected = hashlib.sha256((ROOT / "tools/browser_check.mjs").read_bytes()).hexdigest()
            if report.get("tool_sha256") != expected:
                raise ValueError("browser report does not match the current browser tool")
    return {
        "path": str(file), "sha256": hashlib.sha256(content).hexdigest(),
        "verdict": verdict,
        "collected_at": report.get("collected_at") or report.get("finished_at"),
        "release_verdict": report.get("release_verdict"),
    }


def health_at(url: str) -> dict:
    try:
        with urlopen(url, timeout=15) as response:
            body = json.loads(response.read(8192))
            valid = (response.status == 200 and isinstance(body, dict)
                     and body.get("status") == "ok" and body.get("schema_version") == 1)
            return {"status": "PASS" if valid else "FAILED",
                    "http_status": response.status, "body": body}
    except (URLError, ValueError, TimeoutError, OSError) as exc:
        return {"status": "FAILED", "error_type": type(exc).__name__, "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--numerics", type=Path, required=True)
    parser.add_argument("--local", type=Path, required=True)
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--local-url", default="http://127.0.0.1:8772")
    parser.add_argument("--production-changes", choices=("not-assessed", "none", "applied"),
                        default="not-assessed",
                        help="Operator declaration, not automatically verified deployment evidence")
    args = parser.parse_args()
    output = args.out.resolve()
    external = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1").resolve()
    if external not in output.parents:
        parser.error("snapshot output must be under the OpenSim external artifact root")
    if output.exists():
        parser.error("use a new snapshot path; previous evidence is preserved")
    evidence = {kind: read_report(getattr(args, kind), kind)
                for kind in ("numerics", "local", "browser")}
    try:
        addresses = sorted({entry[4][0] for entry in socket.getaddrinfo(DOMAIN, 443)})
        dns = {"status": "resolved", "addresses": addresses}
    except socket.gaierror as exc:
        dns = {"status": "unresolved", "error": str(exc)}
    if dns["status"] == "resolved":
        health = health_at(f"https://{DOMAIN}/api/health")
    else:
        health = {"status": "NOT_ATTEMPTED", "reason": "DNS_UNRESOLVED"}
    report = {
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "head": git("rev-parse", "HEAD"), "branch": git("branch", "--show-current"),
        "git_status": git("status", "--short"), "evidence": evidence,
        "public_dns": dns, "public_health": health,
        "local_health": health_at(args.local_url.rstrip("/") + "/api/health"),
        "production_changes": {"operator_declaration": args.production_changes,
                               "verification": "NOT_AUTOMATICALLY_VERIFIED"},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
    print(f"OPENSIM_SNAPSHOT collected_at={report['collected_at']} "
          f"head={report['head']} dns={dns['status']} health={health['status']} out={output}")
    # A snapshot succeeds as an observation, not as a claim of public deployment.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
