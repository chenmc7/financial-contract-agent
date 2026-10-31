# -*- coding: utf-8 -*-
"""回归测试：/api/agent/tasks 的意图路由、上下文校正与降级链路。

覆盖三层决策：
1. 公网模式（deterministic_only）或无 Key → 关键词，且不发起任何外部请求；
2. 有 Key → 让模型出计划，白名单过滤后执行；
3. 模型超时、异常、返回非 JSON、返回非法工具 → 回退关键词。

同时锁住上下文校正：单份合同不调情景比较、三份以上不调版本对比、
无财务数据不调契约监控。

测试不读写 data/ 下的真实库，也不依赖 DEMO_MODE：内存里注入两份已解析样本，
并替换 _load_contract_cache，避免被请求中间件重载冲掉。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# 隔离测试产物，避免污染仓库内的 data/ 与 uploads/
os.environ["CONTRACTGUARD_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="cg-agent-"), "test.db")
os.environ["CONTRACTGUARD_UPLOAD_DIR"] = tempfile.mkdtemp(prefix="cg-agent-up-")
# 显式置空而非删除：_load_env_file 只在变量不存在时才从 .env 注入，
# 置空可以阻止本机 .env 里的真实 Key 把「无 Key 走关键词」的用例带偏
os.environ["CONTRACTGUARD_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""

import server  # noqa: E402  （必须在设置环境变量之后导入）
from engines import parser, samples  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _build_records(count=2):
    records = {}
    for sid, sample in list(samples.SAMPLE_CONTRACTS.items())[:count]:
        parsed = parser.parse_contract(sample["content"], sample["name"], use_llm=False)
        records[sid] = {
            "id": sid, "name": sample["name"], "type": sample.get("type", "合同"),
            "source": "test", "text": sample["content"], "parsed": parsed,
        }
    return records


class AgentRoutingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = _build_records(2)
        cls.ids = list(cls.records.keys())
        cls._original_loader = server._load_contract_cache
        server._load_contract_cache = lambda: cls.records
        cls.client = TestClient(server.app)

    @classmethod
    def tearDownClass(cls):
        server._load_contract_cache = cls._original_loader

    def _run(self, task, ids=None, financials=None):
        body = {"task": task, "contract_ids": self.ids[:1] if ids is None else ids}
        if financials is not None:
            body["financials"] = financials
        return self.client.post("/api/agent/tasks", json=body)

    @staticmethod
    def _called(response):
        return {item["tool"] for item in response.json()["tool_trace"]
                if item["status"] == "called" and item["tool"] != "Plan"}

    def _with_llm(self, complete_mock, deterministic=False):
        return mock.patch.multiple(
            server.ai_client,
            is_configured=mock.Mock(return_value=True),
            complete=complete_mock,
        ), mock.patch.dict(server.SECURITY, {"deterministic_only": deterministic})

    # ---- 关键词路径 ----

    def test_01_cost_question_triggers_costlens(self):
        response = self._run("这份合同贵不贵")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Cost Lens", self._called(response))
        self.assertEqual(response.json()["plan_source"], "keyword")

    def test_02_risk_question_triggers_risk_engine(self):
        response = self._run("有什么坑")
        self.assertIn("Risk Engine", self._called(response))

    def test_03_diff_requires_exactly_two_contracts(self):
        response = self._run("改了什么", ids=self.ids[:2])
        self.assertIn("Contract Diff", self._called(response))

    # ---- 上下文校正 ----

    def test_04_scenario_removed_for_single_contract(self):
        response = self._run("比较两份方案", ids=self.ids[:1])
        self.assertNotIn("Scenario Compare", self._called(response))

    def test_05_covenant_removed_without_financials(self):
        response = self._run("检查契约", ids=self.ids[:2])
        self.assertNotIn("Covenant Guard", self._called(response))

    # ---- LLM 路径与降级 ----

    def test_06_deterministic_mode_never_calls_llm(self):
        complete = mock.Mock(return_value='["costlens"]')
        patches, security = self._with_llm(complete, deterministic=True)
        with patches, security:
            response = self._run("这份合同贵不贵")
        complete.assert_not_called()
        self.assertEqual(response.json()["plan_source"], "keyword")
        self.assertIn("Cost Lens", self._called(response))

    def test_07_illegal_tool_falls_back(self):
        patches, security = self._with_llm(mock.Mock(return_value='["delete_all"]'))
        with patches, security:
            response = self._run("这份合同贵不贵")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["plan_source"], "fallback")
        self.assertIn("Cost Lens", self._called(response))

    def test_08_llm_exception_falls_back(self):
        patches, security = self._with_llm(mock.Mock(side_effect=RuntimeError("boom")))
        with patches, security:
            response = self._run("有什么坑")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["plan_source"], "fallback")
        self.assertIn("Risk Engine", self._called(response))

    def test_09_valid_llm_plan_is_used(self):
        patches, security = self._with_llm(mock.Mock(return_value='["costlens"]'))
        with patches, security:
            response = self._run("随便看看")
        self.assertEqual(response.json()["plan_source"], "llm")
        self.assertIn("Cost Lens", self._called(response))

    # ---- 输出结构 ----

    def test_10_ai_context_and_summary_present(self):
        response = self._run("有什么坑")
        payload = response.json()
        self.assertIn("ai_context", payload)
        self.assertIn("ai_summary", payload)
        for item in payload["ai_summary"]:
            self.assertTrue(item.get("title"))
            self.assertIn("evidence", item)
            self.assertIn("clause", item)


if __name__ == "__main__":
    unittest.main()
