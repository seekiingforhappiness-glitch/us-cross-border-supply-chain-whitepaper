# AGENTS.md — freight-audit-agent 项目宪法

约束所有参与本子项目的 AI 会话。本项目位于 `数智供应链/apps/freight-audit-agent/`，是独立的
FDE 部署原型，**与控制塔沙盒隔离**：不共用其对象模型/规则/数据，只共用同一 git 仓库做持久化。

## 0. 定位（一句话）

为物流中小企业做的**承运商运费对账稽核 agent**：读承运商发票 → 和报价/费率/合同逐行比对 →
标出多收/重复/幽灵附加费/费率不符 → 附证据包 + 追回金额 → 人点确认。产出：**追回的真实美元**。
第一个客户 = Daniel 的雇主。面向真实部署，不是学习沙盒。

## 1. 核心纪律（不可违反）

1. **确定性对账，LLM 不算钱。** 金额比对、差异判定全用确定性 Python + 可算 precision/recall 的评估器。
   LLM 只做两件软活：字段归一化、争议信草稿。
2. **draft-not-send。** agent 只"发现差异+举证+建议"，绝不自动发送质询、绝不自动拒付/付款。
   一切外发动作进 outbox 的 draft 态，人点确认才动。
3. **每个差异必须附证据包**：发票原文行 + 对应费率/合同条款 + 计算过程。无证据的差异不成立。
4. **可信写操作**：幂等 key、显式 `as_of`（不用系统时钟）、可回滚、全程审计留痕。
5. **不夺决策权。** 拒付/质询的最终决定永远在人。
6. **误报是头号敌人。** "差异 ≠ 真错误"。人工驳回率是核心度量，冲垮信任比漏掉一笔更致命。
7. **RBAC 真实分离。** 三角色 reviewer/finance/manager：不同页面 + 数据范围 + maker-checker
   （追款发起 actor ≠ 追款授权 actor）。权限在**动作层**强制，不能只在 UI 藏按钮。

## 2. 数据边界

- 真实发票/费率/合同：**本地 `data/`、gitignore、绝不进版本库**。
- 冷启动用**基于真实调研的模拟数据**（`datagen` 固定种子 42 + ground-truth 差异 + 灰区样本），
  明确是"待真实数据替换的脚手架"。
- 不接 ERP 写接口、不接承运商 API、不碰付款系统——只读、只标差异、只生成 draft。

## 3. 栈

Python 3.11+ / pandas / SQLite / Streamlit / 可插拔 LLM（无 API key 时确定性 fallback，端到端可跑）。

## 4. 度量（DoD）

声称"能对账"前必须跑 `eval` 给出：**precision / recall / 人工驳回率 / 追回美元**。
不达标记失败，不改指标——诚实优先。基线（2026-07-08 重建）：P 0.938 / R 0.918 / 追回 $80,252.21（模拟数据上）。

## 5. 持久化纪律（本会话教训）

本项目曾因独立目录 + 仅本地 git 而**整个丢失**。现放入 `数智供应链` 仓库（有 GitHub 远程）。
**每完成一块必须 `git add apps/freight-audit-agent && git commit`，并尽量 push 到 origin** ——
本地 commit + 远程是唯一可靠的防丢手段。

## 6. 目录

```
config/  种子/容差/charge codes/白名单    datagen/ 模拟数据(种子42)+ground truth+灰区
ingest/  发票→结构化(LLM+人工兜底)        recon/   确定性对账引擎:12类判定+证据包
rules/   差异规则+容差+白名单              governance/ transaction+audit(action_log)+outbox
agent/   可插拔LLM工具(归一化/争议信草稿)  app/     Streamlit复核队列+RBAC三角色
eval/    precision/recall+误报率+追回额    data/    数据,gitignore
```
