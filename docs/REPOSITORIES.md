# 仓库与代码同步

## 仓库角色

| 角色 | 地址 | 状态 |
| --- | --- | --- |
| GitLab 主仓 | https://gitlab.ai4everyone.top/infra/opensim | 已回读下面的代码快照 |
| Gitee 同步仓 | https://gitee.com/sdfwefqaa/opensim | 与 GitLab 两个 refs 同 OID |
| GitHub 同步仓 | https://github.com/miaochunhui/opensim | 已有代码，两个 refs 与 GitLab/Gitee 同 OID |

2026-10-11 主席已核新三端同步终态：实际 outer0、三次 push 全部0，
逐端 readback 与所选 main/fluid OID 一致。报告为 `SYNCED`，范围仅
`CODE_BACKUP_ONLY_NOT_RELEASE`；560 个历史对象已扫描，source 稳定、
config 不变，无 failure。GitHub API 账号回读为 miaochunhui/200，仓库回读200；
API 成功本身不代替上述 Git 推送及 refs 回读。
公网尚未上线；代码备份不等于公网发布、真人验收或发布门通过。
常驻 mirror、timer、cron 未安装，目前没有自动仓库同步。
本仓许可证仍未指定，不能据仓库可访问推断开源授权。

## 获取 main

2026-10-11 实测 GitLab 默认 HEAD 为 main，Gitee 为旧 codex/fluid-v1。
三端都明确选择 main，不把默认分支当最新候选：

```bash
git clone -b main https://gitlab.ai4everyone.top/infra/opensim.git
git clone -b main https://gitee.com/sdfwefqaa/opensim.git
git clone -b main https://github.com/miaochunhui/opensim.git
```

以上是三个获取入口，按需任选其一。main 后续可能正常快进；
复现下面指定快照时须另核完整提交 OID，不能只凭分支名。

## 已核代码快照

2026-10-11 新三端同步的实际 push 与逐端回读一致：

| Ref | GitLab、Gitee 与 GitHub 的相同 OID |
| --- | --- |
| refs/heads/main | 4f20b5535fbef00a1aa61f8b6442380a82498d12 |
| refs/heads/codex/fluid-v1 | 97477ba9255c1cdb9f8a15d9145a00e61a63188d |

实际源树为 `/data/dev/opensim-chair-repository-doc-candidate-20261011`，
所选源 HEAD 为上述完整 main OID。主席终态 session85003/de4457 actual0。
证据：`/hf3fs-jd/data/tmp/opensim-fluid-v1/authorized-chair-repository-three-target-http-profile-sync-20261011-v1/report.json`，
SHA256 为 `3d84e35af48577fd8361a1132306e13aa395444ecb3ccf37df31aac17f74b872`；
同目录 `commands.json` 保留各 push 与 readback 的实际码及安全日志坐标。
这是该次代码快照同步，不包含本次后续 README/本文的新文档提交；
新提交另经主席复审、授权推送并逐端回读后才算同步。
fluid 保存历史，不代表当前 main；这不证明自动镜像、HA、持续可用、
公网发布或真人验收。

## 历史快照与 TLS 失败

2026-10-11 14:13（Asia/Shanghai）两端推送与独立回读成功：

| Ref | GitLab 与 Gitee 的相同 OID |
| --- | --- |
| refs/heads/main | 9322c93ddd3ea2e0b212757d4a655156ab6829e1 |
| refs/heads/codex/fluid-v1 | 97477ba9255c1cdb9f8a15d9145a00e61a63188d |

这是已核旧快照，不包含本页和 README 的后续文档提交；新提交只有
另行推送并逐端回读后才算同步。fluid ref 保存历史，不代表当前 main。

2026-10-11 旧 TLS 失败前的只读预检，两端精确相同：

| Ref | GitLab 与 Gitee 预检 OID |
| --- | --- |
| refs/heads/main | c182d9102494cf5ae7e5c7c77a26984b37e71b4b |
| refs/heads/codex/fluid-v1 | 97477ba9255c1cdb9f8a15d9145a00e61a63188d |

这是旧预检回读，不是该失败轮推送成功；当时 GitHub 尚无代码快照。
旧轮在 GitHub TLS 预检处停止，实际首因 `REMOTE_QUERY_FAILED_RC_128`，
原 `27.stderr` 记录 GnuTLS recv error(-110)；该轮没有执行任何 push。
`NET_TLS_PRECHECK_FAILED` 仅为旧文档状态标签，不是工具实际报错。
后续隔离 HTTP/1.1 默认 UA 的 Git Empty reply128 与 curl Git-UA timeout28
也保留为失败。新 HTTP-profile 条件下的成功不定罪 UA、HTTP2 或瞬时故障，
不追认旧失败，不允许自动重试；认证查询/推送成功只属于上面已核新轮。

## 显式同步入口

工具属于外部 infra 仓，不随 OpenSim clone 安装，当前固定源入口为：
`/data/dev/infra/wt-opensim-s5r-repository-http-profile-20261011/ops/opensim-repository/sync_repositories.py`。
该三件工具提交为 `6a841234589259b8140fd9f9c649c17c42ded3a0`。
GitHub Git argv 显式使用 `http.version=HTTP/1.1` 和 `http.userAgent=curl/7.81.0`；
原 helper/TLS 验证/拒绝 redirect/sourcepins/history/fail-loud 保持，
GitLab/Gitee 不变，不改 global config、proxy 或关闭 TLS。
同目录 `test_sync_repositories.py` 和 `README.md` 也须保留并核验，
操作前读取该工具 README；依赖缺失不能声称已有持久同步入口。

仅获授权维护者以 infra 身份执行。`--action sync` 使用干净已审
`--source`、其 HEAD 的完整 `--commit`、明确点名的 `--target gitlab`、
`--target gitee`、`--target github`（仅获授权的目标）
及全新外置 `--out`；不提供绑定旧 HEAD 的重播命令。
历史 fluid 只经显式 `--save-fluid-commit` 同步到上述历史 ref。
仅正常快进所选 refs，不 force、mirror、all、删远端或盲重试。
分叉、未知、部分成功须保留首因并停止后继；逐端实际码与同 OID 回读
才是同步证据。工具不改源 remote、HEAD 或 index；main/upstream 的
本地配置由维护者另行核验，文档角色不代表配置已完成。
