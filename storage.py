# -*- coding: utf-8 -*-
"""本地 SQLite 持久化。只保存业务数据，不保存 API Key。"""
import json
import os
import sqlite3
import time
from contextlib import contextmanager


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._init()

    @contextmanager
    def _conn(self):
        """每次操作独立连接，成功即提交、异常即回滚，退出必定关闭。

        不关闭会导致 Windows 下数据库文件被进程占用，
        备份/迁移/清理以及测试的临时目录回收都会失败。
        """
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _init(self):
        with self._conn() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS contracts(
              id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT, source TEXT,
              text TEXT NOT NULL, parsed_json TEXT NOT NULL, diagnostics_json TEXT,
              file_path TEXT, access_level TEXT DEFAULT 'owner', created_at REAL, updated_at REAL,
              analysis_version INTEGER DEFAULT 1,
              original_filename TEXT, display_name TEXT
            );
            CREATE TABLE IF NOT EXISTS revisions(
              id INTEGER PRIMARY KEY AUTOINCREMENT, contract_id TEXT NOT NULL,
              field_key TEXT, old_json TEXT, new_json TEXT, source TEXT,
              created_at REAL, FOREIGN KEY(contract_id) REFERENCES contracts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS analyses(
              id INTEGER PRIMARY KEY AUTOINCREMENT, contract_id TEXT NOT NULL,
              kind TEXT NOT NULL, result_json TEXT NOT NULL, version INTEGER,
              created_at REAL, FOREIGN KEY(contract_id) REFERENCES contracts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS financial_history(
              id INTEGER PRIMARY KEY AUTOINCREMENT, contract_id TEXT NOT NULL,
              source_name TEXT, data_json TEXT NOT NULL, result_json TEXT,
              created_at REAL, FOREIGN KEY(contract_id) REFERENCES contracts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS review_status(
              contract_id TEXT NOT NULL, item_id TEXT NOT NULL, status TEXT NOT NULL,
              note TEXT, updated_at REAL, PRIMARY KEY(contract_id,item_id),
              FOREIGN KEY(contract_id) REFERENCES contracts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS audit_log(
              id INTEGER PRIMARY KEY AUTOINCREMENT, contract_id TEXT, action TEXT NOT NULL,
              detail_json TEXT, actor TEXT DEFAULT 'local_user', created_at REAL
            );
            """)
            # 兼容 checkpoint 之前已经存在的数据库，不丢失任何合同记录。
            columns = {row[1] for row in db.execute("PRAGMA table_info(contracts)").fetchall()}
            if "original_filename" not in columns:
                db.execute("ALTER TABLE contracts ADD COLUMN original_filename TEXT")
            if "display_name" not in columns:
                db.execute("ALTER TABLE contracts ADD COLUMN display_name TEXT")
            db.execute("UPDATE contracts SET display_name=COALESCE(NULLIF(display_name,''),name), original_filename=COALESCE(NULLIF(original_filename,''),name)")
            # 旧版本只保存了去扩展名的 name；若上传路径仍在，补回文件类型后缀。
            db.execute("""UPDATE contracts SET original_filename = name ||
                       CASE WHEN lower(file_path) LIKE '%.pdf' THEN '.pdf'
                            WHEN lower(file_path) LIKE '%.docx' THEN '.docx'
                            WHEN lower(file_path) LIKE '%.txt' THEN '.txt' ELSE '' END
                       WHERE file_path IS NOT NULL AND original_filename = name AND name NOT LIKE '%.%'""")

    def health(self):
        try:
            with self._conn() as db:
                db.execute("SELECT 1").fetchone()
            return {"ok": True, "persistent": True, "file": os.path.basename(self.path)}
        except sqlite3.Error as exc:
            return {"ok": False, "persistent": True, "error": str(exc)[:120]}

    def save_contract(self, rec, diagnostics=None):
        now = time.time()
        with self._conn() as db:
            db.execute("""INSERT INTO contracts(id,name,type,source,text,parsed_json,diagnostics_json,file_path,access_level,created_at,updated_at,analysis_version,original_filename,display_name)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET name=excluded.name,type=excluded.type,source=excluded.source,
              text=excluded.text,parsed_json=excluded.parsed_json,diagnostics_json=excluded.diagnostics_json,
              file_path=excluded.file_path,access_level=excluded.access_level,updated_at=excluded.updated_at,
              analysis_version=excluded.analysis_version,original_filename=excluded.original_filename,
              display_name=excluded.display_name""",
              (rec["id"], rec["name"], rec.get("type"), rec.get("source"), rec["text"],
               json.dumps(rec["parsed"], ensure_ascii=False), json.dumps(diagnostics or rec.get("file_diagnostics") or {}, ensure_ascii=False),
               rec.get("file_path"), rec.get("access_level", "owner"), rec.get("created_at", now), now,
               rec.get("analysis_version", 1), rec.get("original_filename") or rec["name"],
               rec.get("display_name") or rec["name"]))

    def load_contracts(self):
        with self._conn() as db:
            rows = db.execute("SELECT * FROM contracts ORDER BY created_at").fetchall()
        out = {}
        for r in rows:
            display_name = r["display_name"] or r["name"]
            original_filename = r["original_filename"] or r["name"]
            out[r["id"]] = {"id": r["id"], "name": display_name, "display_name": display_name,
              "original_filename": original_filename, "type": r["type"], "source": r["source"],
              "text": r["text"], "parsed": json.loads(r["parsed_json"]), "file_diagnostics": json.loads(r["diagnostics_json"] or "{}"),
              "file_path": r["file_path"], "access_level": r["access_level"], "created_at": r["created_at"],
              "analysis_version": r["analysis_version"]}
        return out

    def revision(self, cid, field_key, old, new, source="user_edit"):
        with self._conn() as db:
            db.execute("INSERT INTO revisions(contract_id,field_key,old_json,new_json,source,created_at) VALUES(?,?,?,?,?,?)",
                       (cid, field_key, json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False), source, time.time()))
            db.execute("UPDATE contracts SET analysis_version=analysis_version+1,updated_at=? WHERE id=?", (time.time(), cid))

    def revisions(self, cid):
        with self._conn() as db:
            rows = db.execute("SELECT * FROM revisions WHERE contract_id=? ORDER BY id DESC", (cid,)).fetchall()
        return [{"id": r["id"], "field_key": r["field_key"], "old": json.loads(r["old_json"] or "null"),
                 "new": json.loads(r["new_json"] or "null"), "source": r["source"], "created_at": r["created_at"]} for r in rows]

    def save_analysis(self, cid, kind, result, version=1):
        with self._conn() as db:
            db.execute("INSERT INTO analyses(contract_id,kind,result_json,version,created_at) VALUES(?,?,?,?,?)",
                       (cid, kind, json.dumps(result, ensure_ascii=False), version, time.time()))

    def save_financials(self, cid, source_name, data, result):
        with self._conn() as db:
            db.execute("INSERT INTO financial_history(contract_id,source_name,data_json,result_json,created_at) VALUES(?,?,?,?,?)",
                       (cid, source_name, json.dumps(data, ensure_ascii=False), json.dumps(result, ensure_ascii=False), time.time()))

    def financial_history(self, cid):
        with self._conn() as db:
            rows = db.execute("SELECT * FROM financial_history WHERE contract_id=? ORDER BY id DESC", (cid,)).fetchall()
        return [{"id": r["id"], "source_name": r["source_name"], "data": json.loads(r["data_json"]),
                 "result": json.loads(r["result_json"] or "null"), "created_at": r["created_at"]} for r in rows]

    def set_review(self, cid, item_id, status, note=""):
        with self._conn() as db:
            db.execute("INSERT INTO review_status(contract_id,item_id,status,note,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(contract_id,item_id) DO UPDATE SET status=excluded.status,note=excluded.note,updated_at=excluded.updated_at",
                       (cid, item_id, status, note, time.time()))

    def review_statuses(self, cid):
        with self._conn() as db:
            rows = db.execute("SELECT * FROM review_status WHERE contract_id=?", (cid,)).fetchall()
        return {r["item_id"]: {"status": r["status"], "note": r["note"], "updated_at": r["updated_at"]} for r in rows}

    def audit(self, action, cid=None, detail=None):
        with self._conn() as db:
            db.execute("INSERT INTO audit_log(contract_id,action,detail_json,created_at) VALUES(?,?,?,?)",
                       (cid, action, json.dumps(detail or {}, ensure_ascii=False), time.time()))

    def audits(self, cid=None, limit=100):
        with self._conn() as db:
            rows = db.execute("SELECT * FROM audit_log WHERE (? IS NULL OR contract_id=?) ORDER BY id DESC LIMIT ?", (cid, cid, limit)).fetchall()
        return [{"id": r["id"], "contract_id": r["contract_id"], "action": r["action"],
                 "detail": json.loads(r["detail_json"] or "{}"), "actor": r["actor"], "created_at": r["created_at"]} for r in rows]

    def delete_contract(self, cid):
        with self._conn() as db:
            db.execute("DELETE FROM contracts WHERE id=?", (cid,))
            db.execute("DELETE FROM audit_log WHERE contract_id=?", (cid,))
