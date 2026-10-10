"""Generate the Chinese OpenSim application; never edit its HTML output.

Server: render_page(evidence=Path(...) | None) -> str.
CLI: python3 -B tools/render_page.py --out DIR [--evidence numerics.json].
Checks: add --selftest to verify structure, source time and repeatable bytes.
Only the standard library is needed. Chinese literals are user-facing UI text.
CSS owns sizing (including 44px targets); JS owns geometry and state changes.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import sys


VERSION = 1
PAGE_NAME = f"index-v{VERSION}.html"
LOCAL_ZONE = timezone(timedelta(hours=8))
MAX_EVIDENCE_BYTES = 1_048_576
SOURCE_PATHS = ("tools/render_page.py", "web/app.js", "web/app.css",
                "solver.py", "server.py")
METRICS = (
    ("max_velocity_m_s", "最大轴向速度", "m/s"),
    ("mean_velocity_m_s", "平均轴向速度", "m/s"),
    ("flow_rate_m3_s", "体积流量", "m³/s"),
    ("reynolds", "雷诺数", "无量纲"),
    ("hydraulic_diameter_mm", "水力直径", "mm"),
    ("node_count", "网格节点", "个"),
    ("element_count", "三角形单元", "个"),
    ("dof_count", "求解自由度", "个"),
    ("relative_residual", "线性系统相对残差", "无量纲"),
    ("relative_l2_error", "速度相对 L2 误差", "无量纲"),
    ("reference_flow_rate_m3_s", "独立参考流量", "m³/s"),
    ("relative_flow_error", "流量相对误差", "无量纲"),
    ("force_balance_relative_error", "力平衡相对误差", "无量纲"),
)
NUMERIC_FIELDS = (
    ("width_mm", "宽度 / 圆管直径", "mm", "4", "0.1", "100", "any",
     "圆管取直径；矩形管取宽度。允许 0.1 至 100 mm。"),
    ("height_mm", "截面高度", "mm", "4", "0.1", "100", "any",
     "圆管高度必须与直径相等；矩形管可独立调整。"),
    ("length_mm", "直管长度", "mm", "100", "1", "10000", "any",
     "允许 1 至 10000 mm；不模拟入口发展段。"),
    ("pressure_pa", "两端压差", "Pa", "1", "0.0001", "100000", "any",
     "正压差驱动轴向流动；允许 0.0001 至 100000 Pa。"),
    ("viscosity_pa_s", "动力黏度", "Pa·s", "0.001", "0.00001", "10", "any",
     "牛顿流体的动力黏度；允许 0.00001 至 10 Pa·s。"),
    ("density_kg_m3", "流体密度", "kg/m³", "998", "0.1", "20000", "any",
     "用于雷诺数判定；允许 0.1 至 20000 kg/m³。"),
    ("resolution", "网格分辨率", "级", "16", "8", "48", "1",
     "取 8 至 48 的整数；分辨率增加会提高求解成本。"),
)


def _source_metadata() -> tuple[str, str, str]:
    root = Path(__file__).resolve().parent.parent
    modified_times = []
    fingerprint = sha256()
    for relative in SOURCE_PATHS:
        source = root / relative
        modified = source.stat().st_mtime_ns
        modified_times.append(modified)
        fingerprint.update(relative.encode("ascii") + b"\0" + source.read_bytes() + b"\0")
    if not modified_times:
        raise FileNotFoundError("no application sources found for page metadata")
    edited = datetime.fromtimestamp(max(modified_times) // 1_000_000_000, LOCAL_ZONE)
    return (edited.isoformat(timespec="seconds"),
            edited.strftime("%Y-%m-%d %H:%M:%S +08:00"),
            fingerprint.hexdigest()[:12])


def asset_paths() -> dict[str, str]:
    """Content-addressed relative URLs; legacy aliases remain server routes."""
    root = Path(__file__).resolve().parent.parent
    return {extension: f"web/app.{sha256((root / f'web/app.{extension}').read_bytes()).hexdigest()}.{extension}"
            for extension in ("js", "css")}


def _reject_constant(value: str) -> None:
    raise ValueError(f"numerics.json contains non-finite constant: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"numerics.json contains duplicate key: {key}")
        result[key] = value
    return result


def _check_finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("numerics.json contains a non-finite number")
    if isinstance(value, dict):
        for item in value.values():
            _check_finite(item)
    elif isinstance(value, list):
        for item in value:
            _check_finite(item)


def _read_evidence(evidence: Path | None) -> tuple[dict, str] | None:
    if evidence is None:
        return None
    source = Path(evidence)
    with source.open("rb") as stream:
        content = stream.read(MAX_EVIDENCE_BYTES + 1)
    if len(content) > MAX_EVIDENCE_BYTES:
        raise ValueError(f"numerical evidence exceeds {MAX_EVIDENCE_BYTES} bytes")
    report = json.loads(content.decode("utf-8"), parse_constant=_reject_constant,
                        object_pairs_hook=_unique_object)
    if not isinstance(report, dict) or not report:
        raise ValueError("numerical evidence must be a non-empty JSON object")
    _check_finite(report)
    return report, sha256(content).hexdigest()


def _preview(value: object, depth: int = 0) -> object:
    """Keep report previews bounded; the external JSON remains authoritative."""
    if isinstance(value, (dict, list)) and depth >= 4:
        return f"共 {len(value)} 项；完整内容见原始报告"
    if isinstance(value, dict):
        items = sorted(value.items())
        limit = 16 if depth == 0 else 3
        result = {key: _preview(item, depth + 1) for key, item in items[:limit]}
        if len(items) > limit:
            result["其余字段"] = f"另有 {len(items) - limit} 项；见原始报告"
        return result
    if isinstance(value, list):
        result = [_preview(item, depth + 1) for item in value[:3]]
        if len(value) > 3:
            result.append(f"另有 {len(value) - 3} 项；见原始报告")
        return result
    if isinstance(value, str) and len(value) > 240:
        return value[:240] + "（预览截取；完整内容见原始报告）"
    return value


def _evidence_html(evidence: Path | None) -> str:
    loaded = _read_evidence(evidence)
    if loaded is None:
        return """<p>未采集</p>
          <p>尚未提供独立数值验证报告。一次成功求解不等于已通过网格收敛验证。</p>"""
    report, digest = loaded
    convergence_html = ""
    groups = report.get("convergence")
    if report.get("verdict") != "PASS" or report.get("state") != "COMPLETE":
        raise ValueError("numerical evidence must be a completed passing report")
    if not isinstance(groups, dict) or set(groups) != {"circle", "rectangle", "square"}:
        raise ValueError("numerical evidence requires all three convergence groups")
    if type(report.get("checks_executed")) is not int or report["checks_executed"] <= 0:
        raise ValueError("numerical evidence requires a positive check count")
    if any(not isinstance(report.get(key), list) or not report[key]
           for key in ("cases", "rejections")):
        raise ValueError("numerical evidence requires nonempty cases and rejections")
    if groups:
        expected_digest = sha256((Path(__file__).resolve().parents[1] / "solver.py").read_bytes()).hexdigest()
        if report.get("solver", {}).get("sha256") != expected_digest:
            raise ValueError("numerical report does not match the current solver source")
        names = {"circle": "圆管", "rectangle": "矩形管", "square": "方管"}
        colors = ("#176b60", "#b93722", "#3d5e8e")
        rows, series, all_errors = [], [], []
        for index, (key, record) in enumerate(sorted(groups.items())):
            grids = record["resolutions"]
            errors = record["relative_flow_error"]["errors"]
            if record.get("verdict") != "PASS" or len(grids) != 3 or len(errors) != 3:
                raise ValueError("convergence record must contain three verified mesh levels")
            if any(type(grid) is not int for grid in grids) or grids != [8, 16, 32]:
                raise ValueError("invalid convergence resolutions")
            if any(isinstance(value, bool) or not isinstance(value, (float, int)) or value <= 0 for value in errors):
                raise ValueError("invalid positive convergence errors")
            if not all(left > right for left, right in zip(errors, errors[1:])):
                raise ValueError("convergence errors must decrease with mesh refinement")
            label = names.get(key, key)
            text = " → ".join(f"{value * 100:.3f}%" for value in errors)
            rows.append(
                f"<tr><th scope=\"row\">{escape(label)}</th>"
                f"<td>{escape(' / '.join(map(str, grids)))}</td><td>{text}</td></tr>"
            )
            series.append((label, colors[index % len(colors)], errors))
            all_errors.extend(errors)
        minimum, maximum = math.log10(min(all_errors)), math.log10(max(all_errors))
        span = max(maximum - minimum, 0.1)
        curves = []
        for index, (label, color, errors) in enumerate(series):
            points = [(72 + i * 188, 206 - (math.log10(error) - minimum) / span * 158)
                      for i, error in enumerate(errors)]
            polyline = " ".join(f"{x:.3f},{y:.3f}" for x, y in points)
            circles = "".join(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="5" fill="{color}"/>'
                              for x, y in points)
            curves.append(f'<polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="3"/>'
                          f'{circles}<text x="490" y="{72 + index * 36}" fill="{color}">{escape(label)}</text>')
        levels = " / ".join(map(str, next(iter(groups.values()))["resolutions"]))
        convergence_html = f"""<p>独立验收：{escape(report["verdict"])}；
          {int(report["checks_executed"])} 项检查，{len(report["cases"])} 个算例，
          {len(report["rejections"])} 个非法输入被拒绝。
          采集时间：<time>{escape(report["collected_at"])}</time>。
          这些固定算例通过检查，不代表当前任意参数或范围外流动均已验证。</p>
          <figure class="convergence-figure">
            <svg viewBox="0 0 620 270" role="img" aria-labelledby="convergence-chart-title">
              <title id="convergence-chart-title">网格加密后，圆管、矩形管和方管的流量相对误差下降</title>
              <path d="M72 38V220H454" fill="none" stroke="#52615d"/>
              <text x="72" y="26" fill="#202e2d">流量相对误差（对数尺度，越低越好）</text>
              <text x="66" y="250" fill="#202e2d">粗网格</text>
              <text x="422" y="250" fill="#202e2d">细网格</text>
              {''.join(curves)}
            </svg>
            <figcaption>分辨率 {levels}；每条线都取自独立报告，同一数据生成下方误差表。</figcaption>
          </figure>
          <div class="evidence-table" data-local-scroll tabindex="0" aria-label="网格收敛误差表">
            <table><caption>三种截面的流量误差</caption>
              <thead><tr><th scope="col">截面</th><th scope="col">网格分辨率</th>
              <th scope="col">相对流量误差（从粗到细）</th></tr></thead>
              <tbody>{''.join(rows)}</tbody></table>
          </div>"""
    preview = escape(json.dumps(_preview(report), ensure_ascii=False,
                                sort_keys=True, indent=2, allow_nan=False))
    source = escape(str(evidence))
    return f"""<p>已载入独立报告；报告中的判定原样展示，不代表当前参数已获验证。</p>
          {convergence_html}
          <p>来源：<code>{source}</code></p>
          <p>报告 SHA-256：<code>{digest}</code></p>
          <details class="evidence-report">
            <summary>查看数值报告预览</summary>
            <p>各集合最多展示三项，长文本和深层数据仅作预览；完整证据以来源文件为准。</p>
            <pre tabindex="0" aria-label="独立数值报告预览"><code>{preview}</code></pre>
          </details>"""


def _numeric_fields() -> str:
    return "\n".join(
        f"""<div class="field">
                <label for="{key}">{label}（{unit}）</label>
                <input id="{key}" name="{key}" type="number"
                       value="{default}" min="{low}" max="{high}" step="{step}"
                       inputmode="{"numeric" if key == "resolution" else "decimal"}"
                       aria-describedby="{key}-hint" required>
                <p id="{key}-hint" class="field-hint">{hint}</p>
              </div>"""
        for key, label, unit, default, low, high, step, hint in NUMERIC_FIELDS
    )


def _metric_cards() -> str:
    return "\n".join(
        f"""<div class="metric-card">
            <dt>{label} <span class="metric-unit">{unit}</span></dt>
            <dd id="metric-{key}">未计算</dd>
          </div>"""
        for key, label, unit in METRICS
    )


def render_page(evidence: Path | None = None) -> str:
    """Return complete UTF-8-ready HTML, deterministic for unchanged sources.

    None displays 未采集. Explicit missing, unreadable, malformed, duplicate-key,
    empty or non-finite evidence raises rather than claiming verification.
    Editing time is the maximum mtime of all required application sources.
    """
    edited_iso, edited_text, fingerprint = _source_metadata()
    resources = asset_paths()
    evidence_html = _evidence_html(evidence)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="OpenSim 中文流体有限元工作台：圆管与矩形直管的充分发展稳态层流横截面求解，不是通用 CFD。">
  <title>OpenSim · 直管流体有限元工作台 v{VERSION}</title>
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%23164e63'/%3E%3Cpath d='M5 12Q11 6 17 12T29 12M5 20Q11 14 17 20T29 20' fill='none' stroke='%23ffffff' stroke-width='3'/%3E%3C/svg%3E">
  <link rel="stylesheet" href="{resources['css']}">
  <script src="{resources['js']}" defer></script>
</head>
<body data-page-profile="data-canvas" data-page-shell="body"
      data-main-region="#workspace" data-secondary-region=".parameter-panel">
  <a class="skip-link" href="#workspace">跳到主内容</a>
  <header class="app-header">
    <a class="brand" href="index-v{VERSION}.html" aria-current="page"
       aria-label="OpenSim 当前工作台">OpenSim</a>
    <div class="header-meta">
      <span>流体有限元 / 直管横截面</span>
      <div class="version-control">
        <label for="version-menu">工作台版本</label>
        <select id="version-menu" name="version-menu">
          <option value="v{VERSION}" selected>v{VERSION} · 直管层流</option>
        </select>
      </div>
    </div>
  </header>

  <main id="workspace" tabindex="-1" aria-labelledby="page-title">
    <section class="intro" aria-labelledby="page-title">
      <p class="eyebrow">从参数，到可检查的流场</p>
      <h1 id="page-title">看清直管中的流动</h1>
      <p>设置截面、压差和流体属性，实际求解轴向速度，再检查网格、误差与模型假设。
         圆管与矩形管采用真实有限元（FEM）：把截面划成三角形并求解方程，不是通用 CFD 流动模拟器。</p>
    </section>

    <noscript>
      <p role="alert">当前浏览器未启用 JavaScript。仍可阅读参数与模型说明，但无法求解、切换视图或导入导出。
         请启用 JavaScript 后重新载入；本页只有 v{VERSION} 一个版本。</p>
    </noscript>

    <div class="workspace-grid">
      <section class="parameter-panel" aria-labelledby="parameters-title">
        <div class="panel-heading">
          <p class="eyebrow">01 / 定义算例</p>
          <h2 id="parameters-title">输入参数</h2>
          <p>所有参数都参与模型配置。结果只对应最近一次成功计算的参数。</p>
        </div>
        <form id="case-form" autocomplete="off">
          <fieldset class="form-grid">
            <legend>几何、驱动与流体属性</legend>
            <div class="field field-wide">
              <label for="geometry">截面形状</label>
              <select id="geometry" name="geometry" aria-describedby="geometry-hint" required>
                <option value="circle" selected>圆管</option>
                <option value="rectangle">矩形管</option>
              </select>
              <p id="geometry-hint" class="field-hint">圆管宽高相等；矩形管宽高比须在 0.1 至 10 之间。</p>
            </div>
            {_numeric_fields()}
          </fieldset>
          <p class="field-hint">雷诺数表示惯性与黏性作用的相对强弱；超过 2000 的配置不在本版范围内，会被拒绝。</p>
          <div class="buttons">
            <button id="solve-button" class="primary-button" type="submit">计算流场</button>
            <button id="reset-button" class="secondary-button" type="reset">恢复默认参数</button>
          </div>
        </form>
        <div class="field field-wide">
          <label for="import-input">导入算例 JSON</label>
          <input id="import-input" type="file" accept=".json,application/json"
                 aria-describedby="import-hint">
          <p id="import-hint" class="field-hint">可导入已导出的配置或完整结果；导入后必须重新计算，不沿用旧结果。</p>
        </div>
      </section>

      <section class="canvas-panel" aria-labelledby="canvas-title">
        <div class="canvas-toolbar">
          <div class="panel-heading">
            <p class="eyebrow">02 / 检查横截面</p>
            <h2 id="canvas-title">轴向速度场</h2>
          </div>
          <div class="view-switch" role="group" aria-label="画布显示方式">
            <button id="view-field" class="secondary-button" type="button"
                    aria-pressed="true" aria-controls="flow-canvas">速度场</button>
            <button id="view-mesh" class="secondary-button" type="button"
                    aria-pressed="false" aria-controls="flow-canvas">有限元网格</button>
          </div>
        </div>
        <p id="status" role="status" aria-live="polite" aria-atomic="true">尚未计算。请检查参数后计算流场。</p>
        <p id="error" role="alert" tabindex="-1" aria-atomic="true" hidden></p>
        <figure>
          <div class="canvas-stage">
            <canvas id="flow-canvas" width="960" height="640" role="img"
                    aria-label="直管横截面轴向速度或有限元网格"
                    aria-describedby="plot-description">
              当前尚未计算。求解后可在下方文字摘要中检查结果。
            </canvas>
          </div>
          <figcaption id="plot-description">未计算：画布尚无求解数据。显示的是横截面上的轴向速度，不是管内三维流线。</figcaption>
        </figure>
        <div class="color-legend" aria-label="速度颜色图例">
          <span>低轴向速度</span><span>颜色仅在实际求解后对应数值</span><span>高轴向速度</span>
        </div>
      </section>
    </div>

    <section class="results-panel" aria-labelledby="results-title">
      <div class="panel-heading">
        <p class="eyebrow">03 / 读取结果</p>
        <h2 id="results-title">计算结果与质量检查</h2>
        <p>指标来自实际求解；未计算时不预填数字。残差检查方程是否求解到位，参考误差检查与独立解的差异。</p>
      </div>
      <dl class="metric-grid">
        {_metric_cards()}
      </dl>
      <div id="result-summary" aria-label="计算结果文字说明">
        <p>未计算。成功求解后，这里将说明当前算例、结果、误差和警告；修改参数后需要重新计算。</p>
      </div>
      <div class="buttons">
        <button id="export-button" class="secondary-button" type="button" disabled
                aria-describedby="export-hint">导出完整结果 JSON</button>
        <p id="export-hint" class="field-hint">成功计算且参数未改变时才可导出，包含配置、网格、速度、指标和模型说明。</p>
      </div>
    </section>

    <section class="evidence-panel" aria-labelledby="evidence-title">
      <div class="panel-heading">
        <p class="eyebrow">04 / 独立证据</p>
        <h2 id="evidence-title">数值验证与网格收敛</h2>
        <p>网格收敛检查加密网格后结果是否稳定；本区只读取独立验证工具生成的报告，不由页面编造测试结论。</p>
      </div>
      <div id="convergence" aria-label="独立数值验证报告">
        {evidence_html}
      </div>
    </section>

    <section class="model-panel" aria-labelledby="model-title">
      <div class="panel-heading">
        <p class="eyebrow">05 / 理解边界</p>
        <h2 id="model-title">这个模型能算什么</h2>
        <p>适用于圆管或矩形直管内充分发展的、不可压缩牛顿流体稳态层流。
           “充分发展”表示沿管长方向速度分布不再改变；计算的是截面的轴向速度。</p>
      </div>
      <h3>真实有限元，不是公式填图</h3>
      <p>截面采用 P1 线性三角形单元：每个三角形内的速度线性变化。
         对黏性方程建立弱式，消去壁面固定自由度，再求解稀疏线性系统。壁面无滑移，轴向速度为零。</p>
      <p class="model-equation"><code>-μ ∇²u = Δp / L</code>，壁面 <code>u = 0</code>。</p>
      <p>这里 μ 是动力黏度，u 是轴向速度，Δp 是两端压差，L 是管长。
         压力沿轴向线性变化是模型假设，不是另外计算出来的压力场。</p>
      <h3>明确不覆盖的情况</h3>
      <ul>
        <li>不模拟入口发展段、弯管、复杂三维几何或一般外流问题。</li>
        <li>不模拟湍流、瞬态、可压缩流动、非牛顿流体或多物理耦合。</li>
        <li>不把解析参考解、有限体积或插值动画冒称有限元求解；参考值只用于独立误差对照。</li>
      </ul>
      <p>它是范围明确的圆管 / 矩形管真实 FEM 工作台，不是通用计算流体力学（CFD）软件，不能代替范围外的工程分析。</p>
    </section>
  </main>

  <footer class="footer">
    <p>OpenSim · v{VERSION} · 源码版本 <code>{fingerprint}</code> · 直管横截面有限元</p>
    <p>最后修改：<time id="edited-at" datetime="{edited_iso}">{edited_text}</time></p>
    <p>编辑时间取自生成器、交互、样式、求解器和服务源码中已存在文件的最新修改时间，不是访问或重新生成的时间。</p>
  </footer>
</body>
</html>
"""


