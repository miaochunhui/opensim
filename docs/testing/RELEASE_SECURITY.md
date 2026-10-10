# Release 独立安全复核

本席独立审 app、DNS 与离线 transport，不修改被审源码。
原版 app `b0ec93257ab3` / DNS `3b2ecbe1e51f` **FAIL**，
1 类 P1、5 类 P2、7 个独立安全断言报红；修复版另列，不追认旧红。
固定第 6 槽补位，不新增代理。未修改被审源码或生产。
测试与证据只在 `/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-security/`。

## 独立反证方法

`tests/test_release_security.py` 冻结候选源码后创建真实文件系统 fixture 提交，实际运行数值
验证和页面构建，安装并核对真实 release、unit、receipt 字节。
仅外置 fixture 路由被替换；manifest、receipt、读写、校验、锁不被 mock 掉。
DNS API 经本测试文件的固定离线子进程，核固定 payload、真实修改外置
zone 状态并留调用账；生产分支 SSH 被固定假传输替换，绝不执行网络命令。
失败安全断言原样报红，不用 expectedFailure、跳过或改源码使门假绿。

最终原版复核从 Git 对象冻结 app、DNS 和导出源码到新外置目录。
`frozen-source.json` 记录完整 commit、文件摘要和真实位置，读回核逐字节。
主仓随后修改不会改变正在审的 bytes；独立子进程也只读冻结 DNS。
旧 `full-v1` 为冻结机制前的证据，其记录的摘要与最终冻结原版一致，
但最终判据采用 `frozen-original-v1`，不依赖工作树当时是否并发变动。

复跑须使用尚不存在的 `--out`；`--only` 可重复点名测试。
`--app-root`、`--dns-source`、`--transport-root` 指定仓库/文件；
CLI 默认 `/data/dev/opensim` 当前候选，revision 默认 `WORKTREE`，
不再默认旧漏洞源。`--baseline` 显式选原 app/DNS/transport 提交；
显式 revision 参数优先，可选 Git commit，`--transport-only` 仅跑协议类。
`--all` 跑两类完整门，与 discover 的用例集合相同。
当前候选与发布导出件一起冻结：冻结前、读入、
落盘后各核摘要，并在测试结束记录当前输入摘要与冻结摘要是否一致。
HEAD 在这种记录中只是工作树基线，不冒充未提交 bytes 的提交身份。
开始测试前再次对拍整个冻结集合；中途源变动只记录漂移，不混入执行。
`unittest discover` 通过 `load_tests` 创建新的 `discover-UUID` 外置目录，
执行 app/DNS 和 transport 两类真实测试，并从同一个 unittest result
落实际 count/failure/error/skip、report.json、execution.log 及冻结摘要。
没有 dns=None 退化、skip 或 expectedFailure；当前残留漏洞仍必须红。
`--scan-only` 只扫描两份独占写集并验证扫描/脱敏阳性对照；
用现有 infra 扫描器和 memory 值形态引擎，不读取真实凭据，
不冒充真实凭据逐值扫描，空/缺件集合拒绝通过。

## 实测发现

坐标均为指定原版，修复席负责源码，安全席仅提供独立反证。
下面的 `/hf3fs-jd/data/tmp/opensim-fluid-v1/seat-security/` 简称证据根。

### P1 DNS 外置账本可伪造删除所有权

坐标：`tools/dns_release.py:140`、`:204`、`:338`、`:368`、`:432`。
真实 fixture 预置原有精确 OpenSim RecordId=9001，没有执行 CreateRecord。
外置 0666 JSONL 写入唯一 CREATE_INTENT、CREATE_ACK、FINAL，自报
backend=production、before=[]、created_record_id=9001。
production 分支的真实 Ledger/ownership/execute 使用固定假 SSH/API 传输，
实际子进程顺序是 Describe、Describe、DeleteRecord(9001)、Describe；
zone 最终空，工具返回 REVERTED/0，独立安全断言 FAIL/2。
第二臂是真实 fixture apply 后仅改每帧 backend 为 production，同样删除。
不改 backend 的阴性臂在网络前拒绝，说明字符串标签有约束但没有认证。

证据：`first-forged-ledger-v1/.../counterexample.json` 与最终
`frozen-original-v1/test_dns_forged_world_writable_production_ledger_cannot_delete_foreign/`、
`test_dns_relabelled_fixture_ledger_cannot_authorize_production/`。
建议：立即阻断缺受信任 controller/ownership 的 production mutation。
可信 ownership 必须由独立受保护写域保存、绑定目标/后端/发布/RecordId，
在写前 intent 和真实 ack 后更新；外置 JSONL 仅审计，不是删除授权。
只把 infra 所有账本 chmod 只读、加普通摘要或加更多状态字段不能认证来源。
主席已接收第一条，后续 DNS 专席修复；原失败不追认。

