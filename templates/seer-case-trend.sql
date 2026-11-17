SELECT
  CAST(t.{{year_column}} AS integer) AS diagnosis_year,
  COUNT(*)::bigint AS case_count,
  COUNT(DISTINCT t.patient_id)::bigint AS patient_count
FROM {{schema}}.tumors AS t
JOIN {{schema}}.patients AS p
  ON p.patient_id = t.patient_id
WHERE BTRIM(t.{{year_column}}::text) ~ '^[0-9]{4}$'
  AND CAST(t.{{year_column}} AS integer) BETWEEN :start_year AND :end_year
  AND (:race IS NULL OR p.{{race_column}} = :race)
GROUP BY CAST(t.{{year_column}} AS integer)
ORDER BY diagnosis_year

