# OpenSim v1 部署核查与授权门

## 结论与范围

**当前仅交付本地单机演示方案，公网未部署；生产变更等待用户确认。**
本件是席 7 的核查交付，不是执行授权或已实现的部署器。用户已由主席征询
新增 DNS、签发证书、共享入口变更和短暂 edge 重启，本席不重复代答。
只提交本件；infra、主席仓的 `deploy/opensim-web.service` 和
`deploy/edge-block.txt` 全部只读。不安装联网依赖，不改权限、凭据、
crontab、frpc、服务或网络入口，不启动私池或嵌套代理，不 push。

模型范围以 `docs/CONTRACT.md` 为准：充分发展直管、不可压缩牛顿流体、
稳态层流横截面 P1 三角形有限元；不是一般三维 CFD，也不承诺生产 SLA。
即使以后两台边缘都接通，`192.168.1.30:8772` 仍是单机上游，
**双边缘不等于 OpenSim 高可用**。升级 HA 需另行设计后端故障切换、
依赖与证据可达性，并经明确授权的故障演练验收。

## 现读依据

时间均为北京时间 `+08:00`。主席交接基线为 **2026-10-10 14:25**；
下表为本席 **2026-10-10 14:30:16 至 14:32:30** 的独立只读复核，
不把历史交接、SSH 可登录、进程 active 或单独 `rc=0` 当上线证明。

| 对象 | 现读结果 | 依据与边界 |
| --- | --- | --- |
| 公网 DNS | `opensim.ai4everyone.top` 为 `NXDOMAIN`，`ANSWER: 0` | `dig`，14:30:48，经本机配置的递归 DNS；尚未独立核查全部权威 NS |
| DNSPod 计划 | 仅计划创建 `opensim A 43.135.70.136`，TTL 600，默认线路 | 14:23:47 的既有 dry-run 日志；`added=1 existing=0 mode=dry-run` 不是实际写入 |
| 应用端口 | 本机 8772 无 TCP 监听 | `ss -ltn 'sport = :8772'` 只有表头；本机地址含 `192.168.1.30` |
| 边缘 .183/.184 | 均 `active`，`User=root`，`CanReload=no` | 两边 `systemctl show gitlab-edge.service`；仅证明当前服务状态 |
| 实际载体 | `/usr/local/bin/caddy-edge run --config /etc/gitlab-edge/Caddyfile --envfile /opt/docs-site/runtime/caddy.env` | 两边实际 `ExecStart`，不是依据旧说明猜测 |
| 共享配置 | 两边有 `admin off`、`auto_https off`、CAD/math 块，没有 OpenSim 块 | 行 38/39、4660/4671；CAD 上游 8766，math 上游 8770 |
| OpenSim TLS 文件 | 两边 TLS 目录均无 `opensim.*`，.183 无同域 acme 工作目录 | root 只读 `find`；未读取任何私钥或 account.conf 正文 |
| 现有 TLS 权限 | 两边目录 `root:root 0755`；CAD/math 证书 `docsweb:docsweb 0644`、私钥 `docsweb:docsweb 0600` | `stat`；与旧 unit 注释所称目录 0700 不同，不据旧注释改权 |
| CAD/math 真探针 | 两边各自 loopback 18445，CAD/math 均 `http=200 tls_verify=0` | 使用域名 SNI 的 `curl --resolve`，未用 `-k`，不是公网双隧道验收 |
| OpenSim 真探针 | 两边 loopback 18445 均 TLS alert internal error，`curl rc=35 http=000 tls_verify=1` | 14:32 附近，同域 SNI `/api/health`；未就绪的反证，不自动重试 |
| 发布席 Git | 独立 `codex/deploy`，开工工作树干净 | index 为 `/data/dev/opensim/.git/worktrees/opensim-seat-deploy/index` |

失败取证坐标也保留：两边最初 `sudo -n rg` 返回 `rc=1`，
首因为远端未安装 rg，改用系统既有 grep 后读到上述配置，不安装包；
这不是 Caddy 故障，也不把“搜不到工具”解释成“没有站点”。

现有 DNS 证据只读落点：

- `/hf3fs-jd/data/tmp/opensim-fluid-v1/logs/subdomain-dns-add-r640-20261010T142347.log`
- `/hf3fs-jd/data/tmp/opensim-fluid-v1/dns-inspect/dnspod-ai4everyone.top-before-20261010T142347.json`

