# -*- coding: utf-8 -*-
"""ContractGuard 企业级金融合同全生命周期风险管理服务。"""
import json
import os
import re
import time
import uuid
import csv
import io
import tempfile

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List, Dict

from engines import (parser, costlens, scenario, covenant, diffengine, ai_client,
                     samples, assessment, orchestrator, riskengine, file_parser)
from engines.uparse import _attach_evidence_spans
from engines.uparse import transactions as transaction_indexer
from storage import Store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.abspath(os.path.expanduser(os.getenv(
    "CONTRACTGUARD_UPLOAD_DIR", os.path.join(BASE_DIR, "uploads"))))
os.makedirs(UPLOAD_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.abspath(os.path.expanduser(os.getenv(
    "CONTRACTGUARD_DB_PATH", os.path.join(DATA_DIR, "contractguard.db"))))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
DEMO_MODE = os.getenv("CONTRACTGUARD_DEMO_MODE", "0").lower() in ("1", "true", "yes")
SHARE_MODE = os.getenv("CONTRACTGUARD_SHARE_MODE", "0").lower() in ("1", "true", "yes")

app = FastAPI(title="ContractGuard API", version="1.0.0")
ai_client.load_config(CONFIG_PATH)
STORE = Store(DB_PATH)
SECURITY = {"deterministic_only": SHARE_MODE, "external_scope": "masked_evidence_only"}

def _load_contract_cache():
    """从 SQLite 重载合同缓存，让本机与公网进程共享最新数据。"""
    loaded = STORE.load_contracts()
    if not DEMO_MODE:
        # Older versions persisted built-in demo contracts unconditionally. Preserve
        # those database rows, but do not expose or analyze them in real-data mode.
        loaded = {cid: rec for cid, rec in loaded.items() if rec.get("source") != "builtin"}
    return loaded


# 合同仓库：id -> {id, name, type, source, text, parsed}
CONTRACTS = _load_contract_cache()


def _refresh_persisted_evidence():
    """升级旧记录中的证据坐标，避免服务重启后继续展示历史截断片段。"""
    for rec in CONTRACTS.values():
        parsed = rec.get("parsed") or {}
        fields = parsed.get("fields") or {}
        if not fields or not rec.get("text"):
            continue
        try:
            parsed["document"] = _attach_evidence_spans(fields, rec["text"])
            rebuilt_transactions = transaction_indexer.build_transactions(
                rec["text"], fields, parsed.get("contract_type") or {})
            parsed["transactions"] = rebuilt_transactions.get("transactions", [])
            parsed["transaction_count"] = rebuilt_transactions.get("count", 0)
            auth = fields.get("info_auth") or {}
            auth_value = auth.get("value") if isinstance(auth.get("value"), dict) else {}
            retention = auth_value.get("retention_years")
            # 旧版本曾从整个公告段落抓取“2025 年”作为保留期限；仅对未人工确认的
            # 可疑字段用最新规则重算，保留其他用户修正和分析结果。
            if auth.get("status") != "confirmed_by_user" and isinstance(retention, (int, float)) and (retention > 120 or 1900 <= retention <= 2100):
                refreshed = parser.parse_contract(rec["text"], rec.get("name") or "未命名合同", use_llm=False)
                fresh_auth = (refreshed.get("fields") or {}).get("info_auth")
                if fresh_auth:
                    fields["info_auth"] = fresh_auth
            rec["parsed"] = parsed
            STORE.save_contract(rec)
        except Exception:
            # 单条旧记录升级失败不影响合同库启动；新上传仍使用最新绑定逻辑。
            continue


_refresh_persisted_evidence()


@app.middleware("http")
async def refresh_contract_cache(request, call_next):
    """每次请求前读取共享数据库，避免公网进程保留旧的内存合同列表。"""
    global CONTRACTS
    CONTRACTS = _load_contract_cache()
    return await call_next(request)


# ---------- 工具 ----------

def _add_contract(cid, name, ctype, source, text, meta=None, use_llm=False, file_diagnostics=None,
                  file_path=None, original_filename=None, display_name=None):
    parsed = parser.parse_contract(text, name, use_llm=use_llm)
    if meta:
        # 样例合同：用人工核对的元数据补强解析结果
        if meta.get("principal") and parsed.get("principal") is None:
            parsed["principal"] = meta["principal"]
        if meta.get("annual_rate") is not None and parsed.get("annual_rate") is None:
            parsed["annual_rate"] = meta["annual_rate"]
        if meta.get("term_months") and parsed.get("term_months") is None:
            parsed["term_months"] = meta["term_months"]
        if meta.get("repayment"):
            parsed["repayment"] = meta["repayment"]
        if meta.get("fees"):
            parsed["fees"] = meta["fees"]
        if meta.get("prepayment_penalty") and not parsed.get("prepayment_penalty"):
            p = meta.get("principal") or parsed.get("principal") or 0
            parsed["prepayment_penalty"] = {"rate": meta["prepayment_penalty"],
                                           "amount": p * meta["prepayment_penalty"],
                                           "source": "合同约定（样例标注）"}
        if meta.get("guarantee") and parsed.get("guarantee") == "未明确约定":
            parsed["guarantee"] = meta["guarantee"]
        parsed["confidence"] = 1.0
    display_name = (display_name or name or "未命名合同").strip()[:80]
    rec = {"id": cid, "name": display_name, "display_name": display_name,
           "original_filename": original_filename or name, "type": ctype, "source": source,
           "text": text, "parsed": parsed, "created_at": time.time(),
           "file_diagnostics": file_diagnostics or {"format": "text", "status": "success"},
           "file_path": file_path, "access_level": "owner", "analysis_version": 1}
    CONTRACTS[cid] = rec
    STORE.save_contract(rec)
    STORE.audit("contract_created", cid, {"source": source, "type": ctype})
    return rec


def _public(rec, detail=False):
    parsed = rec["parsed"]
    ctype = parsed.get("contract_type") or {}
    fields = parsed.get("fields") or {}
    out = {
        "id": rec["id"], "name": rec.get("display_name") or rec["name"],
        "display_name": rec.get("display_name") or rec["name"],
        "original_filename": rec.get("original_filename") or rec["name"],
        "type": rec["type"], "source": rec["source"],
        "principal": parsed.get("principal"), "annual_rate": parsed.get("annual_rate"),
        "term_months": parsed.get("term_months"), "repayment": parsed.get("repayment"),
        "guarantee": parsed.get("guarantee"), "fees": parsed.get("fees", []),
        "prepayment_penalty": parsed.get("prepayment_penalty"),
        "covenants": parsed.get("covenants", []),
        "confidence": parsed.get("confidence"), "word_count": parsed.get("word_count"),
        "clause_count": len(parsed.get("clauses", [])),
        "created_at": rec["created_at"],
        "analysis_version": rec.get("analysis_version", 1),
        "access_level": rec.get("access_level", "owner"),
        "file_diagnostics": rec.get("file_diagnostics") or {},
        # 通用结构化解析结果
        "type_key": ctype.get("key"),
        "type_label": ctype.get("label") or rec["type"],
        "type_confidence": ctype.get("confidence"),
        "type_evidence": ctype.get("evidence"),
        "document_type": parsed.get("document_type"),
        "transaction_type": parsed.get("transaction_type"),
        "transaction_count": parsed.get("transaction_count", 0),
        "field_count": parsed.get("field_count"),
        "pipeline": parsed.get("pipeline"),
        "field_status": {key: (fields.get(key) or {}).get("status")
                         for key in ("amount", "currency", "rate", "term", "payment_method")},
    }
    if detail:
        out["fields"] = fields
        out["document"] = parsed.get("document") or {}
        out["transactions"] = parsed.get("transactions") or []
    return out


# ---------- 初始化样例合同 ----------

if DEMO_MODE:
    for sid, sc in samples.SAMPLE_CONTRACTS.items():
        if sid not in CONTRACTS:
            _add_contract(sid, sc["name"], sc.get("type", "合同"), "builtin", sc["content"], sc.get("meta"))


# ---------- API：合同管理 ----------

class ImportTextBody(BaseModel):
    name: str
    text: str


class RenameBody(BaseModel):
    display_name: str


@app.get("/api/contracts")
def list_contracts():
    return {"contracts": [_public(r) for r in CONTRACTS.values()]}


@app.patch("/api/contracts/{cid}/name")
def rename_contract(cid: str, body: RenameBody):
    rec = CONTRACTS.get(cid)
    if not rec:
        raise HTTPException(404, "合同不存在")
    new_name = (body.display_name or "").strip()
    if not new_name:
        raise HTTPException(400, "名称不能为空")
    if len(new_name) > 80:
        raise HTTPException(400, "名称不能超过 80 个字符")
    old_name = rec.get("display_name") or rec.get("name") or "未命名合同"
    if new_name == old_name:
        return {"ok": True, "contract": _public(rec)}
    # 记录轻量重命名历史；原始文件名和合同 ID 永不改变。
    STORE.revision(cid, "display_name", old_name, new_name, "user_rename")
    rec["display_name"] = new_name
    rec["name"] = new_name  # 兼容所有仍消费 name 的页面和分析接口
    rec["analysis_version"] = rec.get("analysis_version", 1) + 1
    STORE.save_contract(rec)
    STORE.audit("contract_renamed", cid, {"old_display_name": old_name, "display_name": new_name})
    return {"ok": True, "contract": _public(rec)}


@app.get("/api/contracts/{cid}")
def get_contract(cid: str):
    rec = CONTRACTS.get(cid)
    if not rec:
        raise HTTPException(404, "合同不存在")
    try:
        orch = orchestrator.orchestrate(rec["parsed"])
    except Exception as exc:  # 调度失败不阻塞详情展示
        orch = {"plan": {"steps": [], "cost_ready": False,
                         "advice": "Agent 调度异常：%s" % exc},
                "review": [], "risks": [], "risk_summary": {},
                "diagnostics": rec["parsed"].get("diagnostics") or {},
                "consistency": rec["parsed"].get("consistency") or {"conflicts": []}}
    return {"contract": _public(rec, detail=True), "text": rec["text"],
            "clauses": rec["parsed"].get("clauses", []), "orchestration": orch,
            "revisions": STORE.revisions(cid), "review_status": STORE.review_statuses(cid)}


@app.delete("/api/contracts/{cid}")
def delete_contract(cid: str):
    if cid in samples.SAMPLE_CONTRACTS:
        raise HTTPException(400, "内置样例合同不可删除")
    if cid not in CONTRACTS:
        raise HTTPException(404, "合同不存在")
    rec = CONTRACTS.pop(cid)
    if rec.get("file_path") and os.path.exists(rec["file_path"]):
        os.unlink(rec["file_path"])
    STORE.delete_contract(cid)
    return {"ok": True, "purged": True}


@app.post("/api/contracts/upload")
async def upload_contract(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "上传失败：文件内容为空")
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "上传失败：文件超过 15MB 限制")
    try:
        extracted = file_parser.extract_document(file.filename, data, file.content_type)
    except PermissionError as exc:
        raise HTTPException(422, "解析失败：%s" % exc)
    except ValueError as exc:
        raise HTTPException(415, "上传失败：%s" % exc)
    except RuntimeError as exc:
        raise HTTPException(503, "解析服务不可用：%s" % exc)
    except Exception as exc:
        raise HTTPException(422, "解析失败：%s" % str(exc)[:160])
    text = extracted["text"]
    if not text or len(re.sub(r"\s", "", text)) < 30:
        status = (extracted.get("diagnostics") or {}).get("status")
        if status == "needs_text_version":
            raise HTTPException(422, "解析失败：PDF 为扫描版或无可提取文本，请上传文本型 PDF 或 DOCX")
        raise HTTPException(422, "解析失败：未提取到足够的合同文本")
    cid = "UP" + uuid.uuid4().hex[:8].upper()
    name = os.path.splitext(file.filename)[0][:60]
    ext = os.path.splitext(file.filename)[1].lower()
    path = os.path.join(UPLOAD_DIR, cid + ext)
    fd, tmp_path = tempfile.mkstemp(prefix=cid + "-", suffix=ext, dir=UPLOAD_DIR)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    allow_llm = ai_client.is_configured() and not SECURITY["deterministic_only"]
    rec = _add_contract(cid, name, "上传合同", "upload", text, use_llm=allow_llm,
                        file_diagnostics=extracted["diagnostics"], file_path=path,
                        original_filename=file.filename, display_name=name)
    try:
        orch = orchestrator.orchestrate(rec["parsed"])
    except Exception:
        orch = None
    out = {"contract": _public(rec, detail=True), "clauses": rec["parsed"].get("clauses", [])}
    if orch:
        out["orchestration"] = orch
    return out


