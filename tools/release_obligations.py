"""Independent measured G3/G6 predicates; no producer, signer, or deployment.

Data validation does not authenticate execution. Only the caller can combine
this result with original producer readback and independently verified trust.
"""

from __future__ import annotations

import math
import os
import argparse
from pathlib import Path

import release_gate as gate

SCHEMA = "opensim-measured-obligations-v1"
OBSERVATION = "opensim-measured-observation-v1"
STATES = ("ready", "loading", "error", "empty", "long", "recovery")
MODES = ("default", "hover", "text-200", "page-400", "forced-colors", "reduced-motion")
KINDS = ("page", "text", "control", "form", "dialog", "table", "code", "media", "canvas")
RULE_KINDS = {
    "WEB-A11Y-001": "text", "WEB-A11Y-002": "control", "WEB-A11Y-004": "control", "WEB-A11Y-005": "page",
    "WEB-A11Y-006": "control", "WEB-A11Y-007": "page", "WEB-A11Y-008": "dialog",
    "WEB-INPUT-001": "control", "WEB-INPUT-002": "control", "WEB-INPUT-003": "form",
    "WEB-INPUT-004": "form", "WEB-INPUT-005": "form", "WEB-TYPE-005": "text",
    "WEB-TYPE-006": "text", "WEB-CONTENT-001": "table", "WEB-CONTENT-002": "table",
    "WEB-CONTENT-003": "table", "WEB-CONTENT-004": "code", "WEB-CONTENT-005": "media",
    "WEB-CONTENT-006": "canvas", "WEB-CONTENT-007": "media", "WEB-ADAPT-002": "page",
}
RULES = frozenset(gate.REQUIRED_RULES["G3-accessibility"] | gate.REQUIRED_RULES["G6-content"])
CONDITIONAL = frozenset({"WEB-A11Y-008", *[f"WEB-CONTENT-{i:03}" for i in (1, 2, 3, 4, 5, 7)]})


def exact(value, names, label):
    gate.require(isinstance(value, dict) and set(value) == set(names), "Invalid " + label + " fields")


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def count(value):
    return type(value) is int and value >= 0


def text(value):
    return isinstance(value, str) and bool(value.strip())