上面日志 SHA-256 为
`a26fba18c77504bb1d24a3610bda6d42dd9f3ef41a44355a45ec1a030674fb5d`，
DNSPod before 件为
`a685f55f2d5bd64311229e3c65e062037eda53f14be12eff64b1de72b5c02dcb`。
未重新调用 DNSPod，也未复制凭据到日志或本件。

### 共享 Caddy 当前 diff

核查时 infra 的 Caddyfile、unit、三个旧工具相对 HEAD 无工作树改动；
两台线上 Caddyfile 与 infra 工作树逐字节指纹一致，unit 同样一致。
这只固定本次读时刻，**不能授权之后拿整份仓库文件覆盖线上**。

| 载体 | 本次 SHA-256 |
| --- | --- |
| infra 工作树及两边 `/etc/gitlab-edge/Caddyfile` | `c268b275afe0f28879f0d64508218b78e8b6e7cfb49cec39c6d2af01f968bd1d` |
| infra 工作树及两边 `gitlab-edge.service` | `f3350e01bed795cd872d1041ce69dd63f24944e059921ef94d6aca4c670f63f0` |
| 两边实际 `/usr/local/bin/caddy-edge` | `b7105518e3ed1c0761f232e44fc09345535533c9cb0abf0e12809416c7ac64d9` |
| 主席 `deploy/opensim-web.service` | `382fd263a7da8b8a788acebb8fc1dd26fa881636adea7ef20d500f4eb3a161d7` |
| 主席 `deploy/edge-block.txt` | `079b3793f878e1575ca2cea5f90ce04db46aab7d438d8937240c7276fb63f75f` |

未来 apply 前须重新比较本地 HEAD、index、工作树、两边 live 与候选。
只接受在**各边最新 live 基线**上新增 OpenSim 块的差量；必须保留全部原有
站点、认证占位符、上游、全局选项和 unit，不格式化或顺手整理。
各边基线不同则分别保留差异，不强行统一。提交前和远端原子安装前
都核基线 SHA；任一漂移即中止，由主席与共享入口 owner 协调后重新生成。
Python 编排器要与既有发布 owner 使用同一互斥协议；SHA 检查本身不是锁，
本件尚未实现跨发布链的互斥保护。

## 当前最小方式：本地演示

由主席先整合、运行数值与 HTTP/浏览器验收；使用现有 Python、NumPy、
SciPy，不安装联网包。以下为**未在本席执行**的本地前台入口，
不是 systemd 安装命令，要求主席仓已有完整实现及外置日志目录可写：

```bash
sudo -n -u infra python3 -B /data/dev/opensim/server.py --host 127.0.0.1 --port 8772 --log-dir /hf3fs-jd/data/tmp/opensim-fluid-v1/logs
```

浏览器访问 `http://127.0.0.1:8772/`。不开放公网、不新增 DNS/证书，
不改现有隧道。不要传不存在的 `--evidence`：没有独立数值证据时显示
“未采集”，不要借健康接口伪造“数值验收通过”。以后传证据须核文件
存在、报告通过、求解器 SHA 和版本确实匹配本次部署。
这里的 loopback 是服务所在主机；远程客户端不能直接用自己的 127.0.0.1
访问它，本件不代建新隧道或扩大监听来解决远程访问。
端口占用或依赖/权限缺失应报出首因，禁止停掉未知占用者、静默换端口、
补默认证据或自动安装依赖。本席未启动该演示，未验其页面或计算结果。

主席 unit 草案的 `User=infra`、单线程 BLAS、内存/进程限制、只写日志、
`NoNewPrivileges` 与文件系统保护可供后续参考，但目前
`--host 0.0.0.0` 会扩大监听面，`--evidence .../local/numerics.json`
还需现读验真；不能原样 install/enable。`Restart=on-failure` 不能变成
失败验收后的无限重启兜底，需启动限流、首因日志和停诊规则。

## 公网前必须明确的授权

以下都是**待确认**目标，回退路径不等于已获操作权。主席应将用户
回答绑定具体清单、执行人、维护窗口、影响面和回退授权，不由本席代签。

