import type { ReactNode } from "react";
import type { Missing, Zone } from "../api";
import { formatInt, formatPct, formatUsd, isMasked, isMissing } from "../api";
import Icon from "../components/Icons";

// 区聚合上下文（下钻第二段"队列"下方的补充卡片）——V10 方案 C。
// 有队列的区（客户/供应商/库存/待拍板）：队列已呈现主列表，这里只补"非队列"的聚合卡（不重复主表）。
// 无队列的区（钱/履约/AI 是存量聚合指标，无逐条列表）：这里承接全部聚合卡。
// 全部 null 如实"无数据 + reason"、掩码"无权查看"，形状锚定 apps/api/cockpit.py 各 _zone_* docstring。

// ── 通用小件（沿用原 ZoneDetail 视觉件）────────────────────────────────────
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
  return (
    <div className="cp-masked cp-masked--row">
      <Icon name="lock" size={13} /> 当前角色无权查看（切到 manager 可见）
    </div>
  );
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
            <span className={`cp-bar__fill ${i.tone ?? ""}`} style={{ width: `${(i.value / max) * 100}%` }} />
          </span>
          <span className="cp-bar__num num">{formatInt(i.value)}</span>
        </div>
      ))}
    </div>
  );
}
function guard(v: unknown): "missing" | "masked" | "real" {
  if (isMasked(v)) return "masked";
  if (isMissing(v)) return "missing";
  return "real";
}

type D = Record<string, unknown>;

// ── 无队列区：全聚合卡 ─────────────────────────────────────────────────────
type PaymentFlow = { count: number; amount_usd: number | string; overdue: { count: number; amount_usd: number | string } | Missing };
type NetCash14d = {
  value_usd: number | string;
  window_days: number;
  out_scheduled_usd: number | string;
  in_scheduled_usd: number | string;
  threshold_usd: number | string;
  breach: boolean;
  window: string;
};

/** 应收/应付水位卡体（G2/V8-②，两卡同构，direction 只影响标题/笔数文案）。 */
function PaymentFlowCard({ title, chipSuffix, flow }: { title: string; chipSuffix: string; flow: unknown }) {
  return (
    <Card title={title}>
      {isMissing(flow) ? (
        <MissingBox reason={flow.reason} />
      ) : (
        (() => {
          const f = flow as PaymentFlow;
          return (
            <>
              <div className={`cp-metric-big ${isMasked(f.amount_usd) ? "" : "gold"}`}>{formatUsd(f.amount_usd)}</div>
              <div style={{ marginTop: 8 }}>
                <span className="cp-chip">
                  {formatInt(f.count)} {chipSuffix}
                </span>
              </div>
              <div style={{ marginTop: 10 }}>
                {isMissing(f.overdue) ? (
                  <MissingBox reason={f.overdue.reason} />
                ) : (
                  <Metric
                    label="其中已逾期"
                    value={`${formatUsd(f.overdue.amount_usd)} · ${formatInt(f.overdue.count)} 笔`}
                    tone={!isMasked(f.overdue.amount_usd) && f.overdue.count > 0 ? "neg" : undefined}
                  />
                )}
              </div>
            </>
          );
        })()
      )}
    </Card>
  );
}

/** 14 天净流出预警卡体：breach 是状态位，不随金额一起掩码——ops 也能看到告警灯。 */
function NetCash14dCard({ cash14 }: { cash14: unknown }) {
  return (
    <Card title="现金水位预警（未来窗口）" wide>
      {isMissing(cash14) ? (
        <MissingBox reason={cash14.reason} />
      ) : (
        (() => {
          const c = cash14 as NetCash14d;
          const masked = isMasked(c.value_usd);
          return (
            <>
              <div className={`cp-metric-big ${masked ? "" : c.breach ? "neg" : "gold"}`}>{formatUsd(c.value_usd)}</div>
              <div className="cp-basis">
                未来 {c.window_days} 天 scheduled 应付 − 应收 · 阈值 {formatUsd(c.threshold_usd)}
              </div>
              <div style={{ marginTop: 8 }}>
                <span className={`cp-chip ${c.breach ? "red" : ""}`}>{c.breach ? "预警：净流出击穿阈值" : "水位正常"}</span>
              </div>
              <div style={{ marginTop: 10 }}>
                <Metric label="窗口内应付（scheduled）" value={formatUsd(c.out_scheduled_usd)} />
                <Metric label="窗口内应收（scheduled）" value={formatUsd(c.in_scheduled_usd)} />
              </div>
            </>
          );
        })()
      )}
    </Card>
  );
}

