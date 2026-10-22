# -*- coding: utf-8 -*-
"""合同解析引擎（兼容层）：
委托 engines.uparse 通用结构化解析框架（文本清洗 → 类型识别 → 通用/专属字段抽取
→ 标准化 → 原文证据绑定 → LLM 补强），并保持下游四引擎消费的 legacy 接口不变。"""
import re

from .uparse import parse_universal


def parse_contract(text, name="未命名合同", use_llm=False):
    """主解析入口：任何类型金融合同 → 结构化字段 + legacy 键。

    use_llm=True 时按管线执行 LLM 语义补强（仅在 AI 已配置且字段有缺口时生效，失败静默降级）。

    返回（legacy 键，供 CostLens / Scenario / Covenant / Diff 直接消费）：
      principal, annual_rate, term_months, repayment, guarantee,
      fees, prepayment_penalty, covenants, clauses, confidence, word_count
    新增（供结构化解析面板与前端定位）：
      contract_type, fields, field_count, pipeline
    """
    result = parse_universal(text, name=name, use_llm=use_llm)
    # 兼容旧调用方：annual_rate 允许由 meta 补强
    return result


# ---------- 旧 API 兼容（其他模块直接 import 的工具函数保持可用） ----------

def _to_float(s):
    try:
        return float(str(s).replace(",", "").replace("，", ""))
    except (TypeError, ValueError):
        return None


def split_clauses(text):
    """按 第X条 / 章节切分条款，返回 [{title, body, index}]"""
    parts = re.split(r"\n(?=第[一二三四五六七八九十百\d]+[条章])", text)
    clauses = []
    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"(第[一二三四五六七八九十百\d]+[条章][^\n]*)", part)
        title = m.group(1).strip() if m else ("条款 %d" % (i + 1))
        body = part[len(m.group(1)):] if m else part
        if m and not body.strip():
            one_line = re.match(r"(第[一二三四五六七八九十百\d]+[条章])\s*(.*)", part)
            if one_line and one_line.group(2).strip():
                title = (one_line.group(1) + " " + one_line.group(2).strip()[:18]).strip()
                body = one_line.group(2).strip()
        sub = re.split(r"\n(?=\d+\.\d+\s)", body)
        for sb in sub:
            sb = sb.strip()
            if not sb:
                continue
            sm = re.match(r"(\d+\.\d+\s+[^\n：:，。]{0,16}[：:]?)", sb)
            if sm:
                clauses.append({"title": (title.strip() + " · " + sm.group(1).strip())[:48],
                                "body": sb, "index": len(clauses)})
            else:
                clauses.append({"title": title, "body": sb, "index": len(clauses)})
    return clauses
