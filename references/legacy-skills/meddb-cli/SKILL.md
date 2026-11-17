---
name: meddb-cli
description: Temporarily disabled MedDB CLI integration for SEER, BRFSS, and NHANES. Use when a user asks about the former Codex medical database query workflow; explain that execution is paused pending redesign. Never use MCP, connectors, psql, or meddb_superadmin.
---

# MedDB CLI

## Status

The Codex query integration is temporarily disabled pending redesign.

- Do not execute `invoke_meddb.ps1`.
- Do not query SEER, BRFSS, or NHANES from Codex.
- Do not use MCP, connectors, apps, browser tools, `psql`, PostgreSQL drivers,
  or `meddb_superadmin` as a substitute.
- Route maintenance work to the separate `meddb` skill.
- Tell the user that the integration must be redesigned before Codex query
  execution can resume.
