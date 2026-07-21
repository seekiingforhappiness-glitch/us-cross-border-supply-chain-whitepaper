# Control Tower Maturity Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the current simulation-first cross-border supply-chain ontology prototype into a more mature, auditable control-tower operating prototype without breaking the project rule that it is not a real enterprise system.

**Architecture:** Keep the existing Python + SQLite + Streamlit stack and add maturity in layers: governance boundary, operational work queues, event lineage, MDM, graph traversal, data-quality operations, simulated integration writeback, AI governance, and only then larger business modules. Every ontology or scope expansion is gated by Daniel approval before code changes.

**Tech Stack:** Python 3.11+, pandas, SQLite, Streamlit, PyYAML, standard-library dataclasses/uuid/json/datetime; no new runtime dependencies unless a later approved plan explicitly changes this.

---

## Scope Check

This audit covers several independent subsystems. Do not execute it as one large branch. Treat this as an umbrella plan with independently reviewable waves:

1. Governance and action safety.
2. Source truth, event lineage, MDM, and graph traversal.
3. Operational loops for tasks, data quality, and integration writeback.
4. AI governance and LLM verification.
5. Business expansion modules: inventory/WMS, customs/documents, transportation execution, cost recovery.

Each wave must produce runnable software or a decision document on its own. For tasks that add objects, rules, noise types, or scope, append the decision to the authoritative plan only after Daniel approves the business question.

## Non-Negotiables

- Do not connect to real ERP, WMS, TMS, broker, carrier, accounting, or customs systems.
- Do not use real enterprise data.
- Do not add a new framework, graph database, auth provider, or production database in this plan.
- Do not modify `engine/evaluate.py` or ground-truth generated data to make results pass.
- Do not touch `docs/cross-border-ontology-manual.md` or `ontology/sku-admission-ontology.json`; they remain v0.1 read-only reference assets.
- All engine/UI time logic must use explicit `as_of_date`.
- Every non-trivial modeling choice must include a short "为什么这样建" note in the relevant plan/manual.

## Decision Gates Before Code

Ask Daniel these business questions before Task 1 implementation begins:

1. **Identity depth:** Should this prototype model named demo users and maker-checker controls, or stay role-only for learning simplicity?
2. **MDM scope:** Should cross-system identity resolution cover only shipment/customer/SKU, or also supplier/vendor/broker/carrier/warehouse?
3. **Graph model:** Should relationships remain query-specific SQL, or should we introduce a generic `object_relationships` registry table for traversal and explanation?
4. **Data-quality operations:** Should unresolved source records become operational tasks visible to ops, or remain build-time diagnostics?
5. **Integration simulation:** Should writeback be represented as an outbox with retry states even though no real external systems are called?
6. **First business expansion:** Choose one next domain after platform maturity: inventory/WMS, customs/documents, transportation execution, or cost recovery.

Approval output must be recorded in a new append-only section in `docs/control-tower-plan-v0.2.md` before code that changes ontology or scope.

## File Structure Map

Planned new files:

- `docs/control-tower-maturity-gap-assessment.md` - cleaned, source-backed gap list from the multi-agent audit.
- `docs/demo-assertions-maturity.md` - maturity acceptance assertions for W7+.
- `docs/inventory-control-tower-plan-v0.8.md` - optional later business module plan.
- `docs/customs-document-plan-v0.9.md` - optional later business module plan.
- `docs/transport-execution-plan-v1.0.md` - optional later business module plan.
- `docs/cost-recovery-plan-v1.1.md` - optional later business module plan.
- `app/action_context.py` - typed actor, maker-checker, stable ID, and transaction helpers.
- `app/work_queue.py` - named assignment, SLA status, escalation candidate selection.
- `app/dq_actions.py` - remediation actions for data-quality issues.
- `app/integration_actions.py` - internal actions for simulated writeback outbox.
- `app/maturity_views.py` - pure view-model builders for mature workbench sections.
- `app/test_action_governance.py` - script-style tests for identity, SoD, and audit.
- `app/test_work_queue.py` - script-style tests for owner assignment and SLA.
- `app/test_dq_loop.py` - script-style tests for DQ issue remediation.
- `app/test_outbox.py` - script-style tests for writeback idempotency and retry.
- `app/test_maturity_views.py` - script-style tests for mature workbench view models.
- `pipeline/event_envelope.py` - canonical source-event envelope and raw lineage normalization.
- `pipeline/mdm.py` - deterministic crosswalk resolver for source-system identifiers.
- `pipeline/dq_issues.py` - row-level DQ issue records from parking tables and DQ reports.
- `pipeline/outbox.py` - outbox schema helpers and idempotency key generator.
- `pipeline/test_event_envelope.py` - tests for canonical event semantics.
- `pipeline/test_mdm.py` - tests for crosswalk resolution and ambiguity handling.
- `engine/graph.py` - reusable graph traversal over SQLite relationships.
- `engine/evaluate_maturity.py` - maturity evaluation harness.
- `engine/test_graph.py` - traversal tests.
- `agent/privacy.py` - tool-output minimization and role-aware redaction before LLM calls.
- `agent/test_privacy.py` - privacy and redaction tests.

Planned modified files:

- `STATUS.md` - state source updated after each accepted task.
- `README.md` - navigation updated after each accepted wave.
- `docs/control-tower-plan-v0.2.md` - append M-series decisions only after Daniel approval.
- `docs/control-tower-ontology-manual.md` - updated only for approved mature objects/actions.
- `docs/demo-assertions.md`, `docs/demo-assertions-admission.md`, `docs/demo-assertions-cost.md` - unchanged unless a task explicitly expands an existing assertion.
- `ontology/control-tower-ontology.json` - updated only after approval for new objects/fields/actions.
- `config/datagen.yaml` - add approved source-event, MDM, SLA, and DQ parameters.
- `datagen/generate.py`, `datagen/world.py`, `datagen/noise.py`, `datagen/design_cases.py` - add approved simulated maturity data.
- `pipeline/build_ontology.py`, `pipeline/evaluate.py`, `pipeline/er.py` - add approved schema, normalization, DQ, lineage, and MDM checks.
- `engine/detect.py`, `engine/rules.py`, `engine/evaluate.py`, `engine/evaluate_cost.py` - reuse graph and mature event context without changing existing truth semantics.
- `app/actions.py`, `app/admission_actions.py`, `app/streamlit_app.py` - enforce action boundary, owner/SLA flows, and mature workbench views.
- `agent/tools.py`, `agent/llm_agent.py`, `agent/evaluate.py`, `agent/eval_cases.yaml` - add privacy controls and LLM verification.

---

### Task 0: Baseline Approval Pack

**Files:**
- Create: `docs/control-tower-maturity-gap-assessment.md`
- Create: `docs/demo-assertions-maturity.md`
- Modify: `STATUS.md`
- No code modules changed.

- [ ] **Step 1: Create the gap assessment document**

Write `docs/control-tower-maturity-gap-assessment.md` with this structure:

```markdown
# Control Tower Maturity Gap Assessment

更新时间：2026-07-07

## 审计口径

本清单以成熟跨境供应链控制塔为参照，但本仓库仍保持 simulation-first 原型边界：不接真实企业系统、不使用真实企业数据、不做真实报关逻辑。

## P0 必须先裁决

| 缺口 | 当前状态 | 业务问题 | 推荐裁决 |
| --- | --- | --- | --- |
| Identity/RBAC | Streamlit 角色切换 + role 字符串校验 | 是否需要模拟实名用户和 maker-checker？ | 建议建 demo user + SoD，不接 SSO |
| Integration | datagen + SQLite | 是否需要模拟 outbox/writeback？ | 建议模拟 outbox，不接真实系统 |
| MDM | booking/container/supplier 局部 ER | MDM 覆盖哪些对象？ | 建议 shipment/customer/sku/supplier 先行 |
| Customs/Documents | 只有 customs_status/missing_docs | 是否把单证作为下一业务模块？ | 等 Daniel 在四个业务模块中选择 |
| Inventory/WMS | 明确未建库存闭环 | 是否把库存作为下一业务模块？ | 等 Daniel 在四个业务模块中选择 |

## 成熟度目标

1. 任何动作都能追溯到 named actor、role、policy、before/after、as_of_date。
2. 任何源事件都能追溯到 source_system、source_record_id、message_id、ingested_at、transform_version。
3. 任何异常都能分配到 owner，有 SLA、升级规则和关闭原因。
4. 任何 DQ 停车记录都能转为可处理的 DQ issue。
5. 任何 AI 回答只能看到当前角色允许的最小字段集。
```

