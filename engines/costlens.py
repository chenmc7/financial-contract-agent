# -*- coding: utf-8 -*-
"""CostLens：真实融资成本分析引擎
- 重构每期现金流（等额本息/等额本金/先息后本/到期还本付息）
- 计算总利息、总费用、综合成本率（I+F+P)/N、真实年化成本（IRR）
- 保证金占用现金流：期初冻结、期末返还（无息），计入实际到手资金与 IRR
- 模块自动隐藏（用户规约第八条）：类型不适用 / 要素不完整时返回 hidden
  结构而非 error，前端据此隐藏卡片并展示业务原因
"""
import copy

# 产生融资现金流、适合成本测算的合同类型
COST_TYPES = {"loan", "personal_loan", "enterprise_loan", "leasing", "credit_card", "factoring", "credit"}

# 类型不适用时的业务理由（隐藏而非报错）
COST_SKIP_REASONS = {
    "guarantee": "担保合同不产生融资现金流，CostLens 不适用于该合同，已自动隐藏。"
                 "请重点查看担保范围、保证期间与连带责任风险。",
    "securities": "证券/衍生品类合同以履约保障与净额结算安排为主，"
                  "不适用融资成本测算，CostLens 已自动隐藏。",
    "wealth": "理财/资管合同无融资成本概念，CostLens 已自动隐藏；"
              "建议关注收益规则、风险等级与费用扣减安排。",
    "mortgage": "抵押合同本身不产生融资现金流，已跳过 Cost Lens；请重点审查抵押物权属、登记、顺位和处置条件。",
    "pledge": "质押合同本身不产生融资现金流，已跳过 Cost Lens；请重点审查质押物权属、交付登记、顺位和处置条件。",
    "insurance": "保险合同不适用融资成本测算，CostLens 已自动隐藏。",
    "general": "该合同未明确归类为融资类合同，CostLens 已自动隐藏；"
               "如确有借贷成分，可人工发起成本测算。",
}

# CostLens 三要素：字段 key → legacy 键 → 中文说明
COST_REQUIRED = [
    ("amount", "principal", "实际融资金额/本金"),
    ("rate", "annual_rate", "年化利率"),
    ("term", "term_months", "实际融资期限（月）"),
    ("payment_method", "repayment", "还款方式/现金流结构"),
]

_REPAYMENT_VALUES = {"等额本息", "等额本金", "先息后本", "到期一次还本付息", "按季付息、分期还本"}

# 只有这些业务角色可以进入现金流计算；授信额度、担保金额是"上限"或"敞口"，
# 不是企业实际到手的资金（用户规约：不把授信上限当成实际借款）。
PRINCIPAL_ROLES = {"actual_financing_amount", "loan_amount", "leasing_principal"}
TERM_ROLES = {"actual_financing_term", "loan_term", "leasing_term"}

ROLE_LABELS = {
    "credit_limit": "授信额度",
    "credit_term": "授信期限",
    "guarantee_amount": "担保金额",
    "guarantee_limit": "担保额度",
    "maximum_amount": "最高额",
}


def _role_text(role, fallback="未知角色"):
    return ROLE_LABELS.get(role) or (role or fallback)


def _usable_field(contract, field_key, legacy_key):
    """只有角色正确、状态可确认且已映射到 legacy 的字段才可进入计算。"""
    value = contract.get(legacy_key)
    if value in (None, ""):
        return False
    field = (contract.get("fields") or {}).get(field_key)
    if not field:
        # 兼容已有的内置测试对象；真实上传对象始终有 fields。
        return True
    status = field.get("status")
    if status not in ("confirmed", "confirmed_by_user"):
        return False
    # 人工确认过的字段以用户判断为准，不再被自动角色判定拦截。
    if status != "confirmed_by_user":
        role = field.get("role")
        if field_key == "amount":
            if role and role not in PRINCIPAL_ROLES:
                return False
            type_key = ((contract.get("contract_type") or {}).get("key"))
            if type_key == "factoring" and role != "actual_financing_amount":
                return False
        if field_key == "term" and role not in (None,) + tuple(TERM_ROLES):
            return False
    if field_key == "payment_method" and value not in _REPAYMENT_VALUES:
        return False
    return True


