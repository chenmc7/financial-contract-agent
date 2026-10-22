# -*- coding: utf-8 -*-
"""字段 Schema：所有金融合同通用字段 + 各类型专属字段定义。

字段状态（status）语义——区分"系统识别失败"与"合同本身缺失"：
- confirmed       已确认：原文识别成功且置信度达标
- uncertain       待复核：存在候选内容但置信度不足，或表述无法可靠判断
- not_found       未发现：该字段理论上可能存在，但原文没有找到
- not_applicable  不适用：该字段对当前合同类型没有意义（不作为缺失）
- conflict        冲突：多处条款给出不一致的取值，需人工裁决
"""

# 字段状态 → 展示文案
STATUS_LABELS = {
    "confirmed": "已确认",
    "uncertain": "待复核",
    "not_found": "未发现",
    "not_applicable": "不适用",
    "conflict": "冲突",
    "confirmed_by_user": "用户已确认",
}

# 通用字段（key, label, 分组）——对任意金融合同优先提取
UNIVERSAL_FIELDS = [
    ("contract_name", "合同名称", "基本信息"),
    ("document_type", "文档类型", "基本信息"),
    ("document_title", "文档标题", "基本信息"),
    ("transaction_type", "交易类型", "基本信息"),
    ("contract_type", "合同类型", "基本信息"),
    ("parties", "签约主体", "基本信息"),
    ("roles", "角色关系", "基本信息"),
    ("amount", "金额", "核心交易"),
    ("currency", "币种", "核心交易"),
    ("term", "期限", "核心交易"),
    ("effective_date", "生效日期", "核心交易"),
    ("expiry_date", "到期日期", "核心交易"),
    ("sign_date", "签约日期", "核心交易"),
    ("rate", "利率/费率", "核心交易"),
    ("payment_method", "付款/还款方式", "核心交易"),
    ("guarantee", "担保方式", "风险条款"),
    ("breach_liability", "违约责任", "风险条款"),
    ("early_termination", "提前终止", "风险条款"),
    ("fee_bearing", "费用承担", "风险条款"),
    ("info_auth", "信息授权", "风险条款"),
    ("dispute_resolution", "争议解决", "通用条款"),
    ("jurisdiction", "管辖", "通用条款"),
    ("amendment", "合同变更", "通用条款"),
    ("notice", "通知送达", "通用条款"),
    ("disclaimer", "免责条款", "通用条款"),
    ("attachments", "附件及补充协议", "通用条款"),
]

