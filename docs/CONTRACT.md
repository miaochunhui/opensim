# 流体工作台 v1 契约

## 物理边界

充分发展直管流，轴向速度 `u(x,y)` 满足 `-mu * Laplacian(u) = dp / L`，
横截面壁面无滑移 `u=0`。忽略入口、弯管、湍流、瞬态、可压缩及多物理耦合。
压力沿轴向线性变化，是模型假设，不是独立计算的压力场。
有限元弱式：`integral(mu grad(u) dot grad(v)) = integral((dp/L) v)`。
P1 三角形单元，Dirichlet 消元，稀疏求解。

## Python 与 HTTP

`solver.py` 公开 `solve_case(config: dict) -> dict`、
`validate_config(config: dict) -> dict`、
`InputError(ValueError)` 和 `SolverError(RuntimeError)`。
输入完整，不静默补字段；仅接受下列键，有限实数且拒绝 bool：

```json
{
  "geometry": "circle",
  "width_mm": 4.0,
  "height_mm": 4.0,
  "length_mm": 100.0,
  "pressure_pa": 1.0,
  "viscosity_pa_s": 0.001,
  "density_kg_m3": 998.0,
  "resolution": 16
}
```

geometry 为 `circle|rectangle`；circle 的 width 是直径、height 必须等于 width。
width/height 为 0.1..100 mm，长为 1..10000 mm，压差为 0.0001..100000 Pa，
黏度为 0.00001..10 Pa s，密度为 0.1..20000 kg/m3，
resolution 为整数 8..48。拒绝雷诺数超过 2000 的配置。
rect 宽高比限制为 0.1..10。

结果具有以下固定键，所有值必须有限，JSON 禁止 NaN：

```text
schema_version = 1
config = 规范化输入
model = {name, discretization, assumptions: [str]}
mesh = {nodes_mm: [[x,y]], triangles: [[i,j,k]], boundary_nodes: [i]}
fields = {velocity_m_s: [float]}
metrics = {node_count, element_count, dof_count, max_velocity_m_s,
 mean_velocity_m_s, flow_rate_m3_s, reynolds, hydraulic_diameter_mm,
 relative_residual, relative_l2_error, reference_flow_rate_m3_s,
 relative_flow_error, force_balance_relative_error}
reference = {velocity_m_s: [float], method: str}
warnings = [str]
```

`tools/validate_numerics.py` 独占数值报告生成，运行后写入指定外置 `--out`。
HTTP `GET /api/health` 返回 `{status:"ok",schema_version:1}`。
`POST /api/solve` 只收以上 config，不接文件、任意表达式或执行指令；
成功返回 solver 结果，失败返回 `{error:{code,message,field?}}`，400/422/503/500
区分坏 JSON、非法配置、忙和内部错误。单次计算有界，浏览器无共享文档。
GET `/` 及 `/index-v1.html` 由 Python 生成；assets 只读允许目录。

## DOM 契约

唯一 main `#workspace`，唯一 h1。`#case-form` 内所有输入使用上述字段名作为
id/name；`#solve-button` 提交；`#reset-button` 重置；
`#status` role=status、aria-live；`#error` role=alert、tabindex=-1。
`#flow-canvas` 为 canvas，邻近 `#plot-description` 提供文本摘要。
`#view-field`、`#view-mesh` 切换显示。
`#export-button` 导出完整结果 JSON，未成功求解时禁用；
`#import-input` 导入之前导出的 config/结果 JSON，但必须重新计算。
指标节点 id 为 `metric-` + metrics 原始键，未算显示“未计算”。
`#result-summary` 用于完整文字说明；`#convergence` 显示独立验证报告，
未提供证据时显示“未采集”，不造预填测试数字。
`#version-menu` selected v1，noscript 提供说明。
`#edited-at` 从源文件修改时间生成精确秒级 +08:00。

JS 相对请求 `api/solve`，不依赖 CDN。处理脏参数与旧结果，改输入后标记
“参数已改变，需重新计算”，导出禁止把旧结果当当前配置。
载入时可自动实际求解默认圆管；表单为持续可见标签、44px 控件。

## 八席写集

1. 内核：`solver.py`。
2. 数值：`tests/test_solver.py`、`tools/validate_numerics.py`。
3. 结构：`tools/render_page.py`。
4. 交互：`web/app.js`。
5. 视觉：`web/app.css`。
6. 浏览器：`tools/browser_check.py` 或 `tools/browser_check.mjs`。
7. 发布：只读 infra 与现网，新增 `docs/DEPLOYMENT.md`，不实际变更网络或服务。
8. 协作：`docs/INTEGRATION.md`、`docs/ROADMAP.md`。

主席独占 server、HTTP 测试、项目入口、整合及交付记录。各席不得修改本契约、
AGENTS、他席文件、凭据、共享 infra 或生产对象。任务验收详见各席派单。
