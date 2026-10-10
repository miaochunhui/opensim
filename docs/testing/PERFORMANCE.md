# G5 Performance

本工具执行真实 Chromium CDP 单次冷启动，不安装依赖，不修改服务、网页、
网络入口或凭据。`browser_check.mjs` 的布局/键盘验收不是性能证明。
标准为 `shared-r41`，覆盖 `WEB-PERF-001` 至 `WEB-PERF-006`。

## CLI

在待验提交对应工作树中执行。启动自己的新回环实例，不复用旧进程：

```bash
sudo -n -u infra python3 -B server.py --host 127.0.0.1 --port 0 --log-dir /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-perf/server-logs
sudo -n -u infra node tools/browser_performance.mjs --url http://127.0.0.1:PORT/
sudo -n -u infra node tools/browser_performance.mjs --url https://opensim.ai4everyone.top/
sudo -n -u infra node tools/browser_performance.mjs --parse /ABSOLUTE/REPORT.json
sudo -n -u infra python3 -B tests/test_browser_performance.py --real-browser
sudo -n -u infra python3 -B tests/test_browser_performance.py --real-browser --preflight
sudo -n -u infra node tools/browser_performance.mjs --selftest --fixtures /ABSOLUTE/FIXTURES
```

`PORT` 使用新实例启动日志中的实际端口。完整工具路径为
`/data/dev/opensim-perf-review-v1/tools/browser_performance.mjs`。
可选 `--chrome PATH` 使用已有 Chromium；默认既有 Chrome 140 可执行文件。
Node 必须已有原生 WebSocket。缺浏览器/依赖时失败，不自动安装或下载。
`--out` 仅允许外部 `/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-perf/` 子目录，
检查真实祖先路径，防止符号链接写入仓库。每次产生新的不可覆盖 run 目录。

只允许 `https://opensim.ai4everyone.top/`、同站 `/index-v1.html` 和回环 HTTP
对应页面。不允许替代域名、非标准 HTTPS 端口、凭据、查询串或片段。
公网预查 DNS 必须全部为公网地址，报告核对 Chromium 实际连接地址；
禁代理、禁忽略 TLS 错误。任何重定向在跟随前阻断并保留 FAIL。

退出码：0 为所选范围通过；1 为性能/内容/网络失败；
2 为配置、依赖、报告格式、自检或验收器错误。首因、阶段、规则 ID、
时间和退出码写入 `report.json`、`run.log`，浏览器原日志独立保存。
异常时尽量保存部分样本，但部分样本不能解析为完整 PASS。

## Fixed Baseline

唯一配置 `opensim-mobile-cold-v1`：

| 项目 | 固定值 |
| --- | --- |
| CSS 视口 / DPR / 输入 | 360x800 / 1 / mobile + 真实 CDP touch |
| 延迟 / 下行 / 上行 | 150ms / 200000 bytes/s / 93750 bytes/s |
| CPU | `Emulation.setCPUThrottlingRate` 4 |
| 初始观察 | navigation 后 8000ms |
| 缓存 / service worker | 全新 profile + 禁缓存 + bypass service worker |
| 样本 / 重试 | 1 / 0 |
| 故障等待上限 | 30000ms，不用来放宽预算或延长固定观察 |
| 真实默认算例 | 圆管，宽高4mm、长度100mm、压差1Pa、黏度0.001Pa.s、密度998、resolution16 |

CDP 配置命令必须成功，原始响应时间、资源计数、浏览器版本和参数留证。
CPU 降速是相对当前宿主机的仿真，不是指定物理手机的性能认证。
不提供阈值、网络/CPU调整、repeat、warmup、reload 或选最好值参数。
修复后复验创建新 run，原失败不得改写、删除或追认为通过。

## Metrics And Budgets

- LCP：首个输入前、固定初始窗口中最后一条实际
  `largest-contentful-paint.startTime`；必须存在正值条目，门槛 `<=2500ms`。
