# -*- coding: utf-8 -*-
"""字段标准化层：兼容中文大写金额、阿拉伯数字、百分比、基点、日期区间、自然语言期限等多种写法，
统一转换为可计算的标准值。"""
import re
import datetime

CN_NUM = {"零": 0, "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7, "捌": 8, "玖": 9,
          "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "两": 2}
CN_UNIT = {"拾": 10, "十": 10, "佰": 100, "百": 100, "仟": 1000, "千": 1000}


# ---------- 中文数字 ----------

def _cn_section_to_int(section):
    """万以内的中文数字段（如 壹仟贰佰叁拾肆）→ int"""
    total, current = 0, 0
    for ch in section:
        if ch in CN_NUM:
            current = CN_NUM[ch]
        elif ch in CN_UNIT:
            unit = CN_UNIT[ch]
            total += (current or 1) * unit
            current = 0
    return total + current


def cn_to_int(s):
    """中文数字（含十/百/千）→ int：三十六→36，十→10，一百二十→120"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if not re.fullmatch(r"[零壹贰叁肆伍陆柒捌玖一二三四五六七八九十百拾千仟佰]+", s):
        return None
    total, current = 0, 0
    for ch in s:
        if ch in CN_NUM:
            current = CN_NUM[ch]
        elif ch in CN_UNIT:
            total += (current or 1) * CN_UNIT[ch]
            current = 0
    return total + current


def cn_amount_to_number(s):
    """中文大写金额 → 数值：壹亿伍仟万元整→150000000，柒万伍仟元→75000"""
    if not s:
        return None
    s = re.sub(r"[，,、\s]", "", str(s))
    s = s.replace("人民币", "").replace("圆", "元")
    # 小数部分（角/分）：壹佰元贰角叁分
    cents = 0.0
    mj = re.search(r"元([零壹贰叁肆伍陆柒捌玖]*)角", s)
    mf = re.search(r"[元角]([零壹贰叁肆伍陆柒捌玖]*)分", s)
    if mj:
        cents += (CN_NUM.get(mj.group(1), 1) if mj.group(1) else 1) * 0.1
    if mf:
        cents += (CN_NUM.get(mf.group(1), 1) if mf.group(1) else 1) * 0.01
    s = re.sub(r"[零壹贰叁肆伍陆柒捌玖]+[角分]", "", s)
    s = s.rstrip("元整正")
    if not re.fullmatch(r"[零壹贰叁肆伍陆柒捌玖一二三四五六七八九十百拾千仟佰万亿]*", s or ""):
        return None
    total = 0
    yi_parts = s.split("亿")
    for i, part in enumerate(yi_parts):
        mult = 10 ** (8 * (len(yi_parts) - 1 - i))
        if not part:
            continue
        wan_parts = part.split("万")
        for j, wp in enumerate(wan_parts):
            wmult = mult * (10 ** (4 * (len(wan_parts) - 1 - j)))
            if wp:
                n = _cn_section_to_int(wp)
                total += n * wmult
    result = total + cents
    return result if result else None


# ---------- 阿拉伯数字金额 ----------

def _to_float(s):
    try:
        return float(str(s).replace(",", "").replace("，", ""))
    except (TypeError, ValueError):
        return None


def _amount_to_yuan(num, unit):
    num = _to_float(num)
    if num is None:
        return None
    u = (unit or "").replace("人民币", "")
    if "万亿" in u:
        return num * 1e12
    if "亿" in u:
        return num * 1e8
    if "万" in u:
        return num * 1e4
    return num


AMOUNT_PATTERNS = [
    # 中文大写 + 括号数字（双证据，置信度最高）
    (r"(?:人民币)?([零壹贰叁肆伍陆柒捌玖拾佰仟万亿]+(?:元|元整|圆整))[（(]\s*[¥￥]?\s*([\d,，,]+(?:\.\d+)?)\s*(万亿|万|亿)?\s*元?\s*[）)]", "cn_paren"),
    # 括号数字
    (r"[（(]\s*[¥￥]?\s*(?:人民币)?\s*([\d,，,]+(?:\.\d+)?)\s*(万亿元|万元|亿元|万|亿|元)\s*[）)]", "paren"),
    # 纯中文大写
    (r"(?:人民币)?([零壹贰叁肆伍陆柒捌玖拾佰仟万亿]{2,}(?:元|元整|圆整))", "cn"),
    # 明文数字 + 单位
    (r"([\d,，,]+(?:\.\d+)?)\s*(万亿元|万元|亿元|万|亿|元)", "plain"),
]


def parse_amount_any(text):
    """从文本片段解析金额：优先双证据（大写+括号），返回 (value, raw, confidence)"""
    for pat, kind in AMOUNT_PATTERNS:
        m = re.search(pat, text)
        if not m:
            continue
        if kind == "cn_paren":
            cn_val = cn_amount_to_number(m.group(1))
            ar_val = _amount_to_yuan(m.group(2), m.group(3) or "元")
            # 双证据交叉校验
            if cn_val and ar_val and abs(cn_val - ar_val) / max(ar_val, 1) < 0.02:
                return ar_val, m.group(0), 0.98
            return ar_val or cn_val, m.group(0), 0.9
        if kind == "paren":
            v = _amount_to_yuan(m.group(1), m.group(2))
            if v:
                return v, m.group(0), 0.88
        elif kind == "cn":
            v = cn_amount_to_number(m.group(1))
            if v:
                return v, m.group(0), 0.85
        else:
            v = _amount_to_yuan(m.group(1), m.group(2))
            if v:
                return v, m.group(0), 0.75
    return None, None, 0.0


# ---------- 利率 / 费率 ----------

RATE_PATTERNS = [
    # 年利率 4.35%
    (r"年(?:利率|费率|化(?:利率|收益率))[为是:]?\s*(?:人民币)?\s*([\d.]+)\s*%", "annual", 0.95),
    # 年化 4.2%（理财/资管业绩基准等常见写法）
    (r"年化\s*[为是:]?\s*([\d.]+)\s*%", "annual", 0.9),
    (r"(?:利率|费率)[为是:]?\s*([\d.]+)\s*%\s*/?\s*年", "annual", 0.9),
    (r"([\d.]+)\s*%\s*[（(]\s*年(?:利率|化)?\s*[）)]", "annual", 0.9),
    (r"月(?:利率|费率)[为是:]?\s*([\d.]+)\s*%", "monthly", 0.8),
    (r"日(?:利率|费率)[为是:]?\s*([\d.]+)\s*%", "daily", 0.8),
    # 万分之五（按日） / 万分之八
    (r"按?日?按?(?:金额的)?万分之([\d.]+)", "daily_bp", 0.85),
    # 基点（BP / bp）
    (r"([\d.]+)\s*(?:个?基点|BP|bp|Bp)", "bp", 0.8),
    # LPR 浮动：LPR+50BP / LPR上浮10%
    (r"(?:LPR|lpr|贷款市场报价利率)[＋+]?上?浮?升?\s*([\d.]+)\s*(?:个?基点|BP|bp|%|％)", "lpr_float", 0.8),
]

LPR_FALLBACK = 0.0345  # 展示用参考值（2026年初1年期LPR量级），仅作 note 提示


def parse_rate_any(text):
    """解析利率/费率：返回 (value_decimal, rate_type, raw, confidence, note)
    value 统一为年化小数；无法年化的（基点相对值）value=None 但保留 display"""
    for pat, rtype, conf in RATE_PATTERNS:
        m = re.search(pat, text)
        if not m:
            continue
        num = _to_float(m.group(1))
        if num is None:
            continue
        if rtype == "annual":
            return num / 100.0, "annual", m.group(0), conf, None
        if rtype == "monthly":
            return num / 100.0, "monthly", m.group(0), conf, "月费率 %.2f%%，折算年化约 %.2f%%" % (num, num * 12)
        if rtype == "daily":
            return num / 100.0, "daily", m.group(0), conf, "日费率口径，年化需结合计息天数"
        if rtype == "daily_bp":
            # 万分之N —— 通常为日利率
            daily = num / 10000.0
            return daily, "daily_wm", m.group(0), conf, "日利率万分之%s，年化约 %.2f%%（按360天）" % (m.group(1), daily * 360 * 100)
        if rtype == "bp":
            return num / 10000.0, "bp", m.group(0), conf, "%s个基点（= %s%%），基准利率需结合上下文" % (m.group(1), num / 100)
        if rtype == "lpr_float":
            is_pct = m.group(0).rstrip().endswith(("%", "％"))
            delta = num / 100.0 if is_pct else num / 10000.0
            approx = LPR_FALLBACK + delta
            return None, "lpr_float", m.group(0), conf, "LPR%s%s，按参考LPR %.2f%% 估算约 %.2f%%" % ("+" if not is_pct else "上浮", m.group(1) + ("%" if is_pct else "BP"), LPR_FALLBACK * 100, approx * 100)
    return None, None, None, 0.0, None


# ---------- 日期 ----------

DATE_PATTERNS = [
    r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
    r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})",
]


def parse_date_any(text):
    """解析日期 → datetime.date；返回 (date, iso, raw)"""
    for pat in DATE_PATTERNS:
        m = re.search(pat, text)
        if not m:
            continue
        y, mo, d = (int(x) for x in m.groups())
        try:
            dt = datetime.date(y, mo, d)
            return dt, dt.isoformat(), m.group(0)
        except ValueError:
            continue
    return None, None, None


def months_between(d1, d2):
    """两个日期之间的整月数（向上取整到月）"""
    if not d1 or not d2 or d2 < d1:
        return None
    months = (d2.year - d1.year) * 12 + (d2.month - d1.month)
    if d2.day > d1.day:
        months += 1
    return max(months, 1)


# ---------- 期限 ----------

def parse_term_any(text):
    """解析期限：返回 (months, raw, confidence)
    兼容：12个月 / 一年 / 三年 / 36期 / 自X至Y（日期区间推导）"""
    m = re.search(r"(?:期限|租期|期间|有效期)[为是：:]?\s*([\d一二三四五六七八九十百]+)\s*(个?月|月|年|日|天|期)", text)
    if m:
        n = cn_to_int(m.group(1))
        if n is not None:
            unit = m.group(2)
            if "年" in unit:
                return n * 12, m.group(0), 0.9
            if "月" in unit:
                return n, m.group(0), 0.9
            if "期" in unit:
                return n, m.group(0), 0.75  # 期默认按月
            return max(1, n // 30), m.group(0), 0.6
    # 数字必须带"个月"（避免把日期"2026年1月10日"中的"1月"误当期限）
    m = re.search(r"(\d+)\s*个月", text)
    if m:
        n = int(m.group(1))
        if n:
            return n, m.group(0), 0.7
    # 中文数字可直接接 年/月（"三年"/"十二个月"），排除"第一年/每一年"等序数语境
    m = re.search(r"(?<![第每前后次近])([一二三四五六七八九十百]+)\s*个?(月|年)", text)
    if m:
        n = cn_to_int(m.group(1))
        if n:
            return (n * 12 if m.group(2) == "年" else n), m.group(0), 0.75
    m = re.search(r"期限[为是]?\s*(?:自|从)?(.{6,40}?)[，,。；;]", text)
    if m:
        seg = m.group(1)
        d1 = parse_date_any(seg[:20])
        d2 = parse_date_any(seg[20:]) or parse_date_any(seg)
        if d1[0] and d2[0]:
            mo = months_between(d1[0], d2[0])
            if mo:
                return mo, m.group(0), 0.8
    rm = re.search(r"自\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})\s*[起至]\s*", text)
    if rm:
        d1 = parse_date_any(rm.group(1))
        rest = text[rm.end():]
        d2 = parse_date_any(rest)
        if d1[0] and d2[0]:
            mo = months_between(d1[0], d2[0])
            if mo:
                return mo, rm.group(0) + "…", 0.8
    return None, None, 0.0


# ---------- 币种 ----------

CURRENCY_MAP = [
    ("人民币", "CNY", r"人民币|CNY|¥|￥"),
    ("美元", "USD", r"美元|USD|\$"),
    ("欧元", "EUR", r"欧元|EUR|€"),
    ("港币", "HKD", r"港[币元]|HKD"),
    ("日元", "JPY", r"日元|JPY"),
    ("英镑", "GBP", r"英镑|GBP|£"),
]


def parse_currency(text):
    for label, code, pat in CURRENCY_MAP:
        if re.search(pat, text):
            return code, label
    return None, None


# ---------- 展示格式 ----------

def fmt_money(v):
    if v is None:
        return "-"
    if abs(v) >= 1e8:
        return "¥%.2f亿" % (v / 1e8)
    if abs(v) >= 1e4:
        return "¥%.2f万" % (v / 1e4)
    return "¥%s" % format(int(round(v)), ",")
