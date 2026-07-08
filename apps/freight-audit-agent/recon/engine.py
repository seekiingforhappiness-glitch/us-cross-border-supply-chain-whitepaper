"""Deterministic reconciliation engine (domain-spec §5).

For each invoice: resolve its rate_con (via BOL lane + carrier) and BOL, then
run all 12 discrepancy checks line-by-line and at invoice level. Every hit
carries an evidence pack (invoice line + contract term + calculation) and a
recovery amount. No LLM, no probability — pure arithmetic.

Detention/demurrage expected days come from datagen.dd_calc (shared formula).
"""
from __future__ import annotations

import sqlite3

from config.loader import load_config
from datagen import dd_calc

_cfg = load_config()
_codes = _cfg["charge_codes"]
_SURCHARGE = {c for c, m in _codes.items() if m["category"] == "surcharge"}
_WHITELIST = set(_cfg["accessorial_whitelist"])
_TOL = _cfg["tolerances"]
_BASE_FX = _cfg["base_fx"]
_HIGH_TYPES = {"RATE_MISMATCH", "NOT_IN_CONTRACT", "PHANTOM_ACCESSORIAL"}


def _severity(dtype: str, recovery: float) -> str:
    if recovery > 500 or dtype in _HIGH_TYPES:
        return "high"
    if recovery >= 100:
        return "medium"
    return "low"


def _load_indexes(conn: sqlite3.Connection) -> dict:
    conn.row_factory = sqlite3.Row
    rate_card: dict[tuple[str, str], float] = {}
    con_codes: dict[str, set] = {}
    for r in conn.execute("SELECT * FROM rate_card_lines"):
        rate_card[(r["rate_con_id"], r["charge_code"])] = r["contracted_rate_usd"]
        con_codes.setdefault(r["rate_con_id"], set()).add(r["charge_code"])
    cons_by_id, lane_index = {}, {}
    for r in conn.execute("SELECT * FROM rate_cons"):
        cons_by_id[r["rate_con_id"]] = dict(r)
        lane_index[(r["carrier_id"], r["origin_port"], r["destination_port"])] = r["rate_con_id"]
    bols = {}
    for r in conn.execute("SELECT * FROM bols"):
        bols[r["bl_no"]] = dict(r)
    return {"rate_card": rate_card, "con_codes": con_codes, "cons_by_id": cons_by_id,
            "lane_index": lane_index, "bols": bols}