- [ ] **Step 2: Create maturity demo assertions**

Write `docs/demo-assertions-maturity.md`:

```markdown
# Maturity Upgrade Demo Assertions

## W7 Governance

- [ ] M1. Ops 用户提交 mitigation 后，同一用户即使切换 manager role 也不能审批自己的提案。
- [ ] M2. 所有动作审计包含 actor_id、role、policy_version、target_object、before_state、after_state、as_of_date。
- [ ] M3. Action ID 生成不使用 count(*) + 1，重复运行测试不会产生 ID 冲突。

## W8 Source Truth

- [ ] M4. 一个 milestone 源事件可追溯到 canonical event envelope 和 raw payload 摘要。
- [ ] M5. booking_no/container_no 解析失败时进入 DQ issue，而不是被静默丢弃。
- [ ] M6. 对同一 source message 重放不会创建重复 RiskEvent 或重复 writeback。

## W9 Operations

- [ ] M7. Task 有 named owner、due_at、sla_state、escalation_level。
- [ ] M8. 逾期任务在指定 as_of_date 下被标为 overdue，并生成升级候选。
- [ ] M9. DQ issue 能被分派、修复、关闭，并写审计。

## W10 AI

- [ ] M10. LLM payload 不包含当前角色不可见字段。
- [ ] M11. Prompt-injection 测试不能调用未注册审批工具。
- [ ] M12. OpenAI LLM 实测结果记录 provider、model、tool policy version。
```

- [ ] **Step 3: Run document checks**

Run:

```bash
rtk git diff --check
rtk rg "T[B]D|TO[D]O|fill in detail[s]|implement late[r]" docs/control-tower-maturity-gap-assessment.md docs/demo-assertions-maturity.md
```

Expected:

```text
no output from git diff --check
no matches from rg
```

- [ ] **Step 4: Update status**

Add this bullet under `## 当前位置` in `STATUS.md`:

```markdown
- **成熟控制塔全面升级计划已立项但未批准执行**：计划路径 `docs/superpowers/plans/2026-07-07-control-tower-maturity-upgrade.md`；下一步是 Daniel 裁决 M 系列决策门槛，未获批前不改 ontology/规则/业务范围。
```

- [ ] **Step 5: Commit**

Run:

```bash
rtk git add docs/control-tower-maturity-gap-assessment.md docs/demo-assertions-maturity.md STATUS.md
rtk git commit -m "[W7] 建立成熟控制塔升级批准包"
```

Expected: commit succeeds.

---

### Task 1: Action Boundary Hardening

**Files:**
- Create: `app/action_context.py`
- Create: `app/test_action_governance.py`
- Modify: `app/actions.py`
- Modify: `app/admission_actions.py`
- Modify: `pipeline/build_ontology.py`
- Modify: `docs/control-tower-ontology-manual.md`
- Modify: `STATUS.md`

Decision required before implementation: Daniel approves M1 identity depth and maker-checker behavior.

- [ ] **Step 1: Write failing action-governance tests**

Create `app/test_action_governance.py`:

```python
from __future__ import annotations

import sqlite3

from app.action_context import Actor, ApprovalPolicy, next_stable_id, transaction


def test_manager_cannot_approve_own_proposal() -> None:
    proposer = Actor(actor_id="u-ops-001", role="ops", display_name="Ops One")
    approver = Actor(actor_id="u-ops-001", role="manager", display_name="Ops One")
    allowed, reason = ApprovalPolicy(policy_version="M1").can_approve(
        proposer=proposer,
        approver=approver,
    )
    assert allowed is False
    assert reason == "maker_checker_violation"


def test_different_manager_can_approve() -> None:
    proposer = Actor(actor_id="u-ops-001", role="ops", display_name="Ops One")
    approver = Actor(actor_id="u-mgr-001", role="manager", display_name="Manager One")
    allowed, reason = ApprovalPolicy(policy_version="M1").can_approve(
        proposer=proposer,
        approver=approver,
    )
    assert allowed is True
    assert reason == "approved_by_manager"


def test_next_stable_id_does_not_depend_on_row_count() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("create table task(task_id text primary key)")
    first = next_stable_id(prefix="TSK", entropy="risk-1|assign|2026-07-07")
    second = next_stable_id(prefix="TSK", entropy="risk-1|assign|2026-07-07")
    assert first == second
    conn.execute("insert into task(task_id) values (?)", (first,))
    third = next_stable_id(prefix="TSK", entropy="risk-2|assign|2026-07-07")
    assert third != first


def test_transaction_rolls_back_on_failure() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("create table audit_log(action_id text primary key, result text)")
    try:
        with transaction(conn):
            conn.execute(
                "insert into audit_log(action_id, result) values (?, ?)",
                ("ACT-1", "started"),
            )
            raise RuntimeError("forced failure")
    except RuntimeError:
        pass
    rows = conn.execute("select * from audit_log").fetchall()
    assert rows == []


if __name__ == "__main__":
    test_manager_cannot_approve_own_proposal()
    test_different_manager_can_approve()
    test_next_stable_id_does_not_depend_on_row_count()
    test_transaction_rolls_back_on_failure()
    print("ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m app.test_action_governance
```

Expected:

```text
ModuleNotFoundError: No module named 'app.action_context'
```

- [ ] **Step 3: Implement the action context helper**

Create `app/action_context.py`:

```python
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class Actor:
    actor_id: str
    role: str
    display_name: str


class ApprovalPolicy:
    def __init__(self, policy_version: str) -> None:
        self.policy_version = policy_version

    def can_approve(self, proposer: Actor, approver: Actor) -> tuple[bool, str]:
        if approver.role != "manager":
            return False, "manager_role_required"
        if approver.actor_id == proposer.actor_id:
            return False, "maker_checker_violation"
        return True, "approved_by_manager"


def next_stable_id(prefix: str, entropy: str) -> str:
    digest = hashlib.sha256(entropy.encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}-{digest}"


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    try:
        conn.execute("begin")
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
```

- [ ] **Step 4: Wire policy into existing action functions**

Modify approval paths in `app/actions.py` and `app/admission_actions.py` so approval calls accept proposer and approver identity. Keep backward-compatible wrappers for current UI role switching:

```python
from app.action_context import Actor, ApprovalPolicy


def can_approve_actor(proposer_actor_id: str, approver_actor_id: str, approver_role: str) -> tuple[bool, str]:
    proposer = Actor(actor_id=proposer_actor_id, role="ops", display_name=proposer_actor_id)
    approver = Actor(actor_id=approver_actor_id, role=approver_role, display_name=approver_actor_id)
    return ApprovalPolicy(policy_version="M1").can_approve(proposer=proposer, approver=approver)
```

Why this shape: it upgrades the prototype from role-only to named-actor semantics without adding real auth or SSO.

- [ ] **Step 5: Run tests and full regression**

Run:

```bash
python3 -m app.test_action_governance
python3 -m app.test_closed_loop
python3 -m app.test_admission_loop
python3 -m app.test_cost_loop
```

Expected:

