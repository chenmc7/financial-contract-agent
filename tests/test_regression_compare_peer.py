# -*- coding: utf-8 -*-
"""回归测试：复核重算的情景对比对手固定化（缺陷 A4）。

旧实现取 CONTRACTS 字典里第一个可测算的合同当对手，而 CONTRACTS 由 SQLite 重载，
载入顺序变化就会换对手；同一份合同两次重算得到不同的对比结论。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# 隔离测试产物：不可污染仓库内的 data/ 与 uploads/
os.environ.setdefault("CONTRACTGUARD_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="cg-peer-"), "test.db"))
os.environ.setdefault("CONTRACTGUARD_UPLOAD_DIR", tempfile.mkdtemp(prefix="cg-peer-up-"))

import server  # noqa: E402  （必须在设置环境变量之后导入）


def _record(cid, type_key="loan", type_label="贷款合同", created_at=100.0,
            principal=1_000_000, annual_rate=0.05, term_months=12):
    return {"id": cid, "name": cid, "display_name": cid, "type": type_label,
            "source": "upload", "text": "", "created_at": created_at,
            "parsed": {"contract_type": {"key": type_key, "label": type_label},
                       "principal": principal, "annual_rate": annual_rate,
                       "term_months": term_months, "repayment": "等额本息",
                       "fees": [], "fields": {}, "covenants": [], "clauses": []}}


def _pool(*records):
    return {rec["id"]: rec for rec in records}


class ComparePeerSelectionTests(unittest.TestCase):
    """缺陷 A4：对比对象随机 → 改为同类型且创建时间最近，并且可复现。"""

    def test_peer_prefers_same_type_over_newer_other_type(self):
        pool = _pool(_record("A", "loan", created_at=100),
                     _record("B", "guarantee", "担保合同", created_at=900),
                     _record("C", "loan", created_at=300))
        peer, same_type = server._select_compare_peer(pool["A"], pool)
        self.assertEqual(peer["id"], "C")
        self.assertTrue(same_type)

    def test_peer_is_latest_among_same_type(self):
        pool = _pool(_record("A", created_at=100),
                     _record("OLD", created_at=150),
                     _record("NEW", created_at=800))
        peer, _ = server._select_compare_peer(pool["A"], pool)
        self.assertEqual(peer["id"], "NEW")

    def test_peer_selection_is_order_independent(self):
        records = [_record("A", created_at=100), _record("B", created_at=300),
                   _record("C", created_at=300), _record("D", created_at=200)]
        forward = server._select_compare_peer(_pool(*records)["A"], _pool(*records))[0]["id"]
        backward = server._select_compare_peer(_pool(*reversed(records))["A"], _pool(*reversed(records)))[0]["id"]
        self.assertEqual(forward, backward)

    def test_peer_never_points_to_self(self):
        pool = _pool(_record("A", created_at=900))
        self.assertIsNone(server._select_compare_peer(pool["A"], pool))

    def test_peer_skips_contracts_without_cost_inputs(self):
        pool = _pool(_record("A", created_at=100),
                     _record("BROKEN", created_at=900, principal=None),
                     _record("OK", created_at=200))
        peer, _ = server._select_compare_peer(pool["A"], pool)
        self.assertEqual(peer["id"], "OK")

    def test_peer_falls_back_to_other_type_when_no_same_type(self):
        pool = _pool(_record("A", "loan", created_at=100),
                     _record("B", "leasing", "融资租赁", created_at=500))
        peer, same_type = server._select_compare_peer(pool["A"], pool)
        self.assertEqual(peer["id"], "B")
        self.assertFalse(same_type)


class RerunAnnotatesPeerTests(unittest.TestCase):
    """复核重算的结果里必须显式标注对手，便于追溯与复现。"""

    def _run(self, pool, target):
        with mock.patch.object(server.orchestrator, "orchestrate",
                              return_value={"tool_results": {}}), \
                mock.patch.object(server, "STORE") as store, \
                mock.patch.object(server, "CONTRACTS", pool):
            store.save_contract.return_value = None
            store.save_analysis.return_value = None
            return server._rerun_single(pool[target])

    def test_rerun_marks_peer_and_self(self):
        pool = _pool(_record("A", created_at=100),
                     _record("B", "guarantee", "担保合同", created_at=900),
                     _record("C", created_at=300))
        result = self._run(pool, "A")
        self.assertEqual(result["scenario_compare_peer"]["id"], "C")
        self.assertTrue(result["scenario_compare_peer"]["same_type"])
        self.assertIn("同类型且创建时间最近", result["scenario_compare_peer"]["rule"])
        marks = {item["id"]: item["is_self"] for item in result["scenario_compare"]}
        self.assertEqual(marks, {"A": True, "C": False})

    def test_rerun_is_reproducible_for_same_target(self):
        records = [_record("A", created_at=100), _record("B", created_at=300),
                   _record("C", created_at=300), _record("D", created_at=200)]
        first = self._run(_pool(*records), "A")["scenario_compare_peer"]["id"]
        second = self._run(_pool(*reversed(records)), "A")["scenario_compare_peer"]["id"]
        self.assertEqual(first, second)

    def test_rerun_without_peer_omits_annotation(self):
        pool = _pool(_record("A", created_at=100))
        result = self._run(pool, "A")
        self.assertNotIn("scenario_compare", result)
        self.assertNotIn("scenario_compare_peer", result)


if __name__ == "__main__":
    unittest.main()
