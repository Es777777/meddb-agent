"""Freeze and execute all validation tasks, with per-task resumable checkpoints.

The supervisor reads stored score flags only. Clinical rows stay in CLI exports.
Never tune prompts/Skill or retry a wrong answer based on its Gold result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import run_clinsql_model_generation as runner
from low_impact_benchmark import adaptive_workers, child_flags, lower_parent_priority, resource_snapshot

ROOT = Path(r"F:\database")
TOOLS = ROOT / "benchmark/tools"
ARTIFACTS = ROOT / "benchmark/artifacts"
VALIDATION = ROOT / "vendor/ClinSQL-main/data/splits/validation"
SKILL = Path(r"C:\Users\lenovo\.codex\skills\mimic-postgres-cli")
BASELINES = [
    "clinsql_model_generation_pilot_18_merged.json",
    "clinsql_model_generation_validation_sample20_skill_v2.json",
    "clinsql_skill_v3_dev_six_20261027.json",
    "clinsql_skill_v3_grounded_two_20261027.json",
    "clinsql_skill_v3_zero_shot_smoke_two_20261027.json",
    "clinsql_skill_v3_fewshot_smoke_two_20261027.json",
]


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return runner.sha256_file(Path(path))


def utc():
    return datetime.now(timezone.utc).isoformat()


def freeze(root: Path):
    if root.exists():
        raise ValueError("Run directory exists; use --resume, never overwrite frozen inputs")
    root.mkdir(parents=True)
    frozen = root / "frozen"
    (frozen / "tools").mkdir(parents=True)
    shutil.copytree(SKILL, frozen / "skill", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("run_clinsql_model_generation.py", "run_clinsql_validation_pilot.py",
                 "mimic_dictionary_grounding.py", "low_impact_benchmark.py", "run_clinsql_v4_full.py"):
        shutil.copy2(TOOLS / name, frozen / "tools" / name)
    for name in ("meddb-clinsql-official-validation.json", "meddb-clinsql-local-validation.json", "clinsql_generated_sql_v3.schema.json"):
        shutil.copy2(ROOT / "benchmark/configs" / name, frozen / name)
    shutil.copy2(ROOT / "vendor/ClinSQL-main/model_inference/mimic4_schema.txt", frozen / "mimic4_schema.txt")
    shutil.copy2(ARTIFACTS / "clinsql_official_manifest.json", frozen / "source_manifest.json")
    reserve = read(ARTIFACTS / "clinsql_validation_reserved_remaining18_20261027.json")
    reserve_ids = {item["task_key"] for item in reserve["tasks"]}
    exposed = set()
    (root / "baselines").mkdir()
    for name in BASELINES:
        shutil.copy2(ARTIFACTS / name, root / "baselines" / name)
        exposed.update(t["task_key"] for t in read(ARTIFACTS / name)["tasks"])
    tasks = []
    for question in runner.select_full(VALIDATION):
        key = runner.task_key(question, VALIDATION)
        reference = runner.locate_reference(question)
        tasks.append({
            "task_key": key, "question_sha256": sha(question), "reference_sha256": sha(reference),
            "historically_exposed": key in exposed, "previously_reserved18": key in reserve_ids,
        })
    if len(tasks) != 190 or len({item["task_key"] for item in tasks}) != 190:
        raise ValueError("Expected exactly 190 unique official validation tasks")
    manifest = {
        "purpose": "frozen_validation_evaluation", "created_at_utc": utc(),
        "authorization": "User explicitly requested all 190 validation tasks",
        "profile_sha256": sha(frozen / "skill/references/clinsql-development-profile.md"),
        "tasks": tasks,
        "parameters": {"model": "gpt-5.6-terra", "reasoning_effort": "medium", "workers": 8,
                       "generation_timeout_seconds": 360, "query_timeout_seconds": 45, "repair_attempts": 1,
                       "pairing_mode": "shared", "database_concurrency": 1, "dictionary_grounding": True,
                       "development_examples": False, "batch_size": 10,
                       "adaptive_model_workers": True, "memory_budget_gib_per_model_process": 0.5,
                       "scheduling_authorization": "User requested more threads for acceleration; only scheduling changes, no model/prompt/scorer/SQL changes"},
        "limitations": "Development-derived profile. Historically exposed tasks and former reserve reported separately. All validation Gold-port infrastructure was used previously; not a pristine external test. No test split, no official rubric judge.",
        "reserve_manifest_sha256": sha(ARTIFACTS / "clinsql_validation_reserved_remaining18_20261027.json"),
        "frozen_files": {str(p.relative_to(root)): sha(p) for p in sorted(frozen.rglob("*")) if p.is_file()},
        "baseline_files": {str(p.relative_to(root)): sha(p) for p in sorted((root / "baselines").glob("*.json"))},
    }
    runner.atomic_json(root / "evaluation_manifest.json", manifest)
    runner.atomic_json(root / "state.json", {"status": "frozen", "created_at_utc": utc(), "selected": 190})
    return manifest


def verify(root, manifest):
    for rel, expected in {**manifest["frozen_files"], **manifest["baseline_files"]}.items():
        if sha(root / rel) != expected:
            raise ValueError(f"Frozen file changed: {rel}")
    profile = root / "frozen/skill/references/clinsql-development-profile.md"
    runner.validate_evaluation_manifest(manifest, manifest["tasks"], sha(profile))
    for item in manifest["tasks"]:
        path = runner.select_one(VALIDATION, item["task_key"])[0]
        if sha(path) != item["question_sha256"] or sha(runner.locate_reference(path)) != item["reference_sha256"]:
            raise ValueError("Official question/reference changed after freeze")


def checkpoints(root):
    records, deferred = {}, {}
    for path in sorted((root / "batches").glob("*/checkpoints/*.json")):
        item = read(path)
        task = item["record"]
        key = task["task_key"]
        if task["official"]["generation"]["status"] == "resource_deferred":
            deferred[key] = task
            continue
        if key in records:
            raise ValueError(f"Duplicate completed model attempt: {key}")
        records[key] = task
    return records, {k: t for k, t in deferred.items() if k not in records}


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    pos = (len(values) - 1) * q
    lo = int(pos)
    return round(values[lo] + (values[min(lo + 1, len(values)-1)] - values[lo]) * (pos-lo), 3)


def wilson(successes, n):
    if not n:
        return None
    z = 1.95996398454
    p = successes/n
    mid = (p + z*z/(2*n))/(1+z*z/n)
    half = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n))/(1+z*z/n)
    return [round(100*(mid-half), 2), round(100*(mid+half), 2)]


def metric(tasks, target, repaired=False):
    n = len(tasks)
    out = Counter()
    times, gen_times = [], []
    for task in tasks:
        record = task[target]
        execution = record["execution"]
        comparison = task["comparisons"][target + "_vs_reference"]
        if repaired and "repair" in task:
            execution = task["repair"]["executions"][target]
            comparison = task["repair"]["comparisons"][target + "_vs_reference"]
            out["repair_attempted"] += 1
        out["generated"] += record["generation"]["status"] == "generated"
        out["executed"] += execution["status"] == "executed"
        out["gold_value_correct"] += bool(comparison.get("unordered_numeric_tolerant_value_only_match"))
        out["gold_alias_numeric_correct"] += bool(comparison.get("unordered_numeric_tolerant_match"))
        out["alias_only_difference"] += bool(comparison.get("unordered_numeric_tolerant_value_only_match")) and not bool(comparison.get("columns_match"))
        out["timeout"] += "timeout" in execution["status"] or "timeout" in record["generation"]["status"]
        if execution.get("elapsed_ms") is not None:
            times.append(execution["elapsed_ms"])
        if record["generation"].get("elapsed_ms") is not None:
            gen_times.append(record["generation"]["elapsed_ms"])
    return {"n": n, **{k: out[k] for k in ("generated", "executed", "gold_value_correct", "gold_alias_numeric_correct", "alias_only_difference", "timeout", "repair_attempted")},
            "gold_value_pct": round(100*out["gold_value_correct"]/n, 2) if n else None,
            "gold_value_wilson95_pct": wilson(out["gold_value_correct"], n),
            "execution_ms_p50": percentile(times, .5), "execution_ms_p95": percentile(times, .95),
            "generation_ms_p50": percentile(gen_times, .5), "generation_ms_p95": percentile(gen_times, .95)}


def paired(tasks, repaired=False):
    n = strict = numeric = 0
    for task in tasks:
        if repaired and "repair" in task:
            executions = task["repair"]["executions"]
            # Repair reports contain no O/L comparator; do not invent one.
            continue
        else:
            executions = {t: task[t]["execution"] for t in ("official", "local")}
            comparison = task["comparisons"]["official_vs_local"]
        if all(executions[t]["status"] == "executed" for t in executions):
            n += 1
            strict += bool(comparison.get("unordered_value_match"))
            numeric += bool(comparison.get("unordered_numeric_tolerant_match"))
    return {"both_executed": n, "strict_equal": strict, "numeric_equal": numeric}


def summarize(root, manifest, state):
    done, deferred = checkpoints(root)
    records = [done[t["task_key"]] for t in manifest["tasks"] if t["task_key"] in done]
    groups = {}
    for field in ("domain", "difficulty", "exposure", "previous_reserve"):
        buckets = {}
        for item in manifest["tasks"]:
            key = item["task_key"]
            label = {"domain": key.split("/")[1], "difficulty": key.split("/")[2],
                     "exposure": "historically_exposed" if item["historically_exposed"] else "not_in_prior_generation_reports",
                     "previous_reserve": "previously_reserved18" if item["previously_reserved18"] else "other_validation"}[field]
            if key in done:
                buckets.setdefault(label, []).append(done[key])
        groups[field] = {label: {t: metric(rows, t) for t in ("official", "local")} for label, rows in buckets.items()}
    failures = []
    for task in records:
        for target in ("official", "local"):
            ex = task[target]["execution"]
            comp = task["comparisons"][target + "_vs_reference"]
            if ex["status"] != "executed" or not comp.get("unordered_numeric_tolerant_value_only_match"):
                failures.append({"task_key": task["task_key"], "target": target,
                                 "generation_status": task[target]["generation"]["status"],
                                 "execution_status": ex["status"],
                                 "category": "result_mismatch_requires_semantic_or_port_analysis" if ex["status"] == "executed" else "generation_or_execution_failure",
                                 "diagnostic": ex.get("diagnostic"), "static_validation": ex.get("static_validation")})
    baseline_comparisons = []
    for name in BASELINES:
        old = read(root / "baselines" / name)
        common = [t for t in old["tasks"] if t["task_key"] in done]
        b = c = old_correct = new_correct = 0
        for task in common:
            a = bool(task["comparisons"]["official_vs_reference"].get("unordered_numeric_tolerant_value_only_match"))
            z = bool(done[task["task_key"]]["comparisons"]["official_vs_reference"].get("unordered_numeric_tolerant_value_only_match"))
            old_correct += a; new_correct += z; b += a and not z; c += z and not a
        discord = b+c
        pvalue = min(1, 2*sum(math.comb(discord, i) for i in range(min(b,c)+1))/2**discord) if discord else 1.0
        baseline_comparisons.append({"baseline": name, "common_tasks": len(common), "old_correct": old_correct,
                                     "v4_correct": new_correct, "old_only_correct": b, "v4_only_correct": c,
                                     "mcnemar_exact_p": pvalue, "limitation": "Historical development comparison, not a randomized experiment"})
    payload = {"updated_at_utc": utc(), "status": state, "selected": 190, "completed": len(done),
               "complete": len(done) == 190, "resource_deferred_pending": sorted(deferred),
               "evaluation_manifest_sha256": sha(root / "evaluation_manifest.json"),
               "limitations": manifest["limitations"],
               "first_pass": {t: metric(records, t) for t in ("official", "local")},
               "after_allowed_repair": {t: metric(records, t, True) for t in ("official", "local")},
               "paired_first_pass": paired(records), "by_group": groups,
               "same_task_baselines": baseline_comparisons, "failures": failures, "tasks": records}
    runner.atomic_json(root / "results.json", payload)
    lines = ["# CLINSQL v4 全量 validation 结果", "", f"状态：{state}；已处理 {len(done)}/190 题；更新：{payload['updated_at_utc']}。", "",
             "已完成前均为阶段性结果，不代表 190 题最终准确率。测试集未运行。官方双 rubric judge 未运行；下文为 PostgreSQL 本地结果比较指标。", "",
             "| 目标 | 生成 | 执行成功 | Gold 数值匹配（忽略别名） | 含列名数值匹配 | 超时 |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    if manifest["parameters"].get("adaptive_model_workers"):
        lines.insert(4, "用户授权的加速配置：模型并发按资源动态选择 1–8 路；保留 20% 内存底线并额外预算每路 0.5 GiB。数据库并发仍为 1。实际并发逐批写入调度日志；并非始终运行 8 路。")
    for target, label in (("official", "官方参考库"), ("local", "我们的数据库")):
        m = payload["first_pass"][target]
        suffix = f"，{m['gold_value_pct']}%" if payload["complete"] else "（阶段性）"
        lines.append(f"| {label} | {m['generated']}/{m['n']} | {m['executed']}/{m['n']} | {m['gold_value_correct']}/{m['n']}{suffix} | {m['gold_alias_numeric_correct']}/{m['n']} | {m['timeout']} |")
    p = payload["paired_first_pass"]
    lines += ["", f"两库都执行成功 {p['both_executed']} 题：严格一致 {p['strict_equal']} 题；数值容差一致 {p['numeric_equal']} 题。", "",
              "Gold 数值比较仍要求结果形状、列位置、分组值和数值匹配；未放宽原比较容差。执行成功不等于答案正确，两库一致也不等于符合 Gold。", "",
              "## 分组结果（已处理子集）", "", "| 分组 | 类别 | 已处理题数 | 官方库 Gold 数值匹配 | 本地库 Gold 数值匹配 |", "| --- | --- | ---: | ---: | ---: |"]
    for field, buckets in groups.items():
        for label, values in buckets.items():
            lines.append(f"| {field} | {label} | {values['official']['n']} | {values['official']['gold_value_correct']} | {values['local']['gold_value_correct']} |")
    lines += ["", "## 历史版本同题比较", "", "| 历史报告 | 已完成交集题数 | 旧版正确 | v4 正确 |", "| --- | ---: | ---: | ---: |"]
    for item in baseline_comparisons:
        lines.append(f"| {item['baseline']} | {item['common_tasks']} | {item['old_correct']} | {item['v4_correct']} |")
    lines += ["", "## 限制与审计", "", manifest["limitations"], "",
              "完整 JSON 另含首轮/修复后指标、95% Wilson 区间、生成与执行 p50/p95 延迟、同题 McNemar 检验、逐题状态和错误诊断。修复不使用 Gold，答错题不按 Gold 重试。", "",
              "结果不符目前只能定位为语义或参考端口差异，不能仅凭失败标记断言具体临床根因。运行中不再调整 Skill。", "",
              "资源延期不作错误答案，不生成虚假的全量 0%；尚未完成题目也不进入阶段性分数。"]
    path = root / "REPORT.md"
    temp = path.with_suffix(".md.tmp")
    temp.write_text("\n".join(lines)+"\n", encoding="utf-8")
    temp.replace(path)
    return done, deferred


def execute(root, manifest):
    verify(root, manifest)
    lock = root / "run.lock"
    with lock.open("x", encoding="utf-8") as handle:
        json.dump({"pid": os.getpid(), "started_at_utc": utc()}, handle)
    try:
        p = manifest["parameters"]
        codex = next(Path(r"C:\Users\lenovo\AppData\Roaming\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor").glob("*/bin/codex.exe"))
        frozen = root / "frozen"
        while True:
            done, deferred = summarize(root, manifest, "running")
            pending = [t["task_key"] for t in manifest["tasks"] if t["task_key"] not in done]
            if not pending:
                summarize(root, manifest, "complete")
                runner.atomic_json(root / "state.json", {"status": "complete", "completed": 190, "updated_at_utc": utc()})
                return 0
            snapshot = resource_snapshot()
            workers = adaptive_workers(snapshot, p["workers"]) if p.get("adaptive_model_workers") else p["workers"]
            if workers == 0:
                summarize(root, manifest, "needs_resource_recovery")
                runner.atomic_json(root / "state.json", {"status": "needs_resource_recovery", "completed": len(done),
                                                         "resource_state": snapshot, "actual_model_workers": 0, "updated_at_utc": utc()})
                return 75
            batch_id = len(list((root / "batches").glob("batch-*"))) + 1
            batch = root / "batches" / f"batch-{batch_id:04d}"
            batch.mkdir(parents=True)
            runner.atomic_json(batch / "scheduling.json", {"actual_model_workers": workers, "max_model_workers": p["workers"],
                                                            "resource_state": snapshot, "database_concurrency": 1, "created_at_utc": utc()})
            args = [sys.executable, str(frozen / "tools/run_clinsql_model_generation.py"),
                    "--validation-root", str(VALIDATION), "--meddb-root", str(ROOT / "meddb-agent"),
                    "--codex", str(codex), "--codex-home", r"C:\Users\lenovo\.codex-mimic-benchmark",
                    "--output-schema", str(frozen / "clinsql_generated_sql_v3.schema.json"),
                    "--schema-context", str(frozen / "mimic4_schema.txt"),
                    "--skill-entry", str(frozen / "skill/SKILL.md"),
                    "--skill-protocol", str(frozen / "skill/references/benchmark-protocol.md"),
                    "--static-validator", str(frozen / "skill/scripts/validate_generated_sql.py"),
                    "--gold-helper", str(frozen / "tools/run_clinsql_validation_pilot.py"),
                    "--official-config", str(frozen / "meddb-clinsql-official-validation.json"),
                    "--local-config", str(frozen / "meddb-clinsql-local-validation.json"),
                    "--run-root", str(batch), "--report", str(batch / "batch-report.json"),
                    "--development-profile", str(frozen / "skill/references/clinsql-development-profile.md"),
                    "--evaluation-manifest", str(root / "evaluation_manifest.json"),
                    "--pairing-mode", "shared", "--model", p["model"], "--reasoning-effort", p["reasoning_effort"],
                    "--workers", str(workers), "--generation-timeout-seconds", str(p["generation_timeout_seconds"]),
                    "--query-timeout-seconds", str(p["query_timeout_seconds"]), "--repair-attempts", str(p["repair_attempts"]),
                    "--dictionary-grounding", "--task-keys", *pending[:p["batch_size"]]]
            env = {**os.environ, "MEDDB_CODEX_PROFILE": "no-mcp", "MEDDB_CODEX_SAFE": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
            with (batch / "runner.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=env,
                                           cwd=ROOT / "meddb-agent", creationflags=child_flags())
                last_update = 0
                while process.poll() is None:
                    if time.monotonic() - last_update > 15:
                        done, _ = summarize(root, manifest, "running")
                        runner.atomic_json(root / "state.json", {"status": "running", "pid": os.getpid(), "child_pid": process.pid,
                                                                 "batch": batch.name, "completed": len(done), "actual_model_workers": workers, "updated_at_utc": utc()})
                        last_update = time.monotonic()
                    time.sleep(2)
            done, deferred = summarize(root, manifest, "running")
            if process.returncode or deferred:
                status = "needs_resource_recovery" if deferred and not process.returncode else "runner_error"
                summarize(root, manifest, status)
                runner.atomic_json(root / "state.json", {"status": status, "completed": len(done), "returncode": process.returncode, "updated_at_utc": utc()})
                return 75 if deferred else 1
            recent = read(batch / "batch-report.json")["tasks"]
            if recent and all(t["official"]["generation"]["status"] == "generation_error" for t in recent):
                summarize(root, manifest, "generation_service_error")
                runner.atomic_json(root / "state.json", {"status": "generation_service_error", "completed": len(done), "updated_at_utc": utc()})
                return 1
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--init", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    lower_parent_priority()
    root = args.run_root.resolve()
    if args.init:
        manifest = freeze(root)
        summarize(root, manifest, "frozen")
        print(json.dumps({"status": "frozen", "tasks": len(manifest["tasks"]), "run_root": str(root)}, ensure_ascii=False))
        return
    manifest = read(root / "evaluation_manifest.json")
    if args.summary_only:
        summarize(root, manifest, read(root / "state.json")["status"])
        return
    if not args.resume:
        raise ValueError("Use --init to freeze or --resume to execute the frozen run")
    raise SystemExit(execute(root, manifest))


if __name__ == "__main__":
    main()