```text
ok
existing closed-loop/admission/cost tests pass with no regression
```

- [ ] **Step 6: Commit**

Run:

```bash
rtk git add app/action_context.py app/test_action_governance.py app/actions.py app/admission_actions.py pipeline/build_ontology.py docs/control-tower-ontology-manual.md STATUS.md
rtk git commit -m "[W7][M1] 加固动作身份与审批边界"
```

Expected: commit succeeds.

---

### Task 2: Named Work Queue and SLA

**Files:**
- Create: `app/work_queue.py`
- Create: `app/test_work_queue.py`
- Modify: `pipeline/build_ontology.py`
- Modify: `app/actions.py`
- Modify: `app/streamlit_app.py`
- Modify: `docs/control-tower-ontology-manual.md`
- Modify: `docs/demo-assertions-maturity.md`
- Modify: `STATUS.md`

Decision required before implementation: Daniel approves M2 named owners, SLA states, and escalation semantics.

- [ ] **Step 1: Write failing work-queue tests**

Create `app/test_work_queue.py`:

```python
from __future__ import annotations

from app.work_queue import Owner, assign_owner, sla_state


def test_assign_owner_prefers_matching_role_and_region() -> None:
    roster = [
        Owner(actor_id="u-ops-cn", role="ops", region="CN", active=True),
        Owner(actor_id="u-ops-us", role="ops", region="US", active=True),
        Owner(actor_id="u-fin-us", role="finance", region="US", active=True),
    ]
    owner = assign_owner(required_role="ops", region="US", roster=roster)
    assert owner.actor_id == "u-ops-us"


def test_sla_state_open_before_due_date() -> None:
    assert sla_state(due_at="2026-07-10T00:00:00Z", as_of_date="2026-07-09") == "open"


def test_sla_state_due_today() -> None:
    assert sla_state(due_at="2026-07-10T00:00:00Z", as_of_date="2026-07-10") == "due_today"


def test_sla_state_overdue_after_due_date() -> None:
    assert sla_state(due_at="2026-07-10T00:00:00Z", as_of_date="2026-07-11") == "overdue"


if __name__ == "__main__":
    test_assign_owner_prefers_matching_role_and_region()
    test_sla_state_open_before_due_date()
    test_sla_state_due_today()
    test_sla_state_overdue_after_due_date()
    print("ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m app.test_work_queue
```

Expected:

```text
ModuleNotFoundError: No module named 'app.work_queue'
```

- [ ] **Step 3: Implement work queue helper**

Create `app/work_queue.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Owner:
    actor_id: str
    role: str
    region: str
    active: bool


def assign_owner(required_role: str, region: str, roster: list[Owner]) -> Owner:
    for owner in roster:
        if owner.active and owner.role == required_role and owner.region == region:
            return owner
    for owner in roster:
        if owner.active and owner.role == required_role:
            return owner
    raise ValueError(f"no_active_owner_for_role:{required_role}")


def _parse_utc_date(value: str) -> date:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def sla_state(due_at: str, as_of_date: str) -> str:
    due = _parse_utc_date(due_at)
    current = date.fromisoformat(as_of_date)
    if current < due:
        return "open"
    if current == due:
        return "due_today"
    return "overdue"
```

- [ ] **Step 4: Add schema fields**

Modify task creation in `pipeline/build_ontology.py` to include these columns:

```sql
assignee_user_id text,
assignee_team_id text,
sla_state text,
escalation_level integer default 0,
policy_version text
```

Why this shape: it models an operating queue without adding a real user directory.

- [ ] **Step 5: Run work-queue and app regressions**

Run:

```bash
python3 -m app.test_work_queue
python3 -m pipeline.build_ontology
python3 -m app.test_closed_loop
python3 -m app.test_cost_loop
```

Expected:

```text
ok
pipeline rebuild succeeds
closed-loop and cost tests pass
```

- [ ] **Step 6: Commit**

Run:

```bash
rtk git add app/work_queue.py app/test_work_queue.py pipeline/build_ontology.py app/actions.py app/streamlit_app.py docs/control-tower-ontology-manual.md docs/demo-assertions-maturity.md STATUS.md
rtk git commit -m "[W7][M2] 增加实名任务队列与SLA状态"
```

Expected: commit succeeds.

---

### Task 3: Canonical Event Envelope and Raw Lineage

**Files:**
- Create: `pipeline/event_envelope.py`
- Create: `pipeline/test_event_envelope.py`
- Modify: `datagen/generate.py`
- Modify: `pipeline/build_ontology.py`
- Modify: `pipeline/evaluate.py`
- Modify: `docs/field-gap-analysis.md`
- Modify: `docs/demo-assertions-maturity.md`
- Modify: `STATUS.md`

Decision required before implementation: Daniel approves M3 event envelope and raw lineage as simulated source-truth infrastructure.

- [ ] **Step 1: Write failing event-envelope tests**

Create `pipeline/test_event_envelope.py`:

```python
from __future__ import annotations

from pipeline.event_envelope import normalize_event


def test_normalize_milestone_event_keeps_source_identity() -> None:
    raw = {
        "source_system": "carrier_portal",
        "source_record_id": "SRC-1",
        "message_id": "MSG-1",
        "booking_no": "BKG-2026-0001",
        "container_no": "MSCU1234567",
        "event_type": "eta_change",
        "event_classifier": "ACT",
        "event_time": "2026-07-07T12:00:00Z",
    }
    event = normalize_event(raw, transform_version="M3")
    assert event.message_id == "MSG-1"
    assert event.source_system == "carrier_portal"
    assert event.event_type == "eta_change"
    assert event.transform_version == "M3"
    assert event.idempotency_key == "carrier_portal:MSG-1"


def test_normalize_event_rejects_missing_message_id() -> None:
    raw = {
        "source_system": "carrier_portal",
        "source_record_id": "SRC-1",
        "event_type": "eta_change",
        "event_time": "2026-07-07T12:00:00Z",
    }
    try:
        normalize_event(raw, transform_version="M3")
    except ValueError as exc:
        assert str(exc) == "missing_message_id"
    else:
        raise AssertionError("expected missing_message_id")


if __name__ == "__main__":
    test_normalize_milestone_event_keeps_source_identity()
    test_normalize_event_rejects_missing_message_id()
    print("ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pipeline.test_event_envelope
```

Expected:

```text
ModuleNotFoundError: No module named 'pipeline.event_envelope'
```

- [ ] **Step 3: Implement canonical envelope**

Create `pipeline/event_envelope.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalEvent:
    source_system: str
    source_record_id: str
    message_id: str
    event_type: str
    event_classifier: str
    event_time: str
    booking_no: str | None
    container_no: str | None
    transform_version: str
    payload_json: str

    @property
    def idempotency_key(self) -> str:
        return f"{self.source_system}:{self.message_id}"


def normalize_event(raw: dict[str, object], transform_version: str) -> CanonicalEvent:
    message_id = str(raw.get("message_id") or "")
    if not message_id:
        raise ValueError("missing_message_id")
    source_system = str(raw.get("source_system") or "")
    if not source_system:
        raise ValueError("missing_source_system")
    return CanonicalEvent(
        source_system=source_system,
        source_record_id=str(raw.get("source_record_id") or ""),
        message_id=message_id,
        event_type=str(raw.get("event_type") or ""),
        event_classifier=str(raw.get("event_classifier") or "ACT"),
        event_time=str(raw.get("event_time") or ""),
        booking_no=raw.get("booking_no") if isinstance(raw.get("booking_no"), str) else None,
        container_no=raw.get("container_no") if isinstance(raw.get("container_no"), str) else None,
        transform_version=transform_version,
        payload_json=json.dumps(raw, ensure_ascii=False, sort_keys=True),
    )
```

- [ ] **Step 4: Add source-event tables**

Modify `pipeline/build_ontology.py` to create:

