# 严格发布门与显式同步请求

## 新候选兼容取入边界

`/data/dev/opensim-reviewed-closure-candidate-20261011` 基于固定
`f32d6a6785016eaed06a92d19475789511e62222`，仅取入 gate、obligations、
sync、三份对应测试及两份部署文档，共八路径。前三运行源码及
test_release_gate/test_release_closure 精确取已审 clean
`e4f12162a3c09d819622b66f8722f4c74cde6c23` 的 committed bytes。
test_sync_release 只合 closure 增量，保原 f32d 全部方法与 bundle-mode
入口：真实 prepare 新自有 readonly bundle、两次验证成功、坏 pin 拒绝、
自有副本0644拒绝，不选择或修改共享历史 bundle。产品 copy/Tab、
release_app 的0444门及原失败证据不变，不新增通用 candidate guard。

immutable continuous consumer610c22及其旧sync7dfe/gate40d0 sibling不替换。
closure_api仍加载固定外部55dc/c1b/36f三件；新候选需同字节 committed
source，不能以路径、旧回执或自动PARTIAL改名代此条件。此取入不是
部署、人验或公开发布；缺真实human/trust/measured仍BLOCKED，apply仍REFUSED。
原693OK/十阶段0/PARTIAL是主席已收的f32d自动原件，不认证本新commit。

本候选原窄入口（NEW必须为新独占目录，不能写兄弟既有tests目录）：

```bash
sudo -n -u infra python3 -B /data/dev/opensim-reviewed-closure-candidate-20261011/tests/test_sync_release.py --bundle-mode-only --select test_real_peer_bundle_validate_and_bad_pin --out /hf3fs-jd/data/tmp/opensim-fluid-v1/authorized-s4-sync-bundle-mode-fix-20261011/closure-candidate-bundle-NEW
sudo -n -u infra python3 -B /data/dev/opensim-reviewed-closure-candidate-20261011/tests/test_sync_release.py --closure-select test_complete_closure_reaches_real_gate_missing_trust --closure-select test_v2_rejects_ledger_fallback_and_wrong_closure_pin --closure-select test_v2_apply_guard_refuses_before_gate_or_bundle --closure-select test_closure_late_stdout_and_report_inode_reject_real_child --closure-select test_unknown_desired_field --closure-select test_missing_controller_refused --closure-select test_forged_certified_receipt_cannot_authorize_sync --out /hf3fs-jd/data/tmp/opensim-fluid-v1/authorized-s4-closure-candidate-compatibility-20261011/closure-focused-NEW
```

以下各节保留原实现/审查时点及接口说明，不将其历史测试数追认新候选。

## S4 Closure 同步入口（独立开发）

独立树 `/data/dev/opensim-s4-sync-closure-20261011` 基于 clean
`b7e17b58b5a64ce878e373723a810b90d0eb46ec`，本次仅修改
`tools/sync_release.py`、`tests/test_sync_release.py` 和本文。
以下旧 v1/bridge/assessment 说明保留其原语义，不追认为新版认证。
原 gate、obligations、trust、API、controller 和生产均只读。

新增 `opensim-desired-release-v2` 的字段与 v1 相同，但精确移除
`ledger/gate_receipt`，仅加入 `closure_request={path,sha256}`。
两个 schema 严格互斥，未知字段或同时带旧 ledger 一律拒绝；
不把缺 closure 补成 ledger，也不读取可编辑 CERTIFIED 回执代替 gate。
CLI 参数仍为 `--source-root/--desired/--desired-sha256/--out`。

v2 在本次结果旁创建独占 `closure-gate-UUID` 目录，实际启动当前
candidate 同字节的 `release_gate.py --closure-request`，保存完整 argv、
实际 child rc、stdout/stderr 原始 bytes（允许真实 0 字节）、观测 JSON、
gate 报告/log pin、材料集合和源摘要。不调用 producer/FEM/公网或 root。
gate/sync/测量源码必须等于 committed candidate，不跨树 fallback。

成功准入必须由原 `verify_process_result()` 核实际 CompletedProcess，
另要求 CERTIFIED/APPROVED_EXCEPTION 与正式 scope；TEST_ONLY terminal
及自动 PASS 均不能接纳。BLOCKED1 仅验证精确原 stdout 终行、stderr
首因、provisional log、invocation/report hash、实际 rc，不当成功回执。
报告/log/原空流的前后 inode、bytes、源及 request 必须不变；
失败侧档、晚 stdout、同字节换 inode 都拒收。

