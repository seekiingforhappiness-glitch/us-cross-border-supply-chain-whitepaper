# STATUS.md — 项目状态（唯一状态源）

更新时间：2026-07-06

## 当前位置

- 阶段：**v0.2 + v0.3 + v0.4 全部收官**。三场景一本体：19 对象/23 关系/6 状态机/6 规则/
  12 动作/6 角色；断言 24/24 + 22/22 + 23/24（XE3 记失败）；九评估器全绿；评估 22 题。
- 工作模式：Fable 规划评审 + Opus 执行（X2-X4 三次委托全部一次过审）。
- 后续 v0.5 候选：单证号级 ER、风险评分模型（ground truth 已备）、作品集打磨。
  新场景须先立 plan 经人批准。

## v0.4 进度

- [x] X1：cost-manual-v0.4（Container/Invoice/InvoiceLine/ExpectedCost + Invoice 状态机 +
      R4-R6 + 提案类型扩展 + G4 门禁，勘误 P1-P3）；统一 JSON 0.4.0（19 对象/23 关系/6 状态机）；
      断言清单 24 条（XA/XB/XC/XD/XE）
- [x] X2：datagen/cost.py（Opus 子代理执行，Fable 评审通过）：柜 147（18 票多柜）、
      发票 296/行 824、基准 1161、gt 25 行（R4×5/R5×2/R6×18）、设计案例 CD-A..F；
      verify 第 7 段全绿；六评估器零回归；勘误 P4（G4 矩阵对齐 FOB/CIF/DDP）
- [x] X3（Opus 执行/Fable 评审通过）：cost_rules R4-R6（as_of 安全）+ MatchInvoice +
      dispute/accept/rebill 提案 + G4 门禁 + 费用工作台 + evaluate_cost（R/P 1.000）
      + test_cost_loop 全绿；八评估器零回归；断言 XB/XC 12 条关闭（累计 17/24）
- [x] X4（Opus 执行/Fable 评审通过）：发票查询工具 + cost_explain（F4 归因叙事）+
      评估 22 题全绿（XD 红线全过）；九评估器零回归
- [x] v0.4 收官：断言 23/24——**XE3 未达成如实记录**（增量 52% vs 目标 ≤25%；
      业务闭环本体仅 402 行，脚手架 956 行；结论修正见复盘 §0-v4）

## 全程里程碑

- [x] W0 规划：plan v0.2 + 决策日志 D1-D11 + 治理三件套（AGENTS/STATUS/CLAUDE）
- [x] W1 建模：11 对象 / 4 状态机 / 6 动作五要素 / 权限矩阵 / ontology JSON / 断言清单
- [x] W2 数据：datagen 六模块、15 设计案例、ground truth 双表、真实字段 Tier 1（D11）、
      ISO 6346 柜号等真实感升级，verify 42 项全绿
- [x] W3 管道：含噪源表重建对象层，精度 120/120、705/705；ER 映射；DQ 报告；数据导读
- [x] W4 引擎：R1-R3 as_of 时间旅行安全；KPI Recall/Precision 1.000；高危漏判 0
- [x] W5 闭环：A3-A6 动作层 + 控制塔 UI；C3 人工走查通过（Daniel 亲手走完 RSK-0044 全闭环）
- [x] W6 AI：工具白名单 + 确定性简报 + 可插拔 LLM；评估 11/11；越权拒绝且留审计
- [x] 收官：README 重写、docs/retrospective.md 复盘

## v0.3 进度

- [x] V1：admission-manual-v0.3（3 对象扩展 + 4 新对象 + 状态机 + 6 动作五要素 + 6 角色矩阵，
      勘误 N1-N5）；统一 ontology JSON 0.3.0（15 对象/17 关系/12 动作）；准入断言清单（22 条）
- [x] V2：datagen/admission.py（独立随机流零扰动）+ qms 四表 + 门禁真值 + pipeline 建表；
      verify 54 项全绿；六评估器零回归；AC4 复用性 SQL 预验证通过
- [x] V3：admission_actions B1-B6（G1/G2/G3 门禁）+ 准入工作台 UI（6 角色）+
      test_admission_loop 17 项全绿；七评估器全绿；断言 17/22 自动化通过
- [x] V3 人工验收完成（2026-07-06）：Daniel 四角色接力走完 AC-2026-0041 全链（审计链复核无误）；AC2 由 AppTest 机械验证
- [x] V4：AI 准入扩展（context 工具 + v0.1 §7 schema 简报 + B5/B6 禁用）；
      评估 17 题全绿（AD1-AD4 红线全过）；断言 22/22；复盘/README 更新，v0.3 收官

## 待人自选（不阻塞）

- [ ] LLM 实测：`pip install anthropic`、设置 ANTHROPIC_API_KEY 后
      `python3 -m agent.evaluate --llm` 与 `python3 -m agent.llm_agent "问题"`
- [ ] 录屏 demo（plan §11-W6 可选项）
- [ ] 阅读 docs/data-guide.md + docs/retrospective.md（建议精读复盘 §2/§3——学习目标对账）

## 复现命令（全链路）

```
python3 -m datagen.generate && python3 -m datagen.verify
python3 -m pipeline.build_ontology && python3 -m pipeline.evaluate
python3 -m engine.detect && python3 -m engine.evaluate
python3 -m app.test_closed_loop
python3 -m agent.evaluate
streamlit run app/streamlit_app.py
```

## 会话交接备注

任何模型接手：读 AGENTS.md §0。项目已收官，后续工作（v0.3）须先立新 plan 文档并经人批准。