def ratio(colors):
    gate.require(isinstance(colors, list) and len(colors) == 2 and all(
        isinstance(color, list) and len(color) == 3 and all(number(v) and 0 <= v <= 255 for v in color)
        for color in colors), "Invalid measured RGB pair")
    def luminance(color):
        channels = [v / 255 for v in color]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
        return sum(v * weight for v, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    a, b = sorted(map(luminance, colors))
    return (b + 0.05) / (a + 0.05)


def predicate(rule, m, state):
    """Each rule computes its own criterion; no generic valid/PASS shortcut."""
    gate.require(rule in RULES and isinstance(m, dict) and m, "Unknown or empty measured predicate")
    if rule in {"WEB-A11Y-001", "WEB-A11Y-002"}:
        names = {"pairs", "sampled_pixels", "paint_method", "unresolved_paint", "color_only"}
        if rule == "WEB-A11Y-001":
            names |= {"font_px", "font_weight"}
        exact(m, names, "contrast measurement")
        gate.require(m["paint_method"] == "COMPOSITED_PIXEL_PAIRS" and
                     count(m["sampled_pixels"]) and m["sampled_pixels"] > 0 and
                     isinstance(m["pairs"], list) and len(m["pairs"]) == m["sampled_pixels"] and
                     count(m["unresolved_paint"]) and type(m["color_only"]) is bool,
                     "Unproved complete contrast paint")
        threshold = 3.0
        if rule == "WEB-A11Y-001":
            gate.require(number(m["font_px"]) and m["font_px"] > 0 and number(m["font_weight"]),
                         "Invalid measured font")
            threshold = 3.0 if m["font_px"] >= 24 or (m["font_px"] >= 18.6667 and m["font_weight"] >= 700) else 4.5
        return m["unresolved_paint"] == 0 and not m["color_only"] and all(ratio(pair) >= threshold for pair in m["pairs"])
    if rule == "WEB-A11Y-005":
        exact(m, {"dom_order", "visual_order", "unresolved_groups"}, "reading order")
        gate.require(isinstance(m["dom_order"], list) and m["dom_order"] and
                     all(isinstance(v, str) and v for v in m["dom_order"]) and
                     len(set(m["dom_order"])) == len(m["dom_order"]) and
                     isinstance(m["visual_order"], list) and count(m["unresolved_groups"]),
                     "Invalid reading-order samples")
        return m["visual_order"] == m["dom_order"] and m["unresolved_groups"] == 0
    if rule == "WEB-A11Y-004":
        exact(m, {"rect", "viewport", "indicator_pairs", "uncovered", "focus_visible"}, "actual focus")
        gate.require(isinstance(m["rect"], list) and len(m["rect"]) == 4 and all(number(v) for v in m["rect"]) and
                     isinstance(m["viewport"], list) and len(m["viewport"]) == 2 and all(number(v) and v > 0 for v in m["viewport"]) and
                     isinstance(m["indicator_pairs"], list) and m["indicator_pairs"], "Invalid actual focus sample")
        x, y, width, height = m["rect"]
        return width > 0 and height > 0 and x + width > 0 and y + height > 0 and x < m["viewport"][0] and y < m["viewport"][1] and m["uncovered"] is True and m["focus_visible"] is True and all(ratio(pair) >= 3 for pair in m["indicator_pairs"])
    if rule == "WEB-A11Y-006":
        exact(m, {"tag", "role", "keyboard_paths", "state_exposed"}, "control semantics")
        gate.require(isinstance(m["keyboard_paths"], list) and all(text(v) for v in m["keyboard_paths"]) and
                     isinstance(m["tag"], str) and isinstance(m["role"], str) and type(m["state_exposed"]) is bool,
                     "Invalid semantic sample")
        return m["tag"] in {"BUTTON", "A", "INPUT", "SELECT", "TEXTAREA", "SUMMARY"} or (
            m["role"] in {"button", "link", "checkbox", "radio", "slider", "tab"} and
            {"Tab", "Shift+Tab", "activation", "Escape"} <= set(m["keyboard_paths"]) and m["state_exposed"])
    if rule == "WEB-A11Y-007":
        exact(m, {"lang", "title", "names", "unresolved_names"}, "accessible names")
        gate.require(isinstance(m["lang"], str) and isinstance(m["title"], str) and
                     isinstance(m["names"], list) and m["names"] and count(m["unresolved_names"]),
                     "Invalid accessible-name samples")
        return bool(m["lang"].strip() and m["title"].strip()) and all(
            isinstance(v, str) and v.strip() for v in m["names"]) and m["unresolved_names"] == 0
    if rule == "WEB-A11Y-008":
        exact(m, {"opened_focus", "trap_forward", "trap_reverse", "background_inert", "return_focus", "name"}, "dialog")
        return all(m[k] is True for k in m if k != "name") and isinstance(m["name"], str) and bool(m["name"].strip())
    if rule == "WEB-INPUT-001":
        exact(m, {"width", "height"}, "hit target")
        return all(number(m[k]) and m[k] >= 44 for k in m)
    if rule == "WEB-INPUT-002":
        exact(m, {"independent_pairs", "gaps", "overlaps"}, "target spacing")
        gate.require(count(m["independent_pairs"]) and count(m["overlaps"]) and
                     isinstance(m["gaps"], list) and len(m["gaps"]) == m["independent_pairs"], "Invalid hit-pair scan")
        return m["overlaps"] == 0 and all(number(gap) and gap >= 8 for gap in m["gaps"])
    if rule == "WEB-INPUT-003":
        exact(m, {"labels", "controls", "persistent_visible", "programmatic"}, "form labels")
        gate.require(count(m["controls"]) and m["controls"] > 0 and isinstance(m["labels"], list), "Empty form controls")
        return len(m["labels"]) == m["controls"] and all(isinstance(v, str) and v.strip() for v in m["labels"]) and m["persistent_visible"] is True and m["programmatic"] is True
    if rule == "WEB-INPUT-004":
        exact(m, {"error_tested", "focus_target", "invalid_associated", "location", "reason", "repair", "recovered"}, "error recovery")
        return m["error_tested"] is True and m["invalid_associated"] is True and m["recovered"] is True and all(
            isinstance(m[k], str) and m[k].strip() for k in ("focus_target", "location", "reason", "repair"))
    if rule == "WEB-INPUT-005":
        exact(m, {"busy", "live", "focus_before", "focus_after", "destructive_actions", "confirmed_or_undoable"}, "asynchronous feedback")
        gate.require(count(m["destructive_actions"]), "Invalid action count")
        return m["live"] in {"polite", "assertive"} and type(m["busy"]) is bool and m["busy"] == (state == "loading") and text(m["focus_before"]) and m["focus_before"] == m["focus_after"] and type(m["confirmed_or_undoable"]) is bool and (m["destructive_actions"] == 0 or m["confirmed_or_undoable"] is True)
    if rule in {"WEB-TYPE-005", "WEB-TYPE-006"}:
        exact(m, {"payload_sha256", "rendered_sha256", "clipped", "horizontal_overflow", "expanded", "unique_information_hidden"}, "long content")
        return isinstance(m["payload_sha256"], str) and bool(gate.re.fullmatch(r"[a-f0-9]{64}", m["payload_sha256"])) and m["payload_sha256"] == m["rendered_sha256"] and m["clipped"] is False and m["horizontal_overflow"] is False and m["unique_information_hidden"] is False and type(m["expanded"]) is bool
    if rule == "WEB-CONTENT-001":
        exact(m, {"tag", "caption", "headers", "programmatic_associations"}, "table semantics")
        return m["tag"] == "TABLE" and text(m["caption"]) and isinstance(m["headers"], list) and bool(m["headers"]) and all(isinstance(v, str) and v in {"row", "col", "rowgroup", "colgroup"} for v in m["headers"]) and m["programmatic_associations"] is True
    if rule == "WEB-CONTENT-002":
        exact(m, {"page_scroll_width", "page_client_width", "local_scroll", "focusable", "name"}, "table scrolling")
        return number(m["page_scroll_width"]) and number(m["page_client_width"]) and m["page_scroll_width"] > 0 and m["page_client_width"] > 0 and m["page_scroll_width"] <= m["page_client_width"] + 1 and m["local_scroll"] is True and m["focusable"] is True and text(m["name"])
    if rule == "WEB-CONTENT-003":
        exact(m, {"strategy", "header_value_associations"}, "mobile table strategy")
        return m["strategy"] in {"horizontal-scroll", "priority-columns", "cards", "stepwise"} and m["header_value_associations"] is True
    if rule == "WEB-CONTENT-004":
        exact(m, {"pre_code", "format_preserved", "local_scroll_or_wrap", "page_overflow", "copy_name", "copy_feedback"}, "code block")
        return all(m[k] is True for k in ("pre_code", "format_preserved", "local_scroll_or_wrap", "copy_feedback")) and m["page_overflow"] is False and text(m["copy_name"])
    if rule == "WEB-CONTENT-005":
        exact(m, {"content_width", "container_width", "aspect_reserved", "distorted"}, "responsive media")
        return number(m["content_width"]) and number(m["container_width"]) and 0 < m["content_width"] <= m["container_width"] and m["aspect_reserved"] is True and m["distorted"] is False
    if rule == "WEB-CONTENT-006":
        exact(m, {"phase", "navigation", "current_navigation", "solve_sequence", "latest_sequence", "result_config_sha256", "current_config_sha256", "result_nonempty", "no_result", "metrics_uncomputed", "export_disabled", "adjacent_summary", "color_only"}, "current graphic alternative")
        gate.require(type(m["navigation"]) is int and type(m["current_navigation"]) is int and
                     type(m["solve_sequence"]) is int and type(m["latest_sequence"]) is int and
                     all(m[k] >= 0 for k in ("navigation", "current_navigation", "solve_sequence", "latest_sequence")) and
                     all(type(m[k]) is bool for k in ("result_nonempty", "no_result", "metrics_uncomputed", "export_disabled", "color_only")), "Invalid graphic sequence/state")
        if m["navigation"] != m["current_navigation"] or m["color_only"] is not False:
            return False
        if state in {"ready", "long", "recovery"}:
            return m["phase"] == "ready" and m["solve_sequence"] == m["latest_sequence"] and isinstance(m["current_config_sha256"], str) and bool(gate.re.fullmatch(r"[a-f0-9]{64}", m["current_config_sha256"])) and m["result_config_sha256"] == m["current_config_sha256"] and m["result_nonempty"] is True and m["no_result"] is False and m["metrics_uncomputed"] is False and m["export_disabled"] is False and text(m["adjacent_summary"])
        return m["phase"] == {"loading": "working", "error": "failed", "empty": "failed"}[state] and m["result_nonempty"] is False and m["no_result"] is True and m["metrics_uncomputed"] is True and m["export_disabled"] is True
    if rule == "WEB-CONTENT-007":
        exact(m, {"audible_autoplay", "moving", "pause_available", "video", "captions", "audio", "transcript"}, "media disturbance")
        return m["audible_autoplay"] is False and (m["moving"] is False or m["pause_available"] is True) and (m["video"] is False or m["captions"] is True) and (m["audio"] is False or m["transcript"] is True)
    if rule == "WEB-ADAPT-002":
        exact(m, {"profile", "shell_matches", "main_matches", "main_selector", "shell_selector"}, "content profile")
        return m["profile"] in {"long-form", "application", "data-canvas", "media"} and type(m["shell_matches"]) is int and m["shell_matches"] == 1 and type(m["main_matches"]) is int and m["main_matches"] == 1 and text(m["main_selector"]) and text(m["shell_selector"])
    raise gate.GateError("Measured predicate missing implementation: " + rule)


def policy(identity, pages, implementation_sha256):
    gate.require(isinstance(pages, list) and pages, "Empty measured pages")
    urls = set()
    for page in pages:
        exact(page, {"url", "objects"}, "measured page")
        gate.require(page["url"] == gate.PUBLIC_URL and page["url"] not in urls, "Foreign or duplicate measured page")
        urls.add(page["url"])
        gate.require(isinstance(page["objects"], list) and page["objects"], "Empty measured object inventory")
        ids = set()
        for obj in page["objects"]:
            exact(obj, {"id", "kind"}, "measured object")
            gate.require(isinstance(obj["id"], str) and obj["id"] and not obj["id"].startswith("absent:") and obj["id"] not in ids and obj["kind"] in KINDS, "Invalid measured object")
            ids.add(obj["id"])
        gate.require(sum(obj["kind"] == "page" for obj in page["objects"]) == 1, "Exactly one measured page object required")
    return {"schema": SCHEMA, "standard": "shared-r41", "candidate": identity,
            "implementation_sha256": implementation_sha256, "pages": pages,
            "rules": sorted(RULES), "viewports": [list(v) for v in gate.VIEWPORTS],
            "states": list(STATES), "modes": list(MODES)}


def coordinates(value):
    rows = []
    for page in value["pages"]:
        for view in value["viewports"]:
            for state in value["states"]:
                for mode in value["modes"]:
                    for rule in value["rules"]:
                        kind = RULE_KINDS[rule]
                        objects = [obj for obj in page["objects"] if obj["kind"] == kind]
                        for obj in objects or [{"id": "absent:" + kind}]:
                            rows.append({"rule": rule, "url": page["url"], "object": obj["id"],
                                         "viewport": view, "state": state, "mode": mode})
    return rows


def scans(value):
    return [{"url": page["url"], "viewport": view, "state": state, "mode": mode,
             "objects": page["objects"], "kinds": list(KINDS), "complete": True}
            for page in value["pages"] for view in value["viewports"]
            for state in value["states"] for mode in value["modes"]]


def stamp_pin(pin):
    value, path = gate.reference(pin)
    body, info = gate._audit_file(path, 1)
    gate.require(gate.sha(body) == pin["sha256"], "Measured evidence changed while reading")
    return value, (path, body, info)


def verify_records(records):
    for path, body, info in records:
        gate.require(gate._audit_file(path, 1) == (body, info), "Measured evidence late inode/bytes drift")
        gate.require(not any(os.path.lexists(path.parent / name) for name in (
            "report.failure.json", "finalization-failure.json", "report-revocation.json")) and
            not list(path.parent.glob("report-failure*.json")), "Measured producer failure sidecar")


def evaluate(policy_pin, ledger_pin, identity, implementation_sha256):
    p, p_record = stamp_pin(policy_pin)
    gate.require(p == policy(identity, p.get("pages"), implementation_sha256), "Measured policy version/source/rules/matrix drift")
    ledger, l_record = stamp_pin(ledger_pin)
    exact(ledger, {"schema", "policy", "baseline", "supplements"}, "measured ledger")
    gate.require(ledger["schema"] == SCHEMA and ledger["policy"] == policy_pin and isinstance(ledger["supplements"], list), "Measured ledger version/policy mismatch")
    required = {gate.encode(row): row for row in coordinates(p)}
    records, originals, positives, seen_pins = [p_record, l_record], [], {}, set()
    for index, pin in enumerate([ledger["baseline"], *ledger["supplements"]]):
        report, record = stamp_pin(pin)
        gate.require(record[0] not in seen_pins, "Duplicate measured evidence file")
        seen_pins.add(record[0])
        records.append(record)
        exact(report, {"schema", "candidate", "tool", "scope", "exit_code", "failure", "scans", "checks"}, "measured observation")
        gate.require(report["schema"] == OBSERVATION and report["candidate"] == identity, "Measured observation version/candidate drift")
        exact(report["tool"], {"path", "sha256"}, "measured producer")
        gate.require(report["tool"]["path"] in {"tools/browser_audit.mjs", "tools/browser_check.mjs"} and identity["tool_sha256"].get(report["tool"]["path"]) == report["tool"]["sha256"], "Measured producer tool mismatch")
        gate.require(report["scope"] == "UNATTESTED_MEASUREMENTS_NOT_CERTIFICATION", "Unknown measured scope")
        gate.require(type(report["exit_code"]) is int and report["exit_code"] == 0 and report["failure"] is None, "Measured producer failure veto")
        gate.require(isinstance(report["scans"], list) and sorted(map(gate.encode, report["scans"])) == sorted(map(gate.encode, scans(p))), "Incomplete measured capability scans")
        gate.require(isinstance(report["checks"], list) and report["checks"], "Empty measured checks")
        seen = set()
        for row in report["checks"]:
            exact(row, {"coordinate", "status", "measurements"}, "measured check")
            key = gate.encode(row["coordinate"])
            gate.require(key in required and key not in seen, "Unknown or duplicate measured coordinate")
            seen.add(key)
            gate.require(row["status"] in {"PASS", "FAIL", "NOT_TESTED", "PARTIAL", "NA"}, "Unknown measured status")
            gate.require(row["status"] != "FAIL", "Measured producer failure veto")
            c = required[key]
            if row["status"] == "NA":
                exact(row["measurements"], {"absence_scan"}, "absence proof")
                gate.require(c["rule"] in CONDITIONAL and c["object"] == "absent:" + RULE_KINDS[c["rule"]] and row["measurements"]["absence_scan"] is True, "Unproved measured N/A")
                positives.setdefault(key, []).append({"pin": pin, "row_sha256": gate.sha(gate.encode(row)), "status": "NA"})
            elif row["status"] == "PASS":
                gate.require(not c["object"].startswith("absent:"), "Absent object cannot PASS")
                gate.require(predicate(c["rule"], row["measurements"], c["state"]), "Measured predicate failure: " + c["rule"])
                positives.setdefault(key, []).append({"pin": pin, "row_sha256": gate.sha(gate.encode(row)), "status": "PASS"})
            else:
                gate.require(isinstance(row["measurements"], dict), "Missing original open measurement")
        gate.require(index != 0 or seen == set(required), "Missing measured baseline coordinate")
        originals.append({"pin": pin, "checks": report["checks"]})
    opened = [row for key, row in required.items() if key not in positives]
    closed = [{"coordinate": row, "evidence": positives[key]} for key, row in required.items() if key in positives]
    verify_records(records)
    return {"schema": SCHEMA, "verdict": "OPEN_OBLIGATIONS" if opened else "MEASURED_PREDICATES_CLOSED",
            "release_verdict": "NOT_CERTIFIED", "scope": "MEASURED_PREDICATES_NOT_EXECUTION_AUTHENTICATION",
            "candidate": identity, "policy": policy_pin, "ledger": ledger_pin,
            "required": len(required), "open": opened, "closed": closed,
            "originals": originals, "materials": [policy_pin, ledger_pin, ledger["baseline"], *ledger["supplements"]]}, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--policy-sha256", required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--ledger-sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    guards = []
    report = {"schema": SCHEMA, "verdict": "INVALID_MEASUREMENTS", "exit_code": 2,
              "release_verdict": "NOT_CERTIFIED", "production_changes": False,
              "collected_at": gate.datetime.now(gate.timezone.utc).isoformat()}
    try:
        gate.new_result_paths(args.out)
        identity = gate.identity(args.source_root, args.commit)
        body = gate.committed(args.source_root, args.commit, "tools/release_obligations.py")
        gate.require(gate.read_file(Path(__file__)) == body, "Measured evaluator differs from candidate")
        report, records = evaluate({"path": str(args.policy), "sha256": args.policy_sha256},
                                   {"path": str(args.ledger), "sha256": args.ledger_sha256},
                                   identity, gate.sha(body))
        report.update(exit_code=3 if report["open"] else 0, production_changes=False,
                      collected_at=gate.datetime.now(gate.timezone.utc).isoformat())
        def verify():
            verify_records(records)
            gate.require(gate.identity(args.source_root, args.commit) == identity and
                         gate.committed(args.source_root, args.commit, "tools/release_obligations.py") == body,
                         "Measured candidate late drift")
        guards.append(verify)
    except Exception as exc:
        report.update(verdict="INVALID_MEASUREMENTS", exit_code=2,
                      failure={"type": type(exc).__name__, "reason": str(exc)})
    try:
        return gate.finalize_result(report, args.out, status_key="verdict", failed_status="INVALID_MEASUREMENTS",
                                    label="OPENSIM_MEASURED_CLOSURE", verify=lambda: [v() for v in guards],
                                    emit=gate.native_assessment_stdout)
    except Exception as exc:
        print(f"MEASURED_TERMINAL_FAILURE: {exc}", file=gate.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