随后实际复用原 `evaluate_closure()` 做末次完整原件/测量/trust 验证，
复用其 publication guards，不复制测量或认证算法。实际缺真人、
测量 FAIL、原件 FAIL/sidecar、未登记 production trust 均保持 BLOCKED、
NOT_CERTIFIED/rc1；原 PARTIAL/NOT_TESTED 从不重写。
可信成功路径再走原固定 bundle/API/manifest/epoch/release layout 校验。
本轮尚无真实 production trust/完整原生测量/人验，成功 gate 到 API
联跑未获证实，不能宣称完整发布链或合法生产安装。

`--apply` 对 v1/v2 都在实际 gate/API 调度前 REFUSED；
controller 即使存在也不授权。全部支路 `production_changes=false`。
旧 ledger-only prepare 入口保持原行为。

```bash
sudo -n -u infra python3 -B /data/dev/opensim-s4-sync-closure-20261011/tests/test_sync_release.py --closure-only --out /hf3fs-jd/data/tmp/opensim-fluid-v1/authorized-s4-sync-closure-20261011/NEW
```

新 runner 仅跑本模块新 closure 方法及三项旧 v1 回归，可 repeated
`--closure-select METHOD` 精选。报告写本席授权新目录，实际 gate 材料
仍在固定 `seat-sync`，不修改原 OUTPUT_ROOT/权限或兄弟 tests 资产。
完整夹具复用已冻结 ClosureTests，真实文件/Git/CLI 到达原 trust 拒绝；
不 mock PRODUCTION_VERIFIED、不使用测试 root/channel 认证生产。
late-stream/文件替换仅扰动自有测试输出，原 gate 子进程照实运行。
所有计数、rc、source before/after 与原始空流以当次 summary 为准。
初 runner 外置 root 拒收 rc1 及 red-real-cli-v2 的真实 schema 前置
拒收失败均保留；不重复旧 80/full module 或昂贵产品。

## 当前判定

本件只交付离线准入工具与负例，**不是上线或自动发布完成**。
`release_gate.py` 当前必定保留 `BLOCKED / NOT_CERTIFIED`：
实际真人验收及可信验收来源尚未接入；没有独立可信的例外来源。
自动 `AUTOMATED_PASS`、G3/G6 `PARTIAL`、可编辑 JSON 中的
`approved_by=user` 或 `REAL_DEVICE_HUMAN/PASS` 都不能升格。
`sync_release.py --apply` 当前返回 `REFUSED`，不调用 root、sudo、
systemctl、DNS、证书或 edge 工具。controller 的文件存在也不代表授权。

原v1实现时仅写该席七件独占文件；当前兼容取入写集以上述八路径为准。新夹具、gate/sync 报告、日志都在
`/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-sync/`，旧失败保留。
按用户明确授权，真实 prepare bundle 独立写固定
`/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-release/sync-prepare-<id>/bundle`；
不改 peer API、ARTIFACTS、现有 bundle 或其它席资产。
两个 `opensim-sync.service/.path` 仅为**未安装草案**，没有 enabled
发布链、postcommit hook、全树同步器或宽权限 broker。
固定输出名和缺失的 request-hash 注入入口亦未运营化；不能直接安装草案。

## 准入契约

`release_gate.py` 先核完整 40 位 HEAD、全树 clean（包含未跟踪、
被忽略文件、skip-worktree/assume-unchanged 状态），再从 committed blob
逐字节核当前文件。运行源集合严格为：

- `tools/render_page.py`、`web/app.js`、`web/app.css`、`solver.py`、`server.py`。
- 工具摘要覆盖 acceptance、numerics、render、browser、audit、performance、
  public_gate、release_app、release_gate、sync_release、workbench_acceptance，不省略缺件。

`opensim-release-evidence-v1` ledger 精确字段为
`schema/identity/evidence/executions/gates/manual/exceptions`。
identity 是 `commit/source_sha256/page_build/tool_sha256`。
每个外置引用为 `path/sha256`，拒绝空文件、重复 JSON 键、非有限数、
符号链接、硬链接、越界路径、旧 hash 或过期观察。
页面字节构建按五份运行源码计算，**不以 mtime 定义构建版本**。

`evidence` 必须同时包含 `local/numerics/browser/audit/performance/public/page`。
不改造原报告：

- legacy browser 必须 `mode=OPENSIM_ACCEPTANCE`、`verdict=AUTOMATED_PASS`，
  checks/matrix 使用 `status=PASS`；原生 `page_build.build_text`
  绑定字节指纹，`edited_at` 与被测 HTML/page-check 对应。
- 原报告没有 `exit_code` 时，由 `executions` 独立 launcher 回执提供真实 rc。
  回执 schema 为 `opensim-producer-execution-v1`，精确字段
  `schema/identity/tool/argv/rc/report/stdout/stderr/collected_at`。
  `report/stdout/stderr` 都为外置 path/hash；真实空 stderr 允许，
  但缺文件不允许。不能把 fixtures 的回执当真实 producer 运行。
