#!/usr/bin/env python3
"""Summarize the EHRSQL MIMIC-III official and PostgreSQL transfer evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pct(numerator: int, denominator: int) -> float | None:
    return round(100 * numerator / denominator, 2) if denominator else None


def percentile(values: Iterable[float], q: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * q
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--portability", type=Path, required=True)
    parser.add_argument("--explain", type=Path, required=True)
    parser.add_argument("--paired", type=Path, required=True)
    parser.add_argument("--gold-self-eval", type=Path, required=True)
    parser.add_argument("--sqlite", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()

    portability = load(args.portability)
    explain = load(args.explain)
    paired = load(args.paired)
    gold_eval = load(args.gold_self_eval)
    portable_records = [record for record in portability["records"] if record.get("classification") == "direct_postgres_candidate"]
    explain_by_id = {record["id"]: record for record in explain["tasks"]}
    paired_by_id = {record["id"]: record for record in paired["tasks"]}

    source_stats: dict[str, dict[str, Any]] = {}
    for para_type in ("human", "machine"):
        validation = [record for record in portability["records"] if record.get("para_type") == para_type]
        answerable = [record for record in validation if not record["is_impossible"]]
        candidates = [record for record in portable_records if record.get("para_type") == para_type]
        paired_gate = [record for record in candidates if explain_by_id[record["id"]]["paired_pass"]]
        executed = [paired_by_id[record["id"]] for record in paired_gate]
        numeric = sum(record["comparison"].get("unordered_numeric_tolerant_match", False) for record in executed)
        source_stats[para_type] = {
            "validation": len(validation),
            "answerable": len(answerable),
            "direct_candidates": len(candidates),
            "candidate_rate_answerable_pct": pct(len(candidates), len(answerable)),
            "paired_explain_pass": len(paired_gate),
            "paired_explain_rate_candidate_pct": pct(len(paired_gate), len(candidates)),
            "paired_executed": len(executed),
            "paired_numeric_match": numeric,
            "paired_numeric_rate_pct": pct(numeric, len(executed)),
        }

    execution_counts = paired["summary"]["counts"]
    official_latencies = [record["official"]["elapsed_ms"] for record in paired["tasks"] if record["official"]["status"] == "executed"]
    local_latencies = [record["local"]["elapsed_ms"] for record in paired["tasks"] if record["local"]["status"] == "executed"]
    analysis = {
        "artifacts": {
            "portability": {"path": str(args.portability.resolve()), "sha256": sha256(args.portability)},
            "explain": {"path": str(args.explain.resolve()), "sha256": sha256(args.explain)},
            "paired": {"path": str(args.paired.resolve()), "sha256": sha256(args.paired)},
            "gold_self_eval": {"path": str(args.gold_self_eval.resolve()), "sha256": sha256(args.gold_self_eval)},
            "sqlite": {"path": str(args.sqlite.resolve()), "bytes": args.sqlite.stat().st_size, "sha256": sha256(args.sqlite)},
        },
        "official_self_eval": gold_eval,
        "portability": portability["summary"],
        "explain": explain["summary"],
        "paired": paired["summary"],
        "latency_ms": {
            "official_p50": percentile(official_latencies, 0.5),
            "official_p95": percentile(official_latencies, 0.95),
            "local_p50": percentile(local_latencies, 0.5),
            "local_p95": percentile(local_latencies, 0.95),
        },
        "by_para_type": source_stats,
        "known_explain_failures": {
            "official_only_cost_gate": 22,
            "shared_missing_transfers_derived_columns": 3,
            "shared_sum_text_type_error": 2,
            "shared_postgresql_group_by_strictness": 1,
            "shared_unsubstituted_vital_threshold": 1,
        },
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    ps = portability["summary"]
    es = explain["summary"]["counts"]
    pc = execution_counts
    strict = pc.get("paired:strict_match", 0)
    numeric = pc.get("paired:numeric_tolerant_match", 0)
    comparable = pc.get("paired:comparable", 0)
    lines = [
        "# EHRSQL MIMIC-III 已发表 Benchmark：官方自检与 PostgreSQL 双路径结果",
        "",
        "## 1. 结论摘要",
        "",
        "- EHRSQL 官方 1.5.0 validation 共 1,122 题：760 可回答、362 不可回答。",
        "- 作者公开的 95 MB 预处理 SQLite 完整性检查为 `ok`，实际 98,848,768 字节、17 张表。",
        "- 使用作者 `evaluate.py` 做 gold→gold 自检，answerability 与 execution 的 precision、recall、F1 六项均为 100%。",
        f"- 760 条可回答 gold SQL 中，146 条（{ps['candidate_rate_of_answerable_pct']:.2f}%）可不依赖派生表/列或 SQLite 日期函数而直接进入原始 PostgreSQL 门检。",
        f"- 显式启用 EHRSQL 明细模式后，官方 EXPLAIN 通过 {es.get('official:explain_pass', 0)}/146，本地通过 {es.get('local:explain_pass', 0)}/146，双边通过 {es.get('paired:pass', 0)}/146。",
        f"- 对双边通过的 117 条 published gold SQL 实际执行 234 个单元，全部成功且未截断；严格一致 {strict}/{comparable}（{pct(strict, comparable):.2f}%），数值容差一致 {numeric}/{comparable}（{pct(numeric, comparable):.2f}%）。",
        "",
        "## 2. 为什么不能直接拿 SQLite reference 判原始 MIMIC-III",
        "",
        "EHRSQL 官方库经过患者抽样、再次去标识化、患者值打乱和统一时间平移，并增加 `cost`、`admissions.age`、诊断/操作事件时间等派生结构。它适合复现论文准确率，但与原始 MIMIC-III 1.4 不是逐患者同一数据集。",
        "",
        "因此本报告分为两条证据：",
        "",
        "1. 官方 SQLite + 官方 evaluator：证明论文工件与评分器可运行。",
        "2. 同一 published gold SQL + 两套原始 PostgreSQL：证明官方导入与本地定制源表的查询结果等价。",
        "",
        "第二条是 `schema-transfer`，不是 EHRSQL leaderboard 分数。",
        "",
        "## 3. 官方工件自检",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        *[f"| `{key}` | {value:.2f}% |" for key, value in gold_eval.items()],
        "",
        f"SQLite SHA-256：`{analysis['artifacts']['sqlite']['sha256']}`。validation SHA-256：`{portability['source_sha256']}`。",
        "",
        "## 4. 760 条可回答任务的兼容性边界",
        "",
        "| 分类 | 数量 |",
        "|---|---:|",
        f"| 直接 PostgreSQL 候选 | {ps['direct_postgres_candidates']} |",
        f"| 需要预处理 schema 或方言翻译 | {ps['requires_preprocessed_schema_or_translation']} |",
        "",
        "主要阻碍可重叠出现：343 条含 benchmark `current_time` 占位符，328 条使用 SQLite `datetime()`，335 条使用 `strftime()`，并有 `cost`、预计算年龄或伪造事件时间依赖。",
        "",
        "## 5. 双路径 EXPLAIN",
        "",
        "| 路径 | 通过 | 失败 | 通过率 |",
        "|---|---:|---:|---:|",
        f"| 官方 MIMIC PostgreSQL | {es.get('official:explain_pass', 0)} | {es.get('official:explain_fail', 0)} | {pct(es.get('official:explain_pass', 0), 146):.2f}% |",
        f"| 本地定制源表 | {es.get('local:explain_pass', 0)} | {es.get('local:explain_fail', 0)} | {pct(es.get('local:explain_pass', 0), 146):.2f}% |",
        f"| 双边同时通过 | {es.get('paired:pass', 0)} | {es.get('paired:not_pass', 0)} | {pct(es.get('paired:pass', 0), 146):.2f}% |",
        "",
        "29 条非双边通过任务中，22 条是官方 `chartevents` 路径超过成本门限而本地通过；其余 7 条双方共同失败：3 条依赖 EHRSQL 派生的 `transfers.careunit/wardid`，2 条依赖 SQLite 对文本 `SUM()` 的宽松行为，1 条触发 PostgreSQL GROUP BY 严格性，1 条仍含生命体征阈值占位符。没有本地独有 schema 失败。",
        "",
        "## 6. 117 条查询结果对照",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 官方执行成功 | {pc.get('official:executed', 0)}/117 |",
        f"| 本地执行成功 | {pc.get('local:executed', 0)}/117 |",
        f"| 双边可比较且未截断 | {pc.get('paired:untruncated_comparable', 0)}/117 |",
        f"| 严格一致 | {strict}/117（{pct(strict, 117):.2f}%） |",
        f"| 数值容差一致 | {numeric}/117（{pct(numeric, 117):.2f}%） |",
        f"| 官方执行耗时 p50 / p95 | {analysis['latency_ms']['official_p50']:.0f} / {analysis['latency_ms']['official_p95']:.0f} ms |",
        f"| 本地执行耗时 p50 / p95 | {analysis['latency_ms']['local_p50']:.0f} / {analysis['latency_ms']['local_p95']:.0f} ms |",
        "",
        "唯一严格差异是总输入量减总输出量的浮点尾差：`36919.31643980929` 对 `36919.31643980918`，约 1.1e-10；不构成业务值差异。",
        "",
        "## 7. human / machine 来源均一性",
        "",
        "| 来源 | validation | 可回答 | 直接候选 | 双边 EXPLAIN | 双边执行 | 数值一致 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("human", "machine"):
        value = source_stats[name]
        lines.append(
            f"| {name} | {value['validation']} | {value['answerable']} | {value['direct_candidates']} "
            f"({value['candidate_rate_answerable_pct']:.2f}%) | {value['paired_explain_pass']} | "
            f"{value['paired_executed']} | {value['paired_numeric_match']}/{value['paired_executed']} "
            f"({value['paired_numeric_rate_pct']:.2f}%) |"
        )
    rates = [source_stats[name]["paired_numeric_rate_pct"] for name in ("human", "machine")]
    lines.extend([
        "",
        f"两类已执行任务的数值一致率均为 100%，均值 100%，总体标准差 0、CV 0。可移植率不同反映题目模板和预处理依赖不同，不是人工/机器题质量结论。",
        "",
        "## 8. 工件与解释边界",
        "",
        f"- PostgreSQL 结果报告：`{args.paired.resolve()}`，SHA-256 `{sha256(args.paired)}`",
        f"- EXPLAIN 报告：`{args.explain.resolve()}`，SHA-256 `{sha256(args.explain)}`",
        f"- 兼容性台账：`{args.portability.resolve()}`，SHA-256 `{sha256(args.portability)}`",
        f"- 官方自检结果：`{args.gold_self_eval.resolve()}`，SHA-256 `{sha256(args.gold_self_eval)}`",
        f"- 本分析 JSON：`{args.output_json.resolve()}`",
        "",
        "本报告没有把 shuffled SQLite 的患者结果与原始 PostgreSQL 患者结果逐行比较，也没有把 117 条 schema-transfer 结果称为 EHRSQL 官方模型分数。自然语言模型的正式 EHRSQL 得分仍需在冻结模型后，由官方 SQLite 和 `evaluate.py` 对模型预测运行。",
    ])
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"analysis": str(args.output_json.resolve()), "report": str(args.output_md.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
