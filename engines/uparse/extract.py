# -*- coding: utf-8 -*-
"""规则抽取层：对每个通用字段与类型专属字段做条款级规则抽取，
并绑定原文证据（source_text / 条款位置 / 页码 / 置信度）。"""
import re

from . import standardize as std
from .schemas import make_field, UNIVERSAL_FIELDS, TYPE_FIELDS, FIELD_LABELS

# ---------- 角色映射 ----------
ROLE_MAP = {
    "贷款人": "资金提供方（金融机构）", "借款人": "资金需求方（融资主体）",
    "出租人": "租赁物购买与出租方", "承租人": "租赁物使用方",
    "保证人": "保证担保承担方", "抵押人": "抵押物提供方", "抵押权人": "抵押权享有方",
    "出质人": "质押物提供方", "质权人": "质权享有方",
    "保险人": "保险责任承担方（承保机构）", "投保人": "保费支付方",
    "被保险人": "受保障方", "受益人": "保险金请求方",
    "管理人": "资产管理与投资运作方", "托管人": "资产托管方",
    "委托人": "委托资金提供方", "受托人": "受托管理方",
    "保理商": "应收账款受让方", "应收账款转让人": "应收账款出让方",
    "卖方": "货物或服务提供方", "买方": "货物或服务受让方",
    "债权人": "债权享有方", "债务人": "债务承担方",
    "甲方": "合同甲方", "乙方": "合同乙方", "丙方": "合同丙方", "丁方": "合同丁方",
}

# 金额关键词按特异性排序（列表前面的优先级高）：命中越靠前的关键词，
# 其邻近金额越可能是合同主金额；泛化词（金额/额度/保证金）放最后兜底
AMOUNT_KEYWORDS = [
    # 先匹配具有明确业务角色的标签；不能把“授信额度/担保额度”当成实际本金。
    "实际融资金额", "实际借款金额", "实际贷款金额", "实际提款金额", "实际放款金额",
    "放款金额", "发放金额", "用信金额", "保理融资金额", "实际保理融资款",
    "借款金额", "贷款金额", "融资金额", "融资租赁本金", "租赁融资额", "租赁本金", "保理融资款",
    "债权本金", "投资本金", "借款本金", "贷款本金",
    "担保金额", "被担保金额", "担保余额", "担保额度",
    "购置总价", "租赁物价款", "租赁物购买价款", "转让价款", "总对价", "认购金额",
    "合同金额", "租金总额", "保险金额", "保额", "授信额度", "最高债权",
    "总金额", "本金", "价款", "额度", "金额", "保证金",
]

AMOUNT_ROLE_BY_LABEL = {
    "实际融资金额": "actual_financing_amount", "实际借款金额": "actual_financing_amount",
    "实际贷款金额": "actual_financing_amount", "实际提款金额": "actual_financing_amount",
    "实际放款金额": "actual_financing_amount", "放款金额": "actual_financing_amount",
    "发放金额": "actual_financing_amount", "用信金额": "actual_financing_amount",
    "保理融资金额": "actual_financing_amount", "实际保理融资款": "actual_financing_amount",
    "借款金额": "loan_amount", "贷款金额": "loan_amount", "融资金额": "loan_amount",
    "借款本金": "loan_amount", "贷款本金": "loan_amount",
    "融资租赁本金": "leasing_principal", "租赁融资额": "leasing_principal", "租赁本金": "leasing_principal",
    "保理融资款": "actual_financing_amount", "债权本金": "loan_amount", "投资本金": "loan_amount",
    "担保金额": "guarantee_amount", "被担保金额": "guarantee_amount", "担保余额": "guarantee_amount",
    "担保额度": "guarantee_limit", "授信额度": "credit_limit", "最高债权": "credit_limit",
}

FEE_KEYWORDS = [
    ("融资服务费", ["融资服务费", "服务费", "财务顾问费", "咨询费"]),
    ("担保费", ["担保费", "保证费"]),
    ("保险费", ["保险费"]),
    ("账户管理费", ["账户管理费", "管理费"]),
    ("手续费", ["手续费"]),
    ("承诺费", ["承诺费"]),
    ("评估费", ["评估费", "评级费"]),
    ("保管费", ["保管费", "托管费"]),
    ("认购费", ["认购费", "申购费", "赎回费"]),
]

REPAYMENT_MAP = [
    ("等额本息", ["等额本息"]),
    ("等额本金", ["等额本金"]),
    ("先息后本", ["先息后本", "按月付息", "按季付息.*到期.*还本", "按月结息.*到期"]),
    ("到期一次还本付息", ["到期一次还本付息", "到期一次性还本付息"]),
    ("按季付息、分期还本", ["按季付息.*分期还本", "分期还本"]),
]

GUARANTEE_MAP = [
    ("连带责任保证", ["连带责任保证", "连带清偿责任"]),
    ("一般保证", ["一般保证"]),
    ("抵押担保", ["抵押担保", "提供抵押"]),
    ("质押担保", ["质押担保", "提供质押"]),
]

COVENANT_PATTERNS = [
    (r"(资产负债率)[^。；;]{0,20}?(不得超过|不超过|不高于|不得高于)\s*([\d.]+)\s*%", "max", "<="),
    (r"(流动比率)[^。；;]{0,20}?(不得低于|不低于|不得少于)\s*([\d.]+)", "min", ">="),
    (r"(速动比率)[^。；;]{0,20}?(不得低于|不低于)\s*([\d.]+)", "min", ">="),
    (r"(净资产)[^。；;]{0,20}?(不得低于|不低于)\s*([\d.，,万元亿]+)", "min", ">="),
]

# 契约指标的计算公式（供前端与诊断展示，财务数据缺失时提示所需数据项）
# 口径铁律：本表的公式文本、required_data 必须与 engines/covenant.py 的
# compute_indicator / REQUIRED_FIELDS 完全一致，否则前端公示的口径与实际计算会打架。
COVENANT_FORMULAS = {
    "资产负债率": ("总负债 / 总资产 × 100", ["total_liabilities", "total_assets"], "%"),
    "流动比率": ("流动资产 / 流动负债", ["current_assets", "current_liabilities"], ""),
    # 速动比率只看速动资产（流动资产扣除存货、预付等），不能用流动资产代替；
    # 原先声明为「流动资产 / 流动负债（速动口径）」且 required_data 指向 current_assets，
    # 与 covenant.compute_indicator 的 quick_assets / current_liabilities 不一致，此处对齐实现。
    "速动比率": ("速动资产 / 流动负债", ["quick_assets", "current_liabilities"], ""),
    "净资产": ("总资产 - 总负债", ["total_assets", "total_liabilities"], "元"),
}

# 触发后果关键词（契约被突破后的救济/处置安排）
COVENANT_CONSEQUENCE_PAT = re.compile(
    r"(甲方|贷款人|出租人|债权人)[^。；;]{0,30}?(有权[^。；]{4,60}|要求[^。；]{4,40}|宣布[^。；]{4,40})")


def _f_covenants(doc):
    """财务契约条款：转换为机器可执行规则（指标/方向/阈值/频率/补救期/触发后果）"""
    out = []
    for s in doc["sections"]:
        blob = s["title"] + " " + s["body"]
        for pat, direction, operator in COVENANT_PATTERNS:
            m = re.search(pat, blob)
            if not m:
                continue
            indicator, thr = m.group(1), m.group(3)
            if indicator == "净资产":
                thr_val, _, _ = std.parse_amount_any(thr)
            else:
                thr_val = std._to_float(thr)
            if thr_val is None:
                continue
            freq = "季度"
            if re.search(r"每年度|年度", blob):
                freq = "年度"
            elif re.search(r"每月|月度", blob):
                freq = "月度"
            rm = re.search(r"(\d+)\s*日(?:内|的)?(?:补救|恢复)", blob)
            cm = COVENANT_CONSEQUENCE_PAT.search(blob)
            formula, req, unit = COVENANT_FORMULAS.get(indicator, ("", [], ""))
            out.append({
                "indicator": indicator, "direction": direction,
                "operator": operator, "threshold": thr_val,
                "frequency": freq,
                "remedy_days": int(rm.group(1)) if rm else None,
                "consequence": re.sub(r"\s+", " ", cm.group(0))[:80] if cm else "未明确约定触发后果",
                "formula": formula, "required_data": req, "unit": unit,
                "clause": s["title"],
                "page": s.get("page"),
                "source": re.sub(r"\s+", " ", m.group(0))[:80],
            })
            # 同一条款可含多项契约（如 资产负债率 + 流动比率），继续匹配其余模式
    seen, unique = set(), []
    for c in out:
        key = (c["indicator"], c["direction"], c["threshold"])
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


# ---------- 证据绑定 ----------

_SENTENCE_BOUNDARIES = "。！？!?；;"
_HEADING_LINE = re.compile(r"^\s*(?:第[一二三四五六七八九十百\d]+[条章]|[一二三四五六七八九十百]+、|\d+[、.．]|[（(][一二三四五六七八九十\d]+[）)]).*?")


