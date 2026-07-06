"""W2 主入口：python3 -m datagen.generate [--config config/datagen.yaml]

产出：
  data/raw/    源系统导出（含噪声）：oms_* / srm_* / tms_* / catalog_skus + _manifest.json
  data/truth/  ground truth（仅 datagen/verify/W4 评估可读）：expected_risk_events、
               injected_noise_log、world_snapshot.json
  data/mock_source.sqlite
"""
import argparse
import csv
import json
import random
import sqlite3
from pathlib import Path

import yaml

from . import world as W
from .design_cases import apply_design_cases
from .noise import apply_noise
from .oracle import sweep


def build(cfg):
    rng = random.Random(cfg["seed"])
    w = W.build_world(cfg, rng)
    design_noise = apply_design_cases(w, rng)
    W.ensure_multi_customer_breach(w, cfg, rng)
    W.enrich_shipments(w, rng)      # D11 Tier 1 字段
    W.enrich_milestones(w, rng)
    W.sync_po_status(w)
    W.finalize_line_status(w)
    expected = sweep(w, cfg)
    noise = apply_noise(w, design_noise, cfg, rng)
    return w, expected, noise


def _dump(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(rows)


def write_outputs(w, expected, noise, cfg, raw_dir, truth_dir, sqlite_path=None):
    raw, truth = Path(raw_dir), Path(truth_dir)
    raw.mkdir(parents=True, exist_ok=True)
    truth.mkdir(parents=True, exist_ok=True)
    vm = noise["variant_map"]

    tables = {}
    tables["srm_suppliers"] = ([dict(s) for s in sorted(w["suppliers"].values(), key=lambda x: x["supplier_id"])],
                               ["supplier_id", "supplier_name", "city", "lead_time_days"])
    tables["catalog_skus"] = ([dict(s) for s in sorted(w["skus"].values(), key=lambda x: x["sku_id"])],
                              ["sku_id", "sku_name", "category", "unit_price_usd", "supplier_id"])
    tables["oms_customers"] = ([dict(c) for c in sorted(w["customers"].values(), key=lambda x: x["customer_id"])],
                               ["customer_id", "customer_name", "tier", "us_state"])
    tables["oms_sales_orders"] = ([{**s, "order_date": s["order_date"].isoformat()}
                                   for s in sorted(w["sos"].values(), key=lambda x: x["so_id"])],
                                  ["so_id", "customer_id", "order_date"])
    tables["oms_so_lines"] = ([{**l, "promised_delivery_date": l["promised_delivery_date"].isoformat()}
                               for l in sorted(w["lines"].values(), key=lambda x: x["so_line_id"])],
                              ["so_line_id", "so_id", "sku_id", "qty", "unit_price_usd",
                               "promised_delivery_date"])
    tables["srm_purchase_orders"] = ([{**p, "po_date": p["po_date"].isoformat(),
                                       "expected_ready_date": p["expected_ready_date"].isoformat()}
                                      for p in sorted(w["pos"].values(), key=lambda x: x["po_id"])],
                                     ["po_id", "supplier_id", "sku_id", "qty", "po_date",
                                      "expected_ready_date", "status"])

    ship_rows = []
    for sp in sorted(w["shipments"].values(), key=lambda x: x["shipment_id"]):
        sid = sp["shipment_id"]
        nulled = sid in noise["null_ships"]
        sup_names = sorted({vm.get(w["pos"][p]["supplier_id"],
                                   w["suppliers"][w["pos"][p]["supplier_id"]]["supplier_name"])
                            for p in sp["po_ids"]})
        ship_rows.append({
            "shipment_id": sid, "booking_no": sp["booking_no"], "mbl_no": sp["mbl_no"],
            "mode": sp["mode"], "container_no": sp["container_no"] or "",
            "container_type": sp["container_type"],
            "vessel_voyage": "" if nulled or not sp["vessel_voyage"] else sp["vessel_voyage"],
            "carrier_name": "" if nulled or not sp["carrier_name"] else sp["carrier_name"],
            "carrier_scac": sp["carrier_scac"],  # 真实形态：名字可缺，SCAC 常在
            "incoterm": sp["incoterm"],
            "gross_weight_kg": sp["gross_weight_kg"], "volume_cbm": sp["volume_cbm"],
            "origin_port": sp["origin_port"], "origin_port_locode": sp["origin_port_locode"],
            "destination_port": sp["destination_port"],
            "destination_port_locode": sp["destination_port_locode"],
            "destination_warehouse": sp["destination_warehouse"],
            "etd": sp["etd"].isoformat(), "eta_initial": sp["eta_initial"].isoformat(),
            "status": noise["emit_status"].get(sid, sp["status"]),
            "missing_docs": "|".join(sp["missing_docs"]),
            "supplier_names": "|".join(sup_names), "po_ids": "|".join(sp["po_ids"]),
        })
    tables["tms_shipments"] = (ship_rows,
                               ["shipment_id", "booking_no", "mbl_no", "mode", "container_no",
                                "container_type", "vessel_voyage", "carrier_name", "carrier_scac",
                                "incoterm", "gross_weight_kg", "volume_cbm",
                                "origin_port", "origin_port_locode", "destination_port",
                                "destination_port_locode", "destination_warehouse", "etd",
                                "eta_initial", "status", "missing_docs", "supplier_names", "po_ids"])
    tables["tms_milestones"] = (sorted(noise["ms_rows"], key=lambda r: (r["ingested_at"], r["milestone_id"])),
                                ["milestone_id", "shipment_id", "event_type", "event_classifier",
                                 "event_time", "event_locode", "new_eta", "source_system", "ingested_at"])
    tables["tms_allocations"] = (sorted(w["allocations"], key=lambda a: a["allocation_id"]),
                                 ["allocation_id", "shipment_id", "so_line_id", "allocated_qty"])

    for name, (rows, cols) in tables.items():
        _dump(raw / f"{name}.csv", rows, cols)

    # ground truth
    _dump(truth / "expected_risk_events.csv", expected,
          ["expected_risk_id", "rule_id", "type", "shipment_id", "affected_so_line_ids",
           "severity", "breach_days", "affected_value_usd", "reason", "case_id"])
    _dump(truth / "injected_noise_log.csv", noise["noise_log"],
          ["noise_id", "noise_type", "target_table", "target_id", "description", "case_id"])
    snapshot = {
        "as_of": w["as_of"].isoformat(),
        "shipments": {s["shipment_id"]: {
            "status": s["status"], "eta_current": s["eta_current"].isoformat(),
            "ata": s["ata"].isoformat() if s["ata"] else None,
            "customs_status": s["customs_status"], "missing_docs": s["missing_docs"]}
            for s in sorted(w["shipments"].values(), key=lambda x: x["shipment_id"])},
        "lines": {l["so_line_id"]: l["line_status"]
                  for l in sorted(w["lines"].values(), key=lambda x: x["so_line_id"])},
    }
    (truth / "world_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=1))

    manifest = {"seed": cfg["seed"], "as_of": cfg["window"]["as_of"],
                "counts": {k: len(v[0]) for k, v in tables.items()},
                "expected_risk_events": len(expected), "injected_noise": len(noise["noise_log"])}
    (raw / "_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))

    if sqlite_path:
        Path(sqlite_path).unlink(missing_ok=True)
        con = sqlite3.connect(sqlite_path)
        for name, (rows, cols) in tables.items():
            con.execute(f"CREATE TABLE {name} ({', '.join(c + ' TEXT' for c in cols)})")
            con.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' * len(cols))})",
                            [[str(r.get(c, '')) for c in cols] for r in rows])
        con.commit()
        con.close()
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/datagen.yaml")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    w, expected, noise = build(cfg)
    manifest = write_outputs(w, expected, noise, cfg,
                             cfg["output"]["raw_dir"], cfg["output"]["truth_dir"],
                             sqlite_path="data/mock_source.sqlite")
    print(json.dumps(manifest, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
