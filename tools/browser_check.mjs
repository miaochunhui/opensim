#!/usr/bin/env node
/**
 * OpenSim v1 real Chromium acceptance, without installing dependencies.
 *
 * node tools/browser_check.mjs --url http://127.0.0.1:8772/ \
 *   --out /hf3fs-jd/data/tmp/opensim-fluid-v1/browser
 * node tools/browser_check.mjs --selftest \
 *   --out /hf3fs-jd/data/tmp/opensim-fluid-v1/browser
 * --probe-input with --url runs only the 390px solve/export/input regression.
 * --probe-screenshot with --url tests only full 200% text screenshot coverage.
 *
 * Requires Node >= 22 (native WebSocket) or existing ws, and local Chromium.
 * Each invocation keeps an immutable run directory, including failed runs.
 * Exit 0: selected AUTOMATED checks passed; 1: acceptance failed;
 * 2: configuration/dependency/browser/harness failed.
 * AX inspection is NOT a human screen-reader or physical touch-device test.
 * A selftest fixture is NOT a FEM solver or an OpenSim acceptance result.
 *
 * DIAGNOSIS 2026-10-10: after a completed export, a Chromium touch activation
 * could give #pressure_pa DOM focus without a working number-editor selection:
 * Ctrl+A selected the page, Backspace left "2", and inserting "-1" yielded "".
 * This is a harness editing-focus defect, not proof of an application failure.
 * Explicit CDP editor commands alone also failed the new clear-value assertion.
 * Fix: actual touch activation followed by real Tab / Shift+Tab traversal,
 * ordinary Ctrl+A / Backspace, and a verified empty value before insertText.
 * This is touch activation + KEYBOARD editing, NOT pure-touch-device evidence.
 * No DOM focus/value assignment, input retries, or timeout change is used.
 * Evidence root: /hf3fs-jd/data/tmp/opensim-fluid-v1/browser/
 * Baseline failure: run-2026-10-10T06-36-17-927Z-f547adb3/report.json
 * Instrumented reproduction: run-2026-10-10T06-38-33-425Z-e5d2e418/report.json
 * Editor-command-only failure: run-2026-10-10T06-39-11-338Z-797c6ec4/report.json
 * Fix without editor commands: run-2026-10-10T06-40-07-574Z-54493e51/report.json
 * Selftest: run-2026-10-10T06-40-07-537Z-405adf44/report.json
 * These narrow probes do not certify the full viewport matrix or release gates.
 *
 * DIAGNOSIS readiness: run-2026-10-10T06-41-48-685Z-663c96b9 failed
 * text-200 configuration because navigation waited for DOM/fonts but not
 * automatic FEM completion. Reset also previously checked only input values.
 * Navigation now requires enabled form controls and aria-busy=false; if an
 * auto-solve was observed it must finish successfully. Reset requires its new
 * HTTP result, enabled export, non-stale metrics and completed form state.
 *
 * DIAGNOSIS capture: run-2026-10-10T06-49-45-624Z-e5fa45c7 measured a
 * finite 345x25463 CSS page. A hard-coded 20000px height cap incorrectly
 * classified it as unbounded. Full coverage is now captured in bounded tiles
 * without scaling, hiding content or changing layout. Tiles are sequential
 * with a 16 MiB RGBA allocation bound, not a document-height guess.
 * captureBeyondViewport also changed 345x25463 into 360x23597 in probe
 * run-2026-10-10T06-52-45-825Z-fad77c29, so viewport crops now scroll through
 * the unchanged page and restore the original scroll position in finally.
 * Probe run-2026-10-10T06-53-51-103Z-235f11b5 captured 32 complete tiles.
 * A coverage manifest verifies every original pixel; this is capture evidence
 * only, not a text-layout PASS or a full viewport-matrix certification.
 */
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { EventEmitter, once } from 'node:events';
import * as fs from 'node:fs/promises';
import http from 'node:http';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUTPUT_ROOT = '/hf3fs-jd/data/tmp/opensim-fluid-v1/browser';
const DEFAULT_CHROME = '/home/mch/.cache/ms-playwright/chromium-1187/chrome-linux/chrome';
const CAPTURE_TILE_BYTES = 16 * 1024 * 1024;
const CAPTURE_TILE_AXIS = 16384;
const MATRIX = [
  ['mobile-narrow', 320, 568, true], ['mobile-common', 360, 800, true],
  ['mobile-wide', 390, 844, true], ['tablet-portrait', 768, 1024, true],
  ['desktop-small', 1024, 768, false], ['desktop-browser-common', 1064, 768, false],
  ['desktop-standard', 1280, 800, false], ['desktop-wide', 1440, 900, false],
];
const DEFAULT_CONFIG = {
  geometry: 'circle', width_mm: 4, height_mm: 4, length_mm: 100,
  pressure_pa: 1, viscosity_pa_s: 0.001, density_kg_m3: 998, resolution: 16,
};
const METRICS = [
  'node_count', 'element_count', 'dof_count', 'max_velocity_m_s',
  'mean_velocity_m_s', 'flow_rate_m3_s', 'reynolds', 'hydraulic_diameter_mm',
  'relative_residual', 'relative_l2_error', 'reference_flow_rate_m3_s',
  'relative_flow_error', 'force_balance_relative_error',
];
const CONTRACT = {
  profile: 'data-canvas', shell: 'body', main: '#workspace',
  primary: '#flow-canvas', secondary: '.parameter-panel', prose: '#result-summary',
};
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const digest = data => createHash('sha256').update(data).digest('hex');
const json = value => JSON.stringify(value, null, 2) + '\n';

function persistenceCause(error, step) {
  return { rule: "BROWSER-PERSISTENCE", message: error.message, stack: error.stack,
    code: error.code ?? null, syscall: error.syscall ?? null,
    path: error.path ?? step.path, stage: step.stage,
    close_failures: error.closeFailures ?? [] };
}

async function useHandle(handle, action) {
  let first;
  try { return await action(handle); }
  catch (error) { first = error; throw error; }
  finally {
    try { await handle.close(); }
    catch (error) {
      if (!first) throw error;
      (first.closeFailures ??= []).push({ message: error.message, code: error.code ?? null,
        syscall: error.syscall ?? null, path: error.path ?? null });
    }
  }
}

async function syncDirectory(directory, step) {
  step.stage = "directory-fsync"; step.path = directory;
  const handle = await fs.open(directory, fs.constants.O_RDONLY | fs.constants.O_DIRECTORY | fs.constants.O_NOFOLLOW);
  await useHandle(handle, file => file.sync());
}

async function readDescriptor(handle, target, step) {
  step.stage = "exact-readback"; step.path = target;
  const before = await handle.stat();
  assert(before.isFile() && Number.isSafeInteger(before.size) && before.size <= 16 * 1024 * 1024,
    `Evidence is not a bounded regular file: ${target}`);
  const bytes = Buffer.alloc(before.size + 1);
  let size = 0;
  while (size < bytes.length) {
    const result = await handle.read(bytes, size, bytes.length - size, size);
    if (!result.bytesRead) break;
    size += result.bytesRead;
  }
  const after = await handle.stat(), named = await fs.lstat(target);
  const same = info => info.isFile() && info.dev === before.dev && info.ino === before.ino &&
    info.birthtimeMs === before.birthtimeMs && info.size === before.size &&
    info.mtimeMs === before.mtimeMs && info.ctimeMs === before.ctimeMs;
  assert(same(after) && same(named) && size === before.size, `Evidence changed during readback: ${target}`);
  return { stat: after, bytes: bytes.subarray(0, size) };
}

