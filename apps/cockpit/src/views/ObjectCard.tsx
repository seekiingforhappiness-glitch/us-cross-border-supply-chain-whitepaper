import { useEffect, useState, type CSSProperties } from "react";
import {
  fetchObject,
  fetchObjectsByFilter,
  fetchRiskImpact,
  formatUsd,
  isMasked,
  linksForType,
  MASK_TEXT,
  traverse,
  type ObjectFields,
  type ObjectRef,
  type OntologyLink,
  type PaymentImpactRow,
  type Role,
  type UsableLink,
} from "../api";
import Icon from "../components/Icons";
import AdmissionDecisionBar from "./AdmissionDecisionBar";
import { DecisionButtons, PAY_ANCHOR_RULES, payRowStatus, REF_TYPE_CN, REF_TYPE_OBJ } from "./ImpactPanel";
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

// 轮2·P1-6：对象卡抽屉（fixed 覆盖层）此前 top:0 铺满全高，其头部 + scrim 会盖住顶栏的角色/世界切换钮
// （实测 elementFromPoint 落在 cp-drawer__type 上）——用户点顶栏"老板 manager"其实点在抽屉头上，角色没切、
// 锁定提示自然不刷新（"须关闭重开"的真因是点击被吞，不是 role 传播失效）。把 scrim 与抽屉整体下移到顶栏
// 之下即让顶栏切换钮恢复可点。用组件内常量而非改 styles.css（本单不可碰 styles.css），46=.cp-topbar 高度。
const TOPBAR_H = 46;
const DRAWER_INSET: CSSProperties = { top: TOPBAR_H };

// 轮2·P1-3：RiskEvent 处置状态自相矛盾修复用——风险仍显"未处置/已受理/处置中"（非终态）却已有推进中的
// 关联处置任务时，在处置状态字段下补一句派生解释（任务完成≠风险自动关闭，关闭需运营在影响面板确认）。
const RISK_NONTERMINAL = new Set(["open", "acknowledged", "mitigating"]);

type DispoTask = { id: string; status: string; approval: string };
type DispoHint =
  | { state: "none" }
  | { state: "loading" }
  | { state: "static" } // 有关联任务但逐个状态取不到 → 静态降级解释（不编造具体任务号/阶段）
  | { state: "tasks"; rep: DispoTask }; // 取到最推进的关联任务

// 选出最"推进"的关联任务（已完成 > 已批准 > 处理中/已派单）。仅待审批/已作废的任务不构成
// "任务已推进却仍未处置"的矛盾 → 返回 null（不提示，避免把正常态说成矛盾）。
function pickAdvancedTask(tasks: DispoTask[]): DispoTask | null {
  const rank = (t: DispoTask) =>
    t.status === "done" ? 3 : t.approval === "approved" ? 2 : t.status === "in_progress" || t.status === "assigned" ? 1 : 0;
  let best: DispoTask | null = null;
  let bestR = 0;
  for (const t of tasks) {
    const r = rank(t);
    if (r > bestR) {
      bestR = r;
      best = t;
    }
  }
  return bestR > 0 ? best : null;
}

function dispoPhase(t: DispoTask): string {
  if (t.status === "done") return "已完成";
  if (t.approval === "approved") return "已批准，执行中";
  return "处理中";
}

// 派生提示样式：沿用既有琥珀语义色变量（--sev-amber/--ink-2），不新增 styles.css（本单不可碰）。
const DISPO_HINT_STYLE: CSSProperties = {
  margin: "6px 2px 2px",
  padding: "6px 9px",
  fontSize: "11px",
  lineHeight: 1.5,
  color: "var(--ink-2)",
  background: "color-mix(in srgb, var(--sev-amber) 8%, transparent)",
  border: "1px solid var(--sev-amber-dim)",
  borderRadius: "6px",
};

// 轮3 余量①（客户卡"哪张订单出险"）：Customer 关系区本体只声明 customer_places（→ 销售订单）
// 一条关系，没有 Customer↔RiskEvent 直接关系可 traverse（侦察结论，ontology/control-tower-
// ontology.json links[] 核对过）。R19/R21（付款锚风险，仅这两条规则会产出付款归并行——
// apps/api/cockpit.py::cockpit_risk_impact 对非付款锚风险恒返回 rows=[]，其余规则族即使能
// traverse 到也永远渲染不出東西，故不做那条路）本身锚在 payment_id，订单行/客户都要经
// /cockpit/risk-impact 按 payment→单据→对手方解出，前端无法反向定位"该风险是不是这个客户的"
// ——只能把 R19/R21 全量候选（现查 GET /objects/RiskEvent?rule_id=R19|R21）逐个核对
// counterparty_id 是否命中当前客户。这池子大小只取决于系统级资金流异常总量、不随本客户订单量
// 增长（F1 语义决定"逾期应收/付款异常"是稀发事件——当前验证世界 15 条、模拟世界 21 条，见
// engine/finance_rules.py 阈值）。规格建议"候选>5 只查前5"防的是随客户复杂度线性增长的雪崩；
// 但这池子固定且小，卡 5 反而会把排名靠后的客户（如模拟世界 CUS-0002 排第 10）误判成"无风险"
// （假阴性，比多查几个本地 SQLite 读判断更糟）——改用一个远高于现状的硬顶兜底，防未来数据
// 规模真的失控，不做字面的"5"。
const PAY_RISK_CHECK_CAP = 60;

