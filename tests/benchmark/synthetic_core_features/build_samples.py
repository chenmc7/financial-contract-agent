#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the synthetic ContractGuard core-feature benchmark.

The files produced by this script are intentionally ordinary DOCX/JSON files so
they can be uploaded through the normal product workflow.  No production
parser behavior is special-cased for these samples.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from engines import costlens, scenario  # noqa: E402


# Use a font available in the macOS/LibreOffice render environment.  The
# previous Noto Sans CJK name was not installed there, which made Chinese
# glyphs render as tofu boxes in the QA PNGs.
FONT = "Hiragino Sans GB"
BLUE = RGBColor(31, 77, 120)
MUTED = RGBColor(90, 100, 115)
BLACK = RGBColor(25, 30, 35)


def _set_run_font(run, size=11, color=BLACK, bold=False, italic=False):
    run.font.name = FONT
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), FONT)
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), FONT)
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), FONT)
    run.font.size = Pt(size)
    run.font.color.rgb = color
    run.bold = bold
    run.italic = italic


def _style_document(doc: Document, case_id: str):
    section = doc.sections[0]
    section.top_margin = Inches(0.85)
    section.bottom_margin = Inches(0.8)
    section.left_margin = Inches(1.0)
    section.right_margin = Inches(1.0)
    section.header_distance = Inches(0.35)
    section.footer_distance = Inches(0.35)

    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal._element.rPr.rFonts.set(qn("w:ascii"), FONT)
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), FONT)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    normal.font.size = Pt(11)
    normal.font.color.rgb = BLACK
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.18

    for name, size, color, before, after in (
        ("Heading 1", 14, BLUE, 13, 5),
        ("Heading 2", 12, BLUE, 9, 4),
    ):
        style = doc.styles[name]
        style.font.name = FONT
        style._element.rPr.rFonts.set(qn("w:ascii"), FONT)
        style._element.rPr.rFonts.set(qn("w:hAnsi"), FONT)
        style._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = color
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True

    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    hr = header.add_run("ContractGuard · Synthetic Benchmark · ")
    _set_run_font(hr, 8.5, MUTED)
    hr2 = header.add_run(case_id)
    _set_run_font(hr2, 8.5, MUTED, bold=True)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fr = footer.add_run("仅用于研究与功能验证，不代表真实金融机构、企业或交易")
    _set_run_font(fr, 8.5, MUTED)

    props = doc.core_properties
    props.title = f"ContractGuard 合成测试样本 {case_id}"
    props.subject = "Synthetic Benchmark Sample"
    props.author = "ContractGuard Research Team"
    props.comments = "人工合成，仅用于 ContractGuard 功能验证。"


def _shade(paragraph, fill="E8EEF5"):
    ppr = paragraph._p.get_or_add_pPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), fill)
    ppr.append(shd)


def _add_title(doc, title, subtitle, case_id):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(3)
    r = p.add_run(title)
    _set_run_font(r, 20, BLUE, bold=True)
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p2.paragraph_format.space_after = Pt(12)
    r2 = p2.add_run(subtitle)
    _set_run_font(r2, 10.5, MUTED)
    note = doc.add_paragraph()
    note.paragraph_format.space_before = Pt(3)
    note.paragraph_format.space_after = Pt(12)
    note.paragraph_format.left_indent = Inches(0.08)
    note.paragraph_format.right_indent = Inches(0.08)
    _shade(note)
    rr = note.add_run(
        f"合成样本声明（{case_id}）：本文件由研究团队人工构造，仅用于 ContractGuard 的字段抽取、"
        "证据、成本、情景、契约和版本比较验证；不是真实金融机构合同、真实企业融资案例、真实公开交易或法律模板。"
    )
    _set_run_font(rr, 9.5, BLUE)


def _add_para(doc, text, *, bold_prefix=None, italic=False):
    p = doc.add_paragraph(style="Normal")
    if bold_prefix and text.startswith(bold_prefix):
        r1 = p.add_run(bold_prefix)
        _set_run_font(r1, 11, BLACK, bold=True)
        r2 = p.add_run(text[len(bold_prefix):])
        _set_run_font(r2, 11, BLACK, italic=italic)
    else:
        r = p.add_run(text)
        _set_run_font(r, 11, BLACK, italic=italic)
    return p


def _add_clause(doc, clause_id, title, body_lines):
    h = doc.add_paragraph(style="Heading 1")
    h.paragraph_format.keep_with_next = True
    r = h.add_run(title)
    _set_run_font(r, 14, BLUE, bold=True)
    for line in body_lines:
        _add_para(doc, line)


def _add_signature(doc, rows):
    doc.add_paragraph(style="Heading 1").add_run("签署栏")
    for label, value in rows:
        _add_para(doc, f"{label}：{value}")


def _new_doc(case_id, title, subtitle):
    doc = Document()
    _style_document(doc, case_id)
    _add_title(doc, title, subtitle, case_id)
    return doc


def _write_doc(doc, name):
    path = ROOT / name
    doc.save(path)
    return path


def _evidence(text, clause_id, paragraph_id, *, role=None):
    value = {"text": text, "section": clause_id, "clause_id": clause_id,
             "paragraph_id": paragraph_id, "page": None}
    if role:
        value["role"] = role
    return value


def _field(parser_key, label, value, evidence, *, role=None, status="confirmed", display=None, **extra):
    out = {
        "parser_field_key": parser_key,
        "label": label,
        "value": value,
        "display": display if display is not None else str(value),
        "status": status,
        "evidence": evidence,
    }
    if role:
        out["role"] = role
    out.update(extra)
    return out


