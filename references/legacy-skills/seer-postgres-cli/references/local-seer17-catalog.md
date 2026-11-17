# Local SEER17 catalog snapshot

This reference is derived from `information_schema`, `pg_catalog`, and
`seer17.import_manifest` on 2026-08-15. It documents local facts; it is not a
paper-specific cohort definition or benchmark gold.

## Snapshot

- Database schema: `seer17`
- Import source: SEER Research Data, 17 Registries, Nov 2023 Sub, 2000–2021,
  linked to county attributes.
- Imported rows: 8,687,025; diagnosis-year coverage: 2000–2021.
- Central grain: `seer17.tumors.id` is the tumor-record key;
  `seer17.tumors.patient_id` references `seer17.patients.patient_id`.
- `seer17.tumors.year_of_diagnosis` is **text** in this local import. Compare
  it to quoted strings such as `IN ('2015','2016','2017')`, or cast only after
  validating that the relevant values are numeric.

## One-to-one tumor extensions

Each table below has `id` as its primary key and foreign key to `tumors.id`:

- `tumor_site`: `primary_site`, `primary_site_labeled`, `site_recode_2008`,
  `site_recode_2023`.
- `tumor_histology`: `histologic_type_icd_o_3`, `icd_o_3_hist_behav`,
  `icd_o_3_hist_behav_malignant`, `behavior_code_icd_o_3`,
  `behavior_recode_for_analysis`.
- `stage_summary`: `summary_stage_2000`, `seer_combined_summary_stage_2000`.
- `metastasis`: `mets_bone`, `mets_brain`, `mets_liver`, `mets_lung`.
- `mortality_followup`: `year_of_death_recode`, `vital_status_recode`,
  `survival_months`, `survival_months_flag`.
- `cause_of_death`: `seer_cause_specific_death_classification`,
  `seer_other_cause_death_classification`.
- `socioeconomic`: `median_household_income_2022`,
  `rural_urban_continuum_code`.
- `treatment_surgery`: `surg_prim_site_1998`,
  `reason_no_cancer_directed_surgery`.
- `treatment_radiation`: `radiation_recode`, `surgery_radiation_sequence`.
- `treatment_systemic`: `chemotherapy_recode`,
  `systemic_surgery_sequence_2007`, `time_to_treatment_days_recode`.

## Verified index facts

- `tumors_pkey (id)`; `idx_seer17_tumors_patient_id (patient_id)`;
  `idx_seer17_tumors_year (year_of_diagnosis)`.
- `patients_pkey (patient_id)`.
- `tumor_site_pkey (id)`; `idx_seer17_tumor_site_primary (primary_site)`;
  `idx_seer17_tumor_site_recode2023 (site_recode_2023)`.
- `tumor_histology_pkey (id)`; `idx_seer17_histology_type (histologic_type_icd_o_3)`.
- The listed one-to-one extension tables have primary-key indexes on `id`.
- `stage_summary` has `idx_seer17_stage_summary_combined` only on
  `combined_summary_stage_2004`, not on `summary_stage_2000`.

## Exact observed labels relevant to 2015–2017 lung work

- `tumor_site.site_recode_2008`: `Lung and Bronchus`.
- `tumor_histology.behavior_recode_for_analysis`: `Malignant` and
  `Only malignant in ICD-O-3` appear in the 2015–2017 Lung and Bronchus set.
- `stage_summary.summary_stage_2000`: `Localized`, `Regional`, `Distant`,
  `Unknown/unstaged` (and other labels elsewhere in the snapshot).
- Each `metastasis.mets_*`: usable values are `No`, `Yes`; non-usable values
  include `Unknown`, `N/A`, empty string, and `NULL`.

Use `seer17.variable_dictionary` plus `seer17.value_labels` for fields or
labels not listed here. Use `pg_catalog.pg_constraint` for a relationship that
is not stated here.