```sql
create table if not exists source_events (
    idempotency_key text primary key,
    source_system text not null,
    source_record_id text not null,
    message_id text not null,
    event_type text not null,
    event_classifier text not null,
    event_time text not null,
    booking_no text,
    container_no text,
    transform_version text not null,
    payload_json text not null,
    ingested_at text not null
)
```

Why this shape: it provides traceability without treating carrier feeds as real production integrations.

- [ ] **Step 5: Run lineage regression**

Run:

```bash
python3 -m pipeline.test_event_envelope
python3 -m datagen.generate
python3 -m pipeline.build_ontology
python3 -m pipeline.evaluate
python3 -m engine.detect
python3 -m engine.evaluate
```

Expected:

```text
ok
pipeline precision remains 1.000
engine precision/recall remain at accepted thresholds
```

- [ ] **Step 6: Commit**

Run:

```bash
rtk git add pipeline/event_envelope.py pipeline/test_event_envelope.py datagen/generate.py pipeline/build_ontology.py pipeline/evaluate.py docs/field-gap-analysis.md docs/demo-assertions-maturity.md STATUS.md
rtk git commit -m "[W8][M3] 增加源事件信封与血缘"
```

Expected: commit succeeds.

---

### Task 4: MDM Crosswalk Resolver

**Files:**
- Create: `pipeline/mdm.py`
- Create: `pipeline/test_mdm.py`
- Modify: `pipeline/build_ontology.py`
- Modify: `pipeline/er.py`
- Modify: `config/datagen.yaml`
- Modify: `docs/control-tower-ontology-manual.md`
- Modify: `docs/demo-assertions-maturity.md`
- Modify: `STATUS.md`

Decision required before implementation: Daniel approves M4 object coverage for crosswalk resolution.

- [ ] **Step 1: Write failing MDM tests**

Create `pipeline/test_mdm.py`:

```python
from __future__ import annotations

from pipeline.mdm import CrosswalkEntry, resolve_crosswalk


def test_resolve_exact_external_id() -> None:
    entries = [
        CrosswalkEntry("carrier_portal", "customer", "BESTBUY-US", "CUS-0001", 1.0),
        CrosswalkEntry("erp", "customer", "BBY", "CUS-0001", 0.95),
    ]
    result = resolve_crosswalk(entries, "carrier_portal", "customer", "BESTBUY-US")
    assert result.internal_id == "CUS-0001"
    assert result.status == "resolved"


def test_ambiguous_external_id_is_not_guessed() -> None:
    entries = [
        CrosswalkEntry("erp", "sku", "USB-C-20W", "SKU-0001", 0.7),
        CrosswalkEntry("erp", "sku", "USB-C-20W", "SKU-0002", 0.7),
    ]
    result = resolve_crosswalk(entries, "erp", "sku", "USB-C-20W")
    assert result.internal_id is None
    assert result.status == "ambiguous"


def test_missing_external_id_is_unresolved() -> None:
    result = resolve_crosswalk([], "erp", "vendor", "V-404")
    assert result.internal_id is None
    assert result.status == "unresolved"


if __name__ == "__main__":
    test_resolve_exact_external_id()
    test_ambiguous_external_id_is_not_guessed()
    test_missing_external_id_is_unresolved()
    print("ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pipeline.test_mdm
```

Expected:

```text
ModuleNotFoundError: No module named 'pipeline.mdm'
```

- [ ] **Step 3: Implement deterministic resolver**

Create `pipeline/mdm.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CrosswalkEntry:
    source_system: str
    object_type: str
    external_id: str
    internal_id: str
    confidence: float


@dataclass(frozen=True)
class CrosswalkResult:
    internal_id: str | None
    status: str
    confidence: float


def resolve_crosswalk(
    entries: list[CrosswalkEntry],
    source_system: str,
    object_type: str,
    external_id: str,
) -> CrosswalkResult:
    matches = [
        entry
        for entry in entries
        if entry.source_system == source_system
        and entry.object_type == object_type
        and entry.external_id == external_id
    ]
    if not matches:
        return CrosswalkResult(None, "unresolved", 0.0)
    internal_ids = {entry.internal_id for entry in matches}
    if len(internal_ids) > 1:
        return CrosswalkResult(None, "ambiguous", max(entry.confidence for entry in matches))
    best = max(matches, key=lambda entry: entry.confidence)
    return CrosswalkResult(best.internal_id, "resolved", best.confidence)
```

- [ ] **Step 4: Add crosswalk table**

Modify `pipeline/build_ontology.py`:

```sql
create table if not exists mdm_crosswalk (
    source_system text not null,
    object_type text not null,
    external_id text not null,
    internal_id text not null,
    confidence real not null,
    status text not null,
    updated_at text not null,
    primary key (source_system, object_type, external_id, internal_id)
)
```

Why this shape: crosswalks stay deterministic and inspectable; unresolved/ambiguous records are surfaced instead of guessed.

- [ ] **Step 5: Run MDM and pipeline regression**

Run:

```bash
python3 -m pipeline.test_mdm
python3 -m datagen.generate
python3 -m pipeline.build_ontology
python3 -m pipeline.evaluate
```

Expected:

```text
ok
pipeline precision remains 1.000
unresolved and ambiguous records are counted in the DQ report
```

- [ ] **Step 6: Commit**

Run:

```bash
rtk git add pipeline/mdm.py pipeline/test_mdm.py pipeline/build_ontology.py pipeline/er.py config/datagen.yaml docs/control-tower-ontology-manual.md docs/demo-assertions-maturity.md STATUS.md
rtk git commit -m "[W8][M4] 增加跨系统主数据映射"
```

Expected: commit succeeds.

---

### Task 5: Reusable Graph Traversal Layer

**Files:**
- Create: `engine/graph.py`
- Create: `engine/test_graph.py`
- Modify: `pipeline/build_ontology.py`
- Modify: `engine/detect.py`
- Modify: `engine/cost_rules.py`
- Modify: `agent/tools.py`
- Modify: `docs/architecture.md`
- Modify: `docs/demo-assertions-maturity.md`
- Modify: `STATUS.md`

Decision required before implementation: Daniel approves M5 generic relationship registry instead of query-specific graph logic.

- [ ] **Step 1: Write failing graph traversal test**

Create `engine/test_graph.py`:

```python
from __future__ import annotations

import sqlite3

from engine.graph import explain_path, upsert_relationship


def test_explain_path_returns_ordered_relationships() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "create table object_relationships("
        "relationship_id text primary key, source_type text, source_id text, "
        "target_type text, target_id text, relationship_type text, confidence real, source text)"
    )
    upsert_relationship(conn, "REL-1", "Shipment", "SHP-1", "SalesOrderLine", "SOL-1", "affects", 1.0, "test")
    upsert_relationship(conn, "REL-2", "SalesOrderLine", "SOL-1", "Customer", "CUS-1", "belongs_to", 1.0, "test")
    path = explain_path(conn, "Shipment", "SHP-1", "Customer", "CUS-1", max_depth=2)
    assert [edge.relationship_id for edge in path] == ["REL-1", "REL-2"]


if __name__ == "__main__":
    test_explain_path_returns_ordered_relationships()
    print("ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m engine.test_graph
```

Expected:

```text
ModuleNotFoundError: No module named 'engine.graph'
```

- [ ] **Step 3: Implement graph helper**

Create `engine/graph.py`:

