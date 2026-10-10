# OpenCAD / openopt 文件交接状态

本件是 2026-10-11 的只读库存快照与后续验收要求，不是新的出站请求、接口共识、签收、施工授权或发布证明。
采集于 **2026-10-11 07:27:26 +08:00**：三份非空文档、六项既有请求、零份指定回执、六项未回复。
OpenCAD 与 openopt 均 `NOT_RECEIVED`；请求仍为 `PROPOSED`，共识、实现和联合验收均 `NOT_VERIFIED`。
后来出现的回执须重新运行下列入口计数，不用本快照把新状态追认成旧证据。

<!-- opensim-handoff-status
{
  "schema": "opensim-handoff-status-v1",
  "scope": "FILE_HANDOFF_NOT_AGREEMENT",
  "observed_at": "2026-10-11T07:27:26+08:00",
  "snapshot_counts": {"documents": 3, "requests": 6, "receipts": 0, "unanswered": 6},
  "request_ids": ["OSIM-CAD-001", "OSIM-CAD-002", "OSIM-CAD-003", "OSIM-OPT-001", "OSIM-OPT-002", "OSIM-OPT-003"],
  "agreement": "NOT_VERIFIED",
  "implementation": "NOT_VERIFIED",
  "joint_acceptance": "NOT_VERIFIED",
  "linear_system_status": "NOT_REQUESTED_NOT_AGREED",
  "source_sha256": {
    "20261010-opensim-coordination.md": "bcf01d655d292ef2e583936d41bb55705f256808b2bd38e2fcfcea76dc86061d",
    "20261010-opensim-to-opencad-requests.md": "a05562249fea3e2d66bfd7fd443015bdb5d935fdda0e709c1b9d37adc79866b5",
    "20261010-opensim-to-openopt-requests.md": "0b5e0155c1a2bc1cd4135dad366fb719adb0c0a23609aa6b3503a5ea535633e8"
  }
}
-->

## 唯一原件与缺失响应

原件都在 `/data/dev/infra/docs/handoff/`，只读，不复制发出第二轮请求：

- `/data/dev/infra/docs/handoff/20261010-opensim-coordination.md`：索引、回执格式、双方独占回复路径。
- `/data/dev/infra/docs/handoff/20261010-opensim-to-opencad-requests.md`：三项 CAD 请求及原验收条件。
- `/data/dev/infra/docs/handoff/20261010-opensim-to-openopt-requests.md`：三项优化请求及原验收条件。

OpenCAD 指定回复 `/data/dev/infra/docs/handoff/20261010-opencad-to-opensim-response.md` 当前不存在。
openopt 指定回复 `/data/dev/infra/docs/handoff/20261010-openopt-to-opensim-response.md` 当前不存在。
不存在只在这两个约定收件路径上现查；不据此断言其它聊天、仓库或邮件也无回复。
三原文及 infra 核验器当前是共享树未跟踪资产，未提交或推送的持久交付不能由本件保证。

| 原请求 | 当前缺少的接收方响应 | 进入联合验收前必须提供的最小材料 |
|---|---|---|
| OSIM-CAD-001 | OpenCAD 按编号裁决，精确几何/单位/装配交换版本 | 实现引用、几何与清单样例、mm/m 尺寸体积往返、重复实例/变换、未知单位/坏文件/空实体反例；两方预先确认容差 |
| OSIM-CAD-002 | 修订绑定与改参映射范围裁决 | 修订前后原几何、选择引用、保持/拆分/合并/丢失/歧义状态；实际位置复核与错误挂载反例，未知映射阻止继续求解 |
| OSIM-CAD-003 | 无界面重建入口、变量与取消支持范围裁决 | 变量类型/单位/范围、父修订/生成器版本、新几何；坏参数/失败/取消保留原件，等参物理等价及边界失效检查 |
| OSIM-OPT-001 | 连续 LP/QP 的数学形式、支持矩阵与接口版本裁决 | 同源凸 QP 与参考求解器输入输出，目标差/可行性/KKT；不可行/无界/退化/维数/非有限负例；CPU/CUDA 单列未测硬件 |
| OSIM-OPT-002 | 黑箱或一般非线性是否纳入范围的裁决 | 回调/文件协议、梯度可用性、预算/种子/取消/失败策略；已知解解析例、失败评估及实际评估次数，后续小型物理目标另定 |
| OSIM-OPT-003 | 状态、评估身份与复现语义裁决 | 最优/可行未证最优/不可行/无界/预算耗尽/取消/数值故障/不支持状态表；原问题、配置、轨迹、算法/device/dtype、残差与首因 |

