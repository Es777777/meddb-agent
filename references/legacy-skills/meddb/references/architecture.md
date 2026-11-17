# Architecture

## Runtime Boundary

```text
User's ordinary PowerShell
  -> F:\database\meddb-agent\bin\meddb.cmd
  -> project-local Python 3.10
  -> meddb Python package
  -> PostgreSQL med_research as meddb_reader
```

The ordinary-terminal path keeps all results outside Codex. A separate
`meddb-cli` skill may invoke the same CLI from a `no-mcp` Codex profile using
`meddb_reader`; it exposes only small aggregate output and exports every detail
or oversized result without reading it back into Codex. MCP servers,
connectors, browser tools, and remote services are never part of either path.

## Components

- `src/meddb/cli.py`: commands and interactive terminal menu.
- `src/meddb/security.py`: AST parsing, allowlists, parameter validation.
- `src/meddb/db.py`: read-only transaction, timeout, `EXPLAIN`, row cap.
- `src/meddb/catalog.py`: local schema and bilingual field metadata.
- `src/meddb/templates.py`: manifest-driven template rendering.
- `src/meddb/exporter.py`: table, CSV, TSV, JSON, and clipboard output.
- `src/meddb/audit.py`: process metadata without result content.
- `src/meddb/mcp_guard.py`: static audit that MCP/apps are disabled.
- `templates/`: reviewed parameterized SQL.
- `catalog/catalog.sqlite`: metadata only; no medical record values.
- `audit/audit.jsonl`: local query-process log.
- `exports/`: the only allowed result-export root.

## Data Access

Query schemas:

- `seer8`
- `seer12`
- `seer17`
- `brfss`
- `nhanes`

Metadata-only schema:

- `nhanes_raw`

Tables named `raw`, beginning with `raw_`, or containing `_raw_` are not
queryable by the CLI or `meddb_reader`.
