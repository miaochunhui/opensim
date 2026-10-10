# 工作台组合验收

## 2026-10-10 续跑消费门

独立基线 `6734853` 的新增负例先保留于
`resume-s3-workbench-20261010/original-red-v1/summary.json`：4 methods、
14 failures、0 errors，真实 launcher rc1，source_before==source_after。
这证明原消费门漏拒，不追认旧红；后续计数以新 summary 为准。

完整 browser 必须逐一消费 capture manifest 和实际 PNG，clip/DPR/尺寸、
row-major 无缺口无重叠、selected_view/canvas/coordinate、摘要及实际文件
集合双向一致。固定八矩阵的 width/height/touch 不由报告自行定义；
捕获名字从固定 core、keyboard、media、text/page、rectangle 流程及当前
CSS 断点推导。当前无 dark mode 时为55 captures，不是55 PNG。
八矩阵、keyboard/page core 的 canvas 与实际 flow check 对拍，breakpoint
与实际 view check 对拍；field/mesh hash 必须不同。producer 没有独立
drawing_state 字段，不能虚构它，也不声称 PNG/FNV 摘要能抵抗恶意同 UID
同时重写全部证据。media/text check 没有返回 core canvas，保持明确边界。
配置视口核已有 layout.viewport；捕获宽度核 layout.client_width，不假设滚动条
宽度，不把 clientWidth 与 innerWidth 硬等。keyboard 无独立 layout，使用同尺寸
基线 layout 并明确不称该阶段重新采集 layout。bad-input 已清旧结果，核视图/
有界画布/格式，不要求未清 FEM 像素；正常 field/mesh 仍拒绝空画布。

audit 的44必需 whole 状态包括四视口各 loading/ready/error/empty/long、
三 recovery、mesh-selected、field-unselected、details-open-0。
其它实际 details 状态也必须消费；whole/coverage/所有 tile/实际 PNG
双向闭合，段数、顺序、末段允许 producer 的正常重叠滚动，但不漏段多段。
该逻辑只扩展原纯 validate_audit_coverage，不新增 helper 依赖，供 pinned
release_gate 复用，不执行其 runner 或授予 human/production 信任。

全模式 UI 必须有实际 completed downloads，空集合不假绿。正向组合终态
额外核四 producer、各自完成协议与实际依赖、同次进程码/完整日志、perf
parse 回执、server-before/after 四正文、已停止的自有服务和源码前后一致。
read_final 同样核闭合和完整终止日志；FAIL 诊断可不完整，原首因优先保留。

专项入口（目录必须新建；不运行 producer 或全 FEM）：

```bash
sudo -n -u infra python3 -B tests/test_workbench_acceptance.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/resume-s3-workbench-20261010/module-new --release-source /data/dev/opensim/tools/release_gate.py
```

默认使用本 checkout 的 release_gate；基线树缺新 API 时显式只读覆写其源码。
--focused 仅新 controls。run-start/progress/summary/log 全在指定目录，summary
记实际子进程 rc、tests/failures/errors/skips 和完整 source_before/source_after。
文件夹具只证明检测器，不是实际产品证据。--producer-readback 原生入口
回读旧真实 PNG/POST/captures/receipt/log，但旧 commit 不升本候选认证；
新版 selected-view 证据缺失必须拒绝，不补造历史字段。最终 v3 全组合归主席。

本入口只组合真实本地自动化，不签署授权、例外、公网同步或发布认证。
不安装、不下载、不修改生产、不重试，不复用在役 FEM 端口。
只有当前干净且已提交的候选可以运行；dirty/untracked、运行中源码变化、
缺工具、空证据、非零进程码、未完成终态及身份漂移都 fail-loud。

## 入口与顺序

主代理整合三个 producer 后，在同一候选工作树执行：

```bash
sudo -n -u infra python3 -B tools/workbench_acceptance.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/workbench-integrated-new
```