### P2 DNS 硬链接账本串写其它名称

坐标：`tools/dns_release.py:143`、`:193`。
同 inode 的两个账本路径，dryrun 接受 st_nlink=2 并追加 BEGIN/BEFORE/FINAL；
另一路径的原字节真实改变。
证据：最终 `test_dns_hardlinked_ledger_is_refused_without_append/counterexample.json`。
建议：打开前后核普通文件、单硬链接、所有者/权限，并使用同一受控 fd
读写及核身份，不能仅 is_file 或最后路径 O_NOFOLLOW。

### P2 DNS 父目录换链突破账本路径

坐标：`tools/dns_release.py:119`、`:133`、`:193`。
构造 Ledger 后，将本席可写的父目录移走，同名位置换成指向另一外置目录
的软链接，再 append。实际在新目录生成 receipt.jsonl，未报错。
证据：最终 `test_dns_ledger_parent_swap_cannot_escape_frozen_path/parent-swap.json`。
建议：根路径安全打开并保持目录 fd，逐层禁软链接或使用受约束 openat2，
以 dir_fd 操作和核 inode；生产可信路径同时必须不能被非授权 owner 改名。
此例只针对非 root 可写父目录，不把合作锁当恶意 root sandbox。

### P2 DNS 成功子进程异常字段进入证据

坐标：`tools/dns_release.py:82`、`:342`、`:343`、`:344`、`:446`。
固定子进程 rc=0 返回类型正确但冲突的 Value（仅合成敏感标记，无真实 secret）。
classify 最终失败，但 BEFORE/FINAL 和 stdout receipt 已包含该原始 Value。
证据：最终 `test_dns_successful_child_conflict_secret_not_persisted/secret-field-summary.json`。
失败子进程 stderr/stdout、Error 对象阴性控制均正确不回显。
建议：证据字段来自严格固定值/数字投影，冲突诊断只保留字段名与状态，
不得先持久化任意 child 字符串再分类。此处证明泄漏通道，不声称读到真实密钥。

### P2 App 证据写失败后成功报告残留

坐标：`tools/release_app.py:818`、`:823`、`:824`、`:827`。
真实 apply 已完成，报告落盘后仅对 execution.log 注入 ENOSPC；
工具实际 rc=1，但已落盘 report.json 为 FILES_INSTALLED、exit_code=0，
unit 确实存在、execution.log 缺失。
证据：最终 `test_app_log_evidence_failure_returns_failure_after_install/partial-evidence.json`。
建议：先写证据再发布终态；可采用安全完成标记/报告原子发布，
失败须有独立首因/真实退出码，不让不完整目录携带成功终态。
操作已写入不应被自动回滚或当作“没有发生”，需明确 PARTIAL_EVIDENCE。

### P2 App 必需日志目录缺失仍通过文件验收

坐标：`tools/release_app.py:358`、`:426`、`:547`、`:754`。
真实安装后只删除本席创建的空日志目录，check 仍返回 FILES_VALIDATED。
源、unit、receipt 未改。该状态明确不是运行 PASS，但遗漏了 unit 引用的
必需写目录，不能证明完整安装文件/日志准备就绪。
证据：最终 `test_app_missing_log_directory_is_not_files_validated/missing-log.json`。
建议：安装/文件检查/启动前后统一核日志目录存在、类型、批准所有者、
权限及可写能力；不通过修权限自动扩大授权。主席负责两个 app 问题。

## 原版验收

| run | count | failures | errors | rc | verdict |
| --- | ---: | ---: | ---: | ---: | --- |
| first-forged-ledger-v1 | 1 | 1 | 0 | 2 | FAIL |
| full-v1 | 21 | 5 | 0 | 2 | FAIL |
| frozen-original-v1 | 29 | 7 | 0 | 2 | FAIL |

最终 29 项、22 项通过、7 项失败、0 skip。全部有 report.json、execution.log；
准备有真实 numerics.json、numeric-logs、committed-source、bundle、
preparation.json；最终额外有 frozen-source/ 与 frozen-source.json。
旧失败目录保留，无重试覆盖。实际测试命令：

```bash
sudo -n -u infra python3 -B tests/test_release_security.py --baseline --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-security/frozen-original-v1
```

