from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from meddb.config import PROJECT_ROOT, load_settings
from medresearch.benchmark import discover_validation, file_hash, run_benchmark
from medresearch.contracts import digest, validate_proposal
from medresearch.provider import ResponsesGenerator
from medresearch.runtime import ResearchRunner, atomic_json, generate_proposal
from medresearch.tools import MedDBTool


DEFAULT_OFFICIAL = PROJECT_ROOT / "evals/official"
RULES = PROJECT_ROOT / "context/knowledge/mimiciv.md"


def read_context(official: Path) -> tuple[str, str]:
    schema_path = official / "clinsql/model_inference/mimic4_schema.txt"
    manifest = json.loads((official / "MANIFEST.json").read_text(encoding="utf-8"))
    expected = {x["path"]: x["sha256"] for x in manifest["files"]}
    relative = schema_path.relative_to(official).as_posix()
    if expected.get(relative) != file_hash(schema_path):
        raise ValueError("Official schema integrity mismatch")
    return schema_path.read_text(encoding="utf-8"), RULES.read_text(encoding="utf-8")


def provenance(settings, *, model: str | None = None) -> dict:
    # Hash configuration/catalog; never copy authentication material or paths
    # from the user's environment into model input.
    catalog = settings.paths["catalog"]
    return {"configuration_sha256": digest({"database": settings.database,
             "security": settings.security, "catalog_path": str(catalog)}),
            "catalog_sha256": file_hash(catalog) if catalog.is_file() else None,
            "model": model, "provider": "openai-responses" if model else "supplied-proposal",
            "dataset_version": "3.1", "dataset_version_verified": False}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Medical research workflows, local statistics and CLINSQL validation")
    commands = result.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="Generate a ResearchSpec and SQL, without database access")
    plan.add_argument("--question-file", type=Path, required=True)
    plan.add_argument("--task-id", required=True)
    plan.add_argument("--model", required=True)
    plan.add_argument("--official", type=Path, default=DEFAULT_OFFICIAL)
    plan.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run", help="Execute a supplied proposal using the MedDB core")
    run.add_argument("--proposal", type=Path, required=True)
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--explain-only", action="store_true")
    benchmark = commands.add_parser("benchmark", help="Inspect or run official CLINSQL validation")
    benchmark.add_argument("--official", type=Path, default=DEFAULT_OFFICIAL)
    benchmark.add_argument("--config", type=Path, default=PROJECT_ROOT / "config/meddb-mimic-official.json")
    benchmark.add_argument("--limit", type=int, default=6)
    benchmark.add_argument("--model")
    benchmark.add_argument("--output", type=Path)
    benchmark.add_argument("--preflight", action="store_true", help="Offline inspection only (the default)")
    benchmark.add_argument("--probe-database", action="store_true", help="Also check live role/schema metadata")
    benchmark.add_argument("--execute", action="store_true", help="Call model and execute benchmark SQL")
    benchmark.add_argument("--resume", action="store_true")
    study = commands.add_parser("study", help="Run a version-2 multi-dataset study locally")
    study.add_argument("--spec", type=Path, required=True)
    study.add_argument("--output", type=Path, required=True)
    study.add_argument("--resume", action="store_true")
    design = commands.add_parser("study-plan", help="Plan a study from public metadata, without loading observations")
    design.add_argument("--brief", type=Path, required=True)
    design.add_argument("--model", required=True)
    design.add_argument("--output", type=Path, required=True)
    session = commands.add_parser("revise", help="Append an immutable protocol revision to a study session")
    session.add_argument("--spec", type=Path, required=True)
    session.add_argument("--session", type=Path, required=True)
    session.add_argument("--note", required=True)
    literature = commands.add_parser("literature", help="Explicitly search public PubMed bibliographic metadata")
    literature.add_argument("--query", required=True)
    literature.add_argument("--limit", type=int, default=5)
    literature.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify-evidence", help="Explicitly verify PubMed IDs/titles against public NCBI metadata")
    verify.add_argument("--spec", type=Path, required=True)
    verify.add_argument("--output", type=Path, required=True)
    compare = commands.add_parser("compare", help="Compare constructs and evidence across completed local studies")
    compare.add_argument("--runs", type=Path, nargs="+", required=True)
    compare.add_argument("--output", type=Path, required=True)
    commands.add_parser("capabilities", help="List supported datasets and statistical methods")
    demo = commands.add_parser("demo", help="Run all statistical workflows with synthetic data only")
    demo.add_argument("--output", type=Path, required=True)
    agent = commands.add_parser("agent", help="Plan from public metadata and run the complete local research pipeline")
    agent.add_argument("--brief", type=Path, required=True)
    agent.add_argument("--model", required=True)
    agent.add_argument("--output", type=Path, required=True)
    agent.add_argument("--max-attempts", type=int, default=2)
    agent.add_argument("--resume", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command in {"study", "study-plan", "revise", "literature", "verify-evidence", "compare", "capabilities", "demo", "agent"}:
            return extended_command(args)
        if args.command == "plan":
            generation_path = args.output.with_suffix(args.output.suffix + ".provenance.json")
            if args.output.exists() or generation_path.exists():
                raise ValueError("Refusing to overwrite an existing proposal")
            schema, rules = read_context(args.official)
            proposal = generate_proposal(ResponsesGenerator(args.model),
                question=args.question_file.read_text(encoding="utf-8").strip(),
                task_id=args.task_id, dataset_version="3.1", schema=schema, rules=rules)
            atomic_json(generation_path, {"model": args.model, "provider": "openai-responses",
                "schema_sha256": digest(schema), "rules_sha256": digest(rules),
                "proposal_sha256": digest(proposal)})
            atomic_json(args.output, proposal)
            print(json.dumps({"status": proposal["status"], "output": str(args.output)}))
            return 0
        settings = load_settings(args.config)
        tool = MedDBTool(settings)
        if args.command == "run":
            proposal = validate_proposal(json.loads(args.proposal.read_text(encoding="utf-8")))
            run_provenance = provenance(settings)
            generation_path = args.proposal.with_suffix(args.proposal.suffix + ".provenance.json")
            if generation_path.exists():
                generation = json.loads(generation_path.read_text(encoding="utf-8"))
                if generation["proposal_sha256"] != digest(proposal):
                    raise ValueError("Proposal differs from its generation provenance")
                run_provenance["generation"] = generation
            state = ResearchRunner(tool, args.output, run_provenance).run(
                proposal, resume=args.resume, explain_only=args.explain_only)
            print(json.dumps({"status": state["status"], "output": str(args.output),
                              "error_type": state.get("error_type")}, ensure_ascii=False))
            return 1 if state["status"] == "failed" else 0
        if args.execute and (args.preflight or args.probe_database):
            raise ValueError("Use inspection and --execute in separate invocations")
        tasks = discover_validation(args.official, args.limit)
        schema, rules = read_context(args.official)
        if not args.execute:
            readiness = {"mode": "offline-preflight", "split": "validation",
                "selected": len(tasks), "task_ids": [x["task_id"] for x in tasks],
                "official_schema_verified": True,
                "catalog_present": settings.paths["catalog"].is_file(),
                "api_key_present": bool(os.environ.get("OPENAI_API_KEY")),
                "database_probed": False, "benchmark_executed": False,
                "dataset_version_verified": False, "schema_sha256": digest(schema),
                "rules_sha256": digest(rules)}
            if args.probe_database:
                readiness["database"] = tool.preflight()
                readiness["database_probed"] = True
            if args.output:
                if args.output.exists():
                    raise ValueError("Preflight output already exists")
                atomic_json(args.output / "preflight.json", readiness)
            print(json.dumps(readiness, ensure_ascii=False, indent=2))
            return 0
        if not args.model or not args.output:
            raise ValueError("--execute requires --model and --output")
        # Check connectivity/role/catalog before consuming any model quota.
        tool.preflight()
        summary = run_benchmark(official=args.official, output=args.output, limit=args.limit,
            generator=ResponsesGenerator(args.model), tool=tool, schema=schema, rules=rules,
            provenance=provenance(settings, model=args.model), resume=args.resume)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["executed"] == summary["selected"] else 1
    except Exception as exc:
        # No raw database/HTTP errors or clinical values on agent-visible stdout.
        message = str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else "See configuration and prerequisites"
        print(json.dumps({"status": "error", "error_type": type(exc).__name__,
                          "message": message}, ensure_ascii=False))
        return 1


def extended_command(args) -> int:
    # Optional scientific dependencies are loaded only for these commands.
    from medresearch.study_contract import DATASETS, METHODS
    if args.command == "agent":
        from medresearch.agent import run_agent
        state = run_agent(json.loads(args.brief.read_text(encoding="utf-8")), ResponsesGenerator(args.model),
            args.output, base=args.brief.resolve().parent, max_attempts=args.max_attempts, resume=args.resume)
        print(json.dumps({"status": state["status"], "output": str(args.output),
                          "attempts": len(state["attempts"])}))
        return 0 if state["status"] in {"completed", "needs_review"} else 1
    if args.command == "capabilities":
        print(json.dumps({"protocol_versions": [1, 2], "datasets": DATASETS, "methods": METHODS}, indent=2))
        return 0
    if args.command == "study":
        from medresearch.workflow import StudyRunner
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        generation_file = args.spec.with_suffix(".provenance.json")
        generation = json.loads(generation_file.read_text(encoding="utf-8")) if generation_file.exists() else None
        if generation and generation["study_sha256"] != digest(spec):
            raise ValueError("Study changed after model generation; revise the protocol explicitly")
        state = StudyRunner(args.output, generation_provenance=generation).run(spec,
            base=args.spec.resolve().parent, resume=args.resume)
        print(json.dumps({"status": state["status"], "output": str(args.output),
            "failed_stage": state.get("active_stage") if state["status"] == "failed" else None,
            "error_type": state.get("error_type")}, ensure_ascii=False))
        return 1 if state["status"] in {"failed", "blocked_quality"} else 0
    if args.command == "study-plan":
        from medresearch.planning import plan_study
        from medresearch.data import relative_path
        if args.output.exists() or args.output.with_suffix(".provenance.json").exists():
            raise ValueError("Planning output already exists")
        brief = json.loads(args.brief.read_text(encoding="utf-8"))
        key = "path" if brief["data"]["kind"] == "file" else "config"
        brief["data"][key] = str(relative_path(brief["data"][key], args.brief.resolve().parent))
        study = plan_study(brief, ResponsesGenerator(args.model))
        atomic_json(args.output, study)
        atomic_json(args.output.with_suffix(".provenance.json"), {"model": args.model,
            "study_sha256": digest(study), "public_brief_sha256": digest({k: v for k, v in brief.items() if k != "data"})})
        print(json.dumps({"status": "planned", "protocol": str(args.output)}))
        return 0
    if args.command == "revise":
        from medresearch.planning import revise_session
        print(json.dumps(revise_session(json.loads(args.spec.read_text(encoding="utf-8")), args.session,
            base=args.spec.resolve().parent, note=args.note)))
        return 0
    if args.command == "literature":
        from medresearch.literature import search_pubmed
        if args.output.exists():
            raise ValueError("Literature output already exists")
        result = search_pubmed(args.query, args.limit)
        atomic_json(args.output, result)
        print(json.dumps({"sources": len(result["sources"]), "output": str(args.output)}))
        return 0
    if args.command == "verify-evidence":
        from medresearch.literature import verify_pubmed_sources
        from medresearch.study_contract import validate_study
        if args.output.exists():
            raise ValueError("Evidence verification output already exists")
        spec = validate_study(json.loads(args.spec.read_text(encoding="utf-8")))
        result = verify_pubmed_sources(spec["evidence"])
        atomic_json(args.output, result)
        print(json.dumps({"sources": len(result["sources"]), "output": str(args.output)}))
        return 0 if all(x["status"] in {"verified", "not_pubmed"} for x in result["sources"]) else 1
    if args.command == "compare":
        from medresearch.workflow import validate_across_runs
        result = validate_across_runs(args.runs, args.output)
        print(json.dumps({"studies": len(result["studies"]),
            "compatible_declared_constructs": result["compatible_declared_constructs"], "output": str(args.output)}))
        return 0
    if args.command == "demo":
        from medresearch.demo import run_demo
        result = run_demo(args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["passed"] else 1
    raise ValueError("Unknown research command")


if __name__ == "__main__":
    raise SystemExit(main())
