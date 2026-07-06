# 跨境供应链智能运营原型（Cross-Border Supply Chain Ontology OS）

参考 Palantir Ontology 理念自研的**延误风险控制塔**原型：把中国→美国跨境履约链路建模为
对象-关系-动作网络，当运输延误发生时，系统沿对象图定位受影响的客户订单行、量化影响、
生成风险事件与任务，由人执行动作并回写状态，AI 基于同一套对象和动作做解释与建议
（proposal-only）。

**不是什么**：不是数据分析看板，不是聊天机器人，不是真实企业系统（数据为高真实感合成数据）。

## 快速开始

```bash
pip install -r requirements.txt

python3 -m datagen.generate && python3 -m datagen.verify            # 1. 生成数据（42 项验收）
python3 -m pipeline.build_ontology && python3 -m pipeline.evaluate  # 2. 重建对象层（精度 100%）
python3 -m engine.detect && python3 -m engine.evaluate              # 3. 风险检测（查准查全 1.0）
python3 -m app.test_closed_loop                                     # 4. 动作闭环测试
python3 -m agent.evaluate                                           # 5. AI 层评估（11 题）
streamlit run app/streamlit_app.py                                  # 6. 控制塔 UI
```

演示走查脚本见 `docs/demo-assertions.md`（24 条断言）；看不懂数据先读 `docs/data-guide.md`。

AI 对话模式（可选，需 `pip install anthropic` 并设置 `ANTHROPIC_API_KEY`）：

```bash
python3 -m agent.llm_agent "SHP-2026-0099 为什么有风险？该怎么处理？"
```

## 架构（六层，对应 plan §四）

| 层 | 目录 | 要点 |
| --- | --- | --- |
| 模拟数据 | `datagen/` | 干净世界+emit 层污染；15 个确定性设计案例；ground truth 双表（D6） |
| 数据管道 | `pipeline/` | 判重/乱序消解/状态冲突纠正/供应商 ER；状态只从事件流推导 |
| Ontology | `ontology/` + `data/ontology.sqlite` | 11 对象、4 状态机、6 动作五要素、权限矩阵 |
| 风险引擎 | `engine/` | R1 延误传导 / R2 文件缺失 / R3 静默停滞；as_of 时间旅行安全（D8） |
| 运营应用 | `app/` | 动作层（权限+审计）与 UI 分离；控制塔四视图 + 角色切换 |
| AI 协同 | `agent/` | 工具注册（审批/关闭永不暴露）；确定性简报保证溯源；LLM 可插拔 |

## 治理文件（AI 协作项目的宪法）

- `AGENTS.md`——协作规则与禁止清单（任何 AI 会话从这里开始）
- `STATUS.md`——唯一状态源
- `docs/control-tower-plan-v0.2.md`——总体规划与决策日志 D1-D11（append-only）
- `docs/weekly-notes/`——每周复盘；`docs/retrospective.md`——项目复盘

## 附属资产

- `index.html` / `美国跨境供应链实战白皮书_可视化增强版.html`——美国跨境供应链实战白皮书（领域知识参考）
- `docs/cross-border-ontology-manual.md` + `ontology/sku-admission-ontology.json`——v0.1
  SKU 准入报价场景（v0.3 候选：与控制塔共享 Supplier/Sku/Customer，验证 ontology 复用性）
