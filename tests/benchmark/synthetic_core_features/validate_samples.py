#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the synthetic benchmark through the normal parser and four engines."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[2]
sys.path.insert(0, str(PROJECT))

from engines import covenant, costlens, diffengine, file_parser, parser, scenario  # noqa: E402


def load_doc(name):
    data = (ROOT / name).read_bytes()
    extracted = file_parser.extract_document(name, data, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    return parser.parse_contract(extracted["text"], name, use_llm=False)


def main():
    a = load_doc("sample_A_standard_loan.docx")
    b = load_doc("sample_B_covenant_loan.docx")
    c = load_doc("sample_C_original_contract.docx")
    d = load_doc("sample_D_amendment.docx")
    e = load_doc("sample_E_incomplete_credit_agreement.docx")

    a_cost = costlens.analyze_cost(a)
    a_scenarios = {
        "base": scenario.simulate_one(a, "hold", {}),
        "rate_up_1pp": scenario.simulate_one(a, "rate_up", {"delta_pp": 1.0}),
        "prepay_month_9": scenario.simulate_one(a, "prepay", {"month": 9}),
    }
    fin_payload = json.loads((ROOT / "sample_B_financial_data.json").read_text(encoding="utf-8"))
    financials = [{k: v for k, v in row.items() if k in {"label", "total_liabilities", "total_assets", "current_assets", "current_liabilities", "quick_assets"}}
                  for row in fin_payload["financials"]]
    b_cov = covenant.monitor(b.get("covenants") or [], financials)
    cd = diffengine.diff_contracts(c, d)
    e_cost = costlens.analyze_cost(e)

    summary = {
        "A": {"type": a["contract_type"], "principal": a.get("principal"), "rate": a.get("annual_rate"), "term_months": a.get("term_months"), "repayment": a.get("repayment"), "cost": a_cost, "scenarios": a_scenarios},
        "B": {"type": b["contract_type"], "principal": b.get("principal"), "rate": b.get("annual_rate"), "term_months": b.get("term_months"), "covenant_count": len(b.get("covenants") or []), "covenants": b_cov},
        "C_D": {"old": {"principal": c.get("principal"), "rate": c.get("annual_rate"), "term_months": c.get("term_months"), "guarantee": c.get("guarantee")}, "new": {"principal": d.get("principal"), "rate": d.get("annual_rate"), "term_months": d.get("term_months"), "guarantee": d.get("guarantee")}, "diff": cd},
        "E": {"type": e["contract_type"], "credit_limit": (e.get("fields", {}).get("credit_limit") or {}).get("value"), "term_role": (e.get("fields", {}).get("term") or {}).get("role"), "principal": e.get("principal"), "cost": e_cost},
    }

    # These checks are deliberately about behavior, not UI strings.
    assert a["contract_type"]["key"] == "enterprise_loan"
    assert a.get("principal") == 20_000_000
    assert a.get("annual_rate") == 0.042
    assert a.get("term_months") == 18
    assert a.get("repayment") == "先息后本"
    assert a_cost.get("computable") is True and a_cost.get("total_cost") == 1_340_000.0
    assert abs(a_scenarios["rate_up_1pp"]["total_cost"] - 1_640_000.06) < 0.1
    assert a_scenarios["prepay_month_9"]["total_cost"] == 910_000.0

    assert b["contract_type"]["key"] == "enterprise_loan"
    assert b.get("principal") == 15_000_000
    assert b.get("annual_rate") == 0.045
    assert b.get("term_months") == 24
    assert len(b.get("covenants") or []) == 3, "当前引擎应识别三项已支持契约；利息保障倍数作为能力边界记录"
    debt = next(x for x in b_cov if x["indicator"] == "资产负债率")
    current = next(x for x in b_cov if x["indicator"] == "流动比率")
    net = next(x for x in b_cov if x["indicator"] == "净资产")
    assert debt["series"][0]["status"] == "安全" and debt["series"][1]["status"] == "已触发"
    assert current["series"][0]["status"] == "安全" and current["series"][1]["status"] == "已触发"
    assert net["series"][0]["status"] == "安全" and net["series"][1]["status"] == "已触发"

    assert c["contract_type"]["key"] == d["contract_type"]["key"] == "enterprise_loan"
    assert c.get("principal") == 30_000_000 and d.get("principal") == 35_000_000
    assert c.get("annual_rate") == 0.042 and d.get("annual_rate") == 0.05
    assert c.get("term_months") == 24 and d.get("term_months") == 36
    assert "一般保证" in (c.get("guarantee") or "") and "连带责任保证" in (d.get("guarantee") or "")
    assert any(x.get("aspect") == "年利率" for x in cd.get("changes", []))
    assert any(x.get("aspect") == "融资金额" for x in cd.get("changes", []))
    assert any(x.get("aspect") == "融资期限" for x in cd.get("changes", []))
    assert any(x.get("aspect") == "担保方式" for x in cd.get("changes", []))
    assert any(x.get("aspect") == "逾期违约金" for x in cd.get("changes", []))

    assert e["contract_type"]["key"] == "credit"
    assert (e.get("fields", {}).get("credit_limit") or {}).get("value") == 50_000_000
    assert e.get("principal") is None
    assert e_cost.get("hidden") is True and e_cost.get("computable") is False

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
