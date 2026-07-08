"""SQLite schema (domain-spec §1, 7 tables).

Note on WEIGHT_MISMATCH: the discrepancy catalogue (§3) requires a weight
comparison, but the base DDL in §1 carries no weight column. We keep the 7
tables and their given columns verbatim and add a single nullable
`weight_kg` to invoice_lines and to bols so the WEIGHT_MISMATCH check is
real rather than stubbed. This is the only deviation from §1 and is a
schema addition, not a metric change.
"""
from __future__ import annotations

import sqlite3

DDL = """
CREATE TABLE invoices (
    invoice_id TEXT PRIMARY KEY, invoice_no TEXT NOT NULL, carrier_id TEXT NOT NULL,
    carrier_name TEXT, bill_to_party TEXT, booking_no TEXT, bl_no TEXT,
    invoice_date TEXT NOT NULL, currency TEXT NOT NULL, fx_rate REAL,
    total_amount_usd REAL NOT NULL, as_of_date TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE invoice_lines (
    invoice_line_id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL, line_no INTEGER NOT NULL,
    charge_code TEXT NOT NULL, charge_description TEXT, container_no TEXT, container_type TEXT,
    quantity REAL NOT NULL, unit TEXT NOT NULL, unit_rate_usd REAL NOT NULL,
    amount_usd REAL NOT NULL, currency TEXT, weight_kg REAL,
    as_of_date TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE rate_cons (
    rate_con_id TEXT PRIMARY KEY, carrier_id TEXT NOT NULL, origin_port TEXT NOT NULL,
    destination_port TEXT NOT NULL, effective_date TEXT NOT NULL, expiry_date TEXT NOT NULL,
    contract_party TEXT, currency TEXT NOT NULL, free_time_days INTEGER,
    as_of_date TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE rate_card_lines (
    rate_card_line_id TEXT PRIMARY KEY, rate_con_id TEXT NOT NULL, charge_code TEXT NOT NULL,
    container_type TEXT, contracted_rate_usd REAL NOT NULL, unit TEXT NOT NULL,
    free_time_days INTEGER, as_of_date TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE bols (
    bol_id TEXT PRIMARY KEY, bl_no TEXT NOT NULL, booking_no TEXT, carrier_id TEXT NOT NULL,
    origin_port TEXT, destination_port TEXT, container_no TEXT, container_type TEXT,
    gate_out_date TEXT, return_date TEXT, discharge_date TEXT, pickup_date TEXT,
    free_days_used INTEGER, container_count INTEGER, weight_kg REAL,
    as_of_date TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE expected_discrepancies (
    expected_id TEXT PRIMARY KEY, invoice_line_id TEXT, invoice_id TEXT NOT NULL,
    discrepancy_type TEXT NOT NULL, expected_recovery_usd REAL, injection_note TEXT,
    as_of_date TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE review_queue (
    review_id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL, invoice_line_id TEXT,
    discrepancy_type TEXT NOT NULL, severity TEXT, detected_amount_usd REAL,
    evidence_json TEXT, status TEXT NOT NULL, resolution TEXT, assigned_to TEXT,
    as_of_date TEXT NOT NULL, created_at TEXT NOT NULL);
"""

DATA_TABLES = [
    "invoices", "invoice_lines", "rate_cons", "rate_card_lines", "bols",
    "expected_discrepancies", "review_queue",
]


def reset_schema(conn: sqlite3.Connection) -> None:
    """Drop and recreate all 7 data tables (fresh datagen run)."""
    for tbl in DATA_TABLES + ["action_log", "dispute_outbox"]:
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")
    conn.executescript(DDL)
    conn.commit()