class _StructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict, tuple[str, ...]]] = []
        self.text: dict[str, list[str]] = {}
        self.stack: list[tuple[str, str | None]] = []
        self.doctypes: list[str] = []

    def handle_decl(self, decl: str) -> None:
        self.doctypes.append(decl.lower())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        ancestors = tuple(key for _, key in self.stack if key)
        self.elements.append((tag, attributes, ancestors))
        if tag not in {"meta", "link", "input", "br", "hr", "img"}:
            self.stack.append((tag, attributes.get("id")))

    def handle_endtag(self, tag: str) -> None:
        if not self.stack or self.stack[-1][0] != tag:
            raise ValueError(f"unbalanced HTML tag: {tag}")
        self.stack.pop()

    def handle_data(self, data: str) -> None:
        for _, key in self.stack:
            if key:
                self.text.setdefault(key, []).append(data)


def check_page(page: str) -> dict:
    """Static DOM acceptance; browser sizing and interaction are separate gates."""
    parser = _StructureParser()
    parser.feed(page)
    parser.close()
    checks = 0

    def require(condition: bool, message: str) -> None:
        nonlocal checks
        checks += 1
        if not condition:
            raise ValueError(f"page contract failed: {message}")

    elements = parser.elements
    ids = Counter(attrs["id"] for _, attrs, _ in elements if "id" in attrs)
    by_id = {attrs["id"]: (tag, attrs, ancestors)
             for tag, attrs, ancestors in elements if "id" in attrs}
    require(not parser.stack and parser.doctypes == ["doctype html"], "complete document")
    require(all(count == 1 for count in ids.values()), "unique IDs")
    require(sum(tag == "main" for tag, _, _ in elements) == 1, "unique main")
    require(sum(tag == "h1" for tag, _, _ in elements) == 1, "unique h1")
    require(by_id.get("workspace", (None,))[0] == "main", "main #workspace")
    require(any(tag == "a" and attrs.get("href") == "#workspace"
                for tag, attrs, _ in elements), "skip link reaches main")
    require(any(tag == "html" and attrs.get("lang") == "zh-CN"
                for tag, attrs, _ in elements), "Chinese language")
    viewports = [attrs for tag, attrs, _ in elements
                 if tag == "meta" and attrs.get("name") == "viewport"]
    require(len(viewports) == 1 and viewports[0].get("content") ==
            "width=device-width, initial-scale=1", "exact viewport")
    require(any(tag == "body" and attrs.get("data-page-profile") == "data-canvas"
                for tag, attrs, _ in elements), "page profile")
    labels = {attrs.get("for") for tag, attrs, _ in elements if tag == "label"}
    resources = asset_paths()
    for tag, attrs, _ in elements:
        if tag in {"input", "select"}:
            require(attrs.get("id") in labels, "persistent input label")
        require(not any(key.startswith("on") for key in attrs), "no inline events")
        require("style" not in attrs and tag != "style", "no inline styles")
        if tag == "script":
            require(attrs.get("src") == resources["js"] and "defer" in attrs,
                    "only deferred content-fingerprinted relative script")
    require(sum(tag == "script" for tag, _, _ in elements) == 1, "single external script")
    require(any(tag == "link" and attrs.get("href") == resources["css"]
                for tag, attrs, _ in elements), "content-fingerprinted relative stylesheet")
    require(any(tag == "fieldset" for tag, _, _ in elements) and
            any(tag == "legend" for tag, _, _ in elements), "fieldset and legend")
    require(any(tag == "noscript" for tag, _, _ in elements), "noscript fallback")
    geometry = by_id.get("geometry")
    require(geometry is not None and geometry[1].get("name") == "geometry" and
            "case-form" in geometry[2], "geometry config field")
    defaults = [(attrs.get("value"), "selected" in attrs) for tag, attrs, ancestors
                in elements if tag == "option" and "geometry" in ancestors]
    require(defaults == [("circle", True), ("rectangle", False)], "geometry defaults")
    for key, _, _, value, low, high, step, _ in NUMERIC_FIELDS:
        item = by_id.get(key)
        require(item is not None and item[0] == "input" and "case-form" in item[2],
                f"form field {key}")
        attrs = item[1]
        require(attrs.get("name") == key and attrs.get("value") == value and
                attrs.get("min") == low and attrs.get("max") == high and
                attrs.get("step") == step and "required" in attrs,
                f"complete default and limits for {key}")
    for key, role in (("status", "status"), ("error", "alert")):
        require(key in by_id and by_id[key][1].get("role") == role, f"{key} role")
    require(by_id["status"][1].get("aria-live") == "polite", "live status")
    require(by_id["error"][1].get("tabindex") == "-1", "focusable error")
    for key, tag in (("case-form", "form"), ("flow-canvas", "canvas"),
                     ("solve-button", "button"), ("reset-button", "button"),
                     ("view-field", "button"), ("view-mesh", "button"),
                     ("export-button", "button"), ("import-input", "input")):
        require(key in by_id and by_id[key][0] == tag, f"DOM node {key}")
    require(by_id["solve-button"][1].get("type") == "submit", "submit action")
    require(by_id["reset-button"][1].get("type") == "reset", "reset action")
    require("disabled" in by_id["export-button"][1], "export initially disabled")
    for key in ("plot-description", "result-summary", "convergence"):
        require(bool("".join(parser.text.get(key, [])).strip()), f"nonempty {key}")
    for key, _, _ in METRICS:
        require("".join(parser.text.get(f"metric-{key}", [])).strip() == "未计算",
                f"no fake metric {key}")
    options = [attrs for tag, attrs, ancestors in elements
               if tag == "option" and "version-menu" in ancestors]
    require(len(options) == 1 and options[0].get("value") == "v1" and
            "selected" in options[0], "single usable v1 version")
    iso, display, fingerprint = _source_metadata()
    require(by_id["edited-at"][1].get("datetime") == iso and
            "".join(parser.text.get("edited-at", [])).strip() == display,
            "maximum application source mtime to exact seconds +08:00")
    require(fingerprint in page, "source fingerprint")
    require("通用 CFD" in page and "不是另外计算出来的压力场" in page,
            "honest model boundary")
    return {"checks": checks, "id_count": len(ids), "metric_count": len(METRICS),
            "edited_at": iso, "source_sha256_prefix": fingerprint}