该路径是已完成 run，不可原样重跑覆盖，复跑须换新的目录名。
安全期望从首轮到冻结轮未放宽；新增 8 项控制的 2 项失败是新发现，
不是用测试数量掩盖旧红灯。

### 已证伪与承认的边界

固定 caller pin 下 owner 重新封装 manifest 被拒，内容复制时漂移被拒，
未知 before_unit_base64 和 previous 快照注入被拒，app 源软/硬链接被拒。
root-owned 门拒绝 infra-owned 0444；非 root 生产安装在任何子进程前拒绝；
安装/撤回不执行 release 或依赖代码，依赖版本漂移在网络与 oracle 前拒绝。
receipt/release 固定路径及 unit 精确模板有效；root controller 不要求 x 位，
因为批准的入口是 `/usr/bin/python3 -I -B` 读取脚本，执行位不是承重权限。
可信 stdlib/解释器、安装 controller 的审阅摘要和生产 root 属主仍要独立验收。

owner 0444/0555 可以 chmod 后重新写并给出新 digest，validate_bundle 会接受
caller 新 pin。这是新的批准输入，不是破解旧 pin；commit 字段不是 Git
来源证明，审批必须独立对拍 committed export/完整 manifest。
最小伪造数值 PASS（1 检查、空内容 PASS 行和自造收敛数列）能过结构校验；
外部真实数值与原始产物 gate 不可省略，该组件不独立证明数值报告真实。
两点已用实盘对照记录，不把文档明确外包的信任条件升级成 P1。

未执行真实 systemd、生产 root controller/bootstrap、真实 SSH/身份/
凭据、私钥、网络/公网、生产 rollback；本席没有启动 loopback app
或重做完整计算 probe，真实数值验证仅在 fixture 准备阶段执行。
有限时序注入不证明无全部竞态，合作锁不防恶意 root，未测试跨机 DNS CAS。
DNS production mutation 未有可信 bootstrap 时必须继续关闭；
修复版离线门不能代替生产所有权、跨机 CAS 或实际撤回验收。

### App Changed-Version 回归

主席授权对 `/data/dev/opensim` 未提交 app 当前 bytes 独立取样。
`app-changed-worktree-v1` 冻结后 8 项、0 failure/error、rc=0、PASS：
两条原日志回归、首因保留、既有输出目录不撞写、正常安装/撤回、
数据安装不执行源码、before/previous 注入拒绝。
缺日志目录合理抛 ReleaseError 即 PASS，不用 expectedFailure。

新增报告尾部持久化故障不是上述 PASS 的覆盖范围：
`app-postwrite-fault-v1` 为 1 项、1 failure、0 error、rc=2、FAIL。
冻结、取件前后 app 摘要相同；完整值在该 run 的 frozen-source.json，
基线 HEAD 是 `6d6aeff100df`，不是未提交修复本身的 commit。
测试结束再次读取输入，matches_frozen=true。

### P2 修复版最终报告写后故障仍残留成功 JSON

坐标：修复版 `tools/release_app.py` 的 main 最终 report `write_new` /
异常 return 1；原版等价窗口在 `:823`。
负例 `test_app_report_postwrite_fsync_failure_cannot_leave_success_json` 使用
真实 apply 与真实 write_new。仅在 report.json 已创建、读回完成、调用
最终 sync_directory 后注入 EIO，首因与故障时刻原样保留。
实际 unit 存在，CLI=1、stdout 空；日志带 evidence_pending=1，
完整 report.json 仍为 FILES_INSTALLED/exit_code=0。
stderr 宣称 no final success report，但磁盘确有成功报告。
证据：
`app-postwrite-fault-v1/test_app_report_postwrite_fsync_failure_cannot_leave_success_json/tail-failure.json`。
这是实际故障窗口，不是 root 并发破坏，不能用前置写失败负例代替。

建议终结协议：

1. 文件只记录 action_result 和 evidence 状态；待结束前的文件永久不得
   单独包含整个调用已成功的终判。当前 action 文件成功不等于 CLI 成功。
2. 调用方必须等待真实子进程退出，检查 rc=0、固定版本/事务 ID、
   精确产物路径/摘要、全部读回与证据状态；最终 stdout 只在证据步骤通过
   后打印，调用方留实际进程结果，不能从文件里的自报 exit_code 推断 rc。