@app.post("/api/contracts/import-text")
def import_text(body: ImportTextBody):
    if len(re.sub(r"\s", "", body.text)) < 30:
        raise HTTPException(400, "文本过短，无法识别合同要素")
    cid = "TX" + uuid.uuid4().hex[:8].upper()
    allow_llm = ai_client.is_configured() and not SECURITY["deterministic_only"]
    rec = _add_contract(cid, body.name[:60] or "粘贴合同", "粘贴文本", "text", body.text, use_llm=allow_llm)
    try:
        orch = orchestrator.orchestrate(rec["parsed"])
    except Exception:
        orch = None
    out = {"contract": _public(rec, detail=True), "clauses": rec["parsed"].get("clauses", [])}
    if orch:
        out["orchestration"] = orch
    return out


class FieldUpdateBody(BaseModel):
    value: object = None
    display: Optional[str] = None
    source: str = "user_edit"
    resolution: str = "adopt_value"  # adopt_value / uncertain / legal_review


def _sync_legacy(parsed, key, value):
    if key == "amount":
        parsed["principal"] = float(value) if value not in (None, "") else None
    elif key == "actual_financing_amount":
        parsed["principal"] = float(value) if value not in (None, "") else None
        # 保理场景的通用金额字段与专属实际融资金额必须保持同一业务角色。
        if parsed.get("fields", {}).get("amount"):
            parsed["fields"]["amount"].update(value=parsed["principal"], found=value not in (None, ""),
                                                role="actual_financing_amount")
    elif key == "rate":
        v = float(value) if value not in (None, "") else None
        parsed["annual_rate"] = (v / 100.0 if v is not None and v >= 1 else v)
    elif key == "term": parsed["term_months"] = int(float(value)) if value not in (None, "") else None
    elif key == "single_financing_term":
        if value not in (None, "") and re.fullmatch(r"\d+(?:\.\d+)?", str(value).strip()):
            parsed["term_months"] = int(float(value))
            if parsed.get("fields", {}).get("term"):
                parsed["fields"]["term"].update(value=parsed["term_months"], found=True,
                                                  status="confirmed_by_user", role="actual_financing_term")
    elif key == "payment_method": parsed["repayment"] = str(value) if value not in (None, "") else None
    elif key == "guarantee": parsed["guarantee"] = str(value) if value not in (None, "") else None


