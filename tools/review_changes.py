"""Review only this task's explicit files and credential shapes before commit.

Reads the existing relay token in memory to detect accidental exact leakage.
Never prints values or matching lines. No unreadable or empty file can PASS.
Also preserves a compact test/evidence summary outside the worktree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATHS = (
    "AGENTS.md", "README.md", "server.py", "web/app.js", "tools/render_page.py",
    "tools/snapshot.py", "tools/public_gate.py", "tools/public_watch.py",
    "tools/watch_install.py", "tools/review_changes.py",
    "tests/test_public_gate.py", "tests/test_public_watch.py",
    "deploy/opensim-public-watch.service", "deploy/opensim-public-watch.timer",
    "docs/CONTRACT.md", "docs/PUBLIC_SYNC.md",
    "docs/handoff/20261010-public-sync.md",
)
SECRET = re.compile(
    rb"(?i)\b(?:password|passwd|api[_-]?key|secret|authorization)"
    rb"[\"']?\s*[:=]\s*[\"'][^\"']{6,}[\"']"
)
PEM = re.compile(rb"-----BEGIN " + rb"(?:[A-Z]+ )?PRIVATE KEY-----")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    if Path("/hf3fs-jd/data/tmp/opensim-fluid-v1").resolve() not in out.parents or out.exists():
        parser.error("Use a new OpenSim external report path")
    token = Path("/opt/notify/relay.token").read_bytes().strip()
    if len(token) < 16:
        raise ValueError("Invalid existing token; exact credential scan unavailable")
    rows = []
    # Exact-token positive/negative controls do not write a credential fixture.
    if token not in b"prefix" + token + b"suffix" or token in b"no-credential-here":
        raise AssertionError("Exact credential scanner selftest failed")
    if not PEM.search(b"-----BEGIN " + b"PRIVATE KEY-----") or PEM.search(b"public data"):
        raise AssertionError("Private-key scanner selftest failed")
    for relative in PATHS:
        content = (ROOT / relative).read_bytes()
        rows.append({
            "path": relative, "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "exact_credential_hits": content.count(token),
            "shape_hits": len(SECRET.findall(content)),
            "pem_hits": len(PEM.findall(content)),
        })
    diff = subprocess.run(["git", "diff", "--check"], cwd=ROOT,
                          capture_output=True, text=True, check=True)
    passed = all(row["bytes"] > 0 and not any(row[key] for key in
                  ("exact_credential_hits", "shape_hits", "pem_hits")) for row in rows)
    report = {"collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "verdict": "PASS" if passed else "FAIL",
              "files": rows, "selftest": "PASS", "diff_check": diff.returncode}
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2) + "\n")
    print(f"OPENSIM_CHANGE_REVIEW verdict={report['verdict']} files={len(rows)} "
          f"collected_at={report['collected_at']} report={out}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
