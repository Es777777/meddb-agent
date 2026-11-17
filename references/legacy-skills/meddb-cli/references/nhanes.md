# NHANES

## Scope and joins

Schema `nhanes` contains normalized public analysis tables. Schema `nhanes_raw`
is metadata-only and may not be queried for participant data.

- `SEQN` is the public participant identifier in source files.
- Normalized tables use `id`, `participant_id`, and `cycle_id` according to the catalog.
- Join components by participant and cycle semantics, never by row order.

## Survey and component rules

- Unweighted counts and percentages describe the sample only.
- National estimates require cycle-appropriate weights, strata, and PSU variables.
- Multi-cycle estimates require documented weight adjustment.
- Check examination, laboratory, questionnaire, fasting, and subsample eligibility.
- Check laboratory detection limits and special missing-value codes.
- Do not combine components until the relevant cycles and eligibility overlap.

## Reviewed templates

- `nhanes-participant-counts`
- `nhanes-demographic-distribution`
- `nhanes-component-inventory`
- `nhanes-variable-search`

The first two are sample descriptions unless an appropriate weighted workflow is
explicitly designed and validated.
