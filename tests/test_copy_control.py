"""Real loopback Chromium copy regression; no release, human or production claims."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/authorized-s1-copy-control-20261011")
EVIDENCE = Path("/hf3fs-jd/data/tmp/opensim-fluid-v1/local-v8/numerics.json")
SOURCES = ("tools/render_page.py", "web/app.js", "web/app.css", "tests/test_copy_control.py",
           "docs/testing/COPY_CONTROL.md", "solver.py", "server.py", "tools/browser_audit.mjs")
sys.path.insert(0, str(ROOT))
import server
from tools.render_page import _preview, check_page, render_page

# This module is materialized exclusively inside the fresh test output, never the source tree.
BROWSER_SOURCE = r"""
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
const [source, url, run, expectedFile, mode] = process.argv.slice(2);
assert.equal(process.argv.slice(2).length, 5);
assert(["copy", "tab-exclusion-red"].includes(mode));
assert(run.startsWith("/hf3fs-jd/data/tmp/opensim-fluid-v1/authorized-s1-copy-control-20261011/")
  && path.resolve(run) === run, "Fixed fresh output required");
const { Browser } = await import(pathToFileURL(path.join(source, "tools/browser_audit.mjs")));
const hash = value => createHash("sha256").update(value).digest("hex");
const expected = await fs.readFile(expectedFile, "utf8");
await fs.mkdir(run);
await fs.mkdir(path.join(run, "logs"));
const report = { schema: "opensim-copy-control-browser-v1", scope: "REAL_LOOPBACK_COPY_ONLY",
  release_verdict: "NOT_CERTIFIED", certified: false, checks: [], screenshots: [],
  started_at: new Date().toISOString(), source_root: source, mode, tab_traces: [] };
