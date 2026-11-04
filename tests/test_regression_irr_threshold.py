# -*- coding: utf-8 -*-
"""回归测试：IRR 无解（缺陷 A1）、契约阈值除零（缺陷 A2）、速动比率口径（缺陷 A3）。

每个用例对应一个已确认的真实缺陷，修复前应当失败。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engines import covenant, costlens
from engines.uparse import extract


def _loan(principal=1_000_000, annual_rate=0.05, term_months=12,
          repayment="等额本息", fees=None, deposit=None, type_key="loan"):
    """构造一份最小可计算合同（字段状态 confirmed，与真实上传对象同构）。"""
    return {"contract_type": {"key": type_key, "label": "贷款合同"},
            "principal": principal, "annual_rate": annual_rate,
            "term_months": term_months, "repayment": repayment,
            "fees": fees or [], "deposit": deposit, "fields": {}}


class IrrNoSolutionTests(unittest.TestCase):
    """缺陷 A1：二分 200 次后无论是否收敛都返回区间中点，无解时输出 12000% 类假值。"""

    def test_irr_returns_none_when_all_cashflows_negative(self):
        # 净流入 ≤ 0（费用/保证金吞掉本金）时现金流全同号，IRR 不存在
        self.assertIsNone(costlens.irr([-5_000, -1_000, -1_000]))
        self.assertIsNone(costlens.irr([5_000, 1_000, 1_000]))

    def test_irr_returns_none_when_bracket_has_no_sign_change(self):
        # 区间两端 NPV 同号 → 区间内无零点，必须判无解而不是收敛到边界
        flows = [-100.0, 0.5]
        self.assertIsNone(costlens.irr(flows))

    def test_irr_returns_none_for_degenerate_input(self):
        self.assertIsNone(costlens.irr([]))
        self.assertIsNone(costlens.irr([1_000_000]))

    def test_irr_still_solves_normal_loan(self):
        # 正常贷款年化成本应落在合理区间，不能被"无解"误伤
        flows = [1_000_000] + [-90_000] * 12
        value = costlens.irr(flows)
        self.assertIsNotNone(value)
        self.assertGreater(value, 0)
        self.assertLess(value, 1)

    def test_irr_survives_extreme_periods_without_crashing(self):
        # 400 期 + 下界利率曾触发折现因子下溢（除零）；现在只返回数值或 None
        flows = [1.0, -1_000.0] * 200
        value = costlens.irr(flows)
        self.assertTrue(value is None or isinstance(value, float))

    def test_costlens_does_not_report_absurd_rate_when_fees_exceed_principal(self):
        contract = _loan(fees=[{"name": "融资服务费", "amount": 1_200_000,
                                "timing": "期初", "clause": "第三条"}])
        result = costlens.analyze_cost(contract)
        self.assertTrue(result["computable"])
        self.assertIsNone(result["real_annual_cost_pct"])
        self.assertIn("无解", result["real_annual_cost_note"])
        # 费用超过本金时也不应再把 10.0 的上界年化成 12000%
        self.assertNotEqual(result["real_annual_cost_pct"], 12000.0)

    def test_costlens_reports_normal_annual_cost(self):
        result = costlens.analyze_cost(_loan())
        self.assertIsNotNone(result["real_annual_cost_pct"])
        self.assertLess(result["real_annual_cost_pct"], 100)
        self.assertIsNone(result["real_annual_cost_note"])


class CovenantZeroThresholdTests(unittest.TestCase):
    """缺陷 A2：阈值为 0 时 margin 计算除零，异常冒到接口层变成 500。"""

    def _covenant(self, indicator="流动比率", direction="min", threshold=0):
        return [{"indicator": indicator, "direction": direction,
                 "operator": ">=" if direction == "min" else "<=",
                 "threshold": threshold, "unit": "", "frequency": "季度",
                 "remedy_days": 30, "consequence": "要求提前清偿",
                 "formula": "", "required_data": [], "clause": "第四条", "page": 1,
                 "source": "测试条款"}]

    def test_min_indicator_zero_threshold_does_not_divide_by_zero(self):
        self.assertEqual(covenant.judge_status("流动比率", 1.5, "min", 0), "安全")
        self.assertEqual(covenant.judge_status("流动比率", 0, "min", 0), "安全")
        self.assertEqual(covenant.judge_status("流动比率", -0.5, "min", 0), "已触发")

    def test_max_indicator_zero_threshold_does_not_divide_by_zero(self):
        self.assertEqual(covenant.judge_status("资产负债率", 30, "max", 0), "已触发")
        self.assertEqual(covenant.judge_status("资产负债率", 0, "max", 0), "安全")

    def test_monitor_survives_zero_threshold_and_explains(self):
        financials = [{"label": "2026Q1", "total_assets": 1000, "total_liabilities": 500,
                       "current_assets": 800, "current_liabilities": 400, "quick_assets": 600}]
        rule = covenant.monitor(self._covenant(), financials)[0]
        self.assertEqual(rule["status"], "安全")
        self.assertEqual(rule["value"], 2.0)
        self.assertIn("阈值为 0", rule["threshold_note"])
        self.assertIn("除零", rule["threshold_note"])

    def test_nonzero_threshold_behaviour_unchanged(self):
        financials = [{"label": "2026Q1", "total_assets": 1000, "total_liabilities": 500}]
        rule = covenant.monitor(
            self._covenant("资产负债率", "max", 70), financials)[0]
        self.assertEqual(rule["status"], "安全")
        self.assertIsNone(rule["threshold_note"])


class QuickRatioFormulaTests(unittest.TestCase):
    """缺陷 A3：extract 声明的速动比率口径（流动资产）与 covenant 实现（速动资产）不一致。"""

    def test_all_declared_formulas_match_engine_requirements(self):
        for indicator, (_, required, _) in extract.COVENANT_FORMULAS.items():
            self.assertEqual(required, covenant.REQUIRED_FIELDS[indicator],
                             "指标 %s 的公式声明所需科目与引擎不一致" % indicator)

    def test_quick_ratio_formula_declares_quick_assets(self):
        formula, required, _ = extract.COVENANT_FORMULAS["速动比率"]
        self.assertEqual(formula, "速动资产 / 流动负债")
        self.assertEqual(required, ["quick_assets", "current_liabilities"])
        self.assertNotIn("流动资产", formula)

    def test_extracted_covenant_carries_aligned_formula(self):
        doc = {"sections": [{"title": "第四条 财务指标",
                             "body": "速动比率不得低于 1.0。", "page": 2}]}
        produced = extract._f_covenants(doc)
        self.assertEqual(len(produced), 1)
        self.assertEqual(produced[0]["formula"], "速动资产 / 流动负债")
        self.assertEqual(produced[0]["required_data"], ["quick_assets", "current_liabilities"])

    def test_quick_ratio_needs_quick_assets_not_current_assets(self):
        # 只给流动资产的样本不得被算成速动比率，口径与声明一致
        financials = [{"label": "2026Q1", "current_assets": 800, "current_liabilities": 400}]
        rule = covenant.monitor(
            [{"indicator": "速动比率", "direction": "min", "operator": ">=",
              "threshold": 1.0, "frequency": "季度"}], financials)[0]
        self.assertEqual(rule["status"], "无法判断")
        self.assertIn("速动资产", rule["series"][0]["missing_labels"])
        self.assertIsNone(rule["value"])


if __name__ == "__main__":
    unittest.main()
