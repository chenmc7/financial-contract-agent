# -*- coding: utf-8 -*-
"""ContractGuard 通用金融合同结构化解析框架（Universal Parser）

管线：合同解析 → 合同类型识别 → 通用字段抽取 → 专属字段扩展
     → 字段标准化 → 原文证据绑定 → （可选 LLM 补强）
     → 字段状态标注 → 跨条款一致性检查 → 解析诊断

设计原则：
1. 不针对单一合同类型写死字段：任何金融合同先做通用 23 字段抽取，再按类型扩展专属字段；
2. 兼容中文大写金额、括号数字、阿拉伯数字、百分比、基点、LPR 浮动、日期区间、自然语言期限；
3. 每个抽取结果保留 source_text（原文证据）、条款位置、页码、标准化值、confidence；
4. 字段状态区分 confirmed / uncertain / not_found / not_applicable / conflict，
   不把"系统识别失败"表述成"合同缺少要素"；
5. LLM 语义抽取仅在已配置时补齐规则未发现的字段，任何失败静默降级；
6. 解析诊断（diagnostics）内部量化本次解析质量，供高级模式展示。
"""
import re

from . import normalize as nz
from . import classifier as clf
from . import extract as ex
from . import consistency as consistency_mod
from . import transactions as tx
from .schemas import (UNIVERSAL_FIELDS, TYPE_FIELDS, NA_FIELDS, NA_NOTES,
                       STATUS_LABELS, UNCERTAIN_BELOW)
from .llm_extract import llm_enrich

PIPELINE_STAGES = [
    ("normalize", "文本清洗与版面结构还原"),
    ("classify", "合同类型识别"),
    ("universal", "通用字段抽取"),
    ("extend", "专属字段扩展"),
    ("standardize", "字段标准化"),
    ("evidence", "原文证据绑定"),
    ("llm", "LLM 语义补强"),
    ("status", "字段状态标注"),
    ("consistency", "跨条款一致性检查"),
    ("diagnostics", "解析诊断"),
]


def _assign_status(fields, type_key):
    """字段状态标注：区分 已确认 / 待复核 / 未发现 / 不适用。

    - found 且置信度达标 → confirmed
    - found 但置信度不足 → uncertain（候选内容存在但无法可靠判断）
    - 未 found 且该字段对当前类型无意义 → not_applicable（不作为缺失）
    - 未 found 且理论上可能存在 → not_found
    - conflict 由一致性检查模块写入，此处不覆盖。
    """
    na = NA_FIELDS.get(type_key, set())
    for f in fields.values():
        if f.get("status") == "conflict":
            continue
        if f.get("found"):
            f["status"] = "confirmed" if (f.get("confidence") or 0) >= UNCERTAIN_BELOW else "uncertain"
        elif f["key"] in na:
            f["status"] = "not_applicable"
            note = NA_NOTES.get(f["key"], "对当前合同类型不适用")
            f["note"] = note
            f["display"] = "不适用（%s）" % note
        else:
            f["status"] = "not_found"


def _overall_confidence(fields):
    """整体置信度 = 已发现字段置信度加权平均（核心字段权重更高）"""
    CORE = {"amount", "rate", "term", "payment_method", "parties"}
    num, den = 0.0, 0.0
    for f in fields.values():
        if not f.get("found"):
            continue
        w = 2.0 if f["key"] in CORE else 1.0
        num += w * (f.get("confidence") or 0.5)
        den += w
    if den == 0:
        return 0.0
    # 可发现字段覆盖率修正（不适用字段不计入分母）
    effective = [f for f in fields.values() if f.get("status") != "not_applicable"]
    coverage = len([f for f in effective if f.get("found")]) / max(len(effective), 1)
    return round(min(0.99, (num / den) * 0.75 + coverage * 0.25), 2)


def _locate_span(text, needle, offset=0):
    """在原始文档文本中定位证据；兼容 PDF/DOCX 抽取造成的空白差异。"""
    if not needle:
        return None
    direct = text.find(needle, offset)
    if direct >= 0:
        return direct, direct + len(needle)
    compact = re.sub(r"\s+", "", text[offset:])
    target = re.sub(r"\s+", "", str(needle))
    if not target:
        return None
    idx = compact.find(target)
    if idx < 0:
        return None
    positions = [i for i, ch in enumerate(text[offset:]) if not ch.isspace()]
    if idx >= len(positions):
        return None
    end_i = min(len(positions) - 1, idx + len(target) - 1)
    return offset + positions[idx], offset + positions[end_i] + 1


