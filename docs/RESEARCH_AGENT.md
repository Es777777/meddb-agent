# MIMIC 研究 Agent 与官方题集入口

本文描述保留兼容的 v1 MIMIC 聚合查询与 benchmark 入口。
新增 v2 完整研究协议、统计执行、文献、质控、报告和多数据集流程见
[FULL_RESEARCH_WORKFLOW.md](FULL_RESEARCH_WORKFLOW.md)。下文“本版本”指 v1 路径的范围。

本版本完成第一个可运行闭环：公开问题 → 结构化研究方案与 SQL → MedDB
只读执行 → 本地产物与追踪 → 可选 CLINSQL validation 数值比较。
实现范围是 MIMIC-IV v3.1 的描述性聚合 SQL，不包含生存模型、因果推断、
预测建模、文献检索或官方 LLM rubric 评分；这些任务应返回 unsupported。

## 组件边界

| 组件 | 实现 | 职责 |
| --- | --- | --- |
| 研究协议 | src/medresearch/contracts.py | ResearchSpec、输出协议、参数校验 |
| 生成器 | src/medresearch/provider.py | 无工具的 Responses 调用，仅接收问题、schema、规则 |
| 医学规则 | context/knowledge/mimiciv.md | 粒度、临床定义、时间窗、分母、统计口径 |
| 工具适配 | src/medresearch/tools.py | 强制只读角色检查、schema/catalog、最终聚合输出策略 |
| 执行核心 | src/meddb/ | 唯一 SQL AST 检查、EXPLAIN、超时、行限制与查询审计 |
| 运行器 | src/medresearch/runtime.py | 不可变输入、检查点、失败重试、产物完整性、基础报告 |
| 评测适配 | src/medresearch/benchmark.py | 官方 validation 问题发现、参考 CSV 比较、独立指标 |

模型调用不提供文件、shell、数据库或 MCP 工具，也不发送临床查询结果。
模型输出之后才由父进程验证和执行。评分器在执行结束后读取对应参考 CSV，
不读取 Gold SQL 或 rubric；模型不接收评分结果，也没有 Gold 引导修复。
通用运行器不依赖 evals/legacy，历史实现保持不变。

研究方案记录问题、数据版本、分析单位、目标、人群、暴露、结局、协变量、
索引事件、时间窗、分母、统计量、输出列、假设与定义来源。结构校验不能证明
临床定义正确；本版需要人工审查，不能将“已执行”解释为“科学结论已验证”。

## 安装与离线检查

```powershell
uv venv --python 3.10 .venv
uv pip install --python .venv/Scripts/python.exe -e .
.venv/Scripts/python.exe scripts/run_checks.py
.venv/Scripts/python.exe scripts/run_research.py --help
.venv/Scripts/python.exe scripts/run_research.py benchmark --preflight --limit 18
```

preflight 默认不连接数据库、不调用模型，只检查资源完整性和依赖准备情况。
题集和 schema 来自本机 evals/official/MANIFEST.json 的官方快照，验证 SHA-256。
默认按领域与难度轮转抽取 validation；limit=18 通常覆盖六领域、三个难度。
没有读取封存 test 题目或答案。可用 --official 指定另一份同布局资源目录。

## 生成和执行单个研究任务

本版本模型适配使用 OpenAI Responses API。显式指定可用模型，在本机配置
OPENAI_API_KEY；不将密钥写入配置、方案或产物。不默认选择模型或消耗配额。
仅支持官方 HTTPS API 地址，不继承任意代理端点。

```powershell
# question.txt 是使用者提供的研究问题，不是 Gold 文件。
.venv/Scripts/python.exe scripts/run_research.py plan --question-file question.txt --task-id study-001 --model YOUR_MODEL --output runs/study-001-proposal.json
.venv/Scripts/python.exe scripts/run_research.py run --proposal runs/study-001-proposal.json --config config/meddb-mimic-official.json --output runs/study-001
```

也可以手工编写同一 JSON 协议的方案，跳过模型生成。示例见
examples/mimic-admission-count.proposal.json，不包含实际数据或实际研究结果。
run --explain-only 仅运行受限 EXPLAIN，不执行临床结果查询。