function MoneyCtx({ d }: { d: D }) {
  const fee = d.fee_exposure as { value_usd: number | string; open_risks: number; rules: string[] };
  const itv = d.in_transit_value;
  const blocked = d.intercepted_overbilling as { value_usd: number | string; resolved_r4_risks: number };
  const margin = d.margin_distribution;
  return (
    <>
      <Card title="费用异常敞口（R4-R6 open）">
        <div className={`cp-metric-big ${isMasked(fee.value_usd) ? "" : "gold"}`}>{formatUsd(fee.value_usd)}</div>
        <div style={{ marginTop: 8 }}>
          <span className="cp-chip red">{fee.open_risks} 起未闭环</span>{" "}
          {fee.rules.map((r) => (
            <span className="cp-chip" key={r}>
              {r}
            </span>
          ))}
        </div>
      </Card>
      <Card title="被拦回超收（系统帮你省下）">
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
                  <span className="cp-chip">{v.in_transit_shipments} 票在途</span> <span className="cp-chip">{v.allocation_rows} 分配行</span>
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
            const m = margin as { buckets: Record<string, number> };
            const order = ["亏损(<0)", "0-10%", "10-20%", "≥20%"];
            const tone: Record<string, string> = { "亏损(<0)": "red", "0-10%": "amber", "10-20%": "gold", "≥20%": "green" };
            return <Bars items={order.filter((k) => k in m.buckets).map((k) => ({ label: k, value: m.buckets[k], tone: tone[k] }))} />;
          })()
        )}
      </Card>
      <PaymentFlowCard title="应收水位（在外未收）" chipSuffix="笔在外" flow={d.receivables} />
      <PaymentFlowCard title="应付水位（要付未付）" chipSuffix="笔待付" flow={d.payables} />
      <NetCash14dCard cash14={d.net_cash_14d} />
    </>
  );
}

function FulfillmentCtx({ d }: { d: D }) {
  const otd = d.otd as { measured: number; on_time: number; excluded_no_arrival: number; rate: number | null };
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
        {hist.delayed_shipments === 0 ? <div className="cp-masked">当前无延误票</div> : <Bars items={order.filter((k) => k in hist.buckets).map((k) => ({ label: k, value: hist.buckets[k], tone: tone[k] }))} />}
      </Card>
    </>
  );
}

function AiCtx({ d }: { d: D }) {
  const today = d.today as { date: string | null; detections: number; proposals: number; approvals: number; rejections: number; approval_rate: number | null };
  const all = d.all_time as { proposals: number; approved: number; rejected: number; pending: number; approval_rate: number | null; last_ai_activity_date?: string };
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
                  <span className="cp-chip">
                    {m.with_cited_precedents}/{m.total} 引用先例
                  </span>
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

// ── 有队列区：只补非队列聚合卡（不重复队列主表）─────────────────────────────
function CustomersCtx({ d }: { d: D }) {
  const funnel = d.admission_funnel;
  const health = d.health_cross as { tier: string; customers: number; customers_at_risk: number; open_risks: number }[];
  return (
    <>
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
            {/* key 修复（P1）：tier 是本体敏感字段，ops 等无权角色下每一行的 tier 都被掩码成同一个
                占位串"🔒无权查看"——原先直接拿 h.tier 当 key，掩码时全表行共享同一 key，触发 React
                重复 key 告警（控制台 8 次）。改用行号兜底，掩码/非掩码都保证 key 稳定唯一。 */}
            {health.map((h, i) => (
              <tr key={isMasked(h.tier) ? `masked-${i}` : h.tier}>
                <td>{isMasked(h.tier) ? <Icon name="lock" size={12} /> : `Tier ${h.tier}`}</td>
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
            const f = funnel as { by_status: Record<string, number> };
            return <Bars items={Object.entries(f.by_status).map(([k, v]) => ({ label: k, value: v }))} />;
          })()
        )}
      </Card>
    </>
  );
}