def _paragraph_bounds(text, start, end):
    """返回包含区间的段落边界，不使用固定字符窗口。"""
    if not text:
        return 0, 0
    start = max(0, min(len(text), int(start)))
    end = max(start, min(len(text), int(end)))
    left_break = None
    for match in re.finditer(r"\n\s*\n", text[:start]):
        left_break = match
    left = left_break.end() if left_break else 0
    right_match = re.search(r"\n\s*\n", text[end:])
    right = end + right_match.start() if right_match else len(text)
    return left, right


def expand_evidence_span(text, start, end):
    """将命中的锚点扩展到完整句子/表格行/段落边界。

    证据长度由原文标点和段落结构决定，绝不依赖“前 N 个字符、后 N 个字符”。
    """
    if not text:
        return 0, 0
    start = max(0, min(len(text), int(start)))
    end = max(start, min(len(text), int(end)))
    para_left, para_right = _paragraph_bounds(text, start, end)
    local = text[para_left:para_right]
    rel_start, rel_end = start - para_left, end - para_left

    left_hits = [local.rfind(ch, 0, rel_start) for ch in _SENTENCE_BOUNDARIES]
    right_hits = [local.find(ch, rel_end) for ch in _SENTENCE_BOUNDARIES]
    left_punc = max(left_hits) if left_hits else -1
    right_puncs = [p for p in right_hits if p >= 0]
    right_punc = min(right_puncs) if right_puncs else -1

    # 有明确句号/分号时按完整句子取证；否则至少保留完整段落。
    left = (left_punc + 1) if left_punc >= 0 else 0
    right = (right_punc + 1) if right_punc >= 0 else len(local)

    # 结构化公告/合同的标题行不是事实证据。无标点时，从锚点之前最近的
    # 条款/编号标题之后开始，保留后续换行的正文（不把换行当作任意截断点）。
    heading_end = None
    for line in re.finditer(r"(?m)^([^\n]*)", local[:rel_start]):
        if line.end() < rel_start and _HEADING_LINE.match(line.group(1).strip()):
            heading_end = line.end()
    if heading_end is not None and heading_end > left:
        left = heading_end

    # 无标点的表格/清单按整行取证，避免把相邻行拼成一个业务事实。
    segment = local[left:right]
    if not any(ch in segment for ch in _SENTENCE_BOUNDARIES) and ("|" in local or "\t" in local):
        line_left = local.rfind("\n", 0, rel_start) + 1
        line_right = local.find("\n", rel_end)
        left, right = line_left, (line_right if line_right >= 0 else len(local))

    out_start, out_end = para_left + left, para_left + right
    while out_start < out_end and text[out_start].isspace():
        out_start += 1
    while out_end > out_start and text[out_end - 1].isspace():
        out_end -= 1
    return out_start, out_end


def _normalise_evidence(text):
    """只压缩版面空白，不改变证据语义或顺序。"""
    return re.sub(r"[ \t\r\f\v]+", " ", text or "").replace("\n", " ").strip()


def _locate_local(blob, raw):
    """在条款文本中定位锚点，返回原始坐标。"""
    if not blob or not raw:
        return None
    raw = str(raw)
    direct = blob.find(raw)
    if direct >= 0:
        return direct, direct + len(raw)
    # 规则抽取有时传入的是一个较长正则结果，先用稳定的业务词定位。
    key = raw[:80]
    direct = blob.find(key)
    if direct >= 0:
        return direct, direct + len(key)
    compact_blob = re.sub(r"\s+", "", blob)
    compact_raw = re.sub(r"\s+", "", raw)
    if not compact_raw:
        return None
    idx = compact_blob.find(compact_raw)
    if idx < 0:
        compact_raw = re.sub(r"\s+", "", key)
        idx = compact_blob.find(compact_raw)
    if idx < 0:
        return None
    positions = [i for i, ch in enumerate(blob) if not ch.isspace()]
    if idx >= len(positions):
        return None
    length = len(compact_raw)
    return positions[idx], positions[min(len(positions) - 1, idx + length - 1)] + 1


def _bind_hit(field, blob, hit, raw, clause_title, page):
    start, end = expand_evidence_span(blob, hit[0], hit[1])
    field["clause"] = clause_title
    field["page"] = page
    field["source_text"] = _normalise_evidence(blob[start:end])
    field["raw"] = raw

def _bind(field, doc, raw, clause_title=None):
    """绑定原文证据：按完整句子/段落边界定位，不使用固定字符窗口。"""
    if not raw:
        return
    for s in doc["sections"]:
        blob = s["title"] + "\n" + s["body"]
        hit = _locate_local(blob, raw)
        if hit:
            _bind_hit(field, blob, hit, raw, clause_title or s["title"], s.get("page"))
            return
    t = doc.get("clean_text", "")
    hit = _locate_local(t, raw)
    if hit:
        _bind_hit(field, t, hit, raw, clause_title, field.get("page"))
        return


def _sections_with(doc, *keywords):
    """返回正文包含任一关键词的条款列表"""
    out = []
    for s in doc["sections"]:
        blob = s["title"] + " " + s["body"]
        if any(k in blob for k in keywords):
            out.append(s)
    return out


def _sentences(blob):
    return [x.strip() for x in re.split(r"[。；;\n]", blob) if x.strip()]


def _compact(text):
    """用于角色匹配的无空白文本；证据仍绑定到原始条款。"""
    return re.sub(r"\s+", "", text or "")


def _role_amount(text, labels):
    """从指定业务角色后的局部窗口取金额，不跨角色借用相邻数字。"""
    compact = _compact(text)
    pat = r"(?:" + "|".join(labels) + r")(?:为|是|：|:)?.{0,90}"
    m = re.search(pat, compact)
    if not m:
        return None
    value, raw, conf = std.parse_amount_any(m.group(0))
    anchor = re.search(r"(?:" + "|".join(labels) + r")", m.group(0))
    return (value, raw, conf, anchor.group(0) if anchor else labels[0]) if value else None


TERM_ROLE_DEFS = [
    ("actual_financing_term", "实际融资期限"), ("actual_financing_term", "实际借款期限"),
    ("actual_financing_term", "实际贷款期限"), ("actual_financing_term", "实际提款期限"),
    ("leasing_term", "融资租赁期限"), ("leasing_term", "租赁期限"), ("leasing_term", "租期"),
    ("loan_term", "借款期限"), ("loan_term", "贷款期限"), ("loan_term", "借款期间"),
    ("loan_term", "贷款期间"), ("credit_term", "授信期限"), ("credit_term", "授信期间"),
    ("credit_term", "额度有效期"), ("credit_term", "循环额度有效期"),
    ("guarantee_term", "保证期间"), ("guarantee_term", "保证期限"), ("guarantee_term", "担保期限"),
    # 通用“融资期限”放在具体角色之后，供保理/回租等文件兜底。
    ("actual_financing_term", "融资期限"),
]


def _role_term(text, role_defs):
    return (_role_terms(text, role_defs) or [None])[0]


def _role_terms(text, role_defs):
    compact = _compact(text)
    out = []
    for role, label in role_defs:
        for m in re.finditer(re.escape(label), compact):
            segment = compact[m.start():m.start() + 100]
            value, raw, conf = std.parse_term_any(segment)
            if not raw:
                continue
            if re.search(r"不超过|最长|以内|至多", segment):
                value, conf, raw = None, 0.55, segment[:80]
            out.append((value, raw, conf, role, label))
    return out


# ---------- 通用字段抽取 ----------

def _f_contract_name(f, doc, hint):
    title = doc.get("title")
    if title:
        f.update(found=True, value=title, display=title, confidence=0.9)
        _bind(f, doc, title)
    elif hint and hint != "未命名合同":
        f.update(found=True, value=hint, display=hint, confidence=0.6, method="meta")
    else:
        f.update(display="无法确定（未在正文识别到标题行）")


def _f_contract_type(f, doc, type_info):
    f.update(found=True, value=type_info["key"], display=type_info["label"],
             confidence=type_info["confidence"], note=type_info.get("evidence"))


def _parse_party_label(label):
    label = label.strip()
    tag, role = None, label
    m = re.match(r"^([^（）()]{2,8})[（(]([^（）()]{2,8})[）)]$", label)
    if m:
        a, b = m.group(1).strip(), m.group(2).strip()
        if re.fullmatch(r"[甲乙丙丁][方]", a):
            tag, role = a, b
        else:
            role, tag = a, (b if re.fullmatch(r"[甲乙丙丁][方]", b) else None)
    else:
        mt = re.search(r"[甲乙丙丁][方]", label)
        if mt and mt.group(0) != label:
            tag = mt.group(0)
            role = label.replace(tag, "").strip("（）() ")
    return tag, role


