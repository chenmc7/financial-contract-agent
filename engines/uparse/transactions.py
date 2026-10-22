# -*- coding: utf-8 -*-
"""轻量交易分组：把公告中的金额、期限和责任绑定到同一交易段落。

这不是第二套解析器。它只在现有原文段落上建立 deal/transaction 索引，
并在无法确认归属时保守保留候选，不把不同段落的字段拼成一个交易。
"""
import re

from . import standardize as std
from .extract import expand_evidence_span, _normalise_evidence


AMOUNT_ROLES = (
    ("actual_financing_amount", "实际融资金额"),
    ("actual_financing_amount", "实际借款金额"),
    ("actual_financing_amount", "实际贷款金额"),
    ("actual_financing_amount", "实际提款金额"),
    ("actual_financing_amount", "放款金额"),
    ("actual_financing_amount", "发放金额"),
    ("actual_financing_amount", "用信金额"),
    ("actual_financing_amount", "保理融资金额"),
    ("actual_financing_amount", "实际保理融资款"),
    ("leasing_principal", "租赁本金"),
    ("leasing_principal", "融资租赁本金"),
    ("leasing_principal", "租赁融资额"),
    ("loan_amount", "借款金额"),
    ("loan_amount", "贷款金额"),
    ("loan_amount", "借款本金"),
    ("loan_amount", "贷款本金"),
    ("credit_limit", "授信额度"),
    ("credit_limit", "最高授信额度"),
    ("credit_limit", "授信上限"),
    ("credit_limit", "批准额度"),
    ("guarantee_amount", "担保金额"),
    ("guarantee_amount", "被担保金额"),
    ("guarantee_amount", "担保余额"),
    ("guarantee_limit", "担保额度"),
)
TERM_ROLES = (
    ("actual_financing_term", "融资期限"),
    ("actual_financing_term", "实际融资期限"),
    ("actual_financing_term", "实际借款期限"),
    ("actual_financing_term", "实际贷款期限"),
    ("actual_financing_term", "提款期限"),
    ("leasing_term", "租赁期限"),
    ("leasing_term", "融资租赁期限"),
    ("leasing_term", "租期"),
    ("credit_term", "授信期限"),
    ("credit_term", "授信期间"),
    ("loan_term", "借款期限"),
    ("loan_term", "贷款期限"),
    ("loan_term", "借款期间"),
    ("loan_term", "贷款期间"),
)

# 仅把能说明“这是一笔独立交易”的标题当作边界；财务表格中的“一、二”不触发。
DEAL_HEAD = re.compile(
    r"^(?:担保事项\s*[一二三四五六七八九十\d]+|"
    r"(?:第\s*[一二三四五六七八九十\d]+\s*(?:笔|项|笔交易)|交易\s*[一二三四五六七八九十\d]+)|"
    r"(?:[（(]\s*(?:\d+|[一二三四五六七八九十]+)\s*[）)]|(?:\d+|[一二三四五六七八九十]+)[、.．])\s*"
    r"(?:担保事项[一二三四五六七八九十\d]*|融资租赁(?:业务|事项|安排)?|保理(?:业务|事项|安排)?|"
    r"借款(?:事项|业务|安排)?|贷款(?:事项|业务|安排)?|融资(?:事项|业务|安排)?|授信(?:事项|业务|安排)?))"
)


def _compact(s):
    return re.sub(r"\s+", "", s or "")


def _paragraphs(raw_text):
    """原文行级索引；start/end 始终对应 API 返回的原始文本。"""
    out, pages = [], []
    page = 1
    cursor = 0
    for i, line in enumerate((raw_text or "").splitlines(True), 1):
        body = line.rstrip("\r\n")
        pm = re.search(r"【\s*第\s*(\d+)\s*页\s*】", body)
        if pm:
            page = int(pm.group(1))
            pages.append({"page": page, "start": cursor, "end": cursor + len(line)})
            body = re.sub(r"【\s*第\s*\d+\s*页\s*】", "", body).strip()
        if body.strip():
            start = cursor + (len(line) - len(line.lstrip()))
            end = start + len(body)
            out.append({"paragraph_id": "p%04d" % len(out), "page": page,
                        "start": start, "end": end, "text": body})
        cursor += len(line)
    if not out and raw_text:
        out.append({"paragraph_id": "p0000", "page": 1, "start": 0,
                    "end": len(raw_text), "text": raw_text})
    return out, pages


def _span_for(raw_text, start, end, paragraphs):
    anchor_start = start
    start, end = expand_evidence_span(raw_text or "", start, end)
    p = next((x for x in paragraphs if x["start"] <= anchor_start < x["end"]), None)
    if not p and paragraphs:
        p = min(paragraphs, key=lambda x: abs(x["start"] - anchor_start))
    page = p["page"] if p else 1
    return {"start": start, "end": end, "page": page,
            "paragraph_id": p["paragraph_id"] if p else None,
            "text": _normalise_evidence(raw_text[start:end] or "")}


