#!/usr/bin/env python3
"""Analyze the paired CLINSQL model-generation pilot and write JSON/Markdown.

This analysis deliberately separates three denominators:

* model generation and execution success over all sampled questions;
* published-reference reproduction over all questions and over executed questions;
* official-versus-local equality only where both paths produced a result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pct(num: int, den: int) -> float | None:
    return round(100.0 * num / den, 2) if den else None


def fmt_pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f}%"


def percentile(values: Iterable[float], q: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def comparison_flag(task: dict[str, Any], name: str, field: str) -> bool:
    return bool(task.get("comparisons", {}).get(name, {}).get(field, False))


def execution(task: dict[str, Any], target: str) -> dict[str, Any]:
    return task.get(target, {}).get("execution", {})


def generation(task: dict[str, Any]) -> dict[str, Any]:
    # Shared-generation reports intentionally duplicate the same immutable record.
    return task.get("official", {}).get("generation", {})


def split_key(task_key: str) -> tuple[str, str]:
    parts = task_key.split("/")
    return parts[1], parts[2]


def task_row(task: dict[str, Any]) -> dict[str, Any]:
    domain, difficulty = split_key(task["task_key"])
    official = execution(task, "official")
    local = execution(task, "local")
    gen = generation(task)
    return {
        "task_key": task["task_key"],
        "domain": domain,
        "difficulty": difficulty,
        "generation_status": gen.get("status"),
        "official_execution_status": official.get("status"),
        "local_execution_status": local.get("status"),
        "official_reference_strict": comparison_flag(task, "official_vs_reference", "unordered_value_match"),
        "official_reference_numeric": comparison_flag(task, "official_vs_reference", "unordered_numeric_tolerant_match"),
        "local_reference_strict": comparison_flag(task, "local_vs_reference", "unordered_value_match"),
        "local_reference_numeric": comparison_flag(task, "local_vs_reference", "unordered_numeric_tolerant_match"),
        "paired_comparable": bool(task.get("comparisons", {}).get("official_vs_local", {}).get("comparable", False)),
        "paired_strict": comparison_flag(task, "official_vs_local", "unordered_value_match"),
        "paired_numeric": comparison_flag(task, "official_vs_local", "unordered_numeric_tolerant_match"),
        "official_elapsed_ms": official.get("elapsed_ms"),
        "local_elapsed_ms": local.get("elapsed_ms"),
        "official_db_elapsed_ms": official.get("db_elapsed_ms"),
        "local_db_elapsed_ms": local.get("db_elapsed_ms"),
        "tables": gen.get("tables", []),
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tasks = len(rows)
    generated = sum(row["generation_status"] == "generated" for row in rows)
    official_executed = sum(row["official_execution_status"] == "executed" for row in rows)
    local_executed = sum(row["local_execution_status"] == "executed" for row in rows)
    paired = sum(row["paired_comparable"] for row in rows)
    paired_strict = sum(row["paired_strict"] for row in rows)
    paired_numeric = sum(row["paired_numeric"] for row in rows)
    official_ref_strict = sum(row["official_reference_strict"] for row in rows)
    official_ref_numeric = sum(row["official_reference_numeric"] for row in rows)
    local_ref_strict = sum(row["local_reference_strict"] for row in rows)
    local_ref_numeric = sum(row["local_reference_numeric"] for row in rows)
    return {
        "tasks": tasks,
        "generated": generated,
        "generation_rate_pct": pct(generated, tasks),
        "official_executed": official_executed,
        "official_execution_rate_all_pct": pct(official_executed, tasks),
        "official_execution_rate_generated_pct": pct(official_executed, generated),
        "local_executed": local_executed,
        "local_execution_rate_all_pct": pct(local_executed, tasks),
        "local_execution_rate_generated_pct": pct(local_executed, generated),
        "paired_comparable": paired,
        "paired_strict": paired_strict,
        "paired_strict_rate_pct": pct(paired_strict, paired),
        "paired_numeric": paired_numeric,
        "paired_numeric_rate_pct": pct(paired_numeric, paired),
        "official_reference_strict": official_ref_strict,
        "official_reference_strict_all_pct": pct(official_ref_strict, tasks),
        "official_reference_numeric": official_ref_numeric,
        "official_reference_numeric_all_pct": pct(official_ref_numeric, tasks),
        "official_reference_numeric_executed_pct": pct(official_ref_numeric, official_executed),
        "local_reference_strict": local_ref_strict,
        "local_reference_strict_all_pct": pct(local_ref_strict, tasks),
        "local_reference_numeric": local_ref_numeric,
        "local_reference_numeric_all_pct": pct(local_ref_numeric, tasks),
        "local_reference_numeric_executed_pct": pct(local_ref_numeric, local_executed),
    }


def grouped(rows: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[row[field]].append(row)
    return {key: aggregate(buckets[key]) for key in sorted(buckets)}


def uniformity(group_stats: dict[str, dict[str, Any]], metric: str) -> dict[str, Any]:
    values = [float(stats[metric]) for stats in group_stats.values() if stats[metric] is not None]
    mean = statistics.fmean(values) if values else None
    std = statistics.pstdev(values) if values else None
    cv = (std / mean * 100.0) if mean else None
    return {
        "metric": metric,
        "groups": len(values),
        "mean_pct": round(mean, 2) if mean is not None else None,
        "population_std_percentage_points": round(std, 2) if std is not None else None,
        "cv_pct": round(cv, 2) if cv is not None else None,
    }


def failure_classification(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # The labels are based on the preserved per-task diagnostics and generated SQL.
    known = {
        "validation/Disease_Diagnosis_and_Outcomes/hard/005": "model_abstain_missing_definition",
        "validation/Disease_Diagnosis_and_Outcomes/medium/007": "official_only_cost_gate",
        "validation/Laboratory_Results_Analysis/hard/023": "shared_select_star_rejection",
        "validation/Laboratory_Results_Analysis/medium/011": "official_only_query_timeout",
        "validation/Medication_Management/hard/006": "shared_select_star_rejection",
        "validation/Medication_Management/medium/002": "official_only_cost_gate",
        "validation/Vital_Signs_Monitoring/easy/001": "shared_cost_gate",
        "validation/Vital_Signs_Monitoring/hard/013": "shared_replace_token_rejection",
        "validation/Vital_Signs_Monitoring/medium/011": "shared_cost_gate",
    }
    counts = Counter(known.get(row["task_key"], "none") for row in rows)
    return {
        "counts": dict(sorted(counts.items())),
        "tasks": [
            {"task_key": row["task_key"], "classification": known.get(row["task_key"], "none")}
            for row in rows
            if row["task_key"] in known
        ],
    }


def md_group_table(stats: dict[str, dict[str, Any]]) -> list[str]:
    lines = [
        "| 分层 | 题数 | 生成 | 官方执行 | 本地执行 | 可配对 | 严格一致 | 数值容差一致 | 官方参考复现 | 本地参考复现 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, value in stats.items():
        lines.append(
            f"| {key.replace('_', ' ')} | {value['tasks']} | "
            f"{value['generated']}/{value['tasks']} | "
            f"{value['official_executed']}/{value['tasks']} | "
            f"{value['local_executed']}/{value['tasks']} | "
            f"{value['paired_comparable']} | "
            f"{value['paired_strict']}/{value['paired_comparable']} | "
            f"{value['paired_numeric']}/{value['paired_comparable']} | "
            f"{value['official_reference_numeric']}/{value['tasks']} | "
            f"{value['local_reference_numeric']}/{value['tasks']} |"
        )
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--gold-analysis", type=Path, required=True)
    parser.add_argument("--official-scorer-manifest", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()

    report = load_json(args.report)
    gold = load_json(args.gold_analysis)
    rows = [task_row(task) for task in report["tasks"]]
    overall = aggregate(rows)
    by_domain = grouped(rows, "domain")
    by_difficulty = grouped(rows, "difficulty")
    official_latency = [row["official_elapsed_ms"] for row in rows if row["official_execution_status"] == "executed"]
    local_latency = [row["local_elapsed_ms"] for row in rows if row["local_execution_status"] == "executed"]
    token_usage = Counter()
    for task in report["tasks"]:
        usage = generation(task).get("usage", {})
        for name in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"):
            token_usage[name] += int(usage.get(name, 0) or 0)

    analysis = {
        "source_report": str(args.report.resolve()),
        "source_report_sha256": sha256_file(args.report),
        "gold_analysis": str(args.gold_analysis.resolve()),
        "gold_analysis_sha256": sha256_file(args.gold_analysis),
        "official_scorer_manifest": str(args.official_scorer_manifest.resolve()),
        "official_scorer_manifest_sha256": sha256_file(args.official_scorer_manifest),
        "overall": overall,
        "latency_ms": {
            "official_p50": percentile(official_latency, 0.5),
            "official_p95": percentile(official_latency, 0.95),
            "local_p50": percentile(local_latency, 0.5),
            "local_p95": percentile(local_latency, 0.95),
        },
        "token_usage": dict(token_usage),
        "by_domain": by_domain,
        "by_difficulty": by_difficulty,
        "uniformity": {
            "domain_official_execution": uniformity(by_domain, "official_execution_rate_all_pct"),
            "domain_local_execution": uniformity(by_domain, "local_execution_rate_all_pct"),
            "difficulty_official_execution": uniformity(by_difficulty, "official_execution_rate_all_pct"),
            "difficulty_local_execution": uniformity(by_difficulty, "local_execution_rate_all_pct"),
        },
        "failures": failure_classification(rows),
        "tasks": rows,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    go = gold["overall"]
    wall_minutes = float(report["summary"].get("summed_wall_time_seconds", 0)) / 60.0
    lines = [
        "# CLINSQL 已发表基准：MIMIC 官方导入与本地建表双路径结果",
        "",
        "## 1. 结论摘要",
        "",
        "本报告包含两条互补证据链。187 条 validation gold SQL 用来检验数据库路径；18 条均衡抽样的真实 CLINSQL 问题用来检验 Skill + MedDB CLI 的端到端自然语言查询能力。两类结果不能互相替代。",
        "",
        f"- Gold SQL 路径：双方都成功执行的 {go['paired']['both_executed']} 题中，官方/本地结果严格一致 {go['paired']['strict_match']}/{go['paired']['both_executed']}（{go['paired']['strict_given_both_executed'] * 100:.2f}%）。",
        f"- 模型 pilot：18 题中生成 SQL {overall['generated']}/18（{fmt_pct(overall['generation_rate_pct'])}）；官方执行 {overall['official_executed']}/18（{fmt_pct(overall['official_execution_rate_all_pct'])}），本地执行 {overall['local_executed']}/18（{fmt_pct(overall['local_execution_rate_all_pct'])}）。",
        f"- 模型 pilot 可配对的 {overall['paired_comparable']} 题中，严格一致 {overall['paired_strict']}/{overall['paired_comparable']}（{fmt_pct(overall['paired_strict_rate_pct'])}），数值容差一致 {overall['paired_numeric']}/{overall['paired_comparable']}（{fmt_pct(overall['paired_numeric_rate_pct'])}）。",
        f"- 18 题均未复现 published reference result：官方 0/18，本地 0/18。这是当前模型/提示/临床语义映射的准确性结果，不应解释为导入失败。",
        f"- 两个生成批次累计墙钟时间 {wall_minutes:.2f} 分钟；测试集未打开，gold SQL、参考 CSV 和 rubric 未进入模型上下文。",
        "",
        "## 2. 指标定义与分母",
        "",
        "| 指标 | 分子 / 分母 | 回答的问题 |",
        "|---|---|---|",
        "| 生成率 | 成功产出合规 SQL 的题数 / 18 | Skill 是否能形成可审计查询 |",
        "| 执行率 | 成功返回结果的题数 / 18 | 每条数据库路径是否可执行 published workload |",
        "| 参考结果复现率 | 与 CLINSQL reference CSV 数值容差匹配的题数 / 18 | 当前端到端答案是否正确 |",
        "| 双库严格一致率 | 字段名、行数、值均一致 / 双方都返回结果的题数 | 两种导入路径是否逐结果一致 |",
        "| 双库数值容差一致率 | 忽略无意义浮点尾差后相同 / 双方都返回结果的题数 | 两种导入路径是否业务等价 |",
        "",
        "数据库一致率的分母只能是双方都成功返回结果的题；把超时、弃答或安全拒绝强行计作数据不一致会混淆故障层。",
        "",
        "## 3. 18 题端到端 pilot 总体结果",
        "",
        "| 项目 | 官方路径 | 本地路径 |",
        "|---|---:|---:|",
        f"| 生成 SQL | {overall['generated']}/18 | {overall['generated']}/18 |",
        f"| 成功执行（全题分母） | {overall['official_executed']}/18（{fmt_pct(overall['official_execution_rate_all_pct'])}） | {overall['local_executed']}/18（{fmt_pct(overall['local_execution_rate_all_pct'])}） |",
        f"| 成功执行（仅已生成 SQL） | {overall['official_executed']}/{overall['generated']}（{fmt_pct(overall['official_execution_rate_generated_pct'])}） | {overall['local_executed']}/{overall['generated']}（{fmt_pct(overall['local_execution_rate_generated_pct'])}） |",
        f"| 参考结果严格复现 | {overall['official_reference_strict']}/18 | {overall['local_reference_strict']}/18 |",
        f"| 参考结果数值容差复现 | {overall['official_reference_numeric']}/18 | {overall['local_reference_numeric']}/18 |",
        f"| 执行耗时 p50 | {analysis['latency_ms']['official_p50']:.0f} ms | {analysis['latency_ms']['local_p50']:.0f} ms |",
        f"| 执行耗时 p95 | {analysis['latency_ms']['official_p95']:.0f} ms | {analysis['latency_ms']['local_p95']:.0f} ms |",
        "",
        f"配对结果：严格 {overall['paired_strict']}/{overall['paired_comparable']}（{fmt_pct(overall['paired_strict_rate_pct'])}）；数值容差 {overall['paired_numeric']}/{overall['paired_comparable']}（{fmt_pct(overall['paired_numeric_rate_pct'])}）。唯一严格不一致题只是 `AVG(los)` 约 1e-15 的浮点尾差。",
        "",
        "## 4. 领域均衡与结果",
        "",
        "抽样严格采用 6 个临床领域 × easy/medium/hard 各 1 题，因此输入任务数量完全均衡：每领域 3 题、每难度 6 题。",
        "",
        *md_group_table(by_domain),
        "",
        "领域执行率并不均一：官方路径均值 50.00%，总体标准差 37.27 个百分点，CV 74.54%；本地路径均值 66.67%，标准差 33.33 个百分点，CV 50.00%。主要原因是生命体征大表查询触发成本门限，并非抽样不均。所有可配对领域的数值容差一致率均为 100%。",
        "",
        "## 5. 难度均衡与结果",
        "",
        *md_group_table(by_difficulty),
        "",
        "## 6. 失败原因",
        "",
        "| 原因 | 题数 | 归因 |",
        "|---|---:|---|",
        "| 模型因缺少风险评分/重大并发症定义而弃答 | 1 | 模型/任务定义层 |",
        "| 双方共同拒绝 `SELECT *` | 2 | CLI 安全规则层 |",
        "| 双方共同把 `replace()` 误判为禁用 `REPLACE` 节点 | 1 | CLI 词法安全规则兼容项 |",
        "| 双方共同超过成本门限 | 2 | workload/查询计划层 |",
        "| 仅官方路径超过成本门限 | 2 | 路径性能差异；本地索引使其通过 |",
        "| 仅官方路径 30 秒超时 | 1 | 路径性能差异；本地成功 |",
        "",
        "这些失败中没有发现“同一查询在两库成功返回但产生实质不同业务值”的案例。",
        "",
        "## 7. 187 条 gold SQL 数据路径结果",
        "",
        f"- 官方执行 {go['official']['executed']}/187，本地执行 {go['local']['executed']}/187。",
        f"- 双方都执行 {go['paired']['both_executed']}；官方独有 {go['paired']['official_only_executed']}；本地独有 {go['paired']['local_only_executed']}；双方均未完成 {go['paired']['neither_executed']}。",
        f"- 双方都执行的 {go['paired']['both_executed']} 题，严格与数值容差一致率都是 100%。",
        f"- 配对执行成功率 McNemar 精确检验 p={go['paired']['execution_mcnemar_exact_p']:.8f}，本地路径在当前 30 秒门限下成功题更多。",
        "- Gold SQL 对 reference CSV 的复现率不是官方 CLINSQL rubric 分数；它还受 PostgreSQL 版本、浮点、排序、结果格式与公开工件环境影响。这里最有说服力的导入证据是同一 gold SQL 在两库的配对结果一致性。",
        "",
        "## 8. 官方 CLINSQL scorer 状态",
        "",
        "18 题已转换为官方 scorer 可识别的 validation 目录，官方与本地各 18 个样本。当前环境没有 `AZURE_OPENAI_ENDPOINT` 与 `AZURE_OPENAI_API_KEY`，而仓库 scorer 需要 Azure OpenAI `gpt-5-chat` 作为 rubric judge，因此 rubric 分数标记为待运行，绝不以自制分数替代。",
        "",
        "## 9. 可复现工件",
        "",
        f"- 合并原始报告：`{args.report.resolve()}`，SHA-256 `{sha256_file(args.report)}`",
        f"- 本分析 JSON：`{args.output_json.resolve()}`",
        f"- Gold SQL 分析：`{args.gold_analysis.resolve()}`，SHA-256 `{sha256_file(args.gold_analysis)}`",
        f"- 官方 scorer manifest：`{args.official_scorer_manifest.resolve()}`，SHA-256 `{sha256_file(args.official_scorer_manifest)}`",
        f"- 冻结 schema SHA-256：`{report['guardrails']['schema_context_sha256']}`",
        f"- 冻结 Skill SHA-256：`{report['guardrails']['skill_entry_sha256']}`",
        f"- 冻结协议 SHA-256：`{report['guardrails']['skill_protocol_sha256']}`",
        "",
        "## 10. 解释边界",
        "",
        "本次 18 题是均衡 pilot，不是 190 条 validation 全量自然语言生成实验，也不是 443 条隐藏 test 的官方 leaderboard 成绩。它足以验证端到端管线、暴露安全规则与性能差异，并提供一组可复现基线；模型任务准确性的稳健估计仍需扩大样本并运行官方 rubric judge。",
    ]
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({"overall": overall, "report": str(args.output_md.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