- CLS：真实 `layout-shift` 最大 session window；相邻间隔 `<1000ms`，
  窗口 `<5000ms`，排除 `hadRecentInput`。无条目但 observer 成功时为真实零，
  缺 observer 不是零。门槛 `<=0.1`。
- 交互：真实 touch 激活 `#view-mesh`、`#view-field`、`#solve-button`，
  每个恰好一条可信 click。捕获可信事件时间戳至第二次 rAF 回调的保守绘制机会
  上界，同时取该路径 Event Timing `duration` 的最大值。所有事件、路径取最大，
  门槛 `<=200ms`，不会挑快事件。缺事件/未绘制/控件遮挡均失败。
- 该口径为 `EVENT_TO_PAINT_UPPER_BOUND_NOT_INP`，不是 INP 或用户 P75。
  单条实验室路径不能推断真实用户指标；`inp_ms=null` 明示未测。
  异步 HTTP 求解到结果完成另外核验，不把网络求解耗时冒充输入响应指标。
- 确认真实浏览器收到两次 `/api/solve` 响应，默认配置一致，
  网格/三角形/速度非空，结果和非单色 canvas 实际出现，且可导出状态就绪。
- 传输使用 `Network.loadingFinished.encodedDataLength`：实际压缩线路字节，
  保守包含响应头；不是自行 gzip 估算、源码大小、解压大小或缓存命中。
  KB 为1000字节。初始 HTML + CSS + **全部初始 JS** `<=200000`，
  全部初始资源（保守含自动求解、非首屏资源）`<=1000000`，
  全部初始 JS `<=300000`，字体 `<=150000`。不扣除失败资源。
- 文本解压正文超过512字节必须实际 `gzip` 或 `br`；
  512字节以下仅小响应豁免压缩，不豁免 HTTP 错误。

### Strict Assets

所有初始 JS/CSS 必须使用 `/web/app.<完整64位SHA256>.js|css`；
URL 摘要等于浏览器实际响应内容 SHA256，
`Cache-Control` 必须 `public, max-age>=31536000, immutable`，不含 `no-store`。
legacy `/web/app.js`、`/web/app.css` 的 ETag/revalidate 兼容策略
**不能通过性能缓存门**。不修改 server 席代码，也不自行合并其改动。

无字体请求、无 `@font-face` 时，字体实测为0且系统字体回退天然不受下载失败影响。
引入 webfont 时要求 subset、`swap/optional`、至多一个 preload；
目前工具尚无下载字体阻断后的布局稳定复验，因此该情形明确 FAIL，
不能自行宣称已经覆盖。现代图片检查实际尺寸、固有宽度和响应 MIME。

本版第三方预算为 **0**，所有 off-origin 请求在发送前阻断。
阻断策略始终启用，正常核心求解必须仍能完成；
任何第三方尝试仍为 `WEB-PERF-006` FAIL，不是“已阻断所以通过”。
引入第三方需另行实现逐脚本用途/负责人/时机/移除条件及独立故障隔离，
不能通过关闭检测或放宽白名单接入。

## Report Contract

顶层 `schema_version=1`、`tool=opensim-browser-performance`、
`standard=shared-r41`、`gate=G5-performance`，包含：

```text
started_at, finished_at, commit, tool_sha256, target_url, scope
baseline, limits, baseline_commands_acknowledged, browser, chromium_flags
samples[1], checks[], verdict, failure, failure_count, exit_code
g5_certified, release_verdict, observed_network
third_party, redirects, network_errors, runtime_errors
```

样本包含 `phase/target_url/metrics/raw_metrics/resources/interactions/content/fonts/images/`
`source_identity/cdp_performance/screenshot`。资源记录实际 URL、类型、状态、
传输字节、编码、缓存头、指纹、内容摘要、CDP timing、实际连接地址。
交互记录 selector、click_count、latency、可信事件到绘制上界和 Event Timing。
检查输出 rule、name、status、URL、视口、复现方法和实际值/预算。
`failure` 为首个失败或异常，不吞掉后续 `checks` 与 cleanup_failure。

