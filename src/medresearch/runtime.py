from __future__ import annotations

import json
import platform
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from medresearch.contracts import digest, validate_proposal


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str,
                                    allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def implementation_hash() -> str:
    root = Path(__file__).resolve().parents[1]
    return digest({str(path.relative_to(root)): path.read_text(encoding="utf-8")
                   for package in ("medresearch", "meddb")
                   for path in sorted((root / package).glob("*.py"))})


class ResearchRunner:
    """One output directory per immutable execution; successful stages can resume.

    Output directories contain restricted local results and must stay Git-ignored.
    No result or execution error is sent back to a model in this release.
    """
    def __init__(self, tool: Any, output: Path, provenance: dict[str, Any]):
        self.tool, self.output = tool, output
        self.provenance = dict(provenance, implementation_sha256=implementation_hash(),
                               python_version=platform.python_version())

    def run(self, proposal: dict[str, Any], *, resume: bool = False,
            explain_only: bool = False) -> dict[str, Any]:
        validate_proposal(proposal)
        identity = digest({"proposal": proposal, "provenance": self.provenance,
                           "explain_only": explain_only})
        checkpoint = self.output / "state.json"
        if checkpoint.exists():
            if not resume:
                raise ValueError("Run already exists; use --resume or a new output directory")
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            if state["identity_sha256"] != identity:
                raise ValueError("Resume refused: proposal, configuration, metadata or code changed")
            if state["status"] in {"completed", "explained", "needs_clarification", "unsupported"}:
                for name, expected in state.get("artifact_sha256", {}).items():
                    path = self.output / name
                    if not path.is_file() or digest(path.read_text(encoding="utf-8")) != expected:
                        raise ValueError("Resume refused: missing or modified artifact")
                return state
        elif self.output.exists() and any(self.output.iterdir()):
            raise ValueError("Output directory must be empty for a new run")
        self.output.mkdir(parents=True, exist_ok=True)
        state = {"identity_sha256": identity, "task_id": proposal["spec"]["task_id"],
                 "status": "planned", "provenance": self.provenance,
                 "proposal_sha256": digest(proposal),
                 "updated_at_utc": datetime.now(timezone.utc).isoformat()}
        atomic_json(self.output / "proposal.json", proposal)
        atomic_json(checkpoint, state)
        if proposal["status"] != "ready":
            state["status"] = proposal["status"]
        else:
            state["status"] = "executing"
            atomic_json(checkpoint, state)
            # A failed retry must never retain a previous result as evidence.
            (self.output / "result.json").unlink(missing_ok=True)
            try:
                result = self.tool.execute(proposal, explain_only=explain_only)
                atomic_json(self.output / "result.json", asdict(result))
                state.update(status="explained" if explain_only else "completed",
                             query_id=result.query_id, truncated=result.truncated,
                             returned_rows=result.returned_rows)
            except Exception as exc:
                # Only the class crosses into general run logs. Database errors
                # can echo clinical values, so do not persist their text here.
                state.update(status="failed", error_type=type(exc).__name__)
        report = ["# MIMIC descriptive research run", "",
                  f"Task: {state['task_id']}", f"Status: {state['status']}", "",
                  "Scope: descriptive SQL; no causal, survival-model or predictive claim.",
                  "Clinical semantics and dataset release require independent review.",
                  "ResearchSpec and executed SQL: proposal.json",
                  "Execution provenance: state.json"]
        if (self.output / "result.json").exists():
            report += ["Local execution evidence: result.json",
                       f"Query ID: {state.get('query_id', 'failed attempt')}",
                       f"Truncated: {state.get('truncated', 'unknown')}"]
        if state.get("truncated"):
            report += ["INCOMPLETE: truncated results cannot be scored as a full answer."]
        report += ["", "Declared limitations:"] + [f"- {x}" for x in proposal["limitations"]]
        (self.output / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
        state["artifact_sha256"] = {
            name: digest((self.output / name).read_text(encoding="utf-8"))
            for name in ("proposal.json", "report.md", "result.json") if (self.output / name).exists()
        }
        state["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
        atomic_json(checkpoint, state)
        return state


def generate_proposal(generator: Any, *, question: str, task_id: str,
                      dataset_version: str, schema: str, rules: str) -> dict[str, Any]:
    proposal = validate_proposal(generator.generate(question, task_id, dataset_version, schema, rules))
    if (proposal["spec"]["task_id"], proposal["spec"]["question"], proposal["spec"]["dataset_version"]) != (
            task_id, question, dataset_version):
        raise ValueError("Model changed task identity or declared dataset version")
    return proposal
