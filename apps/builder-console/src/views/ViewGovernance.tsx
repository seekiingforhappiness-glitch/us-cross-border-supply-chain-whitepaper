import { governance } from "../data";
import ViewHead from "../components/ViewHead";
import type { GovSlice } from "../types";

/* 治理控制室（第15视图 · G-Dashboard）——治理证据包收官件。
   六类证据全部现查现算、来源如实标注、无数据如实「暂无」。数字全部来自 governance.json 只读快照。 */

function statusTone(status: string): string {
  if (status.startsWith("通")) return "green";
  if (status.startsWith("部分") || status.startsWith("结构")) return "amber";
  return "violet"; // 待灌 / 其它
}

function pct(rate: number | null): string {
  return rate == null ? "—" : `${(rate * 100).toFixed(1)}%`;
}

/** 空态占位：来源已就位但暂无数据，如实标注（绝不编造）。 */
function Empty({ label }: { label: string }) {
  return (
    <div className="gov-empty">
      <span className="empty-badge">◇ {label}</span>
    </div>
  );
}

function SourceTag({ src }: { src: string | null }) {
  if (!src) return null;
  return <span className="gov-src mono">源 · {src}</span>;
}

// 放权阶梯档位 → 色调（复用既有 tag 色）：影子=中性紫、建议=琥珀、审核=青、自动=绿。
const TIER_TONE: Record<string, string> = {
  shadow: "violet", suggest: "amber", approve: "cyan", auto: "green",
};
const TIER_NAME: Record<string, string> = {
  shadow: "影子", suggest: "建议", approve: "审核", auto: "自动",
};