function SuppliersCtx({ d }: { d: D }) {
  const defect = d.defect_top;
  const r14 = d.single_source_r14 as { value: number };
  const recon = d.recon_diff_r7_r13 as { open_risks: number; amount_usd: number | string };
  // V23① R22/R23（可选键：旧载荷/缺列世界无此键时不渲染，不编造 0）。R22/R23 已接入模拟世界
  // （sim.ai_loop.run_supplier_risk 回填末尾一次性检测）→ 两世界皆后端现算真实计数，"未接入"白话退场。
  const r22 = d.perf_degradation_r22 as { open_risks: number; amount_usd: number | string } | undefined;
  const r23 = d.qual_expiry_r23 as { open_risks: number } | undefined;
  return (
    <>
      <Card title="质量缺陷率 Top（defect ppm）">
        {isMissing(defect) ? (
          <MissingBox reason={defect.reason} />
        ) : (
          (() => {
            const df = defect as { top: { supplier_id: string; supplier_name: string; avg_defect_ppm: number }[] };
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
        {/* R22/R23：两世界皆显后端现算真实计数（sim 已接入）。载荷缺键（旧库/缺列世界）仍不渲染（不编造）。 */}
        {r22 && (
          <Metric
            label="供应商绩效劣化（R22）"
            value={`${r22.open_risks} 家 · ${formatUsd(r22.amount_usd)}`}
            tone={r22.open_risks > 0 ? "neg" : undefined}
          />
        )}
        {r23 && (
          <Metric
            label="资质过期预警（R23）"
            value={`${r23.open_risks} 证`}
            tone={r23.open_risks > 0 ? "neg" : undefined}
          />
        )}
      </Card>
    </>
  );
}

function InventoryCtx({ d }: { d: D }) {
  const variance = d.count_variance;
  const resc = d.rescuable as { lines_checked: number; lines_fully_savable: number; lines_partially_savable: number; lines_no_stock: number; risks_checked: number };
  return (
    <>
      <Card title="盘点差异">
        {isMissing(variance) ? (
          <MissingBox reason={variance.reason} />
        ) : (
          (() => {
            const v = variance as { count: number; abs_variance_units: number };
            return (
              <>
                <Metric label="账实不符批次" value={formatInt(v.count)} tone={v.count > 0 ? "neg" : undefined} />
                <Metric label="差异绝对量" value={formatInt(v.abs_variance_units)} />
              </>
            );
          })()
        )}
      </Card>
      <Card title="在途补给可救性（现货救延误）">
        <Metric label="复算延误风险" value={formatInt(resc.risks_checked)} />
        <Metric label="复算风险行" value={formatInt(resc.lines_checked)} />
        <Metric label="可全救" value={formatInt(resc.lines_fully_savable)} tone="pos" />
        <Metric label="可部分救" value={formatInt(resc.lines_partially_savable)} />
        <Metric label="无现货可救" value={formatInt(resc.lines_no_stock)} tone={resc.lines_no_stock > 0 ? "neg" : undefined} />
      </Card>
    </>
  );
}

function DecisionsCtx({ d }: { d: D }) {
  const overdue = d.overdue_tasks;
  const escalated = d.escalated_tasks;
  return (
    <Card title="超期与升级">
      {isMissing(overdue) ? <MissingBox reason={overdue.reason} /> : <Metric label="超期任务" value={formatInt((overdue as { value: number }).value)} tone={(overdue as { value: number }).value > 0 ? "neg" : undefined} />}
      {isMissing(escalated) ? <MissingBox reason={escalated.reason} /> : <Metric label="升级件" value={formatInt((escalated as { value: number }).value)} tone={(escalated as { value: number }).value > 0 ? "neg" : undefined} />}
    </Card>
  );
}

const CTX: Record<Zone["zone"], (p: { d: D }) => ReactNode> = {
  money: MoneyCtx,
  fulfillment: FulfillmentCtx,
  ai: AiCtx,
  customers: CustomersCtx,
  suppliers: SuppliersCtx,
  inventory: InventoryCtx,
  decisions: DecisionsCtx,
};

export default function ZoneContext({ zone }: { zone: Zone }) {
  const C = CTX[zone.zone];
  return (
    <div className="cp-context-grid">
      <C d={zone.detail as D} />
    </div>
  );
}
