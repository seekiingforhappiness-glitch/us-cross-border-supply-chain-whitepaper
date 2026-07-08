# Release Notes — 数智供应链控制塔 v0.8（五场景 · 对象中心 · 可信 AI）

发布：2026-07-09 ｜ 分支：`codex/maturity-upgrade-execution` ｜ 全绿证据：`docs/release-checklist.md`

## 一句话

一套本体（对象-关系-动作）承载 **5 个业务闭环**，一套 maker-checker 治理，一层对象中心的可信 AI。
参照 Palantir Ontology / AIP 理念自研的跨境供应链控制塔原型。

> **诚实边界**：基于真实调研的**高真实感合成数据**，不接真实系统。证明的是方法与交付能力（FDE 打法），
> 不是"已接好你的 ERP"。P/R=1.000、追回额是模拟数据上的能力证明，非真实战绩。

## v0.8 全景（当前能力）

| 维度 | 状态 |
| --- | --- |
| 业务场景 | **5**：延误运营(R1-R3) · 费用稽核(R4-R6) · 准入合规(门禁 G1-G4) · 采购(R7-R15) · 仓储库存(R16-R18) |
| 风险规则 | **R1-R18 全 P/R = 1.000**（检测 152 事件覆盖全部 18 规则，灰区 0 误报） |
| 对象类型 | **33**（ontology v0.8.0，状态机 · 动作五要素 · 角色权限） |
| 对象工作台 | **6 富工作台**（RiskEvent/Task/Invoice/AdmissionCase/PurchaseOrder/Warehouse）+ **27 标准视图** |
| 对象级 agent | permission-aware：继承 UI 数据范围 · 审批永不注册 · 越权动作层挡回（均独立对抗验证） |
| 角色 | 6 角色真·导航 + 行级数据范围（含多区域 region 可见过滤） |
| 跨场景网 | 采购收货→上架→库存→预留驱动履约→**延误现货救援**（拆单先发+余量改期） |

## 本轮（loop 15 迭代）交付的加固

以"迭代到第 15 次交付完整控制塔"的自定步调 loop 完成，全程 controller **独立重跑验证**（不信报告只信输出），
每次守四条红线（R1-R18 全 P/R=1.000 不扰动 · agent 不越权 · 真值 md5 不变 · maker-checker/FORBIDDEN 不削弱）：

- **能力补齐**（1-2）：Warehouse 升第 6 富工作台 + 对象级 agent；全局 Executive 一页视图（manager 一屏看 5 场景 KPI）。
- **文档全貌**（3-5, 10, 12）：architecture / README 刷新到 5 场景；统一验收清单；retrospective + field-gap 追加采购/仓储全貌；**新建 ONBOARDING.md**（5 分钟上手）+ 修正 STATUS 顶部摘要过时数字。
- **质量加固**（6-9, 13）：统一对抗安全 sweep 测试（6 对象 agent × 注入 288 次全被拒）；approve_mitigation 去重（行为保持）；多区域可见过滤证明；agent eval 扩采购/仓储（22→29，判定不放水）；**健壮性 pass 修 2 个真崩溃点** + 冒烟测试。
- **交付物**（11, 14-15）：面客一页纸可视化 Artifact（塔台雷达全景图，自包含单文件）；全链端到端 release checklist；本 release notes 收官。

**发现的真实 bug（本轮修复）**：① 采购/仓储/预付款风险（shipment_id 为空）的任务台简报 `KeyError`；
② 准入 tab 空库 `.index()` `ValueError`。均在健壮性 pass 中修复并锁进冒烟测试。

## 治理护城河（这套系统真正的价值）

- **决策日志 append-only**：D/E/F/M/P1-P3/W1，每次建模变更有编号、理由、Daniel 亲批（业务语义决策走人裁决）。
- **真值引擎禁读、md5 逐字节可证不变**：8 个真值文件在本会话 15 次迭代全程 byte-identical，防"改真值凑指标"。
- **AI 护栏靠架构不靠提示词**：审批工具从未注册、事实字段只能来自对象、权限对人对 AI 同规、agent 数据范围==UI。
- **诚实文化**：如实记录的 KPI 失败（XE3）、多次拒绝作弊改真值、controller 每次独立重跑。

## 收官验证快照（2026-07-09）

- 全评估器 / 测试模块全绿（详见 `docs/release-checklist.md`：23 模块 + 2 可复现性门）。
- R1-R18 全 P/R=1.000；6 角色 UI 0 未捕获异常；真值 md5 汇总不变。

## 从这里开始

- 新接手：`ONBOARDING.md`（5 分钟）→ `STATUS.md`（状态源）
- 面客：`docs/control-tower-overview.html`（一页纸）+ `docs/demo-walkthrough.md`（5 分钟台本）
- 发版自检：`docs/release-checklist.md`
- 决策与理由：`docs/control-tower-plan-v0.2.md §4`
