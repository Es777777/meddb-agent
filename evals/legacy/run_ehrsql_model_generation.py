#!/usr/bin/env python3
"""Run a balanced EHRSQL MIMIC-III validation pilot through Skill + MedDB CLI.

This is a raw PostgreSQL schema-transfer evaluation, not an EHRSQL leaderboard
submission.  Gold SQL and prior gold outputs are hidden until generation and
execution have completed on both database targets.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_compare_helper(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("ehrsql_compare", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load comparison helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def safe_env() -> dict[str, str]:
    env = os.environ.copy()
    env["MEDDB_CODEX_PROFILE"] = "no-mcp"
    env["MEDDB_CODEX_SAFE"] = "1"
    if not env.get("PGPASSFILE") and env.get("APPDATA"):
        env["PGPASSFILE"] = str(Path(env["APPDATA"]) / "postgresql" / "pgpass.conf")
    return env


def static_validate(
    validator: Path, sql_path: str, params_path: str, dataset: str, schema_context: Path,
) -> dict[str, Any]:
    """Run no-database SQL checks before EXPLAIN."""
    completed = subprocess.run(
        [
            sys.executable, str(validator), "--sql-file", sql_path,
            "--params-file", params_path, "--dataset", dataset,
            "--schema-context", str(schema_context),
        ],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", timeout=30, check=False,
    )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result = {"valid": False, "errors": ["validator_invalid_json"]}
    result["returncode"] = completed.returncode
    if completed.returncode not in (0, 2):
        result["errors"] = ["validator_process_error"]
    return result


def balanced_selection(tasks: list[dict[str, Any]], per_type: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for para_type in ("human", "machine"):
        candidates = sorted(
            (task for task in tasks if task.get("para_type") == para_type),
            key=lambda task: (len(task.get("tables", [])), str(task["id"])),
        )
        if len(candidates) < per_type:
            raise ValueError(f"Not enough {para_type} tasks: {len(candidates)}")
        if per_type == 1:
            indices = [len(candidates) // 2]
        else:
            indices = [round(i * (len(candidates) - 1) / (per_type - 1)) for i in range(per_type)]
        selected.extend(candidates[index] for index in indices)
    return sorted(selected, key=lambda task: (task.get("para_type", ""), str(task["id"])))


def parse_codex_events(stdout: str) -> tuple[str | None, dict[str, Any] | None]:
    thread_id = None
    usage = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
        if event.get("type") == "turn.completed":
            usage = event.get("usage")
    return thread_id, usage


def generation_prompt(
    question: str,
    schema_context: str,
    schema_sha256: str,
    skill_text: str,
    skill_sha256: str,
    protocol_text: str,
    protocol_sha256: str,
) -> str:
    return f"""Apply the frozen mimic-postgres-cli Skill contract and benchmark protocol below.
Do not run tools to reread them.

<skill sha256="{skill_sha256}">
{skill_text}
</skill>

<benchmark_protocol sha256="{protocol_sha256}">
{protocol_text}
</benchmark_protocol>

This is an EHRSQL MIMIC-III validation question evaluated as a raw PostgreSQL
schema-transfer pilot. It is not an official EHRSQL leaderboard submission.
Never open or search EHRSQL files, gold SQL, gold outputs, prior generated SQL,
or benchmark reports. Use only this public benchmark question:

{question}

The parent harness verified both MedDB targets are read-only and MCP-disabled.
Do not run status, catalog, schema, or query commands. Use this frozen official
MIMIC-III raw PostgreSQL DDL (SHA-256 {schema_sha256}) only as schema context:

<schema_context>
{schema_context}
</schema_context>

