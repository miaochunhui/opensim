#!/usr/bin/env node
/**
 * Single cold Chromium G5 laboratory observation. No installs, reloads, retries,
 * warm-cache selection, production mutations or INP claims.
 * See docs/testing/PERFORMANCE.md for metric definitions and fixture contracts.
 */
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { EventEmitter } from 'node:events';
import { lookup } from 'node:dns/promises';
import * as fs from 'node:fs/promises';
import http from 'node:http';
import { isIP } from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUT = '/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-perf';
const CHROME = '/home/mch/.cache/ms-playwright/chromium-1187/chrome-linux/chrome';
export const BASELINE = Object.freeze({
  id: 'opensim-mobile-cold-v1', width: 360, height: 800, device_scale_factor: 1,
  mobile: true, touch: true, latency_ms: 150, download_bytes_s: 200000,
  upload_bytes_s: 93750, cpu_rate: 4, observation_ms: 8000,
  cache_disabled: true, service_worker_bypassed: true, samples: 1,
  timeout_ms: 30000, retries: 0,
});
export const LIMITS = Object.freeze({
  lcp_ms: 2500, cls: 0.1, interaction_latency_ms: 200,
  critical_bytes: 200000, first_screen_bytes: 1000000,
  javascript_bytes: 300000, font_bytes: 150000,
});
const SOURCES = ['tools/render_page.py', 'web/app.js', 'web/app.css', 'solver.py', 'server.py'];
const CONFIG = Object.freeze({ geometry: 'circle', width_mm: 4, height_mm: 4, length_mm: 100,
  pressure_pa: 1, viscosity_pa_s: 0.001, density_kg_m3: 998, resolution: 16 });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const sha = value => createHash('sha256').update(value).digest('hex');
const json = value => JSON.stringify(value, null, 2) + '\n';
const finite = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
class Failure extends Error {
  constructor(rule, message, detail = {}) {
    super(message); this.rule = rule; this.detail = detail;
  }
}
function need(condition, rule, message, detail) {
  if (!condition) throw new Failure(rule, message, detail);
}
function safeURL(value) {
  const url = new URL(value);
  url.username = ''; url.password = ''; url.hash = '';
  for (const [key, val] of url.searchParams) {
    if (!/^[a-f0-9]{8,64}$/i.test(val)) url.searchParams.set(key, '[redacted]');
  }
  return url.href;
}
export function targetScope(value) {
  const url = new URL(value);
  need(!url.username && !url.password && !url.hash && !url.search,
    'CLI', 'Target must have no credentials, query or fragment');
  need(['/', '/index-v1.html'].includes(url.pathname), 'CLI', 'Only the declared OpenSim page is allowed');
  if (url.protocol === 'https:' && url.hostname === 'opensim.ai4everyone.top' && !url.port) {
    return { url: url.href, scope: 'PUBLIC_LAB' };
  }
  need(url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname),
    'CLI', 'Only exact OpenSim HTTPS or loopback HTTP is allowed');
  return { url: url.href, scope: 'LOCAL_PREFLIGHT' };
}
function publicAddress(address) {
  if (isIP(address) === 4) {
    const [a, b] = address.split('.').map(Number);
    return ![0, 10, 127].includes(a) && a < 224 &&
      !(a === 100 && b >= 64 && b <= 127) && !(a === 169 && b === 254) &&
      !(a === 172 && b >= 16 && b <= 31) && !(a === 192 && [0, 168].includes(b)) &&
      !(a === 198 && [18, 19, 51].includes(b)) && !(a === 203 && b === 0);
  }
  if (isIP(address) === 6) {
    return /^[23][a-f0-9]{3}:/i.test(address) && !/^2001:(db8|0*2|1[0-9a-f]):/i.test(address);
  }
  return false;
}
export function parseArgs(argv) {
  const options = { chrome: CHROME, out: OUT };
  const seen = new Set();
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    need(!seen.has(arg), 'CLI', `Duplicate ${arg}`); seen.add(arg);
    if (['--help', '--selftest'].includes(arg)) {
      need(!options[arg.slice(2)], 'CLI', `Duplicate ${arg}`);
      options[arg.slice(2)] = true;
    } else {
      need(['--url', '--out', '--chrome', '--parse', '--fixtures'].includes(arg), 'CLI', `Unknown ${arg}`);
      need(argv[i + 1] && !argv[i + 1].startsWith('--'), 'CLI', `Missing ${arg}`);
      const key = arg.slice(2);
      need(!Object.hasOwn(options, key) || ['out', 'chrome'].includes(key), 'CLI', `Duplicate ${arg}`);
      options[key] = argv[++i];
    }
  }
  if (options.help) return options;
  need([options.url, options.parse, options.selftest].filter(Boolean).length === 1,
    'CLI', 'Choose exactly one of --url, --parse, --selftest');
  need(Boolean(options.fixtures) === Boolean(options.selftest),
    'CLI', '--selftest requires --fixtures; fixtures cannot be used as target evidence');
  options.out = path.resolve(options.out);
  need(options.out === OUT || options.out.startsWith(OUT + path.sep), 'OUTPUT', `Output must be inside ${OUT}`);
  if (options.url) Object.assign(options, targetScope(options.url));
  return options;
}

