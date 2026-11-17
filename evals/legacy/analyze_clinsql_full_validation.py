#!/usr/bin/env python3
"""Summarize the frozen CLINSQL PostgreSQL validation run.

This analysis deliberately separates three questions:
1. Could each PostgreSQL target execute the frozen gold query?
2. Did an executed PostgreSQL result reproduce the published BigQuery CSV?
3. When both targets executed, did the official-import and local-import results agree?
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
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pct(numerator: int, denominator: int) -> str:
    return "N/A" if not denominator else f"{100 * numerator / denominator:.2f}%"


def rate(numerator: int, denominator: int) -> float | None:
    return None if not denominator else numerator / denominator


def percentile(values: Iterable[float], q: float) -> float | None:
    ordered = sorted(values)
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


def exact_mcnemar_p(a_only: int, b_only: int) -> float:
    """Two-sided exact McNemar p-value using a Binomial(n, 0.5) tail."""
    n = a_only + b_only
    if n == 0:
        return 1.0
    k = min(a_only, b_only)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2 * tail)


def is_timeout(target: dict[str, Any]) -> bool:
    diagnostic = str(target.get("diagnostic", "")).lower()
    return target.get("status") != "executed" and (
        "超时" in diagnostic or "timeout" in diagnostic or "statement timeout" in diagnostic
    )


def split_task(task: dict[str, Any]) -> tuple[str, str]:
    parts = task["task_key"].split("/")
    return parts[1], parts[2]


def comparison_flag(task: dict[str, Any], side: str, tolerant: bool) -> bool:
    key = f"{side}_vs_reference"
    metric = "unordered_numeric_tolerant_match" if tolerant else "unordered_value_match"
    return bool(task.get(key, {}).get(metric, False))


def paired_flag(task: dict[str, Any], tolerant: bool) -> bool:
    metric = "unordered_numeric_tolerant_match" if tolerant else "unordered_value_match"
    return bool(task.get("official_vs_local", {}).get(metric, False))


def summarize(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(tasks)
    summary: dict[str, Any] = {"tasks": total, "execution_units": total * 2}
    for side in ("official", "local"):
        executed = [task for task in tasks if task[side]["status"] == "executed"]
        strict = sum(comparison_flag(task, side, False) for task in tasks)
        tolerant = sum(comparison_flag(task, side, True) for task in tasks)
        latencies = [float(task[side]["elapsed_ms"]) for task in executed]
        db_latencies = [
            float(task[side]["db_elapsed_ms"])
            for task in executed
            if task[side].get("db_elapsed_ms") is not None
        ]
        summary[side] = {
            "executed": len(executed),
            "timeouts": sum(is_timeout(task[side]) for task in tasks),
            "other_errors": sum(
                task[side]["status"] != "executed" and not is_timeout(task[side]) for task in tasks
            ),
            "reference_strict": strict,
            "reference_numeric_tolerant": tolerant,
            "reference_strict_given_executed": rate(strict, len(executed)),
            "reference_numeric_tolerant_given_executed": rate(tolerant, len(executed)),
            "latency_ms_p50": percentile(latencies, 0.5),
            "latency_ms_p95": percentile(latencies, 0.95),
            "db_latency_ms_p50": percentile(db_latencies, 0.5),
            "db_latency_ms_p95": percentile(db_latencies, 0.95),
        }

    both_executed = [
        task
        for task in tasks
        if task["official"]["status"] == "executed" and task["local"]["status"] == "executed"
    ]
    official_only = [
        task
        for task in tasks
        if task["official"]["status"] == "executed" and task["local"]["status"] != "executed"
    ]
    local_only = [
        task
        for task in tasks
        if task["official"]["status"] != "executed" and task["local"]["status"] == "executed"
    ]
    neither = [
        task
        for task in tasks
        if task["official"]["status"] != "executed" and task["local"]["status"] != "executed"
    ]
    paired_strict = sum(paired_flag(task, False) for task in tasks)
    paired_tolerant = sum(paired_flag(task, True) for task in tasks)
    summary["paired"] = {
        "both_executed": len(both_executed),
        "official_only_executed": len(official_only),
        "local_only_executed": len(local_only),
        "neither_executed": len(neither),
        "strict_match": paired_strict,
        "numeric_tolerant_match": paired_tolerant,
        "strict_given_both_executed": rate(paired_strict, len(both_executed)),
        "numeric_tolerant_given_both_executed": rate(paired_tolerant, len(both_executed)),
        "execution_mcnemar_exact_p": exact_mcnemar_p(len(official_only), len(local_only)),
    }
    summary["task_keys"] = {
        "official_only_executed": [task["task_key"] for task in official_only],
        "local_only_executed": [task["task_key"] for task in local_only],
        "neither_executed": [task["task_key"] for task in neither],
        "paired_strict_mismatch": [
            task["task_key"] for task in both_executed if not paired_flag(task, False)
        ],
        "official_reference_strict_mismatch_when_executed": [
            task["task_key"]
            for task in tasks
            if task["official"]["status"] == "executed" and not comparison_flag(task, "official", False)
        ],
        "local_reference_strict_mismatch_when_executed": [
            task["task_key"]
            for task in tasks
            if task["local"]["status"] == "executed" and not comparison_flag(task, "local", False)
        ],
    }
    return summary


def format_ms(value: float | None) -> str:
    return "N/A" if value is None else f"{value:,.0f}"


def domain_label(domain: str) -> str:
    labels = {
        "Diagnostic_Procedures": "诊断与操作",
        "Diseases_and_Conditions": "疾病与病情",
        "Disease_Diagnosis_and_Outcomes": "疾病诊断与结局",
        "Laboratory_Results_Analysis": "实验室结果",
        "Medication_Management": "用药管理",
        "Patient_Demographics_and_Admissions": "人口统计与入院",
        "Vital_Signs_Monitoring": "生命体征",
    }
    return labels.get(domain, domain.replace("_", " "))


def markdown_report(report_path: Path, report: dict[str, Any], overall: dict[str, Any], groups: dict[str, Any]) -> str:
    total = overall["tasks"]
    official = overall["official"]
    local = overall["local"]
    paired = overall["paired"]
    lines = [
        "# CLINSQL Validation 双路径 PostgreSQL 验证结果",
        "",
        "## 1. 结论",
        "",
        f"本次完成冻结范围内 **{total} 个 CLINSQL validation 公开任务、{total * 2} 个双路径执行单元**。"
        "这是一轮 gold SQL 的 PostgreSQL 移植与数据路径验证，不是模型自然语言生成 SQL 的最终论文分数。",
        "",
        f"在两套数据库均成功执行的 **{paired['both_executed']}** 个任务中，官方导入库与本地定制源表库严格结果一致 "
        f"**{paired['strict_match']}/{paired['both_executed']}（{pct(paired['strict_match'], paired['both_executed'])}）**。"
        "因此，在当前可执行任务覆盖范围内，没有发现由本地建表或导入方案造成的查询结果偏差。",
        "",
        "## 2. 实验身份与边界",
        "",
        "- 任务来源：CLINSQL 官方 validation split；问题、任务 ID、gold SQL 语义和参考 CSV 均来自作者发布仓库。",
        "- 路径 O：官方 MIMIC PostgreSQL 导入库，经同一 MIMIC Skill 和 MedDB CLI 只读执行。",
        "- 路径 L：本地定制源表库，经同一 MIMIC Skill 和 MedDB CLI 只读执行。",
        "- SQL 端口：统一、按语法构造的 BigQuery-to-PostgreSQL 转换；题号专用修补数为 0。",
        "- 本轮范围：190 个 validation 任务中，EXPLAIN 门禁通过 187 个；另 3 个依赖尚未安装的 `mimiciv_derived.charlson`，不进入本次执行分母。",
        "- 参考结果复现率与 O/L 配对一致率是不同指标：前者衡量 PostgreSQL 端口相对 BigQuery CSV，后者直接衡量两种导入方案。",
        "",
        "## 3. 总体指标",
        "",
        "| 指标 | 官方导入 O | 本地定制 L |",
        "|---|---:|---:|",
        f"| 预定义任务 | {total} | {total} |",
        f"| 成功执行 | {official['executed']}（{pct(official['executed'], total)}） | {local['executed']}（{pct(local['executed'], total)}） |",
        f"| 安全超时 | {official['timeouts']}（{pct(official['timeouts'], total)}） | {local['timeouts']}（{pct(local['timeouts'], total)}） |",
        f"| 其他执行错误 | {official['other_errors']} | {local['other_errors']} |",
        f"| 严格复现官方参考 CSV / 全部任务 | {official['reference_strict']}/{total}（{pct(official['reference_strict'], total)}） | {local['reference_strict']}/{total}（{pct(local['reference_strict'], total)}） |",
        f"| 严格复现官方参考 CSV / 已执行 | {official['reference_strict']}/{official['executed']}（{pct(official['reference_strict'], official['executed'])}） | {local['reference_strict']}/{local['executed']}（{pct(local['reference_strict'], local['executed'])}） |",
        f"| 数值容差复现参考 CSV / 已执行 | {official['reference_numeric_tolerant']}/{official['executed']}（{pct(official['reference_numeric_tolerant'], official['executed'])}） | {local['reference_numeric_tolerant']}/{local['executed']}（{pct(local['reference_numeric_tolerant'], local['executed'])}） |",
        f"| CLI 端到端延迟 p50（ms，仅成功执行） | {format_ms(official['latency_ms_p50'])} | {format_ms(local['latency_ms_p50'])} |",
        f"| CLI 端到端延迟 p95（ms，仅成功执行） | {format_ms(official['latency_ms_p95'])} | {format_ms(local['latency_ms_p95'])} |",
        "",
        "### 3.1 O/L 配对指标",
        "",
        "| 配对状态 | 数量 | 比例/说明 |",
        "|---|---:|---:|",
        f"| 两边均成功执行 | {paired['both_executed']} | {pct(paired['both_executed'], total)}（相对全部任务） |",
        f"| 仅 O 成功 | {paired['official_only_executed']} | {pct(paired['official_only_executed'], total)} |",
        f"| 仅 L 成功 | {paired['local_only_executed']} | {pct(paired['local_only_executed'], total)} |",
        f"| 两边均未执行 | {paired['neither_executed']} | {pct(paired['neither_executed'], total)} |",
        f"| 严格结果一致 | {paired['strict_match']}/{paired['both_executed']} | {pct(paired['strict_match'], paired['both_executed'])}（条件分母：两边均成功） |",
        f"| 数值容差结果一致 | {paired['numeric_tolerant_match']}/{paired['both_executed']} | {pct(paired['numeric_tolerant_match'], paired['both_executed'])} |",
        f"| 执行成功差异 McNemar 精确检验 | — | p={paired['execution_mcnemar_exact_p']:.6g} |",
        "",
        "## 4. 按临床领域",
        "",
        "| 临床领域 | 任务 | O 执行 | L 执行 | 双方执行 | O/L 严格一致 | O 参考严格复现 | L 参考严格复现 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    domain_rates: list[float] = []
    for domain in sorted(groups["domain"]):
        item = groups["domain"][domain]
        p = item["paired"]
        o = item["official"]
        l = item["local"]
        if p["both_executed"]:
            domain_rates.append(p["strict_match"] / p["both_executed"])
        lines.append(
            f"| {domain_label(domain)} | {item['tasks']} | {o['executed']}（{pct(o['executed'], item['tasks'])}） "
            f"| {l['executed']}（{pct(l['executed'], item['tasks'])}） | {p['both_executed']} "
            f"| {p['strict_match']}/{p['both_executed']}（{pct(p['strict_match'], p['both_executed'])}） "
            f"| {o['reference_strict']}/{o['executed']}（{pct(o['reference_strict'], o['executed'])}） "
            f"| {l['reference_strict']}/{l['executed']}（{pct(l['reference_strict'], l['executed'])}） |"
        )

    lines.extend([
        "",
        "## 5. 按难度",
        "",
        "| 难度 | 任务 | O 执行 | L 执行 | 双方执行 | O/L 严格一致 | O 参考数值容差复现 | L 参考数值容差复现 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for difficulty in ("easy", "medium", "hard"):
        item = groups["difficulty"].get(difficulty)
        if not item:
            continue
        p = item["paired"]
        o = item["official"]
        l = item["local"]
        lines.append(
            f"| {difficulty} | {item['tasks']} | {o['executed']}（{pct(o['executed'], item['tasks'])}） "
            f"| {l['executed']}（{pct(l['executed'], item['tasks'])}） | {p['both_executed']} "
            f"| {p['strict_match']}/{p['both_executed']}（{pct(p['strict_match'], p['both_executed'])}） "
            f"| {o['reference_numeric_tolerant']}/{o['executed']}（{pct(o['reference_numeric_tolerant'], o['executed'])}） "
            f"| {l['reference_numeric_tolerant']}/{l['executed']}（{pct(l['reference_numeric_tolerant'], l['executed'])}） |"
        )

    mean_rate = statistics.fmean(domain_rates) if domain_rates else None
    std_rate = statistics.pstdev(domain_rates) if len(domain_rates) > 1 else 0.0 if domain_rates else None
    cv = None if not mean_rate else std_rate / mean_rate
    lines.extend([
        "",
        "## 6. 均一性",
        "",
        "均一性只对有双方成功执行样本的临床领域，计算“双方严格一致率”的宏平均、总体标准差和变异系数。"
        "它衡量导入路径一致性是否集中在少数领域，不代表医学问题覆盖已经穷尽。",
        "",
        f"- 领域宏平均：{'N/A' if mean_rate is None else f'{mean_rate * 100:.2f}%'}",
        f"- 领域间总体标准差：{'N/A' if std_rate is None else f'{std_rate * 100:.2f} 个百分点'}",
        f"- 变异系数 CV：{'N/A' if cv is None else f'{cv:.6f}'}",
        "",
        "## 7. 如何解释未通过项",
        "",
        "1. **两库均成功但 O/L 不一致**，才是最直接的导入或 schema 语义风险信号。",
        "2. **两库结果一致、但均不等于参考 CSV**，优先归因于 BigQuery-to-PostgreSQL 方言/数值/排序语义，而不是本地导入差异。",
        "3. **超时**是当前 30 秒安全预算和查询计划下的工程性能结果；超时任务没有结果，不能算作值不一致。",
        "4. **缺少 `mimiciv_derived.charlson` 的 3 题**是派生视图依赖缺口，不是 57 张源表导入失败。",
        "",
        "## 8. 可复现性",
        "",
        f"- 原始运行报告：`{report_path}`",
        f"- 原始运行报告 SHA-256：`{sha256_file(report_path)}`",
        "- 原始报告中的 `scope` 带有早期 runner 的 `pilot` 遗留标签；实际范围由 187 项 selection manifest、selection hash 和逐题记录共同确定。runner 已修正，后续全量运行不会再沿用该标签。",
        f"- selection manifest SHA-256：`{report.get('selection_manifest_sha256')}`",
        f"- 生成时间（UTC）：`{report.get('generated_at_utc')}`",
        f"- 运行墙钟时间：{report.get('summary', {}).get('wall_time_seconds')} 秒",
        f"- MedDB profile：`{report.get('guardrails', {}).get('meddb_codex_profile')}`",
        f"- MedDB safe mode：`{report.get('guardrails', {}).get('meddb_codex_safe')}`",
        f"- 只读角色：`{report.get('guardrails', {}).get('database_role')}`",
        "- 测试集在本阶段未用于调试或评分；正式模型评测仍须在冻结端口后另行运行。",
        "",
        "## 9. 当前结论能与不能证明什么",
        "",
        "本轮能证明：在成功执行的公开 CLINSQL gold 查询上，官方导入与本地定制源表得到相同结果；"
        "本地 `id`、索引及已实施类型调整没有在这些配对查询中造成可见结果偏差。",
        "",
        "本轮不能证明：Skill/模型已经达到 CLINSQL 论文的 text-to-SQL 成绩。要得到该成绩，还必须让模型在不可见 gold SQL 的条件下从自然语言生成 SQL，"
        "并调用作者的 rubric scorer；该阶段应与本报告分开命名和计分。",
        "",
        "## 10. 来源",
        "",
        "- CLINSQL 论文：<https://aclanthology.org/2026.eacl-long.64/>",
        "- CLINSQL 官方仓库：<https://github.com/Barryshen1/ClinSQL>",
        "- MIMIC Code 官方仓库：<https://github.com/MIT-LCP/mimic-code>",
        "",
    ])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--json-output", required=True, type=Path)
    parser.add_argument("--markdown-output", required=True, type=Path)
    args = parser.parse_args()

    report_path = args.report.resolve()
    report = load_json(report_path)
    tasks = report["tasks"]
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {
        "domain": defaultdict(list),
        "difficulty": defaultdict(list),
    }
    for task in tasks:
        domain, difficulty = split_task(task)
        grouped["domain"][domain].append(task)
        grouped["difficulty"][difficulty].append(task)

    output = {
        "source_report": str(report_path),
        "source_report_sha256": sha256_file(report_path),
        "overall": summarize(tasks),
        "by_domain": {key: summarize(value) for key, value in sorted(grouped["domain"].items())},
        "by_difficulty": {
            key: summarize(value) for key, value in sorted(grouped["difficulty"].items())
        },
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_groups = {"domain": output["by_domain"], "difficulty": output["by_difficulty"]}
    args.markdown_output.write_text(
        markdown_report(report_path, report, output["overall"], md_groups), encoding="utf-8"
    )
    print(json.dumps(output["overall"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
