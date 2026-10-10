(() => {
  "use strict";
  const LIMIT_BYTES = 16 * 1024 * 1024;
  const RULES = {
    width_mm: [0.1, 100, "宽度 / 直径"], height_mm: [0.1, 100, "高度"],
    length_mm: [1, 10000, "管长"], pressure_pa: [0.0001, 100000, "压差"],
    viscosity_pa_s: [0.00001, 10, "动力黏度"], density_kg_m3: [0.1, 20000, "密度"],
    resolution: [8, 48, "网格分辨率"],
  };
  const KEYS = ["geometry", ...Object.keys(RULES)];
  const METRICS = {
    node_count: ["节点数", ""], element_count: ["单元数", ""], dof_count: ["自由度", ""],
    max_velocity_m_s: ["最大速度", "m/s"], mean_velocity_m_s: ["平均速度", "m/s"],
    flow_rate_m3_s: ["体积流量", "m³/s"], reynolds: ["雷诺数 Re", ""],
    hydraulic_diameter_mm: ["水力直径", "mm"], relative_residual: ["相对残差", ""],
    relative_l2_error: ["速度相对 L2 误差", ""],
    reference_flow_rate_m3_s: ["参考流量", "m³/s"], relative_flow_error: ["流量相对误差", ""],
    force_balance_relative_error: ["力平衡相对误差", ""],
  };
  const RESULT_KEYS = ["schema_version", "config", "model", "mesh", "fields", "metrics", "reference", "warnings"];
  const SOURCE_KEYS = ["tools/render_page.py", "web/app.js", "web/app.css", "solver.py", "server.py"];
  const COLORS = [[23, 56, 117], [20, 143, 169], [65, 192, 161], [246, 210, 89], [213, 71, 42]];
  const state = { busy: false, phase: "idle", result: null, dirty: true, revision: 0, sources: null };
  let ui, defaults, observer;
  class CaseError extends Error {
    constructor(message, field, options) {
      super(message, options);
      this.name = "CaseError";
      this.field = field;
    }
  }
  function exactKeys(value, keys, label) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new CaseError(`${label}必须是对象。`);
    }
    const actual = Object.keys(value);
    if (actual.length !== keys.length || actual.some((key) => !keys.includes(key))) {
      throw new CaseError(`${label}字段不完整或包含未知字段。`);
    }
  }
  function finite(value, label) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      throw new CaseError(`${label}必须是有限数值，拒绝 bool、NaN 和 Infinity。`);
    }
    return value;
  }
  function validateConfig(config) {
    exactKeys(config, KEYS, "config");
    if (!["circle", "rectangle"].includes(config.geometry)) {
      throw new CaseError("只支持 circle 或 rectangle 截面。", "geometry");
    }
    const clean = { geometry: config.geometry };
    for (const [key, [min, max, label]] of Object.entries(RULES)) {
      const value = config[key];
      if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max ||
          (key === "resolution" && !Number.isInteger(value))) {
        throw new CaseError(`${label}须为 ${min} 到 ${max} 的${key === "resolution" ? "整数" : "有限数值"}。`, key);
      }
      clean[key] = value;
    }
    if (clean.geometry === "circle" && clean.width_mm !== clean.height_mm) {
      throw new CaseError("圆管高度必须等于直径。", "height_mm");
    }
    const ratio = clean.width_mm / clean.height_mm;
    if (clean.geometry === "rectangle" && (ratio < 0.1 || ratio > 10)) {
      throw new CaseError("矩形宽高比须在 0.1 到 10 之间。", "height_mm");
    }
    return clean;
  }
  function sameConfig(a, b) { return KEYS.every((key) => a[key] === b[key]); }
  function readConfig() {
    const config = { geometry: ui.fields.geometry.value };
    for (const key of Object.keys(RULES)) {
      const raw = ui.fields[key].value.trim();
      if (!raw || !/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(raw)) {
        throw new CaseError(`${RULES[key][2]}不能为空，且必须填写数值。`, key);
      }
      config[key] = Number(raw);
    }
    return validateConfig(config);
  }
  function extrema(values) {
    let min = Infinity, max = -Infinity;
    for (const value of values) { min = Math.min(min, value); max = Math.max(max, value); }
    return [finite(min, "最小值"), finite(max, "最大值")];
  }
  function format(value) {
    finite(value, "显示指标");
    if (value === 0) return "0";
    return Math.abs(value) < 0.0001 || Math.abs(value) >= 100000
      ? value.toExponential(5) : Number(value.toPrecision(6)).toString();
  }
  function strings(value, label) {
    if (!Array.isArray(value) || value.length > 64 ||
        value.some((item) => typeof item !== "string" || item.length > 4096)) {
      throw new CaseError(`${label}必须是有界文字列表。`);
    }
  }
  function validateTime(value) {
    const parts = typeof value === "string" && value.match(
      /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/);
    if (!parts) throw new CaseError("计算时间格式无效。");
    const [, year, month, day, hour, minute, second, zone] = parts;
    const calendar = new Date(`${year}-${month}-01T00:00:00Z`);
    calendar.setUTCMonth(calendar.getUTCMonth() + 1, 0);
    const offset = zone === "Z" ? [0, 0] : zone.slice(1).split(":").map(Number);
    if (Number(year) < 1 || Number(month) < 1 || Number(month) > 12 ||
        Number(day) < 1 || Number(day) > calendar.getUTCDate() ||
        Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59 ||
        offset[0] > 23 || offset[1] > 59 || !Number.isFinite(Date.parse(value)) ||
        Date.parse(value) > Date.now() + 60000) throw new CaseError("计算时间无效或位于未来。");
  }
  function validateSources(sources) {
    exactKeys(sources, SOURCE_KEYS, "运行源码");
    for (const value of Object.values(sources)) {
      if (typeof value !== "string" || !/^[a-f0-9]{64}$/.test(value)) {
        throw new CaseError("运行源码中的 SHA-256 格式无效。");
      }
    }
  }
  function validateResult(result, requested, legacyImport = false) {
    const hasStamp = result && Object.hasOwn(result, "reproducibility");
    if (!hasStamp && !legacyImport) throw new CaseError("在线求解结果缺少复现信息，不可采用或导出。");
    exactKeys(result, hasStamp ? [...RESULT_KEYS, "reproducibility"] : RESULT_KEYS, "求解结果");
    if (hasStamp) {
      const stamp = result.reproducibility;
      const hasSources = stamp && typeof stamp === "object" && Object.hasOwn(stamp, "source_sha256");
      const stampKeys = ["computed_at", "config_sha256", "solver_sha256", "python", "numpy", "scipy"];
      exactKeys(stamp, hasSources ? [...stampKeys, "source_sha256"] : stampKeys, "复现信息");
      if (!hasSources && !legacyImport) throw new CaseError("在线求解结果缺少完整运行源码身份。");
      if (hasSources) {
        const sources = stamp.source_sha256;
        validateSources(sources);
        if (sources["solver.py"] !== stamp.solver_sha256) {
          throw new CaseError("运行源码与求解器身份不一致。");
        }
        if (!legacyImport && (!state.sources ||
            SOURCE_KEYS.some((key) => sources[key] !== state.sources[key]))) {
          throw new CaseError("求解结果与当前页面运行源码身份不一致，请重新载入页面。");
        }
      }
      validateTime(stamp.computed_at);
      for (const key of ["config_sha256", "solver_sha256"]) {
        if (typeof stamp[key] !== "string" || !/^[a-f0-9]{64}$/i.test(stamp[key])) {
          throw new CaseError("复现信息中的 SHA-256 格式无效。");
        }
      }
      for (const key of ["python", "numpy", "scipy"]) {
        if (typeof stamp[key] !== "string" || !stamp[key].trim() || stamp[key].length > 128 ||
            /[\u0000-\u001f]/.test(stamp[key])) throw new CaseError("复现信息中的版本格式无效。");
      }
    }
    if (result.schema_version !== 1) throw new CaseError("不支持的结果版本。");
    if (!sameConfig(validateConfig(result.config), requested)) throw new CaseError("返回配置与请求不一致。");
    exactKeys(result.model, ["name", "discretization", "assumptions"], "model");
    for (const key of ["name", "discretization"]) {
      if (typeof result.model[key] !== "string" || !result.model[key].trim() ||
          result.model[key].length > 4096) throw new CaseError(`缺少 model.${key}。`);
    }
    strings(result.model.assumptions, "模型假设"); strings(result.warnings, "警告");
    exactKeys(result.mesh, ["nodes_mm", "triangles", "boundary_nodes"], "mesh");
    exactKeys(result.fields, ["velocity_m_s"], "fields");
    exactKeys(result.reference, ["velocity_m_s", "method"], "reference");
    exactKeys(result.metrics, Object.keys(METRICS), "metrics");
    const { nodes_mm: nodes, triangles, boundary_nodes: boundary } = result.mesh;
    if (!Array.isArray(nodes) || nodes.length < 3 || nodes.length > 50000 ||
        !Array.isArray(triangles) || !triangles.length || triangles.length > 100000 ||
        !Array.isArray(boundary) || !boundary.length || boundary.length > nodes.length) {
      throw new CaseError("网格为空、过大或缺少边界。");
    }
    for (const node of nodes) {
      if (!Array.isArray(node) || node.length !== 2) throw new CaseError("节点坐标格式错误。");
      node.forEach((value) => {
        if (Math.abs(finite(value, "网格坐标")) > 1000) throw new CaseError("网格坐标超出范围。");
      });
    }
    const index = (value) => Number.isInteger(value) && value >= 0 && value < nodes.length;
    for (const triangle of triangles) {
      if (!Array.isArray(triangle) || triangle.length !== 3 || !triangle.every(index) ||
          new Set(triangle).size !== 3) throw new CaseError("三角形索引无效。");
      const [a, b, c] = triangle.map((i) => nodes[i]);
      if ((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) === 0) {
        throw new CaseError("网格包含退化三角形。");
      }
    }
    if (!boundary.every(index) || new Set(boundary).size !== boundary.length) throw new CaseError("边界索引无效。");
    for (const values of [result.fields.velocity_m_s, result.reference.velocity_m_s]) {
      if (!Array.isArray(values) || values.length !== nodes.length) throw new CaseError("速度场与节点数不一致。");
      values.forEach((value) => finite(value, "节点速度"));
    }
    if (typeof result.reference.method !== "string" || !result.reference.method.trim() ||
        result.reference.method.length > 4096) throw new CaseError("缺少参考方法说明。");
    for (const key of Object.keys(METRICS)) {
      if (finite(result.metrics[key], key) < 0) throw new CaseError(`${key}不能为负。`);
    }
    for (const key of ["node_count", "element_count", "dof_count"]) {
      if (!Number.isInteger(result.metrics[key])) throw new CaseError(`${key}必须为整数。`);
    }
    if (result.metrics.node_count !== nodes.length || result.metrics.element_count !== triangles.length ||
        result.metrics.dof_count !== nodes.length - boundary.length) throw new CaseError("网格计数与指标不一致。");
    if (result.metrics.reynolds > 2000) throw new CaseError("Re 超过 2000，超出层流模型范围。");
    const range = extrema(result.fields.velocity_m_s);
    finite(range[1] - range[0], "速度范围跨度");
    return result;
  }
  async function digest(bytes) {
    if (!globalThis.crypto?.subtle) throw new CaseError("浏览器不支持 SHA-256，无法核验复现身份。");
    return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
      .map((value) => value.toString(16).padStart(2, "0")).join("");
  }
  async function verifyPageSources() {
    let response, health;
    try {
      response = await fetch("api/health", { cache: "no-store", headers: { Accept: "application/json" } });
      health = await response.json();
    } catch (cause) {
      throw new CaseError("无法核验当前服务源码身份，请检查连接后重试。", undefined, { cause });
    }
    if (!response.ok || health?.status !== "ok" || health.schema_version !== 1) {
      throw new CaseError("服务源码身份核验失败。");
    }
    validateSources(health.source_sha256);
    for (const [key, elements, attribute] of [
      ["web/app.js", [...document.scripts].filter((node) => node.src), "src"],
      ["web/app.css", [...document.querySelectorAll('link[rel="stylesheet"]')], "href"],
    ]) {
      if (elements.length !== 1) throw new CaseError("页面资源入口不唯一，无法核验源码。");
      const url = new URL(elements[0][attribute], location.href);
      const extension = key.split(".").at(-1);
      if (url.origin !== location.origin ||
          url.pathname !== `/web/app.${health.source_sha256[key]}.${extension}` ||
          url.search || url.hash) throw new CaseError("页面资源指纹与当前服务源码不一致，请重新载入。");
      const asset = await fetch(url, { cache: "no-store" });
      if (!asset.ok || await digest(await asset.arrayBuffer()) !== health.source_sha256[key]) {
        throw new CaseError("页面资源内容与完整 SHA-256 不一致。");
      }
    }
    state.sources = health.source_sha256;
  }
  async function verifyConfigDigest(result) {
    // Python serializes normalized physical quantities as floats, resolution as int.
    const number = (value) => {
      if (value < 0.0001) {
        return value.toExponential().replace(/e([+-])(\d+)$/, (_, sign, exponent) =>
          `e${sign}${exponent.padStart(2, "0")}`);
      }
      return Number.isInteger(value) ? `${value}.0` : String(value);
    };
    const canonical = `{${[...KEYS].sort().map((key) => {
      const value = result.config[key];
      return `${JSON.stringify(key)}:${key === "geometry" ? JSON.stringify(value)
        : key === "resolution" ? String(value) : number(value)}`;
    }).join(",")}}`;
    if (await digest(new TextEncoder().encode(canonical)) !== result.reproducibility.config_sha256) {
      throw new CaseError("配置 SHA-256 与实际求解参数不一致。");
    }
  }
  function setStatus(message) {
    ui.status.textContent = message;
    ui.status.dataset.state = state.phase;
    ui.workspace.dataset.state = state.phase;
    ui.form.setAttribute("aria-busy", String(state.busy));
  }
  function clearError() {
    ui.error.textContent = "";
    ui.error.hidden = true;
    for (const field of Object.values(ui.fields)) {
      field.removeAttribute("aria-invalid");
      field.setCustomValidity("");
    }
  }
  function updateControls() {
    for (const [control, disabled] of ui.lockable) control.disabled = state.busy || disabled;
    ui.exportButton.disabled = state.busy || state.phase !== "ready" || state.dirty || !state.result;
  }
  function reportError(error, terminal = true) {
    if (error instanceof CaseError) console.warn("OpenSim input/action rejected:", error);
    else console.error("OpenSim unexpected interaction failure:", error);
    if (terminal) { state.phase = "failed"; state.dirty = true; state.result = null; }
    const message = error instanceof Error ? error.message : "未知交互错误。";
    ui.error.textContent = message;
    ui.error.hidden = false;
    if (error.field && Object.hasOwn(ui.fields, error.field)) {
      ui.fields[error.field].setAttribute("aria-invalid", "true");
      ui.fields[error.field].setCustomValidity(message);
      const described = ui.fields[error.field].getAttribute("aria-describedby") || "";
      if (!described.split(/\s+/).includes("error")) {
        ui.fields[error.field].setAttribute("aria-describedby", `${described} error`.trim());
      }
    }
    ui.error.focus();
    setStatus(terminal ? "操作失败；请查看错误并修正后重试。" : "本次操作未执行：已有任务正在进行。");
    refreshResults();
    if (terminal) draw();
    updateControls();
  }
  function syncGeometry() {
    const circle = ui.fields.geometry.value === "circle";
    ui.fields.height_mm.readOnly = circle;
    ui.fields.height_mm.setAttribute("aria-readonly", String(circle));
    if (circle) ui.fields.height_mm.value = ui.fields.width_mm.value;
    ui.geometryNote.textContent = circle
      ? "圆管：宽度为直径，高度与直径同步，不可单独修改。"
      : "矩形管：宽度和高度可分别修改；宽高比限制为 0.1 到 10。";
  }
  function writeConfig(config) {
    for (const key of KEYS) ui.fields[key].value = String(config[key]);
    syncGeometry();
    state.revision += 1;
    state.dirty = true;
  }
  function changed() {
    syncGeometry();
    state.revision += 1;
    state.dirty = true;
    if (!state.busy) state.phase = "dirty";
    clearError();
    setStatus("参数已改变，需重新计算；旧结果不可导出。");
    refreshResults();
    updateControls();
  }
  async function operate(message, action) {
    if (state.busy) {
      reportError(new CaseError("已有求解或导入任务，本次操作未执行。"), false);
      return false;
    }
    state.busy = true;
    state.phase = "working";
    state.dirty = true;
    state.result = null;
    clearError();
    updateControls();
    setStatus(message);
    try {
      refreshResults();
      await action();
      return true;
    } catch (error) {
      reportError(error);
      return false;
    } finally {
      state.busy = false;
      updateControls();
      ui.form.setAttribute("aria-busy", "false");
    }
  }
  function refreshResults() {
    const result = state.result;
    const stale = state.dirty || state.phase !== "ready";
    for (const [key, node] of Object.entries(ui.metrics)) {
      const unit = METRICS[key][1];
      node.textContent = result
        ? `${format(result.metrics[key])}${unit ? ` ${unit}` : ""}${stale ? "（旧结果）" : ""}`
        : "未计算";
    }
    if (!result) {
      ui.summary.textContent = "未计算。只求解直管充分发展、不可压缩、牛顿流体稳态层流横截面；不是通用 CFD。";
      ui.description.textContent = "未计算：无 FEM 速度场。";
      return;
    }
    const [min, max] = extrema(result.fields.velocity_m_s);
    const circle = result.config.geometry === "circle";
    const referenceText = /Hagen|Poiseuille|parabolic/i.test(result.reference.method)
      ? "圆管 Hagen–Poiseuille 抛物线解析解"
      : /Fourier|sine|cosh/i.test(result.reference.method)
        ? "矩形管 Fourier（傅里叶）正弦 / 双曲余弦级数，采用 256 个奇数项及独立流量级数"
        : /[\u3400-\u9fff]/.test(result.reference.method)
          ? result.reference.method : "参考方法描述尚未有中文映射；原文保留在导出 JSON 中，请核查。";
    const warnings = result.warnings.map((text) => {
      if (/inscribed polygon|circular wall/i.test(text)) {
        return `圆壁由 ${6 * result.config.resolution} 边内接多边形近似；L2 误差在多边形域积分，流量误差以理想圆管为参考。`;
      }
      if (/Reynolds|2000 model limit/i.test(text)) {
        return "Re 已接近 2000 的模型上限；取参考解与数值解平均速度对应雷诺数的较大者，请勿据此推广到过渡流或湍流。";
      }
      return /[\u3400-\u9fff]/.test(text) ? text
        : "服务提供了尚未翻译的警告，请核查导出 JSON 中的原文；本结果不代表范围外验证。";
    });
    if (circle && !result.warnings.some((text) => /inscribed polygon|circular wall/i.test(text))) {
      warnings.push(`圆壁为 ${6 * result.config.resolution} 边内接多边形；流量参考理想圆，L2 在多边形域积分。`);
    }
    if (result.metrics.reynolds >= 1800 && !result.warnings.some((text) => /Reynolds|2000 model limit/i.test(text))) {
      warnings.push("Re ≥ 1800，接近 2000 的层流模型上限，请谨慎解读；本版不计算过渡流或湍流。");
    }
    const lines = [
      stale ? "旧结果：参数已改变或本次操作未成功，需重新计算；不可导出。" : "当前配置求解成功。",
      "云图为横截面轴向速度，由 P1 三角形有限元求解；不模拟入口、弯管、湍流或三维流线。",
      `参考方法：${referenceText}。参考数值只用于对照，不填充 FEM 云图。`,
      `当前流量相对参考误差 ${(result.metrics.relative_flow_error * 100).toFixed(3)}%；误差与残差仅验证本算例，不代表普适验证。`,
      "Re 使用参考和数值平均速度对应值的较大者；力平衡按离散壁面反力检查。",
      ...warnings.map((text) => `提醒：${text}`),
    ];
    ui.summary.textContent = lines.join("\n");
    ui.description.textContent = `${stale ? "旧结果，需重新计算。" : "当前 FEM 结果。"}`
      + `横截面轴向速度 ${format(min)} 到 ${format(max)} m/s；`
      + `${result.metrics.node_count} 节点、${result.metrics.element_count} 个 P1 三角形。`
      + `${ui.fieldToggle.enabled ? "颜色为三角形内线性插值的真实 FEM 速度。" : "速度云图已隐藏。"}`
      + `${ui.meshToggle.enabled ? "网格已显示。" : "网格已隐藏。"}不是三维流线或粒子动画。`;
  }
  function color(value) {
    const t = Math.max(0, Math.min(1, value)) * (COLORS.length - 1);
    const i = Math.min(COLORS.length - 2, Math.floor(t)), f = t - i;
    return COLORS[i].map((channel, j) => Math.round(channel * (1 - f) + COLORS[i + 1][j] * f));
  }
  function draw() {
    const canvas = ui.canvas, rect = canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const dpr = finite(window.devicePixelRatio, "设备像素比");
    if (dpr <= 0) throw new CaseError("设备像素比无效。");
    const width = Math.max(1, Math.round(rect.width * dpr));
    const height = Math.max(1, Math.round(rect.height * dpr));
    if (width * height > 16000000) throw new CaseError("画布过大，无法安全绘制。");
    canvas.width = width;
    canvas.height = height;
    const ctx = ui.context;
    const style = window.getComputedStyle(canvas);
    const ink = style.getPropertyValue("--canvas-ink").trim() || style.color || "#263444";
    const meshInk = style.getPropertyValue("--canvas-line").trim() || ink;
    ctx.clearRect(0, 0, width, height);
    const result = state.result;
    if (!result) {
      ctx.font = `${14 * dpr}px sans-serif`;
      ctx.fillStyle = ink;
      ctx.fillText("未计算：等待实际 FEM 求解", 16 * dpr, 28 * dpr);
      return;
    }
    const nodes = result.mesh.nodes_mm, triangles = result.mesh.triangles;
    const [xmin, xmax] = extrema(nodes.map((node) => node[0]));
    const [ymin, ymax] = extrema(nodes.map((node) => node[1]));
    const spanX = xmax - xmin, spanY = ymax - ymin;
    if (spanX <= 0 || spanY <= 0) throw new CaseError("网格显示范围无效。");
    const margin = Math.min(24 * dpr, width * 0.06, height * 0.06);
    const legendHeight = Math.min(82 * dpr, height * 0.3);
    const plotHeight = Math.max(1, height - legendHeight);
    const scale = Math.min((width - 2 * margin) / spanX, (plotHeight - 2 * margin) / spanY);
    if (!Number.isFinite(scale) || scale <= 0) throw new CaseError("画布可用空间不足。");
    const left = (width - spanX * scale) / 2, top = (plotHeight - spanY * scale) / 2;
    const points = nodes.map(([x, y]) => [left + (x - xmin) * scale, top + (ymax - y) * scale]);
    const values = result.fields.velocity_m_s, [vmin, vmax] = extrema(values);
    const span = vmax - vmin;
    if (ui.fieldToggle.enabled) {
      const image = ctx.createImageData(width, height), pixels = image.data;
      // Rasterize at device-pixel centers with P1 barycentric shape functions.
      // Reference/analytic values never enter this rendering path.
      for (const [ia, ib, ic] of triangles) {
        const [a, b, c] = [points[ia], points[ib], points[ic]];
        const det = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]);
        if (!Number.isFinite(det) || det === 0) throw new CaseError("投影三角形退化，无法绘图。");
        const x0 = Math.max(0, Math.floor(Math.min(a[0], b[0], c[0])));
        const x1 = Math.min(width - 1, Math.ceil(Math.max(a[0], b[0], c[0])));
        const y0 = Math.max(0, Math.floor(Math.min(a[1], b[1], c[1])));
        const y1 = Math.min(height - 1, Math.ceil(Math.max(a[1], b[1], c[1])));
        for (let y = y0; y <= y1; y += 1) {
          for (let x = x0; x <= x1; x += 1) {
            const wa = ((b[1] - c[1]) * (x + 0.5 - c[0]) + (c[0] - b[0]) * (y + 0.5 - c[1])) / det;
            const wb = ((c[1] - a[1]) * (x + 0.5 - c[0]) + (a[0] - c[0]) * (y + 0.5 - c[1])) / det;
            const wc = 1 - wa - wb;
            if (wa < -1e-9 || wb < -1e-9 || wc < -1e-9) continue;
            const velocity = wa * values[ia] + wb * values[ib] + wc * values[ic];
            const rgb = color(span === 0 ? 0 : (velocity - vmin) / span), offset = (y * width + x) * 4;
            pixels[offset] = rgb[0]; pixels[offset + 1] = rgb[1]; pixels[offset + 2] = rgb[2];
            pixels[offset + 3] = 255;
          }
        }
      }
      ctx.putImageData(image, 0, 0);
    }
    if (ui.meshToggle.enabled) {
      ctx.beginPath();
      for (const triangle of triangles) {
        const [a, b, c] = triangle.map((i) => points[i]);
        ctx.moveTo(...a); ctx.lineTo(...b); ctx.lineTo(...c); ctx.lineTo(...a);
      }
      ctx.lineWidth = Math.max(0.5, 0.6 * dpr);
      ctx.strokeStyle = meshInk;
      ctx.globalAlpha = 0.65;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    ctx.font = `${Math.max(10, Math.min(13 * dpr, width / 28))}px sans-serif`;
    ctx.fillStyle = ink;
    if (ui.fieldToggle.enabled) {
      const barX = margin, barY = plotHeight + 22 * dpr;
      const barW = width - 2 * margin, barH = Math.max(4, 9 * dpr);
      const gradient = ctx.createLinearGradient(barX, 0, barX + barW, 0);
      COLORS.forEach((rgb, i) => gradient.addColorStop(i / (COLORS.length - 1), `rgb(${rgb.join(",")})`));
      ctx.fillText("FEM 轴向速度 · m/s", barX, plotHeight + 14 * dpr);
      ctx.fillStyle = gradient; ctx.fillRect(barX, barY, barW, barH);
      ctx.fillStyle = ink;
      ctx.textAlign = "left"; ctx.fillText(format(vmin), barX, barY + barH + 17 * dpr);
      ctx.textAlign = "right"; ctx.fillText(format(vmax), barX + barW, barY + barH + 17 * dpr);
      ctx.textAlign = "left";
    } else {
      ctx.fillText("速度云图已隐藏", margin, plotHeight + 20 * dpr);
    }
    if (state.dirty || state.phase !== "ready") {
      ctx.fillStyle = ink;
      ctx.fillText("旧结果：需重新计算", margin, Math.max(12 * dpr, margin));
    }
  }
  function redraw() {
    try { refreshResults(); draw(); }
    catch (error) { reportError(error); }
  }
  function backendError(response, payload) {
    const detail = payload && payload.error;
    if (!detail || typeof detail !== "object" || typeof detail.message !== "string" ||
        typeof detail.code !== "string") {
      return new CaseError(`服务返回 HTTP ${response.status}，错误说明格式无效。`);
    }
    const raw = detail.message;
    if (response.status < 500 || response.status === 503) console.warn("OpenSim request rejected:", response.status, detail.code, raw);
    else console.error("OpenSim API failure:", response.status, detail.code, raw);
    let message;
    if (response.status === 503) message = "求解服务正忙或暂不可用，请稍后手动重试。";
    else if (response.status >= 500) message = "服务内部求解失败；请检查服务端日志后重试。";
    else if (/[\u3400-\u9fff]/.test(raw)) message = raw;
    else if (/reynolds|laminar|2000/i.test(raw)) message = "该配置的雷诺数超过 2000，超出本版稳态层流范围。";
    else if (/rectangle.*height|ratio/i.test(raw)) message = "矩形宽高比必须在 0.1 到 10 之间。";
    else if (/circle.*height|diameter/i.test(raw)) message = "圆管高度必须等于直径。";
    else if (/resolution/i.test(raw)) message = "网格分辨率必须为 8 到 48 之间的整数。";
    else if (detail.field && Object.hasOwn(RULES, detail.field)) {
      const [min, max, label] = RULES[detail.field];
      message = `${label}被服务拒绝；请填写 ${min} 到 ${max} 之间的有限数值。`;
    } else if (/geometry/i.test(raw)) message = "截面类型无效，只支持圆管或矩形管。";
    else if (/missing|unknown|unexpected|required/i.test(raw)) message = "配置字段缺失或包含未知字段，请检查导入格式。";
    else if (/json/i.test(raw)) message = "服务无法读取请求 JSON，请检查配置格式。";
    else if (/finite|number|bool/i.test(raw)) message = "配置必须使用有限数值，不能使用布尔值或非数值。";
    else message = "服务拒绝本次配置或求解失败；详细原因已记录到控制台，请核对输入与服务日志。";
    return new CaseError(`${message}（HTTP ${response.status}）`, detail.field);
  }
  async function calculate(config) {
    const revision = state.revision;
    if (!state.sources) await verifyPageSources();
    const started = Date.now();
    setStatus("正在执行实际有限元求解，请稍候。");
    let response;
    try {
      response = await fetch("api/solve", {
        method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(config), cache: "no-store",
      });
    } catch (cause) {
      throw new CaseError("无法连接求解服务；请检查网络与服务状态。", undefined, { cause });
    }
    let payload;
    try { payload = await response.json(); }
    catch (cause) {
      throw new CaseError(`服务返回 HTTP ${response.status}，但响应不是有效 JSON。`, undefined, { cause });
    }
    if (!response.ok) throw backendError(response, payload);
    const result = validateResult(payload, config);
    await verifyConfigDigest(result);
    if (Date.parse(result.reproducibility.computed_at) < started - 60000) {
      throw new CaseError("在线结果计算时间早于本次请求，不采用旧结果。");
    }
    if (revision !== state.revision || !sameConfig(readConfig(), config)) {
      throw new CaseError("求解期间参数发生变化，本次结果未采用，请重新计算。");
    }
    state.result = result;
    state.phase = "ready";
    state.dirty = false;
    refreshResults();
    draw();
    clearError();
    setStatus("当前配置求解成功；误差与残差仅描述本算例，不代表普适验证。");
  }
  function solve() {
    return operate("正在校验参数。", async () => {
      syncGeometry();
      await calculate(readConfig());
    });
  }
  function reset(event) {
    event.preventDefault();
    return operate("正在恢复默认参数并重新求解。", async () => {
      writeConfig(defaults);
      state.result = null;
      refreshResults();
      draw();
      await calculate(readConfig());
    });
  }
  async function importCase() {
    if (state.busy) {
      reportError(new CaseError("已有求解或导入任务，本次导入未执行。"), false);
      return;
    }
    const files = ui.importInput.files;
    await operate("正在校验导入文件；导入后将重新求解。", async () => {
      const revision = state.revision;
      if (!files || files.length !== 1) throw new CaseError("请选择一个 JSON 文件。");
      const file = files[0];
      if (!Number.isFinite(file.size) || file.size <= 0 || file.size > LIMIT_BYTES) {
        throw new CaseError("导入文件不能为空，且不得超过 16 MiB。");
      }
      let text;
      try { text = await file.text(); }
      catch (cause) { throw new CaseError("无法读取所选文件。", undefined, { cause }); }
      if (!text.trim() || text.length > LIMIT_BYTES) throw new CaseError("JSON 文本为空或过长。");
      let data;
      try { data = JSON.parse(text); }
      catch (cause) { throw new CaseError("文件不是有效 JSON；不接受表达式或脚本。", undefined, { cause }); }
      let config;
      if (data && typeof data === "object" && Object.hasOwn(data, "config")) {
        config = validateConfig(data.config);
        if (Object.keys(data).length === 1) exactKeys(data, ["config"], "导入对象");
        else validateResult(data, config, true);
      } else config = validateConfig(data);
      if (state.revision !== revision) {
        throw new CaseError("导入读取期间参数发生变化，本次文件未采用，请重新导入。");
      }
      writeConfig(config);
      refreshResults();
      draw();
      await calculate(config);
    });
    ui.importInput.value = "";
  }
  function exportCase() {
    if (state.busy) { reportError(new CaseError("已有任务正在进行，导出未执行。"), false); return; }
    let url, link;
    try {
      if (!state.result || state.dirty || state.phase !== "ready") {
        throw new CaseError("只有当前配置的成功求解结果可以导出，请先重新计算。");
      }
      const config = readConfig();
      if (!sameConfig(config, state.result.config)) throw new CaseError("参数已改变，旧结果不可导出。");
      validateResult(state.result, config);
      const text = JSON.stringify(state.result, null, 2);
      const blob = new Blob([text], { type: "application/json;charset=utf-8" });
      if (blob.size > LIMIT_BYTES) throw new CaseError("结果超过 16 MiB 导入上限，导出未执行。");
      url = URL.createObjectURL(blob);
      link = document.createElement("a");
      link.href = url;
      link.download = "opensim-fluid-v1-result.json";
      link.hidden = true;
      document.body.append(link);
      link.click();
      clearError();
      setStatus("已请求浏览器下载当前完整结果 JSON；是否写入磁盘请检查浏览器下载记录。");
    } catch (error) { reportError(error); }
    finally {
      if (link) link.remove();
      if (url) window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  }
  function toggle(element, defaultValue) {
    const checkbox = element.type === "checkbox";
    const initial = element.getAttribute("aria-pressed");
    const control = { enabled: checkbox ? element.checked : initial === null ? defaultValue : initial === "true" };
    const apply = () => {
      element.setAttribute("aria-pressed", String(control.enabled));
      element.dataset.active = String(control.enabled);
    };
    apply();
    element.addEventListener(checkbox ? "change" : "click", (event) => {
      if (!checkbox) event.preventDefault();
      control.enabled = checkbox ? element.checked : !control.enabled;
      apply();
      redraw();
    });
    return control;
  }
  function requireNode(id) {
    const element = document.getElementById(id);
    if (!element) throw new CaseError(`页面缺少必需控件 #${id}，交互未启动。`);
    return element;
  }
  function start() {
    try {
      ui = {
        workspace: requireNode("workspace"), form: requireNode("case-form"),
        status: requireNode("status"), error: requireNode("error"),
        solveButton: requireNode("solve-button"), resetButton: requireNode("reset-button"),
        exportButton: requireNode("export-button"), importInput: requireNode("import-input"),
        canvas: requireNode("flow-canvas"), description: requireNode("plot-description"),
        summary: requireNode("result-summary"), convergence: requireNode("convergence"),
        fields: Object.fromEntries(KEYS.map((key) => [key, requireNode(key)])),
        metrics: Object.fromEntries(Object.keys(METRICS).map((key) => [key, requireNode(`metric-${key}`)])),
      };
      ui.status.setAttribute("role", "status"); ui.status.setAttribute("aria-live", "polite");
      ui.error.setAttribute("role", "alert"); ui.error.setAttribute("tabindex", "-1");
      ui.exportButton.disabled = true;
      ui.context = ui.canvas.getContext("2d");
      if (!ui.context) throw new CaseError("浏览器无法创建二维画布，交互未启动。");
      ui.geometryNote = document.getElementById("geometry-note");
      if (!ui.geometryNote) {
        ui.geometryNote = document.createElement("p");
        ui.geometryNote.id = "geometry-note";
        ui.geometryNote.className = "field-hint";
        ui.fields.height_mm.insertAdjacentElement("afterend", ui.geometryNote);
      }
      ui.geometryNote.hidden = false;
      ui.geometryNote.setAttribute("aria-live", "polite");
      for (const key of ["geometry", "width_mm", "height_mm"]) {
        const described = ui.fields[key].getAttribute("aria-describedby") || "";
        ui.fields[key].setAttribute("aria-describedby", `${described} geometry-note`.trim());
      }
      ui.lockable = [...Object.values(ui.fields), ui.solveButton, ui.resetButton, ui.importInput]
        .map((element) => [element, element.disabled]);
      ui.fieldToggle = toggle(requireNode("view-field"), true);
      ui.meshToggle = toggle(requireNode("view-mesh"), false);
      ui.form.noValidate = true;
      syncGeometry();
      defaults = readConfig();
      for (const field of Object.values(ui.fields)) {
        field.addEventListener("input", changed);
        field.addEventListener("change", changed);
      }
      ui.form.addEventListener("submit", (event) => { event.preventDefault(); void solve(); });
      ui.form.addEventListener("reset", (event) => { void reset(event); });
      if (ui.resetButton.type !== "reset") ui.resetButton.addEventListener("click", (event) => { void reset(event); });
      ui.exportButton.addEventListener("click", (event) => { event.preventDefault(); exportCase(); });
      ui.importInput.addEventListener("change", () => { void importCase(); });
      // Independent evidence belongs to the generated page, not to a single solve.
      if (!ui.convergence.textContent.trim()) ui.convergence.textContent = "未采集";
      if (typeof ResizeObserver !== "function") throw new CaseError("浏览器不支持画布尺寸观察，请使用现代浏览器。");
      observer = new ResizeObserver(redraw);
      observer.observe(ui.canvas);
      window.addEventListener("resize", redraw);
      redraw();
      void solve();
    } catch (error) {
      console.error("OpenSim initialization:", error);
      const box = document.getElementById("error") || document.createElement("p");
      if (!box.parentNode) document.body.append(box);
      box.textContent = error instanceof Error ? error.message : "页面初始化失败。";
      box.hidden = false; box.setAttribute("role", "alert"); box.setAttribute("tabindex", "-1"); box.focus();
      const status = document.getElementById("status");
      if (status) { status.textContent = "初始化失败，未启动求解。"; status.dataset.state = "failed"; }
      const button = document.getElementById("export-button");
      if (button) button.disabled = true;
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
