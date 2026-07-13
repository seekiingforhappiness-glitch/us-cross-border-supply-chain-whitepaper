import { useEffect, useMemo, useState } from "react";
import { loadEntity } from "../data";
import { useNav } from "../components/Nav";
import ViewHead from "../components/ViewHead";
import type { EntityData, EntityInstance, TimelineEvent } from "../types";

const LANE_TONE: Record<string, string> = {
  event: "amber", milestone: "cyan", ai: "violet", memory: "green",
};
const SEV_TONE: Record<string, string> = { critical: "red", high: "amber", medium: "cyan" };

function TimelineRow({ e }: { e: TimelineEvent }) {
  const tone = LANE_TONE[e.lane] ?? "cyan";
  return (
    <div className={`et-tl-row lane-${e.lane}`}>
      <span className="et-tl-date mono">{e.date || "—"}</span>
      <span className={`et-tl-dot tone-${tone}`} />
      <div className="et-tl-body">
        <div className="et-tl-line">
          {e.family && e.family !== "milestone" && e.family !== "ai" && (
            <span className={`tag ${SEV_TONE[e.severity ?? ""] ?? "cyan"}`} style={{ fontSize: 9.5 }}>
              {e.family}{e.severity ? ` · ${e.severity}` : ""}
            </span>
          )}
          {e.actor && <span className="et-tl-actor mono">{e.actor}</span>}
          <span className="et-tl-plain">{e.plain}</span>
        </div>
        {e.causedBy && (
          <div className="et-tl-cause mono">↳ 连锁：由 {e.causedBy} 触发</div>
        )}
      </div>
    </div>
  );
}