| 授权项 | 具体目标与最小写集 | 边界 |
| --- | --- | --- |
| DNS | DNSPod `ai4everyone.top`，仅 `opensim` 默认线路 A=`43.135.70.136`，TTL=600 | 不改裸域、CAD/math/DSH 或任意其它记录；现有异值/重复/冲突记录先裁决 |
| 证书签发及续期 | .183 的既有 `/root/.acme.sh`；仅 `opensim.ai4everyone.top`，DNS-01 临时 TXT `_acme-challenge.opensim` | 明确允许 TXT 创建/清理、CA 请求、同域续期配置及部署回调；不改 account.conf 的凭据值或登录方法 |
| 证书安装 | .183/.184 各 `/opt/docs-site/runtime/tls/opensim.crt`、`opensim.key` | 新文件权限按既有 TLS 模式经授权安装；不 chmod/chown 共享目录或已有证书 |
| 应用服务 | `192.168.1.30` 的 `/etc/systemd/system/opensim-web.service`，固定版本的 OpenSim 代码与外置日志/证据 | 安装、enable/start、监听及停服回退均需明确；代码只读，运行用户 infra，不给应用读密钥权限 |
| 后端网络面 | 优先仅绑定 `192.168.1.30:8772`，仅 .183/.184 可到达 | 必须先现读现有访问控制；需新增防火墙规则则单独授权，不开放公网 8772、不盲用 0.0.0.0 |
| 共享 SNI 入口 | 两边 `/etc/gitlab-edge/Caddyfile` 仅新增 OpenSim 块，仓内对应受控真源同步 | 保持 admin/auto_https、现有 envfile、二进制、unit、所有原有块不变；不触碰 docs 容器配置、frpc/frps 或 DNS 其它域 |
| 生效窗口与恢复 | 两边 `gitlab-edge.service` 各一次短暂 restart，逐边执行与验收，故障按已获准计划恢复 | admin off 且无 reload 能力，不伪称无损热重载；restart 影响共用边缘上的其它域，时长未实测 |

DNS/ACME root 操作仍由 `sudo -n -u infra` 驱动、经现有 infra SSH，
远端已授权的 root 动作再 `sudo -n`；不改 SSH key、known_hosts、
sudo 方法或增加其它用户身份。当前 .183/.184 的只读登录已用既有
`/home/infra/.ssh/id_ed25519` 和 `StrictHostKeyChecking=yes` 复核。
证书 API 类型及变量名只登记 `TENCENTCLOUD_SECRET_ID` /
`TENCENTCLOUD_SECRET_KEY`；来源为 .183 既有 account.conf，值仅进程内传递，
不输出配置正文、私钥、环境、认证 header 或 acme 域配置全文。

## 旧工具只读审查：不可盲用

已完整阅读以下三件。它们是现状证据，不是本次可直接执行的发布入口；
本席没有运行其 apply、证书签发、install、restart 或 selftest。

| 工具 | 代码中可确认的风险 | 本次采用条件 |
| --- | --- | --- |
| `/data/dev/infra/ops/dsh/subdomain-dns-add-r640.py` | 默认 dry-run、可拒 CNAME/NS/异值且写后回读；但 `open_log()` 不可写仅 WARN，after 证据失败仅 WARN；`RecordList` 缺失可被当空集，同值 A 的 SKIP 未充分核线路/TTL/启用/其它重复 A；批量连续三次 API 错误才停；没有 revert | 仅一个明确域，先证明响应完整、无冲突；日志与 before/after 落盘失败必须拒绝，写后独立核所有相关记录和新 RecordId，准备限本次记录的撤回 |
| `/data/dev/infra/ops/dsh/r640-cert-install.py` | 对端 scp 失败仅 WARN 后可报总 OK；远端多命令分号串可能前序失败被尾部成功掩盖；`load_creds()` 读失败静默返回；只看产物存在可跳签，最终只核证书非空、不充分核私钥/SAN/有效期/对端；会生成 shell 回调、暂存私钥给 infra，并默认重启两种服务 | 不直接用于本次生产；由主席在另获写权后做窄域、Python、逐步 fail-loud 的签发/安装/回调，复用既有凭据来源而非照搬危险流程 |
| `/data/dev/infra/ops/dsh/r640-edge-install.py` | 默认真装，取整份仓库 Caddyfile 和 unit 经 `06-edge-public.sh --install` 覆盖；临时目录及权限操作超出本次授权；逐边异常后继续；探针 `-k` 且只看 200/401/302，缺域名参数可 `domains=0` 报 OK | 不用于本次发布；只新增站点块，不替换 unit，不降低 TLS 校验，不接受空验收集 |

