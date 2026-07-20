"""P1 用户侧文案/呈现层工具（陌生人测试 P1-1/P1-4/P1-6 修复）：纯函数、零 Streamlit 依赖。

设计动机（≤5 行「为什么这样建」）：
- 四红线之一是 actions/engine 不可碰——已知的内部码（D9/C4、C1 决策血缘、MEM- id、expedite_flag 等）
  是动作层原样返回的字符串，只能在 UI 展示前这一层清洗/翻译，绝不改 actions.py 的返回值本身。
- 与 rbac_nav.py / my_today.py / data_scope.py 同规：纯数据 + 纯函数，可被 streamlit_app.py /
  object_workbench.py 与测试直接 import，不触发 Streamlit 脚本执行（streamlit_app.py 顶层有副作用，
  不能被安全 import，故所有可单测的呈现逻辑都放这类纯模块）。
- 费种/规则中文名取自权威一手源：cost-manual-v0.4.md §1.3（费种）与 engine/*.py 规则实现本身
  （rule_id/type 字段），不是拍脑袋翻译。
"""
import re

# ========== P1-4① 费种代码 → 中文（权威源：docs/cost-manual-v0.4.md §1.3 charge_code 枚举原文）==========
CHARGE_CODE_CN = {
    "OFT": "海运费", "THC": "码头操作费", "DOC": "文件费", "FSC": "燃油附加费",
    "CUS": "清关费", "DTY": "关税", "WHS": "仓库操作费", "STO": "仓储费",
    "LMD": "尾程派送费", "DET": "滞箱费", "DEM": "滞港费", "CHS": "车架费", "ACC": "地址更正费",
}
CHARGE_CODE_LEGEND = "　".join(f"{k}={v}" for k, v in CHARGE_CODE_CN.items())


def charge_code_label(code):
    """费种列展示用：'OFT 海运费'；未知费种原样返回（不猜）。"""
    cn = CHARGE_CODE_CN.get(code)
    return f"{code} {cn}" if cn else code


# ========== P1-4② 规则代码 → 中文（权威源：engine/rules.py、cost_rules.py、procurement_rules.py、
# warehouse_rules.py、sourcing_rules.py 里 emit() 的 type 字段与规则实现注释）==========
RULE_CN = {
    "R1": "延误传导", "R2": "单证缺失", "R3": "静默停滞",
    "R4": "费率超收", "R5": "重复计费", "R6": "计划外费用",
    "R7": "供应商交期延误", "R8": "短装", "R9": "质量不合格",
    "R10": "价量不符", "R11": "开票超实收", "R12": "预付款敞口",
    "R13": "供应商资质过期", "R14": "单一来源断供", "R15": "绕流程采购(maverick)",
    "R16": "断货", "R17": "不可履约", "R18": "盘点差异",
    # R19-R21 资金流域（源：engine/finance_rules.py emit() type；CN 与 cockpit aiFlowModel.ts 同名）
    "R19": "逾期应收", "R20": "现金水位", "R21": "重复或不符付款",
    "R22": "供应商绩效劣化", "R23": "资质过期预警",
}
RULE_LEGEND = "　".join(f"{k}={v}" for k, v in RULE_CN.items())


def rule_label(rule_id):
    """规则列展示用：'R1 延误传导'；未知规则码原样返回（不猜）。"""
    cn = RULE_CN.get(rule_id)
    return f"{rule_id} {cn}" if cn else rule_id


# ========== 状态枚举 → 中文（供 side_effects 里 "A→B" 箭头翻译；未覆盖的 token 原样保留，不瞎猜）==========
STATUS_CN = {
    "open": "待处理", "acknowledged": "已受理", "mitigating": "处置中",
    "resolved": "已解决", "escalated": "已升级",
    "assigned": "已派单", "in_progress": "处理中", "done": "已完成", "cancelled": "已取消",
    "pending": "待审批", "approved": "已批准", "rejected": "已驳回",
    "draft": "草稿", "in_precheck": "预审中", "plan_ready": "方案就绪", "priced": "已报价",
    "needs_more_info": "待补资料", "quote_with_conditions": "有条件批准",
    "candidate": "候选", "active": "在售",
    "allocated": "已分配", "at_risk": "有风险", "backordered": "缺货待补",
    "reserved": "已预留", "released": "已释放", "fulfilled": "已完成履约",
    "received": "已收货", "under_review": "审核中", "disputed": "争议中",
    "sent": "已发出", "on_hold": "已冻结",
    "expedited": "已加急", "claim_raised": "已发起索赔", "variance_accepted": "差异已接受",
    "revoked": "已吊销", "provided": "已提供", "reconciled": "已核对平", "responded": "已回复",
}