def _calc_contract(principal, rate, term_months, repayment, fees, prepay_rate=0.0):
    parsed = {
        "contract_type": {"key": "enterprise_loan"},
        "principal": principal,
        "annual_rate": rate,
        "term_months": term_months,
        "repayment": repayment,
        "fees": fees,
        "prepayment_penalty": {"rate": prepay_rate} if prepay_rate else None,
        "fields": {
            "amount": {"found": True, "status": "confirmed", "role": "actual_financing_amount", "value": principal},
            "rate": {"found": True, "status": "confirmed", "role": "base_interest_rate", "value": rate},
            "term": {"found": True, "status": "confirmed", "role": "loan_term", "value": term_months},
            "payment_method": {"found": True, "status": "confirmed", "value": repayment},
        },
    }
    return parsed


def build_sample_a():
    case = "A"
    doc = _new_doc(case, "标准企业流动资金借款合同", "ContractGuard 合成测试样本 A · CostLens / ScenarioCompare")
    _add_para(doc, "贷款人（甲方）：启明商业银行股份有限公司")
    _add_para(doc, "借款人（乙方）：华辰科技有限公司")
    _add_para(doc, "签署日期：2026年6月1日")
    _add_clause(doc, "A.c1", "第一条 合同性质与用途", [
        "本合同为企业流动资金贷款合同，贷款资金仅用于华辰科技有限公司的日常经营周转，不得用于证券投资、房地产开发或偿还本合同之外的债务。"
    ])
    _add_clause(doc, "A.c2", "第二条 实际借款金额与放款", [
        "本合同项下实际借款金额（借款本金）为人民币20,000,000元。",
        "贷款人应于2026年6月5日一次性将全部借款金额划入借款人指定账户；2026年6月5日为本笔贷款的放款日。"
    ])
    _add_clause(doc, "A.c3", "第三条 借款期限与到期", [
        "借款期限为18个月，自2026年6月5日放款日起计算，借款到期日为2027年12月4日。",
        "除本合同约定的提前还款或提前到期情形外，借款人应在到期日清偿全部剩余本金和应付利息。"
    ])
    _add_clause(doc, "A.c4", "第四条 利率与计息", [
        "本合同执行固定年利率4.20%，在合同有效期间不因市场利率变化而调整。",
        "利息自放款日起按实际占用本金计算，计息基础为360日。"
    ])
    _add_clause(doc, "A.c5", "第五条 还款与付息", [
        "借款人按月付息、到期一次还本（先息后本），每月5日支付上月利息，最后一期随本金一并结清。",
        "第一期付息日为2026年7月5日，最后一期付息及还本日为2027年12月4日。"
    ])
    _add_clause(doc, "A.c6", "第六条 融资服务费", [
        "借款人应在放款日一次性向贷款人支付融资服务费人民币80,000元，该费用不计入借款本金。"
    ])
    _add_clause(doc, "A.c7", "第七条 提前还款", [
        "借款人可以提前还款，但应至少提前10个工作日书面通知贷款人。",
        "如借款人在放款日起12个月内提前结清，应按提前结清本金的1.00%支付提前结清违约金；放款满12个月后提前结清不收取该项违约金。"
    ])
    _add_clause(doc, "A.c8", "第八条 逾期与违约责任", [
        "借款人未按期支付本金或利息的，逾期罚息按本合同固定年利率上浮50%计收；逾期期间另按逾期金额每日万分之五支付违约金。",
        "借款人连续逾期超过30日的，贷款人有权宣布全部借款提前到期，并要求借款人立即清偿本金、利息、罚息、违约金及实现债权的费用。"
    ])
    _add_clause(doc, "A.c9", "第九条 争议解决与其他", [
        "本合同适用中华人民共和国法律。因本合同产生的争议，双方协商不成的，提交贷款人住所地有管辖权的人民法院诉讼解决。",
        "本合同未尽事宜由双方另行签署书面补充协议；补充协议与本合同具有同等法律效力。"
    ])
    _add_signature(doc, [("贷款人（甲方）", "启明商业银行股份有限公司（盖章）"),
                         ("借款人（乙方）", "华辰科技有限公司（盖章）"),
                         ("签署日期", "2026年6月1日")])
    path = _write_doc(doc, "sample_A_standard_loan.docx")

    fees = [{"name": "融资服务费", "amount": 80_000, "rate": 0.004, "timing": "一次性支付"}]
    parsed = _calc_contract(20_000_000, 0.042, 18, "先息后本", fees, 0.01)
    base = costlens.analyze_cost(parsed)
    scenarios = {
        "base": scenario.simulate_one(parsed, "hold", {}),
        "rate_up_1pp": scenario.simulate_one(parsed, "rate_up", {"delta_pp": 1.0}),
        "prepay_month_9": scenario.simulate_one(parsed, "prepay", {"month": 9}),
    }
    gt = {
        "case_id": "A",
        "sample_type": "标准企业借款合同",
        "synthetic": True,
        "file": path.name,
        "document_type": "原始合同",
        "transaction_type": "贷款",
        "parties": {"lender": "启明商业银行股份有限公司", "borrower": "华辰科技有限公司"},
        "fields": {
            "amount": _field("amount", "实际融资金额/本金", 20_000_000,
                              [_evidence("本合同项下实际借款金额（借款本金）为人民币20,000,000元。", "A.c2", "A.c2.p1")], role="actual_financing_amount"),
            "actual_financing_amount": {"parser_field_key": "amount", "value": 20_000_000, "amount_role": "actual_financing_amount"},
            "rate": _field("rate", "正常融资利率", 0.042,
                            [_evidence("本合同执行固定年利率4.20%，在借款期限内不因市场利率变化而调整。", "A.c4", "A.c4.p1")], role="base_interest_rate", display="4.20%/年"),
            "base_interest_rate": {"parser_field_key": "rate", "value": 0.042, "rate_role": "base_interest_rate"},
            "term": _field("term", "实际借款期限", 18,
                            [_evidence("借款期限为18个月，自2026年6月5日放款日起计算，借款到期日为2027年12月4日。", "A.c3", "A.c3.p1")], role="loan_term", display="18个月"),
            "drawdown_date": _field("effective_date", "放款日", "2026-06-05",
                                     [_evidence("贷款人应于2026年6月5日一次性将全部借款金额划入借款人指定账户；2026年6月5日为本笔贷款的放款日。", "A.c2", "A.c2.p2")], role="drawdown_date"),
            "maturity_date": _field("expiry_date", "到期日", "2027-12-04",
                                     [_evidence("借款期限为18个月，自2026年6月5日放款日起计算，借款到期日为2027年12月4日。", "A.c3", "A.c3.p1")], role="maturity_date"),
            "payment_method": _field("payment_method", "还款方式", "先息后本",
                                      [_evidence("借款人按月付息、到期一次还本（先息后本），每月5日支付上月利息，最后一期随本金一并结清。", "A.c5", "A.c5.p1")], role="cashflow_structure"),
            "service_fee": _field("fee_bearing", "融资服务费", 80_000,
                                   [_evidence("借款人应在放款日一次性向贷款人支付融资服务费人民币80,000元，该费用不计入借款本金。", "A.c6", "A.c6.p1")], role="service_fee"),
            "prepayment_penalty": _field("early_termination", "提前结清违约金率", 0.01,
                                          [_evidence("如借款人在放款日起12个月内提前结清，应按提前结清本金的1.00%支付提前结清违约金；放款满12个月后提前结清不收取该项违约金。", "A.c7", "A.c7.p2")], role="prepayment_penalty"),
            "penalty_rate": _field("breach_liability", "逾期罚息/违约金", "年利率上浮50%；每日万分之五",
                                    [_evidence("借款人未按期支付本金或利息的，逾期罚息按本合同固定年利率上浮50%计收；逾期期间另按逾期金额每日万分之五支付违约金。", "A.c8", "A.c8.p1")], role="penalty_rate"),
        },
        "expected_evidence": {
            "rule": "证据应是完整句或完整款，不使用固定字符窗口；DOCX 不伪造页码。",
            "anchors": ["A.c2.p1", "A.c3.p1", "A.c4.p1", "A.c5.p1", "A.c6.p1", "A.c7.p2", "A.c8.p1"],
        },
        "expected_costlens_state": {
            "computable": True,
            "inputs": {"actual_financing_amount": 20_000_000, "base_interest_rate": 0.042, "term_months": 18, "repayment": "先息后本", "service_fee": 80_000},
            "calculation": {"formula": "利息=本金×年利率×18/12；总融资成本=利息+融资服务费；提前结清违约金不计入基准情景。", "base": base},
        },
        "expected_scenario_behavior": {
            "contract_facts": {"rate": 0.042, "term_months": 18, "prepayment_penalty_rate": 0.01},
            "scenarios": scenarios,
            "note": "利率上升和提前还款参数均为用户情景假设，不修改合同原始事实。",
        },
        "expected_covenants": [],
        "expected_contract_diff": None,
        "expected_status": "confirmed",
    }
    return gt


