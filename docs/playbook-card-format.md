# 打法卡（SkillAsset）格式规范 v1

> 2026-07-12。三源融合：agency-agents 的文件骨架（MIT，见 docs/research/references/）
> × Hermes 的技能生命周期机制 × 本项目治理要求（决策日志 J 系列 / 设计 v2.1 §8）。
> 用途：C3"打法沉淀"的格式标准；C1 处置记忆引用的知识载体；对齐 agentskills.io 生态的
> markdown 事实标准（未来可直接吸收社区资产）。

## 1. 是什么、不是什么

打法卡=**一件事怎么做的可审计说明文**（markdown 文件）。AI 提案时检索引用它作依据，人可读可改可回滚。
- 不是代码（不可执行——执行永远走动作层）；
- 不是提示词全文（是被检索后**按需注入**的知识块——渐进披露）；
- 不是自动生效的（AI 可**提议**新卡/改卡，**人批准后生效**——治理式进化铁律）。

## 2. 文件骨架（五节，借鉴 agency-agents 结构）

```markdown
---
name: <kebab-case 唯一名>
description: <一句话：什么场景用这张卡>
version: <语义版本，如 1.2.0>
status: draft | approved | deprecated     # 只有 approved 可被检索引用
domain: <delay | cost | admission | procurement | warehouse | coordination>
trigger: <触发条件：什么规则/事件/对象状态命中时检索本卡>
approved_by: <实名>；approved_at: <日期>   # 生效审批记录
metadata:
  usage_count: 0        # 系统自动维护（Curator 治理依据）
  last_used: null
  source_cases: []      # 沉淀来源的先例 ID（可溯源）
---

# <标题>

## 适用场景
<2-4 行：这张卡管什么、不管什么（边界写清）>

## Critical Rules（硬约束，Never 句式）
- Never <绝不允许的事，如"Never 在未确认舱位前向客户承诺新交期">
- Never <…>（3-6 条；与冻结区/宪法冲突的卡不允许存在）

## 打法（步骤）
1. <步骤：判断什么 → 做什么 → 引用什么对象/动作>
2. <…每步注明对应的动作层动作名（如 propose_mitigation:expedite）>

## 经济账口径
<这类处置怎么算值不值：公式/阈值/参考数字的取数来源（必须可溯源到对象字段，禁止写死"历史成功率"类数字——数字由 C1 处置记忆运行时注入）>

## 检查表（执行前自查）
- [ ] <3-8 项，如"客户等级已确认""滞箱费倒计时已核对"…>
```

## 3. 生命周期（Hermes 机制 + 本项目治理）

| 环节 | 机制 |
|---|---|
| 诞生 | 两条路：人手写；或系统从先例聚类**提议**草稿（如同类处置满 5 次），附依据先例 ID |
| 生效 | **人批准**（status: draft→approved，记 approved_by/at）；未批准的卡不进检索 |
| 精炼 | patch 式定点修改（old→new），改动=新版本号，重走批准；不整篇重写 |
| 使用 | AI 提案引用卡时必须带 name+version（审计可回放"当时按哪版打法建议的"） |
| 治理 | usage_count/last_used 系统维护；90 天未用标 stale 提醒人审阅；关键卡可 pin |
| 回滚 | 版本历史即 git 历史；deprecated 卡保留不删（先例引用完整性） |

## 4. 安全铁律（本项目特有，高于格式）

1. **示例必须合成或脱敏**——卡内出现的公司名/联系人/价格一律虚构或打码（租户指纹检查过不了不许入库；防跨租户泄漏，评审 S8/B6）；
2. **数字必须可溯源**——卡里只写"取数口径"，不写死统计数字（"过去 N 次成功率 Y%"由 C1 运行时从 action_log 现算注入——防幻觉编造，全局规则 5）；
3. **卡不得授予权限**——打法只引用动作名，权限校验永远在 dispatch/动作层；与 FORBIDDEN 清单冲突的步骤写了也调不动；
4. **入库前过注入扫描**（卡会进提示词上下文——防投毒）。

## 5. 完整示例（合成数据）

```markdown
---
name: msc-eta-delay-triage
description: MSC 船司 ETA 推迟通知的分诊与处置起草
version: 1.0.0
status: approved
domain: delay
trigger: R1 命中且 carrier_scac=MSCU 且 delay_days>=3
approved_by: Daniel；approved_at: 2026-07-12
metadata: {usage_count: 0, last_used: null, source_cases: []}
---

# MSC ETA 延误分诊打法

## 适用场景
MSC 美线 ETA 推迟 ≥3 天的延误事件分诊与处置提案起草。不管：非 MSC 船司（通用卡另立）、
已产生滞箱费的存量案（走 cost 域卡）。

## Critical Rules
- Never 在货代书面确认新 ETA 前，把口头 ETA 写进对客户草稿
- Never 对 delay_days 未经两源核对（货代通知 vs 船司官网查询）的事件直接定 P1
- Never 给出未引用先例 ID 的"历史上通常"类表述

## 打法
1. 两源核对新 ETA（企微通知 vs 船司公开查询）→ 不一致则建 DQ 冲突记录，按较晚者预估
2. 沿对象图算影响：受影响订单行×断货天数×日销×毛利；核对滞箱费倒计时余量
3. 影响 < 抢救成本 → 提案 accept+客户告知草稿；影响 ≥ 成本 → 提案 expedite（附经济账）
4. A 级客户或大促期 → 同步 cs 同事起草客户预期管理草稿（人批后人工发出）

## 经济账口径
断货损失=Σ(受影响行 allocated_qty × sku 日销 × 毛利/件 × max(0, 新ETA+清关缓冲-库存耗尽日))；
取数：InventoryPosition.available / sku.daily_sales / 行成交价。禁止引用本卡外的估算数字。

## 检查表
- [ ] 新 ETA 已两源核对（或已建 DQ 冲突记录）
- [ ] 滞箱费倒计时已核对
- [ ] 经济账两个方案都算了（accept vs expedite）
- [ ] 引用的先例 ID 已附（无先例则写"无先例，首例"）
```
