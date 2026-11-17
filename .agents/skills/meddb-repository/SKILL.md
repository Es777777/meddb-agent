---
name: meddb-repository
description: Maintain or inspect this repository's read-only medical database CLI, templates, tests and architecture. Does not automatically query medical databases or install legacy Skills.
---

# Repository-local MedDB maintenance

Resolve the repository root from this Skill location, not from historical F:/database paths. Read README.md and docs/ARCHITECTURE.md for routing. The only CLI runtime is src/meddb; `python scripts/run_meddb.py --help` delegates to it.

Use `python scripts/run_checks.py` for offline validation. No PostgreSQL connection or model calls belong in these checks. The tests use synthetic metadata only.

Before any authorized database query, confirm the target configuration, schema and read-only user. Preserve single parsed SELECT, schema-qualified tables, aggregate-first output, transaction read-only mode and row caps. Do not expose clinical rows or credentials to the model. Do not change roles or authentication as part of a query.

For historical benchmark work, read evals/README.md. Never load held-out Gold into generation or repair. Keep execution rate, Gold value matching and paired-database agreement separate.

references/legacy-skills contains preserved historical instructions, some disabled and some tied to another machine. Do not automatically activate, install or follow their absolute-path commands. This repository is not a native Pi SDK integration or an installable Codex plugin.
