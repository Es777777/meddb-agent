# 从源码包到仓库

| 原包路径 | 当前路径 |
| --- | --- |
| tools/meddb/src | src |
| tools/meddb/tests、templates、config、examples | 根目录对应同名目录 |
| tools/meddb/scripts | scripts |
| agent/runtime | evals/legacy |
| benchmarks/configs | evals/configs |
| skills | references/legacy-skills（避免自动发现旧入口） |

运行代码保持原样。模板测试以严格的合成元数据 test double 替代真实 catalog 依赖，未知表/列仍拒绝；不通过修改生产安全代码让测试通过。之前源码包的 38/39 测试结果不沿用为当前结果，以本仓库离线验证为准。

历史 Skill 和评测脚本中的绝对路径未盲目替换，便携性限制在 README 和 evals/README 中明示。配置、数据及第三方资源不是本仓库可重新分发的内容。
