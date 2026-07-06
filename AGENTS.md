# AGENTS.md — 项目协作宪法

本文件约束所有参与本项目的 AI 会话（Claude、Codex 及其他）。每行规则都会改变行为；无行为影响的行应被删除。目标 ≤150 行。

## 0. 会话启动协议

按此顺序建立上下文，不要跳步：

1. 本文件（宪法与约定）
2. `STATUS.md`（当前状态、进行中任务、阻塞项——唯一状态源）
3. `docs/control-tower-plan-v0.2.md`（执行依据，按当前任务精读相关章节）

按需再读：`ontology/control-tower-ontology.json`、`docs/demo-assertions.md`、`docs/weekly-notes/`。
`docs/cross-border-ontology-manual.md` 与 `ontology/sku-admission-ontology.json` 是 v0.1 资产：**只读**，仅作模板参考。

会话结束协议（模型连续性保障——项目必须随时可被任何模型接手）：结束前必须 ① 更新 `STATUS.md` ② `git commit`（信息规范见 §7）。未做这两步不得声称本次会话工作完成。

## 1. 项目定位与反目标

一句话：把跨境履约链路建模为对象-关系-动作网络的运营原型——延误发生时，系统沿对象图定位受影响订单行、量化影响、生成风险事件与任务，人执行动作回写状态，AI 基于同一套对象和动作做解释与建议。

反目标（发现工作在朝这些方向漂移时立即停下并报告）：

- 不是数据分析看板——没有动作回写的功能一律不做
- 不是聊天机器人——AI 只能通过注册的工具函数访问对象和动作
- 不是真实企业系统——不接真实 API、真实报关逻辑、真实企业数据

## 2. 事实源层级

冲突时以上游为准，并向人报告冲突，不得自行调和：

```text
docs/control-tower-plan-v0.2.md  >  ontology/control-tower-ontology.json
>  docs/cross-border-ontology-manual.md (v0.1)  >  白皮书 (领域常识参考)
```

## 3. 决策保护（最高规则）

plan v0.2 §4 的决策日志（D1-D8 及未来追加项）是 append-only 的。

变更协议：提出方案（含 trade-off）→ 人批准 → 在决策日志追加记录 → 才允许改代码。跳过任何一步即违规。

以下裁决**永远属于人，AI 只提供带 trade-off 的选项**：

- 建模粒度（对象增删、header/line 拆分、关系对象化）
- KPI 目标值与验收标准
- 范围增减（噪声类型、风险规则、视图、依赖库）
- 每周验收是否通过

## 4. 人机分工（学习目标保护）

本项目首要产出是人的理解，其次才是能跑的原型。

- Ontology 定义、状态机、动作五要素：人主导，AI 提案并审查
- 数据生成器、管道、引擎、UI、测试代码：AI 主导，人验收
- AI 每做一个非平凡设计选择，须附 ≤5 行"为什么这样建"的说明
- 单次交付以一个验收点为单位；禁止一次性生成超出人可审阅量的代码

## 5. 禁止清单

- 不新增对象类型、噪声类型、风险规则、依赖库、顶层目录（须走 §3 变更协议）
- 不修改 KPI 目标值；不为让评估通过而修改 `engine/evaluate.py` 判定逻辑或 ground truth 数据（W2 验收后 `expected_risk_events` / `injected_noise_log` 对 AI 只读）
- 不触碰 v0.1 两个文件；不重写已存在的工具函数——写新代码前先查 repo 有没有现成的
- 不硬编码应进配置的参数；不隐式调用系统当前时间——一律显式 `as_of_date`（D8）
- 不提交 `data/` 生成物、密钥、任何真实企业数据
- 不留未在 `STATUS.md` 登记的半成品；不确定的事写"待人确认"，不猜

## 6. 完成定义（DoD）

一项任务"完成" = 代码可运行 + 对应验证通过 + 相关文档同步 + `STATUS.md` 已更新。四项缺一即未完成，不得声称完成。

- 每周验收：对 `docs/demo-assertions.md` 相应断言打勾，写入 `docs/weekly-notes/`
- W2 起：数据生成必须以固定种子复现后才算完成
- W4 起：声称引擎工作前必须运行 `python engine/evaluate.py` 并原样粘贴 precision/recall 输出

## 7. 工程约定

- 栈锁定：Python 3.11+ / pandas / SQLite / Streamlit。不加框架、不换存储、不上图数据库
- 命名：主键 `*_id`；表与字段 snake_case；金额字段 `_usd` 后缀；时间戳 UTC ISO8601
- 配置：datagen 与引擎参数全部进 `config/*.yaml`，含随机种子
- 提交信息：`[W周次] 说明`，涉及决策引用编号，如 `[W1][D3] 建 allocation 对象表`
- 秘钥走 `.env`（已 gitignore）

## 8. 目录所有权

```text
docs/       规划与验收文档（plan 修改须人批准）
ontology/   ontology JSON（W1 后修改须走 §3）
config/     参数与种子
datagen/    数据生成器（W2 主战场）
pipeline/   清洗与对象层构建（W3）
engine/     风险规则、影响传播、评估（W4；evaluate.py 见 §5）
app/        Streamlit（W5）
agent/      AI 工具函数与评估集（W6）
data/       生成物，gitignore，不进版本库
```

跨目录改动须在交付说明中单独列出原因。

## 9. 本文件的维护

每周复盘时审查：被违反的规则→加强或删除；无行为影响的行→删除。修改本文件须人批准。宪法失去执行力比没有宪法更糟。
