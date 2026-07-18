import { useEffect, useState } from "react";
import {
  fetchProposalEvidence,
  formatPct,
  formatUsd,
  isMasked,
  type EvidenceActionHistorical,
  type EvidenceAlternatives,
  type EvidenceImpact,
  type EvidencePrecedents,
  type EvidenceTrust,
  type ProposalEvidence,
  type Role,
} from "../api";
import Icon from "../components/Icons";
import StateHint from "../components/StateHint";

// 规格③ 证据链卡（ImpactPanel 待拍板动作区，AI 建议与批准/驳回按钮之间——让拍板的人先看证据再按键）。
// 后端契约：GET /proposals/{task_id}/evidence（apps/api/evidence.py，纯读四块，本组件不改一行后端）。
// 四块：影响三数字一行 / 先例分布横条（各决定占比 + 事后有效率，n 与"样本不足"如实）/ 信任档徽章一行
// （tier 白话 + 一致率 + CI + n）/ 备选对比两行小表。全部诚实空态：available:false / empty / null+reason
// 照实画，绝不把"无"画成 0 或编数；金额 MASK 串照实显"无权查看"不当数字。
// 加载走 StateHint；出错只在本卡内显"证据暂不可用"，绝不阻断下方审批按钮（按钮在本卡之外独立渲染）。
//
// 为什么这样建（≤5 行，AGENTS.md §4）：
//   · 证据是"拍板增强"而非门槛——出错/缺数只降级本卡，审批按钮永远可用（本卡与 DecisionButtons 平级同层）。
//   · 白话/翻译尽量用后端拼好的串（recent_examples[].plain、options[].label、note/reason）——前端只补
//     两处最小译名（决定 3 键、档位 4 键），镜像 engine/gating 单一来源，未知值回退原文不猜译。
//   · 先例分布横条复用"预算条"图案语言（track + 分段填色），语义色盘沿用（采纳=绿/改后采纳=蓝/拒绝=红）。

// 决定白话（镜像 engine.resolution_memory.DECISION_LABELS 的 3 键；前端展示用，非权威源；未知值回退原文）。
const DECISION_CN: Record<string, string> = { adopted: "采纳", modified: "改后采纳", rejected: "拒绝" };
// 决定 → 语义色段（采纳=绿、改后采纳=信标蓝、拒绝=红）：复用既有语义盘，先例分布横条按此上色。
const DECISION_TONE: Record<string, string> = { adopted: "green", modified: "beacon", rejected: "red" };
// 决定固定段序（分布横条与图例都按此，只画在场决定；防御性把未列出的决定追加在后）。
const DECISION_ORDER = ["adopted", "modified", "rejected"];
// 档位白话（镜像 agent/gating.py 阶梯：影子 shadow→建议 suggest→审核 approve→自动 auto；未知回退原 tier）。
const TIER_CN: Record<string, string> = { shadow: "影子档", suggest: "建议档", approve: "审核档", auto: "自动档" };
// 备选动作固定序（expedite 加急 / accept_delay 接受延误，与 evidence.py::_ALT_ACTIONS 对齐）。
const ALT_ORDER = ["expedite", "accept_delay"];

// ── ① 影响：受影响订单行 / 金额合计 / 波及客户，三数字一行（金额 MASK 照实显"无权查看"，不当数字）──
function ImpactRow({ impact }: { impact: EvidenceImpact }) {
  if (impact.available === false) {
    return <div className="cp-ev__empty">{impact.reason ?? "该提案关联的风险查无，无法计算影响面。"}</div>;
  }
  const amt = impact.amount_usd;
  return (
    <>
      <div className="cp-ev__nums">
        <div className="cp-ev__num">
          <span className="cp-ev__num-v num">{impact.affected_order_lines ?? 0}</span>
          <span className="cp-ev__num-k">受影响订单行</span>
        </div>
        <div className="cp-ev__num">
          <span className={`cp-ev__num-v num ${isMasked(amt) ? "is-masked" : "gold"}`}>{formatUsd(amt as number | string)}</span>
          <span className="cp-ev__num-k">金额合计</span>
        </div>
        <div className="cp-ev__num">
          <span className="cp-ev__num-v num">{impact.affected_customers ?? 0}</span>
          <span className="cp-ev__num-k">波及客户</span>
        </div>
      </div>
      {impact.note && <div className="cp-ev__note">{impact.note}</div>}
    </>
  );
}

