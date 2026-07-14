import { useState, type ReactElement } from "react";
import ConnectionStatusBar from "./components/ConnectionStatusBar";
import Panorama from "./views/Panorama";
import ObjectCard from "./views/ObjectCard";
import AiWorkflow from "./views/AiWorkflow";

type ViewId = "panorama" | "object-card" | "ai-workflow";

interface ViewDef {
  id: ViewId;
  label: string;
  el: ReactElement;
}

// 三个占位路由——地基单只做「能切换」，不做导航的画面级视觉设计（候 Daniel 对齐画面复述）。
// 不引入 react-router 等新依赖，用最简单的 useState 做视图切换即可。
const VIEWS: ViewDef[] = [
  { id: "panorama", label: "全景", el: <Panorama /> },
  { id: "object-card", label: "对象卡", el: <ObjectCard /> },
  { id: "ai-workflow", label: "AI 工作流", el: <AiWorkflow /> },
];

export default function App() {
  const [viewId, setViewId] = useState<ViewId>("panorama");
  const active = VIEWS.find((v) => v.id === viewId) ?? VIEWS[0];

  return (
    <div>
      <ConnectionStatusBar />
      <nav>
        {VIEWS.map((v) => (
          <button key={v.id} onClick={() => setViewId(v.id)} disabled={v.id === viewId}>
            {v.label}
          </button>
        ))}
      </nav>
      <main>{active.el}</main>
    </div>
  );
}
