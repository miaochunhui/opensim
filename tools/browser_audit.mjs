#!/usr/bin/env node
/**
 * Zero-install Chromium CDP audit. --smoke proves browser machinery only.
 * Evidence is immutable and external; this never certifies a screen reader.
 * node tools/browser_audit.mjs --url http://127.0.0.1:PORT/ --smoke --out DIR
 */
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { EventEmitter } from 'node:events';
import * as fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const OUTPUT = '/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-audit';
export const MATRIX = [[320, 568], [768, 1024], [1064, 768], [1440, 900]];
const CHROME = '/home/mch/.cache/ms-playwright/chromium-1187/chrome-linux/chrome';
const SOURCES = ['tools/render_page.py', 'web/app.js', 'web/app.css', 'solver.py', 'server.py'];
const hash = data => createHash('sha256').update(data).digest('hex');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const json = data => JSON.stringify(data, null, 2) + '\n';

export function authorizedURL(value) {
  const url = new URL(value);
  const local = url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname);
  const publicTarget = url.protocol === 'https:' && url.hostname === 'opensim.ai4everyone.top' &&
    (!url.port || url.port === '443');
  assert((local || publicTarget) && !url.username && !url.password && !url.search && !url.hash,
    'URL must be credential-free loopback HTTP or exact https://opensim.ai4everyone.top/');
  return url.href;
}

export async function outputDirectory(value) {
  const out = path.resolve(value);
  assert(out === OUTPUT || out.startsWith(OUTPUT + path.sep), `Output must be inside ${OUTPUT}`);
  const allowed = await fs.realpath('/hf3fs-jd/data/tmp');
  let ancestor = out;
  for (;;) {
    try {
      const actual = await fs.realpath(ancestor);
      assert(actual === allowed || actual.startsWith(allowed + path.sep), 'Output symlink escapes data mount');
      break;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      ancestor = path.dirname(ancestor);
    }
  }
  await fs.mkdir(out, { recursive: true });
  const run = await fs.mkdtemp(path.join(out, `run-${Date.now()}-`));
  await fs.mkdir(path.join(run, 'logs'));
  const probe = path.join(run, 'write-probe.txt');
  await fs.writeFile(probe, 'audit evidence write/read probe\n', { flag: 'wx' });
  assert.equal(await fs.readFile(probe, 'utf8'), 'audit evidence write/read probe\n');
  return run;
}

