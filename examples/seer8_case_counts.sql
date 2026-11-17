SELECT
  t.diagnosis_year,
  COUNT(*)::bigint AS case_count
FROM seer8.tumors AS t
WHERE t.diagnosis_year BETWEEN :start_year AND :end_year
GROUP BY t.diagnosis_year
ORDER BY t.diagnosis_year

