SELECT
  variable_name,
  original_variable_name,
  component_family,
  domain,
  english_label,
  chinese_label,
  unit,
  first_cycle_id,
  last_cycle_id,
  file_count
FROM nhanes.variable_dictionary
WHERE variable_name ILIKE '%' || :search_text || '%'
   OR original_variable_name ILIKE '%' || :search_text || '%'
   OR english_label ILIKE '%' || :search_text || '%'
   OR chinese_label ILIKE '%' || :search_text || '%'
ORDER BY file_count DESC, variable_name

