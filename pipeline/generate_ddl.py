#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桥3 结构生成（生成器）：本体 objects[].properties → SQLite CREATE TABLE + 影子对比。

    python3 -m pipeline.generate_ddl             # 打印 34 表生成 DDL
    python3 -m pipeline.generate_ddl --shadow     # 逐表对比"生成 DDL vs 实库 schema"

设计选型：结构性低频（DDL）用**生成型**——本体是唯一权威源，改本体重跑。build_ontology 建
34 对象表时 `import` 本模块的 `object_ddls()` 现取生成 DDL（不再散落硬编码 34 处列清单）。

类型映射（plan M3 唯一权威表 + 本体已有但表未列的类型按 SQLite 存量存储最保守读法补全）：
    string/enum/date/datetime/json/json_list/list → TEXT
    number → REAL      integer → INTEGER      boolean → INTEGER（0/1，沿 SQLite 惯例）

影子对比的"对账参照"是桥1 闸门（pipeline.ontology_lint）：**直接复用它的
`expected_affinities` / `sqlite_affinity` / `object_table_name`——同一把尺子**。判定规则与
lint 的 A 断言逐字一致：某列"漂移"当且仅当 `实库列亲和 ∉ 本体类型可接受亲和集`。故：
  · number 列在库里是 INTEGER 或 REAL 都不算差异（亲和皆数值族）——qty 等整数列零噪声；
  · boolean 列在库里是 TEXT（存 "True"/"False"）也不算差异（boolean 容忍 TEXT，避噪）；
  · **唯一不兼容**：skus 的 5 个金额/尺寸字段本体=number 但库里=TEXT（TEXT ∉ 数值族）→ 恰 5 处。
这 5 处正是桥3 要修的 A 类类型漂移（M1 规则8 留给 M3）。切库重建后（number→REAL 落地）影子归零。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from pipeline.ontology_lint import (expected_affinities, load_db_schema,
                                    load_ontology, object_table_name,
                                    sqlite_affinity)

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "ontology.sqlite"

# 本体 type → SQLite 声明类型（plan M3 唯一权威映射表 + docstring 所述补全）。
DDL_TYPE = {
    "string": "TEXT",
    "enum": "TEXT",
    "date": "TEXT",
    "datetime": "TEXT",
    "json": "TEXT",
    "json_list": "TEXT",
    "list": "TEXT",
    "number": "REAL",
    "integer": "INTEGER",
    "boolean": "INTEGER",
}


def ddl_type(onto_type: str) -> str:
    """本体属性类型 → SQLite 声明类型（未知类型最保守读法：TEXT）。"""
    return DDL_TYPE.get(onto_type, "TEXT")


def object_ddls(onto: dict) -> dict[str, tuple[str, list[str]]]:
    """{表名: (CREATE TABLE 语句, 列名有序列表)}——build_ontology 消费。

    列序 = 本体 properties 声明序；主键用 `PRIMARY KEY (pk)` 表级约束（与既有 table() 同形）。
    列名有序列表供 build_ontology 按列名从行 dict 取值做定位插入（顺序无关取值，重排安全）。"""
    out: dict[str, tuple[str, list[str]]] = {}
    for obj in onto["objects"]:
        table = object_table_name(obj)
        colnames = [p["name"] for p in obj["properties"]]
        col_defs = [f"{p['name']} {ddl_type(p.get('type'))}" for p in obj["properties"]]
        pk = obj.get("primaryKey")
        create = f"CREATE TABLE {table} ({', '.join(col_defs)}, PRIMARY KEY ({pk}))"
        out[table] = (create, colnames)
    return out


def _shadow(onto: dict) -> tuple[list[tuple], list[str]]:
    """逐表对比生成 DDL vs 实库 schema，返回 (差异清单, 说明)。

    判定复用 lint 的 expected_affinities（同一把尺子）：列漂移 ⟺ 实库列亲和 ∉ 本体类型可接受集。
    列缺失/多余也报（应为 0，本体属性集 == 实库列集）。"""
    diffs: list[tuple] = []
    notes: list[str] = []
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)  # 红线：只读
    try:
        schema = load_db_schema(con)
    finally:
        con.close()
    for obj in onto["objects"]:
        otype = obj["type"]
        table = object_table_name(obj)
        db_cols = schema.get(table)
        if db_cols is None:
            diffs.append((table, "(整表)", "MISSING", f"生成表 `{table}` 在实库不存在"))
            continue
        db_type_of = {c[0]: c[1] for c in db_cols}
        prop_by_name = {p["name"]: p for p in obj["properties"]}
        gen_names = set(prop_by_name)
        db_names = set(db_type_of)
        for col in sorted(gen_names - db_names):
            diffs.append((table, col, "MISSING", f"生成列 `{col}` 在实库表无对应列"))
        for col in sorted(db_names - gen_names):
            diffs.append((table, col, "EXTRA", f"实库列 `{col}` 本体未声明"))
        for col in sorted(gen_names & db_names):
            otype_col = prop_by_name[col].get("type")
            gen = ddl_type(otype_col)
            db_decl = db_type_of[col] or "(无)"
            exp = expected_affinities(otype_col)
            if not exp:
                continue
            db_aff = sqlite_affinity(db_type_of[col])
            if db_aff not in exp:
                diffs.append((table, col, "DRIFT",
                              f"生成 `{gen}` vs 实库 `{db_decl}`(亲和 {db_aff}) — "
                              f"本体类型={otype_col} 期望亲和∈{sorted(exp)}"))
    notes.append("判定尺子 = 桥1 lint expected_affinities（number 容 INTEGER/REAL、boolean 容 TEXT，避噪）。")
    notes.append("影子对比只读实库；生成侧不建库、不写任何表——纯诊断。")
    return diffs, notes


def render_shadow(onto: dict) -> tuple[str, int]:
    diffs, notes = _shadow(onto)
    lines = []
    lines.append("=" * 78)
    lines.append("桥3 结构生成 · 影子对比（生成 DDL vs 实库 schema）")
    lines.append(f"本体：ontology v{onto.get('version')}（{len(onto['objects'])} 对象表）  "
                 f"实库：{DB_PATH.relative_to(REPO_ROOT)}（只读）")
    lines.append("=" * 78)
    if diffs:
        lines.append(f"差异 {len(diffs)} 处：")
        for i, (table, col, kind, detail) in enumerate(diffs, 1):
            lines.append(f"  {i:>2}. 【{kind}】[{table}.{col}] {detail}")
    else:
        lines.append("✓ 零差异：生成 DDL 与实库 schema 逐表一致。")
    lines.append("")
    lines.append("说明：")
    for n in notes:
        lines.append(f"  - {n}")
    lines.append("=" * 78)
    lines.append(f"影子对比合计：{len(diffs)} 处差异")
    return "\n".join(lines), len(diffs)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m pipeline.generate_ddl",
        description="桥3：本体 → SQLite CREATE TABLE 生成 + 影子对比")
    parser.add_argument("--shadow", action="store_true",
                        help="逐表对比生成 DDL vs 实库 schema（诊断，只读）")
    args = parser.parse_args(argv)
    onto = load_ontology()
    if args.shadow:
        report, _ = render_shadow(onto)
        print(report)
        return 0
    for table, (create, _cols) in object_ddls(onto).items():
        print(create + ";")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
