"""M6 data-quality issue helpers.

Simulation-first: unresolved parking rows become operational issues, but closing an
issue records handling only. It does not repair source data or rerun ingestion.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

POLICY_VERSION = "M6-dq-issue-v1"
CREATED_AT = "2026-08-08T00:00:00Z"


def _stable_issue_id(source_table: str, source_record_id: str, issue_type: str) -> str:
    entropy = f"{source_table}|{source_record_id}|{issue_type}"
    digest = hashlib.sha256(entropy.encode("utf-8")).hexdigest()[:12].upper()
    return f"DQ-{digest}"


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _json_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def create_dq_issue(
    conn: sqlite3.Connection,
    source_table: str,
    source_record_id: str,
    issue_type: str,
    severity: str,
    *,
    detail_json: Any = None,
) -> str:
    """Create a deterministic DQ issue, failing on conflicting duplicate payloads."""
    issue_id = _stable_issue_id(source_table, source_record_id, issue_type)
    row = {
        "dq_issue_id": issue_id,
        "source_table": source_table,
        "source_record_id": source_record_id,
        "issue_type": issue_type,
        "severity": severity,
        "status": "open",
        "assignee_user_id": "",
        "resolution": "",
        "detail_json": _json_text(detail_json),
        "created_at": CREATED_AT,
        "closed_at": "",
        "policy_version": POLICY_VERSION,
    }
    cols = [c for c in row if c in _table_columns(conn, "dq_issues")]
    placeholders = ",".join("?" for _ in cols)
    result = conn.execute(
        f"""insert or ignore into dq_issues ({",".join(cols)})
            values ({placeholders})""",
        [row[c] for c in cols],
    )
    if result.rowcount == 0:
        existing = conn.execute(
            "select severity, detail_json from dq_issues where dq_issue_id=?",
            (issue_id,),
        ).fetchone()
        if existing and (existing[0] != row["severity"] or (existing[1] or "") != row["detail_json"]):
            raise ValueError(
                "dq_issue_conflict:"
                f"{issue_id} already exists for {source_table}.{source_record_id}/{issue_type}"
            )
    return issue_id


def create_unresolved_milestone_issues(
    conn: sqlite3.Connection,
    unresolved_rows: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Create one medium unresolved_reference issue for each unresolved milestone."""
    issue_ids: list[str] = []
    detail_fields = [
        "milestone_id",
        "booking_no",
        "container_no",
        "event_type",
        "event_classifier",
        "event_time",
        "event_locode",
        "new_eta",
        "source_system",
        "ingested_at",
        "reason",
    ]
    for row in sorted(unresolved_rows, key=lambda r: str(r["milestone_id"])):
        detail = {field: row.get(field, "") for field in detail_fields}
        issue_ids.append(
            create_dq_issue(
                conn,
                "unresolved_milestones",
                str(row["milestone_id"]),
                "unresolved_reference",
                "medium",
                detail_json=detail,
            )
        )
    return issue_ids
