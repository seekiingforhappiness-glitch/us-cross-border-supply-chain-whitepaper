# 真实市场数据字段差距分析（W2 补充调研）

日期：2026-07-06 ｜ 依据：DCSA Track & Trace 标准、承运商 EDI X12 315 报文、可视化平台（Vizion 等）数据模型
状态：已裁决——Tier 1 采纳并实施（决策 D11），Tier 2 记录待 v0.3，Tier 3 维持不做

## 0. 参照系

- [DCSA Track & Trace 标准](https://dcsa.org/standards/track-and-trace)：航运数字化联盟的事件标准，事件分 EQUIPMENT / TRANSPORT / SHIPMENT 三类 journey，带 `event_classifier`（ACT 实际 / EST 预估 / PLN 计划）、运输方式、设施/位置码
- [EDI X12 315（Status Details - Ocean）](https://www.maersk.com/~/media_sc9/developerportal/Maersk/edi/products/ocean-carrier-shipment-status/ESP_MIG_X12_315_4010)：承运商实际下发的状态报文，按 BL 号/订舱号/柜号 标识货，B4 段带重量、体积、柜号、状态码、城市
- [Vizion DCSA 事件映射](https://docs.vizionapi.com/docs/dcsa-milestones)：可视化平台如何把各承运商杂乱事件规范化

## 1. 关键发现：三个结构性差距（比缺字段更重要）

**G1 真实世界没有全局 shipment_id。** 跨系统串联靠的是单证号：订舱号（booking number）、
海运提单号（MBL）、柜号（container number）。承运商 315 报文、可视化平台 API 全部以这三者为查询键。
我们的 `shipment_id` 是 ontology 层才该有的合成主键，直接出现在"源系统"里等于把 W3 最难也最有
教学价值的工作（从单证号重建对象）预先做掉了。

**G2 事件没有地点。** 真实事件流每条都带发生地（UN/LOCODE + 设施类型），"延误发生在哪个节点"
是运营解释风险的第一句话。我们的 milestone 只有时间没有地点。

**G3 预估与实际不分。** DCSA 用 event_classifier 区分 ACT/EST/PLN——eta_change 本质是 EST 事件，
departed/arrived 是 ACT。我们混在一个 event_type 里，引擎无法表达"预估到达 vs 实际到达"的语义。

另有一个记录不改的结构差距：真实 FCL 一票常多柜（Container 1:N），我们单柜简化——这是 D5 降级
Container 对象的已知代价，v0.3 对象化时解决。

## 2. 字段差距清单

### Tier 1 — 建议本周补进 datagen（低成本、高真实感、W3 直接受益）

| 表 | 新字段 | 真实来源 | 说明 |
| --- | --- | --- | --- |
| tms_shipments | booking_no | 订舱确认 | 如 MAEU 开头 10 位；跨系统 join 键（G1） |
| tms_shipments | mbl_no | 海运提单 | SCAC+8 位数字；315 报文主键之一（G1） |
| tms_shipments | carrier_scac | 315/订舱 | MAEU/COSU/OOLU…真实报文用 SCAC 不用全名 |
| tms_shipments | container_type | 315 B4 | 40HC / 40GP / 20GP（ISO 尺寸类型） |
| tms_shipments | gross_weight_kg / volume_cbm | 315 B4 重量立方 | 按所载行数量推算 + 噪声 |
| tms_shipments | incoterm | 订舱/合同 | FOB/CIF/DDP——白皮书与 v0.1 手册均强调，v0.2 漏了；v0.3 成本闭环的地基 |
| tms_shipments | origin_port_locode / destination_port_locode | UN/LOCODE | CNYTN/CNSHK/CNNGB → USLAX/USLGB；可读名保留作冗余（真实数据两者常并存且偶有矛盾） |
| tms_milestones | event_locode | DCSA/315 | 事件发生地（G2） |
| tms_milestones | event_classifier | DCSA | ACT/EST（G3）：eta_change=EST，其余=ACT |
| oms_so_lines | unit_price_usd | OMS 常规 | 成交价（≠目录价）；affected_value 应改用成交价口径 |

配套噪声机会（不新增噪声类型，仅丰富现有类型的表现形式）：carrier_name 空但 carrier_scac 在
（现有 null_vessel_carrier 的真实形态）；locode 与可读港名矛盾（现有 status_conflict 的姊妹形态，暂不做）。

### Tier 2 — 记录在案，v0.3 采纳

PO 行级结构与采购价/币种/付款条款；HBL 与货代主体；customs entry number、HTS code、ISF/AMS 状态
（合规闭环）；一票多柜（Container 对象化）；terminal/设施码；事件代码对齐 DCSA 代码表
（DEPA/ARRI/LOAD/GTIN/GTOT…）。

### Tier 3 — 刻意不做（反目标）

AIS 船位流水（体量与价值不匹配）；运价/附加费明细（费用闭环另立项）；多币种。

## 3. 对 G1 的处理边界（重要取舍）

Tier 1 加入单证号后，v0.2 仍保留源表中的 shipment_id（解释：真实 TMS 内部也有自己的内部键），
W3 不强制"从单证号重建对象"。完全删掉 shipment_id、逼管道做单证号级 entity resolution 是更真实
但更重的练习，留给 v0.3 决定。理由：W3 已有 supplier 名称 ER 练习，一周内两个 ER 课题会超时（plan §13）。

后续 H3 已把 `tms_milestones` 源表从内部 `shipment_id` 升级为 `booking_no/container_no` 单证号级 ER。
M3 在此基础上再补一层 source-truth 基础设施：每条 raw milestone 带 `source_record_id` 与 `message_id`，
管道先归一为 canonical event envelope 并写入 `source_events`，再进入 ER 与对象层重建。这个层次只模拟
source identity、idempotency 与 raw payload lineage，不引入真实 carrier API、真实生产集成或新的业务规则。

## 4. 采纳后的影响面

datagen 六模块加字段与生成逻辑（约半天）；ontology manual §2.7/§2.8 与 JSON 的 Shipment/
ShipmentMilestone 属性表同步（走 AGENTS §3 变更协议）；DEMO 案例断言不受影响（新字段不进断言）；
affected_value 口径从目录价改为行成交价（oracle 与 W4 引擎同步，写入规则澄清）。

---

## 5. 采购 / 仓储域字段差距（当前全貌补充，2026-07-08）

原文 §1-§4 是**延误域**的字段调研快照（W2）。后续新增采购（R7-R15）、仓储（R16-R18）两域，
同样是基于真实调研的**高真实感合成数据**，未接真实系统。此处如实记录这两域相对真实源系统的
已知简化，供 FDE 面客时诚实说明"哪些是模拟、真实部署要接什么"。

### 5.1 采购域（真实源=ERP 采购模块 / 供应商门户 / AP 系统）

| 我们的对象 | 真实世界更复杂之处 | 真实来源 | 现状取舍 |
| --- | --- | --- | --- |
| PurchaseOrder / PoLine | 付款条款(Net30/60)、币种与汇率锁、PO 级 incoterm、税金、修订版本(PO revision) | ERP（SAP/Oracle/NetSuite）采购模块 | 简化为核心对账字段；三方对账容差(tolerance)用固定阈值，真实按品类/供应商分档配置 |
| GoodsReceipt / GoodsReceiptLine | ASN 预到货通知、质检(QC hold)、部分收货多次入库、退货(RTV) | WMS/收货系统 | 已补行级 received_date（P2 修复）；QC/ASN 未建模 |
| SupplierInvoice | OCR 抓取、发票匹配例外工作流、税务合规(VAT/withholding) | AP 自动化(Tipalti/Bill.com) | 简化为三方对账所需字段；开票超收货量(R11) 已建模 |
| SupplierQualification | 真实资质来自合规/风控系统的证书库(ISO/验厂/信用) 带有效期与自动预警 | 合规 GRC 系统 | 资质过期(R13)已建模；证书类型简化 |
| RFQ / Quote | 多轮报价、评分卡、审批链、电子签 | 寻源平台(Coupa/Ariba) | 单一来源(R14)/maverick(R15)已建模；多轮谈判简化 |

### 5.2 仓储域（真实源=WMS / 库存系统）

| 我们的对象 | 真实世界更复杂之处 | 真实来源 | 现状取舍 |
| --- | --- | --- | --- |
| Warehouse / InventoryPosition | 库位(bin/location)层级、批次(lot)/序列号(serial)、库龄(FEFO/FIFO)、多货主(3PL) | WMS（Manhattan/Blue Yonder/自研） | 简化为 SKU×仓库的可用/预留/在途/安全库存；无库位/批次 |
| InventoryReservation | 预留优先级、软/硬预留、ATP(available-to-promise) 实时计算 | OMS/库存中台 | 预留驱动履约已建模；ATP 用快照非实时 |
| CycleCount | 循环盘点排程、ABC 分类、差异根因(RCA)工作流、账实调整审批 | WMS 盘点模块 | 盘点差异(R18)已建模；排程/ABC 简化 |
| （断货/不可履约 R16/R17） | 补货建议、采购触发点(ROP)、多仓调拨(transfer order) | 补货引擎 | 断货检测已建模；自动补货/调拨未做（现货救延误走人审批的 SuggestSubstitution） |

### 5.3 诚实结论（面客话术）

这两域证明的是**方法可迁移**——同一 RiskEvent→Task 治理闭环、同一对象中心 + 可信 AI 模式，
能承接采购、仓储与延误/费用/准入并列，且跨域连成一张网（采购收货→库存→履约→延误现货救援）。
真实部署时，datagen 的合成对象换成 ERP/WMS 的真实抽取（ETL/CDC 入湖 → 管道 ER/MDM → 同一 ontology），
业务规则与动作层**不变**。**未做的不是不会做，是刻意不在学习型原型里造真实集成**（反目标，见 Tier 3 精神）。
