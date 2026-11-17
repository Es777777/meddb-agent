---
name: meddb
description: Maintain the local medical database CLI implementation at F:\database\meddb-agent. Use only when changing its security rules, PostgreSQL roles, SQLite catalog, SEER/BRFSS/NHANES templates, exports, audit behavior, tests, installation, or troubleshooting. Do not use for user analysis requests; use the separate meddb-cli skill for safe no-MCP query execution.
---

# MedDB

## Purpose

Maintain the local `meddb` terminal application while preserving this boundary:

```text
ordinary PowerShell -> meddb CLI -> meddb_reader -> PostgreSQL
```

This skill maintains the application and must not act as the query interface.
The separate `meddb-cli` skill owns safe Codex query execution and result limits.

## Non-Negotiable Rules

1. Do not call MCP, connectors, apps, browser tools, or remote services.
2. Do not execute a query that returns medical data rows into Codex context.
3. Do not request, print, log, or hardcode PostgreSQL passwords.
4. Keep `meddb_reader` read-only at both PostgreSQL and CLI layers.
5. Allow only one parsed `SELECT` or `WITH ... SELECT` statement.
6. Require schema-qualified tables and block raw-record tables.
7. Default to aggregate results; require explicit detail mode and cap it at 100 rows.
8. Keep exports inside `F:\database\meddb-agent\exports`.
9. Audit query metadata only, never result content.
10. State dataset-specific statistical limits in every relevant template.

Read [security-policy.md](references/security-policy.md) before changing query
validation, database permissions, exports, credentials, or audit behavior.

## Workflow

### Inspect

- Work in `F:\database\meddb-agent`.
- Read the existing implementation and tests before editing.
- Use `catalog/catalog.sqlite` or `meddb schema` only for structural metadata.
- Never inspect result exports as part of a medical analysis request.
- Read [architecture.md](references/architecture.md) for component ownership.

### Change

- Follow the existing Python 3.10 package patterns.
- Use parameter placeholders for values and catalog-validated allowlists for
  table or column identifiers.
- Add or update a manifest entry and a separate SQL file for each template.
- Preserve the user-facing Chinese CLI while keeping `.ps1` files ASCII for
  Windows PowerShell 5.1 compatibility.
- Never add a model API, MCP server, HTTP client, telemetry service, or cloud
  dependency.

### Verify

Run local unit tests:

```powershell
F:\database\meddb-agent\.conda\python.exe -m unittest discover `
  -s F:\database\meddb-agent\tests -v
```

For template changes, run only PostgreSQL planning validation:

```powershell
F:\database\meddb-agent\.conda\python.exe `
  F:\database\meddb-agent\scripts\validate_templates.py
```

`validate_templates.py` uses `EXPLAIN` and must not fetch query result rows.
Run `scripts\verify_project.ps1` for the combined maintenance check.

### Handoff

- Give the user the exact `meddb` command to run in a new ordinary PowerShell.
- Do not run that analytical command inside Codex.
- Explain where the terminal output or local export will be written.
- Keep passwords out of the command.

## Template Quality

Read [dataset-rules.md](references/dataset-rules.md) before adding a query.

Every template must include:

- A stable template ID and Chinese title/description.
- An explicit supported-dataset list.
- Typed value parameters.
- Catalog-validated identifier parameters where identifiers are dynamic.
- A small, deterministic validation sample.
- Statistical notes that prevent overclaiming.
- Unit coverage and successful `EXPLAIN` for every supported dataset.

## Administration

Database import, DDL, grants, and catalog rebuilds are separate administrator
operations. They still use local scripts and PostgreSQL CLI tools, never MCP.
Use `postgres` only for an explicitly requested administration task; use
`meddb_reader` for catalog reads, template planning, and user queries.

Read [maintenance.md](references/maintenance.md) before role, catalog, PATH,
or Codex configuration changes.
