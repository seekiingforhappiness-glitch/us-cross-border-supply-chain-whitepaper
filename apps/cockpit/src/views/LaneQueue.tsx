import { formatInt } from "../api";
import Icon from "../components/Icons";
import { type Lane } from "./corridorModel";
import WorkQueue from "./WorkQueue";
import type { DrillTarget, QueueRow, QueueSpec } from "./zoneModel";

// 航线异常队列（下钻第二段，履约地图点弧线进入）——V10 补记。
// 面包屑「指挥墙 > 航线地图 > 起→目」。主队列=该航线未闭环异常（按严重度降序），点条 → 风险详情面板；
// 下方上下文=该航线全部在途货件（实体粒度世界才有），点条 → 货件对象卡。与区队列同一下钻语义。

const SEV_CN: Record<string, string> = { critical: "紧急", high: "高", medium: "中", low: "低" };
const sevBadge = (sev: string): QueueRow["badge"] => {
  const rank = { critical: 3, high: 2, medium: 1, low: 0 }[sev] ?? 1;
  return { text: SEV_CN[sev] ?? sev, tone: rank >= 2 ? "red" : "amber" };
};

function laneSpec(lane: Lane): QueueSpec {
  return {
    columns: [{ label: "风险" }, { label: "规则" }, { label: "严重度" }, { label: "货件" }],
    basis: "该航线未闭环异常，按严重度降序（API 锚定口径）",
    rows: lane.alerts.map((a, i) => ({
      key: `${a.risk_event_id}:${a.shipment_id ?? i}`,
      badge: sevBadge(a.severity),
      cells: [{ text: a.risk_event_id, num: true }, { text: a.rule_id }, { text: SEV_CN[a.severity] ?? a.severity }, { text: a.shipment_id ?? "—", num: true }],
      drill: { kind: "risk", riskId: a.risk_event_id, title: a.risk_event_id, subtitle: `${lane.origin}→${lane.dest} · 航线异常` },
    })),
  };
}

interface Props {
  lane: Lane;
  onWall: () => void;
  onMap: () => void;
  onDrill: (t: DrillTarget, key: string) => void;
  activeKey: string | null;
}

export default function LaneQueue({ lane, onWall, onMap, onDrill, activeKey }: Props) {
  const spec = laneSpec(lane);
  // 上下文：该航线全部在途货件（实体粒度世界）——可点开货件对象卡。
  const context =
    lane.shipments.length > 0 ? (
      <div className="cp-context-grid">
        <div className="cp-card cp-card--wide">
          <div className="cp-card__title">
            该航线在途货件 · {lane.count} 票 · {lane.containers} 柜
          </div>
          <table className="cp-table">
            <thead>
              <tr>
                <th>货件</th>
                <th>状态</th>
                <th className="num">延误天</th>
                <th className="num">柜</th>
                <th className="num">告警</th>
                <th aria-label="打开" />
              </tr>
            </thead>
            <tbody>
              {lane.shipments.map((s) => (
                <tr
                  key={s.id}
                  className={s.ref ? "is-click" : ""}
                  onClick={() => s.ref && onDrill({ kind: "object", ref: s.ref, title: s.ref.id }, s.id)}
                >
                  <td className="name num">{s.id.replace(/^shipment:/, "")}</td>
                  <td>{s.status ?? "—"}</td>
                  <td className="num" style={(s.delay ?? 0) > 0 ? { color: "var(--sev-amber)" } : undefined}>
                    {s.delay ? formatInt(s.delay) : "—"}
                  </td>
                  <td className="num">{s.containers ?? 0}</td>
                  <td className="num">{s.alertCount > 0 ? s.alertCount : "—"}</td>
                  <td className="cp-queue__go">{s.ref && <Icon name="chevron-right" size={13} />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    ) : undefined;

  return (
    <WorkQueue
      crumbs={[{ label: "指挥墙", onClick: onWall }, { label: "航线地图", onClick: onMap }, { label: `${lane.origin}→${lane.dest}` }]}
      title={`航线异常 · ${lane.origin}→${lane.dest}`}
      alertCount={lane.alertCount}
      spec={spec}
      onDrill={onDrill}
      activeKey={activeKey}
      emptyHint={<>该航线当前无未闭环异常{lane.shipments.length > 0 ? "——下方为该航线全部在途货件。" : "。"}</>}
      context={context}
    />
  );
}
