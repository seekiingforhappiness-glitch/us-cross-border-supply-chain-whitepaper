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

// 第二层对象卡（右侧抽屉）：点全景实体节点 / AI 卡片 ref / 邻居 id 打开。
// GET /objects/{Type}/{id} 全字段分组呈现 + 该对象 links 列表；点 link 调 traverse 显示邻居 id，
// 邻居可继续点开卡——这就是第三层"关系走廊"的雏形（基本版，不求全）。

interface Props {
  target: ObjectRef;
  role: Role;
  links: OntologyLink[];
  onOpenObject: (r: ObjectRef) => void;
  onClose: () => void;
}

type Expanded = { state: "loading" } | { state: "done"; ids: string[] } | { state: "error" };

function renderVal(v: unknown): { text: string; cls: string; masked?: boolean } {
  if (v === null || v === undefined) return { text: "null", cls: "null" };
  if (isMasked(v)) return { text: MASK_TEXT, cls: "masked", masked: true };
  if (typeof v === "boolean") return { text: v ? "true" : "false", cls: "" };
  if (typeof v === "object") return { text: JSON.stringify(v), cls: "" };
  return { text: String(v), cls: "" };
}

export default function ObjectCard({ target, role, links, onOpenObject, onClose }: Props) {
  const [fields, setFields] = useState<ObjectFields | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, Expanded>>({});

  useEffect(() => {
    let cancelled = false;
    setFields(null);
    setErr(null);
    setExpanded({});
    fetchObject(target.type, target.id, role)
      .then((f) => !cancelled && setFields(f))
      .catch((e: Error) => !cancelled && setErr(e.message));
    return () => {
      cancelled = true;
    };
  }, [target, role]);

  // Esc 关闭
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const usable: UsableLink[] = linksForType(links, target.type);

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
      <aside className="cp-drawer" role="dialog" aria-label={`${target.type} ${target.id}`}>
        <div className="cp-drawer__head">
          <div>
            <div className="cp-drawer__type">{target.type}</div>
            <div className="cp-drawer__id num">{target.id}</div>
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
              <div className="cp-drawer__section-title">字段（{Object.keys(fields).length}）</div>
              <div className="cp-fields">
                {Object.entries(fields).map(([k, v]) => {
                  const r = renderVal(v);
                  return (
                    <div className="cp-field" key={k}>
                      <span className="cp-field__k">{k}</span>
                      <span className={`cp-field__v ${r.cls}`}>
                        {r.masked && <Icon name="lock" size={11} />}
                        {r.text}
                      </span>
                    </div>
                  );
                })}
              </div>

              <div className="cp-drawer__section-title">关系（{usable.length} 条可走）</div>
              <div className="cp-links">
                {usable.map((l) => {
                  const exp = expanded[l.linkType];
                  return (
                    <div key={l.linkType}>
                      <button className="cp-link-btn" onClick={() => toggleLink(l)}>
                        <span className="cp-link-btn__type">{l.linkType}</span>
                        <span style={{ color: "var(--ink-2)" }}>→ {l.neighborType}</span>
                        <span className="cp-link-btn__dir">
                          {l.direction === "forward" ? "正向" : "反向"}
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
                                title={`打开 ${l.neighborType} ${id}`}
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
            </>
          )}
        </div>
      </aside>
    </>
  );
}
