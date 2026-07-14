import { type Zone, type ZoneId } from "../api";
import Icon from "../components/Icons";
import WorkQueue from "./WorkQueue";
import ZoneContext from "./ZoneContext";
import { zoneQueue, ZONE_SHORT, type DrillTarget } from "./zoneModel";

// 区工作队列（下钻第二段）——V10 方案 C。点指挥墙区卡进入：面包屑「指挥墙 > 区」+ 工作队列
// （有队列的区）+ 聚合上下文（ZoneContext）。无队列的存量聚合区如实给出 emptyHint 并由上下文承接。
// 履约区额外给「航线视图」入口（与卡上切换钮同去处），方便在区内也能切到航线地图。

const EMPTY_HINT: Partial<Record<ZoneId, string>> = {
  money: "钱区为存量聚合指标（敞口 / 在途 / 拦回 / 毛利），无逐条工作队列——见下方聚合。",
  fulfillment: "履约 headline 为准交率聚合，逐条在途请走「航线视图」；清关卡点 / 延误分布见下方。",
  ai: "AI 运营账为累计聚合指标，无逐条队列——见下方今日 / 累计 / 记忆命中。",
  suppliers: "当前世界缺采购收货域，无供应商交期队列——见下方对账差异 / 单一依赖。",
  inventory: "当前无安全库存击穿条目——见下方盘点差异 / 现货可救性。",
};

interface Props {
  zone: Zone;
  onBack: () => void;
  onMap?: () => void;
  onDrill: (t: DrillTarget, key: string) => void;
  activeKey: string | null;
}

export default function ZoneQueue({ zone, onBack, onMap, onDrill, activeKey }: Props) {
  return (
    <WorkQueue
      crumbs={[{ label: "指挥墙", onClick: onBack }, { label: ZONE_SHORT[zone.zone] }]}
      title={zone.headline_label}
      alertCount={zone.alert_count}
      headerActions={
        zone.zone === "fulfillment" && onMap ? (
          <button className="cp-switch-btn" onClick={onMap}>
            <Icon name="ship" size={14} /> 航线视图 <Icon name="arrow-right" size={12} />
          </button>
        ) : undefined
      }
      spec={zoneQueue(zone)}
      onDrill={onDrill}
      activeKey={activeKey}
      emptyHint={EMPTY_HINT[zone.zone] ?? "本区无逐条队列——见下方聚合指标。"}
      context={<ZoneContext zone={zone} />}
    />
  );
}