目录必须新建且位于 OpenSim 外置根；再次运行必须另取新目录，不清理旧证据。
默认串行：unittest、JS syntax、真实 numerics、render selftest、repeat render，
随后同一个候选专属 `127.0.0.1:0` 服务依次运行 full browser、audit、perf、UI。
本地阶段沿用 acceptance.py 的五条真实命令，但显式传 numerics 的 `--log-dir`，
避免把日志写到共享默认目录。服务使用本次已回读、matching solver/validator
的 numerics；只关闭本次拥有的对象，不重启、停止或占用其它实例。

也可添加 `--integration-report /ABSOLUTE/integration.json`：仅接受同完整候选
before/after inventory、同 integration tool 摘要、local+fullbrowser 的终态成功
报告。核两进程 rc、日志哈希、local 五步及 unit 完成标记、原始 numerics、
fullbrowser 8 视口、POST、PNG、commit/tool/page_build 后才复用。
旧提交即使应用五源码相同也不能组合；不通过更新日期追认旧报告。
复用浏览器属于先前已停止的同候选实例；新 audit/perf/UI 仍共用本次专属服务。

真实 producer argv 只传 `--url URL --out DIR`，无 smoke/minimal/selftest、
超时放宽或 no-sandbox。输出根由 producer 现有 CLI 约束决定：

| Producer | 工具 | 专属外置根 |
| --- | --- | --- |
| 完整 browser | tools/browser_check.mjs | browser/workbench-UUID |
| G3/G6 extension | tools/browser_audit.mjs | seat-audit/workbench-UUID |
| G5 local cold | tools/browser_performance.mjs | seat-perf/workbench-UUID |
| UI contract | tools/ui_contract_test.mjs | seat-ui/workbench-UUID |

各 root 必须不存在，producer 在其下建立唯一 `run-*`。perf 原始报告额外经
真实 `--parse REPORT --out seat-perf/.../readback` 重新计算 raw/budget；
解析报告为 `OFFLINE_PARSE_NOT_G5`，不是新增产品样本或 G5 证书。

## 现读契约

编排 stdout 仅总判定、真实计数和首因，完整日志/traceback 在外置
`workbench.json`、`workbench.log`、`logs/`。子进程 stdout/stderr 直接落盘，
进程 rc 和 report.exit_code、verdict 同时验证。失败中保留首个 producer 原因，
后续 cleanup/文件故障另记，不用第二个错误覆盖首因。

执行前、进程退出后、产物回读后保存独占递增的 `state-NNN.json` 快照，
每份均 `PROVISIONAL_NOT_FINAL`、`exit_code=null`，候选判词另放
`candidate_verdict`；中断时没有终态报告不算完成，RUNNING/EXITED 只作复飞坐标。

终态协议由 `persist_result` 实施，不依赖操作者记住消费进程 rc：
先独占写 `workbench.provisional.json`（非终态），再写
`workbench.log`（首行 `PROVISIONAL_LOG_NOT_TERMINAL`），文件与目录 fsync、
精确 inode/字节回读通过后才创建 `workbench.json`。最后再次回读日志与终态。
任何晚期 I/O 故障只撤销本次创建且设备/inode/字节完全匹配的正式文件，
移到独占 `revoked-owned-UUID-workbench.json` 保留原始失败证据，不删除。
随后写独占 `persistence-failure-UUID.json`，`FAIL/exit_code=2`，保留 run 首因，
I/O 与撤销故障另记；stdout 只报 FAIL、首因与持久化原因。
若文件被他人替换或改字节，撤销拒绝、保存冲突，绝不覆盖未知资产。
机读消费门 `read_final` 只认固定 `workbench.json` 的 `FINAL_AFTER_DURABLE_LOG`
及日志路径/字节数/摘要，并拒绝同目录持久化失败标记、判词/rc 矛盾；
provisional/state/revoked 文件不能作认证。
旧输出目录、report/log 冲突均保护，不覆盖或继续本地生产测试。

