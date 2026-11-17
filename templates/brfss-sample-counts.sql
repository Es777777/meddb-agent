SELECT
  survey_year,
  COALESCE(NULLIF(BTRIM(state_code), ''), '<missing>') AS state_code,
  COUNT(*)::bigint AS response_count
FROM brfss.responses
WHERE survey_year BETWEEN :start_year AND :end_year
  AND (:state_code IS NULL OR state_code = :state_code)
GROUP BY survey_year, COALESCE(NULLIF(BTRIM(state_code), ''), '<missing>')
ORDER BY survey_year, state_code

