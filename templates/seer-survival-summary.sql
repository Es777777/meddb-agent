WITH cohort AS (
  SELECT
    CAST(t.{{year_column}} AS integer) AS diagnosis_year,
    CASE
      WHEN BTRIM(o.survival_months) ~ '^[0-9]+([.][0-9]+)?$'
      THEN CAST(o.survival_months AS numeric)
      ELSE NULL
    END AS survival_months,
    COALESCE(NULLIF(BTRIM(o.{{vital_column}}), ''), '<missing>') AS vital_status
  FROM {{schema}}.tumors AS t
  JOIN {{schema}}.{{outcomes_table}} AS o
    ON o.id = t.id
  WHERE BTRIM(t.{{year_column}}::text) ~ '^[0-9]{4}$'
    AND CAST(t.{{year_column}} AS integer) BETWEEN :start_year AND :end_year
)
SELECT
  diagnosis_year,
  vital_status,
  COUNT(*)::bigint AS case_count,
  COUNT(survival_months)::bigint AS valid_survival_count,
  ROUND(AVG(survival_months), 2) AS mean_survival_months,
  MIN(survival_months) AS min_survival_months,
  MAX(survival_months) AS max_survival_months
FROM cohort
GROUP BY diagnosis_year, vital_status
ORDER BY diagnosis_year, vital_status