候选前后包含 tracked 文件哈希及 dirty/untracked 全清单；每 producer 前再核，
不以 Git commit 名、运行进程、健康 200 或 rc=0 替代产物验收。
服务前后回读真实 health、HTML、JS、CSS 字节，核五源码、page_build 和稳定哈希。
所有验收文件递归回读、非空和 SHA256；浏览器内部 Chromium profile
只是可再生运行状态，不列为验收产物。空诊断日志明确标 `EMPTY_DIAGNOSTIC`，
UI 的 `bad-empty.json` 明确标 `NEGATIVE_INPUT`，不能算正向非空证据。
总报告、PNG、POST、numerics 原始结果不得空。

报告字段按现有 producer，不虚构共同 schema：

- browser：`mode=OPENSIM_ACCEPTANCE`、`AUTOMATED_PASS`、完整 8 视口；
  逐件 POST 核成功 FEM/source stamp，PNG 核实际 hash、尺寸、CRC、完整解压扫描线。
- audit：`G3_G6_AUTOMATIC_EXTENSION_NOT_RELEASE`、`AUTOMATED_SCOPE_PASS`；
  核 source_sha256/page_build、before/after identity_checks、实际 POST、
  4 视口的 loading-empty/ready/error/empty/long 及三种 recovery 截图，
  每态 coverage.json 的实际 tile 集合、尺寸、无缺口覆盖、16 个唯一请求注入、
  coverage_counts 与 coverage/gates。PARTIAL/NOT_TESTED 逐条保留。
  POST 必须实际 status=200、同 URL/source/solver/time、非空 mesh/velocity，
  成功 http-solve 文件集合与 post_source_checks 双向一致。
  当前 producer 保存解析后的 payload 和 CDP body_sha256/bytes，不保存完整响应
  body 原文字节；此处只验证摘要格式及两处声明一致，不能称独立重算原响应 SHA。
- perf：`LOCAL_PREFLIGHT`、`LOCAL_PREFLIGHT_PASS`、`g5_certified=false`；
  核一份 cold sample、sample.source_identity、固定 360×800 PNG；
  producer 截图没有 width/height，按固定基线核实际尺寸，不补造原字段。
- UI：`REAL_DOM_CDP_CONTRACT`、`PASS`，实际求解记录数量与
  actual_solver_calls 相等，核 sources、原始结果与真实 PNG。
  当前 UI v1 没有 commit/page_build 字段，编排显式记
  `commit_binding=CLEAN_BEFORE_AFTER_INVENTORY`，以工具摘要、五源码、
  前后候选清单及本次服务实际 build 绑定，不伪装 producer 自带 commit。

所有 producer 的 `release_verdict` 必须仍为 `NOT_CERTIFIED`。
scope、tool、commit、source、PNG 及终态不匹配直接 FAIL。
本地全范围成功只为 `LOCAL_AUTOMATION_PASS_NOT_RELEASE`，rc=0；
audit coverage 仍 PARTIAL/NOT_TESTED 时为
`LOCAL_AUTOMATION_PARTIAL_NOT_RELEASE`，rc=3，首因明确剩余覆盖项。
失败 rc=1；参数/落盘入口错误 rc=2。PARTIAL 不升认证。
公网 DNS/TLS/同版本真实 FEM、public G5、物理触屏/软键盘、桌面/移动读屏、
人工截图审阅、生产 apply/revert、授权和例外均另列未验。

## Producer 终态消费

`validate_durable_producer` 对各 producer 强制消费各自的实际协议，
不发明共同 receipt，不以递归读取任意文件或 report PASS 代替完成证明：

- audit `fb852044`：report.completion 为 `durable-final-v2/FINAL`，
  completion.json 为同协议 `COMPLETE`，receipt rc 与真实进程/报告码相等；
  receipt.report_sha256 对 report.json 原字节，receipt 与 report 的 log_sha256
  同对实际 logs/audit.log，候选日志判词/rc/report 路径必须同本 run。
  `report.failure.json` 存在即拒绝。
