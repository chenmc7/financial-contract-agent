# -*- coding: utf-8 -*-
"""Agent Orchestrator：按合同类型与字段完整度自动调度分析工具。

用户规约第十五条：Agent 不是被动等用户挑工具，而是根据合同内容判断：
- 贷款类合同 → CostLens 直接运行；
- 担保合同 → 不调 CostLens（不产生融资现金流），自动隐藏而非报错；
- 两份合同 → ContractDiff 优先；
- 含财务契约 → 提示 CovenantGuard。

输出统一步骤结构：
  {tool, label, status, reason, detail, action}
  status ∈ run（执行）/ degraded（降级执行，列明缺项）/ skip（跳过，给业务理由）
skip 永远携带"为什么"，degraded 永远区分 not_found / not_applicable /
uncertain 三种缺因，不把"系统没算出来"表述成"合同缺少要素"。
"""
from .uparse.schemas import UNCERTAIN_BELOW
from .costlens import COST_TYPES, COST_SKIP_REASONS, missing_detail as _cost_missing_detail

# 类型不适用时的业务理由复用 CostLens 的定义（用户规约第八条：隐藏模块而非报错）
SKIP_REASONS = COST_SKIP_REASONS


def _step(tool, label, status, reason, detail=None, action=None):
    return {"tool": tool, "label": label, "status": status, "reason": reason,
            "detail": detail or [], "action": action}


def _cost_missing(parsed):
    """检查 CostLens 三要素缺项，返回 [(字段名, 缺因状态, 说明)]。"""
    return [(m["label"], m["status"], m["text"]) for m in _cost_missing_detail(parsed)]


def build_plan(parsed):
    """为单份已解析合同生成调度计划。

    parsed 为 parse_universal 的结果（含 contract_type / fields /
    consistency / diagnostics 与 legacy 键）。返回：
      {steps: [...], cost_ready: bool, advice: str}
    """
    type_key = ((parsed.get("contract_type") or {}).get("key")) or "general"
    type_label = ((parsed.get("contract_type") or {}).get("label")) or "其他金融合同"
    fields = parsed.get("fields") or {}
    steps = []

    # 1) 结构化解析：已随合同入库完成
    steps.append(_step(
        "parser", "结构化解析", "run",
        "已完成 %s 的字段抽取、状态标注与一致性检查" % type_label,
        detail=["字段总数 %d" % len(fields),
                "解析置信度 %d%%" % round((parsed.get("confidence") or 0) * 100)],
        action="view"))

    # 2) 六类风险识别：任何合同都执行
    conflicts = len(((parsed.get("consistency") or {}).get("conflicts")) or [])
    steps.append(_step(
        "riskengine", "风险识别（六类）", "run",
        "基于字段与条款证据输出经济成本/信用违约/担保/财务契约/合规数据/操作执行风险",
        detail=["跨条款冲突 %d 项" % conflicts] if conflicts else [],
        action="run"))

    # 3) CostLens：类型适用性 → 字段完整度
    if type_key in COST_TYPES:
        missing = _cost_missing(parsed)
        if not missing:
            steps.append(_step(
                "costlens", "CostLens 成本测算", "run",
                "金额/利率/期限要素齐备，直接执行综合成本率与 IRR 测算",
                action="run"))
            cost_ready = True
        else:
            detail = ["%s：%s" % (m[0], m[2]) for m in missing]
            steps.append(_step(
                "costlens", "CostLens 成本测算", "degraded",
                "融资要素不完整，暂无法计算综合成本；缺项见明细，补齐后可重算",
                detail=detail, action="manual"))
            cost_ready = False
    else:
        reason = SKIP_REASONS.get(type_key) or SKIP_REASONS["general"]
        steps.append(_step("costlens", "CostLens 成本测算", "skip", reason, action="hidden"))
        cost_ready = False

    # 4) CovenantGuard：识别到财务契约才调度
    covenants = parsed.get("covenants") or []
    if covenants:
        steps.append(_step(
            "covenant", "CovenantGuard 契约监控", "run",
            "识别到 %d 条财务契约，已转为机器可执行规则，可结合财务数据输出四态监控" % len(covenants),
            action="run"))
    else:
        steps.append(_step(
            "covenant", "CovenantGuard 契约监控", "skip",
            "未识别到财务契约条款（如资产负债率上限、流动比率下限），无需监控",
            action="hidden"))

    # 5) ContractDiff / ScenarioCompare：单份合同上下文给提示
    steps.append(_step(
        "diff", "ContractDiff 版本对比", "skip",
        "版本对比需要同一合同的新旧两份文本，请在合同库中选择两份后发起",
        action="need_more"))

    steps.append(_step(
        "scenario", "ScenarioCompare 情景比较", "skip",
        "情景比较至少需要两份可测算合同，请在合同库中勾选后发起",
        action="need_more"))

    # 综合建议
    advice_parts = []
    if type_key in COST_TYPES:
        if cost_ready:
            advice_parts.append("融资要素齐备，可直接运行 CostLens 与情景比较。")
        else:
            advice_parts.append("先补齐融资要素（见调度明细），CostLens 恢复可用后再测算。")
    else:
        advice_parts.append("该合同不适合成本测算，重点查看风险识别结果。")
    if covenants:
        advice_parts.append("存在财务契约，建议录入财务报表数据运行 CovenantGuard。")
    if conflicts:
        advice_parts.append("检测到 %d 项跨条款冲突，签约前需逐项核对双方原文。" % conflicts)

    return {"steps": steps, "cost_ready": cost_ready,
            "advice": " ".join(advice_parts)}