def _rerun_single(rec):
    orch = orchestrator.orchestrate(rec["parsed"])
    result = {"orchestration": orch}
    if "costlens" in orch.get("tool_results", {}):
        result["costlens"] = orch["tool_results"]["costlens"]
    result["risk"] = orch.get("tool_results", {}).get("riskengine")
    peers = [x for x in CONTRACTS.values() if x["id"] != rec["id"] and
             x.get("parsed", {}).get("principal") and x.get("parsed", {}).get("annual_rate") is not None and
             x.get("parsed", {}).get("term_months")][:1]
    if peers and rec["parsed"].get("principal") and rec["parsed"].get("annual_rate") is not None and rec["parsed"].get("term_months"):
        result["scenario_compare"] = scenario.compare_contracts([rec, peers[0]], "hold")
    rec["analysis_version"] = rec.get("analysis_version", 1) + 1
    STORE.save_contract(rec)
    STORE.save_analysis(rec["id"], "auto_recompute", result, rec["analysis_version"])
    return result


@app.patch("/api/contracts/{cid}/fields/{field_key}")
def update_field(cid: str, field_key: str, body: FieldUpdateBody):
    rec = CONTRACTS.get(cid)
    if not rec: raise HTTPException(404, "合同不存在")
    fields = rec["parsed"].get("fields") or {}
    if field_key not in fields: raise HTTPException(404, "字段不存在")
    old = json.loads(json.dumps(fields[field_key], ensure_ascii=False))
    f = fields[field_key]
    history = f.setdefault("correction_history", [])
    normalized_value = body.value
    try:
        if field_key in {"amount", "actual_financing_amount", "credit_limit", "term", "credit_term"} and body.value not in (None, ""):
            normalized_value = float(body.value)
            if field_key in {"term", "credit_term"}:
                normalized_value = int(normalized_value)
        if field_key == "rate" and body.value not in (None, ""):
            raw_rate = float(body.value)
            normalized_value = raw_rate / 100.0 if raw_rate >= 1 else raw_rate
    except (TypeError, ValueError):
        raise HTTPException(400, "字段修正失败：请输入有效的数字")
    change = {"old_value": f.get("value"), "new_value": normalized_value, "source": body.source,
              "changed_at": time.time(), "original_evidence_preserved": True}
    history.append(change)
    if body.resolution in {"uncertain", "legal_review"}:
        f["status"] = "uncertain"
        f["note"] = "用户暂不确定，已进入人工复核队列" if body.resolution == "uncertain" else "已提交人工复核，暂不采信该字段"
    else:
        f.update({"found": True, "value": normalized_value, "display": body.display or str(normalized_value),
                  "status": "confirmed_by_user", "method": "user_correction", "confidence": 1.0})
        _sync_legacy(rec["parsed"], field_key, normalized_value)
    STORE.revision(cid, field_key, old, f, body.source)
    STORE.audit("field_updated", cid, {"field": field_key, "resolution": body.resolution})
    recomputed = _rerun_single(rec)
    return {"ok": True, "field": f, "revision": change, "recomputed": recomputed,
            "analysis_version": rec["analysis_version"]}