def project_transaction(contract, transaction_id):
    """将明确选择的交易投影为 CostLens 输入，防止跨交易借字段。"""
    transactions = contract.get("transactions") or []
    target = next((x for x in transactions if x.get("transaction_id") == transaction_id), None)
    if not target:
        return None
    out = copy.deepcopy(contract)
    out["selected_transaction_id"] = transaction_id
    out["transactions"] = [target]
    primary = target.get("primary") or {}
    amount = primary.get("amount") or {}
    term = primary.get("term") or {}
    fields = out.get("fields") or {}
    amount_field = fields.get("amount") or {}
    term_field = fields.get("term") or {}
    # 人工确认过的字段以用户判断为准，允许覆盖自动判定的业务角色。
    user_amount = amount_field.get("status") == "confirmed_by_user"
    user_term = term_field.get("status") == "confirmed_by_user"
    blocked = []

    amount_role = amount.get("role")
    if amount.get("value") is not None:
        if user_amount or amount_role is None or amount_role in PRINCIPAL_ROLES:
            out["principal"] = amount["value"]
            if amount_field:
                amount_field.update(value=amount["value"], found=True,
                                    status=amount_field.get("status") or "confirmed",
                                    role=amount_role or amount_field.get("role"))
        else:
            # 角色不是实际融资额（授信额度/担保金额等），禁止投影为本金。
            out["principal"] = None
            reason = "该笔交易的金额角色为「%s」，不能作为实际融资金额参与成本测算" % _role_text(amount_role)
            blocked.append({"field": "amount", "role": amount_role, "reason": reason})
            if amount_field:
                amount_field.update(value=None, found=True, status="uncertain",
                                    role=amount_role, note=reason)

    term_role = term.get("role")
    if term.get("value") is not None:
        if user_term or term_role is None or term_role in TERM_ROLES:
            out["term_months"] = term["value"]
            if term_field:
                term_field.update(value=term["value"], found=True,
                                  status=term_field.get("status") or "confirmed",
                                  role=term_role or term_field.get("role"))
        else:
            out["term_months"] = None
            reason = "该笔交易的期限角色为「%s」，不能作为实际融资期限参与成本测算" % _role_text(term_role)
            blocked.append({"field": "term", "role": term_role, "reason": reason})
            if term_field:
                term_field.update(value=None, found=True, status="uncertain",
                                  role=term_role, note=reason)
    else:
        out["term_months"] = None
        if term_field:
            term_field.update(value=None, status="uncertain", found=True,
                              role=term_field.get("role") or "actual_financing_term")
    if blocked:
        out["projection_blocked"] = blocked
    rate = primary.get("rate") or {}
    if rate.get("value") is not None:
        out["annual_rate"] = rate["value"]
    elif len(transactions) > 1:
        out["annual_rate"] = None
    # 多交易文档的还款结构若没有交易级证据，不能沿用全局值。
    if not target.get("repayment"):
        out["repayment"] = None
        if out.get("fields", {}).get("payment_method"):
            out["fields"]["payment_method"].update(value=None, found=False, status="not_found")
    return out


def monthly_rate(annual_rate, periods_per_year=12):
    return annual_rate / periods_per_year


