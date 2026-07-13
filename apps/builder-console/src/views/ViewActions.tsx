import { Fragment, useState } from "react";
import { actions } from "../data";
import ViewHead from "../components/ViewHead";
import type { ActionRow } from "../types";

const TIER_TONE: Record<string, string> = { machine: "cyan", human: "amber", frozen: "red" };

function FiveElements({ a }: { a: ActionRow }) {
  return (
    <div className="ac-five">
      <div className="ac-five-row"><span className="ac-five-l">签名</span><span className="ac-five-v mono">{a.signature || "—"}</span></div>
      <div className="ac-five-row"><span className="ac-five-l">作用对象</span><span className="ac-five-v">{a.targetPlain} <span className="mono muted">{a.target}</span></span></div>
      <div className="ac-five-row"><span className="ac-five-l">执行角色</span><span className="ac-five-v">{a.executorsPlain.join(" · ") || "—"}{a.systemCan && <span className="tag cyan" style={{ fontSize: 9.5, marginLeft: 6 }}>系统可自动</span>}</span></div>
      <div className="ac-five-row"><span className="ac-five-l">前置校验</span><span className="ac-five-v">{a.preconditions.join("；") || "—"}</span></div>
      <div className="ac-five-row"><span className="ac-five-l">成功效果</span><span className="ac-five-v">{a.successEffects.join("；") || "—"}</span></div>
      <div className="ac-five-row"><span className="ac-five-l">失败处理</span><span className="ac-five-v">{a.failureHandling.join("；") || "—"}</span></div>
      <div className="ac-five-row"><span className="ac-five-l">审计留痕</span><span className="ac-five-v mono">{a.audit.join(" · ") || "—"}</span></div>
      {a.frozen && <div className="wv-frozen-note">🔴 冻结区：FORBIDDEN_TOOLS，从未注册给 AI。</div>}
    </div>
  );
}

export default function ViewActions() {
  const [open, setOpen] = useState<string | null>(null);
  const [roleFilter, setRoleFilter] = useState<string | null>(null);
  const roles = actions.roles;

  const shown = roleFilter
    ? actions.actions.filter((a) => a.perms[roleFilter])
    : actions.actions;

  return (
    <div>
      <ViewHead idx="05" question={actions.question} subtitle={actions.subtitle} />

      <div className="ac-legend">
        {actions.tiers.map((t) => (
          <span className="ac-leg" key={t.key}>
            <span className={`ac-leg-dot tone-${t.tone}`} />{t.mark} {t.name}
          </span>
        ))}
        <span className="ac-leg-hint mono">点行看五要素 · 冻结区红条</span>
      </div>

      <div className="ac-rolefilter">
        <span className="mono ac-rf-label">按角色筛：</span>
        <button className={`ac-rf${roleFilter === null ? " active" : ""}`} onClick={() => setRoleFilter(null)}>全部</button>
        {roles.map((r) => (
          <button key={r.id} className={`ac-rf${roleFilter === r.id ? " active" : ""}`} onClick={() => setRoleFilter(r.id)}>{r.plain}</button>
        ))}
      </div>

      {/* 权限热力表 */}
      <div className="ac-matrix-wrap">
        <table className="ac-matrix">
          <thead>
            <tr>
              <th className="ac-mh-act">动作</th>
              <th className="ac-mh-sys mono">system</th>
              {roles.map((r) => (
                <th key={r.id} className={`ac-mh-role${roleFilter === r.id ? " hl" : ""}`}>{r.plain}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((a) => (
              <Fragment key={a.id}>
                <tr
                  className={`ac-row${a.frozen ? " frozen" : ""}${open === a.id ? " open" : ""}`}
                  onClick={() => setOpen(open === a.id ? null : a.id)}
                >
                  <td className="ac-cell-act">
                    <span className={`tag ${TIER_TONE[a.tier]}`} style={{ fontSize: 9.5 }}>{a.id}</span>
                    <span className="ac-act-name">{a.name}</span>
                    <span className="ac-act-mark">{a.tierMark}</span>
                  </td>
                  <td className="ac-cell-sys">{a.systemCan ? <span className="ac-dot sys" /> : ""}</td>
                  {roles.map((r) => (
                    <td key={r.id} className={roleFilter === r.id ? "hl" : ""}>
                      {a.perms[r.id] ? <span className={`ac-dot${a.frozen ? " frozen" : ""}`} /> : ""}
                    </td>
                  ))}
                </tr>
                {open === a.id && (
                  <tr className="ac-detail-row">
                    <td colSpan={roles.length + 2}>
                      <p className="ac-detail-plain">{a.plain}</p>
                      <FiveElements a={a} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      <div className="honest-banner" style={{ marginTop: 22 }}>
        <span className="honest-icon">🔴</span>
        <div>
          <div className="honest-label mono">冻结区</div>
          <p className="honest-text">{actions.frozenNote}</p>
        </div>
      </div>
    </div>
  );
}
