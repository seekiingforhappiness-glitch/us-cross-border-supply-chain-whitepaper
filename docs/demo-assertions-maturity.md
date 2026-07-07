# Maturity Upgrade Demo Assertions

当前状态：M1-M6 已完成；M7+ 未获批准前不改 ontology、规则或业务范围。

这些条目是验收检查，不是批准决策。批准决策仍以 Daniel 批准后的 plan/control-tower decision log 为准。

## W7 Governance

- [ ] MA1. Ops 用户提交 mitigation 后，同一用户即使切换 manager role 也不能审批自己的提案。
- [ ] MA2. 所有动作审计包含 actor_id、role、policy_version、target_object、before_state、after_state、as_of_date。
- [ ] MA3. Action ID 生成不使用 count(*) + 1，重复运行测试不会产生 ID 冲突。

## W8 Source Truth

- [x] MA4. 一个 milestone 源事件可追溯到 canonical event envelope 和 raw payload 摘要。
- [x] MA4-MDM. external ID → internal ID crosswalk 可查看 resolved/ambiguous/unresolved 计数，ambiguous 不自动猜测。
- [x] MA4-GRAPH. Shipment → Customer 可通过 object_relationships/explain_path 返回可解释路径。
- [x] MA5. booking_no/container_no 解析失败时进入 DQ issue，而不是被静默丢弃。
- [ ] MA6. 对同一 source message 重放不会创建重复 RiskEvent 或重复 writeback。

## W9 Operations

- [x] MA7. Task 有 named owner、due_at、sla_state、escalation_level。
- [x] MA8. 逾期任务在指定 as_of_date 下被标为 overdue，并以 escalation_level=1 生成升级候选。
- [x] MA9. DQ issue 能被分派、记录处置、关闭，并写审计。

## W10 AI

- [ ] MA10. LLM payload 不包含当前角色不可见字段。
- [ ] MA11. Prompt-injection 测试不能调用未注册审批工具。
- [ ] MA12. OpenAI LLM 实测结果记录 provider、model、tool policy version；真实 LLM 执行需要 `OPENAI_API_KEY`，且不得包含私有或原始企业数据。
