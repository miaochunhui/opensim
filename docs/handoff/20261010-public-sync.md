# 公网同步要求交接

2026-10-10，Asia/Shanghai。本轮响应用户“确保永远公网同步上线”。
结论：同版本交付判据与受管巡检已实施，**首次公网发布和自动部署链未完成**。
不能承诺永久零故障，也不能把巡检安装当作公网发布。

## 已完成及现读证据

- 健康与计算响应包含五份运行源码完整 SHA256；前端严格验证新增复现字段，
  兼容旧导出文件后重新计算。缺必需源码不再静默跳过。
- `tools/public_gate.py` 仅查固定公网 HTTPS，禁代理/跳转绕本地，拒绝私网DNS，
  核页面、JS/CSS、运行身份，每轮用新压差真实计算圆管和矩形管，
  比对完整FEM网格/速度/指标/身份/时间。失败留首因，退出1。
- `tools/public_watch.py` 使用同一判据，每两分钟检查；故障/恢复分标题，
  本地锁与受管状态防重复通知；未知/失败通知不记作已送。
  现役 .183 中继返回 `spooled`，只记 QUEUED_NOT_DELIVERY_VERIFIED，
  未证实邮件或用户收件；没有改任何令牌/凭据/共享通知服务。
- 专属 `opensim-public-watch.service/.timer` 已安装，infra运行、无生产部署权限。
  `watch_install.py` 只写两份专属unit，已有unit/漂移拒绝覆盖，
  只撤本次新增文件且保留证据和状态。未实测生产回退，只完成文件系统夹具。
  `watch-selftest-20261010-v3/report.json` 五项 PASS，2026-10-10T07:21:50Z；
  `watch-install-20261010-v1/report.json` 安装 PASS，07:18:44Z；
  `watch-status-20261010-v1/report.json` 现读enabled/active+字节核验PASS，07:21:51Z。
- `local-v8/acceptance.json`，07:23:18Z，5/5 PASS、229项unittest；
  同目录numerics.json有15算例、3收敛组、152拒绝例、170检查PASS。
- 完整浏览器 `browser/run-2026-10-10T07-21-23-532Z-ae85d653/report.json`，
  07:23:32.539Z，AUTOMATED_PASS，159检查、64真实计算、605分段PNG、
  8视口、12断点、键盘/强制颜色/减少动画、200%字号压力、400%重排等效、
  矩形导入重算通过。源码页面构建 `41429c386585`，Chrome140.0.7339.16。
  **release_verdict仍NOT_CERTIFIED**，不是完整G3/G5/G6或真实设备证明。
  主代理原生看过最新desktop-standard截图，真实网格及参数实际出现。
- 公网 `public-gate/20261010T071648-1a3d99e95c6b46db8bee7a71902ff90b.json`
  07:16:42Z FAIL，DNS gaierror、0/4检查，非“健康已过”。
  定时第二轮 `public-watch/20261010T072324-be4860ca085640f795b9afef2193fabe.json`
  07:23:19Z同因FAIL，未重复发同状态通知。
  正确的故障退出1不是巡检程序崩溃；timer仍active/waiting。
- .183 spool现读行12488确认15:21:19+0800收到该OpenSim故障；
  早两次响应判断错误造成重复队列记录12485/12487，保留未追认。

所有证据根为 `/hf3fs-jd/data/tmp/opensim-fluid-v1/`，物理解析3FS。

## 修复与操作偏差

首次新字段导致前端拒绝响应，浏览器失败留在
`browser/run-2026-10-10T07-19-27-870Z-b8316b88/`，规则FEM-UI-ERROR。
补字段的兼容与完整验证后以上完整新run通过，没有改超时或删样本。
通知首次错误期待 `sent`，实际为 `spooled`；旧notification=FAIL JSON保留，
修复后独立负例确认队列确认不等于送达，不伪报发信通过。

主代理误将3FS工具的 `--check` 放在末尾，工具实际递归统一了本任务证据
目录2777/文件666，未只读。该偏差已向用户说明，没有动生产/密钥。
原逐文件权限未留备份，不猜测宽回滚；不得称“本轮只有unit生产变更”。
后续仅用专属写入检查，不重复宽范围权限工具。

## 阻挡项及复飞

已询问用户：是否允许真实触屏/软键盘以及桌面/移动屏幕阅读器首版临时例外。
**尚无答复，不自行签署例外或弱化MUST**。完整自动G3/G5/G6也未完成，
仍需补齐，例外不能自动扩为所有发布门。若批准，按shared-r41记录
ID/原因/影响/owner/到期/移除条件，并接过期拦截。

DNS仍NXDOMAIN，正式OpenSim应用service未安装；本轮没有改DNS、
证书、gitlab-edge、frpc/frps、OpenCAD/openopt资产，没有Git push。
域名发布目标与双边最小增量/逐边restart风险见DEPLOYMENT.md，
此前PENDING表是历史，用户现在要求推进上线，但不能扩大到所有共享操作。
若需续期重启授权或访问控制，明确窗口与目标。

下一步必须完成安全固定release部署/撤回/续期工具、完整发布门、
首次DNS/TLS/受管应用/双edge最小发布，公网真实检查PASS、
存量站点回归、恢复负例及获准发布触发。只读observer没有自动同步权限，
尚无异地巡检和用户收件实证。公网同版本是交付硬门，不是永久可用性证明。

最新本地实例127.0.0.1:8776，工具session23321，用local-v6数值报告
（同solver），新源码与local-v8一致。旧8772/8773/8774/8775尚在，
因源码漂移不应拿来验新版；不得宽pgrep/kill影响他站。
主仓代码改动点名提交，不push；infra共享脏树和旧跨仓PROPOSED交接不动。
