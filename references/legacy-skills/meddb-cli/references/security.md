# Security

## Enforced identity

All query execution must use `meddb_reader`, which is PostgreSQL read-only and
cannot create databases, roles, schemas, temporary objects, or bypass RLS.
`meddb_superadmin` is never valid for this skill.

## Enforced SQL boundary

- One parsed read-only statement.
- Schema-qualified allowlisted tables.
- Raw-record table patterns blocked.
- Aggregate mode by default.
- Detail mode explicit and capped at 100 rows.
- Statement and lock timeouts.
- EXPLAIN cost ceiling before execution.
- Audit metadata without result content.

## Integration status

Codex query execution is temporarily disabled pending redesign. Never
compensate by invoking another database interface or a higher-privilege role.
