SELECT
  domain,
  COUNT(*)::bigint AS component_family_count,
  SUM(file_count)::bigint AS source_file_count
FROM nhanes.components
GROUP BY domain
ORDER BY source_file_count DESC, domain

