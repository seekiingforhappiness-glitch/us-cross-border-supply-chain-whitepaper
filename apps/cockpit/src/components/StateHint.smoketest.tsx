// U4 StateHint 纯渲染冒烟测试。
//
// 为什么长这样（白话）：apps/cockpit 未接任何测试框架（package.json 无 vitest/jest/
// @testing-library/*），本工作单又明令"零新依赖"（不新增 npm 依赖）——两条约束叠加，装不了
// 常规 describe/it 测试。退而求其次：用项目已有的 react-dom/server（react-dom 自带，非新依赖）
// 服务端渲染四态到静态 HTML 字符串，用手写的最小断言（不借 node:assert——本项目未装
// @types/node，import 会因缺类型声明而编译不过，见下）判断关键片段存在，纯渲染不碰 DOM/事件，
// 故名"冒烟测试"——能测到"渲染不炸 + 关键文案/类名/可访问性属性确实出现"，测不到点击/焦点等
// 交互行为（那需要 jsdom，装不了）。
//
// 本文件被 tsconfig 的 "include": ["src"] 覆盖，会随 tsc --noEmit / npm run build 的 tsc 步骤
// 一起类型检查（这本身就是回归的一部分）；但没有任何视图 import 它，vite build 的模块图不会
// 打包进产物。真正"跑起来"需要手动一步编译+执行（本项目没有测试 runner 帮你接线）：
//
//   cd apps/cockpit && npx esbuild src/components/StateHint.smoketest.tsx \
//     --bundle --platform=node --format=cjs --jsx=automatic \
//     --outfile=/tmp/statehint-smoke.cjs && node /tmp/statehint-smoke.cjs
//
// esbuild 是 vite 的既有间接依赖（node_modules/esbuild 已存在，非新增）；--bundle 把 react /
// react-dom/server 与本组件一起打进单文件。format 必须是 cjs 不是 esm——react-dom 的 Node 服务端
// 渲染实现内部用 require("stream") 这类 CJS 动态 require，esbuild 打 esm 包时这类调用会落到一个
// 在纯 ESM 运行时不存在同步 require 的兜底 shim，实测报 "Dynamic require of 'stream' is not
// supported" 直接崩溃（本文件曾踩这个坑，改 --format=cjs + .cjs 后缀让 Node 按 CommonJS 加载才通，
// 与本包 package.json 顶层 "type": "module" 无关——.cjs 后缀强制该文件按 CJS 解释）。

import { renderToStaticMarkup } from "react-dom/server";
import StateHint from "./StateHint";

let passed = 0;
function check(label: string, ok: boolean) {
  if (!ok) throw new Error(`[FAIL] ${label}`);
  passed += 1;
}

// ── loading：骨架屏脉动，role=status（礼貌播报，非打断） ──────────────────────────
{
  const out = renderToStaticMarkup(<StateHint kind="loading" />);
  check("loading 默认态含 role=status", /role="status"/.test(out));
  check("loading 默认态渲染骨架 cp-skel", /cp-skel/.test(out));
  check("loading 默认标题为白话“加载中…”", /加载中/.test(out));
}

// ── loading compact：单行内联骨架，不用大骨架块 ──────────────────────────────────
{
  const out = renderToStaticMarkup(<StateHint kind="loading" compact title="接线中…" />);
  check("compact loading 带 cp-state--compact", /cp-state--compact/.test(out));
  check("compact loading 用内联骨架条", /cp-skel__row--inline/.test(out));
  check("compact loading 显示自定义 title", /接线中/.test(out));
}

// ── empty：图标 + 白话原因 + 建议动作 ────────────────────────────────────────────
{
  const out = renderToStaticMarkup(
    <StateHint kind="empty" reason="该世界无采购收货域" suggestion="切换到验证世界查看" />,
  );
  check("empty 带 cp-state--empty", /cp-state--empty/.test(out));
  check("empty 原样展示 reason", /该世界无采购收货域/.test(out));
  check("empty 展示 suggestion（带 → 前缀）", /→\s*切换到验证世界查看/.test(out));
  check("empty 默认标题为“暂无数据”", /暂无数据/.test(out));
  check("empty 无 role=alert（非故障，不打断朗读）", !/role="alert"/.test(out));
}

// ── error：原样展示后端白话错误 + 提供 onRetry 才渲染重试按钮 ───────────────────────
{
  const out = renderToStaticMarkup(
    <StateHint kind="error" message="没能连上驾驶舱数据接口。" onRetry={() => {}} />,
  );
  check("error 带 role=alert", /role="alert"/.test(out));
  check("error 原样展示后端错误原文", /没能连上驾驶舱数据接口/.test(out));
  check("error 提供 onRetry 时渲染重试按钮", /<button[^>]*>\s*重试\s*<\/button>/.test(out));
}
{
  const out = renderToStaticMarkup(<StateHint kind="error" message="x" />);
  check("error 无 onRetry 时不渲染重试按钮", !/重试/.test(out));
}

// ── no-permission：灰锁 + 角色白话 ──────────────────────────────────────────────
{
  const out = renderToStaticMarkup(
    <StateHint kind="no-permission" roleHint="需经理角色才能拍板，顶栏切到老板 manager" />,
  );
  check("no-permission 带对应态类名", /cp-state--no-permission/.test(out));
  check("no-permission 展示 roleHint 白话", /需经理角色才能拍板/.test(out));
  check("no-permission 默认标题为“无权查看”", /无权查看/.test(out));
}

// ── icon 覆盖：合法性已由 IconName 联合类型在编译期保证，这里只测运行时确实出图 ──────────
{
  const out = renderToStaticMarkup(<StateHint kind="empty" icon="box" reason="r" />);
  check("覆盖 icon 后仍渲染出 svg", /<svg/.test(out));
}

console.log(`StateHint 冒烟测试全部通过（${passed} 项断言）`);
