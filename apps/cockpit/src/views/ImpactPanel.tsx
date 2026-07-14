import { useEffect, useState } from "react";
import {
  fetchObject,
  formatInt,
  formatUsd,
  isMasked,
  MASK,
  traverse,
  type ObjectFields,
  type ObjectRef,
  type PanoAlert,
  type Role,
} from "../api";
import Icon from "../components/Icons";
import { RULE_CN, RULE_TYPE_CN, SEV_CN } from "./aiFlowModel";

// 影响分析面板（下钻第三段"详情"，右栏滑出）——V10 方案 C 直接复用 B3 资产、解耦全景依赖。
// 风险类队列条目（区队列/航线队列的风险条）点开 → 这里：风险摘要 → 受影响订单行（N 条/金额合计/
// 前 8 明细）→ 波及客户（line→order→customer 归并按敞口排序）→ 处置任务入口 → **动作区占位**
// （V10-B：真实批/驳留 Streamlit 操作台，此处只给指引 + AI 建议摘要，防审批语义分叉/单量爆炸）。
// 数据源：GET /objects + traverse（apps/api 只读消费，不改一行）；掩码/缺数如实。

export interface ImpactFocus {
  eyebrow?: string; // 默认"影响分析"
  title: string; // 主名（风险 id / 客户 / 航线）
  subtitle?: string; // 上下文行（区/层/路径）
  alerts: PanoAlert[]; // 待呈现风险（≥1）；单条时最小 alert 仅需 risk_event_id，详情由 fetch 补全
  members?: { id: string; label: string; ref: ObjectRef | null; note?: string }[];
  actionHint?: string; // AI 建议摘要（如提案 proposed_action），进动作区占位
}

interface CustRow {
  customerId: string;
  lines: number;
  exposure: number;
  masked: boolean;
}

const SEV_RANK: Record<string, number> = { critical: 3, high: 2, medium: 1, low: 0 };
// 规则/severity 译名改从 aiFlowModel 权威映射表取（原地重复定义且部分错译/错键——如
// R1 曾显示"延误击穿承诺"而非权威源 ux_copy.py 的"延误传导"，"missing_docs"键名也与
// ontology 实际枚举值"docs_missing"不符，从未真正命中过——B5 归一为一处映射全端共享）。

function sevChipClass(sev: string): string {
  if (SEV_RANK[sev] >= 2) return "cp-chip red";
  if (SEV_RANK[sev] >= 1) return "cp-chip amber";
  return "cp-chip";
}

function parseIds(raw: unknown): string[] {
  if (typeof raw !== "string" || !raw || raw === "[]") return [];
  try {
    const v = JSON.parse(raw);
    return Array.isArray(v) ? v.map(String) : [];
  } catch {
    return [];
  }
}

const LINE_FETCH_CAP = 8; // 明细行按需拉取上限（用户点击触发，非循环，有界）

