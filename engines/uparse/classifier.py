# -*- coding: utf-8 -*-
"""合同类型识别层：关键词加权打分 + 标题命中加成，输出类型/置信度/判断依据。"""

# 从最特异到最通用排序：先匹配细分类型，贷款兜底
TYPE_RULES = [
    ("mortgage", "抵押合同", {
        "抵押合同": 8, "抵押人": 5, "抵押权人": 5, "抵押物": 5, "抵押登记": 4, "抵押顺位": 4,
    }),
    ("pledge", "质押合同", {
        "质押合同": 8, "出质人": 5, "质权人": 5, "质押物": 5, "质押登记": 4, "权利凭证": 3,
    }),
    ("guarantee", "担保合同", {
        "保证人": 5, "保证合同": 6, "最高额保证": 6, "反担保": 4, "担保物": 4,
        "抵押物": 3, "质押": 3, "保证范围": 4, "保证期间": 4, "承担保证责任": 5,
    }),
    ("leasing", "融资租赁合同", {
        "出租人": 5, "承租人": 5, "融资租赁": 6, "租赁物": 5, "租金": 2, "留购": 4,
    }),
    ("factoring", "保理合同", {
        "保理": 6, "应收账款": 3, "追索权": 2, "保理商": 5, "应收账款受让": 5,
    }),
    ("credit", "授信合同", {
        "综合授信": 6, "授信额度": 5, "循环额度": 4, "额度有效期": 3,
        "授信期间": 3, "分项额度": 3, "用信": 2,
    }),
    ("credit_card", "信用卡合同", {
        "信用卡": 6, "透支": 3, "免息还款期": 4, "最低还款额": 4, "超限费": 4,
    }),
    ("wealth", "理财/资管合同", {
        "资产管理计划": 6, "理财产品": 5, "业绩比较基准": 5, "净值型": 4, "管理人": 2,
        "认购": 2, "申购": 2, "赎回": 2, "风险等级": 2, "托管人": 3, "募集": 3,
    }),
    ("securities", "证券/衍生品合同", {
        "净额结算": 6, "衍生品": 5, "期货": 4, "期权": 3, "远期": 2, "互换": 3,
        "保证金": 2, "盯市": 4, "平仓": 3, "结算会员": 4, "履约保障": 4, "主协议": 3,
    }),
    ("insurance", "保险合同", {
        "保险人": 4, "投保人": 4, "被保险人": 4, "保额": 4, "保费": 3, "除外责任": 5,
        "免赔额": 4, "保险责任": 4, "保单": 3,
    }),
    ("personal_loan", "个人消费贷款", {
        "个人消费贷款": 9, "个人贷款": 6, "借款人身份证": 5, "个人消费": 5, "借款人（乙方）": 1,
    }),
    ("enterprise_loan", "企业贷款/借款合同", {
        "流动资金贷款": 7, "固定资产贷款": 7, "项目贷款": 7, "企业借款": 6, "公司借款": 5,
    }),
    ("loan", "贷款合同", {
        "贷款人": 4, "借款人": 4, "借款合同": 5, "贷款": 3, "授信": 2, "还款": 2,
        "放款": 3, "贷款期限": 3, "贷款本金": 4, "流动资金贷款": 4, "固定资产贷款": 4,
    }),
]


def classify(doc, fallback_name=""):
    """识别合同类型。返回 {key, label, confidence, hits, evidence}"""
    text = doc.get("clean_text", "") or ""
    title = (doc.get("title") or fallback_name or "")
    scores = {}
    hits = {}
    for key, label, kws in TYPE_RULES:
        s = 0
        for kw, w in kws.items():
            c = text.count(kw)
            if c:
                s += w * min(c, 3)
                hits.setdefault(key, []).extend([kw] * min(c, 2))
        # 标题直接命中强加成
        if any(k in title for k in kws) or label[:4] in title:
            s += 10
        if s:
            scores[key] = s
    if not scores:
        return {"key": "general", "label": "其他金融合同", "confidence": 0.35,
                "hits": [], "evidence": "未命中已知合同类型关键词，按通用金融合同处理"}
    # 明确的细分合同名称优先于通用“贷款”词频，避免个人/企业贷款被兜底类型吞掉。
    full_text = title + "\n" + text
    explicit_subtype = False
    credit_context = ("综合授信" in title or "授信合同" in title or
                      ("授信额度" in full_text and "额度有效期" in full_text and "用信" in full_text))
    if credit_context:
        # “综合授信合同”正文常含流动资金贷款/固定资产贷款分项，不能因此
        # 把整份额度协议误判成一笔企业贷款。
        scores["credit"] = max(scores.get("credit", 0), max(scores.values()) + 1)
    elif "个人消费贷款" in full_text or "个人贷款合同" in full_text:
        scores["personal_loan"] = max(scores.get("personal_loan", 0), max(scores.values()) + 1)
        explicit_subtype = True
    elif any(x in full_text for x in ("流动资金贷款", "固定资产贷款", "项目贷款", "企业借款")):
        scores["enterprise_loan"] = max(scores.get("enterprise_loan", 0), max(scores.values()) + 1)
        explicit_subtype = True
    ranked = sorted(scores.items(), key=lambda x: -x[1])
    best_key, best_score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    label = dict((k, l) for k, l, _ in TYPE_RULES)[best_key]
    # 置信度：分数规模 + 与次名的区分度
    base = best_score / (best_score + 8.0)
    margin = 1.0 if second == 0 else min((best_score - second) / best_score, 0.5) + 0.5
    conf = round(min(0.97, base * 0.7 + margin * 0.3), 2)
    if explicit_subtype:
        conf = max(conf, 0.9)
    return {
        "key": best_key, "label": label, "confidence": conf,
        "hits": sorted(set(hits.get(best_key, [])))[:8],
        "evidence": "命中关键词：" + "、".join(sorted(set(hits.get(best_key, [])))[:6]) if hits.get(best_key) else "标题识别",
    }