# side_effects 里常见的英文对象类名前缀（如 "RiskEvent RSK-x: ..."）——对象 ID 本身已含类型信息
# （RSK-/TSK-/SHP- 前缀，voice guide 要求带对象 ID），故英文类名整体可去掉不损失信息。
_CLASS_PREFIXES = ("RiskEvent", "Task", "Shipment", "AdmissionCase", "Sku", "CostScenario",
                   "LogisticsPlan", "PurchaseOrder", "SupplierInvoice", "Reservation",
                   "InventoryPosition", "SalesOrderLine", "DQ issue")
_CLASS_PREFIX_RE = re.compile(r"^(?:" + "|".join(_CLASS_PREFIXES) + r")\s+")

_ARROW_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*→\s*([A-Za-z_][A-Za-z0-9_]*)\b")
# 内部决策/引用码：D8/D9、C1-C4、G1-G4、M1、A5、XC4、XB6 等——只删代码 token，保留同一括号内的人话说明
_CODE_TOKEN_RE = re.compile(r"[DCGMAX]{1,2}\d+(?:/[DCGMAX]{1,2}\d+)*")

QUALITY_LABEL_CN = {"effective": "有效", "partial": "部分有效", "ineffective": "无效"}

# 已知的固定 side_effects 原文 → 人话改写（精确匹配，覆盖陌生人测试点名的具体案例：
# expedite_flag/MEM- 处置记忆 id/D9/C2/C1 决策血缘——这些是 actions.py 里少数几处
# 拼了内部架构术语而非纯状态描述的行，穷举后精确重写，比通用正则更可靠）。
_EXACT_SIDE_EFFECT_REWRITES = [
    (re.compile(r"^Shipment (\S+) expedite_flag=1，行风险解除（D9/C2 简化）$"),
     lambda m: f"{m.group(1)} 已标记加急，交期风险解除"),
    (re.compile(r"^处置记忆已归档 \S+（C1 决策血缘）$"),
     lambda m: "本次处理已归档，供同类风险参考"),
    (re.compile(r"^处置记忆结果已回填 (\d+) 条（质量标签 ([^）]*)）$"),
     lambda m: f"历史处理效果已归档 {m.group(1)} 条（质量标签：{QUALITY_LABEL_CN.get(m.group(2), m.group(2))}）"),
    (re.compile(r"^处置记忆结果已回填 (\d+) 条（未打质量标签）$"),
     lambda m: f"历史处理效果已归档 {m.group(1)} 条（未打质量标签）"),
]
# 已知的固定报错原文 → 人话改写（精确匹配，优先于通用兜底；覆盖陌生人测试点名的具体案例）
_EXACT_ERROR_REWRITES = [
    (re.compile(r"^已存在非终态任务 (\S+)（单风险单任务，D9/C4）$"),
     lambda m: f"该风险已有任务 {m.group(1)} 在处理，不能重复派单"),
]
# DQ 动作层的内部枚举错误码 → 人话（app/dq_actions.py 固定返回这几个 token，穷举覆盖）
_DQ_ERROR_CN = {
    "role_not_permitted": "该角色无权限执行此操作",
    "assignee_user_id_required": "请填写负责人",
    "dq_issue_not_found": "待核对项不存在（可能已被处理）",
    "dq_issue_already_closed": "该项已核对关闭，无需重复处理",
    "resolution_required": "请填写核对结论",
}
# M1 审批边界拒绝原因（app.action_context.ApprovalPolicy.can_approve 固定返回这几个 token，
# actions.py/admission_actions.py 都拼成 "M1 审批边界拒绝：{reason}"——同一正则通吃两处）。
_M1_REASON_CN = {
    "maker_checker_violation": "提案人与审批人不能是同一人（maker-checker：谁提的谁不能批）",
    "manager_role_required": "只有经理角色能审批",
}
_M1_REJECT_RE = re.compile(r"^M1 审批边界拒绝：(\S+)$")


