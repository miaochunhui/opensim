# UI 契约验收

仅本地实现与自动化证据，不是公网、真实触屏或屏幕阅读器认证。
保留奶油底色与青绿数据视觉，物理范围仍为圆/矩形直管横截面稳态层流 P1 FEM。
本席只修改 `web/app.js`、`web/app.css`、`tools/ui_contract_test.mjs`、
`tests/test_ui_contract.py` 和本文。

## 校验与状态

在线成功必须包含复现戳、有效非未来计算时间、五份完整源码 SHA256、
一致的 solver 身份及配置摘要。页面实际指纹资源和内容摘要须匹配 health；
失败/null/非有限数值不进入当前结果，不留可导出的旧结果。
旧 schema 1 导出缺复现戳或缺源码字段只用于导入校验，随后实际重新求解；
不能直接显示、导出或充当当前源码证据。格式里出现 null 或坏字段仍拒绝。

忙态锁定表单、求解、重置、导入与导出；重入只报告未执行，不改变运行任务。
求解与异步文件读取都核 revision；期间改参数则拒绝结果/文件，保持修改，
释放控件，明确失败并允许恢复。导航退出后的新页面重新实际求解。

配置摘要按 Python 规范化浮点 JSON 序列化核验，分辨率为整数。
运行源码顺序沿用 `tools.render_page.SOURCE_PATHS`，不排序替代页面指纹序列。

## 可复跑入口

```bash
sudo -n -u infra python3 -B -m unittest discover -s tests -p test_ui_contract.py -v
sudo -n -u infra node tools/ui_contract_test.mjs --url http://127.0.0.1:PORT/ --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-ui/new-run
sudo -n -u infra python3 -B tools/acceptance.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-ui/local-new
sudo -n -u infra node tools/browser_check.mjs --url http://127.0.0.1:PORT/ --out /hf3fs-jd/data/tmp/opensim-fluid-v1/browser/ui-20261010-unique
sudo -n -u infra python3 -B tests/test_ui_contract.py --scan /hf3fs-jd/data/tmp/opensim-fluid-v1/seat-ui/scan-new.json
```

unittest 启动自己的临时 loopback server 并只关闭该对象。DOM/CDP 测试由真实
Chromium 页面观察 phase、aria-busy、错误与控件状态，实际请求本工作树求解。
响应故障注入只修改真实响应的交付副本，原始 HTTP/FEM JSON 单独外置保存。
重算用新增实际 HTTP、当前源码、P1 积分和旧文件标记未继承验收；
同秒同配置结果可能字节相同，不用字节差异冒充计算次数。
下载必须读浏览器实际文件并核对实际响应。PNG 核签名、尺寸、摘要和回读。

320px、desktop 与桌面窄 workspace 核真实盒尺寸、44px 控件及所有溢出，
不以 `overflow:hidden/clip` 掩盖。对比度采样实际 PNG 背景，包括渐变；
仅临时去除字形再逐元素恢复并核 computed color，不用静态色 token 假通过。
这是自动对比度检查，不代替完整 G3/G6 审计或人工阅读评估。

报告先落 `PROVISIONAL/RUNNING/exit_code=2`。真实执行日志、PNG、实际求解与
下载文件逐件回读，写证据 manifest 并核精确字节，再用本 run 的已知精确
provisional 原子替换为 FINAL。最后回读失败时必须以已知候选精确字节撤销 PASS，
写 `FINALIZATION_FAILED/FAIL/exit_code=2`；替换失败则只移除本 run 已知字节，
保留独立失败记录，不覆盖未知内容。日志写入/回读、证据写入/回读、最终
写入/回读六类故障均在真实最小 DOM/FEM run 注入，Python 同验进程码与报告码。

## 失败证据

全部失败保留，不追认旧 run。证据根 `/hf3fs-jd/data/tmp/opensim-fluid-v1/`：

- `seat-ui/minimal-v1/run-2026-10-10T08-00-07-878Z-9ecbdc1a/report.json`：
  在线响应缺戳仍 ready/可导出，真实缺陷，后续严格在线校验修复。
