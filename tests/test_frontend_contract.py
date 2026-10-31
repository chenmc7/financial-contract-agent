import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    def test_all_direct_id_bindings_exist_in_html(self):
        javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
        html = (ROOT / "static/index.html").read_text(encoding="utf-8")
        bound_ids = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', javascript))
        html_ids = set(re.findall(r'\bid=["\']([^"\']+)', html))
        self.assertEqual(sorted(bound_ids - html_ids), [])

    def test_costlens_statuses_are_localized(self):
        javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
        self.assertIn('confirmed: "已确认"', javascript)
        self.assertIn('confirmed_by_user: "用户已确认"', javascript)
        self.assertNotIn('${esc(x.status)}</span></td>', javascript)

    def test_costlens_renders_every_cashflow_period(self):
        """成本项无变化仍省略柱图；现金流改为逐期全量图，不再抽样 5 点。"""
        javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
        charts = (ROOT / "static/js/charts.js").read_text(encoding="utf-8")
        self.assertIn("showCostChart", javascript)
        # 旧的按 arr.length/5 抽样逻辑必须彻底移除
        self.assertNotIn("arr.length / 5", javascript)
        self.assertNotIn("showFlowChart", javascript)
        # 全量 cashflows 直接交给图表组件
        self.assertIn("CGCharts.flow(cashflows", javascript)
        self.assertIn("line, bars, flow", charts)
        # 图表必须同时画出还款、本金、利息三条序列
        self.assertIn('key: "total"', charts)
        self.assertIn('key: "principal"', charts)
        self.assertIn('key: "interest"', charts)

    def test_library_notices_keep_labels_readable(self):
        css = (ROOT / "static/css/style.css").read_text(encoding="utf-8")
        self.assertIn(".source-notice b { color:var(--text); white-space:nowrap; }", css)
        self.assertIn(".processing-notice b { flex:0 0 auto;", css)
        self.assertIn("line-height:1.8", css)


if __name__ == "__main__":
    unittest.main()
