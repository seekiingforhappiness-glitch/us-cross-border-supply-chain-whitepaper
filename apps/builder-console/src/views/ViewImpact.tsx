import { useEffect, useMemo, useState } from "react";
import { impact } from "../data";
import { useNav } from "../components/Nav";
import ViewHead from "../components/ViewHead";
import type { ImpactType, ImpactField } from "../types";

const TIER_TONE: Record<string, string> = { machine: "cyan", human: "amber", frozen: "red" };

/* 五维消费面计数徽标 */
function CountBadges({ t }: { t: ImpactType }) {
  const c = t.counts;
  const items: { k: string; label: string; n: number; tone: string }[] = [
    { k: "links", label: "关系引用", n: c.links, tone: "cyan" },
    { k: "rules", label: "规则检测", n: c.rules, tone: "amber" },
    { k: "actions", label: "动作读写", n: c.actions, tone: "violet" },
    { k: "tools", label: "AI 工具暴露", n: c.tools, tone: "green" },
    { k: "sensitive", label: "敏感约束", n: c.sensitive, tone: "red" },
  ];
  return (
    <div className="im-badges">
      {items.map((it) => (
        <div className={`im-badge tone-${it.tone}${it.n === 0 ? " zero" : ""}`} key={it.k}>
          <span className="im-badge-n num">{it.n}</span>
          <span className="im-badge-l">{it.label}</span>
        </div>
      ))}
    </div>
  );
}

