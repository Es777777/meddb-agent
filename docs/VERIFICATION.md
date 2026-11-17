# 本地验证结果

## 2026-11-17：v2 完整本地研究流程验收

- Python 3.10.20，安装 `.[research]`；`scripts/run_checks.py` 的 **86/86** 测试通过。
- 全量检查包含既有 MedDB/benchmark 回归，以及研究协议、泄漏约束、已知效应回归/AIPW、
  手工 KM 风险集、手工 Taylor 抽样方差、域分析、单 PSU 拒绝、分组/时间预测切分、
  只读取数适配、截断阻断、阶段恢复、产物篡改检测、Agent 预算与模型边界等。
- `demo --output runs/full-system-verified`：**11/11** 方法全部完成 8 个阶段，
  覆盖 MIMIC、SEER8/12/17、NHANES、BRFSS 路由；独立合成样本证据比较完成。
- 主分析、预设敏感性分析、自动审查、Markdown/HTML 报告和 PNG/SVG 图表均已实际生成。
  生存与预测校准图已做本地视觉检查。验证产物全部位于 Git-ignored 的运行目录。
- 最终报告示例：`runs/full-system-verified/linear/report.html`；验收清单：
  `runs/full-system-verified/demo-summary.json`。
- PubMed 查询/标题核验和模型规划采用 mock 的真实协议形状测试；PostgreSQL 取数采用
  合成结果和核心复用验证。没有查询真实患者数据、调用真实模型或运行官方医学 benchmark。
  公开包镜像仅用于科学计算依赖安装，不向镜像发送研究数据。

上述验收是实现与本地技术验证，不是临床有效性、数据版本或研究结论的认证。
完整能力和统计边界见 [FULL_RESEARCH_WORKFLOW.md](FULL_RESEARCH_WORKFLOW.md)。

## 2026-11-17：MIMIC 研究运行器

在仓库 `.venv` 的 Python 3.10.20 环境执行 `scripts/run_checks.py`：
57/57 单元测试通过，包含新增的 17 项研究协议、只读适配、生成隔离、
检查点恢复、产物完整性和 benchmark 比较测试；AST、仓库排除检查和两个 CLI
的帮助入口均通过。模型协议与数据库执行使用 mock/合成数据验证，未真实联调。

运行官方 CLINSQL `benchmark --preflight --limit 18`：官方 schema 及已选问题
SHA-256 验证通过，覆盖六领域与三个难度。离线报告位于 Git-ignored 的
`runs/mimic-validation-preflight/preflight.json`。catalog 和 API key 均未配置；
按用户选择，本阶段只实现和离线验证，未运行真实数据库或模型 benchmark。
没有准确率成绩，不将合成测试的匹配结果当作医学 benchmark 分数。

当前实现与后续运行方式见 [RESEARCH_AGENT.md](RESEARCH_AGENT.md)。

## 历史整理验证

日期：2026-11-03。

| 检查 | 实际结果 |
| --- | --- |
| 源码及评测历史脚本 Python AST 语法 | 通过 |
| 数据文件及配置凭据字段排除 | 通过；仍需发布前人工秘密审查 |
| 仓库内便携 CLI `--help` | 通过 |
| 单元测试 | 40/40 通过 |
| 仓库维护 Skill 格式校验 | 通过 |
| Git | 已初始化 main；无 commit、无 remote、未 push |
| GitHub Actions | 已提供 Windows/Linux workflow；未在 GitHub 上运行，不宣称远程 CI 已通过 |
| PostgreSQL 与真实 benchmark | 本次未运行 |
| Pi SDK/原生 plugin 安装 | 未实现，不宣称通过 |

使用现有 Python 3.10 环境及已经安装的依赖测试，没有连接数据库或调用模型，也没有修改原始项目。模板测试只使用严格的合成元数据对象，没有复制真实 catalog。

复现离线检查：`python scripts/run_checks.py`。安装依赖：`python -m pip install -e .`。发布前另见 PUBLISHING.md。