- perf `994d8d2`：persistence_protocol.version=2、held-inode strategy、
  exact-byte/single-link/no-follow ownership 和 file/directory fsync/readback
  声明与实际工具一致；实际 run.log 的 EVIDENCE_READY scope/time/
  terminal_authority 对当前终态，report-candidate.json 只能是原终态对应的
  `EVIDENCE_PENDING/exit_code=2/g5_certified=false`。该 candidate 既不是失败
  marker，也不是成功证据。`report-failure-*.json` 即拒绝；原始与 --parse
  输出均经同一消费门，读回所有文件摘要及单链接状态，不伪造日志摘要字段。
- UI `e596718`：state=FINAL，durability 为
  `FINAL_AFTER_FSYNC_AND_INODE_READBACK` 与实际 exclusive/fsync/inode protocol；
  report.evidence_sha256 对 evidence.json 原字节；report/manifest
  run_log_sha256 同对实际 run.log，其首行与候选终止行必须相符。
  manifest 必须 COMPLETE、source/count 同报告；files 不重不漏、不越界，
  与截图、solve 文件及实际完成 downloads 双向一致，逐件 SHA、导出来源验证。
  operations 的 fsync-file/exact-readback 用 `file`，fsync-directory 用 `path`；
  依赖文件实际 dev/ino/SHA 与目录 dev/ino 均对拍，不要求 producer 在 final
  创建前快照中不存在的 final-report 操作。旧 manifest-only 协议明确拒绝。
  finalization-failure.json/report-revocation.json 或真实 failure 字段即拒绝。
- browser：按主席交接的 `browser-durable-final-v1/FINAL_AFTER_DURABLE_LOG`，
  durability.log.file=run.log，bytes/SHA 对实际非空日志；末行完整绑定
  finished_at、BROWSER_CHECK、candidate_verdict/candidate_rc 与本 run 的
  report=路径（获准逻辑/物理同 realpath），必须有行尾换行，不接受额外词。
  `report.failure.json` 存在即拒绝，撤销或未知 report 时先读此侧档保留首因。
  不要求不存在的 FINAL 文本。
  本席未见新版 browser 实际终态产物，只有消费夹具，新版真实组合归主席核验。

报告、receipt、log、manifest 通过有界 no-follow/nonblock 描述符回读，
前后核 dev/ino/size/mtime/ctime/links 与命名路径，结束再次核依赖及失败标记。
仅接受获准 `/hf3fs-jd` 根和其已知 `/3fs` 物理落点的同 realpath 语义，
不拿 `.absolute()` 字符串区别误拒真实日志与 operations。
这不是密码学签名、断电恢复、全盘耐久或恶意同 UID 并发隔离证明。

撤销 report.json 或留下未知替换 bytes 时，`load_producer_terminal` 先回读
上述实际失败侧档，保留 first_failure/finalization_failure/revocations；
原运行首因优先于晚期 I/O，摘要还保留 stage/code/syscall/path。
空/坏失败侧档也不能获得成功。原 browser schema/mode 不变，其新尾部实现
由独占 browser 席与主席收口，本 delta 只消费已明确交接字段。

## 本席验收与交接

```bash
sudo -n -u infra python3 -B tests/test_workbench_acceptance.py
sudo -n -u infra python3 -B tests/test_workbench_acceptance.py --regression
sudo -n -u infra python3 -B tests/test_workbench_acceptance.py --cli-smoke missing
```

测试生成外置真实文件与独立 Git 夹具，保留 manifest/hash、实际 unit count、
summary 和首因日志。覆盖空/缺/旧 source/tool/commit/page、rc!=0、
伪 scope、未完成/partial、PNG hash/尺寸/CRC/解压损坏、缺 POST、dirty/untracked、
外置边界与先前首因；额外启动本 worktree 真实 loopback server 回读实际 HTTP
并验证只关闭本次对象。夹具通过不是真实 producer/工作台/发布通过，
没有 mock producer 为生产 PASS。

