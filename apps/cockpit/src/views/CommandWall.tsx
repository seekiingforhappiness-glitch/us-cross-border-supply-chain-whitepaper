import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import {
  formatInt,
  formatPct,
  isMissing,
  type GovernanceGating,
  type Missing,
  type Role,
  type Zone,
  type ZoneId,
  type ZoneProvenance,
} from "../api";
import Icon from "../components/Icons";
import StateHint from "../components/StateHint";
import { roleLabel } from "../roleActors";
import {
  admissionActionable,
  admissionSummary,
  FIXED_ORDER_IDS,
  headlineOf,
  readAdmissionFunnel,
  ROLE_WALL,
  summaryLines,
  todaysFocus,
  ZONE_ICON,
  zoneShort,
  type AdmissionFunnel,
  type FocusItem,
  type SummaryLine,
} from "./zoneModel";

// 角色化指挥墙（V24；V10 方案 C 首屏中央的角色化重排）——体征带的"放大态"（顶部体征带已移除）。
// 缘起：Daniel"各角色展示页面完全一样"。改法：墙循环从写死七区改**读 ROLE_WALL 配置**（zoneModel.ts）——
// 每角色渲染哪些区块、什么顺序、什么标题各不相同，但**区块组件全部复用**（同构骨架异构内容）。
// 每卡：区图标+角色化区名 + headline 大数字 + 趋势 + 告警徽标 + 该区最要紧 2-3 行摘要
// + 数字溯源钮（U2）；AI 卡额外挂信任档徽章（U3）。sales/compliance 另有"准入案"组合块（复用
// 已下发的 customers 区 admission_funnel，零新端点）。
// manager 保持现七区墙不动（告警优先重排 + byte-identical，见 resolveWall）；其余六角色按配置序。
// 无数据/掩码 headline 如实降饱和。履约卡右上角「航线视图」钮 → 切航线图（stopPropagation）。点卡体 → 下钻。

const FIXED_ORDER: ZoneId[] = FIXED_ORDER_IDS;

// manager 用：告警卡（alert_count>0）按 alert_count 降序在前，无告警卡按固定七区序在后。
function sortZones(zones: Zone[]): Zone[] {
  const rank = new Map(FIXED_ORDER.map((z, i) => [z, i]));
  return [...zones].sort((a, b) => {
    const aa = a.alert_count > 0;
    const ba = b.alert_count > 0;
    if (aa !== ba) return aa ? -1 : 1;
    if (aa && ba) return b.alert_count - a.alert_count || (rank.get(a.zone) ?? 9) - (rank.get(b.zone) ?? 9);
    return (rank.get(a.zone) ?? 9) - (rank.get(b.zone) ?? 9);
  });
}

// 首屏渲染项（V24）：zone 区卡（复用现有渲染）或准入组合块。由 ROLE_WALL[role] 解析而来。
type WallItem =
  | { kind: "zone"; zone: Zone; title: string; drill: ZoneId }
  | { kind: "admission"; funnel: AdmissionFunnel | Missing | undefined; title: string; drill: ZoneId };

// 把 ROLE_WALL[role] 配置解析成有序渲染项。
//  · manager：现七区墙不动——仍用 sortZones（告警优先重排）+ zoneShort 原名，与改前 byte-identical
//    （sortZones 全排序，输入序不影响输出 ⇒ 结果与旧 CommandWall 逐字节一致）。
//  · 其余角色：按 ROLE_WALL 配置序（不告警重排——各角色首屏序是规格的刻意编排，如 finance 钱区永远
//    首位）；zone 块在载荷里找不到对应区（理论不发生，vitals 恒下发七区）则如实跳过，不占空位。
function resolveWall(role: Role, zones: Zone[]): WallItem[] {
  if (role === "manager") {
    return sortZones(zones).map((z) => ({ kind: "zone" as const, zone: z, title: zoneShort(z.zone, role), drill: z.zone }));
  }
  const items: WallItem[] = [];
  for (const b of ROLE_WALL[role]) {
    if (b.kind === "admission") {
      items.push({ kind: "admission", funnel: readAdmissionFunnel(zones), title: b.title, drill: b.drill });
    } else if (b.zone) {
      const z = zones.find((zz) => zz.zone === b.zone);
      if (z) items.push({ kind: "zone", zone: z, title: b.title, drill: b.drill });
    }
  }
  return items;
}

