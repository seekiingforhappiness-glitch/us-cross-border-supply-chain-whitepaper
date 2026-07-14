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
  type Role,
} from "../api";
import Icon from "../components/Icons";
import { LAYER_CN, type PanoSelection } from "./panoramaModel";

// 影响分析面板（V9 追加修正的核心）：替代全景上的实体级线条爆炸——点异常组块后，用结构化
// 紧凑表格呈现传播链：风险摘要 → 受影响订单行（N 条/金额合计/前 8 明细）→ 波及客户
// （从受影响行 line→order→customer 归并，按敞口排序）→ 处置任务入口 → 组内成员。
// 数据源：既有 panorama alerts/affected + GET /objects + traverse（apps/api 只读消费，不改）。
// 注：本世界 /objects/Customer 端点 500（apps/api 既有问题，本单不碰）——客户名不可得，
// 波及客户按 customer_id 呈现；全景同步点亮的结构关联组见 selection.affected（页脚计数）。

interface CustRow {
  customerId: string;
  lines: number;
  exposure: number;
  masked: boolean;
}

const SEV_RANK: Record<string, number> = { critical: 3, high: 2, medium: 1, low: 0 };
const SEV_CN: Record<string, string> = { critical: "紧急", high: "高", medium: "中", low: "低" };
const RULE_CN: Record<string, string> = {
  delay_breach: "延误击穿承诺",
  missing_docs: "清关文件缺失",
  stalled: "在途停滞",
};

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

export default function ImpactPanel({
  selection,
  role,
  onOpenObject,
  onClose,
}: {
  selection: PanoSelection;
  role: Role;
  onOpenObject: (r: ObjectRef) => void;
  onClose: () => void;
}) {
  const { block, affected } = selection;
  const alerts = [...block.alerts].sort((a, b) => (SEV_RANK[b.severity] ?? 1) - (SEV_RANK[a.severity] ?? 1));
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
  }, [block.id]);

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

        // 波及客户：line→order→customer_id 归并 + 敞口(qty×price)排序（/objects/Customer 500，
        // 只到 customer_id 粒度，如实）。SalesOrder 端点可用；按 so_id 去重拉取，有界。
        const soIds = [...new Set(okLines.map((l) => String(l.so_id)).filter(Boolean))];
        const orders = await Promise.all(
          soIds.map((id) => fetchObject("SalesOrder", id, role).catch(() => null)),
        );
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
          <Icon name="spark" size={13} /> 影响分析
        </div>
        <div className="cp-impact__title">
          <span className="cp-impact__layer">{LAYER_CN[block.layer]}</span>
          <span className="cp-impact__name">{block.label}</span>
        </div>
        <button className="cp-drawer__close" onClick={onClose} aria-label="关闭影响分析">
          <Icon name="x" size={15} />
        </button>
      </div>

      <div className="cp-impact__body">
        {alerts.length === 0 ? (
          <div className="cp-impact__sect">
            <div className="cp-impact__ok">
              <Icon name="approve" size={15} /> 该组块当前无未闭环风险
            </div>
            <div className="cp-basis">组块规模 {block.sub}</div>
          </div>
        ) : (
          <>
            {/* 风险切换（组块多风险时） */}
            {alerts.length > 1 && (
              <div className="cp-impact__risks">
                {alerts.map((a, i) => (
                  <button
                    key={a.risk_event_id}
                    className={`cp-riskchip ${i === riskIdx ? "is-active" : ""}`}
                    onClick={() => setRiskIdx(i)}
                  >
                    <span className={sevChipClass(a.severity)} style={{ marginRight: 4 }}>
                      {a.rule_id}
                    </span>
                    {a.risk_event_id.replace(/^RSK-?/, "")}
                  </button>
                ))}
              </div>
            )}

            {/* 风险摘要 */}
            <div className="cp-impact__sect">
              <div className="cp-impact__sect-t">风险摘要</div>
              {err ? (
                <div className="cp-missing"><b>无法加载风险</b> · {err}</div>
              ) : !risk ? (
                <div className="cp-inline-load">{loading ? "加载中…" : "—"}</div>
              ) : (
                <div className="cp-impact__risk">
                  <div className="cp-impact__risk-top">
                    <span className={sevChipClass(String(risk.severity))}>
                      {SEV_CN[String(risk.severity)] ?? String(risk.severity)}
                    </span>
                    <span className="cp-impact__rule num">{String(risk.rule_id)}</span>
                    <span className="cp-impact__rule-cn">
                      {RULE_CN[String(risk.type)] ?? String(risk.type)}
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
                        <tr
                          key={String(l.so_line_id)}
                          className="is-click"
                          onClick={() => onOpenObject({ type: "SalesOrderLine", id: String(l.so_line_id) })}
                        >
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
              {risk && affectedLineCount > (lines?.length ?? 0) && (
                <div className="cp-basis">显示前 {lines?.length} 行（共 {affectedLineCount} 行受影响）</div>
              )}
            </div>

            {/* 波及客户（line→order→customer_id 归并，按敞口排序） */}
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

            {/* AI 处置任务入口 */}
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
        {block.members.length > 0 && (
          <div className="cp-impact__sect">
            <div className="cp-impact__sect-t">
              组内成员 <span className="cp-impact__agg">{block.members.length}</span>
            </div>
            <div className="cp-impact__members">
              {block.members.slice(0, 24).map((m) => (
                <button
                  key={m.id}
                  className={`cp-member ${m.ref ? "" : "is-plain"}`}
                  onClick={() => m.ref && onOpenObject(m.ref)}
                  title={m.ref ? `打开 ${m.ref.type} ${m.ref.id}` : m.label}
                >
                  <span className="num">{m.label.length > 18 ? m.label.slice(0, 17) + "…" : m.label}</span>
                  {m.note && <span className="cp-member__note">{m.note}</span>}
                </button>
              ))}
              {block.members.length > 24 && (
                <span className="cp-member is-plain">+{block.members.length - 24} 更多</span>
              )}
            </div>
          </div>
        )}

        {affected.length > 0 && (
          <div className="cp-impact__foot">
            全景同步点亮 {affected.length} 个结构关联组块（聚合邻域投影，非因果口径）
          </div>
        )}
      </div>
    </div>
  );
}
