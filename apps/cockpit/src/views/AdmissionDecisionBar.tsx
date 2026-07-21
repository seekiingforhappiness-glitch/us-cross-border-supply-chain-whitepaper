import { useEffect, useState } from "react";
import { fetchObject, formatUsd, postDecision, traverse, type ObjectFields, type Role } from "../api";
import { actorForRole, roleShort } from "../roleActors";
import Icon from "../components/Icons";
import StateHint from "../components/StateHint";
import { humanizeFieldValue } from "./objectLabels";

// B-1（V18）准入案决策：批准报价 / 驳回或要补件——挂在 AdmissionCase 的对象卡（ObjectCard）
// 动作区，复用 DecisionButtons 模式（manager gating / 灰态白话 / X-Actor / 失败原样展示，同
// ImpactPanel.tsx::DecisionButtons / CloseRiskButton），零新写路（/decisions 白名单已含
// ApproveQuoteDecision/RejectOrRequestMoreInfo，本次后端零改动）。
//
// 为什么挂在 ObjectCard 而不新造页面：驾驶舱现有的"准入下钻面"链路是 客户区队列 → 客户对象卡 →
// 展开 case_for_customer 关系 → 打开某张准入案对象卡（本体 links 全 35 类通用导航，链路本就存在）
// ——只是链路终点此前纯只读、没有动作区；本文件补的正是这一块：不建新页面、不新增后端接口，
// 案件下的物流方案/成本情景选项同样靠既有 traverse/fetchObject 通用端点两跳现查（case_has_plan →
// plan_has_scenario），不新增专用列表接口。
//
// 后端签名（app/admission_actions.py，函数本体本次一字未改）：
//   approve_quote_decision(admission_case_id, approved_logistics_plan_id, approved_cost_scenario_id,
//                          decision, decision_reason, conditions, actor, role, as_of)
//   reject_or_request_more_info(admission_case_id, decision, missing_documents, rejection_reason,
//                               actor, role, as_of)
// 两动作 executors 分别是 {manager} / {manager, compliance}（本体权限现查核实，非猜测）——驾驶舱
// 只有 manager/ops 两个角色可选，compliance 不在其中，故两个按钮在驾驶舱语境下都按 manager-only
// 前端预判、ops 置灰；真正闸门仍在后端 app 层原函数（前端置灰不等于放松后端校验，同 A-1 注释）。

const CASE_TERMINAL = new Set(["approved", "quote_with_conditions", "rejected"]); // 镜像 admission_actions.py::CASE_TERMINAL（前端预判用，非权威源）
const PLAN_FETCH_CAP = 12; // 按需拉取上限（点开"批准报价"才触发一次两跳查询，非循环，有界，同 ImpactPanel::LINE_FETCH_CAP 先例）

interface PlanScenario {
  planId: string;
  planLabel: string;
  scenarioId: string;
  scenarioLabel: string;
}

function planLabelOf(id: string, f: ObjectFields): string {
  const route = humanizeFieldValue("LogisticsPlan", "route_type", f.route_type) ?? String(f.route_type ?? "—");
  const incoterm = f.incoterm ? String(f.incoterm) : "—";
  const days = f.estimated_transit_days;
  return `${id} ${f.plan_name ?? ""}（${route}/${incoterm}・${days ?? "—"}天）`;
}

function scenarioLabelOf(id: string, f: ObjectFields): string {
  const type = humanizeFieldValue("CostScenario", "scenario_type", f.scenario_type) ?? String(f.scenario_type ?? "—");
  return `${id} ${type}・报价${formatUsd(f.quote_price_usd as number | string)}・毛利${formatUsd(f.gross_margin_usd as number | string)}`;
}

/** 案件下可选的"方案×情景"组合——沿既有关系两跳现查（case_has_plan → plan_has_scenario），不新增
 *  后端接口。任一环节失败（关系不可遍历/对象不存在）静默跳过该分支，不让局部失败拖垮整体列表。 */
