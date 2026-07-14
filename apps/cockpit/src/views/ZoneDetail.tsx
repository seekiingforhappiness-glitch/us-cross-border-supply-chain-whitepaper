import type { ReactNode } from "react";
import type { Zone } from "../api";
import { formatInt, formatPct, formatUsd, isMasked, isMissing } from "../api";

// 中央区展开视图：点体征块后，把该区 detail 的全量指标以高信息密度呈现（表格/排名/分桶条，
// 零动效数字说话——智能感克制侧）。null 指标如实显示"无数据 + reason"，掩码值显示"无权查看"，
// 绝不 display 假数字。所有形状锚定 apps/api/cockpit.py 各 _zone_* builder docstring。

// ── 通用小件 ──────────────────────────────────────────────────────────────
function Card({ title, wide, children }: { title: string; wide?: boolean; children: ReactNode }) {
  return (
    <div className={`cp-card ${wide ? "cp-card--wide" : ""}`}>
      <div className="cp-card__title">{title}</div>
      {children}
    </div>
  );
}

function MissingBox({ reason }: { reason: string }) {
  return (
    <div className="cp-missing">
      <b>无数据</b> · {reason}
    </div>
  );
}

function MaskedBox() {
  return <div className="cp-masked">🔒 当前角色无权查看（切到 manager 可见）</div>;
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: "pos" | "neg" }) {
  return (
    <div className="cp-metric-row">
      <span className="cp-metric-row__label">{label}</span>
      <span className={`cp-metric-row__value ${tone ?? ""}`}>{value}</span>
    </div>
  );
}

type BarItem = { label: string; value: number; tone?: string };
function Bars({ items }: { items: BarItem[] }) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <div className="cp-bars">
      {items.map((i) => (
        <div className="cp-bar" key={i.label}>
          <span className="cp-bar__label">{i.label}</span>
          <span className="cp-bar__track">
            <span
              className={`cp-bar__fill ${i.tone ?? ""}`}
              style={{ width: `${(i.value / max) * 100}%` }}
            />
          </span>
          <span className="cp-bar__num num">{formatInt(i.value)}</span>
        </div>
      ))}
    </div>
  );
}

/** 可能是 Missing / Masked / 真实块——统一先判后渲染。 */
function guard(v: unknown): "missing" | "masked" | "real" {
  if (isMasked(v)) return "masked";
  if (isMissing(v)) return "missing";
  return "real";
}

// ── 各区渲染器 ────────────────────────────────────────────────────────────
type D = Record<string, unknown>;

function Money({ d }: { d: D }) {
  const fee = d.fee_exposure as { value_usd: number | string; open_risks: number; rules: string[] };
  const itv = d.in_transit_value;
  const blocked = d.intercepted_overbilling as { value_usd: number | string; resolved_r4_risks: number };
  const margin = d.margin_distribution;
  return (
    <>
      <Card title="费用异常敞口（R4-R6 open）">
        <div className={`cp-metric-big ${isMasked(fee.value_usd) ? "" : "gold"}`}>
          {formatUsd(fee.value_usd)}
        </div>
        <div style={{ marginTop: 8 }}>
          <span className="cp-chip red">{fee.open_risks} 起未闭环</span>{" "}
          {fee.rules.map((r) => (
            <span className="cp-chip" key={r}>
              {r}
            </span>
          ))}
        </div>
      </Card>
      <Card title="被拦截超收（系统帮你省下）">
        <div className="cp-metric-big">{formatUsd(blocked.value_usd)}</div>
        <div style={{ marginTop: 8 }}>
          <span className="cp-chip">{blocked.resolved_r4_risks} 起 R4 已闭环</span>
        </div>
      </Card>
      <Card title="在途货值">
        {isMissing(itv) ? (
          <MissingBox reason={itv.reason} />
        ) : (
          (() => {
            const v = itv as { value_usd: number | string; in_transit_shipments: number; allocation_rows: number };
            return (
              <>
                <div className="cp-metric-big">{formatUsd(v.value_usd)}</div>
                <div style={{ marginTop: 8 }}>
                  <span className="cp-chip">{v.in_transit_shipments} 票在途</span>{" "}
                  <span className="cp-chip">{v.allocation_rows} 分配行</span>
                </div>
              </>
            );
          })()
        )}
      </Card>
      <Card title="准入毛利率分布">
        {guard(margin) === "masked" ? (
          <MaskedBox />
        ) : isMissing(margin) ? (
          <MissingBox reason={margin.reason} />
        ) : (
          (() => {
            const m = margin as { buckets: Record<string, number>; scenarios_total: number };
            const order = ["亏损(<0)", "0-10%", "10-20%", "≥20%"];
            const tone: Record<string, string> = { "亏损(<0)": "red", "0-10%": "amber", "10-20%": "gold", "≥20%": "green" };
            return (
              <Bars
                items={order
                  .filter((k) => k in m.buckets)
                  .map((k) => ({ label: k, value: m.buckets[k], tone: tone[k] }))}
              />
            );
          })()
        )}
      </Card>
    </>
  );
}

