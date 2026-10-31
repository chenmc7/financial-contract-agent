# 金融合同智能体 · Financial Contract Agent

面向融资、担保、借贷类合同的智能风险管理 Agent：把几十页的合同文本，变成可追溯、可复核、可决策的风险视图。

> 中国数字经济联盟首届实践大赛 · 智能体开发与应用赛道
> 当前仓库为可运行原型（内部代号 ContractGuard）的协作主线。

## 项目定位

一份融资合同的风险藏在三处：条款里写了什么、几个数字之间的比例关系、以及不同版本之间悄悄改了什么。
本项目把这三件事拆成三条独立的确定性管线，再用 Agent 层把它们串成一次可解释的审查流程：

- **读得准**：DOCX / PDF / TXT 解析为结构化要素，每个字段带原文引文，可回定位到段落。
- **算得对**：金额、比率、阈值判定全部由本地规则引擎完成，大模型不参与算术。
- **说得清**：风险结论必须能指回具体条款；要素缺失时明确拒算，而不是套默认值。

## 核心能力

| 模块 | 作用 | 实现位置 |
| --- | --- | --- |
| 文档解析 uparse | 合同文本 → 结构化字段，10 阶段管线（分类、抽取、归一、标准化、一致性校验等） | `engines/uparse/` |
| ContractGuard | 财务契约指标与阈值判定（资产负债率、流动比率、速动比率、净资产） | `engines/covenant.py` |
| CostLens | 融资成本穿透：名义利率 vs 含费用的综合成本 | `engines/costlens.py` |
| ScenarioCompare | 多情景压力测试，比较不同经营假设下的指标走势 | `engines/scenario.py` |
| ContractDiff | 合同版本比对，定位修订前后的条款与金额变化 | `engines/diffengine.py` |
| RiskEngine | 六类风险的识别、分级与依据输出 | `engines/riskengine.py` |
| 人工复核 | 逐字段改判、留痕、审计 | `server.py` + `storage.py` |

## 设计原则

1. **算术不交给模型**：所有金额、比率、阈值由确定性引擎计算，模型只负责语义抽取与文字解释。
2. **证据可回定位**：模型补充的字段必须给出逐字引文，且能在原文中精确定位，否则一律丢弃。
3. **缺要素就拒算**：关键字段不足时返回「无法计算」并说明缺什么，不套用默认假设。
4. **字段状态五态**：`confirmed` / `uncertain` / `not_found` / `not_applicable` / `conflict`，冲突必须显式暴露而非静默取值。

## 技术栈

Python 3.11+ · FastAPI · SQLite · 原生 JavaScript（无前端框架、零构建步骤）

## 目录结构

```
server.py          FastAPI 入口与全部 HTTP 接口
storage.py         SQLite 持久化（合同 / 分析 / 复核 / 审计）
engines/           分析与编排引擎
  uparse/          文档解析管线（10 阶段）
  covenant.py      财务契约指标
  costlens.py      融资成本穿透
  scenario.py      情景压力测试
  diffengine.py    合同版本比对
  riskengine.py    六类风险识别
  orchestrator.py  任务编排
  ai_client.py     大模型接入（异常自动降级）
static/            前端页面、样式与脚本
tests/             单元测试 + 合成样本基准集
deploy/            部署脚本与服务配置
```

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt

cp .env.example .env               # 填入 CONTRACTGUARD_API_KEY（可留空，走内置引擎）
python server.py
```

打开 <http://127.0.0.1:8899>。未配置大模型密钥时，AI 助手自动使用内置智能引擎；也可在应用右上角的 AI 设置中启用 DeepSeek、智谱 GLM、通义千问或 Kimi 的实时流式接口。

### 演示与临时公网分享

```bash
./demo.sh     # 本机演示：环境自检、健康状态、警示提示
./share.sh    # 本机服务 + 临时 Cloudflare 隧道（无需账号或域名）
```

`share.sh` 的隧道地址每次启动都会变化，仅供组内测试。公网实例使用**独立**的 `data/share.db` 与 `uploads-share/`，启动时自动灌入内置合成样本；本地真实合同与人工修正不会出现在公网实例上。演示前若要让公网展示某份合同，需在公网那套库里单独准备。

## 测试

```bash
python -m pytest tests -q
```

`tests/benchmark/synthetic_core_features/` 提供 A–E 五份合成合同、对应的 ground truth 与覆盖率矩阵，可用 `validate_samples.py` 校验样本自洽性。

## 协作规范

### 分支模型（一人一分支）

| 分支 | 归属 | 规则 |
| --- | --- | --- |
| `main` | 全员 | 随时可运行的主线。已开启保护：**禁止直推**，必须走 PR 且至少 1 人批准 |
| `dev/<用户名>` | 每人一条 | 自己的长期开发分支，随便推。例：`dev/chenmc7` |
| `feat/<模块>-<简述>` | 可选 | 单次任务改动较大时，从自己的分支再切一条，做完合回自己的分支 |

已建好的个人分支：`dev/chenmc7`、`dev/lengpaocha013-collab`、`dev/yuedou455-afk`、`dev/ooyee98633`

### 日常流程

```bash
# 第一次
git clone https://github.com/chenmc7/financial-contract-agent.git
cd financial-contract-agent
git checkout dev/<你的用户名>

# 每次开工前，先把主线并进来，减少后面的冲突
git pull origin main

# 改代码 → 提交 → 推自己的分支
git add -A
git commit -m "feat(uparse): 支持表格区域抽取"
git push origin dev/<你的用户名>
```

推完去 GitHub 点 **Compare & pull request**，源分支选 `dev/<用户名>`，目标分支选 `main`，填一下为什么改、怎么验证，等队友点 Approve。

**PR 合并后务必同步一次**（否则下次开 PR 会把上次的改动再带一遍）：

```bash
git fetch origin
git reset --hard origin/main
```

### PR 要求

- 一个 PR 只做一件事，描述里写清「为什么改 / 怎么验证」。
- 至少 1 人 review 后合并；不能自己批自己。
- 合并方式统一用 **Squash merge**（保持 main 历史干净），个人分支不会被自动删除。
- 涉及计算口径、字段定义的改动，必须同时补测试与样本。

### 分工建议

| 方向 | 范围 |
| --- | --- |
| 解析层 | `engines/uparse/`：抽取规则、字段 schema、一致性校验 |
| 分析引擎 | `covenant` / `costlens` / `scenario` / `diffengine` / `riskengine` |
| Agent 编排 | `orchestrator.py`：任务拆解、工具调用、结果校验 |
| 前端交互 | `static/`：审查台、复核流、可视化 |
| 评测与样本 | `tests/`：合成样本、ground truth、覆盖率矩阵 |

### 不要提交

`.env`、`data/*.db`、`uploads/`、`__pycache__/`、`.tools/`、任何真实客户合同或密钥。

## 数据与合规

- 仓库内样本均为**合成合同**，不含真实主体与真实交易信息。
- 上传目录与本地数据库已在 `.gitignore` 中排除，不会进入版本历史。
- 风险结论须附原文依据；无法定位证据的推断按「不确定」处理。

## Roadmap

- [ ] 多模态输入：扫描件 OCR、语音口述条款转结构化
- [ ] 多模态输出：风险报告语音播报、关键指标图表
- [ ] 指标扩展：利息保障倍数、EBITDA、交叉违约与加速到期
- [ ] 评测集扩充：真实公开公告的脱敏样本
- [ ] 编排升级：任务规划由固定模板改为按合同类型动态生成

## 许可

比赛期间保留所有权利。赛后如需开源，另行补充 LICENSE。
