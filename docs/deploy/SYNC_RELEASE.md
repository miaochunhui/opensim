# 严格发布门与显式同步请求

## 当前判定

本件只交付离线准入工具与负例，**不是上线或自动发布完成**。
`release_gate.py` 当前必定保留 `BLOCKED / NOT_CERTIFIED`：
实际真人验收及可信验收来源尚未接入；用户没有批准例外。
自动 `AUTOMATED_PASS`、G3/G6 `PARTIAL`、可编辑 JSON 中的
`approved_by=user` 或 `REAL_DEVICE_HUMAN/PASS` 都不能升格。
`sync_release.py --apply` 当前返回 `REFUSED`，不调用 root、sudo、
systemctl、DNS、证书或 edge 工具。controller 的文件存在也不代表授权。

本席七件独占文件之外不写盘。所有新夹具、报告、日志都在
`/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-sync/`，旧失败保留。
两个 `opensim-sync.service/.path` 仅为**未安装草案**，没有 enabled
发布链、postcommit hook、全树同步器或宽权限 broker。
固定输出名和缺失的 request-hash 注入入口亦未运营化；不能直接安装草案。

## 准入契约

`release_gate.py` 先核完整 40 位 HEAD、全树 clean（包含未跟踪、
被忽略文件、skip-worktree/assume-unchanged 状态），再从 committed blob
逐字节核当前文件。运行源集合严格为：

- `tools/render_page.py`、`web/app.js`、`web/app.css`、`solver.py`、`server.py`。
- 工具摘要覆盖 acceptance、numerics、render、browser、audit、performance、
  public_gate、release_app、release_gate、sync_release，不省略缺件。

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
第1席 prepare 当前限 `seat-release`，本席限 `seat-sync`：
准备输出冲突时明确 REFUSED，**不篡改 ARTIFACTS 或换假目标**。
接口未改且可信完整门未开时，不声称 gate→prepare 链已完成。
测试只读其真实固定夹具 bundle，核完整 manifest/文件 hash 和错误 pin。

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