function Fulfillment({ d }: { d: D }) {
  const otd = d.otd as { fulfilled_lines: number; measured: number; on_time: number; excluded_no_arrival: number; rate: number | null };
  const hist = d.delay_histogram as { buckets: Record<string, number>; delayed_shipments: number };
  const customs = d.customs_blocked as { value: number; definition: string };
  const order = ["1-3天", "4-7天", "8-14天", "15-29天", "≥30天"];
  const tone: Record<string, string> = { "1-3天": "green", "4-7天": "amber", "8-14天": "amber", "15-29天": "red", "≥30天": "red" };
  return (
    <>
      <Card title="准交率 OTD">
        <div className="cp-metric-big">{formatPct(otd.rate)}</div>
        <div style={{ marginTop: 10 }}>
          <Metric label="到达可测行" value={formatInt(otd.measured)} />
          <Metric label="其中准时" value={formatInt(otd.on_time)} tone="pos" />
          <Metric label="无到达信息（不进分母）" value={formatInt(otd.excluded_no_arrival)} />
        </div>
      </Card>
      <Card title="清关卡点">
        <div className="cp-metric-big">{formatInt(customs.value)}</div>
        <div className="cp-basis">{customs.definition}</div>
      </Card>
      <Card title={`延误分布（${hist.delayed_shipments} 票延误）`} wide>
        {hist.delayed_shipments === 0 ? (
          <div className="cp-masked">当前无延误票</div>
        ) : (
          <Bars
            items={order
              .filter((k) => k in hist.buckets)
              .map((k) => ({ label: k, value: hist.buckets[k], tone: tone[k] }))}
          />
        )}
      </Card>
    </>
  );
}

