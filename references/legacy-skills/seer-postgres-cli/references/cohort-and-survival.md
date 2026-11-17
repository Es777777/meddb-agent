# Cohort and survival rules

## Grain

- `COUNT(*)` after a tumor-to-extension-table join should equal
  `COUNT(DISTINCT t.id)` because the documented extensions are one-to-one.
- Count unique patients with `COUNT(DISTINCT t.patient_id)`. Do not label a
  tumor count as a patient count.

## 2015–2017 data rules

- Use `t.year_of_diagnosis BETWEEN '2015' AND '2017'` because this local field
  is text. Do not compare it directly to integer literals.
- Use `s.summary_stage_2000` from `seer17.stage_summary s` joined by `s.id=t.id`.
- Use `m.mets_bone`, `m.mets_brain`, `m.mets_liver`, and `m.mets_lung` from
  `seer17.metastasis m` joined by `m.id=t.id`.
- “All four Mets usable” means all four values are in `('No','Yes')`.

## Survival

- Join `mortality_followup` and `cause_of_death` on `id=t.id`.
- Use `survival_months` only with its native `survival_months_flag`; do not
  derive it from diagnosis and death years.
- State the exact cause-specific inclusion rule and labels observed before
  reporting a disease-specific-survival cohort.
- Do not claim a strict reproduction of an external paper when its registry
  set, release, submission, selection, or loss-to-follow-up rule differs.
