# Pi 概念与现有实现的对应

> 这是上一版源码包的历史架构说明，表中路径对应旧包，不是本仓库当前入口。当前架构以 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 和 [MIGRATION.md](MIGRATION.md) 为准。

本节是架构映射，不是对某个未指定 Pi SDK 的兼容性声明。

| Pi 思想中的责任 | 包内已有实现 | 说明 |
| --- | --- | --- |
| Agent loop | agent/runtime/run_clinsql_model_generation.py | 冻结上下文→生成 SQL/弃答→静态检查→EXPLAIN→执行→可选修复→评分 |
| 模型适配层 | 上述文件 generate_sql | 目前通过 Codex CLI；还不是可任意切换模型的统一 Pi provider |
| Tools | tools/meddb/src/meddb | CLI、AST 安全检查、只读事务、元数据、模板、受限导出 |
| Context | context/schemas 与 Skill references | schema、问题、规则、字典候选；Gold 不进入生成上下文 |
| Extensions | skills/ | 现有 Codex Skill，需要额外适配才能被 Pi 原生加载 |
| Session/state | run_clinsql_v4_full.py | 文件级 checkpoint、冻结清单、恢复与报告，不是通用多轮聊天 session 引擎 |
| Scheduling | low_impact_benchmark.py | 模型请求自适应最多 8 路，数据库串行，资源保护 |
| Evaluation | agent/runtime 的 scorer/audit/analyze 模块 | Gold 数值复现、执行率与两库一致性分离 |

迁移顺序：明确 Pi 版本→定义模型调用与工具协议适配器→注入数据库与路径配置→保持生成上下文与评分答案隔离→离线测试→连接只读数据库验证。当前不为整理目录而拆散耦合模块，避免改变已经评测的语义。
