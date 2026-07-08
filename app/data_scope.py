"""行级数据范围（data scoping）：角色 → demo actor/region → 行级过滤谓词。

设计动机（≤5 行「为什么这样建」）：
- 上一轮做 tab 级角色导航；这一轮做「同一工作台内不同人只看与自己相关的行」，
  对标 project44 Access groups / CargoWise 网点绑定 / NetSuite WMS 仓库绑定。
- 纯函数、零 Streamlit 依赖：可被 streamlit_app 与 test_data_scope 直接 import，可单测。
- **只过滤视图不删数据、不放宽任何动作**：动作层权限仍由 actions.py ROLE_PERMS + maker-checker 硬 gate。
- 复用 actions.DEMO_ROSTER（只读），不改动作层任何基础设施。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

try:  # 包上下文（python3 -m app.*）
    from .actions import DEMO_ROSTER, DEFAULT_DEMO_REGION
except ImportError:  # streamlit run app/streamlit_app.py：脚本目录在 sys.path
    from actions import DEMO_ROSTER, DEFAULT_DEMO_REGION

# 三种数据范围 mode
MODES = ("mine", "team", "all")
MODE_LABELS = {"mine": "我的任务", "team": "本组", "all": "全部"}

# 角色默认 mode：manager 监督全局不受限 → all；其余落到 mine/team
DEFAULT_MODE = {
    "manager": "all",
    "ops": "mine",
    "cs": "mine",
    "finance": "team",
    "compliance": "team",
    "sales": "team",
}


def default_mode(role: str) -> str:
    """角色默认数据范围 mode。未知角色回退 all（不误挡数据）。"""
    return DEFAULT_MODE.get(role, "all")


def resolve_actor(role: str, roster=DEMO_ROSTER):
    """role → (actor_id, region)：取 roster 第一个匹配的 active owner。

    无匹配（如 sales/compliance 未在 roster）→ (None, DEFAULT_DEMO_REGION)，
    此时 mine 谓词恒 False、team 退化到默认 region——只影响视图，绝不放宽动作。
    """
    for owner in roster:
        if owner.active and owner.role == role:
            return owner.actor_id, owner.region
    return None, DEFAULT_DEMO_REGION


def region_of_locode(locode: Optional[str]) -> str:
    """destination LOCODE → region（US.. → US；CN.. → CN；其余回退默认）。"""
    if locode:
        code = str(locode).upper()
        if code.startswith("US"):
            return "US"
        if code.startswith("CN"):
            return "CN"
    return DEFAULT_DEMO_REGION


def _task_region(task) -> Optional[str]:
    """从 assignee_team_id（team-<role>-<region>）反解 region。"""
    tid = task.get("assignee_team_id") or ""
    parts = str(tid).rsplit("-", 1)
    return parts[-1].upper() if len(parts) == 2 and parts[-1] else None


def _label(role: str, region: Optional[str], mode: str) -> str:
    """数据域显示串：manager→Global；否则 '<region> · <mode 中文>'。"""
    if role == "manager":
        return "Global"
    reg = region or DEFAULT_DEMO_REGION
    if mode == "all":
        return f"{reg} · 全部"
    return f"{reg} · {MODE_LABELS[mode]}"


@dataclass(frozen=True)
class Scope:
    """一个已解析的数据范围：可读 label + 任务行过滤（python 谓词 与 SQL WHERE 两条路等价）。"""
    role: str
    actor_id: Optional[str]
    region: Optional[str]
    mode: str            # mine | team | all
    label: str           # 数据域串："US · 我的任务" / "Global"

    def matches_task(self, task) -> bool:
        """任务行是否落在范围内（视图级过滤，不改任何数据）。"""
        if self.mode == "all":
            return True
        if self.mode == "mine":
            return self.actor_id is not None and task.get("assignee_user_id") == self.actor_id
        # team：同 region（网点/团队绑定）
        return _task_region(task) == self.region

    def task_where(self, alias: str = "t"):
        """等价 SQL WHERE 片段（不含 WHERE 关键字）+ 参数；all → ('1=1', [])。"""
        if self.mode == "all":
            return "1=1", []
        if self.mode == "mine":
            # actor_id 为 None 时用不可能匹配的常量，等价 python 谓词的恒 False
            return f"{alias}.assignee_user_id = ?", [self.actor_id or "__no_actor__"]
        return f"{alias}.assignee_team_id LIKE ?", [f"%-{(self.region or '').lower()}"]


def scope_predicate(role: str, actor_id: Optional[str], region: Optional[str], mode: str) -> Scope:
    """核心：由 (role, actor_id, region, mode) 构造 Scope。

    manager 强制 all（监督全局、不受限）；非法 mode 回退角色默认。
    """
    if mode not in MODES:
        mode = default_mode(role)
    if role == "manager":
        mode = "all"
    return Scope(role=role, actor_id=actor_id, region=region, mode=mode,
                 label=_label(role, region, mode))


def scope_for_role(role: str, mode: Optional[str] = None, roster=DEMO_ROSTER) -> Scope:
    """便捷入口：解析 role 的 demo actor/region，按 mode（缺省用角色默认）构造 Scope。"""
    actor_id, region = resolve_actor(role, roster)
    return scope_predicate(role, actor_id, region, mode or default_mode(role))


def risk_in_region_scope(scope: Scope, dest_locode: Optional[str]) -> bool:
    """风险行（按 shipment 目的地 region）是否落在范围内；all → 恒 True（不挡 demo 走查）。"""
    if scope.mode == "all":
        return True
    return region_of_locode(dest_locode) == scope.region