// C·P1（轮3 三人齐报"QUAL/RSK/付款记录 chip 死链 vs 任务徽标可点"）：关系区邻居 chip 原是裸
// span+onClick——鼠标可点但键盘不可达、无障碍树里不存在（沿无障碍树驱动的测试与读屏用户都会把它
// 判成"死链"；对比 AI 区任务链接是真 <button>，可点性因此不一致）。对象读端点 /objects/{type}/{id}
// 覆盖本体全部 35 类（apps/api/main.py TABLE_BY_TYPE 全集，已核验无缺），故关系区邻居 chip 一律
// 真可点：补 role="button"+tabIndex+Enter/Space，键盘焦点态走全局 :focus-visible；纯占位"+N 更多"
// 保持 is-plain 不可点（可点/不可点的视觉区分见 styles.css .cp-neighbor.is-plain）。
function NeighborChip({ id, title, onOpen }: { id: string; title: string; onOpen: () => void }) {
  return (
    <span
      className="cp-neighbor"
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      title={title}
    >
      {id}
    </span>
  );
}

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
  const [dispo, setDispo] = useState<DispoHint>({ state: "none" }); // P1-3 处置状态派生提示
  const [custRiskRows, setCustRiskRows] = useState<PaymentImpactRow[] | null>(null); // 轮3 余量①：客户"出险订单"

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

  // P1-3：RiskEvent 处置状态派生提示。仅当风险自身仍非终态（处置状态显"未处置/已受理/处置中"）才查——
  // 已解决/已升级的风险不存在"状态矛盾"。有关联处置任务且其已推进（批准/完成/在办）→ 取最推进的一条作提示；
  // 无关联任务 → 不提示（"未处置"此时不矛盾）；traverse 成功但逐个任务状态取不到 → 静态降级（如实说有任务
  // 在办但不编造具体状态）；traverse 本身失败 → 不提示（无法确认有任务，绝不凭空造矛盾）。数据全走既有
  // links/traverse 端点（同 ImpactPanel 用法），不新增后端端点。
  const riskStatusForDispo =
    target.type === "RiskEvent" && fields && typeof fields.status === "string" ? fields.status : null;
  useEffect(() => {
    if (target.type !== "RiskEvent" || riskStatusForDispo === null || !RISK_NONTERMINAL.has(riskStatusForDispo)) {
      setDispo({ state: "none" });
      return;
    }
    let cancelled = false;
    setDispo({ state: "loading" });
    traverse("RiskEvent", target.id, "task_handles_risk", role)
      .then(async (t) => {
        if (cancelled) return;
        const ids = t.neighbor_ids.slice(0, 8);
        if (ids.length === 0) {
          setDispo({ state: "none" });
          return;
        }
        const tasks = await Promise.all(
          ids.map((id) =>
            fetchObject("Task", id, role)
              .then((f) => ({ id, status: String(f.status ?? ""), approval: String(f.approval_status ?? "") }))
              .catch(() => null),
          ),
        );
        if (cancelled) return;
        const ok = tasks.filter((x): x is DispoTask => x !== null);
        const rep = pickAdvancedTask(ok);
        if (rep) setDispo({ state: "tasks", rep });
        else if (ok.length === 0) setDispo({ state: "static" }); // 有关系但任务状态全取不到
        else setDispo({ state: "none" }); // 关联任务都仅待审批/已作废 → 不构成矛盾
      })
      .catch(() => !cancelled && setDispo({ state: "none" })); // 关系遍历失败：不确认有任务，不提示
    return () => {
      cancelled = true;
    };
  }, [target, role, riskStatusForDispo]);

  // 轮3 余量①：客户卡"哪张订单出险"。仅 Customer 类型查——现取 R19/R21 全量候选（小池子，见上方
  // PAY_RISK_CHECK_CAP 注释），逐个调 fetchRiskImpact（后端已实现的 payment→单据→对手方归并），
  // 只留 counterparty_id 命中当前客户的行。查无归并行、候选池为空、或任一环节失败＝诚实静默
  // （不渲染不报错）——绝不为了"有内容"而编造或降级展示。
  useEffect(() => {
    if (target.type !== "Customer") {
      setCustRiskRows(null);
      return;
    }
    let cancelled = false;
    setCustRiskRows(null);
    (async () => {
      try {
        const lists = await Promise.all(
          [...PAY_ANCHOR_RULES].map((rule) =>
            fetchObjectsByFilter("RiskEvent", { rule_id: rule }, role, 300).catch(() => null),
          ),
        );
        if (cancelled) return;
        const candidates = lists
          .flatMap((r) => r?.items ?? [])
          .filter((it) => RISK_NONTERMINAL.has(String(it.status)))
          .slice(0, PAY_RISK_CHECK_CAP);
        if (candidates.length === 0) {
          if (!cancelled) setCustRiskRows(null);
          return;
        }
        const impacts = await Promise.all(
          candidates.map((c) => fetchRiskImpact(String(c.risk_event_id), role).catch(() => null)),
        );
        if (cancelled) return;
        const rows: PaymentImpactRow[] = [];
        const seen = new Set<string>();
        for (const imp of impacts) {
          if (!imp || imp.anchor !== "payment") continue;
          for (const row of imp.rows) {
            if (row.counterparty_type === "customer" && row.counterparty_id === target.id && !seen.has(row.payment_id)) {
              seen.add(row.payment_id);
              rows.push(row);
            }
          }
        }
        setCustRiskRows(rows.length > 0 ? rows : null);
      } catch {
        if (!cancelled) setCustRiskRows(null); // 诚实静默：链路任一环失败都不报错、不渲染
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [target, role]);

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
      <div className="cp-drawer-scrim" style={DRAWER_INSET} onClick={onClose} />
      <aside className="cp-drawer" style={DRAWER_INSET} role="dialog" aria-label={`${OBJECT_TYPE_CN[target.type] ?? target.type} ${target.id}`}>
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
                  {/* P1-3 处置状态派生提示：紧跟含"处置状态(status)"的字段组之下，解释"任务已推进却仍显未处置"
                      的业务语义（任务完成≠风险自动关闭），并把在办任务号做成可点链接。 */}
                  {target.type === "RiskEvent" &&
                    dispo.state !== "none" &&
                    groupedFields[g].some(([k]) => k === "status") && (
                      <div style={DISPO_HINT_STYLE}>
                        {dispo.state === "loading" ? (
                          <span style={{ color: "var(--ink-3)" }}>查关联处置任务…</span>
                        ) : dispo.state === "tasks" ? (
                          <>
                            处置任务{" "}
                            <NeighborChip
                              id={dispo.rep.id}
                              title={`打开任务 ${dispo.rep.id}`}
                              onOpen={() => onOpenObject({ type: "Task", id: dispo.rep.id })}
                            />{" "}
                            {dispoPhase(dispo.rep)}——任务完成不等于风险自动关闭，风险关闭需运营在影响面板动作区确认。
                          </>
                        ) : (
                          <>
                            该风险已有关联处置任务在办——“未处置”指风险尚未由运营在影响面板动作区确认关闭（任务完成不等于风险自动关闭）。
                          </>
                        )}
                      </div>
                    )}
                </div>
              ))}

              <div className="cp-drawer__section-title">关系（{usable.length} 条可走）</div>
              {/* 轮3 余量①：客户"出险订单"提示——只在真找到归并行时才现身（诚实静默，查无/失败不渲染）。 */}
              {target.type === "Customer" && custRiskRows && custRiskRows.length > 0 && (
                <div style={DISPO_HINT_STYLE}>
                  <div style={{ fontWeight: 600, color: "var(--ink-1)", marginBottom: 4 }}>
                    <Icon name="warn" size={11} /> 出险订单 · {custRiskRows.length} 笔付款
                  </div>
                  {custRiskRows.map((r) => {
                    const st = payRowStatus(r);
                    const refObj = REF_TYPE_OBJ[r.ref_type];
                    return (
                      <div key={r.payment_id} style={{ display: "flex", alignItems: "baseline", gap: 6, margin: "3px 0", flexWrap: "wrap" }}>
                        {refObj ? (
                          <NeighborChip
                            id={r.ref_id}
                            title={`打开${REF_TYPE_CN[r.ref_type] ?? r.ref_type} ${r.ref_id}`}
                            onOpen={() => onOpenObject({ type: refObj, id: r.ref_id })}
                          />
                        ) : (
                          <span className="num">{r.ref_id}</span>
                        )}
                        <span className="num">{formatUsd(r.amount_usd)}</span>
                        {/* .cp-table td.neg 是表格限定选择器，这里是裸 span，改内联色避免样式不生效
                            （不新增 styles.css 规则——本单不可碰 styles.css）。 */}
                        <span style={st.neg ? { color: "var(--sev-red)" } : undefined}>{st.text}</span>
                      </div>
                    );
                  })}
                </div>
              )}
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
                              <NeighborChip
                                key={id}
                                id={id}
                                title={`打开${OBJECT_TYPE_CN[l.neighborType] ?? l.neighborType} ${id}`}
                                onOpen={() => onOpenObject({ type: l.neighborType, id })}
                              />
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
