# Evaluation

新增便携的 CLINSQL validation 入口：`python scripts/run_research.py benchmark --preflight`。
实现位于 `src/medresearch/benchmark.py`，复用研究运行器与 MedDB，默认不连接数据库或模型。
它比较 PostgreSQL 聚合结果与官方参考 CSV，不是上游 BigQuery/rubric 完整复现；
数据接入和显式执行命令见 [研究 Agent 文档](../docs/RESEARCH_AGENT.md)。

legacy/ 保留原 CLINSQL/EHRSQL 执行、评分、调度和恢复脚本，模块相对导入保持同目录。configs/ 只含非数据评测配置。

按用户要求，官方题集与 Gold 已整理到本机 `official/`，该目录完整受 Git ignore 保护，不会随代码正常提交。位置、版本与使用边界见 [OFFICIAL_RESOURCES.md](OFFICIAL_RESOURCES.md)。这不是允许公开再分发的许可，也不是新的实际运行结果。

这些脚本仍含原 F:/database 和本机 Codex 路径，不能宣称下载仓库即可跑全量。必须先取得官方资源并审核/配置路径。CI 不执行 legacy 中任何模型或数据库调用。

指标分开报告：SQL 生成率、执行率、忽略别名的 Gold 数值匹配、含别名严格匹配、两库一致性、弃答/超时/服务错误，以及首轮与修复后结果。任务集合与分母必须明确，开发回归不是独立未见评测。

原历史文件和算法不因目录整理而改写。新的实际评测必须冻结模型、Skill、schema、比较器、题目及调度参数，不能依照 Gold 重新调优已声称封存的测试题。