def missing_detail(contract):
    """检查 CostLens 三要素缺项，返回 [{field, label, status, text}]。

    缺因三态（用户规约第七条）：
    - not_found     原文确实没有该要素
    - not_applicable 该要素对此合同类型无意义
    - uncertain      有候选但置信度不足 / 标准化失败，需人工复核
    """
    fields = contract.get("fields") or {}
    out = []
    transactions = contract.get("transactions") or []
    if len(transactions) > 1 and not contract.get("selected_transaction_id"):
        out.append({"field": "transaction_id", "label": "交易选择", "status": "uncertain",
                    "text": "检测到 %d 笔交易，请先选择具体交易，禁止跨交易组合金额、期限和利率" % len(transactions)})
    # 交易投影阶段已判定角色不适用（授信额度/授信期限等），直接给出业务原因，
    # 不再回退到"置信度不足"这类含糊描述。
    blocked = {b["field"]: b for b in (contract.get("projection_blocked") or [])}
    for fkey, legacy_key, label in COST_REQUIRED:
        if fkey in blocked:
            out.append({"field": fkey, "label": label, "status": "uncertain",
                        "text": blocked[fkey]["reason"]})
            continue
        if _usable_field(contract, fkey, legacy_key):
            continue
        f = fields.get(fkey)
        if not f or not f.get("found"):
            status = (f or {}).get("status") or "not_found"
            if status == "not_applicable":
                text = "%s对该类型合同不适用" % label
            elif status == "conflict":
                text = "%s存在跨条款冲突，需先核对双方原文再测算" % label
            else:
                text = "%s在原文中未发现，请核对合同正文或补充附件" % label
            out.append({"field": fkey, "label": label, "status": status, "text": text})
            continue
        # found 但 legacy 映射失败：置信度不足或标准化失败
        if (f.get("confidence") or 1) < 0.70 or f.get("status") == "uncertain":
            if fkey == "term" and f.get("role") == "actual_financing_term":
                text = "实际融资期限仅披露为上限或未确定到期日，不能用授信期限代替"
            else:
                text = "%s已识别到候选内容但置信度不足，需人工复核后再参与计算" % label
        elif fkey == "rate":
            text = "利率已识别但未能标准化为年化口径（口径异常或超出合理区间），需人工复核"
        else:
            text = "%s已识别但未能换算为可计算单位，需人工复核" % label
        out.append({"field": fkey, "label": label, "status": "uncertain", "text": text})
    return out


def build_cashflows(principal, annual_rate, term_months, repayment):
    """返回 [{period, interest, principal, total}] 企业视角：期初收到本金，每期支出"""
    r = monthly_rate(annual_rate)
    flows = []
    n = term_months
    if repayment == "等额本息":
        pay = principal * r * (1 + r) ** n / ((1 + r) ** n - 1) if r > 0 else principal / n
        bal = principal
        for t in range(1, n + 1):
            interest = bal * r
            princ = min(pay - interest, bal)
            bal -= princ
            flows.append({"period": t, "interest": round(interest, 2), "principal": round(princ, 2),
                          "total": round(interest + princ, 2), "balance": round(max(bal, 0), 2)})
    elif repayment == "等额本金":
        per_p = principal / n
        bal = principal
        for t in range(1, n + 1):
            interest = bal * r
            bal -= per_p
            flows.append({"period": t, "interest": round(interest, 2), "principal": round(per_p, 2),
                          "total": round(interest + per_p, 2), "balance": round(max(bal, 0), 2)})
    elif repayment == "到期一次还本付息":
        total_interest = principal * r * n
        for t in range(1, n + 1):
            flows.append({"period": t, "interest": 0, "principal": 0, "total": 0,
                          "balance": round(principal, 2)})
        flows[-1] = {"period": n, "interest": round(total_interest, 2), "principal": round(principal, 2),
                     "total": round(total_interest + principal, 2), "balance": 0}
    elif repayment == "按季付息、分期还本":
        # 简化为按季模型：季利率 = 年利率/4
        rq = annual_rate / 4
        nq = n // 3
        amort_start_q = 4  # 第二年起还本
        per_p = principal / max(nq - amort_start_q, 1) if nq > amort_start_q else principal / max(nq, 1)
        bal = principal
        for q in range(1, nq + 1):
            interest = bal * rq
            princ = per_p if q > amort_start_q else 0
            if q == nq:
                princ = bal
            bal -= princ
            flows.append({"period": q, "interest": round(interest, 2), "principal": round(princ, 2),
                          "total": round(interest + princ, 2), "balance": round(max(bal, 0), 2)})
    elif repayment == "先息后本":
        for t in range(1, n + 1):
            interest = principal * r
            princ = principal if t == n else 0
            flows.append({"period": t, "interest": round(interest, 2), "principal": round(princ, 2),
                          "total": round(interest + princ, 2),
                          "balance": round(principal if t < n else 0, 2)})
    else:
        raise ValueError("未识别还款方式，禁止套用默认现金流模型")
    return flows


