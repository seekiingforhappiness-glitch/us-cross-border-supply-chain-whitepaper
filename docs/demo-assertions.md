# 演示断言清单（demo-assertions v0.2-W1）

依据：plan §2.1 演示脚本。每周验收对断言打勾，不对感觉打勾。所有断言必须可通过查询 `ontology.sqlite` / `action_log` 机械验证。

> **自动化覆盖（W4/W5）**：A1-A7、B1-B3、B7 → `engine.evaluate` + `pipeline.evaluate`；
> A8-A13、B4-B6、C1-C2、C4 → `app.test_closed_loop`。以上全部通过。
> **C3（五分钟 UI 走查）是唯一必须由人完成的断言**：`streamlit run app/streamlit_app.py`，
> 按 A 段顺序在界面操作（B7 顺带验证：切到 ops 角色看客户等级列）。

## 固定演示案例 DEMO-01（W2 确定性生成，不靠随机）

```text
Customer  CUS-0007  "BB Reseller"  tier=A
SO        SO-2026-0188 / 行 SOL-0188-1: 500 × USB-C 充电器, 承诺日 2026-08-20
PO        PO-2026-0101 (深圳供应商)
Shipment  SHP-2026-0099  yantian→los_angeles  etd 08-01  eta_initial 08-14
Milestone 08-08 eta_change → new_eta 08-21 (延误 7 天)
预期      R1: 08-21+3+2=08-26 > 08-20, 击穿 6 天 → high, tier=A 升级 → critical
另設      同船 SOL-0201-2（tier=C 客户, 承诺日 09-05）→ 不击穿, 不得进 affected
```

## A. 闭环六步主线断言

感知：

- [ ] A1. `as_of=08-07` 时运行引擎：SHP-0099 无 RiskEvent（D8 时钟正确性）
- [ ] A2. 注入 eta_change milestone 后：`shipment.eta_current` 08-14 → 08-21，`delay_days`=7，由 IngestMilestone 触发且仅此路径可改

传播：

- [ ] A3. 影响链查询从 milestone 出发一次 join 到 Customer：SHP-0099 → allocation → SOL-0188-1 → SO-0188 → CUS-0007
- [ ] A4. affected 集合 = {SOL-0188-1}，**不含** SOL-0201-2（行级精确性，D1 的存在意义）

定级：

- [ ] A5. RiskEvent 生成：type=delay_breach, rule_id=R1, severity=critical（high + tier A 升级）
- [ ] A6. `affected_value_usd` = 500 × 单价，与手算一致
- [ ] A7. SOL-0188-1.line_status: allocated → at_risk（由 A2 CreateRiskEvent 触发）

派发：

- [ ] A8. AssignTask 后：Task=assigned（assignee_role=ops, priority=P1），RiskEvent: open → acknowledged

处置：

- [ ] A9. ProposeMitigation(reschedule, new_promise_date=08-27)：Task → in_progress，approval_status=pending，RiskEvent → mitigating
- [ ] A10. ApproveMitigation(approved, actor=manager)：SOL-0188-1.promised_delivery_date → 08-27，reschedule_count 0→1，line_status: at_risk → allocated，Task → done
- [ ] A11. original_promised_date 仍为 08-20（不可变字段未被污染）

回写：

- [ ] A12. CloseRiskEvent(outcome=mitigated)：RiskEvent → resolved，resolved_at / resolution_summary 非空
- [ ] A13. 再次运行引擎（同 as_of）：不再为 SHP-0099 生成新的 delay_breach（改期后不再击穿）

## B. 反断言（系统不该做的事）

- [ ] B1. 完全相同的 eta_change milestone 重复注入：is_duplicate=true，eta_current 不变，不产生第二个 RiskEvent
- [ ] B2. 乱序注入一条 event_time=08-05 的旧 eta_change(new_eta 08-16)：事件入库，但 eta_current 保持 08-21（不回退）
- [ ] B3. vessel_voyage 为空、供应商名不一致等注入噪声：不产生任何 RiskEvent（对照 injected_noise_log 零匹配）
- [ ] B4. ops 调用 ApproveMitigation：被拒绝，action_log 记录越权尝试
- [ ] B5. 存在非终态 Task 时调用 CloseRiskEvent(outcome=mitigated)：被拒绝
- [ ] B6. 对同一 RiskEvent 二次 AssignTask：被拒绝并返回既有 task_id
- [ ] B7. 以 ops 身份查询 CUS-0007：tier 显示"无权查看"而非空白

## C. 审计与追溯断言

- [ ] C1. 主线走完后，单条 SQL 能拉出完整链：milestone → risk_event → task → 提案 → 审批 → 关闭，时间序单调
- [ ] C2. 上述每一次状态变更在 action_log 中恰有一条对应记录（不多不少）
- [ ] C3. 全流程五分钟演示：非开发者按本清单 A1→A13 顺序操作 UI 可独立完成（W5 验收）
- [ ] C4. action_log 中被驳回/拒绝的调用（B4-B6）同样留痕，含失败原因

## 覆盖检查

| 闭环步骤 | 断言 |
| --- | --- |
| 感知 | A1-A2, B1-B2 |
| 传播 | A3-A4 |
| 定级 | A5-A7, B3 |
| 派发 | A8, B6 |
| 处置 | A9-A11, B4 |
| 回写 | A12-A13, B5, C1-C2 |