def review_items(parsed):
    """人工复核清单：把解析端不确定项汇聚成可操作列表。

    来源：uncertain 字段 / 冲突字段 / 低置信度类型识别 / 需法律复核风险。
    每项 {type, label, text, source}，source 指向字段或冲突记录。
    """
    from .riskengine import assess_risks

    fields = parsed.get("fields") or {}
    items = []
    for f in fields.values():
        if f.get("status") == "uncertain":
            note = str(f.get("note") or "")
            if note and ("检测到" in note or "合同级字段" in note):
                text = note
            elif (f.get("confidence") or 0) < UNCERTAIN_BELOW:
                text = "候选内容「%s」置信度 %.0f%%，低于阈值 %.0f%%" % (
                    (f.get("display") or f.get("raw") or "")[:40],
                    (f.get("confidence") or 0) * 100, UNCERTAIN_BELOW * 100)
            else:
                text = "候选内容「%s」的业务角色或归属尚未确认，请核对原文证据后选择采用值" % (
                    (f.get("display") or f.get("raw") or "")[:60])
            items.append({
                "type": "uncertain_field",
                "label": "字段待复核：%s" % f.get("label"),
                "text": text,
                "source": f.get("key"),
            })
        elif f.get("status") == "conflict":
            items.append({
                "type": "conflict_field",
                "label": "字段冲突：%s" % f.get("label"),
                "text": "跨条款内容不一致，请核对全部原文出处",
                "source": f.get("key"),
            })

    tinfo = parsed.get("contract_type") or {}
    if (tinfo.get("confidence") or 0) < 0.55:
        items.append({
            "type": "low_type_confidence",
            "label": "类型识别待确认",
            "text": "当前判定为「%s」（置信度 %d%%），建议人工确认合同类型后再使用类型专属分析" % (
                tinfo.get("label"), round((tinfo.get("confidence") or 0) * 100)),
            "source": "contract_type",
        })

    try:
        risks = assess_risks(parsed)
        for r in (risks.get("risks") or []):
            if r.get("needs_legal_review"):
                items.append({
                    "type": "legal_review",
                    "label": "需法律复核：%s" % r.get("title"),
                    "text": "%s（涉及法律效力判断，需专业法律人员复核）" % (r.get("explanation") or "")[:120],
                    "source": r.get("id"),
                })
    except Exception:
        pass

    return items


def orchestrate(parsed):
    """主入口：生成计划并实际执行当前上下文中可运行的确定性工具。"""
    from .riskengine import assess_risks
    from .costlens import analyze_cost

    plan = build_plan(parsed)
    review = review_items(parsed)
    try:
        risk = assess_risks(parsed)
    except Exception:
        risk = {"risks": [], "summary": {"total": 0, "high": 0, "medium": 0, "low": 0,
                                         "categories": {}, "legal_review": 0}}
    tool_results = {"riskengine": risk}
    if plan.get("cost_ready"):
        cost_result = analyze_cost(parsed)
        if not cost_result.get("hidden"):
            tool_results["costlens"] = cost_result
            for step in plan.get("steps", []):
                if step.get("tool") == "costlens":
                    step.setdefault("detail", []).append(
                        "已自动测算：总融资成本 %.2f 元，真实年化成本 %s" % (
                            cost_result.get("total_cost") or 0,
                            ("%.2f%%" % cost_result["real_annual_cost_pct"])
                            if cost_result.get("real_annual_cost_pct") is not None else "待复核"))
                    break
    if parsed.get("covenants"):
        tool_results["covenant"] = {
            "status": "awaiting_financials",
            "rule_count": len(parsed.get("covenants") or []),
            "reason": "契约规则已生成，等待用户上传或录入财务数据后执行阈值判断",
        }
    return {
        "plan": plan,
        "review": review,
        "risks": risk.get("risks", []),
        "risk_summary": risk.get("summary", {}),
        "tool_results": tool_results,
        "diagnostics": parsed.get("diagnostics") or {},
        "consistency": parsed.get("consistency") or {"conflicts": []},
    }