3. 可用新 staging/原子发布管理文件集合和恢复，但最终 rename/fsync
   失败也可能留下发布结果，单加 success/DONE 标记不会消除同族窗口。
   故完成标记若使用，也必须和调用方真实 rc/接受记录一起验，不能独立绿。
4. 证据失败时保留 action 首因、已完成写入与 evidence_failure，禁止以
   “CLI失败”推断“没发生安装”，禁止自动重试或回滚未知生产状态。
   结束协议修复需复验前置写失败、完整写后读回/fsync失败、report发布
   失败、已有输出目录和首因保留，以及消费方拒绝孤立成功文件。

该修复版尾部红灯第一时间已回主席；主席补精确报告撤销后，
`app-postwrite-fixed-v2` 为 6 项/0 failure/error/rc0，原失败保留。
`current-default-finalization-v1` 进一步在主仓当前候选上实跑三项：
最终目录 fsync 故障、完整报告首轮读回故障、持续读回/撤销故障，
3/3 PASS；app SHA256 前缀为 `7529226288b0`（完整值见冻结报告），
前后/结束输入摘要相同。

持续 I/O 失效时精确撤销也可能失败，实盘可残留报告自报 rc0，
但实际 CLI1、stdout 空、stderr 明确 invocation unverified。
这是明确的完成协议边界，不假称能在坏盘上保证撤销；
消费方必须使用真实进程 rc，不能从 report.exit_code 独立批准。
测试只证明生产工具公开失败且不输出最终成功，不证明外部消费者都已接门。

## 当前候选与协议反证

2026-10-10 现读主仓基线 `9567f7d6a292`，
app 内容来自 `01311b9`、DNS `e2854d6`、transport `66411f0`。
冻结当前 WORKTREE 不依赖 DNS 专席的旧路径，独立导出当前页面/solver，
用真实外置 FS、固定子进程及实际 OpenSSL，未读真密钥、未联网。

| run | count | failures | errors | rc | 判定 |
| --- | ---: | ---: | ---: | ---: | --- |
| app-postwrite-fixed-v2 | 6 | 0 | 0 | 0 | PASS |
| combined-fixed-v1 | 32 | 1 | 0 | 2 | DNS child Value 仍泄漏 |
| transport-independent-v1 | 10 | 3 | 0 | 2 | 原口径原红保留 |
| discover-b70ab41b30b7* | 42 | 3 | 0 | 1 | 当前源真实 discover |
| discover-d0f7a7b9bb6b* | 48 | 5 | 0 | 1 | 扩展当前源真实 discover |
| discover-a3f9a4b80bf0* | 49 | 6 | 0 | 1 | 整合安全修复前主仓 |
| discover-6c55f8803b6f* | 49 | 0 | 0 | 0 | 主仓 8becc3b 默认当前门 |
| current-default-finalization-v1 | 3 | 0 | 0 | 0 | 当前 app 尾部故障 |
| baseline-cli-control-v1 | 1 | 1 | 0 | 2 | baseline 能看见原伪账本红 |
| transport-baseline-expanded-v2 | 14 | 4 | 0 | 2 | 完整 OAEP 参数门缺失 |
| transport-baseline-multirecipient-v1 | 1 | 1 | 0 | 2 | 原版多 recipient 控制红 |
| followup-app-dns-v1 | 34 | 0 | 0 | 0 | 修复树 DNS + 主仓 app |
| followup-transport-v1 | 15 | 0 | 0 | 0 | 修复树协议完整门 |
| followup-all-v1 | 49 | 0 | 0 | 0 | 修复树同进程联合冻结门 |

全部 0 skip，目录均为证据根下新路径，旧失败无覆盖。
discover 表中的星号是 UUID 后缀缩写，证据根下对应唯一目录；
完整路径由当次判定行输出，不是测试使用 glob。
discover 首轮 42 项的 3 fail 为 DNS child Value 与 OAEP/MGF1 SHA1，
其余包括 app 终结、DNS 生产阻断/硬链接/父目录换链均通过。
扩展 discover 的 5 fail 为 DNS 泄漏及四个 OAEP 参数负例，不是旧源码默认。
最终 49 项 discover 的 6 fail 增加双 recipient；冻结时主仓仍未合六件安全
修复，因此真红保留，不用异树默认规避。主仓 UI 更新不改变已冻结的执行件。
各报告记录完整冻结位置与 before/after/测试后摘要，无中途偷换源码。
`combined-fixed-v1` 只剩 DNS child Value；该红灯由主席接手，不再写被审六件。

### P2 Transport 只核 OAEP OID，未核固定参数