async function loadPlanScenarios(caseId: string, role: Role): Promise<PlanScenario[]> {
  const planRes = await traverse("AdmissionCase", caseId, "case_has_plan", role).catch(() => null);
  const planIds = (planRes?.neighbor_ids ?? []).slice(0, PLAN_FETCH_CAP);
  const out: PlanScenario[] = [];
  for (const planId of planIds) {
    const [plan, scenarioRes] = await Promise.all([
      fetchObject("LogisticsPlan", planId, role).catch(() => null),
      traverse("LogisticsPlan", planId, "plan_has_scenario", role).catch(() => null),
    ]);
    if (!plan) continue;
    const planLabel = planLabelOf(planId, plan);
    const scenarioIds = (scenarioRes?.neighbor_ids ?? []).slice(0, PLAN_FETCH_CAP);
    const scenarios = await Promise.all(scenarioIds.map((sid) => fetchObject("CostScenario", sid, role).catch(() => null)));
    scenarios.forEach((s, i) => {
      if (!s) return;
      out.push({ planId, planLabel, scenarioId: scenarioIds[i], scenarioLabel: scenarioLabelOf(scenarioIds[i], s) });
    });
  }
  return out;
}

function ApproveForm({ caseId, role, onCancel, onDecided }: { caseId: string; role: Role; onCancel: () => void; onDecided?: () => void }) {
  const [combos, setCombos] = useState<PlanScenario[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [loadTick, setLoadTick] = useState(0);
  const [picked, setPicked] = useState("");
  const [decision, setDecision] = useState<"approve" | "quote_with_conditions">("approve");
  const [reason, setReason] = useState("");
  const [conditions, setConditions] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setCombos(null);
    setLoadErr(null);
    loadPlanScenarios(caseId, role)
      .then((c) => !cancelled && setCombos(c))
      .catch((e: Error) => !cancelled && setLoadErr(e.message));
    return () => {
      cancelled = true;
    };
  }, [caseId, role, loadTick]);

  const submit = async () => {
    if (busy || !picked || !reason.trim()) return;
    const [planId, scenarioId] = picked.split("|");
    setBusy(true);
    setErr(null);
    try {
      await postDecision(
        "ApproveQuoteDecision",
        {
          admission_case_id: caseId,
          approved_logistics_plan_id: planId,
          approved_cost_scenario_id: scenarioId,
          decision,
          decision_reason: reason.trim(),
          conditions: decision === "quote_with_conditions" ? conditions.trim() : "",
        },
        role,
        actorForRole(role),
      );
      onDecided?.();
    } catch (e) {
      setErr((e as Error).message); // 失败：后端白话中文原文（G1/G2/G3 门禁未过 / M1 双人复核拒绝…），原样展示
      setBusy(false);
    }
  };

  return (
    <div className="cp-decide cp-decide--form">
      {loadErr ? (
        <StateHint kind="error" compact title="方案/情景加载失败" message={loadErr} onRetry={() => setLoadTick((n) => n + 1)} />
      ) : !combos ? (
        <StateHint kind="loading" compact title="加载可选方案…" />
      ) : combos.length === 0 ? (
        <div className="cp-masked">该案件名下无可选的物流方案/成本情景（需先在 Streamlit 操作台走完 B3 方案设计与 B4 成本核算）</div>
      ) : (
        <>
          <label className="cp-form-row">
            <span className="cp-form-row__k">方案 × 情景*</span>
            <select className="cp-form-select" value={picked} disabled={busy} onChange={(e) => setPicked(e.target.value)}>
              <option value="">请选择</option>
              {combos.map((c) => (
                <option key={`${c.planId}|${c.scenarioId}`} value={`${c.planId}|${c.scenarioId}`}>
                  {c.planLabel} → {c.scenarioLabel}
                </option>
              ))}
            </select>
          </label>
          <label className="cp-form-row">
            <span className="cp-form-row__k">结论</span>
            <select className="cp-form-select" value={decision} disabled={busy} onChange={(e) => setDecision(e.target.value as typeof decision)}>
              <option value="approve">批准</option>
              <option value="quote_with_conditions">有条件批准</option>
            </select>
          </label>
          <label className="cp-form-row">
            <span className="cp-form-row__k">审批理由*</span>
            <textarea
              className="cp-form-textarea"
              value={reason}
              disabled={busy}
              placeholder="必填：为什么批准这个方案/情景"
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          {decision === "quote_with_conditions" && (
            <label className="cp-form-row">
              <span className="cp-form-row__k">附加条件</span>
              <textarea
                className="cp-form-textarea"
                value={conditions}
                disabled={busy}
                placeholder="有条件批准需注明条件"
                onChange={(e) => setConditions(e.target.value)}
              />
            </label>
          )}
          <div className="cp-decide__row">
            <button className="cp-decide-btn cp-decide-btn--approve" disabled={busy || !picked || !reason.trim()} onClick={submit}>
              {busy ? "提交中…" : "确认批准"}
            </button>
            <button className="cp-decide-btn" disabled={busy} onClick={onCancel}>
              取消
            </button>
          </div>
        </>
      )}
      {err && (
        <div className="cp-decide__err">
          <b>没提交成功</b> · {err}
        </div>
      )}
      <div className="cp-decide__basis">
        三重门禁：无未核实的严重合规发现（G1）・DDP 方案要求客户具备 IOR 资质（G2）・案件须已计价且方案/情景有效（G3）。经手身份{" "}
        {actorForRole(role)}
      </div>
    </div>
  );
}

