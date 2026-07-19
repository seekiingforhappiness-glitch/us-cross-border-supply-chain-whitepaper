import { useEffect, useState } from "react";
import { fetchCustomsQueue, fetchVitals, formatInt, type CustomsQueue, type Role, type Zone, type ZoneId } from "../api";
import { roleLabel } from "../roleActors";
import Icon from "../components/Icons";
import WorkQueue from "./WorkQueue";
import ZoneContext from "./ZoneContext";
import { SEV_CN } from "./aiFlowModel";
import { zoneQueue, zoneShort, type DrillTarget, type QueueSpec } from "./zoneModel";

// 区工作队列（下钻第二段）——V10 方案 C。点指挥墙区卡进入：面包屑「指挥墙 > 区」+ 工作队列
// （有队列的区）+ 聚合上下文（ZoneContext）。无队列的存量聚合区如实给出 emptyHint 并由上下文承接。
// 履约区额外给「航线视图」入口（与卡上切换钮同去处），方便在区内也能切到航线地图。

const EMPTY_HINT: Partial<Record<ZoneId, string>> = {
  money: "钱区为存量聚合指标（敞口 / 在途 / 拦回 / 毛利），无逐条工作队列——见下方聚合。",
  // 轮3-G（P0，林律"21 票只有数字、导流去不存在的入口"）：履约区现在有清关卡点逐票队列（下方
  // 列表），此空态只在"当前无卡点票"时出现——原"逐票明细在 Streamlit 操作台查询"是死导流已撤。
  fulfillment: "当前无清关卡点票（未报关且在途 = 0）——延误分布 / 准交聚合见下方；航线视图看聚合航线与告警分布。",
  ai: "AI 运营账为累计聚合指标，无逐条队列——见下方今日 / 累计 / 记忆命中。",
  suppliers: "当前世界缺采购收货域，无供应商交期队列——见下方对账差异 / 单一依赖。",
  inventory: "当前无安全库存击穿条目——见下方盘点差异 / 现货可救性。",
};

// ── 清关卡点逐票队列（轮3-G）：/cockpit/customs-queue → QueueSpec ─────────────────
// 为什么这样建：履约区原本 zoneQueue()=null 只有聚合——"清关卡点 21 票"没有清单可点。后端按七区卡
// 同一 WHERE 现查逐票（票号/目的港/卡点天数/关联 PO/严重度），这里只做呈现映射：复用 WorkQueue 的
// cap+「共 N 条」标注惯例，行点击开该票对象卡（Shipment），口径白话用后端 basis 原文不另造。
const SEV_TONE: Record<string, "red" | "amber" | "neutral"> = { critical: "red", high: "amber" };

function customsSpec(q: CustomsQueue): QueueSpec | null {
  if (q.items.length === 0) return null; // 空 → 走 EMPTY_HINT 诚实空态
  const countLabel = q.count < q.total ? `共 ${q.total} 票 · 显示前 ${q.count} 票 · 按卡点天数降序` : `共 ${q.total} 票 · 按卡点天数降序`;
  return {
    columns: [
      { label: "票号" },
      { label: "目的港" },
      { label: "卡点天数", num: true },
      { label: "关联 PO", num: true },
      { label: "未闭环风险", num: true },
    ],
    countLabel,
    basis: q.basis,
    rows: q.items.map((it) => ({
      key: it.shipment_id,
      badge: it.severity ? { text: SEV_CN[it.severity] ?? it.severity, tone: SEV_TONE[it.severity] ?? "neutral" } : undefined,
      cells: [
        { text: it.shipment_id, num: true },
        { text: it.destination_port ?? "—" },
        { text: it.stuck_days != null ? `${it.stuck_days} 天` : "—", num: true },
        { text: formatInt(it.po_count), num: true },
        { text: it.open_risks > 0 ? `${it.open_risks} 条` : "—", num: true, tone: it.open_risks > 0 ? ("neg" as const) : undefined },
      ],
      drill: { kind: "object" as const, ref: { type: "Shipment", id: it.shipment_id }, title: it.shipment_id },
    })),
  };
}

interface Props {
  zone: Zone;
  role: Role;
  asOf: string | null;
  onBack: () => void;
  onMap?: () => void;
  onDrill: (t: DrillTarget, key: string) => void;
  activeKey: string | null;
}

