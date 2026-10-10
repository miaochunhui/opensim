"""Run local FEM, HTTP and generated-page acceptance, with external evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def execute(argv: list[str], env: dict[str, str]) -> dict:
    result = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, text=True)
    return {"argv": argv, "rc": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    destination = args.out.resolve()
    external = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1").resolve()
    if external not in destination.parents:
        parser.error("acceptance output must be under the OpenSim external artifact root")
    destination.mkdir(parents=True, exist_ok=True)
    if (destination / "acceptance.json").exists():
        parser.error("use a new output directory; previous acceptance evidence is preserved")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1")
    cases = [
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-v"],
        ["node", "--check", "web/app.js"],
        [sys.executable, "-B", "tools/validate_numerics.py",
         "--out", str(destination / "numerics.json"), "--solver-root", str(ROOT)],
        [sys.executable, "-B", "tools/render_page.py",
         "--out", str(destination / "gen"), "--evidence", str(destination / "numerics.json"),
         "--selftest"],
    ]
    steps = []
    for argv in cases:
        result = execute(argv, env)
        steps.append(result)
        print(f"ACCEPTANCE_STEP name={Path(argv[0]).name} rc={result['rc']}")
        if result["rc"] != 0:
            break
    if len(steps) == len(cases) and all(step["rc"] == 0 for step in steps):
        generated = execute(cases[-1], env)
        steps.append(generated)
        print(f"ACCEPTANCE_STEP name=repeat-page rc={generated['rc']}")
    hashes = {}
    for relative in ["solver.py", "server.py", "web/app.js", "web/app.css",
                     "tools/render_page.py", "tools/validate_numerics.py"]:
        file = ROOT / relative
        if not file.is_file():
            raise FileNotFoundError(f"Required source unavailable: {file}")
        hashes[relative] = hashlib.sha256(file.read_bytes()).hexdigest()
    passed = len(steps) == len(cases) + 1 and all(step["rc"] == 0 for step in steps)
    report = {
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdict": "PASS" if passed else "FAIL", "steps": steps, "source_sha256": hashes,
        "scope": "local numeric, HTTP, JavaScript syntax and page generation only",
        "unverified": ["public DNS/TLS deployment", "real touch device", "screen-reader combinations"],
    }
    (destination / "acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
    )
    line = (f"OPENSIM_LOCAL_ACCEPTANCE verdict={report['verdict']} "
            f"steps={len(steps)}/{len(cases) + 1} collected_at={report['collected_at']} "
            f"report={destination / 'acceptance.json'}")
    (destination / "acceptance.log").write_text(
        "\n".join(step["stdout"] + step["stderr"] for step in steps) + "\n" + line + "\n",
        encoding="utf-8",
    )
    print(line)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
