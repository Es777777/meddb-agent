"""Stateful, local research pipeline shared by all dataset adapters."""
from __future__ import annotations

import copy
import importlib.metadata
import json
import platform
import subprocess
from pathlib import Path

import pandas as pd

from meddb.config import PROJECT_ROOT
from medresearch.contracts import digest
from medresearch.data import hash_file, load_data, source_identity
from medresearch.literature import audit_definitions
from medresearch.quality import check_quality, select_cohort
from medresearch.reporting import render_report, review_results
from medresearch.runtime import atomic_json, implementation_hash
from medresearch.statistics import analyze
from medresearch.study_contract import validate_study


def protected_output(path: Path):
    """Patient-level local caches must never be placed in a public repo path."""
    resolved = path.resolve()
    if resolved.is_relative_to(PROJECT_ROOT):
        relative = resolved.relative_to(PROJECT_ROOT)
        # Permit only the explicitly ignored local-artifact roots in this repo.
        if not relative.parts or relative.parts[0] not in {"runs", "artifacts"}:
            raise ValueError("Study outputs in the repository must be under runs/ or artifacts/")
        ignored = subprocess.run(["git", "check-ignore", "-q", "--no-index", str(relative/".probe")], cwd=PROJECT_ROOT)
        tracked = subprocess.run(["git", "ls-files", "--", str(relative)], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True)
        if ignored.returncode or tracked.stdout.strip():
            raise ValueError("Local study output must be Git-ignored and untracked")


def environment_versions() -> dict:
    return {name: importlib.metadata.version(name) for name in (
        "numpy", "pandas", "scipy", "statsmodels", "lifelines", "scikit-learn", "matplotlib", "jsonschema")}


class StudyRunner:
    def __init__(self, output: Path, generation_provenance: dict | None = None):
        self.output = output.resolve()
        self.generation_provenance = generation_provenance

    def run(self, spec: dict, *, base: Path, resume: bool = False) -> dict:
        spec = validate_study(spec)
        protected_output(self.output)
        identity = {"study_sha256": digest(spec), "source": source_identity(spec, base),
            "implementation_sha256": implementation_hash(), "python": platform.python_version(),
            "dependencies": environment_versions(), "generation": self.generation_provenance}
        fingerprint = digest(identity)
        state_path = self.output/"state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if not resume or state["identity_sha256"] != fingerprint:
                raise ValueError("Study exists or inputs/code/environment changed; use a new run directory")
            for name, expected in state["artifacts"].items():
                if not (self.output/name).is_file() or hash_file(self.output/name) != expected:
                    raise ValueError("Study artifact missing or modified; resume refused")
            if state["status"] in {"completed", "needs_review", "blocked_quality"}:
                return state
        else:
            if self.output.exists() and any(self.output.iterdir()):
                raise ValueError("Study output must initially be empty")
            self.output.mkdir(parents=True, exist_ok=True)
            state = {"study_id": spec["study_id"], "identity_sha256": fingerprint,
                     "provenance": identity, "status": "running", "artifacts": {}, "stages": []}

        def checkpoint(stage: str, names: list[str]):
            for name in names:
                state["artifacts"][name] = hash_file(self.output/name)
            if stage not in state["stages"]:
                state["stages"].append(stage)
            state["status"] = "running"
            atomic_json(state_path, state)

        def stage_json(stage: str, name: str, produce):
            state["active_stage"] = stage
            if stage in state["stages"]:
                return json.loads((self.output/name).read_text(encoding="utf-8"))
            atomic_json(state_path, state)
            value = produce()
            atomic_json(self.output/name, value)
            checkpoint(stage, [name])
            return value

        atomic_json(self.output/"study.json", spec)
        checkpoint("protocol", ["study.json"])
        try:
            state["active_stage"] = "intake"
            if "intake" in state["stages"]:
                frame = pd.read_json(self.output/"source.local.json", orient="table")
                intake = json.loads((self.output/"intake.json").read_text(encoding="utf-8"))
            else:
                frame, intake = load_data(spec, base)
                if frame.empty:
                    raise ValueError("Source contains no observations")
                # A local, ignored source snapshot makes post-query stages
                # resumable without silently querying a changed database.
                frame.to_json(self.output/"source.local.json", orient="table", index=False, date_format="iso", double_precision=15)
                atomic_json(self.output/"intake.json", intake)
                checkpoint("intake", ["source.local.json", "intake.json"])
                # Analyze exactly the same frozen representation used on resume.
                frame = pd.read_json(self.output/"source.local.json", orient="table")
            mask, flow = select_cohort(frame, spec["cohort"])
            selected = frame.loc[mask].copy()
            quality = stage_json("quality", "quality.json", lambda: dict(
                check_quality(selected, spec, truncated=intake["truncated"]), cohort_flow=flow))
            definitions = stage_json("definitions", "definitions.json", lambda: audit_definitions(spec))
            if not quality["passed"]:
                atomic_json(self.output/"review.json", {"status": "blocked_quality", "issues": quality["issues"]})
                (self.output/"report.md").write_text("# Study blocked by data quality\n\nSee quality.json; no statistical conclusion was produced.\n", encoding="utf-8")
                checkpoint("blocked", ["review.json", "report.md"])
                state["status"] = "blocked_quality"
                atomic_json(state_path, state)
                return state
            survey_method = spec["analysis"]["method"].startswith("survey_")
            analysis_frame = frame.assign(_domain=mask) if survey_method else selected
            def primary_analysis():
                result = analyze(analysis_frame, spec["analysis"])
                if result["flow"]["analysis_rows"] < spec["quality"]["min_rows"]:
                    raise ValueError("Too few observations remain after missing-data handling")
                return result
            primary = stage_json("analysis", "analysis.json", primary_analysis)

            def sensitivities():
                records = []
                for variant in spec["sensitivity"]:
                    analysis = copy.deepcopy(spec["analysis"])
                    analysis.update(variant["analysis_overrides"])
                    variant_mask, variant_flow = select_cohort(frame, spec["cohort"]+variant["cohort"])
                    variant_spec = dict(spec, analysis=analysis)
                    variant_quality = check_quality(frame.loc[variant_mask], variant_spec)
                    if not variant_quality["passed"]:
                        records.append({"name": variant["name"], "status": "blocked_quality", "quality": variant_quality})
                        continue
                    try:
                        sample = frame.assign(_domain=variant_mask) if survey_method else frame.loc[variant_mask]
                        result = analyze(sample, analysis)
                        if result["flow"]["analysis_rows"] < spec["quality"]["min_rows"]:
                            raise ValueError("Too few sensitivity-analysis observations remain")
                        records.append({"name": variant["name"], "status": "completed", "cohort_flow": variant_flow,
                                        "result": result})
                    except Exception as exc:
                        records.append({"name": variant["name"], "status": "failed", "error_type": type(exc).__name__})
                return records

            sensitivity = stage_json("sensitivity", "sensitivity.json", sensitivities)
            review = stage_json("review", "review.json", lambda: review_results(spec, quality, definitions, primary, sensitivity))
            state["active_stage"] = "report"
            if "report" not in state["stages"]:
                names = render_report(self.output, spec, primary, quality, definitions, review, sensitivity)
                checkpoint("report", names)
            state["status"] = "completed" if review["status"] == "checks_passed" else "needs_review"
            state.pop("error_type", None)
        except Exception as exc:
            state.update(status="failed", error_type=type(exc).__name__)
        atomic_json(state_path, state)
        return state