def _attach_evidence_spans(fields, raw_text):
    """把字段证据映射回唯一原文坐标，不改变左侧原文文本。"""
    paragraphs, pages = tx._paragraphs(raw_text or "")
    for field in fields.values():
        if not field.get("found"):
            continue
        # 优先用抽取器保留的原始锚点，再用展示证据兜底；展示证据可能已包含上下文，
        # 直接拿它定位会把多个相邻业务事实误合并为一个 span。
        candidates = [field.get("raw"), field.get("source_text")]
        hit = None
        for candidate in candidates:
            candidate = str(candidate or "").strip()
            if len(candidate) < 2:
                continue
            hit = _locate_span(raw_text or "", candidate)
            if hit:
                break
        if not hit:
            continue
        anchor_start, anchor_end = hit
        # 先以锚点找到页/段，再按原文标点扩展，确保 evidence_span 不从句中间截断。
        start, end = ex.expand_evidence_span(raw_text or "", anchor_start, anchor_end)
        para = next((p for p in paragraphs if p["start"] <= anchor_start < p["end"]), None)
        if not para and paragraphs:
            para = min(paragraphs, key=lambda p: abs(p["start"] - anchor_start))
        page = para.get("page") if para else field.get("page")
        span = {"start": start, "end": end, "page": page,
                "paragraph_id": para.get("paragraph_id") if para else None,
                "text": (raw_text or "")[start:end].strip()}
        field["evidence_span"] = span
        field["paragraph_id"] = span["paragraph_id"]
        if page:
            field["page"] = page
        # 左侧原文使用连续文本；右侧证据也以同一坐标生成，避免“字段重组原文”。
        field["source_text"] = ex._normalise_evidence(span["text"])
        # 可选上下文只记录真实段落坐标，不参与字段值抽取。
        context_start, context_end = ex._paragraph_bounds(raw_text or "", anchor_start, anchor_end)
        context_text = (raw_text or "")[context_start:context_end].strip()
        if context_text and (context_start != start or context_end != end):
            field["context_span"] = {"start": context_start, "end": context_end,
                                      "page": page, "paragraph_id": span["paragraph_id"],
                                      "text": ex._normalise_evidence(context_text)}
    return {"source": "parser_input", "text_length": len(raw_text or ""),
            "page_count": max([p.get("page", 1) for p in paragraphs] or [1]),
            "pages": pages, "paragraphs": paragraphs}


def _build_diagnostics(doc, type_info, fields, consistency, llm_status):
    """解析诊断：量化本次解析质量，供高级模式 / 开发模式展示。"""
    stats = {"total": len(fields)}
    for st in ("confirmed", "confirmed_by_user", "uncertain", "not_found", "not_applicable", "conflict"):
        stats[st] = len([f for f in fields.values() if f.get("status") == st])
    fees = (fields.get("fee_bearing") or {}).get("items") or []
    risk_found = len([f for f in fields.values()
                      if f.get("group") == "风险条款" and f.get("found")])
    low_conf = [{"key": f["key"], "label": f["label"], "confidence": f.get("confidence") or 0}
                for f in fields.values()
                if f.get("found") and (f.get("confidence") or 0) < UNCERTAIN_BELOW]
    conflicts = consistency.get("conflicts") or []

    lines = [
        "文件解析：成功（%d 个条款 / %d 页）" % (len(doc.get("sections", [])), doc.get("page_count", 1)),
        "合同类型：%s（%d%%）" % (type_info["label"], round(type_info["confidence"] * 100)),
        "字段识别：已确认 %d（其中用户确认 %d）/ 待复核 %d / 未发现 %d / 不适用 %d / 冲突 %d"
        % (stats["confirmed"] + stats["confirmed_by_user"], stats["confirmed_by_user"], stats["uncertain"], stats["not_found"],
           stats["not_applicable"], stats["conflict"]),
        "费用项目：%d" % len(fees),
        "风险条款：%d" % risk_found,
        "低置信度字段：%d" % len(low_conf),
        "冲突字段：%d" % stats["conflict"],
    ]
    return {
        "file_parse": "成功",
        "sections": len(doc.get("sections", [])),
        "pages": doc.get("page_count", 1),
        "contract_type": {"label": type_info["label"],
                          "confidence": type_info["confidence"]},
        "llm": llm_status,
        "field_stats": stats,
        "fee_items": len(fees),
        "risk_fields_found": risk_found,
        "low_confidence_fields": low_conf,
        "conflict_count": len(conflicts),
        "conflict_fields": sorted({c.get("field") for c in conflicts if c.get("field")}),
        "text": lines,
    }


