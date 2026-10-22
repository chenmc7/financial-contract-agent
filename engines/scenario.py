# -*- coding: utf-8 -*-
"""ScenarioCompare：合同情景比较引擎
情景：持有到期 / 提前还款 / 利率上升 / 现金流紧张（部分展期） / 逾期
相同假设下对多份合同模拟，输出总成本、现金流压力、排名与排名翻转条件
"""
from .costlens import build_cashflows, monthly_rate, missing_detail


def _total_normal(p, r, n, repayment):
    flows = build_cashflows(p, r, n, repayment)
    return sum(f["total"] for f in flows), flows


def simulate_one(contract, scenario, params):
    """对单份合同按情景模拟。total_cost = 利息 + 费用 + 违约金（不含本金，与大纲口径一致）"""
    gaps = missing_detail(contract)
    if gaps:
        return {
            "hidden": True, "computable": False,
            "reason": "当前信息不足，暂无法进行可靠的情景模拟。",
            "missing_detail": gaps,
        }
    p = contract.get("principal")
    r = contract.get("annual_rate")
    n = contract.get("term_months")
    repayment = contract.get("repayment")
    if not p or r is None or not n or not repayment:
        return {
            "hidden": True, "computable": False,
            "reason": "当前信息不足，暂无法进行可靠的情景模拟。",
            "missing_detail": [{"field": "cashflow", "label": "现金流结构", "status": "not_found",
                                "text": "金额、利率、实际期限和还款方式必须明确后才能模拟"}],
        }
    fees = sum(f.get("amount") or 0 for f in contract.get("fees", []))
    prepay = contract.get("prepayment_penalty") or {}
    prepay_rate = prepay.get("rate", 0)

    if scenario == "hold":
        flows = build_cashflows(p, r, n, repayment)
        interest = sum(f["interest"] for f in flows)
        return {
            "total_cost": round(interest + fees, 2), "interest": round(interest, 2), "fees": round(fees, 2),
            "penalty": 0, "detail": "按合同约定履约至到期",
        }

    if scenario == "prepay":
        m = min(params.get("month", n // 2), n - 1)
        flows = build_cashflows(p, r, n, repayment)
        elapsed_interest = sum(f["interest"] for f in flows[:m])
        remaining_principal = flows[m - 1]["balance"] if m <= len(flows) else 0
        penalty = remaining_principal * prepay_rate
        total = elapsed_interest + penalty + fees
        return {
            "total_cost": round(total, 2), "interest": round(elapsed_interest, 2), "fees": round(fees, 2),
            "penalty": round(penalty, 2), "remaining_principal": round(remaining_principal, 2),
            "detail": "第 %d 个月提前归还全部剩余本金（剩余本金 %.0f 元），违约金 = 剩余本金 × %.1f%%" % (
                m, remaining_principal, prepay_rate * 100),
        }

    if scenario == "rate_up":
        delta = params.get("delta_pp", 1.0) / 100.0
        flows = build_cashflows(p, r + delta, n, repayment)
        base_flows = build_cashflows(p, r, n, repayment)
        interest = sum(f["interest"] for f in flows)
        base_interest = sum(f["interest"] for f in base_flows)
        return {
            "total_cost": round(interest + fees, 2), "interest": round(interest, 2), "fees": round(fees, 2),
            "penalty": 0, "extra_vs_base": round(interest - base_interest, 2),
            "detail": "利率上浮 %.1f 个百分点后持有到期（浮动利率合同）" % (delta * 100),
        }

    if scenario == "stress":
        months_late = params.get("months_late", 3)
        flows = build_cashflows(p, r, n, repayment)
        interest = sum(f["interest"] for f in flows)
        late_principal = p / max(n, 1) * months_late
        extra = late_principal * (r * 1.5 - r) * months_late / 12 + late_principal * 0.0005 * 30 * months_late
        return {
            "total_cost": round(interest + fees + extra, 2), "interest": round(interest, 2),
            "fees": round(fees, 2), "penalty": round(extra, 2),
            "detail": "现金流紧张，%d 期还款延迟，按罚息（上浮50%%）测算" % months_late,
        }

    if scenario == "overdue":
        months_late = params.get("months_late", 2)
        flows = build_cashflows(p, r, n, repayment)
        interest = sum(f["interest"] for f in flows)
        extra = p * 0.0005 * 30 * months_late + p * r * 0.5 * months_late / 12
        return {
            "total_cost": round(interest + fees + extra, 2), "interest": round(interest, 2),
            "fees": round(fees, 2), "penalty": round(extra, 2),
            "detail": "到期后逾期 %d 个月，按日万分之五违约金 + 罚息测算" % months_late,
        }
    return {"total_cost": 0, "detail": "未知情景"}


def compare_contracts(contracts, scenario, params=None):
    """contracts: [{id, name, parsed}] → 排名 + 翻转分析"""
    params = params or {}
    results = []
    for c in contracts:
        sim = simulate_one(c["parsed"], scenario, params)
        parsed = c["parsed"]
        sim.update({
            "id": c["id"], "name": c["name"],
            "annual_rate": parsed.get("annual_rate"),
            "term_months": parsed.get("term_months"),
            "repayment": parsed.get("repayment"),
            "prepay_rate": (parsed.get("prepayment_penalty") or {}).get("rate", 0),
            "guarantee": parsed.get("guarantee") or "未识别",
        })
        results.append(sim)
    if any(x.get("hidden") for x in results):
        for item in results:
            item.update({"rank": None, "diff_vs_best": None})
        return results
    results.sort(key=lambda x: x["total_cost"])
    for rank, item in enumerate(results, 1):
        item["rank"] = rank
    cheapest = results[0]
    for item in results:
        item["diff_vs_best"] = round(item["total_cost"] - cheapest["total_cost"], 2)
    return results


def flip_analysis(contracts, params=None):
    """持有到期 vs 提前还款 的排名翻转分析（大纲核心案例）

    复用同一现金流模型（simulate_one），输出：
    - hold / prepay 两情景排名（原有接口不变）
    - flips：排名翻转的合同
    - break_even：临界月——提前还款发生在临界月之前/之后，最优合同不同
    - drivers：为什么某合同更合适（利率差/违约金率差/费用差的量化归因）
    """
    params = params or {}
    m = params.get("month", 12)
    hold = compare_contracts(contracts, "hold", params)
    prepay = compare_contracts(contracts, "prepay", {**params, "month": m})
    if any(x.get("hidden") for x in hold + prepay):
        gaps = []
        for item in hold:
            for gap in item.get("missing_detail", []):
                entry = dict(gap); entry["contract"] = item.get("name")
                gaps.append(entry)
        return {"blocked": True, "reason": "当前信息不足，暂无法进行排名翻转分析。",
                "missing_detail": gaps, "hold": hold, "prepay": prepay, "flips": []}
    hold_rank = {c["id"]: c["rank"] for c in hold}
    prepay_rank = {c["id"]: c["rank"] for c in prepay}
    flips = []
    for c in contracts:
        cid = c["id"]
        if hold_rank.get(cid) != prepay_rank.get(cid):
            flips.append({"id": cid, "name": c["name"],
                          "hold_rank": hold_rank.get(cid), "prepay_rank": prepay_rank.get(cid),
                          "note": "%s 在持有到期时排名第 %d，提前还款情景下变为第 %d" % (
                              c["name"], hold_rank.get(cid, 0), prepay_rank.get(cid, 0))})
    result = {"hold": hold, "prepay": prepay, "prepay_month": m, "flips": flips}
    if len(contracts) >= 2:
        result["break_even"] = _break_even(contracts[0], contracts[1], params)
        result["drivers"] = _drivers(contracts[0], contracts[1], hold, prepay)
    return result


def _prepay_total(parsed, m):
    """第 m 月提前还款的总成本（利息 + 费用 + 违约金），复用现金流模型"""
    sim = simulate_one(parsed, "prepay", {"month": m})
    return sim.get("total_cost")


def _break_even(c_a, c_b, params=None):
    """临界月分析：扫描逐月退出成本（含持有到期端点），找两合同排名翻转的月份。

    提前退出时点在临界月之前选合同 X、之后选合同 Y——
    这是"为什么某合同更合适"的关键边界（用户规约第九条）。
    """
    a, b = c_a["parsed"], c_b["parsed"]
    n = min(a.get("term_months") or 0, b.get("term_months") or 0)
    if n < 2:
        return None
    prev_sign = None
    for m in range(1, n + 1):
        if m == n:
            # 到期端点：持有到期，无提前还款违约金
            ta = simulate_one(a, "hold", params).get("total_cost")
            tb = simulate_one(b, "hold", params).get("total_cost")
        else:
            ta, tb = _prepay_total(a, m), _prepay_total(b, m)
        if ta is None or tb is None:
            continue
        sign = 0 if abs(ta - tb) < 1e-6 else (1 if ta > tb else -1)
        if prev_sign and sign and sign != prev_sign:
            # 翻转：sign>0 表示 A（c_a）比 B（c_b）贵
            winner_before = c_b["name"] if prev_sign > 0 else c_a["name"]
            winner_after = c_b["name"] if sign > 0 else c_a["name"]
            if m == n:
                note = ("若在第 %d 个月之前提前退出，%s 总成本更低；一旦持有至到期（第 %d 个月），"
                        "%s 反超成为更优选择——临界点即到期日本身。" % (m, winner_before, m, winner_after))
            else:
                note = ("若在第 %d 个月之前提前还款，%s 总成本更低；从第 %d 个月起，%s 反超成为更优选择。"
                        "持有计划跨过该临界月时，合同选择结论应当切换。" % (m, winner_before, m, winner_after))
            return {"month": m, "note": note}
        if sign:
            prev_sign = sign
    return None


def _drivers(c_a, c_b, hold, prepay):
    """量化归因：用同一现金流模型解释两合同的成本差异来自哪里。"""
    a, b = c_a["parsed"], c_b["parsed"]
    p = a.get("principal") or b.get("principal") or 0
    fa = sum(f.get("amount") or 0 for f in (a.get("fees") or []))
    fb = sum(f.get("amount") or 0 for f in (b.get("fees") or []))
    ra, rb = a.get("annual_rate") or 0, b.get("annual_rate") or 0
    pra = (a.get("prepayment_penalty") or {}).get("rate") or 0
    prb = (b.get("prepayment_penalty") or {}).get("rate") or 0
    n = a.get("term_months") or b.get("term_months") or 0

    hold_gap = (hold[0].get("total_cost") or 0) - (hold[1].get("total_cost") or 0)
    hold_cheaper = hold[0]["name"]
    prepay_gap = (prepay[0].get("total_cost") or 0) - (prepay[1].get("total_cost") or 0)
    prepay_cheaper = prepay[0]["name"]

    parts = []
    if abs(rb - ra) > 1e-9:
        per_month = p * abs(rb - ra) / 12.0
        parts.append("利率差 %.2f pp（%s %.2f%% vs %s %.2f%%），按本金 %s 测算每月利息差约 %s，"
                     "持有期越长优势越累积" % (
                         abs(rb - ra) * 100, c_a["name"], ra * 100, c_b["name"], rb * 100,
                         _wan(p), _wan(per_month)))
    if abs(prb - pra) > 1e-9:
        penalty_gap = p * abs(prb - pra)
        parts.append("提前还款违约金率差 %.1f pp，一次性差异约 %s，只在提前退出时发生" % (
            abs(prb - pra) * 100, _wan(penalty_gap)))
    if abs(fb - fa) > 1:
        parts.append("固定费用差 %s（%s）" % (_wan(abs(fb - fa)),
                    "一次性支出，与持有期无关"))
    if not parts:
        parts.append("两合同成本结构接近，选择应主要考虑担保安排与契约约束")

    return {
        "hold": {"name": hold_cheaper, "gap": round(hold_gap, 2),
                 "text": "持有到期：%s 更省（总成本低 %s）" % (hold_cheaper, _wan(abs(hold_gap)))},
        "prepay": {"name": prepay_cheaper, "gap": round(prepay_gap, 2),
                   "text": "提前还款：%s 更省（总成本低 %s）" % (prepay_cheaper, _wan(abs(prepay_gap)))},
        "factors": parts,
        "conclusion": "；".join(parts) + "。利率优势随时间累积、违约金与费用是一次性成本，"
                      "两者的权衡决定了不同持有期下的最优合同。",
    }


def _wan(v):
    if v is None:
        return "-"
    if abs(v) >= 1e8:
        return "%.2f 亿" % (v / 1e8)
    return "%.1f 万" % (v / 1e4)
