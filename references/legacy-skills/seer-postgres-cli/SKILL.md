---
name: seer-postgres-cli
description: Query and validate the local SEER PostgreSQL snapshot through native psql only. Use for SEER cohort construction, variable mapping, code-label verification, tumor/patient-grain counts, year-specific staging, Mets at DX completeness, survival/outcome queries, join-cardinality checks, and EXPLAIN diagnostics against the local seer17 schema.
---

# SEER PostgreSQL CLI

Use this Skill with `$supabase-postgres-best-practices` for local SEER work.

## Non-negotiable execution boundary

- Use native `psql` only. Do not use MCP, HTTP, a driver, meddb, `PGPASSWORD`, a password-bearing URI, or a password argument.
- Obtain connection fields only from `PGHOST`, `PGPORT`, `PGDATABASE`, and `PGUSER`; let `pgpass.conf` authenticate.
- For every query session, use one transaction:

```sql
BEGIN;
SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY;
SET LOCAL statement_timeout = '180s';
SET LOCAL lock_timeout = '10s';
-- query and checks
ROLLBACK;
```

- Never modify roles, data, schema, indexes, server configuration, or files outside the active workspace.

## Efficient evidence workflow

1. Read `references/local-seer17-catalog.md` first. It is a frozen, database-derived reference, not task gold.
2. Read only the relevant topic reference under `references/`.
3. Use a minimal `information_schema` or `pg_catalog` query only if the reference does not settle the table, field, key, type, label, or index needed for the question. Do not scan the entire schema by default.
4. Make the requested query with exact stored labels/codes. Report the result grain: physical rows, unique tumors (`tumors.id`), and/or unique patients (`patients.patient_id`) as appropriate.
5. For a costly query, run `EXPLAIN (ANALYZE, BUFFERS, SETTINGS)` first, then execute the exact final `SELECT`. Do not recommend or create an index unless explicitly asked.
6. Save the complete executed transaction to one UTF-8, no-BOM `.sql` file, run psql exactly once with `-X -w -v ON_ERROR_STOP=1 -f`, and return that file's exact text as `final_sql`.

For a selective diagnosis-year question followed by a filter on an unindexed
extension-table column (for example `site_recode_2008` or
`summary_stage_2000`), start from the indexed `tumors.year_of_diagnosis` set.
If `EXPLAIN` still scans the full extension table, use a `MATERIALIZED` CTE for
the year cohort before joining the one-to-one extension table. Verify the plan;
do not assert that this is always faster.

## Required SEER safeguards

- Never infer a field solely from its English name. Verify it against the catalog/reference or a targeted metadata query.
- Do not implement an invariant by intentionally causing an error (`1/0`,
  `\quit`, `\gexec`, or another abort branch). Return the invariant as a
  boolean/0-1 output column or describe it from the successful result instead.
- For 2015–2017 stage, use `seer17.stage_summary.summary_stage_2000`; do not substitute a 2018+ stage variable.
- `seer17.tumors.year_of_diagnosis` is text in this import: compare it to
  quoted year strings, for example `IN ('2015','2016','2017')`.
- For 2015–2017 Mets at DX, use `seer17.metastasis.mets_bone`, `mets_brain`, `mets_liver`, and `mets_lung`. In this database, usable values are exactly `No` and `Yes`; `Unknown`, `N/A`, empty strings, and `NULL` are not usable.
- Use native `seer17.mortality_followup.survival_months`, `survival_months_flag`, and `seer17.cause_of_death.seer_cause_specific_death_classification` for survival work. Do not reconstruct survival time from calendar years.
- Treat a paper's “lost to follow-up” rule as `NOT_REPRODUCIBLE` when no local field plus explicit code rule identifies it. Do not invent a proxy.
- A local SEER 17 snapshot cannot be a strict equality check for a paper that used a SEER 22 or a different release/submission. State `SOURCE_COVERAGE_MISMATCH` with evidence.

## References

- `references/local-seer17-catalog.md`: release, tables, verified keys, columns, indexes, and common exact labels.
- `references/cohort-and-survival.md`: tumor/patient grain, stage, Mets and survival rules.
- `references/query-output-contract.md`: response and benchmark execution requirements.