接口研究依据完成的 perf 提交 `b27ef2af`，只读 audit/UI worktree：
`/data/dev/opensim-audit-v1`、`/data/dev/opensim-ui-v1`。
本席基线 `01311b9`；只提交 tools/workbench_acceptance.py、
tests/test_workbench_acceptance.py、本文，不合入或 cherry-pick 他席在制品。
主仓在跑 integration 不纳入这三件，避免源码盘点漂移。

当前本席 worktree 缺 audit/perf/UI，真实完整组合未验，不能称全部开发完成。
主代理 review 后整合三 producer 和本席三件，在最终干净提交的新目录运行本入口；
核实际 report 计数、first cause、before/after diff、PNG 和剩余覆盖，
再单独完成公网/人工/发布门。回退仅由主代理点名撤回本席独立提交，
不覆盖其它模块或清理他人资产；本席未 push。

2026-10-10 首轮自测 25 项，0 failures / 1 error：合法 3FS 物理路径被
再次校验误拒。失败证据保持在
`workbench-tests-f8bea2816bba4c899a2f0f529f942459/summary.json`，
修复逻辑根/已知挂载根后新增反例。32 项文件/HTTP 自测现读
`workbench-tests-691ae12719454ee4acae9b99d054f8ff/summary.json`：
PASS，0 failures / 0 errors，`2026-10-10T08:25:16+00:00`。
CLI 脏候选反例 `workbench-tests-f5fa51c69da24eb993572fc284d478d0/summary.json`
现读 PASS，child_rc=1，启动 local/server 前拒绝。以上只证明验收器检测，
不追认历史产物，也不替代整合后的完整 producer 实测。

独立复核发现“PASS 报告先写、日志失败仍留 PASS”的持久化缺陷，
已按上述 provisional→durable log→final→exact-own revoke 流程修复。
2026-10-10T08:30:57+00:00 专项现读
`workbench-tests-40c5e1ade2f34c058ad5df0e42b03f8d/summary.json`：
39 项 PASS，0 failures / 0 errors。新增真实 I/O 反例使用文件目录冲突、
暂时撤走实际文件触发 ENOENT、关闭 fd 触发真实 fsync EBADF，
以及 inode 替换/同 inode 字节改变；不 mock 判词为生产通过。
验证精确自有终态被撤销、未知文件保留、run 首因不被持久化错覆盖。
本席不再运行旧产品组合；最终组合仍归主席合入全部 delta 后执行。

最终专项现读 `2026-10-10T08:32:21+00:00`：
`workbench-tests-a219194fa86b40c99d6e7b93b004178b/summary.json`，
40 项 PASS，0 failures / 0 errors；增加了机读消费门的持久化失败标记、
真实日志漂移和 provisional 拒绝例。脏候选 CLI 现读
`workbench-tests-020c0347c68e41a1acb5979fd573c108/summary.json`，
PASS，child_rc=1，`2026-10-10T08:32:13+00:00`。

本轮在原 `5dea139` 三件上新增上述终态消费，主席已先合原件；
只读研究 audit `fb852044`、perf `9214c70+994d8d2` 和 UI `e596718`，
不合入或修改他席资产。本轮不运行 --regression、旧完整 FEM 或全组合。
文件反例覆盖 receipt 缺失/空/未完成/错 rc/report/log SHA，实际字节漂移、
失败侧档+未知 report 保护，UI 旧协议/operations 字段和 inode 变异、
manifest 缺漏/重复/越界/错 source/count，perf pending/单链接/log 漂移，
audit POST 与四视口状态/注入/tiles 缺件。夹具只证明检测器，不称产品 PASS。

