"""Capacity-only continuation for an authorized, already frozen validation job.

Never retries completed model attempts or changes the frozen experiment. It only
restarts a resource-deferred supervisor after capacity recovers. No cron/service,
no clinical/Gold files, no other-process control, no threshold relaxation.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--max-hours", type=float, default=24)
    args = parser.parse_args()
    root = args.run_root.resolve()
    helper = root / "frozen/tools/low_impact_benchmark.py"
    spec = importlib.util.spec_from_file_location("capacity_helper", helper)
    impact = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(impact)
    impact.lower_parent_priority()
    own_lock = root / "capacity.lock"
    with own_lock.open("x", encoding="utf-8") as handle:
        json.dump({"pid": os.getpid()}, handle)
    started = time.monotonic()

    def write(status, **extra):
        payload = {"status": status, "pid": os.getpid(), "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                   "policy": impact.POLICY, "max_hours": args.max_hours, **extra}
        path = root / "capacity_state.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(path)

    try:
        while time.monotonic() - started < args.max_hours * 3600:
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            if state["status"] in ("complete", "runner_error", "generation_service_error"):
                write("finished" if state["status"] == "complete" else "attention_required", supervisor_status=state["status"])
                return
            if state["status"] in ("running", "frozen") or (root / "run.lock").exists():
                write("supervisor_active", supervisor_status=state["status"])
                time.sleep(30)
                continue
            if state["status"] != "needs_resource_recovery":
                write("attention_required", supervisor_status=state["status"])
                return
            ready, snapshot = impact.wait_for_capacity(policy={**impact.POLICY, "max_wait_seconds": 0})
            manifest = json.loads((root / "evaluation_manifest.json").read_text(encoding="utf-8"))
            if manifest["parameters"].get("adaptive_model_workers"):
                ready = ready and impact.adaptive_workers(snapshot, manifest["parameters"]["workers"]) > 0
            if not ready:
                write("waiting_for_capacity", resource_state=snapshot)
                time.sleep(30)
                continue
            write("resuming_resource_deferred_only", resource_state=snapshot)
            log = root / "capacity_resumes.log"
            with log.open("a", encoding="utf-8") as handle:
                completed = subprocess.run([
                    sys.executable, "-u", str(root / "frozen/tools/run_clinsql_v4_full.py"),
                    "--resume", "--run-root", str(root),
                ], stdout=handle, stderr=subprocess.STDOUT, cwd=root, creationflags=impact.child_flags())
            if completed.returncode not in (0, 75):
                write("attention_required", returncode=completed.returncode)
                return
        write("capacity_continuation_deadline_reached")
    finally:
        own_lock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