三个工具核查时 SHA-256 依次为：

```text
10fe944144d7fb634599ce3cc67e476f75bf0ec276e55bf437eb21c7557b0fd5
028b3a7538636eb6e57f239c64243dbcc0d0b44ca482eb338234d32ef7eacced
a633ff71e06bf8eec074a0ada7f69ca10d260ad79a6fd37ba118e4f63a9edbde
```

`06-edge-public.sh` 有先校验、站点缩水与 canary 门，但还存在静默恢复错误、
固定临时日志、整份 unit 安装、直接 restart 等行为；旧 `--rollback`
改的是 frpc 18445 -> 18443，不是“只撤 OpenSim”。不得以其名字有 rollback
就执行扩大影响面的恢复。`NO_RESTART=1` 至多影响本次调用环境，
不保证 acme cron 以后续期时也带该变量，更不能修复吞错/私钥暂存问题。

## 获准后的最小发布顺序（本席未执行）

1. **冻结可复现版本与恢复包。** 主席确认整合 commit、物理/HTTP/浏览器
   报告及实际依赖版本；代码固定版本只读，日志/结果只写 3FS。独立外置
   任务目录保存脱敏动作清单、before 指纹、退出码、首因、判定行和时间。
   脚本落仓内 Python 后再跑，需另派独占写集，不能在本件内隐藏可执行逻辑。
   未获准或任何现读与授权目标不一致即停止；不重试、不用超时假绿。
2. **只起本地/指定内网上游。** 先通过健康 JSON、页面、静态资源、
   真求解与失败契约；只读证据要与代码匹配。无登录墙的公网计算需用户
   明确接受匿名演示模式；参数/体积/并发边界不等于鉴权或抗滥用保证。
   不继承 CAD 会话、用户口令或其它服务配置。
3. **先签发与两边装证书，不先装引用缺证书的 Caddy 块。** DNS-01 不依赖
   新 A 记录即可准备，推荐将公网 A 暴露延后至两边验证后；前提是用户
   已单独授权 challenge TXT。如果批准的是先加 A，则承认准备窗口不可用，
   不报上线。使用现有 acme 链，显式 Let's Encrypt / ECC；
   `DOH_USE=4`、`DNSSLEEP=90` 是旧链参数参考，不是本次传播验收。
   先核 .183 现行 acme 锁及 renew 调用方法，再同锁串行，不新开私有并行签发。
4. **注册安全续期并核安装。** 只为本域设置持久部署回调；证书安装/
   复制失败立刻 nonzero，不能继续装 Caddy 或报 OK。安装两边后核 SAN、
   issuer、有效期、完整链、证书公钥与私钥公钥匹配；私钥仅在本地进程
   比较，日志只记布尔结果/证书元数据，不记录私钥内容或指纹。
   私钥不经 3FS、工作树、普通共享 `/tmp` 或 infra 所有的可读临时文件传递；
   如既有安全 root-to-root 传递机制尚未证实，必须停下补授权方案，不能自行
   另建密钥、改权限或登录链。新 runtime 文件按既有 docsweb 权限安装，
   应用进程无证书目录写权，不改变存量权限。
5. **逐边生成与验证候选。** 以下只读参考形态沿用主席 `edge-block.txt`：
   域名根路径反代到单机 `http://192.168.1.30:8772`，不加路径前缀、
   不修改 CAD/math、DSH 或 docs-site 内部 Caddy。以当前各边 live 为基线
   仅新增一次同域块；对缺 env 键/空值也要拒绝，但不打印值。用实际
   `/usr/local/bin/caddy-edge validate --adapter caddyfile --config <候选路径>
   --envfile /opt/docs-site/runtime/caddy.env` 由编排器安全调用并完整检查
   退出码；候选路径由外置清单给定，上述是接口说明、不是可粘贴执行命令。
   本席只查看了实际 `validate --help`，没有生成或 validate OpenSim 候选。