// 趋势箭头：仅准交率 up=好（绿）/down=差（红）语义明确；其余方向中性（flat）不评判。
function trendView(z: Zone): { arrow: string; cls: string; text: string } | null {
  const t = z.trend;
  if (!t) return null;
  const goodWhenUp = z.zone === "fulfillment";
  let cls = "cp-trend--flat";
  let arrow = "—";
  if (t.delta > 0) {
    arrow = "▲";
    cls = goodWhenUp ? "cp-trend--up" : "cp-trend--flat";
  } else if (t.delta < 0) {
    arrow = "▼";
    cls = goodWhenUp ? "cp-trend--down" : "cp-trend--flat";
  }
  const mag = Math.abs(t.delta) < 1 ? formatPct(Math.abs(t.delta)) : String(Math.abs(t.delta));
  return { arrow, cls, text: `${mag} · ${t.window_days}d` };
}

function SummaryRow({ line }: { line: SummaryLine }) {
  const cls = line.state === "real" ? (line.tone ?? "") : line.state === "masked" ? "is-masked" : "is-missing";
  return (
    <div className="cp-wall__sumrow">
      <span className="cp-wall__sumk">{line.label}</span>
      <span className={`cp-wall__sumv num ${cls}`}>
        {line.state === "masked" && <Icon name="lock" size={11} />}
        {line.value}
      </span>
    </div>
  );
}

