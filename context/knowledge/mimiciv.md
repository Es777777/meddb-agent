# MIMIC-IV descriptive research contract — v1

Scope: descriptive aggregate SQL only. Database release 3.1 is a declared
benchmark requirement; schema names alone do not verify the installed release.
Supported schemas: mimiciv_hosp and mimiciv_icu. No inferred derived concepts.

## Analysis unit and joins

- subject_id is a patient, hadm_id is an admission, stay_id is an ICU stay.
- Declare the unit, denominator and zero-event policy before SQL.
- Preaggregate event tables to that unit before joining sources. Prefer EXISTS
  for diagnosis membership. Check one-to-many joins for duplicate multiplication.
- First ICU stay per patient and first stay per admission are different.
  Rank the eligible history before restrictions that would change what is first;
  break ties deterministically with intime and stay_id.
- Include only requested aggregate outputs; never return patient IDs, exact
  patient timestamps, notes, individual measurements or row-level clinical data.

## Clinical definitions

- Dictionary keyword search discovers candidates; it does not validate a phenotype.
- ICD dictionaries join on code and icd_version; ICD-9 and ICD-10 are separate.
- Diagnosis-coded sepsis is not Sepsis-3. Old MI is not necessarily acute MI.
- Distinguish prescription, dispensing and administration when defining exposure.
- Use sourced, versioned code sets. If unavailable, state the operational
  definition and limitations; request clarification if it changes the construct.
- Native anchor_age is age at anchor_year. Admission age, when appropriate, is
  anchor_age + admission calendar year - anchor_year. Do not substitute one age
  definition for the other without stating the assumption.

## Time and statistics

- Declare index event, window boundaries, inclusion of endpoints and discharge cap.
- A date-only procedure field cannot establish an exact hourly window.
- Distinguish percentages from fractions, sample from population SD, and IQR
  width from the pair of quartiles. Specify units and output aliases.
- PostgreSQL percentile_cont interpolates; percentile_disc returns an observed
  value. Neither is identical to BigQuery APPROX_QUANTILES. Declare the dialect
  limitation rather than claiming exact upstream equivalence.
- Use named scalar parameters and explicit casts for ambiguous parameter types.
- Empty, truncated, missing or failed results cannot support substantive conclusions.
- SQL execution success does not establish clinical or statistical validity.
- Observational association does not establish causation. This release does not
  implement regression adjustment, survival models, prediction or causal estimation.

## Provenance

Derived from this repository's preserved MIMIC clinical-semantics and SQL-generation
references, without benchmark-specific answers or development examples.
Reference concepts: https://github.com/MIT-LCP/mimic-code/tree/main/mimic-iv/concepts_postgres
Data release: https://physionet.org/content/mimiciv/3.1/
The preserved legacy references remain historical sources, not executable Skills.
