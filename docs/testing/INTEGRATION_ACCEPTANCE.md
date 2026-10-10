# 整合验收入口

`tools/integration_acceptance.py` 串行执行本地联合验收及可选的完整浏览器验收，
避免多个验收同时占用单个求解槽。只接受干净、已提交的候选版本，前后核对
提交、Git 状态及全部受管文件摘要；任何阶段失败立即停止后续阶段。

证据目录必须是 OpenSim 外置产物根下尚不存在的独立目录。子进程的首因、
退出码、完整输出和报告摘要均保留，不重试、不覆盖旧证据，不接受空测试、
缺少视口的浏览器报告、局部探针或不匹配的源码证据。

本地入口：

```bash
sudo -n -u infra python3 -B /data/dev/opensim/tools/integration_acceptance.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/integration-new
```

加上 `--full-browser --local-url http://127.0.0.1:8776/` 会运行完整旧浏览器矩阵。
使用前须以当前候选源码启动新的本地实例，不能利用旧进程通过健康检查替代真计算。

终判 `LOCAL_INTEGRATION_PASS` 仅表示已选择的本地阶段通过。
`release_verdict` 始终为 `NOT_CERTIFIED`。G3、G5、真实触屏与屏幕阅读器、
生产部署恢复及公网真实计算分别验收，不能由本入口代签。
