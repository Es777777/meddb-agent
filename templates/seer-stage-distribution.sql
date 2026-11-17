WITH cohort AS (
  SELECT
    ss.combined_summary_stage_2004 AS stage_value
  FROM {{schema}}.tumors AS t
  JOIN {{schema}}.stage_summary AS ss
    ON ss.id = t.id
  WHERE BTRIM(t.{{year_column}}::text) ~ '^[0-9]{4}$'
    AND CAST(t.{{year_column}} AS integer) BETWEEN :start_year AND :end_year
)
SELECT
  COALESCE(NULLIF(BTRIM(stage_value), ''), '<missing>') AS stage_value,
  COUNT(*)::bigint AS case_count,
  ROUND(
    COUNT(*)::numeric / NULLIF(SUM(COUNT(*)) OVER (), 0),
    6
  ) AS case_proportion
FROM cohort
GROUP BY COALESCE(NULLIF(BTRIM(stage_value), ''), '<missing>')
ORDER BY case_count DESC, stage_value