def _find(raw_text, needle, offset=0):
    if not needle:
        return None
    direct = raw_text.find(needle, offset)
    if direct >= 0:
        return direct, direct + len(needle)
    compact_text = _compact(raw_text[offset:])
    compact_needle = _compact(needle)
    if not compact_needle:
        return None
    pos = compact_text.find(compact_needle)
    if pos < 0:
        return None
    original = [i for i, ch in enumerate(raw_text[offset:]) if not ch.isspace()]
    if pos >= len(original):
        return None
    start = offset + original[pos]
    last = min(len(original) - 1, pos + len(compact_needle) - 1)
    return start, offset + original[last] + 1


def _candidate(text, role_defs):
    compact = _compact(text)
    found = []
    for role, label in role_defs:
        for m in re.finditer(re.escape(label), compact):
            segment = compact[m.start():m.start() + 120]
            remainder = segment[len(label):]
            # 必须有明确连接词；“借款金额占净资产”不是一笔借款金额。
            if role.endswith("amount") and not re.match(r"^(?:为|是|：|:|合计为|合计是|约为|约是|约|人民币)", remainder):
                continue
            if role == "loan_amount" and re.search(r"占|比例|净资产|总资产", remainder[:40]):
                continue
            if role.endswith("term"):
                value, raw, conf = std.parse_term_any(segment)
                if re.search(r"不超过|最长|以内|至多", segment):
                    value, conf = None, 0.55
                    raw = segment[:80]
                if raw:
                    found.append({"role": role, "label": label, "value": value,
                                  "raw": raw, "confidence": conf or 0.55})
            else:
                value, raw, conf = std.parse_amount_any(segment)
                if value is not None:
                    found.append({"role": role, "label": label, "value": value,
                                  "raw": raw, "confidence": conf})
    return found


def _deal_heads(paragraphs):
    heads = []
    for p in paragraphs:
        txt = p["text"].strip()
        if DEAL_HEAD.search(txt) and len(txt) <= 80:
            heads.append(p)
    return heads


def _is_major_boundary(text):
    return bool(re.match(r"^[一二三四五六七八九十百]+、\s*(?:被担保人基本情况|担保协议|担保余额|财务|其他)", text.strip()))