坐标为冻结 `466463b804fc`
`tools/cert_transport.py:353` 至 `:365`。主仓 `66411f0` 内容摘要相同：
`e4ba8193f694`（完整值见冻结报告）。
接收端只正则核 AuthEnvelopedData/AES256GCM/rsaesOaep OID，
未验证 OAEP hash、MGF1 hash、pSource label 的完整结构/唯一性。

独立生成合成 CA、EC 源 key 和 RSA recipient，真实签名、真实 CMS 封包。
四个负例分别为 SHA1/SHA1、SHA256/SHA1、SHA512/SHA512、SHA256/SHA256
加非空 label `01`；四者均真实调用 authenticated_decrypt、验签/链/配对
并返回 OFFLINE_PROTOCOL_PASS。固定 SHA256/SHA256 空 label 阳性对照通过。
不是破解 GCM 或签名，也未发现生产解密；是固定套件声明与接收行为不符。

具体复现与产物：`transport-baseline-expanded-v2/` 下四个
`test_transport_*` 参数测试目录，各有公开 `suite-*.json` 结构及
`downgrade.json` 或 `suite-refusal.json`，记录 attempted=true/refused=null/
validation_report=OFFLINE_PROTOCOL_PASS；私钥及 payload 未落盘。
建议在私密解密前结构化核唯一 recipient、准确 OAEP SHA256、
MGF1 SHA256、空 pSpecified label、参数/OID 唯一性和 DER 完整性，
不能只增加一个可匹配任意位置的 sha256 字符串。源码修复由主席负责。
新增双 recipient 使用两个实际合成 RSA recipient 生成真实 CMS，
原版 accept 返回 OFFLINE_PROTOCOL_PASS，单项控制红灯保留。

### 修复树独立回归

2026-10-10 主席明确冻结 `/data/dev/opensim-security-followup-v1` 六件 dirty
修复给本席只读；不把 HEAD `9567f7d` 冒充修复 bytes 的提交身份。
DNS 源/测试/文档和 transport 源/测试/文档分别外置冻结，执行前与结束
均核摘要。`followup-app-dns-v1` 34/34、`followup-transport-v1` 15/15，
原 32/10 对应反证及新增故障/参数/多 recipient 控制均真实执行，0 skip。

DNS 源 SHA256 前缀 `878d1f2fae48`，
transport 源 SHA256 前缀 `41c115675552`。
app 源摘要仍为上列 `752922...`。完整六件及导出件摘要在各
frozen-source.json/frozen-transport.json，input_after_tests 均 matches_frozen=true。

DNS 合成 child 冲突 Value 不再进 ledger 或 CLI receipt；
transport 的 SHA1、MGF1 SHA1、SHA512、非空 label 和双 recipient 都在
authenticated_decrypt 前拒绝，固定套件仍通过真实验签/链/配对。
过期 offer 现在 close，accept 合理抛 SESSION_ALREADY_CONSUMED 即 PASS。
这些通过只绑定上述冻结 bytes，不能用专项 51/48 自报替代主仓收口。
主仓合入 `8becc3b` 后已不传任何源覆写运行 default discover，
`discover-6c55f8803b6f*` 49/49 PASS、rc0、0 failure/error/skip。
十三件当前输入路径全部指向 `/data/dev/opensim`；冻结前/后/结束摘要
全匹配，报告/日志/两份冻结清单实际存在。没有依赖历史 DNS worktree。
`followup-all-v1` 同一进程一次冻结 app 与六件修复及导出件，
49/49 PASS、rc0，完整 report.json/execution.log 和冻结清单均实际存在。
本轮点名扫描曾因文档公开完整 SHA/UUID 触发 long-hex，旧 FAIL 留盘；
文档改为短指纹，完整摘要与 UUID 保留在外置报告，不放宽扫描规则。

### 过期公开 offer 改判

原 `transport-independent-v1` 第三条断言只要求过期 offer 不导出，
不足以认定 P2：request/recipient certificate 本身是公开材料，
accept 在 monotonic 过期后仍拒绝私密响应，不能据此声称泄露。
原 10/3fail 产物不改；新
`test_transport_expired_offer_cannot_accept_private_response` 真实先 seal，
再推进 deadline、观察公开 offer，并断言 accept 拒绝/会话消耗或关闭。
discover 与扩展 baseline 该项 PASS；不是 skip 或 expectedFailure。

### Transport 未准入边界

