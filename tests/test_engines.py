import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engines import costlens, covenant, diffengine, orchestrator, parser, samples, scenario
from engines.riskengine import assess_risks


def parsed(sample_id):
    sample = samples.SAMPLE_CONTRACTS[sample_id]
    result = parser.parse_contract(sample["content"], sample["name"])
    meta = sample.get("meta", {})
    for key in ("principal", "annual_rate", "term_months", "repayment", "fees", "guarantee"):
        if key in meta:
            result[key] = meta[key]
    if meta.get("prepayment_penalty"):
        result["prepayment_penalty"] = {
            "rate": meta["prepayment_penalty"],
            "amount": result["principal"] * meta["prepayment_penalty"],
            "source": "测试样例",
        }
    return result


class ContractGuardEngineTests(unittest.TestCase):
    def test_costlens_matches_outline_and_excludes_untriggered_penalty(self):
        result = costlens.analyze_cost(parsed("SC001"))
        self.assertAlmostEqual(result["total_cost"], 325000, delta=1)
        self.assertEqual(result["comprehensive_rate_pct"], 6.5)
        self.assertEqual(result["rate_gap_pp"], 2.5)

    def test_scenario_ranking_flips(self):
        contracts = [
            {"id": "SC002", "name": "A", "parsed": parsed("SC002")},
            {"id": "SC003", "name": "B", "parsed": parsed("SC003")},
        ]
        result = scenario.flip_analysis(contracts, {"month": 12})
        self.assertEqual(result["hold"][0]["id"], "SC002")
        self.assertEqual(result["prepay"][0]["id"], "SC003")
        self.assertAlmostEqual(result["prepay"][1]["total_cost"] - result["prepay"][0]["total_cost"], 160000, delta=1)

    def test_covenant_moves_from_near_to_triggered(self):
        covenants = parsed("SC004")["covenants"]
        results = covenant.monitor(covenants, samples.COVENANT_FINANCIALS["quarters"])
        debt = next(item for item in results if item["indicator"] == "资产负债率")
        self.assertEqual(debt["series"][-2]["status"], "接近阈值")
        self.assertEqual(debt["series"][-1]["status"], "已触发")
        self.assertEqual(debt["series"][-2]["value"], 68.0)
        self.assertEqual(debt["series"][-1]["value"], 71.0)

    def test_contract_diff_quantifies_cost_and_liability(self):
        result = diffengine.diff_contracts(parsed("SC005"), parsed("SC006"), remaining_principal=4_000_000)
        self.assertEqual(result["total_impact"], 80_000)
        self.assertTrue(any(c.get("aspect") == "担保方式" and c["risk"] == "up" for c in result["changes"]))
        self.assertTrue(any(c.get("aspect") == "信息授权范围" and c["risk"] == "up" for c in result["changes"]))

    def test_arbitrary_uploaded_contract_is_parsed(self):
        text = """企业借款合同
第一条 贷款金额为人民币1200万元，期限为18个月。
第二条 本合同年利率为4.2%，按月付息，到期还本。
第三条 借款人应按贷款金额的0.8%支付融资服务费。
第四条 资产负债率不得超过65%，每季度检查。"""
        result = parser.parse_contract(text, "任意合同")
        self.assertEqual(result["principal"], 12_000_000)
        self.assertEqual(result["term_months"], 18)
        self.assertEqual(result["annual_rate"], 0.042)
        self.assertEqual(result["covenants"][0]["threshold"], 65)

    def test_scenario_blocks_missing_cashflow_instead_of_crashing(self):
        parsed = {
            "contract_type": {"key": "factoring"}, "principal": 100000,
            "annual_rate": 0.05, "term_months": None, "repayment": None,
            "fields": {
                "amount": {"found": True, "status": "confirmed", "role": "actual_financing_amount"},
                "rate": {"found": True, "status": "confirmed", "value": 0.05},
                "term": {"found": True, "status": "uncertain", "role": "actual_financing_term"},
                "payment_method": {"found": False, "status": "not_found"},
            },
        }
        rows = scenario.compare_contracts([{"id": "x", "name": "保理", "parsed": parsed}], "hold")
        self.assertTrue(rows[0]["hidden"])
        self.assertIn("实际融资期限", rows[0]["missing_detail"][0]["text"])

    def test_diff_blocks_different_contract_types(self):
        old = parser.parse_contract("保理合同\n保理融资金额100万元。", "old", use_llm=False)
        new = parser.parse_contract("融资租赁合同\n租赁本金100万元，租期12个月。", "new", use_llm=False)
        result = diffengine.diff_contracts(old, new)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["total_impact"], 0)

    def test_covenant_missing_financial_items_is_unknown_not_error(self):
        result = covenant.monitor([{"indicator": "资产负债率", "direction": "max", "threshold": 70}],
                                  [{"label": "2025Q1", "total_assets": 100}])
        self.assertEqual(result[0]["status"], "无法判断")
        self.assertIn("总负债", result[0]["series"][0]["missing_labels"])

    def test_quick_ratio_requires_explicit_quick_assets(self):
        result = covenant.monitor([{"indicator": "速动比率", "direction": "min", "threshold": 1.0}],
                                  [{"label": "2025Q1", "current_assets": 100, "current_liabilities": 80}])
        self.assertEqual(result[0]["status"], "无法判断")
        self.assertIn("速动资产", result[0]["series"][0]["missing_labels"])

    def test_personal_consumer_loan_drives_costlens(self):
        """回归此前测试合同：10万元、固定年利率6%、24个月必须贯通解析与确定性计算。"""
        text = """个人消费贷款合同
贷款人（甲方）：示例银行股份有限公司
借款人（乙方）：张三
第一条 贷款金额
本合同项下贷款金额为人民币100000元，币种为人民币。
第二条 贷款期限
贷款期限为24个月，自实际放款日起计算。
第三条 贷款利率
本贷款执行固定年利率6.00%，在合同期限内不调整。
第四条 还款方式
借款人按月等额本息还款。
第五条 贷款用途
本贷款仅限用于个人消费。"""
        result = parser.parse_contract(text, "个人消费贷款测试合同", use_llm=False)
        self.assertEqual(result["contract_type"]["key"], "personal_loan")
        self.assertEqual(result["principal"], 100_000)
        self.assertEqual(result["fields"]["currency"]["value"], "CNY")
        self.assertEqual(result["annual_rate"], 0.06)
        self.assertEqual(result["term_months"], 24)
        self.assertEqual(result["repayment"], "等额本息")
        for key in ("amount", "currency", "rate", "term"):
            field = result["fields"][key]
            self.assertEqual(field["status"], "confirmed")
            self.assertTrue(field["source_text"])
            self.assertIsNotNone(field["clause"])
            self.assertEqual(field["page"], 1)
        cost = costlens.analyze_cost(result)
        self.assertFalse(cost.get("hidden", False))
        self.assertEqual(cost["principal"], 100_000)
        self.assertEqual(cost["annual_rate"], 0.06)
        self.assertEqual(cost["term_months"], 24)
        self.assertAlmostEqual(cost["total_interest"], 6369.48, delta=0.1)
        self.assertEqual(cost["total_fees"], 0)
        self.assertEqual(cost["rate_gap_pp"], 0)
        self.assertNotIn("额外费用使成本增加", cost["summary"])
        agent = orchestrator.orchestrate(result)
        self.assertIn("costlens", agent["tool_results"])
        self.assertEqual(agent["tool_results"]["costlens"]["principal"], 100_000)
        cost_step = next(s for s in agent["plan"]["steps"] if s["tool"] == "costlens")
        self.assertTrue(any("已自动测算" in line for line in cost_step["detail"]))

    def test_transaction_grouping_keeps_amount_and_term_in_same_deal(self):
        """公告同时披露两笔租赁时，金额/期限只在各自交易段内配对。"""
        text = """关于对外担保的进展公告
1、担保事项一
公司与出租人开展融资租赁业务，租赁本金为35,000万元，租赁期限为60个月，并提供连带责任保证。
2、担保事项二
公司与出租人开展融资租赁业务，租赁本金为33,484.98万元，租赁期限96个月，补充协议将租赁期限96个月变更为122个月，并提供连带责任保证。"""
        result = parser.parse_contract(text, "多交易公告", use_llm=False)
        self.assertEqual(result["transaction_count"], 2)
        first, second = result["transactions"]
        self.assertEqual(first["primary"]["amount"]["value"], 350_000_000)
        self.assertEqual(first["primary"]["term"]["value"], 60)
        self.assertAlmostEqual(second["primary"]["amount"]["value"], 334_849_800, places=2)
        self.assertEqual(second["primary"]["term"]["value"], 122)
        self.assertEqual(second["term_change"], {"from": 96, "to": 122, "status": "confirmed", "label": "期限变更"})
        self.assertIn("交易选择", costlens.missing_detail(result)[0]["label"])

    def test_factoring_roles_and_recourse_risk_are_evidence_bound(self):
        text = """关于保理融资安排的临时公告
克旗明阳与中信银行股份有限公司沧州分行开展附有追索权的国内保理业务。
本次授信额度为111,000,000.00元，授信期限24个月，保理融资金额为102,870,578.66元，
融资利率为2.55%/年，融资期限为自融资款发放日起不超过三年，可以提前还款。
截至2024年12月31日（保理基准日），应收账款为102,870,578.66元。"""
        result = parser.parse_contract(text, "保理公告", use_llm=False)
        fields = result["fields"]
        self.assertEqual(fields["credit_limit"]["value"], 111_000_000)
        self.assertEqual(fields["actual_financing_amount"]["value"], 102_870_578.66)
        self.assertEqual(fields["credit_term"]["value"], 24)
        self.assertIsNone(fields["expiry_date"]["value"])
        self.assertEqual(fields["baseline_date"]["value"], "2024-12-31")
        self.assertTrue(fields["actual_financing_amount"].get("evidence_span"))
        self.assertTrue(any(r["id"] == "crd-recourse" for r in assess_risks(result)["risks"]))
        cost = costlens.analyze_cost(result)
        self.assertTrue(cost["hidden"])
        self.assertIn("实际融资期限", "；".join(x["text"] for x in cost["missing_detail"]))

    def test_project_transaction_does_not_inherit_unrelated_rate_or_repayment(self):
        parsed = {
            "contract_type": {"key": "leasing"}, "principal": 1, "annual_rate": 0.05,
            "term_months": 12, "repayment": "先息后本", "fields": {
                "amount": {"found": True, "status": "confirmed", "role": "leasing_principal"},
                "rate": {"found": True, "status": "confirmed", "value": 0.05},
                "term": {"found": True, "status": "confirmed", "role": "leasing_term"},
                "payment_method": {"found": True, "status": "confirmed", "value": "先息后本"},
            }, "transactions": [
                {"transaction_id": "transaction_1", "primary": {"amount": {"value": 1, "role": "leasing_principal"}, "term": {"value": 12}, "rate": None}},
                {"transaction_id": "transaction_2", "primary": {"amount": {"value": 2, "role": "leasing_principal"}, "term": {"value": 24}, "rate": None}},
            ]}
        selected = costlens.project_transaction(parsed, "transaction_2")
        self.assertEqual(selected["principal"], 2)
        self.assertEqual(selected["term_months"], 24)
        self.assertIsNone(selected["annual_rate"])
        self.assertIsNone(selected["repayment"])
        self.assertTrue(costlens.analyze_cost(selected)["hidden"])


if __name__ == "__main__":
    unittest.main()
