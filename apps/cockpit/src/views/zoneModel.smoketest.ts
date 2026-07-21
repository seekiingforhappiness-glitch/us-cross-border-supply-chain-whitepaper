// V24 ROLE_WALL 角色化首屏配置完整性冒烟测试。
//
// 为什么长这样（白话）：apps/cockpit 未接任何测试框架（见 StateHint.smoketest.tsx 说明，"零新依赖"
// 约束下装不了 vitest/jest）。ROLE_WALL / ROLE_FOCUS 是纯声明式数据，没有渲染可测——退而求其次用
// 手写最小断言（不借 node:assert，本项目未装 @types/node）钉死配置的**结构不变量**：七角色全配置、
// 区块 id 全合法、manager=现七区序、各角色首屏各不相同、焦点源合法。本文件被 tsconfig include:["src"]
// 覆盖，随 tsc --noEmit / npm run build 一起类型检查；真正"跑断言"手动一步（同 StateHint 冒烟）：
//
//   cd apps/cockpit && npx esbuild src/views/zoneModel.smoketest.ts \
//     --bundle --platform=node --format=cjs --outfile=/tmp/zonewall-smoke.cjs \
//     && node /tmp/zonewall-smoke.cjs
//
// （esbuild 是 vite 既有间接依赖，非新增；--format=cjs 同 StateHint 冒烟避免 ESM 动态 require 坑。）

import type { Role, Zone, ZoneId } from "../api";
import { ROLES } from "../roleActors";
import {
  FIXED_ORDER_IDS,
  roleHeadline,
  ROLE_FOCUS,
  ROLE_WALL,
  roleWallZoneIds,
  VALID_BLOCK_IDS,
  type FocusSourceId,
  type WallBlockId,
} from "./zoneModel";

let passed = 0;
function check(label: string, ok: boolean) {
  if (!ok) throw new Error(`[FAIL] ${label}`);
  passed += 1;
}

const ROLE_IDS = ROLES.map((r) => r.id) as Role[];
const ZONE_ID_SET = new Set<ZoneId>(FIXED_ORDER_IDS);
const VALID_FOCUS: ReadonlySet<FocusSourceId> = new Set<FocusSourceId>(["decisions", "cash", "alert", "admission"]);

// ── 1. 七角色全配置（ROLE_WALL / ROLE_FOCUS 键集 = roleActors.ROLES 全集，不多不少）──────────
{
  const wallKeys = Object.keys(ROLE_WALL).sort();
  const focusKeys = Object.keys(ROLE_FOCUS).sort();
  const roleKeys = [...ROLE_IDS].sort();
  check("ROLE_WALL 覆盖且仅覆盖七角色（与 roleActors.ROLES 同集）", JSON.stringify(wallKeys) === JSON.stringify(roleKeys));
  check("ROLE_FOCUS 覆盖且仅覆盖七角色", JSON.stringify(focusKeys) === JSON.stringify(roleKeys));
  check("FIXED_ORDER_IDS 恰为七区", FIXED_ORDER_IDS.length === 7 && ZONE_ID_SET.size === 7);
  check("VALID_BLOCK_IDS = 七区 + admission（共 8）", VALID_BLOCK_IDS.size === 8 && VALID_BLOCK_IDS.has("admission"));
}

// ── 2. 每角色首屏区块结构合法（id 合法、非空标题、drill 是合法区、kind 与 id/zone 自洽）──────────
for (const role of ROLE_IDS) {
  const blocks = ROLE_WALL[role];
  check(`${role}：首屏非空`, blocks.length > 0);
  const ids = new Set<WallBlockId>();
  for (const b of blocks) {
    check(`${role}：区块 id「${b.id}」在合法集内`, VALID_BLOCK_IDS.has(b.id));
    check(`${role}：区块「${b.id}」标题非空`, typeof b.title === "string" && b.title.trim().length > 0);
    check(`${role}：区块「${b.id}」下钻目标是合法区`, ZONE_ID_SET.has(b.drill));
    check(`${role}：区块 id「${b.id}」在本角色内不重复`, !ids.has(b.id));
    ids.add(b.id);
    if (b.kind === "zone") {
      check(`${role}：zone 块「${b.id}」zone 字段 = id 且为合法区`, b.zone === b.id && ZONE_ID_SET.has(b.zone));
    } else {
      check(`${role}：admission 块 id 恒为 "admission"、无 zone 字段`, b.id === "admission" && b.zone === undefined);
      check(`${role}：admission 块下钻落 customers（准入漏斗数据家）`, b.drill === "customers");
    }
  }
}

