"""W4 检测入口：python3 -m engine.detect [--as-of 2026-08-08]

跑 R1-R3 → 按 A2 CreateRiskEvent 语义写入 risk_events：
同 (shipment, type) 非终态事件合并（severity 取高、受影响行取并集），
受影响行置 at_risk，全程写 action_log（审计时间戳用 as_of，不用系统时钟——D8）。
"""
import argparse
import json
import sqlite3
from datetime import date

import yaml

from .rules import detect_risks, SEV_ORDER

DB = "data/ontology.sqlite"


def apply_candidates(con, cands, as_of):
    """A2 语义写库。返回 (created, merged)。"""
    cur = con.cursor()
    ts = f"{as_of.isoformat()}T00:00:00Z"
    seq = cur.execute("SELECT count(*) FROM risk_events").fetchone()[0]
    created = merged = 0
    for c in sorted(cands, key=lambda x: (x["shipment_id"], x["rule_id"])):
        ex = cur.execute("""SELECT * FROM risk_events WHERE shipment_id=? AND type=?
                            AND status NOT IN ('resolved','escalated')""",
                         (c["shipment_id"], c["type"])).fetchone()
        if ex:
            aff = sorted(set(json.loads(ex["affected_so_line_ids"]))
                         | set(json.loads(c["affected_so_line_ids"])))
            sev = ex["severity"] if SEV_ORDER[ex["severity"]] >= SEV_ORDER[c["severity"]] \
                else c["severity"]
            cur.execute("""UPDATE risk_events SET affected_so_line_ids=?, severity=?,
                           affected_value_usd=?, root_cause=? WHERE risk_event_id=?""",
                        (json.dumps(aff), sev, c["affected_value_usd"], c["root_cause"],
                         ex["risk_event_id"]))
            rid, result = ex["risk_event_id"], "merged"
            merged += 1
        else:
            seq += 1
            rid = f"RSK-{seq:04d}"
            cur.execute("""INSERT INTO risk_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (rid, c["type"], c["rule_id"], c["severity"], c["shipment_id"],
                         c["affected_so_line_ids"], c["affected_value_usd"], c["detected_at"],
                         c["root_cause"], "open", None, None, None))
            result = "created"
            created += 1
        # 受影响行 → at_risk（A2 副作用）
        for lid in json.loads(c["affected_so_line_ids"]):
            cur.execute("""UPDATE sales_order_lines SET line_status='at_risk'
                           WHERE so_line_id=? AND line_status='allocated'""", (lid,))
        cur.execute("""INSERT INTO action_log (actor, role, action, target_object_id,
                       params_json, as_of_date, timestamp, result) VALUES (?,?,?,?,?,?,?,?)""",
                    ("engine", "system", "CreateRiskEvent", rid,
                     json.dumps({"rule_id": c["rule_id"], "shipment_id": c["shipment_id"],
                                 "severity": c["severity"]}),
                     as_of.isoformat(), ts, result))
    con.commit()
    return created, merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/datagen.yaml")
    ap.add_argument("--as-of", default=None, help="默认取配置 window.as_of（D8：必须显式，禁系统时钟）")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config, encoding="utf-8"))
    as_of = date.fromisoformat(args.as_of or cfg["window"]["as_of"])

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cands = detect_risks(con, as_of, cfg)
    created, merged = apply_candidates(con, cands, as_of)
    by_rule = {}
    for c in cands:
        by_rule[c["rule_id"]] = by_rule.get(c["rule_id"], 0) + 1
    print(json.dumps({"as_of": as_of.isoformat(), "candidates": len(cands),
                      "created": created, "merged": merged, "by_rule": by_rule},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