def validate_across_runs(paths: list[Path], output: Path) -> dict:
    protected_output(output)
    if output.exists():
        raise ValueError("Comparison output already exists")
    runs = []
    for path in paths:
        state = json.loads((path/"state.json").read_text(encoding="utf-8"))
        if state["status"] not in {"completed", "needs_review"}:
            raise ValueError("Cross-dataset comparison requires completed study analyses")
        for name, expected in state["artifacts"].items():
            if hash_file(path/name) != expected:
                raise ValueError("Comparison source artifact integrity mismatch")
        runs.append({"study": json.loads((path/"study.json").read_text(encoding="utf-8")),
            "analysis": json.loads((path/"analysis.json").read_text(encoding="utf-8")),
            "run_sha256": state["identity_sha256"]})
    if len(runs) < 2:
        raise ValueError("At least two study runs are required")
    first = runs[0]["study"]
    def signature(study):
        analysis = study["analysis"]
        definitions = {d["column"]: d for d in study["definitions"]}
        def role(column):
            definition = definitions[column]
            return (definition["concept"], definition["unit"], definition["operational_definition"], definition["availability"])
        return {"method": analysis["method"], "estimand": analysis.get("estimand"),
            "unit": study["analysis_unit"], "population": study["population"],
            "design": study["design"],
            "role_definitions": {key: role(analysis[key]) for key in ("outcome", "exposure", "duration", "event") if key in analysis},
            "covariate_definitions": sorted(role(c) for c in analysis.get("covariates", []))}
    compatible = all(signature(run["study"]) == signature(first) for run in runs[1:])
    comparison = {"compatible_declared_constructs": compatible,
        "studies": [{"study_id": r["study"]["study_id"], "dataset": r["study"]["dataset"],
                     "run_sha256": r["run_sha256"], "estimates": r["analysis"].get("coefficients", []),
                     "metrics": r["analysis"].get("metrics")} for r in runs],
        "pooled_effect": None, "external_validation_certified": False,
        "limitations": ["Parallel evidence comparison; no patient linkage, automatic pooling or external-validation claim",
                        "Matching declared constructs does not prove transportability or independent populations"]}
    atomic_json(output/"comparison.json", comparison)
    return comparison
