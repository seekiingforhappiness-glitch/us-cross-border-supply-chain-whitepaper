import { world } from "../data";
import ViewHead from "../components/ViewHead";
import type { Forwarder } from "../types";

const FAMILY_TONE: Record<string, string> = {
  delay: "amber", chain: "red", document: "violet", inspection: "cyan", fee: "green", warehouse: "cyan",
};

// 货代参数条：把原始值归一到 0-1 的"越长越好"
function normParam(f: Forwarder, key: string): { pct: number; label: string } {
  switch (key) {
    case "credibility": return { pct: f.credibility, label: f.credibility.toFixed(2) };
    case "quote_level": {
      // 0.9~1.1 映射；越低越省钱，条长表示"报价优势"= 1 - (level-0.9)/0.2 clamp
      const adv = Math.max(0, Math.min(1, 1 - (f.quote_level - 0.9) / 0.2));
      return { pct: adv, label: f.quote_level.toFixed(2) };
    }
    case "volumetric_tendency": return { pct: 1 - f.volumetric_tendency, label: f.volumetric_tendency.toFixed(2) };
    case "billing_error_rate": return { pct: 1 - f.billing_error_rate / 0.1, label: (f.billing_error_rate * 100).toFixed(0) + "%" };
    default: return { pct: 0, label: "" };
  }
}

export default function ViewWorld() {
  const d = world;
  const maxSeason = Math.max(...d.season.map((s) => s.n), 1);
  const maxWeight = Math.max(...d.routes.map((r) => r.weight), 1);
  const spectrumMax = Math.max(...d.spectrum.families.map((f) => f.n), 1);
  const tiers = ["A", "B", "C"];

  return (
    <div>
      <ViewHead idx="08" question={d.question} subtitle={d.subtitle} />

      {/* 公司画像 */}
      <section className="panel wd-company">
        <div className="panel-head">
          <span className="panel-title">公司画像 · {String(d.company.name)}</span>
          <span className="tag cyan" style={{ fontSize: 10 }}>sim · 世界主体</span>
        </div>
        <div className="wd-company-body">
          {[
            { l: "年柜量目标", v: `${d.company.annual_container_target} 柜` },
            { l: "团队规模", v: `${d.company.team_size} 人` },
            { l: "起运枢纽", v: String(d.company.origin_hubs).replace("|", " · ") },
            { l: "欧线占比", v: `${Math.round(Number(d.company.eu_share) * 100)}%` },
            { l: "模拟窗口", v: `${d.company.window_start} → ${d.company.window_end}` },
            { l: "随机种子", v: String(d.company.seed) },
          ].map((x) => (
            <div className="wd-comp-item" key={x.l}>
              <span className="wd-comp-l mono">{x.l}</span>
              <span className="wd-comp-v">{x.v}</span>
            </div>
          ))}
        </div>
      </section>

      {/* 货代性格档案 */}
      <section className="wd-sec">
        <div className="wd-sec-head">6 家货代性格档案<span className="wd-sec-hint">靠谱度/报价/泡重/账单错误率——条越长越"对你有利"（真数据）</span></div>
        <div className="wd-fwd-grid">
          {d.forwarders.map((f) => (
            <article className="wd-fwd" key={f.forwarder_id}>
              <div className="wd-fwd-head">
                <span className="wd-fwd-name">{f.name}</span>
                <span className="wd-fwd-id mono">{f.forwarder_id} · 承运 {f.shipments}</span>
              </div>
              {d.forwarderParams.map((p) => {
                const n = normParam(f, p.key);
                return (
                  <div className="wd-param" key={p.key} title={p.plain}>
                    <span className="wd-param-l">{p.label}</span>
                    <div className="wd-param-bar-wrap">
                      <div className="wd-param-bar" style={{ width: `${Math.max(n.pct * 100, 3)}%` }} />
                    </div>
                    <span className="wd-param-v mono">{n.label}</span>
                  </div>
                );
              })}
            </article>
          ))}
        </div>
      </section>

      <div className="wd-two">
        {/* 客户分层 */}
        <section className="wd-sec">
          <div className="wd-sec-head">客户分层<span className="wd-sec-hint">{d.customers.total} 个客户 · A/B/C 三层 × 区域</span></div>
          <div className="wd-tiers">
            {tiers.map((t) => {
              const rows = d.customers.byTier.filter((c) => c.tier === t);
              const total = rows.reduce((s, r) => s + r.n, 0);
              return (
                <div className="wd-tier" key={t}>
                  <div className="wd-tier-badge">
                    <span className={`tag ${t === "A" ? "amber" : t === "B" ? "cyan" : ""}`} style={{ fontSize: 11 }}>{t} 级</span>
                    <span className="num wd-tier-n">{total}</span>
                  </div>
                  <div className="wd-tier-regions">
                    {rows.map((r) => (
                      <span className="wd-region mono" key={r.region}>{r.region} <b>{r.n}</b></span>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* 航线网络 */}
        <section className="wd-sec">
          <div className="wd-sec-head">航线网络<span className="wd-sec-hint">3 条主线 · 权重=走货占比</span></div>
          <div className="wd-routes">
            {d.routes.map((r) => (
              <div className="wd-route" key={r.route_id}>
                <div className="wd-route-head">
                  <span className="wd-route-id">{r.route_id}</span>
                  <span className="wd-route-lane mono">{r.origin_ports.replace("|", "/")} → {r.dest_ports.replace("|", "/")}</span>
                  <span className="wd-route-transit mono">{r.transit_min}-{r.transit_max}d</span>
                </div>
                <div className="wd-route-bar-wrap">
                  <div className="wd-route-bar" style={{ width: `${(r.weight / maxWeight) * 100}%` }} />
                  <span className="num wd-route-w">{Math.round(r.weight * 100)}%</span>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* 季节曲线 */}
      <section className="wd-sec">
        <div className="wd-sec-head">季节曲线<span className="wd-sec-hint">月度柜量（shipments.etd 真数据）· 旺季峰 vs 春节谷</span></div>
        <div className="wd-season">
          {d.season.map((s) => {
            const isTrough = s.n === Math.min(...d.season.map((x) => x.n));
            const isPeak = s.n === maxSeason;
            return (
              <div className="wd-season-col" key={s.month} title={`${s.month}: ${s.n} 柜`}>
                <span className="wd-season-n num">{s.n}</span>
                <div className={`wd-season-bar${isPeak ? " peak" : ""}${isTrough ? " trough" : ""}`}
                  style={{ height: `${(s.n / maxSeason) * 100}%` }} />
                <span className="wd-season-m mono">{s.month.slice(2)}</span>
                {isPeak && <span className="wd-season-tag peak">旺季峰</span>}
                {isTrough && <span className="wd-season-tag trough">春节谷</span>}
              </div>
            );
          })}
        </div>
      </section>

      {/* 异常谱系 */}
      <section className="wd-sec">
        <div className="wd-sec-head">异常谱系<span className="wd-sec-hint">{d.spectrum.total} 起异常 · 五族 + 连锁（sim_event_log）</span></div>
        <div className="wd-spectrum">
          {d.spectrum.families.map((f) => (
            <div className="wd-spec-row" key={f.family}>
              <span className="wd-spec-name">{f.plain}</span>
              <div className="wd-spec-bar-wrap">
                <div className={`wd-spec-bar tone-${FAMILY_TONE[f.family] ?? "cyan"}`}
                  style={{ width: `${(f.n / spectrumMax) * 100}%` }} />
              </div>
              <span className="wd-spec-n num">{f.n}</span>
              <span className="wd-spec-pct mono">{Math.round((f.n / d.spectrum.total) * 100)}%</span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
