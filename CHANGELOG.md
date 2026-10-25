# 变更记录

## v1.0.2 — 2026-10-25

本次只修使用体验，不改任何内部逻辑：改动全部落在 `static/` 下三个前端文件，
Python、接口、数据结构与金融计算零改动，单元测试仍为 47 passed。

### 修复（9 项）

| 编号 | 问题 | 改法 |
|---|---|---|
| UX-01 | 提示条遮挡右上角状态栏 | `#toast-wrap` 的 `top: 24px → 76px` |
| UX-02 | 同一页面「金额待复核」与「合同金额」自相矛盾 | 交易下拉显示取「交易级金额 ?? 合同级 `principal`」，仅影响显示文本 |
| UX-03 | 同一合同可同时选为版本比对的新旧两版 | 新增 `lockDiffPickers()`，把另一侧已占用的卡片置灰禁用 |
| UX-04 | 风险分 39/100 与性价比 77/100 方向相反且无说明 | 文案补「（越低越安全）」 |
| UX-05 | 契约监控默认空表无录入引导 | 增加一行录入提示；输入框加 `placeholder`（不改空值判定） |
| UX-06 | 解析状态显示英文 `SUCCESS`，且 `DOCX · DOCX` 重复 | 状态映射中文；页注去掉重复格式前缀 |
| UX-07 | 字段表「状态」列被挤成竖排 | 第 5 列 `104px → 128px`，`.f-conf` 加 `white-space: nowrap` |
| UX-08 | 合同卡片缺字段时信息行不对齐 | `principal` / `annual_rate` 缺失补 `—` 占位 |
| UX-09 | 左侧导航 `Scenario Compare` 被截断 | 侧栏 `232px → 252px` |

### 验证

- 自动化断言 `.tools/verify_fixes.py`：12/12 通过（含 boundingBox 不重叠、`disabled=true`、
  `scrollWidth == clientWidth`、`white-space: nowrap` 等可量化项）
- `python -m pytest tests/ -q`：47 passed，与修复前一致
- `git diff --name-only | grep .py`：0

### 已知未修

- `POST /api/ai/config` 会把请求体中的 `model` 直接写入仓库的 `config.json`
  （模糊测试期间曾被改写成随机字符串，已还原）。属内部逻辑问题，另行处理。

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
