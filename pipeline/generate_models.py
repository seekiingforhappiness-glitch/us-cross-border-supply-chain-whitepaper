#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""桥3 结构生成（生成器）：本体 objects[].properties → Pydantic 模型。

    python3 -m pipeline.generate_models          # 重跑，覆盖写 pipeline/ontology_models.py

设计选型（V5 调研结论）：结构性低频（Pydantic 模型 / DDL）用**生成型**——产物文件入版本
控制，改本体重跑生成器（区别于权限/工具的解释型）。本脚本产出 `pipeline/ontology_models.py`
（逐对象模型 + MODEL_BY_TABLE 注册表），供 build_ontology 插入前 `model_validate` 做数据契约校验。

类型映射（plan M3 唯一权威表 + 本体已有但表未列的类型按 SQLite 亲和/存量存储的最保守读法补全）：
    本体 type            Pydantic
    string               str
    number               float
    integer              int
    boolean              bool
    enum(带 values)      Literal[...]
    enum(无 values)      str            （本体个别 enum 未列 values，如 Task.approved_by_role）
    date/datetime        str            （库存 TEXT ISO 串；不转 date 对象，校验原样字符串）
    json/json_list/list  str            （库存 TEXT 的 JSON 串）
    required=false       Optional[...] = None

空串归一（关键）：datagen 对缺省的可空字段产出空串 ""（如 20/35 SKU 的 declared_value_usd=""）。
基类 before-validator 把 "" 归一为 None——可空字段校验为 None（合法），必填字段若为 "" 归一后为
None 则触发校验失败（如实暴露，不掩盖）。这样 model_validate 面对 datagen 原始行（全字符串 + 空串）
能零违例通过，且不改变被插入的原始值（校验是旁路闸门，不做转换写回）。
"""
from __future__ import annotations

from pathlib import Path

from pipeline.ontology_lint import object_table_name, load_ontology

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "pipeline" / "ontology_models.py"

# 标量类型 → Pydantic 类型名（enum 单独处理；date/datetime/json/json_list/list 见模块 docstring）。
PY_SCALAR = {
    "string": "str",
    "date": "str",
    "datetime": "str",
    "json": "str",
    "json_list": "str",
    "list": "str",
    "number": "float",
    "integer": "int",
    "boolean": "bool",
}


def field_annotation(prop: dict) -> str:
    """本体属性 → Pydantic 字段注解（不含默认值部分）。"""
    ptype = prop.get("type")
    if ptype == "enum":
        values = prop.get("values")
        if values:
            inner = ", ".join(repr(v) for v in values)
            return f"Literal[{inner}]"
        return "str"  # 未列 values 的 enum（本体个别字段）退化为 str
    return PY_SCALAR.get(ptype, "str")  # 未知类型最保守读法：str（库存 TEXT）


def render_field(prop: dict) -> str:
    """渲染一行字段声明。required=True → 必填；否则 Optional[...] = None。"""
    ann = field_annotation(prop)
    name = prop["name"]
    if prop.get("required") is True:
        return f"    {name}: {ann}"
    return f"    {name}: Optional[{ann}] = None"


def render_model(obj: dict) -> str:
    cls = obj["type"]
    lines = [f"class {cls}(_Base):"]
    doc = f'    """{cls} — GENERATED, 表 {object_table_name(obj)}。"""'
    lines.append(doc)
    for prop in obj["properties"]:
        lines.append(render_field(prop))
    return "\n".join(lines)


HEADER = '''\
# GENERATED FROM ontology v{version} — DO NOT EDIT，重跑 python3 -m pipeline.generate_models
# -*- coding: utf-8 -*-
"""桥3 结构生成产物：本体 {n_objects} 对象的 Pydantic 模型（数据契约校验用）。

来源：ontology/control-tower-ontology.json（objects[].properties）。
生成器：pipeline/generate_models.py。手改无效——改本体后重跑生成器覆盖本文件。

用途：pipeline.build_ontology 插入前 `Model.model_validate(row)` 做行级契约校验
（warn→enforce 两档）。基类 _Base：extra 忽略（行里多余键不报错）+ 空串→None 归一
（datagen 缺省可空字段产 ""，归一后可空字段合法、必填字段暴露为违例）。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_core.core_schema import ValidationInfo


class _Base(BaseModel):
    """全模型基类：忽略多余键；before-validator 把**可空字段**的空串归一为 None。

    仅对可空（Optional，有默认 None）字段做 ""→None：datagen 对缺省可空字段产空串，语义=缺省。
    必填字段的空串保持原样（"" 对必填 str/list-as-str 是合法在场空值，如 missing_docs="" 表示无缺失
    单证）——不误伤，也不放水（必填数值/枚举字段若为 "" 会在后续类型校验如实暴露为违例）。"""

    model_config = ConfigDict(extra="ignore")

    @field_validator("*", mode="before")
    @classmethod
    def _empty_str_to_none(cls, v, info: ValidationInfo):
        if v == "":
            field = cls.model_fields.get(info.field_name)
            if field is not None and not field.is_required():
                return None
        return v
'''


def build_source(onto: dict) -> str:
    parts = [HEADER.format(version=onto.get("version"), n_objects=len(onto["objects"]))]
    for obj in onto["objects"]:
        parts.append("")
        parts.append("")
        parts.append(render_model(obj))
    # 注册表：表名 → 模型类（build_ontology 按表名取模型校验）。
    parts.append("")
    parts.append("")
    parts.append("MODEL_BY_TABLE = {")
    for obj in onto["objects"]:
        parts.append(f'    {object_table_name(obj)!r}: {obj["type"]},')
    parts.append("}")
    parts.append("")
    parts.append("MODEL_BY_TYPE = {")
    for obj in onto["objects"]:
        parts.append(f'    {obj["type"]!r}: {obj["type"]},')
    parts.append("}")
    parts.append("")
    return "\n".join(parts)


def main() -> int:
    onto = load_ontology()
    src = build_source(onto)
    OUT_PATH.write_text(src, encoding="utf-8")
    n = len(onto["objects"])
    print(f"generate_models: 写出 {n} 个模型 → {OUT_PATH.relative_to(REPO_ROOT)} "
          f"（本体 v{onto.get('version')}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