async function exactRead(target, entry, step) {
  step.stage = "exact-readback"; step.path = target;
  const expected = Buffer.from(entry.bytes);
  assert(expected.length <= 16 * 1024 * 1024, "Final evidence exceeds bounded readback limit");
  const handle = await fs.open(target, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  await useHandle(handle, async file => {
    const actual = await readDescriptor(file, target, step);
    assert(actual.stat.dev === entry.stat.dev && actual.stat.ino === entry.stat.ino &&
      actual.stat.birthtimeMs === entry.stat.birthtimeMs && actual.bytes.equals(expected),
    `Evidence exact inode/bytes changed: ${target}`);
  });
}

async function createDurable(target, bytes, owned, step) {
  step.stage = "exclusive-open"; step.path = target;
  const handle = await fs.open(target, fs.constants.O_WRONLY | fs.constants.O_CREAT |
    fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
  await useHandle(handle, async file => {
    owned.set(target, { stat: await file.stat(), bytes });
    step.stage = "file-write";
    await file.writeFile(bytes);
    step.stage = "file-fsync";
    await file.sync();
  });
  await syncDirectory(path.dirname(target), step);
  await exactRead(target, owned.get(target), step);
}

export async function finalizeBrowser(run, report, rc, fixture = null) {
  const step = {}, owned = new Map(), target = path.join(run, "report.json");
  const log = path.join(run, "run.log"), protocol = "browser-durable-final-v1";
  try {
    await createDurable(path.join(run, "report.provisional.json"), json({ ...report,
      verdict: "PROVISIONAL_NOT_FINAL", exit_code: 2,
      durability: { protocol, state: "PROVISIONAL_NOT_CONSUMABLE", candidate_exit_code: rc } }), owned, step);
    if (fixture === "log-directory") {
      await fs.rename(log, path.join(run, "preserved-run.log"));
      await fs.mkdir(log);
    }
    step.stage = "log-open"; step.path = fixture === "log-enospc" ? "/dev/full" : log;
    const handle = await fs.open(fixture === "log-enospc" ? "/dev/full" : log,
      fs.constants.O_RDWR | fs.constants.O_APPEND | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
    const logEntry = await useHandle(handle, async file => {
      step.stage = "log-write"; step.path = fixture === "log-enospc" ? "/dev/full" : log;
      await file.writeFile(`${report.finished_at} BROWSER_CHECK candidate_verdict=${report.verdict} candidate_rc=${rc} report=${target}\n`);
      step.stage = "log-fsync";
      if (fixture === "log-fsync") {
        step.path = "/dev/full";
        const device = await fs.open("/dev/full", "r");
        await useHandle(device, item => item.sync());
      }
      await file.sync();
      return readDescriptor(file, log, step);
    });
    await syncDirectory(run, step);
    await exactRead(log, logEntry, step);
    report.exit_code = rc;
    report.durability = { protocol, state: "FINAL_AFTER_DURABLE_LOG",
      log: { file: "run.log", bytes: logEntry.bytes.length, sha256: digest(logEntry.bytes) } };
    await createDurable(target, json(report), owned, step);
    if (fixture === "post-publish-fsync") {
      step.stage = "post-publish-fsync"; step.path = "/dev/full";
      const device = await fs.open("/dev/full", "r");
      await useHandle(device, item => item.sync());
    }
    if (fixture === "same-inode") {
      const before = await fs.lstat(target), entry = owned.get(target);
      const bytes = entry.bytes.replace(/"exit_code": 0/, "\"exit_code\": 9");
      assert.notEqual(bytes, entry.bytes, "Same-inode fixture needs a successful candidate");
      await fs.writeFile(target, bytes);
      const after = await fs.lstat(target);
      assert.equal(before.ino, after.ino); assert.equal(before.size, after.size);
      report.same_inode_fixture = { before_ino: before.ino, after_ino: after.ino,
        preserved_sha256: digest(bytes), bytes: after.size };
    }
    if (fixture === "replaced-directory") {
      await fs.rename(target, path.join(run, "preserved-report.json"));
      await fs.mkdir(target);
      await fs.writeFile(path.join(target, "unrelated.txt"), "unknown bytes preserved\n", { flag: "wx" });
    }
    await exactRead(log, logEntry, step);
    await exactRead(target, owned.get(target), step);
    return { rc, path: target };
  } catch (error) {
    const failure = persistenceCause(error, step);
    report.first_failure ??= report.failure ?? report.cleanup_failure ?? failure;
    report.persistence_failure = failure;
    report.verdict = "FAIL"; report.exit_code = 2;
    report.durability = { protocol, state: "FAILED_NOT_CONSUMABLE", revocations: [], secondary_failures: [] };
    if (owned.has(target)) {
      try {
        await exactRead(target, owned.get(target), step);
        // POSIX lacks compare-and-rename; unique run ownership is required.
        const preserved = path.join(run, `revoked-owned-${randomUUID()}.json`);
        await fs.link(target, preserved);
        await exactRead(target, owned.get(target), step);
        await fs.unlink(target);
        report.durability.revocations.push({ status: "EXACT_OWNED_REVOKED", path: target, preserved,
          sha256: digest(owned.get(target).bytes) });
        await syncDirectory(run, step);
      } catch (secondary) {
        report.durability.revocations.push({ status: "UNKNOWN_OR_CHANGED_NOT_REMOVED", path: target });
        report.durability.secondary_failures.push(persistenceCause(secondary, step));
      }
    }
    const marker = path.join(run, "report.failure.json");
    let markerWritten = false;
    try { await createDurable(marker, json(report), new Map(), step); markerWritten = true; }
    catch (secondary) {
      report.durability.secondary_failures.push(persistenceCause(secondary, step));
      console.error(`BROWSER_CHECK FAILURE_MARKER_UNAVAILABLE ${json(report)}`);
    }
    console.error(`BROWSER_CHECK FAIL stage=${failure.stage} code=${failure.code} path=${failure.path} ${failure.message}`);
    return { rc: 2, path: markerWritten ? marker : null };
  }
}

class CheckFailure extends Error {
  constructor(rule, message, detail = {}) {
    super(message);
    this.rule = rule;
    this.detail = detail;
  }
}
function requireCheck(condition, rule, message, detail = {}) {
  if (!condition) throw new CheckFailure(rule, message, detail);
}
function relativeClose(actual, expected, tolerance = 1e-6) {
  return Number.isFinite(actual) && Number.isFinite(expected) &&
    Math.abs(actual - expected) <= Math.max(1e-15, Math.abs(expected) * tolerance);
}
function validatePng(buffer) {
  assert(buffer.length > 100, 'Empty/truncated screenshot');
  assert(buffer.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])));
  assert.equal(buffer.toString('ascii', 12, 16), 'IHDR');
  const dimensions = { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
  assert(dimensions.width > 0 && dimensions.height > 0, 'Empty screenshot dimensions');
  return { ...dimensions, bytes: buffer.length, sha256: digest(buffer) };
}
function capturePlan(content, dpr, viewport) {
  requireCheck(content && ['x', 'y', 'width', 'height'].every(key => Number.isFinite(content[key])) &&
    content.width > 0 && content.height > 0 && Number.isFinite(dpr) && dpr > 0,
  'SCREENSHOT', 'Empty/nonfinite screenshot content or DPR', { content, dpr });
  const width = Math.ceil(content.width), height = Math.ceil(content.height);
  const pixelWidth = Math.ceil(width * dpr), pixelHeight = Math.ceil(height * dpr);
  const rawBytes = pixelWidth * pixelHeight * 4;
  requireCheck(Number.isSafeInteger(rawBytes) && rawBytes > 0,
    'SCREENSHOT-BUDGET', 'Full-resolution screenshot has unsafe pixel dimensions',
    { content, dpr, raw_bytes: rawBytes });
  requireCheck(viewport && Number.isFinite(viewport.width) && viewport.width > 0 &&
    Number.isFinite(viewport.height) && viewport.height > 0,
  'SCREENSHOT', 'Empty/nonfinite capture viewport', { viewport });
  const tileWidth = Math.min(width, Math.floor(viewport.width), Math.floor(CAPTURE_TILE_AXIS / dpr));
  const tileHeight = Math.min(height, Math.floor(viewport.height), Math.floor(CAPTURE_TILE_AXIS / dpr),
    Math.floor(CAPTURE_TILE_BYTES / (Math.ceil(tileWidth * dpr) * 4 * dpr)));
  requireCheck(tileWidth > 0 && tileHeight > 0, 'SCREENSHOT-BUDGET', 'DPR leaves no valid screenshot tile');
  const tiles = [];
  for (let y = 0; y < height; y += tileHeight) {
    for (let x = 0; x < width; x += tileWidth) {
      tiles.push({ x: content.x + x, y: content.y + y,
        width: Math.min(tileWidth, width - x), height: Math.min(tileHeight, height - y), scale: 1 });
    }
  }
  return { content, width, height, dpr, viewport, raw_bytes: rawBytes, tiles,
    budget: { tile_rgba_bytes: CAPTURE_TILE_BYTES, tile_pixel_axis: CAPTURE_TILE_AXIS,
      storage: 'sequential tiles; no full-page bitmap allocated' } };
}
function validateCoverage(plan, artifacts) {
  requireCheck(artifacts.length === plan.tiles.length && artifacts.length > 0,
    'SCREENSHOT-COVERAGE', 'Missing screenshot tiles');
  let area = 0;
  for (let index = 0; index < artifacts.length; index++) {
    const expected = plan.tiles[index], artifact = artifacts[index];
    requireCheck(JSON.stringify(artifact.clip) === JSON.stringify(expected) &&
      artifact.width === Math.ceil(expected.width * plan.dpr) &&
      artifact.height === Math.ceil(expected.height * plan.dpr),
    'SCREENSHOT-COVERAGE', 'Screenshot crop or PNG dimensions disagree with coverage plan',
    { expected, artifact, dpr: plan.dpr });
    area += expected.width * expected.height;
  }
  requireCheck(area === plan.width * plan.height, 'SCREENSHOT-COVERAGE', 'Incomplete screenshot area',
    { area, expected: plan.width * plan.height });
  return { status: 'PASS', source_css_pixels: area, tiles: artifacts.length, scaled: false,
    gaps: 0, overlaps: 0 };
}
function validateResult(result, expected) {
  requireCheck(result && typeof result === 'object', 'FEM-CONTRACT', 'Empty solver result');
  const finiteTree = (value, location) => {
    if (typeof value === 'number') {
      requireCheck(Number.isFinite(value), 'FEM-FINITE', `Nonfinite ${location}`);
    } else if (Array.isArray(value)) {
      value.forEach((item, index) => finiteTree(item, `${location}[${index}]`));
    } else if (value && typeof value === 'object') {
      Object.entries(value).forEach(([key, item]) => finiteTree(item, `${location}.${key}`));
    }
  };
  finiteTree(result, 'result');
  requireCheck(result.schema_version === 1, 'FEM-CONTRACT', 'Wrong schema_version');
  requireCheck(JSON.stringify(Object.keys(result.config ?? {}).sort()) ===
    JSON.stringify(Object.keys(DEFAULT_CONFIG).sort()), 'FEM-CONTRACT', 'Missing/extra config keys');
  for (const [key, value] of Object.entries(expected)) {
    requireCheck(result.config[key] === value, 'FEM-CURRENT-CONFIG', `Stale result.config.${key}`,
      { actual: result.config[key], expected: value });
  }
  const { mesh, fields, metrics, model, reference, warnings } = result;
  requireCheck(model && typeof model.name === 'string' &&
    /P1|linear|线性/i.test(model.discretization ?? '') &&
    /triang|三角/i.test(model.discretization ?? '') &&
    Array.isArray(model.assumptions) && model.assumptions.length > 0,
  'FEM-CONTRACT', 'Model must disclose P1 triangles and assumptions', { model });
  requireCheck(Array.isArray(warnings) && reference && typeof reference.method === 'string',
    'FEM-CONTRACT', 'Missing reference or warnings');
  const nodes = mesh?.nodes_mm, triangles = mesh?.triangles;
  const boundary = mesh?.boundary_nodes, velocity = fields?.velocity_m_s;
  requireCheck(Array.isArray(nodes) && nodes.length > 3 &&
    nodes.every(node => Array.isArray(node) && node.length === 2 && node.every(Number.isFinite)),
  'FEM-MESH', 'Empty/invalid FEM nodes');
  requireCheck(Array.isArray(triangles) && triangles.length > 1 &&
    Array.isArray(boundary) && boundary.length > 2 &&
    Array.isArray(velocity) && velocity.length === nodes.length &&
    Array.isArray(reference.velocity_m_s) && reference.velocity_m_s.length === nodes.length,
  'FEM-MESH', 'Missing/mismatched triangles, boundary or nodal fields');
  requireCheck(new Set(boundary).size === boundary.length &&
    boundary.every(index => Number.isInteger(index) && index >= 0 && index < nodes.length),
  'FEM-MESH', 'Invalid boundary indices');
  let area = 0, integratedFlow = 0;
  for (const triangle of triangles) {
    requireCheck(Array.isArray(triangle) && triangle.length === 3 &&
      new Set(triangle).size === 3 &&
      triangle.every(index => Number.isInteger(index) && index >= 0 && index < nodes.length),
    'FEM-MESH', 'Invalid triangle connectivity', { triangle });
    const [a, b, c] = triangle.map(index => nodes[index]);
    const elementArea = Math.abs((b[0] - a[0]) * (c[1] - a[1]) -
      (c[0] - a[0]) * (b[1] - a[1])) / 2e6;
    requireCheck(elementArea > 0, 'FEM-MESH', 'Degenerate triangle', { triangle });
    area += elementArea;
    integratedFlow += elementArea * triangle.reduce((sum, index) => sum + velocity[index], 0) / 3;
  }
  for (const key of METRICS) {
    requireCheck(typeof metrics?.[key] === 'number' && Number.isFinite(metrics[key]) &&
      metrics[key] >= 0, 'FEM-METRICS', `Missing/nonfinite/negative metrics.${key}`);
  }
  requireCheck(metrics.node_count === nodes.length && metrics.element_count === triangles.length &&
    metrics.dof_count === nodes.length - boundary.length && metrics.dof_count > 0,
  'FEM-METRICS', 'FEM counts disagree with mesh');
  const maximum = Math.max(...velocity);
  requireCheck(maximum > 0 && velocity.every(value => Number.isFinite(value) && value >= -1e-12) &&
    boundary.every(index => Math.abs(velocity[index]) <= Math.max(1e-12, maximum * 1e-9)),
  'FEM-BOUNDARY', 'Invalid velocity/no-slip boundary');
  requireCheck(relativeClose(metrics.max_velocity_m_s, maximum) &&
    relativeClose(metrics.flow_rate_m3_s, integratedFlow) &&
    relativeClose(metrics.mean_velocity_m_s, integratedFlow / area) &&
    metrics.reynolds <= 2000 && metrics.relative_residual <= 1e-7,
  'FEM-METRICS', 'Metrics disagree with P1 nodal integration or residual/laminar boundary',
  { maximum, area_m2: area, integrated_flow_m3_s: integratedFlow, metrics });
  return { ...metrics, integrated_area_m2: area, integrated_flow_m3_s: integratedFlow };
}

function parseArgs(argv) {
  const options = { chrome: process.env.OPENSIM_CHROME ?? DEFAULT_CHROME, timeout: 30000 };
  for (let index = 0; index < argv.length; index++) {
    const argument = argv[index];
    if (['--selftest', '--help', '--no-sandbox', '--probe-input', '--probe-screenshot'].includes(argument)) {
      options[argument.slice(2).replaceAll('-', '_')] = true;
    } else {
      requireCheck(['--url', '--out', '--chrome', '--timeout-ms', '--finalize-fixture', '--view-fixture'].includes(argument),
        'CLI', `Unknown argument ${argument}`);
      requireCheck(argv[index + 1] && !argv[index + 1].startsWith('--'), 'CLI', `Missing ${argument}`);
      options[argument === '--timeout-ms' ? 'timeout' : argument.slice(2)] = argv[++index];
    }
  }
  options.timeout = Number(options.timeout);
  requireCheck(Number.isInteger(options.timeout) && options.timeout >= 1000 && options.timeout <= 120000,
    'CLI', '--timeout-ms must be an integer in 1000..120000');
  if (options.help) return options;
  requireCheck(options.out, 'CLI', '--out is required (evidence must be external)');
  const out = path.resolve(options.out);
  requireCheck(out === OUTPUT_ROOT || out.startsWith(OUTPUT_ROOT + path.sep),
    'OUTPUT-BOUNDARY', `--out must be inside ${OUTPUT_ROOT}`, { out });
  options.out = out;
  requireCheck(options.selftest || options.url, 'CLI', '--url is required unless --selftest');
  requireCheck(!(options.selftest && options.url), 'CLI', '--selftest cannot claim a target URL');
  requireCheck(!options["finalize-fixture"] || options.selftest &&
    ["log-directory", "log-enospc", "log-fsync", "post-publish-fsync", "same-inode", "replaced-directory"].includes(options["finalize-fixture"]),
  "CLI", "Finalization fixtures require --selftest and a known real filesystem fault");
  requireCheck(!options["view-fixture"] || options.selftest &&
    ["wrong-selection", "stuck-toggle", "capture-change"].includes(options["view-fixture"]),
  "CLI", "View fixtures require --selftest and a known DOM fault");
  requireCheck(!(options.selftest && options.probe_input), 'CLI', '--selftest and --probe-input are exclusive');
  requireCheck([options.selftest, options.probe_input, options.probe_screenshot].filter(Boolean).length <= 1,
    'CLI', 'Selftest and narrow probes are mutually exclusive');
  if (options.url) {
    const url = new URL(options.url);
    requireCheck(url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) &&
      !url.username && !url.password, 'LOCAL-ONLY', 'Only credential-free loopback HTTP targets are authorized');
    options.url = url.href;
  }
  return options;
}