```python
from __future__ import annotations

import sqlite3
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Edge:
    relationship_id: str
    source_type: str
    source_id: str
    target_type: str
    target_id: str
    relationship_type: str
    confidence: float
    source: str


def upsert_relationship(
    conn: sqlite3.Connection,
    relationship_id: str,
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
    relationship_type: str,
    confidence: float,
    source: str,
) -> None:
    conn.execute(
        """
        insert or replace into object_relationships
        (relationship_id, source_type, source_id, target_type, target_id, relationship_type, confidence, source)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (relationship_id, source_type, source_id, target_type, target_id, relationship_type, confidence, source),
    )


def _outgoing(conn: sqlite3.Connection, source_type: str, source_id: str) -> list[Edge]:
    rows = conn.execute(
        """
        select relationship_id, source_type, source_id, target_type, target_id, relationship_type, confidence, source
        from object_relationships
        where source_type = ? and source_id = ?
        order by relationship_id
        """,
        (source_type, source_id),
    ).fetchall()
    return [Edge(*row) for row in rows]


def explain_path(
    conn: sqlite3.Connection,
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
    max_depth: int,
) -> list[Edge]:
    queue: deque[tuple[str, str, list[Edge]]] = deque([(source_type, source_id, [])])
    seen = {(source_type, source_id)}
    while queue:
        current_type, current_id, path = queue.popleft()
        if len(path) >= max_depth:
            continue
        for edge in _outgoing(conn, current_type, current_id):
            next_key = (edge.target_type, edge.target_id)
            next_path = path + [edge]
            if next_key == (target_type, target_id):
                return next_path
            if next_key not in seen:
                seen.add(next_key)
                queue.append((edge.target_type, edge.target_id, next_path))
    return []
```

- [ ] **Step 4: Add relationship registry table**

Modify `pipeline/build_ontology.py`:

```sql
create table if not exists object_relationships (
    relationship_id text primary key,
    source_type text not null,
    source_id text not null,
    target_type text not null,
    target_id text not null,
    relationship_type text not null,
    confidence real not null,
    source text not null
)
```

Why this shape: it keeps SQLite and existing joins, but gives AI/tools one shared explanation layer.

- [ ] **Step 5: Run graph and engine regression**

Run:

```bash
python3 -m engine.test_graph
python3 -m pipeline.build_ontology
python3 -m engine.detect
python3 -m engine.evaluate
python3 -m engine.evaluate_cost
python3 -m agent.evaluate
```

Expected:

```text
ok
engine precision/recall remain accepted
cost evaluation remains accepted
agent evaluation remains accepted
```

- [ ] **Step 6: Commit**

Run:

```bash
rtk git add engine/graph.py engine/test_graph.py pipeline/build_ontology.py engine/detect.py engine/cost_rules.py agent/tools.py docs/architecture.md docs/demo-assertions-maturity.md STATUS.md
rtk git commit -m "[W8][M5] 建立可解释关系遍历层"
```

Expected: commit succeeds.

---

### Task 6: Data-Quality Issue Operational Loop

**Files:**
- Create: `pipeline/dq_issues.py`
- Create: `app/dq_actions.py`
- Create: `app/test_dq_loop.py`
- Modify: `pipeline/build_ontology.py`
- Modify: `pipeline/evaluate.py`
- Modify: `app/streamlit_app.py`
- Modify: `docs/data-guide.md`
- Modify: `docs/demo-assertions-maturity.md`
- Modify: `STATUS.md`

Decision required before implementation: Daniel approves M6 treating data-quality problems as operational work.

- [ ] **Step 1: Write failing DQ loop test**

Create `app/test_dq_loop.py`:

```python
from __future__ import annotations

import sqlite3

from app.dq_actions import assign_dq_issue, close_dq_issue
from pipeline.dq_issues import create_dq_issue


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "create table dq_issues("
        "dq_issue_id text primary key, source_table text, source_record_id text, "
        "issue_type text, severity text, status text, assignee_user_id text, resolution text)"
    )
    conn.execute(
        "create table action_log(action_id text primary key, action text, target_object text, result text)"
    )
    return conn


def test_dq_issue_can_be_assigned_and_closed() -> None:
    conn = make_conn()
    issue_id = create_dq_issue(
        conn,
        source_table="unresolved_milestones",
        source_record_id="SRC-404",
        issue_type="unresolved_reference",
        severity="medium",
    )
    assign_dq_issue(conn, issue_id, assignee_user_id="u-ops-001")
    close_dq_issue(conn, issue_id, resolution="mapped booking_no to SHP-1")
    row = conn.execute(
        "select status, assignee_user_id, resolution from dq_issues where dq_issue_id = ?",
        (issue_id,),
    ).fetchone()
    assert row == ("closed", "u-ops-001", "mapped booking_no to SHP-1")


if __name__ == "__main__":
    test_dq_issue_can_be_assigned_and_closed()
    print("ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m app.test_dq_loop
```

Expected:

```text
ModuleNotFoundError: No module named 'app.dq_actions'
```

- [ ] **Step 3: Implement DQ issue creator**

Create `pipeline/dq_issues.py`:

```python
from __future__ import annotations

import hashlib
import sqlite3


def create_dq_issue(
    conn: sqlite3.Connection,
    source_table: str,
    source_record_id: str,
    issue_type: str,
    severity: str,
) -> str:
    entropy = f"{source_table}|{source_record_id}|{issue_type}"
    issue_id = "DQ-" + hashlib.sha256(entropy.encode("utf-8")).hexdigest()[:12].upper()
    conn.execute(
        """
        insert or ignore into dq_issues
        (dq_issue_id, source_table, source_record_id, issue_type, severity, status, assignee_user_id, resolution)
        values (?, ?, ?, ?, ?, 'open', null, null)
        """,
        (issue_id, source_table, source_record_id, issue_type, severity),
    )
    return issue_id
```

- [ ] **Step 4: Implement DQ actions**

Create `app/dq_actions.py`:

```python
from __future__ import annotations

import sqlite3


def assign_dq_issue(conn: sqlite3.Connection, dq_issue_id: str, assignee_user_id: str) -> None:
    conn.execute(
        "update dq_issues set status = 'assigned', assignee_user_id = ? where dq_issue_id = ?",
        (assignee_user_id, dq_issue_id),
    )
    conn.execute(
        "insert into action_log(action_id, action, target_object, result) values (?, ?, ?, ?)",
        (f"ACT-{dq_issue_id}-ASSIGN", "assign_dq_issue", dq_issue_id, "assigned"),
    )


def close_dq_issue(conn: sqlite3.Connection, dq_issue_id: str, resolution: str) -> None:
    conn.execute(
        "update dq_issues set status = 'closed', resolution = ? where dq_issue_id = ?",
        (resolution, dq_issue_id),
    )
    conn.execute(
        "insert into action_log(action_id, action, target_object, result) values (?, ?, ?, ?)",
        (f"ACT-{dq_issue_id}-CLOSE", "close_dq_issue", dq_issue_id, "closed"),
    )
```

Why this shape: DQ becomes a small operational loop while keeping real source repair out of scope.

- [ ] **Step 5: Add DQ issue schema and regression**

Modify `pipeline/build_ontology.py` to create:

```sql
create table if not exists dq_issues (
    dq_issue_id text primary key,
    source_table text not null,
    source_record_id text not null,
    issue_type text not null,
    severity text not null,
    status text not null,
    assignee_user_id text,
    resolution text
)
```

Run:

```bash
python3 -m app.test_dq_loop
python3 -m pipeline.build_ontology
python3 -m pipeline.evaluate
```

Expected:

```text
ok
pipeline evaluation remains accepted
```

- [ ] **Step 6: Commit**

Run:

```bash
rtk git add pipeline/dq_issues.py app/dq_actions.py app/test_dq_loop.py pipeline/build_ontology.py pipeline/evaluate.py app/streamlit_app.py docs/data-guide.md docs/demo-assertions-maturity.md STATUS.md
rtk git commit -m "[W9][M6] 建立数据质量处置闭环"
```

Expected: commit succeeds.

---

### Task 7: Simulated Integration Outbox

