# -*- coding: utf-8 -*-
"""engines/uparse 通用结构化解析框架回归测试。

覆盖：8 类内置合同类型识别、中文大写金额、基点利率、日期区间与自然语言期限、
缺失字段语义、担保/理财专属字段、原文证据绑定、legacy 下游接口兼容。
"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engines import parser, samples
from engines.uparse import standardize
from engines.uparse.llm_extract import _standardize_llm_value, _verified_evidence
from engines.uparse.normalize import normalize_document
from engines.riskengine import assess_risks


class UniversalParserTests(unittest.TestCase):
    """通用结构化解析框架单元测试。"""

    def _parse(self, sample_id):
        sample = samples.SAMPLE_CONTRACTS[sample_id]
        return parser.parse_contract(sample["content"], sample["name"], use_llm=False)

    # ---------- 1. 类型识别 ----------

    def test_all_builtin_types_classified(self):
        """8 份内置样例均被正确分类且置信度 ≥ 0.85。"""
        expected = {
            "SC001": ("enterprise_loan", "企业贷款/借款合同"),
            "SC002": ("enterprise_loan", "企业贷款/借款合同"),
            "SC003": ("enterprise_loan", "企业贷款/借款合同"),
            "SC004": ("enterprise_loan", "企业贷款/借款合同"),
            "SC005": ("leasing", "融资租赁合同"),
            "SC006": ("leasing", "融资租赁合同"),
            "SC007": ("guarantee", "担保合同"),
            "SC008": ("wealth", "理财/资管合同"),
        }
        for sid, (key, label) in expected.items():
            with self.subTest(sample=sid):
                r = self._parse(sid)
                t = r["contract_type"]
                self.assertEqual(t["key"], key)
                self.assertEqual(t["label"], label)
                self.assertGreaterEqual(t["confidence"], 0.85)
                self.assertIn("evidence", t)

    # ---------- 2. 中文大写金额 ----------

    def test_sc007_cn_uppercase_amount(self):
        """最高额保证合同的贰仟万元整被正确标准化为 2000 万元。"""
        r = self._parse("SC007")
        f = r["fields"]["amount"]
        self.assertTrue(f["found"])
        self.assertEqual(f["value"], 20_000_000.0)
        self.assertEqual(f["display"], "¥2000.00万")
        self.assertIn("贰仟万元整", f["source_text"])
        self.assertEqual(f["clause"], "第二条 最高债权余额")

    # ---------- 3. 基点利率 ----------

    def test_basis_point_rate_parsing(self):
        """基点写法（50 BP）被折算为年化小数并保留口径说明。"""
        text = "本合同项下贷款按LPR加50个基点执行。"
        v, rtype, raw, conf, note = standardize.parse_rate_any(text)
        self.assertIsNotNone(v)
        self.assertEqual(rtype, "bp")
        self.assertAlmostEqual(v, 0.0050, places=5)
        self.assertIn("50", note)
        self.assertGreaterEqual(conf, 0.7)

    def test_lpr_floating_rate(self):
        """LPR 浮动利率（百分比口径）给出估算年化口径。"""
        text = "贷款利率按1年期LPR上浮10%执行。"
        v, rtype, raw, conf, note = standardize.parse_rate_any(text)
        self.assertIsNone(v)  # 相对百分比无法直接年化
        self.assertEqual(rtype, "lpr_float")
        self.assertIn("LPR", note)
        self.assertIn("3.45", note)  # 参考 LPR 提示

    # ---------- 4. 日期区间与自然语言期限 ----------

    def test_sc008_date_range_and_term(self):
        """资管合同中的日期区间被正确推导为 18 个月。"""
        r = self._parse("SC008")
        f = r["fields"]["term"]
        self.assertTrue(f["found"])
        self.assertEqual(f["value"], 18)
        self.assertEqual(f["display"], "18 个月")
        # 生效 / 到期日期均从日期区间中识别
        self.assertEqual(r["fields"]["effective_date"]["value"], "2026-03-01")
        self.assertEqual(r["fields"]["expiry_date"]["value"], "2027-08-31")

    def test_natural_language_term(self):
        """自然语言期限（三年 / 一年）正确转换为月数。"""
        self.assertEqual(standardize.parse_term_any("贷款期限为三年")[0], 36)
        self.assertEqual(standardize.parse_term_any("期限为一年")[0], 12)
        self.assertEqual(standardize.parse_term_any("十八个月")[0], 18)

    # ---------- 5. 缺失字段语义 ----------

    def test_missing_fields_with_reason(self):
        """担保合同无利率约定 → 返回"不适用"三态及原因，不阻断后续分析。"""
        r = self._parse("SC007")
        rate = r["fields"]["rate"]
        pay = r["fields"]["payment_method"]
        for f in (rate, pay):
            self.assertFalse(f["found"])
            self.assertEqual(f["status"], "not_applicable")
            self.assertIn("不适用", f["display"])
        # 整体置信度与 legacy 键仍然存在
        self.assertIsNotNone(r["confidence"])
        self.assertEqual(r["principal"], 20_000_000.0)
        self.assertEqual(r["term_months"], 36)

    # ---------- 6. 担保/理财专属字段 ----------

    def test_sc007_guarantee_specific_fields(self):
        """担保合同的担保物、担保范围、保证期间均正确抽取。"""
        r = self._parse("SC007")
        self.assertEqual(r["contract_type"]["key"], "guarantee")
        fields = r["fields"]
        self.assertIn("collateral", fields)
        self.assertIn("guarantee_scope", fields)
        self.assertIn("guarantee_period", fields)

        self.assertTrue(fields["guarantee_scope"]["found"])
        self.assertIn("主债权本金及利息", fields["guarantee_scope"]["value"])
        self.assertEqual(fields["guarantee_scope"]["clause"], "第四条 保证范围")

        self.assertTrue(fields["collateral"]["found"])
        self.assertIn("工业厂房", fields["collateral"]["value"])

        self.assertTrue(fields["guarantee_period"]["found"])
        self.assertIn("三年", fields["guarantee_period"]["value"])

    def test_sc008_wealth_specific_fields(self):
        """理财/资管合同的收益规则、风险等级、认购安排均正确抽取。"""
        r = self._parse("SC008")
        self.assertEqual(r["contract_type"]["key"], "wealth")
        fields = r["fields"]
        self.assertIn("yield_rule", fields)
        self.assertIn("risk_level", fields)
        self.assertIn("subscription", fields)

        self.assertTrue(fields["yield_rule"]["found"])
        self.assertAlmostEqual(fields["yield_rule"]["value"], 0.042, places=4)
        self.assertIn("4.2%", fields["yield_rule"]["display"])

        self.assertTrue(fields["risk_level"]["found"])
        self.assertIn("R2", fields["risk_level"]["value"])

        self.assertTrue(fields["subscription"]["found"])
        self.assertIn("壹佰万元整", fields["subscription"]["source_text"])

    # ---------- 7. 原文证据绑定 ----------

    def test_evidence_binding_on_universal_fields(self):
        """通用字段保留 source_text、clause、page 与 confidence。"""
        r = self._parse("SC007")
        f = r["fields"]["parties"]
        self.assertTrue(f["found"])
        self.assertIn("债权人", f["source_text"])
        self.assertIn("保证人", f["source_text"])
        self.assertEqual(f["page"], 1)
        self.assertGreaterEqual(f["confidence"], 0.9)

    # ---------- 8. Legacy 下游接口回归 ----------

    def test_legacy_keys_regression_for_loan_samples(self):
        """贷款样例的 legacy 键仍可供 CostLens/Scenario/Covenant/Diff 消费。"""
        checks = {
            "SC001": {
                "principal": 5_000_000.0,
                "annual_rate": 0.040,
                "term_months": 12,
                "repayment": "先息后本",
            },
            "SC002": {
                "principal": 8_000_000.0,
                "annual_rate": 0.046,
                "term_months": 36,
                "repayment": "先息后本",
            },
            "SC004": {
                "principal": 150_000_000.0,
                "annual_rate": 0.0435,
                "term_months": 60,
                "repayment": "按季付息、分期还本",
            },
        }
        for sid, expected in checks.items():
            with self.subTest(sample=sid):
                # 模拟服务器对内置样例做的 meta_fill：仅对 parser 未识别出的字段做兜底
                r = self._parse(sid)
                meta = samples.SAMPLE_CONTRACTS[sid].get("meta", {})
                for key, exp in expected.items():
                    got = r.get(key)
                    if got is None and key in meta:
                        got = meta[key]
                    self.assertIsNotNone(got, key)
                    if key == "repayment":
                        self.assertEqual(got, exp)
                    else:
                        self.assertAlmostEqual(got, exp, places=5, msg=key)

    def test_legacy_fees_and_guarantee_and_prepay(self):
        """服务费/担保费、担保安排、提前还款违约金等 legacy 字段保持可用。"""
        r = self._parse("SC001")
        self.assertIsInstance(r["fees"], list)
        names = {f["name"] for f in r["fees"]}
        self.assertIn("融资服务费", names)
        self.assertIn("担保费", names)
        self.assertIn("连带责任保证", r["guarantee"])

        r2 = self._parse("SC002")
        early = r2["fields"]["early_termination"]
        self.assertTrue(early["found"])
        self.assertIn("3.00%", early["display"])
        self.assertEqual(early["value"]["rate"], 0.03)

    def test_legacy_covenants_extraction(self):
        """财务契约条款仍能被下游 Covenant Guard 识别。"""
        r = self._parse("SC004")
        covenants = r.get("covenants", [])
        self.assertTrue(covenants)
        debt = next((c for c in covenants if c.get("indicator") == "资产负债率"), None)
        self.assertIsNotNone(debt)
        self.assertEqual(debt["threshold"], 70)

    # ---------- 9. 管线元数据 ----------

    def test_pipeline_metadata(self):
        """返回管线阶段、字段统计与字数信息，供前端解析面板展示。"""
        r = self._parse("SC001")
        self.assertIn("pipeline", r)
        self.assertIn("field_count", r)
        self.assertEqual(r["pipeline"]["llm"], "skipped")
        self.assertEqual(r["field_count"]["total"], 29)  # 贷款合同：26 通用（含文档/交易元数据）+ 3 专属
        self.assertGreater(r["field_count"]["found"], 0)

    def test_llm_candidate_requires_verifiable_quote_and_deterministic_value(self):
        """LLM 候选不得凭空生成证据，关键数值仍由确定性标准化器复核。"""
        doc = normalize_document("个人贷款合同\n第一条 贷款利率\n本贷款执行固定年利率6.00%。")
        verified = _verified_evidence(doc, "本贷款执行固定年利率6.00%")
        self.assertIsNotNone(verified)
        self.assertEqual(verified["clause"], "第一条 贷款利率")
        self.assertIsNone(_verified_evidence(doc, "年利率9.99%"))
        self.assertEqual(_standardize_llm_value("rate", "0.99", "6.00%", "固定年利率6.00%"), 0.06)

    def test_evidence_uses_complete_sentence_boundaries(self):
        """证据不得从日期中间截断，坐标仍指向同一原文。"""
        text = "公告说明\n截至2024年12月31日，资产负债率已降至70%以下，相应担保额度仍然有效。\n"
        result = parser.parse_contract(text, "公告", use_llm=False)
        evidence = result["fields"]["covenants"]["value"] if result["fields"].get("covenants", {}).get("found") else None
        self.assertTrue(result["fields"].get("amount", {}).get("source_text") is not None)
        spans = [f.get("evidence_span") for f in result["fields"].values() if f.get("evidence_span")]
        self.assertTrue(spans)
        for span in spans:
            self.assertFalse(span["text"].lstrip().startswith("月31日"))
            self.assertEqual(text[span["start"]:span["end"]], span["text"])

    def test_info_auth_date_is_not_retention_duration_or_risk(self):
        """段落中的报告年份/授权期限不能误生成“保留2025年”风险。"""
        text = """融资公告
一、财务情况
截至2025年12月31日，资产负债率为65%，授权期限内担保额度仍然有效。
二、信息授权
本协议授权查询相关信息，但未约定合同终止后的保存年限。"""
        result = parser.parse_contract(text, "公告", use_llm=False)
        auth = result["fields"].get("info_auth") or {}
        self.assertIsNone((auth.get("value") or {}).get("retention_years"))
        self.assertFalse(any(r["id"] == "cmp-auth" for r in assess_risks(result)["risks"]))


if __name__ == "__main__":
    unittest.main()