// ── 3. manager = 现七区序（FIXED_ORDER_IDS），全为 zone 块（回归护栏：manager 墙不动）──────────
{
  const managerIds = ROLE_WALL.manager.map((b) => b.id);
  check("manager 首屏 = 现七区序（FIXED_ORDER_IDS 逐位相等）", JSON.stringify(managerIds) === JSON.stringify(FIXED_ORDER_IDS));
  check("manager 首屏全为 zone 块（无 admission）", ROLE_WALL.manager.every((b) => b.kind === "zone"));
}

// ── 4. 六角色首屏各不相同（Daniel 核心诉求"各角色页面完全一样"的回归护栏）──────────────────────
{
  const seqs = ROLE_IDS.map((r) => ROLE_WALL[r].map((b) => b.id).join(">"));
  const distinct = new Set(seqs);
  check("七角色首屏区块序两两各不相同", distinct.size === ROLE_IDS.length);
  // 另证：非 manager 角色首屏至少与 manager 不同（不再是"老板壳套角色皮"）
  const managerSeq = ROLE_WALL.manager.map((b) => b.id).join(">");
  for (const r of ROLE_IDS) {
    if (r === "manager") continue;
    check(`${r} 首屏区块序 ≠ manager（不再千篇一律）`, ROLE_WALL[r].map((b) => b.id).join(">") !== managerSeq);
  }
}

// ── 5. 焦点源配置合法（ROLE_FOCUS 每源合法、manager 保持改前 [decisions,cash,alert]）──────────
{
  check("manager 焦点源 = 改前 [decisions,cash,alert]（byte-identical 护栏）", JSON.stringify(ROLE_FOCUS.manager) === JSON.stringify(["decisions", "cash", "alert"]));
  for (const role of ROLE_IDS) {
    const sources = ROLE_FOCUS[role];
    check(`${role}：焦点源非空`, sources.length > 0);
    for (const s of sources) check(`${role}：焦点源「${s}」合法`, VALID_FOCUS.has(s));
    // admission 焦点只该出现在准入主责角色（compliance/sales）
    if (sources.includes("admission")) {
      check(`${role}：admission 焦点仅限准入主责角色`, role === "compliance" || role === "sales");
    }
  }
}

// ── 6. roleWallZoneIds 自洽：manager = 全七区；返回集 ⊆ 合法区且与配置里 zone 块一致 ──────────
{
  check("roleWallZoneIds(manager) = 全七区（focus 告警池收窄对 manager 无损，护栏）", roleWallZoneIds("manager").size === 7);
  for (const role of ROLE_IDS) {
    const zoneIds = roleWallZoneIds(role);
    for (const zid of zoneIds) check(`${role}：roleWallZoneIds 元素「${zid}」是合法区`, ZONE_ID_SET.has(zid));
    const zoneBlockCount = ROLE_WALL[role].filter((b) => b.kind === "zone").length;
    check(`${role}：roleWallZoneIds 数量 = 该角色 zone 块数`, zoneIds.size === zoneBlockCount);
  }
}

// ── 7. V24② caliber 口径覆盖配置：全表恰两处（compliance 的 suppliers=合规 / fulfillment=清关），别处无 ──
{
  const calibered: { role: Role; id: WallBlockId; caliber?: string }[] = [];
  for (const role of ROLE_IDS) for (const b of ROLE_WALL[role]) if (b.caliber) calibered.push({ role, id: b.id, caliber: b.caliber });
  check("caliber 覆盖恰 2 处（不误伤其它区卡）", calibered.length === 2);
  check("全部 caliber 都在 compliance 角色下", calibered.every((c) => c.role === "compliance"));
  const supC = calibered.find((c) => c.id === "suppliers");
  const fulC = calibered.find((c) => c.id === "fulfillment");
  check("compliance 供应商块 caliber=compliance", supC?.caliber === "compliance");
  check("compliance 清关卡点（fulfillment）块 caliber=customs", fulC?.caliber === "customs");
  // 采购/ops 的供应商卡、cs/sales 的履约卡不带 caliber（headline 仍是各自默认口径，与其标题相符）
  for (const role of ["procurement", "ops"] as Role[]) {
    const sup = ROLE_WALL[role].find((b) => b.id === "suppliers");
    check(`${role} 供应商块无 caliber（保持绩效/交期口径）`, sup !== undefined && sup.caliber === undefined);
  }
  for (const role of ["cs", "sales"] as Role[]) {
    const ful = ROLE_WALL[role].find((b) => b.id === "fulfillment");
    check(`${role} 履约块无 caliber（履约准交/客户履约标题本就与 OTD 相符）`, ful !== undefined && ful.caliber === undefined);
  }
}

