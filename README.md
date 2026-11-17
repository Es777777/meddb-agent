# MedDB Agent

面向本地医学研究数据库的**研究 Agent、统计分析流水线、只读 CLI 与可复现评测工具**。

完整研究流程见 [医学 AI4S 工作流](docs/FULL_RESEARCH_WORKFLOW.md)：覆盖多数据集协议、
本地质控、11 种统计方法、文献/来源核对、敏感性分析、证据报告与版本化研究任务。
可用 `python scripts/run_research.py demo --output runs/full-system-demo` 执行全合成端到端验收。

本仓库由现有项目源码整理而来，参考 [medsearch_skill](https://github.com/starAndHonor/medsearch_skill) 的工具/Skill/评测分层，以及 [medical-statistics](https://github.com/oliver-szr/medical-statistics) 的软件包、测试与协作结构。未复制这两个项目的实现代码。

> **范围说明**：代码提交不含数据库数据、患者明细、凭据或查询导出。官方 benchmark 题集与 Gold 按用户要求另存于本机 `evals/official/`，完整 Git-ignored，不随正常代码提交或公开发布；详见 [官方资源说明](evals/OFFICIAL_RESOURCES.md)。按 Pi 的职责分层组织，但尚未接入特定 Pi SDK，也不是可直接安装的 Codex plugin。

## 功能与状态

| 模块 | 当前能力 | 边界 |
| --- | --- | --- |
| CLI | 元数据检索、参数化模板、受限只读 SQL、导出与审计 | 实际分析需要外部 PostgreSQL 和本地 catalog |
| Agent/Skill | 仓库内维护入口、领域规则来源、生成/执行流程 | 不自动启用旧查询集成，不使用管理员角色 |
| 研究工作流 v2 | 版本化研究设计、本地质控、统计/敏感性分析、PubMed、报告图表、跨研究比较 | 模型和公开文献真实联调需配置；临床定义与科学有效性仍需研究者审查 |
| MIMIC 评测 | CLINSQL/EHRSQL 生成、双库比较、评分与恢复的历史实现 | evals/legacy 仍依赖原项目路径；不等于便携全量评测器 |
| Pi 架构 | 明确运行循环、工具、上下文、扩展、评测的责任边界 | 未来需按指定 SDK 实现适配器 |

## 快速开始

要求 Python **3.10**。以下命令不连接数据库：

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[research]"
python scripts/run_meddb.py --help
python scripts/run_checks.py
```

数据库连接前，阅读 [配置指南](docs/CONFIGURATION.md)，确认只读账号和 schema。默认配置不含密码，环境认证由使用者在本机另行配置。本仓库不会创建数据库或修改认证方式。

## 项目结构

```text
meddb-agent-github/
├── src/meddb/                 # 唯一 CLI 运行源码
├── .agents/skills/meddb-repository/ # 仓库维护 Skill
├── scripts/                   # CLI 入口、构建与离线检查
├── tests/                     # 不依赖真实数据库的单元测试
├── templates/                 # 只读 SQL 模板与参数清单
├── config/                    # 无密码配置示例
├── examples/                  # SQL/参数使用示例，无患者数据
├── context/schemas/           # Agent 输出结构
├── evals/                     # 评测配置与历史运行实现
├── references/legacy-skills/  # 历史 Skill，仅作来源，不自动发现
├── docs/                      # 架构、配置、评测与发布说明
├── .github/                   # CI、Issue 与 PR 模板
├── pyproject.toml
├── requirements.txt
├── CONTRIBUTING.md
├── SECURITY.md
└── LICENSE_NOTICE.md          # 发布前由作者确认许可
```

## Agent 使用

新增 MIMIC 描述研究运行器与官方 CLINSQL validation 入口，见
[研究 Agent 使用说明](docs/RESEARCH_AGENT.md)。默认 preflight 离线检查资源，
只有显式 `benchmark --execute` 才调用模型并执行查询；尚未接入真实数据跑分。

```bash
python scripts/run_research.py benchmark --preflight --limit 18
```

支持读取 `.agents/skills` 的 Agent 可发现仓库维护 Skill。它调用同一份 `src/meddb`，不复制第二套运行源码。原 Skill 的固定本机路径保留在历史参考目录，不应当直接安装为当前查询入口。

具体查询必须由使用者明确授权，保留单语句 SELECT、schema 限制、聚合优先、只读事务和输出限制；不要将真实患者行发送给模型。

## 评测与验证

```bash
python scripts/run_checks.py
python -m unittest discover -s tests -v
```

CI 只进行离线结构、语法、CLI 冒烟与单元测试；不会连接 PostgreSQL、启动模型或消耗 API 配额。实际 benchmark 需用户自行取得官方题集与数据访问权，参阅 [评测说明](evals/README.md)。**SQL 执行率、Gold 匹配率和双库一致率不是同一个指标。** 本次目录整理不宣称提高历史准确率。

## 发布与许可

公开代码仓库：[Es777777/meddb-agent](https://github.com/Es777777/meddb-agent)。数据库、官方 benchmark 资源、凭据及本地研究运行产物不随代码发布。发布维护流程见 [发布检查](docs/PUBLISHING.md)，历史评测脚本的路径限制见 evals/README.md。

现阶段未指定开源许可证，不因参考其他仓库而自动采用其 MIT 或其他许可证。