- 本地必须有五个实际子进程阶段及非空测试完成标记；numerics 必须
  COMPLETE/PASS、solver 同版、实际算例/拒绝例与三组 8/16/32 收敛。
- page 引用实际 HTML 和 page-check；声明
  `timestamp_origin=SOURCE_MTIME` 时核源码最大实际 mtime，
  `COMMIT_EPOCH` 时核提交时间与 `source_epoch`。两者不得混称；
  固定 bundle 的页面 `edited_at` 用 commit epoch，不套本地 mtime。

`gates` 精确包含 G1..G6，每项为 `evidence/rc/status/checks`。
选中的非空 checks 必须确实存在、PASS、覆盖 `REQUIRED_RULES`，
有 URL/视口/复现坐标；原生报告的 target_url/matrix/coordinate 可复用，
不能凭空加字段或选一个无关 PASS 代表整门。
audit producer 仍 `PARTIAL` 或存在未关闭 `NOT_TESTED` 时阻断 G3/G6。
这是旧 v1 的准入行为。下述显式 v2 桥接将原件与义务分层读取，
不再要求把同一份 native PARTIAL 改成 raw PASS；它仍不认证或发布。

G5 只接受同版本 `PUBLIC_LAB/PUBLIC_G5_PASS`，真实公网 DNS 与浏览器
连接、一个冷样本、固定移动网/CPU 基线、原始 LCP/CLS/event-to-paint、
三次可信交互、实际资源传输/压缩/指纹缓存、字体及第三方隔离全部核验。
本地 `LOCAL_PREFLIGHT`、loopback 假公网、只填预算摘要不能 PASS。
实际 code/FEM 公网报告另外必须同 commit/五源/build、10 分钟内，
圆/矩形真算有新鲜计算身份、节点数、流量及残差；健康 200 不够。

`manual` 必须包含真实触屏、软键盘、桌面/移动 screen-reader、
故障隔离和截图人工审阅，每项必须有设备/组合版本/owner/
页面/路径/实际产物。本轮没有可信人验核验器，因此即使所有离线
字段填满，最终仍阻断；这一能力缺口是显式闭门，不是认证实现完成。

例外候选记录必须有
`id/rule_id/owner/expires_at/reason/scope/removal_condition/status/
approved_by/approved_at/approval_record_id`。
未批准、未知、空字段或到期记录阻断；G5 缺公网不可免成 PASS。
**committed JSON 及其 hash 仅证明版本，不证明用户批准**。
可信用户来源核验器没有获准接入前，所有例外都保持 BLOCKED，不代签。

## G3/G6 补齐与 Legacy 合并边界

当前 audit 如实 PARTIAL，缺口不能通过修改状态词关闭。
既有 legacy browser 可贡献同 commit/build/tool 的八视口、
断点 -1/0/+1、真实求解/导入导出/错误恢复、键盘、AX、强制颜色、
减少动画、200% 字号压力及 400% 重排等效证据。
这些是组合覆盖材料，不是完整标准认证；原生真实缩放仍需补。

- G3：复杂背景、渐变、SVG/canvas、生成内容/native subcontrol
  的实际对比度；默认/hover/focus/错误/加载状态；控件边界、图标、
  焦点 3:1；文字 4.5:1；实际阅读顺序、语义、对话框 trap/
  inert/返回焦点、移动菜单开关/Escape、44px 目标与 8px 间距。
  严重/高等级问题必须为零，普通问题有可追踪处置。
- G6：全关键页面和实际加载/空/错误/恢复状态，超长 URL/hash/
  多语言文案、表格/图/代码/媒体替代与失败；窄屏/平板/宽屏
  构图对比、实际内容盒/字号/有效宽度，禁止只证明没有横滚。
- 人工：真实触屏主任务与软键盘；至少一种桌面及一种移动
  screen-reader 完成标题→地标→导航→主任务→错误恢复；
  关闭图片、阻第三方、慢网/接口失败可见可恢复；逐张人工审阅
  320/768/1024/1064/1440 截图。CDP、AX 或截图文件存在均不替代。

合并者必须逐条列出规则、producer check、复现坐标和外置 hash；
同一规则多个局部证据可解释覆盖边界，但
`PARTIAL + legacy AUTOMATED_PASS` 不等于完整 PASS。
目前工具只消费经 producer 实测报告，不实现任意证据合并升格器。

### S2 原生 Audit 消费

