# OpenSim v1 部署交接

## 结论

**本轮仅本地单机演示，公网未部署，禁止生产变更。** 主席最新报告：
主仓本地 **211 项测试、170 项数值检查 PASS**，unit/template 已备；
这是主席提供的验收结果，本席不重复运行、不冒充独立复验。
尚无用户 network 授权答复，测试通过不等于获得发布权。
首版为直管充分发展稳态层流横截面有限元，不是一般三维 CFD；
单机上游即使有双边缘也不称 HA。

本席只写本件，infra、主仓 `deploy/opensim-web.service`、
`deploy/edge-block.txt` 全部只读。已停止远端侦察，不安装联网包，
不改权限/凭据/生产、不另派代理或启用执行池、不 push。

## 已有证据

主席基线：**2026-10-10 14:25 +08:00**。本席只读复核：
**14:30 至 14:32**；收尾 DNS 复核 **14:37:29** 仍为 `NXDOMAIN`。

| 对象 | 已读结果 |
| --- | --- |
| DNS | `opensim.ai4everyone.top` 不解析；既有日志 `added=1 existing=0 mode=dry-run` 只是计划 |
| 上游 | `192.168.1.30` 的 8772 当时无监听；此历史读数不否定主席随后本地测试 |
| 两边 .183/.184 | `gitlab-edge` active、root 运行、`CanReload=no`；实际载体 `/usr/local/bin/caddy-edge run --config /etc/gitlab-edge/Caddyfile --envfile /opt/docs-site/runtime/caddy.env` |
| 共享配置 | `admin off`、`auto_https off`；现有 CAD/math 块，上游 8766/8770；没有 OpenSim 块 |
| 证书 | 两边无 `opensim.*`，.183 无同域 acme 工作目录；未读私钥/account.conf 正文 |
| 真探针 | 两边本地 SNI：CAD/math 均 200、TLS 校验通过；OpenSim 均 `rc=35 http=000`、TLS alert internal error，不重试 |
| 当前 diff | 读时 infra Caddyfile/unit/三个旧工具无工作树改动；两边 Caddyfile 与仓内同指纹，unit 同样一致 |

两边及仓内 Caddyfile SHA-256：
`c268b275afe0f28879f0d64508218b78e8b6e7cfb49cec39c6d2af01f968bd1d`。
这只是读时基线，**不得之后整份仓库覆盖线上**。DNS dry-run 证据：
`/hf3fs-jd/data/tmp/opensim-fluid-v1/dns-inspect/`，日志
`/hf3fs-jd/data/tmp/opensim-fluid-v1/logs/subdomain-dns-add-r640-20261010T142347.log`。

## 待授权对象

由主席统一收用户答复；以下全部 **PENDING**，回退能力不是授权。

| 项目 | 明确目标、权限与边界 |
| --- | --- |
| DNS | DNSPod `ai4everyone.top`，仅 `opensim` 默认线路 A=`43.135.70.136`、TTL=600；不改其它域/裸域，冲突先裁决 |
| 证书与续期 | .183 既有 acme 链签发 `opensim.ai4everyone.top`；允许本域 DNS-01 TXT 创建/清理、签发、持久续期回调；安装两边 `/opt/docs-site/runtime/tls/opensim.crt` 和 `opensim.key` |
| 共享 edge | 两边 `/etc/gitlab-edge/Caddyfile` 只新增 OpenSim 块；保持所有既有块、unit、二进制、envfile、frpc/frps 不变 |
| edge 重启 | 两边 `gitlab-edge.service` 逐边短暂 restart 与故障恢复窗口；共用站点可能受影响，不能伪称无损 reload；不重启 docs-site |
| 应用服务 | .30 的 `/etc/systemd/system/opensim-web.service` 安装/enable/start 与停服恢复；固定版本代码只读，运行用户 infra，日志/证据外置 |
| 后端网络面 | 优先仅 `192.168.1.30:8772`，仅两边可达；若需新增访问控制另获授权，不开放公网 8772、不原样采用 0.0.0.0 草案 |