**Files:**
- Create: `pipeline/outbox.py`
- Create: `app/integration_actions.py`
- Create: `app/test_outbox.py`
- Modify: `pipeline/build_ontology.py`
- Modify: `app/actions.py`
- Modify: `app/admission_actions.py`
- Modify: `docs/control-tower-ontology-manual.md`
- Modify: `docs/demo-assertions-maturity.md`
- Modify: `STATUS.md`

Decision required before implementation: Daniel approves M7 outbox as simulated writeback, not real external integration.

- [ ] **Step 1: Write failing outbox test**

Create `app/test_outbox.py`:

```python
from __future__ import annotations

import sqlite3

from app.integration_actions import mark_outbox_succeeded
from pipeline.outbox import enqueue_writeback


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "create table integration_outbox("
        "idempotency_key text primary key, target_system text, action_name text, "
        "target_object text, payload_json text, status text, attempt_count integer, last_error text)"
    )
    return conn


def test_enqueue_writeback_is_idempotent() -> None:
    conn = make_conn()
    key1 = enqueue_writeback(conn, "erp", "update_promise_date", "SOL-1", {"date": "2026-08-27"})
    key2 = enqueue_writeback(conn, "erp", "update_promise_date", "SOL-1", {"date": "2026-08-27"})
    assert key1 == key2
    count = conn.execute("select count(*) from integration_outbox").fetchone()[0]
    assert count == 1


def test_mark_outbox_succeeded() -> None:
    conn = make_conn()
    key = enqueue_writeback(conn, "erp", "update_promise_date", "SOL-1", {"date": "2026-08-27"})
    mark_outbox_succeeded(conn, key)
    row = conn.execute(
        "select status, attempt_count, last_error from integration_outbox where idempotency_key = ?",
        (key,),
    ).fetchone()
    assert row == ("succeeded", 1, None)


if __name__ == "__main__":
    test_enqueue_writeback_is_idempotent()
    test_mark_outbox_succeeded()
    print("ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m app.test_outbox
```

Expected:

```text
ModuleNotFoundError: No module named 'app.integration_actions'
```

- [ ] **Step 3: Implement outbox enqueue**

Create `pipeline/outbox.py`:

```python
from __future__ import annotations

import hashlib
import json
import sqlite3


def enqueue_writeback(
    conn: sqlite3.Connection,
    target_system: str,
    action_name: str,
    target_object: str,
    payload: dict[str, object],
) -> str:
    payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    entropy = f"{target_system}|{action_name}|{target_object}|{payload_json}"
    key = hashlib.sha256(entropy.encode("utf-8")).hexdigest()
    conn.execute(
        """
        insert or ignore into integration_outbox
        (idempotency_key, target_system, action_name, target_object, payload_json, status, attempt_count, last_error)
        values (?, ?, ?, ?, ?, 'pending', 0, null)
        """,
        (key, target_system, action_name, target_object, payload_json),
    )
    return key
```

- [ ] **Step 4: Implement outbox status action**

Create `app/integration_actions.py`:

```python
from __future__ import annotations

import sqlite3


def mark_outbox_succeeded(conn: sqlite3.Connection, idempotency_key: str) -> None:
    conn.execute(
        """
        update integration_outbox
        set status = 'succeeded', attempt_count = attempt_count + 1, last_error = null
        where idempotency_key = ?
        """,
        (idempotency_key,),
    )
```

Why this shape: it teaches writeback safety and idempotency without creating a fake real integration.

- [ ] **Step 5: Add schema and wire one approved action**

Modify `pipeline/build_ontology.py`:

```sql
create table if not exists integration_outbox (
    idempotency_key text primary key,
    target_system text not null,
    action_name text not null,
    target_object text not null,
    payload_json text not null,
    status text not null,
    attempt_count integer not null default 0,
    last_error text
)
```

Wire approved reschedule writeback in `app/actions.py`:

```python
from pipeline.outbox import enqueue_writeback


def enqueue_promise_date_writeback(conn, so_line_id: str, promised_delivery_date: str) -> str:
    return enqueue_writeback(
        conn,
        target_system="erp_simulated",
        action_name="update_promise_date",
        target_object=so_line_id,
        payload={"promised_delivery_date": promised_delivery_date},
    )
```

- [ ] **Step 6: Run outbox and closed-loop regression**

Run:

```bash
python3 -m app.test_outbox
python3 -m pipeline.build_ontology
python3 -m app.test_closed_loop
```

Expected:

```text
ok
closed-loop test passes and one simulated outbox row is created for approved reschedule
```

- [ ] **Step 7: Commit**

Run:

```bash
rtk git add pipeline/outbox.py app/integration_actions.py app/test_outbox.py pipeline/build_ontology.py app/actions.py app/admission_actions.py docs/control-tower-ontology-manual.md docs/demo-assertions-maturity.md STATUS.md
rtk git commit -m "[W9][M7] 增加模拟集成写回outbox"
```

Expected: commit succeeds.

---

### Task 8: Business Expansion Decision Briefs

**Files:**
- Create: `docs/inventory-control-tower-plan-v0.8.md`
- Create: `docs/customs-document-plan-v0.9.md`
- Create: `docs/transport-execution-plan-v1.0.md`
- Create: `docs/cost-recovery-plan-v1.1.md`
- Modify: `STATUS.md`
- No code modules changed.

Decision required before implementation: Daniel chooses exactly one of the four modules as the next implementation target.

- [ ] **Step 1: Create inventory decision brief**

Write `docs/inventory-control-tower-plan-v0.8.md`:

```markdown
# v0.8 候选计划 — Inventory/WMS Control Loop

## 业务问题

延误发生时，运营是否能用仓库现货、在途货、替代 SKU、拆单策略保护客户承诺？

## 最小对象候选

- Warehouse
- InventoryPosition
- InventoryReservation
- ReplenishmentSuggestion

## 最小动作候选

- ReserveInventory
- ReleaseReservation
- SuggestSubstitution
- ApproveSubstitution

## 不做

- 不接真实 WMS
- 不做自动补货采购
- 不做库存成本核算

## Daniel 裁决题

当一票 shipment 延误时，是否允许系统建议“用现货先发部分订单，剩余改期”？
```

- [ ] **Step 2: Create customs/document decision brief**

Write `docs/customs-document-plan-v0.9.md`:

```markdown
# v0.9 候选计划 — Customs Document Control Loop

## 业务问题

跨境单证缺失或风险发现后，系统是否能生成证据任务、跟踪 broker/IOR 责任、关闭审计链？

## 最小对象候选

- TradeDocument
- CustomsEntry
- Broker
- EvidenceArtifact

## 最小动作候选

- RequestDocument
- UploadEvidence
- ReviewEvidence
- ReleaseCustomsHold

## 不做

- 不做真实报关
- 不计算真实税则
- 不连接 CBP 或 broker API

## Daniel 裁决题

单证缺失时，责任人优先是内部 compliance、客户 IOR，还是外部 broker？
```

- [ ] **Step 3: Create transport execution decision brief**

Write `docs/transport-execution-plan-v1.0.md`:

```markdown
# v1.0 候选计划 — Transportation Execution Loop

## 业务问题

控制塔是否只看见运输状态，还是能模拟 booking、appointment、drayage、last-mile 执行任务？

## 最小对象候选

- Carrier
- Facility
- Appointment
- DrayageLeg

## 最小动作候选

- BookCarrier
- ScheduleAppointment
- UpdateDrayageStatus
- EscalateCarrierException

## 不做

- 不接真实承运商 API
- 不做价格招投标
- 不做路线优化

## Daniel 裁决题

运输异常优先模拟“港口提柜预约失败”还是“尾程派送预约失败”？
```

- [ ] **Step 4: Create cost recovery decision brief**

Write `docs/cost-recovery-plan-v1.1.md`:

