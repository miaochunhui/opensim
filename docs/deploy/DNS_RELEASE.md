# OpenSim DNS 发布接口

本工具只开发及离线测试，不代表取得生产授权或已发布。
**生产 apply/revert 已无条件关闭，包含带 `--authorized` 的调用，rc=2；
仅保留 production 只读 dryrun 和 fixture mutation。**
固定目标为 DNSPod
`ai4everyone.top` 中 `opensim` 的默认线路 A=`43.135.70.136`、
TTL=600、ENABLE；无域名、线路、值或 TTL 扩面参数，不修改 infra。
不承担证书、edge、服务安装或公网发布门。

不可信查询结果仅在内存比较；证据快照只保留字段完全匹配固定目标的
记录。冲突或异常文本不进入 receipt/JSONL，失败仍保留固定原因码和
已获创建确认的 RecordId，不因此允许盲目回退。

## 集成接口

从固定 release 工作树运行 `tools/dns_release.py`，命令身份为
`sudo -n -u infra python3 -B`。必选参数：

| 参数 | 约定 |
| --- | --- |
| 首个位置参数 | `dryrun`、`apply` 或 `revert` |
| `--release-id` | 同一次发布的稳定标识，最多 80 个字母/数字/点/下划线/短横 |
| `--ledger` | 绝对路径，必须在 `/hf3fs-jd/data/tmp/opensim-fluid-v1/` 下 |
| `--authorized` | 仅保留兼容参数；绝不能自授权限或放行 production apply/revert |
| `--fixture-dir` | 仅离线测试；仓内测试文件作为真实子进程签名器，无 SSH/网络 |

例如只读计划（仍会现读凭据和 DNSPod，不在本开发席执行）：

```bash
sudo -n -u infra python3 -B /data/dev/opensim-dns-v1/tools/dns_release.py dryrun --release-id opensim-v1 --ledger /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-dns/release-v1.jsonl
```

不能把位置参数改为生产 `apply` 或 `revert` 来完成发布。
两者在读账本、取 lease、SSH、凭据读取或 signer 调用前失败，
固定首因为 `production_mutation_disabled_missing_trusted_controller`；
不会追加旧账本，before/after/created_record_id 均为 null。
API 层亦阻止 production CreateRecord/DeleteRecord 等所有非查询动作。
本轮不读取真实凭据，不安装 controller，不改凭据、身份或权限。
不得给 `NO_CHANGE` 虚构新建或回滚凭证。

stdout 恰一份 JSON receipt；成功 rc=0，失败 rc=2。
`outcome` 为 `PLANNED_CREATE`、`NO_CHANGE`、`APPLIED`、`REVERTED`、
`NO_CHANGE_REVERT` 或 `FAILED`。必带 `release_id`、`target`、
`before`、`after`、`created_record_id`、`verified_record_id` 和 `ledger`。
`backend` 仅是来源标签，不是可信证明。修改标签、伪造 CREATE_ACK、
自填 RecordId 或传入动态 JSON 均不能证明生产所有权或授权。
production dryrun 始终不从账本获取 created_record_id；
fixture 则绑定实际外置夹具路径，不可跨夹具挪用。
`null` 明确表示未采集/没有本次新建 ID，不用空列表伪造“验过不存在”。
before/after 只保存 OpenSim 非 secret 字段和 RecordId；
失败有固定分类、动作、子进程 rc/本地 errno，无原始输出。
下游必须核 `outcome`，不能仅凭 rc=0 当真写。

## 身份与严格门

production dryrun 的既有只读链固定用 `/home/infra/.ssh/id_ed25519`、infra SSH 到
`192.168.1.183`，远端 `sudo -n grep` 只读
`/root/.acme.sh/account.conf` 的腾讯云凭据键。
仅进程环境 `TENCENTCLOUD_SECRET_ID/KEY` 交给
`/data/dev/infra/ops/tencent-cloud/tc_api.py`；
沿用该 signer 的 `dnspod ACTION 2021-03-23 -d -` 协议。
不输出凭据、环境、认证 header、签名器/SSH 原始 stdout/stderr，
不允许 signer CSV 回落、不改 SSH 配置/host key/login/权限。
尚未实测线上身份链；若既有 host key 验证失败则停止，不禁验证绕过。

每次查询整个 zone，Offset=0、Limit=3000。必须有对象 response、
UUID RequestId、数组 RecordList、整数 TotalCount 且等于数组长度；
到上限、缺件、类型错误、重复 JSON key/RecordId、无效记录状态均拒绝。
OpenSim 任何多记录或 Type/Value/Line/LineId/TTL/Status/Name 差异拒绝。
唯一精确既有记录二次现读确认只报 `NO_CHANGE`，不修改或取得所有权。
仅 fixture 新增前再完整现读；CreateRecord 必有新整数 RecordId，复核整个 zone
仅增加本次精确记录后才报 `APPLIED`。

## 账本与撤回

