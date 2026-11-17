# MIMIC-IV clinical SQL semantics

Use this reference before generating clinical cohort SQL. A harness must embed
its full frozen contents, together with sql-generation.md, when file tools are
disabled. Record hashes for both. These rules are generic and contain no
CLINSQL task-specific Gold SQL or results.

## Population and analysis unit

Write a short observable query specification (not private chain of thought):
dataset; analysis unit; cohort filters; concept definition/provenance; index
event; window boundaries; denominator/zero policy; statistic; output columns and
units; ambiguity/assumptions. Implement exactly that specification in SQL.

- subject_id identifies a patient; hadm_id an admission; stay_id an ICU stay.
  Repeated hospitalizations are not interchangeable with unique patients.
- Choose a denominator from the question, not a blanket rule. “Among patients
  receiving X” restricts to X recipients. A population-wide burden distribution
  may include zero-event entities. If ambiguous, disclose the choice.
- Preaggregate each event source to the chosen grain before joining outcomes.
  Use EXISTS for diagnosis membership to avoid multiplying events by diagnoses.
- A dictionary condition placed only on a LEFT JOIN does not filter the earlier
  event table. Count the matched dictionary key (or filtered event CTE), not an
  unmatched left event. Composite COUNT(DISTINCT (a,b)) can count (NULL,NULL) as
  one distinct composite; use FILTER (WHERE matched_key IS NOT NULL).
- Summarize comparison cohorts separately before joining their one-row summary
  results. Never CROSS JOIN raw stay populations to compute two group means.

## Age and index admission

The official mimic-code age concept calculates admission age as:

    p.anchor_age + EXTRACT(YEAR FROM a.admittime) - p.anchor_year

Source: F:/database/vendor/mimic-code-official/mimic-iv/concepts_postgres/demographics/age.sql
anchor_age is age at anchor_year, not automatically age at every admission.
Use anchor_age only when the question is explicitly at that reference age or no
admission anchor is appropriate. Do not apply the admission formula indiscriminately.

A declared benchmark profile can instead define reference-age cohorts. Resolve
that precedence explicitly in the observable plan; do not treat an executable
admission-age query as equivalent to an anchor-age reference population.

“First ICU stay per admission” ranks by hadm_id; “first ever ICU stay per patient”
ranks by subject_id. Rank the full eligible history before a filter that would
change which stay is first. Use ORDER BY intime, stay_id for deterministic ties.

## Clinical concept grounding

- Prefer a sourced, versioned code definition over an English-title keyword.
  Record whether a concept is diagnosis-code, billing procedure, bedside ICU
  intervention, laboratory test, medication administration, or derived phenotype.
- ICD-9 and ICD-10 must use separate rules with icd_version. Normalize decimal
  punctuation only when justified by the dictionary format. Join ICD dictionaries
  on both icd_code and icd_version. A diagnosis code family is not a procedure.
- A dictionary title search is candidate discovery, not a complete validated
  phenotype. Do not invent an exhaustive CABG/echo/ACS/sepsis code list from a
  phrase. State a defensible coding definition and its limitations if no source
  is supplied. Do not replace ACS by AMI or Sepsis-3 by diagnosis-coded sepsis.
- Medication class labels often differ from ingredient names. Use a reviewed
  terminology mapping if available, record its scope, and apply it to the
  requested prescription/administration source. A class-label substring alone
  is not a defensible substitute for ingredient discovery.
- If the harness supplies dictionary candidates, use the table, ID, code version,
  label, fluid and linksto metadata to ground an operational definition. The
  candidate list is capped and is not an exhaustive clinical phenotype. When the
  question is an administrative-code cohort, use a clearly stated coding
  definition rather than demanding clinical Sepsis-3 measurements. Reserve
  abstention for a missing definition that changes the requested construct and
  cannot be resolved from the provided metadata; record other reasonable
  assumptions explicitly instead of automatically refusing every ambiguous term.
- Charlson's historical MI definition includes old infarction; it must not be
  copied wholesale as an acute MI cohort. The official sepsis3 concept is a
  derived clinical definition, not synonymous with a title containing “sepsis”.
- Use procedures_icd for coded hospital procedures, hcpcsevents for billing
  events, and procedureevents for timestamped ICU interventions as appropriate.
  COUNT(*), COUNT(DISTINCT icd_code), and COUNT(DISTINCT itemid) measure different
  things. Justify the source and count unit from the requested concept.

## Time and statistics

- procedures_icd.chartdate is a date. Do not claim exact hourly timing from it.
  procedureevents.starttime supports ICU-hour windows; labevents.charttime
  records labs. Preserve the event definition when resolving temporal limits.
- Specify whether the window ends at ICU discharge and whether its right edge
  is inclusive. Do not silently change an explicit question boundary.
- PostgreSQL percentile_cont interpolates; percentile_disc returns an observed
  value. BigQuery APPROX_QUANTILES is neither exactly equivalent. If replicating
  a BigQuery task, declare a fixed dialect policy; do not call PostgreSQL
  percentile_disc an exact APPROX_QUANTILES implementation.
- IQR usually means Q3-Q1; a pair [Q1,Q3] is a different output. SD can be sample
  or population SD. Mortality fraction and mortality percent differ by 100.
- Return all requested summary statistics and no unrelated summary columns.
  Use explicit output units and concise aliases. Gold aliases are not available
  to the generator; alias-sensitive and value-only metrics must be separate.
- Cast named parameters in polymorphic contexts: CAST(:prefix AS text),
  CAST(:n AS integer), CAST(:hours AS integer). Do not interpolate values.

## Self-audit before returning

Check each clinical and statistical clause in the question against the visible
specification and SQL. Check code provenance, age anchor, one row per analysis
unit, denominator, first-stay partition, exact window, count unit, percentile
definition, percent units, output shape, and join multiplication. The audit may
use the question/schema/definitions and EXPLAIN, never held-out Gold artifacts.
