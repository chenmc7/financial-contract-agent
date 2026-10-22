# -*- coding: utf-8 -*-
"""ContractGuard 风险分类引擎：六大风险类别 + 证据绑定 + 人工复核机制。

风险类别（用户规约第十二条）：
- economic   经济成本风险：固定费用、提前退出成本、利率机制等推高综合融资成本
- credit     信用与违约风险：罚息、复利、加速到期、逾期成本等
- guarantee   担保风险：连带责任、担保范围扩大、保证期间等
- covenant    财务契约风险：资产负债率/流动比率等契约阈值与触发后果
- compliance  合规与数据风险：信息授权、征信查询、数据留存与共享
- operation   操作与执行风险：管辖、争议解决方式、条款冲突、表述不明确

每个风险统一包含：标题/类别/等级/影响主体/原文条款/原文证据/风险解释/
可能后果/修改或复核建议/置信度。所有风险均基于规则引擎从解析字段推导，
AI 分析与合同事实在数据结构上分离（facts 见 fields，本引擎输出为分析结论）。
涉及法律效力判断的条目标记 needs_legal_review，明确提示需专业法律人员复核。
"""
import re

CATEGORY_LABELS = {
    "economic": "经济成本风险",
    "credit": "信用与违约风险",
    "guarantee": "担保风险",
    "covenant": "财务契约风险",
    "compliance": "合规与数据风险",
    "operation": "操作与执行风险",
}

LEVEL_ORDER = {"高": 0, "中": 1, "低": 2}


def _mk(rid, title, category, level, affected, field, explanation,
        consequence, suggestion, confidence, clause=None, source_text=None,
        needs_legal_review=False):
    field = field or {}
    return {
        "id": rid,
        "title": title,
        "category": category,
        "category_label": CATEGORY_LABELS[category],
        "level": level,
        "affected_party": affected or "签约相关方",
        "clause": clause or field.get("clause"),
        "page": field.get("page"),
        "source_text": source_text or (field.get("source_text") or ""),
        "field_key": field.get("key"),
        "evidence_span": field.get("evidence_span"),
        "context_span": field.get("context_span"),
        "explanation": explanation,
        "consequence": consequence,
        "suggestion": suggestion,
        "confidence": confidence,
        "needs_legal_review": needs_legal_review,
        "engine": "rule",  # 规则引擎推导（AI 分析结论，非合同原文事实）
    }


_EVIDENCE_REQUIREMENTS = {
    # 风险标题涉及这些语义时，证据必须同时包含事实对象与触发动作；
    # 仅有“授权”或一个年份不能生成确定性风险。
    "cmp-auth": (("信息", "数据", "征信", "资料"), ("保留", "留存", "保存", "终止", "查询")),
    "cmp-share": (("信息", "数据", "征信", "资料"), ("共享", "第三方", "合作")),
    "crd-recourse": (("保理", "追索", "回购"), ("追索", "回款", "付款")),
    "gua-joint": (("连带", "保证"), ("连带", "保证")),
}


def _evidence_consistent(risk):
    text = str(risk.get("source_text") or "").strip()
    requirement = _EVIDENCE_REQUIREMENTS.get(risk.get("id"))
    if not text:
        return False
    if not requirement:
        return True
    objects, actions = requirement
    return any(word in text for word in objects) and any(word in text for word in actions)


def _borrower(roles):
    for k in ("借款人", "承租人", "委托人", "投保人", "债务人"):
        if roles.get(k):
            return roles[k]
    return "融资方"


def _guarantor(roles):
    for k in ("保证人", "抵押人", "出质人"):
        if roles.get(k):
            return roles[k]
    return "担保方"


def _lender(roles):
    for k in ("贷款人", "出租人", "管理人", "保理商", "债权人", "保险人"):
        if roles.get(k):
            return roles[k]
    return "金融机构"


def _fmt_pct(x):
    return "%.2f%%" % (x * 100)


