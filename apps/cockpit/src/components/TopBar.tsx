import { useEffect, useState, type CSSProperties } from "react";
import type { Role, DataWindow, World } from "../api";
import Icon from "./Icons";

// 顶栏常驻：世界切换钮（验证世界 ⇄ 模拟世界，U1）+ 世界时钟时间轴（可拖回放，A-2/V13②）+ 角色切换器。
// 时间轴：范围=数据窗口 windowRange（start=最早真实事件日、end=世界今天），拖动/播放到过去某天 →
// App 带 as_of 令三聚合端点重算（诚实边界：能真回放的重算、存量类如实显当前值，服务端标注）。
// asOf=null 即"今天"（现状不变，不带 as_of 参数）；clock（世界今天）不随拖动改，恒为右端锚。
interface Props {
  activeWorld: World | null; // 当前生效世界（null=首屏尚未确定，跟随服务端默认）——驱动切换钮高亮
  onWorld: (w: World) => void; // 切世界（App 会回今天 + 清下钻）
  clock: string | null;
  windowRange: DataWindow | null;
  asOf: string | null;
  onAsOf: (d: string | null) => void;
  replayNote: string | null;
  role: Role;
  onRole: (r: Role) => void;
}

const ROLE_LABELS: Record<Role, string> = { manager: "老板 manager", ops: "运营 ops" };
const ROLE_HINT: Record<Role, string> = {
  manager: "汇总粒度 · 金额可见",
  ops: "可操作粒度 · 金额脱敏",
};

// 世界切换钮两档（U1）：验证世界=datagen 种子库（规则档案 R/P=1.000 对照源，静态快照）；
// 模拟世界=14 个月连续活世界（时间回放完整威力）。颜色沿用既有 cp-world 语义（绿=验证、蓝=模拟）。
const WORLDS: { id: World; label: string; cls: string; hint: string }[] = [
  { id: "verify", label: "验证世界", cls: "cp-world-seg--verification",
    hint: "验证世界：datagen 种子库，规则档案对照源（静态快照，回放退化为时钟）" },
  { id: "sim", label: "模拟世界", cls: "cp-world-seg--simulation",
    hint: "模拟世界：14 个月连续活世界，时间回放完整威力" },
];

// 轮2·P2-14：双世界切换显著性小改（王总"差点全用空样本做判断"）。
// ① 当前世界高亮加强——在既有 .is-active 底色上叠加更强的同色内环 + 加粗 + ✓ 前缀（用组件内 style 常量，
//    不碰 styles.css）；② 切换钮下补一行小字，如实点明两世界性质（用词对齐既有文案体系）。不改默认世界。
const ACTIVE_SEG_STYLE: CSSProperties = {
  background: "color-mix(in srgb, currentColor 22%, transparent)",
  boxShadow: "inset 0 0 0 1px color-mix(in srgb, currentColor 55%, transparent)",
  fontWeight: 800,
};
const WORLDS_WRAP_STYLE: CSSProperties = {
  display: "inline-flex", flexDirection: "column", alignItems: "flex-start", gap: "2px",
};
const WORLD_CAPTION_STYLE: CSSProperties = {
  fontSize: "9.5px", lineHeight: 1, letterSpacing: "0.01em", whiteSpace: "nowrap",
  color: "var(--ink-2)", paddingLeft: "2px",
};

// —— 纯日期工具（UTC，避开时区漂移；窗口为日粒度）——
const DAY = 86_400_000;
const toDate = (s: string) => new Date(`${s}T00:00:00Z`);
const iso = (d: Date) => d.toISOString().slice(0, 10);
const daysBetween = (a: string, b: string) => Math.round((toDate(b).getTime() - toDate(a).getTime()) / DAY);
const addDays = (s: string, n: number) => iso(new Date(toDate(s).getTime() + n * DAY));

