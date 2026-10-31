# -*- coding: utf-8 -*-
"""回归测试：公网只读保护（缺陷 A5）。

公网隧道实例（SHARE_MODE）与本地实例共用同一份数据库，旧版本下任何拿到链接的人
都能删除合同、改名、上传文件或修改安全配置。修复后 SHARE_MODE 下只放行只读方法，
以及白名单内的纯计算接口；维护者可凭 X-Share-Token 临时放行。
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# 隔离测试产物：不可污染仓库内的 data/ 与 uploads/
os.environ.setdefault("CONTRACTGUARD_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="cg-share-"), "test.db"))
os.environ.setdefault("CONTRACTGUARD_UPLOAD_DIR", tempfile.mkdtemp(prefix="cg-share-up-"))

import server  # noqa: E402  （必须在设置环境变量之后导入）


def _asgi_call(method, path, headers=None):
    """直接以 ASGI 方式调用应用，避免为测试额外引入 httpx 依赖。"""
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "client": ("127.0.0.1", 12345), "server": ("testserver", 80), "state": {},
    }
    captured = {"status": None, "body": b""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
        elif message["type"] == "http.response.body":
            captured["body"] += message.get("body", b"")

    asyncio.run(server.app(scope, receive, send))
    return captured


class ShareGuardDecisionTests(unittest.TestCase):
    """判定规则：本地实例放行、只读方法放行、写方法拦截、令牌放行。"""

    def setUp(self):
        self._mode = server.SHARE_MODE
        self._token = server.SHARE_TOKEN
        server.SHARE_MODE = True
        server.SHARE_TOKEN = ""

    def tearDown(self):
        server.SHARE_MODE = self._mode
        server.SHARE_TOKEN = self._token

    def test_local_mode_allows_everything(self):
        server.SHARE_MODE = False
        self.assertIsNone(server.share_guard_decision("DELETE", "/api/contracts/A"))
        self.assertIsNone(server.share_guard_decision("POST", "/api/contracts/upload"))

    def test_readonly_methods_are_allowed(self):
        for method in ("GET", "HEAD", "OPTIONS"):
            self.assertIsNone(server.share_guard_decision(method, "/api/contracts"))

    def test_write_methods_are_blocked(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            decision = server.share_guard_decision(method, "/api/contracts/A")
            self.assertIsNotNone(decision, "%s 应被拦截" % method)
            self.assertTrue(decision["readonly"])
            self.assertIn("只读", decision["error"])

    def test_mutating_endpoints_are_blocked(self):
        for path in ("/api/contracts/upload", "/api/contracts/import-text",
                     "/api/security", "/api/ai/config", "/api/contracts/A/access"):
            self.assertIsNotNone(server.share_guard_decision("POST", path), path)

    def test_readonly_compute_endpoints_are_allowed(self):
        for path in ("/api/analyze/costlens", "/api/analyze/scenario",
                     "/api/analyze/covenant", "/api/analyze/diff",
                     "/api/analyze/comprehensive", "/api/agent/tasks", "/api/ai/chat"):
            self.assertIsNone(server.share_guard_decision("POST", path), path)

    def test_trailing_slash_cannot_bypass_whitelist(self):
        self.assertIsNone(server.share_guard_decision("POST", "/api/analyze/costlens/"))
        self.assertIsNotNone(server.share_guard_decision("POST", "/api/contracts/upload/"))

    def test_write_methods_stay_blocked_on_compute_paths(self):
        # 白名单只对 POST 生效：DELETE /api/analyze/* 仍应被拦截
        self.assertIsNotNone(server.share_guard_decision("DELETE", "/api/analyze/costlens"))

    def test_token_grants_write_access_only_when_configured(self):
        self.assertIsNotNone(server.share_guard_decision("PATCH", "/api/contracts/A/name", "tk"))
        server.SHARE_TOKEN = "tk"
        self.assertIsNone(server.share_guard_decision("PATCH", "/api/contracts/A/name", "tk"))
        self.assertIsNotNone(server.share_guard_decision("PATCH", "/api/contracts/A/name", "wrong"))


class ShareGuardMiddlewareTests(unittest.TestCase):
    """中间件确实挂在应用上：隧道无法删除合同或修改配置。"""

    def setUp(self):
        self._mode = server.SHARE_MODE
        self._token = server.SHARE_TOKEN
        server.SHARE_MODE = True
        server.SHARE_TOKEN = ""

    def tearDown(self):
        server.SHARE_MODE = self._mode
        server.SHARE_TOKEN = self._token

    def test_delete_contract_is_rejected_over_share(self):
        result = _asgi_call("DELETE", "/api/contracts/ANYID")
        self.assertEqual(result["status"], 403)
        self.assertIn("只读", result["body"].decode("utf-8"))

    def test_rename_and_upload_are_rejected_over_share(self):
        self.assertEqual(_asgi_call("PATCH", "/api/contracts/ANYID/name")["status"], 403)
        self.assertEqual(_asgi_call("POST", "/api/contracts/upload")["status"], 403)

    def test_security_config_change_is_rejected_over_share(self):
        self.assertEqual(_asgi_call("POST", "/api/security")["status"], 403)

    def test_read_endpoints_still_work_over_share(self):
        result = _asgi_call("GET", "/api/health")
        self.assertEqual(result["status"], 200)
        self.assertIn('"share_mode":true', result["body"].decode("utf-8").replace(" ", ""))
        self.assertEqual(_asgi_call("GET", "/api/contracts")["status"], 200)

    def test_local_instance_keeps_write_access(self):
        server.SHARE_MODE = False
        # 本地实例不拦截：合同不存在时是 404，而不是 403
        self.assertEqual(_asgi_call("DELETE", "/api/contracts/ANYID")["status"], 404)


class ShareCovenantPersistenceTests(unittest.TestCase):
    """公网访客的财务数据不落库：只读实例不能污染本地财务历史。"""

    def _contract(self):
        return {"id": "C1", "name": "含契约条款的合同", "display_name": "含契约条款的合同",
                "type": "贷款合同", "source": "upload", "text": "", "created_at": 1.0,
                "parsed": {"contract_type": {"key": "loan", "label": "贷款合同"},
                           "covenants": [{"indicator": "资产负债率", "direction": "max",
                                          "operator": "<=", "threshold": 70, "frequency": "季度",
                                          "required_data": [], "unit": "%"}]}}

    def _body(self):
        return server.CovenantBody(contract_ids=["C1"], financials=[
            server.CovenantFin(label="2026Q1", total_assets=1000, total_liabilities=500)])

    def test_share_mode_does_not_persist_financials(self):
        with mock.patch.object(server, "SHARE_MODE", True), \
                mock.patch.object(server, "CONTRACTS", {"C1": self._contract()}), \
                mock.patch.object(server, "STORE") as store:
            result = server.api_covenant(self._body())
        self.assertFalse(result["monitors"][0]["persisted"])
        store.save_financials.assert_not_called()
        store.audit.assert_not_called()

    def test_local_mode_still_persists_financials(self):
        with mock.patch.object(server, "SHARE_MODE", False), \
                mock.patch.object(server, "CONTRACTS", {"C1": self._contract()}), \
                mock.patch.object(server, "STORE") as store:
            result = server.api_covenant(self._body())
        self.assertNotIn("persisted", result["monitors"][0])
        store.save_financials.assert_called_once()
        store.audit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
