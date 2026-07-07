# 跨境供应链智能运营原型（Cross-Border Supply Chain Ontology OS）

参考 Palantir Ontology 理念自研的统一本体原型，**一套 ontology 承载三个业务闭环**：

- **v0.2 延误风险控制塔**：运输延误 → 沿对象图定位受影响客户订单行 → 风险事件与任务 →
  人工处置回写（改期/加急/接受），全程审计
- **v0.3 SKU 准入报价**：销售建案 → 合规预审 → 物流方案 → 三情景成本 → 经理审批，
  三重门禁（critical 合规/DDP-IOR/流程完整性）挡住该挡的
- **v0.4 费用对账**：账单 → 基准匹配 → 超收/重复/计划外异常 → 争议/接受/按贸易术语转嫁，
  复用同一套风险闭环；滞箱费异常可归因到控制塔的延误事实（跨场景智能）

两场景共享 Supplier/Sku/Customer 对象（零重复定义）与全部治理机制（审计/权限/动作五要素/
AI 护栏），并经 SKU 生命周期咬合：准入批准的商品进入控制塔的履约世界。
AI 基于同一套对象和动作做解释与建议（proposal-only，17 题评估含越权/编造红线）。

**不是什么**：不是数据分析看板，不是聊天机器人，不是真实企业系统（数据为高真实感合成数据）。

## 快速开始

```bash
pip install -r requirements.txt

python3 -m datagen.generate && python3 -m datagen.verify            # 1. 生成数据（54 项验收）
python3 -m pipeline.build_ontology && python3 -m pipeline.evaluate  # 2. 重建对象层（精度 100%）
python3 -m engine.detect && python3 -m engine.evaluate              # 3. 风险检测（查准查全 1.0）
python3 -m app.test_closed_loop                                     # 4. 控制塔动作闭环
python3 -m app.test_admission_loop                                  # 5. 准入闭环与门禁（17 项）
python3 -m engine.evaluate_cost && python3 -m app.test_cost_loop    # 6. 费用引擎与闭环（v0.4）
python3 -m agent.evaluate                                           # 7. AI 层评估（22 题）
streamlit run app/streamlit_app.py                                  # 8. UI（控制塔+准入+费用工作台）
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
