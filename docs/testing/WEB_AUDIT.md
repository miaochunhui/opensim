# Web Audit

独立 Chromium CDP 工具，不安装包、不下载浏览器、不复用主实例。
自动审计不等于真实屏幕阅读器：`WEB-A11Y-009` 必须始终单独报告。

## 集成入口

```bash
sudo -n -u infra python3 -B tests/test_browser_audit.py --product-smoke
sudo -n -u infra python3 -B tests/test_browser_audit.py --product-audit
sudo -n -u infra node tools/browser_audit.mjs --url http://127.0.0.1:PORT/ --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-audit
sudo -n -u infra python3 -B tests/test_browser_audit.py
```

第一条在指定工作树启动新 loopback 临时端口，使用外置真实数值证据，
核 solver 摘要；页面通过原有 Python renderer 生成。只停止本次服务。
Smoke 核真实求解完成、非空控件、页面语义基础和实际 320×568 PNG。
报告包含 URL、视口、源码提交、浏览器版本和失败首因；失败证据不删除。
Smoke 的 `SMOKE_PASS` 不是 G3/G6 或发布 PASS。

入口仅允许无凭据 loopback HTTP 或精确的
`https://opensim.ai4everyone.top/`，禁止其他公网、子域、任意端口及跨源跳转。
本席不访问公网，也不发布。集成者应明确选择已经授权的目标。
`--out` 必须位于上述 seat-audit 目录，产物不进入 Git。

## 自动范围

不带 `--smoke` 运行扩展审计，固定实际 CSS 视口为 320×568、
768×1024、1064×768、1440×900。每视口覆盖以下真实浏览器状态：

- 初次加载：在 CDP 请求层保持求解请求，检查 busy/live 和未计算空结果。
- 正常结果、hover、Tab 和 Shift+Tab：逐个枚举启用控件，实际键盘聚焦，
  测轮廓可见/未遮挡以及 solid CSS 文字、输入值、边界和焦点对比度。
- 错误：请求层注入带字段的 HTTP 503，核错误焦点、位置、说明与修复入口。
- 空响应：注入 HTTP 200 空 body，必须产生明确错误，不能静默当结果。
- 长内容：以实际求解响应为底，仅追加不超过 4096 字符的中文/长 URL 警告，
  核完整关键文本、无页面横溢及无 hidden/clip/line-clamp 截断。
- 每个故障解除后真实键盘重新求解，核当前结果和可导出状态恢复，不刷新。
- 真实键盘恢复默认、网格/速度场切换、证据 details 展开；逐状态自动扫描。

`WEB-A11Y-001/002` 记录实际计算颜色、alpha 合成后的 ratio 和阈值。
透明背景必须找到显式不透明祖先；sRGB 渐变从计算色标进行透明度合成，
对所有像素颜色区间求保守对比下界。下界不足不冒称确定失败，而记为
需要精确像素证据；图片、滤镜、非 sRGB 插值和未知颜色记 `NOT_TESTED`。
SVG 文本读取实际 fill；canvas 核真实非空像素、名称和邻接文字，
逐项对照实际 HTTP 结果的数值指标。收敛 SVG 核命名标题和三系列数值表，
view 状态同时有 aria-pressed 与下划线。以上替代不声称所有空间节点
已有页面数据表，不以图形颜色单独承载主任务数值。
文字链接和局部阅读滚动区不强制装饰边框，焦点仍独立测试。
原生 popup、file-selector-button 等子控件不声称全部覆盖。
`WEB-A11Y-005` 比较主要结构组 DOM 兄弟与真实矩形，并报告 CSS order；
不是任意复杂网格或双向排版的完备证明。
没有使用 AX 树冒充屏幕阅读器。

每个状态有 DOM 明细、首屏 PNG 和按实际滚动位置的全页分段截图/coverage。
恢复态也保留完整截图。截图不缩放、不改布局；底段可能重叠，不冒称
无重叠拼图。矩阵记录主/次区域真实矩形，核移动堆叠和宽屏并列。

报告含 `checks`、`first_failure`、`coverage`、`coverage_counts`、
`focus_samples`、`hover_samples`、`injections`、`matrix`、`gates`；
失败含规则 ID、URL、视口、状态、选择器、ratio 和复现步骤。
`rc=0` 仅为所列自动范围通过，`rc=1` 为审计发现失败，
`rc=2` 为缺依赖/配置/运行/证据错误。G3 仍有实质缺项时保持 PARTIAL；
G6 所列四视口状态/恢复/截图/构图全过才为范围内 PASS，否则 FAIL，
`release_verdict=NOT_CERTIFIED`。未覆盖完整标准矩阵、生产性能、
真实触屏、软键盘、桌面/移动读屏和完整对话框/菜单模型。
实际 DOM 不存在 modal/mobile-menu 时标 `NOT_APPLICABLE`，存在而未执行
才为 `NOT_TESTED`。本工具不自动签署例外，不将范围内 PASS 当发布 PASS。

身份报告含 `source_sha256`、`page_build`、`identity_checks` 和
`post_source_checks`。前后实际取 health 原始字节及 HTML/JS/CSS 字节，
核摘要相同、footer/build 一致；五份产品源码逐项比对已提交 Git blob
和本地文件，正常 served JS/CSS 字节必须等于 blob。每个正常成功 POST
运行来源必须同五源码，注入空 body 以 networkId 显式另记，不计身份通过。
夹具 HTTP 覆盖资产明确标 fixture，不计产品。当前 HEAD 改变、未提交
产品源、健康/POST身份不符或 served 字节前后漂移均 fail-loud。
主代理组合已有 browser_check 报告时应验证同源码/同 build 和真实截图、
POST产物，不按 commit 名称或旧实例端口直接组合。

## 夹具与失败

unittest 使用实际 Python renderer、app 和 solver 的临时 HTTP 服务。
正例仅在测试 HTTP 响应附加黑白对比尺；负例改变测试响应为低文字/边界/
焦点对比、移除一个焦点、逆序布局、隐藏错误和截断长内容。
代码不修改产品 JS/CSS/render/server 或标准，HTML 不手写。
夹具必须声明 `HARNESS_FIXTURE_NOT_PRODUCT`，不计产品验收。
服务健康身份需匹配指定工作树源码，数值证据需匹配 solver 摘要。
负例须实测检出相应规则和低对比焦点，不能只断言退出码。

各次运行、包括失败，保留独立目录、源码/工具摘要和日志。
2026-10-10 初次 smoke 的 Chromium 正常退出误判、关闭 details 误纳入、
文字/滚动区边框误分类、Enter 字符事件及负例焦点样本漏记已经诊断修复；
旧报告不删除、不追认为通过。跨席整合后需用新源码重跑，不能沿用旧实例。