def build_sample_b():
    case = "B"
    doc = _new_doc(case, "带财务契约的企业流动资金贷款合同", "ContractGuard 合成测试样本 B · CovenantGuard / CostLens")
    _add_para(doc, "贷款人（甲方）：澄海商业银行股份有限公司")
    _add_para(doc, "借款人（乙方）：远川能源有限公司")
    _add_para(doc, "签署日期：2026年6月10日")
    _add_clause(doc, "B.c1", "第一条 贷款金额与放款", [
        "本合同项下实际借款金额（借款本金）为人民币15,000,000元。",
        "贷款人于2026年6月15日一次性放款，2026年6月15日为本笔贷款的放款日。"
    ])
    _add_clause(doc, "B.c2", "第二条 期限与利率", [
        "借款期限为24个月，自2026年6月15日放款日起计算，借款到期日为2028年6月14日。",
        "本合同执行固定年利率4.50%，在合同有效期间不调整。"
    ])
    _add_clause(doc, "B.c3", "第三条 还款与费用", [
        "借款人按月付息、到期一次还本（先息后本），每月15日支付利息。",
        "借款人应在放款日一次性支付融资服务费人民币45,000元。"
    ])
    _add_clause(doc, "B.c4", "第四条 财务契约与监测", [
        "借款人应确保每一季度末资产负债率不得超过70%，贷款人每季度检查一次；如突破，借款人应在30日内恢复，贷款人有权要求提前到期。",
        "借款人应确保每一季度末流动比率不得低于1.20，贷款人每季度检查一次；如突破，借款人应在30日内恢复。",
        "借款人应确保每一季度末利息保障倍数（EBITDA/利息费用）不得低于2.50，贷款人每季度检查一次；如突破，贷款人有权要求补充担保。",
        "借款人应确保每一季度末净资产不得低于50,000,000元，贷款人每季度检查一次；如突破，借款人应在30日内补足。"
    ])
    _add_clause(doc, "B.c5", "第五条 违约责任", [
        "借款人未按期支付本金或利息的，逾期罚息按固定年利率上浮50%计收，并按逾期金额每日万分之五支付违约金。",
        "借款人违反任一财务契约且未在补救期内恢复的，贷款人有权宣布全部借款提前到期。"
    ])
    _add_clause(doc, "B.c6", "第六条 其他", [
        "借款人应按季度向贷款人提交未经审计的资产负债表、利润表、现金流量表及契约计算表。",
        "本合同适用中华人民共和国法律，争议提交贷款人住所地有管辖权的人民法院解决。"
    ])
    _add_signature(doc, [("贷款人（甲方）", "澄海商业银行股份有限公司（盖章）"),
                         ("借款人（乙方）", "远川能源有限公司（盖章）"),
                         ("签署日期", "2026年6月10日")])
    path = _write_doc(doc, "sample_B_covenant_loan.docx")

    fees = [{"name": "融资服务费", "amount": 45_000, "rate": 0.003, "timing": "一次性支付"}]
    parsed = _calc_contract(15_000_000, 0.045, 24, "先息后本", fees)
    base = costlens.analyze_cost(parsed)
    gt = {
        "case_id": "B",
        "sample_type": "带财务契约的企业贷款合同",
        "synthetic": True,
        "file": path.name,
        "financial_data_file": "sample_B_financial_data.json",
        "financial_data_missing_file": "sample_B_financial_data_missing.json",
        "document_type": "原始合同",
        "transaction_type": "贷款",
        "parties": {"lender": "澄海商业银行股份有限公司", "borrower": "远川能源有限公司"},
        "fields": {
            "amount": _field("amount", "实际融资金额/本金", 15_000_000, [_evidence("本合同项下实际借款金额（借款本金）为人民币15,000,000元。", "B.c1", "B.c1.p1")], role="actual_financing_amount"),
            "rate": _field("rate", "正常融资利率", 0.045, [_evidence("本合同执行固定年利率4.50%，在借款期限内不调整。", "B.c2", "B.c2.p2")], role="base_interest_rate", display="4.50%/年"),
            "term": _field("term", "实际借款期限", 24, [_evidence("借款期限为24个月，自2026年6月15日放款日起计算，借款到期日为2028年6月14日。", "B.c2", "B.c2.p1")], role="loan_term", display="24个月"),
            "payment_method": _field("payment_method", "还款方式", "先息后本", [_evidence("借款人按月付息、到期一次还本（先息后本），每月15日支付利息。", "B.c3", "B.c3.p1")], role="cashflow_structure"),
            "service_fee": _field("fee_bearing", "融资服务费", 45_000, [_evidence("借款人应在放款日一次性支付融资服务费人民币45,000元。", "B.c3", "B.c3.p2")], role="service_fee"),
        },
        "expected_evidence": {
            "covenant_clause_ids": ["B.c4.p1", "B.c4.p2", "B.c4.p3", "B.c4.p4"],
            "rule": "每项契约的证据应包含指标、方向、阈值和检查频率；利息保障倍数还用于暴露当前解析器覆盖差异。",
        },
        "expected_costlens_state": {"computable": True, "inputs": {"actual_financing_amount": 15_000_000, "base_interest_rate": 0.045, "term_months": 24, "repayment": "先息后本", "service_fee": 45_000}, "calculation": {"formula": "利息=本金×年利率×24/12；总融资成本=利息+融资服务费。", "base": base}},
        "expected_scenario_behavior": {"contract_facts": {"rate": 0.045, "term_months": 24}, "allowed": True, "note": "利率、期限或提前还款比较必须显示为用户情景假设。"},
        "expected_covenants": [
            {"indicator": "资产负债率", "operator": "<=", "threshold": 70, "unit": "%", "frequency": "季度", "required_data": ["total_liabilities", "total_assets"], "current_engine_supported": True, "evidence": _evidence("资产负债率不得超过70%，贷款人每季度检查一次；如突破，借款人应在30日内恢复，贷款人有权要求提前到期。", "B.c4", "B.c4.p1")},
            {"indicator": "流动比率", "operator": ">=", "threshold": 1.20, "unit": "", "frequency": "季度", "required_data": ["current_assets", "current_liabilities"], "current_engine_supported": True, "evidence": _evidence("流动比率不得低于1.20，贷款人每季度检查一次；如突破，借款人应在30日内恢复。", "B.c4", "B.c4.p2")},
            {"indicator": "利息保障倍数", "operator": ">=", "threshold": 2.50, "unit": "倍", "frequency": "季度", "required_data": ["ebitda", "interest_expense"], "current_engine_supported": False, "support_note": "当前 CovenantGuard Schema 未定义 EBITDA/利息费用字段；该条款应在基准测试中明确标记为待扩展，而非伪造判断。", "evidence": _evidence("利息保障倍数（EBITDA/利息费用）不得低于2.50，贷款人每季度检查一次；如突破，贷款人有权要求补充担保。", "B.c4", "B.c4.p3")},
            {"indicator": "净资产", "operator": ">=", "threshold": 50_000_000, "unit": "元", "frequency": "季度", "required_data": ["total_assets", "total_liabilities"], "current_engine_supported": True, "evidence": _evidence("净资产不得低于50,000,000元，贷款人每季度检查一次；如突破，借款人应在30日内补足。", "B.c4", "B.c4.p4")},
        ],
        "expected_contract_diff": None,
        "expected_status": "confirmed_with_covenant_extension_pending",
    }
    fin = {
        "case_id": "B-FIN-01", "synthetic": True, "for_sample": "B",
        "schema": "ContractGuard CovenantFin（当前引擎字段）",
        "financials": [
            {"label": "2026Q1", "total_liabilities": 40_000_000, "total_assets": 100_000_000, "current_assets": 40_000_000, "current_liabilities": 25_000_000, "quick_assets": 35_000_000, "ebitda": 18_000_000, "interest_expense": 6_000_000},
            {"label": "2026Q2", "total_liabilities": 75_000_000, "total_assets": 100_000_000, "current_assets": 20_000_000, "current_liabilities": 25_000_000, "quick_assets": 16_000_000, "ebitda": 8_000_000, "interest_expense": 4_000_000},
        ],
        "expected_series": {
            "2026Q1": {"资产负债率": "PASS", "流动比率": "PASS", "利息保障倍数": "PASS", "净资产": "PASS"},
            "2026Q2": {"资产负债率": "BREACH", "流动比率": "BREACH", "利息保障倍数": "BREACH", "净资产": "BREACH"},
        },
        "note": "ebitda/interest_expense 保留在 Ground Truth 供扩展测试；当前上传接口会按现有 CovenantFin Schema 忽略这两个字段。",
    }
    missing = {
        "case_id": "B-FIN-MISSING", "synthetic": True, "for_sample": "B",
        "financials": [{"label": "2026Q2", "total_assets": 100_000_000}],
        "expected_behavior": "资产负债率、流动比率、净资产均返回 UNKNOWN/无法判断；不得把缺失科目当成0。利息保障倍数因当前引擎未支持而标记为未覆盖。",
    }
    return gt, fin, missing


