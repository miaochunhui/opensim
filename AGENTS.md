# OpenSim 工作入口

本仓承接用户的中文、可自部署、可复现仿真工作台。全局规则继续适用。

- 首版仅做直管充分发展、不可压缩、牛顿流体、稳态层流的横截面有限元。
  不得把有限体积、解析公式填图或插值动画称为有限元求解。
- 本仓命令采用 `sudo -n -u infra`。文件编辑用 `apply_patch`。
- 求解器、输入契约与物理验收优先；界面必须明确模型边界及失败原因。
- HTML 由 `tools/render_page.py` 生成，不手改成品。运行数据、截图及日志
  放 `/hf3fs-jd/data/tmp/opensim-fluid-v1/`，不放工作树。
- OpenCAD 与 openopt 仅经版本化文件契约协作，不修改两仓资产或代签接口共识。
- 生产发布只针对 `opensim.ai4everyone.top`；修改共享网络入口前确认授权、
  当前目标与回退路径。不改凭据，不提交 secret，不自动 push。
- 实现席使用独立 worktree、`codex/` 分支和独占文件；主席整合、测试与提交。

接口见 `docs/CONTRACT.md`；运行入口与已验证范围见 `README.md`。
