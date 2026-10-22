# -*- coding: utf-8 -*-
"""ContractDiff：合同版本风险分析引擎
- 对齐新旧版本含义相同的条款
- 识别数字、责任主体、触发条件、授权范围变化
- 输出：原条款 / 新条款 / 风险方向 / 影响金额 / 复核原因
"""
import re
from difflib import SequenceMatcher


def _norm(s):
    return re.sub(r"\s+", "", s or "")


RISK_ASPECTS = [
    {"key": "prepay_penalty", "label": "提前结清违约金", "pat": r"(?:提前结清|提前还款|提前归还)[^。；;]{0,60}?([\d.]+)\s*%",
     "kind": "cost", "risk_text": "提前退出成本变化"},
    {"key": "overdue_rate", "label": "逾期违约金", "pat": r"(?:日万分之|万分之)\s*([\d.]+)", "kind": "cost", "risk_text": "逾期成本变化"},
    {"key": "overdue_days", "label": "逾期加速条款", "pat": r"逾期超过\s*(\d+)\s*日", "kind": "trigger", "risk_text": "加速到期触发条件收紧"},
    {"key": "guarantee", "label": "担保方式", "pat": r"(连带责任保证|一般保证)", "kind": "liability", "risk_text": "保证人责任变化"},
]


def _similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()


def align_clauses(old_clauses, new_clauses):
    """按标题+内容相似度对齐条款"""
    pairs = []
    used_new = set()
    for oc in old_clauses:
        best, best_score = None, 0.0
        for j, nc in enumerate(new_clauses):
            if j in used_new:
                continue
            score = _similarity(oc["title"], nc["title"]) * 0.6 + _similarity(oc["body"][:120], nc["body"][:120]) * 0.4
            if score > best_score:
                best_score, best = score, (j, nc)
        if best and best_score >= 0.35:
            used_new.add(best[0])
            pairs.append({"old": oc, "new": best[1], "score": best_score})
        else:
            pairs.append({"old": oc, "new": None, "score": best_score})
    for j, nc in enumerate(new_clauses):
        if j not in used_new:
            pairs.append({"old": None, "new": nc, "score": 0})
    return pairs


def _fmt_wan(amount):
    if amount is None:
        return "-"
    if abs(amount) >= 1e8:
        return "%.2f亿" % (amount / 1e8)
    return "%.1f万" % (amount / 1e4)


def _auth_scope(body):
    """授权范围等级：0=仅履行期间 1=终止后延续 2=终止后延续+对外共享"""
    level = 0
    if re.search(r"终止后[^。；;]{0,8}(?:年|月)[^。；;]{0,4}内", body):
        level = 1
    if re.search(r"共享[^。；;]{0,30}(?:合作|第三|机构)", body):
        level = 2
    return level


def _auth_label(level):
    return {0: "仅合同履行期间", 1: "终止后仍延续", 2: "终止后延续并共享给第三方机构"}.get(level, "未知")


def _party_names(parsed):
    items = ((parsed.get("fields") or {}).get("parties") or {}).get("items") or []
    return {str(item.get("name") or "").strip() for item in items if str(item.get("name") or "").strip()}


def _comparison_guard(old_parsed, new_parsed):
    """ContractDiff 只比较同一合同的版本，避免把两笔不同交易拼成版本变化。"""
    old_type = ((old_parsed.get("contract_type") or {}).get("key"))
    new_type = ((new_parsed.get("contract_type") or {}).get("key"))
    if old_type and new_type and old_type != new_type:
        return "两份文件的合同类型不同（%s / %s），不能当作同一合同版本比较。" % (old_type, new_type)
    old_parties, new_parties = _party_names(old_parsed), _party_names(new_parsed)
    if old_parties and new_parties and not (old_parties & new_parties):
        return "两份文件未发现共同签约主体，不能当作同一合同版本比较。"
    return None


