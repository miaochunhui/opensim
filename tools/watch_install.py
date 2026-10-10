"""Install/revert only the OpenSim public observer, with exact-byte drift gates.

The observer runs as infra and has no production mutation privileges. This
installer needs the established root identity chain, but never edits edge,
DNS, TLS, application service or credentials. It refuses pre-existing units
on first install. Revert is permitted only while live bytes are this install's
bytes, and removes only the two newly installed unit paths. It preserves state
and all evidence. Selftest checks real filesystem writes and byte drift.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import grp
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("opensim-public-watch.service", "opensim-public-watch.timer")
LIVE = Path("/etc/systemd/system")
RECEIPT = Path("/var/lib/opensim-public-watch-install/receipt.json")
ARTIFACTS = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1")
COMMANDS: list[dict] = []


def run(*argv: str) -> str:
    result = subprocess.run(list(argv), capture_output=True, text=True)
    COMMANDS.append({"argv": list(argv), "rc": result.returncode,
                     "stdout": result.stdout, "stderr": result.stderr})
    if result.returncode:
        raise RuntimeError(f"{' '.join(argv)} rc={result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def hashes(contents: dict[str, bytes]) -> dict[str, str]:
    return {name: hashlib.sha256(body).hexdigest() for name, body in contents.items()}


def install_files(live: Path, contents: dict[str, bytes]) -> dict:
    if set(contents) != set(NAMES):
        raise ValueError("Unit set must be exactly the two OpenSim observer units")
    if any((live / name).exists() or (live / name).is_symlink() for name in NAMES):
        raise FileExistsError("Existing OpenSim observer units require inspection, not overwrite")
    receipt = {"schema": 1, "installed_sha256": hashes(contents), "state": "INSTALLING"}
    for name, body in contents.items():
        target = live / name
        with target.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        target.chmod(0o644)
        if target.read_bytes() != body:
            raise RuntimeError(f"Installed bytes differ: {target}")
    receipt["state"] = "FILES_INSTALLED"
    return receipt


def validate_live(live: Path, receipt: dict) -> None:
    if (receipt.get("schema") != 1 or set(receipt.get("installed_sha256", {})) != set(NAMES)):
        raise ValueError("Invalid observer install receipt")
    for name, expected in receipt["installed_sha256"].items():
        target = live / name
        if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Live unit drift: {target}; refusing to change it")


def revert_files(live: Path, receipt: dict) -> None:
    validate_live(live, receipt)
    for name in NAMES:
        (live / name).unlink()
    if any((live / name).exists() for name in NAMES):
        raise RuntimeError("Observer unit removal verification failed")


def external_directory(path: Path) -> Path:
    path = path.resolve()
    if ARTIFACTS.resolve() not in path.parents:
        raise ValueError("Output must be a new directory under OpenSim external evidence")
    path.mkdir(parents=True, exist_ok=False)
    return path


def selftest(out: Path) -> dict:
    live = out / "fixture-systemd"
    live.mkdir()
    content = {name: (ROOT / "deploy" / name).read_bytes() for name in NAMES}
    sentinel = live / "unrelated.service"
    sentinel.write_bytes(b"UNRELATED-ASSET\n")
    receipt = install_files(live, content)
    validate_live(live, receipt)
    rejected_existing = rejected_drift = False
    try:
        install_files(live, content)
    except FileExistsError:
        rejected_existing = True
    (live / NAMES[0]).write_bytes(b"OTHER-SESSION-CHANGE\n")
    try:
        revert_files(live, receipt)
    except RuntimeError:
        rejected_drift = True
    if not rejected_existing or not rejected_drift:
        raise AssertionError("Negative controls did not reject")
    if (live / NAMES[0]).read_bytes() != b"OTHER-SESSION-CHANGE\n":
        raise AssertionError("Drift control overwrote another owner")
    (live / NAMES[0]).write_bytes(content[NAMES[0]])
    revert_files(live, receipt)
    if sentinel.read_bytes() != b"UNRELATED-ASSET\n":
        raise AssertionError("Revert changed unrelated unit")
    return {"verdict": "PASS", "checks": 5, "scope": "FILESYSTEM_ONLY",
            "existing_rejected": rejected_existing, "drift_rejected": rejected_drift,
            "revert_exact_absence": True, "unrelated_preserved": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("selftest", "apply", "revert", "status"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = external_directory(args.out)
    report = {"collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "action": args.action, "verdict": "FAIL"}
    try:
        if args.action == "selftest":
            report.update(selftest(out))
        else:
            if os.geteuid() != 0:
                raise PermissionError("Managed install actions require established root identity chain")
            if args.action == "apply":
                if RECEIPT.exists():
                    raise FileExistsError("Install receipt already exists; use status, not overwrite")
                content = {name: (ROOT / "deploy" / name).read_bytes() for name in NAMES}
                run("systemd-analyze", "verify", *(str(ROOT / "deploy" / name) for name in NAMES))
                active = run("systemctl", "show", NAMES[1], "--property=LoadState", "--value")
                if active != "not-found":
                    raise RuntimeError(f"Pre-existing observer timer LoadState={active}")
                info = pwd.getpwnam("infra")
                group = grp.getgrnam("infra")
                watch_dir = ARTIFACTS / "public-watch"
                if not watch_dir.exists():
                    watch_dir.mkdir()
                    os.chown(watch_dir, info.pw_uid, group.gr_gid)
                if watch_dir.stat().st_uid != info.pw_uid:
                    raise PermissionError("Observer evidence directory is not owned by infra")
                # No recursive permission sweep or credential changes.
                RECEIPT.parent.mkdir(mode=0o700, exist_ok=False)
                receipt = {"schema": 1, "state": "PREPARED",
                           "installed_sha256": hashes(content)}
                RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
                receipt = install_files(LIVE, content)
                RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
                run("systemctl", "daemon-reload")
                run("systemctl", "enable", "--now", NAMES[1])
                validate_live(LIVE, receipt)
                if run("systemctl", "is-enabled", NAMES[1]) != "enabled":
                    raise RuntimeError("Observer timer is not enabled")
                if run("systemctl", "is-active", NAMES[1]) != "active":
                    raise RuntimeError("Observer timer is not active")
                receipt["state"] = "TIMER_ACTIVE"
                RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
                report.update({"verdict": "PASS", "timer": "enabled/active",
                               "receipt": str(RECEIPT), "installed_sha256": hashes(content),
                               "observation": "NOT_YET_VERIFIED"})
            else:
                receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
                validate_live(LIVE, receipt)
                if args.action == "revert":
                    run("systemctl", "disable", "--now", NAMES[1])
                    # May be failed from a genuine DNS failure; stop only this exact unit.
                    run("systemctl", "stop", NAMES[0])
                    revert_files(LIVE, receipt)
                    run("systemctl", "daemon-reload")
                    receipt["state"] = "REVERTED"
                    RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
                    report.update({"verdict": "PASS", "state": "REVERTED"})
                else:
                    report.update({"verdict": "PASS", "receipt": receipt,
                                   "timer_active": run("systemctl", "is-active", NAMES[1]),
                                   "timer_enabled": run("systemctl", "is-enabled", NAMES[1])})
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
    report["commands"] = COMMANDS
    path = out / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    line = (f"OPENSIM_WATCH_INSTALL verdict={report['verdict']} action={args.action} "
            f"collected_at={report['collected_at']} report={path}")
    (out / "execution.log").write_text(line + "\n" + report.get("failure", {}).get("reason", "") + "\n",
                                     encoding="utf-8")
    print(line)
    if report["verdict"] != "PASS":
        print(report["failure"]["reason"])
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
