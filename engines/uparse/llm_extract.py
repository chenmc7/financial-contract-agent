# -*- coding: utf-8 -*-
"""LLM 语义抽取补强层：
- 仅当已配置实时 LLM 时启用；规则抽取结果优先，LLM 只补齐 found=False 的字段
- 任何失败（未配置 / 超时 / JSON 解析失败 / 配额耗尽）均静默降级，不影响规则结果
"""
import json
import re
import threading

from . import standardize as std
from ..file_parser import mask_sensitive

try:
    from engines import ai_client
except ImportError:
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from engines import ai_client

LLM_TIMEOUT = 25  # 秒

PROMPT_TMPL = """你是金融合同结构化解析引擎。下面是一份《{ctype}》的合同文本。请仅针对下列【未发现】的字段，从原文中抽取信息并以 JSON 数组返回，每个元素形如：
{{"key": "<字段key>", "value": "<标准化值>", "display": "<给业务人员看的展示>", "quote": "<原文引用片段，必须逐字摘自合同，≤60字>"}}

未发现字段：{missing}

合同文本（可能截断）：
---
{doc}
---

要求：
1. 只返回 JSON 数组，不要任何解释性文字；
2. 原文中确实没有的字段不要返回；
3. 金额统一换算为元（数字）；利率用小数（4.35% → 0.0435）；日期用 YYYY-MM-DD；期限用月数；
4. quote 必须逐字摘自原文，用于人工复核。"""


def _collect_stream(messages, timeout=LLM_TIMEOUT):
    """流式收集 LLM 完整回复（带超时保护）"""
    buf = []

    def run():
        try:
            for chunk in ai_client.chat_stream(messages, context=None):
                buf.append(chunk)
        except Exception as e:
            buf.append({"__error__": str(e)})

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, "timeout"
    if buf and isinstance(buf[0], dict) and "__error__" in buf[0]:
        return None, buf[0]["__error__"]
    return "".join(x for x in buf if isinstance(x, str)), None


def _parse_json_array(text):
    if not text:
        return None
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
        return arr if isinstance(arr, list) else None
    except (ValueError, TypeError):
        return None


def _verified_evidence(doc, quote):
    """只接受能在合同原文中反向定位的逐字引文，防止模型虚构证据。"""
    quote = (quote or "").strip()
    if len(quote) < 2:
        return None
    needle = re.sub(r"\s+", "", quote)
    for section in doc.get("sections", []):
        blob = "%s\n%s" % (section.get("title", ""), section.get("body", ""))
        if needle in re.sub(r"\s+", "", blob):
            return {
                "source_text": quote[:120],
                "clause": section.get("title"),
                "page": section.get("page"),
            }
    if needle in re.sub(r"\s+", "", doc.get("clean_text", "")):
        return {"source_text": quote[:120], "clause": None, "page": None}
    return None


def _standardize_llm_value(key, value, display, quote):
    """LLM 负责语义候选，核心可计算字段仍由确定性标准化器校验。"""
    context = "%s %s" % (quote or "", display or "")
    if key == "amount":
        parsed, _, _ = std.parse_amount_any(context)
        if parsed is not None:
            return parsed
        try:
            parsed = float(value)
            return parsed if parsed > 0 else None
        except (TypeError, ValueError):
            return None
    if key == "rate":
        parsed, _, _, _, _ = std.parse_rate_any(context)
        if parsed is not None:
            return parsed
        try:
            parsed = float(value)
            return parsed if 0 <= parsed < 1 else None
        except (TypeError, ValueError):
            return None
    if key == "term":
        parsed, _, _ = std.parse_term_any(context)
        if parsed is not None:
            return parsed
        try:
            parsed = int(float(value))
            return parsed if parsed > 0 else None
        except (TypeError, ValueError):
            return None
    if key in ("effective_date", "expiry_date", "sign_date"):
        _, iso, _ = std.parse_date_any(context)
        return iso or (str(value) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or "")) else None)
    return value


def llm_enrich(fields, doc, contract_type_label):
    """用 LLM 补齐规则未发现的字段。返回 (更新数量, 状态说明)"""
    if not ai_client.is_configured():
        return 0, "llm_off"  # 未配置：静默使用规则结果
    # 不适用字段（对该合同类型无意义）不参与补强
    missing = [f for f in fields.values()
               if not f.get("found") and f.get("status") != "not_applicable"
               and not f.get("llm_disabled")]
    if not missing:
        return 0, "no_gap"
    keys = "、".join("%s(%s)" % (f["key"], f["label"]) for f in missing)
    doc_text = mask_sensitive(doc.get("clean_text", ""))
    prompt = PROMPT_TMPL.format(ctype=contract_type_label, missing=keys, doc=doc_text[:6000])
    messages = [{"role": "user", "content": prompt}]
    text, err = _collect_stream(messages)
    if err or not text:
        return 0, "llm_fail:%s" % (err or "empty")
    arr = _parse_json_array(text)
    if not arr:
        return 0, "llm_parse_fail"
    updated = 0
    for item in arr:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key", "")).strip()
        f = fields.get(key)
        if not f or f.get("found"):
            continue  # 规则结果优先，不覆盖
        value = item.get("value")
        display = str(item.get("display") or value or "").strip()
        quote = str(item.get("quote") or "").strip()
        if not display or display in ("未发现", "无法确定"):
            continue
        evidence = _verified_evidence(doc, quote)
        if not evidence:
            continue
        value = _standardize_llm_value(key, value, display, quote)
        if key in ("amount", "rate", "term", "effective_date", "expiry_date", "sign_date") and value is None:
            continue
        f.update(found=True, value=value, display=display[:80],
                 raw=quote[:80], confidence=0.75, method="llm_validated",
                 **evidence)
        updated += 1
    return updated, "llm_ok" if updated else "llm_no_verified_evidence"
