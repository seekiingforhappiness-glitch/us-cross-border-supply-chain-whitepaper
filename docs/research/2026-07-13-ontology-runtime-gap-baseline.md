# 本体 ↔ 运行时 差异基线（两张皮实况清单）

> 2026-07-13 · 桥1「本体一致性闸门」首次运行产出（V5 决议①，plan v0.2 §4 V5 条目 / :301）。
> 生成工具：`pipeline/ontology_lint.py`（纯只读核对工具，本次纯新增，不改任何现有代码）。
> 复现：`python3 -m pipeline.ontology_lint`（报告模式，退出码 0）；接发布闸门用 `--strict`（有差异退出码 1）。
> 核对对象：本体 `ontology/control-tower-ontology.json` v0.9.0（34 对象 / 53 关系 / 31 动作） vs
> 运行时 `data/ontology.sqlite`（只读打开）+ `app/*.py` 权限字典 + `agent/tools.py` 工具清单。
> **本文件只记录差异，不修复任何差异**——修复属于桥2（声明驱动）/桥3（结构生成）的工作。

---

## 一、白话总结（非工程师可懂）

我们的系统有一份「设计图纸」（本体 JSON：写明有哪些业务对象、对象之间什么关系、谁能做哪些动作、
哪些字段敏感）。真正在跑的「发动机」是另外三样东西：数据库的表、代码里的权限字典、给 AI 的工具清单。
一直以来，图纸只挂在「看板/透视镜」上给人看，发动机各跑各的——**两者对不对得上，全靠人自觉，没有机器把关**。

桥1 就是造了一个「质检员」，把图纸和发动机逐条比对。这是它第一次上岗的体检结果：

**共发现 15 处对不上，分四类：**

| 类 | 比的是什么 | 差异数 | 一句话说人话 |
|---|---|---:|---|
| A 对象↔表 | 图纸上的对象字段 vs 数据库真实列 | 10 | 大多是数据库多存了几列没写进图纸、5 个尺寸/金额字段本该是数字却按文本存 |
| B 动作↔权限 | 图纸上「谁能做这动作」vs 代码真实授权 | 2 | **代码允许"采购"角色提处置提案，但图纸没写这条**（见疑似真问题）|
| C 动作↔AI 工具 | 给 AI 的工具 vs 图纸动作 | 0 | **完全对得上**——AI 只拿到该拿的工具，审批/关闭/拒接四个"冻结区"动作确实一个都没给 AI |
| D 关系↔外键 | 图纸上的对象关系 vs 数据库连接列 | 3 | 2 处关系用了"非常规列名"连接（能跑，只是没按命名规矩）、1 处关系图纸画了但数据库没造连接列 |

**这些差异意味着什么、谁来修：**

- **好消息**：最要命的一类——「AI 能不能碰它不该碰的动作」（C 类）——**零差异**。审批、关闭风险、
  批准报价、拒接这四个"人必须亲自拍板"的动作，代码里既没注册成 AI 工具、又在黑名单里显式拉黑，
  质检员确认这道安全闸门是严丝合缝的。这是本次体检最值得放心的结论。
- **A 类 10 处**多是"数据库比图纸多长了几列"（如 `skus.supplier_id`、`shipments.po_ids` 这类
  连接用/派生用的列没登记进图纸）——属于图纸没跟上实现，**桥3（结构生成：让图纸直接生成表结构）**会
  从根上消除这类分叉。其中 **5 个数字字段被当文本存**（SKU 的申报价值、包装尺寸/重量）值得单独留意：
  文本存数字，将来做数值比较/统计可能出错，也可能混进非数字脏值。
- **B 类**的核心那条是**疑似真问题**，单列在第二节。**桥2（声明驱动：让权限从图纸生成）**修好后，
  图纸改一行、代码权限自动同步，这类"代码和图纸各说各话"就不会再发生。
- **D 类 3 处**：2 处是关系确实连着、只是列名没随命名规矩（`po_ids` 反向存、`destination_warehouse`
  软命名），能跑不影响功能；1 处（`risk_affects_sku`：风险直接关联到 SKU）是**图纸声明了、数据库
  却没有承载列**——要么补列、要么把这条用不上的关系从图纸删掉，留给桥2/桥3 决定。

**一句话**：发动机本身是好的、安全闸门是紧的；差异集中在「图纸没跟上发动机的迭代」——这正是
造桥1 想暴露、并由桥2/桥3 从机制上根治的「两张皮」。

---

## 二、【疑似真问题】清单