// ── 8. roleHeadline 行为（A1 我组计数 / A2 合规口径 / manager byte-identical 返 null）───────────────
{
  const zDecisions = (pend: { assignee_role: string | null }[], total: number): Zone => ({
    zone: "decisions", headline_label: "待批提案", headline_value: total, trend: null,
    alert_count: 0, detail: { pending_proposals: pend, pending_total: total },
  });
  // A1：非 manager 的 decisions 卡 → 我组计数 + 全司副行；manager → null（走默认，byte-identical）
  const pend3 = [{ assignee_role: "procurement" }, { assignee_role: "procurement" }, { assignee_role: "ops" }];
  const rhProc = roleHeadline({ id: "decisions" }, zDecisions(pend3, 3), "procurement");
  check("A1 procurement 我组待批=2（现算）", rhProc?.text === "2" && rhProc?.label === "我组待批提案");
  check("A1 procurement 副行如实并存全司 3", (rhProc?.sublines?.[0]?.text ?? "").includes("全司 3 条"));
  check("A1 manager 的 decisions 卡不覆盖（返 null=区默认 byte-identical）", roleHeadline({ id: "decisions" }, zDecisions(pend3, 3), "manager") === null);
  // A1 诚实门：列表被 cap 截断（pend.length < total）→ 退回全司大数字、不谎报我组
  const rhCapped = roleHeadline({ id: "decisions" }, zDecisions(pend3, 60), "finance");
  check("A1 截断诚实门：退回全司 60、标签点明暂无法拆", rhCapped?.text === "60" && (rhCapped?.label ?? "").includes("全司待批"));

  // A2：合规供应商卡 caliber → UFLPA 标记家数为主数字；无 caliber 的供应商卡返 null（走默认交期口径）
  const zSup = (comp: unknown): Zone => ({
    zone: "suppliers", headline_label: "交期达成率", headline_value: 0.822, trend: null,
    alert_count: 0, detail: { compliance_dimension: comp },
  });
  const compAvail = { available: true, uflpa_flagged_total: 2, qual_abnormal_total: 1, suppliers_total: 10 };
  const rhComp = roleHeadline({ id: "suppliers", caliber: "compliance" }, zSup(compAvail), "compliance");
  check("A2 合规卡主数字=UFLPA 标记家数 2", rhComp?.text === "2" && (rhComp?.label ?? "").includes("UFLPA"));
  check("A2 合规卡副行含资质异常家数", (rhComp?.sublines?.[0]?.text ?? "").includes("资质异常 1 家"));
  const rhZeroUflpa = roleHeadline({ id: "suppliers", caliber: "compliance" }, zSup({ available: true, uflpa_flagged_total: 0, qual_abnormal_total: 0, suppliers_total: 25 }), "compliance");
  check("A2 零阳性 UFLPA 显 0（诚实空态，非未接入）", rhZeroUflpa?.text === "0" && rhZeroUflpa?.state === "real");
  check("A2 无 caliber 的供应商卡不覆盖（返 null=交期默认口径）", roleHeadline({ id: "suppliers" }, zSup(compAvail), "procurement") === null);

  // 自查：合规"清关卡点"履约卡 caliber=customs → 主数字=清关卡点票数（customs_blocked.value）、OTD 降副行
  const zFul: Zone = {
    zone: "fulfillment", headline_label: "准交率 OTD", headline_value: 0.969, trend: null,
    alert_count: 0, detail: { customs_blocked: { value: 21 } },
  };
  const rhCustoms = roleHeadline({ id: "fulfillment", caliber: "customs" }, zFul, "compliance");
  check("清关卡点卡主数字=清关卡点票数 21（非 96.9%）", rhCustoms?.text === "21" && (rhCustoms?.label ?? "").includes("清关卡点"));
  check("清关卡点卡 OTD 降副行", (rhCustoms?.sublines?.[0]?.text ?? "").includes("准交率 OTD"));
  check("无 caliber 的履约卡不覆盖（返 null=OTD 默认口径）", roleHeadline({ id: "fulfillment" }, zFul, "cs") === null);
}

console.log(`ROLE_WALL 配置完整性冒烟测试全部通过（${passed} 项断言）`);
