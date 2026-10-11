# 仓库与代码同步

## 仓库角色

| 角色 | 地址 | 状态 |
| --- | --- | --- |
| GitLab 主仓 | https://gitlab.ai4everyone.top/infra/opensim | 已回读下面的代码快照 |
| Gitee 同步仓 | https://gitee.com/sdfwefqaa/opensim | 与 GitLab 两个 refs 同 OID |
| GitHub 公开仓 | https://github.com/miaochunhui/opensim | 已创建并回读公开状态，尚未推送代码 |

2026-10-11 主席实收创建流程：账号核验成功，仓库 404 后创建 201，
再次回读 200 / PRESENT_PUBLIC；这不是代码推送或三端同步证明。
随后三端同步在 GitHub TLS 预检处停止，工具实际首因为
`REMOTE_QUERY_FAILED_RC_128`；该次原 `27.stderr` 记录 GnuTLS-110 早断。
`NET_TLS_PRECHECK_FAILED` 仅为本页状态标签，不是工具实际报错。
未执行 push；不声称三端完成或自动重试。
公网尚未上线；代码备份不等于公网发布、真人验收或发布门通过。
常驻 mirror、timer、cron 未安装，目前没有自动仓库同步。
本仓许可证仍未指定，不能据仓库可访问推断开源授权。

## 获取 main

2026-10-11 实测 GitLab 默认 HEAD 为 main，Gitee 为旧 codex/fluid-v1。
两端都明确选择 main，不把默认分支当最新候选：

```bash
git clone -b main https://gitlab.ai4everyone.top/infra/opensim.git
git clone -b main https://gitee.com/sdfwefqaa/opensim.git
```

以上是两个获取入口，按需任选其一。main 后续可能正常快进；
复现下面旧快照时须另核完整提交 OID，不能只凭分支名。

## 已核代码快照

2026-10-11 14:13（Asia/Shanghai）两端推送与独立回读成功：

| Ref | GitLab 与 Gitee 的相同 OID |
| --- | --- |
| refs/heads/main | 9322c93ddd3ea2e0b212757d4a655156ab6829e1 |
| refs/heads/codex/fluid-v1 | 97477ba9255c1cdb9f8a15d9145a00e61a63188d |

这是已核旧快照，不包含本页和 README 的后续文档提交；新提交只有
另行推送并逐端回读后才算同步。fluid ref 保存历史，不代表当前 main。

2026-10-11 本次失败前的最新只读预检，两端仍精确相同：

| Ref | GitLab 与 Gitee 预检 OID |
| --- | --- |
| refs/heads/main | c182d9102494cf5ae7e5c7c77a26984b37e71b4b |
| refs/heads/codex/fluid-v1 | 97477ba9255c1cdb9f8a15d9145a00e61a63188d |

这是预检回读，不是本次推送成功；GitHub 尚无已同步代码快照。

## 显式同步入口

工具属于外部 infra 仓，不随 OpenSim clone 安装，当前固定源入口为：
`/data/dev/infra/wt-opensim-s4-repository-token-path-20261011/ops/opensim-repository/sync_repositories.py`。
该三件工具提交为 `7bb75e82967e718fe5a5cf356f7a79582a8a845b`。
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
