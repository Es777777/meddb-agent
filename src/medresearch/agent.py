"""Bounded planning agent followed by the local scientific workflow.

Only public design metadata enters the model. Analysis outcomes never drive
automatic hypothesis changes or significance-seeking retries.
"""
from __future__ import annotations

import json
from pathlib import Path

from medresearch.contracts import digest
from medresearch.data import hash_file, relative_path
from medresearch.planning import plan_study, revise_session
from medresearch.runtime import atomic_json, implementation_hash
from medresearch.study_contract import validate_study
from medresearch.workflow import StudyRunner, protected_output


def run_agent(brief: dict, generator, output: Path, *, base: Path,
              max_attempts: int = 2, resume: bool = False) -> dict:
    if not 1 <= max_attempts <= 3:
        raise ValueError("Planning attempt budget must be between 1 and 3")
    protected_output(output)
    brief = json.loads(json.dumps(brief))
    key = "path" if brief["data"]["kind"] == "file" else "config"
    brief["data"][key] = str(relative_path(brief["data"][key], base))
    identity = digest({"brief": brief, "model": generator.model,
                       "max_attempts": max_attempts, "implementation": implementation_hash()})
    state_path = output/"agent-state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not resume or state["identity_sha256"] != identity:
            raise ValueError("Agent run exists or planning inputs changed")
        if state.get("plan_sha256") and hash_file(output/"study.json") != state["plan_sha256"]:
            raise ValueError("Agent plan was modified")
    else:
        if output.exists() and any(output.iterdir()):
            raise ValueError("Agent output must be empty initially")
        state = {"identity_sha256": identity, "status": "planning", "attempts": [],
                 "model": generator.model, "observations_sent_to_model": False}
        atomic_json(state_path, state)
    if "plan_sha256" not in state:
        remaining = max_attempts-len(state["attempts"])
        for _ in range(remaining):
            try:
                spec = plan_study(brief, generator)
                atomic_json(output/"study.json", spec)
                state["plan_sha256"] = hash_file(output/"study.json")
                state["attempts"].append({"status": "planned"})
                revise_session(spec, output/"session", base=base, note="Prespecified agent plan before any analysis")
                break
            except Exception as exc:
                state["attempts"].append({"status": "planning_failed", "error_type": type(exc).__name__})
            atomic_json(state_path, state)
        if "plan_sha256" not in state:
            state["status"] = "planning_failed"
            atomic_json(state_path, state)
            return state
    atomic_json(state_path, state)
    spec = validate_study(json.loads((output/"study.json").read_text(encoding="utf-8")))
    execution = StudyRunner(output/"execution", generation_provenance={"model": generator.model,
        "agent_identity_sha256": identity, "plan_sha256": state["plan_sha256"]}).run(spec, base=base, resume=resume)
    state["status"] = execution["status"]
    state["report"] = str(output/"execution/report.html")
    state["automatic_outcome_guided_replanning"] = False
    atomic_json(state_path, state)
    return state
