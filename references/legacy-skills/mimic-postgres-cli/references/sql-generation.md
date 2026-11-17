# SQL generation and validation

Use this reference for natural-language MIMIC queries, complex cohorts, and benchmark inference. It improves generation quality without weakening the read-only boundary or using benchmark answers.

## Plan before SQL

Before producing SQL, identify these fields in a compact internal plan or in the harness's requested structured output:

- **Dataset/version:** MIMIC-III (`mimiciii`) or MIMIC-IV v3.1 (`mimiciv_hosp`, `mimiciv_icu`).
- **Grain:** patient, admission, ICU stay, event, medication, diagnosis, procedure, or measurement.
- **Cohort and denominator:** which entities are retained and what is counted or averaged.
- **Temporal rule:** index admission/stay, first/last event, inclusive window, and event ordering.
- **Physical sources:** tables, join keys, filters, and required columns confirmed from catalog/schema context.
- **Output:** grouping, aggregate, ordering, limit, and named parameters.

For rates, counts, and cohort comparisons, make the denominator explicit. Do not silently mix `subject_id`, `hadm_id`, and ICU stay grain. For an outer join intended to preserve zero-count groups, count a column on the filtered/matched side or use an equivalent conditional aggregate.

## Static validation before EXPLAIN

Validate the generated statement before any database call:

1. It is one read-only `SELECT` or `WITH ... SELECT`; it has no data-definition, mutation, session, file, or administrative operation.
2. Every physical table is schema-qualified and belongs to the selected dataset/version. For CLINSQL, reject `mimiciii` before execution.
3. Every selected physical column exists in the frozen schema context or catalog. Do not assume benchmark-preprocessing-only columns exist in raw MIMIC.
4. It uses explicit columns; `SELECT *` is forbidden except `COUNT(*)`.
5. It contains no positional or driver-specific parameter syntax: `$1`, `$2`, `?`, `%s`, or interpolation placeholders. Use only `:parameter_name`.
6. The set of SQL `:parameter_name` placeholders exactly equals the JSON parameter keys. Values must be typed JSON scalars or arrays supported by the CLI.
7. It has a stated grain and a time predicate before joining a very large event table whenever the question permits that restriction.

If a check fails, correct it before EXPLAIN. If the required schema, parameter value, or clinical definition is genuinely ambiguous, abstain and explain the ambiguity instead of guessing.

The supplied `scripts/validate_generated_sql.py` implements routing and parameter-format checks before EXPLAIN. It is deliberately conservative; PostgreSQL remains the authority for complete parsing and physical-column validation.

## EXPLAIN-guided repair

For ordinary queries, run `--explain-only` before execution. For a benchmark harness, the parent process may perform the check.

- Record the original SQL and its first-pass status.
- A repair may use only the original question, frozen schema/catalog context, generated SQL, parameter object, and CLI/EXPLAIN diagnostic.
- Permit at most one repair attempt unless the benchmark protocol explicitly specifies another limit.
- Never open Gold SQL, expected results, SQL rubrics, result rubrics, or prior successful task outputs to construct a repair.
- Report first-pass execution/accuracy and execution-guided repaired execution/accuracy as different metrics.

## Benchmark-specific adapters

Keep adapters outside the generic query workflow and version them with hashes.

- **EHRSQL 2022:** its official reproduction uses a shuffled, preprocessed MIMIC-III SQLite database. A full raw MIMIC-III PostgreSQL run is a schema-transfer experiment. Do not emulate preprocessing columns ad hoc; use a separately declared adapter or benchmark schema.
- **CLINSQL:** its official Gold SQL is BigQuery. A PostgreSQL conversion must be deterministic, task-independent, and validated against published reference results. Use only MIMIC-IV v3.1 schemas.

Do not include the evaluated task's Gold logic in a prompt, adapter, or static rule.
A separately declared development-example track may supply frozen examples from
an explicitly disjoint development partition. Record every source ID/hash and
reject overlap with all scored IDs. Label this few-shot evaluation separately
from zero-shot; no result CSVs, rubrics, or evaluated-task Gold are generator inputs.

## Development and final evaluation

Use train/development data to improve the Skill, adapter, model prompt, and retry policy. Before an official test run, freeze model/version, reasoning effort, temperature or sampling configuration, Skill hash, schema context hash, adapter hash, limits, and task order. Test Gold is for scoring after the generation/execution decision, not for repair or tuning.

Report SQL validity, EXPLAIN pass rate, execution rate, first-pass Gold metric, optional repaired metric, timeout rate, p50/p95 latency, and official-versus-local paired agreement separately.
