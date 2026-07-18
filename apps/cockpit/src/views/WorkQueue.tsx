import type { ReactNode } from "react";
import Icon from "../components/Icons";
import StateHint from "../components/StateHint";
import type { DrillTarget, QueueSpec } from "./zoneModel";

// 下钻四段式第二段"工作队列"的通用呈现件（区队列 / 航线队列共用）。
// 面包屑（指挥墙 > …）可逐级返回；队列条目排序沿用 API 现成序（不前端造序），每条带关键字段
// + 状态徽标，点条目 → onDrill（右侧滑出详情 / 打开对象卡）。无队列（存量聚合区）→ emptyHint +
// 聚合上下文承接。动作区占位在详情面板（ImpactPanel）底部，此处只做"看清 + 选一条"。

export interface Crumb {
  label: string;
  onClick?: () => void; // 末级无 onClick（当前位置）
}

interface Props {
  crumbs: Crumb[];
  title: string;
  alertCount?: number;
  /** 徽章文案（缺省"告警"）。可读性修正 2026-07-17：待拍板区徽章=超期+升级数，贴着"待批提案"
      标题放会被读成待批数（Daniel 实测困惑）——由调用方按区传消歧文案与 title 白话。 */
  alertLabel?: string;
  alertTitle?: string;
  headerActions?: ReactNode;
  spec: QueueSpec | null;
  onDrill: (t: DrillTarget, key: string) => void;
  emptyHint?: ReactNode;
  context?: ReactNode;
  activeKey?: string | null; // 当前已滑出详情的条目（左缘高亮）
}

const badgeClass = (tone: "red" | "amber" | "neutral") => (tone === "red" ? "cp-chip red" : tone === "amber" ? "cp-chip amber" : "cp-chip");

export default function WorkQueue({ crumbs, title, alertCount, alertLabel, alertTitle, headerActions, spec, onDrill, emptyHint, context, activeKey }: Props) {
  const rows = spec?.rows ?? [];
  return (
    <div className="cp-zone">
      <div className="cp-panel-head">
        <nav className="cp-crumbs" aria-label="下钻路径">
          {crumbs.map((c, i) => (
            <span key={i} className="cp-crumb-wrap">
              {i > 0 && <Icon name="chevron-right" size={12} className="cp-crumb__sep" />}
              {c.onClick ? (
                <button className="cp-crumb is-link" onClick={c.onClick}>
                  {c.label}
                </button>
              ) : (
                <span className="cp-crumb is-current">{c.label}</span>
              )}
            </span>
          ))}
        </nav>
        <span className="cp-panel-head__title">{title}</span>
        {/* 头部"共 N 条"标注（防静默截断，P1/P2）：由 QueueSpec.countLabel 提供，展示行数与全量数
            一致时=「共 N 条·按金额降序」，被截时=「共 N 条·显示前 M 条」如实注明。 */}
        {spec?.countLabel && <span className="cp-panel-head__meta">{spec.countLabel}</span>}
        {alertCount != null && alertCount > 0 && (
          <span className="cp-chip red" title={alertTitle}>{alertCount} {alertLabel ?? "告警"}</span>
        )}
        <span className="cp-panel-head__spacer" />
        {headerActions}
      </div>

      <div className="cp-zone__scroll">
        {spec && rows.length > 0 ? (
          <div className="cp-queue">
            <table className="cp-table cp-queue__table">
              <thead>
                <tr>
                  <th aria-label="状态" />
                  {spec.columns.map((c) => (
                    <th key={c.label} className={c.num ? "num" : ""}>
                      {c.label}
                    </th>
                  ))}
                  <th aria-label="下钻" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  // a11y（P1，L-UX 轮2）：可点行原先 tabIndex=-1/无 role/无键盘事件——键盘/读屏用户无法
                  // 进入详情。不可点行（无 drill）保持不可聚焦、不加 role，与可点行视觉/语义都区分开。
                  const clickable = !!r.drill;
                  const rowLabel = [r.badge?.text, ...r.cells.map((c) => c.text)].filter(Boolean).join(" · ");
                  return (
                    <tr
                      key={r.key}
                      className={`${clickable ? "is-click" : ""} ${activeKey === r.key ? "is-active" : ""}`}
                      onClick={() => r.drill && onDrill(r.drill, r.key)}
                      tabIndex={clickable ? 0 : undefined}
                      role={clickable ? "button" : undefined}
                      aria-label={clickable ? `打开详情：${rowLabel}` : undefined}
                      onKeyDown={
                        clickable
                          ? (e) => {
                              if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault();
                                if (r.drill) onDrill(r.drill, r.key);
                              }
                            }
                          : undefined
                      }
                    >
                      <td className="cp-queue__badge">{r.badge ? <span className={badgeClass(r.badge.tone)}>{r.badge.text}</span> : null}</td>
                      {r.cells.map((cell, ci) => (
                        <td key={ci} className={`${cell.num ? "num" : ""} ${ci === 0 ? "name" : ""} ${cell.tone ?? ""}`}>
                          {cell.masked && <Icon name="lock" size={11} />}
                          {cell.text}
                        </td>
                      ))}
                      <td className="cp-queue__go">{r.drill && <Icon name="chevron-right" size={13} />}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="cp-basis">{spec.basis}</div>
          </div>
        ) : (
          // U4 三态：空态统一走 StateHint（emptyHint 白话作 reason）——原 cp-queue__empty 无图标
          // 不成体系（见三态报告接入点）。下方 context 聚合仍照常承接，故此处不是死胡同、只是"无逐条队列"。
          emptyHint && <StateHint kind="empty" title="暂无队列条目" reason={emptyHint} />
        )}

        {context && <div className="cp-zone__context">{context}</div>}
      </div>
    </div>
  );
}