def _guard_multi_transaction_fields(fields, transaction_index):
    """多交易文件不生成一个虚假的合同级本金/期限/利率。

    交易级候选仍完整保留在 ``transactions``；合同级字段只作为“待选择的候选”展示，
    下游引擎必须在用户选择 transaction_id 后再投影计算。
    """
    count = int((transaction_index or {}).get("count") or 0)
    if count <= 1:
        return
    for key in ("amount", "rate", "term", "payment_method",
                "actual_financing_amount", "single_financing_term"):
        field = fields.get(key)
        if not field or not field.get("found"):
            continue
        field["candidate_value"] = field.get("value")
        field["candidate_display"] = field.get("display")
        field["status"] = "uncertain"
        field["note"] = ("检测到 %d 笔交易，合同级字段未采用全文首个候选；"
                         "请先选择具体交易" % count)
        field["display"] = "检测到 %d 笔交易：请先选择具体交易" % count


def _map_legacy(fields, doc, raw_text):
    """把通用字段映射为下游引擎（CostLens/Scenario/Covenant/Diff）已消费的 legacy 结构。"""
    amt = fields.get("amount", {})
    rate = fields.get("rate", {})
    term = fields.get("term", {})
    pay = fields.get("payment_method", {})
    gua = fields.get("guarantee", {})
    fees_f = fields.get("fee_bearing", {})
    early = fields.get("early_termination", {})

    fees = fees_f.get("items") or (fees_f.get("value") if isinstance(fees_f.get("value"), list) else [])

    # 利率标准化：按口径统一换算为年化小数（供 CostLens/Scenario 计算）
    def _confirmed_value(field):
        """只有已确认字段才能进入合同级 legacy 值；候选留在 fields/transactions。"""
        if field.get("status") not in ("confirmed", "confirmed_by_user"):
            return None
        return field.get("value")

    rate_val = _confirmed_value(rate)
    rate_type = rate.get("rate_type")
    annual_rate = None
    if rate_val is not None:
        if rate_type in ("annual", "bp"):
            annual_rate = rate_val
        elif rate_type == "monthly":
            annual_rate = rate_val * 12
        elif rate_type in ("daily", "daily_wm"):
            annual_rate = rate_val * 360
    if annual_rate is not None and annual_rate >= 1:
        annual_rate = None  # 明显异常，交由人工复核

    term_val = _confirmed_value(term)

    repayment = _confirmed_value(pay)
    if repayment not in ("等额本息", "等额本金", "先息后本", "到期一次还本付息", "按季付息、分期还本"):
        # 未披露还款结构时保持空值，由 CostLens 返回“信息不足”，不作默认假设。
        repayment = None

    early_val = _confirmed_value(early) if isinstance(_confirmed_value(early), dict) else None
    if early_val:
        early_val = dict(early_val)
        early_val.setdefault("source", early.get("source_text") or early.get("raw") or "合同约定")

    dep = fields.get("deposit") or {}
    deposit = _confirmed_value(dep) if isinstance(_confirmed_value(dep), (int, float)) else None

    def _value(key):
        field = fields.get(key) or {}
        return _confirmed_value(field)

    return {
        "principal": _confirmed_value(amt),
        "principal_source": amt.get("source_text") or amt.get("raw"),
        "annual_rate": annual_rate,
        "rate_source": rate.get("source_text") or rate.get("raw"),
        "term_months": term_val,
        "term_source": term.get("source_text") or term.get("raw"),
        "term_role": term.get("role") or "actual_financing_term",
        "repayment": repayment,
        "guarantee": gua.get("display") if gua.get("status") in ("confirmed", "confirmed_by_user") else "未明确约定",
        "fees": fees,
        "deposit": deposit,
        "prepayment_penalty": early_val,
        "covenants": ex._f_covenants(doc),
        # 业务角色字段供 API/前端证据面板与 CostLens 可计算性检查使用。
        "credit_limit": _value("credit_limit"),
        "credit_term_months": _value("credit_term"),
        "actual_financing_amount": _value("actual_financing_amount") or (
            _confirmed_value(amt) if amt.get("role") == "actual_financing_amount" else None),
        "actual_financing_term": _value("single_financing_term"),
        "financing_party": _value("financing_party"),
        "factoring_bank": _value("factoring_bank"),
        "baseline_date": _value("baseline_date"),
        "financial_statement_date": _value("financial_statement_date"),
        "prepayment_allowed": _value("prepayment"),
        "clauses": [{"title": s["title"], "body": s["body"], "index": s["index"],
                     "page": s.get("page")}
                    for s in doc["sections"]],
    }