def _f_parties(f, doc):
    items = []
    heads = doc["sections"][:4]
    scanned = 0
    for s in heads:
        for line in (s["body"] + "\n" + s["title"]).split("\n"):
            line = line.strip()
            if scanned > 14:
                break
            scanned += 1
            m = re.match(r"^([^：:]{2,18})[：:]\s*([^\n]{3,60})$", line)
            if not m:
                continue
            label, name = m.group(1).strip(), m.group(2).strip()
            if not any(k in label for k in list(ROLE_MAP.keys())):
                continue
            tag, role = _parse_party_label(label)
            if not role or len(name) < 3:
                continue
            name = re.sub(r"[（(]以下?简称.*?[）)]$", "", name).strip()
            if any(name == it["name"] for it in items):
                continue
            role_desc = ROLE_MAP.get(role, ROLE_MAP.get(tag or "", role))
            items.append({"label": label, "tag": tag, "name": name,
                          "role": role_desc, "raw_role": role})
        if len(items) >= 2 and scanned > 8:
            break
    if items:
        f["items"] = items
        f.update(found=True, value=[it["name"] for it in items],
                 display="；".join("%s：%s" % (it["label"], it["name"]) for it in items),
                 confidence=0.92 if len(items) >= 2 else 0.75)
        _bind(f, doc, items[0]["label"] + "：" + items[0]["name"])


def _f_roles(f, doc, parties_f):
    parts = (parties_f or {}).get("items") or []
    if parts:
        pairs = []
        for it in parts:
            if it.get("tag"):
                pairs.append("%s=%s（%s）" % (it["tag"], it["raw_role"], it["role"]))
            else:
                pairs.append("%s（%s）" % (it["raw_role"], it["role"]))
        f.update(found=True, value=pairs, display="；".join(pairs), confidence=0.85,
                 source_text=parties_f.get("source_text"), clause=parties_f.get("clause"),
                 page=parties_f.get("page"))
    else:
        f.update(display="未发现（未识别到签约主体行）")


