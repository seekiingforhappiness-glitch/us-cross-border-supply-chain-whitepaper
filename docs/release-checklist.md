# Release Checklist — 全链端到端验证 + 一键复现证据集

> 最近一次全链验证：**2026-07-09**（loop 迭代 14，主会话 controller 从零重跑全链、捕获真实输出）。
> 用途：发版 / 面客前的自检门。每条都有可复跑命令；任何一条变红都不得声称"可交付"。

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
- [x] **检测覆盖**：`engine.detect` → 152 事件 / 152 created / 0 merged，`by_rule` 覆盖全部 R1–R18
      （R1:25 R2:10 R3:16 · R4:5 R5:2 R6:18 · R7:8 R8:8 R9:7 R10:8 R11:7 R12:7 R13:5 · R14:4 R15:4 · R16:6 R17:6 R18:6）
- [x] **延误 R1–R3** `engine.evaluate` P/R=1.000
- [x] **费用 R4–R6** `engine.evaluate_cost` P/R=1.000
- [x] **采购 R7–R15** `engine.evaluate_procurement` P/R=1.000、灰区 0 误报
- [x] **仓储 R16–R18** `engine.evaluate_warehouse` P/R=1.000

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