class ReviewBody(BaseModel):
    status: str
    note: str = ""


@app.post("/api/contracts/{cid}/reviews/{item_id}")
def update_review(cid: str, item_id: str, body: ReviewBody):
    if cid not in CONTRACTS: raise HTTPException(404, "合同不存在")
    allowed = {"submitted_legal", "confirmed", "accepted_risk", "pending", "material_change", "wording_only"}
    if body.status not in allowed: raise HTTPException(400, "无效复核状态")
    STORE.set_review(cid, item_id, body.status, body.note)
    STORE.audit("review_status_updated", cid, {"item": item_id, "status": body.status})
    return {"ok": True, "item_id": item_id, "status": body.status}


@app.post("/api/contracts/{cid}/reparse")
def reparse_contract(cid: str):
    rec = CONTRACTS.get(cid)
    if not rec: raise HTTPException(404, "合同不存在")
    corrections = {k: json.loads(json.dumps(v)) for k, v in (rec["parsed"].get("fields") or {}).items()
                   if v.get("status") == "confirmed_by_user"}
    parsed = parser.parse_contract(rec["text"], rec["name"], use_llm=ai_client.is_configured() and not SECURITY["deterministic_only"])
    for key, corrected in corrections.items():
        if key in parsed.get("fields", {}):
            parsed["fields"][key] = corrected
            _sync_legacy(parsed, key, corrected.get("value"))
    rec["parsed"] = parsed
    result = _rerun_single(rec)
    STORE.audit("contract_reparsed", cid, {"preserved_user_corrections": len(corrections)})
    return {"contract": _public(rec, detail=True), "recomputed": result}


