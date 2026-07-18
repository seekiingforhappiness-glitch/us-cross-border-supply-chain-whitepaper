import { useEffect, useRef } from "react";
import { rules } from "../data";
import { useNav } from "../components/Nav";
import ViewHead from "../components/ViewHead";

const SEV_TONE: Record<string, string> = { critical: "red", high: "amber", medium: "cyan", low: "cyan" };
const SEV_ORDER = ["critical", "high", "medium", "low"];

export default function ViewRules() {
  const { route } = useNav();
  const refs = useRef<Record<string, HTMLElement | null>>({});

  useEffect(() => {
    if (route.view === "rules" && route.focus && refs.current[route.focus]) {
      refs.current[route.focus]?.scrollIntoView({ behavior: "smooth", block: "center" });
      refs.current[route.focus]?.classList.add("rl-flash");
      const el = refs.current[route.focus];
      const t = setTimeout(() => el?.classList.remove("rl-flash"), 1600);
      return () => clearTimeout(t);
    }
  }, [route.view, route.focus]);

  const maxLiving = Math.max(...rules.cards.map((c) => c.living.total), 1);

  return (
    <div>
      <ViewHead idx="04" question={rules.question} subtitle={rules.subtitle} />

      {/* 两世界口径 */}
      <div className="rl-two">
        <div className="rl-two-card living">
          <div className="rl-two-head"><span className="tag cyan" style={{ fontSize: 10 }}>sim</span>{rules.twoWorlds.living.label}</div>
          <p className="rl-two-plain">{rules.twoWorlds.living.plain}</p>
        </div>
        <div className="rl-two-card verify">
          <div className="rl-two-head"><span className="tag green" style={{ fontSize: 10 }}>验证</span>{rules.twoWorlds.verify.label} · <span className="num" style={{ color: "var(--green)" }}>{rules.verifyPrecision}</span></div>
          <p className="rl-two-plain">{rules.twoWorlds.verify.plain}</p>
        </div>
      </div>

      {/* P1 修复：R1"活世界触发93"vs 15页"25次detect"两数字对不上（陌生人建造者实测困惑）——
          根因：两处统计的是不同世界（本页=sim活世界14个月累计；15页=验证世界单批detect检出），页面此前零解释。
          静态口径提示，不读取任何数据字段，与下方两世界卡片/逐条数字互不影响。 */}
      <p className="source-note mono" style={{ marginBottom: 16 }}>
        口径对照：下方每条规则的「活世界触发」＝ sim 活世界连续运转以来的累计触发次数（如 R1）；
        「治理控制室 › ⑤规则台账」显示的是验证世界（ontology.sqlite）单批 detect 检出数——
        两个世界统计口径不同，数字不可直接比较，谁大谁小不说明规则准不准。
      </p>

      <div className="rl-grid">
        {rules.cards.map((c) => {
          const sevs = SEV_ORDER.filter((s) => c.living.bySeverity[s]);
          return (
            <article
              className={`rl-card${c.living.total > 0 ? "" : " dormant"}`}
              key={c.id}
              ref={(el) => { refs.current[c.id] = el; }}
            >
              <div className="rl-card-head">
                <span className="rl-id mono">{c.id}</span>
                <div className="rl-card-titles">
                  <span className="rl-watch">{c.watch}</span>
                  <span className="rl-target mono">盯 {c.targetPlain}</span>
                </div>
              </div>
              <p className="rl-plain">{c.plain}</p>
              <div className="rl-logic mono">{c.logic}</div>

              <div className="rl-stats">
                <div className="rl-stat">
                  <div className="rl-stat-l mono">活世界触发</div>
                  <div className="rl-bar-wrap">
                    <div className="rl-bar" style={{ width: `${Math.max((c.living.total / maxLiving) * 100, c.living.total > 0 ? 5 : 0)}%` }} />
                    <span className="rl-bar-n num">{c.living.total}</span>
                  </div>
                  {sevs.length > 0 ? (
                    <div className="rl-sev">
                      {sevs.map((s) => (
                        <span key={s} className={`tag ${SEV_TONE[s]}`} style={{ fontSize: 9.5 }}>
                          {s} {c.living.bySeverity[s]}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <span className="muted mono" style={{ fontSize: 10.5 }}>活世界未触发（该域实例待灌，规则已就绪）</span>
                  )}
                </div>
                <div className="rl-stat verify">
                  <div className="rl-stat-l mono">验证世界精度</div>
                  <div className="rl-pr num">R/P = 1.000</div>
                  <span className="muted mono" style={{ fontSize: 10 }}>engine.evaluate 口径</span>
                </div>
              </div>
            </article>
          );
        })}
      </div>
      <p className="source-note mono">活世界战绩来自 simworld.risk_events 实时聚合；验证世界 R/P=1.000 为 ontology + data/truth 引擎口径（见 STATUS）。两世界分开报，不混算。</p>
    </div>
  );
}