def audit_invoice(conn: sqlite3.Connection, invoice: dict, lines: list[dict], idx: dict) -> list[dict]:
    """Return the list of detected discrepancies for one invoice (post-tolerance)."""
    out: list[dict] = []
    bol = idx["bols"].get(invoice["bl_no"])
    con_id = None
    if bol is not None:
        con_id = idx["lane_index"].get((bol["carrier_id"], bol["origin_port"], bol["destination_port"]))
    con = idx["cons_by_id"].get(con_id) if con_id else None
    free_time = (con or {}).get("free_time_days", 0)
    rate_card = idx["rate_card"]

    def emit(line_id, dtype, recovery, evidence):
        out.append({
            "invoice_id": invoice["invoice_id"], "invoice_line_id": line_id,
            "discrepancy_type": dtype, "severity": _severity(dtype, recovery),
            "detected_amount_usd": round(recovery, 2), "evidence": evidence,
        })

    seen: dict[tuple, str] = {}
    for ln in lines:
        code = ln["charge_code"]
        qty = ln["quantity"]
        rate = ln["unit_rate_usd"]
        amount = ln["amount_usd"]
        lid = ln["invoice_line_id"]
        contracted = rate_card.get((con_id, code)) if con_id else None

        # duplicate / surcharge-duplicate
        key = (code, ln["container_no"])
        if key in seen:
            dtype = "SURCHARGE_DUPLICATE" if code in _SURCHARGE else "DUPLICATE_CHARGE"
            emit(lid, dtype, amount, {
                "invoice_line": {"line_id": lid, "charge_code": code, "amount_usd": amount},
                "duplicate_of": seen[key], "calc": f"same ({code}+{ln['container_no']}) billed twice"})
        else:
            seen[key] = lid

        # rate mismatch
        if contracted is not None and rate > contracted * (1 + _TOL["rate_mismatch_pct"]):
            rec = (rate - contracted) * qty
            emit(lid, "RATE_MISMATCH", rec, {
                "invoice_line": {"line_id": lid, "charge_code": code, "unit_rate_usd": rate, "quantity": qty},
                "contract": {"contracted_rate_usd": contracted, "tolerance_pct": _TOL["rate_mismatch_pct"]},
                "calc": f"({rate} - {contracted}) x {qty} = {round(rec, 2)}"})

        # phantom / not-in-contract
        if contracted is None and code not in _WHITELIST:
            dtype = "PHANTOM_ACCESSORIAL" if code in _SURCHARGE else "NOT_IN_CONTRACT"
            emit(lid, dtype, amount, {
                "invoice_line": {"line_id": lid, "charge_code": code, "amount_usd": amount},
                "contract": {"in_rate_con": False, "whitelisted": False},
                "calc": f"{code} absent from rate_con {con_id}"})

        # unit math error
        expected_amt = qty * rate
        if abs(amount - expected_amt) > _TOL["amount_math_abs_usd"]:
            rec = amount - expected_amt
            if rec > 0:
                emit(lid, "UNIT_MATH_ERROR", rec, {
                    "invoice_line": {"line_id": lid, "quantity": qty, "unit_rate_usd": rate, "amount_usd": amount},
                    "calc": f"{amount} - ({qty} x {rate}) = {round(rec, 2)}"})

        # container count
        if ln["unit"] == "per_container" and bol is not None and qty > (bol.get("container_count") or 1):
            cnt = bol.get("container_count") or 1
            rec = (qty - cnt) * rate
            emit(lid, "CONTAINER_COUNT_MISMATCH", rec, {
                "invoice_line": {"line_id": lid, "charge_code": code, "billed_qty": qty, "unit_rate_usd": rate},
                "bol": {"container_count": cnt}, "calc": f"({qty} - {cnt}) x {rate} = {round(rec, 2)}"})

        # weight mismatch
        if ln.get("weight_kg") is not None and bol is not None and bol.get("weight_kg"):
            bw = bol["weight_kg"]
            if ln["weight_kg"] > bw * (1 + _TOL["weight_mismatch_pct"]):
                excess = (ln["weight_kg"] - bw) / bw
                emit(lid, "WEIGHT_MISMATCH", amount * excess, {
                    "invoice_line": {"line_id": lid, "billed_weight_kg": ln["weight_kg"], "amount_usd": amount},
                    "bol": {"weight_kg": bw, "tolerance_pct": _TOL["weight_mismatch_pct"]},
                    "calc": f"weight {ln['weight_kg']} > {bw} x {1 + _TOL['weight_mismatch_pct']}"})

        # demurrage / detention miscalc
        if code == "DEM" and bol is not None:
            exp = dd_calc.demurrage_days(bol.get("discharge_date"), bol.get("pickup_date"), free_time)
            if qty > exp:
                rec = (qty - exp) * rate
                emit(lid, "DEMURRAGE_MISCALC", rec, {
                    "invoice_line": {"line_id": lid, "billed_days": qty, "unit_rate_usd": rate},
                    "bol": {"discharge_date": bol.get("discharge_date"), "pickup_date": bol.get("pickup_date"),
                            "free_time_days": free_time, "expected_days": exp},
                    "calc": f"({qty} - {exp}) x {rate} = {round(rec, 2)}"})
        if code == "DET" and bol is not None:
            exp = dd_calc.detention_days(bol.get("gate_out_date"), bol.get("return_date"), free_time)
            if qty > exp:
                rec = (qty - exp) * rate
                emit(lid, "DETENTION_MISCALC", rec, {
                    "invoice_line": {"line_id": lid, "billed_days": qty, "unit_rate_usd": rate},
                    "bol": {"gate_out_date": bol.get("gate_out_date"), "return_date": bol.get("return_date"),
                            "free_time_days": free_time, "expected_days": exp},
                    "calc": f"({qty} - {exp}) x {rate} = {round(rec, 2)}"})

    # invoice-level: FX error
    base = _BASE_FX.get(invoice["currency"])
    fx = invoice["fx_rate"]
    if base and fx is not None and base > 0:
        dev = abs(fx - base) / base
        if dev > _TOL["fx_error_pct"]:
            rec = invoice["total_amount_usd"] * dev / (1 + dev)
            emit(None, "FX_ERROR", rec, {
                "invoice": {"currency": invoice["currency"], "fx_rate": fx, "base_fx": base},
                "calc": f"|{fx} - {base}| / {base} = {round(dev, 4)} > {_TOL['fx_error_pct']}"})

    # invoice-level: tariff expired
    if con is not None and invoice["invoice_date"] > con["expiry_date"]:
        ofr = next((l["amount_usd"] for l in lines if l["charge_code"] == "OFR"), 0.0)
        emit(None, "TARIFF_EXPIRED", ofr, {
            "invoice": {"invoice_date": invoice["invoice_date"]},
            "contract": {"rate_con_id": con_id, "expiry_date": con["expiry_date"]},
            "calc": f"invoice_date {invoice['invoice_date']} > expiry {con['expiry_date']}"})

    # tolerance: suppress sub-threshold recoveries (min_recovery)
    return [d for d in out if d["detected_amount_usd"] >= _TOL["min_recovery_usd"]]


def audit_all(conn: sqlite3.Connection) -> list[dict]:
    idx = _load_indexes(conn)
    conn.row_factory = sqlite3.Row
    lines_by_inv: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT * FROM invoice_lines ORDER BY invoice_id, line_no"):
        lines_by_inv.setdefault(r["invoice_id"], []).append(dict(r))
    results: list[dict] = []
    for inv in conn.execute("SELECT * FROM invoices ORDER BY invoice_id"):
        invoice = dict(inv)
        results.extend(audit_invoice(conn, invoice, lines_by_inv.get(invoice["invoice_id"], []), idx))
    return results