@app.get("/api/contracts/{cid}/export")
def export_contract(cid: str):
    rec = CONTRACTS.get(cid)
    if not rec: raise HTTPException(404, "合同不存在")
    return JSONResponse({"contract": _public(rec, detail=True), "text": rec["text"],
                         "revisions": STORE.revisions(cid), "reviews": STORE.review_statuses(cid),
                         "financial_history": STORE.financial_history(cid)})


class AccessBody(BaseModel):
    access_level: str


@app.patch("/api/contracts/{cid}/access")
def update_access(cid: str, body: AccessBody):
    rec = CONTRACTS.get(cid)
    if not rec: raise HTTPException(404, "合同不存在")
    if body.access_level not in {"owner", "internal_read", "restricted"}: raise HTTPException(400, "无效权限")
    rec["access_level"] = body.access_level; STORE.save_contract(rec); STORE.audit("access_updated", cid, {"level": body.access_level})
    return {"ok": True, "access_level": body.access_level}


@app.get("/api/audit-log")
def audit_log(contract_id: Optional[str] = None):
    return {"events": STORE.audits(contract_id)}


# ---------- API：CostLens ----------

class CostBody(BaseModel):
    contract_ids: List[str]
    # 多交易公告可按合同 ID 选择具体 transaction_id；未选择时保持阻断而不拼接。
    transaction_ids: Optional[Dict[str, str]] = None


@app.post("/api/analyze/costlens")
def api_costlens(body: CostBody):
    out = []
    for cid in body.contract_ids:
        rec = CONTRACTS.get(cid)
        if not rec:
            continue
        selected = (body.transaction_ids or {}).get(cid)
        parsed = rec["parsed"]
        if selected:
            parsed = costlens.project_transaction(parsed, selected)
            if parsed is None:
                raise HTTPException(400, "合同 %s 未找到所选交易" % rec["name"])
        result = costlens.analyze_cost(parsed)
        out.append({"id": cid, "name": rec["name"], "result": result})
    if not out:
        raise HTTPException(400, "请选择至少一份有效合同")
    return {"analyses": out}


# ---------- API：ScenarioCompare ----------

class ScenarioBody(BaseModel):
    contract_ids: List[str]
    scenario: str = "hold"           # hold / prepay / rate_up / stress / overdue
    month: int = 12
    delta_pp: float = 1.0
    months_late: int = 3
    compare_all: bool = False
    transaction_ids: Optional[Dict[str, str]] = None


@app.post("/api/analyze/scenario")
def api_scenario(body: ScenarioBody):
    if body.scenario not in {"hold", "prepay", "rate_up", "stress", "overdue"}:
        raise HTTPException(400, "未知情景类型，请选择持有到期、提前还款、利率上升、现金流紧张或逾期")
    if body.month < 1 or body.delta_pp < 0 or body.months_late < 0:
        raise HTTPException(400, "情景参数必须为有效的非负数")
    contracts = []
    for cid in body.contract_ids:
        rec = CONTRACTS.get(cid)
        if rec:
            selected = (body.transaction_ids or {}).get(cid)
            parsed = costlens.project_transaction(rec["parsed"], selected) if selected else rec["parsed"]
            if selected and parsed is None:
                raise HTTPException(400, "合同 %s 未找到所选交易" % rec["name"])
            contracts.append({"id": cid, "name": rec["name"], "parsed": parsed})
    if len(contracts) < 1:
        raise HTTPException(400, "请选择合同")
    blocked = []
    for item in contracts:
        gaps = costlens.missing_detail(item["parsed"])
        if gaps:
            blocked.append({"id": item["id"], "name": item["name"], "missing_detail": gaps})
    if blocked:
        return {"mode": "blocked", "reason": "当前信息不足，暂无法进行可靠的情景模拟。",
                "contracts": blocked}
    params = {"month": body.month, "delta_pp": body.delta_pp, "months_late": body.months_late}
    if body.compare_all and len(contracts) >= 2:
        result = scenario.flip_analysis(contracts, params)
        return {"mode": "flip", **result}
    results = scenario.compare_contracts(contracts, body.scenario, params)
    return {"mode": "single", "scenario": body.scenario, "results": results}


# ---------- API：CovenantGuard ----------

class CovenantFin(BaseModel):
    label: str
    total_liabilities: Optional[float] = None
    total_assets: Optional[float] = None
    current_assets: Optional[float] = None
    current_liabilities: Optional[float] = None
    quick_assets: Optional[float] = None


class CovenantBody(BaseModel):
    contract_ids: List[str]
    financials: Optional[List[CovenantFin]] = None
    source_name: str = "用户录入"