// Installed before navigation. The second rAF is a conservative bound: a paint
// opportunity lies between the two callbacks. It is NOT an Event Timing INP.
function instrumentation() {
  const state = window.__opensimPerf = {
    supported: [...PerformanceObserver.supportedEntryTypes], lcp: [], shifts: [],
    events: [], paints: [], observer_errors: [], stop_lcp: false,
  };
  const observe = (type, callback, extra = {}) => {
    if (!state.supported.includes(type)) { state.observer_errors.push(`Unsupported ${type}`); return; }
    const observer = new PerformanceObserver(list => callback(list.getEntries()));
    observer.observe({ type, buffered: true, ...extra });
  };
  observe('largest-contentful-paint', entries => {
    if (!state.stop_lcp) for (const e of entries) state.lcp.push({
      startTime: e.startTime, renderTime: e.renderTime, loadTime: e.loadTime,
      size: e.size, element: e.element?.id || e.element?.tagName || null,
    });
  });
  observe('layout-shift', entries => {
    for (const e of entries) state.shifts.push({
      startTime: e.startTime, value: e.value, hadRecentInput: e.hadRecentInput,
    });
  });
  observe('event', entries => {
    for (const e of entries) state.events.push({
      name: e.name, startTime: e.startTime, duration: e.duration,
      processingStart: e.processingStart, processingEnd: e.processingEnd,
      interactionId: e.interactionId, target: e.target?.id || null,
    });
  }, { durationThreshold: 16 });
  for (const type of ['pointerdown', 'pointerup', 'touchstart', 'touchend', 'click']) {
    document.addEventListener(type, event => {
      if (!event.isTrusted) return;
      const target = event.target.closest?.('button')?.id ?? event.target.id ?? null;
      const row = { type, target, trusted: true, start_ms: event.timeStamp,
        capture_ms: performance.now(), bound_ms: null };
      state.paints.push(row);
      requestAnimationFrame(() => requestAnimationFrame(() => {
        row.end_ms = performance.now();
        row.bound_ms = row.end_ms - row.start_ms;
      }));
    }, { capture: true, passive: true });
  }
}
export function clsValue(shifts) {
  need(Array.isArray(shifts), 'METRICS', 'Missing layout-shift observations');
  let max = 0, value = 0, start = null, last = null;
  for (const e of [...shifts].sort((a, b) => a.startTime - b.startTime)) {
    need(finite(e.startTime) && finite(e.value) && typeof e.hadRecentInput === 'boolean',
      'METRICS', 'Invalid layout-shift observation');
    if (e.hadRecentInput) continue;
    if (start === null || e.startTime - last >= 1000 || e.startTime - start >= 5000) {
      value = 0; start = e.startTime;
    }
    value += e.value; last = e.startTime; max = Math.max(max, value);
  }
  return max;
}
export function evaluateSample(sample) {
  need(sample && sample.metrics && Array.isArray(sample.resources) && sample.resources.length,
    'REPORT', 'Empty sample, metrics or resource set');
  const checks = [];
  const check = (rule, name, ok, detail) => checks.push({
    rule, name, status: ok ? 'PASS' : 'FAIL', detail,
    url: sample.target_url, viewport: [BASELINE.width, BASELINE.height],
    reproduce: 'Single fresh-profile invocation with opensim-mobile-cold-v1; no reload/retry',
  });
  need(resourcesValid(sample.resources), 'REPORT', 'Invalid resource byte accounting');
  for (const [metric, limit] of Object.entries(LIMITS).slice(0, 3)) {
    const value = sample.metrics[metric];
    check('WEB-PERF-001', metric, finite(value) && (metric !== 'lcp_ms' || value > 0) &&
      value <= limit, { value, limit });
  }
  check('WEB-PERF-001', 'observed-metrics-not-defaults', sample.metrics.lcp_count > 0 &&
    sample.metrics.cls_observed === true && sample.metrics.interaction_count >= 3 &&
    sample.metrics.metric_name === 'EVENT_TO_PAINT_UPPER_BOUND_NOT_INP',
  { lcp_count: sample.metrics.lcp_count, cls_observed: sample.metrics.cls_observed,
    interaction_count: sample.metrics.interaction_count });
  const required = ['#view-mesh', '#view-field', '#solve-button'];
  check('WEB-PERF-001', 'trusted-core-interactions', required.every(selector =>
    sample.interactions?.some(row => row.selector === selector && row.click_count === 1 &&
      finite(row.latency_ms) && row.latency_ms <= LIMITS.interaction_latency_ms)),
  { required, interactions: sample.interactions });
  check('G5-CONTENT', 'real-solve-and-visible-result', sample.content?.ready === true &&
    sample.content.text_length > 100 && sample.content.canvas_colors > 3 &&
    sample.content.solve_count >= 2 && sample.content.nodes > 10 &&
    sample.content.triangles > 10 && sample.content.max_velocity > 0,
  sample.content);
  const resources = sample.resources;
  const total = rows => rows.reduce((sum, r) => sum + r.transfer_bytes, 0);
  check('WEB-PERF-002', 'complete-cold-transfer-accounting', resources.every(r =>
    finite(r.transfer_bytes) && r.finished === true && !r.cached && r.status === 200),
  { count: resources.length, invalid: resources.filter(r =>
    !finite(r.transfer_bytes) || !r.finished || r.cached || r.status !== 200).map(r => ({
      url: r.url, status: r.status, bytes: r.transfer_bytes, finished: r.finished, cached: r.cached,
    })) });
  const critical = resources.filter(r => ['Document', 'Stylesheet', 'Script'].includes(r.type));
  const scripts = resources.filter(r => r.type === 'Script');
  const fonts = resources.filter(r => r.type === 'Font');
  for (const [rule, name, rows, limit] of [
    ['WEB-PERF-002', 'critical-html-css-all-initial-js', critical, LIMITS.critical_bytes],
    ['WEB-PERF-002', 'all-initial-resources-conservative', resources, LIMITS.first_screen_bytes],
    ['WEB-PERF-003', 'all-initial-javascript', scripts, LIMITS.javascript_bytes],
    ['WEB-PERF-004', 'initial-fonts', fonts, LIMITS.font_bytes],
  ]) check(rule, name, total(rows) <= limit, { bytes: total(rows), limit, count: rows.length });
  check('WEB-PERF-002', 'required-page-resource-types', ['Document', 'Stylesheet', 'Script'].every(type =>
    resources.some(r => r.type === type)), { types: resources.map(r => r.type) });
  const texts = resources.filter(r => /html|css|javascript|json|text\//i.test(r.mime));
  check('WEB-PERF-005', 'text-wire-compression', texts.length > 0 &&
    texts.every(r => finite(r.decoded_body_bytes) && r.decoded_body_bytes <= 512 || /^(gzip|br)$/.test(r.encoding)),
  { resources: texts.map(r => ({ url: r.url, encoding: r.encoding, bytes: r.transfer_bytes })) });
  const assets = resources.filter(r => ['Stylesheet', 'Script', 'Font', 'Image'].includes(r.type));
  check('WEB-PERF-005', 'static-cache-policy', assets.length > 0 && assets.every(r => {
    if (/no-store/i.test(r.cache_control)) return false;
    const age = Number(/(?:^|,)\s*max-age=(\d+)/i.exec(r.cache_control)?.[1] ?? -1);
    if (['Stylesheet', 'Script'].includes(r.type)) {
      const fingerprint = /^\/web\/app\.([a-f0-9]{64})\.(?:js|css)$/.exec(new URL(r.url).pathname)?.[1];
      return fingerprint && r.fingerprinted &&
      fingerprint === r.fingerprint_sha256 && fingerprint === r.body_sha256 && age >= 31536000 &&
      /(?:^|,)\s*immutable\s*(?:,|$)/i.test(r.cache_control) &&
      /(?:^|,)\s*public(?:,|$)/i.test(r.cache_control);
    }
    return r.fingerprinted ? age >= 31536000 && /immutable/i.test(r.cache_control) :
      Boolean(r.etag || r.last_modified) && /must-revalidate|no-cache/i.test(r.cache_control);
  }), { assets: assets.map(r => ({ url: r.url, fingerprinted: r.fingerprinted,
    fingerprint_sha256: r.fingerprint_sha256, body_sha256: r.body_sha256,
    cache_control: r.cache_control, etag: r.etag, last_modified: r.last_modified })) });
  check('WEB-PERF-004', 'font-policy-and-fallback', sample.fonts?.ready === true &&
    sample.fonts.errors?.length === 0 && sample.fonts.preloads <= 1 &&
    sample.fonts.fallback_verified === true &&
    sample.fonts.faces?.every(face => ['swap', 'optional'].includes(face.display) && face.subset),
  sample.fonts);
  check('WEB-PERF-005', 'mobile-images', sample.images?.every(image => image.width > 0 &&
    image.height > 0 && image.intrinsic_width <= image.width * 2 &&
    /image\/(avif|webp|svg\+xml)/.test(image.mime)), { images: sample.images });
  check('WEB-PERF-006', 'zero-third-party-with-enforced-isolation',
    sample.third_party?.length === 0 && sample.isolation === 'BLOCK_ALL_OFF_ORIGIN_BEFORE_REQUEST',
    { third_party: sample.third_party, policy: sample.isolation });
  check('G5-NETWORK', 'no-redirect-or-network-failure',
    sample.redirects?.length === 0 && sample.network_errors?.length === 0 &&
    sample.runtime_errors?.length === 0,
  { redirects: sample.redirects, network_errors: sample.network_errors, runtime_errors: sample.runtime_errors });
  return checks;
}
function resourcesValid(resources) {
  return resources.every(r => finite(r.transfer_bytes) && r.transfer_bytes > 0 &&
    typeof r.url === 'string' && typeof r.type === 'string');
}

class CDP extends EventEmitter {
  constructor(socket) {
    super(); this.socket = socket; this.id = 0; this.pending = new Map();
    socket.addEventListener('message', event => {
      const msg = JSON.parse(event.data.toString());
      if (!msg.id) return this.emit(msg.method, msg.params ?? {}, msg.sessionId);
      const pending = this.pending.get(msg.id);
      if (!pending) return;
      clearTimeout(pending.timer); this.pending.delete(msg.id);
      if (msg.error) pending.reject(new Error(`${pending.method}: ${JSON.stringify(msg.error)}`));
      else pending.resolve(msg.result);
    });
    socket.addEventListener('close', () => this.reject(new Error('CDP closed')));
    socket.addEventListener('error', () => this.reject(new Error('CDP socket error')));
  }
  reject(error) {
    for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(error); }
    this.pending.clear();
  }
  send(method, params = {}, sessionId) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id);
        reject(new Error(`CDP timeout: ${method}`)); }, BASELINE.timeout_ms);
      this.pending.set(id, { resolve, reject, timer, method });
      this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }
}
export class Browser {
  constructor(options, run, report) {
    Object.assign(this, { options, run, report });
    this.requests = new Map(); this.tasks = []; this.thirdParty = []; this.redirects = [];
    this.networkErrors = []; this.runtimeErrors = []; this.solves = []; this.bodies = new Map();
    this.logs = []; this.phase = 'startup';
  }
  send(method, params) { return this.cdp.send(method, params, this.session); }
  async eval(fn, ...args) {
    const reply = await this.send('Runtime.evaluate', {
      expression: `(${fn.toString()})(...${JSON.stringify(args)})`,
      awaitPromise: true, returnByValue: true,
    });
    need(!reply.exceptionDetails, 'BROWSER', reply.exceptionDetails?.exception?.description ??
      reply.exceptionDetails?.text ?? 'Evaluation failed');
    return reply.result.value;
  }
  async wait(label, predicate) {
    const start = Date.now();
    while (Date.now() - start < BASELINE.timeout_ms) {
      if (this.chromeError) throw this.chromeError;
      if (this.chrome.exitCode !== null) throw new Failure('BROWSER', 'Chromium exited before measurement');
      const value = await predicate(); if (value) return value;
      await sleep(50);
    }
    throw new Failure('WAIT', `${label} not observed`, { phase: this.phase, timeout_ms: BASELINE.timeout_ms });
  }
  async launch() {
    need(typeof WebSocket === 'function', 'DEPENDENCY', 'Existing Node native WebSocket required; no installation');
    await fs.access(this.options.chrome, fs.constants.X_OK);
    if (this.options.scope === 'PUBLIC_LAB') {
      const addresses = await lookup('opensim.ai4everyone.top', { all: true, verbatim: true });
      this.report.public_dns = addresses;
      need(addresses.length > 0 && addresses.every(row => publicAddress(row.address)),
        'G5-NETWORK', 'Public target DNS is empty or not exclusively public', { addresses });
    }
    const profile = path.join(this.run, 'chromium-profile');
    const flags = ['--headless=new', '--remote-debugging-address=127.0.0.1',
      '--remote-debugging-port=0', `--user-data-dir=${profile}`, '--no-first-run',
      '--no-default-browser-check', '--disable-background-networking', '--disable-component-update',
      '--disable-default-apps', '--disable-breakpad', '--password-store=basic', '--no-proxy-server',
      '--window-size=360,800', 'about:blank'];
    this.chrome = spawn(this.options.chrome, flags, { stdio: ['ignore', 'pipe', 'pipe'] });
    this.chrome.on('error', error => { this.chromeError = error; });
    this.chrome.stdout.on('data', data => this.logs.push(data));
    this.chrome.stderr.on('data', data => this.logs.push(data));
    const portFile = path.join(profile, 'DevToolsActivePort');
    let endpoint;
    await this.wait('DevToolsActivePort', async () => {
      try { endpoint = (await fs.readFile(portFile, 'utf8')).trim().split('\n'); return endpoint.length === 2; }
      catch (e) { if (e.code === 'ENOENT') return false; throw e; }
    });
    need(/^\d+$/.test(endpoint[0]) && endpoint[1].startsWith('/devtools/browser/'), 'CDP', 'Invalid endpoint');
    const socket = new WebSocket(`ws://127.0.0.1:${endpoint[0]}${endpoint[1]}`);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('CDP connect timeout')), BASELINE.timeout_ms);
      socket.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
      socket.addEventListener('error', () => { clearTimeout(timer); reject(new Error('CDP connect error')); },
        { once: true });
    });
    this.cdp = new CDP(socket);
    this.report.browser = await this.cdp.send('Browser.getVersion');
    this.report.chromium_flags = flags;
    const target = await this.cdp.send('Target.createTarget', { url: 'about:blank' });
    this.session = (await this.cdp.send('Target.attachToTarget', { targetId: target.targetId, flatten: true })).sessionId;
    for (const method of ['Page.enable', 'Runtime.enable', 'Network.enable', 'Performance.enable']) await this.send(method);
    await this.send('Network.setCacheDisabled', { cacheDisabled: true });
    await this.send('Network.setBypassServiceWorker', { bypass: true });
    await this.send('Network.emulateNetworkConditions', { offline: false, latency: BASELINE.latency_ms,
      downloadThroughput: BASELINE.download_bytes_s, uploadThroughput: BASELINE.upload_bytes_s,
      connectionType: 'cellular4g' });
    await this.send('Emulation.setCPUThrottlingRate', { rate: BASELINE.cpu_rate });
    await this.send('Emulation.setDeviceMetricsOverride', { width: BASELINE.width, height: BASELINE.height,
      deviceScaleFactor: BASELINE.device_scale_factor, mobile: true });
    await this.send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 1 });
    await this.send('Page.addScriptToEvaluateOnNewDocument', { source: `(${instrumentation.toString()})()` });
    this.report.baseline_commands_acknowledged = true;
    this.cdp.on('Network.requestWillBeSent', (e, session) => {
      if (session !== this.session) return;
      if (e.redirectResponse) this.redirects.push({ from: safeURL(e.redirectResponse.url),
        to: safeURL(e.request.url), status: e.redirectResponse.status, phase: this.phase });
      this.requests.set(e.requestId, { id: e.requestId, url: e.request.url, method: e.request.method,
        type: e.type, start: e.timestamp, phase: this.phase, finished: false });
    });
    this.cdp.on('Network.responseReceived', (e, session) => {
      if (session !== this.session) return;
      const r = this.requests.get(e.requestId); if (!r) return;
      const headers = Object.fromEntries(Object.entries(e.response.headers).map(([k, v]) => [k.toLowerCase(), String(v)]));
      Object.assign(r, { status: e.response.status, mime: e.response.mimeType, headers,
        cached: Boolean(e.response.fromDiskCache || e.response.fromServiceWorker || e.response.fromPrefetchCache),
        timing: e.response.timing, protocol: e.response.protocol, remote_ip: e.response.remoteIPAddress });
    });
    this.cdp.on('Network.loadingFinished', (e, session) => {
      if (session !== this.session) return;
      const r = this.requests.get(e.requestId); if (!r) return;
      Object.assign(r, { finished: true, transfer_bytes: e.encodedDataLength, end: e.timestamp });
      // Capture actual browser responses, never issue replacement HTTP requests.
      if (r.type === 'Document' || ['Script', 'Stylesheet', 'Fetch', 'Other'].includes(r.type) ||
          new URL(r.url).pathname === '/api/solve') {
        this.track((async () => {
          const body = await this.send('Network.getResponseBody', { requestId: e.requestId });
          const text = body.base64Encoded ? Buffer.from(body.body, 'base64').toString('utf8') : body.body;
          r.body_sha256 = sha(text); r.decoded_body_bytes = Buffer.byteLength(text);
          this.bodies.set(e.requestId, text);
          if (new URL(r.url).pathname === '/api/solve') {
            const result = JSON.parse(text);
            this.solves.push({ phase: r.phase, result, request_id: e.requestId });
            await fs.writeFile(path.join(this.run, `solve-${this.solves.length}.json`), json(result), { flag: 'wx' });
          }
        })(), 'response-capture');
      }
    });
    this.cdp.on('Network.loadingFailed', (e, session) => {
      if (session === this.session) this.networkErrors.push({ request_id: e.requestId,
        url: this.requests.has(e.requestId) ? safeURL(this.requests.get(e.requestId).url) : null,
        error: e.errorText, canceled: e.canceled === true, phase: this.phase });
    });
    this.cdp.on('Runtime.exceptionThrown', (e, session) => {
      if (session === this.session) this.runtimeErrors.push({ type: 'exception',
        message: e.exceptionDetails.exception?.description ?? e.exceptionDetails.text, phase: this.phase });
    });
    await this.send('Fetch.enable', { patterns: [
      { urlPattern: '*', requestStage: 'Request' }, { urlPattern: '*', requestStage: 'Response' },
    ] });
    this.cdp.on('Fetch.requestPaused', (e, session) => {
      if (session !== this.session) return;
      const url = new URL(e.request.url);
      const foreign = url.origin !== new URL(this.options.url).origin;
      const redirect = e.responseStatusCode >= 300 && e.responseStatusCode < 400;
      if (foreign) this.thirdParty.push({ url: safeURL(url.href), type: e.resourceType, phase: this.phase,
        action: 'BLOCKED_BEFORE_NETWORK' });
      if (redirect) this.redirects.push({ from: safeURL(url.href), status: e.responseStatusCode,
        phase: this.phase, action: 'BLOCKED_BEFORE_FOLLOW' });
      this.track(this.send(foreign || redirect ? 'Fetch.failRequest' : 'Fetch.continueRequest',
        foreign || redirect ? { requestId: e.requestId, errorReason: 'BlockedByClient' } :
          { requestId: e.requestId }), 'network-interception');
    });
  }
  track(promise, type) {
    this.tasks.push(promise.catch(error => { this.runtimeErrors.push({
      type, message: error.message, phase: this.phase }); }));
  }
  async touch(selector) {
    const before = await this.eval(selector => {
      const element = document.querySelector(selector);
      if (!element || element.disabled) throw new Error(`Missing/disabled ${selector}`);
      element.scrollIntoView({ block: 'center', behavior: 'instant' });
      return { time: performance.now(), canvas: document.querySelector('#flow-canvas').toDataURL(),
        pressed: element.getAttribute('aria-pressed') };
    }, selector);
    await this.eval(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const point = await this.eval(selector => {
      const element = document.querySelector(selector), rect = element.getBoundingClientRect();
      const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
      if (!(x >= 0 && x < innerWidth && y >= 0 && y < innerHeight) ||
          !element.contains(document.elementFromPoint(x, y))) throw new Error(`Covered/offscreen ${selector}`);
      return { x, y };
    }, selector);
    await this.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ ...point, id: 1 }] });
    await this.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
    const id = selector.slice(1);
    await this.wait(`Trusted click/paint ${selector}`, () => this.eval((id, start) =>
      window.__opensimPerf.paints.some(e => e.target === id && e.type === 'click' &&
        e.start_ms >= start && e.bound_ms !== null), id, before.time));
    const observed = await this.eval((id, start) => ({
      paints: window.__opensimPerf.paints.filter(e => e.target === id && e.start_ms >= start),
      events: window.__opensimPerf.events.filter(e => e.target === id && e.startTime >= start),
      pressed: document.getElementById(id).getAttribute('aria-pressed'),
      canvas_changed: document.querySelector('#flow-canvas').toDataURL(),
    }), id, before.time);
    need(observed.paints.every(e => finite(e.bound_ms)), 'METRICS', 'Unfinished interaction paint observation');
    if (selector !== '#solve-button') need(observed.pressed === String(before.pressed !== 'true') &&
      observed.canvas_changed !== before.canvas, 'G5-CONTENT', 'Touch did not change actual view', { selector });
    return { selector, click_count: observed.paints.filter(e => e.type === 'click').length,
      latency_ms: Math.max(...observed.paints.map(e => e.bound_ms), ...observed.events.map(e => e.duration)),
      paints: observed.paints, event_timing: observed.events };
  }
  async content() {
    return this.eval(() => {
      const canvas = document.querySelector('#flow-canvas');
      const colors = new Set();
      if (canvas) {
        const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        for (let i = 0; i < pixels.length; i += 64) colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]}`);
      }
      return { ready: document.readyState === 'complete' &&
        document.querySelector('#case-form')?.getAttribute('aria-busy') === 'false' &&
        document.querySelector('#export-button')?.disabled === false,
      text_length: document.body?.innerText.trim().length ?? 0, canvas_colors: colors.size,
      summary: document.querySelector('#result-summary')?.textContent, url: location.href };
    });
  }
  async measure() {
    this.phase = 'cold-baseline';
    const navigation = await this.send('Page.navigate', { url: this.options.url });
    need(!navigation.errorText, 'G5-NETWORK', navigation.errorText ?? 'Navigation failed');
    await this.wait('Fixed cold observation window', () => this.eval(() =>
      document.readyState === 'complete' && window.__opensimPerf && performance.now() >= 8000));
    await this.eval(() => { window.__opensimPerf.stop_lcp = true; });
    await Promise.all(this.tasks);
    const initial = [...this.requests.values()].filter(r => r.phase === 'cold-baseline').map(r => this.resource(r));
    const content = await this.content();
    this.partial = { phase: 'PARTIAL_COLD_OBSERVATION_NOT_COMPLETE', target_url: this.options.url,
      resources: initial, content, interactions: [], fonts: null, images: [], third_party: this.thirdParty,
      redirects: this.redirects, network_errors: this.networkErrors, runtime_errors: this.runtimeErrors,
      isolation: 'BLOCK_ALL_OFF_ORIGIN_BEFORE_REQUEST' };
    need(content.ready, 'G5-CONTENT', 'Real initial solve was not ready at fixed observation boundary', content);
    const config = this.solves[0]?.result?.config;
    need(config && Object.keys(config).length === Object.keys(CONFIG).length &&
      Object.entries(CONFIG).every(([key, value]) => config[key] === value),
    'G5-CONTENT', 'Initial real computation does not use fixed representative config', { config, expected: CONFIG });
    const interactions = [];
    this.phase = 'core-interactions';
    for (const selector of ['#view-mesh', '#view-field', '#solve-button']) {
      interactions.push(await this.touch(selector));
      this.partial.interactions = interactions;
    }
    await this.wait('Second real solve and visible result', async () => {
      await Promise.all(this.tasks);
      return this.solves.length >= 2 && (await this.content()).ready;
    });
    const finalContent = await this.content();
    const actual = this.solves.at(-1)?.result;
    Object.assign(finalContent, { solve_count: this.solves.length,
      nodes: actual?.mesh?.nodes_mm?.length, triangles: actual?.mesh?.triangles?.length,
      max_velocity: actual?.metrics?.max_velocity_m_s });
    const raw = await this.eval(() => window.__opensimPerf);
    need(raw.observer_errors.length === 0, 'METRICS', 'Unsupported/incomplete performance observers', raw);
    const policy = await this.eval(() => {
      const faces = [], errors = [];
      const walk = rules => {
        for (const rule of rules) {
          if (rule.type === CSSRule.FONT_FACE_RULE) faces.push({
            display: rule.style.getPropertyValue('font-display').trim(),
            subset: Boolean(rule.style.getPropertyValue('unicode-range').trim()),
            family: rule.style.getPropertyValue('font-family'),
          });
          if (rule.cssRules) walk(rule.cssRules);
        }
      };
      for (const sheet of document.styleSheets) {
        try { walk(sheet.cssRules); } catch (e) { errors.push(e.message); }
      }
      return { ready: document.fonts.status === 'loaded', faces, errors,
        preloads: document.querySelectorAll('link[rel="preload"][as="font"]').length,
        fallback: getComputedStyle(document.body).fontFamily };
    });
    policy.fallback_verified = policy.faces.length === 0 && !initial.some(r => r.type === 'Font');
    policy.fallback_scope = policy.fallback_verified ?
      'SYSTEM_FONTS_ONLY_NO_WEBFONT_REQUESTS' : 'WEBFONT_FAILURE_FALLBACK_NOT_VERIFIED';
    const images = await this.eval(() => [...document.images].map(image => ({
      url: image.currentSrc, width: image.getBoundingClientRect().width,
      height: image.getBoundingClientRect().height, intrinsic_width: image.naturalWidth,
    })));
    for (const image of images) image.mime = initial.find(r => r.url === image.url)?.mime ?? '';
    const screenshot = await this.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    const png = Buffer.from(screenshot.data, 'base64');
    need(png.length > 100 && png.toString('ascii', 12, 16) === 'IHDR', 'SCREENSHOT', 'Empty actual screenshot');
    await fs.writeFile(path.join(this.run, 'result.png'), png, { flag: 'wx' });
    const sample = {
      phase: 'ONE_COLD_NAVIGATION_PLUS_THREE_CORE_TOUCHES', target_url: this.options.url,
      metrics: { lcp_ms: raw.lcp.at(-1)?.startTime ?? null, lcp_count: raw.lcp.length,
        cls: clsValue(raw.shifts), cls_observed: raw.supported.includes('layout-shift'),
        interaction_latency_ms: Math.max(...interactions.map(row => row.latency_ms)),
        interaction_count: interactions.length, metric_name: 'EVENT_TO_PAINT_UPPER_BOUND_NOT_INP',
        inp_ms: null, inp_status: 'NOT_MEASURED_SINGLE_LAB_PATH_IS_NOT_FIELD_INP' },
      resources: initial, interactions, content: finalContent, fonts: policy, images,
      third_party: this.thirdParty, redirects: this.redirects, network_errors: this.networkErrors,
      runtime_errors: this.runtimeErrors, isolation: 'BLOCK_ALL_OFF_ORIGIN_BEFORE_REQUEST',
      raw_metrics: raw, cdp_performance: await this.send('Performance.getMetrics'),
      screenshot: { file: 'result.png', bytes: png.length, sha256: sha(png) },
    };
    this.partial = sample;
    this.phase = 'source-identity';
    const health = await this.eval(async () => {
      const response = await fetch('/api/health', { cache: 'no-store', credentials: 'omit' });
      if (!response.ok) throw new Error(`Health identity HTTP ${response.status}`);
      return response.json();
    });
    const local = {}, committed = {};
    for (const name of SOURCES) {
      local[name] = sha(await fs.readFile(path.join(ROOT, name)));
      const blob = spawnSync('git', ['-C', ROOT, 'show', `HEAD:${name}`], { maxBuffer: 8 * 1024 * 1024 });
      need(blob.status === 0, 'IDENTITY', `Cannot read committed ${name}`);
      committed[name] = sha(blob.stdout);
    }
    const doc = [...this.requests.values()].find(r => r.type === 'Document');
    const page = this.bodies.get(doc?.id);
    const assetsBound = ['js', 'css'].every(extension => [...this.requests.values()].some(r => {
      const pathname = new URL(r.url).pathname;
      return (pathname === `/web/app.${extension}` ||
        /^\/web\/app\.[a-f0-9]{64}\.(js|css)$/.test(pathname) && pathname.endsWith(`.${extension}`)) &&
        r.body_sha256 === local[`web/app.${extension}`];
    }));
    const same = sources => sources && Object.keys(sources).length === SOURCES.length &&
      SOURCES.every(name => sources[name] === local[name]);
    sample.source_identity = { bound: same(health.source_sha256) &&
      same(actual?.reproducibility?.source_sha256) && assetsBound &&
      Object.keys(local).every(name => local[name] === committed[name]) &&
      typeof health.page_build === 'string' && health.page_build.length >= 12 && page?.includes(health.page_build),
    local_source_sha256: local, committed_source_sha256: committed,
    health, solve_source_sha256: actual?.reproducibility?.source_sha256, assets_bound: assetsBound,
    page_build_seen: typeof health.page_build === 'string' && page?.includes(health.page_build),
    document_sha256: doc?.body_sha256 };
    return sample;
  }
  async measureFixture(metric) {
    this.phase = 'REAL_BROWSER_NEGATIVE_FIXTURE_NOT_G5';
    const navigation = await this.send('Page.navigate', { url: this.options.url });
    need(!navigation.errorText, 'SELFTEST', 'Fixture navigation failed');
    await this.wait('Fixture fixed observation', () => this.eval(() =>
      document.readyState === 'complete' && window.__opensimPerf && performance.now() >= 8000));
    await this.eval(() => { window.__opensimPerf.stop_lcp = true; });
    const interaction = await this.touch('#solve-button');
    const raw = await this.eval(() => window.__opensimPerf);
    need(raw.lcp.length > 0 && raw.observer_errors.length === 0 &&
      interaction.click_count === 1 && interaction.paints.every(p => p.trusted),
    'SELFTEST', 'Fixture did not produce real LCP and trusted event/paint observations');
    const metrics = { lcp_ms: raw.lcp.at(-1).startTime, cls: clsValue(raw.shifts),
      interaction_latency_ms: interaction.latency_ms };
    need(finite(metrics[metric]), 'SELFTEST', 'Fixture metric missing');
    return { scope: 'REAL_BROWSER_FIXTURE_NOT_G5', metrics, raw_metrics: raw, interaction,
      metric, threshold: LIMITS[metric], failed: metrics[metric] > LIMITS[metric],
      viewport: [BASELINE.width, BASELINE.height], browser: this.report.browser };
  }
  resource(r) {
    const h = r.headers ?? {};
    const url = new URL(r.url);
    return { url: safeURL(r.url), type: r.type, method: r.method, phase: r.phase,
      status: r.status ?? null, mime: r.mime ?? '', finished: r.finished,
      transfer_bytes: r.transfer_bytes ?? null, transfer_definition: 'CDP encodedDataLength including response headers (conservative compressed wire bytes)',
      encoding: h['content-encoding'] ?? '', cache_control: h['cache-control'] ?? '',
      etag: h.etag ?? '', last_modified: h['last-modified'] ?? '',
      fingerprinted: /^\/web\/app\.[a-f0-9]{64}\.(js|css)$/.test(url.pathname),
      fingerprint_sha256: /^\/web\/app\.([a-f0-9]{64})\.(?:js|css)$/.exec(url.pathname)?.[1] ?? null,
      cached: r.cached ?? false, protocol: r.protocol, remote_ip: r.remote_ip,
      timing: r.timing, body_sha256: r.body_sha256, decoded_body_bytes: r.decoded_body_bytes ?? null };
  }
  async close() {
    await Promise.all(this.tasks);
    if (this.chrome?.exitCode === null) {
      if (this.cdp) await this.cdp.send('Browser.close');
      else this.chrome.kill('SIGTERM');
    }
    if (this.chrome && this.chrome.exitCode === null) {
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Own Chromium did not close')), 5000);
        this.chrome.once('exit', () => { clearTimeout(timer); resolve(); });
      });
    }
    this.cdp?.socket.close();
    await fs.writeFile(path.join(this.run, 'chromium.log'), Buffer.concat(this.logs), { flag: 'wx' });
  }
}

export function validateReport(report) {
  need(report?.schema_version === 1 && report.tool === 'opensim-browser-performance' &&
    report.standard === 'shared-r41' && report.gate === 'G5-performance', 'REPORT', 'Unknown report schema');
  need(report.baseline && Object.keys(report.baseline).length === Object.keys(BASELINE).length &&
    Object.entries(BASELINE).every(([key, value]) => report.baseline[key] === value) &&
    report.baseline_commands_acknowledged === true, 'REPORT', 'Missing/altered fixed baseline');
  need(report.samples?.length === 1 && report.browser?.product?.startsWith('Chrome/'),
    'REPORT', 'Exactly one real Chromium sample required');
  const target = targetScope(report.target_url);
  need(report.scope === target.scope && report.samples[0].target_url === report.target_url,
    'REPORT', 'Target/scope mismatch');
  const sample = report.samples[0], raw = sample.raw_metrics;
  need(sample.phase === 'ONE_COLD_NAVIGATION_PLUS_THREE_CORE_TOUCHES' &&
    raw && Array.isArray(raw.lcp) && Array.isArray(raw.shifts) && raw.supported?.includes('layout-shift') &&
    raw.observer_errors?.length === 0 && sample.interactions?.length === 3,
  'REPORT', 'Missing complete raw observation and three real interactions');
  need(sample.metrics.lcp_ms === (raw.lcp.at(-1)?.startTime ?? null) &&
    sample.metrics.lcp_count === raw.lcp.length && sample.metrics.cls === clsValue(raw.shifts),
  'REPORT', 'LCP/CLS summary differs from raw browser entries');
  for (const row of sample.interactions) {
    need(row.paints?.length > 0 && Array.isArray(row.event_timing) &&
      row.paints.every(p => p.trusted === true && finite(p.start_ms) &&
        finite(p.end_ms) && p.bound_ms === p.end_ms - p.start_ms) &&
      row.click_count === row.paints.filter(p => p.type === 'click').length &&
      row.latency_ms === Math.max(...row.paints.map(p => p.bound_ms), ...row.event_timing.map(e => e.duration)),
    'REPORT', 'Interaction summary differs from trusted raw event/paint bounds');
  }
  need(sample.metrics.interaction_latency_ms === Math.max(...sample.interactions.map(r => r.latency_ms)),
    'REPORT', 'Aggregate interaction latency differs from raw samples');
  const identity = sample.source_identity;
  const matching = map => map && Object.keys(map).length === SOURCES.length &&
    SOURCES.every(name => /^[a-f0-9]{64}$/.test(identity.local_source_sha256?.[name] ?? '') &&
      map[name] === identity.local_source_sha256[name]);
  const identityBound = identity?.bound === true && matching(identity.committed_source_sha256) &&
    matching(identity.health?.source_sha256) && matching(identity.solve_source_sha256) &&
    identity.assets_bound === true && identity.page_build_seen === true &&
    /^[a-f0-9]{12,64}$/.test(identity.health?.page_build ?? '') &&
    identity.document_sha256 === sample.resources.find(r => r.type === 'Document')?.body_sha256 &&
    ['js', 'css'].every(ext => sample.resources.some(r =>
      r.body_sha256 === identity.local_source_sha256[`web/app.${ext}`] &&
      new URL(r.url).pathname.endsWith(`.${ext}`)));
  const checks = evaluateSample(sample);
  checks.push({ rule: 'G5-IDENTITY', name: 'served-page-assets-health-solve-bound-to-commit',
    status: identityBound ? 'PASS' : 'FAIL', detail: identity });
  if (target.scope === 'PUBLIC_LAB') checks.push({
    rule: 'G5-NETWORK', name: 'public-dns-and-actual-browser-connection',
    status: report.public_dns?.length > 0 && report.public_dns.every(row => publicAddress(row.address)) &&
      sample.resources.every(r => publicAddress(r.remote_ip)) ? 'PASS' : 'FAIL',
    detail: { dns: report.public_dns, connections: sample.resources.map(r => ({ url: r.url, address: r.remote_ip })) },
  });
  need(report.commit?.match(/^[a-f0-9]{40}$/) && report.tool_sha256?.match(/^[a-f0-9]{64}$/),
    'REPORT', 'Missing commit/tool identity');
  return checks;
}
async function outputDirectory(out) {
  const allowed = await fs.realpath('/hf3fs-jd/data/tmp');
  let ancestor = out;
  while (true) {
    try {
      const real = await fs.realpath(ancestor);
      need(real === allowed || real.startsWith(allowed + path.sep), 'OUTPUT', 'Symlink escapes external evidence root');
      break;
    } catch (e) { if (e.code !== 'ENOENT') throw e; ancestor = path.dirname(ancestor); }
  }
  await fs.mkdir(out, { recursive: true });
  const run = path.join(out, `run-${new Date().toISOString().replaceAll(/[:.]/g, '-')}-${randomUUID().slice(0, 8)}`);
  await fs.mkdir(run);
  await fs.writeFile(path.join(run, 'write-probe.txt'), 'seat-perf write/read probe\n', { flag: 'wx' });
  assert.equal(await fs.readFile(path.join(run, 'write-probe.txt'), 'utf8'), 'seat-perf write/read probe\n');
  return run;
}
async function offlineSelftest(directory, options, run, report) {
  const manifest = JSON.parse(await fs.readFile(path.join(directory, 'manifest.json'), 'utf8'));
  need(Array.isArray(manifest.cases) && manifest.cases.length >= 6, 'SELFTEST', 'At least six named file fixtures required');
  const names = new Set(), results = [];
  report.selftest = { count: 0, cases: results, browser_count: 0, browser_cases: [],
    scope: 'FILE_FIXTURES_NOT_G5', status: 'RUNNING' };
  for (const test of manifest.cases) {
    need(typeof test.file === 'string' && path.basename(test.file) === test.file &&
      !names.has(test.file) && ['PASS', 'FAIL', 'ERROR'].includes(test.expected),
    'SELFTEST', 'Invalid/duplicate fixture');
    names.add(test.file);
    const bytes = await fs.readFile(path.join(directory, test.file), 'utf8');
    let checks, outcome = 'ERROR', error;
    try {
      checks = validateReport(JSON.parse(bytes));
      outcome = checks.every(c => c.status === 'PASS') ? 'PASS' : 'FAIL';
    } catch (e) { error = { rule: e.rule ?? 'JSON', message: e.message }; }
    need(outcome === test.expected &&
      (!test.rule || checks?.some(c => c.rule === test.rule && c.status === 'FAIL') || error?.rule === test.rule),
    'SELFTEST', 'File fixture did not exercise expected failure', { file: test.file, outcome, expected: test.expected, error });
    results.push({ file: test.file, sha256: sha(bytes), outcome, expected: test.expected, error,
      failed_rules: checks?.filter(c => c.status === 'FAIL').map(c => c.rule) });
    report.selftest.count = results.length;
  }
  need(results.some(r => r.outcome === 'PASS') && results.filter(r => r.outcome === 'FAIL').length >= 3 &&
    results.some(r => r.outcome === 'ERROR'), 'SELFTEST', 'Missing positive, threshold or malformed fixture arm');
  const browserResults = [];
  report.selftest.browser_cases = browserResults;
  if (manifest.browser_cases) {
    need(Array.isArray(manifest.browser_cases) && manifest.browser_cases.length >= 3,
      'SELFTEST', 'Three real browser threshold fixtures required');
    const server = http.createServer((request, response) => {
      const filename = new URL(request.url, 'http://localhost').pathname.slice(1);
      if (!manifest.browser_cases.some(test => test.file === filename)) {
        response.writeHead(404); response.end('Unknown file fixture'); return;
      }
      fs.readFile(path.join(directory, filename)).then(bytes => {
        response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
        response.end(bytes);
      }, error => { response.writeHead(500); response.end('Fixture unavailable');
        report.fixture_io_failure = { file: filename, message: error.message }; });
    });
    await new Promise((resolve, reject) => {
      server.once('error', reject); server.listen(0, '127.0.0.1', resolve);
    });
    try {
      for (const test of manifest.browser_cases) {
        need(path.basename(test.file) === test.file && !names.has(test.file) &&
          ['lcp_ms', 'cls', 'interaction_latency_ms'].includes(test.metric) &&
          typeof test.expected_failure === 'boolean',
        'SELFTEST', 'Invalid real browser fixture');
        names.add(test.file);
        const bytes = await fs.readFile(path.join(directory, test.file));
        const fixtureRun = path.join(run, `browser-fixture-${browserResults.length + 1}`);
        await fs.mkdir(fixtureRun);
        const fixtureReport = {};
        const browser = new Browser({ ...options,
          url: `http://127.0.0.1:${server.address().port}/${test.file}` }, fixtureRun, fixtureReport);
        let observation;
        try {
          await browser.launch();
          observation = await browser.measureFixture(test.metric);
          observation.file = test.file; observation.sha256 = sha(bytes);
          observation.expected_failure = test.expected_failure;
          await fs.writeFile(path.join(fixtureRun, 'observation.json'), json(observation), { flag: 'wx' });
          const reread = JSON.parse(await fs.readFile(path.join(fixtureRun, 'observation.json'), 'utf8'));
          need(reread.failed === test.expected_failure, 'SELFTEST', 'Real browser threshold fixture failed its self-proof',
            observation);
          browserResults.push(observation);
          report.selftest.browser_count = browserResults.length;
        } finally { await browser.close(); }
      }
      need(['lcp_ms', 'cls', 'interaction_latency_ms'].every(metric =>
        browserResults.some(row => row.metric === metric && row.failed)),
      'SELFTEST', 'Missing actual over-threshold browser arm');
    } finally {
      await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    }
  }
  return { count: results.length, cases: results, browser_count: browserResults.length,
    browser_cases: browserResults, scope: 'FILE_FIXTURES_NOT_G5', status: 'PASS' };
}
export async function main(argv = process.argv.slice(2)) {
  let options, run, browser, exitCode = 2;
  const report = { schema_version: 1, tool: 'opensim-browser-performance', standard: 'shared-r41',
    gate: 'G5-performance', started_at: new Date().toISOString(), verdict: 'RUNNING',
    baseline: BASELINE, limits: LIMITS, samples: [], checks: [], g5_certified: false,
    release_verdict: 'NOT_CERTIFIED', scope: 'UNCONFIGURED',
    metric_definitions: { lcp: 'Last buffered LCP entry before first input; positive entry required',
      cls: 'Largest layout-shift session window, gap <1s, duration <5s, exclude recent input',
      interaction: 'Maximum trusted event timestamp to second rAF upper bound, also maximum Event Timing duration; not INP',
      transfer: 'Initial all-resource encoded wire bytes including headers; all initial JS counted as critical; decimal KB',
      fonts: 'No webfonts means measured zero; otherwise swap/optional, unicode-range subset, <=1 preload',
      isolation: 'All off-origin requests blocked before network; any attempt still fails WEB-PERF-006' } };
  try {
    options = parseArgs(argv);
    if (options.help) {
      console.log(`Usage: node tools/browser_performance.mjs --url URL [--out ${OUT}] [--chrome PATH]\n` +
        `       --parse REPORT --out ${OUT}\n       --selftest --fixtures DIR --out ${OUT}\n` +
        'Only exact https://opensim.ai4everyone.top/ or loopback HTTP. No configurable thresholds/retries.');
      return 0;
    }
    run = await outputDirectory(options.out);
    console.log(`PERFORMANCE_START scope=${options.scope ?? 'OFFLINE'} baseline=${BASELINE.id} run=${run}`);
    const git = spawnSync('git', ['-C', ROOT, 'rev-parse', 'HEAD'], { encoding: 'utf8' });
    need(git.status === 0, 'IDENTITY', `Cannot read commit: ${git.stderr}`);
    report.commit = git.stdout.trim();
    report.tool_sha256 = sha(await fs.readFile(fileURLToPath(import.meta.url)));
    report.target_url = options.url ?? null; report.scope = options.scope ?? 'OFFLINE_PARSE_NOT_G5';
    if (options.selftest) {
      report.scope = 'OFFLINE_FILE_FIXTURES_NOT_G5';
      report.selftest = await offlineSelftest(options.fixtures, options, run, report);
      report.verdict = 'SELFTEST_PASS'; exitCode = 0;
    } else if (options.parse) {
      const bytes = await fs.readFile(options.parse, 'utf8');
      const input = JSON.parse(bytes);
      report.checks = validateReport(input);
      report.parsed_source = { path: path.resolve(options.parse), sha256: sha(bytes), scope: input.scope };
      report.input_failure = input.failure ?? null;
      report.verdict = report.checks.every(c => c.status === 'PASS') ? 'OFFLINE_PARSE_PASS' : 'FAIL';
      exitCode = report.verdict === 'FAIL' ? 1 : 0;
    } else {
      browser = new Browser(options, run, report);
      await browser.launch();
      report.samples.push(await browser.measure());
      report.checks = validateReport(report);
      const pass = report.checks.every(c => c.status === 'PASS');
      report.g5_certified = pass && report.scope === 'PUBLIC_LAB';
      report.verdict = pass ? (report.g5_certified ? 'PUBLIC_G5_PASS' : 'LOCAL_PREFLIGHT_PASS') : 'FAIL';
      exitCode = pass ? 0 : 1;
    }
  } catch (e) {
    report.verdict = 'FAIL'; report.g5_certified = false;
    report.failure = { rule: e.rule ?? 'HARNESS', message: e.message,
      detail: e.detail ?? {}, phase: browser?.phase ?? 'configuration', stack: e.stack };
    exitCode = ['CLI', 'OUTPUT', 'DEPENDENCY', 'REPORT', 'SELFTEST', 'IDENTITY'].includes(e.rule) ||
      !(e instanceof Failure) ? 2 : 1;
    console.error(`PERFORMANCE_FAIL rule=${report.failure.rule} phase=${report.failure.phase} ${e.message}`);
    if (browser?.session && browser.chrome?.exitCode === null && browser.partial && report.samples.length === 0) {
      try {
        const raw = await browser.eval(() => window.__opensimPerf);
        const sample = browser.partial;
        sample.raw_metrics = raw;
        sample.metrics = { lcp_ms: raw.lcp.at(-1)?.startTime ?? null, lcp_count: raw.lcp.length,
          cls: clsValue(raw.shifts), cls_observed: raw.supported.includes('layout-shift'),
          interaction_latency_ms: sample.interactions.length ?
            Math.max(...sample.interactions.map(row => row.latency_ms)) : null,
          interaction_count: sample.interactions.length,
          metric_name: 'EVENT_TO_PAINT_UPPER_BOUND_NOT_INP', inp_ms: null };
        report.samples.push(sample);
      } catch (captureError) { report.partial_capture_failure = { message: captureError.message }; }
    }
  } finally {
    if (browser) {
      try { await browser.close(); } catch (e) {
        report.cleanup_failure = { message: e.message }; report.verdict = 'FAIL';
        report.g5_certified = false; exitCode = 2;
      }
      report.observed_network = [...browser.requests.values()].map(r => browser.resource(r));
      report.third_party = browser.thirdParty; report.redirects = browser.redirects;
      report.network_errors = browser.networkErrors; report.runtime_errors = browser.runtimeErrors;
      if (!report.failure && !report.cleanup_failure && report.samples.length === 1) {
        try {
          report.checks = validateReport(report);
          if (report.checks.some(check => check.status === 'FAIL')) {
            report.verdict = 'FAIL'; report.g5_certified = false; exitCode = 1;
          }
        } catch (error) {
          report.failure = { rule: error.rule ?? 'HARNESS', message: error.message,
            phase: 'final-validation', stack: error.stack };
          report.verdict = 'FAIL'; report.g5_certified = false; exitCode = 2;
        }
      }
    }
    const failed = report.checks.find(check => check.status === 'FAIL');
    if (failed && !report.failure) report.failure = {
      rule: failed.rule, message: failed.name, detail: failed.detail, phase: 'gate-evaluation',
    };
    report.failure_count = report.checks.filter(check => check.status === 'FAIL').length;
    report.finished_at = new Date().toISOString(); report.exit_code = exitCode;
    if (run) {
      await fs.writeFile(path.join(run, 'report.json'), json(report), { flag: 'wx' });
      const readback = JSON.parse(await fs.readFile(path.join(run, 'report.json'), 'utf8'));
      assert.equal(readback.verdict, report.verdict); assert.equal(readback.exit_code, exitCode);
      const line = `PERFORMANCE verdict=${report.verdict} scope=${report.scope} g5_certified=${report.g5_certified} ` +
        `rc=${exitCode} samples=${report.samples.length} checks=${report.checks.length} report=${run}/report.json`;
      await fs.writeFile(path.join(run, 'run.log'), `${report.started_at} START\n${report.finished_at} ${line}\n`,
        { flag: 'wx' });
      console.log(line);
    }
  }
  return exitCode;
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main();
}