性能模式在启动 Chromium 前冻结 `identity_before`，停止自有 Chromium 后现读
`identity_after`。两次均检查 HEAD、已加载工具 SHA256、提交中工具 SHA256、
五份完整源码 SHA256 及按生成器顺序现算的 page build；实际工具或源码
未提交、HEAD/源码/工具漂移均闭门，不能把末尾的新 HEAD 归给旧执行代码。
检查两个完整 `/api/health` 和两个完整 `/api/solve`，不只检查最后一个求解。
每个响应与 CDP 请求 ID、状态、实际正文 SHA256 绑定；页面正文及 JS/CSS
原文、health 原文、完整求解、实际 PNG 均外置保存供独立回读。
页面必须出现当前现算 build 的 `<code>`，不是任意字符串包含即可。
固定窗口记录实际结果就绪时间，晚于8000ms完成不能延长观察补绿。

报告持久化先写 `EVIDENCE_PENDING`，再写未认证的 `EVIDENCE_READY` 日志，
回读候选完整报告，最后原子发布终态。晚写、回读或重命名失败为
`persistence_failure`，强制 FAIL/退出2/不认证；已有首因保持原值。
磁盘全部不可写时不能保证新增失败文件，但原有未认证状态不会升为绿灯，
标准错误会保留故障。解析输入保留的 failure/cleanup/persistence 也不能
重新计算为 PASS。日志不是认证权威，只有最后的 `report.json` 是终态。

公网 G5 PASS 要求页面构建、两个实际 JS/CSS 响应、health 及真实求解中的
五份源码 SHA256，全部与当前工作树及当前提交字节一致；
公网 DNS/实际连接地址达标、全部 G5 检查 PASS，才可
`scope=PUBLIC_LAB, verdict=PUBLIC_G5_PASS, g5_certified=true`。
这仍只认证 G5，不认证全部发布门，`release_verdict=NOT_CERTIFIED`。

回环无条件 `scope=LOCAL_PREFLIGHT, g5_certified=false`；
即使全部预算通过也只为 `LOCAL_PREFLIGHT_PASS`，不是生产证书。
离线解析为 `OFFLINE_PARSE_NOT_G5`，自检为
`OFFLINE_FILE_FIXTURES_NOT_G5`，不继承输入报告的认证词。
解析重新从 raw LCP/shift/可信事件计算，不信汇总阈值、PASS 或 bound 自报。
离线文件本身不提供密码学真实性保证，也不能凭修改 JSON 签发 G5。

## Tests And Preserved Evidence

`tests/test_browser_performance.py` 生成外部文件夹具及 manifest，
逐件执行实际 `--parse` CLI，`--selftest` 必须回读这些文件并报告实际 count。
空 manifest、缺文件、坏 JSON、缺样本、原始数据与汇总矛盾、伪造 scope
均 fail-loud。反例覆盖阈值边界、各传输预算、未压缩、legacy alias、
缺 immutable、URL/正文摘要不符、第三方、重定向、离线错误、404、
空求解与字体回退未验证。

`--real-browser` 另起新回环夹具服务，生成4个文件页面，各用新 profile：
快交互对照、3秒后出现的 LCP、大布局移位、350ms 忙循环交互。
夹具非 FEM/产品验收。各自原始 observation.json 和 Chrome 日志外置，
必须确实观察到超阈值，不能仅对数字判定函数打桩。

2026-10-10 首轮证据 `run-2026-10-10T07-48-08-024Z-ba722a82`：
FAIL 首因 `#view-field` 独立开关被工具误认为互斥选择；已修验收器，
未修改应用。第二轮 `run-2026-10-10T07-49-36-732Z-26c05b75`：
真实样本 LCP892ms、CLS0、交互最大86.8ms，但 favicon404导致 FAIL。
该报告早于严格指纹门，不可用旧缓存 PASS 冒充现行结果。
第三次测试入口 `run` 属性覆盖 unittest.run() 导致 TypeError，
测试未执行；属性已改为 `evidence_run`，不追认旧失败。