Use only schema-qualified mimiciii tables and columns that exist in the DDL.
Do not use EHRSQL preprocessing-only objects such as cost, admissions.age,
diagnoses_icd.charttime, or procedures_icd.charttime. The question explicitly
authorizes record-level output for benchmark scoring. Before SQL, make an internal
plan for version, grain, cohort/denominator, temporal rule, sources/join keys,
aggregate, and named parameters. Return one read-only PostgreSQL SELECT or
WITH...SELECT. Use only :parameter_name parameters: never $1, ?, %s, or string
interpolation. Return only the requested JSON object; put
the named-parameter object as valid JSON text in params_json. Do not create files
and do not execute the query; the parent harness will EXPLAIN and execute it."""


def generate_sql(
    *, codex: Path, codex_home: Path, meddb_root: Path, output_schema: Path,
    run_root: Path, task_id: str, question: str, schema_context: str,
    schema_sha256: str, skill_text: str, skill_sha256: str,
    protocol_text: str, protocol_sha256: str, model: str,
    reasoning_effort: str, timeout_seconds: int,
) -> dict[str, Any]:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", task_id)
    task_root = run_root / "shared"
    task_root.mkdir(parents=True, exist_ok=True)
    response_path = task_root / f"{slug}.generation.json"
    transcript_path = task_root / f"{slug}.codex.jsonl"
    prompt = generation_prompt(
        question, schema_context, schema_sha256, skill_text, skill_sha256,
        protocol_text, protocol_sha256,
    )
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [
                str(codex), "exec",
                "-c", f'model_reasoning_effort="{reasoning_effort}"',
                "-m", model,
                "-C", str(meddb_root),
                "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
                "--output-schema", str(output_schema), "--json", "-o", str(response_path),
                prompt,
            ],
            cwd=meddb_root, env={**safe_env(), "CODEX_HOME": str(codex_home)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        transcript_path.write_text((exc.stdout or "") + "\n" + (exc.stderr or ""), encoding="utf-8")
        return {"status": "generation_timeout", "elapsed_ms": round((time.perf_counter() - started) * 1000)}
    transcript_path.write_text(completed.stdout + "\n" + completed.stderr, encoding="utf-8")
    thread_id, usage = parse_codex_events(completed.stdout)
    base = {
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "returncode": completed.returncode,
        "thread_id": thread_id,
        "usage": usage,
        "transcript_path": str(transcript_path),
        "transcript_sha256": sha256_file(transcript_path),
    }
    if completed.returncode != 0 or not response_path.exists():
        return {**base, "status": "generation_error", "diagnostic": (completed.stdout + completed.stderr)[-2000:]}
    try:
        response = load_json(response_path)
        params = json.loads(response.get("params_json", "{}"))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return {**base, "status": "invalid_generation_json", "diagnostic": str(exc)}
    if response.get("status") != "sql":
        return {**base, "status": f"model_{response.get('status')}", "rationale": response.get("rationale")}
    if not isinstance(params, dict):
        return {**base, "status": "invalid_params"}
    sql = str(response.get("sql", ""))
    tables = [str(value).lower() for value in response.get("tables", [])]
    if not re.search(r"\bmimiciii\s*\.", sql, flags=re.IGNORECASE) or any(
        not value.startswith("mimiciii.") for value in tables
    ):
        return {**base, "status": "schema_target_error", "tables": tables}
    sql_path = task_root / f"{slug}.sql"
    params_path = task_root / f"{slug}.params.json"
    sql_path.write_text(sql.rstrip() + "\n", encoding="utf-8")
    params_path.write_text(json.dumps(params, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        **base, "status": "generated", "response_path": str(response_path),
        "response_sha256": sha256_file(response_path), "sql_path": str(sql_path),
        "sql_sha256": sha256_file(sql_path), "params_path": str(params_path),
        "params_sha256": sha256_file(params_path), "tables": tables,
    }


def preflight(meddb_root: Path, config_path: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [str(meddb_root / "bin" / "meddb.cmd"), "--config", str(config_path), "status"],
        cwd=meddb_root, env=safe_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", timeout=30, check=False,
    )
    combined = completed.stdout + "\n" + completed.stderr
    normalized = combined.lower()
    if completed.returncode != 0 or "meddb_mimic_reader" not in normalized or not re.search(
        r"db_default_read_only\s*\|\s*on", normalized
    ) or not re.search(r"mcp_disabled\s*\|\s*true", normalized):
        raise RuntimeError(f"Unsafe or failed preflight for {config_path}: {combined[-2000:]}")
    return {"config_path": str(config_path), "status_output_sha256": sha256_text(combined)}


def execute_generated(
    meddb_root: Path, config_path: Path, config: dict[str, Any], generated: dict[str, Any],
    task_id: str, target: str, timeout_seconds: int, static_validator: Path,
    schema_context: Path,
) -> dict[str, Any]:
    if generated.get("status") != "generated":
        return {"status": "not_executed", "reason": generated.get("status")}
    validation = static_validate(
        static_validator, generated["sql_path"], generated["params_path"], "mimiciii", schema_context,
    )
    if not validation.get("valid"):
        return {"status": "static_validation_error", "static_validation": validation}
    slug = f"ehrsql_skill_{target}_{re.sub(r'[^A-Za-z0-9_.-]+', '_', task_id)}.json"
    common = [
        str(meddb_root / "bin" / "meddb.cmd"), "--config", str(config_path), "query",
        "--sql-file", generated["sql_path"], "--params-file", generated["params_path"],
        "--detail", "--yes",
    ]
    started = time.perf_counter()
    try:
        explained = subprocess.run(
            common + ["--explain-only"], cwd=meddb_root, env=safe_env(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "explain_process_timeout"}
    if explained.returncode != 0:
        return {"status": "explain_error", "diagnostic": (explained.stdout + explained.stderr)[-2000:]}
    try:
        completed = subprocess.run(
            common + ["--format", "json", "--output", slug], cwd=meddb_root,
            env=safe_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_seconds, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "query_process_timeout"}
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    if completed.returncode != 0:
        return {"status": "query_error", "elapsed_ms": elapsed_ms, "diagnostic": (completed.stdout + completed.stderr)[-2000:]}
    export_root = Path(config["paths"]["exports"])
    if not export_root.is_absolute():
        export_root = (meddb_root / export_root).resolve()
    output_path = (export_root / slug).resolve()
    if not output_path.exists():
        return {"status": "missing_output", "elapsed_ms": elapsed_ms}
    payload = load_json(output_path)
    return {
        "status": "executed", "elapsed_ms": elapsed_ms,
        "db_elapsed_ms": payload.get("elapsed_ms"), "returned_rows": payload.get("returned_rows"),
        "truncated": payload.get("truncated"), "columns": payload.get("columns", []),
        "rows": payload.get("rows", []), "output_path": str(output_path),
        "output_sha256": sha256_file(output_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paired-report", type=Path, required=True)
    parser.add_argument("--meddb-root", type=Path, required=True)
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--output-schema", type=Path, required=True)
    parser.add_argument("--schema-context", type=Path, required=True)
    parser.add_argument("--skill-entry", type=Path, required=True)
    parser.add_argument("--skill-protocol", type=Path, required=True)
    parser.add_argument("--static-validator", type=Path, required=True)
    parser.add_argument("--compare-helper", type=Path, required=True)
    parser.add_argument("--official-config", type=Path, required=True)
    parser.add_argument("--local-config", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--per-type", type=int, default=9)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--generation-timeout-seconds", type=int, default=360)
    parser.add_argument("--query-timeout-seconds", type=int, default=45)
    args = parser.parse_args()

    meddb_root = args.meddb_root.resolve()
    source = load_json(args.paired_report.resolve())
    selected = balanced_selection(source["tasks"], args.per_type)
    compare = load_compare_helper(args.compare_helper.resolve())
    configs = {
        "official": (args.official_config.resolve(), load_json(args.official_config.resolve())),
        "local": (args.local_config.resolve(), load_json(args.local_config.resolve())),
    }
    guard_files = {
        "schema_context": args.schema_context.resolve(),
        "skill_entry": args.skill_entry.resolve(),
        "skill_protocol": args.skill_protocol.resolve(),
        "static_validator": args.static_validator.resolve(),
    }
    guard_text = {key: path.read_text(encoding="utf-8").strip() for key, path in guard_files.items()}
    guard_sha = {key: sha256_file(path) for key, path in guard_files.items()}
    preflight_results = {name: preflight(meddb_root, path) for name, (path, _) in configs.items()}
    run_root = args.run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    started = time.perf_counter()
    for index, task in enumerate(selected, start=1):
        generated = generate_sql(
            codex=args.codex.resolve(), codex_home=args.codex_home.resolve(), meddb_root=meddb_root,
            output_schema=args.output_schema.resolve(), run_root=run_root, task_id=str(task["id"]),
            question=task["question"], schema_context=guard_text["schema_context"],
            schema_sha256=guard_sha["schema_context"], skill_text=guard_text["skill_entry"],
            skill_sha256=guard_sha["skill_entry"], protocol_text=guard_text["skill_protocol"],
            protocol_sha256=guard_sha["skill_protocol"], model=args.model,
            reasoning_effort=args.reasoning_effort, timeout_seconds=args.generation_timeout_seconds,
        )
        counts[f"generation:{generated['status']}"] += 1
        actuals: dict[str, dict[str, Any]] = {}
        for target, (config_path, config) in configs.items():
            actuals[target] = execute_generated(
                meddb_root, config_path, config, generated, str(task["id"]), target,
                args.query_timeout_seconds, args.static_validator.resolve(), args.schema_context.resolve(),
            )
            counts[f"{target}:execution:{actuals[target]['status']}"] += 1
        gold_payload = load_json(Path(task["official"]["output_path"]))
        gold = {"status": "executed", "columns": gold_payload.get("columns", []), "rows": gold_payload.get("rows", [])}
        comparisons: dict[str, Any] = {}
        for target in ("official", "local"):
            if actuals[target].get("status") == "executed":
                comparisons[f"{target}_vs_gold"] = compare.compare_targets(gold, actuals[target])
                if comparisons[f"{target}_vs_gold"].get("unordered_value_match"):
                    counts[f"{target}:gold_strict_match"] += 1
                if comparisons[f"{target}_vs_gold"].get("unordered_numeric_tolerant_match"):
                    counts[f"{target}:gold_numeric_match"] += 1
            else:
                comparisons[f"{target}_vs_gold"] = {"comparable": False}
        if all(actuals[target].get("status") == "executed" for target in ("official", "local")):
            comparisons["official_vs_local"] = compare.compare_targets(actuals["official"], actuals["local"])
            counts["paired:comparable"] += 1
            if comparisons["official_vs_local"].get("unordered_value_match"):
                counts["paired:strict_match"] += 1
            if comparisons["official_vs_local"].get("unordered_numeric_tolerant_match"):
                counts["paired:numeric_match"] += 1
        else:
            comparisons["official_vs_local"] = {"comparable": False}
        for target in ("official", "local"):
            actuals[target].pop("rows", None)
        records.append({
            "id": task["id"], "para_type": task.get("para_type"),
            "question_sha256": sha256_text(task["question"]), "tables": task.get("tables", []),
            "gold_sql_sha256": task.get("sql_sha256"), "gold_output_sha256": task["official"].get("output_sha256"),
            "generation": generated, "official": actuals["official"], "local": actuals["local"],
            "comparisons": comparisons,
        })
        print(
            f"progress={index}/{len(selected)} id={task['id']} para={task.get('para_type')} "
            f"gen={generated['status']} O={actuals['official']['status']} L={actuals['local']['status']} "
            f"O_gold={comparisons['official_vs_gold'].get('unordered_numeric_tolerant_match', False)} "
            f"O_L={comparisons['official_vs_local'].get('unordered_numeric_tolerant_match', False)}",
            flush=True,
        )

    manifest = [{"id": record["id"], "para_type": record["para_type"], "question_sha256": record["question_sha256"]} for record in records]
    payload = {
        "report_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "EHRSQL MIMIC-III validation raw PostgreSQL schema-transfer Skill+CLI pilot",
        "guardrails": {
            "official_leaderboard_comparable": False, "gold_visible_to_model": False,
            "test_split_opened": False, "mcp_enabled": False, "database_role": "meddb_mimic_reader",
            "read_only": True, "pairing_mode": "shared", "model": args.model,
            "reasoning_effort": args.reasoning_effort, "preflight": preflight_results,
            "files": {key: {"path": str(path), "sha256": guard_sha[key]} for key, path in guard_files.items()},
        },
        "source_report": str(args.paired_report.resolve()),
        "source_report_sha256": sha256_file(args.paired_report.resolve()),
        "selection_manifest": manifest,
        "selection_manifest_sha256": sha256_text(json.dumps(manifest, sort_keys=True)),
        "summary": {
            "tasks": len(records), "generation_units": len(records),
            "execution_units": len(records) * 2, "counts": dict(sorted(counts.items())),
            "wall_time_seconds": round(time.perf_counter() - started, 3),
        },
        "tasks": records,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