各接收方仅在自身获准的回复路径按编号给 `ACCEPT` / `NEEDS_CHANGES` / `DECLINE`，
注明 owner、独占写集、接口版本、依赖、测试命令与预期行为。若无 infra 写权，按原索引在自身受管文档目录回复，
由主席收件新增指引；S5 不代写或签收。部分回复只减少已回答项，未回答项仍未回复。
有效回执仅记 `RECEIVED_NOT_VERIFIED`；`ACCEPT` 不自动升级为实现、共识或验收。

## 可运行的文件验收

当前真实原件的既有只读入口，不运行物理或浏览器全集：

```bash
sudo -n -u infra python3 -B /data/dev/infra/ops/opensim-coordination/check_handoff.py
```

本轮新增专项入口只读原件并保存真实子进程 rc、stdout/stderr、逐项库存和 source/inode 前后观察。
`--out` 必须为获准根内未存在的新目录，下面是独立审核可直接用的一次性路径：

```bash
sudo -n -u infra python3 -B /data/dev/opensim-s5-integration-handoff-20261011/tests/test_integration_handoff.py --out /hf3fs-jd/data/tmp/opensim-fluid-v1/authorized-s5-integration-handoff-20261011/independent-v1
```

输出 `report.json` / `execution.log` / `handoff-inventory.json` 与真实 child 日志；计数以当次库存为准。
负例仅改测试自有外置副本，原文/回执/source 不修改。命令成功只证明文件完整性及计数，
不证明对方已阅读、接口或算法实现、物理正确性、联合验收、human 或 production。

`/data/dev/opensim/tools/integration_acceptance.py` 是本地五 stage 与可选浏览器编排器，
不是 peer 文件核验器；其 `LOCAL_INTEGRATION_PASS` / `NOT_CERTIFIED` 不替代回执。
本轮不运行它、不重复六请求、不重复原数值或完整 UI 测试。未发现须申请 tool 写权的确切协议 bug。

## 后续几何契约

当前 `/data/dev/opensim/docs/CONTRACT.md` 仅接受圆/矩形直管横截面 config，长度 mm、内部 SI、
结果 `nodes_mm` / `triangles` / `boundary_nodes`。`boundary_nodes` 仅为横截面无滑移壁面，
不是三维入口/出口或任意 CAD 面选择；不得向现 v1 HTTP 偷加 CAD 清单字段。
`/data/dev/opensim/docs/INTEGRATION.md` 的固定截面清单与原 CAD-001 精确 STEP 实体阶段不同，
双方需明确首个支持范围，不能把预览三角网格当精确实体。

几何契约至少须两边确定 schema/格式版本、原文件 hash 与内容身份规则、模型/修订/父修订、
长度单位/坐标系/局部全局变换、零件与重复实例身份、参数与来源、容差、边界角色的修订绑定和失效状态。
OpenSim 独立导入核实际尺寸/面积，实体阶段核体积与变换；坏单位、摘要、空实体及歧义边界显式拒绝。
当前并无已获接受的导入器入口或样例，不能编造其 CLI；接收方回复后由主席派小写集实现再独立验收。

## openopt 与线性系统边界

六原请求没有 `A x = b` 求解服务或稀疏线性系统交换接口；**NOT_REQUESTED_NOT_AGREED**。
OSIM-OPT-001 是连续 LP/QP，建议目标 `0.5 * x.T * Q * x + c.T * x`；
OSIM-OPT-002 是须先裁决范围的黑箱/非线性优化，不等于线性方程求解。
现 FEM 的局部稀疏求解能力也不意味着 openopt 已有兼容 solver。

如主席后续确需线性系统交接，应另经双方版本裁决而非改写六原请求：明确矩阵/RHS/解的文件 schema，
shape、索引基准、CSR/COO/稠密支持、dtype/device、有限性、对称/SPD/不定范围、重复项与排序规则、
原自由度/边界消元映射、物理单位与缩放、矩阵/RHS/配置/source hash、残差范数/容差及首因状态。
最小机验用独立已知解核 `norm(Ax-b)`，覆盖奇异/维数不符/非有限/设备不可用，
再独立读回真实 FEM 装配系统；速度场插值、QP 成功、退出 rc0 均不能充该验收。
这只是开发前需要确认的边界，不是新请求、对方承诺或已实现方案。

## 收口与下一步

本席只新增本文与 `/data/dev/opensim-s5-integration-handoff-20261011/tests/test_integration_handoff.py`。
无需更改原工具或对方资产。主席先取得两个接收方逐项响应，再确认相同接口版本、owner、样例、容差和独立测试命令，
最后各仓独占实现并由 OpenSim 独立复核同源原始输入/输出。当前外部阻塞就是六项响应和版本共识未取得；
未签收、未联合实现、未联合验收，不因为本专项 PASS 而改变这些状态。