6. **受控安装与逐边生效。** 锁内再核 live SHA，保存各边自己的恢复件与
   元数据，原子装只含目标新增块的候选；不改 unit/二进制/envfile。
   一边 restart 后先本边完整验证，过关才动另一边；如需排空现有隧道/
   流量，属新增生产动作，另行确认。不能因有两边就宣称重启零中断。
   两边恢复路径与所需 restart 权必须在操作前获准，不能先写坏再问。
7. **最后加 A 并端到端验收。** 精确新增该域，保存新 RecordId 与 after
   证据；有既有异值、重复 A、AAAA 或委派等现状要先核清，不删除碰到的
   其它对象。权威 NS 与递归解析确认后再做公网 SNI/TLS/页面/计算验收，
   不能以 TTL 到时或等待固定秒数替代。DNS 传播中记待验证，不记完成。

参考块（提案，不是本席安装产物）：

```caddyfile
opensim.ai4everyone.top:{$EDGE_PORT:18445} {
    bind 127.0.0.1
    tls /opt/docs-site/runtime/tls/opensim.crt /opt/docs-site/runtime/tls/opensim.key
    encode gzip
    reverse_proxy http://192.168.1.30:8772
}
```

### 证书续期不能留成“以后手动补”

`auto_https off` 与手工 `tls` 路径意味着本方案不委托 Caddy 签发/续期。
主席需核现有 acme cron/timer 的真实调用、同域续期配置和持久回调，
不创建第二套 cron、不修改凭据。回调应复用同域最小安装与校验流程，
先暂存完整 crt/key 对、核匹配，再安全切换并核两边；.184 不可达或
复制失败必须返回失败、告警、保留各边最后可用版本，禁止 `PUSH_OK` 假绿。
轮换后各边各自 SNI 回读实际证书，防止盘上新证书而进程仍服务旧证书。

admin off 下文件替换不会使运行进程自行重读；首次批准的一次重启
**不自动授权以后任意时刻续期重启**。用户需明确批准持久回调的最小
生效窗口及恢复策略；仅轮换 OpenSim 证书不应重启 `docs-site.service`。
未批准周期生效时不安装会自动重启共享服务的回调，也不谎报自动续期闭环。
建议将不足 30 天列告警阈值；这是设计阈值，非本次证书剩余天数。
本席未检查实际 cron/ARI、未运行续期测试、未实现安全回调/告警。
未有持久 owner 与可复验续期证据，公网发布门保持关闭。

## 验收矩阵：双边缘、实际响应、保留负例

以下由主席在获准后落实仓内 Python 验收器并将脱敏证据写外置目录。
每项需非空对象集、真实命令、采集时刻、退出码与内容判定；未测写
`NOT_TESTED`，失败写 `FAIL` 与首因，不用 `rc=0`/进程存在替代。

| 面 | 最低完成判据 |
| --- | --- |
| 单机应用 | 监听准确，`GET /api/health` 为 200 且 JSON `status=ok,schema_version=1`；`/`、`/index-v1.html`、JS/CSS 非空且内容为指定版本 |
| 真计算 | 圆管/矩形合法输入经 `/api/solve` 返回契约结果，有限数、真实网格/速度场、版本与求解器 SHA 匹配；与独立数值报告核误差/残差，不把 200 当求解正确 |
| 错误边界 | 坏 JSON、缺/多字段、非有限数、越界和模型外输入拒绝；忙 503 有界且不自动重投；500/408 的原因留服务日志；跨站拒绝、体积/并发门与 HTTP 测试一致 |
| 两边各自证书 | .183/.184 都有同域完整可用证书，SAN/有效期/链/公私钥匹配过关；运行时 SNI 呈现预期证书；不使用 `-k`、不输出私钥 |
| 两边各自 SNI | 在各边用 `--resolve opensim.ai4everyone.top:18445:127.0.0.1` 保留域名和 SNI，真实 GET health/page/asset 与 POST solve 均过；检查 TLS 与 HTTP/JSON，不接受 401/302 代替应用成功 |
| 存量非回归 | 各边 docs/gitlab/全部既有 DSH 与 CAD/math 域集合不缩水，认证/上游行为不变；独立固定 canary 加 live 站点枚举双轨，CAD/math 保持本次 200+TLS 基线；存量异常单列 owner，不新增失败 |
| 公网与两条边缘路径 | `opensim` 权威/递归 A 正确，公网 443 TLS、真实 health/page/solve 和桌面/手机交互过关；明确外部 443 请求命中的实际隧道/边缘证据，不能在两边发同一公网请求就算分别经过两边 |
| 恢复与续期 | 外置夹具先证逐字节恢复、基线漂移拒绝、缺证书/对端失败/日志失败/空验收拒绝；获准后回调实测、两边轮换 SNI 回读与恢复报告完整 |