@app.post("/api/analyze/covenant")
def api_covenant(body: CovenantBody):
    if not body.financials:
        raise HTTPException(400, "未提供财务数据；CovenantGuard 不会自动使用演示数据")
    fins = [f.dict() for f in body.financials]
    out = []
    for cid in body.contract_ids:
        rec = CONTRACTS.get(cid)
        if not rec:
            continue
        covenants = rec["parsed"].get("covenants", [])
        if not covenants:
            continue
        results = covenant.monitor(covenants, fins)
        out.append({"id": cid, "name": rec["name"], "results": results,
                    "summary": covenant.summary(results),
                    "quarters": [f.get("label") for f in fins]})
        STORE.save_financials(cid, body.source_name, fins, out[-1])
        STORE.audit("covenant_recomputed", cid, {"source": body.source_name, "periods": len(fins)})
    if not out:
        raise HTTPException(400, "所选合同未识别到财务契约条款（如资产负债率上限），请换用含契约条款的合同")
    return {"monitors": out}


FIN_HEADER_MAP = {
    "期间": "label", "季度": "label", "日期": "label", "period": "label", "quarter": "label",
    "总负债": "total_liabilities", "负债合计": "total_liabilities", "total liabilities": "total_liabilities",
    "总资产": "total_assets", "资产总计": "total_assets", "total assets": "total_assets",
    "流动资产": "current_assets", "流动资产合计": "current_assets", "current assets": "current_assets",
    "流动负债": "current_liabilities", "流动负债合计": "current_liabilities", "current liabilities": "current_liabilities",
    "速动资产": "quick_assets", "quick assets": "quick_assets", "quick_assets": "quick_assets",
}


def _map_fin_rows(headers, rows):
    mapped = []
    keys = [FIN_HEADER_MAP.get(str(h).strip().lower(), FIN_HEADER_MAP.get(str(h).strip())) for h in headers]
    for row in rows:
        item = {}
        for key, value in zip(keys, row):
            if not key: continue
            if key == "label": item[key] = str(value or "")
            else:
                try: item[key] = float(str(value).replace(",", "")) if value not in (None, "") else None
                except ValueError: item[key] = None
        if item.get("label"): mapped.append(item)
    return mapped


def _normalize_financial_rows(rows):
    """统一 JSON/CSV/XLSX 财务数据类型，无法转换的科目保留为空以便标记无法判断。"""
    if not isinstance(rows, list):
        return rows
    numeric = {"total_liabilities", "total_assets", "current_assets", "current_liabilities", "quick_assets"}
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = {"label": str(row.get("label") or row.get("期间") or row.get("日期") or "")}
        for key in numeric:
            value = row.get(key)
            if value in (None, ""):
                item[key] = None
                continue
            try:
                item[key] = float(str(value).replace(",", ""))
            except (TypeError, ValueError):
                item[key] = None
        if item["label"]:
            normalized.append(item)
    return normalized


@app.post("/api/contracts/{cid}/financials/upload")
async def upload_financials(cid: str, file: UploadFile = File(...)):
    rec = CONTRACTS.get(cid)
    if not rec: raise HTTPException(404, "合同不存在")
    data = await file.read(); ext = os.path.splitext(file.filename or "")[1].lower()
    if not data: raise HTTPException(400, "财务数据上传失败：文件为空")
    if len(data) > 5 * 1024 * 1024: raise HTTPException(413, "财务数据上传失败：文件超过 5MB 限制")
    try:
        if ext == ".json":
            raw = json.loads(data.decode("utf-8")); fins = raw.get("financials", raw) if isinstance(raw, dict) else raw
            fins = _normalize_financial_rows(fins)
        elif ext == ".csv":
            text = data.decode("utf-8-sig"); table = list(csv.reader(io.StringIO(text))); fins = _map_fin_rows(table[0], table[1:])
        elif ext == ".xlsx":
            from openpyxl import load_workbook
            ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).active
            table = list(ws.iter_rows(values_only=True)); fins = _map_fin_rows(table[0], table[1:])
        else: raise ValueError("仅支持 CSV、JSON、XLSX")
    except Exception as exc:
        raise HTTPException(400, "财务数据解析失败：%s" % exc)
    if not isinstance(fins, list) or not fins: raise HTTPException(400, "未识别到有效财务数据行")
    covenants = rec["parsed"].get("covenants") or []
    results = covenant.monitor(covenants, fins) if covenants else []
    payload = {"id": cid, "name": rec["name"], "results": results, "summary": covenant.summary(results),
               "financials": fins, "source_name": file.filename, "mapped_headers": sorted({k for f in fins for k in f})}
    STORE.save_financials(cid, file.filename, fins, payload); STORE.audit("financial_file_uploaded", cid, {"name": file.filename, "rows": len(fins)})
    return payload


@app.get("/api/contracts/{cid}/financials/history")
def financial_history(cid: str):
    if cid not in CONTRACTS: raise HTTPException(404, "合同不存在")
    return {"history": STORE.financial_history(cid)}


# ---------- API：ContractDiff ----------

class DiffBody(BaseModel):
    old_id: str
    new_id: str
    remaining_principal: Optional[float] = None