def build_sample_c():
    doc = _new_doc("C", "企业借款合同", "ContractGuard 合成测试样本 C · ContractDiff old contract")
    _add_para(doc, "贷款人（甲方）：启明商业银行股份有限公司")
    _add_para(doc, "借款人（乙方）：华辰科技有限公司")
    _add_para(doc, "合同编号：HC-LOAN-2026-001")
    _add_para(doc, "签署日期：2026年5月20日")
    _add_clause(doc, "C.c1", "第一条 借款金额与放款", [
        "本合同项下实际借款金额（借款本金）为人民币30,000,000元。",
        "贷款人于2026年5月25日一次性放款，2026年5月25日为本笔贷款的放款日。"
    ])
    _add_clause(doc, "C.c2", "第二条 借款期限与到期", [
        "借款期限为24个月，自2026年5月25日放款日起计算，借款到期日为2028年5月24日。"
    ])
    _add_clause(doc, "C.c3", "第三条 利率与还款", [
        "本合同执行固定年利率4.20%，按月付息、到期一次还本（先息后本）。"
    ])
    _add_clause(doc, "C.c4", "第四条 担保方式", [
        "远川能源有限公司为本合同提供一般保证，保证期间为主债务履行期限届满之日起两年。"
    ])
    _add_clause(doc, "C.c5", "第五条 提前还款与违约责任", [
        "借款人提前还款无需支付提前结清违约金，但应提前10个工作日通知贷款人。",
        "借款人逾期的，按逾期金额每日万分之5支付违约金；逾期超过30日的，贷款人有权宣布借款提前到期。"
    ])
    _add_clause(doc, "C.c6", "第六条 争议解决", [
        "本合同适用中华人民共和国法律，争议提交贷款人住所地有管辖权的人民法院解决。"
    ])
    _add_signature(doc, [("贷款人（甲方）", "启明商业银行股份有限公司（盖章）"), ("借款人（乙方）", "华辰科技有限公司（盖章）")])
    path = _write_doc(doc, "sample_C_original_contract.docx")
    return path