外置 JSONL 是**不可信的审计证据**，不是生产所有权、授权或 receipt 真伪源。
共享 666 文件、合法 schema、文件 mode/uid、摘要和 backend 标签都不能
单独证明某个 RecordId 是本次创建。不得通过 chmod “修复授权”。
JSONL 同 release 追加，fixture 写前 intent、API ack、复核、终态均 fsync；
失败不覆盖历史。schema/release/backend/target 不匹配或已截断的账本
保持原字节，不追加修补；此类初始化失败仅 stdout 安全 receipt，
主席须将该 receipt 留入自身发布总账。
read/append 均逐级以 O_DIRECTORY+O_NOFOLLOW 打开父目录，
以 dir_fd+O_NOFOLLOW 打开叶文件，核 regular 与 nlink=1，
在读前/后及追加前/后核父子 inode 绑定、大小、mtime/ctime，
追加前重读原字节；首次创建使用 O_EXCL，禁止截断或修复旧文件。
symlink（父或子）、硬链接、路径替换、可观测读写竞态拒绝并保留首因/errno。
唯一固定存储别名 `/hf3fs-jd` 必须仍为 root-owned symlink 指向
`/3fs/research3fs`；将其转换为物理路径后，所有组件继续逐级拒绝 symlink。
这只是既定外置落点映射，不是授权链。不允许其它动态别名。
这些检查不是共享可写文件系统的攻击者隔离；无法把用户态检查和写入之间
的最后竞态窗口称为消除，必须由后述受管存储/唯一 controller 承担生产安全。
使用固定 OpenSim 外置独占 lease 防本机 production dryrun 并发执行，
fixture lease 则放在对应夹具目录，离线验收不占共享生产锁；
不自动抢 stale lease、不自动重试、不把超时当未写。
崩溃后须主席核实无活动操作及 DNS/账本状态，单独获准恢复 lease；
不同控制机仍须共享入口 owner 串行协调，DNSPod API 没有 CAS，
两次读取不能消除最后一次读取与写入之间的跨机竞态。

以下撤回行为**仅适用于 fixture**，不是 production ownership 证明：
revert 使用本夹具账本唯一 CREATE_INTENT/CREATE_ACK 跟踪模拟新建 ID。
清单中仅一个 OpenSim 且 ID 与全部字段仍相同，删除前再次完整现读
无漂移才 DeleteRecord；删除后完整清单确认本 ID/目标消失、其它记录
不变才报 `REVERTED`。不撤现存记录、替换 ID 或冲突/漂移记录。
已确认删除的 ID 再次现读确实不存在可报 `NO_CHANGE_REVERT`。
CreateRecord 结果未知、缺 RecordId/RequestId、截断或缺失账本一律
停止并人工裁决，不能猜 ID 或自动重建。成功 CREATE_ACK 后复核失败
仍保留 ID；之后 revert 仍须现读同 ID/同字段完整验证。

## DNS-OWNERSHIP-01 未完成

本补丁只完成最小 fail-closed 阻断和离线 I/O 加固，ISSUE
`DNS-OWNERSHIP-01` 仍 OPEN。真正完成需要主席另获明确最小 bootstrap
授权后实现并独立验证以下设计，本工具没有安装或假装已有窄 broker：

- root 受管、调用者不可修改的所有权存储，绑定固定 zone/target、
  release identity、真实 CreateRecord RequestId/RecordId、intent/ack 和复核状态；
  外置账本只保存该存储的审计副本，不能导入自填所有权。
- root-owned 窄 controller 与受管依赖、可信调用入口及显式最小权限边界；
  只容许固定 OpenSim 参数，杜绝任意 action/payload/signer/path 执行，
  凭据不下发给调用者，不把 repo checkout 或动态 JSON 当可信 controller。
- controller 内串行执行授权、现读、intent、调用、ack 和复核，
  创建结果未知须停机人工裁决，不接受调用者声称“拥有 ID”后删除。
  revert 必须核受管所有权和现读全字段，不能回收既有 RecordId9001。
- 可信 receipt 的来源和完整性由 controller/受管记录核验，拒绝重放、
  伪造、backend 重标记、篡改及调用者自签。不能用未经验证动态 JSON
  或“存在 root 文件”证明 receipt、controller 或 bootstrap 已可信。
- 明确 bootstrap 目标、写集、授权人、恢复路径及撤销方式后才能实施；
  独立负例验证共享账本/目录攻击、controller/receipt 缺失与篡改均 nonzero，
  缺任何一项继续关闭生产 mutation。只有主席完成实测与授权才能拆除此阻断。

## 离线验收

```bash
sudo -n -u infra python3 -B /data/dev/opensim-dns-v1/tests/test_dns_release.py --all --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-dns/security-fix-unique/full-v3
```

新目录独占创建，报告与所有真实假签名器子进程调用/文件状态/账本
外置保存；复验必须换新目录，不覆盖上述已运行目录。
夹具代码固定在本测试文件，子进程内禁 socket，
严格核 signer argv、固定 DNS payload 与离线凭据。
覆盖缺清单/类型/数量/上限、重复/冲突/状态、API 失败、
新建 ack 后读失败、未知结果不重建、revert 字段/ID 漂移及删除无效。
新增真实外置文件/子进程负例覆盖伪造 production ledger、
fixture backend 重标记、带/不带 authorized 仍拒绝且不 SSH/不 signer/不 Delete；
production dryrun 的真实离线假 transport 只 Describe、不取得所有权；
hardlink 在 read 或 read 后引入均不 append，父子 symlink、FIFO、
目录/文件替换及可控 TOCTOU 均拒绝；追加失败保留 API 首因与证据 errno。
3FS 创建具名 FIFO 返回 errno 38，首轮失败证据保留；non-regular
控制改用真实目录和真实 pipe fd，不将不支持的文件类型记为通过。
加 `--all` 可复跑全仓 unittest，DNS 子进程证据仍放同一新目录。
`production-probe.json`、`hardlink-proof.json`、`firstcause-proof.json`、
processes.json 和原始 calls/state/ledger 构成盘面证据。
报告 `REAL_EXTERNAL_FS_OFFLINE_SUBPROCESS_AND_PRODUCTION_FAKE_TRANSPORT`，
不冒充真实生产 controller、DNS 或公网验收。
点名扫描仅三件源码/文档，保留合成 secret 命中/脱敏负对照，
不读真实凭据或 `/opt/notify/relay.token`。旧红证据原目录保留不覆盖，
安全席自己的 `tests/test_release_security.py` 保持只读独立复验。