def _status_cn(tok):
    return STATUS_CN.get(tok, tok)


def _arrow_sub(m):
    return f"{_status_cn(m.group(1))}→{_status_cn(m.group(2))}"


def _try_exact_rewrites(text, table):
    for pat, repl in table:
        m = pat.match(text)
        if m:
            return repl(m)
    return None


def sanitize_technical(text):
    """通用兜底清洗（P1-1 安全网）：去掉内部决策引用码、英文类名前缀，状态箭头翻成中文。
    未识别的 token 原样保留——宁可留一点技术痕迹，不编中文。已知的整句消息优先走精确改写表
    （_EXACT_SIDE_EFFECT_REWRITES），本函数是它们之外的通用兜底，覆盖长尾 admission/
    coordination/procurement/warehouse 等动作模块的零散消息。"""
    if not text:
        return text
    t = text
    t = _CLASS_PREFIX_RE.sub("", t)
    # 箭头翻译须在 "case→" 字面替换之前做（替换后左侧变中文，ASCII 箭头正则无法再识别）
    t = _ARROW_RE.sub(_arrow_sub, t)
    t = t.replace("case→", "案件状态→")
    t = re.sub(r"\bcreated \(draft\)", "已创建（草稿）", t)
    t = re.sub(r"\bcreated\b", "已创建", t)
    t = re.sub(r"\bassigned to\b", "已分配给", t)
    t = re.sub(r"\s*margin=", "，毛利 $", t)
    t = re.sub(r",?\s*risk_level=", "，风险等级=", t)
    t = re.sub(r"^(\d+) findings recorded$", r"\1 条合规发现已记录", t)
    # 括号内只剩内部引用码 → 整个括号去掉；括号内还有其它人话文字 → 只删码留文字
    t = re.sub(r"[（(]\s*" + _CODE_TOKEN_RE.pattern + r"\s*[）)]", "", t)

    def _strip_code_in_parens(m):
        inner = m.group(1)
        cleaned = _CODE_TOKEN_RE.sub("", inner)
        cleaned = re.sub(r"^[，,、/\s]+|[，,、/\s]+$", "", cleaned)
        return f"（{cleaned}）" if cleaned else ""

    t = re.sub(r"[（(]([^）)]*)[）)]", _strip_code_in_parens, t) if _CODE_TOKEN_RE.search(t) else t
    t = re.sub(r"\s{2,}", " ", t).strip(" ，,")
    return t


def humanize_error(text):
    """失败回执人话化：先查精确改写表/DQ 码表/M1 拒绝原因，查不到再走通用兜底清洗（P1-1）。"""
    if not text:
        return text
    if text in _DQ_ERROR_CN:
        return _DQ_ERROR_CN[text]
    if text.startswith("sqlite_error:"):
        return "系统写入异常，请重试；如反复出现请联系管理员（技术详情已记入审计日志）"
    m1 = _M1_REJECT_RE.match(text)
    if m1:
        return _M1_REASON_CN.get(m1.group(1), m1.group(1))
    exact = _try_exact_rewrites(text, _EXACT_ERROR_REWRITES)
    if exact is not None:
        return exact
    return sanitize_technical(text)


def sanitize_side_effect_line(text):
    """单条 side_effect 人话化：先查精确改写表，查不到再走通用兜底清洗。"""
    if not text:
        return text
    exact = _try_exact_rewrites(text, _EXACT_SIDE_EFFECT_REWRITES)
    if exact is not None:
        return exact
    return sanitize_technical(text)


def humanize_side_effects(side_effects):
    """成功回执兜底人话化：逐条清洗后用中文分号拼接（无 human= 覆盖时的默认路径）。"""
    cleaned = [sanitize_side_effect_line(s) for s in (side_effects or [])]
    cleaned = [c for c in cleaned if c]
    return "；".join(cleaned)


# ========== P1-4③ 根因（root_cause）人话模板 ==========
# 只翻译 engine/rules.py 的 R1-R3（英文技术表述，delay/docs/stalled）；R4-R23 的 root_cause 在引擎
# 实现里本就是中文人话（cost/procurement/warehouse/sourcing/finance/supplier_risk 规则自带——
# R22/R23 根因模板见 engine/supplier_risk_rules.py，含供应商名/达成率/迟交单列表/证书到期日），
# 故不在此重复处理。
_R1_RE = re.compile(r"^eta_current\+(\d+)d buffers breaches promise by (\d+)d$")
_R2_RE = re.compile(r"^missing ([\w,]+) with eta within (\d+)d$")
_R3_RE = re.compile(r"^in_transit with no milestone for (\d+)d$")
_MISSING_DOC_CN = {
    "commercial_invoice": "商业发票", "packing_list": "装箱单",
    "bill_of_lading": "提单", "isf": "ISF 进口安全申报",
}


