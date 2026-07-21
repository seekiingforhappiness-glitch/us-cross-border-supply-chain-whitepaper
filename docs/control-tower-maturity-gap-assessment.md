# Control Tower Maturity Gap Assessment

更新时间：2026-07-07

## 审计口径

本清单以成熟跨境供应链控制塔为参照，但本仓库仍保持 simulation-first 原型边界：不接真实企业系统、不使用真实企业数据、不做真实报关逻辑。

## P0 必须先裁决

| 缺口 | 当前状态 | 业务问题 | 推荐裁决 |
| --- | --- | --- | --- |
| Identity/RBAC | Streamlit 角色切换 + role 字符串校验 | 是否需要模拟实名用户和 maker-checker？ | 建议建 demo user + SoD，不接 SSO |
| Integration | datagen + SQLite | 是否需要模拟 outbox/writeback？ | 建议模拟 outbox，不接真实系统 |
| MDM | booking/container/supplier 局部 ER | MDM 覆盖哪些对象？ | 建议 shipment/customer/sku/supplier 先行 |
| Graph/relationship model | query-specific SQL + JSON lists | 是否引入通用 object_relationships registry table？ | Daniel 批准后再建通用 registry |
| Data-quality operations | build-time DQ + parking records | 未解决记录是否转为运营任务？ | Daniel 批准后再建 DQ issue loop |
| Business expansion choice | 库存/单证/运输执行/费用追偿均未进入下一阶段 | 下一业务模块在 inventory/WMS、customs/documents、transportation execution、cost recovery 四选一选哪个？ | 平台成熟度补齐后由 Daniel 只选一个 |

## 成熟度目标

1. 任何动作都能追溯到 named actor、role、policy、before/after、as_of_date。
2. 任何源事件都能追溯到 source_system、source_record_id、message_id、ingested_at、transform_version。
3. 任何异常都能分配到 owner，有 SLA、升级规则和关闭原因。
4. 任何 DQ 停车记录都能转为可处理的 DQ issue。
5. 任何 AI 回答只能看到当前角色允许的最小字段集。