export default function ZoneQueue({ zone, role, asOf, onBack, onMap, onDrill, activeKey }: Props) {
  // 待批提案队列"我组的"筛选（V22 任务1；缘起：财务陌生人"待批队列里自己的活要靠运气翻到"）。
  // 只对 decisions 区、且非 manager（老板本就是收件人全集，"我组的"=全部，冗余不显）。切"我组的"→
  // 带 assignee_role=当前角色重取一份 vitals，仅取其 decisions 区覆盖显示；**不污染** App 共享 vitals
  // （指挥墙墙卡仍是全集，权限/掩码全由后端按 X-Role 同源，前端只透传角色）。后端非法值 422、缺省不筛。
  const canFilter = zone.zone === "decisions" && role !== "manager";
  const [mineOnly, setMineOnly] = useState(false);
  const [mineZone, setMineZone] = useState<Zone | null>(null);
  const [mineLoading, setMineLoading] = useState(false);
  const [mineErr, setMineErr] = useState(false);

  // 离开可筛区 / 换角色 → 复位到"全部"（不把上一次筛选态带去别处）。
  useEffect(() => {
    if (!canFilter) setMineOnly(false);
  }, [canFilter, role]);

  // "我组的"激活 → 拉取过滤后的 decisions 区（世界跟随 api 模块级单例，随 X-Role/asOf）。
  useEffect(() => {
    if (!mineOnly || !canFilter) {
      setMineZone(null);
      setMineErr(false);
      return;
    }
    let cancelled = false;
    setMineLoading(true);
    setMineErr(false);
    fetchVitals(role, asOf, true, role)
      .then((v) => {
        if (cancelled) return;
        setMineZone(v.zones.find((z) => z.zone === "decisions") ?? null);
        setMineLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setMineErr(true);
        setMineLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [mineOnly, canFilter, role, asOf]);

  const filtering = mineOnly && canFilter;
  // 队列列表用过滤后的 decisions 区；告警计数/聚合上下文仍用原区（超期/升级是独立信号，不随"我组的"变）。
  const listZone = filtering && mineZone ? mineZone : zone;

  // 清关卡点逐票队列（轮3-G）：仅履约区取数——角色/世界随 X-Role/X-World 头同源（脱敏后端做）。
  const isFulfillment = zone.zone === "fulfillment";
  const [customs, setCustoms] = useState<CustomsQueue | null>(null);
  const [customsLoading, setCustomsLoading] = useState(false);
  const [customsErr, setCustomsErr] = useState(false);
  useEffect(() => {
    if (!isFulfillment) {
      setCustoms(null);
      setCustomsErr(false);
      return;
    }
    let cancelled = false;
    setCustomsLoading(true);
    setCustomsErr(false);
    fetchCustomsQueue(role)
      .then((q) => {
        if (cancelled) return;
        setCustoms(q);
        setCustomsLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setCustomsErr(true);
        setCustomsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isFulfillment, role]);

  const filterChips = canFilter ? (
    <div className="cp-qfilter" role="group" aria-label="待批提案筛选">
      <button
        type="button"
        className={`cp-qfilter__chip ${!mineOnly ? "is-on" : ""}`}
        aria-pressed={!mineOnly}
        onClick={() => setMineOnly(false)}
      >
        全部
      </button>
      <button
        type="button"
        className={`cp-qfilter__chip ${mineOnly ? "is-on" : ""}`}
        aria-pressed={mineOnly}
        onClick={() => setMineOnly(true)}
        title={`只看指派给「${roleLabel(role)}」的待批提案`}
      >
        我组的
      </button>
    </div>
  ) : undefined;

  // 空态：筛选态显诚实"当前没有指派给〈角色〉的待批提案"；加载失败给可操作出路；否则沿用区级 emptyHint。
  const emptyHint = mineErr
    ? "「我组的」筛选加载失败——请切回「全部」或稍后重试"
    : filtering
      ? `当前没有指派给「${roleLabel(role)}」的待批提案`
      : isFulfillment && customsErr
        ? "清关卡点逐票队列加载失败——稍后重试；卡点计数仍见下方聚合。"
        : EMPTY_HINT[zone.zone] ?? "本区无逐条队列——见下方聚合指标。";

  // 履约区队列＝清关卡点逐票（轮3-G）；其余区沿用 zoneQueue 聚合映射。
  const spec = isFulfillment ? (customs ? customsSpec(customs) : null) : zoneQueue(listZone);

  return (
    <WorkQueue
      crumbs={[{ label: "指挥墙", onClick: onBack }, { label: zoneShort(zone.zone, role) }]}
      title={zone.headline_label}
      alertCount={zone.alert_count}
      alertLabel={zone.zone === "decisions" ? "超时/升级告警" : undefined}
      alertTitle={zone.zone === "decisions"
        ? "告警数=超期任务+升级件，不是待批提案数（待批数见卡片大数字与下方列表行数）"
        : undefined}
      filterChips={filterChips}
      loading={(filtering && mineLoading && !mineZone) || (isFulfillment && customsLoading && !customs)}
      headerActions={
        zone.zone === "fulfillment" && onMap ? (
          <button className="cp-switch-btn" onClick={onMap}>
            <Icon name="ship" size={14} /> 航线视图 <Icon name="arrow-right" size={12} />
          </button>
        ) : undefined
      }
      spec={spec}
      onDrill={onDrill}
      activeKey={activeKey}
      emptyHint={emptyHint}
      context={<ZoneContext zone={zone} />}
    />
  );
}
