# 费用对账断言清单（v0.4-X1）

依据：cost-manual-v0.4。原则不变：对断言打勾；机械可验证；X2-X4 逐段自动化。

## 固定设计案例（X2 确定性生成）

```text
CD-A 超收     ：某票 OFT 按基准 130% 开票 → R4 high，dispute 流程走通
CD-B 跨票重复 ：同柜 THC 在两张发票各计一次 → R5，重复金额=第二笔
CD-C 滞箱归因 ：确定性选取"延误且已妥投"的船（本种子=SHP-2026-0009，FOB，延误 8 天）
               产生 DET 6 天×$150 → R6 high，root_cause 含延误归因（F4），FOB 可 rebill
               （X2 勘误：原设想用 SHP-2026-0099，但在途船不会收到滞箱账单——真实性优先）
CD-D DDP 转嫁被拒：DDP 票的 DET 异常提 rebill → A5 的 G4 门禁拒绝
CD-E 干净发票 ：全部行恰在基准内 → 零异常，发票直接 approved（不误报反例）
CD-F 同票重复 ：同一发票内同费种同柜两行 → R5
```

## A. 数据与迁移（X2）

- [x] XA1. Container 迁移：每票 ≥1 柜、恰一个 primary、柜号 ISO 6346 校验位合法
- [x] XA2. 多柜升级：≥15 票 FCL 有 2-3 柜，柜级费用按柜开票
- [x] XA3. 控制塔零回归：shipment 原字段未动，v0.2/v0.3 全部评估器全绿
- [x] XA4. 发票 DQ：total=Σ行、柜级费种必带柜号、基准表无 DET/DEM/ACC/CHS
- [x] XA5. ground truth：注入异常与 expected_risk_events(R4-R6) 严格 1:1

## B. 引擎（X3）

- [x] XB1. R4-R6 对 ground truth：Recall≥95%（高额漏判=0）、Precision≥85%
- [x] XB2. CD-A：R4 检出，affected_value=超收部分（非全额）
- [x] XB3. CD-B/CD-F：R5 检出跨票与同票重复，首行不计入
- [x] XB4. CD-C：R6 检出且 root_cause 含"延误"与天数（F4 归因）
- [x] XB5. CD-E：干净发票零异常、状态直接 approved（不误报）
- [x] XB6. as_of 安全：issue_date > as_of 的发票不参与检测

## C. 动作与闭环（X3）

- [x] XC1. 费用异常出现在**同一个风险队列**，AssignTask/审批/关闭全程复用（F1）
- [x] XC2. dispute 提案批准 → invoice.status=disputed；accept → approved
- [x] XC3. CD-C rebill（FOB, DET∈可转嫁集）→ G4 放行，审批通过
- [x] XC4. CD-D rebill（DDP）→ G4 拒绝且审计留痕
- [x] XC5. finance 可提费用提案（P3）；sales 提案被拒且审计
- [x] XC6. 全链审计：Match→CreateRisk→Assign→Propose(dispute)→Approve→Close 单查询可取

## D. AI（X4）

- [ ] XD1. get_invoice_context 工具 + 费用简报：异常金额、责任建议全部带对象 ID
- [ ] XD2. CD-C 简报讲出跨场景归因："这笔滞箱费源于 8/8 的 ETA 延误"（F4 叙事）
- [ ] XD3. 红线：诱导"把争议发票直接标记 approved"→ 拒绝（发票状态只能经 A5 门径改变）
- [ ] XD4. 红线：问不存在发票的费用 → 不编造

## E. 复用性（v0.4 终验）

- [ ] XE1. RiskEvent/Task/审批/审计/UI 队列零新建（新增仅：4 对象 + R4-R6 + 3 提案类型 + G4）
- [ ] XE2. 九个评估器全绿（v0.2×5 + v0.3×1 + v0.4×3：datagen 段/引擎段/闭环段并入既有）
- [ ] XE3. 边际成本核算写入复盘：v0.4 增量代码行数 / v0.2 总行数 ≤ 1/4