/* 单字段的下游消费面（点开展开） */
function FieldRow({ f, onToolJump }: { f: ImpactField; onToolJump: () => void }) {
  const [open, setOpen] = useState(false);
  const hasImpact = !!(f.sensitive || f.carriesLink || f.exposedByTools.length);
  return (
    <div className={`im-field${open ? " open" : ""}`}>
      <button className="im-field-head" onClick={() => setOpen((o) => !o)} disabled={!hasImpact}>
        <span className="im-field-name mono">{f.name}</span>
        <span className="im-field-type mono">{f.type}</span>
        <span className="im-field-tags">
          {f.sensitive && <span className="tag red" style={{ fontSize: 9 }}>敏感</span>}
          {f.carriesLink && <span className="tag cyan" style={{ fontSize: 9 }}>承载关系</span>}
          {f.exposedByTools.length > 0 && <span className="tag green" style={{ fontSize: 9 }}>AI 工具 ×{f.exposedByTools.length}</span>}
          {!hasImpact && <span className="muted im-field-clean">无下游牵连</span>}
        </span>
        {hasImpact && <span className="im-field-caret">{open ? "−" : "+"}</span>}
      </button>
      {open && hasImpact && (
        <div className="im-field-body">
          {f.desc && <p className="im-field-desc">{f.desc}</p>}
          {f.sensitive && (
            <div className="im-field-line"><span className="im-field-l">敏感门控</span>
              <span>受限 · 仅 {f.sensitive.join(" / ")} 可见（sensitiveFieldRules）</span></div>
          )}
          {f.carriesLink && (
            <div className="im-field-line"><span className="im-field-l">承载关系</span>
              <span className="mono">{f.carriesLink}</span>
              <span className="muted"> — 改此列即改这条关系的连接（storage 承载声明）</span></div>
          )}
          {f.exposedByTools.length > 0 && (
            <div className="im-field-line"><span className="im-field-l">AI 工具暴露</span>
              <span className="im-tool-chips">
                {f.exposedByTools.map((tn) => (
                  <button className="im-tool-chip mono" key={tn} onClick={onToolJump}>{tn}</button>
                ))}
              </span></div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ViewImpact() {
  const { route, navigate } = useNav();
  const [selected, setSelected] = useState(impact.defaultType);
  const [fieldsOpen, setFieldsOpen] = useState(false);

  useEffect(() => {
    if (route.view === "impact" && route.focus && impact.types[route.focus]) {
      setSelected(route.focus);
      setFieldsOpen(false);
    }
  }, [route.view, route.focus]);

  const t = impact.types[selected];
  const impactedFields = useMemo(
    () => t.fields.filter((f) => f.sensitive || f.carriesLink || f.exposedByTools.length),
    [t]
  );

  return (
    <div>
      <ViewHead idx="13" question={impact.question} subtitle={impact.subtitle} />
      <div className="wv-hint mono">{impact.hint}</div>

      <div className="wv-layout">
        {/* 左：35 类型按域，标注影响半径 */}
        <aside className="wv-rail">
          {impact.domains.map((d) => (
            <div className="wv-dom" key={d.id}>
              <div className="wv-dom-name">{d.name}</div>
              {d.types.map((ty) => (
                <button
                  key={ty.type}
                  className={`wv-type${ty.type === selected ? " active" : ""}`}
                  onClick={() => { setSelected(ty.type); setFieldsOpen(false); }}
                >
                  <span className="wv-type-name">{ty.plainName}</span>
                  <span className="wv-type-meta mono" title="下游消费面总数（关系+规则+动作+工具+敏感）">{ty.impact}</span>
                </button>
              ))}
            </div>
          ))}
        </aside>

        {/* 中：影响面板 */}
        <section className="wv-card">
          <header className="wv-card-head">
            <div>
              <div className="wv-card-titles">
                <h2 className="wv-card-title">{t.plainName}</h2>
                <span className="wv-card-type mono">{t.type}</span>
              </div>
              <p className="wv-card-why">改动 <b>{t.plainName}</b> 会波及以下下游——先看半径，再决定动不动。{t.why}</p>
            </div>
            <div className="im-actions-goto">
              <button className="wv-goto" onClick={() => navigate("weave", t.type)}>关联织网 →</button>
              {t.covered && <button className="wv-goto" onClick={() => navigate("entity", t.type)}>查看实例 →</button>}
            </div>
          </header>

          <CountBadges t={t} />

          {/* ① 被哪些关系引用 */}
          <div className="wv-block">
            <div className="wv-block-head">被哪些关系引用<span className="wv-block-hint">links source/target · 承载列改动 = 连接改动</span></div>
            {t.links.length ? (
              <div className="im-grid">
                {t.links.map((l, i) => (
                  <button className="im-cell" key={i} onClick={() => navigate("weave", l.other)}>
                    <span className={`im-cell-dir ${l.dir}`}>{l.dir === "out" ? "→ 指向" : "← 被指"}</span>
                    <span className="im-cell-main">{l.plain} · {l.otherPlain}</span>
                    <span className="im-cell-meta mono">{l.linkType} · {l.cardinality}</span>
                    {l.storage && <span className="im-cell-storage mono">承载列 {l.storage.table}.{l.storage.column}{l.storage.discriminator ? ` [${l.storage.discriminator}=${l.storage.discriminator_value}]` : ""}</span>}
                  </button>
                ))}
              </div>
            ) : <p className="muted im-empty">无关系引用它。</p>}
          </div>

          {/* ② 哪些规则盯着 */}
          <div className="wv-block">
            <div className="wv-block-head">哪些风险规则盯着它<span className="wv-block-hint">riskRules 检测逻辑的目标对象 · 点跳规则档案</span></div>
            {t.rules.length ? (
              <div className="im-chips">
                {t.rules.map((r) => (
                  <button className="im-rule" key={r.id} onClick={() => navigate("rules", r.id)}>
                    <span className="tag amber" style={{ fontSize: 10 }}>{r.id}</span>
                    <span className="im-rule-watch">{r.watch}</span>
                  </button>
                ))}
              </div>
            ) : <p className="muted im-empty">没有规则直接盯它。</p>}
          </div>

          {/* ③ 哪些动作读写 */}
          <div className="wv-block">
            <div className="wv-block-head">哪些动作读写它<span className="wv-block-hint">动作五要素的作用/触碰对象 · 点跳动作与权限</span></div>
            {t.actions.length ? (
              <div className="im-grid">
                {t.actions.map((a) => (
                  <button className={`im-cell${a.frozen ? " frozen" : ""}`} key={a.id} onClick={() => navigate("actions")}>
                    <span className={`tag ${TIER_TONE[a.tier]}`} style={{ fontSize: 9.5 }}>{a.id}</span>
                    <span className="im-cell-main">{a.name}</span>
                    <span className="im-cell-tags">
                      {a.primary ? <span className="im-tag-mini">主作用</span> : <span className="im-tag-mini muted">次要触碰</span>}
                      {a.exposedAsTool && <span className="tag green" style={{ fontSize: 9 }}>AI 工具 {a.toolName}</span>}
                      {a.frozen && <span className="tag red" style={{ fontSize: 9 }}>冻结区</span>}
                    </span>
                  </button>
                ))}
              </div>
            ) : <p className="muted im-empty">没有动作直接作用于它（多为被读取/被牵连对象）。</p>}
          </div>

          {/* ④ 哪些 AI 工具暴露 */}
          <div className="wv-block">
            <div className="wv-block-head">哪些 AI 工具暴露它<span className="wv-block-hint">aiQueryTools（只读查询）+ exposed 动作（写提案）· 点跳动作与权限</span></div>
            {t.queryTools.length || t.writeTools.length ? (
              <div className="im-tools">
                {t.queryTools.map((q) => (
                  <div className="im-tool" key={q.name}>
                    <span className="tag cyan" style={{ fontSize: 9.5 }}>只读查询</span>
                    <span className="im-tool-name mono">{q.name}</span>
                    <span className="im-tool-desc muted">{q.description}</span>
                  </div>
                ))}
                {t.writeTools.map((w) => (
                  <button className="im-tool write" key={w.actionId} onClick={() => navigate("actions")}>
                    <span className="tag green" style={{ fontSize: 9.5 }}>写提案</span>
                    <span className="im-tool-name mono">{w.toolName}</span>
                    <span className="im-tool-desc muted">{w.description || w.name}{w.primary ? "" : "（次要触碰）"}</span>
                  </button>
                ))}
              </div>
            ) : <p className="muted im-empty">没有 AI 工具暴露它——AI 读不到、也写不到这个对象。</p>}
          </div>

          {/* ⑤ 哪些敏感规则约束 */}
          <div className="wv-block">
            <div className="wv-block-head">哪些敏感规则约束它<span className="wv-block-hint">sensitiveFieldRules · 字段级权限门控</span></div>
            {t.sensitive.length ? (
              <div className="im-chips">
                {t.sensitive.map((s) => (
                  <div className="im-sens" key={s.field}>
                    <span className="im-sens-field mono">{s.field}</span>
                    <span className="im-sens-vis">受限 · 仅 {s.visibleTo.join(" / ")} 可见</span>
                  </div>
                ))}
              </div>
            ) : <p className="muted im-empty">没有敏感字段规则约束它（本对象字段对所有角色可见）。</p>}
          </div>

          {/* 字段级消费面（可展开） */}
          <div className="wv-block">
            <button className="im-fields-toggle" onClick={() => setFieldsOpen((o) => !o)}>
              <span className="wv-block-head" style={{ marginBottom: 0 }}>
                字段级消费面<span className="wv-block-hint">逐字段看：改它牵连哪些 AI 工具 / 敏感门控 / 承载关系</span>
              </span>
              <span className="im-fields-caret">
                {impactedFields.length} 个字段有下游牵连 {fieldsOpen ? "▲" : "▼"}
              </span>
            </button>
            {fieldsOpen && (
              <div className="im-fields">
                {t.fields.map((f) => (
                  <FieldRow key={f.name} f={f} onToolJump={() => navigate("actions")} />
                ))}
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
