WITH prepared AS (
  SELECT
    v.survey_year,
    COALESCE(NULLIF(BTRIM(v.{{variable_column}}), ''), '<missing>') AS value_code,
    CASE
      WHEN BTRIM(d.{{weight_column}}) ~ '^[+-]?[0-9]+([.][0-9]+)?$'
      THEN CAST(d.{{weight_column}} AS numeric)
      ELSE NULL
    END AS survey_weight
  FROM brfss.{{topic_table}} AS v
  JOIN brfss.survey_design AS d
    ON d.id = v.id
  WHERE v.survey_year BETWEEN :start_year AND :end_year
),
grouped AS (
  SELECT
    survey_year,
    value_code,
    COUNT(*)::bigint AS sample_count,
    COUNT(survey_weight)::bigint AS valid_weight_count,
    SUM(survey_weight) AS weighted_total
  FROM prepared
  GROUP BY survey_year, value_code
)
SELECT
  survey_year,
  value_code,
  sample_count,
  valid_weight_count,
  ROUND(weighted_total, 4) AS weighted_total,
  ROUND(
    weighted_total / NULLIF(SUM(weighted_total) OVER (PARTITION BY survey_year), 0),
    6
  ) AS weighted_proportion
FROM grouped
ORDER BY survey_year, weighted_total DESC NULLS LAST, value_code

