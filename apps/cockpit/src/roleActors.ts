import type { Role } from "./api";

// 角色 → demo 演员身份 id 映射（A-1 人类决策通道 POST /decisions 的 X-Actor 来源）。
//
// 为什么需要它（白话）：驾驶舱顶栏切的是"角色"（manager/ops），但人类决策通道要的是"具体是谁"
// ——审批要留痕『谁批的』，还要靠真实身份挡住『提案人自己批自己』（maker-checker 双人复核）。
// 这里把当前角色映射到一个 demo 演员 id 带给后端。
//
// 这些 id 取自后端真实存在的演员（app/actions.py DEMO_ROSTER / datagen/seed_demo_ops.py）：
//   manager → u-manager-us（唯一的经理演员——能审批处置/准入、拒接、也是拍板人）
//   ops     → u-ops-us（运营演员——能关闭风险事件 CloseRiskEvent）
// 为什么这么配 maker-checker 才走得通：seeded 待批任务的提案人都是 ops 系（u-ops-us / u-ops-cn /
// u-ops-us-amelia …），故 manager（u-manager-us）审批时"提案人≠审批人"天然成立，双人复核不会误伤。
//
// ⚠ 原型级身份，真实系统请换成 SSO / OIDC 登录主体（当前会话用户的真实账号），不要用这张写死的表。
export const ROLE_ACTOR: Record<Role, string> = {
  manager: "u-manager-us",
  ops: "u-ops-us",
};

/** 当前角色对应的 demo 演员 id（人类决策通道 X-Actor）。原型级，真实系统换 SSO。 */
export function actorForRole(role: Role): string {
  return ROLE_ACTOR[role];
}
