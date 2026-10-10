"""Fail-closed shared-r41 evidence gate. This tool never deploys or approves.

The ledger pins actual external reports; it is not a replacement for running
their producers. Editable user-approval JSON alone never authorizes an exception.
Filesystem test fixtures prove gate behaviour, never real release acceptance.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1")
OUTPUT_ROOT = ARTIFACT_ROOT / "seat-sync"
PUBLIC_URL = "https://opensim.ai4everyone.top"
SOURCES = ("tools/render_page.py", "web/app.js", "web/app.css", "solver.py", "server.py")
TOOLS = (
    "tools/acceptance.py", "tools/validate_numerics.py", "tools/render_page.py",
    "tools/browser_check.mjs", "tools/browser_audit.mjs",
    "tools/browser_performance.mjs", "tools/public_gate.py",
    "tools/release_app.py", "tools/release_gate.py", "tools/sync_release.py",
)
GATES = ("G1-structure", "G2-layout", "G3-accessibility", "G4-keyboard",
         "G5-performance", "G6-content")
MANUAL = ("PHYSICAL-TOUCH", "SOFT-KEYBOARD", "SCREEN-READER-DESKTOP",
          "SCREEN-READER-MOBILE", "FAULT-ISOLATION", "SCREENSHOT-REVIEW")
REQUIRED_RULES = {
    "G1-structure": {"WEB-BASE-001", "WEB-BASE-002", "WEB-BASE-003", "WEB-BASE-005",
                     "WEB-ADAPT-001", "WEB-ADAPT-002", "WEB-NAV-001", "WEB-NAV-002",
                     "WEB-INPUT-003", "WEB-A11Y-006", "WEB-A11Y-007"},
    "G2-layout": {"WEB-BASE-004", "WEB-RWD-001", "WEB-RWD-002", "WEB-RWD-003",
                  "WEB-RWD-005", "WEB-ADAPT-003", "WEB-ADAPT-004", "WEB-ADAPT-005",
                  "WEB-ADAPT-006", "WEB-TYPE-001", "WEB-TYPE-002", "WEB-TYPE-003",
                  "WEB-TYPE-004", "WEB-A11Y-004"},
    "G3-accessibility": {"WEB-A11Y-001", "WEB-A11Y-002", "WEB-A11Y-005",
                         "WEB-A11Y-006", "WEB-A11Y-007", "WEB-A11Y-008", "WEB-INPUT-001",
                         "WEB-INPUT-002", "WEB-INPUT-003", "WEB-INPUT-004", "WEB-INPUT-005"},
    "G4-keyboard": {"WEB-A11Y-003", "WEB-A11Y-004", "WEB-A11Y-008", "WEB-NAV-004",
                    "WEB-NAV-005", "WEB-INPUT-006", "WEB-MOTION-001", "WEB-MOTION-002",
                    "WEB-MOTION-003"},
    "G5-performance": {f"WEB-PERF-{i:03}" for i in range(1, 7)},
    "G6-content": {"WEB-TYPE-005", "WEB-TYPE-006", "WEB-CONTENT-001", "WEB-CONTENT-002",
                   "WEB-CONTENT-003", "WEB-CONTENT-004", "WEB-CONTENT-005",
                   "WEB-CONTENT-006", "WEB-CONTENT-007", "WEB-INPUT-004",
                   "WEB-INPUT-005", "WEB-ADAPT-002"},
}
VIEWPORTS = ((320, 568), (360, 800), (390, 844), (768, 1024),
             (1024, 768), (1064, 768), (1280, 800), (1440, 900))
LIMIT = 16_000_000


class GateError(RuntimeError):
    pass


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise GateError(reason)


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def encode(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("ascii")


def decode(body: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def nonfinite(value):
        raise GateError(f"Nonfinite JSON: {value}")

    value = json.loads(body, object_pairs_hook=unique, parse_constant=nonfinite)
    require(isinstance(value, dict) and bool(value), "Empty/non-object evidence")
    encode(value)
    return value


def external(path: Path, base: Path = ARTIFACT_ROOT) -> Path:
    require(path.is_absolute() and ".." not in path.parts, "Absolute external path required")
    entry = base if path.is_relative_to(base) else base.resolve()
    require(path.is_relative_to(entry) and path != entry, f"Path outside evidence root: {path}")
    # The host's mount alias is trusted, but no descendant symlink is.
    cursor = entry
    for part in path.relative_to(entry).parts:
        cursor /= part
        require(not cursor.is_symlink(), f"Evidence symlink rejected: {cursor}")
    require(path.resolve().is_relative_to(base.resolve()), "Evidence escaped physical root")
    return path


def read_file(path: Path) -> bytes:
    require(path.is_absolute(), "Absolute file path required")
    require(not path.is_symlink(), f"Symlink file rejected: {path}")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                f"Nonregular/hardlinked evidence: {path}")
        body = stream.read(LIMIT + 1)
    require(0 < len(body) <= LIMIT, f"Empty/oversized evidence: {path}")
    return body


def write_new(path: Path, body: bytes) -> None:
    external(path, OUTPUT_ROOT)
    require(bool(body), "Empty output rejected")
    path.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                           0o644), "wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    require(read_file(path) == body, f"Output readback differs: {path}")


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["/usr/bin/git", "-c", "core.fsmonitor=false", *args], cwd=root,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "GIT_OPTIONAL_LOCKS": "0"},
        capture_output=True,
    )
    require(result.returncode == 0,
            f"git rc={result.returncode} args={args!r}: {result.stderr.decode(errors='replace')}")
    return result.stdout


def relative(name: str) -> str:
    require(isinstance(name, str) and bool(name), "Empty relative file name")
    path = PurePosixPath(name)
    require(not path.is_absolute() and ".." not in path.parts and str(path) == name,
            f"Unsafe relative file name: {name}")
    return name


def committed(root: Path, commit: str, name: str) -> bytes:
    relative(name)
    record = git(root, "ls-tree", commit, "--", name).decode().split()
    require(len(record) == 4 and record[0:2] == ["100644", "blob"],
            f"Required committed regular file: {name}")
    cursor = root
    for part in PurePosixPath(name).parts:
        cursor /= part
        require(not cursor.is_symlink(), f"Source symlink rejected: {cursor}")
    body = git(root, "cat-file", "blob", record[2])
    require(read_file(cursor) == body, f"Worktree bytes differ from commit: {name}")
    return body


def identity(root: Path, commit: str) -> dict:
    require(re.fullmatch(r"[a-f0-9]{40}", commit or "") is not None, "Full clean commit required")
    require(root.is_absolute() and root.is_dir() and not root.is_symlink(), "Invalid source root")
    require(Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() == root.resolve(),
            "Source root must be checkout root")
    require(git(root, "rev-parse", "HEAD").decode().strip() == commit, "HEAD/commit mismatch")
    require(not git(root, "status", "--porcelain=v1", "--untracked-files=all",
                    "--ignored=matching").strip(),
            "Dirty checkout (including untracked files)")
    require(all(row.startswith(b"H ") for row in git(root, "ls-files", "-v", "-z").split(b"\0") if row),
            "Skip-worktree/assume-unchanged files cannot prove clean commit")
    bodies = {name: committed(root, commit, name) for name in dict.fromkeys((*SOURCES, *TOOLS))}
    fingerprint = hashlib.sha256()
    for name in SOURCES:
        fingerprint.update(name.encode("ascii") + b"\0" + bodies[name] + b"\0")
    return {"commit": commit, "source_sha256": {name: sha(bodies[name]) for name in SOURCES},
            "page_build": fingerprint.hexdigest()[:12],
            "tool_sha256": {name: sha(bodies[name]) for name in TOOLS}}


def time_value(value: str) -> datetime:
    require(isinstance(value, str), "Timezone timestamp required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "Naive timestamp rejected")
    return result


def fresh(report: dict, now: datetime, seconds: int = 86400) -> None:
    collected = report.get("finished_at") or report.get("collected_at")
    age = (now - time_value(collected)).total_seconds()
    require(0 <= age <= seconds, f"Stale/future evidence: {collected}")


def reference(record: dict) -> tuple[dict, Path]:
    require(isinstance(record, dict) and set(record) == {"path", "sha256"},
            "Evidence reference requires exactly path/sha256")
    require(re.fullmatch(r"[0-9a-f]{64}", record["sha256"] or "") is not None,
            "Full evidence SHA256 required")
    path = external(Path(record["path"]))
    body = read_file(path)
    require(sha(body) == record["sha256"], f"Evidence hash drift: {path}")
    return decode(body), path


def artifact(record: dict) -> tuple[bytes, Path]:
    require(isinstance(record, dict) and set(record) == {"path", "sha256"},
            "Actual artifact requires path/sha256")
    path = external(Path(record["path"]))
    body = read_file(path)
    require(re.fullmatch(r"[a-f0-9]{64}", record["sha256"] or "") and
            sha(body) == record["sha256"], f"Artifact hash drift: {path}")
    return body, path


def log_artifact(record: dict) -> None:
    require(isinstance(record, dict) and set(record) == {"path", "sha256"},
            "Actual launcher log requires path/sha256")
    path = external(Path(record["path"]))
    require(path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1,
            "Launcher log must be a real regular file")
    # Empty stderr is legitimate; its actual file and hash remain mandatory.
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Unsafe launcher log")
        body = stream.read(LIMIT + 1)
    require(len(body) <= LIMIT and sha(body) == record["sha256"], "Launcher log hash drift")


def check_rows(rows: object, label: str) -> list[dict]:
    require(isinstance(rows, list) and bool(rows), f"Empty checks: {label}")
    require(all(isinstance(row, dict) and
                (row.get("status") or row.get("verdict")) == "PASS" for row in rows),
            f"Incomplete/failed checks: {label}")
    return rows


def screenshots(report: dict, path: Path) -> None:
    rows = report.get("screenshots")
    require(isinstance(rows, list) and bool(rows), "Missing actual screenshot evidence")
    for row in rows:
        require(isinstance(row, dict) and re.fullmatch(r"[a-f0-9]{64}", row.get("sha256", "")),
                "Missing screenshot hash")
        name = relative(row.get("file", ""))
        body = read_file(external(path.parent / name))
        require(sha(body) == row["sha256"] and body.startswith(b"\x89PNG\r\n\x1a\n"),
                f"Missing/corrupt PNG: {name}")
        require(len(body) > 32 and body[12:16] == b"IHDR" and
                int.from_bytes(body[16:20], "big") > 0 and
                int.from_bytes(body[20:24], "big") > 0 and b"IEND" in body[-12:],
                f"Empty/truncated screenshot PNG: {name}")


def binding(report: dict, expected: dict, tool: str, now: datetime) -> None:
    require(report.get("commit") == expected["commit"], f"Report commit mismatch: {tool}")
    require(report.get("tool_sha256") == expected["tool_sha256"][tool],
            f"Old/unknown tool hash: {tool}")
    sources = report.get("source_sha256")
    # Native performance producer binds health, committed source and solve separately.
    if tool == "tools/browser_performance.mjs":
        sample = report.get("samples", [{}])[0]
        provenance = sample.get("source_identity", {})
        require(provenance.get("bound") is True and provenance.get("assets_bound") is True,
                "Performance identity not bound")
        for key in ("local_source_sha256", "committed_source_sha256", "solve_source_sha256"):
            require(provenance.get(key) == expected["source_sha256"], f"Performance {key} differs")
        health = provenance.get("health", {})
        sources = health.get("source_sha256")
        page = health.get("page_build")
    elif tool == "tools/browser_check.mjs":
        page_record = report.get("page_build", {})
        require(isinstance(page_record, dict), "Browser page_build must contain DOM observation")
        text = page_record.get("build_text", "")
        require(isinstance(text, str) and
                re.search(rf"(?<![a-f0-9]){expected['page_build']}(?![a-f0-9])", text),
                "Browser DOM build_text differs from five-source byte build")
        require(time_value(page_record.get("edited_at")).utcoffset().total_seconds() == 28800,
                "Browser edited_at must preserve precise +08:00 source timestamp")
        if sources is None:
            # Native browser_check records the five-source fingerprint in the DOM.
            sources = expected["source_sha256"]
        page = expected["page_build"]
    elif tool == "tools/browser_audit.mjs":
        # Native partial audit has no source map/page DOM field. The mandatory
        # launcher pins its whole checkout and bytes; coverage still stays PARTIAL.
        if sources is None:
            sources = expected["source_sha256"]
        page = report.get("page_build", expected["page_build"])
    else:
        page = report.get("page_build")
    require(sources == expected["source_sha256"], f"Report must bind all five sources: {tool}")
    require(page == expected["page_build"], f"Report page_build mismatch: {tool}")
    fresh(report, now)


def approvals(root: Path, commit: str, record: dict | None, now: datetime) -> dict:
    if record is None:
        return {}
    require(isinstance(record, dict) and set(record) == {"path", "sha256"},
            "Approval registry must be explicitly pinned")
    name = relative(record["path"])
    body = committed(root, commit, name)
    require(sha(body) == record["sha256"], "Approval registry hash mismatch")
    value = decode(body)
    require(value.get("schema") == "opensim-user-approvals-v1" and
            isinstance(value.get("records"), list), "Unknown approval registry")
    result = {}
    for item in value["records"]:
        fields = {"id", "rule_id", "owner", "expires_at", "reason", "scope",
                  "removal_condition", "status", "approved_by", "approved_at", "approval_record_id"}
        require(isinstance(item, dict) and set(item) == fields, "Incomplete user approval")
        require(all(isinstance(item[key], str) and item[key].strip() for key in fields),
                "Empty approval field")
        require(item["id"] not in result and item["rule_id"] in {*GATES, *MANUAL},
                "Duplicate/unknown exception ID or rule")
        require(item["status"] == "APPROVED" and item["approved_by"] == "user",
                "Exception not explicitly user-approved")
        require(time_value(item["approved_at"]) <= now < time_value(item["expires_at"]),
                f"Expired/future exception: {item['id']}")
        require(item["scope"] == PUBLIC_URL, "Exception scope is not exact OpenSim release target")
        result[item["id"]] = item
    return result


def execution(pin: dict, report_pin: dict, expected: dict, tool: str, now: datetime) -> dict:
    value, _ = reference(pin)
    require(set(value) == {"schema", "identity", "tool", "argv", "rc", "report",
                           "stdout", "stderr", "collected_at"},
            "Missing launcher execution receipt fields")
    require(value["schema"] == "opensim-producer-execution-v1" and
            value["identity"] == expected and value["tool"] == tool and
            value["report"] == report_pin and type(value["rc"]) is int and value["rc"] == 0,
            "Launcher does not bind producer rc/report/clean identity")
    argv = value["argv"]
    require(isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv) and
            any(a.endswith("/" + tool) or a == tool for a in argv),
            "Launcher argv does not identify required producer")
    log_artifact(value["stdout"])
    log_artifact(value["stderr"])
    fresh(value, now)
    return value


def performance(report: dict) -> None:
    require(report.get("scope") == "PUBLIC_LAB" and report.get("verdict") == "PUBLIC_G5_PASS"
            and report.get("g5_certified") is True and
            report.get("target_url", "").rstrip("/") == PUBLIC_URL,
            "G5 requires PUBLIC_LAB; LOCAL_PREFLIGHT is not public PASS")
    samples = report.get("samples")
    require(isinstance(samples, list) and len(samples) == 1, "Exactly one cold public sample required")
    sample = samples[0]
    require(sample.get("target_url", "").rstrip("/") == PUBLIC_URL,
            "Loopback/unknown sample cannot be public")
    metrics = sample.get("metrics", {})
    for key, limit in (("lcp_ms", 2500), ("interaction_latency_ms", 200), ("cls", 0.1)):
        value = metrics.get(key)
        require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= limit,
                f"G5 budget/missing measurement: {key}")
    require(metrics.get("lcp_count", 0) > 0 and metrics.get("interaction_count", 0) >= 3 and
            metrics.get("cls_observed") is True, "G5 measurements cannot be defaults")
    rows = check_rows(report.get("checks"), "G5")
    require({f"WEB-PERF-{i:03}" for i in range(1, 7)} <= {r.get("rule") for r in rows},
            "G5 missing WEB-PERF-001..006")
    resources = sample.get("resources")
    require(isinstance(resources, list) and resources and
            all(isinstance(r, dict) and type(r.get("transfer_bytes")) is int and
                r["transfer_bytes"] > 0 and r.get("finished") is True and
                r.get("cached") is False and r.get("status") == 200 for r in resources),
            "G5 missing actual cold transfer evidence")
    require({r.get("type") for r in resources} >= {"Document", "Stylesheet", "Script"},
            "G5 resource set incomplete")
    dns = report.get("public_dns")
    require(isinstance(dns, list) and dns and
            all(isinstance(row, dict) and ipaddress.ip_address(row.get("address", "")).is_global
                for row in dns) and
            all(ipaddress.ip_address(row.get("remote_ip", "")).is_global for row in resources),
            "G5 actual browser connection/DNS not public")
    for types, limit in (({"Document", "Stylesheet", "Script"}, 200000),
                         (None, 1000000), ({"Script"}, 300000), ({"Font"}, 150000)):
        require(sum(r["transfer_bytes"] for r in resources if types is None or r["type"] in types)
                <= limit, "G5 transfer budget exceeded")
    texts = [r for r in resources if re.search(r"html|css|javascript|json|text/", r.get("mime", ""), re.I)]
    require(texts and all(type(r.get("decoded_body_bytes")) in (int, float) and
                         (r["decoded_body_bytes"] <= 512 or r.get("encoding") in {"gzip", "br"})
                         for r in texts), "G5 text resources not actually compressed")
    assets = [r for r in resources if r["type"] in {"Stylesheet", "Script", "Font", "Image"}]
    require(assets, "G5 static resource set empty")
    for row in assets:
        policy = row.get("cache_control", "")
        require(isinstance(policy, str) and not re.search(r"no-store", policy, re.I),
                "G5 static resource no-store")
        age = re.search(r"(?:^|,)\s*max-age=(\d+)", policy, re.I)
        if row["type"] in {"Stylesheet", "Script"} or row.get("fingerprinted"):
            require(row.get("fingerprinted") is True and age and int(age[1]) >= 31536000 and
                    re.search(r"immutable", policy, re.I) and
                    row.get("fingerprint_sha256") == row.get("body_sha256") and
                    re.fullmatch(r"[a-f0-9]{64}", row.get("body_sha256", "")),
                    "G5 fingerprint/cache bytes mismatch")
        else:
            require((row.get("etag") or row.get("last_modified")) and
                    re.search(r"must-revalidate|no-cache", policy, re.I), "G5 asset cache lacks validator")
    fonts = sample.get("fonts", {})
    require(fonts.get("ready") is True and fonts.get("errors") == [] and
            type(fonts.get("preloads")) is int and 0 <= fonts["preloads"] <= 1 and
            fonts.get("fallback_verified") is True and isinstance(fonts.get("faces"), list) and
            all(face.get("display") in {"swap", "optional"} and face.get("subset") is True
                for face in fonts["faces"]), "G5 font/fallback policy missing")
    require(sample.get("third_party") == [] and
            sample.get("isolation") == "BLOCK_ALL_OFF_ORIGIN_BEFORE_REQUEST" and
            sample.get("redirects") == [] and sample.get("network_errors") == [] and
            sample.get("runtime_errors") == [], "G5 isolation/redirect/runtime failure")
    content = sample.get("content", {})
    require(content.get("ready") is True and content.get("text_length", 0) > 100 and
            content.get("canvas_colors", 0) > 3 and content.get("solve_count", 0) >= 2 and
            content.get("nodes", 0) > 10 and content.get("triangles", 0) > 10 and
            content.get("max_velocity", 0) > 0, "G5 real content/compute evidence missing")
    require(isinstance(sample.get("images"), list) and
            all(image.get("width", 0) > 0 and image.get("height", 0) > 0 and
                image.get("intrinsic_width", float("inf")) <= image["width"] * 2 and
                image.get("mime") in {"image/avif", "image/webp", "image/svg+xml"}
                for image in sample["images"]), "G5 mobile image budget unverified")
    baseline = {"id": "opensim-mobile-cold-v1", "width": 360, "height": 800,
                "device_scale_factor": 1, "mobile": True, "touch": True,
                "latency_ms": 150, "download_bytes_s": 200000, "upload_bytes_s": 93750,
                "cpu_rate": 4, "observation_ms": 8000, "cache_disabled": True,
                "service_worker_bypassed": True, "samples": 1, "timeout_ms": 30000, "retries": 0}
    require(report.get("baseline_commands_acknowledged") is True and
            report.get("baseline") == baseline and
            report.get("browser", {}).get("product", "").startswith("Chrome/"),
            "G5 lacks browser/network/CPU baseline")
    raw = sample.get("raw_metrics")
    require(isinstance(raw, dict) and isinstance(raw.get("lcp"), list) and raw["lcp"] and
            isinstance(raw.get("shifts"), list) and "layout-shift" in raw.get("supported", []) and
            raw.get("observer_errors") == [] and
            metrics["lcp_ms"] == raw["lcp"][-1].get("startTime") and
            metrics["lcp_count"] == len(raw["lcp"]), "G5 raw LCP/CLS observations missing/differ")
    maximum, current, start, last = 0.0, 0.0, None, None
    for shift in sorted(raw["shifts"], key=lambda r: r["startTime"]):
        moment, value = shift["startTime"], shift["value"]
        require(type(moment) in (int, float) and math.isfinite(moment) and moment >= 0 and
                type(value) in (int, float) and math.isfinite(value) and value >= 0 and
                type(shift.get("hadRecentInput")) is bool, "G5 invalid raw shift observation")
        if shift["hadRecentInput"]:
            continue
        if start is None or moment - last >= 1000 or moment - start >= 5000:
            current, start = 0.0, moment
        current += value
        last = moment
        maximum = max(maximum, current)
    require(metrics["cls"] == maximum, "G5 CLS summary differs from raw session windows")
    interactions = sample.get("interactions")
    require(sample.get("phase") == "ONE_COLD_NAVIGATION_PLUS_THREE_CORE_TOUCHES" and
            isinstance(interactions, list) and len(interactions) == 3 and
            {row.get("selector") for row in interactions} == {"#view-mesh", "#view-field", "#solve-button"},
            "G5 missing three actual core interactions")
    for row in interactions:
        paints = row.get("paints")
        events = row.get("event_timing")
        require(isinstance(paints, list) and paints and isinstance(events, list) and
                all(p.get("trusted") is True and type(p.get("start_ms")) in (int, float) and
                    type(p.get("end_ms")) in (int, float) and
                    math.isfinite(p["start_ms"]) and math.isfinite(p["end_ms"]) and
                    p["end_ms"] >= p["start_ms"] and
                    p.get("bound_ms") == p["end_ms"] - p["start_ms"] for p in paints) and
                row.get("click_count") == 1 == sum(p.get("type") == "click" for p in paints),
                "G5 missing trusted raw interaction")
        durations = [event.get("duration") for event in events]
        require(all(type(d) in (int, float) and math.isfinite(d) and d >= 0 for d in durations) and
                row.get("latency_ms") == max([p["bound_ms"] for p in paints] + durations),
                "G5 interaction summary differs from raw timings")
    require(metrics["interaction_latency_ms"] == max(row["latency_ms"] for row in interactions),
            "G5 interaction aggregate differs")


def evaluate(root: Path, commit: str, ledger_path: Path,
             approval_pin: dict | None = None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    expected = identity(root, commit)
    raw = read_file(external(ledger_path))
    ledger = decode(raw)
    require(set(ledger) == {"schema", "identity", "evidence", "executions", "gates", "manual", "exceptions"},
            "Unknown/missing release ledger fields")
    require(ledger["schema"] == "opensim-release-evidence-v1", "Unknown release evidence schema")
    require(ledger["identity"] == expected, "Ledger does not bind clean commit/sources/build/tools")
    registry = approvals(root, commit, approval_pin, now)
    used = ledger["exceptions"]
    require(isinstance(used, list) and all(isinstance(item, str) for item in used) and
            len(set(used)) == len(used) and
            all(item in registry for item in used), "Unapproved/unknown exception")
    waived = {registry[item]["rule_id"] for item in used}
    require("G5-performance" not in waived, "Missing public G5 cannot be waived to PASS")
    require(not used, "BLOCKED: no trusted user-approval verifier; editable JSON is not approval")
    evidence = ledger["evidence"]
    require(isinstance(evidence, dict) and set(evidence) ==
            {"local", "numerics", "browser", "audit", "performance", "public", "page"},
            "Required actual local/browser/audit/perf/public evidence missing")
    reports = {kind: reference(record) for kind, record in evidence.items()}
    executions = ledger["executions"]
    producer_tools = {"local": "tools/acceptance.py", "numerics": "tools/validate_numerics.py",
                      "browser": "tools/browser_check.mjs", "audit": "tools/browser_audit.mjs",
                      "performance": "tools/browser_performance.mjs", "public": "tools/public_gate.py",
                      "page": "tools/render_page.py"}
    require(isinstance(executions, dict) and set(executions) == set(producer_tools),
            "Missing required producer launcher receipts")
    for kind, tool in producer_tools.items():
        execution(executions[kind], evidence[kind], expected, tool, now)
    page = reports["page"][0]
    require(set(page) == {"schema", "commit", "source_sha256", "page_build", "html",
                         "page_check", "timestamp_origin", "source_epoch"},
            "Missing actual generated-page binding")
    require(page["schema"] == "opensim-page-evidence-v1" and page["commit"] == commit and
            page["source_sha256"] == expected["source_sha256"] and
            page["page_build"] == expected["page_build"], "Generated page identity mismatch")
    html, _ = artifact(page["html"])
    page_check, _ = reference(page["page_check"])
    require(page_check.get("verdict") == "PASS" and
            page_check.get("html_sha256") == sha(html) and
            page_check.get("source_sha256_prefix") == expected["page_build"] and
            expected["page_build"].encode() in html,
            "Actual generated page/check/source mismatch")
    edited_at = time_value(page_check.get("edited_at"))
    require(edited_at.utcoffset().total_seconds() == 28800 and
            page["timestamp_origin"] in {"SOURCE_MTIME", "COMMIT_EPOCH"},
            "Generated page must declare truthful edit timestamp origin")
    if page["timestamp_origin"] == "COMMIT_EPOCH":
        epoch = int(git(root, "show", "-s", "--format=%ct", commit).strip())
        require(type(page["source_epoch"]) is int and page["source_epoch"] == epoch and
                edited_at.timestamp() == epoch, "Fixed page epoch differs from commit time")
    else:
        require(page["source_epoch"] is None and edited_at.timestamp() ==
                int(max((root / name).stat().st_mtime for name in SOURCES)),
                "Local edited_at must reflect actual source mtime, not deployment time")
    local = reports["local"][0]
    require(local.get("verdict") == "PASS" and local.get("source_sha256") ==
            {**expected["source_sha256"], "tools/validate_numerics.py":
             expected["tool_sha256"]["tools/validate_numerics.py"]}, "Stale/incomplete local evidence")
    fresh(local, now)
    steps = local.get("steps")
    require(isinstance(steps, list) and len(steps) == 5 and
            all(isinstance(s, dict) and type(s.get("rc")) is int and s["rc"] == 0 and
                isinstance(s.get("argv"), list) and s["argv"] and
                isinstance(s.get("stdout"), str) and isinstance(s.get("stderr"), str)
                for s in steps), "Missing complete local subprocess evidence")
    require(any(re.search(r"Ran [1-9][0-9]* tests", s["stdout"] + s["stderr"]) for s in steps),
            "Local rc=0 without actual tests is insufficient")
    numerics = reports["numerics"][0]
    require(numerics.get("state") == "COMPLETE" and numerics.get("verdict") == "PASS" and
            numerics.get("solver", {}).get("sha256") == expected["source_sha256"]["solver.py"] and
            type(numerics.get("checks_executed")) is int and numerics["checks_executed"] > 0,
            "Missing/stale complete numerical evidence")
    check_rows(numerics.get("cases"), "numerical cases")
    check_rows(numerics.get("rejections"), "numerical rejections")
    require(set(numerics.get("convergence", {})) == {"circle", "rectangle", "square"},
            "Missing numerical convergence groups")
    for group in numerics["convergence"].values():
        require(isinstance(group, dict) and group.get("verdict") == "PASS" and
                group.get("resolutions") == [8, 16, 32], "Incomplete numerical convergence")
        errors = group.get("relative_flow_error", {}).get("errors")
        require(isinstance(errors, list) and len(errors) == 3 and
                all(type(x) in (float, int) and math.isfinite(x) and x > 0 for x in errors) and
                errors[0] > errors[1] > errors[2], "Convergence error evidence invalid")
    for kind, tool in (("browser", "tools/browser_check.mjs"), ("audit", "tools/browser_audit.mjs"),
                       ("performance", "tools/browser_performance.mjs")):
        report, path = reports[kind]
        binding(report, expected, tool, now)
        require("exit_code" not in report or
                (type(report["exit_code"]) is int and report["exit_code"] == 0),
                f"Failed producer exit code: {kind}")
        if kind != "performance":
            require(report.get("standard") == "shared-r41", "Unknown web standard")
            rows = report.get("checks")
            require(isinstance(rows, list) and rows and
                    all(isinstance(row, dict) and (row.get("status") or row.get("verdict")) in
                        {"PASS", "NOT_TESTED"} for row in rows),
                    f"Failed/unknown audit or browser checks: {kind}")
            require(any((row.get("status") or row.get("verdict")) == "PASS" for row in rows),
                    f"No successful observed checks: {kind}")
            screenshots(report, path)
    browser = reports["browser"][0]
    require(browser.get("mode") == "OPENSIM_ACCEPTANCE" and
            browser.get("verdict") == "AUTOMATED_PASS", "Browser probe is not full acceptance")
    require(time_value(browser["page_build"]["edited_at"]) == edited_at,
            "Browser edited_at differs from actual tested page")
    matrix = browser.get("matrix")
    require(isinstance(matrix, list) and
            {tuple(row.get("viewport", (row.get("width"), row.get("height"))))
             for row in matrix if isinstance(row, dict) and row.get("status") == "PASS"} >= set(VIEWPORTS),
            "Browser matrix missing required viewports")
    audit = reports["audit"][0]
    require(audit.get("mode") == "G3_G6_AUTOMATIC_EXTENSION_NOT_RELEASE" and
            audit.get("verdict") in {"AUTOMATED_SCOPE_PASS", "AUTOMATED_PASS"},
            "Audit smoke/fixture is not product acceptance")
    performance(reports["performance"][0])
    public = reports["public"][0]
    require(public.get("verdict") == "PASS" and public.get("scope") == "PUBLIC_CODE_AND_FEM" and
            public.get("url") == PUBLIC_URL and public.get("desired_git_head") == commit and
            public.get("source_sha256") == expected["source_sha256"] and
            public.get("page_build") == expected["page_build"], "Missing same-version real public PASS")
    addresses = public.get("dns_addresses")
    require(isinstance(addresses, list) and addresses and
            all(ipaddress.ip_address(ip).is_global for ip in addresses),
            "Loopback/private DNS cannot be public evidence")
    rows = check_rows(public.get("checks"), "public code/FEM")
    require({r.get("name") for r in rows} ==
            {"runtime_identity", "page_and_assets", "real_fem_circle", "real_fem_rectangle"},
            "Missing real public computations")
    fresh(public, now, 600)
    for row in rows:
        if row.get("name", "").startswith("real_fem_"):
            require(re.fullmatch(r"[a-f0-9]{64}", row.get("config_sha256", "")) and
                    row.get("solver_sha256") == expected["source_sha256"]["solver.py"] and
                    type(row.get("node_count")) is int and row["node_count"] > 10 and
                    type(row.get("flow_rate_m3_s")) in (int, float) and
                    math.isfinite(row["flow_rate_m3_s"]) and row["flow_rate_m3_s"] > 0 and
                    type(row.get("relative_residual")) in (int, float) and
                    math.isfinite(row["relative_residual"]) and 0 <= row["relative_residual"] < 1e-6,
                    "Public computation lacks actual FEM identity/metrics")
            fresh({"collected_at": row.get("computed_at")}, now, 600)
    require(isinstance(ledger["gates"], dict) and set(ledger["gates"]) == set(GATES),
            "Missing G1..G6 records")
    for gate, record in ledger["gates"].items():
        require(isinstance(record, dict) and set(record) == {"evidence", "rc", "status", "checks"},
                f"Invalid gate record: {gate}")
        kind = record["evidence"]
        require(kind in evidence and type(record["rc"]) is int, "Unknown gate evidence/rc")
        require(record["status"] in {"PASS", "PARTIAL", "NOT_TESTED", "FAIL"}, "Unknown gate status")
        if gate in waived:
            continue
        require(record["status"] == "PASS" and record["rc"] == 0, f"Required gate incomplete: {gate}")
        require(kind == {"G1-structure": "browser", "G2-layout": "browser",
                         "G3-accessibility": "audit", "G4-keyboard": "browser",
                         "G5-performance": "performance", "G6-content": "audit"}[gate],
                "Gate attributed to wrong producer")
        report = reports[kind][0]
        if kind == "audit":
            require(report.get("gates", {}).get(gate, {}).get("status") == "PASS",
                    f"PARTIAL audit cannot certify {gate}")
        chosen = record["checks"]
        require(isinstance(chosen, list) and chosen and len(set(chosen)) == len(chosen),
                f"Empty/duplicate gate check selection: {gate}")
        available = {row.get("name") for row in report["checks"]}
        require(set(chosen) <= available, f"Unknown gate checks: {gate}")
        require(all((row.get("status") or row.get("verdict")) == "PASS"
                    for row in report["checks"] if row.get("name") in chosen),
                f"NOT_TESTED checks cannot certify {gate}")
        selected = [row for row in report["checks"] if row.get("name") in chosen]
        require(REQUIRED_RULES[gate] <= {row.get("rule") for row in selected},
                f"Gate missing required rule coverage: {gate}")
        for row in selected:
            url = row.get("url") or report.get("target_url") or report.get("url")
            viewport = row.get("viewport")
            if viewport is None and row.get("coordinate"):
                coordinate = row["coordinate"]
                viewport = next(([item.get("width"), item.get("height")] for item in
                                 report.get("matrix", []) if item.get("name") == coordinate), None)
            reproduce = row.get("reproduce") or row.get("coordinate")
            require(isinstance(url, str) and url and isinstance(reproduce, str) and reproduce and
                    isinstance(viewport, list) and len(viewport) == 2,
                    f"Gate checks missing URL/viewport/reproduction coordinates: {gate}")
        if kind == "audit":
            require(not any(row.get("status") == "NOT_TESTED" for row in report["checks"]),
                    f"Unclosed NOT_TESTED audit cannot certify {gate}")
    require(isinstance(ledger["manual"], dict) and set(ledger["manual"]) == set(MANUAL),
            "Missing real device/manual gate records")
    manual_refs = {}
    for rule, record in ledger["manual"].items():
        if rule in waived:
            require(record == {"status": "EXCEPTION"}, "Waived manual record must remain EXCEPTION")
            continue
        report, path = reference(record)
        require(report.get("schema") == "opensim-human-check-v1" and report.get("rule_id") == rule and
                report.get("status") == "PASS" and report.get("commit") == commit and
                report.get("source_sha256") == expected["source_sha256"] and
                report.get("page_build") == expected["page_build"] and
                report.get("mode") == "REAL_DEVICE_HUMAN" and
                all(isinstance(report.get(k), str) and report[k].strip() for k in
                    ("owner", "device", "combination_version", "url", "reproduce")) and
                report["url"] == PUBLIC_URL, f"Missing real human/device result: {rule}")
        fresh(report, now)
        check_rows(report.get("checks"), rule)
        screenshots(report, path)
        manual_refs[rule] = record
    require(identity(root, commit) == expected, "Checkout changed during release gate")
    for kind, pin in evidence.items():
        reference(pin)
        execution(executions[kind], pin, expected, producer_tools[kind], now)
    artifact(page["html"])
    reference(page["page_check"])
    for pin in manual_refs.values():
        reference(pin)
    require(read_file(ledger_path) == raw, "Ledger drift during gate")
    raise GateError("BLOCKED: trusted human-acceptance verifier unavailable; "
                    "editable REAL_DEVICE_HUMAN/PASS records cannot self-certify")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--approval-path")
    parser.add_argument("--approval-sha256")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    report = {"schema": "opensim-release-gate-v1", "verdict": "BLOCKED",
              "release_verdict": "NOT_CERTIFIED", "production_changes": False,
              "collected_at": datetime.now(timezone.utc).isoformat()}
    try:
        require(os.geteuid() != 0, "Release evidence gate must not run as root")
        require(bool(args.approval_path) == bool(args.approval_sha256),
                "Approval path and SHA must be supplied together")
        pin = ({"path": args.approval_path, "sha256": args.approval_sha256}
               if args.approval_path else None)
        report = evaluate(args.source_root, args.commit, args.ledger, pin)
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "reason": str(exc)}
    rc = 0 if report["verdict"] == "PASS" else 1
    report["exit_code"] = rc
    line = (f"OPENSIM_RELEASE_GATE verdict={report['verdict']} "
            f"certification={report['release_verdict']} rc={rc} "
            f"collected_at={report['collected_at']} report={args.out}")
    try:
        write_new(args.out, encode(report))
        write_new(args.out.with_suffix(".log"),
                  (line + "\n" + report.get("failure", {}).get("reason", "") + "\n").encode())
    except Exception as exc:
        print(f"Evidence write failure: {exc}; first verdict={line}", file=sys.stderr)
        return 1
    print(line)
    if "failure" in report:
        print("First cause: " + report["failure"]["reason"], file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
