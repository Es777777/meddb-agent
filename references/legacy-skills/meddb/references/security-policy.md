# Security Policy

## Defense in Depth

1. PostgreSQL role:
   - `NOSUPERUSER`
   - `NOCREATEDB`
   - `NOCREATEROLE`
   - `NOREPLICATION`
   - `NOBYPASSRLS`
   - `default_transaction_read_only=on`
   - explicit `SELECT` grants only
   - no database `CREATE` or `TEMPORARY` privilege
2. CLI parser:
   - one statement
   - parsed `SELECT` only
   - schema-qualified allowlisted tables
   - blocked DDL/DML/COPY/SET and dangerous functions
   - no `SELECT *` except `COUNT(*)`
3. Execution:
   - explicit read-only transaction
   - statement and lock timeouts
   - `EXPLAIN` cost ceiling before execution
   - aggregate and detail row limits
4. Output:
   - exports confined to the project export root
   - clipboard is local and user initiated
5. Audit:
   - SQL hash, parameters, tables, timing, row count, and output path
   - no returned values or result-body snapshots

## Credentials

- Never put a password in source, JSON, SQL templates, examples, logs, or
  terminal commands shown to the user.
- `setup_reader.ps1` creates a random reader password and stores it in the
  current Windows user's protected PostgreSQL `pgpass.conf`.
- Administrator credentials are accepted only through a secure local prompt
  or a transient environment variable supplied outside committed files.

## Custom SQL

Custom SQL files receive the same parser and database restrictions as built-in
templates. They require confirmation unless `--yes` is explicitly used.
Identifier interpolation is not supported in custom SQL; table and column
names must be fixed and schema-qualified.
