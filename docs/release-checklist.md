# Release Checklist — 全链端到端验证 + 一键复现证据集

> 最近一次全链验证：**2026-07-09**（loop 迭代 14，主会话 controller 从零重跑全链、捕获真实输出）。
> 用途：发版 / 面客前的自检门。每条都有可复跑命令；任何一条变红都不得声称"可交付"。

## W. WAIVED 豁免通道（V16③ 2026-07-16 Daniel 批准设立并认领签字人）

本清单原本"全绿 or 不可交付"一刀切。WAIVED 是第三条**诚实的路**：某条已知是红的、但有兜底，
可以带着"红得明明白白"交付，条件是**每条豁免必须登记四要素**：

| 要素 | 要求 |
|---|---|
| 签字人 | **仅 Daniel**（不可代签、不可由 AI 或子代理记账时自行补签） |
| 原因 | 白话写清为什么这条暂时做不全 |
| 期限 | 到期必须复审（转绿或重新签） |
| 补偿措施 | 红着的期间靠什么兜底 |

**不可豁免清单（任何情况下不得 WAIVED，映射既有红线）**：
① 真值指纹不变（datagen 真值 md5）②冻结区四动作不入任何 AI 工具面 + maker-checker 双人复核
不削弱 ③ AI 越权全被拒 = 0 ④ 业务库只读边界（影子测量/评估不写业务库）⑤ 审计留痕不可关。

**登记表（append-only，当前无豁免）**：

| 日期 | 条目 | 原因 | 期限 | 补偿 | 签字 |
|---|---|---|---|---|---|
| — | （尚无豁免记录） | | | | |

## 0. 一键复现（从零到全绿）

```bash
cd ~/Desktop/数智供应链 && pip install -r requirements.txt
# ① 造世界 + 可复现性
python3 -m datagen.generate && python3 -m datagen.verify
# ② 建本体 + 管道
python3 -m pipeline.build_ontology && python3 -m pipeline.evaluate
# ③ 检测 + 五评估器（R1–R18 全 P/R=1.000）
python3 -m engine.detect
python3 -m engine.evaluate && python3 -m engine.evaluate_cost \
  && python3 -m engine.evaluate_procurement && python3 -m engine.evaluate_warehouse
python3 -m datagen.seed_demo_ops
python3 -m pipeline.apply_seam_columns    # 波2-2b 接缝列链尾幂等步（tasks/risk_events 等引擎表补 version/tenant_id）
# 重建确定性门（尺子=业务逻辑指纹，遥测表豁免见 pipeline/db_digest.py 头注）：两次全链重建
# python3 -m pipeline.db_digest 输出必须一致（2026-07-16 起文件 md5 因 G-Ledger 真实时间戳在重建间合法不同）
# ④ 五场景闭环
python3 -m app.test_closed_loop && python3 -m app.test_cost_loop && python3 -m app.test_admission_loop \
  && python3 -m app.test_procurement_loop && python3 -m app.test_sourcing_loop && python3 -m app.test_warehouse_loop
# ⑤ 对象中心
python3 -m app.test_object_workbench && python3 -m app.test_object_workbench_admission \
  && python3 -m app.test_object_workbench_task_invoice && python3 -m app.test_object_workbench_warehouse \
  && python3 -m app.test_standard_object_view
# ⑥ 可信 AI + 治理
python3 -m app.test_scope_parity && python3 -m app.test_region_scope && python3 -m app.test_agent_security \
  && python3 -m app.test_data_scope && python3 -m app.test_rbac_nav && python3 -m app.test_executive_view \
  && python3 -m app.test_smoke && python3 -m agent.evaluate
# ⑦ UI
streamlit run app/streamlit_app.py          # 改代码后【完整重启】，别热重载
```

## 1. 发版门（release gates）—— 2026-07-09 全部 ✅

### A. 检测精度 · R1–R18 全 P/R = 1.000
- [x] **可复现性**：同种子逐字节一致（`datagen.verify` PASS）、管道评估 PASS（`pipeline.evaluate`）
- [x] **本体一致性闸门（桥1，V5 起新增发版门）**：`python3 -m pipeline.ontology_lint --strict`
      退出码 0——本体↔表结构↔权限字典↔AI 工具四类断言零差异才放行（declared_only 显式豁免
      不阻断但每次报告可见；2026-07-14 M3 后达成 strict-clean 并入此门）
- [x] **检测覆盖**：`engine.detect` → 152 事件 / 152 created / 0 merged，`by_rule` 覆盖全部 R1–R18
      （R1:25 R2:10 R3:16 · R4:5 R5:2 R6:18 · R7:8 R8:8 R9:7 R10:8 R11:7 R12:7 R13:5 · R14:4 R15:4 · R16:6 R17:6 R18:6）
- [x] **延误 R1–R3** `engine.evaluate` P/R=1.000
- [x] **费用 R4–R6** `engine.evaluate_cost` P/R=1.000
- [x] **采购 R7–R15** `engine.evaluate_procurement` P/R=1.000、灰区 0 误报
- [x] **仓储 R16–R18** `engine.evaluate_warehouse` P/R=1.000
- [x] **资金流 R19–R21** `engine.evaluate_finance` P/R=1.000（F1，2026-07-14 起新增发版门）

