# -*- coding: utf-8 -*-
"""跨条款一致性检查：整份合同解析完成后做一次全局一致性分析。

检查项（对应用户规约第十三条）：
- 金额前后是否一致（本金类表述出现不同数值）
- 利率前后是否一致（不同条款出现不同年利率/费率）
- 期限是否与起止日期冲突
- 起止日期逻辑是否冲突（生效晚于到期）
- 主体名称是否变化（同一角色标签对应多个不同主体）
- 提前还款违约金率是否冲突（多个不同比例）
- 固定利率与调息条款是否矛盾
- 违约责任是否前后不一致（多个不同罚息上浮幅度）

每个冲突输出双方（或多方）原文证据与冲突原因，并把受影响字段标记为 conflict。
"""
import re

from . import standardize as std

# 本金类关键词：出现在同一句中才参与"金额一致性"比对
PRINCIPAL_KEYS = ("借款金额", "贷款金额", "融资金额", "租赁本金", "保理融资款",
                  "投资本金", "债权本金", "合同金额", "购置总价")

# 排除句（费用/比例上下文中的金额不属于本金口径）
_EXCLUDE_SENT = re.compile(r"[费率%‰]|按.{0,8}的|不超过|不得低于")

_RATE_SENT = re.compile(r"(年利率|年化|年费率)")
_RATE_EXCLUDE = re.compile(r"罚息|逾期|上浮|加收|违约|基准|参考|LPR|预期收益率|比较")

_PREPAY_PAT = re.compile(r"(?:提前还款|提前结清|提前归还)[^。；;]{0,50}?([\d.]+)\s*%")
_PENALTY_PAT = re.compile(r"(?:上浮|加收)[^。；;]{0,12}?([\d.]+)\s*%")


def _sentences_of(doc):
    """产出 (section, sentence) 序列，保留条款定位"""
    for s in doc["sections"]:
        blob = s["title"] + "\n" + s["body"]
        for sent in re.split(r"[。；;\n]", blob):
            sent = sent.strip()
            if sent:
                yield s, sent


def _ev(value, clause, text):
    return {"value": value, "clause": clause, "source_text": text[:100]}


def check_amount(doc, fields):
    """本金类金额前后一致性"""
    mentions = []
    for s, sent in _sentences_of(doc):
        if not any(k in sent for k in PRINCIPAL_KEYS):
            continue
        if _EXCLUDE_SENT.search(sent):
            continue
        v, raw, conf = std.parse_amount_any(sent)
        if v and conf >= 0.85:
            mentions.append({"value": v, "clause": s["title"],
                             "source_text": re.sub(r"\s+", " ", sent)[:100]})
    if len(mentions) < 2:
        return None
    vals = [m["value"] for m in mentions]
    max_v = max(vals)
    others = [m for m in mentions if abs(max_v - m["value"]) / max(max_v, 1) > 0.02]
    if not others:
        return None
    return {
        "type": "amount", "severity": "high", "field": "amount",
        "title": "金额前后不一致",
        "explanation": "不同条款对融资本金给出了不同的数值，须以最终签署版本核对，"
                       "金额口径差异会直接影响利息测算与担保责任范围。",
        "evidences": [_ev(m["value"], m["clause"], m["source_text"]) for m in mentions[:4]],
    }


def check_rate(doc, fields):
    """年利率/费率前后一致性（排除罚息、基准、LPR 等非合同利率语境）"""
    mentions = []
    for s, sent in _sentences_of(doc):
        if not _RATE_SENT.search(sent):
            continue
        if _RATE_EXCLUDE.search(sent):
            continue
        m = re.search(r"([\d.]+)\s*%", sent)
        if not m:
            continue
        val = float(m.group(1))
        if 0 < val <= 36:
            mentions.append({"value": val, "clause": s["title"],
                             "source_text": re.sub(r"\s+", " ", sent)[:100]})
    if len(mentions) < 2:
        return None
    vals = sorted(set(m["value"] for m in mentions))
    if len(vals) < 2:
        return None
    return {
        "type": "rate", "severity": "high", "field": "rate",
        "title": "利率前后不一致",
        "explanation": "不同条款出现了不同的利率数值（%s）。若合同同时约定固定利率与"
                       "另一处不同利率，需人工确认执行口径与适用范围。" % "、".join(str(v) for v in vals),
        "evidences": [_ev(m["value"], m["clause"], m["source_text"]) for m in mentions[:4]],
    }


def _months_between(d1, d2):
    try:
        from datetime import date
        return (d2.year - d1.year) * 12 + (d2.month - d1.month) + (d2.day - d1.day) / 30.0
    except Exception:
        return None


def check_term_vs_dates(doc, fields):
    """期限条款与起止日期的一致性"""
    eff = fields.get("effective_date") or {}
    exp = fields.get("expiry_date") or {}
    term = fields.get("term") or {}
    if eff.get("found") and exp.get("found"):
        try:
            y1, m1, d1 = [int(x) for x in str(eff["value"]).split("-")]
            y2, m2, d2 = [int(x) for x in str(exp["value"]).split("-")]
            from datetime import date
            d_start, d_end = date(y1, m1, d1), date(y2, m2, d2)
        except Exception:
            d_start = d_end = None
        if d_start and d_end and d_end <= d_start:
            return {
                "type": "date", "severity": "high", "field": "effective_date",
                "title": "起止日期逻辑冲突",
                "explanation": "合同到期日期不晚于生效日期，起止区间不成立，需核对原文日期。",
                "evidences": [_ev(eff.get("value"), eff.get("clause"), eff.get("source_text") or ""),
                              _ev(exp.get("value"), exp.get("clause"), exp.get("source_text") or "")],
            }
        if d_start and d_end and term.get("found"):
            months = _months_between(d_start, d_end)
            if months and abs(months - float(term["value"])) > 2:
                return {
                    "type": "term", "severity": "medium", "field": "term",
                    "title": "期限条款与起止日期不一致",
                    "explanation": "期限条款约定 %s 个月，但起止日期推算约 %.0f 个月，两者相差超过 2 个月，"
                                   "以何者为准影响利息计提与契约检查时点。" % (term["value"], months),
                    "evidences": [_ev(term.get("value"), term.get("clause"), term.get("source_text") or ""),
                                  _ev(eff.get("value"), eff.get("clause"), eff.get("source_text") or ""),
                                  _ev(exp.get("value"), exp.get("clause"), exp.get("source_text") or "")],
                }
    return None