@app.post("/api/analyze/diff")
def api_diff(body: DiffBody):
    if body.old_id == body.new_id:
        raise HTTPException(400, "请选择两份不同的合同版本")
    old = CONTRACTS.get(body.old_id)
    new = CONTRACTS.get(body.new_id)
    if not old or not new:
        raise HTTPException(400, "请选择两份合同（旧版与新版）")
    result = diffengine.diff_contracts(old["parsed"], new["parsed"],
                                       remaining_principal=body.remaining_principal)
    return {
        "old": {"id": old["id"], "name": old["name"]},
        "new": {"id": new["id"], "name": new["name"]},
        **result,
    }


# ---------- API：四引擎综合评估 ----------

class ComprehensiveBody(BaseModel):
    contract_ids: List[str]
    prepay_month: int = 12
    financials: Optional[List[CovenantFin]] = None


@app.post("/api/analyze/comprehensive")
def api_comprehensive(body: ComprehensiveBody):
    records = [CONTRACTS[cid] for cid in body.contract_ids if cid in CONTRACTS]
    if len(records) < 2:
        raise HTTPException(400, "一键综合评估至少需要两份有效合同")
    fins = [f.dict() for f in body.financials] if body.financials else []
    try:
        return assessment.analyze_bundle(records, fins, max(1, body.prepay_month))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ---------- API：任务驱动 Agent ----------

class AgentTaskBody(BaseModel):
    task: str
    contract_ids: List[str]
    financials: Optional[List[CovenantFin]] = None


@app.post("/api/agent/tasks")
def run_agent_task(body: AgentTaskBody):
    records = [CONTRACTS[cid] for cid in body.contract_ids if cid in CONTRACTS]
    if not records: raise HTTPException(400, "请先选择合同")
    task = body.task.strip()
    tool_trace, results, review = [], {}, []

    def trace(tool, status, why, kind="deterministic"):
        tool_trace.append({"tool": tool, "status": status, "reason": why, "result_type": kind})

    if any(k in task for k in ("审查", "风险", "谈判", "复核")):
        agents = []
        for rec in records:
            out = orchestrator.orchestrate(rec["parsed"]); agents.append({"id": rec["id"], "name": rec["name"], **out})
            review.extend(out.get("review", []))
        results["reviews"] = agents
        trace("Risk Engine", "called", "任务需要识别风险、证据与人工复核项")
    else:
        trace("Risk Engine", "skipped", "当前目标未要求完整风险审查")

    if any(k in task for k in ("成本", "融资", "谈判", "审查")):
        analyses = []
        for rec in records:
            r = costlens.analyze_cost(rec["parsed"])
            if not r.get("hidden"): analyses.append({"id": rec["id"], "name": rec["name"], "result": r})
        if analyses:
            results["costlens"] = analyses; trace("Cost Lens", "called", "所选合同属于融资类且可计算要素齐备")
        else: trace("Cost Lens", "skipped", "合同类型不产生融资现金流或关键要素不足")
    else: trace("Cost Lens", "skipped", "用户目标不涉及融资成本")

    if len(records) >= 2 and any(k in task for k in ("比较", "方案", "融资", "谈判")):
        wrapped = [{"id": r["id"], "name": r["name"], "parsed": r["parsed"]} for r in records]
        try:
            scenario_result = scenario.flip_analysis(wrapped, {"month": 12})
            if scenario_result.get("blocked"):
                trace("Scenario Compare", "degraded", "部分合同缺少可确认的实际现金流要素，已阻止排名比较")
            else:
                results["scenario"] = scenario_result
                trace("Scenario Compare", "called", "检测到多份融资合同，需要统一情景比较")
        except Exception:
            trace("Scenario Compare", "skipped", "部分合同缺少可计算要素")
    else: trace("Scenario Compare", "skipped", "需要至少两份可测算合同且任务为方案比较")

    if len(records) == 2 and any(k in task for k in ("版本", "变化", "新旧")):
        results["diff"] = diffengine.diff_contracts(records[0]["parsed"], records[1]["parsed"])
        trace("Contract Diff", "called", "用户明确要求检查两份合同的版本变化")
    else: trace("Contract Diff", "skipped", "仅在两份文本且目标为版本变化时调用，避免把不同方案误当版本")

    if body.financials and any(k in task for k in ("契约", "财务", "监控")):
        fins = [f.dict() for f in body.financials]
        monitored = []
        for rec in records:
            if rec["parsed"].get("covenants"):
                rs = covenant.monitor(rec["parsed"]["covenants"], fins)
                monitored.append({"id": rec["id"], "name": rec["name"], "results": rs, "summary": covenant.summary(rs)})
        results["covenant"] = monitored; trace("Covenant Guard", "called" if monitored else "skipped", "使用用户提供的财务数据计算合同契约指标")
    else: trace("Covenant Guard", "skipped", "未提供财务数据或任务不涉及契约监控")

    ai_used = ai_client.is_configured() and not SECURITY["deterministic_only"]
    trace("External LLM", "available" if ai_used else "skipped", "仅用于语义解释；关键金融计算均由确定性代码完成", "ai_judgment")
    STORE.audit("agent_task_run", records[0]["id"], {"task": task, "contracts": [r["id"] for r in records], "tools": tool_trace})
    return {"task": task, "contracts": [{"id": r["id"], "name": r["name"]} for r in records],
            "tool_trace": tool_trace, "results": results, "human_review": review,
            "external_llm": {"used": ai_used, "scope": SECURITY["external_scope"] if ai_used else "none"}}


