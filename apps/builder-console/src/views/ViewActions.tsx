import { Fragment, useState } from "react";
import { actions } from "../data";
import ViewHead from "../components/ViewHead";
import type { ActionRow, ToolInputSchema } from "../types";

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
    </div>
  );
}

/* JSON Schema 语法高亮块（轻量·纯 CSS 着色，不引库） */
function SchemaBlock({ schema }: { schema: ToolInputSchema }) {
  const json = JSON.stringify(schema, null, 2);
  return (
    <pre className="ac-schema mono">
      {json.split("\n").map((line, i) => {
        // 高亮 "key":  和 枚举/类型值
        const m = line.match(/^(\s*)"([^"]+)"(\s*:\s*)(.*)$/);
        if (m) {
          return (
            <div key={i} className="ac-schema-line">
              <span>{m[1]}</span>
              <span className="ac-sk-key">"{m[2]}"</span>
              <span>{m[3]}</span>
              <span className="ac-sk-val">{m[4]}</span>
            </div>
          );
        }
        return <div key={i} className="ac-schema-line">{line}</div>;
      })}
    </pre>
  );
}

/* 「作为 AI 工具长什么样」联动面板（板块③ · Stripe 概念↔代码联动） */
function ToolPanel({ a }: { a: ActionRow }) {
  const tool = a.tool;
  if (a.frozen || tool.aiExecutable === "frozen") {
    return (
      <div className="ac-tool ac-tool--frozen">
        <div className="ac-tool-head">
          <span className="tag red">永不暴露给 AI · frozen</span>
        </div>
        <p className="ac-tool-forbidden">
          🔴 <b>FORBIDDEN</b>——此动作的工具函数<b>从未注册</b>给 AI（agent.tools.FORBIDDEN_TOOLS）。
          不是权限不够，是根本不存在：审批 / 关闭 / 合规裁决属冻结区，AI 提案后交人拍板（maker-checker）。
        </p>
        <div className="ac-tool-line"><span className="ac-tool-l">ai_executable</span><span className="mono">{tool.aiExecutable}</span></div>
        <div className="ac-tool-line"><span className="ac-tool-l">enforcement</span><span className="mono">{tool.enforcement || "—"}</span></div>
      </div>
    );
  }
  if (!tool.exposedAsTool) {
    return (
      <div className="ac-tool ac-tool--internal">
        <div className="ac-tool-head"><span className="tag" style={{ fontSize: 10 }}>不暴露 · {tool.aiExecutable}</span></div>
        <p className="ac-tool-forbidden muted">
          未暴露为 AI 工具——{tool.aiExecutablePlain}。引擎/人内部动作，不进 TOOL_DEFS。
        </p>
        <div className="ac-tool-line"><span className="ac-tool-l">enforcement</span><span className="mono">{tool.enforcement || "—"}</span></div>
      </div>
    );
  }
  return (
    <div className="ac-tool ac-tool--exposed">
      <div className="ac-tool-head">
        <span className="tag green">exposed_as_tool</span>
        <span className="tag cyan" style={{ fontSize: 10 }}>{tool.aiExecutable}</span>
      </div>
      <div className="ac-tool-line"><span className="ac-tool-l">工具名</span><span className="ac-tool-name mono">{tool.toolName}</span></div>
      <div className="ac-tool-line"><span className="ac-tool-l">enforcement</span><span className="mono">{tool.enforcement || "—"}</span></div>
      {tool.toolDescription && <p className="ac-tool-desc">{tool.toolDescription}</p>}
      <div className="ac-tool-schema-label mono">tool_input_schema</div>
      {tool.toolInputSchema
        ? <SchemaBlock schema={tool.toolInputSchema} />
        : <p className="muted" style={{ fontSize: 12 }}>（本体未声明 input_schema）</p>}
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
        <span className="ac-leg-hint mono">点行看五要素 + 「作为 AI 工具」JSON Schema · 冻结区红条</span>
      </div>

      <div className="ac-toolsum">
        <span className="ac-toolsum-seg"><span className="tag green" style={{ fontSize: 10 }}>exposed</span> {actions.toolSummary.exposed} 个写提案工具</span>
        <span className="ac-toolsum-seg"><span className="tag red" style={{ fontSize: 10 }}>frozen</span> {actions.toolSummary.frozen} 个冻结区永不暴露</span>
        <span className="ac-toolsum-note muted">{actions.toolSummary.note}</span>
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
                      <div className="ac-detail-split">
                        <div className="ac-detail-left">
                          <div className="ac-detail-cap mono">五要素（本体动作定义）</div>
                          <FiveElements a={a} />
                        </div>
                        <div className="ac-detail-right">
                          <div className="ac-detail-cap mono">作为 AI 工具长什么样</div>
                          <ToolPanel a={a} />
                        </div>
                      </div>
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