class CDP extends EventEmitter {
  constructor(socket) {
    super();
    this.socket = socket;
    this.next = 0;
    this.pending = new Map();
    socket.addEventListener('message', event => {
      const message = JSON.parse(String(event.data));
      if (!message.id) return this.emit(message.method, message.params, message.sessionId);
      const waiter = this.pending.get(message.id);
      if (!waiter) return;
      this.pending.delete(message.id);
      clearTimeout(waiter.timer);
      if (message.error) waiter.reject(new Error(`${waiter.method}: ${json(message.error)}`));
      else waiter.resolve(message.result);
    });
    socket.addEventListener('close', () => {
      for (const waiter of this.pending.values()) {
        clearTimeout(waiter.timer);
        waiter.reject(new Error(`CDP closed during ${waiter.method}`));
      }
      this.pending.clear();
    });
  }
  send(method, params = {}, sessionId) {
    const id = ++this.next;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP timeout: ${method}`));
      }, 30000);
      this.pending.set(id, { resolve, reject, timer, method });
      this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }
}

export class Browser {
  constructor(url, run, report, chrome = CHROME) {
    this.url = authorizedURL(url);
    this.run = run;
    this.report = report;
    this.chromePath = chrome;
    this.tasks = [];
    this.errors = [];
    this.runtimeEvents = [];
    this.injections = [];
    this.phase = 'startup';
    this.requests = new Map();
    this.responses = [];
    this.injectedNetwork = new Map();
  }
  async wait(label, action, exiting = false) {
    const end = Date.now() + 30000;
    while (Date.now() < end) {
      this.assertErrors();
      if (!exiting && this.child && this.child.exitCode !== null) throw new Error(`Chromium exited: ${this.child.exitCode}`);
      const result = await action();
      if (result) return result;
      await pause(50);
    }
    throw new Error(`Readiness timeout: ${label}`);
  }
  assertErrors() {
    if (this.errors.length) throw this.errors[0];
  }
  send(method, params = {}) { return this.cdp.send(method, params, this.session); }
  async evaluate(fn, ...args) {
    const answer = await this.send('Runtime.evaluate', {
      expression: `(${fn.toString()})(...${JSON.stringify(args)})`, awaitPromise: true, returnByValue: true,
    });
    if (answer.exceptionDetails) throw new Error(answer.exceptionDetails.exception?.description ??
      answer.exceptionDetails.text);
    return answer.result.value;
  }
  async launch() {
    assert.equal(typeof WebSocket, 'function', 'Native WebSocket required; no packages will be installed');
    await fs.access(this.chromePath, fs.constants.X_OK);
    const profile = path.join(this.run, 'chromium-profile');
    this.child = spawn(this.chromePath, [
      '--headless=new', '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
      `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
      '--disable-background-networking', '--disable-component-update', '--disable-default-apps',
      '--disable-breakpad', '--password-store=basic', 'about:blank',
    ], { stdio: ['ignore', 'pipe', 'pipe'] });
    this.child.on('error', error => this.errors.push(error));
    this.chromeLog = [];
    this.child.stdout.on('data', data => this.chromeLog.push(data));
    this.child.stderr.on('data', data => this.chromeLog.push(data));
    const endpoint = await this.wait('DevToolsActivePort', async () => {
      try { return await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8'); }
      catch (error) { if (error.code === 'ENOENT') return false; throw error; }
    });
    const [port, route] = endpoint.trim().split('\n');
    assert(/^\d+$/.test(port) && route.startsWith('/devtools/browser/'), 'Malformed DevTools endpoint');
    const socket = new WebSocket(`ws://127.0.0.1:${port}${route}`);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('WebSocket connection timeout')), 30000);
      socket.addEventListener('open', () => { clearTimeout(timer); resolve(); }, { once: true });
      socket.addEventListener('error', () => {
        clearTimeout(timer); reject(new Error('WebSocket connection failed'));
      }, { once: true });
    });
    this.cdp = new CDP(socket);
    this.report.browser = await this.cdp.send('Browser.getVersion');
    this.report.dependencies = { node: process.version, chrome: this.chromePath, installed: false, sandbox: true };
    const { targetId } = await this.cdp.send('Target.createTarget', { url: 'about:blank' });
    this.session = (await this.cdp.send('Target.attachToTarget', { targetId, flatten: true })).sessionId;
    for (const domain of ['Page', 'Runtime', 'Network', 'Accessibility']) await this.send(`${domain}.enable`);
    await this.cdp.send('Browser.setDownloadBehavior', { behavior: 'deny' });
    await this.send('Network.setCacheDisabled', { cacheDisabled: true });
    this.cdp.on('Network.requestWillBeSent', (event, session) => {
      if (session === this.session && event.request.method === 'POST' &&
          new URL(event.request.url).pathname === new URL('api/solve', this.url).pathname) {
        this.requests.set(event.requestId, { url: event.request.url,
          config: JSON.parse(event.request.postData), state: this.phase });
      }
    });
    this.cdp.on('Network.responseReceived', (event, session) => {
      if (session === this.session && this.requests.has(event.requestId)) {
        this.requests.get(event.requestId).status = event.response.status;
      }
    });
    this.cdp.on('Network.loadingFinished', (event, session) => {
      if (session !== this.session || !this.requests.has(event.requestId)) return;
      const request = this.requests.get(event.requestId);
      const task = (async () => {
        const result = await this.send('Network.getResponseBody', { requestId: event.requestId });
        const text = result.base64Encoded ? Buffer.from(result.body, 'base64').toString('utf8') : result.body;
        const entry = { ...request, injection: this.injectedNetwork.get(event.requestId) ?? null,
          body_sha256: hash(text), bytes: Buffer.byteLength(text),
          payload: text.trim() ? JSON.parse(text) : null };
        const filename = `http-solve-${String(this.responses.length).padStart(3, '0')}.json`;
        await fs.writeFile(path.join(this.run, filename), json(entry), { flag: 'wx' });
        entry.artifact = filename;
        this.responses.push(entry);
      })().catch(error => this.errors.push(error));
      this.tasks.push(task);
    });
    this.cdp.on('Runtime.exceptionThrown', (event, session) => {
      if (session === this.session) this.errors.push(new Error(
        `Runtime exception: ${event.exceptionDetails.exception?.description ?? event.exceptionDetails.text}`));
    });
    this.cdp.on('Runtime.consoleAPICalled', (event, session) => {
      if (session === this.session && ['error', 'warning'].includes(event.type)) {
        this.runtimeEvents.push({ phase: this.phase, type: event.type,
          message: event.args.map(item => item.value ?? item.description).join(' ') });
      }
    });
    this.cdp.on('Fetch.requestPaused', (event, session) => {
      if (session !== this.session) return;
      const task = this.intercept(event).catch(error => this.errors.push(error));
      this.tasks.push(task);
    });
    await this.send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] });
  }
  async intercept(event) {
    const url = new URL(event.request.url);
    const allowed = url.origin === new URL(this.url).origin ||
      ['data:', 'blob:', 'about:'].includes(url.protocol);
    if (!allowed) {
      await this.send('Fetch.failRequest', { requestId: event.requestId, errorReason: 'BlockedByClient' });
      throw new Error(`Unauthorized network blocked: ${url.origin}${url.pathname}`);
    }
    const solve = url.pathname === new URL('api/solve', this.url).pathname && event.request.method === 'POST';
    if (solve && this.fault) {
      const fault = this.fault;
      if (event.networkId) this.injectedNetwork.set(event.networkId, fault);
      const entry = { mode: fault, phase: this.phase, url: url.href, request_id: event.requestId };
      this.injections.push(entry);
      if (fault === 'hold') {
        assert(!this.held, 'More than one held solve request');
        this.held = event.requestId;
        return;
      }
      let body = '', code = 200;
      if (fault === 'error') {
        code = 503;
        body = JSON.stringify({ error: { code: 'AUDIT_INJECTED', field: 'pressure_pa',
          message: 'Audit injected unavailable service; retry after removing the fault.' } });
      } else if (fault === 'long') {
        // Obtain real solver output, change only bounded content, never fabricate FEM.
        const response = await fetch(url, { method: 'POST', redirect: 'error',
          headers: { 'Content-Type': 'application/json' }, body: event.request.postData });
        assert.equal(response.status, 200, 'Long-content source solve failed');
        const result = await response.json();
        assert(result.mesh?.nodes_mm?.length > 0 && result.metrics?.flow_rate_m3_s > 0,
          'Long-content injection requires a real nonempty solver response');
        this.longText = '\u5ba1\u8ba1\u957f\u6587\u672c\u6062\u590d\u68c0\u67e5 ' +
          'https://example.invalid/' + 'long-hash-0123456789abcdef'.repeat(60) +
          ' ' + '\u4e2d\u6587\u957f\u8bf4\u660e\u4e0d\u5f97\u622a\u65ad '.repeat(70);
        assert(this.longText.length <= 4096, 'Injected warning exceeds actual product contract');
        result.warnings.push(this.longText);
        body = JSON.stringify(result);
        entry.real_mesh_nodes = result.mesh.nodes_mm.length;
        entry.injected_warning_length = this.longText.length;
      } else assert.equal(fault, 'empty', 'Unknown injection mode');
      entry.status = code;
      entry.body_sha256 = hash(body);
      await this.send('Fetch.fulfillRequest', { requestId: event.requestId, responseCode: code,
        responseHeaders: [{ name: 'Content-Type', value: 'application/json' }],
        body: Buffer.from(body).toString('base64') });
      return;
    }
    await this.send('Fetch.continueRequest', { requestId: event.requestId });
  }
  async release() {
    this.fault = null;
    if (this.held) {
      const requestId = this.held;
      this.held = null;
      await this.send('Fetch.continueRequest', { requestId });
    }
  }
  async viewport(width, height) {
    this.viewportSize = { width, height };
    await this.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
    await this.send('Emulation.setEmulatedMedia', {
      features: [{ name: 'prefers-reduced-motion', value: 'reduce' }],
    });
  }
  async navigate(waitReady = true) {
    const result = await this.send('Page.navigate', { url: this.url });
    assert(!result.errorText, result.errorText);
    await this.wait('nonempty rendered page', () => this.evaluate(() =>
      document.readyState === 'complete' && document.body?.innerText.trim().length > 30));
    await this.evaluate(async () => { await document.fonts.ready; });
    if (waitReady) await this.ready();
  }
  async ready() {
    return this.wait('real solve ready', async () => {
      const state = await this.state();
      if (state.phase === 'failed') throw new Error(`Unexpected failed solve: ${state.error}`);
      return state.phase === 'ready' && state.busy === 'false' && state.export_enabled && state;
    });
  }
  async state() {
    return this.evaluate(() => {
      const error = document.querySelector('#error'), status = document.querySelector('#status');
      return { phase: document.querySelector('#workspace')?.dataset.state,
        busy: document.querySelector('#case-form')?.getAttribute('aria-busy'),
        export_enabled: !document.querySelector('#export-button')?.disabled,
        solve_enabled: !document.querySelector('#solve-button')?.disabled,
        error: error?.textContent, error_visible: !!error && !error.hidden && error.getClientRects().length > 0,
        error_role: error?.getAttribute('role'), error_focus: error === document.activeElement,
        field_invalid: document.querySelector('#pressure_pa')?.getAttribute('aria-invalid'),
        field_described: document.querySelector('#pressure_pa')?.getAttribute('aria-describedby'),
        status: status?.textContent, live: status?.getAttribute('aria-live'),
        summary: document.querySelector('#result-summary')?.textContent,
        metric: document.querySelector('#metric-flow_rate_m3_s')?.textContent };
    });
  }
  async identity(stage) {
    const served = await this.evaluate(async () => {
      const response = await fetch('/api/health', { cache: 'no-store', redirect: 'error' });
      if (!response.ok) throw new Error(`Health HTTP ${response.status}`);
      const raw = await response.text(), health = JSON.parse(raw);
      const footer = document.querySelector('footer')?.textContent ?? '';
      const bytes = {};
      for (const resource of ['/', '/web/app.css', '/web/app.js']) {
        const fetched = await fetch(resource, { cache: 'no-store', redirect: 'error' });
        if (!fetched.ok) throw new Error(`Identity resource ${resource} HTTP ${fetched.status}`);
        bytes[resource] = Array.from(new Uint8Array(await fetched.arrayBuffer()));
      }
      return { health, health_raw: raw, resource_bytes: bytes, footer, url: location.href };
    });
    assert.equal(served.url, this.url, 'Served URL changed or redirected');
    assert.equal(served.health.status, 'ok', 'Health not ready');
    assert.deepEqual(Object.keys(served.health.source_sha256).sort(), SOURCES.toSorted(), 'Health five-source set missing');
    const local = {}, committed = {};
    const fingerprint = createHash('sha256');
    for (const relative of SOURCES) {
      const bytes = await fs.readFile(path.join(ROOT, relative));
      local[relative] = hash(bytes);
      const blob = spawnSync('git', ['-C', ROOT, 'show', `${this.report.commit}:${relative}`]);
      assert.equal(blob.status, 0, `Cannot read committed source blob ${relative}`);
      committed[relative] = hash(blob.stdout);
      assert.equal(local[relative], committed[relative], `Uncommitted product source ${relative}`);
      assert.equal(served.health.source_sha256[relative], committed[relative], `Served health source drift ${relative}`);
      fingerprint.update(relative + '\0').update(bytes).update('\0');
    }
    const expectedBuild = fingerprint.digest('hex').slice(0, 12);
    assert.equal(served.health.page_build, expectedBuild, 'Health page build differs from committed product sources');
    assert(served.footer.includes(expectedBuild), 'Actually rendered footer does not contain health build');
    this.report.identity_checks ??= [];
    const resourceDigests = Object.fromEntries(Object.entries(served.resource_bytes).map(([key, bytes]) =>
      [key, hash(Buffer.from(bytes))]));
    if (this.report.mode !== 'HARNESS_FIXTURE_NOT_PRODUCT') {
      for (const relative of ['web/app.js', 'web/app.css']) {
        assert.equal(resourceDigests['/' + relative], committed[relative], `Actually served asset bytes differ ${relative}`);
      }
    }
    const rawDigests = { health: hash(served.health_raw), ...resourceDigests };
    this.report.identity_checks.push({ stage, collected_at: new Date().toISOString(),
      health: served.health, footer: served.footer, url: served.url, served_byte_sha256: rawDigests,
      local_source_sha256: local, committed_source_sha256: committed,
      fixture_asset_override: this.report.mode === 'HARNESS_FIXTURE_NOT_PRODUCT', verdict: 'PASS' });
    this.report.source_sha256 = served.health.source_sha256;
    this.report.page_build = { id: served.health.page_build, footer: served.footer };
    if (stage === 'after') {
      assert.deepEqual(this.report.identity_checks[0].health, served.health, 'Served identity changed during audit');
      assert.deepEqual(this.report.identity_checks[0].served_byte_sha256, rawDigests,
        'Actually served health/page/assets bytes changed during audit');
      assert(this.responses.length > 0, 'Identity requires actual POST response evidence');
      for (const entry of this.responses.filter(entry => entry.status === 200)) {
        if (entry.injection === 'empty') {
          assert.equal(entry.bytes, 0, 'Empty fault response must be explicitly intercepted empty bytes');
          continue;
        }
        assert(entry.payload?.reproducibility?.source_sha256, 'Successful POST lacks runtime five-source stamp');
        assert.deepEqual(entry.payload.reproducibility.source_sha256, committed, `POST source drift ${entry.artifact}`);
        assert.equal(entry.payload.reproducibility.solver_sha256, committed['solver.py'], 'POST solver identity drift');
      }
      this.report.post_source_checks = this.responses.filter(entry => entry.status === 200 && entry.injection !== 'empty')
        .map(entry => ({ artifact: entry.artifact, url: entry.url,
          body_sha256: entry.body_sha256, source_sha256: entry.payload.reproducibility.source_sha256, verdict: 'PASS' }));
      const current = spawnSync('git', ['-C', ROOT, 'rev-parse', 'HEAD'], { encoding: 'utf8' });
      assert.equal(current.status, 0, current.stderr);
      assert.equal(current.stdout.trim(), this.report.commit, 'Audit worktree HEAD changed during audit');
    }
  }
  async key(key, modifiers = 0) {
    const code = { Tab: 'Tab', Enter: 'Enter', Escape: 'Escape', ' ': 'Space' }[key] ?? key;
    const virtual = { Tab: 9, Enter: 13, Escape: 27, ' ': 32 }[key];
    const params = { key, code, modifiers,
      ...(virtual ? { windowsVirtualKeyCode: virtual, nativeVirtualKeyCode: virtual } : {}) };
    await this.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', ...params });
    if (key === 'Enter' || key === ' ') {
      await this.send('Input.dispatchKeyEvent', { type: 'char', ...params, text: key === 'Enter' ? '\r' : ' ' });
    }
    await this.send('Input.dispatchKeyEvent', { type: 'keyUp', ...params });
  }
  async activate(selector) {
    const count = await this.evaluate(() => document.querySelectorAll('button,input,select,a[href],summary,[tabindex]').length);
    for (let i = 0; i <= count * 2 + 2; i++) {
      if (await this.evaluate(sel => document.activeElement?.matches(sel), selector)) {
        await this.key('Enter');
        return;
      }
      await this.key('Tab');
    }
    throw new Error(`Keyboard cannot reach ${selector}`);
  }
  async screenshot(name, top = true) {
    if (top) await this.evaluate(() => window.scrollTo(0, 0));
    const { data } = await this.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    const bytes = Buffer.from(data, 'base64');
    assert(bytes.length > 100 && bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])),
      'Missing/truncated PNG');
    const width = bytes.readUInt32BE(16), height = bytes.readUInt32BE(20);
    assert.equal(width, this.viewportSize.width, 'Screenshot not actual CSS viewport width');
    assert.equal(height, this.viewportSize.height, 'Screenshot not actual CSS viewport height');
    const filename = name + '.png';
    await fs.writeFile(path.join(this.run, filename), bytes, { flag: 'wx' });
    assert.equal(hash(await fs.readFile(path.join(this.run, filename))), hash(bytes));
    this.report.screenshots.push({ file: filename, width, height, sha256: hash(bytes),
      url: this.url, state: name, coverage: 'viewport only, not full page' });
  }
  async contentScreenshots(name) {
    const before = await this.evaluate(() => ({ x: scrollX, y: scrollY,
      width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight }));
    assert(Number.isFinite(before.height) && before.height > 0, 'Nonfinite/empty document height');
    const tiles = [];
    try {
      for (let y = 0; y < before.height; y += this.viewportSize.height) {
        const actual = await this.evaluate(position => {
          window.scrollTo(0, position);
          return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(scrollY))));
        }, y);
        const tile = `${name}-tile-${String(tiles.length).padStart(3, '0')}`;
        await this.screenshot(tile, false);
        tiles.push({ file: tile + '.png', document_y: actual, viewport: this.viewportSize });
      }
      const end = await this.evaluate(() => ({ width: document.documentElement.scrollWidth,
        height: document.documentElement.scrollHeight }));
      assert.equal(end.height, before.height, 'Screenshot changed document height');
      assert.equal(end.width, before.width, 'Screenshot changed document width');
      let covered = 0;
      for (const tile of tiles) {
        assert(tile.document_y <= covered, 'Screenshot vertical coverage gap');
        covered = Math.max(covered, tile.document_y + this.viewportSize.height);
      }
      assert(covered >= before.height, 'Screenshot did not cover actual long content');
      await fs.writeFile(path.join(this.run, name + '.coverage.json'), json({
        status: 'PASS', document: before, vertical_coverage: covered,
        horizontal_coverage: before.width <= this.viewportSize.width ? 'COMPLETE' : 'PAGE_OVERFLOW_NOT_CAPTURED',
        scale: 1, tiles, overlap: 'last viewport may overlap; no reflow or rescaling',
      }), { flag: 'wx' });
    } finally {
      await this.evaluate(position => window.scrollTo(position.x, position.y), before);
    }
  }
  async close() {
    const failures = [];
    if (this.cdp) {
      try {
        await this.release();
        await Promise.all(this.tasks);
        await this.cdp.send('Browser.close');
      } catch (error) { failures.push(error); }
    }
    if (this.child && this.child.exitCode === null) {
      try { await this.wait('owned Chromium exit', async () => this.child.exitCode !== null, true); }
      catch (error) { failures.push(error); this.child.kill('SIGTERM'); }
    }
    await fs.writeFile(path.join(this.run, 'logs', 'chromium.log'), Buffer.concat(this.chromeLog ?? []), { flag: 'wx' });
    if (failures.length) throw new AggregateError(failures, 'Chromium cleanup failed');
  }
}

