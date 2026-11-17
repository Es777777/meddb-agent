# Query output contract

For benchmark execution:

1. Write the transaction to `task.sql` in the isolated workspace with UTF-8
   no-BOM encoding.
2. Include `BEGIN`, repeatable-read/read-only transaction settings,
   `statement_timeout`, `lock_timeout`, all metadata checks and final query,
   then `ROLLBACK`.
3. Run native psql exactly once with `-X -w -v ON_ERROR_STOP=1 -f task.sql`.
4. Copy the exact contents of `task.sql` into the structured response
   `final_sql`; do not reconstruct or abbreviate it.
5. Put only rows actually printed by psql into `result`. State the relevant
   tables/fields and any uncertainty in `metadata_evidence` and `answer`.
