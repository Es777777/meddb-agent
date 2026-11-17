# Maintenance

## Project Verification

```powershell
powershell -ExecutionPolicy Bypass -File `
  C:\Users\lenovo\.codex\skills\meddb\scripts\verify_project.ps1
```

This runs unit tests, local catalog metadata checks, static no-MCP audit, and
template `EXPLAIN` validation. It must not execute an analytical result query.

## Rebuild Catalog

Use `meddb_reader`:

```powershell
F:\database\meddb-agent\.conda\python.exe `
  F:\database\meddb-agent\scripts\build_catalog.py `
  --db-user meddb_reader
```

The catalog contains table/column/relationship metadata and bilingual labels,
not medical record values.

## Reader Role

Run only for explicit administration:

```powershell
powershell -ExecutionPolicy Bypass -File `
  F:\database\meddb-agent\scripts\setup_reader.ps1
```

The script prompts for the PostgreSQL administrator password, creates or
rotates `meddb_reader`, reapplies least-privilege grants, and updates
`pgpass.conf`.

## Codex Integration

Codex database query execution is temporarily disabled pending redesign.
