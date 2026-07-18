import { useEffect, useState } from "react";
import {
  fetchObject,
  isMasked,
  linksForType,
  MASK_TEXT,
  traverse,
  type ObjectFields,
  type ObjectRef,
  type OntologyLink,
  type Role,
  type UsableLink,
} from "../api";
import Icon from "../components/Icons";
import AdmissionDecisionBar from "./AdmissionDecisionBar";
import { DecisionButtons } from "./ImpactPanel";
import {
  fieldGroup,
  fieldLabel,
  FIELD_GROUP_ORDER,
  humanizeDqWarning,
  humanizeFieldValue,
  linkPhrase,
  OBJECT_TYPE_CN,
  OBJECT_TYPE_DESC,
  type DqWarning,
  type FieldGroupName,
} from "./objectLabels";

// 第二层对象卡（右侧抽屉）：点全景实体节点 / AI 卡片 ref / 邻居 id 打开。
// GET /objects/{Type}/{id} 全字段分组呈现 + 该对象 links 列表；点 link 调 traverse 显示邻居 id，
// 邻居可继续点开卡——这就是第三层"关系走廊"的雏形（基本版，不求全）。
//
// V12（Daniel 贴 Supplier 对象卡截图"这里不容易懂"）：字段名/枚举值/关系词条全部经 objectLabels.ts
// 人话化；_validation_warnings 不再作为字段行直出 JSON，改为顶部可折叠"数据质量提示"徽标。
// 语义红线：本文件只改变呈现，不改变 fetchObject 拿到的数据本身——humanizeFieldValue 返回 null 时
// 原样展示，未映射字段/枚举值不编造。
//
// U5（35 类白话铺满）：① 抬头新增一句白话"这是什么"（OBJECT_TYPE_DESC，35 类全覆盖）；
// ② 字段区按 标识/状态/时间/金额/业务 五组分块渲染（fieldGroup()，35 类全覆盖，未登记类型/字段
// 优雅降级到"业务"兜底组，不丢字段）；③ 脱敏字段沿用既有 renderVal()/isMasked() 路径原样显示
// "无权查看"（非空白）——该行为在本次改动前就已生效，这里未新增逻辑，只是分组不影响它。

interface Props {
  target: ObjectRef;
  role: Role;
  links: OntologyLink[];
  onOpenObject: (r: ObjectRef) => void;
  onClose: () => void;
  onActed?: () => void; // B-1：准入案决策成功后通知父层刷新体征（语义同 ImpactPanel::onActed）
  onSwitchRole?: (r: Role) => void; // 欠账修复：任务审批灰态里的内联「切到老板角色」（同 ImpactPanel）
}

type Expanded = { state: "loading" } | { state: "done"; ids: string[] } | { state: "error" };

const DQ_WARNINGS_KEY = "_validation_warnings";

function renderVal(type: string, field: string, v: unknown): { text: string; cls: string; masked?: boolean } {
  if (v === null || v === undefined) return { text: "—", cls: "null" };
  if (isMasked(v)) return { text: MASK_TEXT, cls: "masked", masked: true };
  const humanized = humanizeFieldValue(type, field, v);
  if (humanized !== null) return { text: humanized, cls: "" };
  if (typeof v === "object") return { text: JSON.stringify(v), cls: "" };
  return { text: String(v), cls: "" };
}

