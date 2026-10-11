# 持续发布与真实公网采集

状态：DEVELOPMENT_ONLY / PRODUCTION_BLOCKED / NOT_CERTIFIED。
`APPROVED_EXECUTOR=None`，本工具不会部署、restart、延长验证租约或安装timer。
完整gate、真实human与受审owner executor缺失时仍BLOCKED；LOCAL_TEST不升认证。

## Desired V2 原件消费

当前 main 集成候选 `/data/dev/opensim-s5r-consumer-main-integration-20261011`
基于 `db725b0d6282e6fbfd90456e206174546ab25131`，仅本工具、对应测试和本文三件。
三件来源为 `dad726defbd2c8306426ca2dc4affc2ec56f95b2`；tool 保留原 blob，
main 五源与 sibling sync/gate 不覆盖、不动态改 pin。原 consumer 树只读保留。
main 没有 `deploy/opensim-sync.timer`，本集成不补 timer、不改 service 或 executor。
有限采集使用 main sibling 基础 ABI；完整 closure 仍使用下面原固定入口与依赖。
v1保留原receipt协议与依赖，不把v2补成v1。v2精确移除ledger/gate_receipt，
只接pinned closure_request；额外认证布尔值、混合字段、缺pin均拒绝。
固定S4 clean `e4f12162a3c09d819622b66f8722f4c74cde6c23`的原plan API，
路径 `/data/dev/opensim-s4-sync-closure-20261011/tools`，sync SHA
`c1b483654bee51984ebf00b79faabc4f014e75843299f3b06a8eaf94aeecbd14`，
gate SHA `55dc52edc95115e5077add86dc4d48446d48567054e4a8bab7ad69e587653ae3`，
obligations SHA `36f6912c6bb98f0c6e338a73a478891c0d2528a41d6700dc47c5ff64e62c509a`。
原trust/bridge固定三依赖由CLOSURE_LIBRARIES显式绑定，无sys.path fallback或动态source。

`--inspect-candidate`实际调用原plan，原plan单次启动原gate closure CLI并完整复验。
本工具保存owner真实argv/rc/raw streams/report/log和材料/source前后inode/hash；
`closure_execution={owner,inputs,sources,outputs}`只是观察，不是另一份可编辑认证回执。
缺真人、production provenance、OPEN/FAIL仍BLOCKED/NOT_CERTIFIED，原native PARTIAL
不改。成功路径仍额外要求原完整CERTIFIED，APPROVED_EXCEPTION尚不接纳，
且必须原bundle/API校验完成；当前没有受审真实可信成功材料，不能称已联通生产。
工具不运行测量producer、不调用生产、不创建human证据、不修改S4冻结。

plan绑定只排除每次collected_at；每次原closure输出不同，分别保存为terminal依赖。
原request、递归public材料、截图、目录inventory、source和raw输出均重核。
audit只允许原owner校验的report.json/report.candidate.json同inode恰双链接，
第三链接或普通材料链接拒绝。报告/log/空流按原actual bytes读取；非空stdout不当JSON。
inspect在输出前后重新核原件，晚inode/hash/sidecar/输出/写盘失败返回非零并保
report.failure.json；原首因与evidence_failure/closure_readback_secondary分开记录。
失败侧档也写不成时stderr明确报告。单份report.json不替launcher实际rc。

只读入口：原四显式参数source-root/desired/desired-sha256/out不变；out位于固定
continuous-release外根且必须新建，父级缺失可创建，既有输出或symlink仍拒绝。
固定source修改需新候选审查，不能由请求自报pin绕过；原APPROVED_EXECUTOR仍None。

```bash
sudo -n -u infra python3 -I -B /data/dev/opensim-s5r-consumer-main-integration-20261011/tests/test_continuous_release.py --select test_finite_collector_main_abi_and_real_cli --select test_finite_collector_offline_failure_and_late_veto --out /hf3fs-jd/data/tmp/opensim-fluid-v1/authorized-s5r-consumer-main-integration-20261011/review-new-unique
```

本集成仅上述两个 bounded selector：真实 nonroot collector CLI、原 public_gate
CLI/report/log writer及原 capture/storage；私有 seam 将 producer 映射到固定本地
文件夹具，socket/DNS 被拒绝，不启动 ApplicationServer、不访问公网或 browser。
夹具合成 LOCAL_TEST 协议数据，不证明真实 FEM 或公网 PASS，不能用于发布。
覆盖明确 main sibling/path/SHA、timer 缺席、executor None、raw streams/actual rc，
以及缺源/坏 source pin/错 commit/producer rc7/输出后同字节换 inode 的 late veto。
测试只在这两个 selector 下显式使用有限 inventory，不无条件读取缺失 timer；
原 full/closure/loopback selectors 留作历史，未在本 main 集成验证，不得据旧绿升格。

原 consumer 的 closure-module是七个新增v2方法加四public CLI和两旧owner绑定回归，不是全部历史
模块或680测试。可用--select精确单方法，复用现有run_suite，无重复runner。
真实nonroot/closedstdin原S4文件Git夹具到达真实gate BLOCKED；夹具明确非human/
非production。边界负控实际/dev/full、写完报告后注入fsync失败、输出后同字节换
gate inode及新增measured sidecar；未知文件保留。原public正控仍来自真实loopback
ApplicationServer与原public_gate CLI，不合成认证PASS。全部source前后摘要另存
target-source.json，原失败目录保留；依当次launcher实际rc/count验收，不追认旧freeze。

