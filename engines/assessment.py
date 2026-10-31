# -*- coding: utf-8 -*-
"""四引擎综合评估：把成本、情景、契约与条款差异汇总为决策结论。"""

from . import costlens, scenario, covenant, diffengine


KNOWLEDGE_NOTES = [
    {
        "title": "综合融资成本不等于名义利率",
        "text": "服务费、担保费、账户管理费等会减少实际可用资金，应与利息一起纳入现金流和真实年化成本测算。",
        "source": "中国人民银行贷款市场报价利率（LPR）",
        "url": "https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125440/3876551/index.html",
    },
    {
        "title": "一般保证与连带责任保证的责任路径不同",
        "text": "连带责任保证下，债务人不履行到期债务时，债权人可请求债务人或保证人在保证范围内承担责任。",
        "source": "《中华人民共和国民法典》第686—688条",
        "url": "https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/bgt/art/2023/art_60fb3863b7364b759123a42d1df568ca.html",
    },
    {
        "title": "融资租赁需核对租金构成与租赁物归属",
        "text": "融资租赁合同通常应明确租赁物、租期、租金构成与支付方式，以及期满后租赁物归属。",
        "source": "《中华人民共和国民法典》第735—736条",
        "url": "https://fjca.miit.gov.cn/zwgk/zcwj/wjfb/art/2020/art_9f0ef44677164c5eb3a5585bf82a07cb.html",
    },
]


def _clamp(value, low=0, high=100):
    return max(low, min(high, round(value)))


def _evidence(parsed, limit=3):
    clauses = parsed.get("clauses", [])
    preferred = []
    for clause in clauses:
        body = (clause.get("body") or "").strip()
        if not body:
            continue
        if any(k in body for k in ("利率", "费用", "提前", "担保", "违约", "资产负债率")):
            preferred.append({
                "title": clause.get("title") or "合同条款",
                "text": body[:260],
                "source": clause.get("source") or "合同原文",
            })
    return preferred[:limit] or [
        {"title": c.get("title") or "合同条款", "text": (c.get("body") or "")[:260], "source": c.get("source") or "合同原文"}
        for c in clauses[:limit]
    ]


def analyze_bundle(records, financials=None, prepay_month=12):
    if len(records) < 2:
        raise ValueError("综合评估至少需要两份合同")

    # 只有适合成本测算的合同（贷款/租赁/保理/授信/信用卡）才能进入现金流比较
    from .costlens import COST_TYPES
    usable = [r for r in records
              if (((r["parsed"].get("contract_type") or {}).get("key")) or "general") in COST_TYPES]
    if len(usable) < 2:
        raise ValueError("所选合同中可进行成本测算的不足两份（担保/理财等类型不适合现金流比较），"
                         "请选择贷款、融资租赁、保理或授信类合同")

    costs = []
    for r in usable:
        result = costlens.analyze_cost(r["parsed"])
        costs.append({"id": r["id"], "name": r["name"], "result": result, "evidence": _evidence(r["parsed"])})

    # 不用缺字段合同拼接出“综合结论”。成本和情景必须建立在同一组可计算现金流上。
    valid_costs = [x for x in costs if not x["result"].get("hidden")]
    if len(valid_costs) < 2:
        missing = []
        for item in costs:
            if item["result"].get("hidden"):
                missing.append("%s：%s" % (item["name"], "; ".join(
                    x.get("text", "") for x in item["result"].get("missing_detail", []))))
        raise ValueError("所选合同的成本/现金流要素不足，无法生成可靠的综合评估。" +
                         (" 缺项：" + "；".join(missing) if missing else ""))

    valid_ids = {x["id"] for x in valid_costs}
    valid_records = [r for r in usable if r["id"] in valid_ids]
    valid_wrapped = [{"id": r["id"], "name": r["name"], "parsed": r["parsed"]} for r in valid_records]
    scenario_result = scenario.flip_analysis(valid_wrapped[:2], {"month": prepay_month})
    diff_result = diffengine.diff_contracts(valid_records[0]["parsed"], valid_records[1]["parsed"],
                                            remaining_principal=valid_records[1]["parsed"].get("principal"))

    covenant_results = []
    if financials:
        for r in usable:
            covenants = r["parsed"].get("covenants", [])
            if covenants:
                monitored = covenant.monitor(covenants, financials)
                covenant_results.append({
                    "id": r["id"], "name": r["name"], "results": monitored,
                    "summary": covenant.summary(monitored),
                })

    # IRR 无解时 real_annual_cost_pct 为 None（见 costlens.irr），必须先剔除再求均值，
    # 否则 sum() 会因 int + None 抛 TypeError，导致整个综合评估接口 500。
    _rates = [x["result"]["real_annual_cost_pct"] for x in valid_costs
              if x["result"].get("real_annual_cost_pct") is not None]
    avg_rate = sum(_rates) / max(1, len(_rates))
    triggered = sum(x["summary"].get("triggered", 0) for x in covenant_results)
    near = sum(x["summary"].get("near", 0) for x in covenant_results)
    diff_up = diff_result.get("up_count", 0)
    risk_score = _clamp(28 + avg_rate * 2.3 + triggered * 24 + near * 10 + diff_up * 9)
    if risk_score >= 75:
        risk_level = "高风险"
    elif risk_score >= 50:
        risk_level = "中风险"
    else:
        risk_level = "低风险"
    value_score = _clamp(92 - avg_rate * 3.2 - triggered * 14 - near * 5 - diff_up * 4)

    hold_best = scenario_result["hold"][0]
    prepay_best = scenario_result["prepay"][0]
    conclusion = (
        f"持有到期优先考虑“{hold_best['name']}”，第{prepay_month}个月提前还款时优先考虑“{prepay_best['name']}”。"
        f"综合风险为{risk_level}（{risk_score}/100），性价比评分{value_score}/100。"
    )
    review = []
    if diff_up:
        review.append(f"两份合同存在 {diff_up} 项风险上升差异，签署前需逐条复核。")
    if triggered or near:
        review.append(f"财务契约中已触发 {triggered} 项、接近阈值 {near} 项，应同步制定补救方案。")
    if scenario_result.get("flips"):
        review.append("不同还款情景下合同排序发生翻转，决策必须结合预计持有期限。")
    if not review:
        review.append("未发现立即触发项，但仍需核验费用附件、担保范围与提前还款条件。")

    return {
        "costs": costs,
        "scenario": scenario_result,
        "covenants": covenant_results,
        "diff": diff_result,
        "overall": {
            "risk_score": risk_score,
            "risk_level": risk_level,
            "value_score": value_score,
            "conclusion": conclusion,
            "review": review,
            "method": "规则引擎量化评分；用于筛查和比较，不替代法律、审计或授信审批意见。",
        },
        "knowledge": KNOWLEDGE_NOTES,
    }