function Customers({ d }: { d: D }) {
  const top = d.top_exposure as { customer_id: string; customer_name: string; exposure_usd: number | string; open_risks: number; affected_lines: number }[];
  const funnel = d.admission_funnel;
  const health = d.health_cross as { tier: string; customers: number; customers_at_risk: number; open_risks: number }[];
  const linesTotal = d.affected_line_ids_total as number;
  return (
    <>
      <Card title={`风险敞口 Top 客户（波及 ${formatInt(linesTotal)} 订单行）`} wide>
        {top.length === 0 ? (
          <div className="cp-masked">当前无客户被 open 风险波及</div>
        ) : (
          <table className="cp-table">
            <thead>
              <tr>
                <th>客户</th>
                <th className="num">敞口</th>
                <th className="num">风险</th>
                <th className="num">波及行</th>
              </tr>
            </thead>
            <tbody>
              {top.map((c) => (
                <tr key={c.customer_id}>
                  <td className="name" title={c.customer_name}>
                    {c.customer_name}
                  </td>
                  <td className="num">{formatUsd(c.exposure_usd)}</td>
                  <td className="num">{c.open_risks}</td>
                  <td className="num">{c.affected_lines}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="客户分层健康度">
        <table className="cp-table">
          <thead>
            <tr>
              <th>分层</th>
              <th className="num">客户</th>
              <th className="num">受波及</th>
              <th className="num">风险</th>
            </tr>
          </thead>
          <tbody>
            {health.map((h) => (
              <tr key={h.tier}>
                <td>{isMasked(h.tier) ? "🔒" : `Tier ${h.tier}`}</td>
                <td className="num">{h.customers}</td>
                <td className="num">{h.customers_at_risk}</td>
                <td className="num">{h.open_risks}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <Card title="准入漏斗">
        {isMissing(funnel) ? (
          <MissingBox reason={funnel.reason} />
        ) : (
          (() => {
            const f = funnel as { by_status: Record<string, number>; cases_total: number };
            return <Bars items={Object.entries(f.by_status).map(([k, v]) => ({ label: k, value: v }))} />;
          })()
        )}
      </Card>
    </>
  );
}

function Suppliers({ d }: { d: D }) {
  const delivery = d.delivery_hit_rate;
  const defect = d.defect_top;
  const r14 = d.single_source_r14 as { value: number };
  const recon = d.recon_diff_r7_r13 as { open_risks: number; amount_usd: number | string };
  return (
    <>
      <Card title="交期达成率（最差供应商在前）" wide>
        {isMissing(delivery) ? (
          <MissingBox reason={delivery.reason} />
        ) : (
          (() => {
            const dv = delivery as {
              rate: number | null;
              pos_measured: number;
              worst_suppliers: { supplier_id: string; supplier_name: string; pos_measured: number; on_time: number; rate: number | null }[];
            };
            return (
              <>
                <div className="cp-metric-big">{formatPct(dv.rate)}</div>
                <div className="cp-basis">{dv.pos_measured} 张有收货 PO 计入分母</div>
                <table className="cp-table" style={{ marginTop: 10 }}>
                  <thead>
                    <tr>
                      <th>供应商</th>
                      <th className="num">达成率</th>
                      <th className="num">PO</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dv.worst_suppliers.slice(0, 8).map((s) => (
                      <tr key={s.supplier_id}>
                        <td className="name" title={s.supplier_name}>
                          {s.supplier_name}
                        </td>
                        <td className="num">{formatPct(s.rate)}</td>
                        <td className="num">{s.pos_measured}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            );
          })()
        )}
      </Card>
      <Card title="质量缺陷率 Top（defect ppm）">
        {isMissing(defect) ? (
          <MissingBox reason={defect.reason} />
        ) : (
          (() => {
            const df = defect as { top: { supplier_id: string; supplier_name: string; avg_defect_ppm: number; grn_lines: number }[] };
            return (
              <table className="cp-table">
                <thead>
                  <tr>
                    <th>供应商</th>
                    <th className="num">ppm</th>
                  </tr>
                </thead>
                <tbody>
                  {df.top.map((s) => (
                    <tr key={s.supplier_id}>
                      <td className="name" title={s.supplier_name}>
                        {s.supplier_name}
                      </td>
                      <td className="num">{formatInt(s.avg_defect_ppm)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            );
          })()
        )}
      </Card>
      <Card title="风险预警">
        <Metric label="单一供应商依赖（R14）" value={formatInt(r14.value)} tone={r14.value > 0 ? "neg" : undefined} />
        <Metric label="发票对账差异（R7-R13）" value={`${recon.open_risks} 起`} tone={recon.open_risks > 0 ? "neg" : undefined} />
        <Metric label="对账差异金额" value={formatUsd(recon.amount_usd)} />
      </Card>
    </>
  );
}

function Inventory({ d }: { d: D }) {
  const breaches = d.safety_breaches as {
    count: number;
    positions: { inventory_position_id: string; sku_id: string; warehouse_id: string; available_qty: number; safety_stock: number; gap: number }[];
  };
  const variance = d.count_variance;
  const resc = d.rescuable as { risks_checked: number; lines_checked: number; lines_fully_savable: number; lines_partially_savable: number; lines_no_stock: number };
  return (
    <>
      <Card title="安全库存击穿 SKU" wide>
        <div className="cp-metric-big">{formatInt(breaches.count)}</div>
        {breaches.positions.length > 0 && (
          <table className="cp-table" style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>SKU</th>
                <th>仓</th>
                <th className="num">现货</th>
                <th className="num">安全线</th>
                <th className="num">缺口</th>
              </tr>
            </thead>
            <tbody>
              {breaches.positions.slice(0, 8).map((p) => (
                <tr key={p.inventory_position_id}>
                  <td>{p.sku_id}</td>
                  <td>{p.warehouse_id}</td>
                  <td className="num">{p.available_qty}</td>
                  <td className="num">{p.safety_stock}</td>
                  <td className="num" style={{ color: "var(--sev-red)" }}>
                    -{p.gap}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="盘点差异">
        {isMissing(variance) ? (
          <MissingBox reason={variance.reason} />
        ) : (
          (() => {
            const v = variance as { count: number; abs_variance_units: number };
            return (
              <>
                <Metric label="账实不符批次" value={formatInt(v.count)} />
                <Metric label="差异绝对量" value={formatInt(v.abs_variance_units)} />
              </>
            );
          })()
        )}
      </Card>
      <Card title="在途补给可救性（现货救延误）">
        <Metric label="复算风险行" value={formatInt(resc.lines_checked)} />
        <Metric label="可全救" value={formatInt(resc.lines_fully_savable)} tone="pos" />
        <Metric label="可部分救" value={formatInt(resc.lines_partially_savable)} />
        <Metric label="无现货可救" value={formatInt(resc.lines_no_stock)} tone={resc.lines_no_stock > 0 ? "neg" : undefined} />
      </Card>
    </>
  );
}

function Ai({ d }: { d: D }) {
  const today = d.today as { date: string | null; detections: number; proposals: number; approvals: number; rejections: number; approval_rate: number | null };
  const all = d.all_time as { proposals: number; approved: number; rejected: number; pending: number; approval_rate: number | null; sim_activity?: Record<string, number>; last_ai_activity_date?: string };
  const mem = d.resolution_memory;
  const llm = d.llm_calls;
  return (
    <>
      <Card title={`AI 今日（${today.date ?? "—"}）`}>
        <Metric label="检测" value={formatInt(today.detections)} />
        <Metric label="提案" value={formatInt(today.proposals)} />
        <Metric label="批准 / 驳回" value={`${today.approvals} / ${today.rejections}`} />
        <Metric label="今日通过率" value={today.approval_rate === null ? "今日无决策" : formatPct(today.approval_rate)} />
      </Card>
      <Card title="累计运营账">
        <Metric label="提案总数" value={formatInt(all.proposals)} />
        <Metric label="通过率" value={formatPct(all.approval_rate)} tone="pos" />
        <Metric label="批准 / 驳回 / 待批" value={`${all.approved} / ${all.rejected} / ${all.pending}`} />
        {all.last_ai_activity_date && <Metric label="最近 AI 活动" value={all.last_ai_activity_date} />}
      </Card>
      <Card title="历史处置记忆命中">
        {isMissing(mem) ? (
          <MissingBox reason={mem.reason} />
        ) : (
          (() => {
            const m = mem as { total: number; with_cited_precedents: number; hit_rate: number | null };
            return (
              <>
                <div className="cp-metric-big">{formatPct(m.hit_rate)}</div>
                <div style={{ marginTop: 8 }}>
                  <span className="cp-chip">{m.with_cited_precedents}/{m.total} 引用先例</span>
                </div>
              </>
            );
          })()
        )}
      </Card>
      <Card title="AI 调用审计（llm_calls）">
        {isMissing(llm) ? (
          <MissingBox reason={llm.reason} />
        ) : (
          (() => {
            const l = llm as { total: number; by_call_type: Record<string, number> };
            if (l.total === 0) return <div className="cp-masked">审计流水 0 条</div>;
            return <Bars items={Object.entries(l.by_call_type).map(([k, v]) => ({ label: k, value: v }))} />;
          })()
        )}
      </Card>
    </>
  );
}

function Decisions({ d }: { d: D }) {
  const pending = d.pending_proposals as {
    task_id: string;
    title: string;
    priority: string | null;
    proposed_action: string | null;
    assignee_role: string | null;
    amount_usd: number | string | null;
    waiting_since: string | null;
  }[];
  const overdue = d.overdue_tasks;
  const escalated = d.escalated_tasks;
  return (
    <>
      <Card title="等审批的提案（金额降序）" wide>
        {pending.length === 0 ? (
          <div className="cp-masked">当前无待批提案</div>
        ) : (
          <table className="cp-table">
            <thead>
              <tr>
                <th>提案</th>
                <th>动作</th>
                <th>指派</th>
                <th className="num">金额</th>
              </tr>
            </thead>
            <tbody>
              {pending.map((p) => (
                <tr key={p.task_id}>
                  <td className="name" title={p.title}>
                    {p.priority && <span className="cp-chip amber" style={{ marginRight: 6 }}>{p.priority}</span>}
                    {p.title}
                  </td>
                  <td>{p.proposed_action ?? "—"}</td>
                  <td>{p.assignee_role ?? "—"}</td>
                  <td className="num">{formatUsd(p.amount_usd)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="超期与升级">
        {isMissing(overdue) ? (
          <MissingBox reason={overdue.reason} />
        ) : (
          <Metric label="超期任务" value={formatInt((overdue as { value: number }).value)} tone={(overdue as { value: number }).value > 0 ? "neg" : undefined} />
        )}
        {isMissing(escalated) ? (
          <MissingBox reason={escalated.reason} />
        ) : (
          <Metric label="升级件" value={formatInt((escalated as { value: number }).value)} tone={(escalated as { value: number }).value > 0 ? "neg" : undefined} />
        )}
      </Card>
    </>
  );
}

const RENDERERS: Record<Zone["zone"], (p: { d: D }) => ReactNode> = {
  money: Money,
  fulfillment: Fulfillment,
  customers: Customers,
  suppliers: Suppliers,
  inventory: Inventory,
  ai: Ai,
  decisions: Decisions,
};

interface Props {
  zone: Zone;
  onBack: () => void;
}

export default function ZoneDetail({ zone, onBack }: Props) {
  const Renderer = RENDERERS[zone.zone];
  return (
    <div className="cp-zone">
      <div className="cp-panel-head">
        <button className="cp-zone__back" onClick={onBack}>
          ← 返回全景
        </button>
        <span className="cp-panel-head__title">{zone.headline_label}</span>
        {zone.alert_count > 0 && <span className="cp-chip red">{zone.alert_count} 告警</span>}
        <span className="cp-panel-head__spacer" />
        {zone.trend && (
          <span className="cp-panel-head__meta num">
            {zone.trend.metric} {zone.trend.delta >= 0 ? "+" : ""}
            {zone.trend.delta} · {zone.trend.window_days}d 窗
          </span>
        )}
      </div>
      <div className="cp-zone__body">
        <Renderer d={zone.detail} />
      </div>
    </div>
  );
}