class CDP extends EventEmitter {
  constructor(socket, timeout) {
    super();
    this.socket = socket;
    this.timeout = timeout;
    this.sequence = 0;
    this.pending = new Map();
    socket.addEventListener('message', event => {
      const message = JSON.parse(typeof event.data === 'string' ? event.data : event.data.toString());
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        clearTimeout(pending.timer);
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(`${pending.method}: ${JSON.stringify(message.error)}`));
        else pending.resolve(message.result);
      } else {
        this.emit(message.method, message.params ?? {}, message.sessionId);
      }
    });
    const rejectPending = error => {
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timer);
        pending.reject(error);
      }
      this.pending.clear();
    };
    socket.addEventListener('close', () => rejectPending(new Error('Chromium CDP socket closed')));
    socket.addEventListener('error', event => rejectPending(new Error(`CDP socket: ${event.message ?? 'error'}`)));
  }
  send(method, params = {}, sessionId) {
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP command timed out (${this.timeout}ms): ${method}, session=${sessionId ?? 'browser'}`));
      }, this.timeout);
      this.pending.set(id, { resolve, reject, timer, method });
      this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }
}
async function websocketConstructor() {
  if (typeof globalThis.WebSocket === 'function') return { Constructor: globalThis.WebSocket, source: 'node-native' };
  const require = createRequire(import.meta.url);
  for (const candidate of ['ws', '/usr/share/nodejs/ws/index.js']) {
    try {
      return { Constructor: require(candidate), source: require.resolve(candidate) };
    } catch (error) {
      if (error.code !== 'MODULE_NOT_FOUND') throw error;
    }
  }
  throw new Error('Missing native WebSocket and local ws. Dependency installation is forbidden.');
}

class Harness {
  constructor(options, run, report) {
    this.options = options;
    this.run = run;
    this.report = report;
    this.responses = [];
    this.responseTasks = [];
    this.requests = new Map();
    this.errors = [];
    this.downloads = [];
    this.chromiumLog = '';
    this.sequence = 0;
    this.coordinate = 'startup';
  }
  log(message) {
    const line = `${new Date().toISOString()} ${message}\n`;
    this.logTask = (this.logTask ?? Promise.resolve()).then(() =>
      fs.appendFile(path.join(this.run, 'run.log'), line));
    console.log(message);
  }
  async check(rule, name, action) {
    this.log(`CHECK ${this.coordinate} ${name}`);
    try {
      const detail = await action();
      // Full mesh/fields already have dedicated HTTP artifacts; do not multiply
      // them inside the summary report for every viewport and recovery solve.
      const summary = detail && typeof detail === 'object' && Object.hasOwn(detail, 'result') ?
        Object.fromEntries(Object.entries(detail).filter(([key]) => key !== 'result')) : detail;
      this.report.checks.push({ rule, name, coordinate: this.coordinate, status: 'PASS', detail: summary });
      return detail;
    } catch (error) {
      const failure = {
        rule: error.rule ?? rule, name, coordinate: this.coordinate, status: 'FAIL',
        message: error.message, detail: error.detail ?? {}, stack: error.stack,
      };
      this.report.checks.push(failure);
      throw error;
    }
  }
  send(method, params) { return this.cdp.send(method, params, this.session); }
  async evaluate(fn, ...args) {
    const expression = typeof fn === 'function' ?
      `(${fn.toString()})(...${JSON.stringify(args)})` : fn;
    const response = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (response.exceptionDetails) {
      throw new Error(`Browser evaluation: ${response.exceptionDetails.exception?.description ??
        response.exceptionDetails.text}`);
    }
    return response.result.value;
  }
  async waitFor(label, inspect) {
    const start = Date.now();
    let last;
    while (Date.now() - start < this.options.timeout) {
      if (this.chrome.exitCode !== null) throw new Error(`Chromium exited ${this.chrome.exitCode}: ${this.chromiumLog}`);
      if (this.errors.length) throw new CheckFailure('BROWSER-RUNTIME', 'Browser/network error', { errors: this.errors });
      last = await inspect();
      if (last) return last;
      await delay(80);
    }
    throw new CheckFailure('WAIT-FAIL', `${label} did not complete in ${this.options.timeout}ms`,
      { coordinate: this.coordinate, last });
  }
  async launch() {
    await fs.access(this.options.chrome, fs.constants.X_OK);
    const socketImplementation = await websocketConstructor();
    this.report.dependencies = {
      node: process.version, websocket: socketImplementation.source, chromium: this.options.chrome,
      installation: 'none', chromium_sandbox: this.options.no_sandbox ? 'explicitly-disabled' : 'enabled',
    };
    const profile = path.join(this.run, 'chromium-profile');
    const flags = [
      '--headless=new', '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
      `--user-data-dir=${profile}`, '--window-size=1280,800', '--no-first-run',
      '--no-default-browser-check', '--disable-background-networking', '--disable-component-update',
      '--disable-default-apps', '--disable-breakpad', '--password-store=basic', 'about:blank',
    ];
    if (this.options.no_sandbox) flags.unshift('--no-sandbox');
    this.chrome = spawn(this.options.chrome, flags, { stdio: ['ignore', 'pipe', 'pipe'] });
    const chunks = [];
    const collect = data => {
      chunks.push(Buffer.from(data));
      this.chromiumLog = Buffer.concat(chunks).toString('utf8').slice(-16000);
    };
    this.chrome.stdout.on('data', collect);
    this.chrome.stderr.on('data', collect);
    this.rawChromiumChunks = chunks;
    let spawnError;
    this.chrome.on('error', error => { spawnError = error; });
    let portInfo;
    await this.waitFor('Chromium DevTools endpoint', async () => {
      if (spawnError) throw spawnError;
      try {
        portInfo = await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8');
        return portInfo.trim().split('\n').length === 2;
      } catch (error) {
        if (error.code === 'ENOENT') return false;
        throw error;
      }
    });
    const [port, endpoint] = portInfo.trim().split('\n');
    requireCheck(/^\d+$/.test(port) && endpoint.startsWith('/devtools/browser/'),
      'BROWSER-CDP', 'Invalid DevToolsActivePort');
    const socket = new socketImplementation.Constructor(`ws://127.0.0.1:${port}${endpoint}`);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('CDP socket connection timed out')), this.options.timeout);
      socket.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
      socket.addEventListener('error', event => {
        clearTimeout(timer);
        reject(new Error(`CDP socket connect failed: ${event.message ?? 'error'}`));
      }, { once: true });
    });
    this.cdp = new CDP(socket, this.options.timeout);
    this.report.browser = await this.cdp.send('Browser.getVersion');
    const { targetId } = await this.cdp.send('Target.createTarget', { url: 'about:blank' });
    const attached = await this.cdp.send('Target.attachToTarget', { targetId, flatten: true });
    this.session = attached.sessionId;
    await this.send('Page.enable');
    await this.send('Runtime.enable');
    await this.send('Network.enable');
    await this.send('Accessibility.enable');
    await this.send('Network.setCacheDisabled', { cacheDisabled: true });
    await this.send('Page.setDownloadBehavior', { behavior: 'allow', downloadPath: path.join(this.run, 'downloads') });
    await this.cdp.send('Browser.setDownloadBehavior', {
      behavior: 'allowAndName', downloadPath: path.join(this.run, 'downloads'), eventsEnabled: true,
    });
    this.cdp.on('Browser.downloadWillBegin', event => {
      this.downloads.push({ ...event, state: 'inProgress', coordinate: this.coordinate });
    });
    this.cdp.on('Browser.downloadProgress', event => {
      Object.assign(this.downloads.find(item => item.guid === event.guid) ?? {}, event);
    });
    this.cdp.on('Runtime.exceptionThrown', (event, session) => {
      if (session === this.session) this.errors.push({
        type: 'exception', coordinate: this.coordinate,
        message: event.exceptionDetails.exception?.description ?? event.exceptionDetails.text,
      });
    });
    this.cdp.on('Runtime.consoleAPICalled', (event, session) => {
      if (session === this.session && event.type === 'error') this.errors.push({
        type: 'console-error', coordinate: this.coordinate,
        message: event.args.map(argument => argument.value ?? argument.description).join(' '),
      });
    });
    this.cdp.on('Network.requestWillBeSent', (event, session) => {
      if (session === this.session) this.requests.set(event.requestId, {
        url: event.request.url, method: event.request.method, body: event.request.postData,
        coordinate: this.coordinate, start_ms: Date.now(),
      });
    });
    this.cdp.on('Network.responseReceived', (event, session) => {
      if (session === this.session && this.requests.has(event.requestId)) {
        const request = this.requests.get(event.requestId);
        request.status = event.response.status;
        request.mime = event.response.mimeType;
        request.response_url = event.response.url;
      }
    });
    this.cdp.on('Network.loadingFailed', (event, session) => {
      const request = this.requests.get(event.requestId);
      if (session === this.session && !event.canceled && request &&
        !request.url.startsWith('blob:')) this.errors.push({
        type: 'network', url: request.url, message: event.errorText, coordinate: this.coordinate,
      });
    });
    this.cdp.on('Network.loadingFinished', (event, session) => {
      if (session !== this.session) return;
      const request = this.requests.get(event.requestId);
      if (!request) return;
      request.encoded_bytes = event.encodedDataLength;
      if (request.method !== 'POST' || new URL(request.url).pathname.split('/').pop() !== 'solve') return;
      const task = (async () => {
        const body = await this.send('Network.getResponseBody', { requestId: event.requestId });
        const text = body.base64Encoded ? Buffer.from(body.body, 'base64').toString('utf8') : body.body;
        const parsed = JSON.parse(text);
        const response = {
          ...request, duration_ms: Date.now() - request.start_ms,
          config: JSON.parse(request.body), result: parsed,
        };
        const filename = `solve-${String(++this.sequence).padStart(3, '0')}.json`;
        await fs.writeFile(path.join(this.run, filename), json(response), { flag: 'wx' });
        response.artifact = filename;
        this.responses.push(response);
      })().catch(error => {
        this.errors.push({ type: 'solve-capture', coordinate: request.coordinate, message: error.message });
      });
      this.responseTasks.push(task);
    });
    // Target content may not pull third-party code or contact production.
    await this.send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] });
    this.cdp.on('Fetch.requestPaused', (event, session) => {
      if (session !== this.session) return;
      const url = new URL(event.request.url);
      const allowed = url.origin === new URL(this.options.url).origin ||
        ['data:', 'blob:', 'about:'].includes(url.protocol);
      const task = this.send(allowed ? 'Fetch.continueRequest' : 'Fetch.failRequest',
        allowed ? { requestId: event.requestId } : { requestId: event.requestId, errorReason: 'BlockedByClient' });
      if (!allowed) this.errors.push({
        type: 'unauthorized-network', url: url.href, coordinate: this.coordinate,
      });
      this.responseTasks.push(task.catch(error => {
        this.errors.push({ type: 'request-interception', message: error.message, coordinate: this.coordinate });
      }));
    });
  }
  async viewport(width, height, touch = false, scale = 1, deviceScaleFactor = 1) {
    await this.send('Emulation.setDeviceMetricsOverride', {
      width, height, deviceScaleFactor, mobile: false, screenWidth: width, screenHeight: height,
    });
    await this.send('Emulation.setTouchEmulationEnabled', { enabled: touch, maxTouchPoints: 1 });
    await this.send('Emulation.setPageScaleFactor', { pageScaleFactor: scale });
    await this.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    this.touch = touch;
  }
  async navigate(url = this.options.url) {
    const response = await this.send('Page.navigate', { url });
    requireCheck(!response.errorText, 'BROWSER-NAVIGATION', response.errorText ?? 'Navigation failed');
    await this.waitFor('Visible nonempty page', async () => this.evaluate(() =>
      document.readyState === 'complete' && document.body?.innerText.trim().length > 30));
    await this.evaluate(async () => { await document.fonts.ready; });
    if (!this.options.selftest) await this.waitFormReady('Navigation form readiness');
    if (this.options.probe_input) await this.evaluate(() => {
      window.__opensimInputEvents = [];
      for (const type of ['touchstart', 'touchend', 'mousedown', 'mouseup', 'click', 'focusin',
        'focusout', 'keydown', 'keyup', 'beforeinput', 'input', 'change']) {
        document.addEventListener(type, event => {
          window.__opensimInputEvents.push({
            type, time_ms: performance.now(), target: event.target.id || event.target.tagName,
            active: document.activeElement?.id || document.activeElement?.tagName,
            value: event.target.value ?? null, key: event.key ?? null,
            data: event.data ?? null, trusted: event.isTrusted,
          });
          if (window.__opensimInputEvents.length > 500) window.__opensimInputEvents.shift();
        }, true);
      }
    });
  }
  async formState() {
    return this.evaluate(() => {
      const form = document.querySelector('#case-form');
      if (!form) throw new Error('Missing #case-form readiness contract');
      const selectors = ['#geometry', '#solve-button', '#reset-button', '#import-input'];
      const controls = [...new Set([...form.querySelectorAll('input,select,textarea,button'),
        ...selectors.map(selector => {
          const element = document.querySelector(selector);
          if (!element) throw new Error(`Missing readiness control ${selector}`);
          return element;
        })])];
      const error = document.querySelector('#error');
      if (!error) throw new Error('Missing #error readiness contract');
      const exportButton = document.querySelector('#export-button');
      const metric = document.querySelector('#metric-flow_rate_m3_s');
      const box = error.getBoundingClientRect();
      return {
        busy: form.getAttribute('aria-busy'),
        disabled: controls.filter(element => element.disabled).map(element => element.id || element.name),
        error: !error.hidden && box.width > 0 && box.height > 0 &&
          getComputedStyle(error).visibility !== 'hidden' ? error.textContent.trim() : '',
        export_enabled: !!exportButton && !exportButton.disabled,
        metric_text: metric?.textContent.trim() ?? '',
        status: document.querySelector('#status')?.textContent.trim() ?? '',
      };
    });
  }
  async waitFormReady(label, { requireResult = false, marker = null } = {}) {
    let initial, observedBusy = false;
    const started = Date.now();
    const outcome = await this.waitFor(label, async () => {
      const state = await this.formState();
      initial ??= state;
      observedBusy ||= state.busy === 'true' || state.disabled.length > 0;
      requireCheck(!state.error, 'FEM-UI-ERROR', `${label}: ${state.error}`, { state });
      const candidates = marker === null ? [] : this.responses.slice(marker);
      const response = candidates.at(-1);
      if (response) requireCheck(response.status === 200, 'FEM-HTTP',
        `${label}: HTTP ${response.status}`, { artifact: response.artifact, error: response.result.error });
      if (state.busy !== 'false' || state.disabled.length > 0) return false;
      if (marker !== null && !response) return false;
      if ((requireResult || observedBusy) && (!state.export_enabled ||
        !/[0-9]/.test(state.metric_text) || /未计算|旧结果/.test(state.metric_text))) return false;
      return { state, response };
    });
    this.report.readiness ??= [];
    this.report.readiness.push({
      label, coordinate: this.coordinate, initial, final: outcome.state,
      elapsed_ms: Date.now() - started, observed_busy: observedBusy,
      new_response_artifact: outcome.response?.artifact ?? null,
    });
    return outcome;
  }
  async key(key, code, modifiers = 0) {
    const definition = {
      Tab: [9, 'Tab'], Enter: [13, 'Enter'], Escape: [27, 'Escape'], Backspace: [8, 'Backspace'],
      ArrowDown: [40, 'ArrowDown'], ArrowUp: [38, 'ArrowUp'], Home: [36, 'Home'],
      End: [35, 'End'], a: [65, 'KeyA'], ' ': [32, 'Space'],
    }[key];
    requireCheck(definition, 'HARNESS-KEY', `Unknown key ${key}`);
    const params = { key, code: code ?? definition[1], windowsVirtualKeyCode: definition[0],
      nativeVirtualKeyCode: definition[0], modifiers };
    await this.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', ...params });
    if (key === 'Enter' || key === ' ') {
      await this.send('Input.dispatchKeyEvent', { type: 'char', ...params, text: key === 'Enter' ? '\r' : ' ' });
    }
    await this.send('Input.dispatchKeyEvent', { type: 'keyUp', ...params });
  }
  async focusInfo() {
    return this.evaluate(() => {
      const element = document.activeElement, rect = element.getBoundingClientRect();
      const style = getComputedStyle(element), viewport = window.visualViewport;
      const x = Math.max(0, Math.min(innerWidth - 1, rect.left + rect.width / 2));
      const y = Math.max(0, Math.min(innerHeight - 1, rect.top + rect.height / 2));
      const top = document.elementFromPoint(x, y);
      return {
        id: element.id, tag: element.tagName, text: element.textContent.trim().slice(0, 80),
        visible: rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0 &&
          rect.top < innerHeight && rect.left < innerWidth,
        uncovered: element === top || element.contains(top),
        outline: style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0,
        box_shadow: style.boxShadow !== 'none', border: style.borderStyle,
        focus_visible: element.matches(':focus-visible'), viewport_scale: viewport?.scale ?? 1,
      };
    });
  }
  async tabTo(selector) {
    const count = await this.evaluate(() => document.querySelectorAll(
      'a[href],button,input,select,textarea,[tabindex]:not([tabindex="-1"])').length);
    requireCheck(count > 0, 'WEB-A11Y-003', 'Empty keyboard target set');
    const traversal = [];
    for (let index = 0; index < count * 2 + 3; index++) {
      if (await this.evaluate(sel => document.activeElement.matches(sel), selector)) return traversal;
      await this.key('Tab');
      const info = await this.focusInfo();
      traversal.push(info);
      if (info.tag !== 'BODY') requireCheck(info.visible && info.uncovered &&
        (!info.focus_visible || info.outline || info.box_shadow),
      'WEB-A11Y-004', 'Keyboard focus hidden, covered or without a focus-visible indicator', { info, selector });
    }
    throw new CheckFailure('WEB-A11Y-003', `Tab cannot reach ${selector}`, { traversal });
  }
  async activate(selector, keyboard = false) {
    if (keyboard) {
      await this.tabTo(selector);
      await this.key('Enter');
      return;
    }
    const point = await this.evaluate(sel => {
      const element = document.querySelector(sel);
      if (!element) throw new Error(`Missing ${sel}`);
      if (element.disabled) throw new Error(`Disabled ${sel}`);
      element.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
      const rect = element.getBoundingClientRect(), visual = window.visualViewport;
      const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
      const top = document.elementFromPoint(x, y);
      if (!(top === element || element.contains(top))) throw new Error(`Covered ${sel}`);
      return { x: (x - (visual?.offsetLeft ?? 0)) * (visual?.scale ?? 1),
        y: (y - (visual?.offsetTop ?? 0)) * (visual?.scale ?? 1) };
    }, selector);
    if (this.touch) {
      await this.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ ...point, radiusX: 1, radiusY: 1 }] });
      await this.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    } else {
      await this.send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...point });
      await this.send('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...point });
      await this.send('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...point });
    }
  }
  async input(selector, value, keyboard = false) {
    const trace = { selector, expected: String(value), keyboard, touch: this.touch,
      coordinate: this.coordinate, stages: [] };
    const sample = async stage => {
      trace.stages.push({ stage, ...await this.evaluate(sel => {
        const element = document.querySelector(sel), active = document.activeElement;
        return {
          time_ms: performance.now(), value: element?.value, active: active?.id || active?.tagName,
          active_value: active?.value ?? null, focused: element === active,
          selected_text: getSelection()?.toString().slice(0, 80) ?? '',
          rect: element ? { x: element.getBoundingClientRect().x, y: element.getBoundingClientRect().y } : null,
          scroll_y: scrollY, document_focused: document.hasFocus(),
        };
      }, selector) });
    };
    const locked = await this.evaluate(sel => {
      const element = document.querySelector(sel);
      if (!element) throw new Error(`Missing ${sel}`);
      return element.readOnly ? { value: element.value } : null;
    }, selector);
    if (locked) {
      requireCheck(locked.value === String(value), 'BROWSER-INPUT',
        `Read-only ${selector} did not follow dependent parameter`, { actual: locked.value, expected: value });
      return;
    }
    if (keyboard) await this.tabTo(selector);
    else await this.activate(selector);
    await sample('after-activate');
    requireCheck(trace.stages.at(-1).focused, 'BROWSER-INPUT-FOCUS',
      `Real input activation did not focus ${selector}`, { trace });
    if (this.touch && !keyboard) {
      // A download can leave a number control's internal editor unfocused
      // despite activeElement matching. A real keyboard focus traversal
      // re-enters that editor without DOM focus(), value assignment or retries.
      await this.key('Tab');
      await this.key('Tab', undefined, 8);
      await sample('after-keyboard-focus-roundtrip');
      requireCheck(trace.stages.at(-1).focused, 'BROWSER-INPUT-FOCUS',
        `Tab/Shift+Tab did not return to ${selector}`, { trace });
    }
    await this.key('a', undefined, 2);
    await sample('after-select-all');
    await this.key('Backspace');
    await sample('after-backspace');
    requireCheck(trace.stages.at(-1).focused && trace.stages.at(-1).value === '',
      'BROWSER-INPUT-CLEAR', `Real key events did not clear ${selector}`, { trace });
    if (String(value)) await this.send('Input.insertText', { text: String(value) });
    await sample('after-insert');
    await this.key('Tab');
    await sample('after-tab');
    this.report.input_traces ??= [];
    this.report.input_traces.push(trace);
    const actual = trace.stages.at(-1).value;
    requireCheck(actual === String(value), 'BROWSER-INPUT',
      `Real input events did not set ${selector}: expected=${value}, actual=${actual}, active=${trace.stages.at(-1).active}`,
      { value, actual, trace });
  }
  async choose(selector, value, keyboard = false) {
    const index = await this.evaluate((sel, desired) =>
      [...document.querySelector(sel).options].findIndex(option => option.value === desired), selector, value);
    requireCheck(index >= 0, 'DOM-CONTRACT', `Missing ${selector} option ${value}`);
    if (keyboard) await this.tabTo(selector);
    else await this.activate(selector);
    await this.key('Home');
    for (let step = 0; step < index; step++) await this.key('ArrowDown');
    await this.key('Enter');
    await this.key('Tab');
    requireCheck(await this.evaluate((sel, desired) => document.querySelector(sel).value === desired,
      selector, value), 'BROWSER-INPUT', `Real select events did not choose ${value}`);
  }
  async config(config, keyboard = false) {
    await this.choose('#geometry', config.geometry, keyboard);
    for (const [key, value] of Object.entries(config)) {
      if (key !== 'geometry') await this.input(`#${key}`, value, keyboard);
    }
  }
  async viewState() {
    const state = await this.evaluate(() => Object.fromEntries(["field", "mesh"].map(view => {
      const controls = document.querySelectorAll(`#view-${view}`);
      return [view, { count: controls.length, pressed: controls[0]?.getAttribute("aria-pressed") ?? null }];
    })));
    if (Object.values(state).every(control => control.count === 0)) return { selected_view: "unavailable" };
    requireCheck(Object.values(state).every(control => control.count === 1 &&
      ["true", "false"].includes(control.pressed)), "BROWSER-VIEW", "Missing, duplicate or invalid view controls", { state });
    const field = state.field.pressed === "true", mesh = state.mesh.pressed === "true";
    return { field, mesh, selected_view: field ? (mesh ? "field+mesh" : "field") : (mesh ? "mesh" : "none") };
  }
  async confirmView(expected = null) {
    const state = await this.viewState();
    requireCheck(expected === null || ["field", "mesh"].includes(expected),
      "BROWSER-VIEW", "Unknown expected view", { expected });
    requireCheck(expected === null || state.selected_view === expected,
      "BROWSER-VIEW", "Actual selected view does not match capture expectation", { expected, ...state });
    return state;
  }
  async selectView(expected, keyboard = false) {
    requireCheck(["field", "mesh"].includes(expected), "BROWSER-VIEW", "Unknown requested view", { expected });
    let state = await this.viewState();
    requireCheck(state.selected_view !== "unavailable", "BROWSER-VIEW", "Cannot select missing view controls", { expected });
    // Independent toggles: clicking an already enabled control would turn it off.
    for (const view of ["field", "mesh"]) {
      if (state[view] === (view === expected)) continue;
      await this.activate(`#view-${view}`, keyboard);
      await this.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      state = await this.viewState();
      requireCheck(state[view] === (view === expected), "BROWSER-VIEW",
        "Real activation did not set requested view toggle", { expected, activated: view, ...state });
    }
    return this.confirmView(expected);
  }
  async screenshot(name, expectedView = null) {
    await this.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const view = await this.confirmView(expectedView);
    const confirmCaptureView = async () => {
      const actual = await this.confirmView(expectedView);
      requireCheck(actual.selected_view === view.selected_view, "BROWSER-VIEW",
        "Selected view changed during screenshot capture", { before: view, after: actual, capture: name });
    };
    const metrics = await this.send('Page.getLayoutMetrics');
    const content = metrics.cssContentSize ?? metrics.contentSize;
    const original = await this.evaluate(() => ({ dpr: devicePixelRatio, x: scrollX, y: scrollY,
      width: document.documentElement.clientWidth, height: innerHeight }));
    const plan = capturePlan(content, original.dpr, original);
    const artifacts = [];
    try {
      for (let index = 0; index < plan.tiles.length; index++) {
        const clip = plan.tiles[index];
        const position = await this.evaluate(async crop => {
          scrollTo({ left: crop.x, top: crop.y, behavior: 'instant' });
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          return { x: scrollX, y: scrollY, width: document.documentElement.clientWidth, height: innerHeight };
        }, clip);
        requireCheck(clip.x >= position.x && clip.y >= position.y &&
          clip.x + clip.width <= position.x + position.width + 1 &&
          clip.y + clip.height <= position.y + position.height + 1,
        'SCREENSHOT-COVERAGE', 'Planned crop is not visible at the actual scroll position', { clip, position });
        await confirmCaptureView();
        const response = await this.send('Page.captureScreenshot', {
          format: 'png', captureBeyondViewport: false, fromSurface: true, clip,
        });
        await confirmCaptureView();
        const buffer = Buffer.from(response.data, 'base64');
        const file = plan.tiles.length === 1 ? `${name}.png` :
          `${name}-tile-${String(index + 1).padStart(3, '0')}.png`;
        const artifact = { file, capture: name, coordinate: this.coordinate, selected_view: view.selected_view,
          clip, scroll: position, ...validatePng(buffer) };
        validateCoverage({ ...plan, tiles: [clip], width: clip.width, height: clip.height }, [artifact]);
        await fs.writeFile(path.join(this.run, file), buffer, { flag: 'wx' });
        const reread = await fs.readFile(path.join(this.run, file));
        requireCheck(digest(reread) === artifact.sha256, 'SCREENSHOT', 'Screenshot read-back hash mismatch', { file });
        artifacts.push(artifact);
        this.report.screenshots.push(artifact);
      }
    } finally {
      await this.evaluate(async position => {
        scrollTo({ left: position.x, top: position.y, behavior: 'instant' });
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      }, original);
    }
    await confirmCaptureView();
    const after = await this.send('Page.getLayoutMetrics');
    requireCheck(JSON.stringify(after.cssContentSize ?? after.contentSize) === JSON.stringify(content),
      'SCREENSHOT-COVERAGE', 'Page geometry changed while capturing full coverage',
      { before: content, after: after.cssContentSize ?? after.contentSize });
    const coverage = validateCoverage(plan, artifacts);
    const manifest = { name, coordinate: this.coordinate, selected_view: view.selected_view, ...plan, artifacts, coverage };
    const manifestFile = `${name}-capture.json`;
    await fs.writeFile(path.join(this.run, manifestFile), json(manifest), { flag: 'wx' });
    const reread = JSON.parse(await fs.readFile(path.join(this.run, manifestFile), 'utf8'));
    validateCoverage(reread, reread.artifacts);
    requireCheck(reread.selected_view === view.selected_view &&
      reread.artifacts.every(artifact => artifact.selected_view === view.selected_view),
    "BROWSER-VIEW", "Capture manifest selected view read-back mismatch", { manifestFile });
    this.report.captures ??= [];
    this.report.captures.push({ name, manifest: manifestFile, width: plan.width, height: plan.height,
      dpr: plan.dpr, selected_view: view.selected_view, ...coverage });
    return plan.tiles.length === 1 ? artifacts[0] : { manifest: manifestFile, ...coverage };
  }
  async canvas() {
    const result = await this.evaluate(() => {
      const canvas = document.querySelector('#flow-canvas'), context = canvas?.getContext('2d');
      if (!context || canvas.width <= 0 || canvas.height <= 0) return null;
      const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
      let nontransparent = 0;
      const colors = new Set();
      let hash = 2166136261;
      for (let index = 0; index < data.length; index += 4) {
        if (data[index + 3]) nontransparent++;
        colors.add(`${data[index]},${data[index + 1]},${data[index + 2]},${data[index + 3]}`);
        for (let channel = 0; channel < 4; channel++) {
          hash ^= data[index + channel];
          hash = Math.imul(hash, 16777619);
        }
      }
      return { width: canvas.width, height: canvas.height, nontransparent,
        distinct_colors: colors.size, pixel_hash: (hash >>> 0).toString(16) };
    });
    requireCheck(result && result.nontransparent > 10 && result.distinct_colors > 2,
      'REAL-RENDER', 'Canvas is missing, transparent, blank or uniform', { result });
    return result;
  }
  async solve(config, keyboard = false) {
    const marker = this.responses.length;
    await this.activate('#solve-button', keyboard);
    const response = await this.waitFor('Actual /api/solve response and visible metrics', async () => {
      const candidates = this.responses.slice(marker);
      if (!candidates.length) return false;
      const current = candidates.at(-1);
      requireCheck(current.status === 200, 'FEM-HTTP', 'Solver HTTP failure',
        { status: current.status, artifact: current.artifact, response: current.result });
      const state = await this.formState();
      requireCheck(!state.error, 'FEM-UI-ERROR', state.error, { state });
      return state.busy === 'false' && state.disabled.length === 0 && state.export_enabled &&
        /[0-9]/.test(state.metric_text) && !/未计算|旧结果/.test(state.metric_text) ? current : false;
    });
    const metrics = validateResult(response.result, config);
    const displayed = await this.evaluate(keys => Object.fromEntries(keys.map(key => {
      const element = document.querySelector(`#metric-${key}`);
      if (!element) throw new Error(`Missing metric-${key}`);
      return [key, { text: element.textContent.trim(), value: element.dataset.value ?? null }];
    })), METRICS);
    for (const key of METRICS) {
      const item = displayed[key];
      const raw = item.value ?? item.text.replaceAll(',', '').match(/[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/)?.[0];
      let value = Number(raw);
      requireCheck(raw !== undefined && Number.isFinite(value), 'FEM-DOM-METRICS', `Blank/nonfinite visible ${key}`, { item });
      if (item.value === null) {
        if (item.text.includes('%')) value /= 100;
        if (key === 'flow_rate_m3_s' || key === 'reference_flow_rate_m3_s') {
          if (/m[lL].*(min|分钟)/.test(item.text)) value /= 6e7;
          else if (/m[lL].*(\/s|秒)/.test(item.text)) value /= 1e6;
          else if (/\bL.*(min|分钟)/.test(item.text)) value /= 6e4;
        }
        if (key.endsWith('velocity_m_s') && /mm\s*(\/s|·s|每秒)/.test(item.text)) value /= 1000;
      }
      const mantissa = String(raw).split(/[eE]/)[0];
      const places = mantissa.includes('.') ? mantissa.split('.')[1].length : 0;
      const exponent = Number(String(raw).split(/[eE]/)[1] ?? 0);
      const rounding = item.value !== null ? 0 : 0.51 * 10 ** (exponent - places) *
        (Number(raw) === 0 ? 1 : Math.abs(value / Number(raw)));
      requireCheck(!['flow_rate_m3_s', 'reference_flow_rate_m3_s', 'max_velocity_m_s',
        'mean_velocity_m_s', 'hydraulic_diameter_mm', 'node_count', 'element_count', 'dof_count'].includes(key) ||
        value > 0, 'FEM-DOM-METRICS', `Positive ${key} was displayed as zero`, { item, actual: metrics[key] });
      requireCheck(Math.abs(value - metrics[key]) <= Math.max(rounding, Math.abs(metrics[key]) * 0.02, 1e-14),
        'FEM-DOM-METRICS', `Visible ${key} disagrees with real solver response`,
        { item, parsed: value, actual: metrics[key] });
    }
    await this.waitFor('Rendered FEM canvas', () => this.canvas());
    return { metrics, displayed, response_artifact: response.artifact, result: response.result };
  }
  async exportResult(expected, keyboard = false) {
    const marker = this.downloads.length;
    await this.activate('#export-button', keyboard);
    const download = await this.waitFor('Completed real browser JSON export', async () => {
      const item = this.downloads.slice(marker).at(-1);
      requireCheck(!item || item.state !== 'canceled', 'EXPORT', 'Browser export canceled', { item });
      return item?.state === 'completed' ? item : false;
    });
    requireCheck(/\.json$/i.test(download.suggestedFilename), 'EXPORT', 'Export is not a JSON file', { download });
    const bytes = await fs.readFile(path.join(this.run, 'downloads', download.guid));
    requireCheck(bytes.length > 100, 'EXPORT', 'Empty/truncated browser export');
    const parsed = JSON.parse(bytes.toString('utf8'));
    validateResult(parsed, expected.config);
    requireCheck(JSON.stringify(parsed) === JSON.stringify(expected), 'EXPORT',
      'Export differs from actual HTTP result (stale or incomplete)');
    return { guid: download.guid, filename: download.suggestedFilename, bytes: bytes.length, sha256: digest(bytes) };
  }
  async coreFlow(name, keyboard = false) {
    await this.waitFormReady(`${name}: form ready before configuration`);
    await this.check('DOM-CONTRACT', `${name}: configure default case`, () => this.config(DEFAULT_CONFIG, keyboard));
    const baseline = await this.check('FEM-METRICS', `${name}: real solve and all FEM metrics`,
      () => this.solve(DEFAULT_CONFIG, keyboard));
    await this.selectView("field", keyboard);
    const field = await this.canvas();
    await this.selectView("mesh", keyboard);
    const mesh = await this.waitFor('Mesh redraw', async () => {
      const rendered = await this.canvas();
      return rendered.pixel_hash !== field.pixel_hash ? rendered : false;
    });
    await this.screenshot(`${name}-mesh`, "mesh");
    await this.selectView("field", keyboard);
    await this.waitFor('Field redraw', async () => (await this.canvas()).pixel_hash !== mesh.pixel_hash);
    const changed = { ...DEFAULT_CONFIG, pressure_pa: 2 };
    await this.input('#pressure_pa', 2, keyboard);
    await this.check('FEM-DIRTY-STATE', `${name}: changed parameters invalidate export`, async () => {
      const state = await this.evaluate(() => ({
        disabled: document.querySelector('#export-button').disabled,
        text: document.querySelector('#status').textContent,
      }));
      requireCheck(state.disabled && /参数|重新|改变|dirty/i.test(state.text),
        'FEM-DIRTY-STATE', 'Old result remains exportable after parameter change', { state });
      return state;
    });
    const doubled = await this.check('FEM-PARAMETER-RECOMPUTE', `${name}: pressure change recomputes`,
      () => this.solve(changed, keyboard));
    requireCheck(relativeClose(doubled.metrics.flow_rate_m3_s, baseline.metrics.flow_rate_m3_s * 2, 0.005) &&
      relativeClose(doubled.metrics.max_velocity_m_s, baseline.metrics.max_velocity_m_s * 2, 0.005),
    'FEM-LINEARITY', 'Doubling pressure did not double actual FEM flow and peak velocity');
    await this.check('EXPORT', `${name}: real JSON export`, () => this.exportResult(doubled.result, keyboard));
    await this.input('#pressure_pa', -1, keyboard);
    const marker = this.responses.length;
    await this.activate('#solve-button', keyboard);
    await this.check('WEB-INPUT-004', `${name}: bad input is loud and repairable`, async () => {
      const failure = await this.waitFor('Visible accessible bad-input feedback', async () => this.evaluate(() => {
        const error = document.querySelector('#error'), field = document.querySelector('#pressure_pa');
        const rect = error.getBoundingClientRect();
        return error.textContent.trim() && rect.width > 0 && rect.height > 0 &&
          getComputedStyle(error).visibility !== 'hidden' ? {
            text: error.textContent.trim(), invalid: field.getAttribute('aria-invalid'),
            described_by: field.getAttribute('aria-describedby'), focused: document.activeElement.id,
            export_disabled: document.querySelector('#export-button').disabled,
          } : null;
      }));
      requireCheck(failure.invalid === 'true' && failure.described_by &&
        ['error', 'pressure_pa'].includes(failure.focused) && failure.export_disabled,
      'WEB-INPUT-004', 'Error lacks field association, focus or stale-export protection', { failure });
      const badResponses = this.responses.slice(marker);
      requireCheck(badResponses.every(item => [400, 422].includes(item.status) && item.result.error?.message),
        'FEM-BAD-INPUT', 'Invalid pressure returned success or silently empty error', { badResponses });
      await this.screenshot(`${name}-bad-input`);
      return failure;
    });
    await this.input('#pressure_pa', 1, keyboard);
    await this.check('FEM-RECOVERY', `${name}: recover without refresh`, () => this.solve(DEFAULT_CONFIG, keyboard));
    await this.check('FEM-RESET', `${name}: actual reset control`, async () => {
      const marker = this.responses.length;
      await this.activate('#reset-button', keyboard);
      const completed = await this.waitFormReady(`${name}: reset solve completed`,
        { requireResult: true, marker });
      const metrics = validateResult(completed.response.result, DEFAULT_CONFIG);
      const actual = await this.evaluate(keys => Object.fromEntries(keys.map(key =>
        [key, key === 'geometry' ? document.querySelector(`#${key}`).value : Number(document.querySelector(`#${key}`).value)])),
      Object.keys(DEFAULT_CONFIG));
      requireCheck(JSON.stringify(actual) === JSON.stringify(DEFAULT_CONFIG), 'FEM-RESET', 'Reset did not restore defaults', { actual });
      return { actual, metrics, response_artifact: completed.response.artifact,
        state: completed.state };
    });
    await this.selectView("field", keyboard);
    await this.screenshot(`${name}-field`, "field");
    return { baseline: baseline.metrics, doubled: doubled.metrics, field, mesh };
  }
  async layout(name) {
    const snapshot = await this.evaluate(layoutSnapshot, CONTRACT);
    await fs.writeFile(path.join(this.run, `${name}-layout.json`), json(snapshot), { flag: 'wx' });
    const check = (condition, rule, message, detail) => requireCheck(condition, rule, message, detail);
    check(snapshot.viewport_meta.length === 1 &&
      snapshot.viewport_meta[0] === 'width=device-width, initial-scale=1', 'WEB-BASE-001', 'Wrong viewport metadata');
    check(snapshot.client_width > 0 && snapshot.scroll_width <= snapshot.client_width + 1 &&
      snapshot.overflowing.length === 0, 'WEB-BASE-004', 'Page/content horizontal overflow', snapshot);
    check(!['hidden', 'clip'].includes(snapshot.html_overflow_x) &&
      !['hidden', 'clip'].includes(snapshot.body_overflow_x), 'WEB-BASE-005', 'Global overflow hiding masks content');
    check(snapshot.main_count === 1 && snapshot.h1_count === 1 && snapshot.duplicate_ids.length === 0,
      'WEB-NAV-001', 'Invalid landmarks/headings or duplicate IDs', snapshot);
    check(snapshot.skip_link, 'WEB-NAV-001', 'Missing focusable skip link to unique main');
    check(snapshot.lang && snapshot.title && snapshot.unnamed.length === 0 &&
      snapshot.unlabeled.length === 0, 'WEB-A11Y-007', 'Missing page/control names or labels', snapshot);
    check(snapshot.small_targets.length === 0, 'WEB-INPUT-001', 'Targets smaller than 44x44', snapshot.small_targets);
    check(snapshot.close_targets.length === 0, 'WEB-INPUT-002', 'Independent targets separated by less than 8px', snapshot.close_targets);
    check(snapshot.font_failures.length === 0, 'WEB-TYPE-001', 'Text below minimum font size', snapshot.font_failures);
    check(snapshot.clipped_text.length === 0, 'WEB-TYPE-006', 'Clipped key text', snapshot.clipped_text);
    check(snapshot.contrast_failures.length === 0, 'WEB-A11Y-001', 'Measured solid-background text contrast below budget',
      snapshot.contrast_failures);
    check(snapshot.invalid_box_model.length === 0, 'WEB-BASE-002', 'Non-border-box rendered elements',
      snapshot.invalid_box_model);
    check(Object.values(snapshot.regions).every(region => region && region.content_width > 0),
      'WEB-ADAPT-002', 'Missing/empty contract region selectors', snapshot.regions);
    check(snapshot.declared_profile === CONTRACT.profile &&
      snapshot.declared_shell === CONTRACT.shell &&
      snapshot.declared_main === CONTRACT.main &&
      snapshot.declared_secondary === CONTRACT.secondary,
    'WEB-ADAPT-002', 'Page profile or selectors disagree with browser manifest', snapshot);
    return snapshot;
  }
  async accessibility(name) {
    const tree = await this.send('Accessibility.getFullAXTree');
    requireCheck(Array.isArray(tree.nodes) && tree.nodes.length > 0, 'AX-EMPTY', 'Empty Chromium AX tree');
    await fs.writeFile(path.join(this.run, `${name}-ax.json`), json(tree), { flag: 'wx' });
    const interactive = new Set(['button', 'link', 'textbox', 'combobox', 'spinbutton', 'checkbox', 'radio']);
    const unnamed = tree.nodes.filter(node => !node.ignored && interactive.has(node.role?.value) && !node.name?.value?.trim())
      .map(node => ({ nodeId: node.nodeId, role: node.role?.value }));
    requireCheck(unnamed.length === 0, 'WEB-A11Y-007', 'Unnamed controls in Chromium AX tree', { unnamed });
    return { nodes: tree.nodes.length, unnamed, method: 'Chromium Accessibility.getFullAXTree; NOT human screen-reader testing' };
  }
  async sourceAudit() {
    const audit = await this.evaluate(async () => {
      const sheets = [...document.styleSheets].map(sheet => {
        const rules = [...sheet.cssRules];
        const collect = list => list.flatMap(rule => [
          { text: rule.cssText, condition: rule.conditionText ?? '' },
          ...(rule.cssRules ? collect([...rule.cssRules]) : []),
        ]);
        return collect(rules);
      }).flat();
      const scripts = await Promise.all([...document.scripts].filter(script => !script.type ||
        ['module', 'text/javascript', 'application/javascript'].includes(script.type)).map(async script => {
        if (!script.src) return script.textContent;
        const response = await fetch(script.src, { cache: 'force-cache' });
        if (!response.ok) throw new Error(`Cannot audit script ${script.src}: HTTP ${response.status}`);
        return response.text();
      }));
      const widths = sheets.flatMap(rule => [...rule.condition.matchAll(
        /(?:min-|max-)?width\s*:\s*(\d+(?:\.\d+)?)(px|rem|em)/g)].map(match =>
        Number(match[1]) * (match[2] === 'px' ? 1 : 16)));
      return {
        media_widths: [...new Set(widths)].sort((a, b) => a - b),
        rules: sheets, script_count: scripts.length,
        user_agent_branch: scripts.some(source => /navigator\s*(?:\.\s*userAgent|\[\s*['"]userAgent['"]\s*\])/.test(source)),
        reduced_motion_rule: sheets.some(rule => /prefers-reduced-motion/.test(rule.text)),
        dark_mode_supported: sheets.some(rule => /prefers-color-scheme\s*:\s*dark/.test(rule.text)),
      };
    });
    requireCheck(audit.script_count > 0 && !audit.user_agent_branch,
      'WEB-ADAPT-001', 'Empty scripts or UA-driven layout');
    for (const width of audit.media_widths) {
      requireCheck(width > 1 && width < 5000 && Number.isInteger(width),
        'WEB-RWD-001', 'Unsupported/nonintegral CSS breakpoint', { width });
    }
    await fs.writeFile(path.join(this.run, 'source-audit.json'), json(audit), { flag: 'wx' });
    return audit;
  }
  async keyboardBasics() {
    await this.navigate();
    await this.key('Tab');
    const skip = await this.focusInfo();
    const target = await this.evaluate(() => document.activeElement.getAttribute('href'));
    requireCheck(target === '#workspace', 'WEB-NAV-001', 'First Tab must reach skip-to-main link', { skip, target });
    await this.key('Enter');
    requireCheck(await this.evaluate(() => document.activeElement.id === 'workspace'),
      'WEB-NAV-001', 'Skip link does not focus main');
    await this.tabTo('#geometry');
    await this.key('Tab', undefined, 8);
    const reverse = await this.focusInfo();
    requireCheck(reverse.id !== 'geometry' && reverse.visible && reverse.uncovered,
      'WEB-A11Y-003', 'Shift+Tab unusable', { reverse });
    await this.key('Escape');
    return { skip, reverse, activation: 'CDP key events only; no HTMLElement.click/focus calls' };
  }
  async runAcceptance() {
    this.coordinate = 'initial';
    await this.viewport(1280, 800);
    await this.navigate();
    const dom = await this.evaluate(() => {
      const required = [
        'workspace', 'case-form', 'solve-button', 'reset-button', 'status', 'error', 'flow-canvas',
        'plot-description', 'view-field', 'view-mesh', 'export-button', 'import-input', 'result-summary',
        'convergence', 'version-menu', 'edited-at',
      ];
      return {
        missing: required.filter(id => !document.getElementById(id)),
        status_role: document.querySelector('#status')?.getAttribute('role'),
        live: document.querySelector('#status')?.getAttribute('aria-live'),
        error_role: document.querySelector('#error')?.getAttribute('role'),
        error_tabindex: document.querySelector('#error')?.getAttribute('tabindex'),
        version: document.querySelector('#version-menu')?.value,
        edited_at: document.querySelector('#edited-at')?.getAttribute('datetime'),
        build_text: document.querySelector('footer')?.textContent.trim(),
      };
    });
    requireCheck(dom.missing.length === 0 && dom.status_role === 'status' && dom.live &&
      dom.error_role === 'alert' && dom.error_tabindex === '-1', 'DOM-CONTRACT', 'Missing DOM contract', dom);
    requireCheck(dom.version === 'v1' && typeof dom.edited_at === 'string' &&
      /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+08:00$/.test(dom.edited_at),
    'DOM-VERSION', 'Wrong v1 selection or imprecise edit timestamp', dom);
    this.report.page_build = dom;
    const audit = await this.check('WEB-ADAPT-001', 'local source and media audit', () => this.sourceAudit());
    this.report.source_audit = audit;
    for (const [name, width, height, touch] of MATRIX) {
      this.coordinate = name;
      await this.viewport(width, height, touch);
      this.report.matrix.push({ name, width, height, touch, status: 'RUNNING' });
      await this.check('BROWSER-CORE', 'real render, pointer/touch events and complete main flow',
        () => this.coreFlow(name));
      await this.check('WEB-BASE-004', 'rendered layout and actual content boxes', () => this.layout(name));
      await this.check('WEB-A11Y-007', 'automatic Chromium AX tree', () => this.accessibility(name));
      this.report.matrix.at(-1).status = 'PASS';
    }
    const breakpoints = [...new Set([576, 768, 1024, 1280, ...audit.media_widths])].sort((a, b) => a - b);
    this.report.breakpoints = breakpoints;
    for (const breakpoint of breakpoints) {
      for (const delta of [-1, 0, 1]) {
        const width = breakpoint + delta, name = `breakpoint-${width}`;
        this.coordinate = name;
        await this.viewport(width, 800, width < 1024);
        await this.check('WEB-RWD-002', 'live resize continuity, geometry and unobstructed click', async () => {
          const layout = await this.layout(name);
          await this.activate('#view-mesh');
          const mesh = await this.canvas();
          await this.activate('#view-field');
          const field = await this.waitFor('Breakpoint view toggle', async () => {
            const result = await this.canvas();
            return result.pixel_hash !== mesh.pixel_hash ? result : false;
          });
          await this.screenshot(name);
          return { width, layout, field };
        });
      }
    }
    for (const [name, width, height] of [['keyboard-desktop', 1280, 800], ['keyboard-mobile', 320, 568]]) {
      this.coordinate = name;
      await this.viewport(width, height);
      await this.check('WEB-A11Y-003', 'Tab, Shift+Tab, skip-link, Enter, Escape', () => this.keyboardBasics());
      await this.check('WEB-A11Y-003', 'keyboard-only solve, edit, bad input, recovery, mesh and export',
        () => this.coreFlow(name, true));
    }
    const modes = [
      ['reduced-motion', [{ name: 'prefers-reduced-motion', value: 'reduce' }]],
      ['forced-colors', [{ name: 'forced-colors', value: 'active' }]],
      ...(audit.dark_mode_supported ? [['dark-mode', [{ name: 'prefers-color-scheme', value: 'dark' }]]] : []),
    ];
    for (const [name, features] of modes) {
      this.coordinate = name;
      await this.viewport(360, 800, true);
      await this.send('Emulation.setEmulatedMedia', { features });
      await this.check('WEB-MOTION-001', 'media mode and full functionality', async () => {
        await this.coreFlow(name);
        const active = await this.evaluate(() => ({
          reduced: matchMedia('(prefers-reduced-motion: reduce)').matches,
          forced: matchMedia('(forced-colors: active)').matches,
          animations: document.getAnimations().filter(animation => animation.playState === 'running')
            .map(animation => ({ duration: animation.effect.getTiming().duration, target: animation.effect.target?.id })),
        }));
        requireCheck(name !== 'reduced-motion' || (active.reduced && active.animations.every(item =>
          typeof item.duration === 'number' && item.duration <= 10)),
        'WEB-MOTION-001', 'Nonessential animation continues in reduced-motion mode', active);
        await this.layout(name);
        return active;
      });
    }
    await this.send('Emulation.setEmulatedMedia', { features: [] });
    this.coordinate = 'text-200';
    await this.viewport(360, 800, true);
    await this.navigate();
    // Browser default font preferences do not enlarge fixed px declarations.
    // Enlarge every computed font, not just rem text; record this as a text-only
    // stress simulation rather than claiming a browser-native text zoom feature.
    await this.textStress();
    await this.check('WEB-TYPE-004', '200% computed text-size stress and full main flow', async () => {
      await this.coreFlow('text-200');
      return this.layout('text-200');
    });
    this.coordinate = 'page-400';
    await this.viewport(320, 200, false, 1, 4);
    await this.navigate();
    await this.check('WEB-INPUT-006', '400% page-zoom reflow equivalent and full keyboard main flow', async () => {
      const result = await this.coreFlow('page-400', true);
      await this.layout('page-400');
      return { ...result, method: '1280x800 desktop / 4 = 320x200 CSS viewport, DPR 4; reflow equivalent, not browser-UI zoom' };
    });
    this.coordinate = 'rectangle';
    await this.viewport(1280, 800);
    await this.navigate();
    const rectangular = { ...DEFAULT_CONFIG, geometry: 'rectangle', width_mm: 4, height_mm: 2 };
    await this.config(rectangular);
    const result = await this.check('FEM-RECTANGLE', 'real noncircular mesh/solution and export',
      () => this.solve(rectangular));
    await this.exportResult(result.result);
    await this.screenshot('rectangle');
    await this.check('IMPORT', 'actual exported-file import must recompute', async () => {
      const exported = await this.exportResult(result.result);
      const marker = this.responses.length;
      const root = await this.send('DOM.getDocument');
      const { nodeId } = await this.send('DOM.querySelector', { nodeId: root.root.nodeId, selector: '#import-input' });
      requireCheck(nodeId > 0, 'IMPORT', 'Missing import file input');
      await this.send('DOM.setFileInputFiles', { nodeId, files: [path.join(this.run, 'downloads', exported.guid)] });
      const response = await this.waitFor('Import actual solver recomputation', () =>
        this.responses.slice(marker).find(item => item.status === 200));
      await this.waitFormReady('Imported result UI completed', { requireResult: true, marker });
      return { source: exported, metrics: validateResult(response.result, rectangular),
        method: 'CDP native file input selection; operating-system file chooser not human-tested' };
    });
    await Promise.all(this.responseTasks);
    requireCheck(this.report.matrix.length === 8 && this.report.matrix.every(item => item.status === 'PASS') &&
      this.responses.length > 0 && this.report.screenshots.length >= 8,
    'NONEMPTY-ACCEPTANCE', 'Incomplete matrix, empty result set or missing screenshots');
    requireCheck(this.errors.length === 0, 'BROWSER-RUNTIME', 'Browser/network errors', { errors: this.errors });
    this.report.result_count = this.responses.length;
    this.report.network = [...this.requests.values()].map(({ body, ...request }) => request);
  }
  async probeInput() {
    this.coordinate = 'probe-mobile-wide';
    await this.viewport(390, 844, true);
    await this.navigate();
    await this.config(DEFAULT_CONFIG);
    await this.solve(DEFAULT_CONFIG);
    await this.input('#pressure_pa', 2);
    const changed = { ...DEFAULT_CONFIG, pressure_pa: 2 };
    const solved = await this.solve(changed);
    await this.exportResult(solved.result);
    this.report.input_method = 'Actual CDP touch activation + real Tab/Shift+Tab and keyboard editing; not pure touch';
    await this.check('BROWSER-INPUT', '390px touch activation + keyboard editing after completed export',
      () => this.input('#pressure_pa', -1));
    await this.screenshot('probe-after-input');
    this.report.probe_events = await this.evaluate(() => window.__opensimInputEvents);
  }
  async textStress() {
    await this.evaluate(() => {
      const elements = [document.documentElement, ...document.querySelectorAll('body,body *')];
      const sizes = elements.map(element => parseFloat(getComputedStyle(element).fontSize));
      elements.forEach((element, index) => element.style.setProperty('font-size', `${sizes[index] * 2}px`, 'important'));
    });
  }
  async probeScreenshot() {
    this.coordinate = 'probe-text-200-capture';
    await this.viewport(360, 800, true);
    await this.navigate();
    await this.textStress();
    const solved = await this.solve(DEFAULT_CONFIG);
    await this.selectView("mesh");
    await this.check('SCREENSHOT-COVERAGE', '200% text full-resolution complete-page screenshot',
      () => this.screenshot('probe-text-200-mesh', "mesh"));
    this.report.probe_metrics = solved.metrics;
    this.report.probe_scope = 'Capture only; text layout and complete main flow remain independent acceptance gates';
  }
  async close() {
    if (!this.chrome) return;
    if (this.chrome.exitCode === null) {
      // Only the browser child owned by this invocation is terminated.
      this.chrome.kill('SIGTERM');
      const closed = await Promise.race([once(this.chrome, 'close').then(() => true), delay(5000).then(() => false)]);
      if (!closed) {
        this.chrome.kill('SIGKILL');
        await once(this.chrome, 'close');
        throw new Error('Owned Chromium child required SIGKILL during cleanup');
      }
    }
    await fs.writeFile(path.join(this.run, 'chromium.log'),
      Buffer.concat(this.rawChromiumChunks ?? []).toString('utf8') || this.chromiumLog || '(no Chromium output)\n');
    if (this.logTask) await this.logTask;
  }
}

function layoutSnapshot(contract) {
  const rect = element => {
    const box = element.getBoundingClientRect(), style = getComputedStyle(element);
    return {
      x: box.x, y: box.y, width: box.width, height: box.height,
      content_width: box.width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight) -
        parseFloat(style.borderLeftWidth) - parseFloat(style.borderRightWidth),
      font_px: parseFloat(style.fontSize), line_height: style.lineHeight,
      display: style.display, order: style.order,
    };
  };
  const visible = element => element.getClientRects().length > 0 &&
    getComputedStyle(element).visibility !== 'hidden' && getComputedStyle(element).display !== 'none';
  const identify = element => element.id ? `#${element.id}` :
    `${element.tagName.toLowerCase()}${element.className ? '.' + String(element.className).trim().split(/\s+/).join('.') : ''}`;
  const name = element => element.getAttribute('aria-label') ??
    element.getAttribute('aria-labelledby')?.split(/\s+/).map(id => document.getElementById(id)?.textContent ?? '').join(' ') ??
    [...(element.labels ?? [])].map(label => label.textContent.trim()).join(' ') ??
    element.textContent.trim();
  const elements = [...document.querySelectorAll('body *')].filter(visible);
  const controls = elements.filter(element => element.matches(
    'button,a[href],input:not([type=hidden]),select,textarea,[role=button],[role=link]'));
  const ids = [...document.querySelectorAll('[id]')].map(element => element.id);
  const duplicateIds = [...new Set(ids.filter((id, index) => ids.indexOf(id) !== index))];
  const unnamed = controls.filter(element => {
    const label = element.getAttribute('aria-label') ||
      element.getAttribute('aria-labelledby')?.split(/\s+/).map(id => document.getElementById(id)?.textContent ?? '').join(' ') ||
      [...(element.labels ?? [])].map(item => item.textContent.trim()).join(' ') || element.textContent.trim();
    return !label;
  }).map(identify);
  const unclipped = element => {
    const style = getComputedStyle(element);
    return style.overflowX === 'visible' && style.overflowY === 'visible';
  };
  const small = controls.filter(element => {
    if (element.matches('a') && element.closest('p')) return false;
    const box = element.getBoundingClientRect();
    return box.width < 43.99 || box.height < 43.99;
  }).map(element => ({ selector: identify(element), ...rect(element) }));
  const close = [];
  for (let index = 0; index < controls.length; index++) {
    for (let other = index + 1; other < controls.length; other++) {
      const a = controls[index], b = controls[other];
      if (a.contains(b) || b.contains(a) || a.closest('p') || b.closest('p')) continue;
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      const dx = Math.max(0, ra.left - rb.right, rb.left - ra.right);
      const dy = Math.max(0, ra.top - rb.bottom, rb.top - ra.bottom);
      if (Math.hypot(dx, dy) < 7.99) close.push({ a: identify(a), b: identify(b), gap: Math.hypot(dx, dy) });
    }
  }
  const textElements = elements.filter(element =>
    [...element.childNodes].some(node => node.nodeType === Node.TEXT_NODE && node.textContent.trim()) &&
    !element.matches('script,style,option'));
  const parseColor = color => {
    const match = color.match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+))?\s*\)$/);
    return match ? [Number(match[1]), Number(match[2]), Number(match[3]), Number(match[4] ?? 1)] : null;
  };
  const luminance = color => color.slice(0, 3).map(value => {
    const fraction = value / 255;
    return fraction <= 0.04045 ? fraction / 12.92 : ((fraction + 0.055) / 1.055) ** 2.4;
  }).reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
  const contrastFailures = [], contrastUnmeasured = [];
  for (const element of textElements) {
    if (element.disabled) continue;
    const foreground = parseColor(getComputedStyle(element).color);
    let background = [255, 255, 255, 1], complex = false;
    const ancestors = [];
    for (let parent = element; parent; parent = parent.parentElement) ancestors.unshift(parent);
    for (const parent of ancestors) {
      const style = getComputedStyle(parent), color = parseColor(style.backgroundColor);
      if (style.backgroundImage !== 'none' || Number(style.opacity) < 1) complex = true;
      if (color) background = color.slice(0, 3).map((value, index) =>
        value * color[3] + background[index] * (1 - color[3])).concat(1);
    }
    if (!foreground || complex || foreground[3] < 1) {
      contrastUnmeasured.push(identify(element));
      continue;
    }
    const first = luminance(foreground), second = luminance(background);
    const ratio = (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
    const style = getComputedStyle(element), size = parseFloat(style.fontSize);
    const threshold = size >= 24 || (size >= 18.66 && Number(style.fontWeight) >= 700) ? 3 : 4.5;
    if (ratio < threshold) contrastFailures.push({ selector: identify(element), ratio, threshold });
  }
  const regions = Object.fromEntries(Object.entries(contract).filter(([key]) => key !== 'profile').map(([key, selector]) =>
    [key, document.querySelector(selector) ? { selector, ...rect(document.querySelector(selector)) } : null]));
  const primary = regions.primary, secondary = regions.secondary;
  const usefulWidth = primary && secondary ? Math.max(primary.x + primary.width, secondary.x + secondary.width) -
    Math.min(primary.x, secondary.x) : null;
  const skipLink = [...document.querySelectorAll('a[href="#workspace"]')].some(element =>
    element.tabIndex >= 0 && (element.textContent.trim() || name(element)));
  return {
    profile: contract.profile, selectors: contract, viewport: { width: innerWidth, height: innerHeight },
    declared_profile: document.body.dataset.pageProfile,
    declared_shell: document.body.dataset.pageShell,
    declared_main: document.body.dataset.mainRegion,
    declared_secondary: document.body.dataset.secondaryRegion,
    viewport_meta: [...document.querySelectorAll('meta[name=viewport]')].map(element => element.content),
    client_width: document.documentElement.clientWidth, scroll_width: document.documentElement.scrollWidth,
    html_overflow_x: getComputedStyle(document.documentElement).overflowX,
    body_overflow_x: getComputedStyle(document.body).overflowX,
    main_count: document.querySelectorAll('main').length, h1_count: document.querySelectorAll('h1').length,
    duplicate_ids: duplicateIds, skip_link: skipLink, lang: document.documentElement.lang, title: document.title,
    regions, useful_width: usefulWidth, useful_width_ratio: usefulWidth / innerWidth,
    secondary_position: primary && secondary ?
      (Math.abs(primary.y - secondary.y) < Math.min(primary.height, secondary.height) ? 'beside' : 'stacked') : 'missing',
    unnamed, unlabeled: controls.filter(element => element.matches('input,select,textarea') &&
      !element.labels?.length && !element.getAttribute('aria-labelledby') && !element.getAttribute('aria-label')).map(identify),
    small_targets: small, close_targets: close, contrast_failures: contrastFailures,
    contrast_unmeasured: contrastUnmeasured,
    font_failures: textElements.filter(element => parseFloat(getComputedStyle(element).fontSize) <
      (element.matches('p') && !element.closest('.field-hint,.field-unit,.metric-unit,.eyebrow,.footer') ?
        16 : 14)).map(element => ({ selector: identify(element), ...rect(element) })),
    clipped_text: textElements.filter(element => !unclipped(element) &&
      (element.scrollHeight > element.clientHeight + 1 || element.scrollWidth > element.clientWidth + 1) &&
      !element.closest('pre,[data-local-scroll]')).map(identify),
    overflowing: elements.filter(element => {
      const box = element.getBoundingClientRect();
      if (element.closest('pre,[data-local-scroll]')) return false;
      // Offscreen skip links are allowed only while not focused.
      if (element.matches('a[href="#workspace"]') && element !== document.activeElement) return false;
      return box.right > document.documentElement.clientWidth + 1 || box.left < -1;
    }).map(element => ({ selector: identify(element), ...rect(element) })),
    invalid_box_model: elements.filter(element => getComputedStyle(element).boxSizing !== 'border-box').map(identify),
  };
}