已回读测试 `python-tests-20261010T075340Z-1b5ff1df/summary.json`：
5项测试、26文件夹具、37 CLI调用和4真实浏览器夹具 PASS；
对应 `run-2026-10-10T07-53-46-220Z-b149a51b/report.json`。
后续新增反例使 count 变化，以最终报告现读数字为准，不挑最好。
证据根均为 `/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-perf/`。

最终严格门预检 `run-2026-10-10T07-57-44-918Z-829c759d/report.json`：
1真实样本、19检查、LCP820ms、CLS0、交互最大76ms，
关键传输21688字节、初始全部42425字节；`FAIL`，失败两项：
favicon404、legacy JS/CSS 缺完整指纹和 immutable 长缓存。
对应最新测试 `python-tests-20261010T075744Z-dd6d7624/summary.json`：
5项测试、27文件夹具、38 CLI调用、4真实浏览器夹具 PASS。
这些是检测器反证与 LOCAL_PREFLIGHT，不是产品 G5 PASS。

未绑定公网、未验证 server 席指纹版本，均不能声称产品 G5 已通过。
主代理整合后从完整提交启动新实例，再执行一次固定预检及获准公网测量。
回滚只涉及本席这三个新增文件的独立提交，不回退、覆盖或清理其他席资产。

## Independent Review V1

2026-10-10，本席从 `01311b9` 建立独立 `codex/perf-review-v1`，
以 `b27ef2af499de7526919c1ff2bc8a9a706f30168` 为性能依赖
（本工作树依赖提交 `1adfdc1e471f0ec37fd488c6a839e0b7c4985c03`）。
仅改本工具、性能测试与本文件，不修改 server/render、infra、
OpenCAD/openopt、生产、凭据或公网入口，不 push。

修前产品单次冷启动
`review-v1-20261010/before/run-2026-10-10T08-17-51-547Z-1dc9a8d8/report.json`
有1样本、19检查、LOCAL_PREFLIGHT_PASS，证明该基线图标与指纹资源已修。
新增回归反例的首次红灯
`review-v1-20261010/python-tests-20261010T081935Z-d73d1ff9/summary.json`
为6测试、28文件夹具、3失败：首个求解身份错配被旧工具解析成 PASS；
自检捕捉该假绿后失败；`run.log` 文件碰撞造成进程退出失败但报告仍 PASS。
原失败完整保留，修后不追认旧报告。

新增41文件夹具覆盖提交/工具/源码前后漂移、首个 health/solve 错配、
缺初始求解、空速度、假页面 build、旧错误终态、超观察窗口和 INP 冒称。
实际 CLI 的日志/候选报告碰撞分别验证绿路径降为 FAIL 与旧首因保持。
`--preflight` 再用新专属回环服务执行真实 Chromium 日志晚写失败负例，
之后执行一次完整固定产品冷启动；结束只停止该入口创建的服务进程。
`--real-browser` 仍为四个真实 Chromium 文件页面：
快速输入阴性对照、晚 LCP、CLS 跳动、350ms 忙循环输入三个阳性反例，
不是四个产品算例，也不是生产认证。

全部新增测试及最终产物只在
`/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-perf/review-v1-20261010/`
新建带时间/随机后缀的目录，最终 `summary.json` 和 `preflight.json`
现读实际计数、提交、工具摘要、原始响应/截图核验与自有服务停止状态。
既有两次启动测试曾用默认 evidence 根创建独立新目录，未覆盖其他产物。
未覆盖 actual root/public/manual 发布记录，不签 G5 公网或全发布门，
缺真实触屏、桌面/移动屏幕阅读器以及公网同版本实算仍明确未验。
