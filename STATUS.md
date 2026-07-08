# STATUS.md — 项目状态（唯一状态源）

更新时间：2026-07-07

## 当前位置

- 阶段：**v0.2 + v0.3 + v0.4 全部收官**。三场景一本体：19 对象/23 关系/6 状态机/6 规则/
  12 动作/6 角色；断言 24/24 + 22/22 + 23/24（XE3 记失败）；九评估器全绿；评估 22 题。
- 工作模式：Fable 规划评审 + Opus 执行（X2-X4 三次委托全部一次过审）。
- **v0.5 作品集打磨已完成**：架构图/演示台本/一页纸/案例文章 + README 导航。
- **v0.6 收尾双专题已完成**（final-plan-v0.6，H1-H3）：
  Y1 风险评分模型——手写 LR 零依赖，早警 AUC 0.969；静态 0.75 诊断为快照删失混杂（H1 勘误）
  Y2 单证号级 ER——milestone 源表去内部 ID，解析率 98.3%，typo 停车表 1:1，known-loss 如实计数
- **候选清单已清空。项目全部完成**：十评估器全绿，~6,000 行，28 次提交。
  剩余事项全部只有人能做：LLM 实测（配 OpenAI API key）、录屏、git push、通读并个人化 article.md。
- **OpenAI LLM provider 已配置**：`agent.llm_agent` 默认 `AGENT_PROVIDER=openai` +
  `AGENT_MODEL=gpt-5.5`；Anthropic provider 保留。注意：ChatGPT/Codex 订阅通道只适用于
  Codex 客户端，项目 Python 代码真实 LLM 调用仍需 `OPENAI_API_KEY`。
- **控制塔 UI 视觉升级已完成**：基于生成概念图重做 Streamlit 设计系统，暗色未来感命令中心、
  真实 KPI 顶栏、深色表格、侧栏/Tab/Form/Button 统一样式；动作逻辑与数据层未改。
- **成熟控制塔全面升级计划已进入逐 Task 审批执行**：计划路径
  `docs/superpowers/plans/2026-07-07-control-tower-maturity-upgrade.md`；approval pack 路径
  `docs/control-tower-maturity-gap-assessment.md`、`docs/demo-assertions-maturity.md`；M1-M6 已完成，
  M7 已实现待 controller review；M8+ 未获批前不改 ontology/规则/业务范围
  （MA 系列只是验收检查）。
- **M1 Task 1 已完成并通过 controller review**：Daniel 已批准 demo named actor + maker-checker 的动作边界升级；
  已按批准范围实现 `app/action_context.py`、动作层 proposer/approver 校验、稳定任务 ID、事务 helper 与 schema 文档；四项动作回归测试全绿。
- **M2 Task 2 已完成并通过 controller review**：Daniel 已批准 demo named owner + SLA state +
  escalation_level；实现范围限定为 `Task` 字段、demo roster 分配、显式 `as_of_date` SLA 计算和
  UI 呈现，不接真实用户目录/通知/排班系统，不推进 M3+。子代理规格/质量复审均已通过；验证：
  work_queue、action_governance、全链路 datagen/pipeline/engine/app/agent 回归全绿。
- **M3 Task 3 已完成并通过 controller review**：Daniel 已批准 canonical event envelope + raw lineage
  的模拟 source-truth 基础设施；实现范围限定为 raw `tms_milestones` source identity、
  `source_events` 表、idempotency key 与 pipeline lineage 评估。不接真实 carrier/API、不做 DQ issue
  workflow、outbox、MDM crosswalk 或业务规则。子代理规格/质量复审均已通过；验证：event_envelope、
  datagen、pipeline、engine、app、agent 回归全绿，engine 仍为 Recall=1.000 / Precision=1.000。
- **M4 Task 4 已完成并通过 controller review**：Daniel 已批准 simulated MDM crosswalk resolver；
  本次仅新增 external ID → internal ID crosswalk resolver、`mdm_crosswalk` 表与 key-level DQ 可观测计数
  （resolved=108 / ambiguous=1 / unresolved=1，candidate_rows=111）。
  不接企业 MDM 平台，不做自动主数据合并、人工治理队列、真实主数据同步、M5 graph traversal、
  relationship registry、DQ issue workflow 或 outbox。两轮子代理规格/质量复审均已通过；验证：
  MDM、datagen、pipeline、engine、app、agent 回归全绿，engine 仍为 Recall=1.000 / Precision=1.000。
- **M5 Task 5 已完成并通过 controller review**：Daniel 已批准 SQLite `object_relationships` registry +
  可解释 graph traversal；本次仅新增通用关系表、`engine.graph.explain_path`、既有对象关系投影、
  RiskEvent 关系边与只读 AI 查询工具。不推进 M6+，不做 DQ issue、outbox、AI redaction、
  新业务对象或新风险规则。子代理规格/质量复审均已通过；验证：graph、datagen、pipeline、engine、
  app、agent 回归全绿，engine 仍为 Recall=1.000 / Precision=1.000。
- **M6 Task 6 已完成并通过 controller review**：Daniel 已批准把模拟 pipeline 的 unresolved / parking
  记录升级为可运营 `dq_issues` 队列；本次仅新增 deterministic DQ issue 创建、assign / close
  两个处置动作、审计留痕、pipeline/evaluate 门禁、Streamlit “DQ 处置”入口和数据导读/断言同步。
  不推进 outbox、AI redaction、M7+，不做真实源系统修复、外部回写、新风险规则或新噪声类型。
  子代理规格/质量复审均已通过，质量复审指出的 DQ 幂等冲突、事务回滚、默认队列筛选和 detail
  JSON 容错均已修复；验证：DQ loop、datagen、pipeline、engine、app、agent 回归全绿，engine 仍为
  Recall=1.000 / Precision=1.000。
- **M7 Task 7 已实现，待 controller review**：Daniel 已批准模拟集成写回 outbox；本次范围限定为
  SQLite `integration_outbox`、deterministic idempotency key、success/failure 状态标记、
  已批准 reschedule 动作的模拟写回与脚本测试/断言同步。不接真实 ERP/TMS/QMS，不发送网络请求，
  不新增 retry worker，不推进 M8+。子代理验证：outbox、pipeline.evaluate、engine.evaluate、
  closed_loop 全绿；待主会话规格/质量复核。

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

- [ ] LLM 实测：`pip install openai`、设置 OPENAI_API_KEY 后
      `python3 -m agent.evaluate --llm` 与 `python3 -m agent.llm_agent "问题"`
- [ ] Anthropic 备用实测：`pip install anthropic`、设置 ANTHROPIC_API_KEY 后
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

## 会话交接备注（任何模型接管的启动路径）

1. 读 AGENTS.md（§0 启动协议 + §4 软知识——与 Daniel 协作的方式）
2. 读本文件（唯一状态源）；深入某场景再读对应 plan/manual
3. 自检接管质量：跑一遍复现命令（十个评估器应全绿），然后向 Daniel 用三句话
   复述项目现状——复述与现实一致即交接成功
4. 新工作一律先立 plan 经 Daniel 批准（候选清单已清空，无遗留承诺）
5. 剩余人工事项（无需模型）：LLM 实测、录屏、git push、article.md 个人化