// ── ② 先例：各决定占比横条 + 事后有效率 + n（首例/样本不足/放宽全航线都如实标）──
function PrecedentsRow({ prec }: { prec: EvidencePrecedents }) {
  if (!prec.available) {
    return <div className="cp-ev__empty">{prec.reason ?? "无法检索同类先例。"}</div>;
  }
  if (prec.empty || prec.n === 0) {
    return <div className="cp-ev__empty">{prec.note ?? "无同类先例（首例）——暂无历史处置记忆可参照。"}</div>;
  }
  const n = prec.n;
  const by = prec.by_decision ?? {};
  const eff = prec.effectiveness;
  const segs = [
    ...DECISION_ORDER.filter((d) => (by[d] ?? 0) > 0),
    ...Object.keys(by).filter((d) => !DECISION_ORDER.includes(d) && (by[d] ?? 0) > 0),
  ].map((d) => ({ d, count: by[d] }));
  const scope =
    prec.match_scope === "rule_only"
      ? "同规则·全航线"
      : `${prec.rule_id ?? "?"}${prec.lane ? ` · ${prec.lane}` : ""}`;
  const hasRate = !!eff && eff.labeled > 0 && eff.effective_rate != null;
  return (
    <>
      <div className="cp-ev__bar" role="img" aria-label={`同类 ${n} 例决定分布`}>
        {segs.map((s) => (
          <div
            key={s.d}
            className="cp-ev__seg"
            data-tone={DECISION_TONE[s.d] ?? "neutral"}
            style={{ width: `${(s.count / n) * 100}%` }}
            title={`${DECISION_CN[s.d] ?? s.d} ${s.count} 例（${formatPct(s.count / n, 0)}）`}
          />
        ))}
      </div>
      <div className="cp-ev__legend">
        {segs.map((s) => (
          <span key={s.d} className="cp-ev__leg">
            <i className="cp-ev__dot" data-tone={DECISION_TONE[s.d] ?? "neutral"} />
            {DECISION_CN[s.d] ?? s.d} <b className="num">{s.count}</b>
          </span>
        ))}
      </div>
      <div className="cp-ev__meta">
        同类 <b className="num">{n}</b> 例（{scope}）·{" "}
        {hasRate ? (
          <>
            事后有效率 <b className="num">{formatPct(eff!.effective_rate)}</b>（已结案 {eff!.labeled} 例）
          </>
        ) : (
          "同类先例均未回填事后结果（无有效率可算）"
        )}
      </div>
      {prec.sample_note && <div className="cp-ev__warn">{prec.sample_note}</div>}
      {prec.widen_reason && <div className="cp-ev__note">{prec.widen_reason}</div>}
    </>
  );
}

// ── ③ 信任档：tier 白话 + 一致率 + CI + n，一行徽章（display_only 原样透传"档位≠已授权·请谨慎复核"）──
function TrustRow({ trust }: { trust: EvidenceTrust }) {
  if (!trust.available) {
    return <div className="cp-ev__empty">{trust.reason ?? "该规则的放权档位暂不可用。"}</div>;
  }
  const tier = trust.tier ?? "";
  const tierCn = TIER_CN[tier] ?? (tier || "—");
  const ci = trust.ci;
  const ciStr = ci && ci.length === 2 ? ` [${formatPct(ci[0], 0)}–${formatPct(ci[1], 0)}]` : "";
  return (
    <>
      <div className="cp-ev__trust">
        <span className={`cp-gating__tier cp-gating__tier--${tier || "shadow"}`}>
          {tierCn}
          {trust.name ? ` · ${trust.name}` : ""}
        </span>
        <span className="cp-ev__trust-meta num">
          一致率 {formatPct(trust.rate)}
          {ciStr} · n={trust.n ?? 0}
        </span>
      </div>
      {trust.display_only && (
        <div className="cp-ev__warn">display-only：档位=模型在该域历史一致率的展示，非工具授权（不代表 AI 可自动执行）——请谨慎复核。</div>
      )}
    </>
  );
}

// 某备选动作的历史有效率白话（n=0 无案例 / 有例未回填 / 有率）——都如实，绝不编。
function historicalText(h: EvidenceActionHistorical): string {
  if (h.n === 0) return "无历史案例";
  if (h.effective_rate == null) return `${h.n} 例 · 未回填结果`;
  return `${formatPct(h.effective_rate)}（${h.labeled ?? h.n} 例）`;
}