def build_sample_d():
    doc = _new_doc("D", "企业借款合同补充协议", "ContractGuard 合成测试样本 D · ContractDiff amended contract")
    _add_para(doc, "本补充协议系《企业借款合同》（合同编号：HC-LOAN-2026-001）的补充协议。除本协议明确修改的条款外，原合同其他约定继续有效。")
    _add_para(doc, "贷款人（甲方）：启明商业银行股份有限公司")
    _add_para(doc, "借款人（乙方）：华辰科技有限公司")
    _add_para(doc, "签署日期：2026年8月20日")
    _add_clause(doc, "D.c1", "第一条 借款金额与放款（修订）", [
        "本补充协议生效后，本合同项下调整后的实际借款金额（借款本金）为人民币35,000,000元。",
        "新增借款部分于2026年8月25日放款，原借款关系的其他放款安排继续有效。"
    ])
    _add_clause(doc, "D.c2", "第二条 借款期限与到期（修订）", [
        "本合同调整后的借款期限为36个月，自2026年5月25日首次放款日起计算，调整后的借款到期日为2029年5月24日。"
    ])
    _add_clause(doc, "D.c3", "第三条 利率与还款（修订）", [
        "本合同调整后的固定年利率为5.00%，仍按月付息、到期一次还本（先息后本）。"
    ])
    _add_clause(doc, "D.c4", "第四条 担保方式（修订）", [
        "远川能源有限公司改为对本合同承担连带责任保证，保证期间为主债务履行期限届满之日起三年。"
    ])
    _add_clause(doc, "D.c5", "第五条 提前还款与违约责任（修订）", [
        "借款人提前还款仍无需支付提前结清违约金，但应提前10个工作日通知贷款人。",
        "借款人逾期的，按逾期金额每日万分之8支付违约金；逾期超过15日的，贷款人有权宣布借款提前到期。"
    ])
    _add_clause(doc, "D.c6", "第六条 争议解决", [
        "本合同适用中华人民共和国法律，争议提交贷款人住所地有管辖权的人民法院解决。"
    ])
    _add_signature(doc, [("贷款人（甲方）", "启明商业银行股份有限公司（盖章）"), ("借款人（乙方）", "华辰科技有限公司（盖章）")])
    path = _write_doc(doc, "sample_D_amendment.docx")
    return path


