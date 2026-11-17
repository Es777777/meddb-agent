#!/usr/bin/env python3
"""Run CLINSQL validation questions through isolated Codex Skill + MedDB CLI.

The model sees only the public question and catalog metadata. Gold SQL, result
CSV, and rubrics are loaded only after both target executions have completed.
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

from low_impact_benchmark import bounded_db_env, bounded_model_call, child_flags, configure_model_slots, lower_parent_priority, shared_generation_stream


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_development_examples(path: Path, scored_ids: set[str]) -> dict[str, Any]:
    pack = load_json(path)
    if pack.get("purpose") != "declared_validation_development_examples":
        raise ValueError("Example pack must declare the development-only partition")
    allowed = {"task_key", "question", "sql", "question_sha256", "sql_sha256"}
    for item in pack["examples"]:
        if set(item) != allowed or not item["task_key"].startswith("validation/"):
            raise ValueError("Example packs may contain validation question/SQL/source hashes only")
    example_ids = [item["task_key"] for item in pack["examples"]]
    if len(example_ids) != len(set(example_ids)):
        raise ValueError("Duplicate development example IDs")
    overlap = set(example_ids) & scored_ids
    if overlap:
        raise ValueError(f"Refusing Gold leakage: example/scored ID overlap: {sorted(overlap)}")
    return pack


def load_development_manifest(path: Path, scored_ids: set[str]) -> dict[str, Any]:
    pack = load_json(path)
    if pack.get("purpose") != "already_exposed_development_regression":
        raise ValueError("Profile regression requires an explicitly exposed development partition")
    allowed = pack.get("already_exposed_ids", [])
    if not allowed or len(allowed) != len(set(allowed)) or any(
        not isinstance(key, str) or not key.startswith("validation/") for key in allowed
    ):
        raise ValueError("Development manifest must contain unique validation IDs")
    if not scored_ids or not scored_ids <= set(allowed):
        raise ValueError("Refusing development-profile evaluation on unlisted/held-out tasks")
    return pack


def validate_evaluation_manifest(pack: dict, selected: list[dict], profile_sha256: str) -> None:
    if pack.get("purpose") != "frozen_validation_evaluation" or pack.get("profile_sha256") != profile_sha256:
        raise ValueError("Frozen evaluation purpose/profile hash mismatch")
    entries = pack.get("tasks", [])
    allowed = {item["task_key"]: item["question_sha256"] for item in entries}
    if not entries or len(allowed) != len(entries) or any(not key.startswith("validation/") for key in allowed):
        raise ValueError("Frozen evaluation IDs must be unique and validation-only")
    if not selected or any(allowed.get(item["task_key"]) != item["question_sha256"] for item in selected):
        raise ValueError("Evaluation task/question hash is not in the frozen manifest")


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_runner(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("clinsql_gold_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def static_validate(
    validator: Path, sql_path: str, params_path: str, schema_context: Path,
) -> dict[str, Any]:
    """Run no-database SQL checks before EXPLAIN."""
    completed = subprocess.run(
        [
            sys.executable, str(validator), "--sql-file", sql_path,
            "--params-file", params_path, "--dataset", "mimiciv",
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


def task_key(query_path: Path, validation_root: Path) -> str:
    rel = query_path.relative_to(validation_root)
    difficulty = rel.parts[1].replace("_level_queries", "")
    return f"validation/{rel.parts[0]}/{difficulty}/{rel.parts[2]}"


def select_pilot(validation_root: Path) -> list[Path]:
    selected: list[Path] = []
    for domain in sorted(path for path in validation_root.iterdir() if path.is_dir()):
        for difficulty in ("easy_level_queries", "medium_level_queries", "hard_level_queries"):
            root = domain / difficulty
            if not root.exists():
                continue
            candidates = sorted(root.glob("*/query_*.txt"))
            if candidates:
                selected.append(candidates[0])
    return sorted(selected, key=lambda path: task_key(path, validation_root))


def select_full(validation_root: Path) -> list[Path]:
    """Return every official validation question in deterministic path order."""
    return sorted(
        validation_root.glob("*/*_level_queries/*/query_*.txt"),
        key=lambda path: task_key(path, validation_root),
    )


def select_one(validation_root: Path, key: str) -> list[Path]:
    parts = key.split("/")
    if len(parts) != 4 or parts[0] != "validation":
        raise ValueError("--task-key must be validation/<domain>/<difficulty>/<id>")
    path = validation_root / parts[1] / f"{parts[2]}_level_queries" / parts[3] / f"query_{parts[3]}.txt"
    if not path.exists():
        raise FileNotFoundError(path)
    return [path]


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
    target: str,
    schema_context: str,
    schema_sha256: str,
    skill_text: str,
    skill_sha256: str,
    protocol_text: str,
    protocol_sha256: str,
) -> str:
    return f"""Apply the frozen mimic-postgres-cli Skill contract and published benchmark protocol supplied below.
