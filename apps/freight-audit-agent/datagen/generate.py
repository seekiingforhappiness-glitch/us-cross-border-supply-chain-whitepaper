"""Deterministic simulated dataset (seed 42) with ground-truth discrepancies.

domain-spec §4. 150 invoices: 105 clean (incl. 12 gray-zone with no ground
truth) + 45 dirty (1-3 injected discrepancies each). Every injected
discrepancy writes an expected_discrepancies row so eval can score
precision/recall. Gray-zone invoices look suspicious but are legitimate and
are NEVER written to ground truth (they test the false-positive strategy).

datagen and recon share datagen.dd_calc for detention/demurrage.
Re-running produces a byte-identical dataset (no system clock, seeded rng).
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from config.loader import db_path, load_config
from datagen import dd_calc
from datagen.schema import reset_schema

import random

CARRIERS = [
    ("CAR-MAEU", "MAEU", "Maersk Line"),
    ("CAR-MSCU", "MSCU", "MSC"),
    ("CAR-COSU", "COSU", "COSCO Shipping"),
    ("CAR-OOLU", "OOLU", "OOCL"),
    ("CAR-CMDU", "CMDU", "CMA CGM"),
]
ORIGINS = ["CNSHA", "CNNGB", "CNSZX"]
DESTS = ["USLAX", "USNYC", "NLRTM", "DEHAM", "SGSIN"]

# Contracted charge codes present in every rate_con (12 -> 15 cons x 12 = 180).
CONTRACTED = ["OFR", "BAF", "CAF", "THCO", "THCD", "DOC", "SEAL", "DET", "DEM", "DRAY", "CUS", "LSS"]
# Base charges billed on every invoice (all contracted -> never phantom).
BASE_LINES = ["OFR", "BAF", "THCO", "THCD", "DOC", "DRAY", "CUS"]

VALID_CON_IDX = list(range(12))
EXPIRED_CON_IDX = [12, 13, 14]

FREE_TIME = 5
VALID_EFF, VALID_EXP = "2026-01-01", "2026-12-31"
EXPIRED_EFF, EXPIRED_EXP = "2025-06-01", "2026-04-30"


def _weighted_sample(rng: random.Random, weights: dict, n: int) -> list[str]:
    pool = list(weights.items())
    chosen: list[str] = []
    for _ in range(n):
        if not pool:
            break
        total = sum(w for _, w in pool)
        r = rng.random() * total
        acc = 0.0
        for idx, (t, w) in enumerate(pool):
            acc += w
            if r <= acc:
                chosen.append(t)
                pool.pop(idx)
                break
    return chosen


def generate(target_db: str | None = None) -> dict:
    cfg = load_config()
    as_of = cfg["datagen_as_of"]
    created_at = f"{as_of}T00:00:00Z"
    tol = cfg["tolerances"]
    base_fx = cfg["base_fx"]
    codes = cfg["charge_codes"]
    whitelist = set(cfg["accessorial_whitelist"])
    surcharge = {c for c, m in codes.items() if m["category"] == "surcharge"}
    weights = cfg["discrepancy_weights"]

    rng = random.Random(cfg["random_seed"])
    dbp = target_db or db_path()
    conn = sqlite3.connect(dbp)
    reset_schema(conn)

    invoices, inv_lines, rate_cons, rate_card_rows, bols, expected = [], [], [], [], [], []
    rate_card: dict[str, dict] = {}   # rate_con_id -> {code: contracted_rate}
    con_meta: dict[int, dict] = {}

    # ---- rate_cons + rate_card_lines (180) ----
    for rc in range(15):
        carrier = CARRIERS[rc % 5]
        origin = ORIGINS[(rc // 5) % 3]
        dest = DESTS[rc % 5]
        expired = rc in EXPIRED_CON_IDX
        rc_id = f"RC-{rc:02d}"
        eff, exp = (EXPIRED_EFF, EXPIRED_EXP) if expired else (VALID_EFF, VALID_EXP)
        ctype = cfg["container_types"][rc % 3]
        rate_cons.append((rc_id, carrier[0], origin, dest, eff, exp, "ShipperCo", "USD",
                          FREE_TIME, as_of, created_at))
        rate_card[rc_id] = {}
        con_meta[rc] = {"id": rc_id, "carrier": carrier, "origin": origin, "dest": dest,
                        "ctype": ctype, "expiry": exp}
        for code in CONTRACTED:
            m = codes[code]
            rate = round(rng.uniform(m["min_usd"], m["max_usd"]), 2)
            rate_card[rc_id][code] = rate
            rate_card_rows.append((f"RCL-{rc:02d}-{code}", rc_id, code, ctype, rate,
                                   m["unit"], FREE_TIME, as_of, created_at))

    # ---- roles ----
    order = list(range(150))
    rng.shuffle(order)
    dirty_list = order[:45]
    gray_list = order[45:57]
    dirty_set = set(dirty_list)
    gray_kind = {}
    for pos, idx in enumerate(gray_list):
        gray_kind[idx] = "rate" if pos < 6 else ("fx" if pos < 9 else "whitelist")
    fn_set = set(dirty_list[:8])  # tiny (<$5) injections -> intentional FN

    exp_seq = [0]

    def add_expected(inv_id, line_id, dtype, recovery, note):
        exp_seq[0] += 1
        expected.append((f"EXP-{exp_seq[0]:04d}", line_id, inv_id, dtype,
                         round(recovery, 2), note, as_of, created_at))

    tariff_counter = 0

    for i in range(150):
        inv_id = f"INV-{i:04d}"
        is_dirty = i in dirty_set
        gk = gray_kind.get(i)
        types = _weighted_sample(rng, weights, rng.randint(1, 3)) if is_dirty else []

        # ---- rate_con / carrier ----
        if "TARIFF_EXPIRED" in types:
            rc = EXPIRED_CON_IDX[tariff_counter % 3]
            tariff_counter += 1
        else:
            rc = VALID_CON_IDX[i % 12]
        meta = con_meta[rc]
        rc_id = meta["id"]
        carrier = meta["carrier"]
        ctype = meta["ctype"]
        card = rate_card[rc_id]

        # ---- currency / fx ----
        currency, fx_rate = "USD", 1.0
        if "FX_ERROR" in types or gk == "fx":
            currency = "EUR"
            fx_rate = base_fx["EUR"]

        # ---- dates ----
        if "TARIFF_EXPIRED" in types:
            inv_date = date.fromisoformat(meta["expiry"]) + timedelta(days=15)
        else:
            inv_date = date.fromisoformat(VALID_EFF) + timedelta(days=90 + (i % 80))
        inv_date_s = inv_date.isoformat()

        # ---- bol + dd dates ----
        ddem = rng.randint(1, 4)
        ddet = rng.randint(1, 4)
        weight = 18000 + (i % 40) * 100
        discharge = inv_date - timedelta(days=25)
        pickup = discharge + timedelta(days=FREE_TIME + ddem)
        gate_out = pickup + timedelta(days=2)
        ret = gate_out + timedelta(days=FREE_TIME + ddet)
        cont_no = f"CN-{i:04d}"
        bl_no = f"BL-{i:04d}"
        booking = f"BKG-{i:04d}"
        bols.append((f"BOL-{i:04d}", bl_no, booking, carrier[0], meta["origin"], meta["dest"],
                     cont_no, ctype, gate_out.isoformat(), ret.isoformat(),
                     discharge.isoformat(), pickup.isoformat(), FREE_TIME + ddem, 1,
                     float(weight), as_of, created_at))

        # ---- clean lines ----
        lines: list[dict] = []

        def add_line(code, quantity, rate, weight_kg=None):
            m = codes[code]
            amt = round(quantity * rate, 2)
            lines.append({"charge_code": code, "container_no": cont_no,
                          "container_type": ctype, "quantity": float(quantity),
                          "unit": m["unit"], "unit_rate_usd": rate,
                          "amount_usd": amt, "weight_kg": weight_kg})
            return lines[-1]

        for code in BASE_LINES:
            add_line(code, 1, card[code])
        if rng.random() < 0.40:
            add_line("CAF", 1, card["CAF"])
        if rng.random() < 0.30:
            add_line("LSS", 1, card["LSS"])
        dd_flag = rng.random() < 0.30
        bills_dem = dd_flag or "DEMURRAGE_MISCALC" in types
        bills_det = dd_flag or "DETENTION_MISCALC" in types
        if bills_dem:
            add_line("DEM", ddem, card["DEM"])
        if bills_det:
            add_line("DET", ddet, card["DET"])

        used_codes: set[str] = set()

        def free_line(candidates):
            for ln in lines:
                if ln["charge_code"] in candidates and ln["charge_code"] not in used_codes:
                    return ln
            return None

        # ---- injections (dirty) ----
        for dtype in types:
            if dtype == "RATE_MISMATCH":
                ln = free_line(["OFR", "DRAY", "THCO", "THCD", "CAF", "LSS"])
                if not ln:
                    continue
                old = ln["unit_rate_usd"]
                factor = round(rng.uniform(1.12, 1.40), 3)
                ln["unit_rate_usd"] = round(old * factor, 2)
                ln["amount_usd"] = round(ln["quantity"] * ln["unit_rate_usd"], 2)
                used_codes.add(ln["charge_code"])
                ln["_gt"] = ("RATE_MISMATCH", round((ln["unit_rate_usd"] - old) * ln["quantity"], 2),
                             f"rate {old}->{ln['unit_rate_usd']}")
            elif dtype == "DEMURRAGE_MISCALC":
                ln = next((l for l in lines if l["charge_code"] == "DEM"), None)
                if not ln:
                    continue
                delta = rng.randint(1, 3)
                ln["quantity"] = float(ddem + delta)
                ln["amount_usd"] = round(ln["quantity"] * ln["unit_rate_usd"], 2)
                ln["_gt"] = ("DEMURRAGE_MISCALC", round(delta * ln["unit_rate_usd"], 2),
                             f"billed {ddem + delta}d vs {ddem}d")
            elif dtype == "DETENTION_MISCALC":
                ln = next((l for l in lines if l["charge_code"] == "DET"), None)
                if not ln:
                    continue
                delta = rng.randint(1, 3)
                ln["quantity"] = float(ddet + delta)
                ln["amount_usd"] = round(ln["quantity"] * ln["unit_rate_usd"], 2)
                ln["_gt"] = ("DETENTION_MISCALC", round(delta * ln["unit_rate_usd"], 2),
                             f"billed {ddet + delta}d vs {ddet}d")
            elif dtype == "PHANTOM_ACCESSORIAL":
                code = rng.choice(["PSS", "GRI", "CGS"])
                m = codes[code]
                rate = round(rng.uniform(m["min_usd"], m["max_usd"]), 2)
                ln = add_line(code, 1, rate)
                ln["_gt"] = ("PHANTOM_ACCESSORIAL", rate, f"{code} not in rate_con")
            elif dtype == "NOT_IN_CONTRACT":
                code = rng.choice(["TLX", "AMS", "ISF", "CHAS"])
                m = codes[code]
                rate = round(rng.uniform(m["min_usd"], m["max_usd"]), 2)
                qty = 2 if m["unit"] == "per_day" else 1
                ln = add_line(code, qty, rate)
                ln["_gt"] = ("NOT_IN_CONTRACT", ln["amount_usd"], f"{code} not in rate_con")
            elif dtype == "DUPLICATE_CHARGE":
                ln = free_line(["OFR", "DRAY", "THCO", "THCD", "DOC", "CUS"])
                if not ln:
                    continue
                dup = add_line(ln["charge_code"], ln["quantity"], ln["unit_rate_usd"])
                used_codes.add(ln["charge_code"])
                dup["_gt"] = ("DUPLICATE_CHARGE", dup["amount_usd"], f"dup {ln['charge_code']}")
            elif dtype == "SURCHARGE_DUPLICATE":
                ln = free_line(["BAF", "CAF", "LSS"])
                if not ln:
                    continue
                dup = add_line(ln["charge_code"], ln["quantity"], ln["unit_rate_usd"])
                used_codes.add(ln["charge_code"])
                dup["_gt"] = ("SURCHARGE_DUPLICATE", dup["amount_usd"], f"dup surcharge {ln['charge_code']}")
            elif dtype == "CONTAINER_COUNT_MISMATCH":
                ln = free_line(["OFR", "DRAY", "THCO", "THCD"])
                if not ln:
                    continue
                extra = rng.randint(1, 2)
                ln["quantity"] = float(1 + extra)
                ln["amount_usd"] = round(ln["quantity"] * ln["unit_rate_usd"], 2)
                used_codes.add(ln["charge_code"])
                ln["_gt"] = ("CONTAINER_COUNT_MISMATCH", round(extra * ln["unit_rate_usd"], 2),
                             f"billed {1 + extra} vs bol 1")
            elif dtype == "WEIGHT_MISMATCH":
                ln = free_line(["OFR", "DRAY", "THCO", "THCD"])
                if not ln:
                    continue
                excess = round(rng.uniform(0.08, 0.15), 3)
                ln["weight_kg"] = round(weight * (1 + excess), 1)
                used_codes.add(ln["charge_code"])
                ln["_gt"] = ("WEIGHT_MISMATCH", round(ln["amount_usd"] * excess, 2),
                             f"wt {ln['weight_kg']} vs bol {weight}")
            elif dtype == "UNIT_MATH_ERROR":
                ln = free_line(["OFR", "DRAY", "THCO", "THCD", "CUS"])
                if not ln:
                    continue
                bump = round(rng.uniform(50, 150), 2)
                ln["amount_usd"] = round(ln["quantity"] * ln["unit_rate_usd"] + bump, 2)
                used_codes.add(ln["charge_code"])
                ln["_gt"] = ("UNIT_MATH_ERROR", bump, f"amount tampered +{bump}")
            elif dtype == "FX_ERROR":
                dev = round(rng.uniform(0.03, 0.08), 3)
                fx_rate = round(base_fx["EUR"] * (1 + dev), 4)
                # invoice-level; recovery filled after total is known below
            elif dtype == "TARIFF_EXPIRED":
                pass  # invoice-level; recovery = OFR amount (after build)

        # ---- FN: tiny sub-threshold rate mismatch on SEAL ----
        if i in fn_set:
            seal_rate = card["SEAL"]
            ln = add_line("SEAL", 1, round(seal_rate + 0.40, 2))
            ln["_gt"] = ("RATE_MISMATCH", round(0.40, 2), "sub-threshold seal markup")

        # ---- gray zone (no ground truth) ----
        if gk == "rate":
            ln = free_line(["OFR", "DRAY", "THCO", "THCD"])
            if ln:
                ln["unit_rate_usd"] = round(ln["unit_rate_usd"] * 1.05, 2)
                ln["amount_usd"] = round(ln["quantity"] * ln["unit_rate_usd"], 2)
        elif gk == "fx":
            fx_rate = round(base_fx["EUR"] * 1.015, 4)
        elif gk == "whitelist":
            m = codes["ISPS"]
            add_line("ISPS", 1, round(rng.uniform(m["min_usd"], m["max_usd"]), 2))

        # ---- persist lines + ground truth ----
        total = 0.0
        for n, ln in enumerate(lines, start=1):
            line_id = f"LN-{i:04d}-{n:02d}"
            total += ln["amount_usd"]
            inv_lines.append((line_id, inv_id, n, ln["charge_code"],
                              codes[ln["charge_code"]]["desc_en"], ln["container_no"],
                              ln["container_type"], ln["quantity"], ln["unit"],
                              ln["unit_rate_usd"], ln["amount_usd"], currency,
                              ln["weight_kg"], as_of, created_at))
            if "_gt" in ln:
                gt_type, gt_rec, gt_note = ln["_gt"]
                add_expected(inv_id, line_id, gt_type, gt_rec, gt_note)
        total = round(total, 2)

        # invoice-level ground truth
        if "FX_ERROR" in types:
            dev = round(abs(fx_rate - base_fx["EUR"]) / base_fx["EUR"], 4)
            add_expected(inv_id, None, "FX_ERROR", total * dev / (1 + dev),
                         f"fx {fx_rate} vs {base_fx['EUR']}")
        if "TARIFF_EXPIRED" in types:
            ofr_amt = next((l["amount_usd"] for l in lines if l["charge_code"] == "OFR"), 0.0)
            add_expected(inv_id, None, "TARIFF_EXPIRED", ofr_amt,
                         f"inv {inv_date_s} > expiry {meta['expiry']}")

        invoices.append((inv_id, f"{carrier[1]}-{i:04d}", carrier[0], carrier[2], "ShipperCo",
                         booking, bl_no, inv_date_s, currency, fx_rate, total, as_of, created_at))

    # ---- bulk insert ----
    conn.executemany("INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", invoices)
    conn.executemany("INSERT INTO invoice_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", inv_lines)
    conn.executemany("INSERT INTO rate_cons VALUES (?,?,?,?,?,?,?,?,?,?,?)", rate_cons)
    conn.executemany("INSERT INTO rate_card_lines VALUES (?,?,?,?,?,?,?,?,?)", rate_card_rows)
    conn.executemany("INSERT INTO bols VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", bols)
    conn.executemany("INSERT INTO expected_discrepancies VALUES (?,?,?,?,?,?,?,?)", expected)
    conn.commit()

    counts = {
        "carriers": len({c[2] for c in invoices}),
        "rate_cons": len(rate_cons),
        "rate_card_lines": len(rate_card_rows),
        "invoices": len(invoices),
        "invoice_lines": len(inv_lines),
        "bols": len(bols),
        "expected_discrepancies": len(expected),
        "dirty_invoices": len(dirty_set),
        "gray_invoices": len(gray_list),
        "dirty_ids": sorted(f"INV-{i:04d}" for i in dirty_set),
        "gray_ids": sorted(f"INV-{i:04d}" for i in gray_list),
    }
    conn.close()
    return counts


if __name__ == "__main__":
    c = generate()
    print("datagen complete ->", db_path())
    for k, v in c.items():
        print(f"  {k:24s} {v}")