def irr(cashflows, annualize=True, periods_per_year=12):
    """牛顿迭代求内部收益率（每期），再年化"""
    def npv(rate):
        return sum(cf / (1 + rate) ** (i + 1) for i, cf in enumerate(cashflows))
    lo, hi = -0.99, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2
        v = npv(mid)
        if abs(v) < 1e-7:
            break
        if npv(lo) * v <= 0:
            hi = mid
        else:
            lo = mid
    return mid * periods_per_year if annualize else mid


def _deposit_amount(contract):
    """保证金金额：优先 legacy deposit 键，其次租赁专属字段。"""
    dep = contract.get("deposit")
    if isinstance(dep, (int, float)) and dep > 0:
        return dep
    f = (contract.get("fields") or {}).get("deposit")
    if f and f.get("found") and isinstance(f.get("value"), (int, float)):
        return f["value"]
    return 0


def _input_evidence(contract):
    """关键计算输入统一证据结构；人工修正仅改变标准值，不删除原始证据。"""
    fields = contract.get("fields") or {}
    labels = (("amount", "实际融资金额/本金", "现金流本金"),
              ("rate", "固定年利率", "月利率 = 年利率 / 12"),
              ("term", "实际融资期限", "还款期数"),
              ("payment_method", "还款方式", "确定现金流模型"))
    out = []
    for key, label, role in labels:
        field = fields.get(key) or {}
        evidence = field.get("evidence") or []
        primary = evidence[0] if evidence else {}
        out.append({
            "field": key, "label": label, "normalized": field.get("display") or field.get("value"),
            "source_text": primary.get("source_text") or field.get("source_text") or "",
            "clause": primary.get("clause") or field.get("clause") or "",
            "page": primary.get("page") if primary.get("page") is not None else field.get("page"),
            "confidence": field.get("confidence"), "status": field.get("status") or "not_found",
            "method": field.get("method") or "rule_extraction", "formula_role": role,
            "correction_history": field.get("correction_history") or [],
        })
    return out


