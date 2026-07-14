import { useEffect, useState } from "react";
import { fetchOntologySummary, type OntologySummary } from "../api";

type ConnState =
  | { phase: "loading" }
  | { phase: "connected"; data: OntologySummary }
  | { phase: "error" };

// 首页顶部常驻一行连接状态条——零硬编码，字段全部来自 GET /ontology。
// apps/api 未起时（fetch 失败/网络错误）优雅降级为提示文案，不让整页崩掉。
// 纯信息展示，不做画面级视觉设计（无样式，候 Daniel 对画面复述确认）。
export default function ConnectionStatusBar() {
  const [state, setState] = useState<ConnState>({ phase: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetchOntologySummary()
      .then((data) => {
        if (!cancelled) setState({ phase: "connected", data });
      })
      .catch(() => {
        if (!cancelled) setState({ phase: "error" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.phase === "loading") {
    return <p>连接中…</p>;
  }
  if (state.phase === "error") {
    return <p>API 未连接（uvicorn 启动命令见 README）</p>;
  }

  const { world, version, summary } = state.data;
  return (
    <p>
      已连接 {world} 世界 · 本体 v{version} · {summary.object_types} 对象/{summary.links} 关系/
      {summary.actions} 动作
    </p>
  );
}
