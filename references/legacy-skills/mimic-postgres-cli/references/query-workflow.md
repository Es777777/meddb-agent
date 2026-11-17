# MIMIC query workflow

## Target commands

Run from `F:\database\meddb-agent`.

```powershell
.\bin\meddb.cmd --config config\meddb-mimic-official.json status
.\bin\meddb.cmd --config config\meddb-mimic-local.json status
```

Use the same `--config` value for every subsequent command in one task. Do not switch targets after inspecting query results.

## Discover before querying

1. Search the catalog for the clinical concept.
2. Show candidate table definitions and relationships.
3. Confirm the unit of analysis: patient, admission, ICU stay, event, medication, diagnosis, procedure, or measurement.
4. Resolve MIMIC-III versus MIMIC-IV and the correct schema.
5. Before writing SQL, state a compact plan: dataset/version, grain, cohort/denominator, time window, primary table, join keys, and intended aggregate or output.

Useful commands:

```powershell
.\bin\meddb.cmd --config <config> catalog search "<term>"
.\bin\meddb.cmd --config <config> schema list --dataset <schema>
.\bin\meddb.cmd --config <config> schema show <schema.table>
```

## SQL contract

- Write one `SELECT` or `WITH ... SELECT` statement.
- Qualify every physical table with `mimiciii`, `mimiciv_hosp`, or `mimiciv_icu`.
- List columns explicitly; only `COUNT(*)` may use a star.
- Use `:parameter_name` placeholders and a separate JSON parameter file.
- Keep identifiers fixed after catalog validation.
- State the denominator and grain for every rate or count.
- Treat `subject_id`, `hadm_id`, and ICU stay identifiers as different grains.
- Do not query `import_manifest`, `data_quality_issues`, raw tables, precision-fix backups, or physical partition children.

Store SQL and parameter files under `F:\database\meddb-agent\queries`. Run `--explain-only` first, then execute only if it passes the CLI cost and security checks.

Before `--explain-only`, reject SQL with positional parameters (`$1`, `?`, `%s`), unqualified physical tables, unknown columns, write statements, or parameter keys that do not exactly match the `:name` placeholders. For natural-language SQL generation and repair rules, read [sql-generation.md](sql-generation.md).

```powershell
.\bin\meddb.cmd --config <config> query --sql-file <sql> --params-file <json> --explain-only
.\bin\meddb.cmd --config <config> query --sql-file <sql> --params-file <json> --yes --format json
```

Use `--detail` only for an explicitly requested export. Never send record-level results to the chat or clipboard.
