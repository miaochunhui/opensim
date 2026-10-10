"""One read-only public observation plus durable fault/recovery notification.

Designed for an OpenSim-only systemd timer; never restarts, publishes, modifies
DNS, or retries a failed deployment. A persistent local flock covers probe,
notification, and state so two invocations cannot duplicate transition mail.
Every probe gets a new external JSON/log. Only accepted notifications advance
the notification state; a failed delivery remains visible for the next tick.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import stat
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

from public_gate import ARTIFACT_ROOT, NoRedirect, check_endpoint, verdict_line, write_evidence

STATE_ROOT = Path("/var/lib/opensim-public-watch")
TOKEN_FILE = Path("/opt/notify/relay.token")
RELAY = "http://192.168.1.183:9525/notify"


def notification_for(previous: dict | None, report: dict, path: Path) -> tuple[str, str] | None:
    desired = report["verdict"]
    if desired not in ("PASS", "FAIL") or report["scope"] != "PUBLIC_CODE_AND_FEM":
        raise ValueError("Only a real public terminal observation can change watch state")
    old = previous["notified_verdict"] if previous else None
    if old == desired:
        return None
    if desired == "FAIL":
        title = "OpenSim 公网同步故障"
        detail = report["failure"]["reason"]
    elif old == "FAIL":
        title = "OpenSim 公网同步恢复"
        detail = "已回读同版本页面、资源并完成圆管和矩形管真实有限元计算。"
    else:
        title = "OpenSim 公网同步巡检首次通过"
        detail = "同版本与真实计算通过；这不是完整发布门认证。"
    body = (f"时间：{report['collected_at']}\n地址：{report['url']}\n"
            f"结果：{desired}\n说明：{detail}\n证据：{path}")
    return title, body


def relay_ack(status: int, url: str, body: bytes) -> str:
    if status != 200 or url != RELAY:
        raise RuntimeError("Notification relay did not acknowledge delivery")
    if body == b"sent":
        return "RELAY_SENT"
    if body == b"spooled\n":
        return "QUEUED_NOT_DELIVERY_VERIFIED"
    raise RuntimeError("Notification relay returned an unknown acknowledgement")


def send_notice(title: str, body: str) -> str:
    descriptor = os.open(TOKEN_FILE, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        mode = os.fstat(stream.fileno()).st_mode
        if not stat.S_ISREG(mode) or mode & 0o007:
            raise ValueError("Notification token must be a non-world-accessible regular file")
        token = stream.read(4097).strip()
    if not token or len(token) > 4096:
        raise ValueError("Invalid notification token size")
    request = Request(
        RELAY, data=json.dumps({"title": title, "body": body}).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Token": token, "X-W25-Token": token},
    )
    # Never log the request/headers/token. Queue acceptance is not human delivery.
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=15) as response:
        return relay_ack(response.status, response.geturl(), response.read(1025))


def read_state(path: Path) -> dict | None:
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or state.get("schema") != 1
            or state.get("notified_verdict") not in ("PASS", "FAIL")
            or not isinstance(state.get("report"), str)):
        raise ValueError("Invalid watch state; refusing to reset notification history")
    return state


def save_state(path: Path, report: dict, evidence: Path, acknowledgement: str) -> None:
    temporary = path.with_suffix(".pending")
    value = {"schema": 1, "notified_verdict": report["verdict"],
             "collected_at": report["collected_at"], "report": str(evidence),
             "notification_ack": acknowledgement}
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ARTIFACT_ROOT / "public-watch")
    args = parser.parse_args()
    if not STATE_ROOT.is_dir():
        raise FileNotFoundError("Managed watch state directory is missing")
    with (STATE_ROOT / "watch.lock").open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("OPENSIM_PUBLIC_WATCH verdict=BUSY observation=NOT_ATTEMPTED")
            return 3
        state_path = STATE_ROOT / "state.json"
        previous = read_state(state_path)
        report = check_endpoint()
        evidence = write_evidence(report, args.out_dir)
        print(verdict_line(report, evidence), flush=True)
        notice = notification_for(previous, report, evidence)
        acknowledgement = previous.get("notification_ack", "UNKNOWN") if previous else "UNKNOWN"
        if notice:
            try:
                acknowledgement = send_notice(*notice)
            except Exception as exc:
                # Keep the failed observation and notification failure separate.
                notice_report = {
                    "schema": 1, "scope": "NOTIFICATION", "verdict": "FAIL",
                    "collected_at": report["collected_at"], "checks": [],
                    "failure": {"type": type(exc).__name__, "reason": str(exc)},
                    "observation_report": str(evidence),
                }
                notice_path = write_evidence(notice_report, args.out_dir)
                print(f"OPENSIM_PUBLIC_WATCH notification=FAIL report={notice_path}", flush=True)
                return 2
            print(f"OPENSIM_PUBLIC_WATCH notification={acknowledgement} state={report['verdict']}", flush=True)
        save_state(state_path, report, evidence, acknowledgement)
        return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