class SecurityBody(BaseModel):
    deterministic_only: bool
    external_scope: str = "masked_evidence_only"


@app.get("/api/security")
def security_status():
    return {**SECURITY, "local_processing": True, "external_model_configured": ai_client.is_configured(),
            "external_data": "脱敏后的必要证据片段" if not SECURITY["deterministic_only"] else "不发送"}


@app.post("/api/security")
def update_security(body: SecurityBody):
    SECURITY.update({"deterministic_only": body.deterministic_only, "external_scope": body.external_scope})
    STORE.audit("security_setting_updated", None, SECURITY)
    return {"ok": True, **SECURITY}


# ---------- API：AI ----------

class AIConfigBody(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    proxy: Optional[str] = None
    test: bool = False


class AIChatBody(BaseModel):
    messages: List[dict]
    context: Optional[str] = None


@app.get("/api/ai/status")
def ai_status():
    return ai_client.status()


@app.post("/api/ai/config")
def ai_config(body: AIConfigBody):
    if body.provider:
        ai_client.set_provider(body.provider)
    if body.model:
        ai_client.CONFIG["model"] = body.model.strip()
    if body.proxy is not None:
        ai_client.CONFIG["proxy"] = body.proxy.strip()
    ai_client.save_config(CONFIG_PATH)
    st = ai_client.status()
    test_result = None
    if body.test and st["configured"]:
        test_result = ai_client.test_connection()
    return {"ok": True, "status": st, "test": test_result}


@app.post("/api/ai/chat")
def ai_chat(body: AIChatBody):
    if not body.messages or len(body.messages) > 30:
        raise HTTPException(400, "消息数量必须为 1–30 条")
    if sum(len(str(m.get("content", ""))) for m in body.messages) > 40_000:
        raise HTTPException(400, "对话内容过长，请精简后重试")
    def gen():
        try:
            first = True
            stream = (ai_client._builtin_engine_stream(body.messages, body.context)
                      if SECURITY["deterministic_only"] else ai_client.chat_stream(body.messages, file_parser.mask_sensitive(body.context or "")))
            for chunk in stream:
                if first:
                    yield "data: " + json.dumps({"type": "meta", "engine": "builtin" if SECURITY["deterministic_only"] else ("llm" if ai_client.is_configured() else "builtin")},
                                                 ensure_ascii=False) + "\n\n"
                    first = False
                yield "data: " + json.dumps({"type": "delta", "content": chunk}, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"type": "done"}, ensure_ascii=False) + "\n\n"
        except Exception as e:
            yield "data: " + json.dumps({"type": "error", "message": str(e)[:200]}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/ai/chat-once")
def ai_chat_once(body: AIChatBody):
    """Non-streaming fallback for Cloudflare Quick Tunnel, which does not support SSE."""
    if not body.messages or len(body.messages) > 30:
        raise HTTPException(400, "消息数量必须为 1–30 条")
    if sum(len(str(m.get("content", ""))) for m in body.messages) > 40_000:
        raise HTTPException(400, "对话内容过长，请精简后重试")
    try:
        stream = (ai_client._builtin_engine_stream(body.messages, body.context)
                  if SECURITY["deterministic_only"] else
                  ai_client.chat_stream(body.messages, file_parser.mask_sensitive(body.context or "")))
        return {"content": "".join(stream), "engine": "builtin" if SECURITY["deterministic_only"]
                else ("llm" if ai_client.is_configured() else "builtin")}
    except Exception as exc:
        raise HTTPException(502, "AI 服务暂时不可用：%s" % str(exc)[:160])


@app.get("/api/health")
def health():
    """用于演示环境快速检查：确定性引擎始终可用，AI 可独立降级。"""
    st = ai_client.status()
    db = STORE.health()
    return {"ok": db["ok"], "mode": "share" if SHARE_MODE else ("demo" if DEMO_MODE else "local"),
            "demo_mode": DEMO_MODE, "share_mode": SHARE_MODE, "contracts": len(CONTRACTS),
            "database": db, "ai": {k: st[k] for k in ("configured", "provider", "label", "model")}}


# ---------- 静态资源 ----------

app.mount("/", StaticFiles(directory=os.path.join(BASE_DIR, "static"), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.getenv("CONTRACTGUARD_HOST", "127.0.0.1"),
                port=int(os.getenv("CONTRACTGUARD_PORT", "8899")))
