# Dataset Rules

## SEER

- One `tumors` row is one registered tumor case, not necessarily one person.
- Join tumor-level topic tables through `id`.
- Join patient-level information through `patient_id` where present.
- Do not call case counts an incidence rate without a matched population
  denominator and the required SEER statistical method.
- Do not mix AJCC, CS, EOD, Summary Stage, or transition-period fields without
  respecting their applicable years and definitions.
- Observational association does not establish disease causation.

## BRFSS

- One `responses` row is one survey response in one year.
- Join normalized topic tables through `id`.
- Raw sample percentages are not population estimates.
- Formal prevalence estimates require the correct year's weight, PSU, and
  strata variables plus an appropriate complex-survey variance method.
- Questionnaire wording and variable names change over time; check
  `variable_dictionary` and `variable_availability`.
- Associations in cross-sectional self-reported data do not prove causality.

## NHANES

- `SEQN` is the public participant identifier within the source data.
- Internal normalized tables use `id`; participant linkage also uses
  `participant_id`/`cycle_id` according to the schema catalog.
- Components must be joined by participant and cycle semantics, not row order.
- Unweighted counts describe the sample only.
- National estimates require cycle-appropriate weights and correct strata/PSU
  handling; multi-cycle analyses require weight adjustment.
- Laboratory limits, fasting subsamples, and component eligibility must be
  checked before interpretation.

