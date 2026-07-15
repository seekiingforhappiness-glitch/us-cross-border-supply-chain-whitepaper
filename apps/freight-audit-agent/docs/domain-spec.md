# Freight Audit Agent — 领域开发规格（单一事实源）

> 所有 schema、datagen、对账引擎、评估器实现以本文件为准。
> 【真实】= 有货代行业来源支撑；【设定】= 为原型简化的合理假设。种子固定 `RANDOM_SEED=42`。

## 1. SQLite Schema（7 张表）

设计原则：主键 `*_id`，金额 `*_usd`，snake_case，时间 ISO8601，显式 `as_of_date`，每表含 `created_at`。

```sql
CREATE TABLE invoices (
    invoice_id TEXT PRIMARY KEY, invoice_no TEXT NOT NULL, carrier_id TEXT NOT NULL,
    carrier_name TEXT, bill_to_party TEXT, booking_no TEXT, bl_no TEXT,
    invoice_date TEXT NOT NULL, currency TEXT NOT NULL, fx_rate REAL,
    total_amount_usd REAL NOT NULL, as_of_date TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE invoice_lines (
    invoice_line_id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL, line_no INTEGER NOT NULL,
    charge_code TEXT NOT NULL, charge_description TEXT, container_no TEXT, container_type TEXT,
    quantity REAL NOT NULL, unit TEXT NOT NULL, unit_rate_usd REAL NOT NULL,
    amount_usd REAL NOT NULL, currency TEXT, as_of_date TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE rate_cons (
    rate_con_id TEXT PRIMARY KEY, carrier_id TEXT NOT NULL, origin_port TEXT NOT NULL,
    destination_port TEXT NOT NULL, effective_date TEXT NOT NULL, expiry_date TEXT NOT NULL,
    contract_party TEXT, currency TEXT NOT NULL, free_time_days INTEGER,
    as_of_date TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE rate_card_lines (
    rate_card_line_id TEXT PRIMARY KEY, rate_con_id TEXT NOT NULL, charge_code TEXT NOT NULL,
    container_type TEXT, contracted_rate_usd REAL NOT NULL, unit TEXT NOT NULL,
    free_time_days INTEGER, as_of_date TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE bols (
    bol_id TEXT PRIMARY KEY, bl_no TEXT NOT NULL, booking_no TEXT, carrier_id TEXT NOT NULL,
    origin_port TEXT, destination_port TEXT, container_no TEXT, container_type TEXT,
    gate_out_date TEXT, return_date TEXT, discharge_date TEXT, pickup_date TEXT,
    free_days_used INTEGER, as_of_date TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE expected_discrepancies (   -- ground truth，评估器算 precision/recall 用
    expected_id TEXT PRIMARY KEY, invoice_line_id TEXT, invoice_id TEXT NOT NULL,
    discrepancy_type TEXT NOT NULL, expected_recovery_usd REAL, injection_note TEXT,
    as_of_date TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE review_queue (             -- 复核队列 + draft-not-send
    review_id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL, invoice_line_id TEXT,
    discrepancy_type TEXT NOT NULL, severity TEXT, detected_amount_usd REAL,
    evidence_json TEXT, status TEXT NOT NULL, resolution TEXT, assigned_to TEXT,
    as_of_date TEXT NOT NULL, created_at TEXT NOT NULL);
```

## 2. Charge Code 目录（20 项）

| Code | 英文 | 中文 | 单位 | USD区间 | 类别 |
|---|---|---|---|---|---|
| OFR | Ocean Freight | 海运费 | per_container | 800–3500 | 基础 |
| BAF | Bunker Adjustment Factor | 燃油附加费 | per_container | 150–600 | 附加 |
| CAF | Currency Adjustment Factor | 货币贬值附加费 | per_container | 50–200 | 附加 |
| PSS | Peak Season Surcharge | 旺季附加费 | per_container | 100–500 | 附加 |
| GRI | General Rate Increase | 综合费率上涨 | per_container | 100–400 | 附加 |
| THCO | Terminal Handling Origin | 起运港码头费 | per_container | 100–250 | 基础 |
| THCD | Terminal Handling Dest | 目的港码头费 | per_container | 120–300 | 基础 |
| ISPS | Ship & Port Security | 安保费 | per_container | 10–40 | 附加 |
| CGS | Congestion Surcharge | 拥堵费 | per_container | 50–300 | 附加 |
| DOC | Documentation Fee | 文件费 | per_bl | 30–80 | 单证 |
| SEAL | Seal Fee | 铅封费 | per_container | 5–15 | 单证 |
| TLX | Telex Release | 电放费 | per_bl | 25–50 | 单证 |
| AMS | Automated Manifest System | 舱单预报费 | per_bl | 25–40 | 单证 |
| ISF | Importer Security Filing | 进口安全申报费 | per_bl | 30–50 | 单证 |
| DET | Detention | 滞箱费 | per_day | 75–200 | 滞留 |
| DEM | Demurrage | 滞港费 | per_day | 100–300 | 滞留 |
| CHAS | Chassis Fee | 底盘车费 | per_day | 20–45 | 滞留 |
| DRAY | Drayage | 拖车费 | per_container | 150–600 | 运输 |
| CUS | Customs Clearance | 清关费 | per_shipment | 80–200 | 清关 |
| LSS | Low Sulphur Surcharge | 低硫附加费 | per_container | 20–100 | 附加 |