// ── ④ 备选对比：加急 vs 接受延误，两行小表（延误天数/受影响货值为共享上下文；历史有效率逐选择）──
function AlternativesRow({ alt }: { alt: EvidenceAlternatives }) {
  if (!alt.available) {
    return <div className="cp-ev__empty">{alt.reason ?? "无法比较备选方案代价。"}</div>;
  }
  const opts = alt.options ?? {};
  const keys = [...ALT_ORDER.filter((k) => opts[k]), ...Object.keys(opts).filter((k) => !ALT_ORDER.includes(k))];
  const val = alt.affected_value_usd;
  // 空态修复（P2，王总"两格全空仍摆表"）：全部备选方案都没有可比的历史有效率（effective_rate 全 null，
  // 含"无案例"与"有案例未回填"两种情况）时，不摆一张两格都写"无历史案例"的空表，改一句话空态；
  // 只要有任一方案有真实有效率，就保留表格、无数据的格子照实标注（不隐藏，只是不单独摆一张全空表）。
  const anyRate = keys.some((k) => opts[k].historical.effective_rate != null);
  return (
    <>
      <div className="cp-ev__alt-ctx">
        {alt.delay_days != null ? (
          <>
            当前延误 <b className="num">{alt.delay_days}</b> 天
          </>
        ) : (
          "无货件延误天数"
        )}
        {" · 受影响货值 "}
        <span className={`num ${isMasked(val) ? "is-masked" : "gold"}`}>{formatUsd((val ?? null) as number | string | null)}</span>
      </div>
      {keys.length === 0 ? (
        <div className="cp-ev__empty">{alt.note ?? "无可比较的备选方案。"}</div>
      ) : !anyRate ? (
        <div className="cp-ev__empty">各方案都还没有可比的历史有效率数据——先例结果回填后这里会给出量化对比。</div>
      ) : (
        <table className="cp-ev__alt">
          <thead>
            <tr>
              <th>选择</th>
              <th className="num">历史有效率</th>
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k}>
                <td>{opts[k].label}</td>
                <td className="num">{historicalText(opts[k].historical)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {alt.delay_reason && <div className="cp-ev__note">{alt.delay_reason}</div>}
    </>
  );
}

// ═══════════════════════════ 证据链卡主体 ═══════════════════════════
export default function EvidenceCard({ taskId, role }: { taskId: string; role: Role }) {
  const [ev, setEv] = useState<ProposalEvidence | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0); // 出错重试驱动（StateHint 重试按钮）

  useEffect(() => {
    let cancelled = false;
    setEv(null);
    setErr(null);
    fetchProposalEvidence(taskId, role)
      .then((d) => !cancelled && setEv(d))
      .catch((e) => !cancelled && setErr((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [taskId, role, tick]);

  return (
    <div className="cp-ev">
      <div className="cp-ev__head">
        <Icon name="detect" size={12} />
        <span className="cp-ev__head-t">证据链</span>
        <span className="cp-ev__head-sub">拍板前先看：影响 · 先例 · 信任档 · 备选</span>
      </div>
      {err ? (
        // 证据挂了如实显示"证据暂不可用"，不阻断审批——按钮在本卡之外独立渲染，永远可点。
        <StateHint
          kind="error"
          compact
          title="证据暂不可用"
          message={`${err}（不影响批准/驳回，可照常拍板）`}
          onRetry={() => setTick((n) => n + 1)}
        />
      ) : !ev ? (
        <StateHint kind="loading" compact title="证据加载中…" />
      ) : (
        <>
          <section className="cp-ev__sect">
            <div className="cp-ev__sect-t">影响</div>
            <ImpactRow impact={ev.impact} />
          </section>
          <section className="cp-ev__sect">
            <div className="cp-ev__sect-t">同类先例怎么处置的</div>
            <PrecedentsRow prec={ev.precedents} />
          </section>
          <section className="cp-ev__sect">
            <div className="cp-ev__sect-t">AI 在这类事上的信任档</div>
            <TrustRow trust={ev.trust} />
          </section>
          <section className="cp-ev__sect">
            <div className="cp-ev__sect-t">备选方案代价</div>
            <AlternativesRow alt={ev.alternatives} />
          </section>
        </>
      )}
    </div>
  );
}
