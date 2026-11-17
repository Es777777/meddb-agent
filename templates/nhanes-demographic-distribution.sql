SELECT
  c.cycle_code,
  c.cycle_label,
  p.{{demographic_column}} AS value_code,
  COUNT(*)::bigint AS participant_count
FROM nhanes.participants AS p
JOIN nhanes.cycles AS c
  ON c.id = p.cycle_id
GROUP BY c.cycle_code, c.cycle_label, p.{{demographic_column}}
ORDER BY c.cycle_code, participant_count DESC, value_code

