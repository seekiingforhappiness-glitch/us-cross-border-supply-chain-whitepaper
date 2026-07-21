/* 线稿 SVG 图标集：stroke 用 currentColor，蓝图气质。24×24 viewBox。 */
import type { ReactNode } from "react";

function I({ children, size = 24 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.4}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

export type IconName =
  | "brain"
  | "gears"
  | "gauge"
  | "pillar"
  | "keys"
  | "ledger"
  | "ship"
  | "invoice"
  | "gate"
  | "dock"
  | "warehouse"
  | "thread"
  | "core";

export function Icon({ name, size }: { name: IconName; size?: number }) {
  switch (name) {
    case "brain":
      return (
        <I size={size}>
          <path d="M12 5.5a3 3 0 0 0-5.6-1.3A2.6 2.6 0 0 0 4 8a2.7 2.7 0 0 0 .5 4.4A2.8 2.8 0 0 0 6 17a2.7 2.7 0 0 0 6 .6z" />
          <path d="M12 5.5a3 3 0 0 1 5.6-1.3A2.6 2.6 0 0 1 20 8a2.7 2.7 0 0 1-.5 4.4A2.8 2.8 0 0 1 18 17a2.7 2.7 0 0 1-6 .6z" />
          <path d="M12 5.5v12.1" opacity="0.5" />
        </I>
      );
    case "gears":
      return (
        <I size={size}>
          <circle cx="9" cy="9" r="2.4" />
          <path d="M9 3.6v1.6M9 12.8v1.6M3.6 9h1.6M12.8 9h1.6M5.2 5.2l1.1 1.1M11.7 11.7l1.1 1.1M12.8 5.2l-1.1 1.1M6.3 11.7l-1.1 1.1" />
          <circle cx="16.5" cy="16.5" r="1.8" />
          <path d="M16.5 12.6v1.1M16.5 19.3v1.1M12.6 16.5h1.1M19.3 16.5h1.1" opacity="0.75" />
        </I>
      );
    case "gauge":
      return (
        <I size={size}>
          <path d="M4 15a8 8 0 0 1 16 0" />
          <path d="M12 15l4-3.4" />
          <circle cx="12" cy="15" r="1.1" fill="currentColor" stroke="none" />
          <path d="M4 15h1.6M18.4 15H20M6.2 9.4l1.1 1.1M17.8 9.4l-1.1 1.1" opacity="0.6" />
        </I>
      );
    case "pillar":
      return (
        <I size={size}>
          <path d="M3.5 8 12 4l8.5 4z" />
          <path d="M5 8v8M9.5 8v8M14.5 8v8M19 8v8" />
          <path d="M3.5 16.5h17M4.5 19h15" />
        </I>
      );
    case "keys":
      return (
        <I size={size}>
          <circle cx="7.5" cy="9" r="3.2" />
          <path d="M9.8 11.3 19 20.5M16 17.5l2-2M13.4 14.9l1.8-1.8" />
        </I>
      );
    case "ledger":
      return (
        <I size={size}>
          <rect x="4.5" y="3.5" width="15" height="17" rx="1.5" />
          <path d="M8 7.5h8M8 11h8M8 14.5h5" />
          <path d="M4.5 7.5H6M4.5 11H6M4.5 14.5H6" opacity="0.6" />
        </I>
      );
    case "ship":
      return (
        <I size={size}>
          <path d="M4 14.5h16l-1.7 4.2a1.5 1.5 0 0 1-1.4 1H7.1a1.5 1.5 0 0 1-1.4-1z" />
          <path d="M6.5 14.5V9.5h11v5" />
          <path d="M9.2 9.5V6.8h5.6v2.7" />
          <path d="M12 4v2.8M4 14.5l1.5-2.2h13l1.5 2.2" opacity="0.7" />
        </I>
      );
    case "invoice":
      return (
        <I size={size}>
          <path d="M6 3.5h9l3 3v14l-2-1-2 1-2-1-2 1-2-1-2 1V3.5z" />
          <path d="M8.5 8h7M8.5 11h7M8.5 14h4" />
        </I>
      );
    case "gate":
      return (
        <I size={size}>
          <path d="M4 20V6.5L12 4l8 2.5V20" />
          <path d="M4 20h16" />
          <path d="M9.5 20v-6a2.5 2.5 0 0 1 5 0v6" />
          <path d="M4 9.5h16" opacity="0.6" />
        </I>
      );
    case "dock":
      return (
        <I size={size}>
          <rect x="3.5" y="10.5" width="7" height="6" rx="0.6" />
          <rect x="13.5" y="10.5" width="7" height="6" rx="0.6" />
          <path d="M3.5 13.5h7M13.5 13.5h7" opacity="0.6" />
          <path d="M3.5 19.5h17M6 10.5V8h5M18 10.5V8h-3.5" />
        </I>
      );
    case "warehouse":
      return (
        <I size={size}>
          <path d="M3.5 20V9l8.5-4 8.5 4v11" />
          <path d="M3.5 20h17" />
          <rect x="7" y="12.5" width="4" height="7.5" />
          <rect x="13" y="12.5" width="4" height="4" />
        </I>
      );
    case "thread":
      return (
        <I size={size}>
          <circle cx="6" cy="7" r="2" />
          <circle cx="18" cy="12" r="2" />
          <circle cx="7" cy="17.5" r="2" />
          <path d="M7.7 8.4 16.4 11M16.4 13.3 8.7 16.4" opacity="0.85" />
        </I>
      );
    case "core":
      return (
        <I size={size}>
          <circle cx="12" cy="12" r="2.4" />
          <circle cx="12" cy="12" r="7.5" opacity="0.55" />
          <path d="M12 4.5v2.1M12 17.4v2.1M4.5 12h2.1M17.4 12h2.1" />
        </I>
      );
  }
}

export function LogoMark({ size = 26 }: { size?: number }) {
  // 等距三层堆叠标记
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" fill="none" aria-hidden="true">
      <path d="M14 3.5 24 8.2 14 13 4 8.2z" stroke="var(--layer-intel)" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M14 10 24 14.7 14 19.5 4 14.7z" stroke="var(--layer-biz)" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M14 16.5 24 21.2 14 26 4 21.2z" stroke="var(--layer-gov)" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  );
}