export default function ViewGovernance() {
  const g = governance;
  const { telemetry: t, security: sec, goldset, shadow: sh, gating, ledger, coverage, lineage, cost } = g;

  return (
    <div className="gov">
      <ViewHead idx="15" total={15} question={g.question} subtitle={g.subtitle} />

      {/* ── 数据源 provenance 总账 ── */}
      <section className="panel gov-sources">
        <div className="panel-head">
          <span className="panel-title">六本账 · 数据源如实标注</span>
          <span className="tag cyan" style={{ fontSize: 10 }}>现查现算 · 只读快照</span>
        </div>
        <div className="gov-src-grid">
          {g.sources.map((s) => (
            <div className="gov-src-cell" key={s.key}>
              <span className="gov-src-label">{s.label}</span>
              <span className="gov-src-table mono">{s.table}</span>
              <span className="gov-src-path mono muted">{s.path}</span>
              <span className={`gov-src-status ${s.status.includes("未就位") || s.status.includes("待灌") || s.status.includes("空") ? "warn" : "ok"}`}>
                {s.status}
              </span>
            </div>
          ))}
        </div>
      </section>

      {/* ── 护栏卡（可骄傲展示的证据）：铁证优先，放最上 ── */}
      <h2 className="gov-h2">护栏铁证 <span className="gov-h2-sub">可骄傲展示 · 全部现查</span></h2>
      <div className="gov-guardrails">
        {g.guardrails.map((gr) => (
          <div className={`gov-guard ${gr.pass ? "pass" : "fail"}`} key={gr.key}>
            <div className="gov-guard-top">
              <span className="gov-guard-val num">{gr.value}</span>
              <span className="gov-guard-unit">{gr.unit}</span>
              <span className={`gov-guard-mark ${gr.pass ? "pass" : "fail"}`}>{gr.pass ? "✓" : "✕"}</span>
            </div>
            <div className="gov-guard-label">{gr.label} = {gr.value}</div>
            <p className="gov-guard-plain">{gr.plain}</p>
            <SourceTag src={gr.source} />
          </div>
        ))}
      </div>

      {/* ── 七问框架 ── */}
      <h2 className="gov-h2">看板七问 <span className="gov-h2-sub">每个运行必须能回答的 7 问——看板照出治理盲区</span></h2>
      <div className="gov-seven">
        {g.sevenQuestions.map((q, i) => (
          <div className="gov-q" key={i}>
            <div className="gov-q-head">
              <span className="gov-q-n mono">{String(i + 1).padStart(2, "0")}</span>
              <span className="gov-q-title">{q.q}？</span>
              <span className={`gov-q-status tag ${statusTone(q.status)}`}>{q.status}</span>
            </div>
            <div className="gov-q-plain muted">{q.plain}</div>
            <div className="gov-q-by mono">→ {q.answeredBy}</div>
            <div className="gov-q-detail">{q.detail}</div>
          </div>
        ))}
      </div>

      {/* ── 六类证据卡 ── */}
      <h2 className="gov-h2">六类证据 <span className="gov-h2-sub">遥测 · 安全 · 金标 · 影子 · 台账 · 覆盖度</span></h2>

      {/* 卡①：AI 调用遥测 */}
      <section className="panel gov-card">
        <div className="panel-head">
          <span className="panel-title">① AI 调用遥测 · llm_calls</span>
          <SourceTag src={t.source} />
        </div>
        <div className="gov-card-body">
          {t.available ? (
            <div className="gov-tele">
              <div className="gov-tele-kpis">
                <div className="stat"><span className="stat-num cyan">{t.total.toLocaleString()}</span><span className="stat-label">调用总数</span></div>
                <div className="stat"><span className="stat-num">{t.latencyMs?.p50 ?? "—"}</span><span className="stat-label">耗时 p50 (ms)</span></div>
                <div className="stat"><span className="stat-num">{t.latencyMs?.p95 ?? "—"}</span><span className="stat-label">耗时 p95 (ms)</span></div>
                <div className="stat"><span className="stat-num amber">{t.degraded}</span><span className="stat-label">降级次数</span></div>
                <div className="stat"><span className="stat-num">{(t.tokens.input + t.tokens.output).toLocaleString()}</span><span className="stat-label">tokens（估算）</span></div>
              </div>
              <div className="gov-tele-breaks">
                {[["call_type", t.byType, "type"], ["provider", t.byProvider, "provider"], ["status", t.byStatus, "status"]].map(
                  ([lbl, arr]) => (
                    <div className="gov-break" key={lbl as string}>
                      <span className="gov-break-lbl mono">{lbl as string}</span>
                      {(arr as { n: number; [k: string]: unknown }[]).map((x, i) => (
                        <span className="gov-break-row" key={i}>
                          <span className="mono">{String(x[(lbl === "call_type" ? "type" : (lbl as string))] ?? "")}</span>
                          <span className="num">{x.n}</span>
                        </span>
                      ))}
                    </div>
                  )
                )}
              </div>
            </div>
          ) : (
            <Empty label="llm_calls 表 0 条 · 待接生产遥测" />
          )}
          <p className="gov-note">{t.note}</p>
        </div>
      </section>

      {/* 卡②：安全对抗 */}
      <section className="panel gov-card">
        <div className="panel-head">
          <span className="panel-title">② 安全对抗 · 越权 denied + 288 注入</span>
          <SourceTag src={sec.source} />
        </div>
        <div className="gov-card-body">
          <div className="gov-two-col">
            <div className="gov-sec-block">
              <div className="stat"><span className={`stat-num ${sec.deniedLive > 0 ? "amber" : "cyan"}`}>{sec.deniedLive}</span><span className="stat-label">越权 denied（现查主库 action_log）</span></div>
              <div className="gov-sec-results">
                {sec.byResult.map((r) => (
                  <span className="gov-chip" key={r.result}><span className="mono">{r.result}</span> <span className="num">{r.n}</span></span>
                ))}
                <span className="gov-chip total"><span className="mono">total</span> <span className="num">{sec.actionLogTotal}</span></span>
              </div>
            </div>
            <div className="gov-sec-block">
              <div className="gov-inj">
                <span className="tag green">288 注入 · {sec.injection.result.split("（")[0]}</span>
                <div className="gov-inj-detail">{sec.injection.result}</div>
                <div className="gov-inj-ref mono muted">{sec.injection.ref}</div>
                <div className="gov-inj-asof mono">最近记录 · {sec.injection.asOf}</div>
                <div className="gov-inj-note">{sec.injection.note}</div>
              </div>
            </div>
          </div>
          <p className="gov-note">{sec.note}</p>
        </div>
      </section>

      {/* 卡③：金标评估 */}
      <section className="panel gov-card">
        <div className="panel-head">
          <span className="panel-title">③ 金标评估 · shadow_run(bench2 金标)</span>
          <SourceTag src={goldset.source} />
        </div>
        <div className="gov-card-body">
          {goldset.available && goldset.latest ? (
            <div className="gov-gold">
              <div className="gov-gold-hero">
                <div className="stat"><span className="stat-num green">{pct(goldset.latest.rate)}</span><span className="stat-label">最近一轮 scripted 通过率</span></div>
                <div className="gov-gold-frac num">{goldset.latest.passed} / {goldset.latest.n}</div>
                <div className="gov-gold-run mono muted">{goldset.latest.run_id}</div>
              </div>
              <div className="gov-runs">
                <span className="gov-runs-lbl mono">历史 {goldset.runs.length} 轮</span>
                {goldset.runs.map((r) => (
                  <div className="gov-run-row" key={r.run_id}>
                    <span className="mono muted">{r.created_at.slice(0, 19).replace("T", " ")}</span>
                    <span className="num">{r.passed}/{r.n}</span>
                    <span className={`gov-run-dot ${r.passed === r.n ? "ok" : "warn"}`} />
                  </div>
                ))}
              </div>
              {goldset.llm ? (
                <div className="gov-gold-llm">
                  <span className="tag amber">LLM 档 · 真 AI 过题</span>
                  <span className="num">{goldset.llm.passed}/{goldset.llm.n}</span>
                  <span className="mono">{pct(goldset.llm.rate)}</span>
                </div>
              ) : (
                <Empty label="LLM 档未跑 · 需 shadow_bench --llm" />
              )}
            </div>
          ) : (
            <Empty label="shadow_run 无 bench2 记录 · 需先跑 shadow_bench" />
          )}
          <p className="gov-note">{goldset.note}</p>
        </div>
      </section>

      {/* 卡④：影子一致率 */}
      <section className="panel gov-card">
        <div className="panel-head">
          <span className="panel-title">④ 影子一致率 · shadow_run(bench1 分域切片)</span>
          <SourceTag src={sh.source} />
        </div>
        <div className="gov-card-body">
          {sh.measured && sh.overall ? (
            <div className="gov-shadow">
              <div className="stat"><span className="stat-num cyan">{pct(sh.overall.rate)}</span><span className="stat-label">总体一致率 · {sh.overall.consistent}/{sh.overall.n}</span></div>
              {(["byRule", "byLane", "bySeverity"] as const).map((k) => (
                <div className="gov-slice-group" key={k}>
                  <span className="gov-slice-lbl mono">{k === "byRule" ? "分规则" : k === "byLane" ? "分航线" : "分严重度"}</span>
                  <div className="gov-slices">
                    {(sh[k] as GovSlice[]).map((s) => (
                      <div className={`gov-slice ${s.lowConfidence ? "low" : ""}`} key={s.key}>
                        <span className="mono">{s.key}</span>
                        {s.lowConfidence ? (
                          <span className="gov-slice-warn mono" title={`样本 ${s.n} < ${sh.minSliceN}`}>样本 {s.n}·太小不下结论</span>
                        ) : (
                          <span className="num">{pct(s.rate)} <span className="muted">({s.consistent}/{s.n})</span></span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="gov-shadow-unmeasured">
              <Empty label="影子档1（bench1_resolution）未落数 · 一致率暂无" />
              {sh.population && (
                <div className="gov-pop">
                  <div className="gov-pop-head">
                    <span className="gov-pop-title">可比案例底数（现查，非一致率）</span>
                    <span className="num gov-pop-total">{sh.population.total}</span>
                    <span className="mono muted">例</span>
                  </div>
                  <div className="gov-pop-src mono muted">{sh.population.source}</div>
                  <div className="gov-pop-slices">
                    <div className="gov-pop-col">
                      <span className="gov-slice-lbl mono">分规则</span>
                      {sh.population.byRule.map((x) => (
                        <span className="gov-pop-row" key={x.key}><span className="mono">{x.key}</span><span className="num">{x.n}</span></span>
                      ))}
                    </div>
                    <div className="gov-pop-col">
                      <span className="gov-slice-lbl mono">分严重度</span>
                      {sh.population.bySeverity.map((x) => (
                        <span className="gov-pop-row" key={x.key}><span className="mono">{x.key}</span><span className="num">{x.n}</span></span>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
          {/* escalation recall 单独卡 */}
          {sh.escalation && (
            <div className="gov-esc">
              <span className="gov-esc-lbl">escalation recall（升级召回·放权关键）</span>
              <span className="num">{sh.escalation.recall == null ? "—" : pct(sh.escalation.recall)}</span>
              <span className="mono muted">escalated 案例 {sh.escalation.escalatedCases}</span>
              <p className="gov-note gov-esc-note">{sh.escalation.note}</p>
            </div>
          )}
          <p className="gov-note">{sh.note}</p>
        </div>
      </section>

      {/* 卡④½：放权阶梯（波1·C · display-only）——各域当前该在放权阶梯哪一档 */}
      <section className="panel gov-card">
        <div className="panel-head">
          <span className="panel-title">④½ 放权阶梯 · 各域当前档位（display-only）</span>
          <SourceTag src={gating.source} />
        </div>
        <div className="gov-card-body">
          {/* 阶梯图例 + 阈值版本（草案候人裁决） */}
          {gating.config && (
            <div className="gov-ladder" style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 10 }}>
              {gating.config.ladder.map((L, i) => (
                <span key={L.tier} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <span className={`tag ${TIER_TONE[L.tier] ?? "violet"}`} title={L.plain}>{L.name}</span>
                  {i < gating.config!.ladder.length - 1 && <span className="muted">→</span>}
                </span>
              ))}
              <span className="mono muted" style={{ marginLeft: "auto", fontSize: 10 }}>
                阈值 {gating.config.version} · 草案候 Daniel 裁决
              </span>
            </div>
          )}
          {/* escalation recall 白话（放权关键指标，必解释术语） */}
          <div className="gov-note" style={{ marginBottom: 8 }}>
            白话：<b>放权阶梯</b>＝系统按各域 AI 实测可靠度，算它现在该被信任到哪一档（只展示、绝不自动改权限，V15 保护条款）。
            <b>escalation recall</b>＝该转给人的案子里，AI 也说要转人/更保守的比例——放权最怕漏升级，这个比一致率更决定"敢不敢放权"。
          </div>
          {gating.available && gating.summary ? (
            <>
              <div className="gov-cov-summary" style={{ marginBottom: 10 }}>
                {Object.entries(gating.summary.by_tier).map(([tier, n]) => (
                  <div className="stat" key={tier}>
                    <span className={`stat-num ${tier === "shadow" ? "" : "cyan"}`}>{n}</span>
                    <span className="stat-label">{TIER_NAME[tier] ?? tier} 档域数</span>
                  </div>
                ))}
                <div className="stat">
                  <span className="stat-num">{gating.summary.domain_count}</span>
                  <span className="stat-label">域总数</span>
                </div>
              </div>
              {["resolution", "goldset"].map((grp) => {
                const rows = gating.domains.filter((d) => d.group === grp);
                if (!rows.length) return null;
                return (
                  <div className="gov-slice-group" key={grp}>
                    <span className="gov-slice-lbl mono">{grp === "resolution" ? "档1 · 分规则（对历史决定）" : "档2 · 分题类（真 AI 过金标题）"}</span>
                    <div className="gov-gate-rows" style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
                      {rows.map((d) => (
                        <div className="gov-gate-row" key={`${d.group}-${d.domain}`}
                          style={{ display: "flex", flexDirection: "column", gap: 2, padding: "6px 8px", borderRadius: 6, background: "rgba(127,127,127,0.06)" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                            <span className={`tag ${TIER_TONE[d.tier] ?? "violet"}`}>{TIER_NAME[d.tier] ?? d.tier}</span>
                            <span className="mono">{d.domain}</span>
                            <span className="muted">{d.plain}</span>
                            <span className="num" style={{ marginLeft: "auto" }}>
                              {d.rate == null ? "—" : pct(d.rate)}
                              {d.ci && <span className="muted mono" style={{ fontSize: 10 }}> [{pct(d.ci[0])}–{pct(d.ci[1])}]</span>}
                              <span className="muted mono" style={{ fontSize: 10 }}> n={d.n}{d.low_sample ? " ⚠样本不足" : ""}</span>
                            </span>
                          </div>
                          {d.escalation && d.escalation.ref_n > 0 && (
                            <div className="mono muted" style={{ fontSize: 10 }}>
                              escalation recall {d.escalation.rate == null ? "—" : pct(d.escalation.rate)} ({d.escalation.caught}/{d.escalation.ref_n})
                            </div>
                          )}
                          <div className="muted" style={{ fontSize: 11 }}>
                            离下一档（{TIER_NAME[d.next_tier] ?? d.next_tier}）差：{d.gaps.join("；")}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
              {gating.summary.note && <p className="gov-note">{gating.summary.note}</p>}
            </>
          ) : (
            <Empty label="放权报告未就位 · 需先跑 python3 -m agent.gating" />
          )}
          <p className="gov-note">{gating.honestNote ?? gating.note}</p>
        </div>
      </section>

      {/* 卡⑤：规则台账 */}
      <section className="panel gov-card">
        <div className="panel-head">
          <span className="panel-title">⑤ 规则台账 · rule_run_ledger</span>
          <SourceTag src={ledger.source} />
        </div>
        <div className="gov-card-body">
          {ledger.available ? (
            <div className="gov-ledger">
              <div className="gov-ledger-runs">
                {ledger.runs.map((r, i) => (
                  <div className="gov-ledger-run" key={i}>
                    <span className="mono muted">as_of {r.as_of}</span>
                    <span className="gov-ledger-detected num">{r.total}</span>
                    <span className="stat-label">检测 · {r.rules} 规则{r.errors > 0 ? ` · ${r.errors} error` : ""}</span>
                  </div>
                ))}
              </div>
              <div className="gov-ledger-rules">
                {ledger.latestByRule.map((r) => (
                  <div className={`gov-lrule ${r.status !== "ok" ? "err" : ""}`} key={r.rule_id}>
                    <span className="gov-lrule-id mono">{r.rule_id}</span>
                    <span className="gov-lrule-n num">{r.detected_count}</span>
                    <span className="gov-lrule-fp mono muted" title="input_fingerprint">{r.fp}</span>
                  </div>
                ))}
              </div>
              {ledger.diff ? (
                <div className={`gov-diff ${ledger.diff.idempotent ? "ok" : "warn"}`}>
                  <span className="mono">diff · as_of {ledger.diff.asOf}</span>
                  {ledger.diff.idempotent ? (
                    <span className="tag green">逐规则一致 · 幂等已修</span>
                  ) : (
                    <span className="tag amber">{ledger.diff.changed.length} 条变化</span>
                  )}
                  {ledger.diff.changed.map((c) => (
                    <span className="gov-diff-row mono" key={c.rule_id}>{c.rule_id}: {c.before} → {c.after}</span>
                  ))}
                </div>
              ) : null}
            </div>
          ) : (
            <Empty label="rule_run_ledger 未就位 · 需先跑 engine.detect" />
          )}
          <p className="gov-note">{ledger.note}</p>
        </div>
      </section>

      {/* 卡⑥：需求覆盖度 */}
      <section className="panel gov-card">
        <div className="panel-head">
          <span className="panel-title">⑥ 需求覆盖度 · 每条能力被哪个测试锁定</span>
          <SourceTag src={coverage.source} />
        </div>
        <div className="gov-card-body">
          <div className="gov-cov-summary">
            <div className="stat"><span className="stat-num green">{coverage.assertions.checked}/{coverage.assertions.total}</span><span className="stat-label">演示断言（demo-assertions）</span></div>
            <div className="stat"><span className="stat-num green">{coverage.gates.checked}/{coverage.gates.total}</span><span className="stat-label">发版门（release-checklist）</span></div>
            <div className="gov-cov-asof">
              <span className="stat-label">文档记录最近验证</span>
              <span className="mono">{coverage.asOf ?? "未标注"}</span>
            </div>
          </div>
          {coverage.assertions.groups.map((grp) => (
            <div className="gov-cov-group" key={grp.section}>
              <div className="gov-cov-group-head mono">{grp.section} · {grp.name}</div>
              <div className="gov-cov-rows">
                {grp.items.map((it) => (
                  <div className="gov-cov-row" key={it.id}>
                    <span className={`gov-cov-box ${it.checked ? "on" : "off"}`}>{it.checked ? "✓" : "○"}</span>
                    <span className="gov-cov-id mono">{it.id}</span>
                    <span className="gov-cov-text">{it.text}</span>
                    <span className="gov-cov-test mono muted">{it.test}</span>
                  </div>
                ))}
              </div>
            </div>
          ))}
          <div className="gov-cov-repro mono">
            <span className="tag cyan">复现</span> {coverage.reproduce}
          </div>
          <div className="honest-banner" style={{ marginTop: 14 }}>
            <span className="honest-icon">◈</span>
            <div>
              <div className="honest-label mono">状态怎么来的</div>
              <p className="honest-text">{coverage.note}</p>
            </div>
          </div>
        </div>
      </section>

      {/* ── 血缘拼接演示（七问·实际改了什么）── */}
      <h2 className="gov-h2">血缘拼接 <span className="gov-h2-sub">七问·「实际改了什么」——trace_id 拼 llm_calls ↔ action_log</span></h2>
      <section className="panel gov-card gov-lineage">
        <div className="panel-head">
          <span className="panel-title">G-Trace 血缘链</span>
          <div className="gov-lineage-cols">
            <span className={`tag ${lineage.llmCallsHasTrace ? "green" : "red"}`}>llm_calls.trace_id {lineage.llmCallsHasTrace ? "✓" : "✕"}</span>
            <span className={`tag ${lineage.actionLogHasTrace ? "green" : "red"}`}>action_log.trace_id {lineage.actionLogHasTrace ? "✓" : "✕"}</span>
          </div>
        </div>
        <div className="gov-card-body">
          {lineage.instances.length > 0 ? (
            <div className="gov-joins">
              {lineage.instances.map((j, i) => (
                <div className="gov-join" key={i}>
                  <div className="gov-join-trace mono">trace {j.trace_id}</div>
                  <div className="gov-join-flow">
                    <span className="gov-join-node llm">
                      <span className="mono">{j.call_type}</span>
                      <span className="muted mono">{j.provider}/{j.model ?? "—"} · {j.status}</span>
                    </span>
                    <span className="gov-join-arrow">→</span>
                    <span className="gov-join-node al">
                      <span className="mono">{j.action}</span>
                      <span className="muted mono">{j.target_object_id} · {j.result}</span>
                    </span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <Empty label="暂无实例可拼 · 链路结构已通、数据待灌" />
          )}
          <p className="gov-note">{lineage.note}</p>
        </div>
      </section>

      {/* ── 成本分账 ── */}
      <section className="panel gov-card">
        <div className="panel-head">
          <span className="panel-title">成本分账 · token/耗时 × call_type × provider</span>
          <SourceTag src={cost.source} />
        </div>
        <div className="gov-card-body">
          {cost.available ? (
            <div className="gov-cost">
              {cost.byTypeProvider.map((c, i) => (
                <div className="gov-cost-row" key={i}>
                  <span className="mono">{c.call_type} · {c.provider}</span>
                  <span className="num">{c.n} 次</span>
                  <span className="num">{c.tokens.toLocaleString()} tok</span>
                  <span className="num">{c.duration_ms.toLocaleString()} ms</span>
                </div>
              ))}
            </div>
          ) : (
            <Empty label="llm_calls 0 条 · 成本账待灌" />
          )}
          <p className="gov-note">{cost.note}</p>
        </div>
      </section>

      {/* ── 收官诚实横幅 ── */}
      <div className="honest-banner" style={{ marginTop: 22 }}>
        <span className="honest-icon">◈</span>
        <div>
          <div className="honest-label mono">这一屏的诚实边界</div>
          <p className="honest-text">
            六类证据全部现查现算、来源如实标注。空账（AI 调用遥测 / 影子档1 / 成本分账 / 血缘实例）不是缺陷，是诚实——
            这些数要拿去做「以后要不要给 AI 更多权限」的放权决策，无一手实测绝不给好看的假数字（全局规则 5：自信的具体 ≠ 真实）。
            护栏铁证（AI 非法落库=0 / 重复副作用=0 / AI 越权全被拒）与金标通过率、规则台账是已经跑出来的真证据；其余如实标「暂无/需先跑 X」。
          </p>
        </div>
      </div>
    </div>
  );
}
