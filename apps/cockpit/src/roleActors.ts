import type { Role } from "./api";

// 角色清单 = 前端已适配角色的**单一数据源**（顶栏选择器 + 灰态"当前是X"提示 + 人类决策通道 X-Actor）。
//
// 为什么合成一张表（白话）：角色信息此前散在三处——TopBar 的钮文案/粒度说明、各灰态提示里写死的
// "当前是运营 ops"、以及这里的 actor 映射。散着就会像 finance 接入前那样：切了财务，提示却还说"当前是
// 运营 ops"。归一到 ROLES 后，加一个角色 = **加一行**，顶栏钮/粒度文案/提示短名/审计身份一次配齐。
//
// 批D（2026-07-19，V22①"其后 cs/procurement/compliance/sales"）：本体 7 角色全部接入 UI——
// 顶栏能切进去的角色，数据范围均经 API X-Role 同源验证（本文件 hint 文案依据侦察 ontology
// sensitiveFieldRules + agent/tools.py _can_see_cost 写就，见各行注释；apps/api/test_cockpit.py
// 有对应断言钉死）。cs/procurement 额外接入协调写权限（AiWorkflow.tsx COORD_UI_ROLES）；
// compliance/sales 与协调权限组（COORD_PERMS）无关，驾驶舱内保持只读。
//
// 权限边界（宪法不变量 5）：这张表**不含任何权限/脱敏规则**——谁能看多少钱、哪些字段掩码，全由
// apps/api 按 X-Role 同源执行；前端只透传角色头 + 呈现返回值。label/hint 是纯展示文案，不是权限声明。
export interface RoleMeta {
  id: Role;
  /** 顶栏切换钮文案（中文名 + 英文角色码，与既有"老板 manager"体例一致）。 */
  label: string;
  /** 灰态提示里"当前是X"用的短名（同 label，独立字段留待将来两者需分开时不改调用点）。 */
  short: string;
  /** 顶栏粒度说明文案（该角色能看到什么颗粒度的白话，纯展示，非权限规则）。 */
  hint: string;
  /**
   * demo 演员 id（人类决策通道 POST /decisions 的 X-Actor 来源）——审计留痕『谁批的』+ maker-checker
   * 靠它挡『提案人自己批自己』。取自后端真实演员（app/actions.py DEMO_ROSTER / datagen/seed_demo_ops.py），
   * 取各角色 region=US 的规范首位（非 named 变体）。⚠ 原型级身份，真实系统换 SSO / OIDC 登录主体。
   *
   * 批D 侦察缺口（如实记录，未编造）：DEMO_ROSTER 里**没有 compliance / sales 的 Owner 条目**
   * （app/actions.py 逐行核对确认）——这两个角色本批在驾驶舱只读（不在 COORD_PERMS，无冻结动作
   * executors），其 actor 值只用于展示文案（如"经手身份 X"）、从不会真正进入写请求（写按钮均按
   * role==='ops'/'manager' 等条件单独把关，与此值无关）。这里按既有 `u-<角色码>-us` 命名惯例合成
   * 展示占位符，与真实 roster 条目视觉一致但**无 DEMO_ROSTER 背书**；后端 app/data_scope.py
   * resolve_actor() 对此早有文档化容错（"无匹配 → (None, DEFAULT_DEMO_REGION)，只影响视图"）。
   * 若未来给这两角色开写权限，须先在 DEMO_ROSTER 补真实 Owner，此占位符须同步替换。
   */
  actor: string;
}

