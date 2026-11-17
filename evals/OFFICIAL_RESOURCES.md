# 本地官方题集与 Gold

本地位置：`evals/official/`。完整目录默认 Git-ignored，不会随正常 Git 提交上传。**不要强制 git add -f，也不要把包含该目录的整个文件夹直接压缩后公开分享。** 题目中可能带数据库采样标识，答案可能是 MIMIC 派生结果；代码许可不等于数据访问或再分发许可。

## 布局

```text
official/
├── MANIFEST.json                 # 版本来源、数量、文件大小与 SHA-256
├── clinsql/
│   ├── README.upstream.md
│   ├── model_inference/mimic4_schema.txt
│   └── data/splits/
│       ├── validation/           # 190 题
│       └── test/                 # 443 题，封存用于独立评分
└── ehrsql/
    ├── README.upstream.md
    ├── LICENSE
    └── dataset/ehrsql/
        ├── tables.json
        ├── CHANGELOG.md
        ├── mimic_iii/            # train/valid/test JSON、schema SQL
        └── eicu/                 # 原始官方另一子集；不表示 CLI 已支持 eICU
```

## 哪些是答案

- CLINSQL 每题包括 `query_*.txt`（问题）、`sql_*.sql`（官方 BigQuery Gold）、`result_*.csv`（官方参考答案）、两个 rubric JSON。不是我们生成的 SQL，不混入 PostgreSQL 端口。
- EHRSQL 的题目与 Gold SQL 位于对应 split JSON 的 question/query 等字段。不可回答题的 query 可能为 nan；不能将其算作缺失答案而自行补造。本地官方快照没有单独的参考答案 CSV，故不伪造或将本机执行结果标为官方 CSV。schema SQL 不是逐题 Gold。
- 未复制任何 `.sqlite` 数据库、患者表、原始 MIMIC CSV、模型输出或本地 PostgreSQL 转换 SQL。

## 来源与使用边界

本地已有快照来源：[CLINSQL](https://github.com/Barryshen1/ClinSQL)、[EHRSQL](https://github.com/glee4810/EHRSQL)。逐文件哈希与 EHRSQL commit 记录在本地 MANIFEST.json。CLINSQL 快照未带独立 LICENSE，不能因此推断允许公开再分发；保留上游 README。EHRSQL 原 LICENSE 一并保留，数据访问仍须遵守相应来源要求。

test 文件只作为不透明文件复制/哈希、JSON 元数据计数，不向生成器或模型展示题目与答案正文。未来评测需父进程隔离评分，不能因文件已在仓库下就让 Agent 自由读取 test Gold。本次没有运行评测或修改 Skill。

`legacy/` 的历史固定路径尚未全部迁移到该目录；整理资源不等于脚本已自动适配或完整复现论文成绩。已有目录不可覆盖，可通过 `scripts/collect_official_resources.py --clinsql <官方本地路径> --ehrsql <官方本地路径>` 在新的空资源目录中重建。
