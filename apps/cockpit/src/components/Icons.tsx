// 统一线条几何图标系统（V9-B：全 emoji 清零）。24px 网格、stroke 用 currentColor（继承文字色）、
// 圆角端点、线宽 1.5-2px。体征带/图例/卡片/tab/层标签全部改用本组件，零系统 emoji。
import type { CSSProperties } from "react";

export type IconName =
  // 七掌控区
  | "money"
  | "box"
  | "person"
  | "factory"
  | "rack"
  | "chip"
  | "stamp"
  // 全景层
  | "ship"
  // AI 工作流 kind
  | "llm"
  | "action"
  | "flow"
  | "detect"
  | "propose"
  | "approve"
  | "reject"
  | "close"
  // 通用
  | "lock"
  | "chat"
  | "mail"
  | "users"
  | "link"
  | "arrow-right"
  | "warn"
  | "spark"
  | "x"
  | "chevron-right"
  // 世界时钟时间轴回放（A-2）
  | "play"
  | "pause";

// 每图标 = 24x24 viewBox 内的一组 path/线段（stroke=currentColor，fill=none 除非填充点）。
const PATHS: Record<IconName, JSX.Element> = {
  money: (
    <>
      <rect x="3" y="7" width="18" height="12" rx="2.5" />
      <path d="M3 10.5h13a2 2 0 0 1 0 4H3" />
      <circle cx="16.5" cy="12.5" r="1.15" fill="currentColor" stroke="none" />
    </>
  ),
  box: (
    <>
      <path d="M12 3 4 7v10l8 4 8-4V7l-8-4Z" />
      <path d="M4 7l8 4 8-4M12 11v10" />
    </>
  ),
  person: (
    <>
      <circle cx="12" cy="8" r="3.4" />
      <path d="M5.5 20a6.5 6.5 0 0 1 13 0" />
    </>
  ),
  factory: (
    <>
      <path d="M3 21V10l6 4V10l6 4V6h3v15H3Z" />
      <path d="M6.5 17.5h2M11 17.5h2M15.5 17.5h2" />
    </>
  ),
  rack: (
    <>
      <rect x="3.5" y="4" width="17" height="16" rx="1.2" />
      <path d="M3.5 9.5h17M3.5 15h17M9 4v16M15 4v16" />
    </>
  ),
  chip: (
    <>
      <rect x="6.5" y="6.5" width="11" height="11" rx="1.6" />
      <path d="M9.5 10.5h5v5h-5z" fill="currentColor" stroke="none" opacity="0.55" />
      <path d="M9 3v2.5M15 3v2.5M9 18.5V21M15 18.5V21M3 9h2.5M3 15h2.5M18.5 9H21M18.5 15H21" />
    </>
  ),
  stamp: (
    <>
      <path d="M9 3.5h6a2 2 0 0 1 2 2.2l-.8 4.3a1.5 1.5 0 0 0 1.5 1.8H6.3a1.5 1.5 0 0 0 1.5-1.8L7 5.7a2 2 0 0 1 2-2.2Z" />
      <path d="M4.5 16.5h15M6 20.5h12" />
    </>
  ),
  ship: (
    <>
      <path d="M4 13.5 5 9.5h14l1 4" />
      <path d="M12 4v5.5M8.5 9.5V7h7v2.5" />
      <path d="M3.5 14.5c1.6 1.4 3.1 1.4 4.7 0 1.6 1.4 3.1 1.4 4.7 0 1.6 1.4 3.1 1.4 4.7 0" />
    </>
  ),
  llm: (
    <>
      <circle cx="12" cy="12" r="2.2" fill="currentColor" stroke="none" />
      <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1" />
    </>
  ),
  action: (
    <>
      <path d="M13 3 5 13h5l-1 8 8-10h-5l1-8Z" fill="currentColor" stroke="none" />
    </>
  ),
  flow: (
    <>
      <circle cx="6" cy="6" r="2.2" />
      <circle cx="18" cy="18" r="2.2" />
      <path d="M8 7.2c6 1.5 7.8 4 8.8 8.6" />
    </>
  ),
  detect: (
    <>
      <circle cx="10.5" cy="10.5" r="6" />
      <path d="M15 15l5 5" />
    </>
  ),
  propose: (
    <>
      <path d="M6 3.5h9l4 4V20a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z" />
      <path d="M14 3.5V8h4.5M8.5 13h7M8.5 16.5h5" />
    </>
  ),
  approve: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M8 12.2l2.6 2.6L16 9.4" />
    </>
  ),
  reject: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M9 9l6 6M15 9l-6 6" />
    </>
  ),
  close: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5v5l3 2" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="10.5" width="14" height="9.5" rx="2" />
      <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" />
      <circle cx="12" cy="15" r="1.1" fill="currentColor" stroke="none" />
    </>
  ),
  chat: (
    <>
      <path d="M4 5.5h16a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H9l-4 3.5V16.5H4a1 1 0 0 1-1-1v-9a1 1 0 0 1 1-1Z" />
    </>
  ),
  mail: (
    <>
      <rect x="3.5" y="5.5" width="17" height="13" rx="1.6" />
      <path d="M4 7l8 6 8-6" />
    </>
  ),
  users: (
    <>
      <circle cx="9" cy="8.5" r="3" />
      <path d="M3.5 19a5.5 5.5 0 0 1 11 0" />
      <path d="M16 6.2a3 3 0 0 1 0 5.6M17.5 19a5.5 5.5 0 0 0-2.6-4.6" />
    </>
  ),
  link: (
    <>
      <path d="M9.5 14.5l5-5" />
      <path d="M8 12l-2 2a2.8 2.8 0 0 0 4 4l2-2M16 12l2-2a2.8 2.8 0 0 0-4-4l-2 2" />
    </>
  ),
  "arrow-right": (
    <>
      <path d="M4 12h15M13 6l6 6-6 6" />
    </>
  ),
  warn: (
    <>
      <path d="M12 4 21 19H3L12 4Z" />
      <path d="M12 10v4.5M12 17h.01" />
    </>
  ),
  spark: (
    <>
      <path d="M12 3l2.1 6.2L20 11l-5.9 1.8L12 19l-2.1-6.2L4 11l5.9-1.8L12 3Z" />
    </>
  ),
  x: (
    <>
      <path d="M6 6l12 12M18 6L6 18" />
    </>
  ),
  "chevron-right": (
    <>
      <path d="M9 5l7 7-7 7" />
    </>
  ),
  play: (
    <>
      <path d="M7 5l12 7-12 7V5Z" fill="currentColor" stroke="none" />
    </>
  ),
  pause: (
    <>
      <rect x="6.5" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none" />
      <rect x="13.5" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none" />
    </>
  ),
};

interface Props {
  name: IconName;
  size?: number;
  strokeWidth?: number;
  className?: string;
  style?: CSSProperties;
  title?: string;
}

export default function Icon({ name, size = 16, strokeWidth = 1.7, className, style, title }: Props) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
      style={style}
    >
      {title ? <title>{title}</title> : null}
      {PATHS[name]}
    </svg>
  );
}