def _f_amount(f, doc, type_key):
    # 保理公告同时披露授信额度、应收账款与本次实际融资金额。
    # 主金额必须绑定“实际/保理融资金额”，不能按全文首个金额或授信额度兜底。
    if type_key == "factoring":
        text = doc.get("clean_text", "")
        hit = _role_amount(text, ("实际融资金额", "实际保理融资款", "保理融资金额", "实际提款金额", "放款金额", "发放金额", "融资金额"))
        if hit:
            value, raw, conf, anchor = hit
            f.update(found=True, value=value, display=std.fmt_money(value), confidence=max(conf, 0.9),
                     role="actual_financing_amount")
            _bind(f, doc, anchor)
            return

    candidates = []  # (关键词优先级, -置信度, 出现顺序, role, value, raw, 句子)
    order = 0
    for s in _sections_with(doc, *AMOUNT_KEYWORDS):
        for sent in _sentences(s["title"] + " " + s["body"]):
            # 每句只取特异性最高的关键词，并在其邻近窗口内解析金额，
            # 避免把同句中无关数字（期限/利率/保证金）误认为主金额
            sent_match = _compact(sent) if type_key == "factoring" else sent
            for pr, kw in enumerate(AMOUNT_KEYWORDS):
                pos = sent_match.find(kw)
                if pos < 0:
                    continue
                role = AMOUNT_ROLE_BY_LABEL.get(kw)
                if type_key in ("guarantee", "mortgage", "pledge") and role == "credit_limit":
                    role = "guarantee_limit"
                # 授信/担保额度只进入对应专属字段，不作为融资本金。
                if type_key in ("factoring", "loan", "enterprise_loan", "personal_loan", "leasing", "credit") and (
                        role in ("credit_limit", "guarantee_limit") or kw in ("额度", "最高债权")):
                    order += 1
                    continue
                window = sent_match[max(0, pos - 15): pos + len(kw) + 45]
                v, raw, conf = std.parse_amount_any(window)
                if not v or conf <= 0:
                    v, raw, conf = std.parse_amount_any(sent_match)  # 窗口失败回退整句
                if v and conf > 0:
                    candidates.append((pr, -conf, order, role, v, raw, sent))
                break
            order += 1
    if not candidates:
        # 融资/授信文件如果只有额度、上限或担保口径，不能把它们降级成实际本金。
        if type_key in ("factoring", "loan", "enterprise_loan", "personal_loan", "leasing", "credit"):
            f.update(display="未发现（未明确实际融资金额/本金，授信额度不作为本金）")
            return
        # 兜底：全文前 40% 区域扫描
        for i, s in enumerate(doc["sections"][: max(3, len(doc["sections"]) // 3)]):
            v, raw, conf = std.parse_amount_any(s["title"] + " " + s["body"])
            if v and conf > 0:
                candidates.append((len(AMOUNT_KEYWORDS), -conf * 0.8, i, "", v, raw,
                                   s["title"] + " " + s["body"]))
    if not candidates:
        f.update(display="未发现（未识别到金额表述，建议人工核对）")
        return
    # 关键词特异性优先 → 置信度 → 出现顺序
    candidates.sort()
    _, _, _, role, v, raw, sent = candidates[0]
    f.update(found=True, value=v, display=std.fmt_money(v), confidence=0.92)
    if role:
        f["role"] = role
    _bind(f, doc, raw)


def _f_currency(f, doc):
    code, label = std.parse_currency(doc.get("clean_text", ""))
    if code:
        f.update(found=True, value=code, display="%s（%s）" % (label, code), confidence=0.95)
        idx = doc["clean_text"].find(label)
        _bind(f, doc, label)
    else:
        f.update(display="未发现（未明确约定币种）")


def _f_term(f, doc, type_key=None):
    # 保理文件常把“授信期限”和“单笔融资期限”并列披露。
    # 通用期限只接受实际融资期限；授信期限由专属字段单独承载。
    if type_key == "factoring":
        text = doc.get("clean_text", "")
        compact = _compact(text)
        m = re.search(r"融资期限(?:为|是|：|:)?([^。；\n]{0,70})", compact)
        if m:
            phrase = m.group(0)
            # “不超过/最长”不是确定的现金流期限，保留证据但标记待复核。
            if re.search(r"不超过|最长|以内|至多", phrase):
                f.update(found=True, value=None, display=phrase[:80], confidence=0.55,
                         role="actual_financing_term", llm_disabled=True)
                _bind(f, doc, "融资期限")
                return
            months, raw, conf = std.parse_term_any(phrase)
            if months:
                f.update(found=True, value=months, display="%d 个月" % months,
                         confidence=conf, role="actual_financing_term")
                _bind(f, doc, "融资期限")
                return
        f.update(display="未发现（未明确单笔实际融资期限）", role="actual_financing_term",
                 llm_disabled=True)
        return
    # 先按业务角色抽取期限，再回退到日期区间；授信期限、担保期限等不能
    # 因为在全文中先出现就覆盖实际融资期限。
    preferred = {
        "factoring": {"actual_financing_term": 0},
        "leasing": {"leasing_term": 0, "actual_financing_term": 1},
        "personal_loan": {"actual_financing_term": 0, "loan_term": 1},
        "enterprise_loan": {"actual_financing_term": 0, "loan_term": 1},
        "loan": {"actual_financing_term": 0, "loan_term": 1},
        "credit": {"credit_term": 0, "actual_financing_term": 1},
        "guarantee": {"guarantee_term": 0},
        "mortgage": {"guarantee_term": 0},
        "pledge": {"guarantee_term": 0},
    }.get(type_key, {})
    role_candidates = []
    for value, raw, conf, role, anchor in _role_terms(doc.get("clean_text", ""), TERM_ROLE_DEFS):
        role_candidates.append((preferred.get(role, 8), -conf, role, value, raw, anchor))
    if role_candidates:
        # 对贷款/租赁，只有实际融资、借款或租赁期限才可进入通用“期限”；
        # 授信期限/额度有效期必须留在授信专属字段，不能冒充现金流期限。
        eligible = role_candidates
        if preferred:
            eligible = [x for x in role_candidates if x[2] in preferred]
            if not eligible and type_key in ("loan", "enterprise_loan", "personal_loan", "leasing"):
                f.update(found=False, value=None,
                         display="未发现（仅识别到授信/额度有效期，未明确实际融资期限）",
                         confidence=0.0, role="actual_financing_term", llm_disabled=True)
                return
            if not eligible:
                eligible = role_candidates
        eligible.sort(key=lambda x: (x[0], x[1]))
        best_role = eligible[0][2]
        same_role = [x for x in eligible if x[2] == best_role]
        values = {x[3] for x in same_role if x[3] is not None}
        # 同一业务角色出现多个不同期限时保守置为 uncertain，除非正文明确是变更。
        if len(values) > 1:
            f.update(found=True, value=None, display="；".join(x[4] for x in same_role)[:100],
                     confidence=0.55, role=best_role)
            _bind(f, doc, same_role[0][5])
            return
        _, neg_conf, role, months, raw, anchor = same_role[0]
        f.update(found=True, value=months,
                 display=("%d 个月" % months) if months is not None else raw[:80],
                 confidence=round(-neg_conf, 2), role=role)
        _bind(f, doc, anchor)
        return

    best = (0, None, None)
    for s in _sections_with(doc, "期限", "起至", "止", "年期"):
        months, raw, conf = std.parse_term_any(s["title"] + " " + s["body"])
        if months and conf > best[0]:
            best = (conf, months, raw)
    if best[1] is None:
        months, raw, conf = std.parse_term_any(doc.get("clean_text", ""))
        if months:
            best = (conf * 0.85, months, raw)
    if best[1] is None:
        f.update(display="未发现（未识别到期限约定）")
        return
    conf, months, raw = best
    f.update(found=True, value=months, display="%d 个月" % months,
             confidence=round(conf, 2))
    _bind(f, doc, raw)


DATE_FIELD_DEFS = [
    ("sign_date", ["签订日期", "签署日期", "签字日期"]),
    ("effective_date", ["自.*?起", "生效", "起始日", "起息日"]),
    ("expiry_date", ["到期日", "止", "终止日", "届满"]),
]


def _f_dates(fields, doc):
    text = doc.get("clean_text", "")
    for key, kws in DATE_FIELD_DEFS:
        f = fields[key]
        found = None
        if key == "sign_date":
            date_pat = r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
            # 合同常用“签署日期：…”；公告披露则常用“于…签署/签订”。
            m = re.search(r"(?:签订|签署|签字)日期[：:]?\s*(%s)" % date_pat, text)
            if not m:
                m = re.search(r"(?:于|在)\s*(%s)\s*(?:签订|签署|签字)" % date_pat, text)
            if not m:
                m = re.search(r"(%s)\s*(?:签订|签署|签字)" % date_pat, text)
                if m and re.search(r"公告日期[：:]?\s*$", text[max(0, m.start() - 8):m.start()]):
                    m = None
            if m:
                d = std.parse_date_any(m.group(1))
                if d[0]:
                    found = (d[1], m.group(0), 0.9)
        elif key == "effective_date":
            m = re.search(r"自\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})\s*起", text)
            if not m:
                # 兜底：日期区间起点（"2026年1月10日至2028年1月9日"形式）
                m = re.search(r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})\s*(?:至|到|—|–)", text)
            if m:
                d = std.parse_date_any(m.group(1))
                if d[0]:
                    found = (d[1], m.group(0), 0.85)
        else:
            # 只接受明确“到期/期限届满/终止”的日期，不能把“截至财务报表日”
            # 或“保理基准日”误当合同到期日。
            m = re.search(r"(?:合同)?(?:到期日|到期日期|期限届满日?|终止日|届满日)[：:]?\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})", text)
            if not m:
                # 仅对完整“自日期至日期”的区间取终点，排除“截至日期”。
                m = re.search(r"(?:自|从)\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*起?\s*(?:至|到|—|-)\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)", text)
            if m:
                d = std.parse_date_any(m.group(1) if m.lastindex == 1 else m.group(0))
                if d[0]:
                    found = (d[1], m.group(0), 0.85)
        if found:
            f.update(found=True, value=found[0], display=found[0], confidence=found[2])
            _bind(f, doc, found[1])
        else:
            f.update(display="未发现" if key != "sign_date" else "未发现（正文未见签署日期）")
        if key == "expiry_date" and not found:
            # 到期日属于高风险角色，不交给 LLM 从“截至/基准日/报表日”猜测。
            f["llm_disabled"] = True


def _f_rate(f, doc):
    best = (0, None, None, None, None)
    rate_secs = _sections_with(doc, "利率", "费率", "万分之", "基点", "LPR", "收益率")
    pools = [s["title"] + " " + s["body"] for s in rate_secs] or [doc.get("clean_text", "")]
    for blob in pools:
        val, rtype, raw, conf, note = std.parse_rate_any(blob)
        if conf > best[0] and (val is not None or rtype == "lpr_float"):
            best = (conf, val, rtype, raw, note)
    if best[3] is None:
        val, rtype, raw, conf, note = std.parse_rate_any(doc.get("clean_text", ""))
        if raw:
            best = (conf * 0.9, val, rtype, raw, note)
    if best[3] is None:
        f.update(display="未发现（未识别到利率或费率约定）")
        return
    conf, val, rtype, raw, note = best
    f["rate_type"] = rtype
    if rtype == "annual":
        f.update(found=True, value=val, display="年化 %.2f%%" % (val * 100), confidence=conf)
    elif val is not None:
        f.update(found=True, value=val, display=raw.strip(), confidence=conf, note=note)
    else:
        f.update(found=True, value=None, display=raw.strip(), confidence=conf, note=note)
    _bind(f, doc, raw)


def _f_payment(f, doc, type_key):
    blob_secs = _sections_with(doc, "还款", "还款方式", "支付", "付息", "还本", "租金", "分期")
    text = " ".join(s["title"] + " " + s["body"] for s in blob_secs) or doc.get("clean_text", "")
    if type_key in ("loan", "personal_loan", "enterprise_loan", "leasing", "credit_card"):
        for name, kws in REPAYMENT_MAP:
            for kw in kws:
                m = re.search(kw, text)
                if m:
                    f.update(found=True, value=name, display=name, confidence=0.9)
                    _bind(f, doc, m.group(0))
                    return
        m = re.search(r"(按(?:月|季|年|日)[，,]?){1,2}(?:支付|偿还|结息|付息)[^。；]{0,24}", text)
        if m:
            f.update(found=True, value="周期性支付", display=m.group(0), confidence=0.7)
            _bind(f, doc, m.group(0))
            return
        f.update(display="未发现（原文未明确还款/现金流结构）", found=False,
                 value=None, confidence=0.0,
                 llm_disabled=(type_key == "factoring"))
        return
    m = re.search(r"(一次性(?:支付|缴清|支付)|分期(?:支付|缴纳)|到期(?:一次性)?(?:支付|兑付|兑结)|按(?:月|季|年)支付)", text)
    if m:
        f.update(found=True, value=m.group(1), display=m.group(1), confidence=0.8)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未识别到付款方式约定）")


def _f_guarantee(f, doc):
    found = []
    text = doc.get("clean_text", "")
    for name, kws in GUARANTEE_MAP:
        for kw in kws:
            m = re.search(kw, text)
            if m:
                found.append((name, m.group(0)))
                break
    if found:
        f.update(found=True, value=found[0][0],
                 display="、".join(x[0] for x in found), confidence=0.88)
        _bind(f, doc, found[0][1])
    else:
        f.update(display="未发现（未识别到担保安排，可能为信用方式）", confidence=0.0)


def _f_breach(f, doc):
    secs = _sections_with(doc, "违约", "罚息", "逾期")
    if not secs:
        f.update(display="未发现（未识别到违约责任条款）")
        return
    # 优先取标题含"违约"的条款（避免"保证范围"里列举的"违约金"抢绑）
    titled = [s for s in secs if "违约" in s["title"]]
    target = (titled or secs)[0]
    blob = target["title"] + " " + target["body"]
    penalties = re.findall(r"(?:上浮|加收|按)[^。；]{0,18}?([\d.]+)\s*%|万分之([\d.]+)", blob)
    ups = [p[0] for p in penalties if p[0]]
    wms = [p[1] for p in penalties if p[1]]
    desc = []
    if ups:
        desc.append("罚息上浮" + "/".join(ups) + "%")
    if wms:
        desc.append("日万分之" + "/".join(wms))
    m = re.search(r"(?:逾期|违约)[^。；]{10,80}", blob)
    evidence = m.group(0) if m else blob[:60]
    f.update(found=True, value={"penalty_ups": [float(x) for x in ups],
                                "daily_wm": [float(x) for x in wms]},
             display="；".join(desc) if desc else "已识别违约责任条款（详见原文）",
             confidence=0.85)
    _bind(f, doc, evidence, clause_title=target["title"])


def _f_early_term(f, doc, principal=None):
    secs = _sections_with(doc, "提前")
    text = doc.get("clean_text", "")
    m = re.search(r"(?:提前还款|提前结清|提前归还|提前终止|提前到期)[^。；;]{0,60}?([\d.]+)\s*%", text)
    if m:
        rate = float(m.group(1)) / 100.0
        f.update(found=True,
                 value={"rate": rate, "amount": (principal * rate) if principal else None},
                 display="提前还款违约金 %.2f%%" % (rate * 100), confidence=0.9)
        _bind(f, doc, m.group(0))
        return
    if secs:
        s = secs[0]
        m2 = re.search(r"(?:提前还款|提前结清|提前终止)[^。；]{0,70}", s["title"] + " " + s["body"])
        f.update(found=True, value=None, display="已约定提前终止机制（未见明确违约金率）",
                 confidence=0.6)
        _bind(f, doc, m2.group(0) if m2 else s["title"], clause_title=s["title"])
        return
    f.update(display="未发现（未识别到提前终止条款）")


def _f_fee_bearing(f, doc, principal):
    fees = []
    for fee_name, kws in FEE_KEYWORDS:
        for kw in kws:
            hit = None
            for s in doc["sections"]:
                blob = s["title"] + " " + s["body"]
                if kw not in blob:
                    continue
                # 句级作用域：费率/金额必须与费用关键词同句，避免被其他费用的费率污染
                for m in re.finditer(re.escape(kw), blob):
                    sent = _sentence_around(blob, m.start(), m.end())
                    mrate = re.search(r"([\d.]+)\s*%", sent)
                    rate = std._to_float(mrate.group(1)) / 100.0 if mrate else None
                    if rate is not None and not (0 < rate <= 1):
                        continue
                    mamt = re.search(r"(?:计|合计|共计|为|支付)\s*(?:一次性)?\s*(?:人民币)?\s*([\d,，,]+(?:\.\d+)?)\s*(万|亿)?\s*元", sent)
                    amount = std._amount_to_yuan(mamt.group(1), mamt.group(2) or "元") if mamt else None
                    if amount is None:
                        # 兼容中文大写/括号金额（如"计人民币柒万伍仟元整（¥75,000）"）
                        v2, _, _ = std.parse_amount_any(sent)
                        if v2 is not None and (not principal or v2 < principal * 0.9):
                            amount = v2
                    if amount is None and principal and rate:
                        amount = principal * rate
                    if amount is None:
                        continue
                    if rate is None and principal:
                        rate = amount / principal
                    timing = "一次性支付" if re.search(r"一次性|三个工作日内|放款前", sent) else "按约定周期支付"
                    hit = {"name": kw, "rate": rate, "amount": amount,
                           "timing": timing, "clause": s["title"],
                           "source": std.parse_amount_any(sent)[1] or ""}
                    break
                if hit:
                    break
            if hit:
                if not any(abs((x.get("rate") or 0) - (hit.get("rate") or 0)) < 1e-9 and x["name"] == kw for x in fees):
                    fees.append(hit)
                break
    if fees:
        f.update(found=True, value=fees,
                 display="；".join("%s %s（%s）" % (x["name"], ("%.2f%%" % (x["rate"] * 100)) if x.get("rate") is not None else "固定金额", std.fmt_money(x["amount"])) for x in fees),
                 confidence=0.9, items=fees)
        _bind(f, doc, fees[0]["clause"])
    else:
        f.update(display="未发现（未识别到独立费用条款）")


def _sentence_around(blob, start, end):
    """返回包含 [start, end) 区间的句子（以 。；\n 切分）"""
    s0 = max(blob.rfind("。", 0, start), blob.rfind("；", 0, start), blob.rfind("\n", 0, start)) + 1
    ends = [x for x in (blob.find("。", end), blob.find("；", end), blob.find("\n", end)) if x >= 0]
    s1 = min(ends) if ends else len(blob)
    return blob[s0:s1]


def _f_info_auth(f, doc):
    secs = _sections_with(doc, "信息授权", "征信", "授权", "查询", "共享", "收集")
    for s in secs:
        blob = s["title"] + " " + s["body"]
        # 先确认确实是信息/数据授权，再寻找“保存/留存/合同终止后”等
        # 明确保留语义；禁止把段落中的日期（如 2025 年）当成期限。
        if not re.search(r"(信息|数据|征信|个人资料|客户资料)", blob) or not re.search(r"(同意|授权|查询|收集|共享)", blob):
            continue
        sentences = [x.strip() for x in re.split(r"[。；;\n]", blob) if x.strip()]
        auth_sentence = next((x for x in sentences if re.search(r"(同意|授权|查询|收集|共享)", x)
                              and re.search(r"(信息|数据|征信|个人资料|客户资料)", x)), None)
        if not auth_sentence:
            continue
        retention_years = None
        retention_sentence = None
        for sentence in sentences:
            # 期限必须与保存/留存/保留或合同终止后语义同句相连。
            m_year = re.search(r"(?:合同终止后|终止后|保存|留存|保留|保存期限|留存期限|授权期限)[^\d。；;]{0,40}(\d{1,3})\s*年", sentence)
            if not m_year:
                continue
            candidate = int(m_year.group(1))
            # 1900-2100 等日期年份、以及明显不合理的超长期限不属于持续年数。
            if candidate <= 0 or candidate > 120 or 1900 <= candidate <= 2100:
                continue
            retention_years = candidate
            retention_sentence = sentence
            break
        evidence = retention_sentence or auth_sentence
        note = ("期限：合同终止后 %s 年" % retention_years) if retention_years is not None else "未发现明确的信息保留期限"
        f.update(found=True, value={"scope": evidence, "retention_years": retention_years},
                 display=_normalise_evidence(evidence)[:120], confidence=0.85, note=note)
        _bind(f, doc, evidence, clause_title=s["title"])
        return
    f.update(display="未发现（未识别到信息授权条款）")


def _f_dispute(f, doc):
    text = doc.get("clean_text", "")
    # 优先识别明确的诉讼/仲裁约定；仅约定协商时才取协商
    m = re.search(r"(向[^。；]{1,30}人民法院提起诉讼|提交[^。；]{1,30}仲裁委员会仲裁|诉讼方式解决)", text)
    if not m:
        m = re.search(r"(协商解决)", text)
    if m:
        method = "仲裁" if "仲裁" in m.group(1) else "协商解决" if "协商" in m.group(1) else "诉讼"
        disp = method if m.group(1).strip() == method else method + "（" + m.group(1)[:30] + "）"
        f.update(found=True, value=method, display=disp, confidence=0.85)
        _bind(f, doc, m.group(1))
    else:
        f.update(display="未发现（未识别到争议解决条款）")


def _f_jurisdiction(f, doc):
    text = doc.get("clean_text", "")
    # 优先取"向/提交 + 法院/仲裁机构"的干净表述，避免从前半句截断
    m = re.search(r"(?:向|提交)\s*((?:[^\s。；，,、]{1,20}?)(?:人民法院|仲裁委员会))", text)
    if not m:
        m = re.search(r"((?:合同签订地|被告住所地|甲方住所地|贷款人|甲方)所在[^。；]{0,10}法院|[^。；]{2,20}(?:人民法院|仲裁委员会))", text)
    if m:
        val = re.sub(r"\s+", "", m.group(1) if m.lastindex else m.group(0))
        f.update(found=True, value=val, display=val[:40], confidence=0.8)
        _bind(f, doc, val)
    else:
        f.update(display="未发现（未识别到管辖约定）")


def _f_amendment(f, doc):
    secs = _sections_with(doc, "变更", "补充协议", "修改")
    if secs:
        s = secs[0]
        f.update(found=True, value="需书面协商变更",
                 display="已识别变更条款（%s）" % s["title"][:20], confidence=0.7)
        _bind(f, doc, s["title"])
    else:
        f.update(display="未发现（未识别到变更条款）")


def _f_notice(f, doc):
    secs = _sections_with(doc, "通知", "送达")
    if secs:
        s = secs[0]
        m = re.search(r"(书面|邮件|传真|专人)[^。；]{0,40}(?:通知|送达)", s["title"] + " " + s["body"])
        f.update(found=True, value="书面通知为主" if m else "已约定通知条款",
                 display=(m.group(0) if m else s["title"])[:40], confidence=0.7)
        _bind(f, doc, m.group(0) if m else s["title"])
    else:
        f.update(display="未发现（未识别到通知送达条款）")


def _f_disclaimer(f, doc):
    secs = _sections_with(doc, "免责", "不可抗力")
    if secs:
        s = secs[0]
        f.update(found=True, value="存在免责/不可抗力条款",
                 display="已识别免责条款（%s）" % s["title"][:20], confidence=0.7)
        _bind(f, doc, s["title"])
    else:
        f.update(display="未发现（未识别到免责条款）")


def _f_attachments(f, doc):
    text = doc.get("clean_text", "")
    hits = []
    for m in re.finditer(r"(附件[一二三四五六七八九十\d]*|补充协议|附录[一二三四五六七八九十\d]*)", text):
        if m.group(1) not in hits:
            hits.append(m.group(1))
    if hits:
        f.update(found=True, value=hits, display="、".join(dict.fromkeys(hits)), confidence=0.75)
        _bind(f, doc, hits[0])
    else:
        f.update(display="未发现（未识别到附件或补充协议）")


# ---------- 类型专属字段抽取 ----------


def _t_document_type(f, doc):
    title = doc.get("title") or ""
    text = doc.get("clean_text", "")
    head = "\n".join(text.splitlines()[:20])
    if "补充协议" in title or ("补充协议" in head and "合同" in head):
        category = "补充协议"
    elif re.search(r"公告|信息披露|临时报告|公告编号|证券代码", title + "\n" + head):
        category = "上市公司公告/信息披露文件"
    elif re.search(r"合同|协议|确认书|承诺函", title):
        category = "原始合同"
    else:
        category = "其他金融文件"
    f.update(found=True, value=category, display=category, confidence=0.92,
             note=("原始标题：" + title) if title else None)
    if title:
        _bind(f, doc, title)


def _t_document_title(f, doc):
    title = doc.get("title") or ""
    if title:
        f.update(found=True, value=title, display=title, confidence=0.9)
        _bind(f, doc, title)
    else:
        f.update(display="未发现（未识别到文档标题）")


def _t_transaction_type(f, doc):
    type_key = doc.get("type_key")
    labels = {
        "personal_loan": "贷款", "enterprise_loan": "贷款", "loan": "贷款",
        "credit": "授信", "credit_card": "贷款", "factoring": "保理",
        "leasing": "融资租赁", "guarantee": "担保", "mortgage": "担保",
        "pledge": "担保", "wealth": "理财", "securities": "证券/衍生品",
        "insurance": "保险", "general": "其他",
    }
    value = labels.get(type_key, "其他")
    text = doc.get("clean_text", "")
    if value == "融资租赁" and "担保" in text:
        value = "融资租赁 + 担保"
    f.update(found=True, value=value, display=value, confidence=0.88,
             note="交易类型依据合同类型关键词；文档类别见文档类型")
    _bind(f, doc, value.split(" + ")[0])


def _t_financing_business(f, doc):
    text = doc.get("clean_text", "")
    factoring = re.search(r"国内保理|国际保理|保理融资|保理业务|保理", text)
    recourse = re.search(r"附(?:有|带)?追索权|有追索权|完全、?无条件追索权|无条件追索权", text)
    if recourse and factoring:
        kind = "国内保理" if "国内保理" in text else "保理"
        value = "附追索权%s融资" % kind
        m = re.search(r"(?:附(?:有|带)?追索权|有追索权|完全、?无条件追索权|无条件追索权)[^。；\n]{0,40}(?:国内保理|国际保理|保理融资|保理业务|保理)|(?:国内保理|国际保理|保理融资|保理业务|保理)[^。；\n]{0,40}(?:附(?:有|带)?追索权|有追索权|完全、?无条件追索权|无条件追索权)", text)
        f.update(found=True, value=value, display=value, confidence=0.95)
        _bind(f, doc, m.group(0) if m else value)
    elif factoring:
        kind = "国内保理" if "国内保理" in text else "保理"
        f.update(found=True, value="%s融资" % kind, display="%s融资" % kind, confidence=0.8)
        _bind(f, doc, factoring.group(0))
    else:
        f.update(display="未发现（未明确金融业务类型）")


def _entity_aliases(text):
    """读取“全称（以下简称“简称”）”定义，不依赖任何具体公司名称。"""
    pat = re.compile(
        r"(?P<name>[^，。；\n]{2,100}?(?:有限责任公司|股份有限公司|集团股份公司|集团有限公司|有限公司|"
        r"商业银行|银行|分行|金融租赁|融资租赁|财务公司|证券公司|证券股份公司|信托公司|保险公司|"
        r"基金管理公司|基金公司))\s*[（(]\s*以下简称\s*[“\"']?(?P<alias>[^”\"'）)]+)"
        r"(?:[”\"']\s*[）)]|[）)])"
    )
    out = []
    for m in pat.finditer(text or ""):
        name = re.sub(r"^(?:分别是|公司全资子公司|全资子公司|本公司|公司|与|和|及|、)\s*", "", m.group("name")).strip()
        # 前一实体的地址/括号可能紧挨“和/与”连接词；不要把地址尾部拼进下一实体全称。
        name = re.split(r"[）)](?:和|与|及|、)\s*", name)[-1].strip()
        alias = m.group("alias").strip()
        if name and alias and not any(x["name"] == name and x["alias"] == alias for x in out):
            out.append({"name": name, "alias": alias, "match": m})
    return out


def _entity_score(text, alias, keywords):
    score = 0
    for hit in re.finditer(re.escape(alias), text or ""):
        window = text[max(0, hit.start() - 100):hit.end() + 220]
        score += sum(1 for kw in keywords if kw in window)
        if re.search(r"(?:与|向|由)\s*" + re.escape(alias), window):
            score += 2
    return score


def _clean_entity_value(value):
    value = re.sub(r"^[与和及、\s]+", "", value or "")
    value = re.sub(r"[（(]\s*以下简称.*?[）)]", "", value).strip(" ，,：:")
    return value


def _t_financing_party(f, doc):
    text = doc.get("clean_text", "")
    # 合同正文若直接标注“借款人/承租人/融资方：”，优先采用该业务角色。
    direct = re.search(r"(?:融资方|融资主体|借款人|承租人|债务人|申请人)\s*[：:]\s*([^\n，。；]{2,100})", text)
    if direct:
        value = _clean_entity_value(direct.group(1))
        if value:
            f.update(found=True, value=value, display=value, confidence=0.95)
            _bind(f, doc, direct.group(0))
            return
    aliases = _entity_aliases(text)
    candidates = [x for x in aliases if not re.search(r"银行|租赁|金融|财务|证券|信托|保险|基金|保理商", x["name"])]
    if candidates:
        scored = sorted(candidates, key=lambda x: (_entity_score(text, x["alias"],
                           ("融资", "保理", "借款", "贷款", "授信", "租赁", "承租", "债务")),
                           -x["match"].start()), reverse=True)
        best = scored[0]
        # 没有业务上下文时保留候选但标成待复核，避免把公告主体误当融资方。
        score = _entity_score(text, best["alias"], ("融资", "保理", "借款", "贷款", "授信", "租赁", "承租", "债务"))
        value = _clean_entity_value(best["name"])
        f.update(found=True, value=value, display=value, confidence=0.95 if score >= 2 else 0.68)
        _bind(f, doc, best["match"].group(0))
        return
    f.update(display="未发现（未明确融资方全称）")


def _t_factoring_bank(f, doc):
    text = doc.get("clean_text", "")
    # 先处理明确的“保理银行：全称”行。
    direct = re.search(r"保理银行\s*[：:]\s*([^\n，。；]{2,100})", text)
    if direct:
        value = _clean_entity_value(direct.group(1))
        if value:
            f.update(found=True, value=value, display=value, confidence=0.95)
            _bind(f, doc, direct.group(0))
            return
    aliases = _entity_aliases(text)
    candidates = [x for x in aliases if re.search(r"银行|金融|财务|保理商|租赁", x["name"]) or
                  re.search(r"保理银行|保理商", x["alias"])]
    if candidates:
        scored = sorted(candidates, key=lambda x: (_entity_score(text, x["alias"],
                           ("保理", "应收账款", "融资", "签署", "放款")), -x["match"].start()), reverse=True)
        best = scored[0]
        value = _clean_entity_value(best["name"])
        f.update(found=True, value=value, display=value, confidence=0.95)
        _bind(f, doc, best["match"].group(0))
        return
    # 未使用简称时，从“与/向 + 金融机构 + 签署/开展保理”句式中提取机构。
    m = re.search(r"(?:与|向)\s*([^，。；\n]{2,80}?(?:银行|金融机构|财务公司|保理商))(?:签署|开展|办理)[^。；\n]{0,30}(?:保理|应收账款)", text)
    if m:
        value = _clean_entity_value(m.group(1))
        f.update(found=True, value=value, display=value, confidence=0.82)
        _bind(f, doc, m.group(0))
        return
    f.update(display="未发现（未明确保理银行全称）")


def _t_credit_term(f, doc):
    text = _compact(doc.get("clean_text", ""))
    m = re.search(r"(?:授信期限|授信期间)(?:为|是|：|:)?[^。；]{0,30}", text)
    if m:
        months, raw, conf = std.parse_term_any(m.group(0))
        if months:
            f.update(found=True, value=months, display="%d 个月" % months,
                     confidence=max(conf, 0.9), role="credit_term")
            _bind(f, doc, raw or m.group(0))
            return
    f.update(display="未发现（未明确授信期限）", role="credit_term")


def _t_actual_financing_amount(f, doc):
    hit = _role_amount(doc.get("clean_text", ""), ("实际融资金额", "实际保理融资款", "保理融资金额", "实际提款金额", "放款金额", "发放金额", "融资金额"))
    if hit:
        value, raw, conf, anchor = hit
        f.update(found=True, value=value, display=std.fmt_money(value), confidence=max(conf, 0.9),
                 role="actual_financing_amount")
        _bind(f, doc, anchor)
    else:
        f.update(display="未发现（未明确实际融资金额）", role="actual_financing_amount")


def _t_single_financing_term(f, doc):
    text = _compact(doc.get("clean_text", ""))
    m = re.search(r"融资期限(?:为|是|：|:)?[^。；]{0,70}", text)
    if not m:
        f.update(display="未发现（未明确单笔融资期限）", role="actual_financing_term", llm_disabled=True)
        return
    phrase = m.group(0)
    # 上限/最长期限不是本次现金流的唯一到期日。
    exact = not re.search(r"不超过|最长|以内|至多", phrase)
    f.update(found=True, value=phrase if exact else {"text": phrase, "exact": False},
             display=phrase[:80], confidence=0.82 if exact else 0.55,
             role="actual_financing_term", llm_disabled=not exact)
    _bind(f, doc, "融资期限")


def _t_rate_mechanism(f, doc):
    text = _compact(doc.get("clean_text", ""))
    m = re.search(r"(?:\d+年期)?LPR\s*(?:加|减|上浮|下浮)\s*[\d.]+\s*(?:个?基点|BP|bp|%)", text)
    if m:
        value = re.sub(r"\s+", " ", m.group(0))
        f.update(found=True, value=value, display=value, confidence=0.9)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未明确利率机制）")


def _t_prepayment(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"(?:可以|允许|可)提前还款", text)
    if m:
        f.update(found=True, value=True, display="允许", confidence=0.95)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未明确提前还款安排）")


def _t_baseline_date(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})\s*[（(]?[^。；\n]{0,16}保理基准日", text)
    if m:
        d = std.parse_date_any(m.group(1))
        if d[1]:
            f.update(found=True, value=d[1], display=d[1], confidence=0.95)
            _bind(f, doc, m.group(0))
            return
    f.update(display="未发现（未明确保理基准日）")


def _t_financial_statement_date(f, doc):
    text = doc.get("clean_text", "")
    date_pat = r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
    for m in re.finditer(r"截至\s*(%s)" % date_pat, text):
        segment = text[m.start():m.start() + 100]
        if re.search(r"财务报表|资产负债率", segment):
            d = std.parse_date_any(m.group(1))
            if d[1]:
                f.update(found=True, value=d[1], display=d[1], confidence=0.92)
                _bind(f, doc, m.group(0))
                return
    f.update(display="未发现（未明确财务报表基准日）")


def _type_extractors():
    return {
        "credit_limit": _t_credit_limit, "repayment_plan": _t_repayment_plan,
        "loan_purpose": _t_loan_purpose, "revolving_terms": _t_revolving_terms,
        "commitment_fee": _t_commitment_fee,
        "collateral": _t_collateral, "guarantee_scope": _t_guarantee_scope,
        "guarantee_period": _t_guarantee_period, "leased_asset": _t_leased_asset,
        "collateral_title": _t_collateral_title, "registration": _t_registration,
        "priority": _t_priority, "maximum_amount": _t_maximum_amount,
        "disposal_conditions": _t_disposal_conditions,
        "rent_plan": _t_rent_plan, "purchase_option": _t_purchase_option,
        "deposit": _t_deposit,
        "receivables": _t_receivables, "recourse": _t_recourse,
        "advance_ratio": _t_advance_ratio,
        "yield_rule": _t_yield_rule, "risk_level": _t_risk_level, "subscription": _t_subscription,
        "margin": _t_margin, "valuation": _t_valuation, "netting": _t_netting,
        "early_term_events": _t_early_term_events,
        "trade_type": _t_trade_type, "default_events": _t_default_events,
        "collateral_arr": _t_collateral_arr,
        "insured_amount": _t_insured_amount, "premium": _t_premium, "exclusions": _t_exclusions,
        "interest_free_period": _t_interest_free,
        "document_type": _t_document_type, "document_title": _t_document_title,
        "transaction_type": _t_transaction_type, "financing_business": _t_financing_business,
        "financing_party": _t_financing_party, "factoring_bank": _t_factoring_bank,
        "credit_term": _t_credit_term, "actual_financing_amount": _t_actual_financing_amount,
        "single_financing_term": _t_single_financing_term, "rate_mechanism": _t_rate_mechanism,
        "prepayment": _t_prepayment, "baseline_date": _t_baseline_date,
        "financial_statement_date": _t_financial_statement_date,
    }


def _t_credit_limit(f, doc):
    text = doc.get("clean_text", "")
    hit = _role_amount(text, ("授信额度", "最高授信额度", "授信上限", "批准额度", "最高综合授信", "授信总额"))
    if hit:
        value, raw, conf, anchor = hit
        f.update(found=True, value=value, display=std.fmt_money(value), confidence=max(conf, 0.9),
                 role="credit_limit")
        _bind(f, doc, anchor)
    else:
        f.update(display="未发现（未约定授信额度，可能为单笔借款合同）", role="credit_limit")


def _t_repayment_plan(f, doc):
    secs = _sections_with(doc, "还款", "还本", "分期", "摊还")
    if secs:
        s = secs[0]
        m = re.search(r"(每(?:月|季度|年)[^。；]{0,40}(?:归还|偿还|还本)[^。；]{0,20}|分期[^。；]{0,40}|按(?:月|季)等额[^。；]{0,20})", s["title"] + " " + s["body"])
        f.update(found=True, value=m.group(0) if m else s["title"],
                 display=(m.group(0) if m else s["body"][:40])[:50], confidence=0.8)
        _bind(f, doc, m.group(0) if m else s["title"], clause_title=s["title"])
    else:
        f.update(display="未发现（未约定分期还款计划）")


def _t_collateral(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"(?:抵押物|质押物|担保物|租赁物抵押|抵质押物)[为是][^。；，,]{2,40}", text)
    if m:
        f.update(found=True, value=re.sub(r"^[为是]", "", m.group(0)).strip(),
                 display=m.group(0)[:50], confidence=0.85)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未列明具体担保物）")


def _simple_special(f, doc, patterns, missing):
    text = doc.get("clean_text", "")
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            val = re.sub(r"\s+", " ", m.group(0))[:100]
            f.update(found=True, value=val, display=val, confidence=0.82)
            _bind(f, doc, m.group(0))
            return
    f.update(display=missing)


def _t_collateral_title(f, doc):
    _simple_special(f, doc, [r"(?:抵押物|质押物|质押权利)[^。；]{0,80}(?:所有权|权属|合法拥有|权利人)[^。；]{0,50}"], "未发现（未明确权属）")


def _t_registration(f, doc):
    _simple_special(f, doc, [r"(?:抵押|质押)(?:登记|备案|交付)[^。；]{0,90}"], "未发现（未明确登记或交付安排）")


def _t_priority(f, doc):
    _simple_special(f, doc, [r"(?:第一顺位|第二顺位|优先顺位|顺位)[^。；]{0,60}"], "未发现（未明确担保顺位）")


def _t_maximum_amount(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"(?:最高额|最高债权额|最高担保额)[^。；]{0,60}", text)
    if m:
        value, raw, conf = std.parse_amount_any(m.group(0))
        f.update(found=True, value=value, display=std.fmt_money(value) if value else m.group(0)[:80], confidence=max(conf, 0.78))
        _bind(f, doc, raw or m.group(0))
    else:
        f.update(display="未发现（未明确最高额）")


def _t_disposal_conditions(f, doc):
    _simple_special(f, doc, [r"(?:折价|拍卖|变卖|处置|实现抵押权|实现质权)[^。；]{0,100}"], "未发现（未明确担保物处置条件）")


def _t_guarantee_scope(f, doc):
    # 优先在标题含"保证范围/担保范围"的条款内匹配；其次"主债权"条款；最后全文
    pat = r"(?:担保范围|保证范围|担保的主债权|被担保的主债权)\s*[为是：:]\s*[^。]{4,90}"
    for title_keys in (("保证范围", "担保范围"), ("主债权",)):
        for s in doc["sections"]:
            if any(k in s["title"] for k in title_keys):
                m = re.search(pat, s["title"] + "\n" + s["body"])
                if m:
                    val = re.sub(r"\s+", " ", m.group(0))
                    f.update(found=True, value=val[:80], display=val[:80], confidence=0.88)
                    _bind(f, doc, m.group(0), clause_title=s["title"])
                    return
    m = re.search(pat, doc.get("clean_text", ""))
    if m:
        val = re.sub(r"\s+", " ", m.group(0))
        f.update(found=True, value=val[4:80], display=val[:80], confidence=0.85)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未明确担保范围）")


def _t_guarantee_period(f, doc):
    text = doc.get("clean_text", "")
    # 要求"保证期间 + 为/是/："，避免命中标题后把正文整段吞进来
    m = re.search(r"保证期间\s*[为是：:]\s*[^。；]{0,60}", text)
    if not m:
        m = re.search(r"保证期间[^。；]{0,60}", text)
    if m:
        val = re.sub(r"\s+", " ", m.group(0))
        f.update(found=True, value=val, display=val[:50], confidence=0.85)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未约定保证期间）")


def _t_leased_asset(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"租赁物[为是][^。；]{2,50}", text)
    if m:
        f.update(found=True, value=m.group(0)[4:], display=m.group(0)[:50], confidence=0.88)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未列明租赁物）")


def _t_rent_plan(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"租金[^。；]{0,60}", text)
    if m:
        f.update(found=True, value=m.group(0), display=m.group(0)[:60], confidence=0.8)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未约定租金安排）")


def _t_receivables(f, doc):
    text = doc.get("clean_text", "")
    best_v, best_seg = None, None
    for m in re.finditer(r"应收账款[^。；]{0,80}", text):
        seg = m.group(0)
        v, raw, conf = std.parse_amount_any(seg)
        if v:
            best_v, best_seg = v, seg
            break  # 优先取带金额的表述（发票金额/转让金额）
        if best_seg is None and ("转让" in seg or "发票" in seg or "到期" in seg):
            best_seg = seg  # 次选：实质条款表述，而非抬头中的主体名称
    if best_seg:
        raw = None
        if best_v is None:
            best_v, raw, _conf = std.parse_amount_any(best_seg)
        f.update(found=True, value=best_v,
                 display=(std.fmt_money(best_v) + "（应收账款）") if best_v else best_seg[:60],
                 confidence=0.8)
        _bind(f, doc, raw or best_seg[:24])
    else:
        f.update(display="未发现（未描述应收账款）")


def _t_recourse(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"无追索权|不承担追索", text)
    if m:
        f.update(found=True, value="无追索权", display="无追索权（买断式保理）", confidence=0.9)
        _bind(f, doc, m.group(0))
    else:
        m = re.search(r"有追索权|保留追索权|追索权", text)
        if m:
            f.update(found=True, value="有追索权", display="有追索权（回购型保理）", confidence=0.85)
            _bind(f, doc, m.group(0))
            return
        f.update(display="未发现（未约定追索权安排）")


def _t_yield_rule(f, doc):
    text = doc.get("clean_text", "")
    # 要求"业绩比较基准 + 为/是/："，避免从标题起把正文整段吞进来
    m = re.search(r"(?:业绩比较基准|预期年化收益率|业绩报酬计提基准|参考收益率)\s*[为是：:]\s*[^。；]{2,40}", text)
    if not m:
        m = re.search(r"(?:业绩比较基准|预期年化收益率|业绩报酬计提基准|参考收益率)[^。；]{2,40}", text)
    if m:
        val, rtype, raw, conf, note = std.parse_rate_any(m.group(0))
        disp = re.sub(r"\s+", " ", m.group(0))
        f.update(found=True, value=val, display=disp[:60], confidence=0.85, note=note)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未约定业绩基准或收益规则）")


def _t_risk_level(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"风险等级[为是：:]?\s*([Rr]\d|低风险|中低风险|中等风险|中高风险|高风险|一级|二级|三级|四级|五级)", text)
    if m:
        f.update(found=True, value=m.group(1), display="风险等级 " + m.group(1), confidence=0.9)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未标注风险等级）")


def _t_subscription(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"(?:认购|申购|赎回)[^。；]{0,50}", text)
    v, raw, conf = std.parse_amount_any(m.group(0)) if m else (None, None, 0)
    if m:
        f.update(found=True, value=v, display=m.group(0)[:60], confidence=0.75)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未约定认购/赎回安排）")


def _t_margin(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"保证金[^。；]{0,50}", text)
    if m:
        v, raw, conf = std.parse_amount_any(m.group(0))
        pct_m = re.search(r"([\d.]+)\s*%", m.group(0))
        f.update(found=True, value=v or (float(pct_m.group(1)) / 100 if pct_m else None),
                 display=m.group(0)[:60], confidence=0.8)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未约定保证金）")


def _t_valuation(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"(?:估值|盯市|市值重估)[^。；]{0,60}", text)
    if m:
        f.update(found=True, value=m.group(0), display=m.group(0)[:60], confidence=0.75)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未约定估值安排）")


def _t_netting(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"(?:净额结算|差额结算)[^。；]{0,40}", text)
    if m:
        f.update(found=True, value=m.group(0), display=m.group(0)[:50], confidence=0.85)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未约定净额结算）")


def _t_early_term_events(f, doc):
    secs = _sections_with(doc, "提前终止")
    if secs:
        s = secs[0]
        f.update(found=True, value=s["body"][:200], display=s["title"][:50], confidence=0.75)
        _bind(f, doc, s["title"], clause_title=s["title"])
    else:
        f.update(display="未发现（未列举提前终止事件）")


def _t_insured_amount(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"(?:保险金额|保额)[为是：:]?\s*[^。；]{2,50}", text)
    if m:
        v, raw, conf = std.parse_amount_any(m.group(0))
        f.update(found=True, value=v, display=std.fmt_money(v) if v else m.group(0)[:40],
                 confidence=0.85)
        _bind(f, doc, raw or m.group(0)[:20])
    else:
        f.update(display="未发现（未约定保险金额）")


def _t_premium(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"(?:保险费|保费)[为是：:]?\s*[^。；]{2,50}", text)
    if m:
        v, raw, conf = std.parse_amount_any(m.group(0))
        f.update(found=True, value=v, display=std.fmt_money(v) if v else m.group(0)[:40],
                 confidence=0.85)
        _bind(f, doc, raw or m.group(0)[:20])
    else:
        f.update(display="未发现（未约定保费）")


def _t_exclusions(f, doc):
    secs = _sections_with(doc, "除外责任", "责任免除")
    if secs:
        s = secs[0]
        f.update(found=True, value=s["body"][:200], display=s["title"][:50], confidence=0.8)
        _bind(f, doc, s["title"], clause_title=s["title"])
    else:
        f.update(display="未发现（未约定除外责任）")


def _t_interest_free(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"免息(?:还款)?期[^。；]{0,30}", text)
    if m:
        f.update(found=True, value=m.group(0), display=m.group(0)[:40], confidence=0.85)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未约定免息期）")


def _t_loan_purpose(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"(?:贷款|借款|资金)用途[为是：:]?\s*[^。；]{2,40}", text)
    if m:
        val = re.sub(r"\s+", " ", m.group(0))
        f.update(found=True, value=val, display=val[:50], confidence=0.85)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未约定贷款用途）")


def _t_revolving_terms(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"(?:循环(?:使用|用信|额度)[^。；]{0,50}|额度有效期[^。；]{0,40}|一次性(?:使用|发放)[^。；]{0,30})", text)
    if m:
        val = re.sub(r"\s+", " ", m.group(0))
        f.update(found=True, value=val, display=val[:55], confidence=0.8)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未约定循环用信安排）")


def _t_commitment_fee(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"承诺费[^。；]{0,60}", text)
    if m:
        val = re.sub(r"\s+", " ", m.group(0))
        rate_m = re.search(r"([\d.]+)\s*%", val)
        f.update(found=True, value=(float(rate_m.group(1)) / 100 if rate_m else None),
                 display=val[:55], confidence=0.85)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未约定承诺费）")


def _t_purchase_option(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"(?:留购|期满(?:后)?(?:所有权|归属)|租赁物所有权)[^。；]{0,60}", text)
    if m:
        val = re.sub(r"\s+", " ", m.group(0))
        f.update(found=True, value=val, display=val[:60], confidence=0.8)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未约定留购或所有权安排）")


def _t_deposit(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"保证金[^。；]{0,60}", text)
    if m:
        v, raw, _c = std.parse_amount_any(m.group(0))
        val = re.sub(r"\s+", " ", m.group(0))
        f.update(found=True, value=v, display=val[:60], confidence=0.8)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未约定保证金）")


def _t_advance_ratio(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"(?:按|为)[^。；]{0,20}?应收账款[^。；]{0,12}?([\d.]+)\s*%", text)
    if not m:
        m = re.search(r"融资比例[^。；]{0,20}?([\d.]+)\s*%", text)
    if m:
        ratio = float(m.group(1))
        f.update(found=True, value=ratio / 100, display="应收账款的 %.0f%%" % ratio,
                 confidence=0.85)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未约定保理融资比例）")


def _t_trade_type(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"(利率互换|货币互换|交叉货币互换|远期利率协议|远期外汇|国债期货|股指期货|商品期货|期权交易|场外衍生品|场内衍生品|掉期交易)", text)
    if m:
        f.update(found=True, value=m.group(1), display=m.group(1), confidence=0.9)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未明确交易类型）")


def _t_default_events(f, doc):
    secs = _sections_with(doc, "违约事件")
    if secs:
        s = secs[0]
        f.update(found=True, value=s["body"][:200], display=s["title"][:50], confidence=0.8)
        _bind(f, doc, s["title"], clause_title=s["title"])
    else:
        f.update(display="未发现（未列举违约事件）")


def _t_collateral_arr(f, doc):
    text = doc.get("clean_text", "")
    m = re.search(r"(?:履约保障(?:机制|安排)|担保品|合格担保品)[^。；]{0,70}", text)
    if m:
        val = re.sub(r"\s+", " ", m.group(0))
        f.update(found=True, value=val, display=val[:70], confidence=0.8)
        _bind(f, doc, m.group(0))
    else:
        f.update(display="未发现（未约定履约保障安排）")


# ---------- 主入口 ----------

def extract_all(doc, type_info, name_hint=""):
    """执行通用字段 + 类型专属字段抽取，返回 fields dict（key → field）"""
    doc["type_key"] = type_info.get("key")
    fields = {}
    for key, label, group in UNIVERSAL_FIELDS:
        fields[key] = make_field(key, label, group)
    for key, label, group in TYPE_FIELDS.get(type_info["key"], TYPE_FIELDS["general"]):
        fields[key] = make_field(key, label, group)

    _f_contract_name(fields["contract_name"], doc, name_hint)
    _f_contract_type(fields["contract_type"], doc, type_info)
    _f_parties(fields["parties"], doc)
    _f_roles(fields["roles"], doc, fields["parties"])
    _f_amount(fields["amount"], doc, type_info["key"])
    _f_currency(fields["currency"], doc)
    _f_term(fields["term"], doc, type_info["key"])
    _f_dates(fields, doc)
    _f_rate(fields["rate"], doc)
    _f_payment(fields["payment_method"], doc, type_info["key"])
    _f_guarantee(fields["guarantee"], doc)
    _f_breach(fields["breach_liability"], doc)
    _f_early_term(fields["early_termination"], doc, fields["amount"].get("value"))
    _f_fee_bearing(fields["fee_bearing"], doc, fields["amount"].get("value"))
    _f_info_auth(fields["info_auth"], doc)
    _f_dispute(fields["dispute_resolution"], doc)
    _f_jurisdiction(fields["jurisdiction"], doc)
    _f_amendment(fields["amendment"], doc)
    _f_notice(fields["notice"], doc)
    _f_disclaimer(fields["disclaimer"], doc)
    _f_attachments(fields["attachments"], doc)

    ex = _type_extractors()
    for key in list(fields.keys()):
        if key in ex:
            ex[key](fields[key], doc)
    return fields
