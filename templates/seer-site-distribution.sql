WITH cohort AS (
  SELECT
    s.{{site_column}} AS tumor_site
  FROM {{schema}}.tumors AS t
  JOIN {{schema}}.patients AS p
    ON p.patient_id = t.patient_id
  JOIN {{schema}}.{{site_table}} AS s
    ON s.id = t.id
  WHERE BTRIM(t.{{year_column}}::text) ~ '^[0-9]{4}$'
    AND CAST(t.{{year_column}} AS integer) BETWEEN :start_year AND :end_year
    AND (:race IS NULL OR p.{{race_column}} = :race)
)
SELECT
  COALESCE(NULLIF(BTRIM(tumor_site), ''), '<missing>') AS tumor_site,
  COUNT(*)::bigint AS case_count,
  ROUND(
    COUNT(*)::numeric / NULLIF(SUM(COUNT(*)) OVER (), 0),
    6
  ) AS case_proportion
FROM cohort
GROUP BY COALESCE(NULLIF(BTRIM(tumor_site), ''), '<missing>')
ORDER BY case_count DESC, tumor_site

