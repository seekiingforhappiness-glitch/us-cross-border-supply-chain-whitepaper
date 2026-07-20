# ONBOARDING — 5 分钟上手（新会话 / 新模型 / 新人）

> 目标：任何模型或人从零到"能正确接着干"只需 5 分钟。这是**导航图**，不是状态源——
> 权威状态永远看 `STATUS.md`，协作宪法看 `AGENTS.md`（会话必须遵守其 §0 启动协议）。

## 1. 这是什么（30 秒）

参照 Palantir Ontology / AIP 理念自研的**跨境供应链控制塔**：一套本体（对象-关系-动作）
承载 **6 个业务闭环**，一套 maker-checker 治理，一层对象中心的可信 AI。
Python 3.11 + SQLite + Streamlit，可插拔 LLM（无 API key 时确定性 fallback，端到端可跑）。

**诚实边界**：数据是基于真实调研的高真实感**合成数据**，不接真实系统。证明的是方法与交付能力，
不是"已接好你的 ERP"。P/R=1.000 是合成数据上的能力证明，非真实战绩。

## 2. 五分钟路径（照做）

```bash
# ① 一条命令证明"全绿、不是花架子"（造世界→建本体→检测→四评估器 R/P=1.000）
python3 -m datagen.generate && python3 -m datagen.verify
python3 -m pipeline.build_ontology && python3 -m engine.detect
python3 -m engine.evaluate && python3 -m engine.evaluate_cost \
  && python3 -m engine.evaluate_procurement && python3 -m engine.evaluate_warehouse
python3 -m datagen.seed_demo_ops                    # 运营快照（活数据）

# ② 可信 AI 的两条硬证据
python3 -m app.test_scope_parity                    # agent 数据范围 == UI（逐字一致）
python3 -m app.test_agent_security                  # 6 对象 agent × 注入 288 次全被拒 + 审计

# ③ 起 UI：切角色 → 点对象 → 对象级 agent
streamlit run app/streamlit_app.py                  # 改代码后【完整重启】，别热重载
```

**读文档顺序**（按 AGENTS §0）：`AGENTS.md` → `STATUS.md` 顶部摘要 → 本文件 →
（面客）`docs/control-tower-overview.html` 全景图 + `docs/demo-walkthrough.md` 台本 →
（改代码）`docs/control-tower-plan-v0.2.md §4` 决策日志。

## 3. 心智模型（一定要建对）

- **对象-关系-动作网络**：世界是对象（Shipment/PurchaseOrder/Warehouse…），对象间有关系（可 explain_path），
  改变世界只能走**动作**（五要素：权限/前置/成功/失败/审计），不在按钮里、在动作层。
- **一条风险队列被 5 场景共用**：R1-R23 二十三种规则的风险都流进**同一个** `RiskEvent→Task` 闭环，
  走同一套派单/提案/审批(maker-checker)/关闭/审计。RiskEvent 锚点是多态的（shipment/po/supplier/warehouse）。
- **对象即工作台**：6 个核心决策对象有富工作台 + 只懂该对象的 agent；其余 27 对象走自动标准视图。
- **跨场景连成一张网**：采购收货→上架→库存→预留驱动履约→延误时查目的仓现货→拆单先发+余量改期。

## 4. 代码在哪（目录图）

| 层 | 目录 | 干什么 |
| --- | --- | --- |
| 造世界 | `datagen/` | 各场景独立随机流；R1-R23 注入 + ground truth（**只存 `data/truth/`、引擎禁读**） |
| 管道 | `pipeline/` | 判重/乱序消解/ER/MDM/DQ；状态只从事件流推导 |
| 本体 | `ontology/` + `data/ontology.sqlite` | 33 对象 · 状态机 · 动作五要素 · 角色权限（v0.8.0） |
| 引擎 | `engine/` | R1-R23 检测 + 评估器；as_of 时间旅行安全；真值禁读 |
| 应用 | `app/` | 角色导航 + 行级数据范围 + maker-checker + 6 富工作台 + 27 标准视图 |
| AI | `agent/` | 对象级 agent（继承 UI 范围 · 审批永不注册 · 越权动作层挡回）+ 确定性简报 |

## 5. 会咬你的规则（务必先知道）

- **§3 建模决策属于人**：新对象/新规则/scope 变更**不能自己拍板**——联网调研→设计提案→Daniel
  `AskUserQuestion` 亲批→写 `docs/control-tower-plan-v0.2.md §4` 决策日志→才写代码。
- **§5 真值只读**：ground truth 在 `data/truth/`，引擎**禁读**，改代码不许碰真值凑指标；
  数据改动后 `data/truth/` 的 md5 必须逐字节可证不变（否则你扰动了检测基线）。
- **controller 纪律**：子代理报告**不可信，只信输出**——每次独立重跑验证（本项目多次靠这抓到真 bug/假阳性）。
- **四条红线**（每次改动都要守）：R1-R23 全 P/R=1.000 不扰动 · agent 不越权 · 真值 md5 不变 · maker-checker/FORBIDDEN 不削弱。
- **streamlit 改代码后完整重启**，别热重载（旧模块驻留会造成假 ImportError）。

## 6. 安全改一处的套路

1. 先读 `STATUS.md` + 相关决策日志，确认这是 §4（AI 主导人验收）还是 §3（需人亲批）。
2. §4：小切片实现 → **独立重跑**验证（评估器全绿 + 四条红线未破）→ 更新 `STATUS.md` → `git commit`（§7 规范）。
3. §3：停下走 `AskUserQuestion`，不自己拍板。
4. 会话结束前必做：① 更新 `STATUS.md` ② `git commit`——否则不得声称工作完成（AGENTS §0 会话结束协议）。