# 类型专属字段：按识别出的合同类型动态扩展
TYPE_FIELDS = {
    "personal_loan": [
        ("credit_limit", "授信额度", "个人贷款专属"),
        ("repayment_plan", "还款计划", "个人贷款专属"),
        ("loan_purpose", "贷款用途", "个人贷款专属"),
    ],
    "enterprise_loan": [
        ("credit_limit", "授信额度", "企业贷款专属"),
        ("repayment_plan", "还款计划", "企业贷款专属"),
        ("loan_purpose", "贷款用途", "企业贷款专属"),
    ],
    "loan": [
        ("credit_limit", "授信额度", "贷款专属"),
        ("repayment_plan", "还款计划", "贷款专属"),
        ("loan_purpose", "贷款用途", "贷款专属"),
    ],
    "credit": [
        ("credit_limit", "授信额度", "授信专属"),
        ("revolving_terms", "循环用信安排", "授信专属"),
        ("commitment_fee", "承诺费安排", "授信专属"),
    ],
    "guarantee": [
        ("collateral", "担保物", "担保专属"),
        ("guarantee_scope", "担保范围", "担保专属"),
        ("guarantee_period", "保证期间", "担保专属"),
    ],
    "mortgage": [
        ("collateral", "抵押物", "抵押专属"), ("collateral_title", "抵押物权属", "抵押专属"),
        ("registration", "抵押登记", "抵押专属"), ("priority", "抵押顺位", "抵押专属"),
        ("maximum_amount", "最高额", "抵押专属"), ("guarantee_scope", "担保范围", "抵押专属"),
        ("disposal_conditions", "处置条件", "抵押专属"), ("guarantee_period", "担保期限", "抵押专属"),
    ],
    "pledge": [
        ("collateral", "质押物/权利", "质押专属"), ("collateral_title", "质押权属", "质押专属"),
        ("registration", "质押登记/交付", "质押专属"), ("priority", "质押顺位", "质押专属"),
        ("maximum_amount", "最高额", "质押专属"), ("guarantee_scope", "担保范围", "质押专属"),
        ("disposal_conditions", "处置条件", "质押专属"), ("guarantee_period", "担保期限", "质押专属"),
    ],
    "credit_card": [
        ("credit_limit", "授信额度", "信用卡专属"),
        ("interest_free_period", "免息期", "信用卡专属"),
    ],
    "leasing": [
        ("financial_statement_date", "财务报表基准日", "租赁专属"),
        ("leased_asset", "租赁物", "租赁专属"),
        ("rent_plan", "租金支付安排", "租赁专属"),
        ("purchase_option", "留购与所有权安排", "租赁专属"),
        ("deposit", "保证金", "租赁专属"),
    ],
    "factoring": [
        ("financing_business", "金融业务", "保理专属"),
        ("financing_party", "融资方", "保理专属"),
        ("factoring_bank", "保理银行", "保理专属"),
        ("credit_limit", "授信额度", "保理专属"),
        ("credit_term", "授信期限", "保理专属"),
        ("actual_financing_amount", "实际融资金额", "保理专属"),
        ("single_financing_term", "单笔融资期限", "保理专属"),
        ("rate_mechanism", "利率机制", "保理专属"),
        ("prepayment", "提前还款", "保理专属"),
        ("baseline_date", "保理基准日", "保理专属"),
        ("receivables", "应收账款", "保理专属"),
        ("recourse", "追索权安排", "保理专属"),
        ("advance_ratio", "保理融资比例", "保理专属"),
    ],
    "wealth": [
        ("yield_rule", "收益规则", "理财专属"),
        ("risk_level", "风险等级", "理财专属"),
        ("subscription", "认购/赎回安排", "理财专属"),
    ],
    "securities": [
        ("trade_type", "交易类型", "证券衍生品专属"),
        ("margin", "保证金", "证券衍生品专属"),
        ("valuation", "估值安排", "证券衍生品专属"),
        ("netting", "净额结算", "证券衍生品专属"),
        ("early_term_events", "提前终止事件", "证券衍生品专属"),
        ("default_events", "违约事件", "证券衍生品专属"),
        ("collateral_arr", "履约保障安排", "证券衍生品专属"),
    ],
    "insurance": [
        ("insured_amount", "保额", "保险专属"),
        ("premium", "保费", "保险专属"),
        ("exclusions", "除外责任", "保险专属"),
    ],
    "general": [],
}

FIELD_LABELS = {k: lbl for k, lbl, _ in UNIVERSAL_FIELDS}

# 各合同类型不适用的通用字段：这些字段缺失时返回"不适用"，而不是"未发现"
NA_FIELDS = {
    "guarantee": {"rate", "payment_method"},   # 保证合同本身不生息、无还款安排
    "mortgage": {"rate", "payment_method"},
    "pledge": {"rate", "payment_method"},
    "securities": {"rate", "payment_method"},   # 衍生品协议无借贷利率与分期还款
    "insurance": {"rate"},                      # 保险合同以费率计价，无借贷利率
}
NA_NOTES = {
    "rate": "该类合同通常不涉及借贷利率条款",
    "payment_method": "该类合同通常无分期还款安排",
}

# 判定为"uncertain"的置信度阈值：低于该值的已发现字段需要人工复核
UNCERTAIN_BELOW = 0.70


def make_field(key, label, group):
    """统一字段结构：未发现时 found=False，不阻断后续分析"""
    return {
        "key": key, "label": label, "group": group,
        "found": False, "status": "not_found",
        "value": None, "display": "未发现",
        "raw": "", "source_text": "", "clause": None, "page": None,
        "confidence": 0.0, "method": "rule", "note": None, "items": None,
    }