```markdown
# v1.1 候选计划 — Cost Recovery Loop

## 业务问题

费用异常识别后，系统是否能追踪 AP hold、vendor claim、rebill、credit memo、回收账龄？

## 最小对象候选

- CostClaim
- CreditMemo
- RebillCase
- RecoveryAgingBucket

## 最小动作候选

- HoldPayment
- OpenVendorClaim
- ApproveRebill
- CloseRecoveryCase

## 不做

- 不接真实 AP
- 不生成真实发票
- 不做真实收款

## Daniel 裁决题

转嫁客户费用时，是先由 finance 审核金额，还是先由 CS 确认客户可接受？
```

- [ ] **Step 5: Run doc checks**

Run:

```bash
rtk git diff --check
rtk rg "T[B]D|TO[D]O|fill in detail[s]|implement late[r]" docs/inventory-control-tower-plan-v0.8.md docs/customs-document-plan-v0.9.md docs/transport-execution-plan-v1.0.md docs/cost-recovery-plan-v1.1.md
```

Expected:

```text
no output from git diff --check
no matches from rg
```

- [ ] **Step 6: Commit**

Run:

```bash
rtk git add docs/inventory-control-tower-plan-v0.8.md docs/customs-document-plan-v0.9.md docs/transport-execution-plan-v1.0.md docs/cost-recovery-plan-v1.1.md STATUS.md
rtk git commit -m "[W10] 拆分成熟控制塔业务扩展候选计划"
```

Expected: commit succeeds.

---

### Task 9: AI Privacy and LLM Verification

**Files:**
- Create: `agent/privacy.py`
- Create: `agent/test_privacy.py`
- Modify: `agent/tools.py`
- Modify: `agent/llm_agent.py`
- Modify: `agent/evaluate.py`
- Modify: `agent/eval_cases.yaml`
- Modify: `README.md`
- Modify: `STATUS.md`

Decision required before implementation: Daniel approves role-aware minimization before LLM calls. Real LLM execution still requires `OPENAI_API_KEY`; ChatGPT/Codex subscription is not API authentication for this Python code.

- [ ] **Step 1: Write failing privacy tests**

Create `agent/test_privacy.py`:

```python
from __future__ import annotations

from agent.privacy import redact_payload_for_role


def test_cs_cannot_see_invoice_amounts_in_llm_payload() -> None:
    payload = {
        "invoice_id": "INV-1",
        "invoice_total_usd": 1200.0,
        "customer_name": "Best Buy Reseller",
        "risk_summary": "Delay risk",
    }
    redacted = redact_payload_for_role(payload, role="cs")
    assert redacted["invoice_total_usd"] == "[REDACTED]"
    assert redacted["customer_name"] == "Best Buy Reseller"


def test_finance_can_see_invoice_amounts() -> None:
    payload = {"invoice_total_usd": 1200.0, "risk_summary": "Cost anomaly"}
    redacted = redact_payload_for_role(payload, role="finance")
    assert redacted["invoice_total_usd"] == 1200.0


def test_sensitive_flags_are_redacted_for_ai_by_default() -> None:
    payload = {"uflpa_risk_flag": True, "risk_summary": "Compliance issue"}
    redacted = redact_payload_for_role(payload, role="ops")
    assert redacted["uflpa_risk_flag"] == "[REDACTED]"


if __name__ == "__main__":
    test_cs_cannot_see_invoice_amounts_in_llm_payload()
    test_finance_can_see_invoice_amounts()
    test_sensitive_flags_are_redacted_for_ai_by_default()
    print("ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m agent.test_privacy
```

Expected:

```text
ModuleNotFoundError: No module named 'agent.privacy'
```

- [ ] **Step 3: Implement redaction helper**

Create `agent/privacy.py`:

```python
from __future__ import annotations

FINANCE_FIELDS = {"invoice_total_usd", "amount_usd", "expected_amount_usd", "actual_amount_usd"}
COMPLIANCE_FIELDS = {"uflpa_risk_flag", "origin_evidence_status", "credit_terms", "risk_tier"}


def redact_payload_for_role(payload: dict[str, object], role: str) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in payload.items():
        if key in FINANCE_FIELDS and role != "finance":
            redacted[key] = "[REDACTED]"
        elif key in COMPLIANCE_FIELDS and role not in {"compliance", "manager"}:
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value
    return redacted
```

- [ ] **Step 4: Wire redaction before LLM prompts**

Modify `agent/llm_agent.py` to apply `redact_payload_for_role` to tool results before sending them to a provider. Record provider/model/tool policy version in the response metadata:

```python
from agent.privacy import redact_payload_for_role


def prepare_tool_payload_for_llm(payload: dict[str, object], role: str) -> dict[str, object]:
    return redact_payload_for_role(payload, role=role)
```

Why this shape: it keeps the AI proposal-only design while adding explicit data minimization.

- [ ] **Step 5: Run deterministic and optional LLM verification**

Run deterministic tests:

```bash
python3 -m agent.test_privacy
python3 -m agent.evaluate
```

Expected:

```text
ok
agent evaluation remains accepted
```

Optional OpenAI API verification after setting `OPENAI_API_KEY`:

```bash
export AGENT_PROVIDER=openai
export AGENT_MODEL=gpt-5.5
python3 -m agent.evaluate --llm
python3 -m agent.llm_agent "SHP-2026-0099 为什么有风险？该怎么处理？"
```

Expected:

```text
LLM answers cite only registered tool facts
no approval tool is available
provider/model/tool policy version are printed or logged
```

- [ ] **Step 6: Commit**

Run:

```bash
rtk git add agent/privacy.py agent/test_privacy.py agent/tools.py agent/llm_agent.py agent/evaluate.py agent/eval_cases.yaml README.md STATUS.md
rtk git commit -m "[W10] 增加AI数据最小化与LLM实测护栏"
```

Expected: commit succeeds.

---

### Task 10: Mature Workbench UI View Models

**Files:**
- Create: `app/maturity_views.py`
- Create: `app/test_maturity_views.py`
- Modify: `app/streamlit_app.py`
- Modify: `docs/demo-script.md`
- Modify: `docs/demo-assertions-maturity.md`
- Modify: `STATUS.md`

- [ ] **Step 1: Write failing view-model test**

Create `app/test_maturity_views.py`:

```python
from __future__ import annotations

from app.maturity_views import build_maturity_nav_items


def test_maturity_nav_items_are_stable() -> None:
    items = build_maturity_nav_items()
    labels = [item["label"] for item in items]
    assert labels == ["Work Queue", "Data Quality", "Integration Outbox", "Lineage"]


def test_maturity_nav_items_have_icons() -> None:
    items = build_maturity_nav_items()
    assert all(item["icon"] for item in items)


if __name__ == "__main__":
    test_maturity_nav_items_are_stable()
    test_maturity_nav_items_have_icons()
    print("ok")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m app.test_maturity_views
```

Expected:

```text
ModuleNotFoundError: No module named 'app.maturity_views'
```

- [ ] **Step 3: Implement pure view-model helper**

Create `app/maturity_views.py`:

```python
from __future__ import annotations


def build_maturity_nav_items() -> list[dict[str, str]]:
    return [
        {"label": "Work Queue", "icon": "assignment_ind"},
        {"label": "Data Quality", "icon": "rule"},
        {"label": "Integration Outbox", "icon": "sync_alt"},
        {"label": "Lineage", "icon": "account_tree"},
    ]
```

- [ ] **Step 4: Wire the workbench into Streamlit**

Modify `app/streamlit_app.py` to import `build_maturity_nav_items()` and render a maturity tab that links to work queue, DQ issues, outbox, and lineage sections. Keep UI reads side-effect-free.

Why this shape: it extends the command-center UI around actual operational work, not decorative dashboards.

- [ ] **Step 5: Verify UI-related tests**

Run:

