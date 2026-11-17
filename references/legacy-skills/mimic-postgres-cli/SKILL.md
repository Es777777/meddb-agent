---
name: mimic-postgres-cli
description: Query and benchmark the local MIMIC-III and MIMIC-IV PostgreSQL databases through the read-only MedDB CLI. Use for MIMIC schema discovery, aggregate cohort questions, and paired official-versus-local published text-to-SQL benchmark runs. Do not use for SEER, BRFSS, NHANES, writes, or direct psql access.
---

# MIMIC PostgreSQL CLI

Use only `F:\database\meddb-agent\bin\meddb.cmd`. Never substitute MCP, a connector, direct `psql`, or an administrator role.

Choose one target explicitly:

- `official`: `config\meddb-mimic-official.json`, database `mimic_official_compare`.
- `local`: `config\meddb-mimic-local.json`, database `med_research`.

Both targets must connect as `meddb_mimic_reader` with database default read-only mode enabled. Treat the target as unresolved if the user has not specified it and the answer would differ by target.

For ordinary MIMIC questions, read [references/query-workflow.md](references/query-workflow.md). Start with `status`, then use catalog/schema commands before writing SQL. Keep the analysis aggregate unless the user explicitly requests record-level export.

For natural-language SQL generation, complex cohorts, or any benchmark item, also read [references/sql-generation.md](references/sql-generation.md). Make a compact query plan before SQL and statically validate dataset version, table/column names, grain, named parameters, and read-only form before calling `--explain-only`.

For MIMIC-IV clinical cohort questions, also read [references/clinical-semantics.md](references/clinical-semantics.md). Return a short, observable query specification: age anchor, clinical concept source, analysis unit, denominator, index event/window, statistic, and output units. A tool-disabled benchmark harness must embed these references in its frozen prompt; a link alone does not load their content.

When clinical codes or item IDs are unresolved, use read-only dictionary metadata from d_labitems, d_items, d_icd_diagnoses and d_icd_procedures. The CLINSQL harness supports `--dictionary-grounding` to retrieve and freeze candidates before inference. Dictionary candidate searches are not complete validated clinical phenotypes; label assumptions and use the correct data source/version.

Route dictionary searches by concept and source rather than mixing every term
into every dictionary. Report truncation and normalize padded ICD codes.
Medication classes require ingredient terminology, not diagnosis dictionaries;
the optional [medication registry](references/medication-class-registry.json)
contains explicitly scoped development name mappings, not patient records.

Only for an explicitly declared CLINSQL development-profile run, read
[clinsql-development-profile.md](references/clinsql-development-profile.md).
Its benchmark assumptions override generic defaults, never explicit question
requirements. Keep this mode separate from ordinary clinical queries and label
regression on previously analyzed cases as development, not held-out accuracy.

For a published benchmark run or an official-versus-local comparison, also read [references/benchmark-protocol.md](references/benchmark-protocol.md). Never invent questions, gold SQL, labels, task IDs, or scores when official artifacts are absent.

An execution-guided repair may use only the model's own SQL and CLI/EXPLAIN feedback, never Gold SQL, reference results, or rubric contents. Keep first-pass and repaired outcomes separate. Do not run a benchmark test split until the model, Skill, schema context, adapter, and retry policy have been frozen.

For every CLINSQL item, use MIMIC-IV v3.1 only (`mimiciv_hosp`, `mimiciv_icu`, and an explicitly available `mimiciv_derived` object). Treat any generated reference to `mimiciii` as a schema-target failure rather than executing it.

Stop before querying if `status` reports `mcp_disabled=false`; formal benchmark runs require the repository's no-MCP Codex profile. A status check may still be used to diagnose that condition.

Do not expose clinical row data, credentials, or benchmark test gold labels to model context. Report aggregate results and run metadata only. Detail-mode results must be exported under `F:\database\meddb-agent\exports` and must not be reopened by Codex.