def _field_changes(old_parsed, new_parsed, remain):
    """字段级量化（用户规约第十一条）：核心字段变化 → 金融影响金额。

    与条款级 RISK_ASPECTS 互补不重叠：
    - 条款级覆盖违约金率/逾期罚息/加速天数/担保方式/授权范围等条款文本变化；
    - 字段级覆盖解析出的数值字段（利率/金额/期限/费用）变化。
    每条含旧值/新值/双方原文证据/影响金额/测算过程。
    """
    changes = []

    def _val(v, pct_fmt=False):
        if v is None:
            return "-"
        return "%.2f%%" % (v * 100) if pct_fmt else _fmt_wan(v)

    # 1) 年利率变化 → 持有期利息影响 = Δr × 剩余本金 × 剩余年限
    ro, rn = old_parsed.get("annual_rate"), new_parsed.get("annual_rate")
    if ro is not None and rn is not None and abs(rn - ro) > 1e-9:
        p = remain or old_parsed.get("principal") or new_parsed.get("principal") or 0
        n_months = old_parsed.get("term_months") or new_parsed.get("term_months") or 0
        impact = (rn - ro) * p * (n_months / 12.0)
        changes.append({
            "type": "field", "layer": "field", "field": "annual_rate", "aspect": "年利率",
            "title": "利率调整", "old_value": ro, "new_value": rn,
            "old_text": old_parsed.get("rate_source") or _val(ro, True),
            "new_text": new_parsed.get("rate_source") or _val(rn, True),
            "risk": "up" if rn > ro else "down",
            "impact": round(impact, 2), "impact_text": _fmt_wan(impact),
            "reason": "年利率由 %s 调整为 %s，按剩余本金 %s、剩余期限 %d 个月测算，"
                      "持有期利息支出%s约 %s" % (
                          _val(ro, True), _val(rn, True), _fmt_wan(p), n_months,
                          "增加" if impact > 0 else "减少", _fmt_wan(abs(impact))),
        })

    # 2) 金额/本金变化 → 融资规模变化的利息影响 = ΔP × 利率 × 年限
    po, pn = old_parsed.get("principal"), new_parsed.get("principal")
    if po is not None and pn is not None and abs(pn - po) > 1:
        r = new_parsed.get("annual_rate") or old_parsed.get("annual_rate") or 0
        n_months = new_parsed.get("term_months") or old_parsed.get("term_months") or 0
        impact = (pn - po) * r * (n_months / 12.0)
        changes.append({
            "type": "field", "layer": "field", "field": "principal", "aspect": "融资金额",
            "title": "融资规模调整", "old_value": po, "new_value": pn,
            "old_text": old_parsed.get("principal_source") or _val(po),
            "new_text": new_parsed.get("principal_source") or _val(pn),
            "risk": "up" if pn > po else "down",
            "impact": round(impact, 2), "impact_text": _fmt_wan(impact),
            "reason": "融资金额由 %s 调整为 %s（差额 %s），按年利率 %.2f%%、期限 %d 个月测算，"
                      "利息支出%s约 %s" % (
                          _val(po), _val(pn), _fmt_wan(abs(pn - po)), r * 100, n_months,
                          "增加" if impact > 0 else "减少", _fmt_wan(abs(impact))),
        })

    # 3) 期限变化 → 额外利息 = 本金 × 利率 × Δ月 / 12（先息后本近似）
    no_, nn_ = old_parsed.get("term_months"), new_parsed.get("term_months")
    if no_ and nn_ and nn_ != no_:
        p = remain or new_parsed.get("principal") or old_parsed.get("principal") or 0
        r = new_parsed.get("annual_rate") or old_parsed.get("annual_rate") or 0
        impact = p * r * (nn_ - no_) / 12.0
        changes.append({
            "type": "field", "layer": "field", "field": "term_months", "aspect": "融资期限",
            "title": "期限调整", "old_value": no_, "new_value": nn_,
            "old_text": old_parsed.get("term_source") or ("%d 个月" % no_),
            "new_text": new_parsed.get("term_source") or ("%d 个月" % nn_),
            "risk": "up" if nn_ > no_ else "down",
            "impact": round(impact, 2), "impact_text": _fmt_wan(impact),
            "reason": "融资期限由 %d 个月调整为 %d 个月（%s %d 个月），按本金 %s、年利率 %.2f%% 测算，"
                      "利息支出%s约 %s" % (
                          no_, nn_, "延长" if nn_ > no_ else "缩短", abs(nn_ - no_),
                          _fmt_wan(p), r * 100,
                          "增加" if impact > 0 else "减少", _fmt_wan(abs(impact))),
        })

    # 4) 费用变化 → 直接成本差
    fo = sum(f.get("amount") or 0 for f in (old_parsed.get("fees") or []))
    fn = sum(f.get("amount") or 0 for f in (new_parsed.get("fees") or []))
    if abs(fn - fo) > 1:
        names = sorted({f.get("name") for f in (new_parsed.get("fees") or []) if f.get("amount")})
        changes.append({
            "type": "field", "layer": "field", "field": "fees", "aspect": "固定费用",
            "title": "费用总额调整", "old_value": fo, "new_value": fn,
            "old_text": "固定费用合计 %s" % _fmt_wan(fo),
            "new_text": "固定费用合计 %s（%s）" % (_fmt_wan(fn), "、".join(names) or "明细见费用条款"),
            "risk": "up" if fn > fo else "down",
            "impact": round(fn - fo, 2), "impact_text": _fmt_wan(fn - fo),
            "reason": "固定费用合计由 %s 变为 %s，直接成本%s %s" % (
                _fmt_wan(fo), _fmt_wan(fn), "增加" if fn > fo else "减少", _fmt_wan(abs(fn - fo))),
        })

    return changes