冻结主仓 `75beb17` 的 audit 使用 `page_build={id,footer}`，不是字符串。
必须实有五源 `source_sha256`，并核 before/after 的 local/committed/health
五源、footer 与实际服务 JS/CSS 摘要；缺字段不补 expected。
`evaluate()` 首次及末次实际调用 `read_audit_scope()`，不只在测试调用。
helper 只返回 `AUTOMATIC_SCOPE_OBSERVED / AUDIT_AUTOMATION_NOT_RELEASE`，
有 PARTIAL、NOT_TESTED 或 NA 时保持 `AUTOMATED_PARTIAL / NOT_CERTIFIED`。
真人、完整矩阵及公网 G5 在此 extension 内必须保持 NOT_TESTED；
dialog/menu 的实际零计数 NA 仅如实回读，不是例外许可或 PASS。

原生 checks 无 name，ledger 可用
`{pointer:"/checks/N",sha256:SHA256(encode(original_row))}` 逐项选择。
`encode` 是本工具排序、ASCII、缩进 2 加末尾换行的序列化，
位置、原行摘要、URL、`viewport={width,height}`、state 和非空 reproduce
数组都核验，拒旧名字虚构匹配、重复及未知坐标。不重写 producer 报告。

仅 audit 的终态 `report.json` 可与同目录 `report.candidate.json` 同 inode、
恰好双链接，且两者 bytes/hash/stat 与 durable-final-v2 FINAL 一致。
launcher、首次及末次回读均走这一窄入口；其它 evidence 仍拒硬链接。
实际 `completion.json`、`logs/audit.log`、report hash/rc 和 PNG 必须吻合，
`report.failure.json` 存在即 veto。可编辑回执只绑定材料，不证明用户身份；
最终可信人验拒绝未删除，G3/G6 发布门未放宽。

定向离线复跑，不启动 FEM、浏览器或服务：

```bash
sudo -n -u infra python3 -B /data/dev/opensim-sync-v1/tests/test_release_gate.py --native-audit --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-sync/audit-native-new
```

真实 v2 回读入口为测试文件的 `--audit-readback WORKBENCH_JSON`
及独立 `--workbench-sha256/--source-root/--commit/--out`；从 committed blob
计算 expected，核既有实际 launcher 记录，再实际启动只读 reader 子进程。
这不是重新执行 audit，也不是可信用户来源或完整 workbench 认证。
本轮 v2 回读 261 checks/408 PNG、2 NA、9 个 remaining 保留，
原 workbench 仍 FAIL/rc1（perf G5-IDENTITY），不是组合 PASS。
五源 byte build 不变不等于工具 inventory 不变，主席整合后须重新冻结候选。

S1 三处依赖缺口补验：同一显式 source_root 必须包含完整 SHA pinned 的
`tools/workbench_acceptance.py`，缺件拒绝，无跨 checkout fallback。
只提取其 `validate_audit_payload`、`validate_audit_coverage`、`png_record`
及必要纯断言，不执行模块 import、runner、网络或产品求解入口。
所有依赖均先核 external 祖先、单链接和稳定 inode/bytes，再校验并末次回读。
核成功 POST 非空且与实际 http-solve 文件完整集合相符、payload 五源/solver/
时间/非空 mesh/field、CDP 摘要一致；显式 HTTP503/empty 不混成成功算例。
现有产物没有 raw response body，故只称 parsed payload 与 CDP 摘要绑定，
**不称独立重算响应 SHA**。四视口注入/状态覆盖、所有 PNG IHDR 尺寸、
URL/state、实际 coverage tiles 连续性与完整集合分别核验，不要求扩成八视口。
receipt/log 首次及末次读取均核祖先 symlink；真实原生回读已消费
25 个成功 POST、33 个 HTTP 产物、44 份 coverage 和 408 PNG，
仍 AUTOMATED_PARTIAL/NOT_CERTIFIED。完整候选须重新冻结新增 helper inventory。

测试入口逐项写外置 `progress.log`，另写非终态 `run-start.json`，终态报告
记录执行前后的测试/工具 SHA；任何执行期间漂移直接 FAIL。
错误尺寸在 required viewport/state 前置门拒绝，不能要求后置 PNG 报错；
联动 sync 测试同样核首个 `PARTIAL audit cannot certify G3-accessibility`，
保留 REFUSED、无生产变更和请求字节保护。最终 human 信任分支未获得
可达的完整证据路径，不把前置拒绝冒称该分支已执行。
先前61项回归及6项反例的同一报错断言失败保留，不追认为新版本通过。

## 显式 V2 原件与义务桥接

独立开发树为 `/data/dev/opensim-authorized-gate-bridge-20261010`，
分支为 `codex/authorized-gate-bridge-20261010`，只改 gate/test/本文。
主仓 `97477ba` 和旧候选 `06630d3` 不修改；不部署、合主、代签或调用公网。
v1 保持原行为；v2 ledger schema 为 `opensim-release-evidence-v2`，
在原七字段上只增加 `bridge`，CLI 仍使用 `--source-root/--commit/--ledger/--out`。
v2 evaluator 的实际字节必须等于 candidate committed gate tool。

