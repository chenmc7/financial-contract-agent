# -*- coding: utf-8 -*-
"""CovenantGuard：财务契约监控引擎
- 契约在解析层已转为机器可执行规则：指标/比较方向(≤/≥)/阈值/频率/补救期/
  触发后果/计算公式/所需财务科目（engines/uparse/extract.py::_f_covenants）
- 结合财务数据计算指标值、阈值距离、四态状态：
  安全 / 接近阈值 / 已触发 / 无法判断（缺数据，并列明所需科目）
"""

# 指标 → 计算所需财务科目
REQUIRED_FIELDS = {
    "资产负债率": ["total_liabilities", "total_assets"],
    "流动比率": ["current_assets", "current_liabilities"],
    "速动比率": ["quick_assets", "current_liabilities"],
    "净资产": ["total_assets", "total_liabilities"],
}

FIELD_LABELS = {
    "total_liabilities": "总负债",
    "total_assets": "总资产",
    "current_assets": "流动资产",
    "current_liabilities": "流动负债",
    "quick_assets": "速动资产",
}


def compute_indicator(indicator, fin):
    if indicator == "资产负债率":
        if not fin.get("total_assets") or fin.get("total_liabilities") is None:
            return None
        return fin["total_liabilities"] / fin["total_assets"] * 100
    if indicator == "流动比率":
        if fin.get("current_assets") is None or not fin.get("current_liabilities"):
            return None
        return fin["current_assets"] / fin["current_liabilities"]
    if indicator == "速动比率":
        if fin.get("quick_assets") is None or not fin.get("current_liabilities"):
            return None
        return fin["quick_assets"] / fin["current_liabilities"]
    if indicator == "净资产":
        if fin.get("total_assets") is None or fin.get("total_liabilities") is None:
            return None
        return fin["total_assets"] - fin["total_liabilities"]
    return None


def judge_status(indicator, value, direction, threshold):
    """四态判定：安全 / 接近阈值 / 已触发 / 无法判断"""
    if value is None:
        return "无法判断"
    if direction == "max":
        if value > threshold:
            return "已触发"
        margin = (threshold - value) / threshold * 100
        return "接近阈值" if margin <= 10 else "安全"
    else:  # min
        if value < threshold:
            return "已触发"
        margin = (value - threshold) / threshold * 100
        return "接近阈值" if margin <= 10 else "安全"


def _missing_fields(indicator, fin):
    missing = []
    for key in REQUIRED_FIELDS.get(indicator, []):
        value = fin.get(key)
        if value is None or (key in {"total_assets", "current_liabilities"} and value <= 0):
            missing.append(key)
    return missing


def monitor(covenants, financials):
    """covenants: parse 结果（机器可执行规则）; financials: [{label, ...}] 多期财务数据"""
    results = []
    for cov in covenants:
        ind = cov["indicator"]
        thr = cov["threshold"]
        direction = cov["direction"]
        series = []
        missing_all = []
        for fin in financials:
            v = compute_indicator(ind, fin)
            missing = _missing_fields(ind, fin)
            status = judge_status(ind, v, direction, thr)
            series.append({
                "label": fin.get("label", ""),
                "value": round(v, 2) if v is not None else None,
                "status": status,
                **({"missing_fields": missing,
                    "missing_labels": [FIELD_LABELS.get(k, k) for k in missing],
                    "note": "缺少 %s，无法计算" % "、".join(FIELD_LABELS.get(k, k) for k in missing)
                    } if missing else {}),
            })
            if missing and missing not in missing_all:
                missing_all.append(missing)
        latest = series[-1] if series else None
        value = latest["value"] if latest else None
        status = latest["status"] if latest else "无法判断"
        distance = None
        if value is not None:
            distance = round(thr - value, 2) if direction == "max" else round(value - thr, 2)

        # 机器可执行规则完整透传（前端展示与后续自动化消费）
        rule = {
            "indicator": ind,
            "direction": direction,
            "operator": cov.get("operator") or ("<=" if direction == "max" else ">="),
            "threshold": thr,
            "frequency": cov.get("frequency"),
            "remedy_days": cov.get("remedy_days"),
            "consequence": cov.get("consequence"),
            "formula": cov.get("formula"),
            "required_data": cov.get("required_data") or REQUIRED_FIELDS.get(ind, []),
            "unit": cov.get("unit"),
            "clause": cov.get("clause"),
            "page": cov.get("page"),
            "source": cov.get("source"),
            "rule_text": "%s %s %s%s，%s检查" % (
                ind, cov.get("operator") or ("<=" if direction == "max" else ">="),
                thr, cov.get("unit") or "", cov.get("frequency") or "季度"),
        }
        rule.update({
            "value": value, "status": status, "distance": distance,
            "series": series,
            "advice": _advice(ind, status, distance, cov, value),
        })
        results.append(rule)
    return results


def _advice(indicator, status, distance, cov, value=None):
    remedy = cov.get("remedy_days")
    remedy_txt = ("应在 %d 日补救期内恢复" % remedy) if remedy else "应尽快恢复至合同约定水平"
    consequence = cov.get("consequence")
    if status == "无法判断":
        req = cov.get("required_data") or REQUIRED_FIELDS.get(indicator, [])
        labels = "、".join(FIELD_LABELS.get(k, k) for k in req)
        return "缺少财务数据（%s），无法计算该指标；请补录后重新监控。" % labels
    if status == "已触发":
        base = "契约已被突破（当前 %.2f，阈值 %s %s），%s。" % (
            value if value is not None else 0,
            cov.get("operator") or "<=", cov.get("threshold"), remedy_txt)
        if indicator == "资产负债率":
            return base + "可考虑：① 增资扩股补充净资产；② 提前偿还部分负债；③ 与贷款人协商豁免或修改条款。贷后人员应立即启动人工核查。"
        return base + "建议立即与债权人沟通补救方案，避免触发提前到期等合同后果。"
    if status == "接近阈值":
        return "指标距阈值仅 %s 个百分点，建议提前布局降杠杆/补充流动性，避免季度末被动触发。" % distance
    return "指标运行在安全区间，按 %s 频率持续监控即可。" % (cov.get("frequency") or "季度")


def summary(results):
    triggered = [r for r in results if r["status"] == "已触发"]
    near = [r for r in results if r["status"] == "接近阈值"]
    safe = [r for r in results if r["status"] == "安全"]
    unknown = [r for r in results if r["status"] == "无法判断"]
    level = "red" if triggered else ("amber" if near else "green")
    return {"triggered": len(triggered), "near": len(near),
            "safe": len(safe), "unknown": len(unknown), "level": level}