def assess_risks(parsed):
    """主入口：parsed = parse_contract 结果。返回 {risks, summary}。"""
    fields = parsed.get("fields") or {}
    parties = (fields.get("parties") or {}).get("items") or []
    roles = {it.get("raw_role"): it.get("name") for it in parties}
    borrower = _borrower(roles)
    guarantor = _guarantor(roles)
    lender = _lender(roles)
    type_key = (parsed.get("contract_type") or {}).get("key")
    risks = []

    # ---------- 经济成本风险 ----------
    fees_f = fields.get("fee_bearing") or {}
    fee_items = fees_f.get("items") or []
    if fee_items:
        total_rate = sum(x.get("rate") or 0 for x in fee_items)
        names = "、".join(x["name"] for x in fee_items[:3])
        level = "高" if total_rate >= 0.02 else ("中" if total_rate >= 0.01 else "低")
        risks.append(_mk(
            "eco-fees", "固定费用推高综合融资成本", "economic", level, borrower, fees_f,
            "合同约定 %d 项独立费用（%s），合计费率约 %s，这些费用不随利率下调而减少。"
            % (len(fee_items), names, _fmt_pct(total_rate)),
            "按本金测算，费用使综合融资成本率高于名义利率约 %s，实际到手资金减少。"
            % _fmt_pct(total_rate),
            "与金融机构协商费用减免或摊薄；比较无费用方案的利率上浮空间；把费用写入综合成本口径评估。",
            0.92, clause=fee_items[0].get("clause"),
            source_text=str(fee_items[0].get("source") or "")))
    early = fields.get("early_termination") or {}
    early_val = early.get("value") if isinstance(early.get("value"), dict) else {}
    if early_val.get("rate"):
        r = early_val["rate"]
        level = "高" if r >= 0.01 else ("中" if r >= 0.003 else "低")
        risks.append(_mk(
            "eco-prepay", "提前退出成本偏高", "economic", level, borrower, early,
            "提前还款/结清需支付剩余本金 %s 的违约金，锁定了较长的资金占用期。"
            % _fmt_pct(r),
            "若未来现金流宽裕想提前还款，需支付一笔不可摊薄的违约成本，抑制再融资灵活性。",
            "签约前模拟未来 12–36 个月的现金流，评估提前还款概率；协商阶梯式违约金或免赔额度。",
            0.9))
    rate = fields.get("rate") or {}
    if rate.get("rate_type") == "lpr_float" and rate.get("found"):
        risks.append(_mk(
            "eco-lpr", "浮动利率敞口（LPR 挂钩）", "economic", "中", borrower, rate,
            "利率按 LPR 加点执行，属于浮动利率机制，付息成本随市场利率上行而增加。",
            "若 LPR 上行 100BP，按本金规模利息支出同步上升，影响利润与契约指标。",
            "评估利率上限保护（利率互换/上限条款）；在现金流压力测试中纳入利率上行情景。",
            0.85))
    # 保证金占用（租赁/衍生品）
    deposit = fields.get("deposit") or (fields.get("margin") or {})
    for dk in ("deposit", "margin"):
        d = fields.get(dk)
        if d and d.get("found") and isinstance(d.get("value"), (int, float)) and d["value"]:
            risks.append(_mk(
                "eco-deposit", "保证金占用资金", "economic", "中", borrower, d,
                "合同要求缴纳保证金（%s），该部分资金在履约期间被占用且通常不计利息。"
                % (d.get("display") or ""),
                "实际可用资金减少，等价于隐性成本；若触发扣划条件将直接损失。",
                "协商降低保证金比例或以银行保函替代；把保证金占用计入综合成本测算。",
                0.8))
            break

    # ---------- 信用与违约风险 ----------
    breach = fields.get("breach_liability") or {}
    breach_val = breach.get("value") if isinstance(breach.get("value"), dict) else {}
    ups = breach_val.get("penalty_ups") or []
    wms = breach_val.get("daily_wm") or []
    if ups:
        top = max(ups)
        risks.append(_mk(
            "crd-penalty", "罚息上浮幅度 %d%%" % top, "credit",
            "高" if top >= 50 else "中", borrower, breach,
            "逾期部分按合同利率上浮 %s 计收罚息，逾期时间越长惩罚性成本越高。"
            % ("/".join("%d%%" % u for u in ups),
               ),
            "一旦现金流紧张发生逾期，利息负担迅速放大，并可能触发加速到期条款。",
            "建立还款日前置提醒与备付金池；与金融机构预沟通展期与调整机制。",
            0.9))
    if wms:
        top_wm = max(wms)
        annualized = top_wm / 10000 * 360 * 100
        risks.append(_mk(
            "crd-daily", "逾期违约金按日万分之 %s" % ("/".join(str(w) for w in wms)), "credit",
            "高" if top_wm >= 5 else "中", borrower, breach,
            "按日计收的违约金年化约 %.0f%%，属于惩罚性费率。" % annualized,
            "长期逾期状态下违约金累积速度远超正常利息，债务快速膨胀。",
            "逾期早期立即协商还款计划；核对是否存在上限条款或司法调整空间（需法律复核）。",
            0.88, needs_legal_review=True))
    # 保理追索权是重要责任字段：即使没有罚息条款，也必须进入风险输出。
    recourse = fields.get("recourse") or {}
    if recourse.get("found") and "有追索权" in str(recourse.get("value") or recourse.get("display") or ""):
        risks.append(_mk(
            "crd-recourse", "附追索权：应收账款未回款仍需承担付款责任", "credit", "高", borrower, recourse,
            "原文明确保理银行保留追索权，基础交易债务人或应收账款未按期回款时，保理银行仍可向融资方追偿。",
            "应收账款回款风险不会转移，融资方可能同时承担回款不确定性与融资偿付压力。",
            "核对追索触发条件、回购/差额补足义务和最长还款期限，并将未回款情景纳入流动性压力测试。",
            0.92, needs_legal_review=True))
    # 加速到期触发（逾期 N 日即要求清偿全部剩余债务）
    for i, cl in enumerate(parsed.get("clauses") or []):
        m = None
        body = cl.get("body") or ""
        if "逾期" in body and ("全部剩余" in body or "提前" in body):
            m = re.search(r"逾期超过\s*(\d+)\s*日", body)
            if m:
                risks.append(_mk(
                    "crd-accel-%d" % i, "加速到期：逾期 %s 日即触发全部清偿" % m.group(1), "credit",
                    "中", borrower, None,
                    "合同约定逾期超过 %s 日，金融机构有权要求立即支付全部剩余本息（加速到期）。" % m.group(1),
                    "一旦触发，单期逾期会放大为全额债务即时到期，流动性压力骤增。",
                    "在还款日前设置多重提醒；与金融机构预沟通展期机制，避免被动触发。",
                    0.85, clause=cl.get("title"), source_text=body[:100]))
                break

    # ---------- 担保风险 ----------
    gua = fields.get("guarantee") or {}
    if gua.get("found") and "连带" in str(gua.get("value") or ""):
        affected = guarantor if type_key == "guarantee" else guarantor
        risks.append(_mk(
            "gua-joint", "连带责任保证（放弃先诉抗辩）", "guarantee",
            "高", affected, gua,
            "保证方式为连带责任保证，债务人不履行到期债务时，债权人可直接要求保证人在保证范围内承担责任。",
            "保证人失去先诉抗辩权，可能先于债务人被追偿，或有负债直接显性化。",
            "保证人应评估自身代偿能力上限；争取一般保证或限定担保份额；关联担保需履行内部决议程序。",
            0.92, needs_legal_review=True))
    gscope = fields.get("guarantee_scope") or {}
    if gscope.get("found"):
        scope_txt = str(gscope.get("value") or gscope.get("display") or "")
        if any(k in scope_txt for k in ("罚息", "复利", "实现债权", "损害赔偿")):
            risks.append(_mk(
                "gua-scope", "担保范围覆盖罚息、复利及实现债权费用", "guarantee",
                "中", guarantor if type_key == "guarantee" else borrower, gscope,
                "担保范围不仅含本金利息，还覆盖罚息、复利、违约金及实现债权的费用，实际责任敞口大于本金。",
                "诉讼、执行、保全等费用都会叠加进担保责任，最终代偿金额可能显著高于本金。",
                "在担保决策模型中按「本金 + 利息 + 罚息 + 费用」估算全额敞口，而非仅本金。",
                0.85))
    gperiod = fields.get("guarantee_period") or {}
    if gperiod.get("found"):
        risks.append(_mk(
            "gua-period", "保证期间约定为 %s" % str(gperiod.get("display") or "")[:30], "guarantee",
            "中", guarantor, gperiod,
            "保证期间决定了债权人向保证人主张责任的窗口，期间届满后保证人可免除责任（需法律复核）。",
            "对债权人：错过期间将丧失对保证人的请求权；对保证人：期间越长或有负债暴露越久。",
            "债权人在保证期间内及时主张权利并留痕；保证人建立期间台账跟踪。涉及法律效力，需专业法律人员复核。",
            0.8, needs_legal_review=True))

    # ---------- 财务契约风险 ----------
    for i, cov in enumerate(parsed.get("covenants") or []):
        op = cov.get("operator") or ("<=" if cov.get("direction") == "max" else ">=")
        unit = cov.get("unit") or ("%" if cov.get("indicator") == "资产负债率" else "")
        risks.append(_mk(
            "cov-%d" % i, "财务契约：%s %s %s%s" % (cov.get("indicator"), op,
                                                 cov.get("threshold"), unit),
            "covenant", "中", borrower, None,
            "合同设置了持续监控的财务红线（%s %s %s%s，%s检查），突破即构成契约违约。"
            % (cov.get("indicator"), op, cov.get("threshold"), unit, cov.get("frequency") or "季度"),
            (cov.get("consequence") or "触发后金融机构有权要求补救、追加担保或宣布提前到期")[:80],
            "上传最近财务报表进入 CovenantGuard 监控；在补救期（%s）内预留增资/降债方案。"
            % (("%d 日" % cov["remedy_days"]) if cov.get("remedy_days") else "合同约定"),
            0.9, clause=cov.get("clause"), source_text=cov.get("source")))

    # ---------- 合规与数据风险 ----------
    auth = fields.get("info_auth") or {}
    auth_val = auth.get("value") if isinstance(auth.get("value"), dict) else {}
    if auth.get("found"):
        years = auth_val.get("retention_years")
        scope_txt = str(auth_val.get("scope") or "")
        if years and years >= 3:
            risks.append(_mk(
                "cmp-auth", "信息授权在合同终止后仍保留 %d 年" % years, "compliance",
                "高" if years >= 5 else "中", borrower, auth,
                "授权条款允许在合同终止后 %d 年内持续查询、留存主体信息，属于较长的数据留存安排。" % years,
                "企业敏感经营与征信数据长期暴露在机构内部流转中，存在后续授权范围扩大的合规隐患。",
                "核对授权范围与目的限定；到期后书面申请删除或销毁；对共享第三方条款做留痕管理。",
                0.85))
        if "共享" in scope_txt and ("第三" in scope_txt or "合作" in scope_txt):
            risks.append(_mk(
                "cmp-share", "信息可共享给第三方/合作机构", "compliance", "中", borrower, auth,
                "授权范围包含向合作机构共享主体信息，数据流转超出了单一交易对手。",
                "信息一旦共享，控制链条延长，出现数据滥用时难以溯源。",
                "在附件或补充协议中列明共享机构白名单与用途；建立数据资产台账。",
                0.8))
    # ---------- 操作与执行风险 ----------
    jur = fields.get("jurisdiction") or {}
    if jur.get("found"):
        jtxt = str(jur.get("value") or "")
        if any(k in jtxt for k in ("甲方", "贷款人", "原告", "债权人")) and "所在地" in jtxt:
            risks.append(_mk(
                "ops-jur", "管辖约定在对方（金融机构）所在地", "operation", "中", borrower, jur,
                "争议须在金融机构所在地法院诉讼/仲裁，融资方异地应诉的交通、代理成本更高。",
                "发生争议时维权成本上升，小额纠纷可能因成本放弃主张。",
                "协商改为合同签订地或被告住所地管辖；在成本模型中计入争议处理成本。",
                0.75, needs_legal_review=True))
    disp = fields.get("dispute_resolution") or {}
    if disp.get("found") and str(disp.get("value")) == "仲裁":
        risks.append(_mk(
            "ops-arb", "争议提交仲裁（一裁终局）", "operation", "中", borrower, disp,
            "约定仲裁解决且一裁终局，程序快但救济渠道有限（需法律复核）。",
            "仲裁裁决作出后基本没有上诉空间，败诉风险一次性锁定。",
            "评估仲裁机构与仲裁地选择；重要合同建议由法律顾问复核仲裁条款。",
            0.75, needs_legal_review=True))
    pay = fields.get("payment_method") or {}
    if pay.get("status") == "uncertain":
        risks.append(_mk(
            "ops-pay", "还款方式表述不明确", "operation", "低", borrower, pay,
            "未能可靠识别分期还款安排，系统未作先息后本等默认假设，可能影响现金流测算准确性。",
            "若实际还款结构与预期不同，每期现金流出可能发生变化，造成流动性误判。",
            "人工复核还款计划条款原文；确认结息日与还本安排后重新运行成本测算。",
            0.6))
    # 条款冲突
    consistency = parsed.get("consistency") or {}
    for i, c in enumerate(consistency.get("conflicts") or []):
        cat = {"amount": "economic", "rate": "economic", "term": "economic",
               "date": "operation", "party": "operation", "prepay": "economic",
               "rate_fix": "economic", "penalty": "credit"}.get(c.get("type"), "operation")
        risks.append(_mk(
            "conflict-%d" % i, c.get("title") or "条款冲突", cat,
            "高" if c.get("severity") == "high" else "中", "签约相关方", None,
            (c.get("explanation") or "")[:120],
            "冲突条款并存时，签署后容易就适用口径产生争议。",
            "签署前逐条核对冲突证据（见冲突详情），必要时以补充协议明确唯一口径。",
            0.9, clause=(c.get("evidences") or [{}])[0].get("clause"),
            source_text="；".join(str(e.get("source_text")) for e in (c.get("evidences") or [])[:2])[:120]))

    # 风险结论必须能在同一条证据中找到对应事实语义；
    # 目前对最容易误报的授权/追索/连带保证风险做硬校验，其余风险保留原有规则输出。
    validated = []
    for risk in risks:
        risk["evidence_status"] = "confirmed" if _evidence_consistent(risk) else "uncertain"
        if risk.get("id") in _EVIDENCE_REQUIREMENTS and risk["evidence_status"] != "confirmed":
            continue
        validated.append(risk)
    risks = validated
    risks.sort(key=lambda r: (LEVEL_ORDER.get(r["level"], 9), r["category"]))
    summary = {
        "total": len(risks),
        "high": len([r for r in risks if r["level"] == "高"]),
        "medium": len([r for r in risks if r["level"] == "中"]),
        "low": len([r for r in risks if r["level"] == "低"]),
        "categories": {CATEGORY_LABELS[c]: len([r for r in risks if r["category"] == c])
                       for c in CATEGORY_LABELS},
        "legal_review": len([r for r in risks if r.get("needs_legal_review")]),
    }
    return {"risks": risks, "summary": summary}
