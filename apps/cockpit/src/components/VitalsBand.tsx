import type { Zone, ZoneId } from "../api";
import { formatInt, formatPct, formatUsd, isMasked } from "../api";

// 公司体征带：七掌控区各一枚体征块横排。点任一枚 → 中央区切换为该区展开视图。
// "扫一眼知道公司今天怎么样"的答案（二稿一缺的掌控感主要由它补上）。
interface Props {
  zones: Zone[];
  activeZone: ZoneId | null;
  onSelect: (z: ZoneId) => void;
}

const ICON: Record<ZoneId, string> = {
  money: "💰",
  fulfillment: "📦",
  customers: "🧑‍💼",
  suppliers: "🏭",
  inventory: "📊",
  ai: "🤖",
  decisions: "✅",
};

// 短标签（体征带空间紧，用短名；详情视图里用 API 的完整 headline_label）
const SHORT: Record<ZoneId, string> = {
  money: "费用敞口",
  fulfillment: "准交率",
  customers: "客户敞口",
  suppliers: "供应商",
  inventory: "库存击穿",
  ai: "AI 今日",
  decisions: "待拍板",
};

// headline 是比率的区（0-1 → 百分比）；其余按计数/金额/字符串各自处理
const RATE_ZONES: ReadonlySet<ZoneId> = new Set<ZoneId>(["fulfillment", "suppliers"]);

function renderValue(z: Zone): { text: string; cls: string } {
  const v = z.headline_value;
  if (isMasked(v)) return { text: v, cls: "is-masked" };
  if (v === null || v === undefined) return { text: "无数据", cls: "is-missing" };
  if (z.headline_unit === "usd") return { text: formatUsd(v), cls: "" };
  if (typeof v === "string") return { text: v, cls: "" }; // AI 区"N 检 / M 提案"
  if (RATE_ZONES.has(z.zone)) return { text: formatPct(v), cls: "" };
  return { text: formatInt(v), cls: "" };
}

export default function VitalsBand({ zones, activeZone, onSelect }: Props) {
  return (
    <nav className="cp-vitals" aria-label="公司体征带">
      {zones.map((z) => {
        const val = renderValue(z);
        const t = z.trend;
        // 趋势箭头：仅准交率 up=好（绿）/down=差（红）语义明确；其余（如 AI 检测数）方向中性不评判
        const goodWhenUp = z.zone === "fulfillment";
        let trendCls = "cp-trend--none";
        let arrow = "—";
        if (t) {
          if (t.delta > 0) {
            arrow = "▲";
            trendCls = goodWhenUp ? "cp-trend--up" : "cp-trend--flat";
          } else if (t.delta < 0) {
            arrow = "▼";
            trendCls = goodWhenUp ? "cp-trend--down" : "cp-trend--flat";
          } else {
            arrow = "—";
            trendCls = "cp-trend--flat";
          }
        }
        return (
          <button
            key={z.zone}
            className={`cp-vital cp-vital--${z.zone} ${activeZone === z.zone ? "is-active" : ""}`}
            onClick={() => onSelect(z.zone)}
            aria-pressed={activeZone === z.zone}
            title={z.headline_reason || z.headline_label}
          >
            {z.alert_count > 0 && (
              <span className={`cp-vital__alert ${z.alert_count > 20 ? "" : "is-amber"}`}>
                {z.alert_count > 99 ? "99+" : z.alert_count}
              </span>
            )}
            <span className="cp-vital__top">
              <span className="cp-vital__icon" aria-hidden>
                {ICON[z.zone]}
              </span>
              <span className="cp-vital__label">{SHORT[z.zone]}</span>
            </span>
            <div className={`cp-vital__value num ${val.cls}`}>{val.text}</div>
            <div className="cp-vital__sub">
              {t ? (
                <span className={`cp-trend ${trendCls}`}>
                  {arrow} {Math.abs(t.delta) < 1 ? formatPct(Math.abs(t.delta)) : Math.abs(t.delta)}
                  <span style={{ color: "var(--ink-3)", marginLeft: 3 }}>· {t.window_days}d</span>
                </span>
              ) : (
                <span className="cp-trend cp-trend--none">—</span>
              )}
            </div>
          </button>
        );
      })}
    </nav>
  );
}