`bridge` 精确包含 `implementations/native_closure/canonical/provenance`：

- `implementations` 包含 `obligations/trust` 的 `path/sha256`。目前只批准
  `release_gate.BRIDGE_IMPLEMENTATIONS` 中的两个候选路径及精确源码 SHA；
  不沿 sys.path 搜索、不跨树 fallback、不执行其 CLI/顶层导入，
  只装载已审源码中的算法和常量。集成后的路径/版本变化需新审，
  不能由 ledger 自报任意可执行代码或 TRUSTED 状态。
- `native_closure` 是 gate 的 `native_obligations()` 结果的外置 JSON pin。
  首先实际调用原 `read_audit_scope()`：raw source/tool/build、rc、receipt、
  log、PNG、POST、coverage 和失败 sidecar 全部仍须成立。再复用
  `release_obligations.map_native_audit()` 映射；提交的 closure 必须和
  本次重算的完整坐标、原行索引/SHA、原报告 SHA/源绑定逐字等价。
  仅排除 mapper 自身新生成的 `collected_at`；原报告 freshness 不排除。
  `native_obligations()` 是只读 API，不是执行 producer 或签署人证的入口。
- `canonical=null` 表示未提供协议补证；否则必须为 `policy/ledger` 两 pin，
  实际调用原 `release_obligations.evaluate()`。包括能力集合、精确坐标、
  原 FAIL/非0/sidecar veto、缺件、source/tool/commit、重复及补证行 SHA。
  即使结果 `AUTOMATION_SCOPE_CLOSED`，其权限仍仅
  `CANONICAL_AUTOMATED_PROTOCOL_CLOSURE_NOT_NATIVE_SEMANTIC_OR_TRUST_CERTIFICATION`。
  canonical 补证不能关闭 native 没有 predicate 的原义务；原 NOT_TESTED
  和 raw PARTIAL 不重写。原件先验失败时不读取 canonical 来盖住它。
- `provenance` 精确包含 `human/exceptions` 两个 map。key 分别为实际未豁免
  MANUAL rule 和明确使用的 exception ID；value 仅为
  `envelope/revocations` 两 pin。人证 material SHA 是原人证 bytes SHA，
  例外 material SHA 是 `SHA256(encode(approvals()[ID]))`，role 分别为
  `human/user-approval`，candidate 是完整 commit。没有 root 注入、
  TEST_ONLY、editable trust state 或批准捷径。

v2 的 audit gate ledger `status` 必须等于原 producer 的实际 status，
选中的原行必须真实 PASS、有效 pointer/hash/坐标；这些只表示观测贡献，
缺少的全规则/对象/视口/状态覆盖由 closure OPEN 保留，不伪造覆盖。
非 audit 门继续完整原检查，G5 的公网 LAB/code/FEM 不能豁免。
真人材料依然检查设备/组合/owner/目标/原截图/源/期限，明确例外依然
复用 `approvals()` 的精确 scope、ID、owner、expiry 和 removal condition；
材料合规不代表批准来源可信。仅 manual EXCEPTION 保留原 EXCEPTION 状态，
不把未经信任的例外当作已经免除义务。

当前真实生产调用 `release_trust.verify(..., test_only=False)` 返回
`TRUST_UNAVAILABLE:independently_approved_production_root`。即使缺全部
provenance，也实际调用这个原 verifier 并分别记 `MISSING_PROVENANCE`。
算法的 TEST_ONLY Ed25519 正控制不进入 gate 的认证路径。
v2 在原件/材料全读回后返回 `BLOCKED`、真实 CLI rc1、
`bridge.scope=OBSERVATION_NOT_CERTIFICATION`，保留 native/canonical
结果、精确未闭义务、material 与独立 trust 状态；既有 durable finalizer
及实际 launcher 校验协议不变，不能只消费落盘 JSON。

### 已确定的可实施依赖缺口

当前 closure mapper 只接受 native G3 PARTIAL，且仅映射真实 focus 和
当前 canvas 的部分基线坐标。它明确 `object_inventory.complete=false`、
`OPEN_OBLIGATIONS`，未实现完整对象扫描、全部 rule predicate、额外 modes
与 native supplement attestation。AS_PASS 原件合法可读，但该候选尚无
adapter，v2 明确 `NATIVE_G3_ADAPTER_UNSUPPORTED`，不改成 PARTIAL 套接口。
需要 closure owner 扩充 native adapter/实际补证来源，再给精确红绿证据；
canonical CLOSED 或人签一个 blanket gate PASS 都不能代替该实现。

