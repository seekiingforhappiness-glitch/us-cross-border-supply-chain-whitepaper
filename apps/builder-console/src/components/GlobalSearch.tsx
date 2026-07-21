import { useEffect, useMemo, useRef, useState } from "react";
import { weave, loadSearch } from "../data";
import { useNav } from "./Nav";
import type { SearchIndex, SearchItem } from "../types";

// 全局跨类型搜索（v3 板块① · Bloom search-first）：
// 顶部常驻搜索框——输入任意实例 id（SHP-SIM-00055 / RSK- / PAY-…）或对象类型名/中文名，
// 即时下拉命中，回车/点击跳转。类型匹配走已在主包的 weave（即时）；实例匹配走 search.json（懒加载）。

interface TypeHit { kind: "type"; type: string; plainName: string; domain: string; count: number; }
interface InstHit { kind: "inst"; item: SearchItem; plainName: string; }
type Hit = TypeHit | InstHit;

const MAX_RESULTS = 24;

export default function GlobalSearch() {
  const { navigate } = useNav();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [index, setIndex] = useState<SearchIndex | null>(null);
  const [loading, setLoading] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);

  // app 挂载即后台预取索引，使搜索"常驻即时"
  useEffect(() => {
    setLoading(true);
    loadSearch()
      .then((idx) => setIndex(idx))
      .catch(() => setIndex(null))
      .finally(() => setLoading(false));
  }, []);

  // 点击外部关闭
  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  const hits = useMemo<Hit[]>(() => {
    const query = q.trim().toLowerCase();
    if (!query) return [];
    const out: Hit[] = [];
    // ① 类型名/中文名匹配（即时）
    for (const [type, t] of Object.entries(weave.types)) {
      if (type.toLowerCase().includes(query) || t.plainName.toLowerCase().includes(query)) {
        out.push({ kind: "type", type, plainName: t.plainName, domain: t.domain, count: t.count });
      }
    }
    // ② 实例 id / 摘要匹配（索引就绪后）
    if (index) {
      for (const it of index.items) {
        if (out.length >= MAX_RESULTS) break;
        if (it.id.toLowerCase().includes(query) || it.s.toLowerCase().includes(query)) {
          out.push({ kind: "inst", item: it, plainName: index.types[it.t]?.plainName ?? it.t });
        }
      }
    }
    return out.slice(0, MAX_RESULTS);
  }, [q, index]);

  useEffect(() => { setActive(0); }, [q]);

  function go(h: Hit) {
    if (h.kind === "type") {
      navigate("weave", h.type);
    } else {
      navigate("entity", `${h.item.t}:${h.item.id}`);
    }
    setOpen(false);
    setQ("");
  }

  function onKey(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (e.key === "Enter" && hits[active]) { e.preventDefault(); go(hits[active]); }
    else if (e.key === "Escape") { setOpen(false); }
  }

  return (
    <div className="gs" ref={boxRef}>
      <span className="gs-ico" aria-hidden>
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none">
          <circle cx="7" cy="7" r="5" stroke="currentColor" strokeWidth="1.5" />
          <path d="M11 11L14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      </span>
      <input
        className="gs-input"
        placeholder="全局搜索：任意实例 id（SHP-SIM-00055 / RSK- / PAY-…）或对象类型名/中文名"
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKey}
        spellCheck={false}
      />
      {q && <button className="gs-clear" onClick={() => { setQ(""); setOpen(false); }} aria-label="清除">×</button>}
      <span className="gs-hint mono">{index ? `${index.total.toLocaleString()} 实例` : loading ? "索引载入中…" : ""}</span>

      {open && q.trim() && (
        <div className="gs-drop">
          {hits.length === 0 ? (
            <div className="gs-empty">
              {index ? "无命中——换个 id 或类型名试试。" : "实例索引载入中，先给出类型匹配…"}
            </div>
          ) : (
            hits.map((h, i) => (
              <button
                key={h.kind === "type" ? `t:${h.type}` : `i:${h.item.t}:${h.item.id}`}
                className={`gs-hit${i === active ? " active" : ""}`}
                onMouseEnter={() => setActive(i)}
                onClick={() => go(h)}
              >
                {h.kind === "type" ? (
                  <>
                    <span className="tag amber gs-hit-badge">类型</span>
                    <span className="gs-hit-main">{h.plainName} <span className="mono gs-hit-sub">{h.type}</span></span>
                    <span className="gs-hit-meta mono">{h.count >= 0 ? `${h.count.toLocaleString()} 条 →织网` : "→织网"}</span>
                  </>
                ) : (
                  <>
                    <span className="tag cyan gs-hit-badge">{h.plainName}</span>
                    <span className="gs-hit-main mono">{h.item.id}</span>
                    <span className="gs-hit-meta">{h.item.s || "→实例全景"}</span>
                  </>
                )}
              </button>
            ))
          )}
          <div className="gs-foot mono">↑↓ 选择 · ⏎ 跳转 · 类型→关联织网 · 实例→实体全景</div>
        </div>
      )}
    </div>
  );
}