def check_parties(doc, fields):
    """同一角色标签出现多个不同主体名称"""
    parties = (fields.get("parties") or {}).get("items") or []
    by_tag = {}
    for it in parties:
        tag = it.get("tag") or it.get("raw_role")
        if tag:
            by_tag.setdefault(tag, []).append(it)
    conflicts = []
    for tag, items in by_tag.items():
        names = {it["name"] for it in items}
        if len(names) > 1:
            conflicts.append({
                "type": "party", "severity": "medium", "field": "parties",
                "title": "主体名称前后变化",
                "explanation": "同一签约角色（%s）出现了多个不同主体名称，可能存在主体变更或名称笔误，"
                               "需核对签署页与资质文件。" % tag,
                "evidences": [_ev(it["name"], (fields.get("parties") or {}).get("clause") or "签署主体",
                                  it["name"]) for it in items],
            })
    return conflicts or None


def check_prepay_conflict(doc, fields):
    """多个不同的提前还款违约金率"""
    mentions = []
    for s, sent in _sentences_of(doc):
        for m in _PREPAY_PAT.finditer(sent):
            mentions.append({"value": float(m.group(1)), "clause": s["title"],
                             "source_text": re.sub(r"\s+", " ", m.group(0))[:100]})
    vals = sorted({m["value"] for m in mentions})
    if len(vals) < 2:
        return None
    return {
        "type": "prepay", "severity": "high", "field": "early_termination",
        "title": "提前还款条款冲突",
        "explanation": "合同中出现了多个不同的提前还款违约金率（%s），需确认适用条件或以"
                       "对借款人不利条款复核。" % "、".join("%s%%" % v for v in vals),
        "evidences": [_ev(m["value"], m["clause"], m["source_text"]) for m in mentions[:4]],
    }


def check_fixed_vs_repricing(doc, fields):
    """固定利率与调息条款矛盾"""
    rate = fields.get("rate") or {}
    text = doc.get("clean_text", "")
    fixed = re.search(r"利率[^。；]{0,12}(固定|不变|不调整)", text) or re.search(r"固定利率", text)
    repricing = re.search(r"利率调整|调整利率|重定价|利率浮动|浮动利率", text)
    if fixed and repricing:
        return {
            "type": "rate_fix", "severity": "medium", "field": "rate",
            "title": "固定利率与调息条款并存",
            "explanation": "合同既约定利率固定不变，又包含利率调整/重定价表述，实际计息口径"
                           "存在矛盾，建议人工确认利率机制。",
            "evidences": [_ev("固定利率", rate.get("clause") or "", fixed.group(0)),
                          _ev("调息条款", "", repricing.group(0))],
        }
    return None


def check_penalty_consistency(doc, fields):
    """多处不同的罚息上浮幅度"""
    mentions = []
    for s, sent in _sentences_of(doc):
        for m in _PENALTY_PAT.finditer(sent):
            if "违约" in sent or "罚息" in sent or "逾期" in sent:
                mentions.append({"value": float(m.group(1)), "clause": s["title"],
                                 "source_text": re.sub(r"\s+", " ", m.group(0))[:100]})
    vals = sorted({m["value"] for m in mentions})
    if len(vals) < 2:
        return None
    return {
        "type": "penalty", "severity": "medium", "field": "breach_liability",
        "title": "违约责任前后不一致",
        "explanation": "不同条款出现了不同的罚息上浮幅度（%s），需确认各情形下的适用规则。"
                       % "、".join("%s%%" % v for v in vals),
        "evidences": [_ev(m["value"], m["clause"], m["source_text"]) for m in mentions[:4]],
    }


CHECKS = [
    ("金额一致性", check_amount),
    ("利率一致性", check_rate),
    ("期限与日期", check_term_vs_dates),
    ("主体一致性", check_parties),
    ("提前还款冲突", check_prepay_conflict),
    ("固定与调息", check_fixed_vs_repricing),
    ("违约责任一致性", check_penalty_consistency),
]


def run_consistency(doc, fields):
    """执行全部一致性检查。返回 {conflicts: [...], checked: [...]}，并把受影响字段标记为 conflict。"""
    conflicts = []
    checked = []
    for name, fn in CHECKS:
        checked.append(name)
        try:
            out = fn(doc, fields)
        except Exception:
            out = None
        if not out:
            continue
        items = out if isinstance(out, list) else [out]
        for it in items:
            it["checked"] = name
            conflicts.append(it)
            f = fields.get(it.get("field"))
            if f is not None:
                f["status"] = "conflict"
                if not f.get("found"):
                    f["display"] = "冲突（多处条款不一致，见下方冲突详情）"
    return {"conflicts": conflicts, "checked": checked}