同一 ReplayGuard 阻断重复读取，换 guard 后可再 seal 是文档明确的
进程内边界，需要 root-owned 持久授权拒绝旧事务，不能称持久重放保护。
固定 caller pin 下替换 recipient 在读源私钥前拒绝；若调用者自行批准
攻击者 pins，是改变可信输入，不是旧 pin 绕过。
真实 OpenSSL 子进程失败的敏感合成诊断不进入异常；未证安全擦除或恶意同 UID 隔离。
生产动作仍全部拒绝。host 信任锚、可信 controller/source/dependencies、
持久状态、memfd/core/swap 隔离、权限、真实双端安装/恢复和持续 renew 尚未验。

## 证书密文转运设计

**条件可行，当前不能准入。** 可保留现有 infra SSH+sudo 传输，不需 root
peer 登录凭据；临时 recipient 公钥只用于加密，不应加入 authorized_keys、
SSH agent 或 sudo 配置。这里区分“中转不接触明文”与“抵抗恶意中转”。

最小设计约束：

1. 两端预先批准并逐字节核 root-owned 窄 controller、解释器/crypto 依赖、
   固定 host/域名/文件路径/允许动作；现有登录链不改，不运行可写 checkout。
   infra 如果仍拥有任意 sudo-root 权限，就在端点信任域内；不能宣称对该
   身份攻陷提供密钥隔离。收窄 sudo 是额外授权，不在本方案中偷偷实施。
2. 目标 root 生成一次性 recipient 密钥，公钥连同 source/destination
   host ID、域名、用途、transaction ID、随机 challenge、期限、controller
   版本形成规范编码请求。源 root 必须独立认证这份完整请求；现有 infra
   SSH host-key 验证只认证该跳远端，不认证 infra 中转后的 recipient
   内容。未认证时中转可替换为自己的公钥，源会把私钥加密给攻击者。
3. 双向消息须由预先信任且未授权中转不可替换的身份认证机制保护。
   若考虑现有 root-only host signing key，复用用途也要单独审阅许可，
   固定签名 namespace、host principal 和批准公钥，不开放任意签名。
   未核实有这种既有信任材料之前，不能假设可自动 bootstrap。HPKE/AEAD
   的完整性不等于谁发送；加密本身也不证明 recipient 属于哪台机器。
4. 使用已审库的固定 authenticated envelope，绑定上述请求摘要、
   package 长度/摘要与版本，验认证/篡改后再安装；不自写密码协议。
   两端 root-owned 持久交易状态记录一次性 nonce/接受状态，拒过期、
   重放、跨 host/域名、降级和重复安装。HPKE 不自带事务级重放保护。
   root 进程崩溃/重启应消耗该事务并需新 challenge，不从 666 总账重建授权。
5. 明文 TLS key、recipient 私钥与解密结果仅在 root 受控进程/内存 fd，
   不进 infra 管道、argv/env、stdout/stderr、3FS、工作树/报告/core。
   普通 memfd 不是保密内存承诺，须核 descriptor 继承/`/proc` 可见性、
   dump、swap/休眠与中间复制，关闭无需继承 fd，并对清零/生命周期设门。
   目标最终安装 key 是已授权 root/服务私域，不应声称整个生命周期零落盘。
6. 限定非 tar 任意路径包，严格解析/长度，链信任、仅 OpenSim SAN、有效期、
   EKU/key pair 对拍；原子安装证书与 key、失败精确恢复，证据只含公开信息。
   固定双 edge 各自 canary，重启范围/窗口另获授权。renewer 只能产出受验
   新事务，不拥有任意 sudo 或任意域名/命令；全 ACME writer 锁、失败通知、
   部分双端失败恢复、每次 host 身份验证与长期 restart 授权都要重新成门。

待逐条核验的规范入口：RFC 9180（认证与重放限制）、
OpenBSD `ssh-keygen(1)` 的 `-Y verify` / allowed signers（principal/
namespace），Linux `memfd_create(2)` 与 `memfd_secret(2)`（内存 fd 边界）。
本次在线原文检索未返回可核内容，规范逐条对拍未完成，不据此签生产许可。
只读设计未实现 crypto/controller/broker，未读或签现有 host 私钥，
未核实际 host signing 能力、swap/core/sudo 约束或持续 renew。
若现有链没有可独立认证 recipient 的信任锚，或不能把中转与任意 root
执行区分，结论就是 **BLOCKED**，不能用“只有密文”解除证书门。

ROOT controller、生产 systemd、真实身份链、网络、renew 与恶意 root
均没有实际执行或取得许可。离线协议测试通过也不能解除这些门。