def analyze_cost(contract, extra=None):
    """CostLens 主入口。contract 为 parse_universal 的结果。

    返回三类结构：
    - 类型不适用 → {hidden: true, reason_code: "not_applicable", reason, missing_detail}
    - 要素不完整 → {hidden: true, reason_code: "insufficient_params", reason, missing_detail}
    - 正常测算   → 现金流 / 总成本 / 综合成本率 / IRR（口径不变，保证金只影响净到手与 IRR）
    """
    type_key = ((contract.get("contract_type") or {}).get("key")) or "general"
    if type_key not in COST_TYPES:
        return {"hidden": True, "reason_code": "not_applicable",
                "reason": COST_SKIP_REASONS.get(type_key, COST_SKIP_REASONS["general"]),
                "missing_detail": []}

    p = contract.get("principal")
    r = contract.get("annual_rate")
    n = contract.get("term_months")
    gaps = missing_detail(contract)
    if gaps or not p or r is None or not n:
        return {"hidden": True, "reason_code": "insufficient_params",
                "computable": False,
                "reason": "当前信息不足，暂无法唯一计算总融资成本。",
                "missing_detail": gaps,
                "inputs": _input_evidence(contract)}

    repayment = contract.get("repayment")
    flows = build_cashflows(p, r, n, repayment)
    periods_per_year = 4 if repayment == "按季付息、分期还本" else 12

    total_interest = sum(f["interest"] for f in flows)
    fees = contract.get("fees", [])
    total_fees = sum(f.get("amount") or 0 for f in fees)

    # 保证金占用：期初冻结、期末无息返还（占用成本体现在净到手与 IRR）
    deposit = _deposit_amount(contract)

    # 综合成本率（大纲简化公式）：(I + F + P) / N
    total_cost = total_interest + total_fees
    comprehensive_rate = total_cost / p if p else 0

    # 真实年化成本 IRR：期初净流入（扣费用与保证金），每期流出，期末返还保证金
    net_flow0 = p - total_fees - deposit
    cf = [net_flow0] + [-f["total"] for f in flows if f["total"] > 0]
    if deposit:
        cf[-1] += deposit  # 最后一期返还保证金
    real_annual = None
    try:
        real_annual = irr(cf, annualize=True, periods_per_year=periods_per_year)
    except Exception:
        real_annual = None

    nominal_total = p * r * (n / 12)
    cost_items = [{"name": "利息支出", "amount": round(total_interest, 2), "type": "interest",
                   "detail": "名义利率 %.2f%% × 本金 × 期限" % (r * 100)}]
    for f in fees:
        if f.get("amount"):
            fee_basis = ("费率 %.2f%%" % (f["rate"] * 100)) if f.get("rate") is not None else "合同约定固定金额"
            cost_items.append({"name": f["name"], "amount": round(f["amount"], 2), "type": "fee",
                               "detail": fee_basis + "，%s，%s" % (f.get("timing", ""), f.get("clause", "")),
                               "clause": f.get("clause"), "source": f.get("source")})
    if deposit:
        cost_items.append({"name": "保证金占用（期末返还）", "amount": round(deposit, 2), "type": "deposit",
                           "detail": "期初冻结 %s，期末无息返还；占用降低实际可用资金并推高 IRR" % ("%.0f 元" % deposit)})
    # 成本最高条款
    max_item = max(cost_items, key=lambda x: x["amount"]) if cost_items else None

    net_received = p - total_fees - deposit  # 企业实际到手资金（保证金被冻结）

    fee_uplift = total_fees / p if p else 0
    summary = "合同名义年利率 %.2f%%；按还款计划测算总利息 %s、固定费用 %s，期限内总融资成本率约 %.2f%%。" % (
        r * 100, "%.2f 元" % total_interest, "%.2f 元" % total_fees,
        comprehensive_rate * 100)
    if total_fees:
        summary += " 固定费用相当于本金的 %.2f%%。" % (fee_uplift * 100)
    if deposit:
        summary += "另有保证金 %.0f 元在期初冻结、期末返还，虽不计入总成本口径，但使实际到手资金降为 %s，真实年化成本（IRR）已包含该占用影响。" % (
            deposit, "%.0f 元" % net_received)

    return {
        "computable": True,
        "principal": p,
        "annual_rate": r,
        "term_months": n,
        "repayment": repayment,
        "cashflows": flows,
        "total_interest": round(total_interest, 2),
        "total_fees": round(total_fees, 2),
        "deposit": round(deposit, 2) if deposit else 0,
        "total_cost": round(total_cost, 2),
        "net_received": round(net_received, 2),
        "nominal_cost": round(nominal_total, 2),
        "comprehensive_rate": round(comprehensive_rate, 4),
        "comprehensive_rate_pct": round(comprehensive_rate * 100, 2),
        "real_annual_cost_pct": round(real_annual * 100, 2) if real_annual is not None else None,
        "rate_gap_pp": round(fee_uplift * 100, 2),
        "cost_items": cost_items,
        "inputs": _input_evidence(contract),
        "max_cost_item": max_item,
        "summary": summary,
    }
