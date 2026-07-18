import type { ReactNode } from "react";
import Icon, { type IconName } from "./Icons";

// U4 三态底座（V17 波U，spec docs/superpowers/specs/2026-07-16-waveU-user-facing.md）：
// 加载 / 空 / 错误 / 无权 四态统一组件。
//
// 为什么要建它（白话）：现状四态各写各的、长得还不一样——App.tsx 指挥墙加载只有一句灰字「指挥墙
// 加载中…」；AiWorkflow/RouteMap 各自重复几乎相同的「加载中…」「加载失败——确认 API 已启动」；
// ObjectCard/ImpactPanel 的行内错误是 <b>前缀</b>+原文但没有重试出口；ZoneQueue/LaneQueue 的空态
// （emptyHint）有白话但没图标也不成体系；DecisionButtons 的无权提示（灰锁+「需经理角色…」白话）
// 其实已经是本组件要长成的样子，只是没抽出来给别处复用。四态传达的信息价值不同——加载=请稍候、
// 空=正常但没内容/给出路、错误=系统坏了/给出路、无权=身份不够不是你的错/给出路——统一组件把
// 「有信息量地空白」做一次，各视图一行接入，而不是每处补一句「加载中…」了事（干巴巴空白的病根）。
//
// 本文件本次只新增组件本身，不改任何现有视图（整装归下一阶段，接入点清单见交付报告）。

export type StateHintKind = "loading" | "empty" | "error" | "no-permission";

export interface StateHintProps {
  kind: StateHintKind;
  /** 覆盖默认标题；四态各有白话默认值（见 DEFAULT_TITLE），多数场景可不传。 */
  title?: ReactNode;
  /** empty 态：为什么空的白话原因（如"该世界无采购收货域"）。 */
  reason?: ReactNode;
  /** empty 态：建议动作白话（纯文案展示，不代跳转——各视图动作五花八门，由调用方决定是否包一层可点）。 */
  suggestion?: ReactNode;
  /** error 态：后端白话错误原文，原样展示、不加工不美化（沿用 api.ts "不吞错" 的约定）。 */
  message?: ReactNode;
  /** error 态：提供则渲染「重试」按钮；不提供则只展示错误、不给按钮（如调用方无重取入口时）。 */
  onRetry?: () => void;
  /** no-permission 态：需要的角色白话（如「需经理角色才能拍板，顶栏切到老板 manager」）。 */
  roleHint?: ReactNode;
  /** 覆盖默认图标（loading 态走骨架屏不用图标，此 prop 对 loading 无效）。 */
  icon?: IconName;
  /** 紧凑内联态：用于抽屉/面板局部行内位置（对齐既有 cp-inline-load / DecisionButtons 无权提示的
   *  尺寸观感）。默认（false）= 铺满容器居中的面板级呈现（对齐既有 cp-fill-msg / cp-queue__empty）。 */
  compact?: boolean;
  /** loading 非紧凑态的骨架行数，默认 3；紧凑态固定单行小骨架条，本项不生效。 */
  skeletonRows?: number;
  /** 极少数特殊定位需要时的附加类名。 */
  className?: string;
}

const DEFAULT_ICON: Record<Exclude<StateHintKind, "loading">, IconName> = {
  empty: "detect",
  error: "warn",
  "no-permission": "lock",
};

const DEFAULT_TITLE: Record<StateHintKind, string> = {
  loading: "加载中…",
  empty: "暂无数据",
  error: "出错了",
  "no-permission": "无权查看",
};

// 骨架行宽度错落（100%/82%/58% 循环），避免"三条等长灰条"的机械感——纯观感细节，不承载信息。
const ROW_WIDTHS = ["100%", "82%", "58%"];

function Skeleton({ rows }: { rows: number }) {
  return (
    <div className="cp-skel">
      {Array.from({ length: Math.max(1, rows) }).map((_, i) => (
        <div key={i} className="cp-skel__row" style={{ width: ROW_WIDTHS[i % ROW_WIDTHS.length] }} />
      ))}
    </div>
  );
}

export default function StateHint({
  kind,
  title,
  reason,
  suggestion,
  message,
  onRetry,
  roleHint,
  icon,
  compact = false,
  skeletonRows = 3,
  className,
}: StateHintProps) {
  const resolvedTitle = title ?? DEFAULT_TITLE[kind];

  // loading：骨架屏脉动，不走"图标+标题"的信息卡外观——骨架本身就是内容占位，多加图标反而是噪音。
  // aria-live=polite + role=status：加载态是短暂的，用礼貌播报而非打断（区别于 error 的 role=alert）。
  if (kind === "loading") {
    return compact ? (
      <div className={`cp-state cp-state--loading cp-state--compact ${className ?? ""}`} role="status" aria-live="polite">
        <span className="cp-skel__row cp-skel__row--inline" aria-hidden />
        <span className="cp-state__caption">{resolvedTitle}</span>
      </div>
    ) : (
      <div className={`cp-state cp-state--loading ${className ?? ""}`} role="status" aria-live="polite">
        <Skeleton rows={skeletonRows} />
        <div className="cp-state__caption">{resolvedTitle}</div>
      </div>
    );
  }

  // 走到这里 kind 已被 TS 窄化为 "empty" | "error" | "no-permission"（loading 分支已 return）。
  const resolvedIcon = icon ?? DEFAULT_ICON[kind];
  const body = kind === "empty" ? reason : kind === "error" ? message : roleHint;

  return (
    <div
      className={`cp-state cp-state--${kind} ${compact ? "cp-state--compact" : ""} ${className ?? ""}`}
      // error=系统真的坏了，用 alert 主动播报；empty/no-permission 是正常边界态，不打断屏幕阅读器。
      role={kind === "error" ? "alert" : undefined}
    >
      <span className="cp-state__icon" aria-hidden>
        <Icon name={resolvedIcon} size={compact ? 13 : 20} />
      </span>
      <div className="cp-state__text">
        <div className="cp-state__title">{resolvedTitle}</div>
        {body != null && body !== "" && <div className="cp-state__body">{body}</div>}
        {kind === "empty" && suggestion != null && suggestion !== "" && (
          <div className="cp-state__suggestion">→ {suggestion}</div>
        )}
        {kind === "error" && onRetry && (
          <button type="button" className="cp-state__retry" onClick={onRetry}>
            重试
          </button>
        )}
      </div>
    </div>
  );
}