- `seat-ui/contract-v3/run-2026-10-10T08-04-35-792Z-3a65745d/report.json`：
  同秒同配置结果误判，验收器改为新增 HTTP 与旧标记未继承。
- `seat-ui/contract-v4/run-2026-10-10T08-05-12-215Z-a0588a16/report.json`：
  忙态预期重入提示被等待器误当终态；改为等 busy 释放后核最终错误。
- `seat-ui/contract-v5/run-2026-10-10T08-06-46-332Z-582a8feb/report.json`：
  背景样式未生效，文字仍在，ratio=1 不构成 CSS 缺陷。
- `seat-ui/contract-v6/run-2026-10-10T08-07-56-252Z-7127b55c/report.json`：
  字色恢复不完整；后续 CSSOM 原样恢复并核每个 computed color。
- `seat-ui/contract-v7/run-2026-10-10T08-09-52-728Z-8f164dfb/report.json`：
  恢复颜色断言拒绝假绿，改为 CSSOM 精确恢复，而非受策略影响的属性重写。
- `seat-ui/contract-v8/run-2026-10-10T08-11-37-858Z-427b934b/report.json`：
  SVG 字形仍使用 fill，采样器补实际 fill 取色/隐藏，不能据此改 CSS。
- `seat-ui/contract-v9/run-2026-10-10T08-12-30-741Z-9d8b50bb/report.json`：
  窄 workspace 双列导致 canvas 宽度归零、浅字落奶油底，ratio=1.473。
  修为 workspace 容器宽度启用双列，并核父容器边界，不隐藏溢出。

## 当前结果

依赖原样 cherry-pick：`88d5c6f` 对应本分支 `1fef12b`，`c44ac23` 对应
`a4084d4`。仅作为获准依赖，不将其文件再次纳入本席提交。未修改 server、
favicon、主仓、infra、CAD/openopt、凭据、身份或权限；无生产与 push 操作。

- `seat-ui/contract-v10/run-2026-10-10T08-14-33-745Z-b8f2b763/report.json`：
  43 项真实 DOM/CDP 契约、61 次真实求解 PASS，含背景对比度与窄容器。
- `seat-ui/unittest-20261010T082132Z-299f3429/run-2026-10-10T08-21-32-411Z-5b0d4ad5/report.json`：
  FINAL、进程退出 0，与真实日志及 evidence manifest 摘要同验。
- `seat-ui/finalization-20261010T082156Z-2835dee7/execution.json`：
  六故障回归 PASS；各故障原 run 为 FAIL、进程及报告退出码均 2，各实际求解 3 次。
- `seat-ui/local-v1/acceptance.json`：2026-10-10T08:16:11Z，5/5 PASS、
  241 unittest；数值 15 算例、3 收敛组、152 拒绝例、170 检查 PASS。
- `browser/ui-20261010-unique/run-2026-10-10T08-16-34-312Z-95128bab/report.json`：
  完整 8 视口、12 断点、桌面/手机键盘矩阵，159 检查、64 实际求解、
  588 张分段 PNG AUTOMATED_PASS；不是 probe 或 588 个独立用例。
- `seat-ui/browser-artifacts-v1.json`：现读全部 588 PNG 的签名、尺寸、SHA256，
  64 个实际求解 JSON 的当前五份源码身份 PASS；browser 报告 SHA256
  `227cd63347f1f8df15777f3f1aeac0fad75e7eaed1d89c30df27106c13d09d29`。
- 最终全本地 `seat-ui/local-v2/acceptance.json`：
  2026-10-10T08:26:03Z，5/5 PASS、242 unittest，数值仍为 15/3/152/170 PASS。
  新工具终结协议实跑在 `seat-ui/unittest-20261010T082451Z-c16798fb/`，
  六类故障及退出码/失败报告同验在
  `seat-ui/finalization-20261010T082516Z-0c613cc5/execution.json`。
  不追认旧工具版本 run，旧报告保留以便复查。

完整 browser 的固定外置边界不同，按用户裁决写 `browser/ui-20261010-unique/`，
不改工具固定边界。`release_verdict=NOT_CERTIFIED` 保持；未获得真实触屏及
screen-reader 例外，不代签、不将自动化 PASS 宣称为发布认证。