// 世界时钟时间轴滑条（回放核心）：拖动=选时点、播放=自动推进到今天定格。
function TimeScrubber({ start, end, asOf, onAsOf }: { start: string; end: string; asOf: string | null; onAsOf: (d: string | null) => void }) {
  const totalDays = Math.max(0, daysBetween(start, end));
  const [dragIdx, setDragIdx] = useState<number | null>(null); // 拖动中的即时手柄位（松手才提交，免刷爆 API）
  const [playing, setPlaying] = useState(false);
  const [playCursor, setPlayCursor] = useState(0);
  const step = Math.max(1, Math.ceil(totalDays / 30)); // 播放约 30 帧走完整窗，与窗口跨度无关

  // 播放：以 playCursor 为独立游标逐帧推进（不依赖 asOf 闭包），asOf 跟随；到今天则定格并复位。
  useEffect(() => {
    if (!playing) return;
    const t = setTimeout(() => {
      const next = playCursor + step;
      if (next >= totalDays) {
        onAsOf(null); // 回到今天=现状，退出回放
        setPlaying(false);
      } else {
        setPlayCursor(next);
        onAsOf(addDays(start, next));
      }
    }, 750);
    return () => clearTimeout(t);
  }, [playing, playCursor, start, totalDays, step, onAsOf]);

  const replaying = asOf !== null;
  const valueIdx = dragIdx ?? (asOf ? Math.min(totalDays, Math.max(0, daysBetween(start, asOf))) : totalDays);
  const commit = (idx: number) => {
    setDragIdx(null);
    onAsOf(idx >= totalDays ? null : addDays(start, idx)); // 拖到最右=今天=退出回放
  };
  const togglePlay = () => {
    if (playing) {
      setPlaying(false);
      return;
    }
    if (totalDays <= 0) return;
    const from = asOf ? daysBetween(start, asOf) : 0; // 在今天按播放=从窗口起点重放
    const startIdx = from >= totalDays ? 0 : Math.max(0, from);
    setPlayCursor(startIdx);
    onAsOf(addDays(start, startIdx));
    setPlaying(true);
  };

  return (
    <div className={`cp-scrub ${replaying ? "is-replay" : ""}`}>
      <button
        className="cp-scrub__play"
        onClick={togglePlay}
        aria-label={playing ? "暂停回放" : "播放回放"}
        title={playing ? "暂停" : "从头播放世界演进"}
      >
        <Icon name={playing ? "pause" : "play"} size={12} />
      </button>
      <div className="cp-scrub__track">
        <input
          className="cp-scrub__range"
          type="range"
          min={0}
          max={totalDays}
          step={1}
          value={valueIdx}
          aria-label="世界时钟时间轴"
          onChange={(e) => setDragIdx(Number(e.target.value))}
          onPointerUp={(e) => commit(Number((e.target as HTMLInputElement).value))}
          onKeyUp={(e) => commit(Number((e.target as HTMLInputElement).value))}
        />
        <div className="cp-scrub__ends num">
          <span>{start}</span>
          <span>今天 {end}</span>
        </div>
      </div>
      <span className={`cp-scrub__at num ${replaying ? "is-replay" : ""}`}>
        {replaying ? (
          <>
            <span className="cp-scrub__dot" />回放 {asOf}
          </>
        ) : (
          <>实时 {end}</>
        )}
      </span>
      {replaying && (
        <button className="cp-scrub__now" onClick={() => { setPlaying(false); onAsOf(null); }} title="回到今天（退出回放）">
          回到今天
        </button>
      )}
    </div>
  );
}

export default function TopBar({ activeWorld, onWorld, clock, windowRange, asOf, onAsOf, replayNote, role, onRole }: Props) {
  const scrubbable = !!(windowRange && windowRange.start && windowRange.end && windowRange.start < windowRange.end);
  return (
    <header className="cp-topbar">
      <span className="cp-topbar__brand">控制塔驾驶舱</span>
      <div style={WORLDS_WRAP_STYLE}>
        <div className={`cp-worlds cp-worlds--${activeWorld ?? "pending"}`} role="group" aria-label="世界切换">
          {WORLDS.map((wd) => {
            const active = activeWorld === wd.id;
            return (
              <button
                key={wd.id}
                className={`cp-world-seg ${wd.cls} ${active ? "is-active" : ""}`}
                style={active ? ACTIVE_SEG_STYLE : undefined}
                onClick={() => onWorld(wd.id)}
                aria-pressed={active}
                title={wd.hint}
              >
                {active && <span aria-hidden="true" style={{ fontWeight: 800 }}>✓</span>}
                {wd.label}
              </button>
            );
          })}
        </div>
        <span style={WORLD_CAPTION_STYLE}>
          验证世界=固定小快照 · 模拟世界=14 个月连续活世界
        </span>
      </div>
      {scrubbable ? (
        <span title={replayNote ?? "拖动或播放回放世界时钟；能真回放的重算，存量类显当前值"}>
          <TimeScrubber start={windowRange!.start!} end={windowRange!.end!} asOf={asOf} onAsOf={onAsOf} />
        </span>
      ) : (
        <span className="cp-clock">
          世界时钟 <b className="num">{clock ?? "—"}</b>
        </span>
      )}
      <span className="cp-topbar__spacer" />
      <span className="cp-role-hint">{ROLE_HINT[role]}</span>
      <div className="cp-roles" role="group" aria-label="角色缩放">
        {(["manager", "ops"] as Role[]).map((r) => (
          <button
            key={r}
            className={`cp-role-btn ${r === role ? "is-active" : ""}`}
            onClick={() => onRole(r)}
            aria-pressed={r === role}
          >
            {ROLE_LABELS[r]}
          </button>
        ))}
      </div>
    </header>
  );
}