def build_sample_e():
    doc = _new_doc("E", "综合授信协议", "ContractGuard 合成测试样本 E · 信息不完整 / 正确降级")
    _add_para(doc, "授信人（甲方）：启明商业银行股份有限公司")
    _add_para(doc, "受信人（乙方）：星瀚制造有限公司")
    _add_para(doc, "签署日期：2026年7月1日")
    _add_clause(doc, "E.c1", "第一条 授信额度与有效期", [
        "本协议核定综合授信额度为人民币50,000,000元。",
        "额度有效期自2026年7月1日至2028年6月30日，额度有效期届满后未使用额度自动失效。"
    ])
    _add_clause(doc, "E.c2", "第二条 利率机制", [
        "每笔用信的利率机制为1年期LPR加80个基点，具体执行利率在每次提款时由双方在单笔借款合同中确定。"
    ])
    _add_clause(doc, "E.c3", "第三条 用信条件", [
        "本协议仅约定可用额度及用信条件，未约定任何实际提款金额、实际提款日、单笔融资期限、还本付息方式或完整现金流。",
        "受信人可在额度有效期内申请提款；每笔提款须另行签署借款合同，实际融资金额和期限以单笔借款合同及放款凭证为准。"
    ])
    _add_clause(doc, "E.c4", "第四条 信息不足时的处理", [
        "在未签署单笔借款合同或未取得放款凭证前，任何成本、利息或提前还款测算均只能作为受信人主动设定的情景，不得视为本协议已经发生的融资事实。"
    ])
    _add_clause(doc, "E.c5", "第五条 其他", [
        "本协议不构成对任何单笔借款必然放款的承诺，双方的权利义务以实际签署的单笔合同为准。"
    ])
    _add_signature(doc, [("授信人（甲方）", "启明商业银行股份有限公司（盖章）"), ("受信人（乙方）", "星瀚制造有限公司（盖章）")])
    path = _write_doc(doc, "sample_E_incomplete_credit_agreement.docx")
    return path


def build_diff_gt():
    old = _calc_contract(30_000_000, 0.042, 24, "先息后本", [], 0.0)
    new = _calc_contract(35_000_000, 0.05, 36, "先息后本", [], 0.0)
    return {
        "case_id": "C-D",
        "synthetic": True,
        "old_file": "sample_C_original_contract.docx",
        "new_file": "sample_D_amendment.docx",
        "document_type": {"old": "原始合同", "new": "补充协议"},
        "transaction_type": {"old": "贷款", "new": "贷款"},
        "same_contract_relationship": "D 明确引用合同编号 HC-LOAN-2026-001；相同贷款人与借款人。",
        "expected_contract_diff": [
            {"field_name": "amount", "parser_field_key": "principal", "old_value": 30_000_000, "new_value": 35_000_000, "change_type": "increased", "old_evidence": _evidence("本合同项下实际借款金额（借款本金）为人民币30,000,000元。", "C.c1", "C.c1.p1"), "new_evidence": _evidence("本补充协议生效后，本合同项下调整后的实际借款金额（借款本金）为人民币35,000,000元。", "D.c1", "D.c1.p1"), "change_summary": "融资金额增加500万元。"},
            {"field_name": "rate", "parser_field_key": "annual_rate", "old_value": 0.042, "new_value": 0.05, "change_type": "increased", "old_evidence": _evidence("本合同执行固定年利率4.20%，按月付息、到期一次还本（先息后本）。", "C.c3", "C.c3.p1"), "new_evidence": _evidence("本合同调整后的固定年利率为5.00%，仍按月付息、到期一次还本（先息后本）。", "D.c3", "D.c3.p1"), "change_summary": "固定年利率由4.20%提高至5.00%。"},
            {"field_name": "term_months", "parser_field_key": "term_months", "old_value": 24, "new_value": 36, "change_type": "extended", "old_evidence": _evidence("借款期限为24个月，自2026年5月25日放款日起计算，借款到期日为2028年5月24日。", "C.c2", "C.c2.p1"), "new_evidence": _evidence("本合同调整后的借款期限为36个月，自2026年5月25日首次放款日起计算，调整后的借款到期日为2029年5月24日。", "D.c2", "D.c2.p1"), "change_summary": "期限延长12个月。"},
            {"field_name": "guarantee", "parser_field_key": "guarantee", "old_value": "一般保证", "new_value": "连带责任保证", "change_type": "liability_strengthened", "old_evidence": _evidence("远川能源有限公司为本合同提供一般保证，保证期间为主债务履行期限届满之日起两年。", "C.c4", "C.c4.p1"), "new_evidence": _evidence("远川能源有限公司改为对本合同承担连带责任保证，保证期间为主债务履行期限届满之日起三年。", "D.c4", "D.c4.p1"), "change_summary": "担保责任由一般保证改为连带责任保证，保证期间延长。"},
            {"field_name": "overdue_penalty", "parser_field_key": "breach_liability", "old_value": "每日万分之5", "new_value": "每日万分之8", "change_type": "increased", "old_evidence": _evidence("借款人逾期的，按逾期金额每日万分之5支付违约金；逾期超过30日的，贷款人有权宣布借款提前到期。", "C.c5", "C.c5.p2"), "new_evidence": _evidence("借款人逾期的，按逾期金额每日万分之8支付违约金；逾期超过15日的，贷款人有权宣布借款提前到期。", "D.c5", "D.c5.p2"), "change_summary": "逾期违约金日利率提高，且加速到期触发从30日收紧至15日。"},
        ],
        "unchanged_terms": [{"field_name": "payment_method", "value": "先息后本", "note": "措辞虽增加‘仍’，经济含义未变化，不应标记为变更。"}, {"field_name": "dispute_resolution", "value": "贷款人住所地法院", "note": "原文相同，不应标记为变化。"}],
        "expected_costlens_after_amendment": {"computable": True, "inputs": {"principal": 35_000_000, "annual_rate": 0.05, "term_months": 36, "repayment": "先息后本"}, "calculation": costlens.analyze_cost(new)},
        "expected_evidence": {"rule": "每个差异必须同时带旧合同和新协议的完整证据句；D 的补充协议关系不能只靠文件名推断。"},
        "expected_status": "five_material_changes_and_unchanged_terms_preserved",
    }


