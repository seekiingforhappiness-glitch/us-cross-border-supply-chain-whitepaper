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

import type { Role, ZoneId } from "../api";
import { ROLES } from "../roleActors";
import {
  FIXED_ORDER_IDS,
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

console.log(`ROLE_WALL 配置完整性冒烟测试全部通过（${passed} 项断言）`);