> 判据：本体声明与代码在「授权语义」上直接冲突（如"本体说某角色不能做某动作、但代码允许"），
> 属可能藏着越权的严重类。逐条附证据与建议核对方向——**只报不改**，是否处置由创始人裁决。

### 疑似真问题 #1 —— 代码允许 `procurement` 提处置提案，本体未声明

- **现象**：本体 `actions[]` 里 `ProposeMitigation` 的 `executors = [ops, cs, finance]`，
  **未含 `procurement`**；但代码 `app/actions.py` 的 `ROLE_PERMS["ProposeMitigation"] =
  {ops, cs, finance, procurement}`——**代码多授予了 `procurement` 角色**。
- **连带**：本体 `roles[]` 只声明 7 个角色（compliance/cs/finance/manager/ops/sales/system），
  **整个 `procurement` 角色都不在本体角色表里**，但代码 `ROLE_PERMS` 与 `COORD_PERMS` 都在用它。
- **可复现证据**：
  - 本体：`ontology/control-tower-ontology.json` → action `ProposeMitigation`.executors / 顶层 `roles`
  - 代码：`app/actions.py:23-30` `ROLE_PERMS`；`app/coordination_actions.py:22` `COORD_PERMS`
  - 运行 `python3 -m pipeline.ontology_lint`，见 B 节第 1、2 条
- **研判（先验证后下结论，不做单边惊叫）**：这**几乎肯定是"本体滞后"而非"越权漏洞"**。
  证据：`docs/control-tower-plan-v0.2.md:223` 决策日志 **P4**「新增专职『采购 procurement』角色
  （2026-07-09，Daniel 经 AskUserQuestion 亲批）……权限仅 +ProposeMitigation」。即这条授权是
  **人亲批过的**，只是当时改了代码、没同步更新本体 JSON（本体停在把 procurement 纳入前的版本）。
- **为何仍单列为疑似真问题**：桥1 的使命就是把"代码授权 ⊃ 本体声明"这类分叉一律拦下——
  因为**同样的形态既可能是良性滞后、也可能是真越权**，机器不能替人判断动机，必须交人核对。
- **建议核对方向（不在本任务修）**：确认 P4 裁决就是"procurement 可提处置提案"后，
  由桥2 把 `procurement` 补进本体 `roles[]` 与 `ProposeMitigation.executors`，让图纸追平已批准的现实。
  **在本体补齐前，不要以"本体没写"为由去删代码里的 procurement 授权**（那会推翻 P4 人批决策）。

> 其余 14 处差异均为结构/命名/登记类，无授权语义冲突，不构成疑似真问题，明细见第三节。

---

## 三、分类差异明细（每条可复现）

> 差异种类：【缺失】=本体声明了、运行时找不到；【多余】=运行时存在、本体未声明；
> 【漂移】=两侧都在但对不上。每条写清"核对的是哪个文件哪个字段 vs 哪张表哪列"。

### A 对象↔表结构（10 条：缺失 1 / 多余 4 / 漂移 5）

核对规则：对象类型 → 表名（显式 `table` 字段优先，否则 PascalCase→snake_case→复数化），
再比 `objects[].properties` 与 SQLite 实际表列。

| # | 种类 | 对象 | 证据 |
|---|---|---|---|
| 1 | 多余 | Sku | 表 `skus` 有列 `supplier_id`，本体 Sku 未声明该属性（承载 `supplier_provides` 关系的去规范化 FK） |
| 2 | 漂移 | Sku | `Sku.declared_value_usd` 本体=number，`skus.declared_value_usd` 声明=TEXT（数字按文本存） |
| 3 | 漂移 | Sku | `Sku.package_h_cm` 本体=number，`skus.package_h_cm`=TEXT |
| 4 | 漂移 | Sku | `Sku.package_l_cm` 本体=number，`skus.package_l_cm`=TEXT |
| 5 | 漂移 | Sku | `Sku.package_w_cm` 本体=number，`skus.package_w_cm`=TEXT |
| 6 | 漂移 | Sku | `Sku.package_weight_kg` 本体=number，`skus.package_weight_kg`=TEXT |
| 7 | 多余 | Shipment | 表 `shipments` 有列 `last_event_time`，本体 Shipment 未声明（派生/事件戳列） |
| 8 | 多余 | Shipment | 表 `shipments` 有列 `po_ids`，本体未声明（一票多 PO 的反向 json 列，见 D#1） |
| 9 | 多余 | Shipment | 表 `shipments` 有列 `status_source`，本体未声明（状态来源标记列） |
| 10 | 缺失 | ShipmentMilestone | 本体属性 `ShipmentMilestone.payload` 在表 `shipment_milestones` 无对应列 |