def build_transactions(raw_text, fields=None, type_info=None):
    paragraphs, pages = _paragraphs(raw_text)
    heads = _deal_heads(paragraphs)
    # 没有独立交易标题时，整份文件作为一个候选交易，避免跨段拼接。
    groups = []
    if heads:
        for i, head in enumerate(heads):
            next_starts = [x["start"] for x in paragraphs
                           if x["start"] > head["start"] and _is_major_boundary(x["text"])]
            end_candidates = ([heads[i + 1]["start"]] if i + 1 < len(heads) else []) + next_starts
            end = min(end_candidates) if end_candidates else len(raw_text or "")
            groups.append({"head": head, "start": head["start"], "end": end})
    else:
        groups = [{"head": None, "start": 0, "end": len(raw_text or "")}]

    transactions = []
    for i, group in enumerate(groups, 1):
        start, end = group["start"], group["end"]
        block = (raw_text or "")[start:end]
        amounts = _candidate(block, AMOUNT_ROLES)
        terms = _candidate(block, TERM_ROLES)
        # 同一角色同一数值可能在公告摘要和正文中重复出现；去重后仍保留不同数值，
        # 供下方的“不确定”保护逻辑识别修订/多口径情况。
        def dedupe(items):
            seen = set(); out = []
            for item in items:
                key = (item.get("role"), item.get("value"))
                if item.get("value") is None:
                    key += (item.get("raw"),)
                if key in seen:
                    continue
                seen.add(key); out.append(item)
            return out
        amounts, terms = dedupe(amounts), dedupe(terms)
        # 只输出真正包含融资/租赁本金与期限的交易段；担保额度单独保留为附属金额。
        primary_amounts = [x for x in amounts if x["role"] in
                           {"actual_financing_amount", "leasing_principal", "loan_amount"}]
        primary_terms = [x for x in terms if x["role"] in
                         {"actual_financing_term", "leasing_term", "loan_term"}]
        # 同一交易段出现两个不同“实际本金”时，无法凭关键词判断哪个是最终口径，
        # 不强行选择；页面仍展示候选金额并要求人工复核。
        amount_values = {x.get("value") for x in primary_amounts if x.get("value") is not None}
        primary_amount = primary_amounts[0] if len(amount_values) <= 1 and primary_amounts else None
        term_values = {x.get("value") for x in primary_terms if x.get("value") is not None}
        primary_term = primary_terms[0] if len(term_values) <= 1 and primary_terms else None
        if not primary_amounts and not primary_terms:
            continue
        def evidence(item):
            hit = _find(block, item["raw"])
            if not hit:
                hit = _find(block, item["label"])
            if not hit:
                return None
            return _span_for(raw_text, start + hit[0], start + hit[1], paragraphs)

        entry = {
            "transaction_id": "transaction_%d" % len(transactions + [None]),
            "label": (group["head"]["text"] if group["head"] else "全文唯一交易")[:80],
            "transaction_type": ((fields or {}).get("transaction_type") or {}).get("value") or
                                ((type_info or {}).get("key") or "other"),
            "amounts": [], "terms": [], "rates": [], "parties": [], "guarantee": [],
            "evidence": {"start": start, "end": end,
                          "page": group["head"].get("page") if group["head"] else 1,
                          "paragraph_id": group["head"].get("paragraph_id") if group["head"] else None},
        }
        for item in amounts:
            ev = evidence(item)
            item = {**item, "evidence": ev}
            entry["amounts"].append(item)
        for item in terms:
            ev = evidence(item)
            item = {**item, "evidence": ev}
            entry["terms"].append(item)
        # 补充协议常写“96个月变更为122个月”，保留变更后的候选并绑定同一交易证据。
        for m in re.finditer(r"(?:融资|租赁|借款|贷款)(?:期限|期间)[^。；\n]{0,90}?(?:变更为|调整为|延长至|缩短为)\s*(\d+)\s*个?月", block):
            value = int(m.group(1))
            if not any(x.get("value") == value and x.get("role") in
                       {"leasing_term", "actual_financing_term", "loan_term"}
                       for x in entry["terms"]):
                raw = m.group(0)
                hit = _find(block, raw)
                entry["terms"].append({"role": "leasing_term", "label": "租赁期限",
                                       "value": value, "raw": raw, "confidence": 0.9,
                                       "evidence": _span_for(raw_text, start + hit[0], start + hit[1], paragraphs) if hit else None})
        # 利率、保证责任和主体只在当前交易段落内采集，避免借用相邻交易。
        for p in paragraphs:
            if p["start"] < start or p["start"] >= end:
                continue
            txt = p["text"]
            if re.search(r"利率|费率|LPR|基点", txt):
                val, rtype, raw, conf, note = std.parse_rate_any(txt)
                if raw:
                    hit = _find(raw_text, raw, p["start"])
                    entry["rates"].append({"value": val, "rate_type": rtype, "raw": raw,
                                           "confidence": conf, "note": note,
                                           "evidence": _span_for(raw_text, hit[0], hit[1], paragraphs) if hit else None})
            for g in re.findall(r"连带责任保证|一般保证|保证担保|抵押担保|质押担保|有追索权|无追索权", txt):
                hit = _find(raw_text, g, p["start"])
                entry["guarantee"].append({"value": g, "evidence": _span_for(raw_text, hit[0], hit[1], paragraphs) if hit else None})
        if fields:
            for key in ("financing_party", "factoring_bank", "parties"):
                field = fields.get(key) or {}
                span = field.get("evidence_span") or {}
                if span.get("start") is not None and start <= span["start"] < end:
                    value = field.get("value")
                    if key == "parties":
                        value = [x.get("name") for x in (field.get("items") or []) if x.get("name")]
                    if value:
                        entry["parties"].append({"role": key, "value": value,
                                                 "evidence": span})
        # 同一交易段的多个不同年化利率同样不能凭出现顺序拍板；基点/LPR 机制与
        # 已披露的实际年化利率可以并存，不把二者误判成冲突。
        annual_rate_values = {x.get("value") for x in entry["rates"]
                              if x.get("value") is not None and x.get("rate_type") == "annual"}
        primary_rate = next((x for x in entry["rates"] if x.get("value") is not None and
                             x.get("rate_type") not in {"bp", "lpr_float"}),
                            entry["rates"][0] if entry["rates"] else None)
        if len(annual_rate_values) > 1:
            primary_rate = None
        entry["primary"] = {
            "amount": primary_amount,
            "term": primary_term,
            "rate": primary_rate,
            "status": "confirmed" if primary_amount and primary_term and
                      primary_term.get("value") is not None else "uncertain",
        }
        # 期限冲突只比较“实际融资/租赁/借款”角色；授信期限是额度有效期，
        # 不能因为与实际融资期限不同就把交易标成不确定。
        primary_term_values = [x.get("value") for x in entry["terms"]
                               if x.get("value") is not None and x.get("role") in
                               {"actual_financing_term", "leasing_term", "loan_term"}]
        change_match = re.search(r"(?:融资|租赁|借款|贷款)(?:期限|期间)[^。；\n]{0,90}?(?:变更为|调整为|延长至|缩短为)", block)
        if len(set(primary_term_values)) > 1 and change_match:
            entry["term_change"] = {"from": primary_term_values[0], "to": primary_term_values[-1],
                                     "status": "confirmed", "label": "期限变更"}
            entry["primary"]["term"] = next((x for x in reversed(entry["terms"])
                                               if x.get("value") == primary_term_values[-1] and
                                               x.get("role") in {"actual_financing_term", "leasing_term", "loan_term"}),
                                               entry["primary"]["term"])
            entry["primary"]["status"] = "confirmed" if entry["primary"].get("amount") else "uncertain"
        elif len(set(primary_term_values)) > 1:
            entry["primary"]["term"] = None
            entry["primary"]["status"] = "uncertain"
        if group["head"]:
            entry["evidence"]["text"] = group["head"]["text"]
        transactions.append(entry)

    # 无交易专属金额时仍返回空数组，而不是制造一个“交易”。
    return {"count": len(transactions), "transactions": transactions,
            "paragraphs": paragraphs, "pages": pages}