def _selftest(out: Path, evidence: Path | None) -> dict:
    original_source = Path(__file__).read_bytes()
    compile(original_source, str(Path(__file__)), "exec")
    first = render_page(evidence=evidence)
    second = render_page(evidence=evidence)
    if first.encode("utf-8") != second.encode("utf-8"):
        raise ValueError("same-source HTML is not byte-identical")
    report = check_page(first)
    for broken in (first.replace("id=\"status\"", "id=\"error\"", 1),
                   first.replace("未计算</dd>", "0</dd>", 1)):
        try:
            check_page(broken)
        except ValueError:
            pass
        else:
            raise ValueError("structure negative control was not rejected")
    not_provided = render_page(evidence=None)
    if "未采集" not in not_provided:
        raise ValueError("None evidence incorrectly presented as collected")
    try:
        render_page(evidence=out / "not-collected.json")
    except FileNotFoundError:
        pass
    else:
        raise ValueError("explicit missing evidence did not fail loudly")
    root = Path(__file__).resolve().parent.parent
    expected_ns = max((root / relative).stat().st_mtime_ns for relative in SOURCE_PATHS)
    expected_iso = datetime.fromtimestamp(expected_ns // 1_000_000_000,
                                          LOCAL_ZONE).isoformat(timespec="seconds")
    if report["edited_at"] != expected_iso:
        raise ValueError("editing timestamp is not maximum existing source mtime")
    fixture = out / "selftest-evidence.json"
    fixture_report = {
        "verdict": "PASS", "state": "COMPLETE",
        "note": "<script>not executable</script>",
        "collected_at": "SELFTEST_ONLY_NOT_PHYSICAL_EVIDENCE",
        "checks_executed": 1, "cases": ["selftest"], "rejections": ["selftest"],
        "solver": {"sha256": sha256((root / "solver.py").read_bytes()).hexdigest()},
        "convergence": {
            name: {"verdict": "PASS", "resolutions": [8, 16, 32],
                   "relative_flow_error": {"errors": [0.04, 0.01, 0.0025]}}
            for name in ("circle", "rectangle", "square")
        },
    }
    fixture.write_text(json.dumps(fixture_report, sort_keys=True), encoding="utf-8")
    rendered = render_page(evidence=fixture)
    if "&lt;script&gt;not executable&lt;/script&gt;" not in rendered:
        raise ValueError("evidence was not HTML-escaped")
    invalid_samples = ("{}", "[]", "{", "{\"value\": NaN}",
                       "{\"value\": 1e999}", "{\"a\": 1, \"a\": 2}")
    for index, sample in enumerate(invalid_samples):
        invalid = out / f"selftest-invalid-{index}.json"
        invalid.write_text(sample, encoding="utf-8")
        try:
            render_page(evidence=invalid)
        except (ValueError, UnicodeError):
            pass
        else:
            raise ValueError(f"invalid evidence accepted: fixture {index}")
    if Path(__file__).read_bytes() != original_source:
        raise ValueError("selftest modified the generator source")
    report.update({"repeat_bytes_identical": True, "syntax": "PASS",
                   "negative_controls": 3 + len(invalid_samples),
                   "unprovided_evidence": "未采集", "explicit_missing_evidence": "FAIL_LOUD",
                   "maximum_source_mtime": "PASS", "source_paths": list(SOURCE_PATHS),
                   "evidence_escape": "PASS",
                   "html_bytes": len(first.encode("utf-8")),
                   "html_sha256": sha256(first.encode("utf-8")).hexdigest()})
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 OpenSim 中文应用页面")
    parser.add_argument("--out", type=Path, required=True, help="外置 HTML 产物目录")
    parser.add_argument("--evidence", type=Path, help="外置独立 numerics.json；指定缺失文件将报错")
    parser.add_argument("--selftest", action="store_true", help="验证结构、时间、重复字节与失败路径")
    parser.add_argument("--check", action="store_true", help="核对现有产物与当前真源；不写盘")
    args = parser.parse_args(argv)
    if args.check and args.selftest:
        parser.error("--check and --selftest cannot be combined")
    target = args.out / PAGE_NAME
    try:
        page = render_page(evidence=args.evidence)
        report = check_page(page)
        content = page.encode("utf-8")
        if args.check:
            if target.read_bytes() != content:
                raise ValueError(f"generated artifact differs from source: {target}")
        else:
            args.out.mkdir(parents=True, exist_ok=True)
            if args.selftest:
                report = _selftest(args.out, args.evidence)
            target.write_bytes(content)
            if not content or target.read_bytes() != content:
                raise ValueError(f"empty or inconsistent generated artifact: {target}")
            report.update({"verdict": "PASS", "artifact": str(target),
                           "html_bytes": len(content),
                           "html_sha256": sha256(content).hexdigest(),
                           "collected_at": datetime.now(LOCAL_ZONE).isoformat(timespec="seconds")})
            (args.out / "page-check.json").write_text(
                json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2,
                           allow_nan=False) + "\n", encoding="utf-8")
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        print(f"RENDER_PAGE verdict=FAIL target={target} cause={type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    checks = report["checks"]
    edited_at = report["edited_at"]
    print(f"RENDER_PAGE verdict=PASS artifact={target} bytes={len(content)} "
          f"checks={checks} edited_at={edited_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
