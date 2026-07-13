import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
  type MouseEvent as ReactMouseEvent,
} from "react";

export interface TipContent {
  title: string;
  plain: string;
  meta?: string[];
  tone?: "cyan" | "amber" | "green" | "violet" | "red";
}

interface TipState extends TipContent {
  x: number;
  y: number;
}

interface Ctx {
  show: (c: TipContent, e: ReactMouseEvent) => void;
  move: (e: ReactMouseEvent) => void;
  hide: () => void;
}

const TooltipCtx = createContext<Ctx | null>(null);

const toneColor: Record<NonNullable<TipContent["tone"]>, string> = {
  cyan: "var(--cyan)",
  amber: "var(--amber)",
  green: "var(--green)",
  violet: "var(--violet)",
  red: "var(--red)",
};

export function TooltipProvider({ children }: { children: ReactNode }) {
  const [tip, setTip] = useState<TipState | null>(null);

  const show = useCallback((c: TipContent, e: ReactMouseEvent) => {
    setTip({ ...c, x: e.clientX, y: e.clientY });
  }, []);
  const move = useCallback((e: ReactMouseEvent) => {
    const cx = e.clientX;
    const cy = e.clientY;
    setTip((t) => (t ? { ...t, x: cx, y: cy } : t));
  }, []);
  const hide = useCallback(() => setTip(null), []);

  const ctx = useMemo(() => ({ show, move, hide }), [show, move, hide]);

  return (
    <TooltipCtx.Provider value={ctx}>
      {children}
      {tip && (
        <div
          className="tip"
          style={{
            left: Math.min(Math.max(tip.x, 180), window.innerWidth - 180),
            top: tip.y - 14,
            borderColor: tip.tone ? toneColor[tip.tone] : undefined,
          }}
          role="tooltip"
        >
          <div className="tip-title">
            {tip.tone && (
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: 2,
                  background: toneColor[tip.tone],
                  flex: "none",
                }}
              />
            )}
            {tip.title}
          </div>
          <div className="tip-plain">{tip.plain}</div>
          {tip.meta && tip.meta.length > 0 && (
            <div className="tip-meta">
              {tip.meta.map((m, i) => (
                <span key={i}>{m}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </TooltipCtx.Provider>
  );
}

export function useTip() {
  const ctx = useContext(TooltipCtx);
  if (!ctx) throw new Error("useTip must be used within TooltipProvider");
  const { show, move, hide } = ctx;
  // 便捷绑定：把 hover 白话挂到任意元素
  const bind = useCallback(
    (c: TipContent) => ({
      onMouseEnter: (e: ReactMouseEvent) => show(c, e),
      onMouseMove: (e: ReactMouseEvent) => move(e),
      onMouseLeave: () => hide(),
    }),
    [show, move, hide]
  );
  return { bind, show, move, hide };
}
