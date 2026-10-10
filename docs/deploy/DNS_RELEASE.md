# OpenSim DNS 发布接口

本工具只开发及离线测试，不代表取得生产授权或已发布。只操作 DNSPod
`ai4everyone.top` 中 `opensim` 的默认线路 A=`43.135.70.136`、
TTL=600、ENABLE；无域名、线路、值或 TTL 扩面参数，不修改 infra。
不承担证书、edge、服务安装或公网发布门。

## 集成接口

从固定 release 工作树运行 `tools/dns_release.py`，命令身份为
`sudo -n -u infra python3 -B`。必选参数：

| 参数 | 约定 |
| --- | --- |
| 首个位置参数 | `dryrun`、`apply` 或 `revert` |
| `--release-id` | 同一次发布的稳定标识，最多 80 个字母/数字/点/下划线/短横 |
| `--ledger` | 绝对路径，必须在 `/hf3fs-jd/data/tmp/opensim-fluid-v1/` 下 |
| `--authorized` | 真实 apply/revert 前需另获相应生产授权；此旗仅确认，不创造授权 |
| `--fixture-dir` | 仅离线测试；仓内测试文件作为真实子进程签名器，无 SSH/网络 |

例如只读计划（仍会现读凭据和 DNSPod，不在本开发席执行）：

```bash
sudo -n -u infra python3 -B /data/dev/opensim-dns-v1/tools/dns_release.py dryrun --release-id opensim-v1 --ledger /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-dns/release-v1.jsonl
```

主席可用同一 release-id/ledger 改位置参数为 `apply` 或 `revert`，
但只有取得 DNS 新建/本次记录删除授权后才能附 `--authorized`。
不得给 `NO_CHANGE` 虚构新建或回滚凭证。

stdout 恰一份 JSON receipt；成功 rc=0，失败 rc=2。
`outcome` 为 `PLANNED_CREATE`、`NO_CHANGE`、`APPLIED`、`REVERTED`、
`NO_CHANGE_REVERT` 或 `FAILED`。必带 `release_id`、`target`、
`before`、`after`、`created_record_id`、`verified_record_id` 和 `ledger`。
`backend` 固定绑定 production 或实际外置 fixture 路径；离线账本不可用于
生产，也不可跨夹具挪用。
`null` 明确表示未采集/没有本次新建 ID，不用空列表伪造“验过不存在”。
before/after 只保存 OpenSim 非 secret 字段和 RecordId；
失败有固定分类、动作、子进程 rc/本地 errno，无原始输出。
下游必须核 `outcome`，不能仅凭 rc=0 当真写。

## 身份与严格门

生产固定用 `/home/infra/.ssh/id_ed25519`、infra SSH 到
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
新增前再完整现读；CreateRecord 必有新整数 RecordId，复核整个 zone
仅增加本次精确记录后才报 `APPLIED`。

## 账本与撤回

JSONL 同 release 追加，写前 intent、API ack、复核、终态均 fsync；
失败不覆盖历史。schema/release/backend/target 不匹配或已截断的账本
保持原字节，不追加修补；此类初始化失败仅 stdout 安全 receipt，
主席须将该 receipt 留入自身发布总账。使用固定 OpenSim 外置独占 lease 防本机并发执行，
不自动抢 stale lease、不自动重试、不把超时当未写。
崩溃后须主席核实无活动操作及 DNS/账本状态，单独获准恢复 lease；
不同控制机仍须共享入口 owner 串行协调，DNSPod API 没有 CAS，
两次读取不能消除最后一次读取与写入之间的跨机竞态。

revert 必须由本账本唯一 CREATE_INTENT/CREATE_ACK 证明本次新建 ID。
清单中仅一个 OpenSim 且 ID 与全部字段仍相同，删除前再次完整现读
无漂移才 DeleteRecord；删除后完整清单确认本 ID/目标消失、其它记录
不变才报 `REVERTED`。不撤现存记录、替换 ID 或冲突/漂移记录。
已确认删除的 ID 再次现读确实不存在可报 `NO_CHANGE_REVERT`。
CreateRecord 结果未知、缺 RecordId/RequestId、截断或缺失账本一律
停止并人工裁决，不能猜 ID 或自动重建。成功 CREATE_ACK 后复核失败
仍保留 ID；之后 revert 仍须现读同 ID/同字段完整验证。

## 离线验收

```bash
sudo -n -u infra python3 -B /data/dev/opensim-dns-v1/tests/test_dns_release.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-dns/acceptance-v1
```

新目录独占创建，报告与所有真实假签名器子进程调用/文件状态/账本
外置保存。夹具代码固定在本测试文件，子进程内禁 socket，
严格核 signer argv、固定 DNS payload 与离线凭据。
覆盖缺清单/类型/数量/上限、重复/冲突/状态、API 失败、
新建 ack 后读失败、未知结果不重建、revert 字段/ID 漂移及删除无效。
报告 `OFFLINE_SUBPROCESS_ONLY`，不冒充生产 DNS 或公网验收。
