# -*- coding: utf-8 -*-
import tempfile
import unittest
from pathlib import Path

from storage import Store


class SharedDatabaseSyncTests(unittest.TestCase):
    def test_second_service_sees_new_contract_and_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "contractguard.db")
            local_store = Store(db_path)
            public_store = Store(db_path)
            rec = {
                "id": "UPSHARED01", "name": "共享测试合同",
                "display_name": "共享测试合同", "original_filename": "shared.docx",
                "type": "上传合同", "source": "upload", "text": "合同文本" * 10,
                "parsed": {"fields": {}}, "file_diagnostics": {}, "file_path": None,
                "created_at": 1, "analysis_version": 1,
            }
            local_store.save_contract(rec)
            self.assertIn("UPSHARED01", public_store.load_contracts())
            local_store.revision("UPSHARED01", "rate", {"value": 0.04}, {"value": 0.05})
            self.assertEqual(public_store.revisions("UPSHARED01")[0]["new"]["value"], 0.05)


if __name__ == "__main__":
    unittest.main()