trust 候选无 production root enrollment，只具备 TEST_ONLY crypto。
须由用户和可信渠道明确 signer、角色、domain/policy、材料绑定、
期限/撤销/sequence/freshness 及 root 来源，另由 owner 实施 production
enrollment 和结果协议。当前 gate **尚无 CERTIFIED 成功支路**，
不存在删 sentinel、改 raw PASS 或接受 fake 记录就能上线的承诺。
这次完成的是可复跑的原件/义务/来源桥接，不是完整认证实现。

### 复跑和原件边界

```bash
sudo -n -u infra python3 -B /data/dev/opensim-authorized-gate-bridge-20261010/tests/test_release_gate.py --bridge-core --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-sync/gate-bridge-core-new
sudo -n -u infra python3 -B /data/dev/opensim-authorized-gate-bridge-20261010/tests/test_release_gate.py --bridge --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-sync/gate-bridge-native-new
sudo -n -u infra python3 -B /data/dev/opensim-authorized-gate-bridge-20261010/tests/test_release_gate.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-sync/gate-bridge-full-new
```

核心/完整模块均用真实外置文件/Git/CLI，无 mock 成功判定。
原始 stdout/stderr 按实际 bytes 保存，包括空流 0 字节；报告绑定
实际 child argv/rc、report/stream SHA 与 byte counts，fixture 放本次
输出根的独占子目录。full report 的 test 数依据当次 runner，不硬编码。
source_before/after 含三件写集及两依赖源码，漂移直接 FAIL。
原红控制 `gate-bridge-20261010/red-v1` 的两失败证明旧 raw PASS 矛盾；
后续所有失败独立留存，不追认为绿。

真实 v4 测试只回读原 workbench、audit 与其全部依赖，不重跑 producer。
固定原 workbench SHA 为
`1d9abf07f544d56e8751db480f82daaa29356a33478eba93956033d433526f64`，
原 commit `97477ba9255c1cdb9f8a15d9145a00e61a63188d`、主席 launcher rc3，
原 verdict 仍 `LOCAL_AUTOMATION_PARTIAL_NOT_RELEASE`。v4 源以该 commit
的真实外置干净 Git 快照读回，主树 ignored bytecode 不清理、不忽略门。
测试核每个 mapped closed 原行 SHA、path/report SHA、open 集及 source
前后；这是历史同源适用性，不是新版候选的产品/公网/真人认证。

## Versioned Native Observation Assessment

新独立树 `/data/dev/opensim-authorized-native-assessment-20261011` 基于
bridge `2ff33e191340fc0d9d428e24e197167cd7cc489e`，只写 gate/test/本文。
新模式不是 v2 ledger 的新解释，也不是 sync/app 的准入回执。
v1/v2 的 `--ledger`、原件/义务/可信来源检查及最终闭门保持不变。
assessment CLI 与 `--ledger` 互斥，拒绝 approval 输入和 root 身份：

```bash
sudo -n -u infra python3 -B /data/dev/opensim-authorized-native-assessment-20261011/tools/release_gate.py --source-root /ABSOLUTE/CANDIDATE --commit FULL40 --native-assessment /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-sync/REQUEST.json --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-sync/NEW/assessment.json
```

请求精确三字段：`schema=opensim-release-native-assessment-request-v1`、
`adapter={path,sha256}`、`observation={path,sha256}`。
adapter 只批准 pairV3 clean commit
`0a65b20011fea90ef907d81ba9aeef2cd6692d78` 的固定
`/data/dev/opensim-release-native-adapter-main-v1/tools/release_native_adapter.py`，
SHA `4c998f3d0587a4d09064ced6a76171da37e95cb8bddd24c4d912416fe95b5b00`。
不沿 sys.path 搜索或调用 adapter CLI，只执行已固定摘要的只读算法；
依赖原 reader 的固定 SHA 与完整终态协议不改。路径/源码变化需新审。
S6 pairV3 `ALLOW_FILE_ONLY_FOR_PAIR_V3` 只核文件协议，非原 launcher 认证。

实际首次及末次调用 `read_observation()`，正常结果记真实调用数2；
拒收首读时记1，前置拒收记0。原 reader 核完整 workbench 和四 producer、
durable receipt/log/PNG/raw/dependencies、candidate clean/commit、实际
launcher 观察的固定 argv/rc/stream hash，并拒所有既有 failure sidecar。
只容许原 native audit 固定同 inode 双链接，third link 仍拒收。
这里不发起浏览器/FEM/公网请求，也不重写原 PARTIAL、AS_PASS、NOT_TESTED。
launcher 材料仍是 `NONE_LOCAL_COLLECTOR_OBSERVATION`，没有密码学认证；
文件夹具的 scope 原样保留，不能伪称真实产品运行。

