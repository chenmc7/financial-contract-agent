# -*- coding: utf-8 -*-
"""回归测试：财务期间排序（缺陷 1）与交易投影角色校验（缺陷 2）。

每个用例对应一个已确认的真实缺陷，修复前应当失败。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engines import covenant, costlens
from engines.uparse.schemas import make_field


def _contract(transactions, principal=15_000_000, annual_rate=0.045, term_months=24,
              repayment="先息后本", type_key="loan"):
    """构造一份最小可计算合同，字段状态默认为 confirmed。"""
    fields = {}
    for key, value in (("amount", principal), ("rate", annual_rate),
                       ("term", term_months), ("payment_method", repayment)):
        f = make_field(key, key, "测试")
        f.update(found=True, value=value, display=str(value), status="confirmed", confidence=0.95)
        fields[key] = f
    return {"contract_type": {"key": type_key, "label": "贷款合同"},
            "fields": fields, "principal": principal, "annual_rate": annual_rate,
            "term_months": term_months, "repayment": repayment, "fees": [],
            "transactions": transactions}


def _covenant():
    return [{"indicator": "资产负债率", "direction": "max", "operator": "<=",
             "threshold": 70, "unit": "%", "frequency": "季度", "remedy_days": 30,
             "consequence": "要求提前清偿", "formula": "", "required_data": [],
             "clause": "第四条", "page": 1, "source": "资产负债率不得超过70%"}]


class PeriodOrderingTests(unittest.TestCase):
    """缺陷 1：financials 行序被当成时间先后，倒序录入时取到最旧一期。"""

    def test_period_sorted_when_entered_newest_first(self):
        financials = [
            {"label": "2026Q2", "total_assets": 1000, "total_liabilities": 500},   # 50%，安全
            {"label": "2026Q1", "total_assets": 1000, "total_liabilities": 800},   # 80%，已触发
        ]
        rule = covenant.monitor(_covenant(), financials)[0]
        self.assertEqual([s["label"] for s in rule["series"]], ["2026Q1", "2026Q2"])
        self.assertEqual(rule["latest_period"], "2026Q2")
        self.assertEqual(rule["value"], 50.0)
        self.assertEqual(rule["status"], "安全")
        self.assertEqual(rule["period_order"], "label_sorted")

    def test_period_reversed_by_label_when_partially_parsable(self):
        financials = [
            {"label": "2026Q2（合并）", "total_assets": 1000, "total_liabilities": 500},
            {"label": "2026Q1（合并）", "total_assets": 1000, "total_liabilities": 800},
            {"label": "上期比较数", "total_assets": 1000, "total_liabilities": 900},
        ]
        rule = covenant.monitor(_covenant(), financials)[0]
        self.assertEqual(rule["period_order"], "reversed_by_label")
        self.assertEqual(rule["latest_period"], "2026Q2（合并）")
        self.assertEqual(rule["status"], "安全")

    def test_period_unparsable_keeps_input_order(self):
        financials = [
            {"label": "第一期", "total_assets": 1000, "total_liabilities": 500},
            {"label": "第二期", "total_assets": 1000, "total_liabilities": 800},
        ]
        rule = covenant.monitor(_covenant(), financials)[0]
        self.assertEqual(rule["period_order"], "input_order")
        self.assertEqual([s["label"] for s in rule["series"]], ["第一期", "第二期"])
        self.assertEqual(rule["latest_period"], "第二期")

    def test_period_key_formats(self):
        same_quarter = ["2026Q1", "2026-Q1", "2026年第一季度", "2026年1季度", "2026Q1（合并）"]
        self.assertEqual(len({covenant._period_key(x) for x in same_quarter}), 1)
        same_day = ["2026-03-31", "2026年3月31日", "20260331"]
        self.assertEqual(len({covenant._period_key(x) for x in same_day}), 1)
        self.assertIsNone(covenant._period_key("上期比较数"))
        self.assertLess(covenant._period_key("2025Q4"), covenant._period_key("2026Q1"))
        self.assertLess(covenant._period_key("2026Q1"), covenant._period_key("2026-06"))


class TransactionProjectionTests(unittest.TestCase):
    """缺陷 2：project_transaction 只看数值不看角色，授信额度会变成本金。"""

    def _tx(self, amount_role, term_role="actual_financing_term", amount=1_200_000_000):
        return [{"transaction_id": "T1", "repayment": "先息后本",
                 "primary": {"amount": {"value": amount, "role": amount_role},
                             "term": {"value": 12, "role": term_role},
                             "rate": {"value": 0.03}}}]

    def test_projection_rejects_credit_limit_as_principal(self):
        contract = _contract(self._tx("credit_limit"))
        projected = costlens.project_transaction(contract, "T1")
        self.assertIsNone(projected["principal"])
        self.assertTrue(projected["projection_blocked"])
        result = costlens.analyze_cost(projected)
        self.assertTrue(result["hidden"])
        self.assertEqual(result["reason_code"], "insufficient_params")
        self.assertIn("授信额度", "；".join(x["text"] for x in result["missing_detail"]))

    def test_projection_rejects_credit_term(self):
        contract = _contract(self._tx("actual_financing_amount", term_role="credit_term"))
        projected = costlens.project_transaction(contract, "T1")
        self.assertIsNone(projected["term_months"])
        self.assertEqual(projected["projection_blocked"][0]["role"], "credit_term")
        result = costlens.analyze_cost(projected)
        self.assertTrue(result["hidden"])
        self.assertIn("授信期限", "；".join(x["text"] for x in result["missing_detail"]))

    def test_projection_allows_user_confirmed_override(self):
        contract = _contract(self._tx("credit_limit"))
        contract["fields"]["amount"]["status"] = "confirmed_by_user"
        projected = costlens.project_transaction(contract, "T1")
        self.assertEqual(projected["principal"], 1_200_000_000)
        self.assertFalse(projected.get("projection_blocked"))

    def test_projection_keeps_legit_roles(self):
        contract = _contract(self._tx("leasing_principal", term_role=None), type_key="leasing")
        projected = costlens.project_transaction(contract, "T1")
        self.assertEqual(projected["principal"], 1_200_000_000)
        self.assertEqual(projected["term_months"], 12)
        self.assertNotIn("projection_blocked", projected)


if __name__ == "__main__":
    unittest.main()
