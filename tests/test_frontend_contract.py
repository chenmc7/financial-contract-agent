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

    def test_costlens_omits_unchanged_charts(self):
        javascript = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
        self.assertIn("showCostChart", javascript)
        self.assertIn("showFlowChart", javascript)
        self.assertIn("各展示期还款金额一致，已省略无变化曲线。", javascript)

    def test_library_notices_keep_labels_readable(self):
        css = (ROOT / "static/css/style.css").read_text(encoding="utf-8")
        self.assertIn(".source-notice b { color:var(--text); white-space:nowrap; }", css)
        self.assertIn(".processing-notice b { flex:0 0 auto;", css)
        self.assertIn("line-height:1.8", css)


if __name__ == "__main__":
    unittest.main()