// Runs in Chromium: solid alpha-composited CSS colors only. Complex paint is
// explicitly untested, never silently substituted with a white background.
export function inspectDOM(focusedOnly = false) {
  const selector = element => element.id ? '#' + CSS.escape(element.id) :
    element.parentElement ? selector(element.parentElement) + ' > ' + element.tagName.toLowerCase() +
      ':nth-child(' + (Array.from(element.parentElement.children).indexOf(element) + 1) + ')' : 'html';
  const rect = el => {
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right, bottom: r.bottom };
  };
  const painted = el => {
    if (!el || el.closest('[hidden],[inert]')) return false;
    for (let parent = el.parentElement; parent; parent = parent.parentElement) {
      if (parent.matches('details:not([open])') && !parent.querySelector(':scope > summary')?.contains(el)) return false;
    }
    const s = getComputedStyle(el), r = rect(el);
    return s.display !== 'none' && s.visibility === 'visible' && Number(s.opacity) !== 0 && r.width > 0 && r.height > 0;
  };
  const color = value => {
    const match = value.match(/^rgba?\(([\d.]+)[, ]+([\d.]+)[, ]+([\d.]+)(?:\s*[,/]\s*([\d.]+))?\)$/);
    return match ? [Number(match[1]), Number(match[2]), Number(match[3]), match[4] === undefined ? 1 : Number(match[4])] : null;
  };
  const composite = (front, back) => {
    const a = front[3] + back[3] * (1 - front[3]);
    return a ? [0, 1, 2].map(i => (front[i] * front[3] + back[i] * back[3] * (1 - front[3])) / a).concat(a) : [0, 0, 0, 0];
  };
  const background = el => {
    let variants = [[0, 0, 0, 0]], reason = null, gradient = false;
    for (let current = el; current; current = current.parentElement) {
      const style = getComputedStyle(current);
      if (Number(style.opacity) !== 1 || style.filter !== 'none' || style.mixBlendMode !== 'normal') {
        reason = 'opacity/filter/blend'; break;
      }
      const next = color(style.backgroundColor);
      if (!next) { reason = 'unsupported CSS color'; break; }
      let layers = [[0, 0, 0, 0]];
      if (style.backgroundImage !== 'none') {
        // CSS gradients interpolate between stops. Enumerating endpoint
        // composites and bounding each channel encloses every possible pixel.
        if (/url\(|(?:in\s+(?:oklab|lab|lch|oklch|hsl|hwb|xyz|display-p3))|color\(/i.test(style.backgroundImage)) {
          reason = 'image or non-sRGB gradient interpolation'; break;
        }
        const images = [];
        let depth = 0, start = 0;
        for (let i = 0; i < style.backgroundImage.length; i++) {
          if (style.backgroundImage[i] === '(') depth++;
          if (style.backgroundImage[i] === ')') depth--;
          if (style.backgroundImage[i] === ',' && depth === 0) {
            images.push(style.backgroundImage.slice(start, i));
            start = i + 1;
          }
        }
        images.push(style.backgroundImage.slice(start));
        for (const image of images) {
          if (image.trim() === 'none') continue;
          if (!/^\s*(?:repeating-)?(?:linear|radial)-gradient\(/.test(image)) {
            reason = 'unsupported background layer'; break;
          }
          const stops = Array.from(image.matchAll(/rgba?\([^()]+\)/g)).map(match => color(match[0]));
          if (stops.length < 2 || stops.some(stop => !stop)) { reason = 'unsupported gradient stop'; break; }
          layers = layers.flatMap(front => stops.map(back => composite(front, back)));
          if (layers.length * variants.length > 512) { reason = 'gradient endpoint combination limit'; break; }
        }
        if (reason) break;
        gradient = true;
      }
      layers = layers.map(front => composite(front, next));
      variants = variants.flatMap(front => layers.map(back => composite(front, back)));
      if (variants.every(item => item[3] > 0.999)) {
        const low = [0, 1, 2].map(i => Math.min(...variants.map(item => item[i]))).concat(1);
        const high = [0, 1, 2].map(i => Math.max(...variants.map(item => item[i]))).concat(1);
        return { value: variants[0], gradient, bounds: { low, high }, endpoint_count: variants.length };
      }
    }
    return { value: null, reason: reason ?? 'no explicit opaque background' };
  };
  const luminance = c => c.slice(0, 3).map(v => v / 255)
    .map(v => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)
    .reduce((sum, v, i) => sum + v * [0.2126, 0.7152, 0.0722][i], 0);
  const ratio = (a, b) => {
    const x = luminance(a), y = luminance(b);
    return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
  };
  const contrast = (ink, bg) => {
    if (!bg.gradient) return ratio(composite(ink, bg.value), bg.value);
    if (ink[3] !== 1) return null;
    const foreground = luminance(ink), low = luminance(bg.bounds.low), high = luminance(bg.bounds.high);
    if (foreground >= low && foreground <= high) return 1;
    return foreground < low ? (low + 0.05) / (foreground + 0.05) : (foreground + 0.05) / (high + 0.05);
  };
  const enabled = el => !el.matches(':disabled,[aria-disabled="true"]') && !el.closest('[inert]');
  const native = 'a[href],button,input:not([type="hidden"]),select,textarea,summary';
  const controls = Array.from(document.querySelectorAll(native + ',[role="button"],[role="checkbox"],[role="switch"],[role="tab"],[tabindex]'))
    .filter(el => painted(el) && el.tabIndex >= 0 && enabled(el));
  const text = [], boundaries = [], issues = [], unknown = [], focus = [];
  const issue = (rule, message, element, detail = {}) => issues.push({ rule, message,
    selector: selector(element), rect: rect(element), ...detail });
  const notTested = (rule, reason, element) => unknown.push({ rule, reason, selector: selector(element) });
  const name = el => {
    const ids = (el.getAttribute('aria-labelledby') ?? '').split(/\s+/).filter(Boolean);
    return ids.length ? ids.map(id => document.getElementById(id)?.textContent ?? '').join(' ').trim() :
      (el.getAttribute('aria-label') ?? '').trim() || Array.from(el.labels ?? []).map(l => l.textContent).join(' ').trim() ||
      (el.tagName === 'INPUT' ? (['submit', 'button'].includes(el.type) ? el.value : '') : el.textContent.trim()) ||
      Array.from(el.querySelectorAll('img[alt]')).map(img => img.alt).join(' ').trim();
  };
  if (!focusedOnly) {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const el = node.parentElement;
      if (!node.textContent.trim() || !painted(el) || !enabled(el) ||
          el.closest('script,style,noscript')) continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      if (!Array.from(range.getClientRects()).some(r => r.width && r.height)) continue;
      const style = getComputedStyle(el), bg = background(el);
      const ink = color(el instanceof SVGElement ? style.fill : style.color);
      const large = parseFloat(style.fontSize) >= 24 ||
        (parseFloat(style.fontSize) >= 18.66 && Number(style.fontWeight) >= 700);
      if (!bg.value || !ink || style.textShadow !== 'none') {
        notTested('WEB-A11Y-001', bg.reason ?? 'text-shadow/unsupported ink', el);
        continue;
      }
      const actual = contrast(ink, bg), threshold = large ? 3 : 4.5;
      text.push({ selector: selector(el), text: node.textContent.trim().slice(0, 100), ratio: actual, threshold,
        ink, background: bg, method: bg.gradient ? 'conservative sRGB gradient interval lower bound' : 'computed solid alpha composite' });
      if (actual === null || bg.gradient && actual + 0.001 < threshold) {
        notTested('WEB-A11Y-001', 'Gradient lower bound insufficient: needs exact pixel proof, not a confirmed failure', el);
      } else if (actual + 0.001 < threshold) issue('WEB-A11Y-001', 'Insufficient text contrast', el, { ratio: actual, threshold });
    }
    for (const el of controls) {
      const style = getComputedStyle(el), box = rect(el);
      if (el.matches('input,select,textarea')) {
        const bg = background(el), ink = color(style.color);
        if (!bg.value || !ink) notTested('WEB-A11Y-001', bg.reason ?? 'Unsupported control ink', el);
        else {
          const actual = contrast(ink, bg);
          text.push({ selector: selector(el), text: String(el.value).slice(0, 100), ratio: actual, threshold: 4.5,
            source: 'native control computed value ink, not popup/pseudo-element paint' });
          if (actual === null || bg.gradient && actual < 4.5) notTested('WEB-A11Y-001', 'Native value gradient needs pixel proof', el);
          else if (actual + 0.001 < 4.5) issue('WEB-A11Y-001', 'Insufficient input value text contrast', el,
            { ratio: actual, threshold: 4.5 });
        }
      }
      if (box.width < 44 || box.height < 44) issue('WEB-INPUT-001', 'Target smaller than 44x44', el);
      if (!name(el)) issue('WEB-A11Y-007', 'Unnamed interactive control', el);
      if (el.matches('input,textarea,select') && !(Array.from(el.labels ?? []).some(label => painted(label)) ||
          (el.getAttribute('aria-labelledby') ?? '').split(/\s+/).some(id => painted(document.getElementById(id))))) {
        issue('WEB-INPUT-003', 'Missing persistent visible programmatic label', el);
      }
      if (el.matches('a[href]') || !el.matches('button,input,select,textarea,summary,[role="button"],[role="checkbox"],[role="switch"],[role="tab"]')) {
        continue;
      }
      const bg = background(el.parentElement);
      if (!bg.value) { notTested('WEB-A11Y-002', bg.reason, el); continue; }
      const borders = ['Top', 'Right', 'Bottom', 'Left'].filter(side =>
        parseFloat(style['border' + side + 'Width']) > 0 && !['none', 'hidden'].includes(style['border' + side + 'Style']))
        .map(side => color(style['border' + side + 'Color'])).filter(Boolean);
      const textOnly = el.matches('a[href],[tabindex]:not(input):not(button):not(select):not(textarea):not(summary)');
      const fill = textOnly && !borders.length ? null : color(style.backgroundColor);
      const ratios = [...borders, ...(fill?.[3] > 0 ? [fill] : [])].map(c => contrast(c, bg)).filter(c => c !== null);
      if (!ratios.length) {
        if (el.matches('a[href],summary') || el.matches('[tabindex]:not(input):not(button):not(select)')) {
          notTested('WEB-A11Y-002', 'Text-only control, no independent boundary', el);
        } else issue('WEB-A11Y-002', 'No contrasting control boundary or fill', el);
      } else {
        const actual = Math.max(...ratios);
        boundaries.push({ selector: selector(el), ratio: actual, threshold: 3 });
        if (bg.gradient && actual < 3) notTested('WEB-A11Y-002', 'Gradient boundary lower bound insufficient; pixel proof needed', el);
        else if (actual + 0.001 < 3) issue('WEB-A11Y-002', 'Insufficient control boundary contrast', el, { ratio: actual });
      }
    }
    for (const el of document.querySelectorAll('[onclick],div[role],span[role],div,span')) {
      if (!painted(el) || !enabled(el) || el.matches(native)) continue;
      const clickable = el.onclick || getComputedStyle(el).cursor === 'pointer' || el.matches('[role="button"],[role="checkbox"],[role="switch"]');
      if (clickable && (!el.getAttribute('role') || el.tabIndex < 0)) {
        issue('WEB-A11Y-006', 'Custom clickable lacks role or keyboard reachability', el);
      }
    }
    const mains = document.querySelectorAll('main,[role="main"]'), headings = Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'));
    if (mains.length !== 1) issue('WEB-NAV-001', 'Expected exactly one main landmark', document.body);
    if (headings.filter(el => el.tagName === 'H1').length !== 1) issue('WEB-NAV-002', 'Expected exactly one h1', document.body);
    for (let i = 1; i < headings.length; i++) {
      if (Number(headings[i].tagName[1]) > Number(headings[i - 1].tagName[1]) + 1) {
        issue('WEB-NAV-002', 'Heading level skips', headings[i]);
      }
    }
    if (!document.documentElement.lang || !document.title.trim()) issue('WEB-A11Y-007', 'Missing title or document language', document.body);
    for (const el of document.querySelectorAll('dialog,[role="dialog"],iframe,[role="region"],nav')) {
      if (painted(el) && !name(el)) issue('WEB-A11Y-007', 'Unnamed dynamic region or frame', el);
    }
    for (const group of document.querySelectorAll('main,section,nav,.workspace-grid,.form-grid,.metric-grid,.buttons,.view-switch')) {
      const children = Array.from(group.children).filter(painted);
      for (let i = 0; i < children.length; i++) {
        const a = children[i], sa = getComputedStyle(a), ra = rect(a);
        if (sa.order !== '0') issue('WEB-A11Y-005', 'CSS order requires reading-order review', a, { order: sa.order });
        for (const b of children.slice(i + 1)) {
          const rb = rect(b);
          const sameRow = Math.min(ra.bottom, rb.bottom) - Math.max(ra.y, rb.y) > Math.min(ra.height, rb.height) * 0.5;
          const rtl = getComputedStyle(group).direction === 'rtl';
          if (rb.bottom <= ra.y + 1 || (sameRow && (rtl ? rb.x > ra.right + 1 : rb.right < ra.x - 1))) {
            issue('WEB-A11Y-005', 'Visual sibling reading order reverses DOM order', a,
              { later_selector: selector(b), later_rect: rb, group: selector(group) });
          }
        }
      }
    }
  }
  const active = document.activeElement;
  if (focusedOnly && active && active !== document.body && active !== document.documentElement) {
    const box = rect(active), style = getComputedStyle(active), bg = background(active.parentElement);
    const left = Math.max(0, box.x), right = Math.min(innerWidth, box.right);
    const top = Math.max(0, box.y), bottom = Math.min(innerHeight, box.bottom);
    const points = right > left && bottom > top ? [[(left + right) / 2, (top + bottom) / 2],
      [left + 1, top + 1], [right - 1, bottom - 1]] : [];
    const uncovered = points.some(([x, y]) => {
      const hit = document.elementFromPoint(x, y);
      return hit && (hit === active || active.contains(hit));
    });
    const ink = color(style.outlineColor), visible = parseFloat(style.outlineWidth) > 0 && style.outlineStyle !== 'none';
    const sample = { selector: selector(active), rect: box, uncovered, outline: style.outline,
      ratio: null, indicator_visible: visible, focus_visible: active.matches(':focus-visible') };
    focus.push(sample);
    if (!uncovered) issue('WEB-A11Y-004', 'Keyboard focus outside viewport or covered', active);
    if (!visible) {
      if (style.boxShadow !== 'none') notTested('WEB-A11Y-002', 'Box-shadow focus needs pixel analysis', active);
      else issue('WEB-A11Y-004', 'Missing visible focus indicator', active);
    } else if (!bg.value || !ink) notTested('WEB-A11Y-002', bg.reason ?? 'Unsupported outline color', active);
    else {
      const actual = contrast(ink, bg);
      if (actual === null || bg.gradient && actual < 3) notTested('WEB-A11Y-002', 'Gradient focus lower bound insufficient; pixel proof needed', active);
      else if (actual + 0.001 < 3) issue('WEB-A11Y-002', 'Insufficient focus contrast', active, { ratio: actual });
      sample.ratio = actual;
    }
  }
  const clipped = [];
  for (const el of document.querySelectorAll('#error,#status,#result-summary,#plot-description,.field-hint,pre')) {
    if (!painted(el)) continue;
    const s = getComputedStyle(el);
    if ((el.scrollHeight > el.clientHeight + 1 && ['hidden', 'clip'].includes(s.overflowY)) ||
        (el.scrollWidth > el.clientWidth + 1 && ['hidden', 'clip'].includes(s.overflowX)) ||
        s.webkitLineClamp !== 'none' && s.webkitLineClamp !== '' && s.webkitLineClamp !== '0') {
      clipped.push(selector(el));
      issue('WEB-TYPE-006', 'Key content clipped', el);
    }
  }
  if (document.documentElement.scrollWidth > document.documentElement.clientWidth + 1) {
    issue('WEB-BASE-004', 'Page horizontal overflow', document.documentElement, {
      scroll_width: document.documentElement.scrollWidth, client_width: document.documentElement.clientWidth });
  }
  return { text, boundaries, issues, unknown, focus, clipped,
    controls: controls.map(el => ({ selector: selector(el), name: name(el), tag: el.tagName, rect: rect(el) })),
    active: active && selector(active), active_tag: active?.tagName,
    title: document.title, lang: document.documentElement.lang,
    profile: document.body.dataset.pageProfile,
    panels: Array.from(document.querySelectorAll('.workspace-grid > *')).map(el => ({ selector: selector(el), rect: rect(el) })) };
}

function record(report, browser, rule, status, message, detail = {}) {
  const item = { rule, status, message, detail, url: browser.url, viewport: browser.viewportSize,
    state: browser.phase, reproduce: [`Open ${browser.url}`, `Set CSS viewport ${browser.viewportSize.width}x${browser.viewportSize.height}`,
      `Run state ${browser.phase}; ${message}`] };
  report.checks.push(item);
  if (status === 'FAIL' && !report.first_failure) report.first_failure = item;
  return item;
}

export async function auditSnapshot(browser, report, label, keyboard = true) {
  browser.phase = label;
  const line = `${new Date().toISOString()} AUDIT_STATE ${label} viewport=${browser.viewportSize.width}x${browser.viewportSize.height}\n`;
  await fs.appendFile(path.join(browser.run, 'logs', 'progress.log'), line);
  console.log(line.trim());
  const observation = await browser.evaluate(inspectDOM);
  assert(observation.controls.length > 0 && observation.title && observation.profile === 'data-canvas',
    'Empty or incorrect audit scope');
  for (const issue of observation.issues) record(report, browser, issue.rule, 'FAIL', issue.message, issue);
  for (const unknown of observation.unknown) record(report, browser, unknown.rule, 'NOT_TESTED', unknown.reason, unknown);
  report.contrast_totals ??= { text_samples: 0, gradient_samples: 0, boundary_samples: 0 };
  report.contrast_totals.text_samples += observation.text.length;
  report.contrast_totals.gradient_samples += observation.text.filter(item => item.background?.gradient).length;
  report.contrast_totals.boundary_samples += observation.boundaries.length;
  record(report, browser, 'G3-accessibility', observation.issues.length ? 'FAIL' : 'PASS',
    'Enumerated solid-text/control/semantic/reading-order scan; partial, not full G3',
    { text_samples: observation.text.length, control_samples: observation.boundaries.length,
      targets: observation.controls.length, unresolved_paint: observation.unknown.length });
  const filename = label + '.dom.json';
  await fs.writeFile(path.join(browser.run, filename), json(observation), { flag: 'wx' });
  await auditGraphics(browser, report);
  if (keyboard) await auditKeyboard(browser, report, observation.controls);
  await browser.screenshot(label);
  await browser.contentScreenshots(label);
  return observation;
}

export async function auditGraphics(browser, report) {
  const visual = await browser.evaluate(() => {
    const canvas = document.querySelector('#flow-canvas'), description = document.querySelector('#plot-description');
    const summary = document.querySelector('#result-summary');
    const context = canvas?.getContext('2d');
    const colors = new Set();
    if (context) {
      const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
      for (let i = 0; i < pixels.length; i += 4) {
        if (pixels[i + 3]) colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]},${pixels[i + 3]}`);
        if (colors.size > 256) break;
      }
    }
    const visible = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility === 'visible';
    return { canvas: { width: canvas?.width, height: canvas?.height, colors: colors.size,
      role: canvas?.getAttribute('role'), name: canvas?.getAttribute('aria-label'),
      describedby: canvas?.getAttribute('aria-describedby') },
    description: description?.textContent, summary: summary?.textContent,
    adjacent_visible: visible(description) && visible(summary),
    metrics: Object.fromEntries(Array.from(document.querySelectorAll('[id^="metric-"]'))
      .map(el => [el.id.slice(7), el.textContent])),
    toggles: ['view-field', 'view-mesh'].map(id => {
      const el = document.getElementById(id);
      return { id, pressed: el.getAttribute('aria-pressed'), text: el.textContent,
        decoration: getComputedStyle(el).textDecorationLine };
    }),
    evidence_chart: { title: document.querySelector('#convergence-chart-title')?.textContent,
      caption: document.querySelector('.convergence-figure figcaption')?.textContent,
      curves: document.querySelectorAll('.convergence-figure polyline').length,
      table: Array.from(document.querySelectorAll('.evidence-table tbody tr')).map(row =>
        Array.from(row.cells).map(cell => cell.textContent.trim())) } };
  });
  const state = await browser.state();
  let reference;
  if (state.phase === 'ready') {
    reference = await browser.wait('recorded real HTTP result for graphics alternative', async () =>
      browser.responses.findLast(entry => entry.status === 200 && entry.payload?.metrics));
  } else if (!browser.phase.includes('loading-empty')) {
    reference = browser.responses.findLast(entry => entry.status === 200 && entry.payload?.metrics);
  }
  const named = visual.canvas.role === 'img' && visual.canvas.name &&
    visual.canvas.describedby.split(/\s+/).includes('plot-description') && visual.adjacent_visible;
  if (reference) {
    const mismatches = Object.entries(reference.payload.metrics).filter(([key, expected]) => {
      const actual = Number.parseFloat(visual.metrics[key]);
      return !Number.isFinite(actual) || Math.abs(actual - expected) > Math.max(1e-12, Math.abs(expected) * 1e-5);
    }).map(([key]) => key);
    const valid = named && visual.canvas.colors > 1 && mismatches.length === 0 &&
      visual.description.includes('P1') && visual.summary.includes('FEM') &&
      visual.description.includes(String(reference.payload.metrics.node_count)) &&
      visual.description.includes(String(reference.payload.metrics.element_count));
    record(report, browser, 'WEB-CONTENT-006', valid ? 'PASS' : 'FAIL',
      'Information canvas has named adjacent summary and independently matched real HTTP numerical metrics',
      { visual, mismatched_metrics: mismatches, reference_artifact: reference.artifact,
        limit: 'Numerical/model/range summary alternative; no claim all nodal spatial values are exposed as an in-page table' });
  } else {
    record(report, browser, 'WEB-CONTENT-006', named && visual.canvas.colors > 0 &&
      visual.description.includes('\u672a\u8ba1\u7b97') ? 'PASS' : 'FAIL',
      'Empty information canvas has explicit adjacent no-result alternative', visual);
  }
  const encoded = visual.toggles.every(toggle => ['true', 'false'].includes(toggle.pressed) &&
    toggle.text.trim() && (toggle.pressed === 'false' || toggle.decoration.includes('underline')));
  record(report, browser, 'WEB-A11Y-002', encoded ? 'PASS' : 'FAIL',
    'View state not color-only: native named buttons, aria-pressed and selected underline', { toggles: visual.toggles });
  if (visual.evidence_chart.curves) {
    record(report, browser, 'WEB-CONTENT-006', visual.evidence_chart.curves === 3 &&
      visual.evidence_chart.title && visual.evidence_chart.caption && visual.evidence_chart.table.length === 3 &&
      visual.evidence_chart.table.every(row => row.length === 3 && row[0] && row[2].includes('%')) ?
      'PASS' : 'FAIL', 'Convergence SVG has named summary and labeled numerical table instead of color-only series', visual.evidence_chart);
  }
}

export async function coverageSummary(browser, report) {
  const capability = await browser.evaluate(() => ({
    dialogs: document.querySelectorAll('dialog,[role="dialog"],[aria-modal="true"]').length,
    mobile_menus: document.querySelectorAll('[aria-haspopup="menu"],[aria-controls][aria-expanded],nav button').length,
    images: document.querySelectorAll('img,iframe').length,
    generated_content: Array.from(document.querySelectorAll('*')).flatMap(el =>
      ['::before', '::after'].filter(pseudo => !['none', 'normal', '""'].includes(getComputedStyle(el, pseudo).content))
        .map(pseudo => ({ tag: el.tagName, id: el.id, pseudo }))),
  }));
  report.page_capabilities = capability;
  for (const [rule, count] of [['WEB-A11Y-008', capability.dialogs], ['WEB-NAV-004', capability.mobile_menus]]) {
    const item = report.coverage.find(entry => entry.rule === rule);
    item.status = count ? 'NOT_TESTED' : 'NOT_APPLICABLE';
    item.reason = count ? `Found ${count} applicable UI; full workflow not implemented` :
      'Actual DOM capability scan found no applicable dialog/menu, not a missing implementation gate';
    item.actual_count = count;
  }
  const unresolved = report.checks.filter(item => item.status === 'NOT_TESTED');
  for (const rule of ['WEB-A11Y-001', 'WEB-A11Y-002']) {
    const item = report.coverage.find(entry => entry.rule === rule);
    const failures = report.checks.filter(c => c.rule === rule && c.status === 'FAIL');
    const uncertain = unresolved.filter(c => c.rule === rule);
    item.status = failures.length ? 'FAIL' : uncertain.length || capability.generated_content.length ? 'PARTIAL' : 'PASS';
    item.unresolved = uncertain.length;
    item.failure_count = failures.length;
    item.reason = 'Solid CSS/alpha, conservative sRGB gradient interval, SVG text and native input value text; boundary/outline focus. Exact uncertain paints remain enumerated.';
  }
  const contentFailure = report.checks.filter(item => item.status === 'FAIL' &&
    ['G6-content', 'WEB-TYPE-005', 'WEB-TYPE-006', 'WEB-BASE-004', 'WEB-INPUT-004', 'WEB-INPUT-005', 'WEB-CONTENT-006'].includes(item.rule));
  report.gates = {
    'G3-accessibility': { status: report.checks.some(c => c.status === 'FAIL') ? 'FAIL' :
      unresolved.length || capability.generated_content.length ||
        report.coverage.some(c => c.rule?.startsWith('WEB-A11Y') && ['PARTIAL', 'NOT_TESTED'].includes(c.status)) ?
        'PARTIAL' : 'AUTOMATED_SCOPE_PASS',
      scope: 'Text/control/focus contrast, selected semantic reading order and actual keyboard reachability; not human G3 certification',
      remaining_rules: report.coverage.filter(c => c.rule &&
        ['PARTIAL', 'NOT_TESTED'].includes(c.status) && c.rule.startsWith('WEB-A11Y')).map(c => c.rule),
      release_status: 'NOT_CERTIFIED' },
    'G6-content': { status: contentFailure.length ? 'FAIL' : 'PASS',
      scope: 'Required extension four viewports with loading/empty/error/long content, actual recovery and composition; other matrix covered only by separately verified existing harness',
      full_standard_matrix: 'NOT_TESTED_IN_THIS_EXTENSION', release_status: 'NOT_CERTIFIED' },
  };
  const g6 = report.coverage.find(item => item.gate === 'G6-content');
  g6.status = contentFailure.length ? 'FAIL' : 'PASS';
  g6.reason = 'Actual four extension viewports, all required injected states, screenshots, keyboard recovery and changed composition validated. Not blanket release PASS.';
  report.coverage.find(item => item.gate === 'G3-accessibility').status = report.gates['G3-accessibility'].status;
}

export async function auditKeyboard(browser, report, controls) {
  const expected = new Set(controls.map(c => c.selector)), seen = new Set(), trace = [];
  // Real Tab events only; no DOM focus assignment or positive tabindex sorting.
  for (let i = 0; i < controls.length * 2 + 4; i++) {
    await browser.key('Tab');
    const current = await browser.evaluate(inspectDOM, true);
    trace.push(current.active);
    if (expected.has(current.active)) seen.add(current.active);
    for (const issue of current.issues) record(report, browser, issue.rule, 'FAIL', issue.message, issue);
    for (const unknown of current.unknown) record(report, browser, unknown.rule, 'NOT_TESTED', unknown.reason, unknown);
    if (current.focus.length) report.focus_samples.push({ state: browser.phase, viewport: browser.viewportSize, ...current.focus[0] });
    if (seen.size === expected.size) break;
  }
  const missing = [...expected].filter(sel => !seen.has(sel));
  record(report, browser, 'WEB-A11Y-003', missing.length ? 'FAIL' : 'PASS', 'Real Tab reachability',
    { expected: expected.size, visited: seen.size, missing, trace });
  const before = await browser.evaluate(() => document.activeElement.tagName);
  await browser.key('Tab', 8);
  const reverse = await browser.evaluate(inspectDOM, true);
  record(report, browser, 'WEB-A11Y-003', reverse.active && reverse.active_tag !== 'BODY' ? 'PASS' : 'FAIL',
    'Real Shift+Tab reaches previous target', { before, after: reverse.active });
}

export async function auditHover(browser, report) {
  const controls = (await browser.evaluate(inspectDOM)).controls;
  for (const control of controls) {
    const point = await browser.evaluate(sel => {
      const el = document.querySelector(sel);
      el.scrollIntoView({ block: 'center', inline: 'nearest' });
      const r = el.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    }, control.selector);
    await browser.send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...point });
    // Reduced motion is emulated, so no transition timing guess is needed.
    const observation = await browser.evaluate(inspectDOM);
    for (const issue of observation.issues.filter(i => i.selector === control.selector &&
        ['WEB-A11Y-001', 'WEB-A11Y-002'].includes(i.rule))) {
      record(report, browser, issue.rule, 'FAIL', 'Hover: ' + issue.message, issue);
    }
    for (const unknown of observation.unknown.filter(i => i.selector === control.selector)) {
      record(report, browser, unknown.rule, 'NOT_TESTED', 'Hover: ' + unknown.reason, unknown);
    }
    report.hover_samples.push({ selector: control.selector, state: browser.phase, viewport: browser.viewportSize,
      text: observation.text.filter(i => i.selector === control.selector),
      boundaries: observation.boundaries.filter(i => i.selector === control.selector) });
  }
  await browser.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 0, y: 0 });
}

export async function runAudit(browser, report) {
  for (const [width, height] of MATRIX) {
    await browser.viewport(width, height);
    browser.phase = `${width}-loading-empty`;
    browser.fault = 'hold';
    await browser.navigate(false);
    await browser.wait('intercepted loading request', async () => browser.held);
    const pending = await browser.state();
    record(report, browser, 'WEB-INPUT-005', pending.busy === 'true' && pending.live === 'polite' &&
      pending.status.trim() && !pending.export_enabled ? 'PASS' : 'FAIL', 'Loading is visible, announced and stale export blocked', pending);
    record(report, browser, 'G6-content', pending.summary.includes('\u672a\u8ba1\u7b97') ? 'PASS' : 'FAIL',
      'Initial empty result is explicit, not silent blank', pending);
    await auditSnapshot(browser, report, `${width}-loading-empty`);
    await auditHover(browser, report);
    await browser.release();
    await browser.ready();
    const baseline = await auditSnapshot(browser, report, `${width}-ready`);
    await auditHover(browser, report);
    report.matrix.push({ width, height, panels: baseline.panels });
    for (const fault of ['error', 'empty', 'long']) {
      browser.phase = `${width}-${fault}`;
      browser.fault = fault;
      const start = browser.injections.length;
      await browser.activate('#solve-button');
      await browser.wait('fault request actually intercepted', async () => browser.injections.length > start);
      if (fault === 'long') {
        await browser.ready();
        const state = await browser.state();
        record(report, browser, 'WEB-TYPE-005', state.summary.includes(browser.longText) ? 'PASS' : 'FAIL',
          'Bounded long warning and unbroken URL preserved in real response UI', {
            injected_length: browser.longText.length, summary_length: state.summary.length });
      } else {
        const failure = await browser.wait('visible fault error', async () => {
          const state = await browser.state();
          return state.phase === 'failed' && state.busy === 'false' && state;
        });
        record(report, browser, 'WEB-INPUT-004', failure.error_visible && failure.error_role === 'alert' &&
          failure.error_focus && failure.error.trim() && failure.solve_enabled && !failure.export_enabled &&
          (fault !== 'error' || failure.field_invalid === 'true' && failure.field_described.split(/\s+/).includes('error')) ?
          'PASS' : 'FAIL', 'Injected fault has error position/cause, repair affordance and stale-export protection', failure);
      }
      await auditSnapshot(browser, report, `${width}-${fault}`);
      await auditHover(browser, report);
      browser.fault = null;
      browser.phase = `${width}-${fault}-recovery`;
      const responseCount = browser.responses.length;
      await browser.activate('#solve-button');
      const recovered = await browser.ready();
      const recoveryResponse = await browser.wait('new real HTTP recovery response', async () =>
        browser.responses.slice(responseCount).find(entry => entry.status === 200 && entry.payload?.metrics));
      record(report, browser, 'G6-content', !recovered.error_visible && recovered.export_enabled &&
        !recovered.summary.includes(browser.longText ?? '__not_injected__') &&
        !recovered.metric.includes('\u65e7\u7ed3\u679c') ? 'PASS' : 'FAIL',
      'Injection removed: real keyboard re-solve restores current result without reload',
      { ...recovered, new_http_artifact: recoveryResponse.artifact });
      await browser.screenshot(`${width}-${fault}-recovery`);
      await browser.contentScreenshots(`${width}-${fault}-recovery`);
    }
    browser.phase = `${width}-reset`;
    const resetCount = browser.responses.length;
    await browser.activate('#reset-button');
    const reset = await browser.ready();
    const resetResponse = await browser.wait('new reset response', async () =>
      browser.responses.slice(resetCount).find(entry => entry.status === 200 && entry.payload?.metrics));
    record(report, browser, 'WEB-A11Y-003', reset.export_enabled && !reset.error_visible ? 'PASS' : 'FAIL',
      'Real keyboard reset completes fresh default solve', { ...reset, new_http_artifact: resetResponse.artifact });
    await browser.activate('#view-mesh');
    await auditSnapshot(browser, report, `${width}-mesh-selected`);
    await auditHover(browser, report);
    await browser.activate('#view-field');
    await auditSnapshot(browser, report, `${width}-field-unselected`);
    await auditHover(browser, report);
    const details = await browser.evaluate(() => Array.from(document.querySelectorAll('details > summary')).map(el =>
      el.id ? '#' + CSS.escape(el.id) : 'details > summary'));
    for (let i = 0; i < details.length; i++) {
      await browser.activate(details[i]);
      await auditSnapshot(browser, report, `${width}-details-open-${i}`);
      await auditHover(browser, report);
    }
  }
  assert(report.matrix.length === MATRIX.length, 'Incomplete audit viewport matrix');
  report.injections = browser.injections;
  report.runtime_events = browser.runtimeEvents;
  assert(browser.injections.filter(i => i.mode === 'long').length === MATRIX.length, 'Missing long-content injections');
  const first = report.matrix[0].panels, wide = report.matrix.at(-1).panels;
  const narrowStack = first.length >= 2 && first[1].rect.y >= first[0].rect.bottom - 1;
  const wideParallel = wide.length >= 2 && wide[1].rect.x >= wide[0].rect.right - 1 &&
    Math.abs(wide[1].rect.y - wide[0].rect.y) <= 1;
  record(report, browser, 'G6-content', narrowStack && wideParallel ? 'PASS' : 'FAIL',
    'Measured data-canvas composition changes from stacked mobile to parallel desktop',
    { narrow_panels: first, wide_panels: wide });
}

export async function smoke(browser, report) {
  await browser.viewport(320, 568);
  await browser.navigate();
  const snapshot = await browser.evaluate(() => ({
    title: document.title, lang: document.documentElement.lang,
    main: document.querySelectorAll('main').length,
    controls: document.querySelectorAll('button,input,select,a[href]').length,
    ready: document.querySelector('#workspace').dataset.state,
    viewport: { width: innerWidth, height: innerHeight },
  }));
  assert(snapshot.title && snapshot.lang && snapshot.main === 1 && snapshot.controls > 0 &&
    snapshot.ready === 'ready', 'Empty or incomplete smoke scope');
  await browser.screenshot('smoke-320-ready');
  report.checks.push({ rule: 'AUDIT-SMOKE', status: 'PASS', url: browser.url,
    viewport: snapshot.viewport, detail: snapshot, scope: 'browser machinery, not G3/G6 acceptance' });
}

async function main() {
  let browser, run, rc = 2;
  const report = { tool: 'opensim-browser-audit', schema_version: 1, standard: 'shared-r41',
    started_at: new Date().toISOString(), verdict: 'RUNNING', checks: [], screenshots: [],
    release_verdict: 'NOT_CERTIFIED',
    focus_samples: [], hover_samples: [], matrix: [],
    coverage: [
      { gate: 'G3-accessibility', status: 'NOT_TESTED', reason: 'Smoke only' },
      { gate: 'G6-content', status: 'NOT_TESTED', reason: 'Smoke only' },
      { rule: 'WEB-A11Y-009', status: 'NOT_TESTED', reason: 'No desktop/mobile screen-reader session; CDP/AX is not a screen reader' },
    ] };
  try {
    const args = process.argv.slice(2), options = {};
    for (let i = 0; i < args.length; i++) {
      if (['--smoke', '--help', '--fixture'].includes(args[i])) options[args[i].slice(2)] = true;
      else {
        assert(['--url', '--out', '--chrome'].includes(args[i]) && args[i + 1], `Unknown/missing option ${args[i]}`);
        options[args[i].slice(2)] = args[++i];
      }
    }
    if (options.help) {
      console.log(`node tools/browser_audit.mjs --url URL [--smoke] --out ${OUTPUT} [--chrome PATH]`);
      return;
    }
    assert(options.out, '--out required');
    run = await outputDirectory(options.out);
    report.output = run;
    const url = authorizedURL(options.url);
    report.url = url;
    assert(!options.fixture || new URL(url).protocol === 'http:', 'Fixture mode is loopback only');
    report.mode = options.smoke ? 'AUDIT_SMOKE_NOT_ACCEPTANCE' :
      options.fixture ? 'HARNESS_FIXTURE_NOT_PRODUCT' : 'G3_G6_AUTOMATIC_EXTENSION_NOT_RELEASE';
    if (!options.smoke) {
      report.coverage = [
        { gate: 'G3-accessibility', status: 'PARTIAL', reason: 'Enumerated solid CSS text/boundary/outline, DOM semantics and real keyboard paths; not a full external audit engine' },
        { gate: 'G6-content', status: 'PARTIAL', reason: 'Four requested widths, held initial loading/empty result, HTTP503/empty-response/long-warning faults and keyboard recovery; other content variants untested' },
        { rule: 'WEB-A11Y-009', status: 'NOT_TESTED', reason: 'No actual desktop/mobile screen reader; neither DOM nor AX is screen-reader evidence' },
        { rule: 'WEB-A11Y-008', status: 'NOT_TESTED', reason: 'Full dialog trap/background/return-focus model not implemented' },
        { rule: 'WEB-NAV-004', status: 'NOT_TESTED', reason: 'Mobile menu open/escape/close workflow not implemented' },
        { rule: 'WEB-A11Y-001', status: 'PARTIAL', reason: 'Complex backgrounds, SVG/canvas paint, generated content and pseudo/native subcontrols listed separately as NOT_TESTED' },
        { rule: 'WEB-A11Y-002', status: 'PARTIAL', reason: 'Solid border/fill/outline only; complex paint, canvas/icons and box-shadow focus untested' },
        { rule: 'WEB-A11Y-003', status: 'PARTIAL', reason: 'Actual Tab/Shift+Tab, Enter solve/reset/view/details paths; export/import/native picker and all arrow-key models delegated to existing acceptance or NOT_TESTED' },
        { rule: 'WEB-A11Y-005', status: 'PARTIAL', reason: 'Selected structural sibling groups and CSS order only; nested bidirectional visual interpretation untested' },
        { rule: 'WEB-INPUT-002', status: 'NOT_TESTED', reason: 'Target spacing/parent hit-area equivalence not audited' },
        { rule: 'PHYSICAL-TOUCH', status: 'NOT_TESTED', reason: 'No physical touch or soft keyboard' },
        { rule: 'FULL-STANDARD-MATRIX', status: 'NOT_TESTED', reason: 'Extension uses required 320/768/1064/1440; remaining 360/390/1024/1280 and breakpoint/zoom modes belong to browser_check.mjs' },
        { gate: 'G5-performance', status: 'NOT_TESTED', reason: 'No production network/CPU/cache/transfer budgets' },
        { rule: 'SCREENSHOT-REVIEW', status: 'NOT_TESTED', reason: 'Capture and measured composition are not human visual acceptance' },
      ];
    }
    const git = spawnSync('git', ['-C', ROOT, 'rev-parse', 'HEAD'], { encoding: 'utf8' });
    assert.equal(git.status, 0, git.stderr);
    report.commit = git.stdout.trim();
    report.tool_sha256 = hash(await fs.readFile(fileURLToPath(import.meta.url)));
    browser = new Browser(url, run, report, options.chrome);
    await browser.launch();
    if (options.smoke) {
      await smoke(browser, report);
      await browser.identity('before');
      await Promise.all(browser.tasks);
      await browser.identity('after');
    }
    else {
      await browser.viewport(320, 568);
      await browser.navigate();
      await browser.identity('before');
      if (options.fixture) {
        assert(await browser.evaluate(() => document.body.dataset.auditFixture === 'true'),
          'Fixture did not attest test-only renderer output');
      }
      await runAudit(browser, report);
      await Promise.all(browser.tasks);
      await browser.identity('after');
      await coverageSummary(browser, report);
    }
    browser.assertErrors();
    const failed = report.checks.filter(c => c.status === 'FAIL');
    report.verdict = failed.length ? 'FAIL' : options.smoke ? 'SMOKE_PASS' :
      options.fixture ? 'FIXTURE_AUTOMATED_PASS' : 'AUTOMATED_SCOPE_PASS';
    report.findings = { failure_count: failed.length,
      not_tested_count: report.checks.filter(c => c.status === 'NOT_TESTED').length,
      failure_rules: [...new Set(failed.map(c => c.rule))] };
    if (options.smoke) report.gates = {};
    rc = failed.length ? 1 : 0;
  } catch (error) {
    report.verdict = 'FAIL';
    report.harness_failure = { rule: 'AUDIT-HARNESS', message: error.message, stack: error.stack,
      url: report.url ?? null, viewport: browser?.viewportSize ?? null, state: browser?.phase };
    report.first_failure ??= report.harness_failure;
    console.error(`AUDIT FAIL ${error.message}`);
  } finally {
    if (browser) {
      report.injections = browser.injections;
      report.runtime_events = browser.runtimeEvents;
      try { await browser.close(); }
      catch (error) {
        report.cleanup_failure = { message: error.message, errors: error.errors?.map(e => e.message) };
        report.verdict = 'FAIL'; rc = 2;
      }
    }
    if (run) {
      report.exit_code = rc;
      report.finished_at = new Date().toISOString();
      report.coverage_counts = {
        matrix: report.matrix.length, checks: report.checks.length, screenshots: report.screenshots.length,
        focus_samples: report.focus_samples.length, hover_samples: report.hover_samples.length,
        injected_requests: report.injections?.length ?? 0,
      };
      await fs.writeFile(path.join(run, 'report.json'), json(report), { flag: 'wx' });
      assert.equal(JSON.parse(await fs.readFile(path.join(run, 'report.json'), 'utf8')).exit_code, rc);
      const line = `AUDIT verdict=${report.verdict} rc=${rc} report=${path.join(run, 'report.json')}`;
      await fs.writeFile(path.join(run, 'logs', 'audit.log'), line + '\n', { flag: 'wx' });
      console.log(line);
    }
    process.exitCode = rc;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