```bash
python3 -m app.test_maturity_views
python3 -m app.test_closed_loop
python3 -m app.test_admission_loop
python3 -m app.test_cost_loop
```

Expected:

```text
ok
existing app tests pass
```

- [ ] **Step 6: Manual UI check**

Run:

```bash
streamlit run app/streamlit_app.py
```

Expected:

```text
the app starts and shows maturity workbench navigation without overlapping text
```

- [ ] **Step 7: Commit**

Run:

```bash
rtk git add app/maturity_views.py app/test_maturity_views.py app/streamlit_app.py docs/demo-script.md docs/demo-assertions-maturity.md STATUS.md
rtk git commit -m "[W10] 增加成熟控制塔工作台视图"
```

Expected: commit succeeds.

---

### Task 11: Maturity Evaluation Harness

**Files:**
- Create: `engine/evaluate_maturity.py`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/retrospective.md`
- Modify: `STATUS.md`

- [ ] **Step 1: Write evaluator skeleton with explicit metrics**

Create `engine/evaluate_maturity.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path


DB_PATH = Path("data/ontology.sqlite")


def scalar(conn: sqlite3.Connection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0] or 0)


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    metrics = {
        "tasks_with_named_owner": scalar(
            conn,
            "select count(*) from task where coalesce(assignee_user_id, '') != ''",
        ),
        "open_dq_issues": scalar(
            conn,
            "select count(*) from sqlite_master where type='table' and name='dq_issues'",
        ),
        "pending_outbox_rows": scalar(
            conn,
            "select count(*) from sqlite_master where type='table' and name='integration_outbox'",
        ),
    }
    for key, value in metrics.items():
        print(f"{key}: {value}")
    conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run evaluator before schema exists**

Run:

```bash
python3 -m engine.evaluate_maturity
```

Expected before Tasks 2, 6, and 7:

```text
sqlite OperationalError for missing maturity columns or tables
```

Expected after Tasks 2, 6, and 7:

```text
tasks_with_named_owner: non-negative integer
open_dq_issues: non-negative integer
pending_outbox_rows: non-negative integer
```

- [ ] **Step 3: Harden evaluator against missing optional tables**

Replace `engine/evaluate_maturity.py` with a version that checks table existence before querying optional tables:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path


DB_PATH = Path("data/ontology.sqlite")


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "select 1 from sqlite_master where type='table' and name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"pragma table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def count_or_zero(conn: sqlite3.Connection, table_name: str, where: str = "1 = 1") -> int:
    if not table_exists(conn, table_name):
        return 0
    row = conn.execute(f"select count(*) from {table_name} where {where}").fetchone()
    return int(row[0] or 0)


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    if table_exists(conn, "task") and column_exists(conn, "task", "assignee_user_id"):
        named_owner = count_or_zero(conn, "task", "coalesce(assignee_user_id, '') != ''")
    else:
        named_owner = 0
    metrics = {
        "tasks_with_named_owner": named_owner,
        "open_dq_issues": count_or_zero(conn, "dq_issues", "status != 'closed'"),
        "pending_outbox_rows": count_or_zero(conn, "integration_outbox", "status = 'pending'"),
    }
    for key, value in metrics.items():
        print(f"{key}: {value}")
    conn.close()


if __name__ == "__main__":
    main()
```

Why this shape: the evaluator can run during partial wave execution and make gaps visible rather than crashing.

- [ ] **Step 4: Run full regression suite**

Run:

```bash
python3 -m datagen.generate && python3 -m datagen.verify
python3 -m pipeline.build_ontology && python3 -m pipeline.evaluate
python3 -m engine.detect && python3 -m engine.evaluate
python3 -m engine.evaluate_cost
python3 -m engine.evaluate_scoring
python3 -m engine.evaluate_maturity
python3 -m app.test_closed_loop
python3 -m app.test_admission_loop
python3 -m app.test_cost_loop
python3 -m agent.evaluate
```

Expected:

```text
all existing evaluators pass
evaluate_maturity prints maturity metrics
```

- [ ] **Step 5: Commit**

Run:

```bash
rtk git add engine/evaluate_maturity.py README.md docs/architecture.md docs/retrospective.md STATUS.md
rtk git commit -m "[W10] 增加成熟度评估器"
```

Expected: commit succeeds.

---

### Task 12: Closeout and Handoff

**Files:**
- Modify: `STATUS.md`
- Modify: `README.md`
- Modify: `docs/retrospective.md`
- Modify: `docs/demo-script.md`
- Modify: `docs/demo-assertions-maturity.md`

- [ ] **Step 1: Update README quick-start**

Add maturity evaluator to the quick-start block:

```bash
python3 -m engine.evaluate_maturity                            # 8. 成熟度评估（任务/SLA/DQ/outbox）
streamlit run app/streamlit_app.py                             # 9. UI（控制塔+准入+费用+成熟工作台）
```

- [ ] **Step 2: Update STATUS**

Add exact completion status under `## 当前位置`:

```markdown
- **成熟控制塔升级执行状态**：已完成 W7-W10 平台成熟度升级；实名动作边界、SLA 队列、源事件血缘、MDM crosswalk、关系遍历、DQ issue、模拟 outbox、AI 数据最小化、成熟工作台与 evaluate_maturity 均已接入；下一步只在 Daniel 选择一个业务扩展模块后继续。
```

- [ ] **Step 3: Run final full verification**

Run:

```bash
python3 -m datagen.generate && python3 -m datagen.verify
python3 -m pipeline.build_ontology && python3 -m pipeline.evaluate
python3 -m engine.detect && python3 -m engine.evaluate
python3 -m engine.evaluate_cost
python3 -m engine.evaluate_scoring
python3 -m engine.evaluate_maturity
python3 -m app.test_closed_loop
python3 -m app.test_admission_loop
python3 -m app.test_cost_loop
python3 -m app.test_action_governance
python3 -m app.test_work_queue
python3 -m app.test_dq_loop
python3 -m app.test_outbox
python3 -m app.test_maturity_views
python3 -m pipeline.test_event_envelope
python3 -m pipeline.test_mdm
python3 -m engine.test_graph
python3 -m agent.test_privacy
python3 -m agent.evaluate
```

Expected:

```text
all commands pass
engine.evaluate prints accepted precision/recall
engine.evaluate_cost prints accepted precision/recall
agent.evaluate prints accepted evaluation result
```

- [ ] **Step 4: Commit closeout**

Run:

```bash
rtk git add STATUS.md README.md docs/retrospective.md docs/demo-script.md docs/demo-assertions-maturity.md
rtk git commit -m "[W10] 收官成熟控制塔平台升级"
```

Expected: commit succeeds.

---

## Recommended Execution Order

1. Execute Task 0 first and stop for Daniel approval.
2. If Daniel approves platform maturity, execute Tasks 1-7 in order.
3. Execute Task 8 and stop for Daniel to choose exactly one business expansion module.
4. Execute Tasks 9-11 after the platform tasks pass, because AI and UI should sit on stable data/policy boundaries.
5. Execute Task 12 only after every evaluator and manual UI check passes.

## Self-Review

Spec coverage:

- Functional modules: covered by Task 8 module briefs plus Task 10 UI and Task 11 evaluator.
- Role definition and governance: covered by Tasks 1 and 2.
- Relationship graph: covered by Tasks 4 and 5.
- Business workflows: covered by Tasks 2, 6, 7, and 8.
- AI governance and OpenAI verification: covered by Task 9.
- Verification and closeout: covered by Tasks 11 and 12.

Placeholder scan:

- This plan contains no placeholder markers from the writing-plans skill failure list.

Type consistency:

- `Actor`, `ApprovalPolicy`, `Owner`, `CanonicalEvent`, `CrosswalkEntry`, and `Edge` names are defined before later tasks reference them.
- All shell commands use project-local `rtk` when invoking git or text checks.