单位：per_container 按箱 / per_bl 按提单 / per_day 按天(需free_time基准) / per_shipment 按票。

## 3. Discrepancy 类型目录（12 类）

| 类型 | 中文 | 判定逻辑 | 证据字段 | 注入参数 |
|---|---|---|---|---|
| RATE_MISMATCH | 费率不符 | invoice.unit_rate_usd > rate_card.contracted_rate_usd × (1+容差) | 发票rate、合同rate、差额 | 加价10-40% |
| DUPLICATE_CHARGE | 重复计费 | 同invoice内同(charge_code+container)出现>1次 | 两行line_id | 复制一行 |
| PHANTOM_ACCESSORIAL | 无依据附加费 | invoice有该charge_code但rate_con无此项且不在白名单 | 发票行、缺失合同项 | 插合同外费用 |
| CONTAINER_COUNT_MISMATCH | 箱量不符 | invoice计费箱数 > BOL实际箱数 | 发票箱数、BOL箱数 | 虚增1-2箱 |
| WEIGHT_MISMATCH | 重量不符 | invoice重量 > BOL重量×(1+容差) | 发票重量、BOL重量 | 虚增5-15% |
| FX_ERROR | 汇率错误 | abs(invoice.fx_rate-基准)/基准 > 2% | 发票汇率、基准汇率 | 偏离3-8% |
| DEMURRAGE_MISCALC | 滞港费算错 | 计费天数 > (pickup-discharge).days - free_time | 计费天、实际天、free_time | 多算1-3天 |
| DETENTION_MISCALC | 滞箱费算错 | 计费天数 > (return-gate_out).days - free_time | 计费天、实际天 | 多算1-3天 |
| SURCHARGE_DUPLICATE | 附加费重复 | BAF/CAF等同类附加费出现>1次 | 重复附加费行 | 复制附加费行 |
| NOT_IN_CONTRACT | 合同外费用 | charge_code不在rate_con且不在白名单 | 发票行、合同清单 | 插未约定费用 |
| UNIT_MATH_ERROR | 单价×数量错 | abs(amount_usd - quantity×unit_rate_usd) > 0.01 | 发票行三字段 | 篡改amount |
| TARIFF_EXPIRED | 过期费率 | invoice_date > rate_con.expiry_date | 发票日、失效日 | 用过期费率 |

严重度：high(>$500 或合同违约:RATE_MISMATCH/NOT_IN_CONTRACT/PHANTOM) / medium($100-500) / low(<$100)。

## 4. 模拟数据生成规格

规模：5 carriers、15 rate_cons、180 rate_card_lines、150 invoices、~1300 invoice_lines、150 bols、
**~98 expected_discrepancies（45 张 dirty × 每张 1-3 条差异；这是权威口径，早期"~361"是笔误已废）**、
12 灰区样本。承运商用真实感 SCAC：MAEU/MSCU/COSU/OOLU/CMDU。
注：invoice_lines 与 bols 各含可空 `weight_kg` 列以支持 WEIGHT_MISMATCH（§1 DDL 补列）。

注入（种子42）：
- 70% 干净发票（105张，严格按 rate_card 定价，detention/demurrage 按 free_time 正确算）
- 30% 含差异（45张，每张1-3差异，先生成干净版再污染特定行 + 写 expected_discrepancies + expected_recovery_usd）
- 差异类型加权：RATE_MISMATCH 30% / DEM+DET_MISCALC 20% / PHANTOM+NOT_IN_CONTRACT 15% /
  DUPLICATE+SURCHARGE_DUP 15% / CONTAINER+WEIGHT 10% / FX+UNIT_MATH+TARIFF 10%