def build_sample_e_gt():
    return {
        "case_id": "E",
        "sample_type": "信息不完整综合授信协议",
        "synthetic": True,
        "file": "sample_E_incomplete_credit_agreement.docx",
        "document_type": "原始合同",
        "transaction_type": "授信",
        "parties": {"lender": "启明商业银行股份有限公司", "borrower": "星瀚制造有限公司"},
        "fields": {
            "credit_limit": _field("credit_limit", "授信额度", 50_000_000, [_evidence("本协议核定综合授信额度为人民币50,000,000元。", "E.c1", "E.c1.p1")], role="credit_limit", display="50,000,000元"),
            "credit_term": _field("term", "授信有效期", 24, [_evidence("额度有效期自2026年7月1日至2028年6月30日，额度有效期届满后未使用额度自动失效。", "E.c1", "E.c1.p2")], role="credit_term", display="24个月"),
            "rate_mechanism": _field("rate", "利率机制", "1年期LPR+80BP", [_evidence("每笔用信的利率机制为1年期LPR加80个基点，具体执行利率在每次提款时由双方在单笔借款合同中确定。", "E.c2", "E.c2.p1")], role="lpr_spread"),
            "actual_financing_amount": {"parser_field_key": "amount", "value": None, "status": "not_found", "amount_role": "actual_financing_amount", "note": "授信额度不得代替实际提款金额"},
            "drawdown_date": {"parser_field_key": "effective_date", "value": None, "status": "not_found", "date_role": "drawdown_date"},
            "drawdown_term": {"parser_field_key": "term", "value": None, "status": "not_found", "term_role": "actual_financing_term"},
            "payment_method": {"parser_field_key": "payment_method", "value": None, "status": "not_found", "role": "cashflow_structure"},
        },
        "expected_evidence": {"anchors": ["E.c1.p1", "E.c1.p2", "E.c2.p1", "E.c3.p1"], "rule": "授信额度、额度有效期和LPR机制各自保留证据，不组合成实际融资现金流。"},
        "expected_costlens_state": {"computable": False, "reason": "当前信息不足，暂无法唯一计算总融资成本。", "missing_inputs": ["actual_financing_amount", "drawdown_date", "drawdown_term", "payment_method"], "blocking_reason": "授信协议未披露实际提款金额、放款日、单笔实际融资期限和完整还款现金流；不得使用授信额度或额度有效期替代。"},
        "expected_scenario_behavior": {"without_user_assumptions": "blocked", "reason": "没有真实现金流，禁止直接模拟。", "with_user_assumptions": {"assumption_label": "用户设定的情景假设（不属于合同原始事实）", "principal": 20_000_000, "annual_rate": 0.053, "term_months": 12, "repayment": "先息后本", "expected": "仅在用户主动补充这些参数后允许情景计算。"}},
        "expected_covenants": [],
        "expected_contract_diff": None,
        "expected_status": "insufficient_information",
    }


