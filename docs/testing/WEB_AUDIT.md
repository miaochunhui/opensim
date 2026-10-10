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

各次运行、包括失败，保留独立目录、源码/工具摘要和日志。
2026-10-10 初次 smoke 的 Chromium 正常退出误判、关闭 details 误纳入、
文字/滚动区边框误分类、Enter 字符事件及负例焦点样本漏记已经诊断修复；
旧报告不删除、不追认为通过。跨席整合后需用新源码重跑，不能沿用旧实例。