// ═══════════════════════════ 轻量浮层（U2 溯源 / U3 信任档共用）═══════════════════════════
// 固定定位锚到触发元素下方（rect 为触发钮 getBoundingClientRect），Esc / 点遮罩关闭，不是新页面
// 也不入路由。reduced-motion 由 styles.css 无障碍块统一尊重（.cp-pop 的入场动画在该偏好下静止）。
function Popover({ rect, label, onClose, children }: { rect: DOMRect; label: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const W = 308;
  const left = Math.max(10, Math.min(rect.left, window.innerWidth - W - 12));
  const openUp = rect.bottom > window.innerHeight * 0.62; // 触发钮偏下时向上弹，避免浮层被裁
  const style: CSSProperties = openUp
    ? { left, bottom: Math.max(12, window.innerHeight - rect.top + 6), width: W }
    : { left, top: rect.bottom + 6, width: W };
  return (
    <>
      <div className="cp-pop-scrim" onClick={onClose} />
      <div className={`cp-pop ${openUp ? "cp-pop--up" : ""}`} role="dialog" aria-label={label} style={style} onClick={(e) => e.stopPropagation()}>
        {children}
      </div>
    </>
  );
}

// U2 溯源浮层内容：口径白话 + 来源表/对象 + 样例 id（可跳透视镜）+ 完整血缘提示。
// F·P1：区名经 zoneShort 角色适配（非 manager 的"待我拍板"→"待批提案"，与卡面标题一致）。
function ProvBody({ zone, role, prov }: { zone: ZoneId; role?: Role; prov: ZoneProvenance }) {
  return (
    <>
      <div className="cp-pop__head">
        <Icon name="link" size={13} /> 数字溯源 · {zoneShort(zone, role)}
      </div>
      <div className="cp-pop__sect">
        <div className="cp-pop__k">口径（这个数怎么来的）</div>
        <div className="cp-pop__caliber">{prov.caliber}</div>
      </div>
      <div className="cp-pop__sect">
        <div className="cp-pop__k">来源表 / 对象</div>
        <div className="cp-pop__srcs">
          {prov.sources.map((s, i) => (
            <span key={i} className="cp-pop__src num">
              {s}
            </span>
          ))}
        </div>
      </div>
      <div className="cp-pop__sect">
        <div className="cp-pop__k">样例 id（{prov.sample_ids.length}）</div>
        {prov.sample_ids.length > 0 ? (
          <div className="cp-pop__samples num">{prov.sample_ids.map(String).join("　·　")}</div>
        ) : (
          <div className="cp-pop__muted">当前世界无样例（缺数或该域未灌）</div>
        )}
      </div>
      <div className="cp-pop__foot">
        {/* 悬空引用修复（P2，陈会计）：驾驶舱与透视镜是两个独立应用（不同端口），这里点不出去、
            原文案又像能跳转——改成不承诺跳转的如实表述，说清"去哪找"而非"点这里去"。 */}
        <Icon name="link" size={11} /> 完整血缘可在建造者透视镜（开发者工具）查看
      </div>
    </>
  );
}

// U3 信任档浮层内容：display-only 声明 + 整体白话 + 分域档位小列表（tier→白话名从 ladder 现取不手抄）。
function GatingBody({ gating }: { gating: GovernanceGating | null | undefined }) {
  if (!gating) {
    return (
      <>
        <div className="cp-pop__head">
          <Icon name="chip" size={13} /> AI 信任档
        </div>
        <StateHint kind="loading" compact title="信任档加载中…" />
      </>
    );
  }
  if (!gating.available) {
    return (
      <>
        <div className="cp-pop__head">
          <Icon name="chip" size={13} /> AI 信任档
        </div>
        <StateHint kind="empty" compact title="信任档尚未生成" reason={gating.reason} />
      </>
    );
  }
  const tierName: Record<string, string> = {};
  for (const step of gating.ladder) if (step.tier) tierName[step.tier] = String(step.name ?? step.tier);
  const label = (t: string | null) => (t ? tierName[t] ?? t : "—");
  return (
    <>
      <div className="cp-pop__head">
        <Icon name="chip" size={13} /> AI 信任档 · 分域档位
      </div>
      {gating.display_only && <div className="cp-pop__tag">display-only · 只算档不放权（档位≠已授权）</div>}
      {gating.summary_note && <div className="cp-pop__caliber">{gating.summary_note}</div>}
      <div className="cp-gating__list">
        {gating.domains.map((d) => (
          <div key={d.domain} className="cp-gating__row">
            <div className="cp-gating__top">
              <span className="cp-gating__name">{d.name}</span>
              <span className={`cp-gating__tier cp-gating__tier--${d.tier ?? "shadow"}`}>{label(d.tier)}</span>
            </div>
            <div className="cp-gating__meta num">
              n={d.n ?? 0}
              {d.next_tier ? ` · 下一档 ${label(d.next_tier)}` : ""}
            </div>
            {d.gaps.length > 0 && <div className="cp-gating__gap">差：{d.gaps[0]}</div>}
          </div>
        ))}
      </div>
      <div className="cp-pop__foot">
        {/* 悬空引用修复（P2，陈会计）：同上——点不出去，改为如实指路而非承诺跳转。 */}
        <Icon name="link" size={11} /> 档位阶梯 / 升档门槛完整白话可在建造者透视镜（开发者工具）的治理控制室查看
      </div>
    </>
  );
}

// AI 卡信任档徽章文案（紧凑，卡面窄）：主导档位（域数最多）→ 白话名（从 ladder 现取）。徽章前缀已
// 有"信任档"，此处只回档位摘要，不重复"信任档"三字。全部同档=「N 域·档名」，混合=「档名 x/N」。
function trustSummary(gating: GovernanceGating | null | undefined): { text: string; muted: boolean } {
  if (!gating) return { text: "加载中…", muted: true };
  if (!gating.available) return { text: "未生成", muted: true };
  const tierName: Record<string, string> = {};
  for (const s of gating.ladder) if (s.tier) tierName[s.tier] = String(s.name ?? s.tier);
  const entries = Object.entries(gating.tier_distribution ?? {});
  if (entries.length === 0) return { text: "无域", muted: true };
  entries.sort((a, b) => b[1] - a[1]);
  const [topTier, topCount] = entries[0];
  const total = entries.reduce((s, [, c]) => s + c, 0);
  const name = tierName[topTier] ?? topTier;
  return { text: entries.length === 1 ? `${total} 域·${name}` : `${name} ${topCount}/${total}`, muted: false };
}

// ═══════════════════════════ 今日焦点条（V22⑤）═══════════════════════════
// 指挥墙顶部横条，回答"30 秒说出今天最要紧的三件事"（陌生人测试李珊"七张卡全喊急"）。规则写死可解释
// （zoneModel.todaysFocus 现算，非 AI 排序）：一行紧凑排布，克制——无渐变无动画，纯既有 token 复用；
// 全部无数据时不渲染（不摆空架子，见 todaysFocus 注释）。button 语义保证键盘可达（全局 :focus-visible）。
function FocusBar({ items, onZone }: { items: FocusItem[]; onZone: (z: ZoneId) => void }) {
  if (items.length === 0) return null;
  return (
    <div className="cp-focus">
      <span className="cp-focus__label">
        <Icon name="spark" size={13} /> 今日焦点
      </span>
      <div className="cp-focus__items" role="list" aria-label="今日焦点 · 按优先级">
        {items.map((it) => (
          <button
            key={it.key}
            type="button"
            role="listitem"
            className="cp-focus__item"
            title={it.source}
            onClick={() => onZone(it.zone)}
          >
            {it.text}
          </button>
        ))}
      </div>
    </div>
  );
}

interface Props {
  zones: Zone[];
  role: Role; // V22 任务1：今日焦点条"你组 M 条"自队待批联动需当前角色（manager 不显）
  provenance?: Record<ZoneId, ZoneProvenance>; // U2 溯源信封（App 恒带 provenance=1 拉取）
  gating?: GovernanceGating | null; // U3 AI 信任档（App 取一次；null=加载中/失败）
  onZone: (z: ZoneId) => void;
  onMap: () => void;
}

type Pop = { kind: "prov"; zone: ZoneId; rect: DOMRect } | { kind: "gating"; rect: DOMRect };

export default function CommandWall({ zones, role, provenance, gating, onZone, onMap }: Props) {
  const items = resolveWall(role, zones);
  const focus = todaysFocus(zones, role);
  const [pop, setPop] = useState<Pop | null>(null);
  const openPop = (p: Pop) => setPop(p);

  // 面板头角色化：manager 保持"七区指挥墙"；其余角色标明"这是按你职责排的首屏"，并点破"首屏收窄≠
  // 权限收窄"（未摆出的区经搜索/对象卡仍全量可达，规格微调点 3）。
  const isManager = role === "manager";
  const headTitle = isManager ? "七区指挥墙" : `${roleLabel(role)} · 首屏`;
  const headMeta = isManager
    ? "告警区自动排前 · 点卡下钻工作队列 · 指标可溯源"
    : "按你的职责排布 · 点卡下钻 · 未摆出的区经搜索 / 对象卡仍全量可达";

  // ── 准入组合块卡（V24，sales/compliance）：复用 wall-card 骨架，数据取自 customers 区 admission_funnel。
  //    缺准入域（模拟世界无 admission_cases 表）→ headline"无数据" + reason 副行，诚实空态不填 0。
  const renderAdmissionCard = (funnel: AdmissionFunnel | Missing | undefined, title: string, drill: ZoneId): ReactNode => {
    const missingFunnel = funnel === undefined || isMissing(funnel);
    const f = missingFunnel ? null : (funnel as AdmissionFunnel);
    const actionable = f ? admissionActionable(f) : 0;
    const alerted = actionable > 0;
    const reason = isMissing(funnel) ? funnel.reason : "该世界无准入域数据（准入案表未灌）";
    return (
      <button
        key="admission"
        role="listitem"
        className={`cp-wall-card cp-wall-card--admission ${alerted ? "is-alerted" : ""} ${missingFunnel ? "is-nodata" : ""}`}
        onClick={() => onZone(drill)}
        title={missingFunnel ? reason : "准入案 · 点击下钻客户区看完整漏斗"}
      >
        {alerted && <span className={`cp-wall-card__alert ${actionable > 20 ? "" : "is-amber"}`}>{actionable > 99 ? "99+" : actionable}</span>}
        <div className="cp-wall-card__top">
          <span className="cp-wall-card__icon" aria-hidden>
            <Icon name="stamp" size={17} />
          </span>
          <span className="cp-wall-card__name">{title}</span>
        </div>
        <div className="cp-wall-card__headline">
          <span className={`cp-wall-card__big num ${missingFunnel ? "is-missing" : ""}`}>{missingFunnel ? "无数据" : formatInt(f!.cases_total)}</span>
          <span className="cp-trend cp-trend--none" title="准入案计数为当前值——无逐日趋势对比">当前值</span>
        </div>
        <div className="cp-wall-card__hllabel">准入案总数</div>
        <div className="cp-wall-card__sum">
          {missingFunnel ? (
            <div className="cp-wall-card__subnote" title="模拟世界未灌准入域；切验证世界可见真实准入漏斗">
              {reason}
            </div>
          ) : (
            admissionSummary(f!).map((line, i) => <SummaryRow key={i} line={line} />)
          )}
        </div>
      </button>
    );
  };

  // ── zone 区卡（复用现有渲染，抽成内部函数）：name 改为角色化 title、点击下钻改为配置的 drill 目标。
  //    manager 传入 title=zoneShort(z.zone,role)、drill=z.zone ⇒ 与改前 byte-identical。
  const renderZoneCard = (z: Zone, title: string, drill: ZoneId): ReactNode => {
            const hl = headlineOf(z);
            const nodata = hl.state === "missing"; // 只对真·无数据降饱和；掩码是权限态，显锁不降卡
            const trend = trendView(z);
            const alerted = z.alert_count > 0;
            const isFulfillment = z.zone === "fulfillment";
            const prov = provenance?.[z.zone];
            return (
              <button
                key={z.zone}
                role="listitem"
                className={`cp-wall-card cp-wall-card--${z.zone} ${alerted ? "is-alerted" : ""} ${nodata ? "is-nodata" : ""}`}
                onClick={() => onZone(drill)}
                title={z.headline_reason || `${z.headline_label} · 点击下钻`}
              >
                {alerted && <span className={`cp-wall-card__alert ${z.alert_count > 20 ? "" : "is-amber"}`}>{z.alert_count > 99 ? "99+" : z.alert_count}</span>}

                <div className="cp-wall-card__top">
                  <span className="cp-wall-card__icon" aria-hidden>
                    <Icon name={ZONE_ICON[z.zone]} size={17} />
                  </span>
                  {/* V24 角色化区名（口径诚实，见 ROLE_WALL）；manager 传入 = zoneShort 原名（byte-identical）。 */}
                  <span className="cp-wall-card__name">{title}</span>
                  {prov && (
                    <span
                      role="button"
                      tabIndex={0}
                      className="cp-prov-trigger"
                      aria-label={`${z.headline_label} 数字溯源`}
                      title="这个数怎么来的（口径 / 来源表 / 样例 id）"
                      onClick={(e) => {
                        e.stopPropagation();
                        openPop({ kind: "prov", zone: z.zone, rect: e.currentTarget.getBoundingClientRect() });
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          e.stopPropagation();
                          openPop({ kind: "prov", zone: z.zone, rect: (e.currentTarget as HTMLElement).getBoundingClientRect() });
                        }
                      }}
                    >
                      <Icon name="link" size={11} /> 溯源
                    </span>
                  )}
                </div>

                <div className="cp-wall-card__headline">
                  <span className={`cp-wall-card__big num ${hl.state === "real" ? "" : hl.state === "masked" ? "is-masked" : "is-missing"}`}>
                    {hl.state === "masked" && <Icon name="lock" size={14} />}
                    {hl.text}
                  </span>
                  {trend ? (
                    <span className={`cp-trend ${trend.cls}`}>
                      {trend.arrow} {trend.text}
                    </span>
                  ) : (
                    // 黑话消除（P2）：原"静态快照"是行话、两世界都读不懂——改白话"当前值"+ title 讲清
                    // 为什么没有涨跌箭头（该指标暂无逐日历史可对比，显示的就是此刻实时值，非造假趋势）。
                    <span className="cp-trend cp-trend--none" title="该指标暂无趋势对比数据——显示的是当前实时值">
                      当前值
                    </span>
                  )}
                </div>
                {/* 待拍板卡消歧（P2）：大数字=待批提案数，右上角徽章另有"超时/升级"告警数，两个数字贴太近
                    易被读成一个——大数字下补一行点明徽章口径（数据用已有 alert_count；为 0 不显=诚实空态）。 */}
                {z.zone === "decisions" && z.alert_count > 0 && (
                  <div className="cp-wall-card__subnote" title="超时/升级告警数=超期任务+升级件（右上角徽章即此数）；这是另一口径的计数，别把右上角徽章读成待批提案数">
                    其中超时/升级 {z.alert_count}
                  </div>
                )}
                <div className="cp-wall-card__hllabel">
                  {z.headline_label}
                  {/* 回放态诚实标注：headline_as_of 仅回放时下发。current=该指标无时点历史→显当前值（不造假），
                      replayed=真按时点重算。存量类的小灰标是本任务"诚实边界"的画面兑现。 */}
                  {z.headline_as_of === "current" && (
                    <span className="cp-asof-tag is-current" title="该指标无时点历史·回放时显示当前值">显示当前值</span>
                  )}
                  {z.headline_as_of === "replayed" && (
                    <span className="cp-asof-tag is-replayed" title="按所选时点重算">回放</span>
                  )}
                </div>

                <div className="cp-wall-card__sum">
                  {summaryLines(z).map((line, i) => (
                    <SummaryRow key={i} line={line} />
                  ))}
                </div>

                {/* U3：AI 运营账卡挂 AI 当前信任档徽章（display-only：只算档不放权）·点开看分域小列表 */}
                {z.zone === "ai" && (
                  <span
                    role="button"
                    tabIndex={0}
                    className={`cp-trust-badge ${trustSummary(gating).muted ? "is-muted" : ""}`}
                    title="AI 当前信任档（display-only：只算档不放权）· 点开看分域档位"
                    onClick={(e) => {
                      e.stopPropagation();
                      openPop({ kind: "gating", rect: e.currentTarget.getBoundingClientRect() });
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        e.stopPropagation();
                        openPop({ kind: "gating", rect: (e.currentTarget as HTMLElement).getBoundingClientRect() });
                      }
                    }}
                  >
                    <Icon name="chip" size={11} /> 信任档 · {trustSummary(gating).text}
                  </span>
                )}

                {isFulfillment && (
                  <div
                    className="cp-wall-card__switch"
                    role="button"
                    tabIndex={0}
                    aria-label="切换到航线地图"
                    onClick={(e) => {
                      e.stopPropagation();
                      onMap();
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        e.stopPropagation();
                        onMap();
                      }
                    }}
                  >
                    <Icon name="ship" size={15} />
                    <span>航线视图</span>
                    <Icon name="arrow-right" size={13} />
                  </div>
                )}
              </button>
            );
  };

  return (
    <div className="cp-wall-wrap">
      <div className="cp-panel-head">
        <span className="cp-panel-head__title">{headTitle}</span>
        <span className="cp-panel-head__meta">{headMeta}</span>
      </div>
      <FocusBar items={focus} onZone={onZone} />
      {items.length === 0 ? (
        <StateHint
          kind="empty"
          title="首屏暂无体征"
          reason="当前世界没有可展示的掌控区数据。"
          suggestion="切换世界，或确认该世界数据已灌入。"
        />
      ) : (
        <div className="cp-wall" role="list">
          {items.map((it) =>
            it.kind === "admission"
              ? renderAdmissionCard(it.funnel, it.title, it.drill)
              : renderZoneCard(it.zone, it.title, it.drill),
          )}
        </div>
      )}

      {pop && (
        <Popover
          rect={pop.rect}
          label={pop.kind === "prov" ? "数字溯源" : "AI 信任档"}
          onClose={() => setPop(null)}
        >
          {pop.kind === "prov" ? (
            provenance?.[pop.zone] ? (
              <ProvBody zone={pop.zone} role={role} prov={provenance[pop.zone]} />
            ) : (
              <StateHint kind="empty" compact title="无溯源信息" reason="该指标未附溯源信封。" />
            )
          ) : (
            <GatingBody gating={gating} />
          )}
        </Popover>
      )}
    </div>
  );
}
