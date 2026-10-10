# 工作台组合验收

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
  4 视口、coverage/gates。PARTIAL/NOT_TESTED 逐条保留。
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
