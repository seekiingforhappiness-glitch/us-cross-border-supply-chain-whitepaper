# STATUS.md — 项目状态（唯一状态源）

更新时间：2026-07-06

## 当前位置

- 阶段：**v0.2 全部完成收官**。W0-W6 六周计划关闭，24/24 断言、全链路评估全绿。
- 项目处于可演示、可交接、可扩展状态。下一动作由人决定（v0.3 方向见 docs/retrospective.md §5）。

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
