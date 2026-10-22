# 变更记录

## v1.0.1 — 2026-10-22

本次修复三个已复现缺陷，均配有回归测试；测试总数 38 → 47，全部通过。

### 修复

**1. 财务期间按标签排序，最新期不再取决于录入顺序**（`engines/covenant.py`）

- 现象：财务数据按“新→旧”录入（Wind / 同花顺 / Excel 透视的默认导出顺序）时，`monitor()` 用
  `series[-1]` 当作最新期，导致用最旧一期判断契约状态。实测把 `2026Q2（50%，安全）` 与
  `2026Q1（80%，已触发）` 倒序录入，系统报“最新一期 80%，已触发”，结论方向完全相反。
- 修复：新增 `_period_key()` 与 `order_financials()`，按期间标签升序排列；支持
  `2026Q1` / `2026-Q1` / `2026年第一季度` / `2026-03-31` / `2026年3月31日` / `20260331` /
  `2026-03` / `2026年` / `2026半年度` 等格式。标签无法解析时**不猜测**，保持录入顺序。
- 结果新增 `period_order`（`label_sorted` / `reversed_by_label` / `input_order` / `single_period`）
  与 `latest_period`，便于审计与前端提示。

**2. 交易投影校验业务角色，授信额度不再被当成本金**（`engines/costlens.py`）

- 现象：`project_transaction()` 只取 `primary.amount.value`，不校验 `role`。一笔
  `role=credit_limit` 的交易会被投影成 12 亿本金，算出 3600 万利息、3.0% 综合成本率，且无任何提示。
- 修复：新增 `PRINCIPAL_ROLES` / `TERM_ROLES` 白名单，角色不在白名单时**拒绝投影**并登记
  `projection_blocked`；`missing_detail()` 据此输出业务原因（“该笔交易的金额角色为「授信额度」……”），
  由 `analyze_cost()` 走既有的 `insufficient_params` 降级路径。
- 保留人工出口：字段状态为 `confirmed_by_user` 时跳过角色校验，避免误判导致永久无法计算。

**3. SQLite 连接显式关闭**（`storage.py`）

- 现象：`_conn()` 每次新建连接，`with` 只提交不关闭。Windows 下数据库文件被进程占用，
  备份、迁移、临时目录回收都会失败（原有 2 个测试即因此失败）。
- 修复：`_conn()` 改为 `@contextmanager`，成功提交、异常回滚、`finally` 关闭。

### 新增测试

- `tests/test_regression_period_projection.py`：8 个用例，覆盖期间排序的四种策略、5 类标签格式、
  授信额度/授信期限拦截、人工确认出口、合法角色兼容。
- `tests/test_storage.py`：新增 `test_database_file_released_after_write`。

### 已知未修（后续版本）

| 编号 | 问题 | 位置 |
|---|---|---|
| 3 | IRR 无解时静默返回区间端点（费用>本金 → 12000%；按季付息+短期 → 4000%） | `costlens.irr()` |
| 4 | 契约阈值为 0 时 `judge_status()` 除零，返回 500 | `covenant.judge_status()` |
| 6 | LLM 补强外发整段正文，仅掩码证件/手机/卡号 | `uparse/llm_extract.py` |
| 7 | 综合成本率分母用本金（9.30%）与文档 9.33% 不一致 | `costlens.analyze_cost()` |
| 8 | 速动比率声明用流动资产、实现用速动资产 | `uparse/extract.py`、`covenant.py` |
| 9 | 修正字段后情景比较对手取“库中第一份可算合同” | `server._rerun_single()` |
