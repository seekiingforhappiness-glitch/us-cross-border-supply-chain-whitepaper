import { useEffect, useMemo, useState } from "react";
import { weave, impact } from "../data";
import { useNav } from "../components/Nav";
import ViewHead from "../components/ViewHead";
import type { StateMachine, WeaveAction, ImpactType } from "../types";

const TIER_TONE: Record<string, string> = { machine: "cyan", human: "amber", frozen: "red" };
const TIER_LABEL: Record<string, string> = { machine: "🔵 机器/AI 可自动", human: "🟠 人的判断", frozen: "🔴 冻结区" };

/* 折叠板块（v3 板块④：依赖 / 使用情况——评估改动半径，Ontology Manager 7 板块补齐） */
function Fold({ title, hint, count, defaultOpen, children }: {
  title: string; hint: string; count: number; defaultOpen?: boolean; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(!!defaultOpen);
  return (
    <div className={`wv-fold${open ? " open" : ""}`}>
      <button className="wv-fold-head" onClick={() => setOpen((o) => !o)}>
        <span className="wv-fold-title">{title}</span>
        <span className="wv-fold-hint">{hint}</span>
        <span className="wv-fold-count num">{count}</span>
        <span className="wv-fold-caret">{open ? "−" : "+"}</span>
      </button>
      {open && <div className="wv-fold-body">{children}</div>}
    </div>
  );
}

/* 依赖板块：出边/入边承载列（本类型靠哪些列连出去 / 谁靠哪些列连进来） */
function DependencyFold({ im, onJump }: { im: ImpactType; onJump: (t: string) => void }) {
  const out = im.links.filter((l) => l.dir === "out");
  const inc = im.links.filter((l) => l.dir === "in");
  const carriers = im.fields.filter((f) => f.carriesLink);
  return (
    <Fold title="依赖" hint="本类型的关系出边 / 入边 + 承载列——它靠什么连接，谁靠什么连它" count={im.links.length}>
      <div className="wv-rels">
        <div className="wv-rel-col">
          <div className="wv-rel-dir mono">→ 它依赖（出边 {out.length}）</div>
          {out.length ? out.map((l, i) => (
            <button className="wv-rel" key={i} onClick={() => onJump(l.other)}>
              <span className="wv-rel-plain">{l.plain}</span>
              <span className="wv-rel-target">{l.otherPlain}<span className="mono wv-rel-card">{l.cardinality}</span></span>
              {l.storage && <span className="wv-rel-storage mono">承载列 {l.storage.column}</span>}
            </button>
          )) : <span className="muted wv-rel-empty">无</span>}
        </div>
        <div className="wv-rel-col">
          <div className="wv-rel-dir mono">← 谁依赖它（入边 {inc.length}）</div>
          {inc.length ? inc.map((l, i) => (
            <button className="wv-rel in" key={i} onClick={() => onJump(l.other)}>
              <span className="wv-rel-plain">{l.plain}</span>
              <span className="wv-rel-target">{l.otherPlain}<span className="mono wv-rel-card">{l.cardinality}</span></span>
              {l.storage && <span className="wv-rel-storage mono">承载列 {l.storage.column}</span>}
            </button>
          )) : <span className="muted wv-rel-empty">无</span>}
        </div>
      </div>
      {carriers.length > 0 && (
        <div className="wv-carriers">
          <div className="wv-carriers-l mono">本类型的承载列（改这些列 = 改连接）</div>
          {carriers.map((f) => (
            <span className="wv-carrier mono" key={f.name}>{f.name} → {f.carriesLink}</span>
          ))}
        </div>
      )}
    </Fold>
  );
}

/* 使用情况板块：下游消费面摘要 + 实例计数（②的浓缩，点跳影响分析看全） */
function UsageFold({ im, onImpact }: { im: ImpactType; onImpact: () => void }) {
  const c = im.counts;
  const total = c.links + c.rules + c.actions + c.tools + c.sensitive;
  return (
    <Fold title="使用情况" hint="被谁消费——规则/动作/AI 工具/敏感约束摘要 + 实例计数" count={total}>
      <div className="wv-usage-metrics">
        <div className="wv-um"><span className="wv-um-n num">{im.covered ? im.count.toLocaleString() : "—"}</span><span className="wv-um-l">活世界实例</span></div>
        <div className="wv-um"><span className="wv-um-n num">{c.rules}</span><span className="wv-um-l">规则盯它</span></div>
        <div className="wv-um"><span className="wv-um-n num">{c.actions}</span><span className="wv-um-l">动作读写</span></div>
        <div className="wv-um"><span className="wv-um-n num">{c.tools}</span><span className="wv-um-l">AI 工具暴露</span></div>
        <div className="wv-um"><span className="wv-um-n num">{c.sensitive}</span><span className="wv-um-l">敏感约束</span></div>
      </div>
      <div className="wv-usage-lists">
        {im.queryTools.length + im.writeTools.length > 0 && (
          <div className="wv-usage-row">
            <span className="wv-usage-l mono">AI 工具</span>
            <span className="wv-usage-chips">
              {im.queryTools.map((q) => <span className="wv-usage-chip cyan mono" key={q.name}>{q.name}</span>)}
              {im.writeTools.map((w) => <span className="wv-usage-chip green mono" key={w.actionId}>{w.toolName}</span>)}
            </span>
          </div>
        )}
        {im.sensitive.length > 0 && (
          <div className="wv-usage-row">
            <span className="wv-usage-l mono">敏感字段</span>
            <span className="wv-usage-chips">
              {im.sensitive.map((s) => <span className="wv-usage-chip red mono" key={s.field}>{s.field} · {s.visibleTo.join("/")}</span>)}
            </span>
          </div>
        )}
      </div>
      <button className="wv-goto" onClick={onImpact} style={{ marginTop: 10 }}>影响分析看全牵连面 →</button>
    </Fold>
  );
}

/* 迷你状态机图（SVG）——状态横排，转移画箭头 */
function StateMachineSVG({ sm }: { sm: StateMachine }) {
  const states = sm.states;
  const n = states.length;
  const W = Math.max(560, n * 128);
  const H = 116;
  const padX = 64;
  const gap = n > 1 ? (W - padX * 2) / (n - 1) : 0;
  const xs = states.map((_, i) => padX + i * gap);
  const y = 52;
  const boxW = 92;
  const boxH = 30;
  const idxOf = (s: string) => states.indexOf(s);

  return (
    <svg className="sm-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
      <defs>
        <marker id="sm-arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0 0 L6 3 L0 6 z" fill="var(--cyan)" />
        </marker>
      </defs>
      {sm.transitions.map((t, i) => {
        const a = idxOf(t.from), b = idxOf(t.to);
        if (a < 0 || b < 0) return null;
        const x1 = xs[a] + (b > a ? boxW / 2 : -boxW / 2);
        const x2 = xs[b] + (b > a ? -boxW / 2 - 4 : boxW / 2 + 4);
        const back = b < a;
        const skip = Math.abs(b - a) > 1;
        const yy = y + (back ? boxH / 2 : skip ? -boxH / 2 : 0);
        const ctrlY = back ? yy + 34 : skip ? yy - 30 : yy;
        const midX = (x1 + x2) / 2;
        return (
          <g key={i}>
            <path
              d={`M ${x1} ${yy} Q ${midX} ${ctrlY} ${x2} ${yy}`}
              fill="none" stroke="var(--cyan-line)" strokeWidth="1.2" markerEnd="url(#sm-arrow)"
            />
            <text x={midX} y={ctrlY + (back ? 12 : -4)} className="sm-trig" textAnchor="middle">
              {t.trigger.replace(/^A\d+\(?/, "").replace(/\)$/, "").slice(0, 22)}
            </text>
          </g>
        );
      })}
      {states.map((s, i) => (
        <g key={s}>
          <rect x={xs[i] - boxW / 2} y={y - boxH / 2} width={boxW} height={boxH} rx="4"
            fill="var(--surface-2)" stroke={i === 0 ? "var(--green)" : i === n - 1 ? "var(--amber-line)" : "var(--line-2)"} strokeWidth="1.2" />
          <text x={xs[i]} y={y + 4} className="sm-state" textAnchor="middle">{s}</text>
        </g>
      ))}
    </svg>
  );
}

function ActionCard({ a }: { a: WeaveAction }) {
  const [open, setOpen] = useState(false);
  const tone = TIER_TONE[a.tier] ?? "cyan";
  return (
    <div className={`wv-action${a.frozen ? " frozen" : ""}`}>
      <button className="wv-action-head" onClick={() => setOpen((o) => !o)}>
        <span className={`tag ${tone}`} style={{ fontSize: 10 }}>{a.id}</span>
        <span className="wv-action-name">{a.name}</span>
        {!a.primary && <span className="wv-badge-2 mono">次要触碰</span>}
        <span className="wv-action-tier mono">{TIER_LABEL[a.tier]}</span>
        <span className="wv-action-caret">{open ? "−" : "+"}</span>
      </button>
      <p className="wv-action-plain">{a.plain}</p>
      {open && (
        <div className="wv-five">
          <div className="wv-five-row"><span className="wv-five-l">签名</span><span className="wv-five-v mono">{a.signature || "—"}</span></div>
          <div className="wv-five-row"><span className="wv-five-l">执行角色</span><span className="wv-five-v">{a.executors.join(" · ") || "—"}</span></div>
          <div className="wv-five-row"><span className="wv-five-l">前置校验</span><span className="wv-five-v">{a.preconditions.join("；") || "—"}</span></div>
          <div className="wv-five-row"><span className="wv-five-l">成功效果</span><span className="wv-five-v">{a.successEffects.join("；") || "—"}</span></div>
          <div className="wv-five-row"><span className="wv-five-l">失败处理</span><span className="wv-five-v">{a.failureHandling.join("；") || "—"}</span></div>
          <div className="wv-five-row"><span className="wv-five-l">审计留痕</span><span className="wv-five-v mono">{a.audit.join(" · ") || "—"}</span></div>
          {a.frozen && <div className="wv-frozen-note">🔴 冻结区：此动作从未注册为 AI 工具（FORBIDDEN_TOOLS）。不是权限不够，是根本不存在。</div>}
        </div>
      )}
    </div>
  );
}

export default function ViewWeave() {
  const { route, navigate } = useNav();
  const [selected, setSelected] = useState(weave.defaultType);

  useEffect(() => {
    if (route.view === "weave" && route.focus && weave.types[route.focus]) {
      setSelected(route.focus);
    }
  }, [route.view, route.focus]);

  const t = weave.types[selected];
  const im = impact.types[selected];
  const rels = useMemo(() => {
    const out = t.relationships.filter((r) => r.dir === "out");
    const inc = t.relationships.filter((r) => r.dir === "in");
    return { out, inc };
  }, [t]);

  return (
    <div>
      <ViewHead idx="03" question={weave.question} subtitle={weave.subtitle} />
      <div className="wv-hint mono">{weave.hint}</div>

      <div className="wv-layout">
        {/* 左：34 类型按域 */}
        <aside className="wv-rail">
          {weave.domains.map((d) => (
            <div className="wv-dom" key={d.id}>
              <div className="wv-dom-name">{d.name}</div>
              {d.types.map((ty) => (
                <button
                  key={ty.type}
                  className={`wv-type${ty.type === selected ? " active" : ""}${ty.covered ? "" : " uncovered"}`}
                  onClick={() => setSelected(ty.type)}
                >
                  <span className="wv-type-name">{ty.plainName}</span>
                  <span className="wv-type-meta mono">
                    {ty.covered ? ty.count.toLocaleString() : "—"}
                  </span>
                </button>
              ))}
            </div>
          ))}
        </aside>

        {/* 中：关联卡 */}
        <section className="wv-card">
          <header className="wv-card-head">
            <div>
              <div className="wv-card-titles">
                <h2 className="wv-card-title">{t.plainName}</h2>
                <span className="wv-card-type mono">{t.type}</span>
              </div>
              <p className="wv-card-why">{t.why || "—"}</p>
              <div className="wv-card-meta">
                {t.ownerRole && <span className="tag" style={{ fontSize: 10 }}>owner · {t.ownerRole}</span>}
                {t.covered ? (
                  <span className="tag cyan" style={{ fontSize: 10 }}>活世界 {t.count.toLocaleString()} 条 · sim</span>
                ) : (
                  <span className="tag amber" style={{ fontSize: 10 }}>活世界暂未覆盖此域</span>
                )}
                <span className="tag" style={{ fontSize: 10 }}>{t.fieldCount} 个字段</span>
              </div>
            </div>
            {t.covered && (
              <button className="wv-goto" onClick={() => navigate("entity", t.type)}>
                查看实例 →
              </button>
            )}
          </header>

          {/* 字段 */}
          <div className="wv-block">
            <div className="wv-block-head">字段清单<span className="wv-block-hint">带白话；「受限」= 权限门控字段</span></div>
            <div className="wv-fields">
              {t.fields.map((f) => (
                <div className="wv-field" key={f.name}>
                  <span className="wv-field-name mono">{f.name}</span>
                  <span className="wv-field-type mono">{f.type}{f.values ? `(${f.values.length})` : ""}</span>
                  <span className="wv-field-desc">
                    {f.desc || <span className="muted">—</span>}
                    {f.sensitive && <span className="wv-sens">受限 · 仅 {f.sensitive.join("/")} 可见</span>}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* 状态机 */}
          {t.stateMachine && (
            <div className="wv-block">
              <div className="wv-block-head">状态机<span className="wv-block-hint">它一生会经历哪些状态、怎么流转</span></div>
              <div className="wv-sm">
                <StateMachineSVG sm={t.stateMachine} />
                {t.stateMachine.notes && <p className="wv-sm-note muted">{t.stateMachine.notes}</p>}
              </div>
            </div>
          )}

          {/* 关系 */}
          <div className="wv-block">
            <div className="wv-block-head">它的关系<span className="wv-block-hint">点任意一条穿梭到对端类型</span></div>
            <div className="wv-rels">
              <div className="wv-rel-col">
                <div className="wv-rel-dir mono">→ 出边（本类型指向）</div>
                {rels.out.length ? rels.out.map((r, i) => (
                  <button className="wv-rel" key={i} onClick={() => setSelected(r.other)}>
                    <span className="wv-rel-plain">{r.plain}</span>
                    <span className="wv-rel-target">{r.otherPlain}<span className="mono wv-rel-card">{r.cardinality}</span></span>
                  </button>
                )) : <span className="muted wv-rel-empty">无</span>}
              </div>
              <div className="wv-rel-col">
                <div className="wv-rel-dir mono">← 入边（谁指向本类型）</div>
                {rels.inc.length ? rels.inc.map((r, i) => (
                  <button className="wv-rel in" key={i} onClick={() => setSelected(r.other)}>
                    <span className="wv-rel-plain">{r.plain}</span>
                    <span className="wv-rel-target">{r.otherPlain}<span className="mono wv-rel-card">{r.cardinality}</span></span>
                  </button>
                )) : <span className="muted wv-rel-empty">无</span>}
              </div>
            </div>
          </div>

          {/* 动作 */}
          <div className="wv-block">
            <div className="wv-block-head">能对它做的动作<span className="wv-block-hint">点开看五要素（前置/效果/失败/审计）</span></div>
            {t.actions.length ? (
              <div className="wv-actions">
                {t.actions.map((a) => <ActionCard key={a.id} a={a} />)}
              </div>
            ) : <p className="muted wv-rel-empty">没有直接作用于它的动作——它多为被读取/被牵连的对象（动作作用在它的关联对象上）。</p>}
          </div>

          {/* 规则 */}
          <div className="wv-block">
            <div className="wv-block-head">盯着它的风险规则<span className="wv-block-hint">点跳规则档案看战绩</span></div>
            {t.rules.length ? (
              <div className="wv-rules">
                {t.rules.map((r) => (
                  <button className="wv-rule" key={r.id} onClick={() => navigate("rules", r.id)}>
                    <span className="tag amber" style={{ fontSize: 10 }}>{r.id}</span>
                    <span className="wv-rule-watch">{r.watch}</span>
                    <span className="wv-rule-plain muted">{r.plain}</span>
                  </button>
                ))}
              </div>
            ) : <p className="muted wv-rel-empty">没有规则直接盯它。</p>}
          </div>

          {/* v3 板块④：依赖 + 使用情况两折叠板块（评估改动半径，Ontology Manager 7 板块补齐） */}
          {im && (
            <div className="wv-block wv-folds">
              <DependencyFold im={im} onJump={setSelected} />
              <UsageFold im={im} onImpact={() => navigate("impact", t.type)} />
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