- 8% 灰区（12张，看似差异实为合理：口头加价协议/合同允许±5%浮动/合理FX波动）→ **不**写 ground truth，测误报

**detention/demurrage 计算（datagen 与 recon 共享 `datagen/dd_calc.py`，避免各写一份）**：
```
demurrage_days = max(0, (pickup_date - discharge_date).days - free_time_days)
detention_days = max(0, (return_date - gate_out_date).days - free_time_days)
charge = days × per_diem_rate
```

## 5. 确定性对账引擎规则

匹配流程：对每张 invoice → 按 bl_no/booking_no 找 rate_con+bol → 对每 invoice_line 按
(charge_code+container_type) 找 rate_card_lines 合同费率 → 逐类跑12条判定 → 命中生成
discrepancy+证据包(发票行/合同条款/计算)+追回金额 → 写 review_queue(status='pending')+action_log。

```python
# RATE_MISMATCH
if line.charge_code in rate_card:
    contracted = rate_card[line.charge_code].contracted_rate_usd
    if line.unit_rate_usd > contracted * (1 + TOL_RATE):
        recovery = (line.unit_rate_usd - contracted) * line.quantity
        flag(RATE_MISMATCH, recovery, evidence={invoice_line, contracted})
# DEMURRAGE_MISCALC（用 dd_calc 共享公式）
expected_days = max(0, (bol.pickup_date - bol.discharge_date).days - free_time)
if line.quantity > expected_days:
    flag(DEMURRAGE_MISCALC, (line.quantity-expected_days)*line.unit_rate_usd, evidence={...})
# DUPLICATE_CHARGE
seen={}
for line in lines:
    key=(line.charge_code, line.container_no)
    if key in seen: flag(DUPLICATE_CHARGE, line.amount_usd, evidence={seen[key], line})
    seen[key]=line
```

容差（进 config/config.yaml）：
```yaml
tolerances:
  rate_mismatch_pct: 0.02
  fx_error_pct: 0.02
  weight_mismatch_pct: 0.05
  amount_math_abs_usd: 0.01
  min_recovery_usd: 5.0
```

## 6. 误报控制

- `accessorial_whitelist`：常见合理附加费(BAF/CAF/ISPS)即使不在 rate_con 也不报 PHANTOM。
- 容差分层：金额类2%、计算类$0.01(必须精确)、最小追回$5。
- 灰区：free_time 来自口头约定→标 medium 人工复核；合同允许±X%浮动→计入容差。

## 7. Demo 验收断言

端到端：注入 RATE_MISMATCH 的发票(OFR报$2000/合同$1500) → 引擎在 review_queue 生成1条
RATE_MISMATCH，detected_amount_usd==(2000-1500)×qty，evidence_json含发票行/合同费率/计算，status=='pending'。

评估器：跑完150张 → precision≥0.90、recall≥0.85；灰区12张不应被误报(计入FP)；报告追回总额/误报率/漏报清单。
基线（2026-07-08 重建实测）：检出96、TP90、FP6(全为6张灰区口头加价)、FN8(小额<$5 SEAL阈值抑制,设计内)、
**P0.938、R0.918、误报率0.062、追回$80,252.21**。(旧口径 P0.944/R0.978/$48k 属已丢失的构建,不再适用。)

## 8. RBAC（三角色，权限在动作层强制）

| 动作 | reviewer | finance | manager |
|---|---|---|---|
| 看复核队列+证据 | ✓ | ✓ | ✓ |
| 标记/驳回差异 | ✓ | ✓ | ✓ |
| 核准差异→追款草稿(发起) | ✓ | ✓ | ✗ |
| 授权追款(draft→authorized) | ✗ | ✓ | ✓ |
| KPI看板+误报率+高额争议 | ✗ | ✗ | ✓ |
| 回滚 | ✓ | ✓ | ✓ |

**maker-checker 硬规则**：发起追款的 actor_id ≠ 授权追款的 actor_id。授权动作层校验，违反则结构化
拒绝+写审计。角色页面：reviewer→复核队列、finance→授权台、manager→KPI看板(真正按角色渲染不同视图，
非藏按钮)。仍守 draft-not-send(授权只改状态不真发)。