结果 schema 为 `opensim-release-native-assessment-v1`，
scope 为 `NATIVE_OBSERVATION_ASSESSMENT_NOT_RELEASE`。
合法 PARTIAL/AS_PASS 保持 `OPEN_OBLIGATIONS/rc3`；
原 FAIL 保持 `ORIGINAL_PRODUCER_FAILURE_VETO/rc1`，首因保留在
`first_cause`、动作日志及 stderr，不把失败摘要当原件通过。
材料缺失/错版本/错 pin/argv/链接/候选漂移返回
`INVALID_NATIVE_ASSESSMENT/rc2`。首次 reader 拒收时可保留
`native_observation_diagnostic`，明确标为
`REJECTED_UNVALIDATED_DIAGNOSTIC_NOT_AUTHORITY`；验证失败仍是权威首因。
所有支路 `release_verdict=NOT_CERTIFIED`、零 production mutation。
原义务完整保存、`closed_obligations=[]`、human 全 NOT_TESTED、
公网 G5 NOT_TESTED 且不可豁免、production trust 为 TRUST_UNAVAILABLE；
不调用签署器、不补 root enrollment、不生成 CERTIFIED 成功支路。
对象/状态/模式 predicate 和真实人证仍是下一开发包，不能用 blanket
signer 或 canonical CLOSED 来代替。AS_PASS 此处可观察，不声称旧
v2 obligations mapper 已支持 AS_PASS。

报告仍先 durable provisional log、再写报告并精确 readback/fsync；
本模式额外在写报告前后调用原 `verify_observation()`，核原依赖与
candidate 及请求/adapter/observation 的 held inode/bytes，晚期变更拒收。
末次验证或 stdout 写盘失败撤销的仅是本次 exact-own bytes/inode，
未知/已有输出不覆盖。失败结果 `finalization=FAILED/rc1`，原动作首因
与 `evidence_failure` 分开保留；native stdout 使用实际无缓冲写出，
避免退出 flush 把 promised rc1 改成120。最终结果仍要求真实 launcher
rc、stdout report SHA/ID 与日志一致；单份落盘 JSON 不可独立消费。
现有 `verify_process_result()` 的 rc0 发布动作含义未改，
不能拿它或本 assessment 当认证器。

测试入口仅使用原冻结 fixture 和真实外置 Git/文件/子 CLI：

```bash
sudo -n -u infra python3 -B /data/dev/opensim-authorized-native-assessment-20261011/tests/test_release_gate.py --native-assessment-tests --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-sync/native-assessment-20261011/NEW-focused
sudo -n -u infra python3 -B /data/dev/opensim-authorized-native-assessment-20261011/tests/test_release_gate.py --native-assessment-module --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-sync/native-assessment-20261011/NEW-module
```

module 是全部新 assessment 方法加四项旧 bridge 回归，不冒称旧80全集。
文件协议正控制明确 NOT_PRODUCT/NOT_RELEASE，不模拟成功认证。
stdout/stderr 按真实 bytes 保存，0字节不补文本；`/dev/full` 的 stdout
未捕获时明确 `captured=false`，不是空实际 stdout 的证据。
所有真实 child argv/rc/stream hash、原 fixture child、三件写集和五份
adapter/reader/fixture/doc 依赖的 source before/after 都在新报告里。
初红 `red-v1` 的 ledger 缺模式及 `focused-v2` 的真实 stdout120/错误前置
assert 保留不追认。测试计数以本次 runner 为准，不把 fixture 常量当覆盖量。

## 固定 Release API 与同步边界

只读对接第1席：
`prepare(root, commit, evidence, bundle)` 精确返回
`state=BUNDLE_PREPARED/scope=SOURCE_AND_NUMERICS_ONLY/bundle/
manifest_sha256/commit/page_build`。
`validate_bundle(bundle, digest, commit)`；
manifest 精确字段 `schema/commit/source_epoch/source_sha256/page_build/files`。
source_epoch 必须等于该 commit 时间，导出只有五源加 requirements，
payload 只有 `source/...`、numerics、build HTML/page-check；
`Layout.release()` 必须等于 `/opt/opensim/releases/<commit>-<manifest_sha256>`。

本席调用前核 API 文件 hash、函数签名、SOURCES/EXPORTS/PAYLOAD/
CONTROLLER；不用旧 bytecode、不以未核 API 代替 gate。
第1席 prepare bundle 固定 `seat-release`，gate/sync 报告固定 `seat-sync`。
调用前先核 API 的实际 ARTIFACTS 精确等于固定 allowed root，再核 bundle
路径及物理落点；错误 `seat-sync` bundle、宽根或未知 API root 均拒绝，
**不篡改 ARTIFACTS 或换假目标**。
真实离线 prepare/validate_bundle 成功只证明 SOURCE_AND_NUMERICS_ONLY，
可信完整门未开时，不声称 gate→prepare 发布链已完成。
原v1测试只读固定夹具 bundle；新候选保f32d修正，原pinned API只prepare
新的自有readonly bundle，并核完整manifest/hash、错误pin和0644负控。

