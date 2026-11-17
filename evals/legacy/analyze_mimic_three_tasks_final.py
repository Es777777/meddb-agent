from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
SEED = 20260919
BOOTSTRAP_REPLICATES = 100_000


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 2) if denominator else 0.0


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> list[float]:
    if total == 0:
        return [0.0, 0.0]
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(
        proportion * (1 - proportion) / total + z * z / (4 * total * total)
    ) / denominator
    return [round(100 * (center - margin), 2), round(100 * (center + margin), 2)]


def exact_mcnemar_p(left_only: int, right_only: int) -> float:
    discordant = left_only + right_only
    if discordant == 0:
        return 1.0
    tail = min(left_only, right_only)
    probability = sum(math.comb(discordant, k) for k in range(tail + 1)) / (2**discordant)
    return min(1.0, 2 * probability)


def percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    position = (len(sorted_values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction


def paired_bootstrap_delta_ci(
    left: list[bool], right: list[bool], *, seed: int = SEED, replicates: int = BOOTSTRAP_REPLICATES
) -> list[float]:
    if len(left) != len(right) or not left:
        raise ValueError("paired vectors must be non-empty and have equal length")
    rng = random.Random(seed)
    n = len(left)
    deltas = []
    for _ in range(replicates):
        left_sum = 0
        right_sum = 0
        for _ in range(n):
            index = rng.randrange(n)
            left_sum += int(left[index])
            right_sum += int(right[index])
        deltas.append(100.0 * (right_sum - left_sum) / n)
    deltas.sort()
    return [round(percentile(deltas, 0.025), 2), round(percentile(deltas, 0.975), 2)]


def correctness_summary(left: list[bool], right: list[bool], left_name: str, right_name: str) -> dict:
    total = len(left)
    left_correct = sum(left)
    right_correct = sum(right)
    left_only = sum(a and not b for a, b in zip(left, right))
    right_only = sum(not a and b for a, b in zip(left, right))
    return {
        "tasks": total,
        left_name: {
            "correct": left_correct,
            "accuracy_pct": pct(left_correct, total),
            "wilson_95_ci_pct": wilson_interval(left_correct, total),
        },
        right_name: {
            "correct": right_correct,
            "accuracy_pct": pct(right_correct, total),
            "wilson_95_ci_pct": wilson_interval(right_correct, total),
        },
        "delta_right_minus_left_percentage_points": round(
            100.0 * (right_correct - left_correct) / total, 2
        ),
        "paired_bootstrap_delta_95_ci_percentage_points": paired_bootstrap_delta_ci(left, right),
        "discordance": {
            f"{left_name}_only_correct": left_only,
            f"{right_name}_only_correct": right_only,
            "mcnemar_exact_two_sided_p": exact_mcnemar_p(left_only, right_only),
        },
    }


def main() -> None:
    gold_pilot_path = ARTIFACTS / "clinsql_validation_pilot_18_reanalyzed.json"
    model_pilot_path = ARTIFACTS / "clinsql_model_generation_pilot_18_analysis.json"
    gold_full_path = ARTIFACTS / "clinsql_validation_full_187.json"
    ehrsql_path = ARTIFACTS / "ehrsql_mimiciii_results_analysis.json"

    gold_pilot = load_json(gold_pilot_path)
    model_pilot = load_json(model_pilot_path)
    gold_full = load_json(gold_full_path)
    ehrsql = load_json(ehrsql_path)

    gold_by_key = {task["task_key"]: task for task in gold_pilot["tasks"]}
    model_by_key = {task["task_key"]: task for task in model_pilot["tasks"]}
    if set(gold_by_key) != set(model_by_key):
        raise RuntimeError("CLINSQL 18-task gold and Skill+CLI pilots do not contain identical task keys")
    keys = sorted(gold_by_key)

    direct_executed = [gold_by_key[key]["official"]["status"] == "executed" for key in keys]
    skill_executed = [model_by_key[key]["official_execution_status"] == "executed" for key in keys]
    direct_strict = [
        bool(gold_by_key[key]["official_vs_reference"].get("comparable"))
        and bool(gold_by_key[key]["official_vs_reference"].get("unordered_value_match"))
        for key in keys
    ]
    direct_numeric = [
        bool(gold_by_key[key]["official_vs_reference"].get("comparable"))
        and bool(gold_by_key[key]["official_vs_reference"].get("unordered_numeric_tolerant_match"))
        for key in keys
    ]
    skill_strict = [bool(model_by_key[key]["official_reference_strict"]) for key in keys]
    skill_numeric = [bool(model_by_key[key]["official_reference_numeric"]) for key in keys]

    task1 = {
        "benchmark": "CLINSQL validation balanced 18-task pilot",
        "pairing": "same task_key, same official PostgreSQL target",
        "execution": correctness_summary(direct_executed, skill_executed, "direct_gold_sql", "skill_cli"),
        "strict_reference_accuracy": correctness_summary(
            direct_strict, skill_strict, "direct_gold_sql", "skill_cli"
        ),
        "numeric_tolerant_reference_accuracy": correctness_summary(
            direct_numeric, skill_numeric, "direct_gold_sql", "skill_cli"
        ),
        "skill_sql_generation": {
            "generated": model_pilot["overall"]["generated"],
            "tasks": model_pilot["overall"]["tasks"],
            "generation_rate_pct": model_pilot["overall"]["generation_rate_pct"],
        },
        "interpretation": (
            "The direct arm executes the published gold SQL PostgreSQL port. The Skill+CLI arm starts from "
            "the natural-language question. The difference therefore measures end-to-end semantic query generation, "
            "not database import fidelity."
        ),
    }

    full_tasks = gold_full["tasks"]
    official_executed = [task["official"]["status"] == "executed" for task in full_tasks]
    local_executed = [task["local"]["status"] == "executed" for task in full_tasks]
    official_strict = [
        bool(task["official_vs_reference"].get("comparable"))
        and bool(task["official_vs_reference"].get("unordered_value_match"))
        for task in full_tasks
    ]
    local_strict = [
        bool(task["local_vs_reference"].get("comparable"))
        and bool(task["local_vs_reference"].get("unordered_value_match"))
        for task in full_tasks
    ]
    official_numeric = [
        bool(task["official_vs_reference"].get("comparable"))
        and bool(task["official_vs_reference"].get("unordered_numeric_tolerant_match"))
        for task in full_tasks
    ]
    local_numeric = [
        bool(task["local_vs_reference"].get("comparable"))
        and bool(task["local_vs_reference"].get("unordered_numeric_tolerant_match"))
        for task in full_tasks
    ]
    paired_executed = [a and b for a, b in zip(official_executed, local_executed)]
    paired_strict_equal = [
        bool(task["official_vs_local"].get("comparable"))
        and bool(task["official_vs_local"].get("unordered_value_match"))
        for task in full_tasks
    ]
    paired_numeric_equal = [
        bool(task["official_vs_local"].get("comparable"))
        and bool(task["official_vs_local"].get("unordered_numeric_tolerant_match"))
        for task in full_tasks
    ]

    task2 = {
        "benchmark": "CLINSQL validation 187-task PostgreSQL port",
        "execution": correctness_summary(official_executed, local_executed, "official", "local"),
        "strict_reference_accuracy": correctness_summary(official_strict, local_strict, "official", "local"),
        "numeric_tolerant_reference_accuracy": correctness_summary(
            official_numeric, local_numeric, "official", "local"
        ),
        "database_result_equivalence": {
            "both_executed": sum(paired_executed),
            "strict_equal": sum(paired_strict_equal),
            "strict_equal_given_both_executed_pct": pct(sum(paired_strict_equal), sum(paired_executed)),
            "numeric_tolerant_equal": sum(paired_numeric_equal),
            "numeric_tolerant_equal_given_both_executed_pct": pct(
                sum(paired_numeric_equal), sum(paired_executed)
            ),
        },
        "skill_cli_18_task_pilot": model_pilot["overall"],
        "ehrsql_mimiciii_schema_transfer": {
            "tasks": ehrsql["paired"]["tasks"],
            "strict_match": ehrsql["paired"]["counts"]["paired:strict_match"],
            "strict_match_pct": pct(
                ehrsql["paired"]["counts"]["paired:strict_match"], ehrsql["paired"]["tasks"]
            ),
            "numeric_tolerant_match": ehrsql["paired"]["counts"]["paired:numeric_tolerant_match"],
            "numeric_tolerant_match_pct": pct(
                ehrsql["paired"]["counts"]["paired:numeric_tolerant_match"], ehrsql["paired"]["tasks"]
            ),
        },
        "interpretation": (
            "The local database is not a narrow-table decomposition. It is a reconstruction of the same 57 official "
            "source tables with one synthetic id per table, additional constraints/indexes, and limited type/precision changes."
        ),
    }

    output = {
        "report_version": "mimic-three-tasks-final-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "bootstrap": {"seed": SEED, "replicates": BOOTSTRAP_REPLICATES},
        "inputs": {
            str(path.relative_to(ROOT)): {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in [gold_pilot_path, model_pilot_path, gold_full_path, ehrsql_path]
        },
        "task_1_official_direct_vs_skill_cli": task1,
        "task_2_official_vs_modified_local": task2,
    }

    destination = ARTIFACTS / "mimic_three_tasks_final_analysis.json"
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(destination)


if __name__ == "__main__":
    main()