- **A#10 补充**：`payload` 很可能是在 M3「事件信封/原始血缘」改造时**下沉到了原始层**——
  `source_events` 表有 `payload_json` 列承载原始报文，对象层 `shipment_milestones` 不再直接存 payload。
  本体 ShipmentMilestone 仍按旧设计声明了 payload 属性。属登记滞后，非数据丢失。
- **A#2-6（数字存成文本）** 是本类里唯一带"数据质量隐患"的：SKU 的申报价值与包装长宽高/重量在
  数据库里是 TEXT。留意但不在本任务修。

### B 动作↔权限（2 条：缺失 1 / 多余 1 / 漂移 0）

核对规则：本体 `actions[].executors` vs 代码 5 个权限字典（主 `app/actions.py` ROLE_PERMS，
另交叉核对 admission/procurement/warehouse/coordination 四个兄弟字典）。

| # | 种类 | 动作/项 | 证据 |
|---|---|---|---|
| 1 | 多余 | ProposeMitigation | `ROLE_PERMS["ProposeMitigation"]`(app/actions.py) 授予 `procurement`，本体 executors 未声明（→疑似真问题 #1） |
| 2 | 缺失 | roles[] | 运行时权限字典使用角色 `procurement`，本体 `roles[]` 未声明 |

**覆盖情况（信息，非差异）**——B 类只这 2 条差异，说明其余 29 个动作的执行者声明与代码授权**逐条一致**：

- ROLE_PERMS（主）覆盖 4 动作：AssignTask / ProposeMitigation / ApproveMitigation / CloseRiskEvent。
- 兄弟字典逐条已交叉核对且**全部一致**：ADM_PERMS（B1-B6 准入 6 动作）、PROC_PERMS（A7/A8 收货/对票）、
  WH_PERMS（A11-A13/A15 仓储 4 动作）、COORD_PERMS（A20-A25 协调 6 动作，共用 ManageCoordination 组权限）。
- 系统级动作（`executors ⊆ {system}`，引擎执行、无人类 RBAC 键，预期）：IngestMilestone。
- 提案子类型动作（经 propose→approve 提案流执行，无独立权限键，预期）：SuggestSubstitution /
  AdjustInventory / InitiateSecondSource / BlockNonPoPayment / BackfillPo。
- ⚠ 未在任何权限字典找到对应键的动作：**CreateRiskEvent**（system/ops，由引擎创建）、
  **RecordPurchasePayment**（A9，finance/system）、**RecordSupplierQualification**（A10，compliance/system）。
  这三者要么在别处内联 gate、要么尚未接到 UI 权限字典——**登记盲区，建议桥2 一并厘清**（非授权冲突，故未列疑似真问题）。

### C 动作↔AI 工具（0 条差异）

核对规则：`agent/tools.py` 的 `TOOL_DEFS` 与 `FORBIDDEN_TOOLS` vs 本体 actions。**三项子核对全通过**：

1. **暴露的工具能溯源到本体**：6 个写工具全部按 snake_case 溯源到本体动作
   （assign_task→AssignTask / propose_mitigation→ProposeMitigation / create_admission_case→CreateAdmissionCase /
   run_compliance_precheck→RunCompliancePrecheck / build_logistics_plan→BuildLogisticsPlan /
   calculate_cost_scenario→CalculateCostScenario）；11 个读工具是对象查询工具（本体无"读动作"，溯源到对象，预期）。
2. **冻结区动作确实不在 TOOL_DEFS**：本体审批/关闭/拒接类 4 动作
   （ApproveMitigation / ApproveQuoteDecision / CloseRiskEvent / RejectOrRequestMoreInfo）一个都没暴露给 AI。
3. **FORBIDDEN_TOOLS 覆盖完整**：`{approve_mitigation, approve_quote_decision, close_risk_event,
   reject_or_request_more_info}` 恰好逐一对应上述 4 个冻结区动作，无遗漏、无指向失效动作的死条目。

> 附注（非差异）：花钱/写库类动作（如 RecordPurchasePayment、BlockNonPoPayment）本就未注册进 TOOL_DEFS，
> AI 根本不可达；FORBIDDEN_TOOLS 是对"最易被诱导越权"的审批/关闭/拒接四动作的**显式纵深拉黑**（双保险）。

### D 关系↔外键（3 条：缺失 1 / 多余 0 / 漂移 2）

核对规则：`links[]` 的 source/target 端 → 数据库外键列存在性（N:1 外键在 source 表=target 主键；
1:N 在 target 表=source 主键；N:M 存 source 表 `affected_<target主键去_id>_ids` json 列）。
**核对深度=列名存在性**（SQLite 未声明 FOREIGN KEY 约束，不验证引用完整性）。

