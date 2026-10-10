# Web Audit

独立 Chromium CDP 工具，不安装包、不下载浏览器、不复用主实例。
自动审计不等于真实屏幕阅读器：`WEB-A11Y-009` 必须始终单独报告。

## 集成入口

```bash
sudo -n -u infra python3 -B tests/test_browser_audit.py --product-smoke
sudo -n -u infra python3 -B tests/test_browser_audit.py --product-audit
sudo -n -u infra node tools/browser_audit.mjs --url http://127.0.0.1:PORT/ --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-audit
sudo -n -u infra python3 -B tests/test_browser_audit.py
sudo -n -u infra python3 -B tests/test_browser_audit.py --io-selftest
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

`WEB-INPUT-002` 按实际启用、已绘制的原生/声明角色目标与关联 label
分段矩形，计算不同激活目标之间的最短欧氏边界距离，门槛 8 CSS px。
所有测量保留在 `spacing_samples`，包含状态、视口、双目标选择器和矩形。
隐藏、禁用、屏外隐藏入口、只读滚动容器不当独立触控目标；普通链接仍测。
嵌套目标模型不明记 `NOT_TESTED`，不存在默认的“共享父容器”豁免；父级
委托命中区、原生弹窗及 pseudo 子目标不声称已认证。正负例实际测出
view 两按钮至少 8px 与 0px，不用 CSS gap 字面值代替实际浏览器矩形。

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

## 报告收尾协议

`durable-final-v2` 首先以独占创建保存 `report.provisional.json`，其
`exit_code=2`、`PROVISIONAL_NOT_ACCEPTED` 永远不能作为成功证据。
写入并回读最终候选日志，文件 fsync、日志目录 fsync 成功后，才创建
完整候选报告，再通过无覆盖 hard link 发布 `report.json` 并同步目录。
回读后独占保存 `completion.json`，记录报告与日志 SHA256 和 CLI rc，
文件/目录同步及回读完成才返回最终退出码。不支持同步或硬链接须失败，
不静默降级；候选、provisional 和旧失败全部保留。

组合者必须同时核 CLI rc、`report.completion.status=FINAL`、receipt 的
`status=COMPLETE` 及报告/日志实际字节摘要一致，不得仅接受 report rc0。
任何收尾异常均 rc2，`report.failure.json` 保存原始 errno、syscall、path、
stage、首因及撤销明细；更早浏览器错误不被后续 I/O 错误替换。
只撤本次创建的 receipt/final：有界 8MiB、O_NOFOLLOW/O_NONBLOCK 描述符
回读，前后核 dev/ino/birthtime、尺寸、mtime/ctime、命名路径及原始字节。
同 inode 改写或替换目录均原样保留，并使完成证据不成立。
POSIX 无 compare-and-unlink，以上针对唯一拥有的 run 目录与已观测漂移，
不是恶意并发写入隔离或断电/全盘丢失保证。存储连失败报告也不能保存时
stderr 输出首因与次因，不冒称已保存。

`--finalize-fixture` 仅配合明确的 loopback `--fixture --smoke` 使用。
真实负例覆盖日志路径被目录占用、`/dev/full` 的 ENOSPC 和 fsync EINVAL、
发布后 fsync 故障精确撤销、发布后未知目录保护及同 inode 同长度改写保护。
夹具修改实际文件对象，不 mock fs 方法/异常；不宽 chmod、不改产品或标准。

## 夹具与失败

unittest 使用实际 Python renderer、app 和 solver 的临时 HTTP 服务。
正例仅在测试 HTTP 响应附加黑白对比尺；负例改变测试响应为低文字/边界/
焦点对比、移除一个焦点、逆序布局、隐藏错误和截断长内容。
负例另将实际独立 view 控件间距压至 0px，必须检出 `WEB-INPUT-002`。
代码不修改产品 JS/CSS/render/server 或标准，HTML 不手写。
夹具必须声明 `HARNESS_FIXTURE_NOT_PRODUCT`，不计产品验收。
服务健康身份需匹配指定工作树源码，数值证据需匹配 solver 摘要。
负例须实测检出相应规则和低对比焦点，不能只断言退出码。

### 指纹资源夹具兼容

夹具直接使用当前 server 的 `request_path()` 与 `FINGERPRINT_PATH`，
不以旧 server 回退静默补件。只接受 legacy `web/app.js/css` alias 和
完整 SHA256 文件名；指纹必须等于该工作树的实际源码摘要及运行摘要。
未知指纹返回真实 404/NOT_FOUND，不通过宽匹配提供测试资产。
预检从真实 renderer HTML 提取唯一资源引用，逐项请求实际引用、alias、
正确指纹和错误指纹；覆盖字节、原文回读、错误响应 body 都保存并记摘要。
`fixture_assets` 是这些 HTTP 内容验收坐标，不是浏览器已执行的证明。
`fixture_requests` 另记录 Chromium 的 script/style 实际请求及
`/__audit_fixture_marker` 204，标记请求只由实际执行的附加脚本触发。
全矩阵正负尺子仍必须在实际浏览器中检出规定规则、间距、焦点和恢复状态。

最新 UI 会主动按完整 SHA256 回读页面资源。测试夹具依据
`Sec-Fetch-Dest`，在 script/style 加载时提供明确附加的对比尺/标记，
在正确指纹的 fetch 回读时提供未修改原文。false health 阴性夹具依据
`Accept`，保留 UI 的 application/json 启动读取，向审计/default 读侧
注入 solver 摘要错误。两种请求头分流只是测试夹具能力，既不是安全边界，
也不证明产品被修改后的资产具有原始源码身份。覆盖资产与原文必须分别
记录；fixture scope 绝不计入产品来源或发布通过。未修改产品的主动校验。

身份阴性必须先完成实际 smoke ready 与 PNG，再在 audit 的 before 来源
比对以 `Served health source drift solver.py` 拒绝，记录
`identity_rejection.stage=before-source-comparison-after-smoke`；不得以
marker 缺失、资源加载失败或后续 fsync 故障冒充身份拒绝首因。

2026-10-10 修前首因保留在外置
`seat-audit/fixture-positive-1791622584757645322/logs/browser.stderr.log`：
`Fixture did not attest test-only renderer output`，实际 rc2，0 checks/PNG。
同目录 server.log 显示页面请求
`/web/app.ac73a0a8fcd8347229d3d00378a89eceb3cf377b6a47ea3ed72b8b3de2f7339e.css`
与 `/web/app.bd5dfab9fb6a073fefaca896c1b4b26239b24b753e9b08a9aa69b90aab7337e3.js`
均 200，但旧夹具只覆盖 legacy alias，故 marker 未执行。这是完整
内容寻址文件名漏匹配，不是 query 参数问题。旧 FAIL 不删除、不追认。
本次专项使用独立 `/data/dev/opensim-audit-fixture-v1`、
`codex/audit-fixture-v1`，完整主仓源码基线
`1bb38a5238a31a77063fa9ea85865778b7c7599c`；未修改主树或产品源码。

路由修通后的先行正例也保留 FAIL：
`seat-audit/fixture-positive-1791622957967981906/run-1791622958113-Q0PLDs/report.json`
实际 rc1，8 条 `WEB-CONTENT-006`，均为 320/768/1064/1440 的 error/empty。
实际 canvas 有 211 色，但 description/summary/全部数值为“未计算”；
最新 UI 清除结果后会清画布并绘制“未计算：等待实际 FEM 求解”文字，
颜色数包含文字抗锯齿，不能当作当前结果证据。原 `auditGraphics` 对非 ready
且非 loading-empty 状态仍取历史成功 POST（首例 http-solve-001.json），
因此把正确清空的指标误当作当前结果缺失。不是以调整颜色阈值修复。

审计现按实际状态绑定：ready 只读取本次导航的最新实际 solve 请求对应
响应，核请求/响应/当前表单配置一致、全部数值、模型摘要与导出状态；
非 ready 只允许当前 UI 的 idle/working/failed，未知状态失败；不读取历史
成功响应，核明确无结果说明、全部13项“未计算”、
禁用导出和非空命名画布。原内容检查仍输出，不画假图、不补旧值。
正例另用真实 ready/failed 观测和实际 HTTP 产物执行定向纯判据尺子：
两项正控与六项阴性（非 ready 残留数值、ready 空结果、无当前响应、
配置漂移、非 ready 可导出、未知状态）。该尺子明确标
`PURE_GRAPHICS_RULER_ON_REAL_OBSERVATIONS_NOT_BROWSER_MUTANTS`，
不冒称六项阴性是在浏览器里篡改产品；全矩阵正负夹具仍为真实 Chromium。

最终专项已现读 `seat-audit/suite-1791623397637805725/summary.json`
与 unittest.log：7 方法 PASS、0 failures/errors，212.392s；11 次实际
Chromium 子运行与6项 CLI 拒绝，另有上述纯尺子2正控/6阴性。
工具 SHA256 为
`5b64fd765af00682943dc36672be262d9c19e567fd246609f96000f3c25de853`，
测试 SHA256 为
`0a5a1b0fcf4727db67eaff2906d9c585ea98e4ada2827b6ad077f08a8f543778`。
正例 `seat-audit/fixture-positive-1791623494441596738/summary.json`
实际 rc0、261检查、403分段PNG、4视口、16注入、556 focus/hover各样本、
3720间距对、0发现；不是403个独立用例。负例
`seat-audit/fixture-negative-1791623404010180471/summary.json` 实际rc1，
检出全部要求规则及真实0px间距/低焦点对比。身份阴性
`seat-audit/fixture-identity-negative-1791623400590228267/summary.json`
实际rc2，先完成smoke，再正确拒绝false health。六项真实I/O负例与更早
身份首因保留均通过；suite链接全部子报告、字节与stderr证据。
语法、diff与三文件敏感信息模式扫描通过（零命中）。只验证该完整基线
的专项兼容；未重跑完整产品/FEM，不追认两轮旧FAIL，不改变真人读屏
NOT_TESTED与G3实质PARTIAL。最终候选由主席整合后统一验收。

各次运行、包括失败，保留独立目录、源码/工具摘要和日志。
2026-10-10 初次 smoke 的 Chromium 正常退出误判、关闭 details 误纳入、
文字/滚动区边框误分类、Enter 字符事件及负例焦点样本漏记已经诊断修复；
旧报告不删除、不追认为通过。跨席整合后需用新源码重跑，不能沿用旧实例。