export default function ObjectCard({ target, role, links, onOpenObject, onClose, onActed, onSwitchRole }: Props) {
  const [fields, setFields] = useState<ObjectFields | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, Expanded>>({});
  const [dqOpen, setDqOpen] = useState(false); // 数据质量提示徽标：默认折叠

  useEffect(() => {
    let cancelled = false;
    setFields(null);
    setErr(null);
    setExpanded({});
    setDqOpen(false);
    fetchObject(target.type, target.id, role)
      .then((f) => !cancelled && setFields(f))
      .catch((e: Error) => !cancelled && setErr(e.message));
    return () => {
      cancelled = true;
    };
  }, [target, role]);

  // B-1：准入决策提交成功后只重取本对象字段本身（案件 status/decision 等会变）——不重置关系
  // 展开态/DQ 折叠态，避免刚展开的关系面板被决策动作意外收起（与上面 target/role 触发的整体
  // 重置效果区分开：那是"换了一个对象"，这是"同一个对象的字段变了"）。
  const refetchFields = () => {
    fetchObject(target.type, target.id, role)
      .then((f) => setFields(f))
      .catch((e: Error) => setErr(e.message));
  };

  // Esc 关闭
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const usable: UsableLink[] = linksForType(links, target.type);

  // _validation_warnings 不是业务字段——从字段列表里摘出来单独走徽标；其余字段照常渲染。
  const rawWarnings = fields?.[DQ_WARNINGS_KEY];
  const warnings: DqWarning[] = Array.isArray(rawWarnings) ? (rawWarnings as DqWarning[]) : [];
  const fieldEntries = fields ? Object.entries(fields).filter(([k]) => k !== DQ_WARNINGS_KEY) : [];

  // U5：按 标识/状态/时间/金额/业务 分组（顺序取 FIELD_GROUP_ORDER），组内保留 API 原始字段顺序。
  const groupedFields: Record<FieldGroupName, [string, unknown][]> = {
    "标识": [], "状态": [], "时间": [], "金额": [], "业务": [],
  };
  for (const entry of fieldEntries) {
    groupedFields[fieldGroup(target.type, entry[0])].push(entry);
  }

  function toggleLink(l: UsableLink) {
    const cur = expanded[l.linkType];
    if (cur) {
      setExpanded((p) => {
        const n = { ...p };
        delete n[l.linkType];
        return n;
      });
      return;
    }
    setExpanded((p) => ({ ...p, [l.linkType]: { state: "loading" } }));
    traverse(target.type, target.id, l.linkType, role)
      .then((r) => setExpanded((p) => ({ ...p, [l.linkType]: { state: "done", ids: r.neighbor_ids } })))
      .catch(() => setExpanded((p) => ({ ...p, [l.linkType]: { state: "error" } })));
  }

  return (
    <>
      <div className="cp-drawer-scrim" onClick={onClose} />
      <aside className="cp-drawer" role="dialog" aria-label={`${OBJECT_TYPE_CN[target.type] ?? target.type} ${target.id}`}>
        <div className="cp-drawer__head" style={{ alignItems: "flex-start" }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="cp-drawer__type" title={target.type}>
              {OBJECT_TYPE_CN[target.type] ?? target.type}
            </div>
            <div className="cp-drawer__id num">{target.id}</div>
            {OBJECT_TYPE_DESC[target.type] && (
              <div style={{ fontSize: "11px", color: "var(--ink-2)", marginTop: "4px", lineHeight: 1.4 }}>
                {OBJECT_TYPE_DESC[target.type]}
              </div>
            )}
          </div>
          <button className="cp-drawer__close" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </div>
        <div className="cp-drawer__body">
          {err ? (
            <div className="cp-missing">
              <b>无法加载对象</b> · {err}
            </div>
          ) : !fields ? (
            <div className="cp-inline-load">加载中…</div>
          ) : (
            <>
              {warnings.length > 0 && (
                <div className="cp-dq">
                  <button
                    className={`cp-dq__toggle ${dqOpen ? "is-open" : ""}`}
                    onClick={() => setDqOpen((v) => !v)}
                    aria-expanded={dqOpen}
                  >
                    <Icon name="warn" size={12} />
                    数据质量提示 {warnings.length}
                    <Icon name="chevron-right" size={12} className="cp-dq__chev" />
                  </button>
                  {dqOpen && (
                    <ul className="cp-dq__list">
                      {warnings.map((w, i) => (
                        <li key={`${w.field}-${i}`}>{humanizeDqWarning(target.type, w, fields[w.field])}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              <div className="cp-drawer__section-title">字段（{fieldEntries.length}）</div>
              {FIELD_GROUP_ORDER.filter((g) => groupedFields[g].length > 0).map((g) => (
                <div key={g}>
                  <div
                    className="cp-field-group__title"
                    style={{
                      fontSize: "9.5px", letterSpacing: "0.08em", textTransform: "uppercase",
                      color: "var(--ink-3)", fontWeight: 600, margin: "10px 0 4px 2px",
                    }}
                  >
                    {g}（{groupedFields[g].length}）
                  </div>
                  <div className="cp-fields">
                    {groupedFields[g].map(([k, v]) => {
                      const r = renderVal(target.type, k, v);
                      return (
                        <div className="cp-field" key={k}>
                          <span className="cp-field__k" title={k}>
                            {fieldLabel(target.type, k)}
                          </span>
                          <span className={`cp-field__v ${r.cls}`}>
                            {r.masked && <Icon name="lock" size={11} />}
                            {r.text}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}

              <div className="cp-drawer__section-title">关系（{usable.length} 条可走）</div>
              <div className="cp-links">
                {usable.map((l) => {
                  const exp = expanded[l.linkType];
                  const arrow = l.direction === "forward" ? "→" : "←";
                  return (
                    <div key={l.linkType}>
                      <button className="cp-link-btn" onClick={() => toggleLink(l)} title={l.linkType}>
                        <span className="cp-link-btn__type">
                          {arrow} {OBJECT_TYPE_CN[l.neighborType] ?? l.neighborType}
                        </span>
                        <span className="cp-link-btn__dir">
                          （{linkPhrase(l.linkType, l.direction)}）
                          {exp?.state === "done" ? ` · ${exp.ids.length}` : ""}
                        </span>
                      </button>
                      {exp?.state === "loading" && <div className="cp-inline-load">遍历中…</div>}
                      {exp?.state === "error" && (
                        <div className="cp-inline-load" style={{ color: "var(--sev-amber)" }}>
                          该关系不可遍历（declared_only 或端点不符）
                        </div>
                      )}
                      {exp?.state === "done" &&
                        (exp.ids.length === 0 ? (
                          <div className="cp-inline-load">无邻居</div>
                        ) : (
                          <div className="cp-neighbors">
                            {exp.ids.slice(0, 40).map((id) => (
                              <span
                                key={id}
                                className="cp-neighbor"
                                onClick={() => onOpenObject({ type: l.neighborType, id })}
                                title={`打开${OBJECT_TYPE_CN[l.neighborType] ?? l.neighborType} ${id}`}
                              >
                                {id}
                              </span>
                            ))}
                            {exp.ids.length > 40 && (
                              <span className="cp-neighbor is-plain">+{exp.ids.length - 40} 更多</span>
                            )}
                          </div>
                        ))}
                    </div>
                  );
                })}
              </div>

              {/* B-1（V18）：准入案对象卡动作区——批准报价 / 驳回或要补件，仅 AdmissionCase 类型渲染，
                  其余 34 类对象卡不受影响。组件内部按案件状态/角色自行判断是否有可拍板动作。 */}
              {target.type === "AdmissionCase" && (
                <AdmissionDecisionBar
                  caseId={target.id}
                  caseStatus={String(fields.status ?? "")}
                  role={role}
                  onDecided={() => {
                    refetchFields();
                    onActed?.();
                  }}
                />
              )}

              {/* P0·审批闭环：任务对象卡承接审批——当对象是「待审批」的处置任务时，卡内直接给批准/驳回
                  （复用 ImpactPanel 同一套 DecisionButtons：manager 门控灰态白话 / X-Actor / Idempotency-Key /
                  postDecision 人类决策通道，零新写路）。这让"AI 任务时间线→打开处置任务"不再是审批死胡同，
                  队列被截断/从别处进来的任务也能就地拍板。成功后 refetchFields（approval_status 变→本区
                  自动收起）+ onActed 刷新体征。DecisionButtons 只用 decision.taskId，proposedAction 仅作
                  上下文透传（此处金额无需，置 null）。 */}
              {target.type === "Task" && String(fields.approval_status) === "pending" && (
                <div className="cp-action">
                  <div className="cp-action__t">
                    <Icon name="stamp" size={13} /> 动作区
                  </div>
                  <DecisionButtons
                    decision={{
                      taskId: target.id,
                      proposedAction: fields.proposed_action != null ? String(fields.proposed_action) : null,
                      amountUsd: null,
                    }}
                    role={role}
                    onActed={() => {
                      refetchFields();
                      onActed?.();
                    }}
                    onSwitchRole={onSwitchRole}
                  />
                  <div className="cp-action__note">
                    批准 / 驳回在此直接拍板（人类决策通道，实时回写并留痕）——与待拍板队列、影响面板走的是同一条通道。
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  );
}