## 固定只读采集

`--collect-public`调用指定候选中的原`tools/public_gate.py`固定CLI，仅
`https://opensim.ai4everyone.top`，正常TLS，无任意URL/command/root参数。
显式source-root必须对应当前候选，source-set SHA是五个`gate.SOURCES`相对路径
到完整SHA的对象，经`gate.encode`（ASCII、sort_keys、indent2、尾换行）后的SHA。
public_gate协议另固定SHA
`5fd9ed850e68a39cdfb303655cb1e6f1727d060b8ae9e520cf82b98d7c9b66e3`，不动态fallback。

```bash
sudo -n -u infra python3 -I -B /data/dev/opensim-s5r-consumer-main-integration-20261011/tools/continuous_release.py --collect-public --source-root /opt/opensim-sync/candidate --expect-source-set-sha256 REVIEWED_64HEX --expect-commit REVIEWED_40HEX --out /hf3fs-jd/data/tmp/opensim-fluid-v1/continuous-release/public-new-unique-directory
```

此命令是可复用只读入口示例，本轮没有对公网执行。父目录须预先存在，out须新建。
生产candidate路径由原`--once`固定；CLI显式选择本地候选不是生产授权。
依赖复用固定infra capture/storage，前后核实际bytes和inode/metadata，不复制runner：

- invoke_runtime：`/data/dev/infra/wt-opensim-authorized-invoke-20261010/ops/opensim-deploy/invoke_runtime.py`，SHA `a09d15d95a940bb1ce1a0b7cc4c9fb314204157b9027913346b4e90df5a9377a`。
- storage：`/data/dev/infra/ops/opensim-deploy/inspect_release.py`，SHA `ec6a9b3754793b648e72a64a74baa15e1e8bac9f188730115e5c6d049a8c36de`。

真实stdout是verdict-line而非JSON。采集分开绑定raw stdout SHA/bytes、raw JSON
SHA/inode、canonical JSON SHA、同stem原log、stderr/hash/bytes、actual child rc与
固定argv。原report必须位于本次producer目录，准确一json一log，原log必须等于
verdict-line，stdout准确等于原CLI输出（FAIL含First cause第二行）。拒未知文件、
sidecar、link、目录/原件/source/hash/inode漂移；空stderr是明确真实零bytes。
原writer的report/log另执行fsync，不以close或rc0代持久证据。

双pipe drain到真实wait，单次执行，不retry、不kill。失败仍留capture.json、原streams、
实际rc/首因/secondary与source前后；stdout过大只保摘要和bounded capture，不当完整。
每轮新out内有START.json/capture.json/stdout.bin/stderr.bin/producer原report和log/
execution.log/collection.json。collection内部exit_code表示采集结果，不是外层launcher
终态；成功stdout明确PROVISIONAL，调用者须亲poll真实rc并读回原件与无failure sidecar。
输出/最后readback失败返回非零并留report.failure.json；写盘/fsync失败保首因并留
collection.failure.json，sidecar也失败时stderr明示，不追认已有success字节。

## 原事务边界

`--once`/`--init-state`不接受路径、source、command、root覆写。原nonroot0700状态根
`/var/lib/opensim-sync/continuous`和owned0600请求原协议不变，完整sync/gate重新验。
`--inspect-candidate`仍只VALIDATED_NOT_DEPLOYED，不替human/trust。
不存在受审executor时零deploy、BLOCKED，不能把采集四checks变成完整发布门PASS。

原UUID intent、单owner内核锁、严格plan比较（只剔collected_at）、exact provisional
owner CAS与durable admission-pending保持。stdout同字节换inode和异字节owner替换
拒绝；fsync/unlink/stop/veto连失败仍pending关闭，不删除他人替换。
只在准确owned stop后尝试exact revert；stop失败不继续revert，失败不自动重放。
本次将collector source、raw report/log/streams、目录inventory与collection终态
加入原closure依赖；最终输出后与下次准入都重新读取，不重基线、不只采时看一次。
pending最后移除不fsync的保守崩溃策略保持，持久崩溃恢复仍未实现。

## 可机核测试

以下为原 collector 历史 loopback 测试入口，不是本 main 集成的验收命令；
本片没有网络授权，不运行此组或原 full suite。当前有限入口见上方两个 selector。

```bash
sudo -n -u infra python3 -I -B /data/dev/opensim-s5-public-collector-20261011/tests/test_continuous_release.py --focused public-cli --out /hf3fs-jd/data/tmp/opensim-fluid-v1/authorized-s5-public-collector-tests-20261011/chair-new-focused
```

测试真实nonroot子进程运行原`public_gate.main`、原report/log writer和原
ApplicationServer/FEM。只在Python私有runner seam映射loopback transport，不改CLI
协议、不合成PASS。固定公网入口无此选项。专项覆盖原verdict行、stderr/rc7/pin拒绝、
真实pipe read/实际wait、/dev/full、写盘后fsync、输出后同字节inode、独立原件/sidecar。
原模块full另含owner/CAS/pending/recovery负控，不重旧680/数字全集。
历史red与每次夹具错误保持原目录，修后新目录，不追认旧full或旧freeze。

真人触屏与桌面/移动读屏仍NOT_TESTED，trust/human不能代签，公网此次NOT_TESTED。
采集可用不等于持久executor完成；timer liveness或1800s窗口不能充长期授权。