They are exact snapshots of the installed Skill artifacts. Do not run tools to reread them.

<skill sha256="{skill_sha256}">
{skill_text}
</skill>

<benchmark_protocol sha256="{protocol_sha256}">
{protocol_text}
</benchmark_protocol>

This is a CLINSQL validation SQL-generation task for MIMIC-IV v3.1 in {target} pairing mode.
Never open or search CLINSQL benchmark files, gold SQL, result CSVs, rubrics, prior generated SQL, or benchmark outputs.
Use only this public question:

{question}

The parent harness already verified both MedDB targets are read-only and MCP-disabled. Do not run status, catalog, schema, or query commands.
Use this frozen CLINSQL official MIMIC-IV schema context (SHA-256 {schema_sha256}):

<schema_context>
{schema_context}
</schema_context>

CLINSQL is MIMIC-IV v3.1: use only mimiciv_hosp, mimiciv_icu, and an explicitly available mimiciv_derived object. Never use mimiciii.
Do not create files and do not execute the clinical query; the parent harness will safely EXPLAIN and execute the returned SQL.
Return only the requested JSON object. Put the named-parameter object as valid JSON text in params_json.
Before SQL, make an internal plan for version, grain, cohort/denominator, temporal rule, sources/join keys, aggregate, and named parameters.
Use one schema-qualified PostgreSQL SELECT or WITH...SELECT, named parameters, explicit physical table columns, and no record-level output. Use only :parameter_name parameters: never $1, ?, %s, or string interpolation."""


@bounded_model_call
def generate_sql(
    *,
    codex: Path,
    codex_home: Path,
    meddb_root: Path,
    output_schema: Path,
    run_root: Path,
    key: str,
    question: str,
    target: str,
    schema_context: str,
    schema_sha256: str,
    skill_text: str,
    skill_sha256: str,
    protocol_text: str,
    protocol_sha256: str,
    model: str,
    reasoning_effort: str,
    timeout_seconds: int,
    references: dict[str, str] | None = None,
    repair_feedback: str | None = None,
) -> dict[str, Any]:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", key)
    target_root = run_root / target
    target_root.mkdir(parents=True, exist_ok=True)
    response_path = target_root / f"{slug}.generation.json"
    transcript_path = target_root / f"{slug}.codex.jsonl"
    prompt = generation_prompt(
        question, target, schema_context, schema_sha256,
        skill_text, skill_sha256, protocol_text, protocol_sha256,
    )
    for name, content in (references or {}).items():
        digest = sha256_bytes(content.encode("utf-8"))
        prompt += f'\n<frozen_reference name="{name}" sha256="{digest}">\n{content}\n</frozen_reference>\n'
    prompt += (
        "\nReturn a compact observable specification in the plan object, not a reasoning transcript. "
        "For each requested clinical/statistical clause, ensure SQL implements the plan. "
        "Do not use a blanket zero-event policy; derive the denominator from the question. "
        "Distinguish anchor age from admission age; apply an explicitly declared benchmark "
        "profile before generic defaults, and explicit question requirements before either. "
        "Do not join two raw cohort populations "
        "with CROSS JOIN. Explicitly cast parameters passed to CONCAT/NTILE/time intervals.\n"
    )
    if repair_feedback:
        prompt += (
            "\nThis is one execution-guided repair. Correct only the supplied generated SQL using "
            "these static/EXPLAIN diagnostics. Do not access Gold, results or rubrics.\n"
            f"<repair_feedback>\n{repair_feedback}\n</repair_feedback>\n"
        )
    prompt += "\nIf declared_development_examples.json is supplied, those explicitly disjoint examples are permitted training context. They are not the Gold for this task. Adapt the reasoning pattern to the current question; never copy their age/sex/cohort constants blindly.\n"
    env = os.environ.copy()
    env["CODEX_HOME"] = str(codex_home)
    env["MEDDB_CODEX_PROFILE"] = "no-mcp"
    env["MEDDB_CODEX_SAFE"] = "1"
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [
                str(codex),
                "exec",
                "-c", "model_reasoning_effort=\"" + reasoning_effort + "\"",
                "-m", model,
                "-C", str(meddb_root),
                "--skip-git-repo-check",
                "--ephemeral",
                "--sandbox", "read-only",
                "--output-schema", str(output_schema),
                "--json",
                "-o", str(response_path),
                "-",
            ],
            input=prompt,
            creationflags=child_flags(),
            cwd=meddb_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        def decode(value: str | bytes | None) -> str:
            return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")
        transcript = decode(exc.stdout) + "\n" + decode(exc.stderr)
        transcript_path.write_text(transcript, encoding="utf-8")
        return {
            "status": "generation_timeout",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "transcript_path": str(transcript_path),
            "transcript_sha256": sha256_file(transcript_path),
        }
    transcript = completed.stdout + "\n" + completed.stderr
    transcript_path.write_text(transcript, encoding="utf-8")
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
        return {**base, "status": "generation_error", "diagnostic": transcript[-2000:]}
    try:
        response = load_json(response_path)
        params = json.loads(response.get("params_json", "{}"))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return {**base, "status": "invalid_generation_json", "diagnostic": str(exc)}
    if not isinstance(params, dict):
        return {**base, "status": "invalid_params", "diagnostic": "params_json is not an object"}
    model_status = response.get("status")
    if model_status != "sql":
        return {
            **base,
            "status": f"model_{model_status}",
            "response_path": str(response_path),
            "response_sha256": sha256_file(response_path),
            "rationale": response.get("rationale"),
        }
    sql = str(response.get("sql", ""))
    tables = [str(value).lower() for value in response.get("tables", [])]
    if re.search(r"\bmimiciii\s*\.", sql, flags=re.IGNORECASE) or any(
        value.startswith("mimiciii.") for value in tables
    ):
        return {
            **base,
            "status": "schema_target_error",
            "response_path": str(response_path),
            "response_sha256": sha256_file(response_path),
            "tables": tables,
        }
    sql_path = target_root / f"{slug}.sql"
    params_path = target_root / f"{slug}.params.json"
    sql_path.write_text(sql.rstrip() + "\n", encoding="utf-8")
    params_path.write_text(json.dumps(params, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        **base,
        "status": "generated",
        "response_path": str(response_path),
        "response_sha256": sha256_file(response_path),
        "sql_path": str(sql_path),
        "sql_sha256": sha256_file(sql_path),
        "params_path": str(params_path),
        "params_sha256": sha256_file(params_path),
        "tables": tables,
        "plan": response.get("plan"),
    }


def execute_generated(
    *,
    meddb_root: Path,
    config_path: Path,
    config: dict[str, Any],
    generated: dict[str, Any],
    key: str,
    timeout_seconds: int,
    static_validator: Path,
    schema_context: Path,
) -> dict[str, Any]:
    if generated.get("status") != "generated":
        return {"status": "not_executed", "reason": generated.get("status")}
    validation = static_validate(static_validator, generated["sql_path"], generated["params_path"], schema_context)
    if not validation.get("valid"):
        return {"status": "static_validation_error", "static_validation": validation}
    cli = meddb_root / "bin" / "meddb.cmd"
    # A run-specific prefix prevents another run from overwriting scored exports.
    run_tag = sha256_bytes(str(Path(generated["sql_path"]).parent.parent).encode("utf-8"))[:12]
    slug = run_tag + "_" + re.sub(r"[^A-Za-z0-9_.-]+", "_", key) + ".json"
    env = os.environ.copy()
    env["MEDDB_CODEX_PROFILE"] = "no-mcp"
    env["MEDDB_CODEX_SAFE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env = bounded_db_env(env)
    common = [
        str(cli), "--config", str(config_path), "query",
        "--sql-file", generated["sql_path"],
        "--params-file", generated["params_path"],
        "--yes",
    ]
    started = time.perf_counter()
    try:
        explain = subprocess.run(
            common + ["--explain-only"], cwd=meddb_root, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_seconds, check=False,
            creationflags=child_flags(),
        )
    except subprocess.TimeoutExpired:
        return {"status": "explain_process_timeout", "elapsed_ms": round((time.perf_counter() - started) * 1000)}
    if explain.returncode != 0:
        return {
            "status": "explain_error",
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "diagnostic": (explain.stdout + "\n" + explain.stderr)[-2000:],
        }
    try:
        completed = subprocess.run(
            common + ["--format", "json", "--output", slug],
            cwd=meddb_root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=timeout_seconds, check=False,
            creationflags=child_flags(),
        )
    except subprocess.TimeoutExpired:
        return {"status": "query_process_timeout", "elapsed_ms": round((time.perf_counter() - started) * 1000)}
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    if completed.returncode != 0:
        return {
            "status": "query_error",
            "elapsed_ms": elapsed_ms,
            "diagnostic": (completed.stdout + "\n" + completed.stderr)[-2000:],
        }
    export_root = Path(config["paths"]["exports"])
    if not export_root.is_absolute():
        export_root = (meddb_root / export_root).resolve()
    output_path = (export_root / slug).resolve()
    if not output_path.exists():
        return {"status": "missing_output", "elapsed_ms": elapsed_ms}
    payload = load_json(output_path)
    return {
        "status": "executed",
        "elapsed_ms": elapsed_ms,
        "db_elapsed_ms": payload.get("elapsed_ms"),
        "returned_rows": payload.get("returned_rows"),
        "truncated": payload.get("truncated"),
        "columns": payload.get("columns", []),
        "rows": payload.get("rows", []),
        "output_path": str(output_path),
        "output_sha256": sha256_file(output_path),
    }


def locate_reference(query_path: Path) -> Path:
    matches = list(query_path.parent.glob("result_*.csv"))
    if len(matches) != 1:
        raise ValueError(f"Expected one reference CSV beside {query_path}")
    return matches[0]


def preflight_status(meddb_root: Path, config_path: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["MEDDB_CODEX_PROFILE"] = "no-mcp"
    env["MEDDB_CODEX_SAFE"] = "1"
    completed = subprocess.run(
        [str(meddb_root / "bin" / "meddb.cmd"), "--config", str(config_path), "status"],
        cwd=meddb_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    combined = completed.stdout + "\n" + completed.stderr
    if completed.returncode != 0:
        raise RuntimeError(f"Status preflight failed for {config_path}: {combined[-2000:]}")
    normalized = combined.lower()
    if (
        "meddb_mimic_reader" not in normalized
        or not re.search(r"db_default_read_only\s*\|\s*on", normalized)
        or not re.search(r"mcp_disabled\s*\|\s*true", normalized)
    ):
        raise RuntimeError(f"Status preflight did not prove safe state for {config_path}")
    return {
        "config_path": str(config_path),
        "status_output_sha256": sha256_bytes(combined.encode("utf-8")),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validation-root", type=Path, required=True)
    parser.add_argument("--meddb-root", type=Path, required=True)
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--output-schema", type=Path, required=True)
    parser.add_argument("--schema-context", type=Path, required=True)
    parser.add_argument("--skill-entry", type=Path, required=True)
    parser.add_argument("--skill-protocol", type=Path, required=True)
    parser.add_argument("--static-validator", type=Path, required=True)
    parser.add_argument("--gold-helper", type=Path, required=True)
    parser.add_argument("--official-config", type=Path, required=True)
    parser.add_argument("--local-config", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--selection", choices=("one", "pilot", "full"), default="one")
    parser.add_argument("--pairing-mode", choices=("shared", "independent"), default="shared")
    parser.add_argument("--start-index", type=int, default=0, help="Zero-based index after deterministic selection")
    parser.add_argument("--max-tasks", type=int, help="Optional batch size after --start-index")
    parser.add_argument("--task-key", default="validation/Diagnostic_Procedures/easy/001")
    parser.add_argument("--task-keys", nargs="+", help="Explicit frozen validation IDs; same-task development regression")
    parser.add_argument("--skill-reference", action="append", type=Path, default=[])
    parser.add_argument("--repair-attempts", type=int, choices=(0, 1), default=0)
    parser.add_argument("--dictionary-grounding", action="store_true", help="Retrieve question-only dictionary candidates via read-only MedDB CLI")
    parser.add_argument("--development-examples", type=Path, help="Frozen disjoint validation-development question/SQL examples; no results/rubrics")
    parser.add_argument("--development-profile", type=Path, help="Explicitly scoped development conventions, never implicit ordinary-query defaults")
    parser.add_argument("--development-manifest", type=Path, help="Allowlisted already-exposed IDs for development-profile regression")
    parser.add_argument("--evaluation-manifest", type=Path, help="Separate frozen validation evaluation; does not relabel unseen questions as exposed development")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=1, help="Up to eight model workers; all dictionary/SQL work remains serial")
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--generation-timeout-seconds", type=int, default=360)
    parser.add_argument("--query-timeout-seconds", type=int, default=45)
    args = parser.parse_args()
    configure_model_slots(args.workers)
    low_priority = lower_parent_priority()
    if args.workers > 1 and args.pairing_mode != "shared":
        raise ValueError("Parallel prefetch supports shared-generation pairing only")

    validation_root = args.validation_root.resolve()
    if validation_root.name.lower() != "validation":
        raise ValueError("This development runner accepts only the validation split")
    if args.repair_attempts and args.pairing_mode != "shared":
        raise ValueError("Repair requires shared generation for an identical paired SQL")
    meddb_root = args.meddb_root.resolve()
    run_root = args.run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    if args.task_keys:
        if len(args.task_keys) != len(set(args.task_keys)):
            raise ValueError("Duplicate --task-keys")
        selected = [select_one(validation_root, key)[0] for key in args.task_keys]
    elif args.selection == "one":
        selected = select_one(validation_root, args.task_key)
    elif args.selection == "pilot":
        selected = select_pilot(validation_root)
    else:
        selected = select_full(validation_root)
    if args.start_index < 0:
        raise ValueError("--start-index must be non-negative")
    selected = selected[args.start_index:]
    if args.max_tasks is not None:
        if args.max_tasks <= 0:
            raise ValueError("--max-tasks must be positive")
        selected = selected[:args.max_tasks]
    if not selected:
        raise ValueError("Selection batch is empty")
    manifest = [
        {
            "task_key": task_key(path, validation_root),
            "question_sha256": sha256_file(path),
        }
        for path in selected
    ]
    manifest_hash = sha256_bytes(json.dumps(manifest, sort_keys=True).encode("utf-8"))
    if args.development_manifest and args.evaluation_manifest:
        raise ValueError("Choose development regression OR frozen evaluation, not both")
    if bool(args.development_profile) != bool(args.development_manifest or args.evaluation_manifest):
        raise ValueError("Profile requires a development or frozen evaluation manifest")
    development_manifest = None
    evaluation_manifest = None
    if args.evaluation_manifest:
        evaluation_manifest = load_json(args.evaluation_manifest)
        validate_evaluation_manifest(evaluation_manifest, manifest, sha256_file(args.development_profile))
        if args.development_examples:
            raise ValueError("This frozen evaluation is zero-example; no development SQL examples permitted")
    elif args.development_profile:
        development_manifest = load_development_manifest(
            args.development_manifest, {item["task_key"] for item in manifest},
        )
    gold = load_runner(args.gold_helper.resolve())
    schema_context_path = args.schema_context.resolve()
    schema_context = schema_context_path.read_text(encoding="utf-8").strip()
    schema_context_sha256 = sha256_file(schema_context_path)
    skill_entry_path = args.skill_entry.resolve()
    skill_entry_text = skill_entry_path.read_text(encoding="utf-8").strip()
    skill_entry_sha256 = sha256_file(skill_entry_path)
    skill_protocol_path = args.skill_protocol.resolve()
    skill_protocol_text = skill_protocol_path.read_text(encoding="utf-8").strip()
    skill_protocol_sha256 = sha256_file(skill_protocol_path)
    static_validator_path = args.static_validator.resolve()
    static_validator_sha256 = sha256_file(static_validator_path)
    reference_paths = args.skill_reference or [
        skill_entry_path.parent / "references" / "sql-generation.md",
        skill_entry_path.parent / "references" / "clinical-semantics.md",
    ]
    reference_paths = [path.resolve() for path in reference_paths]
    if args.development_profile:
        reference_paths.append(args.development_profile.resolve())
    references = {path.name: path.read_text(encoding="utf-8").strip() for path in reference_paths}
    if len(references) != len(reference_paths):
        raise ValueError("Reference basenames must be unique")
    examples_manifest = None
    if args.development_examples:
        examples_manifest = load_development_examples(
            args.development_examples.resolve(), {item["task_key"] for item in manifest},
        )
        references["declared_development_examples.json"] = json.dumps(examples_manifest, ensure_ascii=False)
    # Freeze exact inference files before generation. Gold is not included.
    frozen_root = run_root / "frozen"
    frozen_root.mkdir(exist_ok=True)
    for path in [skill_entry_path, skill_protocol_path, schema_context_path, args.output_schema.resolve(), *reference_paths]:
        (frozen_root / path.name).write_bytes(path.read_bytes())
    if args.development_examples:
        (frozen_root / "declared_development_examples.json").write_bytes(args.development_examples.read_bytes())
    if args.development_manifest:
        (frozen_root / "development_manifest.json").write_bytes(args.development_manifest.read_bytes())
    if args.evaluation_manifest:
        (frozen_root / "evaluation_manifest.json").write_bytes(args.evaluation_manifest.read_bytes())
    registry_path = skill_entry_path.parent / "references" / "medication-class-registry.json"
    if args.dictionary_grounding and registry_path.exists():
        (frozen_root / registry_path.name).write_bytes(registry_path.read_bytes())
    for source_path in (
        Path(__file__), args.gold_helper.resolve(), static_validator_path,
        Path(__file__).with_name("low_impact_benchmark.py"),
        Path(__file__).with_name("mimic_dictionary_grounding.py"),
    ):
        (frozen_root / source_path.name).write_bytes(source_path.read_bytes())
    (run_root / "selection_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    configs = {
        "official": (args.official_config.resolve(), load_json(args.official_config.resolve())),
        "local": (args.local_config.resolve(), load_json(args.local_config.resolve())),
    }
    preflight = {
        target: preflight_status(meddb_root, config_path)
        for target, (config_path, _) in configs.items()
    }

    prepared = {}

    def prepare(path):
        key = task_key(path, validation_root)
        if key in prepared:
            return prepared[key]
        question = path.read_text(encoding="utf-8").strip()
        task_references = dict(references)
        metadata = None
        if args.dictionary_grounding:
            from mimic_dictionary_grounding import retrieve
            context = retrieve(question, meddb_root, configs["official"][0], run_root / "dictionary_metadata", key, registry_path=registry_path)
            text = json.dumps(context, ensure_ascii=False)
            task_references["dictionary_candidates.json"] = text
            metadata = {
                "sha256": sha256_bytes(text.encode("utf-8")),
                "sources": [{k: item.get(k) for k in ("concept", "table", "status", "candidate_count", "total_candidates", "truncated")} for item in context["sources"]],
            }
        prepared[key] = (question, task_references, metadata)
        return prepared[key]

    def generate_shared(path):
        key = task_key(path, validation_root)
        question, task_references, _ = prepared[key]
        return generate_sql(
            codex=args.codex.resolve(), codex_home=args.codex_home.resolve(),
            meddb_root=meddb_root, output_schema=args.output_schema.resolve(),
            run_root=run_root, key=key, question=question, target="shared",
            schema_context=schema_context, schema_sha256=schema_context_sha256,
            skill_text=skill_entry_text, skill_sha256=skill_entry_sha256,
            protocol_text=skill_protocol_text, protocol_sha256=skill_protocol_sha256,
            model=args.model, reasoning_effort=args.reasoning_effort,
            timeout_seconds=args.generation_timeout_seconds, references=task_references,
        )

    records: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    wall_started = time.perf_counter()
    generation_stream = (
        shared_generation_stream(selected, prepare, generate_shared, args.workers)
        if args.pairing_mode == "shared" else ((path, None) for path in selected)
    )
    for index, (query_path, prefetched) in enumerate(generation_stream, start=1):
        counts_before_task = counts.copy()
        key = task_key(query_path, validation_root)
        question, task_references, metadata = prepare(query_path)
        task_record: dict[str, Any] = {"task_key": key, "question_sha256": sha256_file(query_path)}
        if metadata is not None:
            task_record["dictionary_grounding"] = metadata
        actuals: dict[str, dict[str, Any]] = {}
        shared_generated = None
        if args.pairing_mode == "shared":
            shared_generated = prefetched
            counts[f"shared:generation:{shared_generated['status']}"] += 1
        for target in ("official", "local"):
            config_path, config = configs[target]
            if shared_generated is not None:
                generated = {**shared_generated, "shared_generation": True}
            else:
                generated = generate_sql(
                    codex=args.codex.resolve(), codex_home=args.codex_home.resolve(),
                    meddb_root=meddb_root, output_schema=args.output_schema.resolve(),
                    run_root=run_root, key=key, question=question, target=target,
                    schema_context=schema_context, schema_sha256=schema_context_sha256,
                    skill_text=skill_entry_text, skill_sha256=skill_entry_sha256,
                    protocol_text=skill_protocol_text, protocol_sha256=skill_protocol_sha256,
                    model=args.model, reasoning_effort=args.reasoning_effort,
                    timeout_seconds=args.generation_timeout_seconds,
                    references=task_references,
                )
                counts[f"{target}:generation:{generated['status']}"] += 1
            actual = execute_generated(
                meddb_root=meddb_root, config_path=config_path, config=config,
                generated=generated, key=key, timeout_seconds=args.query_timeout_seconds,
                static_validator=static_validator_path, schema_context=schema_context_path,
            )
            actuals[target] = actual
            task_record[target] = {"generation": generated, "execution": actual}
            counts[f"{target}:execution:{actual['status']}"] += 1

        repaired_actuals: dict[str, dict[str, Any]] = {}
        repaired_generated = None
        repairable = {"static_validation_error", "explain_error"}
        if args.repair_attempts and shared_generated and shared_generated.get("status") == "generated" and any(
            value.get("status") in repairable for value in actuals.values()
        ):
            # Read only the model's own attempt and database planning diagnostics.
            feedback = json.dumps({
                "sql": Path(shared_generated["sql_path"]).read_text(encoding="utf-8"),
                "params_json": Path(shared_generated["params_path"]).read_text(encoding="utf-8"),
                "diagnostics": {
                    target: {k: value[k] for k in ("status", "diagnostic", "static_validation") if k in value}
                    for target, value in actuals.items() if value.get("status") in repairable
                },
            }, ensure_ascii=False)
            repaired_generated = generate_sql(
                codex=args.codex.resolve(), codex_home=args.codex_home.resolve(), meddb_root=meddb_root,
                output_schema=args.output_schema.resolve(), run_root=run_root / "repair", key=key,
                question=question, target="shared", schema_context=schema_context,
                schema_sha256=schema_context_sha256, skill_text=skill_entry_text,
                skill_sha256=skill_entry_sha256, protocol_text=skill_protocol_text,
                protocol_sha256=skill_protocol_sha256, model=args.model,
                reasoning_effort=args.reasoning_effort, timeout_seconds=args.generation_timeout_seconds,
                references=task_references, repair_feedback=feedback,
            )
            counts[f"repair:generation:{repaired_generated['status']}"] += 1
            for target, (config_path, config) in configs.items():
                repaired_actuals[target] = execute_generated(
                    meddb_root=meddb_root, config_path=config_path, config=config, generated=repaired_generated,
                    key=key, timeout_seconds=args.query_timeout_seconds,
                    static_validator=static_validator_path, schema_context=schema_context_path,
                )
                counts[f"repair:{target}:execution:{repaired_actuals[target]['status']}"] += 1

        # Gold artifacts are first opened here, after both generations/executions.
        reference_path = locate_reference(query_path)
        reference_columns, reference_rows = gold.load_reference(reference_path)
        comparisons = {}
        for target in ("official", "local"):
            comparisons[f"{target}_vs_reference"] = gold.compare(
                reference_columns, reference_rows, actuals[target]
            )
        comparisons["official_vs_local"] = gold.compare_targets(actuals["official"], actuals["local"])
        task_record["reference_sha256"] = sha256_file(reference_path)
        task_record["reference_rows"] = len(reference_rows)
        task_record["comparisons"] = comparisons
        if repaired_generated is not None:
            repair_comparisons = {
                f"{target}_vs_reference": gold.compare(reference_columns, reference_rows, repaired_actuals[target])
                for target in ("official", "local")
            }
            for target in ("official", "local"):
                if repair_comparisons[f"{target}_vs_reference"].get("unordered_numeric_tolerant_value_only_match"):
                    counts[f"repair:{target}:reference_value_numeric_match"] += 1
                repaired_actuals[target].pop("rows", None)
            task_record["repair"] = {
                "generation": repaired_generated, "executions": repaired_actuals, "comparisons": repair_comparisons,
            }
        for target in ("official", "local"):
            if comparisons[f"{target}_vs_reference"].get("unordered_value_match"):
                counts[f"{target}:reference_strict_match"] += 1
            if comparisons[f"{target}_vs_reference"].get("unordered_numeric_tolerant_match"):
                counts[f"{target}:reference_numeric_tolerant_match"] += 1
            if comparisons[f"{target}_vs_reference"].get("unordered_numeric_tolerant_value_only_match"):
                counts[f"{target}:reference_value_numeric_match"] += 1
        if comparisons["official_vs_local"].get("unordered_value_match"):
            counts["paired:strict_match"] += 1
        if comparisons["official_vs_local"].get("unordered_numeric_tolerant_match"):
            counts["paired:numeric_tolerant_match"] += 1
        if comparisons["official_vs_local"].get("unordered_value_only_match"):
            counts["paired:value_only_match"] += 1
        if comparisons["official_vs_local"].get("unordered_numeric_tolerant_value_only_match"):
            counts["paired:numeric_tolerant_value_only_match"] += 1

        for target in ("official", "local"):
            task_record[target]["execution"].pop("rows", None)
        records.append(task_record)
        # Only sanitized aggregate score flags, never returned clinical rows.
        checkpoint_path = run_root / "checkpoints" / (re.sub(r"[^A-Za-z0-9_.-]+", "_", key) + ".json")
        if checkpoint_path.exists():
            raise RuntimeError("Refusing to overwrite a completed task checkpoint")
        atomic_json(checkpoint_path, {
            "record": task_record,
            "counts": dict(counts - counts_before_task),
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        print(
            f"progress={index}/{len(selected)} task={key} "
            f"O_gen={task_record['official']['generation']['status']} O_exec={actuals['official']['status']} "
            f"L_gen={task_record['local']['generation']['status']} L_exec={actuals['local']['status']} "
            f"O_ref={comparisons['official_vs_reference'].get('unordered_value_match', False)} "
            f"L_ref={comparisons['local_vs_reference'].get('unordered_value_match', False)} "
            f"O_L={comparisons['official_vs_local'].get('unordered_value_match', False)} "
            f"O_L_value={comparisons['official_vs_local'].get('unordered_value_only_match', False)}",
            flush=True,
        )

    payload = {
        "report_version": 4,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": f"CLINSQL validation model generation ({'explicit-ids' if args.task_keys else args.selection}, {args.pairing_mode})",
        "guardrails": {
            "test_split_opened": False,
            "gold_visible_to_model": False,
            "evaluated_task_gold_visible_to_model": False,
            "development_profile": {"path": str(args.development_profile.resolve()), "sha256": sha256_file(args.development_profile), "evaluation_status": "frozen-validation-evaluation" if evaluation_manifest else "exposed-development-regression-not-heldout", "manifest_sha256": sha256_file(args.evaluation_manifest or args.development_manifest)} if args.development_profile else None,
            "declared_development_sql_visible_to_model": bool(examples_manifest),
            "mcp_enabled": False,
            "codex_home": str(args.codex_home.resolve()),
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "pairing_mode": args.pairing_mode,
            "schema_context_path": str(schema_context_path),
            "schema_context_sha256": schema_context_sha256,
            "skill_entry_path": str(skill_entry_path),
            "skill_entry_sha256": skill_entry_sha256,
            "skill_protocol_path": str(skill_protocol_path),
            "skill_protocol_sha256": skill_protocol_sha256,
            "static_validator_path": str(static_validator_path),
            "static_validator_sha256": static_validator_sha256,
            "references": [{"path": str(path), "sha256": sha256_file(path)} for path in reference_paths],
            "repair_attempts": args.repair_attempts,
            "dictionary_grounding": args.dictionary_grounding,
            "workers": args.workers,
            "database_query_concurrency": 1,
            "reader_session_parallel_workers": 0,
            "reader_session_jit": False,
            "below_normal_process_priority": low_priority,
            "resource_policy": {"cpu_launch_threshold_pct": 55, "min_free_gb": 2, "min_free_fraction": 0.20, "max_wait_seconds": 120},
            "development_examples": {"path": str(args.development_examples.resolve()), "sha256": sha256_file(args.development_examples), "ids": [item["task_key"] for item in examples_manifest["examples"]]} if examples_manifest else None,
            "dictionary_retriever_sha256": sha256_file(Path(__file__).with_name("mimic_dictionary_grounding.py")) if args.dictionary_grounding else None,
            "medication_registry_sha256": sha256_file(registry_path) if args.dictionary_grounding and registry_path.exists() else None,
            "runner_sha256": sha256_file(Path(__file__)),
            "low_impact_scheduler_sha256": sha256_file(Path(__file__).with_name("low_impact_benchmark.py")),
            "scorer_sha256": sha256_file(args.gold_helper.resolve()),
            "config_sha256": {target: sha256_file(path) for target, (path, _) in configs.items()},
            "metric_policy": "Report alias-sensitive and value-only numeric accuracy separately; neither is official rubric score",
            "database_role": "meddb_mimic_reader",
            "preflight": preflight,
        },
        "selection_manifest": manifest,
        "selection_manifest_sha256": manifest_hash,
        "selection_batch": {"start_index": args.start_index, "max_tasks": args.max_tasks},
        "summary": {
            "tasks": len(records),
            "accuracy_evaluation_status": "incomplete_resource_deferred" if any(
                task[target]["generation"].get("status") == "resource_deferred"
                for task in records for target in ("official", "local")
            ) else ("completed_frozen_validation" if evaluation_manifest else "completed_development"),
            "resource_deferred_tasks": sum(any(
                task[target]["generation"].get("status") == "resource_deferred"
                for target in ("official", "local")
            ) for task in records),
            "generation_units": len(records) * (1 if args.pairing_mode == "shared" else 2),
            "counts": dict(sorted(counts.items())),
            "wall_time_seconds": round(time.perf_counter() - wall_started, 3),
        },
        "tasks": records,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
