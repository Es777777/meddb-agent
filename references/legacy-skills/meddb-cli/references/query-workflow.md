# Query Workflow

## Request contract

Resolve these fields before execution:

1. Dataset and release or cycle.
2. Unit of analysis.
3. Population and exclusions.
4. Time range.
5. Grouping variables.
6. Measure and denominator.
7. Weighting or sample-only interpretation.
8. Aggregate output or explicit detail export.

## Custom SQL

- Write one `SELECT` or `WITH ... SELECT` statement.
- Qualify every physical table with its schema.
- List columns explicitly; only `COUNT(*)` may use a star.
- Use `:parameter_name` placeholders for values.
- Keep identifiers fixed after validating them through the local catalog.
- Prefer reviewed joins and explicit denominators.
- Do not use DDL, DML, `COPY`, `SET`, temporary objects, or blocked functions.

Store the SQL and JSON parameters under `F:\database\meddb-agent\queries`.
Do not put credentials or returned values in either file.

## Execution

Run `--explain-only` first. A passing plan is permission to execute, not proof of
statistical validity. If execution is export-only, report metadata and stop. Never
open an export-only file to answer the user.