async function selftest(harness) {
  const fixture = `<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Browser harness fixture only</title>
<style>html{box-sizing:border-box}*,*::before,*::after{box-sizing:inherit}
body{margin:0;padding:16px;font:16px/1.6 sans-serif}button,input,select,a{min-height:44px;min-width:44px}
button,input,select{margin:8px}canvas{max-width:100%;height:auto}*:focus-visible{outline:3px solid #003399}
@media(prefers-reduced-motion:reduce){*{animation:none;transition:none}}</style>
<a href="#workspace">Skip to main</a><main id="workspace" tabindex="-1"><h1>Harness fixture, not OpenSim or FEM</h1>
<label for="probe">Probe</label><input id="probe" type="number" value="1">
<label for="geometry">Choice</label><select id="geometry"><option value="circle">circle</option><option value="rectangle">rectangle</option></select>
<button id="draw">Render</button><button id="save">Export fixture</button>
<button id="view-field" aria-pressed="true">Fixture field</button>
<button id="view-mesh" aria-pressed="false">Fixture mesh</button>
<p id="error" role="alert" tabindex="-1"></p><p id="state" role="status" aria-live="polite">Fixture loaded</p>
<canvas id="flow-canvas" width="200" height="120" aria-label="Fixture drawing"></canvas></main>
<script>
const canvas=document.getElementById('flow-canvas'),ctx=canvas.getContext('2d');
let color=0;
for(const view of ["field","mesh"]){
 const button=document.getElementById("view-"+view);
 button.addEventListener("click",()=>button.setAttribute("aria-pressed",String(button.getAttribute("aria-pressed")!=="true")));
}
document.getElementById('draw').addEventListener('click',()=>{
 const input=document.getElementById('probe'),error=document.getElementById('error');
 if(Number(input.value)<=0){error.textContent='Probe must be positive; enter 1 to recover';input.setAttribute('aria-invalid','true');error.focus();return}
 input.removeAttribute('aria-invalid');error.textContent='';ctx.fillStyle='#fff';ctx.fillRect(0,0,200,120);
 ctx.fillStyle=++color%2?'#035':'#a30';ctx.fillRect(10,10,180,100);
 ctx.fillStyle='#097';ctx.fillRect(40,30,50,60);
 document.getElementById('state').textContent='Real canvas rendered '+color;
});
document.getElementById('save').addEventListener('click',()=>{
 const anchor=document.createElement('a');anchor.href=URL.createObjectURL(new Blob([JSON.stringify({fixture:true,purpose:'browser machinery only'})],{type:'application/json'}));
 anchor.download='fixture-only.json';anchor.click();setTimeout(()=>URL.revokeObjectURL(anchor.href),1000);
});
</script></html>`;
  const server = http.createServer((request, response) => {
    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    response.end(fixture);
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  harness.options.url = `http://127.0.0.1:${server.address().port}/`;
  harness.report.fixture_url = harness.options.url;
  try {
    await harness.launch();
    harness.coordinate = 'selftest';
    await harness.viewport(320, 568, true);
    await harness.navigate();
    await harness.check('HARNESS-REAL-INPUT', 'real typing and select', async () => {
      await harness.input('#probe', 2);
      await harness.choose('#geometry', 'rectangle');
    });
    await harness.check('HARNESS-REAL-RENDER', 'real touch event and nonuniform canvas', async () => {
      await harness.activate('#draw');
      const canvas = await harness.canvas();
      await harness.screenshot('selftest-render');
      return canvas;
    });
    await harness.check('HARNESS-KEYBOARD', 'keyboard error and recovery', async () => {
      await harness.input('#probe', -1, true);
      await harness.activate('#draw', true);
      requireCheck(await harness.evaluate(() => document.activeElement.id === 'error' &&
        document.querySelector('#error').textContent.length > 10),
      'HARNESS-KEYBOARD', 'Fixture error not focused');
      await harness.input('#probe', 1, true);
      await harness.activate('#draw', true);
      requireCheck(await harness.evaluate(() => !document.querySelector('#error').textContent),
        'HARNESS-KEYBOARD', 'Fixture error did not recover');
      return harness.canvas();
    });
    await harness.check('HARNESS-EXPORT', 'real browser export artifact', async () => {
      await harness.activate('#save', true);
      const download = await harness.waitFor('Fixture export', () =>
        harness.downloads.find(item => item.state === 'completed'));
      const text = await fs.readFile(path.join(harness.run, 'downloads', download.guid), 'utf8');
      requireCheck(JSON.parse(text).fixture === true, 'HARNESS-EXPORT', 'Incorrect actual downloaded fixture');
      return { ...download, sha256: digest(text) };
    });
    await harness.check('HARNESS-AX', 'nonempty real AX tree', () => harness.accessibility('selftest'));
    await harness.check('HARNESS-MEDIA', 'real reduced-motion emulation and live resize', async () => {
      await harness.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
      await harness.viewport(1064, 768);
      requireCheck(await harness.evaluate(() => innerWidth === 1064 &&
        matchMedia('(prefers-reduced-motion: reduce)').matches), 'HARNESS-MEDIA', 'Emulation did not take effect');
      await harness.screenshot('selftest-resize');
    });
    await harness.check("HARNESS-VIEW", "real view activation, capture binding and wrong-selection rejection", async () => {
      const fault = harness.options["view-fixture"];
      const initial = await harness.confirmView("field");
      if (fault === "stuck-toggle") await harness.evaluate(() => {
        document.querySelector("#view-field").addEventListener("click", event => event.stopImmediatePropagation(),
          { capture: true, once: true });
      });
      const mesh = await harness.selectView("mesh");
      if (fault === "wrong-selection") await harness.screenshot("selftest-wrong-field", "field");
      await harness.screenshot("selftest-mesh", "mesh");
      const field = await harness.selectView("field", true);
      if (fault === "capture-change") await harness.evaluate(() => {
        window.addEventListener("scroll", () => {
          document.querySelector("#view-field").setAttribute("aria-pressed", "false");
          document.querySelector("#view-mesh").setAttribute("aria-pressed", "true");
        }, { once: true });
        const spacer = document.createElement("div");
        spacer.style.height = "1600px";
        document.body.append(spacer);
      });
      await harness.screenshot("selftest-field", "field");
      const captures = harness.report.captures.length, screenshots = harness.report.screenshots.length;
      await assert.rejects(() => harness.screenshot("selftest-wrong-mesh", "mesh"),
        error => error.rule === "BROWSER-VIEW" && error.detail.selected_view === "field");
      assert.equal(harness.report.captures.length, captures);
      assert.equal(harness.report.screenshots.length, screenshots);
      assert(!(await fs.readdir(harness.run)).some(file => file.startsWith("selftest-wrong-")));
      const again = await harness.selectView("field", true);
      assert.equal(again.selected_view, "field");
      for (const [pressed, selected] of [[["true", "true"], "field+mesh"], [["false", "false"], "none"],
        [["invalid", "false"], null]]) {
        await harness.evaluate(values => ["field", "mesh"].forEach((view, index) =>
          document.querySelector(`#view-${view}`).setAttribute("aria-pressed", values[index])), pressed);
        await assert.rejects(() => harness.confirmView("field"), error => error.rule === "BROWSER-VIEW" &&
          (selected === null || error.detail.selected_view === selected));
      }
      await assert.rejects(() => harness.selectView("field"), error => error.rule === "BROWSER-VIEW");
      // Restore only the deliberately invalid DOM fixture; production never repairs attributes.
      await harness.evaluate(() => {
        document.querySelector("#view-field").setAttribute("aria-pressed", "true");
        document.querySelector("#view-mesh").setAttribute("aria-pressed", "false");
      });
      return { initial, mesh, field, wrong_selection_rejected: true, no_wrong_capture_written: true,
        repeated_selection_preserved: true, both_none_invalid_rejected: true, scope: "REAL_DOM_FIXTURE_NOT_PRODUCT" };
    });
    await harness.check('HARNESS-NEGATIVE', 'empty screenshot/result and nonfinite metrics are rejected', async () => {
      assert.throws(() => validatePng(Buffer.alloc(0)));
      assert.throws(() => validateResult({}, DEFAULT_CONFIG), CheckFailure);
      assert.throws(() => validateResult({ schema_version: 1, config: DEFAULT_CONFIG, metrics: { reynolds: NaN } },
        DEFAULT_CONFIG), CheckFailure);
      await harness.evaluate(() => document.querySelector('#flow-canvas').getContext('2d').clearRect(0, 0, 200, 120));
      await assert.rejects(() => harness.canvas(), error => error.rule === 'REAL-RENDER');
      return { empty_png_rejected: true, empty_result_rejected: true, nonfinite_rejected: true, blank_canvas_rejected: true };
    });
    await harness.check('HARNESS-FEM-RULER', 'synthetic P1 integration ruler and malformed/stale result rejection', async () => {
      // Algebra-only ruler selftest, explicitly not a solver or physics proof.
      const sample = {
        schema_version: 1, config: { ...DEFAULT_CONFIG },
        model: { name: 'selftest fixture', discretization: 'P1 linear triangles', assumptions: ['not a physical solver'] },
        mesh: { nodes_mm: [[-1, -1], [1, -1], [1, 1], [-1, 1], [0, 0]],
          triangles: [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]], boundary_nodes: [0, 1, 2, 3] },
        fields: { velocity_m_s: [0, 0, 0, 0, 0.01] },
        reference: { velocity_m_s: [0, 0, 0, 0, 0.01], method: 'algebraic fixture only' },
        warnings: ['not OpenSim acceptance'],
        metrics: {
          node_count: 5, element_count: 4, dof_count: 1, max_velocity_m_s: 0.01,
          mean_velocity_m_s: 0.01 / 3, flow_rate_m3_s: 4e-6 * 0.01 / 3,
          reynolds: 1, hydraulic_diameter_mm: 2, relative_residual: 0,
          relative_l2_error: 0, reference_flow_rate_m3_s: 4e-6 * 0.01 / 3,
          relative_flow_error: 0, force_balance_relative_error: 0,
        },
      };
      const integrated = validateResult(sample, DEFAULT_CONFIG);
      const badFlow = structuredClone(sample);
      badFlow.metrics.flow_rate_m3_s *= 2;
      assert.throws(() => validateResult(badFlow, DEFAULT_CONFIG), error => error.rule === 'FEM-METRICS');
      const badTriangle = structuredClone(sample);
      badTriangle.mesh.triangles[0] = [0, 0, 4];
      assert.throws(() => validateResult(badTriangle, DEFAULT_CONFIG), error => error.rule === 'FEM-MESH');
      const badBoundary = structuredClone(sample);
      badBoundary.fields.velocity_m_s[0] = 0.01;
      assert.throws(() => validateResult(badBoundary, DEFAULT_CONFIG), error => error.rule === 'FEM-BOUNDARY');
      assert.throws(() => validateResult(sample, { ...DEFAULT_CONFIG, pressure_pa: 2 }),
        error => error.rule === 'FEM-CURRENT-CONFIG');
      return { integrated, wrong_flow_rejected: true, malformed_mesh_rejected: true,
        no_slip_violation_rejected: true, stale_config_rejected: true, fixture_is_solver: false };
    });
    await harness.check('HARNESS-READINESS', 'enabled controls do not bypass aria-busy completion', async () => {
      await harness.evaluate(() => {
        const form = document.createElement('form');
        form.id = 'case-form';
        form.setAttribute('aria-busy', 'true');
        document.querySelector('#workspace').append(form);
        for (const id of ['geometry', 'probe']) form.append(document.getElementById(id));
        for (const id of ['solve-button', 'reset-button', 'import-input', 'export-button']) {
          const element = document.createElement(id === 'import-input' ? 'input' : 'button');
          element.id = id;
          element.textContent = id;
          (id === 'export-button' ? document.querySelector('#workspace') : form).append(element);
        }
        document.querySelector('#export-button').disabled = true;
        const metric = document.createElement('p');
        metric.id = 'metric-flow_rate_m3_s';
        metric.textContent = '未计算';
        form.append(metric);
        document.querySelector('#draw').addEventListener('click', () => {
          metric.textContent = '0.01';
          document.querySelector('#export-button').disabled = false;
          form.setAttribute('aria-busy', 'false');
        }, { once: true });
      });
      let settled = false;
      const pending = harness.waitFormReady('Selftest actual form completion', { requireResult: true })
        .then(outcome => { settled = true; return { outcome }; }, error => ({ error }));
      let before, result;
      try {
        before = await harness.formState();
        requireCheck(before.busy === 'true' && before.disabled.length === 0 && !settled,
          'HARNESS-READINESS', 'Enabled controls prematurely released a busy form', { before, settled });
        await harness.activate('#draw', true);
        result = await pending;
      } finally {
        // Even a failed fixture assertion must settle its outstanding waiter.
        if (!result) {
          await harness.evaluate(() => {
            document.querySelector('#case-form').setAttribute('aria-busy', 'false');
            document.querySelector('#export-button').disabled = false;
            document.querySelector('#metric-flow_rate_m3_s').textContent = '0.01';
          });
          result = await pending;
        }
      }
      if (result.error) throw result.error;
      const completed = result.outcome;
      requireCheck(completed.state.busy === 'false' && completed.state.export_enabled,
        'HARNESS-READINESS', 'Actual completion state did not release readiness');
      return { before, completed: completed.state, release: 'real keyboard activation of fixture completion control' };
    });
    await harness.check('HARNESS-CAPTURE-COVERAGE', 'long-page coverage and missing/corrupt tile rejection', async () => {
      const plan = capturePlan({ x: 0, y: 0, width: 345, height: 25463 }, 1, { width: 345, height: 800 });
      const artifacts = plan.tiles.map(clip => ({ clip, width: clip.width, height: clip.height }));
      const coverage = validateCoverage(plan, artifacts);
      requireCheck(plan.tiles.length === 32 && plan.tiles.every(clip => clip.width * clip.height * 4 <= CAPTURE_TILE_BYTES),
        'HARNESS-CAPTURE-COVERAGE', 'Long-page tiles exceed viewport or bitmap budget');
      assert.throws(() => validateCoverage(plan, artifacts.slice(1)),
        error => error.rule === 'SCREENSHOT-COVERAGE');
      const corrupt = structuredClone(artifacts);
      corrupt[0].height--;
      assert.throws(() => validateCoverage(plan, corrupt), error => error.rule === 'SCREENSHOT-COVERAGE');
      assert.throws(() => capturePlan({ x: 0, y: 0, width: 345, height: Infinity }, 1, plan.viewport),
        error => error.rule === 'SCREENSHOT');
      return { coverage, missing_tile_rejected: true, wrong_png_size_rejected: true, nonfinite_content_rejected: true };
    });
    requireCheck(harness.errors.length === 0, 'HARNESS-RUNTIME', 'Selftest browser errors', { errors: harness.errors });
  } finally {
    await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  }
}

async function outputDirectory(out) {
  // Resolve existing ancestors before mkdir, so a symlink cannot redirect writes
  // into another repository/resource. The mount itself may be a symlink.
  const allowedParent = await fs.realpath('/hf3fs-jd/data/tmp');
  let ancestor = out;
  while (true) {
    try {
      const resolved = await fs.realpath(ancestor);
      requireCheck(resolved === allowedParent || resolved.startsWith(allowedParent + path.sep),
        'OUTPUT-BOUNDARY', 'Output ancestor escapes authorized external data mount', { ancestor, resolved });
      break;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      ancestor = path.dirname(ancestor);
    }
  }
  await fs.mkdir(out, { recursive: true });
  const run = path.join(out, `run-${new Date().toISOString().replaceAll(/[:.]/g, '-')}-${randomUUID().slice(0, 8)}`);
  await fs.mkdir(run);
  await fs.mkdir(path.join(run, 'downloads'));
  await fs.writeFile(path.join(run, 'write-probe.txt'), 'Browser evidence write/read probe\n', { flag: 'wx' });
  assert.equal(await fs.readFile(path.join(run, 'write-probe.txt'), 'utf8'), 'Browser evidence write/read probe\n');
  return run;
}
async function main() {
  let options, harness, run;
  const report = {
    schema_version: 1, tool: 'opensim-browser-check', standard: 'shared-r41',
    started_at: new Date().toISOString(), verdict: 'RUNNING', checks: [], screenshots: [], matrix: [],
    automated_scope: 'OpenSim DOM/HTTP/mesh/metrics/export and specified browser paths, not complete release certification',
    manual_gates: [
      { rule: 'WEB-A11Y-009', status: 'NOT_RUN', reason: 'No human desktop/mobile screen-reader combination was tested; AX is automated only.' },
      { rule: 'PHYSICAL-TOUCH', status: 'NOT_RUN', reason: 'CDP touch activation uses Tab/Shift+Tab plus keyboard editing; not pure-touch, physical-device or software-keyboard verification.' },
      { rule: 'SCREENSHOT-REVIEW', status: 'NOT_RUN', reason: 'Screenshot capture is not a human composition/readability review.' },
    ],
    uncovered_automatic: [
      'G5-performance: production/mobile network and CPU budgets, LCP/INP/CLS, compression/cache/font/third-party-failure checks',
      'Full G3: all state contrast, focus-border contrast, complex/image backgrounds, reading-order and dialog-trap audit',
      'WEB-BASE-003 / WEB-RWD-001: mobile-first token provenance needs source review; CSSOM widths alone do not prove it',
      'WEB-RWD-005: isolated component narrow-container tests',
      'WEB-TYPE-002/003: complete prose line-height and script-aware line-length audit',
      'G6: unavailable loading/empty state fixtures, long-text injection and interface-failure fault isolation',
      'Native text-only 200% and browser UI 400% zoom: tool records computed-font stress and CSS reflow equivalents',
    ],
    release_verdict: 'NOT_CERTIFIED',
  };
  let exitCode = 2;
  try {
    options = parseArgs(process.argv.slice(2));
    if (options.help) {
      exitCode = 0;
      console.log('Usage: node tools/browser_check.mjs --url http://127.0.0.1:8772/ --out ' + OUTPUT_ROOT);
      console.log('       node tools/browser_check.mjs --selftest --out ' + OUTPUT_ROOT);
      console.log('Options: --chrome PATH --timeout-ms 1000..120000 --no-sandbox (explicit local-only opt-in)');
      return;
    }
    run = await outputDirectory(options.out);
    const git = spawnSync('git', ['-C', path.resolve(HERE, '..'), 'rev-parse', 'HEAD'], { encoding: 'utf8' });
    requireCheck(git.status === 0, 'BUILD-IDENTITY', `Cannot read commit SHA: ${git.stderr}`);
    report.commit = git.stdout.trim();
    report.tool_sha256 = digest(await fs.readFile(fileURLToPath(import.meta.url)));
    report.target_url = options.url ?? null;
    report.mode = options.selftest ? 'HARNESS_SELFTEST_NOT_OPENSIM' :
      options.probe_input ? 'INPUT_PROBE_NOT_FULL_ACCEPTANCE' :
        options.probe_screenshot ? 'SCREENSHOT_PROBE_NOT_FULL_ACCEPTANCE' : 'OPENSIM_ACCEPTANCE';
    report.contract = CONTRACT;
    report.output = run;
    harness = new Harness(options, run, report);
    harness.log(`START mode=${report.mode} out=${run}`);
    if (options.selftest) await selftest(harness);
    else {
      await harness.launch();
      if (options.probe_input) await harness.probeInput();
      else if (options.probe_screenshot) await harness.probeScreenshot();
      else await harness.runAcceptance();
    }
    report.verdict = options.selftest ? 'SELFTEST_PASS' :
      options.probe_input ? 'INPUT_PROBE_PASS' :
        options.probe_screenshot ? 'SCREENSHOT_PROBE_PASS' : 'AUTOMATED_PASS';
    exitCode = 0;
  } catch (error) {
    report.verdict = 'FAIL';
    report.failure = { rule: error.rule ?? 'HARNESS', message: error.message,
      coordinate: harness?.coordinate ?? 'configuration', detail: error.detail ?? {}, stack: error.stack };
    exitCode = error instanceof CheckFailure && ![
      'CLI', 'OUTPUT-BOUNDARY', 'LOCAL-ONLY', 'BUILD-IDENTITY', 'WAIT-FAIL',
    ].includes(error.rule) ? 1 : 2;
    console.error(`BROWSER_CHECK FAIL rule=${report.failure.rule} coordinate=${report.failure.coordinate} ${error.message}`);
    if (harness?.session && harness.chrome?.exitCode === null) {
      if (options?.probe_input) {
        try { report.probe_events = await harness.evaluate(() => window.__opensimInputEvents); }
        catch (probeError) { report.probe_capture_failure = probeError.message; }
      }
      try { await harness.screenshot('failure'); }
      catch (captureError) {
        report.failure_screenshot_error = { message: captureError.message, stack: captureError.stack };
      }
    }
  } finally {
    if (harness) {
      try { await harness.close(); }
      catch (error) {
        report.cleanup_failure = { message: error.message, stack: error.stack };
        report.verdict = 'FAIL';
        exitCode = 2;
        console.error(`BROWSER_CHECK cleanup failed: ${error.message}`);
      }
      report.browser_errors = harness.errors;
      report.downloads = harness.downloads;
    }
    report.finished_at = new Date().toISOString();
    report.exit_code = exitCode;
    if (run) {
      if (harness?.logTask) {
        try { await harness.logTask; }
        catch (error) {
          report.first_failure ??= report.failure ?? persistenceCause(error, { stage: "queued-log", path: path.join(run, "run.log") });
          report.verdict = "FAIL"; exitCode = 2;
        }
      }
      const completed = await finalizeBrowser(run, report, exitCode, options?.["finalize-fixture"]);
      exitCode = completed.rc;
      console.log(`BROWSER_CHECK verdict=${report.verdict} rc=${exitCode} checks=${report.checks.length} screenshots=${report.screenshots.length} report=${completed.path}`);
    }
    process.exitCode = exitCode;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
