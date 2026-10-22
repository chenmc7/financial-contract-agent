# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage import Store


class ContractNamePersistenceTests(unittest.TestCase):
    def test_display_name_is_separate_and_persistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(str(Path(tmp) / "contractguard.db"))
            rec = {
                "id": "UPTEST01", "name": "明阳REIT保理融资公告",
                "display_name": "明阳REIT保理融资公告", "original_filename": "1222858931.pdf",
                "type": "上传合同", "source": "upload", "text": "真实合同文本" * 10,
                "parsed": {"fields": {}}, "file_diagnostics": {}, "file_path": None,
                "created_at": 1, "analysis_version": 1,
            }
            store.save_contract(rec)
            loaded = store.load_contracts()["UPTEST01"]
            self.assertEqual(loaded["display_name"], "明阳REIT保理融资公告")
            self.assertEqual(loaded["original_filename"], "1222858931.pdf")
            self.assertEqual(loaded["name"], loaded["display_name"])

    def test_database_file_released_after_write(self):
        """缺陷 5：连接未显式关闭时，Windows 上 DB 文件会被占用，导致备份/迁移/清理失败。"""
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "contractguard.db")
        store = Store(path)
        store.audit("test_event", None, {"k": 1})
        try:
            os.remove(path)
        except PermissionError as exc:
            self.fail("写入后数据库文件仍被占用：%s" % exc)
        finally:
            for name in os.listdir(tmp):
                try:
                    os.remove(os.path.join(tmp, name))
                except OSError:
                    pass
            try:
                os.rmdir(tmp)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