### A+. 本体运行时化与 AI 通道（V5-V6 API 层，2026-07-14 起新增发版门）
- [x] **权限/工具单一权威源**：`app/test_ontology_runtime.py` 迁移一致性全绿（本体解释生成==
      人批基线；含 frozen∩exposed 变异防线三用例、traverse 四承载+判别式正反向）
- [x] **MCP server 四门槛**：`agent/test_mcp_server.py` 全绿（角色过滤/声明脱敏/审计入库
      llm_calls/冻结区协议层拦截+7 写工具走既有 dispatch 无第二写路径）
- [x] **对抗安全**：`app/test_agent_security.py` 288 注入全拒（扩资金流工具后）
- [x] **API 聚合层**：`pytest apps/api/` 全绿（五路由本体驱动+驾驶舱三端点手工 SQL 对照+
      双世界+脱敏；36 用例）
- [x] **驾驶舱构建**：`cd apps/cockpit && npx tsc --noEmit && npm run build` 零错

### A++. 企业级交付收口（2026-07-14 全部回填）
- [x] 透视镜 v3 四板块交付+既有 12 视图零回归（50fe2ef：搜索/影响分析/动作工具联动/等距归位）
- [x] Task 动作词表对齐后双世界全对象 model_validate 零警告（201af80：sim 207+real 20 全过，
      顺带治愈 seed 路径 6 条 finance 任务静默失败的预存 bug）
- [x] 前端死样式清理后全交互回归（e960d09：净减 486 行、268 类零残留、译名归一）
- [x] README/ONBOARDING 对齐 0.11.2 形态（六场景/R1-R21/三座桥/MCP/双 React 应用/双世界）

### B. 五场景闭环（风险→派单→提案→审批 maker-checker→审计）
- [x] 延误 `test_closed_loop` · 费用 `test_cost_loop` · 准入 `test_admission_loop`
- [x] 采购 `test_procurement_loop` · 富化 `test_sourcing_loop` · 仓储 `test_warehouse_loop`（含现货救延误）

### C. 对象中心（6 富工作台 + 27 标准视图）
- [x] `test_object_workbench` / `_admission` / `_task_invoice` / `_warehouse` 全绿
- [x] `test_standard_object_view` 全绿（27 标准视图自动派生、对象图可导航）

### D. 可信 AI + 治理（本仓库护城河）
- [x] **agent 数据范围 == UI** `test_scope_parity`（逐字一致）
- [x] **多区域可见过滤** `test_region_scope`（manager 见全部；CN 运营只见 CN、US 只见 US；分区无泄漏）
- [x] **agent 越权全被拒** `test_agent_security`（6 对象 agent × 注入 288 次全 refused + 292 条 denied 审计）
- [x] **行级数据范围** `test_data_scope` · **真·角色导航** `test_rbac_nav` · **全局 KPI** `test_executive_view`
- [x] **健壮性** `test_smoke`（真实库+空库各 6 角色 0 未捕获异常、空/边界优雅降级、空库授权仍拦截）
- [x] **AI 评估 29 题** `agent.evaluate`（按角色重定、含越权/编造红线、判定不放水）

### E. 真值防线（防"改真值凑指标"）
- [x] ground truth 只在 `data/truth/`、引擎禁读
- [x] **md5 逐字节可证不变**——8 个真值文件在本会话 14 次迭代全程 byte-identical：

```
7fb07724fd5201dedd95ada66c5e25d3  expected_admission_gates.csv
21d2ac37eb1dbb6eb5ef73320dfb3cf6  expected_cost_anomalies.csv
d08428e44de772d3dcf445e39411bf4a  expected_procurement_risks.csv
9da8897c1ac1f63b30836aba0425120d  expected_risk_events.csv
4e7f6add1b36d1835503cc0a0c072ee1  expected_sourcing_risks.csv
5e10862f55f048d4bed0db351ed2a439  expected_warehouse_risks.csv
14ca06f6d39f16c8f47b989ce5f49cd4  injected_noise_log.csv
e41f8bce9b1803c920102786ac4d6f97  world_snapshot.json
```

### F. 面客诚实边界（demo 前必讲）
- [x] 数据为基于真实调研的**高真实感合成数据**，不接真实企业系统
- [x] P/R=1.000、追回额是**模拟数据上的能力证明**，非真实战绩
- [x] 证明的是"把供应链建成本体、AI 可信嵌入"的**方法与交付能力**（FDE 打法）

## 2. 结论

23 个评估器 / 测试模块（4 检测精度 + 6 闭环 + 5 对象中心 + 8 可信 AI/治理，含 agent.evaluate）
+ 2 个可复现性门（datagen.verify / pipeline.evaluate）全绿；R1–R18 全 P/R=1.000；真值 md5 逐字节不变；
6 角色 UI 0 异常；maker-checker / FORBIDDEN / 数据范围继承 未被削弱。**当前分支满足面客交付门。**
