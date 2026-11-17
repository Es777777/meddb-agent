SELECT
  a.survey_year,
  COALESCE(NULLIF(BTRIM(a.{{column_a}}), ''), '<missing>') AS value_a,
  COALESCE(NULLIF(BTRIM(b.{{column_b}}), ''), '<missing>') AS value_b,
  COUNT(*)::bigint AS sample_count
FROM brfss.{{table_a}} AS a
JOIN brfss.{{table_b}} AS b
  ON b.id = a.id
WHERE a.survey_year BETWEEN :start_year AND :end_year
GROUP BY
  a.survey_year,
  COALESCE(NULLIF(BTRIM(a.{{column_a}}), ''), '<missing>'),
  COALESCE(NULLIF(BTRIM(b.{{column_b}}), ''), '<missing>')
ORDER BY a.survey_year, sample_count DESC, value_a, value_b