本次只实测了上文 DNS、端口、双边缘存量 CAD/math 和 OpenSim TLS 负例；
**未测**应用求解/浏览器、公网双路径、证书签发/续期、候选安装与回退。
若单一公网出口不能分别选择两边，第二条公网路径记未验证；能先给
两边本地 SNI 证据，但不得升级成双出口或 HA 证明，不为测试擅自切隧道。
超时只是失败终态，记录是连接/传输/计算哪一步，不掩盖成“后台还在跑”。

## 回退：只撤本次对象

回退要在获准写盘前说明对象、owner、确切前镜像、恢复执行权与复验路径。
外置恢复证据不得含明文 secret；Caddy 备份只保存经核不含 secret 字面量的
配置，envfile/私钥不搬到 3FS。证书最后可用副本留在既有受保护
runtime/acme 流程，若需新保护落点先获得授权，不自行改变权限。

1. **止损与入口恢复。** 失败时停第二边投递，保留首因/退出码/现状；
   若 live 仍是本次 installed SHA，恢复该边操作前 Caddyfile，核逐字节
   指纹和元数据，并按已授权窗口 restart + 双轨非回归。
   若另有 owner 已改 live，不整份覆回旧备份；在当前 live 上只撤
   OpenSim 差量，重新校验与协调。未获得恢复授权不得擅自重启。
2. **DNS 精确撤回。** 只按本次创建的 RecordId 撤回 OpenSim A，或恢复
   明确授权修改的原记录；API 结果与权威 NS 回读，说明 TTL/负缓存窗口。
   不删除其它人新增的同名记录、裸域或 CAD/math；旧 DNS 工具没有 revert，
   精确撤回实现未交付，需主席补齐才可 apply。
3. **应用服务。** 只停止/disable 本次新装的 OpenSim unit 或恢复明确
   旧版 unit/代码引用；保留日志与结果，不动其它服务、不清理未知文件。
   更新场景在固定旧版代码上恢复服务并重复健康/真求解验收。
4. **证书与回调。** 在入口不再引用后，仅停用本域新增的续期部署注册、
   撤回本域 challenge/本次文件或恢复上一可用对；不要停全局 acme cron，
   不撤销别域证书、不删共享 TLS 目录、不改账户密钥。删除/撤销行为
   必须单独在恢复清单内获准，未获准先保留受保护产物。

DNS、证书、入口、服务是不同对象，一项回退成功不能代替整体验收。
不用 `06-edge-public.sh --rollback` 绕过整个 SNI 层，不以恢复命令成功
代替旧站点真实可达。上述流程和门目前是**规范，尚未结构性化**；
本席独占写集只有文档，没有实现/安装 deploy/revert/renew 工具或哨兵。

## 规则核查与交接

已读全局 AGENTS、infra AGENTS、OpenSim AGENTS/CONTRACT，使用
`memory-router` 定向读项目索引及相关交接；历史 OpenCAD 中的 zyj2、
docs-site 镜像发布方法不扩成本次执行授权，以现读 ExecStart 为准。
已读 `shared-flash-pool` 及 README/能力记录，当前 `DENY/default=closed`，
本席不另派代理或启用执行池。

infra 完整性门现读为 `RULE_ROUTING_CHECK verdict=PASS rules=57`；
`inspect/publish/collaborate` 及随后 `edit/test/commit` 的并集路由通过，
完整读 `execution/collaboration/correctness/data/git/publish` 六专题。
`edit` 路由目标 `docs/DEPLOYMENT.md` 只用于选必读规则，不表示获得 infra
或其它仓文件写权；实际唯一写集是本发布 worktree 的同名文件。
选中文件拼接摘要：
`564d57c43117309b85b897de9cf00b23ed0339841134bacb94db5282714cd7cc`。

主席后续入口：整合本文件的单文件提交；继续本地验收，同时等待上面
具体生产授权。获准后另派窄写集开发安全编排/续期/回退工具，先证
负例与可恢复，再签发、两边验证和公网发布；授权与验证未齐不记上线。
