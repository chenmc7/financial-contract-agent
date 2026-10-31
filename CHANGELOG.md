# 变更记录

## v1.0.3 — 2026-10-31

本次为稳定性与安全修复（成员 A 线）：修复 4 个已知缺陷，并为公网演示实例加上只读保护。
不改解析层 schema、不做前端重构；测试总数 47 → 86，全部通过。

### 修复

**1. IRR 无解时返回 None，不再输出 12000% 类假值**（`engines/costlens.py`、`static/js/app.js`）

- 现象：`irr()` 二分 200 次后无条件 `return mid`。当现金流全同号（费用加保证金吞掉本金）时，
  区间内无符号变化，二分只会把 `mid` 推向区间上界 `10.0`，年化后输出 `12000%`。
- 修复：二分前先判断现金流是否全同号、区间两端 NPV 是否同号，无解直接返回 `None`；
  二分后补收敛性检查（区间宽度 `1e-10` + 残差 `1e-7`），不收敛同样返回 `None`。
- 新增 `_npv()` 处理极端期数下折现因子上溢（`OverflowError`）与下溢（除零）。
- 前端 `rawPct()` 对 `null` 显示「—」而非 `0.00%`，KPI 副标题显示无解原因
  （`real_annual_cost_note`）。

**2. 契约阈值为 0 时除零抛 500**（`engines/covenant.py`）

- 现象：`judge_status()` 用 `(threshold - value) / threshold` 计算距阈值百分比，
  `threshold == 0` 时抛 `ZeroDivisionError` 并冒到接口层变成 500。
- 修复：阈值为 0 时跳过除法，按比较方向直接给出四态；`monitor()` 输出
  `threshold_note` 说明「已跳过距阈值百分比计算」。

**3. 速动比率口径声明与实现不一致**（`engines/uparse/extract.py`、`engines/covenant.py`）

- 现象：`COVENANT_FORMULAS` 声明为「流动资产 / 流动负债（速动口径）」、
  所需科目给的是 `current_assets`，而 `compute_indicator()` 实算
  `quick_assets / current_liabilities`，前端提示的补录科目因而是错的。
- 修复：以正确实现为准对齐声明为「速动资产 / 流动负债」，
  `required_data` 改为 `quick_assets` + `current_liabilities`，三处加注释互相锁定。

**4. 复核重算的对比对手随机漂移**（`server.py`）

- 现象：`_rerun_single()` 取 `[x for x in CONTRACTS.values() ...][:1]` 作为对比对手，
  SQLite 载入顺序一变对手就变，同一合同两次重算结果不可复现。
- 修复：新增 `_select_compare_peer()`，按「同类型优先 → 创建时间倒序 → 合同 ID 兜底」排序；
  结果标注 `scenario_compare_peer`（对手 ID / 名称 / 类型 / 是否同类型 / 规则）
  与每条结果的 `is_self`。

### 安全

**公网演示实例只读保护**（`server.py`）

- 现象：隧道实例（`share.sh` 启动，`SHARE_MODE` 开启）与本地实例共用同一数据库，
  任何拿到链接的人都能删除合同、改名、上传文件或修改安全配置。
- 修复：新增 `share_guard_decision()` 纯函数 + `share_readonly_guard` 中间件。
  `SHARE_MODE` 下 `GET/HEAD/OPTIONS` 放行，`POST/PATCH/PUT/DELETE` 返回 403 与可读提示。
- 只读计算接口（analyze/costlens、scenario、covenant、diff、comprehensive、
  agent/tasks、ai/chat）在 POST 白名单内，公网演示不受影响；白名单仅对 POST 生效，
  尾部斜杠与 `DELETE /api/analyze/*` 不能绕过。
- 可选令牌：配置 `CONTRACTGUARD_SHARE_TOKEN` 后，请求头 `X-Share-Token` 匹配即放行写操作。
- `SHARE_MODE` 下 `/api/analyze/covenant` 只算不落库，响应以 `persisted=false` 标注，
  访客试算的财务数据不写共享库。
- `/api/health` 增加 `readonly` 与 `write_token_configured`，便于隧道侧自检。
- 中间件注册在缓存刷新之后，被拦截的写请求连数据库都不会读。

### 新增测试

- `tests/test_regression_irr_threshold.py`（15 条）：IRR 无解、极端期数、阈值 0 四态、速动口径一致性。
- `tests/test_regression_compare_peer.py`（9 条）：对手选择规则与结果标注。
- `tests/test_regression_share_readonly.py`（15 条）：判定规则、ASGI 层实测 403、本地实例放行、财务数据不落库。

### 验证

- `python -m pytest tests -q`：**86 passed**（原 47 + 新增 39）。
- `node --check static/js/app.js`：通过。

### 已知未修（记入 Roadmap，不在本次范围）

- ~~`irr()` 的 NPV 把首笔现金流按 `(1+r)^1` 折现，正确应为 `(1+r)^0`，现有 IRR 数值被系统性低估~~
  —— **已澄清，无需修改**：`ΣCF/(1+r)^(i+1)` 与 `ΣCF/(1+r)^i` 只差一个 `(1+r)` 因子，
  NPV 零点完全相同，IRR 数值不受影响。实测两种写法结果一致（4.48 / 4.66 / 4.20 / 5.00），
  原文「会改变全部历史数值与既有断言」的判断不成立。

## v1.0.2 — 2026-10-25

本次为使用体验修复（9 项），只改展示层：`static/css/style.css`、`static/js/app.js`、
`static/index.html`，未触及 Python、接口与数据结构。

| 编号 | 问题 | 改法 |
|---|---|---|
| UX-01 | 提示条遮挡右上角状态栏，错误提示看不见 | `#toast-wrap` 的 `top: 24px → 76px` |
| UX-02 | 同页「金额待复核」与「合同金额」自相矛盾 | 交易下拉回退显示合同级 `principal` |
| UX-03 | 同一合同可同时选为版本比对的新旧两版 | 新增 `lockDiffPickers()`，另一侧置灰禁用 |
| UX-04 | 风险分与性价比分方向相反且无说明 | 文案补「（越低越安全）」 |
| UX-05 | 契约页空表无录入引导 | 增加录入提示与输入占位符 |
| UX-06 | 解析状态显示英文 `SUCCESS`，`DOCX · DOCX` 重复 | 状态映射中文，去掉重复格式前缀 |
| UX-07 | 字段表「状态」列被挤成竖排 | 列宽 104 → 128px，加 `white-space: nowrap` |
| UX-08 | 合同卡片缺字段时信息行不对齐 | 缺 `principal` / `annual_rate` 补 `—` |
| UX-09 | 左侧导航 `Scenario Compare` 被截断 | 侧栏 232 → 252px |

验证：Playwright 12 项断言全部通过；`pytest tests -q` 47 passed。

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