def _translate_r1(root_cause):
    m = _R1_RE.match(root_cause)
    if not m:
        return None
    days = m.group(2)
    return f"预计到港时间（含清关+尾程缓冲）比承诺交期晚 {days} 天"


def _translate_r2(root_cause):
    m = _R2_RE.match(root_cause)
    if not m:
        return None
    docs = "、".join(_MISSING_DOC_CN.get(d, d) for d in m.group(1).split(","))
    return f"缺少单证：{docs}（预计到港在 {m.group(2)} 天内，须尽快补齐）"


def _translate_r3(root_cause):
    m = _R3_RE.match(root_cause)
    if not m:
        return None
    return f"货物在途，但已 {m.group(1)} 天没有物流状态更新（静默停滞，需人工核实）"


_ROOT_CAUSE_TRANSLATORS = {"R1": _translate_r1, "R2": _translate_r2, "R3": _translate_r3}


def humanize_root_cause(rule_id, root_cause):
    """P1-4③：按规则类型翻译 root_cause 为人话；翻不了的保留原文，前缀「技术表述：」标注来源
    （不是查不到字典就瞎编中文，宁可留原文让人自己判断）。R4-R18 已是人话，原样返回。"""
    if not root_cause:
        return root_cause
    translator = _ROOT_CAUSE_TRANSLATORS.get(rule_id)
    if translator is None:
        return root_cause
    out = translator(root_cause)
    if out is not None:
        return out
    return f"技术表述：{root_cause}"


# ========== P1-6 风险队列搜索 / 排序（纯函数，操作 list[dict]，不动数据口径只筛呈现）==========
def filter_risks_by_search(risks, query):
    """按风险 ID / 货运号 / 类型模糊匹配（大小写不敏感）；query 为空则不过滤。"""
    if not query or not query.strip():
        return risks
    q = query.strip().lower()
    return [r for r in risks
            if q in str(r.get("risk_event_id", "")).lower()
            or q in str(r.get("shipment_id", "")).lower()
            or q in str(r.get("type", "")).lower()]


RISK_SORT_CHOICES = ("级别→金额（默认）", "延误天数", "影响金额")


def sort_risks(risks, sort_choice):
    """默认选项保持调用方已排好的顺序（级别→金额，含 ROLE_RISK_FOCUS 置顶）不重排；
    其余选项按对应字段降序（最急/影响最大的在前）。"""
    if sort_choice == "延误天数":
        return sorted(risks, key=lambda r: -(r.get("delay_days") or 0))
    if sort_choice == "影响金额":
        return sorted(risks, key=lambda r: -(r.get("affected_value_usd") or 0))
    return risks


# ========== P1-6 发票列表搜索 / 只看异常（纯函数）==========
def anomaly_invoice_ids(risk_affected_line_id_lists, invoice_line_to_invoice):
    """risk_affected_line_id_lists: 各费用风险 affected_invoice_line_ids（已 json.loads 的 list）；
    invoice_line_to_invoice: {invoice_line_id: invoice_id}。返回命中费用异常的 invoice_id 集合。"""
    hit_lines = set()
    for ids in risk_affected_line_id_lists:
        hit_lines |= set(ids or [])
    return {invoice_line_to_invoice[lid] for lid in hit_lines if lid in invoice_line_to_invoice}


def filter_invoices(invoices, query, anomaly_ids, only_anomaly):
    """按发票号 / vendor / 货运号模糊匹配 + 可选「只看异常」（anomaly_ids 命中）。"""
    out = invoices
    if only_anomaly:
        out = [x for x in out if x.get("invoice_id") in anomaly_ids]
    if query and query.strip():
        q = query.strip().lower()
        out = [x for x in out
               if q in str(x.get("invoice_id", "")).lower()
               or q in str(x.get("vendor_name", "")).lower()
               or q in str(x.get("shipment_id", "")).lower()]
    return out
