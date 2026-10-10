#!/usr/bin/env node
/**
 * Real OpenSim DOM/CDP contract checks, with response-layer fault injection.
 * Requires existing Chromium and Node native WebSocket or existing ws.
 * --minimal preserves the first missing-provenance regression, not full acceptance.
 * Evidence is immutable under seat-ui; no installation or production requests.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { constants, fsyncSync } from 'node:fs';
import * as fs from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { inflateSync } from 'node:zlib';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUT = '/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-ui';
const FOCUS_OUT = '/hf3fs-jd/data/tmp/opensim-fluid-v1/resume-s5-ui-focus-20261010';
const CHROME = '/home/mch/.cache/ms-playwright/chromium-1187/chrome-linux/chrome';
const SOURCES = ['tools/render_page.py', 'web/app.js', 'web/app.css', 'solver.py', 'server.py'];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const sha = value => createHash('sha256').update(value).digest('hex');
const encode = value => JSON.stringify(value, null, 2) + '\n';
const requireCheck = (condition, message, detail = {}) => {
  if (!condition) throw Object.assign(new Error(message), { detail });
};

function pngPixels(bytes) {
  assert(bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])));
  const width = bytes.readUInt32BE(16), height = bytes.readUInt32BE(20);
  requireCheck(bytes[24] === 8 && [2, 6].includes(bytes[25]) && bytes[28] === 0,
    'Unsupported screenshot PNG encoding');
  const channels = bytes[25] === 6 ? 4 : 3, blocks = [];
  for (let offset = 8; offset < bytes.length;) {
    const size = bytes.readUInt32BE(offset);
    if (bytes.toString('ascii', offset + 4, offset + 8) === 'IDAT') {
      blocks.push(bytes.subarray(offset + 8, offset + 8 + size));
    }
    offset += size + 12;
  }
  const packed = inflateSync(Buffer.concat(blocks)), stride = width * channels;
  requireCheck(packed.length === (stride + 1) * height, 'Incomplete PNG scanlines');
  const pixels = Buffer.alloc(stride * height);
  const paeth = (a, b, c) => {
    const p = a + b - c, da = Math.abs(p - a), db = Math.abs(p - b), dc = Math.abs(p - c);
    return da <= db && da <= dc ? a : db <= dc ? b : c;
  };
  for (let y = 0; y < height; y++) {
    const filter = packed[y * (stride + 1)];
    requireCheck(filter <= 4, 'Invalid PNG filter');
    for (let x = 0; x < stride; x++) {
      const i = y * stride + x, left = x >= channels ? pixels[i - channels] : 0;
      const up = y ? pixels[i - stride] : 0, diagonal = y && x >= channels ? pixels[i - stride - channels] : 0;
      pixels[i] = (packed[y * (stride + 1) + x + 1] +
        [0, left, up, Math.floor((left + up) / 2), paeth(left, up, diagonal)][filter]) & 255;
    }
  }
  return { width, height, pixel: (x, y) => [...pixels.subarray(
    (y * width + x) * channels, (y * width + x) * channels + 3)] };
}

const luminance = rgb => rgb.map(channel => {
  const v = channel / 255;
  return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
}).reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0);
const contrast = (a, b) => (Math.max(luminance(a), luminance(b)) + 0.05) /
  (Math.min(luminance(a), luminance(b)) + 0.05);

function args(argv) {
  const options = { chrome: CHROME };
  for (let i = 0; i < argv.length; i++) {
    if (['--minimal', '--import-focus'].includes(argv[i])) options[argv[i].slice(2)] = true;
    else {
      requireCheck(['--url', '--out', '--chrome', '--finalize-fault', '--finalize-io'].includes(argv[i]) && argv[i + 1],
        `Invalid argument ${argv[i]}`);
      options[argv[i].slice(2)] = argv[++i];
    }
  }
  requireCheck(options.url && options.out, '--url and --out are required');
  const url = new URL(options.url);
  requireCheck(url.protocol === 'http:' && url.hostname === '127.0.0.1' &&
    !url.username && !url.password && url.pathname === '/' && !url.search && !url.hash,
  'Only the owned credential-free loopback root is allowed');
  options.out = path.resolve(options.out);
  const roots = [OUT, ...(options['import-focus'] ? [FOCUS_OUT] : [])];
  requireCheck(roots.some(root => options.out === root || options.out.startsWith(root + path.sep)),
    'Evidence must stay under seat-ui or the focused import review root');
  requireCheck(!options['import-focus'] ||
    (!options.minimal && !options['finalize-fault'] && !options['finalize-io']),
  'Import focus cannot be combined with unrelated regressions');
  if (options['finalize-fault']) {
    requireCheck(options.minimal && [
      'log-write', 'log-readback', 'evidence-write', 'evidence-readback',
      'final-write', 'final-readback',
    ].includes(options['finalize-fault']), 'Finalization fault requires a named minimal real run');
  }
  if (options['finalize-io']) {
    requireCheck(options.minimal && !options['finalize-fault'] && [
      'directory-evidence', 'dev-full-log', 'closed-fd-file-sync', 'closed-fd-directory-sync',
      'same-bytes-provisional-replacement', 'same-bytes-final-replacement',
      'symlink-final-replacement', 'late-evidence-directory',
    ].includes(options['finalize-io']), 'Filesystem negative requires a named minimal real run');
  }
  return options;
}

class Browser {
  constructor(options, run, report) {
    Object.assign(this, { options, run, report, pending: new Map(), id: 0, calls: [],
      failures: [], downloads: [], tasks: new Set(), injection: null, held: null });
  }
  async wait(label, action) {
    const until = Date.now() + 30000;
    while (Date.now() < until) {
      if (this.failures.length) throw Object.assign(new Error(this.failures[0].message),
        { detail: this.failures[0] });
      const result = await action();
      if (result) return result;
      await sleep(25);
    }
    throw new Error(`Timed out: ${label}`);
  }
  track(action) {
    const task = action.catch(error => this.failures.push({ message: error.message, stack: error.stack }));
    this.tasks.add(task);
    void task.finally(() => this.tasks.delete(task));
  }
  send(method, params = {}, browser = false) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP timed out: ${method}`));
      }, 30000);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.send(JSON.stringify({ id, method, params,
        ...(!browser && this.session ? { sessionId: this.session } : {}) }));
    });
  }
  async evaluate(fn, ...values) {
    const response = await this.send('Runtime.evaluate', {
      expression: `(${fn.toString()})(...${JSON.stringify(values)})`,
      returnByValue: true, awaitPromise: true,
    });
    requireCheck(!response.exceptionDetails, 'Browser evaluation failed',
      response.exceptionDetails ?? {});
    return response.result.value;
  }
  async launch() {
    await fs.access(this.options.chrome, fs.constants.X_OK);
    let Socket = globalThis.WebSocket;
    if (!Socket) {
      const require = createRequire(import.meta.url);
      Socket = require('/usr/share/nodejs/ws/index.js');
    }
    const profile = path.join(this.run, 'chromium-profile');
    this.chrome = spawn(this.options.chrome, [
      '--headless=new', '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
      `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
      '--disable-background-networking', '--disable-component-update',
      '--disable-default-apps', '--disable-breakpad', '--password-store=basic', 'about:blank',
    ], { stdio: ['ignore', 'pipe', 'pipe'] });
    this.chromeLog = [];
    this.chrome.stdout.on('data', data => this.chromeLog.push(data));
    this.chrome.stderr.on('data', data => this.chromeLog.push(data));
    this.chrome.on('error', error => this.failures.push({ message: error.message }));
    const endpoint = await this.wait('Owned Chromium endpoint', async () => {
      requireCheck(this.chrome.exitCode === null, `Chromium exited ${this.chrome.exitCode}`);
      try { return await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8'); }
      catch (error) { if (error.code !== 'ENOENT') throw error; return false; }
    });
    const [port, route] = endpoint.trim().split('\n');
    requireCheck(/^\d+$/.test(port) && route.startsWith('/devtools/browser/'),
      'Invalid CDP endpoint');
    this.socket = new Socket(`ws://127.0.0.1:${port}${route}`);
    await new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, { once: true });
      this.socket.addEventListener('error', reject, { once: true });
    });
    this.socket.addEventListener('message', event => {
      const message = JSON.parse(typeof event.data === 'string' ? event.data : event.data.toString());
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        clearTimeout(pending.timer);
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(JSON.stringify(message.error)));
        else pending.resolve(message.result);
      } else this.event(message);
    });
    this.report.browser = await this.send('Browser.getVersion', {}, true);
    const { targetId } = await this.send('Target.createTarget', { url: 'about:blank' }, true);
    this.session = (await this.send('Target.attachToTarget', { targetId, flatten: true }, true)).sessionId;
    for (const domain of ['Page', 'Runtime', 'Network', 'DOM']) await this.send(`${domain}.enable`);
    await this.send('Browser.setDownloadBehavior', {
      behavior: 'allowAndName', downloadPath: path.join(this.run, 'downloads'), eventsEnabled: true,
    }, true);
    await this.send('Fetch.enable', { patterns: [
      { urlPattern: '*', requestStage: 'Request' },
      { urlPattern: '*api/solve', requestStage: 'Response' },
    ] });
  }
  event(message) {
    const { method, params, sessionId } = message;
    if (method === 'Browser.downloadWillBegin') this.downloads.push({ ...params, state: 'inProgress' });
    if (method === 'Browser.downloadProgress') {
      Object.assign(this.downloads.find(item => item.guid === params.guid) ?? {}, params);
    }
    if (sessionId !== this.session) return;
    if (method === 'Runtime.exceptionThrown') this.failures.push({
      message: params.exceptionDetails.exception?.description ?? params.exceptionDetails.text,
      coordinate: this.coordinate,
    });
    if (method === 'Runtime.consoleAPICalled') {
      this.report.console.push({ coordinate: this.coordinate, type: params.type,
        text: params.args.map(value => value.value ?? value.description).join(' ') });
    }
    if (method === 'Fetch.requestPaused') this.track(this.intercept(params));
  }
  async intercept(event) {
    const url = new URL(event.request.url);
    requireCheck(url.origin === new URL(this.options.url).origin ||
      ['about:', 'data:', 'blob:'].includes(url.protocol), 'Unexpected external request',
    { url: url.origin });
    if (!event.responseStatusCode) {
      await this.send('Fetch.continueRequest', { requestId: event.requestId });
      return;
    }
    const body = await this.send('Fetch.getResponseBody', { requestId: event.requestId });
    const text = body.base64Encoded ? Buffer.from(body.body, 'base64').toString('utf8') : body.body;
    const original = JSON.parse(text);
    const call = { coordinate: this.coordinate, status: event.responseStatusCode,
      config: JSON.parse(event.request.postData), result: original,
      original_sha256: sha(text), artifact: `solve-${String(this.calls.length + 1).padStart(3, '0')}.json` };
    const injection = this.injection;
    this.injection = null;
    let delivered = text, status = call.status;
    if (injection?.mutate) {
      const altered = structuredClone(original);
      injection.mutate(altered);
      delivered = JSON.stringify(altered);
    }
    if (injection?.raw !== undefined) delivered = injection.raw;
    if (injection?.status) status = injection.status;
    call.delivered_sha256 = sha(delivered);
    call.injected = injection?.name ?? null;
    await fs.writeFile(path.join(this.run, call.artifact), encode(call), { flag: 'wx' });
    this.calls.push(call);
    const release = async () => {
      if (!injection || (injection.hold && !injection.mutate && injection.raw === undefined)) {
        await this.send('Fetch.continueResponse', { requestId: event.requestId });
      } else {
        await this.send('Fetch.fulfillRequest', {
          requestId: event.requestId, responseCode: status,
          responseHeaders: [{ name: 'Content-Type', value: 'application/json; charset=utf-8' }],
          body: Buffer.from(delivered).toString('base64'),
        });
      }
    };
    if (injection?.hold) this.held = { call, release };
    else await release();
  }
  async state() {
    return this.evaluate(() => ({
      phase: document.querySelector('#workspace').dataset.state,
      statusPhase: document.querySelector('#status').dataset.state,
      busy: document.querySelector('#case-form').getAttribute('aria-busy'),
      exportDisabled: document.querySelector('#export-button').disabled,
      locked: [...document.querySelectorAll('#case-form input,#case-form select,#solve-button,#reset-button,#import-input')]
        .map(node => ({ id: node.id, disabled: node.disabled, value: node.value })),
      error: document.querySelector('#error').hidden ? '' : document.querySelector('#error').textContent,
      active: document.activeElement.id,
      summary: document.querySelector('#result-summary').textContent,
      metric: document.querySelector('#metric-flow_rate_m3_s').textContent,
      pressure: document.querySelector('#pressure_pa').value,
      file: document.querySelector('#import-input').value,
    }));
  }
  async ready() {
    return this.wait('Actual current successful DOM result', async () => {
      const state = await this.state();
      if (state.busy !== 'false') return false;
      requireCheck(!state.error, 'Unexpected UI error while waiting for success', state);
      return state.phase === 'ready' && state.busy === 'false' && !state.exportDisabled &&
        state.locked.every(control => !control.disabled) && !/旧结果|未计算/.test(state.metric) ? state : false;
    });
  }
  async navigate() {
    await this.send('Page.navigate', { url: this.options.url });
    await this.wait('Loaded OpenSim document', () => this.evaluate(() =>
      document.readyState === 'complete' && !!document.querySelector('#case-form')));
    await this.ready();
    await this.evaluate(async () => { await document.fonts.ready; });
  }
  async key(key, modifiers = 0) {
    const [code, value] = { Tab: ['Tab', 9], Enter: ['Enter', 13], a: ['KeyA', 65],
      Backspace: ['Backspace', 8], Escape: ['Escape', 27] }[key];
    const params = { key, code, windowsVirtualKeyCode: value, modifiers };
    await this.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', ...params });
    if (key === 'Enter') await this.send('Input.dispatchKeyEvent', { type: 'char', ...params, text: '\r' });
    await this.send('Input.dispatchKeyEvent', { type: 'keyUp', ...params });
  }
  async tabTo(selector) {
    for (let i = 0; i < 65; i++) {
      if (await this.evaluate(sel => document.activeElement.matches(sel), selector)) return;
      await this.key('Tab');
    }
    throw new Error(`Native Tab could not reach ${selector}`);
  }
  async activate(selector) {
    await this.tabTo(selector);
    await this.key('Enter');
  }
  async input(value) {
    await this.tabTo('#pressure_pa');
    await this.key('a', 2);
    await this.key('Backspace');
    requireCheck(await this.evaluate(() => document.querySelector('#pressure_pa').value === ''),
      'Native keyboard did not clear pressure');
    await this.send('Input.insertText', { text: String(value) });
    await this.key('Tab');
    requireCheck((await this.state()).pressure === String(value), 'Native input value mismatch');
  }
  async solve() {
    const marker = this.calls.length;
    await this.activate('#solve-button');
    await this.ready();
    await this.wait('New actual solver response', () => this.calls.length > marker);
    return this.calls.at(-1);
  }
  async file(data, name) {
    const filename = path.join(this.run, `${name}.json`);
    await fs.writeFile(filename, typeof data === 'string' ? data : encode(data), { flag: 'wx' });
    const root = await this.send('DOM.getDocument');
    const { nodeId } = await this.send('DOM.querySelector', { nodeId: root.root.nodeId, selector: '#import-input' });
    await this.send('DOM.setFileInputFiles', { nodeId, files: [filename] });
    return filename;
  }
  async rejected(marker, imported = false) {
    return this.wait('Loud rejection and released controls', async () => {
      const state = await this.state();
      if (state.busy !== 'false') return false;
      requireCheck(state.phase === 'failed' && state.statusPhase === 'failed' &&
        state.exportDisabled && state.error && state.locked.every(control => !control.disabled) &&
        !/当前配置求解成功/.test(state.summary), 'False success or locked/old export after rejection', state);
      if (imported) requireCheck(state.active === 'error' && state.file === '',
        'Import rejection lost error focus or retained file selection', state);
      requireCheck(this.calls.length >= marker, 'Lost actual solve capture');
      return state;
    });
  }
  async screenshot(name) {
    const response = await this.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    const bytes = Buffer.from(response.data, 'base64');
    requireCheck(bytes.length > 100 && bytes.subarray(0, 8).equals(
      Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])), 'Empty or invalid PNG');
    const artifact = { file: `${name}.png`, sha256: sha(bytes), bytes: bytes.length,
      width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20), coordinate: this.coordinate };
    await fs.writeFile(path.join(this.run, artifact.file), bytes, { flag: 'wx' });
    requireCheck(sha(await fs.readFile(path.join(this.run, artifact.file))) === artifact.sha256,
      'PNG readback hash mismatch');
    this.report.screenshots.push(artifact);
    return artifact;
  }
  async exportCurrent(expected) {
    const marker = this.downloads.length;
    await this.activate('#export-button');
    const download = await this.wait('Actual completed JSON download', () =>
      this.downloads.slice(marker).find(item => item.state === 'completed'));
    const bytes = await fs.readFile(path.join(this.run, 'downloads', download.guid));
    const result = JSON.parse(bytes.toString('utf8'));
    requireCheck(JSON.stringify(result) === JSON.stringify(expected), 'Export differs from actual solver result');
    return { guid: download.guid, bytes: bytes.length, sha256: sha(bytes) };
  }
  async release() {
    const held = this.held;
    requireCheck(held, 'No owned paused response');
    this.held = null;
    await held.release();
  }
  async check(name, action) {
    this.coordinate = name;
    await fs.appendFile(path.join(this.run, 'run.log'), `${new Date().toISOString()} START ${name}\n`);
    try {
      const detail = await action();
      this.report.checks.push({ name, verdict: 'PASS', detail });
      await fs.appendFile(path.join(this.run, 'run.log'), `${new Date().toISOString()} CHECK ${name} PASS\n`);
      console.log(`UI_CONTRACT_CHECK name=${name} verdict=PASS`);
    } catch (error) {
      this.report.checks.push({ name, verdict: 'FAIL', message: error.message, detail: error.detail });
      await fs.appendFile(path.join(this.run, 'run.log'), `${new Date().toISOString()} CHECK ${name} FAIL ${error.message}\n`);
      throw error;
    }
  }
  async close() {
    if (this.held) { await this.held.release(); this.held = null; }
    await Promise.all(this.tasks);
    if (this.chrome?.exitCode === null) {
      const closed = once(this.chrome, 'close');
      this.chrome.kill('SIGTERM');
      requireCheck(await Promise.race([closed.then(() => true), sleep(5000).then(() => false)]),
        'Owned Chromium did not stop after SIGTERM');
    }
    this.socket?.close();
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new Error('Owned browser closed'));
    }
    this.pending.clear();
    await fs.writeFile(path.join(this.run, 'chromium.log'), Buffer.concat(this.chromeLog ?? []));
  }
}

function realResult(call, sources) {
  requireCheck(call.status === 200 && !call.injected, 'Baseline is not a real successful solve');
  const result = call.result;
  requireCheck(result.reproducibility.source_sha256 &&
    SOURCES.every(key => result.reproducibility.source_sha256[key] === sources[key]),
  'Actual solve sources do not match this worktree');
  requireCheck(Date.parse(result.reproducibility.computed_at) > 0 &&
    result.metrics.relative_residual < 1e-7 && result.metrics.reynolds <= 2000,
  'Actual solve lacks time or valid residual/model boundary');
  const { mesh, fields, metrics } = result;
  let flow = 0;
  for (const [i, j, k] of mesh.triangles) {
    const [a, b, c] = [mesh.nodes_mm[i], mesh.nodes_mm[j], mesh.nodes_mm[k]];
    const area = Math.abs((b[0] - a[0]) * (c[1] - a[1]) -
      (c[0] - a[0]) * (b[1] - a[1])) / 2e6;
    requireCheck(area > 0, 'Degenerate actual mesh');
    flow += area * (fields.velocity_m_s[i] + fields.velocity_m_s[j] + fields.velocity_m_s[k]) / 3;
  }
  requireCheck(flow > 0 && Math.abs(flow / metrics.flow_rate_m3_s - 1) < 1e-9 &&
    mesh.boundary_nodes.every(i => fields.velocity_m_s[i] === 0) &&
    metrics.node_count === mesh.nodes_mm.length, 'Actual P1 integral/no-slip/count mismatch');
  return { artifact: call.artifact, computed_at: result.reproducibility.computed_at,
    integrated_flow_m3_s: flow, nodes: mesh.nodes_mm.length, elements: mesh.triangles.length };
}

async function badImports(browser, variants, focusNegatives = false) {
  for (const variant of variants) {
    await browser.check(`bad-import-${variant}`, async () => {
      const sample = structuredClone(browser.calls.at(-1).result);
      let data = sample;
      if (variant === 'null') data = null;
      if (variant === 'empty') data = '';
      if (variant === 'invalid-json') data = '{';
      if (variant === 'unknown') data = { ...sample.config, unexpected: 1 };
      if (variant === 'null-config') sample.config = null;
      if (variant === 'null-stamp') sample.reproducibility = null;
      if (variant === 'missing-time') delete sample.reproducibility.computed_at;
      if (variant === 'null-source') sample.reproducibility.source_sha256 = null;
      if (variant === 'nonfinite') data = JSON.stringify({ ...sample.config, pressure_pa: 'OVERFLOW' })
        .replace('"OVERFLOW"', '1e999');
      if (variant === 'null-field') sample.fields.velocity_m_s[0] = null;
      const marker = browser.calls.length;
      await browser.file(data, `bad-${variant}`);
      const state = await browser.rejected(marker, true);
      requireCheck(browser.calls.length === marker && state.file === '',
        'Invalid file triggered solve or remained selected');
      if (focusNegatives) {
        requireCheck(state.active === 'error', 'Focus negative lacks a real focused import error', state);
        // Remove focus using real keyboard traversal, not a scripted product mutation.
        await browser.key('Tab');
        const displaced = await browser.state();
        requireCheck(displaced.active !== 'error' && displaced.file === '' &&
          displaced.busy === 'false' && displaced.phase === 'failed',
        'Focus negative did not isolate lost error focus', displaced);
        await assert.rejects(() => browser.rejected(marker, true), error =>
          error.message === 'Import rejection lost error focus or retained file selection' &&
          error.detail.active === displaced.active && error.detail.file === '');
        await browser.file(data, `bad-${variant}-focus-return`);
        await browser.rejected(marker, true);
        // Exercise the same rejection API with only file readback changed.
        const retained = Object.create(browser);
        retained.state = async () => ({ ...state, file: 'C:\\fakepath\\bad-import.json' });
        await assert.rejects(() => retained.rejected(marker, true), error =>
          error.message === 'Import rejection lost error focus or retained file selection' &&
          error.detail.active === 'error' && error.detail.file !== '');
        requireCheck(browser.calls.length === marker, 'Focus negatives unexpectedly triggered a solve');
      }
      const recovery = await browser.solve();
      requireCheck(browser.calls.length === marker + 1, 'Import recovery did not perform one new actual solve');
      realResult(recovery, browser.report.sources);
      return { no_http_solve: true, rejection: state, recovery: recovery.artifact,
        recovery_state: await browser.state(),
        focus_negatives: focusNegatives ? {
          lost_focus: 'REAL_NATIVE_TAB', retained_file: 'REJECTION_API_STATE_DOUBLE',
          product_mutated: false, no_additional_http: true,
        } : null };
    });
  }
}

async function exercise(browser, report, options) {
  await browser.send('Emulation.setDeviceMetricsOverride', {
    width: 320, height: 568, deviceScaleFactor: 1, mobile: false,
  });
  await browser.navigate();
  await browser.check('actual-320px-FEM', async () => {
    const baseline = realResult(browser.calls.at(-1), report.sources);
    await browser.screenshot('initial-320');
    return { ...baseline, state: await browser.state() };
  });
  if (options['import-focus']) {
    await badImports(browser, ['invalid-json'], true);
    return;
  }
  const defects = [
    ['missing-provenance', result => { delete result.reproducibility; }],
  ];
  if (!options.minimal) defects.push(
    ['null-result', null, 'null'],
    ['false-success', null, '{"status":"ok"}'],
    ['null-provenance', result => { result.reproducibility = null; }],
    ['missing-time', result => { delete result.reproducibility.computed_at; }],
    ['invalid-calendar-time', result => { result.reproducibility.computed_at = '2026-02-30T12:00:00Z'; }],
    ['future-time', result => { result.reproducibility.computed_at = '2099-01-01T00:00:00Z'; }],
    ['missing-sources', result => { delete result.reproducibility.source_sha256; }],
    ['null-sources', result => { result.reproducibility.source_sha256 = null; }],
    ['incomplete-sources', result => { delete result.reproducibility.source_sha256['web/app.css']; }],
    ['foreign-js-source', result => { result.reproducibility.source_sha256['web/app.js'] = '0'.repeat(64); }],
    ['mismatched-solver', result => { result.reproducibility.solver_sha256 = '0'.repeat(64); }],
    ['wrong-config-digest', result => { result.reproducibility.config_sha256 = '0'.repeat(64); }],
    ['null-metric', result => { result.metrics.flow_rate_m3_s = null; }],
    ['bool-metric', result => { result.metrics.reynolds = false; }],
    ['nonfinite-json', null, '{"schema_version":1,"metric":NaN}'],
    ['overflow-number', result => { result.metrics.flow_rate_m3_s = '__INFINITY__'; }],
    ['null-node', result => { result.mesh.nodes_mm[0][0] = null; }],
    ['null-velocity', result => { result.fields.velocity_m_s[0] = null; }],
  );
  for (const [name, mutate, raw] of defects) {
    await browser.check(`response-${name}`, async () => {
      await browser.ready();
      const marker = browser.calls.length;
      if (name === 'overflow-number') {
        const sample = structuredClone(browser.calls.at(-1).result);
        sample.metrics.flow_rate_m3_s = '__INFINITY__';
        browser.injection = { name, raw: JSON.stringify(sample).replace('"__INFINITY__"', '1e999') };
      } else browser.injection = { name, mutate, raw };
      await browser.activate('#solve-button');
      await browser.wait('Injected actual response', () => browser.calls.length > marker);
      const state = await browser.rejected(marker + 1);
      const downloadCount = browser.downloads.length;
      await browser.evaluate(() => document.querySelector('#export-button')
        .dispatchEvent(new MouseEvent('click', { bubbles: true })));
      requireCheck(browser.downloads.length === downloadCount, 'Rejected result was exported');
      const call = browser.calls.at(-1);
      await browser.solve();
      return { injected_actual_response: call.artifact, state, recovery: await browser.state() };
    });
  }
  if (options.minimal) return;
  await browser.check('actual-export-and-dirty-boundary', async () => {
    const original = browser.calls.at(-1).result;
    const exported = await browser.exportCurrent(original);
    await browser.input(2);
    const dirty = await browser.state();
    requireCheck(dirty.phase === 'dirty' && dirty.exportDisabled &&
      /旧结果/.test(dirty.summary), 'Dirty input retained a current export', dirty);
    const doubled = await browser.solve();
    realResult(doubled, report.sources);
    requireCheck(Math.abs(doubled.result.metrics.flow_rate_m3_s / original.metrics.flow_rate_m3_s - 2) < 1e-9,
      'Native pressure edit did not recompute a doubled FEM flow');
    return { exported, dirty, doubled: doubled.artifact };
  });
  for (const variant of ['no-stamp', 'no-sources', 'foreign-sources', 'config-wrapper', 'plain-config']) {
    await browser.check(`legacy-import-${variant}-recomputes`, async () => {
      const old = structuredClone(browser.calls.at(-1).result);
      old.config.pressure_pa = 1.23456789;
      old.warnings.push('OLD_IMPORT_MARKER_NOT_SOLVER_OUTPUT');
      let data = old;
      if (variant === 'no-stamp') delete old.reproducibility;
      if (variant === 'no-sources') delete old.reproducibility.source_sha256;
      if (variant === 'foreign-sources') {
        old.reproducibility.source_sha256['web/app.js'] = '0'.repeat(64);
      }
      if (variant === 'config-wrapper') data = { config: old.config };
      if (variant === 'plain-config') data = old.config;
      const marker = browser.calls.length;
      const source = await browser.file(data, `legacy-${variant}`);
      await browser.ready();
      await browser.wait('Import actual new solve', () => browser.calls.length > marker);
      const fresh = browser.calls.at(-1);
      realResult(fresh, report.sources);
      requireCheck(fresh.result.config.pressure_pa === old.config.pressure_pa &&
        !fresh.result.warnings.includes('OLD_IMPORT_MARKER_NOT_SOLVER_OUTPUT') &&
        (await browser.state()).file === '',
      'Legacy import did not recalculate or release file input');
      return { source: path.basename(source), actual: fresh.artifact,
        exported: await browser.exportCurrent(fresh.result) };
    });
  }
  await badImports(browser, ['null', 'empty', 'invalid-json', 'unknown', 'null-config', 'null-stamp',
    'missing-time', 'null-source', 'nonfinite', 'null-field']);
  await browser.check('busy-reentrant-import-reset-export', async () => {
    const marker = browser.calls.length, downloads = browser.downloads.length;
    browser.injection = { name: 'held-real-solve', hold: true };
    await browser.activate('#solve-button');
    await browser.wait('Paused real response', () => browser.held);
    const busy = await browser.state();
    requireCheck(busy.busy === 'true' && busy.phase === 'working' && busy.exportDisabled &&
      busy.locked.every(control => control.disabled), 'Busy state did not lock actions', busy);
    await browser.evaluate(() => {
      document.querySelector('#case-form').dispatchEvent(new Event('submit', { cancelable: true }));
      document.querySelector('#case-form').dispatchEvent(new Event('reset', { cancelable: true }));
      document.querySelector('#import-input').dispatchEvent(new Event('change'));
      document.querySelector('#export-button').dispatchEvent(new MouseEvent('click'));
    });
    requireCheck(browser.calls.length === marker + 1 && browser.downloads.length === downloads &&
      (await browser.state()).busy === 'true', 'Busy reentry started another action');
    await browser.release();
    await browser.ready();
    return { busy, recovered: await browser.state(), paused_actual: browser.calls.at(-1).artifact };
  });
  await browser.check('busy-forced-edit-discards-response', async () => {
    browser.injection = { name: 'held-edit-solve', hold: true };
    await browser.activate('#solve-button');
    await browser.wait('Held edit response', () => browser.held);
    await browser.evaluate(() => {
      const field = document.querySelector('#pressure_pa');
      field.value = '1.345';
      field.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await browser.release();
    const state = await browser.rejected(browser.calls.length);
    requireCheck(/参数发生变化/.test(state.error), 'In-flight edit was not rejected with its first cause', state);
    await browser.solve();
    return { rejected: state, recovered: browser.calls.at(-1).artifact,
      method: 'deliberate scripted disabled-control mutation; ordinary keyboard controls stay locked' };
  });
  await browser.check('busy-import-read-edit-race', async () => {
    await browser.evaluate(() => {
      window.__uiOriginalFileText = File.prototype.text;
      File.prototype.text = function () {
        const file = this;
        return new Promise((resolve, reject) => {
          window.__uiReleaseFileRead = () => window.__uiOriginalFileText.call(file).then(resolve, reject);
        });
      };
    });
    const marker = browser.calls.length;
    await browser.file({ config: browser.calls.at(-1).result.config }, 'held-file-read');
    await browser.wait('Actual file read paused', () => browser.evaluate(() => !!window.__uiReleaseFileRead));
    await browser.evaluate(() => {
      document.querySelector('#import-input').dispatchEvent(new Event('change'));
      const field = document.querySelector('#pressure_pa');
      field.value = '1.456';
      field.dispatchEvent(new Event('input', { bubbles: true }));
      window.__uiReleaseFileRead();
      File.prototype.text = window.__uiOriginalFileText;
    });
    const state = await browser.rejected(marker);
    requireCheck(browser.calls.length === marker && state.pressure === '1.456' && state.file === '',
      'Async import overwrote an edit or launched stale solve', state);
    await browser.solve();
    return { rejection: state, no_stale_solve: true };
  });
  await browser.check('busy-exit-and-reload-recovery', async () => {
    browser.injection = { name: 'held-before-exit', hold: true };
    await browser.activate('#solve-button');
    await browser.wait('Actual exit response paused', () => browser.held);
    await browser.send('Page.navigate', { url: 'about:blank' });
    // Navigation cancels this target's paused response; never release it into a new document.
    browser.held = null;
    await browser.wait('Owned document exited', () => browser.evaluate(() => location.href === 'about:blank'));
    await browser.navigate();
    realResult(browser.calls.at(-1), report.sources);
    return { after_reload: await browser.state(), actual: browser.calls.at(-1).artifact };
  });
  for (const width of [320, 1280]) {
    await browser.check(`layout-${width}-real-boxes-and-background-contrast`, async () => {
      await browser.send('Emulation.setDeviceMetricsOverride', {
        width, height: 800, deviceScaleFactor: 1, mobile: false,
      });
      return await layoutAndContrast(browser, `layout-${width}`);
    });
  }
  await browser.check('narrow-workspace-on-desktop', async () => {
    await browser.evaluate(() => document.querySelector('#workspace').style.inlineSize = '320px');
    const layout = await layoutAndContrast(browser, 'narrow-workspace');
    await browser.evaluate(() => document.querySelector('#workspace').style.removeProperty('inline-size'));
    return layout;
  });
}

async function layoutAndContrast(browser, name) {
  const measurements = [];
  const height = await browser.evaluate(() => document.documentElement.scrollHeight);
  for (let top = 0; top < height; top += 800) {
    await browser.evaluate(async y => {
      scrollTo(0, y);
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }, top);
    const dom = await browser.evaluate(() => {
      const id = node => node.id ? `#${node.id}` : node.tagName.toLowerCase() +
        (typeof node.className === 'string' && node.className ? `.${node.className.trim().replaceAll(' ', '.')}` : '');
      const visible = node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
      const elements = [...document.querySelectorAll('body *')].filter(visible);
      const overflowing = elements.filter(node => {
        if (node.closest('[data-local-scroll],pre') || node.matches('.skip-link')) return false;
        const box = node.getBoundingClientRect();
        const workspace = node.closest('#workspace')?.getBoundingClientRect();
        return box.left < -1 || box.right > document.documentElement.clientWidth + 1 ||
          (workspace && (box.left < workspace.left - 1 || box.right > workspace.right + 1));
      }).map(id);
      const controls = elements.filter(node => node.matches('button,input,select,summary,a[href]'));
      const small = controls.filter(node => {
        const box = node.getBoundingClientRect();
        return box.width < 43.99 || box.height < 43.99;
      }).map(id);
      const text = elements.filter(node => !node.matches('script,style,option') &&
        [...node.childNodes].some(child => child.nodeType === Node.TEXT_NODE && child.textContent.trim()))
        .flatMap(node => {
          const style = getComputedStyle(node);
          const color = (node instanceof SVGElement ? style.fill : style.color).match(/[\d.]+/g)?.map(Number);
          if (!color || style.opacity !== '1') throw new Error(`Unmeasured text color/opacity: ${id(node)}`);
          const range = document.createRange();
          range.selectNodeContents(node);
          const rects = [...range.getClientRects()].filter(box => box.width > 1 && box.height > 1 &&
            box.top >= 0 && box.bottom <= innerHeight && box.left >= 0 && box.right <= innerWidth);
          return rects.map(box => ({ selector: id(node), color: color.slice(0, 3), alpha: color[3] ?? 1,
            threshold: parseFloat(style.fontSize) >= 24 ||
              (parseFloat(style.fontSize) >= 18.66 && Number(style.fontWeight) >= 700) ? 3 : 4.5,
            box: { x: box.x, y: box.y, width: box.width, height: box.height } }));
        });
      return { overflowing, small, text, client: document.documentElement.clientWidth,
        scroll: document.documentElement.scrollWidth, hidden: elements.filter(node =>
          ['hidden', 'clip'].includes(getComputedStyle(node).overflowX) &&
          node.scrollWidth > node.clientWidth + 1).map(id) };
    });
    requireCheck(!dom.overflowing.length && !dom.small.length && !dom.hidden.length &&
      dom.scroll <= dom.client + 1, 'Real layout overflow, clipped content or sub-44px target', dom);
    await browser.evaluate(() => {
      window.__uiTextStyles = [...document.querySelectorAll('body *')].map(node => ({
        node, original: node.getAttribute('style'), color: getComputedStyle(node).color,
      }));
      for (const { node } of window.__uiTextStyles) {
        node.style.setProperty('color', 'transparent', 'important');
        node.style.setProperty('-webkit-text-fill-color', 'transparent', 'important');
        node.style.setProperty('text-shadow', 'none', 'important');
        if (node instanceof SVGElement && node.tagName.toLowerCase() === 'text') {
          node.style.setProperty('fill', 'transparent', 'important');
        }
      }
      if (getComputedStyle(document.querySelector('.brand')).color !== 'rgba(0, 0, 0, 0)') {
        throw new Error('Actual glyph hiding failed');
      }
    });
    let artifact;
    try { artifact = await browser.screenshot(`${name}-background-${top}`); }
    finally {
      await browser.evaluate(() => {
        for (const { node, original } of window.__uiTextStyles) {
          node.style.cssText = original ?? '';
        }
      });
      await browser.wait('Actual foreground colors restored', async () => {
        const pending = await browser.evaluate(() => window.__uiTextStyles.filter(
          ({ node, color }) => getComputedStyle(node).color !== color).map(({ node, color }) => ({
          id: node.id || node.tagName, expected: color, actual: getComputedStyle(node).color,
        })));
        if (pending.length) browser.restorePending = pending;
        return pending.length === 0;
      });
      await browser.evaluate(() => { delete window.__uiTextStyles; });
    }
    const png = pngPixels(await fs.readFile(path.join(browser.run, artifact.file)));
    for (const item of dom.text) {
      let minimum = Infinity, samples = 0;
      for (const fy of [0.25, 0.5, 0.75]) {
        for (const fx of [0.25, 0.5, 0.75]) {
          const x = Math.floor(item.box.x + item.box.width * fx);
          const y = Math.floor(item.box.y + item.box.height * fy);
          if (x < 0 || y < 0 || x >= png.width || y >= png.height) continue;
          const background = png.pixel(x, y);
          const foreground = item.color.map((v, i) => v * item.alpha + background[i] * (1 - item.alpha));
          minimum = Math.min(minimum, contrast(foreground, background));
          samples++;
        }
      }
      requireCheck(samples > 0 && minimum >= item.threshold,
        'Screenshot-background text contrast below threshold', { ...item, ratio: minimum, artifact: artifact.file });
      measurements.push({ selector: item.selector, ratio: minimum, threshold: item.threshold, samples });
    }
  }
  requireCheck(measurements.length > 20, 'Empty real contrast measurement');
  await browser.evaluate(() => scrollTo(0, 0));
  await browser.screenshot(`${name}-visible`);
  return { measurements, method: 'Real PNG backgrounds with only glyph ink made transparent; WCAG relative luminance',
    physical_device: false, screenshot_count: browser.report.screenshots.length };
}

async function rereadArtifacts(run, report, browser) {
  const files = [...report.screenshots.map(image => ({ file: image.file, sha256: image.sha256 })),
    ...browser.calls.map(call => ({ file: call.artifact, sha256: sha(encode(call)) }))];
  for (const download of browser.downloads) {
    requireCheck(download.state === 'completed', 'Noncompleted browser download in final evidence');
    const file = `downloads/${download.guid}`;
    const bytes = await fs.readFile(path.join(run, file));
    const parsed = JSON.parse(bytes.toString('utf8'));
    requireCheck(bytes.length > 100 && parsed.reproducibility &&
      SOURCES.every(key => parsed.reproducibility.source_sha256[key] === report.sources[key]),
    'Final exported file has incomplete/foreign provenance');
    files.push({ file, sha256: sha(bytes) });
  }
  for (const row of files) {
    const bytes = await fs.readFile(path.join(run, row.file));
    requireCheck(bytes.length > 0 && sha(bytes) === row.sha256,
      'Final artifact readback mismatch', { file: row.file });
  }
  for (const source of SOURCES) {
    requireCheck(sha(await fs.readFile(path.join(ROOT, source))) === report.sources[source],
      `Source changed during contract: ${source}`);
  }
  return files;
}

const ioFailure = error => ({ message: error.message, code: error.code ?? null,
  syscall: error.syscall ?? null, path: error.path ?? null, stack: error.stack });
const inode = info => ({ dev: String(info.dev), ino: String(info.ino) });
const sameInode = (info, expected) => String(info.dev) === expected.dev && String(info.ino) === expected.ino;

class DurableFiles {
  constructor(run, options) {
    Object.assign(this, { run, options, owned: new Map(), directories: new Map(), operations: [] });
  }
  async directory(directory, closed = false) {
    const handle = await fs.open(directory, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
    try {
      const info = await handle.stat();
      if (this.directories.has(directory)) {
        requireCheck(sameInode(info, this.directories.get(directory)), 'Evidence directory inode changed');
      } else this.directories.set(directory, inode(info));
      if (closed) {
        const fd = handle.fd;
        await handle.close();
        fsyncSync(fd);
      }
      await handle.sync();
      this.operations.push({ operation: 'fsync-directory', path: directory, ...inode(info) });
      requireCheck(sameInode(await fs.lstat(directory), inode(info)), 'Directory replaced during fsync');
    } finally {
      if (handle.fd !== -1) await handle.close();
    }
  }
  async verify(file) {
    const expected = this.owned.get(file);
    requireCheck(expected, 'Refusing to read unowned evidence', { file });
    const handle = await fs.open(file, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
      const info = await handle.stat();
      requireCheck(info.isFile() && sameInode(info, expected), 'Evidence inode changed; unknown file protected',
        { file, expected: { dev: expected.dev, ino: expected.ino }, actual: inode(info) });
      const bytes = await handle.readFile();
      requireCheck(bytes.equals(expected.bytes), 'Exact evidence readback mismatch', { file });
      const current = await fs.lstat(file);
      requireCheck(current.isFile() && sameInode(current, expected), 'Evidence replaced during readback', { file });
      this.operations.push({ operation: 'exact-readback', file, ...inode(current), sha256: sha(bytes) });
    } finally { await handle.close(); }
  }
  async create(file, content, closed = false) {
    const bytes = Buffer.from(content);
    const handle = await fs.open(file, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o644);
    const info = await handle.stat();
    const owned = { ...inode(info), bytes: Buffer.alloc(0) };
    this.owned.set(file, owned);
    try {
      let offset = 0;
      while (offset < bytes.length) {
        const { bytesWritten } = await handle.write(bytes, offset, bytes.length - offset, offset);
        requireCheck(bytesWritten > 0, 'Zero byte evidence write', { file });
        offset += bytesWritten;
        owned.bytes = bytes.subarray(0, offset);
      }
      if (closed) {
        const fd = handle.fd;
        await handle.close();
        fsyncSync(fd);
      }
      await handle.sync();
      this.operations.push({ operation: 'fsync-file', file, ...inode(info), sha256: sha(bytes) });
    } finally {
      if (handle.fd !== -1) await handle.close();
    }
    await this.directory(path.dirname(file));
    await this.verify(file);
  }
  async capture(file, digest) {
    const handle = await fs.open(file, constants.O_RDONLY | constants.O_NOFOLLOW);
    try {
      const info = await handle.stat(), bytes = await handle.readFile();
      if (this.owned.has(file)) {
        requireCheck(sameInode(info, this.owned.get(file)), 'Captured evidence inode changed', { file });
      }
      requireCheck(info.isFile() && bytes.length > 0 && sha(bytes) === digest,
        'Actual artifact differs before durable capture', { file });
      this.owned.set(file, { ...inode(info), bytes });
      await handle.sync();
      this.operations.push({ operation: 'fsync-file', file, ...inode(info), sha256: digest });
    } finally { await handle.close(); }
    await this.directory(path.dirname(file));
    await this.verify(file);
  }
  async append(file, content) {
    await this.verify(file);
    const expected = this.owned.get(file);
    const handle = await fs.open(file, constants.O_WRONLY | constants.O_NOFOLLOW);
    try {
      requireCheck(sameInode(await handle.stat(), expected), 'Log inode changed before append', { file });
      const bytes = Buffer.from(content), initial = expected.bytes;
      let offset = 0;
      while (offset < bytes.length) {
        const { bytesWritten } = await handle.write(bytes, offset, bytes.length - offset, initial.length + offset);
        requireCheck(bytesWritten > 0, 'Zero byte log append');
        offset += bytesWritten;
        expected.bytes = Buffer.concat([initial, bytes.subarray(0, offset)]);
      }
      await handle.sync();
      this.operations.push({ operation: 'fsync-file', file, dev: expected.dev, ino: expected.ino,
        sha256: sha(expected.bytes) });
    } finally { await handle.close(); }
    await this.directory(path.dirname(file));
    await this.verify(file);
    return expected.bytes;
  }
  async revoke(file) {
    await this.verify(file);
    const saved = path.join(this.run, `revoked-owned-${randomUUID()}-${path.basename(file)}`);
    // Move only our exact inode/bytes out of the consumable terminal name.
    await fs.rename(file, saved);
    const expected = this.owned.get(file);
    this.owned.delete(file);
    this.owned.set(saved, expected);
    await this.directory(this.run);
    await this.verify(saved);
    return { original: file, preserved: saved, dev: expected.dev, ino: expected.ino,
      sha256: sha(expected.bytes), consumable: false };
  }
}

async function filesystemNegative(stage, run, store) {
  const selected = store.options['finalize-io'];
  if (stage === 'log-write' && selected === 'dev-full-log') {
    const handle = await fs.open('/dev/full', constants.O_WRONLY | constants.O_NOFOLLOW);
    try { await handle.write(Buffer.from('UI finalization real device write\n')); }
    finally { await handle.close(); }
    throw new Error('/dev/full unexpectedly accepted data');
  }
  if (stage === 'evidence-write' && selected === 'directory-evidence') {
    await fs.mkdir(path.join(run, 'evidence.json'));
  }
  if ((stage === 'before-final' && selected === 'same-bytes-provisional-replacement') ||
      (stage === 'after-final' && ['same-bytes-final-replacement', 'symlink-final-replacement'].includes(selected))) {
    const file = path.join(run, stage === 'before-final' ? 'report.provisional.json' : 'report.json');
    const original = await fs.lstat(file), bytes = await fs.readFile(file);
    await fs.rename(file, path.join(run, `negative-original-${path.basename(file)}`));
    if (selected === 'symlink-final-replacement') {
      await fs.symlink('negative-original-report.json', file);
    } else await fs.writeFile(file, bytes, { flag: 'wx' });
    const replacement = await fs.lstat(file);
    await store.create(path.join(run, 'unknown-replacement.json'), encode({
      file, original: inode(original), replacement: inode(replacement),
      sha256: sha(bytes), symlink: replacement.isSymbolicLink(), scope: 'FILESYSTEM_NEGATIVE_NOT_PRODUCT',
    }));
  }
  if (stage === 'after-final' && selected === 'late-evidence-directory') {
    const file = path.join(run, 'evidence.json');
    await fs.rename(file, path.join(run, 'negative-original-evidence.json'));
    await fs.mkdir(file);
    await fs.readFile(file);
  }
}

async function finalize(run, report, browser, code, options, store) {
  const filename = path.join(run, 'report.json');
  let terminal = filename;
  const fault = stage => {
    if (options['finalize-fault'] === stage) {
      throw Object.assign(new Error(`Fault simulation: ${stage}`), { code: 'SIMULATED_EIO' });
    }
  };
  try {
    await store.verify(path.join(run, 'report.provisional.json'));
    const files = browser ? await rereadArtifacts(run, report, browser) : [];
    for (const row of files) await store.capture(path.join(run, row.file), row.sha256);
    const logFile = path.join(run, 'run.log');
    await store.capture(logFile, sha(await fs.readFile(logFile)));
    fault('log-write');
    await filesystemNegative('log-write', run, store);
    const logBytes = await store.append(logFile,
      `${new Date().toISOString()} CANDIDATE verdict=${report.verdict} rc=${code} checks=${report.checks.length}\n`);
    fault('log-readback');
    await store.verify(path.join(run, 'run.log'));
    const evidence = encode({ state: 'COMPLETE', run_log_sha256: sha(logBytes), files,
      actual_solver_calls: browser?.calls.length ?? 0, sources: report.sources,
      collected_at: new Date().toISOString() });
    fault('evidence-write');
    await filesystemNegative('evidence-write', run, store);
    await store.create(path.join(run, 'evidence.json'), evidence,
      options['finalize-io'] === 'closed-fd-file-sync');
    fault('evidence-readback');
    await store.verify(path.join(run, 'evidence.json'));
    await store.directory(run, options['finalize-io'] === 'closed-fd-directory-sync');
    await filesystemNegative('before-final', run, store);
    for (const file of store.owned.keys()) await store.verify(file);
    report.state = 'FINAL';
    report.evidence_sha256 = sha(evidence);
    report.run_log_sha256 = sha(logBytes);
    report.exit_code = code;
    report.durability = { state: 'FINAL_AFTER_FSYNC_AND_INODE_READBACK',
      protocol: 'exclusive-create/no-follow/fsync-file/fsync-directory/exact-inode-and-bytes-readback',
      operations: [...store.operations] };
    fault('final-write');
    await store.create(filename, encode(report));
    fault('final-readback');
    await store.verify(filename);
    // Recheck dependencies after terminal readback: a late failure must revoke PASS.
    await filesystemNegative('after-final', run, store);
    for (const file of store.owned.keys()) await store.verify(file);
    if (browser) await rereadArtifacts(run, report, browser);
    console.log(`UI_CONTRACT verdict=${report.verdict} rc=${code} checks=${report.checks.length} actual_solver_calls=${report.actual_solver_calls} report=${filename}`);
    return code;
  } catch (error) {
    const failure = { ...ioFailure(error), candidate_verdict: report.verdict,
      candidate_exit_code: code, stage: 'finalization',
      method: options['finalize-fault'] ? 'FAULT_SIMULATION' : 'REAL_FILESYSTEM_OR_READBACK' };
    report.verdict = 'FAIL';
    report.state = 'FINALIZATION_FAILED';
    report.exit_code = 2;
    report.finalization_failure = failure;
    report.finished_at = new Date().toISOString();
    report.durability = { state: 'FAILED_NOT_CONSUMABLE', operations: store.operations };
    if (store.owned.has(filename)) {
      try { report.revocation = await store.revoke(filename); }
      catch (revokeError) { report.revocation_failure = ioFailure(revokeError); }
    }
    try {
      terminal = path.join(run, 'finalization-failure.json');
      await store.create(terminal, encode(report));
      // Never overwrite an unknown report, even one with identical bytes.
      if (!(await fs.lstat(filename).catch(error => {
        if (error.code === 'ENOENT') return null;
        throw error;
      }))) {
        await store.create(filename, encode(report));
        terminal = filename;
      }
    } catch (recordError) {
      console.error(`UI_CONTRACT failure-record unavailable: ${recordError.message}`);
    }
    console.error(`UI_CONTRACT FINALIZATION_FAIL rc=2 ${failure.message}`);
    console.log(`UI_CONTRACT verdict=FAIL rc=2 checks=${report.checks.length} actual_solver_calls=${report.actual_solver_calls} report=${terminal}`);
    return 2;
  }
}

async function main() {
  const report = { schema_version: 1, tool: 'opensim-ui-contract', started_at: new Date().toISOString(),
    mode: 'REAL_DOM_CDP_CONTRACT', verdict: 'RUNNING', release_verdict: 'NOT_CERTIFIED',
    manual_gates: { physical_touch: 'NOT_RUN', screen_reader: 'NOT_RUN' },
    checks: [], screenshots: [], console: [], sources: {} };
  let run, browser, code = 2, options, store;
  try {
    options = args(process.argv.slice(2));
    const mount = await fs.realpath('/hf3fs-jd/data/tmp');
    let ancestor = options.out;
    while (true) {
      try {
        const resolved = await fs.realpath(ancestor);
        requireCheck(resolved.startsWith(mount + path.sep), 'Output ancestor escapes external data');
        break;
      } catch (error) {
        if (error.code !== 'ENOENT') throw error;
        ancestor = path.dirname(ancestor);
      }
    }
    await fs.mkdir(options.out, { recursive: true });
    run = path.join(options.out, `run-${new Date().toISOString().replaceAll(/[:.]/g, '-')}-${randomUUID().slice(0, 8)}`);
    await fs.mkdir(run);
    await fs.mkdir(path.join(run, 'downloads'));
    report.state = 'PROVISIONAL';
    report.exit_code = 2;
    store = new DurableFiles(run, options);
    await store.directory(run);
    await store.create(path.join(run, 'report.provisional.json'), encode(report));
    await store.create(path.join(run, 'run.log'), `${report.started_at} START mode=PROVISIONAL\n`);
    for (const source of SOURCES) report.sources[source] = sha(await fs.readFile(path.join(ROOT, source)));
    report.tool_sha256 = sha(await fs.readFile(fileURLToPath(import.meta.url)));
    report.target_url = options.url;
    report.mode = options['import-focus'] ? 'IMPORT_FOCUS_REGRESSION_NOT_FULL_ACCEPTANCE' :
      options.minimal ? 'MINIMAL_REGRESSION_NOT_FULL_ACCEPTANCE' : report.mode;
    browser = new Browser(options, run, report);
    await browser.launch();
    await exercise(browser, report, options);
    requireCheck(browser.failures.length === 0 && report.screenshots.length > 0 &&
      browser.calls.length > 1, 'Empty or failed browser acceptance');
    report.verdict = 'PASS';
    code = 0;
  } catch (error) {
    report.verdict = 'FAIL';
    report.failure = { message: error.message, detail: error.detail, stack: error.stack,
      coordinate: browser?.coordinate ?? 'startup' };
    if (browser?.restorePending) report.restore_pending = browser.restorePending;
    code = 1;
    console.error(`UI_CONTRACT FAIL coordinate=${report.failure.coordinate} ${error.message}`);
  } finally {
    if (browser) {
      try { await browser.close(); }
      catch (error) { report.verdict = 'FAIL'; code = 2; report.cleanup_failure = error.message; }
      report.actual_solver_calls = browser.calls.length;
      report.downloads = browser.downloads;
      report.browser_failures = browser.failures;
    }
    report.finished_at = new Date().toISOString();
    report.exit_code = code;
    if (run && store) {
      code = await finalize(run, report, browser, code, options, store);
    }
    process.exitCode = code;
  }
}

await main();
