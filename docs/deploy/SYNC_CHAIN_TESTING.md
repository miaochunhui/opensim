# Sync Prepare Pin Regression

本件只验证严格门之后的 prepare handoff，不是完整自动发布链验收。
不运行生产 apply/bootstrap，也不接入批准来源、部署触发或放宽 G1..G6。

## 独立缺口

`prepare_guard` 原来在 evaluate 后重读可变 ledger，未绑定被门验过的
ledger 字节，也未比对产出 bundle 的 numerics hash 与门验过的引用。
现在读取一次原 ledger，核 evaluate 返回的 ledger hash，prepare 前后
核字节未漂移，并核 bundle numerics hash；失败保留外置候选，不清理。

可达性限制：现役 `gate.evaluate` 最终仍因可信人验核验器缺失抛错，
本缺口目前不是已可达的生产绕门。这里加固未来获准 gate 返回后的
交接边界；真实 API 联跑没有完成，四例绿仅代表局部回归通过。
未来 gate 成功返回必须提供与本次读取一致的 `ledger_sha256`，
缺该字段会明确失败，不能借开发夹具推开现有闭门。

新增 `PreparePinTests` 四例：不变正控制、门后 ledger 漂移拒绝调用
prepare、prepare 期间 ledger 漂移拒绝返回候选、bundle numerics
摘要不同拒绝返回候选。gate/API 返回值是显式注入的单位测试边界，
文件和子进程是真实的；不能据此宣称真实 gate→prepare 集成通过。
它不使用 S1 的 `Fixture`，不运行旧 inventory，也不运行 workbench。

独立分支从 `67348532388b9609d6cddd3cf5b5d7d6765487f8` 开始。
修前四例为 1 正控制通过、3 负例失败，真实 launcher rc=1；
证据在 `resume-s6-sync-chain-20261010/red-v1/`，失败不覆盖。
下列入口复用既有 `run_suite`，保留真实子进程 rc/stdout/stderr、
测试完成标记、报告 hash 和四份源码前后摘要。输出必须用新目录：

```bash
sudo -n -u infra python3 -B /data/dev/opensim-resume-sync-chain-20261010/tests/test_sync_release.py --prepare-pin-only --out /hf3fs-jd/data/tmp/opensim-fluid-v1/resume-s6-sync-chain-20261010/focused-v1
```

## 未验与外部条件

完整生产周期失败/恢复、公网不同步阻止最终交付、实际固定 release
后台触发均未在本席实跑。当前 `sync_release --apply` 仍 REFUSED；
controller 文件存在不等于可信 narrow 协议获准。

生产开放仍须可信人验和批准来源、完整 G1..G6（含真实 G3/G6 与
公网 G5）、首次 DNS/TLS/受管应用/edge 的逐项授权与回退边界，
固定 release controller 协议及触发授权、真实恢复验证、同版本
公网真实计算 PASS。单位测试绿不关闭这些门，不代签或自动批准。
主代理已将 S1 与三处精确 PARTIAL 初因预期整合到 `5f1d3fd`，现读 clean；
本补丁不修改这些方法或 release_gate 的两件文件。
