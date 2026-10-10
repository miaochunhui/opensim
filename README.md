# OpenSim

中文、可自部署、可复现的流体有限元 Web 工作台。

首版实现直管充分发展层流的横截面问题，采用线性三角形有限元，
计算轴向速度而不是一般三维流动。圆管及矩形管为首批算例。
已实现真实有限元求解、中文参数与结果页、网格切换、错误恢复和 JSON
导入导出。独立数值与 HTTP 联合测试通过；公网域名尚未部署。

## 本地运行

本机已验证 Python 3.10、NumPy 1.21.5、SciPy 1.8.0；依赖声明见
`requirements.txt`。升级依赖需要重新执行数值验收。

```bash
sudo -n -u infra python3 -B /data/dev/opensim/server.py --host 127.0.0.1 --port 8772 --evidence /hf3fs-jd/data/tmp/opensim-fluid-v1/local-v2/numerics.json
```

浏览器打开 `http://127.0.0.1:8772/`。这是服务主机的本地地址，未开放公网。
无验证报告时可省略 `--evidence`，页面显示未采集；指定坏文件明确报错。
所有计算为无会话共享状态的请求，不上传任意执行脚本、不执行用户表达式。
最大网格分辨率 48，超过层流模型雷诺数上限的输入拒绝。
运行源码发生变化时，旧实例返回 `SOURCE_CHANGED`，不能用旧代码冒称新版本；
需要维护者启动新实例，不自动改动共享或生产服务。

## 验收与协作

```bash
sudo -n -u infra python3 -B /data/dev/opensim/tools/acceptance.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/acceptance-new
sudo -n -u infra node /data/dev/opensim/tools/browser_check.mjs --url http://127.0.0.1:8772/ --out /hf3fs-jd/data/tmp/opensim-fluid-v1/browser
```

每次验收使用新的目录保留失败证据。第一条验收数值、真实 loopback HTTP、
JavaScript 语法及页面生成；第二条为真实 Chromium 自动化，不能代替人工
触屏设备或屏幕阅读器测试，也不是公网性能或发布通过的证明。

开发契约见 `docs/CONTRACT.md`，范围与迭代门见 `docs/ROADMAP.md`，
跨仓提案见 `docs/INTEGRATION.md`，待授权发布与回退见 `docs/DEPLOYMENT.md`。
所有实验结果与浏览器证据外置。
公网同版本交付门、专属持续巡检和当前欠账见 `docs/PUBLIC_SYNC.md`；
巡检不自动发布，公网检查尚未通过时不能称交付完成。
本地验收与当前实例的断点见 `docs/handoff/20261010-fluid-v1-local.md`。
本仓未指定开源许可证，需由权利人决定后再声明授权条款。
