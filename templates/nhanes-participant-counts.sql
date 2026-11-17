SELECT
  c.cycle_code,
  c.cycle_label,
  p.riagendr AS sex_code,
  COALESCE(p.ridreth3, p.ridreth1) AS race_ethnicity_code,
  COUNT(*)::bigint AS participant_count
FROM nhanes.participants AS p
JOIN nhanes.cycles AS c
  ON c.id = p.cycle_id
GROUP BY
  c.cycle_code,
  c.cycle_label,
  p.riagendr,
  COALESCE(p.ridreth3, p.ridreth1)
ORDER BY c.cycle_code, sex_code, race_ethnicity_code