def diff_contracts(old_parsed, new_parsed, principal=None, remaining_principal=None):
    """主入口：返回结构化差异 + 风险方向 + 影响金额"""
    guard_reason = _comparison_guard(old_parsed, new_parsed)
    if guard_reason:
        return {
            "comparable": False, "changes": [], "field_changes": [],
            "up_count": 0, "down_count": 0, "review_count": 0,
            "total_impact": 0, "total_impact_text": "-", "conclusion": guard_reason,
            "blocked_reason": guard_reason,
        }
    base_p = principal if principal is not None else (old_parsed.get("principal") or 0)
    remain = remaining_principal if remaining_principal is not None else base_p
    pairs = align_clauses(old_parsed.get("clauses", []), new_parsed.get("clauses", []))
    changes = []

    for pair in pairs:
        oc, nc = pair["old"], pair["new"]
        if oc is None:
            changes.append({"type": "added", "title": nc["title"], "old_text": "", "new_text": nc["body"][:180],
                            "risk": "up", "impact": None, "reason": "新增条款，需评估义务与授权范围"})
            continue
        if nc is None:
            changes.append({"type": "removed", "title": oc["title"], "old_text": oc["body"][:180], "new_text": "",
                            "risk": "down", "impact": None, "reason": "条款被删除，原约定义务消失"})
            continue
        if _norm(oc["body"]) == _norm(nc["body"]):
            continue  # 文本完全一致，无变化
        title = nc["title"] if _similarity(oc["title"], nc["title"]) > 0.5 else oc["title"]

        detected = False
        for aspect in RISK_ASPECTS:
            mo = re.search(aspect["pat"], oc["body"])
            mn = re.search(aspect["pat"], nc["body"])
            if not mo or not mn:
                continue
            if mo.group(1) == mn.group(1):
                continue
            if aspect["key"] == "prepay_penalty":
                old_v, new_v = float(mo.group(1)), float(mn.group(1))
                impact = (new_v - old_v) / 100.0 * remain
                changes.append({
                    "type": "modified", "title": title, "aspect": aspect["label"],
                    "old_text": mo.group(0), "new_text": mn.group(0),
                    "risk": "up" if new_v > old_v else "down",
                    "impact": round(impact, 2), "impact_text": _fmt_wan(impact),
                    "reason": "违约金率由 %.1f%% 变为 %.1f%%，按剩余本金 %s 测算，提前退出成本%s %s" % (
                        old_v, new_v, _fmt_wan(remain), "增加" if impact > 0 else "减少", _fmt_wan(abs(impact))),
                })
                detected = True
            elif aspect["key"] == "overdue_rate":
                old_v, new_v = float(mo.group(1)), float(mn.group(1))
                changes.append({
                    "type": "modified", "title": title, "aspect": aspect["label"],
                    "old_text": mo.group(0), "new_text": mn.group(0),
                    "risk": "up" if new_v > old_v else "down", "impact": None,
                    "reason": "逾期违约金日利率由万分之%s 变为万分之%s，逾期成本上升约 %.0f%%" % (old_v, new_v, (new_v / old_v - 1) * 100),
                })
                detected = True
            elif aspect["key"] == "overdue_days":
                old_v, new_v = int(mo.group(1)), int(mn.group(1))
                changes.append({
                    "type": "modified", "title": title, "aspect": aspect["label"],
                    "old_text": mo.group(0), "new_text": mn.group(0),
                    "risk": "up" if new_v < old_v else "down", "impact": None,
                    "reason": "加速到期触发天数由 %d 日收紧至 %d 日，违约处置更易被触发" % (old_v, new_v),
                })
                detected = True
            elif aspect["key"] == "guarantee":
                changes.append({
                    "type": "modified", "title": title, "aspect": aspect["label"],
                    "old_text": mo.group(0), "new_text": mn.group(0),
                    "risk": "up" if "连带" in mn.group(1) else "down", "impact": None,
                    "reason": "担保方式由%s 变为%s，%s" % (mo.group(1), mn.group(1),
                             "保证人责任明显加重（放弃先诉抗辩权）" if "连带" in mn.group(1) else "保证人责任有所减轻"),
                })
                detected = True

        # 信息授权范围：专用检测（终止后N年 / 履行期间 / 共享给第三方）
        auth_old = _auth_scope(oc["body"])
        auth_new = _auth_scope(nc["body"])
        if auth_old != auth_new:
            changes.append({
                "type": "modified", "title": title, "aspect": "信息授权范围",
                "old_text": oc["body"][:120], "new_text": nc["body"][:120],
                "risk": "up" if auth_new > auth_old else "down", "impact": None,
                "reason": "信息授权由「%s」扩展为「%s」，数据留存与共享范围显著扩大，涉及征信合规" % (
                    _auth_label(auth_old), _auth_label(auth_new)),
            })
            detected = True

        if not detected:
            changes.append({
                "type": "modified", "title": title,
                "old_text": oc["body"][:160], "new_text": nc["body"][:160],
                "risk": "review", "impact": None,
                "reason": "条款表述发生变化，建议人工复核语义差异",
            })

    up = [c for c in changes if c.get("risk") == "up"]
    down = [c for c in changes if c.get("risk") == "down"]
    review = [c for c in changes if c.get("risk") == "review"]

    # 字段级量化变化（利率/金额/期限/费用），与条款级互补不重复计量
    field_changes = _field_changes(old_parsed, new_parsed, remain)
    changes.extend(field_changes)
    up.extend([c for c in field_changes if c.get("risk") == "up"])
    down.extend([c for c in field_changes if c.get("risk") == "down"])

    total_impact = sum(c.get("impact") or 0 for c in up) - sum(abs(c.get("impact") or 0) for c in down if c.get("impact"))

    return {
        "comparable": True,
        "changes": changes, "field_changes": field_changes,
        "up_count": len(up), "down_count": len(down), "review_count": len(review),
        "total_impact": round(total_impact, 2) if total_impact else 0,
        "total_impact_text": _fmt_wan(total_impact) if total_impact else "-",
        "conclusion": _conclusion(changes, up),
    }


def _conclusion(changes, up):
    if not changes:
        return "两个版本未发现实质性差异。"
    parts = []
    cost_up = [c for c in up if c.get("aspect") and ("违约金" in c["aspect"] or "成本" in c["aspect"])]
    if cost_up:
        parts.append("主要成本条款趋严：" + "；".join(c["reason"] for c in cost_up[:2]))
    liab = [c for c in changes if c.get("aspect") == "担保方式"]
    if liab:
        parts.append(liab[0]["reason"])
    auth = [c for c in changes if c.get("aspect") == "信息授权范围"]
    if auth:
        parts.append(auth[0]["reason"])
    if not parts:
        parts.append("共发现 %d 处条款变化，其中 %d 处风险方向上升，建议逐条复核。" % (len(changes), len(up)))
    return " ".join(parts)
