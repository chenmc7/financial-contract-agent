import io
import unittest
import zipfile

from engines import costlens, file_parser, parser


CONTRACT_TEXT = """
流动资金借款合同
借款人：上海真实数据科技有限公司
贷款人：中国测试银行股份有限公司
第一条 借款金额
借款金额人民币1,200万元。
第二条 借款期限
借款期限12个月。
第三条 利率与还款
年利率4.20%，按月付息，到期一次还本。
第四条 费用
借款人应支付一次性融资服务费人民币6万元。
""".strip()


def make_docx(paragraphs):
    xml = "".join("<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % p for p in paragraphs)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as zf:
        zf.writestr("word/document.xml", '<w:document xmlns:w="x"><w:body>%s</w:body></w:document>' % xml)
    return payload.getvalue()


class RealFileFlowTests(unittest.TestCase):
    def test_docx_keeps_real_text_and_drives_cost(self):
        data = make_docx(CONTRACT_TEXT.splitlines())
        extracted = file_parser.extract_document(
            "real.docx", data,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertNotIn("【段落", extracted["text"])
        parsed = parser.parse_contract(extracted["text"], "real", use_llm=False)
        self.assertEqual(parsed["principal"], 12_000_000)
        self.assertEqual(parsed["fees"][0]["amount"], 60_000)
        result = costlens.analyze_cost(parsed)
        self.assertEqual(result["total_fees"], 60_000)
        self.assertGreater(result["real_annual_cost_pct"], 4.2)

    def test_signature_and_mime_are_verified(self):
        with self.assertRaisesRegex(ValueError, "不是有效 PDF"):
            file_parser.extract_document("fake.pdf", b"not a pdf", "application/pdf")
        with self.assertRaisesRegex(ValueError, "MIME"):
            file_parser.extract_document("fake.txt", b"plain text", "application/pdf")


if __name__ == "__main__":
    unittest.main()
