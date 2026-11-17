# BRFSS

## Scope and unit

Schema `brfss` stores normalized annual survey responses. One `responses` row is
one response in one survey year. Join normalized topic tables through `id` only
after checking the catalog.

## Survey design

- Raw counts and percentages describe the sample, not a state or national population.
- Formal prevalence estimates require the correct year's weight, PSU, and strata.
- Variance and confidence intervals require a complex-survey method; a weighted
  SQL percentage alone is not a complete survey estimate.
- Questionnaire wording, variable names, and coding change across years. Check
  `variable_dictionary` and `variable_availability` before a multi-year query.
- Treat refusal, unknown, and missing codes according to the year-specific dictionary.
- Cross-sectional self-reported associations do not establish causation.

## Reviewed templates

- `brfss-sample-counts`
- `brfss-weighted-distribution`
- `brfss-cross-tab`

Label every output as sample-only, weighted descriptive, or formal survey
estimate. Do not imply the last category unless design-based variance is handled.
