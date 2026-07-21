import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

export interface Route {
  view: string;
  focus?: string; // 穿梭携带：类型名 / 类型:实例 / 规则号 / 动作号
}

interface NavCtxShape {
  route: Route;
  navigate: (view: string, focus?: string) => void;
}

const NavCtx = createContext<NavCtxShape | null>(null);

export function NavProvider({ children }: { children: ReactNode }) {
  const [route, setRoute] = useState<Route>({ view: "structure" });
  const navigate = useCallback((view: string, focus?: string) => {
    setRoute({ view, focus });
    // 切视图时把主区滚回顶部
    requestAnimationFrame(() => {
      const stage = document.querySelector(".stage");
      if (stage) stage.scrollTop = 0;
    });
  }, []);
  const value = useMemo(() => ({ route, navigate }), [route, navigate]);
  return <NavCtx.Provider value={value}>{children}</NavCtx.Provider>;
}

export function useNav(): NavCtxShape {
  const ctx = useContext(NavCtx);
  if (!ctx) throw new Error("useNav must be used within NavProvider");
  return ctx;
}
