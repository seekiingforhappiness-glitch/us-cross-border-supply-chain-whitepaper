"""Small SQLite-backed object graph helpers for M5 relationship registry."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import sqlite3


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


def ensure_relationship_table(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS object_relationships (
        relationship_id text primary key,
        source_type text not null,
        source_id text not null,
        target_type text not null,
        target_id text not null,
        relationship_type text not null,
        confidence real not null,
        source text not null
    )""")


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
    ensure_relationship_table(conn)
    conn.execute(
        """INSERT INTO object_relationships (
               relationship_id, source_type, source_id, target_type, target_id,
               relationship_type, confidence, source
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(relationship_id) DO UPDATE SET
               source_type=excluded.source_type,
               source_id=excluded.source_id,
               target_type=excluded.target_type,
               target_id=excluded.target_id,
               relationship_type=excluded.relationship_type,
               confidence=excluded.confidence,
               source=excluded.source""",
        (
            relationship_id,
            source_type,
            source_id,
            target_type,
            target_id,
            relationship_type,
            confidence,
            source,
        ),
    )


def _edge_from_row(row: sqlite3.Row) -> Edge:
    return Edge(
        relationship_id=row["relationship_id"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        target_type=row["target_type"],
        target_id=row["target_id"],
        relationship_type=row["relationship_type"],
        confidence=float(row["confidence"]),
        source=row["source"],
    )


def explain_path(
    conn: sqlite3.Connection,
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
    max_depth: int,
) -> list[Edge]:
    """Return the first directed BFS path, with stable relationship_id ordering."""
    if max_depth < 0:
        return []
    table_exists = conn.execute(
        """SELECT count(*) FROM sqlite_master
           WHERE type='table' AND name='object_relationships'"""
    ).fetchone()[0]
    if not table_exists:
        return []
    if (source_type, source_id) == (target_type, target_id):
        return []

    original_row_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        queue = deque([((source_type, source_id), [])])
        visited = {(source_type, source_id)}

        while queue:
            (current_type, current_id), path = queue.popleft()
            if len(path) >= max_depth:
                continue
            rows = conn.execute(
                """SELECT relationship_id, source_type, source_id, target_type, target_id,
                          relationship_type, confidence, source
                   FROM object_relationships
                   WHERE source_type=? AND source_id=?
                   ORDER BY relationship_id""",
                (current_type, current_id),
            ).fetchall()
            for row in rows:
                edge = _edge_from_row(row)
                next_node = (edge.target_type, edge.target_id)
                next_path = path + [edge]
                if next_node == (target_type, target_id):
                    return next_path
                if next_node not in visited:
                    visited.add(next_node)
                    queue.append((next_node, next_path))
    finally:
        conn.row_factory = original_row_factory
    return []