desired request schema 为 `opensim-desired-release-v1`，精确字段：
`schema/request_id/action/commit/ledger/gate_receipt/approval_pin/bundle/
manifest_sha256/source_epoch/release/before_receipt`。
只有 `action=INSTALL_FIXED_RELEASE`，调用者必须独立传整个请求 SHA256。
unknown desired、宽树 action、错 gate receipt、证据漂移一律拒绝；
gate receipt 不能仅自称 CERTIFIED，必须重新运行同版严格门。
`before_receipt=null` 只表示显式首装，不自动推断。
共享 DNS APPLIED/authorized/CREATE_ACK 不在请求 schema，
不读它们，不拿共享可编辑 receipt 授权删除或回退生产对象。

没有可信 narrow root controller 协议和用户授权前，
`--apply` 永久 REFUSED，不执行任何 privileged command。
本件没有生产 revert/恢复实证、后台发布触发、unit 安装或授权；
必须由主席单独完成审查、最小授权、可信 receipt 和真实恢复验收后
另行开发，不能靠“工具已提交”认定上线。

## 可复跑测试

### 结果落盘终态

两个 CLI 在动作前拒绝已有 `.json` 或配对 `.log` 输出，不覆盖旧证据。
日志先标 `PROVISIONAL`；报告包含 invocation ID、日志 path/hash 和
`FINAL_PROCESS_RC_REQUIRED`，它仍须与 launcher 实际进程 rc、
stdout 最终 marker/report hash 一致才可接纳。
`verify_process_result()` 强制接收 launcher 的实际 `CompletedProcess`，
逐项比对 argv、rc、stdout/stderr bytes 和外置 hash，
只核进程和产物绑定，**不验证用户身份或批准**。
可编辑 observation JSON 也不能自证进程来源，调用者须保留实际
`subprocess.run()` 返回值及原始日志，不能靠文件中的 rc 字面量放行。

日志/实际 stdout ENOSPC、报告写完后的 fsync/readback/输出失败均返回 1；
失败报告保留 `action_result`、原 `failure` 和独立 `evidence_failure`。
撤销仅针对本次 O_EXCL 创建、相同 dev/inode、逐字节仍一致的候选报告；
外部替换或原有同字节报告不撤销、不覆盖。撤销失败明确报错，
失败进程的任何文件不得单独作为成功证据。日志永久 provisional，
不制造“落盘写成功等于进程/发布成功”的另一套终态。
失败时先向 stderr 输出证据故障及原首因；stdout 再次故障也明确报出，
不让失败 marker 写入失败吞掉原首因。

旧目录冲突拒绝证据保留。用户明确允许真实 API 的固定 `seat-release`
bundle 后，离线集成从真实主仓提交的外置干净 Git 快照导出，分别调用当前主仓和
peer 的 pinned prepare，回读 validate_bundle、manifest/source_epoch、
五源 byte build、edited_at、数值 bytes、实际 render 子进程 rc，
并核调用前后 source/API 不变。launcher rc/原始 stdout/stderr、最终
报告与日志 hash 都在 `seat-sync`。这不是假接口，也不改 peer 常量；
实际操作仍无安装、权限授权、服务运行或生产发布。
主仓既有 ignored bytecode 也受 strict clean 门约束，不删除他人缓存、
不放宽门；快照以本地 Git clone 保留同一真实 commit，无网络下载，
并核原树 ignored 状态和所有导出源码前后不变。

每次使用新的输出目录，不覆盖历史：

```bash
sudo -n -u infra python3 -B /data/dev/opensim-sync-v1/tests/test_release_gate.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-sync/gate-tests-rerun
sudo -n -u infra python3 -B /data/dev/opensim-sync-v1/tests/test_sync_release.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-sync/sync-tests-rerun
sudo -n -u infra python3 -B /data/dev/opensim-sync-v1/tests/test_sync_release.py --scan --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-sync/scan-rerun.json
```

测试用真实外置文件、真实 Git 和 CLI 子进程，不 mock 判据；
负例断言首因而非仅 rc=1，正控制独立验证身份绑定/性能/真实 API bundle。
fixture 内容明确不是人验/公网/性能报告，不能用于真实 ledger。
判定行包含实测 tests/failures/errors/rc/时刻，完整 traceback 留外置日志。
扫描只七个点名文件，读取既有 token 仅在内存检测字面泄漏，
不输出凭据；再审阅 diff 并仅按七个 pathspec 提交，不 push。