export const ROLES: RoleMeta[] = [
  { id: "manager", label: "老板 manager", short: "老板 manager", hint: "汇总粒度 · 金额可见", actor: "u-manager-us" },
  { id: "ops", label: "运营 ops", short: "运营 ops", hint: "可操作粒度 · 金额脱敏", actor: "u-ops-us" },
  // finance（V22①）：财务粒度——费用异常/应收应付/净流出金额可见（后端 _can_see_cost=finance），
  // 但合规专属字段（Supplier.uflpa_risk_flag 等）对财务仍掩码，一切以 API X-Role 返回值为准。
  { id: "finance", label: "财务 finance", short: "财务 finance", hint: "财务粒度 · 费用与应收可见", actor: "u-fin-us" },
  // cs（批D）：客服粒度——本体 sensitiveFieldRules 声明 Customer.tier 对 cs 可见（客户分层，R1 严重度
  // 判定依据），但 cs 不在 _COST_VISIBLE（agent/tools.py），钱区/成本情景/发票等 *_usd 字段仍掩码。
  // cs 在 COORD_PERMS.ManageCoordination 内，本批同步接入协调流写操作（催办/记回应/升级/达成/谈崩）。
  { id: "cs", label: "客服 cs", short: "客服 cs", hint: "客服粒度 · 客户分层可见 · 金额脱敏", actor: "u-cs-us" },
  // procurement（批D）：本体 sensitiveFieldRules 8 条规则逐条核对，无一条 visibleTo 含 procurement——
  // 无专属可见敏感字段；但 V21① 自队金额例外（own_team_amount_visible）对任何角色通用，指派给采购
  // 团队的待批提案金额对采购自己可见（他队仍掩码）。procurement 在 COORD_PERMS 内，本批同步接入
  // 协调流写操作。actor 取自 DEMO_ROSTER 真实条目（app/actions.py，P4 采购 demo owner）。
  { id: "procurement", label: "采购 procurement", short: "采购 procurement", hint: "采购粒度 · 自队提案金额可见 · 他队与合规字段掩码", actor: "u-proc-us" },
  // compliance（批D）：本体 sensitiveFieldRules 声明 Supplier.uflpa_risk_flag 对 compliance 可见
  // （供应商强迫劳动合规旗标），AdmissionCase 准入域可读（ADMISSION_READ_ROLES 含 compliance）；
  // 但不在 _COST_VISIBLE，钱区/成本情景仍掩码。compliance 不在 COORD_PERMS，驾驶舱协调流保持只读。
  { id: "compliance", label: "合规 compliance", short: "合规 compliance", hint: "合规粒度 · 供应商合规与准入可见 · 金额脱敏", actor: "u-compliance-us" },
  // sales（批D）：本体 sensitiveFieldRules 8 条规则逐条核对，无一条 visibleTo 含 sales——当前**无
  // 任何专属可见敏感字段**（如实记录，非猜测；sales 的准入建案权 CreateAdmissionCase 是写动作非
  // 读可见性）。ADMISSION_READ_ROLES 含 sales（AI 工具面准入域只读），钱区/合规专属字段掩码。
  // sales 不在 COORD_PERMS，驾驶舱协调流保持只读。
  { id: "sales", label: "销售 sales", short: "销售 sales", hint: "销售粒度 · 准入域可读 · 金额与合规字段脱敏", actor: "u-sales-us" },
];

const ROLE_BY_ID = Object.fromEntries(ROLES.map((r) => [r.id, r])) as Record<Role, RoleMeta>;

/** 角色元数据（label / short / hint / actor）。传入的 role 恒在 ROLES 内（Role 类型即 ROLES 的并集）。 */
export function roleMeta(role: Role): RoleMeta {
  return ROLE_BY_ID[role];
}

/** 顶栏钮/正式场合的角色显示名（如"财务 finance"）。 */
export function roleLabel(role: Role): string {
  return ROLE_BY_ID[role].label;
}

/** 灰态提示"当前是X"用的角色短名（当前与 label 相同）。 */
export function roleShort(role: Role): string {
  return ROLE_BY_ID[role].short;
}

/** 当前角色对应的 demo 演员 id（人类决策通道 X-Actor）。原型级，真实系统换 SSO。 */
export function actorForRole(role: Role): string {
  return ROLE_BY_ID[role].actor;
}
