# Published benchmark protocol

## Allowed benchmark sources

- MIMIC-IV v3.1: CLINSQL official 633-task dataset, dual rubrics, reference results, and scorer.
- MIMIC-IV reliable abstention: EHRSQL 2024 Shared Task official repository and scorer.
- MIMIC-III: EHRSQL 2022 MIMIC-III official task files and evaluator.
- Optional MIMIC-III supplement: MIMICSQL/TREQS only after reproducing its published five-table derived database.

Do not use locally authored questions as published text-to-SQL benchmark items. Keep ABCS import-integrity checks in a separate report section.

## Artifact gate

Before any scored run, verify and record:

- source repository URL and commit hash;
- SHA-256 for task, schema, database, gold-label, and scorer files;
- exact train/valid/test counts read from the files;
- database preprocessing version;
- license/access prerequisites.

If any required official artifact is absent, stop the scored run. Do not reconstruct examples from a paper or README.

## Paired execution

For the query-plan, static-validation, and bounded no-Gold repair rules used before execution, read [sql-generation.md](sql-generation.md).

For every official `question_id`, run the same generation configuration twice:

1. official target;
2. local target.

Freeze model version, prompt, Skill version, catalog version, temperature, limits, retry policy, and question order. Only target configuration and a predeclared mechanical schema mapping may differ.

Develop on train, tune on valid, freeze, then score test. Never expose test gold SQL or expected results to the generation process. Artificial repair after viewing gold is a failure for the first-pass metric.

The local CLINSQL release has validation/test only. A development-example track
may partition validation into declared example IDs and disjoint scored IDs.
Freeze example question/SQL hashes before inference; supply no result CSVs or
rubrics, and reject any example/scored ID intersection. Report this few-shot
track separately from the historical zero-shot runs. Already analyzed examples
are development data and must not be counted as held-out evaluation results.

For the primary import-path comparison, generate one SQL statement from the common frozen benchmark schema context and execute that identical SQL on O and L. This shared-generation pairing isolates database/import effects from model sampling variance. Report independent O/L generations separately as a system-stability track; do not treat alias-only differences between two independently generated statements as data-value disagreements.

The batch harness may provide a frozen schema artifact whose hash and provenance were validated before the run, together with a parent-process status attestation for both read-only targets. In that mode, use the supplied schema context and do not repeat per-question catalog discovery. This keeps the prompt identical across O/L and matches ordinary text-to-SQL benchmark inference more closely.

Audit filtered outer joins before returning SQL. If a `LEFT JOIN` is used to retain zero-count groups, the counted expression must come from the filtered/matched side (or use an equivalent conditional expression); counting an unfiltered left-side event after applying a category condition only to a later lookup join silently counts non-matching events.

## Version boundary

CLINSQL uses MIMIC-IV v3.1 but publishes BigQuery SQL. A PostgreSQL run must preserve every official question and rubric while using one deterministic, task-independent dialect conversion. Validate each converted gold query against the published reference result. Label the result `CLINSQL PostgreSQL port`, not an unqualified reproduction of the BigQuery score.

CLINSQL routing is fixed: discover and query only `mimiciv_hosp`, `mimiciv_icu`, and explicitly installed `mimiciv_derived` objects. The same target database also contains MIMIC-III for other benchmarks, so generic catalog search can return both versions; never choose `mimiciii` for a CLINSQL item. The benchmark harness must reject a generated CLINSQL SQL statement that names `mimiciii` before execution and record `schema_target_error`.

EHRSQL 2024 uses a preprocessed MIMIC-IV demo 2.2 database. EHRSQL 2022 uses a shuffled and preprocessed MIMIC-III SQLite database. Exact paper reproduction must use those official benchmark databases.

Running the published query structures on full MIMIC-IV 3.1 or MIMIC-III 1.4 is a separate `schema-transfer` experiment. Preserve the official templates and deterministic mapping, but do not label its score as an original benchmark or leaderboard score.

## Reporting

Use the official scorer and primary metrics. For CLINSQL report overall, difficulty, scenario, SQL rubric, result rubric, critical-first and execution-plausibility results. For EHRSQL 2024 report `RS(10)`, `RS(0)`, `RS(N)`, execution accuracy, and answerable/unanswerable strata. For MIMICSQL report the official logical-form and execution metrics. Add SQL validity, executable rate, correct abstention, timeout rate, and p50/p95 latency as engineering metrics.

For official-versus-local comparison, report paired agreement, direction of disagreements, score deltas, paired bootstrap 95% confidence intervals, and McNemar's test on correctness. Do not average ABCS import checks into the paper benchmark score.

## Local resource budget

The CLINSQL harness supports `--workers 2` for model-only prefetch. Keep dictionary
queries, EXPLAIN and result execution serial in the parent; repairs share the
same two-call model cap. Use below-normal child priority and defer new calls
when CPU/memory exceed the recorded thresholds. Reader-session PGOPTIONS may
disable parallel query/JIT without changing server-wide settings. Record these
limits because they can affect timeout coverage and latency comparisons.