def write_json(name, value):
    path = ROOT / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_readme():
    text = """# ContractGuard 金融合同功能测试样本集

本目录中的文件均为 **ContractGuard 研究与功能验证使用的人工合成金融合同样本**（Synthetic Benchmark Sample）。它们不是真实金融机构合同、真实企业融资案例、真实公开交易，也不是法律模板；主体名称、金额、日期、合同编号均为虚构测试数据。

## 文件清单

| 文件 | 用途 |
|---|---|
| `sample_A_standard_loan.docx` | 标准企业借款：完整现金流，测试 CostLens 与 ScenarioCompare |
| `sample_B_covenant_loan.docx` | 带财务契约贷款，测试 CovenantGuard，并辅助 CostLens/ScenarioCompare |
| `sample_B_financial_data.json` | B 的两期财务数据：Q1 多项通过，Q2 多项违反 |
| `sample_B_financial_data_missing.json` | B 的缺数据场景：应返回无法判断，不得默认 0 |
| `sample_C_original_contract.docx` | ContractDiff 旧版本 |
| `sample_D_amendment.docx` | C 的补充协议/新版本 |
| `sample_E_incomplete_credit_agreement.docx` | 信息不完整授信协议：验证主动降级 |
| `ground_truth_A.json` | A 的字段、证据、CostLens 和情景 Ground Truth |
| `ground_truth_B.json` | B 的字段、四类 Covenant、成本和数据 Ground Truth |
| `ground_truth_C_D_diff.json` | C→D 的差异 Ground Truth |
| `ground_truth_E.json` | E 的拒算与用户情景 Ground Truth |
| `coverage_matrix.md` | 四大核心功能覆盖矩阵 |
| `build_samples.py` | 可重复生成上述文件的构建脚本 |

## 当前 Schema 对照

Ground Truth 优先沿用项目现有字段：`amount`、`rate`、`term`、`payment_method`、`fee_bearing`、`early_termination`、`breach_liability`、`credit_limit`、`covenants`。业务角色写在同一条记录的 `role`/`amount_role`/`rate_role`/`term_role` 中；例如 `actual_financing_amount` 映射到当前解析器的通用 `amount`，`base_interest_rate` 映射到 `rate`。

当前 `CovenantGuard` 已支持资产负债率、流动比率、速动比率、净资产；B 额外放入“利息保障倍数（EBITDA/利息费用）”作为能力边界样本。当前 Schema 没有 EBITDA/利息费用输入字段，因此 Ground Truth 明确标记为“待扩展”，不得把它伪装成已通过的判断。

DOCX 解析器不会可靠地产生真实页码，所以 Ground Truth 使用 `section`、`clause_id`、合成源段落 ID 和完整原文句子；`page` 明确为 `null`，不伪造页码。上传后 ContractGuard 仍会生成自己的解析 `paragraph_id` 和字符偏移，人工核验以完整证据文本为准。

## 逐个上传与测试

1. 打开 ContractGuard 合同库，依次上传 A、B、C、D、E 五个 DOCX。
2. A：进入 CostLens，确认使用 2,000 万实际本金、4.20%、18 个月、先息后本和 8 万服务费；再进入 ScenarioCompare，运行基准、利率上升 1 个百分点、提前还款第 9 个月。页面应注明后两项为用户情景假设。
3. B：上传后先查看识别到的财务契约；在 CovenantGuard 上传 `sample_B_financial_data.json`。逐期核对 Ground Truth 的 Q1/Q2 结果；再上传缺失数据文件，所有需要缺失科目的指标应为“无法判断/信息不足”。
4. C 与 D：在 ContractDiff 中选择 C 为旧版本、D 为新版本。应识别金额、利率、期限、担保责任、逾期违约金/加速到期五类实质变化；“先息后本”和争议解决条款不应被误报为变化。
5. E：CostLens 和 ScenarioCompare 在没有用户补充真实提款现金流时应拒算/阻断。只有用户主动输入本金、利率、期限和还款方式后，才允许运行标记为“用户设定的情景假设”的模拟。

## 预期行为重点

- 授信额度不等于实际融资金额；授信有效期不等于单笔实际期限。
- 只有输入属于同一笔交易、且金额/利率/期限/现金流结构足够明确时，CostLens 才输出确定结果。
- 缺财务数据应返回 UNKNOWN/无法判断，不把缺失科目当作 0。
- ContractDiff 的每项变化都要同时带旧证据和新证据；相同经济含义的措辞变化不应被标为重大变化。
- 所有证据应来自这些文件的真实原文整句/整款，不能用固定字符窗口或模型补写。

## 已知能力边界

当前项目没有为本样本集新增生产字段或特殊分支。利息保障倍数的结构化监控、DOCX 视觉坐标级定位、复杂多交易无标题材料的自动归属仍属于后续能力边界；样本集会把这些差异明确暴露出来，不能以“样本已生成”替代功能已完成。
"""
    (ROOT / "README.md").write_text(text, encoding="utf-8")


def write_matrix():
    text = """# 四大核心功能覆盖矩阵

| 样本 | CostLens | ScenarioCompare | CovenantGuard | ContractDiff | 具体验证行为 |
|---|---|---|---|---|---|
| A 标准借款 | 完整现金流唯一计算：2,000万元、4.20%、18个月、先息后本、8万元服务费 | 基准、利率+1个百分点、放款后第9个月提前还款；假设与合同事实分离 | 不适用，不生成契约结果 | 不作为版本比较 | 检查利息、服务费、提前结清违约金和场景总成本可人工复算 |
| B 财务契约贷款 | 完整贷款成本可计算 | 可在明确用户参数后比较利率/期限/提前还款 | Q1/Q2 逐期判断；缺数据文件必须返回无法判断；利息保障倍数暴露当前 Schema 缺口 | 不作为版本比较 | 至少看到多项 PASS 与 BREACH；不得用0填缺失财务科目 |
| C 原始合同 | 作为 old contract 的完整成本输入 | 可作为对照情景 | 不适用 | 与 D 配对 | 提供金额、利率、期限、还款、担保、违约和加速到期证据 |
| D 补充协议 | 修改后成本应基于新金额/利率/期限 | 修改后仍可做情景比较 | 不适用 | 与 C 比较五类实质变化 | 30m→35m、4.20%→5.00%、24→36个月、一般→连带、万分之五→万分之八；保留未变化条款 |
| E 信息不完整授信 | 拒绝假精确总成本；不使用授信额度替代本金 | 无用户假设时阻断；补充本金/利率/期限/还款方式后才允许情景模拟 | 不适用 | 不作为版本比较 | 验证授信额度、额度有效期和LPR机制不会被拼成实际现金流 |

## 覆盖结论

- A：CostLens + ScenarioCompare 的“可唯一计算”路径。
- B：CovenantGuard 的通过、违反、缺数据路径，同时覆盖 CostLens。
- C+D：ContractDiff 的金额、利率、期限、担保、违约责任变化及不变条款保护。
- E：CostLens 拒算和 ScenarioCompare 先阻断、后接受显式用户情景的降级路径。
"""
    (ROOT / "coverage_matrix.md").write_text(text, encoding="utf-8")


def main():
    gt_a = build_sample_a()
    gt_b, fin_b, missing_b = build_sample_b()
    build_sample_c()
    build_sample_d()
    build_sample_e()
    write_json("ground_truth_A.json", gt_a)
    write_json("ground_truth_B.json", gt_b)
    write_json("ground_truth_C_D_diff.json", build_diff_gt())
    write_json("ground_truth_E.json", build_sample_e_gt())
    write_json("sample_B_financial_data.json", fin_b)
    write_json("sample_B_financial_data_missing.json", missing_b)
    write_readme()
    write_matrix()
    print("generated:")
    for path in sorted(ROOT.iterdir()):
        if path.suffix in {".docx", ".json", ".md"}:
            print(path.name)


if __name__ == "__main__":
    main()