function RejectForm({ caseId, role, onCancel, onDecided }: { caseId: string; role: Role; onCancel: () => void; onDecided?: () => void }) {
  const [decision, setDecision] = useState<"reject" | "more_info">("more_info");
  const [reason, setReason] = useState("");
  const [missingDocs, setMissingDocs] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const docsList = missingDocs
    .split(/[,，\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
  const canSubmit = decision === "reject" ? reason.trim().length > 0 : docsList.length > 0;

  const submit = async () => {
    if (busy || !canSubmit) return;
    setBusy(true);
    setErr(null);
    try {
      await postDecision(
        "RejectOrRequestMoreInfo",
        {
          admission_case_id: caseId,
          decision,
          missing_documents: decision === "more_info" ? docsList : [],
          rejection_reason: decision === "reject" ? reason.trim() : "",
        },
        role,
        actorForRole(role),
      );
      onDecided?.();
    } catch (e) {
      setErr((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="cp-decide cp-decide--form">
      <label className="cp-form-row">
        <span className="cp-form-row__k">结论</span>
        <select className="cp-form-select" value={decision} disabled={busy} onChange={(e) => setDecision(e.target.value as typeof decision)}>
          <option value="more_info">要补资料</option>
          <option value="reject">拒接</option>
        </select>
      </label>
      {decision === "reject" ? (
        <label className="cp-form-row">
          <span className="cp-form-row__k">拒接原因*</span>
          <textarea className="cp-form-textarea" value={reason} disabled={busy} placeholder="必填：明确的拒接原因" onChange={(e) => setReason(e.target.value)} />
        </label>
      ) : (
        <label className="cp-form-row">
          <span className="cp-form-row__k">缺失材料*</span>
          <textarea
            className="cp-form-textarea"
            value={missingDocs}
            disabled={busy}
            placeholder="必填：逐项列出缺什么，逗号或换行分隔"
            onChange={(e) => setMissingDocs(e.target.value)}
          />
        </label>
      )}
      <div className="cp-decide__row">
        <button className="cp-decide-btn cp-decide-btn--reject" disabled={busy || !canSubmit} onClick={submit}>
          {busy ? "提交中…" : decision === "reject" ? "确认拒接" : "确认要补件"}
        </button>
        <button className="cp-decide-btn" disabled={busy} onClick={onCancel}>
          取消
        </button>
      </div>
      {err && (
        <div className="cp-decide__err">
          <b>没提交成功</b> · {err}
        </div>
      )}
      <div className="cp-decide__basis">拒接仅经理可执行（reject 强制 manager）；要补资料需列出缺失项清单。经手身份 {actorForRole(role)}</div>
    </div>
  );
}

export default function AdmissionDecisionBar({
  caseId,
  caseStatus,
  role,
  onDecided,
}: {
  caseId: string;
  caseStatus: string;
  role: Role;
  onDecided?: () => void;
}) {
  const [mode, setMode] = useState<null | "approve" | "reject">(null);
  // ApproveQuoteDecision executors={manager}；RejectOrRequestMoreInfo executors={manager,compliance}
  // ——驾驶舱无 compliance 角色可选，故两按钮在此都按 manager-only 预判（ops 置灰）。
  const canDecide = role === "manager";

  if (CASE_TERMINAL.has(caseStatus)) return null; // 已终态（approved/quote_with_conditions/rejected）：无待拍板动作

  // 提交成功后收起子表单、退回二按钮初始态——不能只指望"案件转终态后 CASE_TERMINAL 短路隐藏整个
  // 组件"来间接复位（RejectForm 的 more_info 结果是 needs_more_info，非终态，那条路径不会被上面
  // 那行短路，子表单会带着"提交中…"的僵死 busy 态留在原地——ApproveForm/RejectForm 提交成功分支
  // 都没有回落 setBusy(false)，事在此处统一收口，不必每个子表单各自处理）。
  const handleDecided = () => {
    setMode(null);
    onDecided?.();
  };

  return (
    <div className="cp-action">
      <div className="cp-action__t">
        <Icon name="stamp" size={13} /> 动作区
      </div>
      {!canDecide ? (
        <div className="cp-decide">
          <div className="cp-decide__row">
            <button className="cp-decide-btn" disabled>
              批准报价
            </button>
            <button className="cp-decide-btn" disabled>
              驳回 / 要补件
            </button>
          </div>
          <StateHint
            kind="no-permission"
            compact
            title="需经理角色才能拍板"
            // 「当前是X」用真实当前角色（原写死"运营 ops"，财务等角色进来会显错）——目标"老板 manager"
            // 是权限事实（ApproveQuoteDecision executors=[manager]）不随当前角色变。
            roleHint={`顶栏切到「老板 manager」才能批准报价 / 驳回 / 要补件；当前是${roleShort(role)}，只能看不能批。`}
          />
        </div>
      ) : mode === null ? (
        <div className="cp-decide">
          <div className="cp-decide__row">
            <button className="cp-decide-btn cp-decide-btn--approve" disabled={caseStatus !== "priced"} onClick={() => setMode("approve")}>
              批准报价
            </button>
            <button className="cp-decide-btn cp-decide-btn--reject" onClick={() => setMode("reject")}>
              驳回 / 要补件
            </button>
          </div>
          {caseStatus !== "priced" && (
            <div className="cp-decide__basis">批准报价需案件先走到"已计价"（当前状态：{caseStatus}）；驳回/要补件不受此限。</div>
          )}
        </div>
      ) : mode === "approve" ? (
        <ApproveForm caseId={caseId} role={role} onCancel={() => setMode(null)} onDecided={handleDecided} />
      ) : (
        <RejectForm caseId={caseId} role={role} onCancel={() => setMode(null)} onDecided={handleDecided} />
      )}
      <div className="cp-action__note">
        批准报价 / 驳回或要补件在此直接拍板（人类决策通道，实时回写并留痕）。建案、预审、方案设计与成本核算（B1-B4）仍需去 Streamlit 操作台发起。
      </div>
    </div>
  );
}
