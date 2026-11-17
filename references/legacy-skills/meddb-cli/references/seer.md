# SEER

## Scope

Schemas `seer8`, `seer12`, and `seer17` contain related but non-identical SEER
releases. Inspect the catalog before selecting fields; never assume a column name
is shared across releases.

## Unit and joins

- One `tumors` row is one registered tumor case, not necessarily one person.
- Join tumor-level topic tables through `id`.
- Join patient-level information through `patient_id` when the catalog confirms it.
- State whether a count represents tumors, distinct patients, or another unit.

## Required cautions

- Do not call case counts or case composition an incidence rate without a matched
  population denominator and the required SEER statistical method.
- Do not mix AJCC, CS, EOD, Summary Stage, or transition-period fields across
  inapplicable years.
- Descriptive survival summaries do not replace Kaplan-Meier, competing-risk,
  or relative-survival analysis.
- Observational associations do not establish causation.

## Reviewed templates

- `seer-case-trend`
- `seer-site-distribution`
- `seer-stage-distribution`
- `seer-survival-summary`

Prefer these templates before generating custom SQL.
