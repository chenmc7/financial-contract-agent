# ContractGuard 金融合同功能测试样本集

本目录中的文件均为 **ContractGuard 研究与功能验证使用的人工合成金融合同样本**（Synthetic Benchmark Sample）。它们不是真实金融机构合同、真实企业融资案例、真实公开交易，也不是法律模板；主体名称、金额、日期、合同编号均为虚构测试数据。

## 文件清单

| 文件 | 用途 |
|---|---|
| `sample_A_standard_loan.docx` | 标准企业借款：完整现金流，测试 CostLens 与 ScenarioCompare |
| `sample_B_covenant_loan.docx` | 带财务契约贷款，测试 CovenantGuard，并辅助 CostLens/ScenarioCompare |
| `sample_B_financial_data.json` | B 的两期财务数据：Q1 多项通过，Q2 多项违反 |
| `sample_B_financial_data_missing.json` | B 的缺数据场景：应返回无法判断，不得默认 0 |
| `sample_C_original_contract.docx` | ContractDiff 旧版本 |
| `sample_D_amendment.docx` | C 的补充协议/新版本 |
| `sample_E_incomplete_credit_agreement.docx` | 信息不完整授信协议：验证主动降级 |
| `ground_truth_A.json` | A 的字段、证据、CostLens 和情景 Ground Truth |
| `ground_truth_B.json` | B 的字段、四类 Covenant、成本和数据 Ground Truth |
| `ground_truth_C_D_diff.json` | C→D 的差异 Ground Truth |
| `ground_truth_E.json` | E 的拒算与用户情景 Ground Truth |
| `coverage_matrix.md` | 四大核心功能覆盖矩阵 |
| `build_samples.py` | 可重复生成上述文件的构建脚本 |

## 当前 Schema 对照

Ground Truth 优先沿用项目现有字段：`amount`、`rate`、`term`、`payment_method`、`fee_bearing`、`early_termination`、`breach_liability`、`credit_limit`、`covenants`。业务角色写在同一条记录的 `role`/`amount_role`/`rate_role`/`term_role` 中；例如 `actual_financing_amount` 映射到当前解析器的通用 `amount`，`base_interest_rate` 映射到 `rate`。

当前 `CovenantGuard` 已支持资产负债率、流动比率、速动比率、净资产；B 额外放入“利息保障倍数（EBITDA/利息费用）”作为能力边界样本。当前 Schema 没有 EBITDA/利息费用输入字段，因此 Ground Truth 明确标记为“待扩展”，不得把它伪装成已通过的判断。

DOCX 解析器不会可靠地产生真实页码，所以 Ground Truth 使用 `section`、`clause_id`、合成源段落 ID 和完整原文句子；`page` 明确为 `null`，不伪造页码。上传后 ContractGuard 仍会生成自己的解析 `paragraph_id` 和字符偏移，人工核验以完整证据文本为准。

## 逐个上传与测试

1. 打开 ContractGuard 合同库，依次上传 A、B、C、D、E 五个 DOCX。
2. A：进入 CostLens，确认使用 2,000 万实际本金、4.20%、18 个月、先息后本和 8 万服务费；再进入 ScenarioCompare，运行基准、利率上升 1 个百分点、提前还款第 9 个月。页面应注明后两项为用户情景假设。
3. B：上传后先查看识别到的财务契约；在 CovenantGuard 上传 `sample_B_financial_data.json`。逐期核对 Ground Truth 的 Q1/Q2 结果；再上传缺失数据文件，所有需要缺失科目的指标应为“无法判断/信息不足”。
4. C 与 D：在 ContractDiff 中选择 C 为旧版本、D 为新版本。应识别金额、利率、期限、担保责任、逾期违约金/加速到期五类实质变化；“先息后本”和争议解决条款不应被误报为变化。
5. E：CostLens 和 ScenarioCompare 在没有用户补充真实提款现金流时应拒算/阻断。只有用户主动输入本金、利率、期限和还款方式后，才允许运行标记为“用户设定的情景假设”的模拟。

## 预期行为重点

- 授信额度不等于实际融资金额；授信有效期不等于单笔实际期限。
- 只有输入属于同一笔交易、且金额/利率/期限/现金流结构足够明确时，CostLens 才输出确定结果。
- 缺财务数据应返回 UNKNOWN/无法判断，不把缺失科目当作 0。
- ContractDiff 的每项变化都要同时带旧证据和新证据；相同经济含义的措辞变化不应被标为重大变化。
- 所有证据应来自这些文件的真实原文整句/整款，不能用固定字符窗口或模型补写。

## 已知能力边界

当前项目没有为本样本集新增生产字段或特殊分支。利息保障倍数的结构化监控、DOCX 视觉坐标级定位、复杂多交易无标题材料的自动归属仍属于后续能力边界；样本集会把这些差异明确暴露出来，不能以“样本已生成”替代功能已完成。
