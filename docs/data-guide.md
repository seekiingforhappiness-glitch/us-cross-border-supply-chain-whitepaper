# 数据导读——写给没做过跨境供应链的你

目的：让你不需要行业经验也能看懂 `data/raw/` 里的每张表在讲什么、真实从业者拿这些数据做什么。
建议配合一个真实故事读：DEMO-01（那票延误的充电器）。

## 0. 一句话版业务流程

你是一家帮中国工厂把货卖到美国的贸易公司。美国客户向你下单（**销售订单 SO**），你向深圳工厂下单
（**采购订单 PO**），工厂交货后你订一个集装箱走海运（**Shipment**），船公司一路发状态事件
（**Milestone**），货到洛杉矶清关入仓，最后履约给客户。你赚差价，赌的是"按承诺日交付"。

## 1. 九张表 = 三个系统的导出

真实公司数据散在多个系统里，这就是为什么有三种前缀：

| 前缀 | 模拟的系统 | 表 |
| --- | --- | --- |
| oms_ | 订单管理系统（面向客户） | customers、sales_orders、so_lines |
| srm_ | 采购系统（面向供应商） | suppliers、purchase_orders |
| tms_ | 物流/运输系统（面向货代船司） | shipments、milestones、allocations |
| catalog_ | 商品主数据 | skus |

**关键体感**：三个系统互相不认识对方的主键。运输系统里供应商叫 "SZ Hongyu Elec. Co"，
采购系统里叫 "Shenzhen Hongyu Electronics Co., Ltd."——同一家。真实公司天天在受这个苦，
这正是 W3 管道要解决的问题（entity resolution）。

## 2. 跟着 DEMO-01 的货走一遍

打开各表搜这些 ID，就是一个完整故事：

1. `oms_customers` 找 **CUS-0007**（BB Reseller，tier=A 大客户）
2. `oms_sales_orders` 找 **SO-2026-0188**：6/25 下的单
3. `oms_so_lines` 找 **SOL-0188-1**：500 个 20W USB-C 充电器，承诺 8/20 交付
4. `srm_purchase_orders` 找 **PO-2026-0101**：向深圳鸿宇电子采购
5. `tms_shipments` 找 **SHP-2026-0099**：8/1 从盐田港（CNYTN）开船去洛杉矶（USLAX），
   原计划 8/14 到（eta_initial）
6. `tms_milestones` 按 shipment_id 过滤：8/8 有一条 **eta_change** 事件，new_eta=8/21——
   船晚了 7 天。这条事件就是整个控制塔的"第一块多米诺骨牌"
7. `tms_allocations` 告诉你这船上装着谁的货：SOL-0188-1 的 500 件在上面

推理链（W4 引擎要自动做的事）：8/21 到港 + 3 天清关 + 2 天尾程 = 8/26 才能交，
承诺是 8/20 → 违约 6 天 → A 级客户 → 高危风险。

## 3. 每个"行话字段"是什么

**tms_shipments（一行 = 一票货 = 一个集装箱）**

- `booking_no / mbl_no`：订舱号 / 海运提单号。行业里查货用这两个号+柜号，不存在什么全局 shipment_id
- `container_no`：柜号，如 `CCLU4676464`。前 4 位是箱主代码（CCLU=中远的箱子），
  后 6 位序号 + 1 位校验码（ISO 6346 标准，输错一位立刻校验不过——业内防手误的设计）
- `container_type`：40HC=40尺高柜（最常见），20GP=20尺普柜
- `carrier_scac`：船公司四字码（COSU=中远海运，OOLU=东方海外，EGLV=长荣……报文里只认这个）
- `vessel_voyage`：船名+航次，如 `COSCO SHIPPING PISCES 084E`，E=东行（去美国）
- `incoterm`：贸易术语。FOB=客户负责海运后的一切；DDP=你负责到门，风险最大
- `origin_port_locode`：联合国港口代码。CNYTN=盐田，USLAX=洛杉矶。全球系统对接都用它
- `etd / eta`：预计开船日 / 预计到港日。**ETA 是全行业最不可信的字段**，所以才有 eta_change 事件
- `missing_docs`：缺的清关文件。commercial_invoice=商业发票，isf=美国要求开船前 24h 申报的安全文件，
  缺了会被海关扣（这就是 R2 风险规则）

**tms_milestones（一行 = 一个事件，这是控制塔的心跳）**

- `event_classifier`：ACT=已发生的事实（开船了），EST=预估（预计晚到）。区分两者是行业标准（DCSA）的核心
- `event_time` vs `ingested_at`：事情发生的时间 vs 你的系统收到消息的时间。中间差几小时到几天，
  还可能乱序到达——真实数据管道最头疼的两件事，我们特意模拟了
- `event_locode`：事件发生在哪。transshipment（中转）常在 SGSIN=新加坡、KRPUS=釜山

**oms_so_lines**：`unit_price_usd` 是这一单谈下来的价，和目录价差 ±15% 很正常；
`promised_delivery_date` 是对客户的承诺——整个系统守护的就是这个日期。

## 4. 数据里埋了什么"坑"（故意的）

真实数据从不干净。这些坑全部有账（data/truth/injected_noise_log.csv），W3 管道要逐一处理：

重复上报（同一事件收到两次）、乱序到达（旧消息后到）、状态打架（表里说在途，事件流显示已到港——
信事件流）、字段空值（船名/船司缺失但 SCAC 在）、同一供应商三个系统三种写法。

## 4.1 unresolved_milestones 和 dq_issues 怎么看

v0.6-H3 开始，`tms_milestones` 源表不再带内部 `shipment_id`，管道先用 `booking_no` 或
`container_no` 解析回 Shipment。解析失败的事件不会被丢弃，也不会被猜测归属，而是进入
`unresolved_milestones` 停车表。

M6 把这些停车记录升级为 `dq_issues` 运营队列：

- `source_table/source_record_id` 指回原停车记录，例如 `unresolved_milestones.MS-...`
- `issue_type=unresolved_reference` 表示单证号无法解析
- `detail_json` 保留 milestone/source 信息，方便运营解释为什么停住
- `status/assignee_user_id/resolution` 记录分派和关闭过程

边界很重要：关闭 `dq_issues` **只记录处置说明**，例如“已确认 carrier 事件单证号有 typo，源修复待外部系统处理”。
本原型不会真实修复源系统、不会回写承运商、不会重跑源数据，也不会因此改变 milestone 解析率目标。

## 5. 你怎么抽查（不需要经验的三个动作）

1. 挑任意一票货，把它的 milestones 按 event_time 排序读一遍——像不像一个合理的故事？
   （订舱→开船→中转→改期→到港→报关→放行→妥投）
2. 挑一个柜号去 https://www.bic-code.org/identification-number-check/ 验校验位（应该能过）
3. 对着 §2 把 DEMO-01 走一遍，能自己讲出"为什么这票货有风险"，数据就算过关
