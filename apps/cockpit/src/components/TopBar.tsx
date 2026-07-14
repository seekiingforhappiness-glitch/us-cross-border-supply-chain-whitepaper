import type { Role } from "../api";

// 顶栏常驻：世界徽标（verification/simulation）+ 世界时钟（vitals.clock，D8 库内推导非系统时间）
// + 角色切换器（manager/ops 两档起步）。世界/时钟由 App 从 vitals 载荷透传，角色回调改 X-Role。
interface Props {
  world: string | null;
  clock: string | null;
  role: Role;
  onRole: (r: Role) => void;
}

const ROLE_LABELS: Record<Role, string> = { manager: "老板 manager", ops: "运营 ops" };
const ROLE_HINT: Record<Role, string> = {
  manager: "汇总粒度 · 金额可见",
  ops: "可操作粒度 · 金额脱敏",
};

function worldLabel(world: string | null): { cls: string; text: string } {
  if (world === "verification") return { cls: "cp-world--verification", text: "验证世界" };
  if (world === "simulation") return { cls: "cp-world--simulation", text: "模拟世界" };
  return { cls: "cp-world--simulation", text: world ?? "未连接" };
}

export default function TopBar({ world, clock, role, onRole }: Props) {
  const w = worldLabel(world);
  return (
    <header className="cp-topbar">
      <span className="cp-topbar__brand">控制塔驾驶舱</span>
      <span className={`cp-world ${w.cls}`}>{w.text}</span>
      <span className="cp-clock">
        世界时钟 <b className="num">{clock ?? "—"}</b>
      </span>
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