命令沿用 `sudo -n -u infra`、既有 infra SSH，获准 root 操作再远端
`sudo -n`，不改身份或登录链。新证书按既有 `docsweb:docsweb` 模式安装
（crt 0644、key 0600），不改存量权限或 TLS 目录；应用不读密钥。
凭据沿用 .183 account.conf，仅进程内传 `TENCENTCLOUD_SECRET_ID/KEY`，
不输出值、私钥、认证 header 或 env 正文。

## 最小实施与恢复

当前仅用主仓已备入口继续 loopback 本地演示；unit/template 是参考，
不是安装授权。不存在/不匹配的数值证据不能补默认或伪报通过。
本席未启动服务或执行公网流程。

1. 获准后由主席用仓内 Python 编排器留脱敏 before/after、首因、退出码与外置恢复件；先完成可恢复/失败负例，不以内联命令或旧工具盲跑。
2. 先确认本机健康 JSON、真求解、页面与错误边界；固定代码/依赖/数值证据版本。匿名公网演示及单机边界要明示。
3. 用既有 .183 acme 锁串行签发，先将同域证书安全装到两边、核 SAN/有效期/链/公私钥匹配；不把私钥放工作树、3FS、共享临时目录或临时交给 infra。安全传递未证实即停下裁决。
4. 在各边最新 live 基线上只加 OpenSim 块，反代 `http://192.168.1.30:8772`；与共享入口 owner 同锁，安装前再次核 SHA，漂移即停。用实际 caddy-edge、caddyfile adapter 和既有 envfile 验候选，不回显 secret。
5. 保存各边前镜像，逐边原子装配置、按授权 restart，一边验收通过才动另一边；最后新增 A。DNS-01 可先准备证书，A 延后暴露；不同授权顺序须明确不可用准备窗口。
6. 失败停后续投递。live 仍为本次 SHA 才整份恢复本边前镜像并逐字节核对；他人已改 live 则只撤 OpenSim 差量。DNS 只按本次 RecordId 撤回，服务只撤本次 unit，证书只停本域注册；删除/恢复/重启均须预获授权，不停全局 cron、不改 frpc。

续期必须绑定现有 acme 调度和安全回调；对端失败/证书不匹配/日志失败
立即 nonzero，保留最后可用版本并告警，不能仅 WARN 后报 OK。
`admin off` 下盘上换证不等于运行时生效；首次 restart 授权
**不自动覆盖以后续期 restart**，需明确周期窗口与恢复策略。
`NO_RESTART=1` 不是持久续期保证。安全 deploy/revert/renew 工具、
互斥和告警尚未由本席实现，不能声称结构性保证。

## 验收与旧工具风险

两边分别用域名 SNI 指向本边 `127.0.0.1:18445`，不用 `-k`：
真实 GET health/page/assets、POST solve，核 HTTP、JSON、数值与版本；
CAD/math 保持 200+TLS 基线，全部原有域枚举加独立 canary 不缩水。
再核权威/递归 DNS、公网 443、桌面/手机和真实计算。
公网双路径要有各自实际隧道/边缘证据，从两边发同一公网请求不算双路径。
缺测记 `NOT_TESTED`；本席未验公网、签发/续期、安装或生产回退。

已完整只读审查 `/data/dev/infra/ops/dsh/` 下三个旧工具：

- `subdomain-dns-add-r640.py`：日志/after 证据失败可仅 WARN；缺 RecordList 可当空集，同值 SKIP 对重复/线路/状态核查不足，无 revert。
- `r640-cert-install.py`：对端推送失败可仅 WARN，总结果可能假绿；临时私钥给 infra，回调默认 restart edge 和 docs-site，不能盲用。
- `r640-edge-install.py`：整份覆盖 Caddyfile/unit、直接安装重启，`-k` 弱探针且空域集可 OK；不是本次最小发布器。

`06-edge-public.sh --rollback` 改整个 frpc 入口，不是“只撤 OpenSim”，禁盲用。
全局/两仓 AGENTS、CONTRACT、相关交接及六个选中专题已完整读；
完整性门 `PASS rules=57`，`inspect/publish/collaborate/edit/test/commit`
并集路由通过。本席只提交本件，主席收授权并负责后续整合与最终验收。