只读实际产物协议入口为测试工具的 `--producer-readback PRODUCER REPORT
--producer-rc RC`；RC 须取原同次 execution/invocations 记录，不能猜测。
该入口输出 `EXISTING_PRODUCER_PROTOCOL_READBACK_NOT_PRODUCT`，不以旧
commit、最小 run 或文件夹具冒充本候选完整执行。
首轮新专项 `workbench-tests-f549e6905904456692bec691903e0f65/summary.json`
现读 2026-10-10T08:45:06Z：53 项、0 failures、1 error；真实缺 POST ENOENT
暴露旧反例只捕获 EvidenceError，已统一缺证据报错后新目录复测，旧失败保留。
修后专项 `workbench-tests-1658db1c2c7447d1b93797b9a02430c4/summary.json`
现读 2026-10-10T08:47:26Z：53 PASS、0 failures/errors；随后增加三种实际失败
侧档否决未变成功 report 的反例，最终计数以最新 summary 为准。

只读真实协议回读，未启动 producer 或 FEM，证据均在同外置根：

- audit `workbench-tests-fa8c4260733b4d4289d98ae395522142/summary.json`：
  2026-10-10T08:47:34Z PASS；读取 fb852044 工具产生的真实 Chromium 正向夹具
  receipt/report/log、成功 POST 和32份必需状态覆盖。mode 仍为 fixture，
  此结果不是产品 audit 通过。
- perf `workbench-tests-31a9796268614d98a9cc548cea0aa728/summary.json`：
  2026-10-10T08:46:20Z PASS；读取 994d8d2 的实际 --parse rc0/21 checks 输出，
  log/pending/protocol 通过；输入为合成尺子，不是产品 cold/G5。
  原 invocations.json 与 cli-003.log 同验 rc0；先前挑到 --selftest 失败报告的
  读回为 FAIL，首因 SELFTEST/At least six named file fixtures required，留在
  `workbench-tests-12fc4df9260d416b96c650ad75f890aa/summary.json`，不追认。
- UI `workbench-tests-3ace5a110d254e9eb79dd900fb7807dd/summary.json`：
  2026-10-10T08:47:39Z PASS；读取 e596718 完整真实 UI run
  `seat-ui/unittest-20261010T084206Z-7bd59a43/run-2026-10-10T08-42-06-706Z-03680c1c/report.json`，
  原 execution.json 同次 rc0，43 checks、61 solve、28 PNG、6 downloads，
  实际 report SHA256 为
  `29b6c3bbbac86a57d3eed6cb7ac26018e0f682a028e8a02b480d98d590a5c7b8`；
  全部 manifest/log/SHA/operations dev/ino 对拍。此前 durable-smoke 仅为
  新协议最小3 solve run；没有使用 cb75283 preprotocol 报告充当新版证明。

以上不追认主仓新候选组合、不代人工/公网认证；最终冻结候选全组合由主席执行。
完整三 producer 消费专项最终 2026-10-10T08:49:23Z 为54 PASS，
0 failures/errors/skips，报告
`workbench-tests-2c33673c196146468f7f9572b3c91bde/summary.json`。
主席随后明确 browser 新尾部字段，本席只加对应日志消费及8种文件阴性变体，
不修改 browser 席资产；最后测试计数以新增后 summary 为准。
新增 browser 消费后的最终专项现读 2026-10-10T08:51:36Z：
`workbench-tests-b87fe2fd22ca4b20b4e2dc14e0f2ded4/summary.json`，
55 PASS、0 failures/errors/skips；所有专项已终止，无遗留 RUNNING。
新版 browser 真实产物仍未回读，整合后的同候选串行组合未运行，
本席不声明发布认证或全部开发完成；未 push。
最后 browser 消费修订补齐实际 `report.failure.json` 读取/否决和完整尾行绑定。
2026-10-10T08:54:34Z 专项
`workbench-tests-04d9c6db4f6d4834bab9d677955d61c2/summary.json`：
56 PASS、0 failures/errors/skips；含错时间/工具/路径、多词/缺换行反例，
合法获准路径别名正例，以及失败侧档首因/未知资产保护。无遗留 RUNNING；
新版 browser 真实产物和最终候选全组合仍由主席验证。