function Panorama({
  data, type, id, onJump,
}: {
  data: EntityData; type: string; id: string;
  onJump: (t: string, i: string) => void;
}) {
  const tp = data.types[type];
  const inst: EntityInstance | undefined = tp?.instances[id];
  if (!inst) {
    const summary = tp?.index.find((r) => r.id === id);
    return (
      <div className="et-panorama">
        <div className="et-pan-head">
          <span className="tag amber">{type}</span>
          <span className="et-pan-id mono">{id}</span>
        </div>
        <p className="empty-badge" style={{ marginTop: 14 }}>
          此实例未导出全景详情（活世界为控体量，全景详情覆盖前若干条 + hero 邻居；索引仍全量）。
        </p>
        {summary && (
          <div className="et-fields" style={{ marginTop: 14 }}>
            {Object.entries(summary).map(([k, v]) => (
              <div className="et-field" key={k}>
                <span className="et-field-name mono">{k}</span>
                <span className="et-field-val">{String(v ?? "—")}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }
  const relsByDir = {
    out: inst.rels.filter((r) => r.dir === "out"),
    in: inst.rels.filter((r) => r.dir === "in"),
  };
  const grouped = (arr: typeof inst.rels) => {
    const m: Record<string, typeof inst.rels> = {};
    arr.forEach((r) => { (m[r.targetType] ||= []).push(r); });
    return m;
  };

  return (
    <div className="et-panorama">
      <div className="et-pan-head">
        <span className="tag cyan">{tp.plainName} · {type}</span>
        <span className="et-pan-id mono">{id}</span>
      </div>

      <div className="et-pan-grid">
        {/* 字段值 */}
        <section className="et-pan-block">
          <div className="et-block-head">全部字段值<span className="et-block-hint">「受限」= 权限门控字段（本页创始人视角全显）</span></div>
          <div className="et-fields">
            {inst.fields.map((f) => (
              <div className="et-field" key={f.name}>
                <span className="et-field-name mono">{f.name}</span>
                <span className="et-field-val">
                  {f.value === null || f.value === "" ? <span className="muted">—</span> : String(f.value)}
                  {f.sensitive && <span className="wv-sens">受限 · {f.sensitive.join("/")}</span>}
                </span>
              </div>
            ))}
          </div>
        </section>

        {/* 关系网 */}
        <section className="et-pan-block">
          <div className="et-block-head">它的关系网<span className="et-block-hint">真实实例间的连接 · 点击穿梭</span></div>
          <div className="et-relnet">
            {(["out", "in"] as const).map((dir) => {
              const g = grouped(relsByDir[dir]);
              const keys = Object.keys(g);
              if (!keys.length) return null;
              return (
                <div className="et-rel-dir" key={dir}>
                  <div className="et-rel-dir-l mono">{dir === "out" ? "→ 它连向" : "← 连向它"}</div>
                  {keys.map((k) => (
                    <div className="et-rel-grp" key={k}>
                      <span className="et-rel-grp-plain">{g[k][0].plain} · {k}</span>
                      <div className="et-rel-chips">
                        {g[k].slice(0, 14).map((r, i) =>
                          r.targetId ? (
                            <button className="et-chip" key={i}
                              onClick={() => data.types[r.targetType]?.instances[r.targetId!] ? onJump(r.targetType, r.targetId!) : onJump(r.targetType, r.targetId!)}>
                              {r.targetId}
                            </button>
                          ) : (
                            <span className="et-chip more mono" key={i}>…更多</span>
                          )
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              );
            })}
            {!inst.rels.length && <span className="muted">无关联实例。</span>}
          </div>
        </section>
      </div>

      {/* 时间线 */}
      {inst.timeline.length > 0 && (
        <section className="et-pan-block full">
          <div className="et-block-head">
            它身上发生过的一切
            <span className="et-block-hint">里程碑 + 异常 + AI 提案 + 审批 + 处置 · 含 caused_by 连锁</span>
          </div>
          <div className="et-timeline">
            {inst.timeline.map((e, i) => <TimelineRow e={e} key={i} />)}
          </div>
        </section>
      )}
    </div>
  );
}

export default function ViewEntity() {
  const { route } = useNav();
  const [data, setData] = useState<EntityData | null>(null);
  const [err, setErr] = useState<string>("");
  const [type, setType] = useState("Shipment");
  const [inst, setInst] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    loadEntity().then((d) => {
      setData(d);
      setType(d.hero.type);
      setInst(d.hero.id);
    }).catch((e) => setErr(String(e)));
  }, []);

  // 响应穿梭：focus = "Type" 显示列表；focus = "Type:id" 显示全景
  useEffect(() => {
    if (route.view !== "entity" || !route.focus || !data) return;
    const [ft, fi] = route.focus.split(":");
    if (data.types[ft]) {
      setType(ft);
      setInst(fi || null);
      setQuery("");
    }
  }, [route.view, route.focus, data]);

  const tp = data?.types[type];
  const filtered = useMemo(() => {
    if (!tp) return [];
    const q = query.trim().toLowerCase();
    if (!q) return tp.index;
    return tp.index.filter((r) =>
      Object.values(r).some((v) => String(v ?? "").toLowerCase().includes(q))
    );
  }, [tp, query]);

  if (err) {
    return (
      <div>
        <ViewHead idx="09" question="一票具体的货长什么样？" subtitle="加载活世界实体数据…" />
        <p className="empty-badge">实体数据加载失败：{err}（需先跑 export_data.py 生成 public/data/entity.json）</p>
      </div>
    );
  }
  if (!data || !tp) {
    return (
      <div>
        <ViewHead idx="09" question="一票具体的货长什么样？" subtitle="加载活世界实体数据…" />
        <div className="et-loading mono">载入活世界实例中… (public/data/entity.json ≈ 9MB)</div>
      </div>
    );
  }

  return (
    <div>
      <ViewHead idx="09" question={data.question} subtitle={data.subtitle} />

      <div className="wv-layout">
        {/* 左：类型选择 */}
        <aside className="wv-rail">
          {data.domains.map((d) => (
            <div className="wv-dom" key={d.id}>
              <div className="wv-dom-name">{d.name}</div>
              {d.types.map((ty) => (
                <button
                  key={ty.type}
                  className={`wv-type${ty.type === type ? " active" : ""}${ty.covered ? "" : " uncovered"}`}
                  onClick={() => { if (ty.covered) { setType(ty.type); setInst(null); setQuery(""); } }}
                  disabled={!ty.covered}
                  title={ty.covered ? "" : "活世界暂未覆盖此域实例"}
                >
                  <span className="wv-type-name">{ty.plainName}</span>
                  <span className="wv-type-meta mono">{ty.covered ? ty.count.toLocaleString() : "—"}</span>
                </button>
              ))}
            </div>
          ))}
        </aside>

        {/* 中：列表 or 全景 */}
        <section className="wv-card">
          {inst ? (
            <>
              <button className="et-back" onClick={() => setInst(null)}>← 返回 {tp.plainName} 列表（{tp.count.toLocaleString()} 条）</button>
              {inst === data.hero.id && type === data.hero.type && (
                <div className="et-hero-note"><span className="tag amber" style={{ fontSize: 10 }}>默认 · 有完整故事的一票</span>{data.hero.note}</div>
              )}
              <Panorama data={data} type={type} id={inst}
                onJump={(t, i) => { setType(t); setInst(i); }} />
            </>
          ) : (
            <>
              <div className="et-list-head">
                <div>
                  <h2 className="wv-card-title">{tp.plainName} <span className="wv-card-type mono">{tp.type}</span></h2>
                  <p className="wv-card-why">{tp.why || "从活世界真实数据里点进任意一条看它的全景。"}</p>
                </div>
                <input className="et-search" placeholder="搜 ID / 状态 / 字段…"
                  value={query} onChange={(e) => setQuery(e.target.value)} />
              </div>
              <div className="et-list-meta mono">
                共 {tp.count.toLocaleString()} 条 · 索引显示 {tp.index.length} · 全景详情 {tp.detailCount ?? 0} 条 · sim
              </div>
              <div className="et-table">
                <div className="et-tr et-th">
                  <span>ID</span>
                  {tp.indexFields.map((f) => <span key={f}>{f}</span>)}
                </div>
                {filtered.slice(0, 200).map((r) => {
                  const hasDetail = !!tp.instances[String(r.id)];
                  return (
                    <button className={`et-tr${hasDetail ? "" : " nodetail"}`} key={String(r.id)}
                      onClick={() => setInst(String(r.id))}>
                      <span className="mono et-tr-id">{String(r.id)}</span>
                      {tp.indexFields.map((f) => <span key={f} className="et-tr-cell">{String(r[f] ?? "—")}</span>)}
                    </button>
                  );
                })}
                {filtered.length === 0 && <div className="muted" style={{ padding: 16 }}>没有匹配的实例。</div>}
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