| # | 种类 | 关系 | 证据 |
|---|---|---|---|
| 1 | 漂移 | po_shipped_by | PurchaseOrder→Shipment(N:1) 期望 `purchase_orders.shipment_id` 不存在；实以反向 json 列 `shipments.po_ids` 承载（一票多 PO，方向/表示与声明基数相反） |
| 2 | 漂移 | shipment_to_warehouse | Shipment→Warehouse(N:1) 期望 `shipments.warehouse_id` 不存在；实由软命名列 `shipments.destination_warehouse` 承载（列存 warehouse_id 值，只是列名没按约定） |
| 3 | 缺失 | risk_affects_sku | RiskEvent→Sku(N:M) 在 `risk_events` 找不到承载列（期望 `affected_sku_ids` 的 json 列）——图纸声明了、数据库无实现 |

**其余 50 条关系全部对得上**（47 条标准外键列存在；3 条 N:M 由标准 `affected_*_ids` json 列承载：
risk_affects_line→`affected_so_line_ids`、risk_affects_invoice_line→`affected_invoice_line_ids`、
risk_affects_po_line→`affected_po_line_ids`）。

- **D#1、D#2** 是"关系真连着、只是列名/方向没随命名约定"——功能正常（代码经 `po_ids` / `destination_warehouse`
  正常连接，见 `app/warehouse_actions.py:215-217`），属命名漂移。
- **D#3** 是唯一"声明了但无实现"的关系：RiskEvent 目前锚在 shipment/po/supplier/warehouse + 受影响行，
  没有直连 SKU 的列。要么补 `affected_sku_ids` 列、要么把这条用不上的关系从本体删除——留桥2/桥3 裁决。

---

## 四、本工具用到的映射契约假设（桥2 要固化的正是这些）

质检员为了把"图纸"和"发动机"对上，必须先假定一套映射规则。这些假设本身就是桥2 要落成
「唯一权威映射」的契约草稿，此处逐条列明供核对：

- **[M1] 对象类型→表名**：显式 `table` 字段优先（仅 RFQ→rfqs / RFQLine→rfq_lines / Quote→quotes 三个
  缩写词声明了）；否则 PascalCase→snake_case→末词复数化（y→ies，s/x/z/ch/sh→es，否则 +s）。
  这是对 `pipeline/build_ontology.py:442-657` 里 34 处硬编码 `table("<名>", ...)` 调用的**重建**——
  build_ontology 里表名是硬编码字符串、无单一派生函数，本工具的派生规则实测对全 34 对象类型均命中真实表。
- **[M2] 动作→权限字典键**：本体 `action.name` 直接等于权限字典的键；运行时权限分散在 5 个字典
  （actions.py ROLE_PERMS 为主 + admission/procurement/warehouse/coordination 四兄弟）；协调域 6 动作共用
  `ManageCoordination` 组权限键；executors 里的括号注解（如 `compliance (more_info)`）比对时剥离只取角色名。
- **[M3] AI 工具→动作**：写工具名 = snake_case(action.name)；读工具（list_/get_/explain_ 前缀）是对象查询、
  不对应动作（本体无读动作）；冻结区动作 = 名以 Approve/Close/Reject 起，不得进 TOOL_DEFS、应在 FORBIDDEN_TOOLS。
- **[M4] 关系→外键列**：N:1 外键在 source 表=target 主键；1:N 在 target 表=source 主键；
  N:M 存 source 表的 `affected_<target主键去_id>_ids` json 列；找不到标准列时尽力二次扫描
  （反向 json 列 / 含目标类型名的软命名列），找到报【漂移】、彻底没有报【缺失】。

## 五、本次核对的局限（如实声明）

- **类型漂移是 best-effort**：SQLite 用"类型亲和"而非严格类型，故只做尽力判定；boolean 因 SQLite 无
  布尔型，0/1(INTEGER) 与 'true'/'false'(TEXT) 均视为兼容、不报漂移（A#2-6 的 number-vs-TEXT 是明确漂移，未受此影响）。
- **D 类只核对"列名存在性"**：SQLite 表未声明 FOREIGN KEY 约束，本工具不验证引用完整性
  （孤儿行/悬挂引用需另做数据级检查，超出桥1 范围）。
- **表名派生是对硬编码的重建**：若未来 build_ontology 新增对象用了不符 M1 规律的表名，需在本体加显式
  `table` 字段或更新派生规则——这也是桥2 要把映射从"两处各写"收敛到"一处声明"的动因。
