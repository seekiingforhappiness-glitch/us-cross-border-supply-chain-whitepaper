import type { Role } from "./api";

// 角色清单 = 前端已适配角色的**单一数据源**（顶栏选择器 + 灰态"当前是X"提示 + 人类决策通道 X-Actor）。
//
// 为什么合成一张表（白话）：角色信息此前散在三处——TopBar 的钮文案/粒度说明、各灰态提示里写死的
// "当前是运营 ops"、以及这里的 actor 映射。散着就会像 finance 接入前那样：切了财务，提示却还说"当前是
// 运营 ops"。归一到 ROLES 后，加一个角色 = **加一行**，顶栏钮/粒度文案/提示短名/审计身份一次配齐。
//
// 本表只列**前端已适配**的角色（本批 = manager/ops/finance），不虚列后端已有但 UI 未做的
// cs/procurement/compliance/sales——顶栏只给能真正切进去、数据范围经 API X-Role 验证过的角色，避免
// 切了却半适配。后续接入某角色时在此追加一行即可（顺带在 api.ts 的 Role 并集补该字符串）。
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
   */
  actor: string;
}

export const ROLES: RoleMeta[] = [
  { id: "manager", label: "老板 manager", short: "老板 manager", hint: "汇总粒度 · 金额可见", actor: "u-manager-us" },
  { id: "ops", label: "运营 ops", short: "运营 ops", hint: "可操作粒度 · 金额脱敏", actor: "u-ops-us" },
  // finance（V22①）：财务粒度——费用异常/应收应付/净流出金额可见（后端 _can_see_cost=finance），
  // 但合规专属字段（Supplier.uflpa_risk_flag 等）对财务仍掩码，一切以 API X-Role 返回值为准。
  { id: "finance", label: "财务 finance", short: "财务 finance", hint: "财务粒度 · 费用与应收可见", actor: "u-fin-us" },
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
