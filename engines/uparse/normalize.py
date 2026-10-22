# -*- coding: utf-8 -*-
"""文本清洗与版面结构还原层：
- 清理零宽字符、重复页眉页脚、异常空白
- 保留标题层级（合同名/章/条/子条款）、段落与表格行
- 跟踪 PDF 页码标记（【第N页】）
- 输出结构化 Document：sections + paragraphs + page_count
"""
import re

PAGE_MARK = re.compile(r"【\s*第\s*(\d+)\s*页\s*】")
CLAUSE_HEAD = re.compile(r"^(第[一二三四五六七八九十百\d]+[条章])\s*(.*)$")
SUB_HEAD = re.compile(r"^(\d+(?:\.\d+)+)\s*(.*)$")
CN_LIST_HEAD = re.compile(r"^([（(][一二三四五六七八九十]+[）)]|[一二三四五六七八九十]+[、.])\s*(.*)$")
NUM_HEAD = re.compile(r"^(\d+)[、.．]\s*(.*)$")
MAJOR_HEAD = re.compile(r"^(?:[一二三四五六七八九十百]+、|\d+[、.．])\s*(.{1,80})$")
ROLE_LINE = re.compile(r"(?:借款人|贷款人|承租人|出租人|甲方|乙方|保证人|抵押人|出质人|保理商|保理银行|债务人|债权人)")
# 上市公司披露文件通常先出现公司全称，下一行才是“关于……的公告”。
# 将“公告/报告”纳入标题候选，避免把公司名误当文档标题；合同类标题规则保持不变。
TITLE_HINT = re.compile(r"(合同|协议|确认书|说明书|计划书|契约|保单|要约|函|公告|报告)$")


