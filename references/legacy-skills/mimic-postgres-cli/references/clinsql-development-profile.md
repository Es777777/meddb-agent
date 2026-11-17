# CLINSQL PostgreSQL development profile v4

Apply only when the harness explicitly declares this profile. These conventions
were selected after retrospective analysis of already exposed validation cases.
They are development assumptions, NOT unseen-task evidence, clinical standards,
or an exact implementation of the official BigQuery scorer. Explicit question
requirements override defaults; disclose each default in the observable plan.

## Resolve conflicting defaults before writing SQL

- An explicit age band in the question means `patients.anchor_age` in this
  profile unless the question explicitly requests admission-adjusted age. Do not
  automatically add an admissions join or anchor_year correction. The generic
  MIMIC admission-age formula remains correct for ordinary admission-age work.
- If the question says only "similar patients" and gives one age, the intended
  age band is unresolved. State a proposed band as an assumption; do not invent
  a universal +/-5, exact-age, or decade-binning benchmark rule. Such an item may
  remain unmatched even when the SQL is executable.
- For hospital length of stay or prescription duration requested in days,
  default to calendar-date difference, not elapsed seconds / 86400. Require
  nonnull endpoints and nonnegative duration. Use exact elapsed time if asked.
- Use STDDEV_SAMP for an unspecified sample standard deviation. For scalar
  numerical summaries in this profile, default to ROUND(value::numeric, 2),
  unless the question explicitly specifies a different precision or raw output.
- The fixed local PostgreSQL port convention for quantiles is PERCENTILE_DISC;
  this is not an exact emulation of BigQuery APPROX_QUANTILES. Do not silently
  choose PERCENTILE_CONT. Report this dialect limitation, not a paper score.

## Clinical concepts and data sources

- A medication class name need not occur in the drug-name field. When a
  reviewed medication-class entry is supplied, use its ingredient patterns in
  prescriptions.drug (or the explicitly requested administration source). Do
  not substitute a literal class-name substring or an ICD diagnosis/procedure.
- Nitrates in the supplied development registry are the nitroglycerin and
  isosorbide ingredient families. This is a declared operational name mapping,
  not a comprehensive medication terminology or clinically validated phenotype.
- Dictionary retrieval separates each clinical concept and physical source.
  Examine total_candidates, returned_candidates and truncated. Trim fixed-width
  ICD codes and retain icd_version. A ranked candidate set is not an exhaustive
  phenotype. Do not silently treat all keyword hits as the intended cohort.
- Lab item identification, diagnostic cohort definitions, physiologic range
  filters and "similar" cohort rules are distinct decisions. Finding a platelet
  item ID does not establish sepsis codes, platelet plausibility limits or the
  age band. Without a specified source, expose those unresolved decisions.

## Validation boundary

Check the SQL against the observable plan: age basis, day arithmetic, nonnegative
duration, medication ingredient predicate, sample/population SD, quantile policy
and rounding. Do not inspect this item's reference SQL, CSV or rubric during
generation/repair. No task-ID lookup, expected values or reference SQL templates
belong in this profile. Evaluate this version on declared exposed development
cases first; freeze it before a separately authorized held-out evaluation.