const browser = new Browser(url, run, report);
const record = (name, detail) => report.checks.push({ name, status: "PASS", detail });
let rc = 1;
async function identity(stage) {
  const observed = await browser.evaluate(async () => {
    const health = await fetch("/api/health", { cache: "no-store", redirect: "error" });
    if (!health.ok) throw new Error("Health is not ready");
    const raw = await health.text(), resources = {};
    for (const relative of ["/", "/web/app.js", "/web/app.css"]) {
      const response = await fetch(relative, { cache: "no-store", redirect: "error" });
      if (!response.ok) throw new Error("Asset not ready " + relative);
      resources[relative] = Array.from(new Uint8Array(await response.arrayBuffer()));
    }
    return { health: JSON.parse(raw), raw, resources, url: location.href,
      footer: document.querySelector("footer").textContent };
  });
  assert.equal(observed.url, url);
  const local = {}, fingerprint = createHash("sha256");
  for (const relative of ["tools/render_page.py", "web/app.js", "web/app.css", "solver.py", "server.py"]) {
    const data = await fs.readFile(path.join(source, relative));
    local[relative] = hash(data);
    fingerprint.update(relative + "\0").update(data).update("\0");
  }
  assert.deepEqual(observed.health.source_sha256, local);
  const build = fingerprint.digest("hex").slice(0, 12);
  assert.equal(observed.health.page_build, build);
  assert(observed.footer.includes(build));
  const assets = Object.fromEntries(Object.entries(observed.resources).map(([key, value]) =>
    [key, hash(Buffer.from(value))]));
  for (const name of ["web/app.js", "web/app.css"]) assert.equal(assets["/" + name], local[name]);
  const current = { source_sha256: local, page_build: build, asset_sha256: assets,
    health_sha256: hash(observed.raw), url };
  report.identity ??= [];
  if (stage === "after") assert.deepEqual(current, report.identity[0].value, "Late served drift");
  report.identity.push({ stage, value: current, observed_at: new Date().toISOString() });
}
async function state() {
  return browser.evaluate(() => {
    const button = document.querySelector("#copy-evidence-button"), status = document.querySelector("#copy-evidence-status");
    return { text: document.querySelector("#evidence-preview code")?.textContent,
      name: button?.textContent.trim(), state: status?.dataset.state, feedback: status?.textContent,
      role: status?.getAttribute("role"), live: status?.getAttribute("aria-live"),
      atomic: status?.getAttribute("aria-atomic"), focus: document.activeElement?.id,
      aria_disabled: button?.getAttribute("aria-disabled"), phase: document.querySelector("#workspace").dataset.state,
      export_enabled: !document.querySelector("#export-button").disabled,
      visible: !!status && status.getClientRects().length > 0 && !status.hidden };
  });
}
async function click() {
  const point = await browser.evaluate(() => {
    const button = document.querySelector("#copy-evidence-button");
    button.scrollIntoView({ block: "center" });
    const r = button.getBoundingClientRect(), x = r.x + r.width / 2, y = r.y + r.height / 2;
    if (document.elementFromPoint(x, y) !== button) throw new Error("Copy target occluded");
    return { x, y };
  });
  await browser.send("Input.dispatchMouseEvent", { type: "mousePressed", ...point, button: "left", clickCount: 1 });
  await browser.send("Input.dispatchMouseEvent", { type: "mouseReleased", ...point, button: "left", clickCount: 1 });
}
async function focusSnapshot() {
  return browser.evaluate(() => {
    const active = document.activeElement;
    return { tag: active?.tagName, id: active?.id,
      summary: !!active?.matches("#convergence .evidence-report > summary"),
      copy: active?.id === "copy-evidence-button",
      tabindex: document.querySelector("#copy-evidence-button").getAttribute("tabindex"),
      details_open: document.querySelector("#convergence .evidence-report").open };
  });
}
async function tabToSummary() {
  const count = await browser.evaluate(() => document.querySelectorAll("button,input,select,a[href],summary,[tabindex]").length);
  for (let index = 0; index <= count + 2; index++) {
    if ((await focusSnapshot()).summary) return;
    await browser.key("Tab");
  }
  assert.fail("Real Tab cannot reach convergence summary");
}
async function tabToCopy(label) {
  const start = await focusSnapshot();
  assert(start.summary && !start.copy && start.details_open, "Tab must start on open summary, not copy button");
  const trace = { label, start, events: [], reached: false };
  report.tab_traces.push(trace);
  const count = await browser.evaluate(() => document.querySelectorAll("button,input,select,a[href],summary,[tabindex]").length);
  for (let index = 0; index <= count + 2; index++) {
    await browser.key("Tab");
    const focus = await focusSnapshot();
    trace.events.push({ key: "Tab", focus });
    if (focus.copy) { trace.reached = true; break; }
    if (focus.summary) break;
  }
  assert(trace.reached, "Real Tab cannot reach copy button from summary");
  assert(trace.events.length > 0);
  return trace;
}
async function success(name, action, text) {
  await browser.evaluate(async () => { await navigator.clipboard.writeText("COPY_TEST_SENTINEL"); });
  await action();
  await browser.wait("Copy success " + name, async () => (await state()).state === "copied");
  const current = await state(), clipboard = await browser.evaluate(async () => navigator.clipboard.readText());
  assert.equal(clipboard, text, "Clipboard differs from original pre text");
  assert.equal(hash(Buffer.from(clipboard)), hash(Buffer.from(text)));
  assert.equal(current.text, text);
  assert.match(current.feedback, /\u5df2\u590d\u5236/);
  assert.equal(current.focus, "copy-evidence-button");
  assert.equal(current.phase, "ready");
  assert.equal(current.export_enabled, true);
  assert.equal(current.visible, true);
  assert.equal(current.aria_disabled, "false");
  record(name, { ...current, text: undefined, bytes: Buffer.byteLength(clipboard),
    clipboard_sha256: hash(clipboard), clipboard: "REAL_CHROMIUM_READBACK" });
}
try {
  await browser.launch();
  await browser.cdp.send("Browser.grantPermissions", { origin: new URL(url).origin,
    permissions: ["clipboardReadWrite", "clipboardSanitizedWrite"] });
  for (const [width, height] of mode === "copy" ? [[320, 568], [1440, 900]] : [[320, 568]]) {
    await browser.viewport(width, height);
    await browser.navigate();
    if (width === 320) await identity("before");
    assert.equal(await browser.evaluate(() => document.querySelectorAll("#copy-evidence-button").length), 1,
      "WEB-CONTENT-004 missing native copy control");
    await browser.activate("#convergence .evidence-report > summary");
    const current = await state();
    assert.equal(current.text, expected);
    assert.equal(current.name, "\u590d\u5236\u6570\u503c\u62a5\u544a\u9884\u89c8");
    assert.equal(current.role, "status");
    assert.equal(current.live, "polite");
    assert.equal(current.atomic, "true");
    const geometry = await browser.evaluate(() => {
      const b = document.querySelector("#copy-evidence-button"), p = document.querySelector("#evidence-preview");
      const r = b.getBoundingClientRect();
      return { width: r.width, height: r.height, page_width: document.documentElement.scrollWidth,
        viewport_width: document.documentElement.clientWidth, overflow_x: getComputedStyle(p).overflowX,
        white_space: getComputedStyle(p.querySelector("code")).whiteSpace };
    });
    assert(geometry.width >= 44 && geometry.height >= 44);
    assert(geometry.page_width <= geometry.viewport_width + 1);
    assert(["auto", "scroll"].includes(geometry.overflow_x));
    assert(["pre", "pre-wrap", "break-spaces"].includes(geometry.white_space));
    record("geometry-" + width, geometry);
    if (mode === "tab-exclusion-red") {
      await browser.evaluate(() => document.querySelector("#copy-evidence-button").setAttribute("tabindex", "-1"));
      await success("legacy-mouse-TEST_ONLY", click, expected);
      await success("legacy-enter-TEST_ONLY", () => browser.activate("#copy-evidence-button"), expected);
      await tabToSummary();
      await tabToCopy("excluded-red-" + width);
      assert.fail("Excluded copy button unexpectedly passed Tab oracle");
    }
    record("keyboard-tab-" + width, await tabToCopy("native-" + width));
    await browser.screenshot("copy-tab-focus-" + width, false);
    await success("keyboard-enter-" + width, () => browser.key("Enter"), expected);
    await success("keyboard-space-" + width, () => browser.key(" "), expected);
    const originalTabindex = await browser.evaluate(() => document.querySelector("#copy-evidence-button").getAttribute("tabindex"));
    assert.equal(originalTabindex, null, "Original copy button must retain native tab order");
    try {
      await browser.evaluate(() => document.querySelector("#copy-evidence-button").setAttribute("tabindex", "-1"));
      await tabToSummary();
      await assert.rejects(() => tabToCopy("excluded-" + width), {
        name: "AssertionError", message: "Real Tab cannot reach copy button from summary",
      });
      record("tabindex-minus-one-excluded-" + width, report.tab_traces.at(-1));
    } finally {
      await browser.evaluate(value => {
        const button = document.querySelector("#copy-evidence-button");
        if (value === null) button.removeAttribute("tabindex");
        else button.setAttribute("tabindex", value);
      }, originalTabindex);
    }
    await tabToSummary();
    record("keyboard-tab-restored-" + width, await tabToCopy("restored-" + width));
    await success("mouse-" + width, click, expected);
    await browser.screenshot("copy-success-" + width, false);
  }
  await browser.viewport(320, 568);
  const special = "  \u4e2d\u6587 <tag> & \u5f15\u53f7\"\n\t\u7b2c\u4e8c\u884c   \n\n";
  await browser.evaluate(text => { document.querySelector("#evidence-preview code").textContent = text; }, special);
  await success("exact-whitespace-TEST_ONLY", click, special);
  await browser.evaluate(() => { document.querySelector("#evidence-preview code").textContent = ""; });
  await click();
  await browser.wait("Empty text refusal", async () => (await state()).state === "failed");
  assert.match((await state()).feedback, /\u590d\u5236\u5931\u8d25/);
  assert.equal(await browser.evaluate(async () => navigator.clipboard.readText()), special);
  record("empty-TEST_ONLY", { clipboard_unchanged: true });
  await browser.evaluate(text => { document.querySelector("#evidence-preview code").textContent = text; }, expected);
  for (const fault of ["denied", "missing", "sync-throw"]) {
    const priorClipboard = await browser.evaluate(async () => navigator.clipboard.readText());
    await browser.evaluate(mode => {
      window.copyOriginal = Object.getOwnPropertyDescriptor(navigator, "clipboard");
      window.copyAPI = navigator.clipboard;
      const error = new DOMException("TEST_ONLY clipboard denied", "NotAllowedError");
      Object.defineProperty(navigator, "clipboard", { configurable: true, value: mode === "missing" ? undefined : {
        writeText: () => { if (mode === "sync-throw") throw error; return Promise.reject(error); },
      } });
    }, fault);
    await click();
    await browser.wait("Clipboard failure " + fault, async () => (await state()).state === "failed");
    const current = await state();
    assert.match(current.feedback, /\u590d\u5236\u5931\u8d25/);
    assert.doesNotMatch(current.feedback, /\u5df2\u590d\u5236/);
    assert(current.visible && current.export_enabled);
    assert.equal(current.focus, "copy-evidence-button");
    assert.equal(current.phase, "ready");
    assert.equal(current.aria_disabled, "false");
    await browser.screenshot("copy-failure-" + fault, false);
    record("fault-" + fault, { ...current, text: undefined, method: "TEST_ONLY_API_NOT_PHYSICAL_PERMISSION_UI" });
    await browser.evaluate(() => {
      if (window.copyOriginal) Object.defineProperty(navigator, "clipboard", window.copyOriginal);
      else delete navigator.clipboard;
    });
    assert.equal(await browser.evaluate(async () => navigator.clipboard.readText()), priorClipboard,
      "Failed copy changed clipboard contents");
    await success("retry-" + fault, click, expected);
  }
  await browser.evaluate(() => {
    window.copyOriginal = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    window.copyAPI = navigator.clipboard; window.copyCalls = 0;
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
      writeText: text => { window.copyCalls++; return new Promise((resolve, reject) => {
        window.copyRelease = () => window.copyAPI.writeText(text).then(resolve, reject);
      }); },
    } });
  });
  await click();
  await browser.wait("Held copy starts", async () => (await state()).state === "copying");
  await click();
  assert.equal(await browser.evaluate(() => window.copyCalls), 1);
  assert.equal((await state()).aria_disabled, "true");
  await browser.evaluate(async () => {
    await window.copyRelease();
    if (window.copyOriginal) Object.defineProperty(navigator, "clipboard", window.copyOriginal);
    else delete navigator.clipboard;
  });
  await browser.wait("Held copy done", async () => (await state()).state === "copied");
  assert.equal(await browser.evaluate(async () => navigator.clipboard.readText()), expected);
  record("duplicate-activation-TEST_ONLY", { actual_writes: 1 });
  assert(browser.runtimeEvents.some(e => e.message.includes("OpenSim evidence copy failed:")));
  assert(browser.runtimeEvents.some(e => e.message.includes("NotAllowedError")), "Original first cause swallowed");
  await Promise.all(browser.tasks);
  browser.assertErrors();
  assert.equal(browser.responses.length, 2, "Copy triggered unexpected FEM POST");
  await identity("after");
  for (const response of browser.responses) {
    assert.equal(response.status, 200);
    assert.deepEqual(response.payload.reproducibility.source_sha256, report.identity[0].value.source_sha256);
  }
  rc = 0;
} catch (error) {
  report.first_failure = { name: error.name, message: error.message, stack: error.stack };
} finally {
  try { await browser.close(); }
  catch (error) { report.cleanup_failure = { name: error.name, message: error.message }; rc = 1; }
}
report.chromium_exit_code = browser.child?.exitCode ?? null;
report.responses = browser.responses;
report.runtime_events = browser.runtimeEvents;
report.actual_exit_code = rc;
report.verdict = rc === 0 ? "PASS_SCOPE_COPY_ONLY" : "FAIL";
report.finished_at = new Date().toISOString();
await fs.writeFile(path.join(run, "report.json"), JSON.stringify(report, null, 2) + "\n", { flag: "wx" });
console.log(`COPY_CONTROL_BROWSER verdict=${report.verdict} rc=${rc} checks=${report.checks.length} out=${run}`);
process.exitCode = rc;
"""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def write_new(file, data):
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    if file.read_bytes() != data:
        raise OSError("Test output byte readback mismatch")


def inventory():
    return {name: sha((ROOT / name).read_bytes()) for name in SOURCES}


class CodeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_code = False
        self.text = ""

    def handle_starttag(self, tag, attrs):
        if tag == "code":
            self.in_code = True

    def handle_endtag(self, tag):
        if tag == "code":
            self.in_code = False

    def handle_data(self, value):
        if self.in_code:
            self.text += value


class CopyControlTests(unittest.TestCase):
    out = None
    children = []

    def test_existing_page_regression(self):
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_page.py")
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
        write_new(self.out / "page-regression.log", stream.getvalue().encode())
        write_new(self.out / "page-regression.json", encode({"tests": result.testsRun,
                  "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)}))
        self.assertGreater(result.testsRun, 0)
        self.assertFalse(result.skipped)
        self.assertTrue(result.wasSuccessful(), stream.getvalue())

    def test_original_renderer_exact_preview_and_absent_evidence(self):
        html = render_page(EVIDENCE)
        self.assertEqual(html.count("id=\"copy-evidence-button\""), 1)
        self.assertIn("aria-controls=\"evidence-preview\"", html)
        self.assertIn("aria-describedby=\"copy-evidence-status\"", html)
        self.assertGreater(check_page(html)["checks"], 0)
        parsed = CodeParser()
        preview = html.split("<pre", 1)[1].split("</pre>", 1)[0]
        parsed.feed("<pre" + preview + "</pre>")
        expected = json.dumps(_preview(json.loads(EVIDENCE.read_bytes())), ensure_ascii=False,
                              sort_keys=True, indent=2, allow_nan=False)
        self.assertEqual(parsed.text, expected)
        absent = render_page()
        self.assertNotIn("copy-evidence-button", absent)
        self.assertNotIn("<pre", absent)
        write_new(self.out / "rendered.html", html.encode())

    def test_real_clipboard_click_keyboard_failure_and_retry(self):
        self._real_browser()

    def test_real_tabindex_minus_one_is_rejected(self):
        self._real_browser("tab-exclusion-red", expected_rc=1)

    def _real_browser(self, mode="copy", expected_rc=0):
        out = self.out
        if mode == "tab-exclusion-red":
            out = self.out / "tab-exclusion"
            out.mkdir(exist_ok=False)
        numerical = json.loads(EVIDENCE.read_bytes())
        self.assertEqual(numerical["solver"]["sha256"], server.RUNTIME_SOURCES["solver.py"])
        expected = json.dumps(_preview(numerical), ensure_ascii=False, sort_keys=True,
                              indent=2, allow_nan=False)
        expected_file = out / "expected-preview.txt"
        module = out / "copy-browser.mjs"
        write_new(expected_file, expected.encode())
        write_new(module, BROWSER_SOURCE.encode())
        handler = logging.FileHandler(out / "server.log", encoding="utf-8")
        logging.getLogger().addHandler(handler)
        app = server.ApplicationServer(("127.0.0.1", 0), evidence=EVIDENCE)
        worker = threading.Thread(target=app.serve_forever, daemon=True)
        worker.start()
        argv = ["node", str(module), str(ROOT), f"http://127.0.0.1:{app.server_port}/",
                str(out / "browser"), str(expected_file), mode]
        started = datetime.now(timezone.utc).isoformat()
        try:
            with (out / "browser.stdout.log").open("xb") as stdout, (out / "browser.stderr.log").open("xb") as stderr:
                result = subprocess.run(argv, cwd=ROOT, stdout=stdout, stderr=stderr, timeout=180)
            record = {"argv": argv, "actual_rc": result.returncode, "started_at": started,
                      "finished_at": datetime.now(timezone.utc).isoformat()}
            self.children.append(record)
            write_new(out / "browser-process.json", encode(record))
            report_file = out / "browser/report.json"
            report = json.loads(report_file.read_bytes())
            first_failure = report.get("first_failure")
            self.assertEqual(result.returncode, expected_rc, f"{report_file}: {first_failure}")
            self.assertEqual(report["actual_exit_code"], result.returncode)
            self.assertEqual(report["release_verdict"], "NOT_CERTIFIED")
            self.assertEqual(report["chromium_exit_code"], 0)
            if mode == "tab-exclusion-red":
                self.assertEqual(report["verdict"], "FAIL")
                self.assertEqual(report["first_failure"]["name"], "AssertionError")
                self.assertEqual(report["first_failure"]["message"], "Real Tab cannot reach copy button from summary")
                self.assertEqual([row["name"] for row in report["checks"]],
                                 ["geometry-320", "legacy-mouse-TEST_ONLY", "legacy-enter-TEST_ONLY"])
                self.assertEqual(len(report["tab_traces"]), 1)
                trace = report["tab_traces"][0]
                self.assertTrue(trace["start"]["summary"])
                self.assertEqual(trace["start"]["tabindex"], "-1")
                self.assertFalse(trace["reached"])
                self.assertGreater(len(trace["events"]), 0)
                self.assertTrue(all(event["key"] == "Tab" and not event["focus"]["copy"] for event in trace["events"]))
                self.assertTrue(trace["events"][-1]["focus"]["summary"])
                write_new(out / "tab-negative-readback.json", encode({
                    "scope": "TEST_ONLY_DYNAMIC_TABINDEX_EXCLUSION", "child_actual_rc": result.returncode,
                    "first_failure": report["first_failure"], "trace": trace,
                    "report_sha256": sha(report_file.read_bytes()),
                    "module_sha256": sha(module.read_bytes()),
                    "legacy_mouse_and_enter_still_pass": True, "certified": False,
                }))
                return
            expected_names = {f"{kind}-{width}" for width in (320, 1440)
                              for kind in ("geometry", "mouse", "keyboard-enter", "keyboard-space",
                                           "keyboard-tab", "tabindex-minus-one-excluded", "keyboard-tab-restored")}
            expected_names.update(("exact-whitespace-TEST_ONLY", "empty-TEST_ONLY",
                                   "duplicate-activation-TEST_ONLY"))
            expected_names.update(f"{kind}-{fault}" for kind in ("fault", "retry")
                                  for fault in ("denied", "missing", "sync-throw"))
            names = [row["name"] for row in report["checks"]]
            self.assertEqual(len(names), len(set(names)), "Duplicate test check names")
            self.assertEqual(set(names), expected_names)
            self.assertTrue(all(row["status"] == "PASS" for row in report["checks"]))
            self.assertEqual([trace["label"] for trace in report["tab_traces"]],
                             [f"{kind}-{width}" for width in (320, 1440) for kind in ("native", "excluded", "restored")])
            for trace in report["tab_traces"]:
                self.assertTrue(trace["start"]["summary"] and not trace["start"]["copy"])
                self.assertGreater(len(trace["events"]), 0)
                self.assertTrue(all(event["key"] == "Tab" for event in trace["events"]))
                excluded = trace["label"].startswith("excluded-")
                self.assertEqual(trace["reached"], not excluded)
                self.assertEqual(trace["events"][-1]["focus"]["copy"], not excluded)
                if excluded:
                    self.assertEqual(trace["start"]["tabindex"], "-1")
                    self.assertFalse(any(event["focus"]["copy"] for event in trace["events"]))
            self.assertEqual(len(report["responses"]), 2)
            self.assertEqual(len(report["identity"]), 2)
            self.assertEqual({row["file"] for row in report["screenshots"]}, {
                "copy-tab-focus-320.png", "copy-tab-focus-1440.png",
                "copy-success-320.png", "copy-success-1440.png",
                "copy-failure-denied.png", "copy-failure-missing.png", "copy-failure-sync-throw.png",
            })
            for row in report["responses"]:
                self.assertEqual(row["status"], 200)
                self.assertEqual(row["payload"]["reproducibility"]["source_sha256"], server.RUNTIME_SOURCES)
                original = json.loads((out / "browser" / row["artifact"]).read_bytes())
                self.assertEqual(original["payload"], row["payload"])
                self.assertEqual(original["body_sha256"], row["body_sha256"])
            for row in report["screenshots"]:
                data = (out / "browser" / row["file"]).read_bytes()
                self.assertEqual(sha(data), row["sha256"])
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            write_new(out / "browser-artifacts.json", encode({
                "report_sha256": sha(report_file.read_bytes()),
                "log_sha256": sha((out / "browser/logs/chromium.log").read_bytes()),
                "module_sha256": sha(module.read_bytes()), "embedded_source_sha256": sha(BROWSER_SOURCE.encode()),
                "expected_preview_sha256": sha(expected_file.read_bytes()),
                "posts": [{"file": row["artifact"], "sha256": sha((out / "browser" / row["artifact"]).read_bytes())}
                          for row in report["responses"]],
                "screenshots": report["screenshots"],
            }))
        finally:
            app.shutdown()
            app.server_close()
            worker.join(10)
            logging.getLogger().removeHandler(handler)
            handler.close()
            self.assertFalse(worker.is_alive(), "Owned test server did not stop")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--method", action="append", choices=unittest.defaultTestLoader.getTestCaseNames(CopyControlTests))
    args = parser.parse_args()
    out = args.out.absolute()
    if ".." in args.out.parts or not out.is_relative_to(EXTERNAL) or out == EXTERNAL:
        parser.error("Fresh output must be a child of the fixed external root")
    out.mkdir(parents=True, exist_ok=False)
    CopyControlTests.out = out
    before = inventory()
    write_new(out / "run-start.json", encode({"started_at": datetime.now(timezone.utc).isoformat(),
              "source_before": before, "pid": os.getpid(), "methods": args.method,
              "evidence_sha256": sha(EVIDENCE.read_bytes())}))
    suite = unittest.TestSuite(CopyControlTests(name) for name in args.method) if args.method else unittest.defaultTestLoader.loadTestsFromTestCase(CopyControlTests)
    stream = io.StringIO()
    start = time.monotonic()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    after = inventory()
    rc = 0 if result.wasSuccessful() and result.testsRun > 0 and not result.skipped and before == after else 1
    write_new(out / "tests.log", stream.getvalue().encode() + f"\nACTUAL_RUNNER_RC={rc}\n".encode())
    report = {"schema": "opensim-copy-control-tests-v1", "verdict": "PASS" if rc == 0 else "FAIL",
              "scope": "REAL_LOOPBACK_COPY_NOT_RELEASE", "release_verdict": "NOT_CERTIFIED", "certified": False,
              "actual_runner_rc": rc, "tests": result.testsRun, "failures": len(result.failures),
              "errors": len(result.errors), "skipped": len(result.skipped), "seconds": time.monotonic() - start,
              "source_before": before, "source_after": after, "source_stable": before == after,
              "children": CopyControlTests.children, "finished_at": datetime.now(timezone.utc).isoformat()}
    write_new(out / "report.json", encode(report))
    verdict = report["verdict"]
    print(f"COPY_CONTROL_TEST verdict={verdict} rc={rc} out={out}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