export default function ImpactPanel({ focus, role, onOpenObject, onClose }: { focus: ImpactFocus; role: Role; onOpenObject: (r: ObjectRef) => void; onClose: () => void }) {
  const alerts = [...focus.alerts].sort((a, b) => (SEV_RANK[b.severity] ?? 1) - (SEV_RANK[a.severity] ?? 1));
  const members = focus.members ?? [];
  const [riskIdx, setRiskIdx] = useState(0);
  const focusAlert = alerts[riskIdx] ?? null;

  const [risk, setRisk] = useState<ObjectFields | null>(null);
  const [lines, setLines] = useState<ObjectFields[] | null>(null);
  const [custRows, setCustRows] = useState<CustRow[] | null>(null);
  const [taskIds, setTaskIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setRiskIdx(0);
  }, [focus.title]);

  useEffect(() => {
    if (!focusAlert) {
      setRisk(null);
      setLines(null);
      setCustRows(null);
      setTaskIds([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setErr(null);
    setRisk(null);
    setLines(null);
    setCustRows(null);
    setTaskIds([]);
    const rid = focusAlert.risk_event_id;
    fetchObject("RiskEvent", rid, role)
      .then(async (r) => {
        if (cancelled) return;
        setRisk(r);
        const ids = parseIds(r.affected_so_line_ids).slice(0, LINE_FETCH_CAP);
        const [lineRes, taskRes] = await Promise.all([
          Promise.all(ids.map((id) => fetchObject("SalesOrderLine", id, role).catch(() => null))),
          traverse("RiskEvent", rid, "task_handles_risk", role).catch(() => null),
        ]);
        if (cancelled) return;
        const okLines = lineRes.filter((x): x is ObjectFields => x !== null);
        setLines(okLines);
        setTaskIds(taskRes?.neighbor_ids ?? []);

        // 波及客户：line→order→customer_id 归并 + 敞口(qty×price)排序，有界。
        const soIds = [...new Set(okLines.map((l) => String(l.so_id)).filter(Boolean))];
        const orders = await Promise.all(soIds.map((id) => fetchObject("SalesOrder", id, role).catch(() => null)));
        if (cancelled) return;
        const soToCust = new Map<string, string>();
        orders.forEach((o, i) => {
          if (o && o.customer_id) soToCust.set(soIds[i], String(o.customer_id));
        });
        const agg = new Map<string, CustRow>();
        for (const l of okLines) {
          const cust = soToCust.get(String(l.so_id));
          if (!cust) continue;
          const row = agg.get(cust) ?? { customerId: cust, lines: 0, exposure: 0, masked: false };
          row.lines += 1;
          const qty = l.qty;
          const price = l.unit_price_usd;
          if (price === MASK) row.masked = true;
          else if (typeof qty === "number" && typeof price === "number") row.exposure += qty * price;
          agg.set(cust, row);
        }
        setCustRows([...agg.values()].sort((a, b) => b.exposure - a.exposure));
      })
      .catch((e: Error) => !cancelled && setErr(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [focusAlert, role]);

  const affectedLineCount = risk ? parseIds(risk.affected_so_line_ids).length : 0;
  const totalUsd = risk?.affected_value_usd;

  return (
    <div className="cp-impact">
      <div className="cp-impact__head">
        <div className="cp-impact__eyebrow">
          <Icon name="spark" size={13} /> {focus.eyebrow ?? "影响分析"}
        </div>
        <div className="cp-impact__title">
          {focus.subtitle && <span className="cp-impact__layer">{focus.subtitle}</span>}
          <span className="cp-impact__name">{focus.title}</span>
        </div>
        <button className="cp-drawer__close" onClick={onClose} aria-label="关闭影响分析">
          <Icon name="x" size={15} />
        </button>
      </div>

      <div className="cp-impact__body">
        {alerts.length === 0 ? (
          <div className="cp-impact__sect">
            <div className="cp-impact__ok">
              <Icon name="approve" size={15} /> 当前无未闭环风险
            </div>
            {focus.subtitle && <div className="cp-basis">{focus.subtitle}</div>}
          </div>
        ) : (
          <>
            {/* 风险切换（多风险时） */}
            {alerts.length > 1 && (
              <div className="cp-impact__risks">
                {alerts.map((a, i) => (
                  <button key={`${a.risk_event_id}-${i}`} className={`cp-riskchip ${i === riskIdx ? "is-active" : ""}`} onClick={() => setRiskIdx(i)}>
                    {a.rule_id && (
                      <span className={sevChipClass(a.severity)} style={{ marginRight: 4 }}>
                        {a.rule_id}
                      </span>
                    )}
                    {a.risk_event_id.replace(/^RSK-?/, "")}
                  </button>
                ))}
              </div>
            )}

            {/* 风险摘要 */}
            <div className="cp-impact__sect">
              <div className="cp-impact__sect-t">风险摘要</div>
              {err ? (
                <div className="cp-missing">
                  <b>无法加载风险</b> · {err}
                </div>
              ) : !risk ? (
                <div className="cp-inline-load">{loading ? "加载中…" : "—"}</div>
              ) : (
                <div className="cp-impact__risk">
                  <div className="cp-impact__risk-top">
                    <span className={sevChipClass(String(risk.severity))}>{SEV_CN[String(risk.severity)] ?? String(risk.severity)}</span>
                    <span className="cp-impact__rule num">{String(risk.rule_id)}</span>
                    <span className="cp-impact__rule-cn">
                      {RULE_CN[String(risk.rule_id)] || RULE_TYPE_CN[String(risk.type)] || String(risk.type)}
                    </span>
                    <span className="cp-impact__risk-id num" onClick={() => onOpenObject({ type: "RiskEvent", id: String(risk.risk_event_id) })}>
                      {String(risk.risk_event_id)}
                    </span>
                  </div>
                  <div className="cp-impact__cause">{String(risk.root_cause ?? "—")}</div>
                  <div className="cp-impact__meta num">
                    检出 {String(risk.detected_at ?? "—")} · 状态 {String(risk.status ?? "—")}
                    {risk.shipment_id ? <> · 锚 {String(risk.shipment_id)}</> : null}
                  </div>
                </div>
              )}
            </div>

            {/* 受影响订单行 */}
            <div className="cp-impact__sect">
              <div className="cp-impact__sect-t">
                受影响订单行
                {risk && (
                  <span className="cp-impact__agg">
                    {affectedLineCount} 条 · 合计 <b className={isMasked(totalUsd) ? "" : "gold"}>{formatUsd(totalUsd as number | string)}</b>
                  </span>
                )}
              </div>
              {!lines ? (
                <div className="cp-inline-load">{loading ? "加载中…" : "—"}</div>
              ) : lines.length === 0 ? (
                <div className="cp-masked">无可展开的订单行明细</div>
              ) : (
                <table className="cp-table cp-table--tight">
                  <thead>
                    <tr>
                      <th>订单行</th>
                      <th>SKU</th>
                      <th className="num">数量</th>
                      <th className="num">金额</th>
                      <th>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lines.map((l) => {
                      const qty = l.qty as number;
                      const price = l.unit_price_usd;
                      const amount = typeof price === "number" && typeof qty === "number" ? qty * price : price;
                      return (
                        <tr key={String(l.so_line_id)} className="is-click" onClick={() => onOpenObject({ type: "SalesOrderLine", id: String(l.so_line_id) })}>
                          <td className="name num">{String(l.so_line_id).replace(/^SOL-?/, "")}</td>
                          <td className="num">{String(l.sku_id)}</td>
                          <td className="num">{formatInt(qty)}</td>
                          <td className="num">{formatUsd(amount as number | string)}</td>
                          <td>{String(l.line_status)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
              {risk && affectedLineCount > (lines?.length ?? 0) && <div className="cp-basis">显示前 {lines?.length} 行（共 {affectedLineCount} 行受影响）</div>}
            </div>

            {/* 波及客户 */}
            <div className="cp-impact__sect">
              <div className="cp-impact__sect-t">
                波及客户
                {custRows && custRows.length > 0 && <span className="cp-impact__agg">{custRows.length} 家</span>}
              </div>
              {!custRows ? (
                <div className="cp-inline-load">{loading ? "归并中…" : "—"}</div>
              ) : custRows.length === 0 ? (
                <div className="cp-masked">无可归并的客户（受影响行未关联到订单）</div>
              ) : (
                <table className="cp-table cp-table--tight">
                  <thead>
                    <tr>
                      <th>客户</th>
                      <th className="num">波及行</th>
                      <th className="num">敞口</th>
                    </tr>
                  </thead>
                  <tbody>
                    {custRows.map((c) => (
                      <tr key={c.customerId}>
                        <td className="name num">{c.customerId}</td>
                        <td className="num">{c.lines}</td>
                        <td className="num">{c.masked ? formatUsd(MASK) : formatUsd(c.exposure)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            {/* 处置任务入口（打开对象卡看任务） */}
            {taskIds.length > 0 && (
              <div className="cp-impact__sect">
                <div className="cp-impact__sect-t">处置任务</div>
                <div className="cp-links">
                  {taskIds.map((tid) => (
                    <button key={tid} className="cp-link-btn" onClick={() => onOpenObject({ type: "Task", id: tid })}>
                      <Icon name="propose" size={14} />
                      <span className="num">{tid}</span>
                      <span className="cp-link-btn__dir">打开处置任务 →</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {/* 组内成员（可开对象卡） */}
        {members.length > 0 && (
          <div className="cp-impact__sect">
            <div className="cp-impact__sect-t">
              相关成员 <span className="cp-impact__agg">{members.length}</span>
            </div>
            <div className="cp-impact__members">
              {members.slice(0, 24).map((m) => (
                <button key={m.id} className={`cp-member ${m.ref ? "" : "is-plain"}`} onClick={() => m.ref && onOpenObject(m.ref)} title={m.ref ? `打开 ${m.ref.type} ${m.ref.id}` : m.label}>
                  <span className="num">{m.label.length > 18 ? m.label.slice(0, 17) + "…" : m.label}</span>
                  {m.note && <span className="cp-member__note">{m.note}</span>}
                </button>
              ))}
              {members.length > 24 && <span className="cp-member is-plain">+{members.length - 24} 更多</span>}
            </div>
          </div>
        )}

        {/* 动作区占位（V10-B：真实批/驳留 Streamlit 操作台，此处只指引 + AI 建议摘要） */}
        <div className="cp-action">
          <div className="cp-action__t">
            <Icon name="stamp" size={13} /> 动作区
          </div>
          {focus.actionHint && (
            <div className="cp-action__hint">
              <span className="cp-action__hint-k">AI 建议</span>
              <span className="cp-action__hint-v">{focus.actionHint}</span>
            </div>
          )}
          <div className="cp-action__note">处置动作（批准 / 驳回 / 关闭）在 Streamlit 操作台执行——驾驶舱专注"看清 + 拍板定位"，审批语义与 maker-checker 留在操作台，防单量爆炸。</div>
        </div>
      </div>
    </div>
  );
}