def clean_text(raw):
    """基础清洗：去零宽字符、统一全角数字、压缩空白但保留换行结构"""
    t = raw.replace("\u200b", "").replace("\ufeff", "").replace("\u3000", " ")
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    # 全角数字/字母 → 半角（金额与编号更规整）
    def _fw(ch):
        code = ord(ch)
        if 0xFF10 <= code <= 0xFF19:
            return chr(code - 0xFF10 + 0x30)
        if 0xFF21 <= code <= 0xFF3A:
            return chr(code - 0xFF21 + 0x41)
        if 0xFF41 <= code <= 0xFF5A:
            return chr(code - 0xFF41 + 0x61)
        return ch
    t = "".join(_fw(c) for c in t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _strip_repeated_lines(lines, pages):
    """删除跨页重复的页眉/页脚行（出现次数 ≥ 3 且占比高的短行），同步过滤页码"""
    if len(lines) < 12:
        return lines, pages
    from collections import Counter
    cnt = Counter(l.strip() for l in lines if 0 < len(l.strip()) <= 40)
    repeated = {l for l, c in cnt.items() if c >= 3 and len(lines) >= c * 4}
    if not repeated:
        return lines, pages
    out_l, out_p = [], []
    for l, p in zip(lines, pages):
        if l.strip() not in repeated:
            out_l.append(l)
            out_p.append(p)
    return out_l, out_p


def normalize_document(raw):
    """主入口：raw text → 结构化文档
    返回 {
      clean_text, title, sections: [{index, level, marker, title, body, page, start}],
      page_count
    }"""
    text = clean_text(raw)
    lines = text.split("\n")
    # 页码标记单独成行时剥离并记录
    page_of_line = []
    cur_page = 1
    max_page = 1
    kept = []
    for ln in lines:
        pm = PAGE_MARK.search(ln)
        if pm:
            cur_page = int(pm.group(1))
            max_page = max(max_page, cur_page)
            ln = PAGE_MARK.sub("", ln)
        if ln.strip():
            kept.append(ln)
            page_of_line.append(cur_page)
    lines, page_of_line = _strip_repeated_lines(kept, page_of_line) if len(kept) >= 12 else (kept, page_of_line)

    # 合同标题：首个含“合同/协议”且非条款/主体的行
    title = ""
    title_idx = -1
    for i, ln in enumerate(lines[:15]):
        s = ln.strip()
        if not s or len(s) > 40:
            continue
        if CLAUSE_HEAD.match(s) or SUB_HEAD.match(s):
            break
        if ("：" in s or s.startswith("甲方") or s.startswith("乙方")) and TITLE_HINT.search(s) is None:
            continue
        if TITLE_HINT.search(s):
            title = s
            title_idx = i
            break
    if not title:
        for i, ln in enumerate(lines[:5]):
            s = ln.strip()
            if 4 <= len(s) <= 40 and not CLAUSE_HEAD.match(s) and "：" not in s:
                title = s
                title_idx = i
                break

    # 条款切分（条 → 子条款）。这是结构化索引，不是原文展示层。
    # 公告正文常用“一、/二、”作为一级标题；普通表格行不能各自生成“前言”。
    sections = []
    cur = None
    for i, ln in enumerate(lines):
        if i == title_idx:
            continue
        s = ln.strip()
        if not s:
            continue
        pg = page_of_line[i] if i < len(page_of_line) else 1
        if cur is None and "：" in s and ROLE_LINE.search(s) and not CLAUSE_HEAD.match(s) and not SUB_HEAD.match(s):
            # 前置主体行（贷款人（甲方）：X）
            if sections and sections[-1]["level"] == 0 and sections[-1]["title"] == "签署主体":
                sections[-1]["body"] += "\n" + s
            else:
                sections.append({"index": len(sections), "level": 0, "marker": "",
                                 "title": "签署主体", "body": s, "page": pg})
            continue
        mh = CLAUSE_HEAD.match(s)
        sh = SUB_HEAD.match(s)
        if mh:
            cur = {"index": len(sections), "level": 1, "marker": mh.group(1),
                   "title": (mh.group(1) + " " + mh.group(2)).strip()[:60],
                   "body": "", "page": pg}
            sections.append(cur)
        elif MAJOR_HEAD.match(s) and len(s) < 90:
            # “一、保理融资安排”“1、担保事项一”等公告层级标题。
            mh2 = MAJOR_HEAD.match(s)
            cur = {"index": len(sections), "level": 1, "marker": mh2.group(0).split(mh2.group(1), 1)[0].strip(),
                   "title": s[:60], "body": "", "page": pg}
            sections.append(cur)
        elif sh and cur is not None:
            sub = {"index": len(sections), "level": 2, "marker": sh.group(1),
                   "title": (sh.group(1) + " " + (sh.group(2) or "")).strip()[:60],
                   "body": s, "page": pg}
            sections.append(sub)
        elif CN_LIST_HEAD.match(s) and cur is not None and len(s) < 200 and not MAJOR_HEAD.match(s):
            ch = CN_LIST_HEAD.match(s)
            sub = {"index": len(sections), "level": 3, "marker": ch.group(1),
                   "title": (ch.group(1) + " " + (ch.group(2) or "")).strip()[:60],
                   "body": s, "page": pg}
            sections.append(sub)
        else:
            if cur is not None:
                cur["body"] += ("\n" if cur["body"] else "") + s
            else:
                # 连续前置文本合并为一个区块，避免每个公告字段行都出现“前言”。
                if sections and sections[-1]["level"] == 0 and sections[-1]["title"] == "前言":
                    sections[-1]["body"] += ("\n" if sections[-1]["body"] else "") + s
                else:
                    sections.append({"index": len(sections), "level": 0, "marker": "",
                                     "title": "前言", "body": s, "page": pg})

    # 无条款结构时全文作为单一区块
    if not sections:
        sections = [{"index": 0, "level": 0, "marker": "", "title": "全文",
                     "body": "\n".join(l.strip() for l in lines if l.strip()), "page": 1}]

    return {
        "clean_text": text,
        "title": title,
        "sections": sections,
        "page_count": max_page,
    }


def flat_text(doc):
    """文档的扁平文本（条款标题 + 正文），供全文字段检索"""
    return "\n".join("%s\n%s" % (s["title"], s["body"]) for s in doc["sections"]) if doc["sections"] else doc["clean_text"]


def find_section(doc, pos):
    """给定 clean_text 中的字符位置，返回所在 section（线性扫描缓存）"""
    best = None
    cursor = 0
    for s in doc["sections"]:
        seg = s["title"] + "\n" + s["body"] + "\n"
        if cursor <= pos <= cursor + len(seg):
            return s
        cursor += len(seg)
        best = s
    return best or (doc["sections"][0] if doc["sections"] else None)
