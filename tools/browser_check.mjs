#!/usr/bin/env node
/**
 * OpenSim v1 real Chromium acceptance, without installing dependencies.
 *
 * node tools/browser_check.mjs --url http://127.0.0.1:8772/ \
 *   --out /hf3fs-jd/data/tmp/opensim-fluid-v1/browser
 * node tools/browser_check.mjs --selftest \
 *   --out /hf3fs-jd/data/tmp/opensim-fluid-v1/browser
 * --probe-input with --url runs only the 390px solve/export/input regression.
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
    if (['--selftest', '--help', '--no-sandbox', '--probe-input'].includes(argument)) {
      options[argument.slice(2).replaceAll('-', '_')] = true;
    } else {
      requireCheck(['--url', '--out', '--chrome', '--timeout-ms'].includes(argument),
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
  requireCheck(!(options.selftest && options.probe_input), 'CLI', '--selftest and --probe-input are exclusive');
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
  async screenshot(name) {
    await this.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const metrics = await this.send('Page.getLayoutMetrics');
    const content = metrics.cssContentSize ?? metrics.contentSize;
    requireCheck(content.width > 0 && content.height > 0 && content.height <= 20000,
      'SCREENSHOT', 'Empty/unbounded screenshot content', { content });
    const response = await this.send('Page.captureScreenshot', {
      format: 'png', captureBeyondViewport: true, fromSurface: true,
    });
    const buffer = Buffer.from(response.data, 'base64');
    const file = `${name}.png`;
    const artifact = { file, coordinate: this.coordinate, ...validatePng(buffer) };
    await fs.writeFile(path.join(this.run, file), buffer, { flag: 'wx' });
    this.report.screenshots.push(artifact);
    return artifact;
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
      const ready = await this.evaluate(() =>
        !document.querySelector('#solve-button').disabled &&
        !document.querySelector('#export-button').disabled &&
        /[0-9]/.test(document.querySelector('#metric-flow_rate_m3_s').textContent));
      return ready ? current : false;
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
    await this.check('DOM-CONTRACT', `${name}: configure default case`, () => this.config(DEFAULT_CONFIG, keyboard));
    const baseline = await this.check('FEM-METRICS', `${name}: real solve and all FEM metrics`,
      () => this.solve(DEFAULT_CONFIG, keyboard));
    const field = await this.canvas();
    await this.activate('#view-mesh', keyboard);
    const mesh = await this.waitFor('Mesh redraw', async () => {
      const rendered = await this.canvas();
      return rendered.pixel_hash !== field.pixel_hash ? rendered : false;
    });
    await this.screenshot(`${name}-mesh`);
    await this.activate('#view-field', keyboard);
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
      await this.activate('#reset-button', keyboard);
      const actual = await this.evaluate(keys => Object.fromEntries(keys.map(key =>
        [key, key === 'geometry' ? document.querySelector(`#${key}`).value : Number(document.querySelector(`#${key}`).value)])),
      Object.keys(DEFAULT_CONFIG));
      requireCheck(JSON.stringify(actual) === JSON.stringify(DEFAULT_CONFIG), 'FEM-RESET', 'Reset did not restore defaults', { actual });
      return actual;
    });
    await this.screenshot(`${name}-field`);
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
    await this.evaluate(() => {
      const elements = [document.documentElement, ...document.querySelectorAll('body,body *')];
      const sizes = elements.map(element => parseFloat(getComputedStyle(element).fontSize));
      elements.forEach((element, index) => element.style.setProperty('font-size', `${sizes[index] * 2}px`, 'important'));
    });
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
    await this.waitFor('Initial actual solve releases form controls', () => this.evaluate(() =>
      !document.querySelector('#geometry').disabled && !document.querySelector('#solve-button').disabled));
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
<p id="error" role="alert" tabindex="-1"></p><p id="state" role="status" aria-live="polite">Fixture loaded</p>
<canvas id="flow-canvas" width="200" height="120" aria-label="Fixture drawing"></canvas></main>
<script>
const canvas=document.getElementById('flow-canvas'),ctx=canvas.getContext('2d');
let color=0;
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
      options.probe_input ? 'INPUT_PROBE_NOT_FULL_ACCEPTANCE' : 'OPENSIM_ACCEPTANCE';
    report.contract = CONTRACT;
    report.output = run;
    harness = new Harness(options, run, report);
    harness.log(`START mode=${report.mode} out=${run}`);
    if (options.selftest) await selftest(harness);
    else {
      await harness.launch();
      if (options.probe_input) await harness.probeInput();
      else await harness.runAcceptance();
    }
    report.verdict = options.selftest ? 'SELFTEST_PASS' :
      options.probe_input ? 'INPUT_PROBE_PASS' : 'AUTOMATED_PASS';
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
      await fs.writeFile(path.join(run, 'report.json'), json(report), { flag: 'wx' });
      const reread = JSON.parse(await fs.readFile(path.join(run, 'report.json'), 'utf8'));
      assert.equal(reread.verdict, report.verdict);
      assert.equal(reread.exit_code, exitCode);
      await fs.appendFile(path.join(run, 'run.log'),
        `${report.finished_at} BROWSER_CHECK verdict=${report.verdict} rc=${exitCode} report=${path.join(run, 'report.json')}\n`);
      console.log(`BROWSER_CHECK verdict=${report.verdict} rc=${exitCode} checks=${report.checks.length} screenshots=${report.screenshots.length} report=${path.join(run, 'report.json')}`);
    }
    process.exitCode = exitCode;
  }
}

await main();