输出目录必须是新的空目录；使用 --resume 恢复同一运行。成功运行不重新执行；
失败运行可重试。方案、配置、catalog、代码、Python 版本或执行模式变化时，
拒绝沿用旧运行。成功产物缺失或被修改也拒绝恢复。
请为每次运行指定独立目录，不要多个进程共享输出目录。

plan 同时保存 `.provenance.json` 伴随文件，包含模型、规则/schema 与方案指纹；
run 自动核对并将其关联到执行记录。手工修改方案后，应另建方案与运行目录，
不得让旧的生成记录为新方案背书。

本地产物包括 proposal.json、state.json、result.json 和 report.md。
state 记录代码/配置/catalog 指纹、查询 ID、执行状态和产物哈希。
report 是执行记录与局限性说明，不自动编造医学结论。
查询结果只写本地，终端返回状态和产物路径；不得将 runs/ 或完整工作目录公开发布。

## 接入 MIMIC 数据库后运行 validation

官方题集与数据库是两类资源：仓库中的官方题集不含 MIMIC 数据库。
config/meddb-mimic-official.json 的 mimic_official_compare 是本地目标名称，
不代表数据库版本、完整性已经由官方认证。源 CLINSQL 基于官方 BigQuery
MIMIC-IV v3.1；本版是在用户提供的 PostgreSQL 副本上比较官方参考 CSV。
原生 BigQuery 执行器尚未实现。

先准备授权的 MIMIC-IV v3.1 PostgreSQL、非特权只读账号和 metadata catalog。
认证沿用本机 PostgreSQL 机制。配置指南见 CONFIGURATION.md；catalog 构建入口为
scripts/build_catalog.py --help。不要使用管理员账号执行研究，也不要为跑评测修改角色。

```powershell
# 该命令显式连接数据库，只检查角色、schema、只读事务和 catalog 元数据。
.venv/Scripts/python.exe scripts/run_research.py benchmark --preflight --probe-database --limit 18
# 该命令才会调用模型并执行临床 SQL；串行运行，默认不修复。
.venv/Scripts/python.exe scripts/run_research.py benchmark --execute --model YOUR_MODEL --limit 18 --output runs/clinsql-validation-001
# 恢复需使用相同模型、配置、limit、资源和代码。
.venv/Scripts/python.exe scripts/run_research.py benchmark --execute --model YOUR_MODEL --limit 18 --output runs/clinsql-validation-001 --resume
```

可以通过 --config 指向本地只读配置。执行前检查配置角色白名单、实际数据库/
用户名、只读事务、超级用户/建库/建角色/bypassrls 标记、MIMIC 表写权限和 schema。
运行器始终使用聚合模式，不依赖 MEDDB_CODEX_SAFE 环境开关。
输出层的保守聚合/字段检查不是匿名化证明；患者数据始终不得发送到模型。

## 指标及边界

summary.json 分开保存生成率、执行率、参考数值匹配率和含别名匹配率，
分母统一为冻结的已选题数。records.json 保留失败/弃答状态，错误和弃答不从分母删除。
数值比较忽略行顺序，保留重复行次数；列按位置比较，数值容差为 rtol=1e-6、
atol=1e-8，CSV 空字段解释为 NULL，NULL 不等于零。截断结果不能判为完整匹配。
这是一种透明的本地比较约定，不能等同于上游完整评分。

paired_database_agreement 和 official_rubric_score 固定为 null，表示未计算。
validation 是开发评测，不是独立未见 test 成绩。数据 release 尚不能自动确认，
PostgreSQL 与 BigQuery 的近似分位数等差异也会影响数值匹配。
最终 SELECT 必须直接聚合的保守策略，会拒绝一部分合法的复杂 benchmark SQL；
应独立记录这些失败，不能为了分数取消输出限制。

官方 API 参考：https://platform.openai.com/docs/api-reference/responses/create
本轮环境访问文档站点返回 HTTP 403，API 适配仅完成 mock 协议测试，未完成真实模型联调。