def parse_universal(raw_text, name="未命名合同", use_llm=None):
    """主入口：全类型金融合同通用结构化解析。

    返回 dict：
      - name / word_count / confidence（legacy 顶层）
      - contract_type: {key,label,confidence,evidence}
      - fields: OrderedDict key → field（通用 + 类型专属，含 status 三态/冲突标注）
      - consistency: {conflicts: [...], checked: [...]}（跨条款一致性检查）
      - diagnostics: 解析诊断（高级模式展示）
      - pipeline: 管线阶段记录
      - legacy 映射键：principal / annual_rate / term_months / repayment / guarantee /
        fees / prepayment_penalty / covenants / clauses（供四引擎直接消费）
    """
    # 1. 文本清洗与版面结构还原
    doc = nz.normalize_document(raw_text)
    # 2. 合同类型识别
    type_info = clf.classify(doc, fallback_name=name)
    # 3+4. 通用字段抽取 + 类型专属字段扩展（含 5 标准化与 6 证据绑定）
    fields = ex.extract_all(doc, type_info, name_hint=name)
    # 字段状态首轮标注（不适用字段不参与 LLM 补强）
    _assign_status(fields, type_info["key"])
    # 7. LLM 语义补强（可选）
    llm_status = "skipped"
    if use_llm is None or use_llm:
        try:
            _, llm_status = llm_enrich(fields, doc, type_info["label"])
        except Exception:
            llm_status = "llm_error"
        _assign_status(fields, type_info["key"])  # LLM 结果重新标注状态
    # 8. 跨条款一致性检查（金额/利率/期限/主体/违约责任等）
    document = _attach_evidence_spans(fields, raw_text)
    transaction_index = tx.build_transactions(raw_text, fields, type_info)
    _guard_multi_transaction_fields(fields, transaction_index)
    consistency = consistency_mod.run_consistency(doc, fields)
    # 9. 解析诊断
    diagnostics = _build_diagnostics(doc, type_info, fields, consistency, llm_status)
    # 整体置信度
    confidence = _overall_confidence(fields)
    legacy = _map_legacy(fields, doc, raw_text)
    if name and name != "未命名合同":
        fields["contract_name"]["value"] = fields["contract_name"].get("value") or name

    found_n = len([f for f in fields.values() if f.get("found")])
    return {
        "name": name,
        "contract_type": type_info,
        "fields": fields,
        "field_count": {"total": len(fields), "found": found_n,
                        "missing": len(fields) - found_n,
                        **diagnostics["field_stats"]},
        "consistency": consistency,
        "diagnostics": diagnostics,
        "pipeline": {"stages": [s[0] for s in PIPELINE_STAGES],
                     "llm": llm_status,
                     "sections": len(doc["sections"]),
                     "pages": doc.get("page_count", 1)},
        "document": document,
        "transactions": transaction_index.get("transactions", []),
        "transaction_count": transaction_index.get("count", 0),
        "document_type": (fields.get("document_type") or {}).get("value"),
        "transaction_type": (fields.get("transaction_type") or {}).get("value"),
        "confidence": confidence,
        "word_count": len(re.sub(r"\s", "", raw_text or "")),
        **legacy,
    }
