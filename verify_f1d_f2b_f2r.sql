-- =====================================================================
-- Verification checks for F1D, F2B and F2R (DuckDB)
-- Run after: F1 (re-projection), F1D, F2B, F2R, in that order.
--   duckdb -readonly <path-to-graph.duckdb> -f verify_f1d_f2b_f2r.sql
-- Each check says what to expect. Anything else is worth investigating.
-- =====================================================================
.mode box

-- ---------------------------------------------------------------------
-- F1D: source tables
-- ---------------------------------------------------------------------

-- 1. Source tables per system. Expect ARC1, ARC2, ARC3 and COMPASS with
--    system_type pas, other included systems with their own type, and
--    no ATOS rows (ATOS has no DMS source tables).
SELECT json_extract_string(attrs, '$.system') AS system, json_extract_string(attrs, '$.system_type') AS system_type,
       json_extract_string(attrs, '$.flow') AS flow, count(*) AS tables
FROM graph.node
WHERE node_type = 'table' AND layer = 'source'
GROUP BY ALL ORDER BY system, flow;

-- 2. Every source table uses the src. key pattern. Expect 0.
SELECT count(*) AS source_tables_with_wrong_key
FROM graph.node
WHERE layer = 'source' AND NOT starts_with(node_key, 'src.');

-- 3. No old pas_mssql keys left anywhere. Expect 0 and 0.
SELECT
  (SELECT count(*) FROM graph.node WHERE starts_with(node_key, 'pas_mssql.')) AS old_nodes,
  (SELECT count(*) FROM graph.edge WHERE starts_with(src_key, 'pas_mssql.')
                                      OR starts_with(dst_key, 'pas_mssql.')) AS old_edges;

-- 4. Composer tables appear at most once per ARC instance. Expect no rows.
SELECT json_extract_string(attrs, '$.product_table') AS product_table,
       json_extract_string(attrs, '$.system') AS system, count(*) AS n
FROM graph.node
WHERE json_extract_string(attrs, '$.product') = 'Composer' AND layer = 'source'
GROUP BY ALL HAVING count(*) > 1;

-- ---------------------------------------------------------------------
-- F2B: source_equivalent, distill_kind, landing locations
-- ---------------------------------------------------------------------

-- 5. Distill tables by kind and link method. Compare with the naming
--    report: about 43% naming_convention, 4% name_match.
SELECT json_extract_string(attrs, '$.distill_kind') AS distill_kind,
       json_extract_string(attrs, '$.source_equivalent_method') AS method, count(*) AS tables
FROM graph.node
WHERE layer = 'distill' AND node_type = 'table'
GROUP BY ALL ORDER BY tables DESC;

-- 6. Every source_equivalent points to a real source table. Expect 0.
SELECT count(*) AS dangling_source_equivalent
FROM graph.node d
WHERE d.layer = 'distill' AND d.node_type = 'table'
  AND json_extract_string(d.attrs, '$.source_equivalent') IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM graph.node s
                  WHERE s.node_key = json_extract_string(d.attrs, '$.source_equivalent'));

-- 7. No link crosses systems. Expect 0.
SELECT count(*) AS cross_system_links
FROM graph.node d
JOIN graph.node s ON s.node_key = json_extract_string(d.attrs, '$.source_equivalent')
WHERE d.layer = 'distill' AND d.node_type = 'table'
  AND json_extract_string(d.attrs, '$.system') IS DISTINCT FROM json_extract_string(s.attrs, '$.system');

-- 8. Only replicas have a source_equivalent. Expect no rows.
SELECT json_extract_string(attrs, '$.distill_kind') AS distill_kind, count(*) AS n
FROM graph.node
WHERE layer = 'distill' AND node_type = 'table'
  AND json_extract_string(attrs, '$.source_equivalent') IS NOT NULL
  AND json_extract_string(attrs, '$.distill_kind') <> 'replica'
GROUP BY ALL;

-- 9. ATOS: Distill tables tagged, none with a source_equivalent, and
--    landing locations present. Expect atos_with_source_equivalent = 0
--    and landing_locations >= 1.
SELECT
  (SELECT count(*) FROM graph.node WHERE layer = 'distill' AND json_extract_string(attrs, '$.system') = 'ATOS') AS atos_distill_tables,
  (SELECT count(*) FROM graph.node WHERE json_extract_string(attrs, '$.system') = 'ATOS'
                                     AND json_extract_string(attrs, '$.source_equivalent') IS NOT NULL) AS atos_with_source_equivalent,
  (SELECT count(*) FROM graph.node WHERE json_extract_string(attrs, '$.system') = 'ATOS'
                                     AND json_extract_string(attrs, '$.ingestion') = 'file' AND layer = 'raw') AS landing_locations;

-- ---------------------------------------------------------------------
-- F2R: keys and relationships
-- ---------------------------------------------------------------------

-- 10. Primary key coverage on Distill tables.
SELECT count(*) FILTER (WHERE json_extract_string(attrs, '$.primary_key') IS NOT NULL) AS with_primary_key,
       count(*) FILTER (WHERE json_extract_string(attrs, '$.key_discriminators') IS NOT NULL) AS with_discriminators,
       count(*) AS distill_tables
FROM graph.node
WHERE layer = 'distill' AND node_type = 'table';

-- 11. Relationships by origin and tier.
SELECT origin, tier, count(*) AS edges
FROM graph.edge
WHERE edge_type = 'references' AND json_extract_string(attrs, '$.flow') = 'F2R'
GROUP BY ALL ORDER BY edges DESC;

-- 12. No name-match relationship crosses systems. Expect 0.
SELECT count(*) AS cross_system_references
FROM graph.edge e
JOIN graph.node sc ON sc.node_key = e.src_key
JOIN graph.node st ON st.node_key = sc.parent_key
JOIN graph.node dc ON dc.node_key = e.dst_key
JOIN graph.node dt ON dt.node_key = dc.parent_key
WHERE e.edge_type = 'references' AND e.origin = 'name_match'
  AND json_extract_string(st.attrs, '$.system') IS DISTINCT FROM json_extract_string(dt.attrs, '$.system');

-- 13. Most-referenced key columns. Eyeball these: a column with hundreds
--     of inbound edges usually means a generic name slipped through.
SELECT dst_key, count(*) AS inbound
FROM graph.edge
WHERE edge_type = 'references' AND json_extract_string(attrs, '$.flow') = 'F2R'
GROUP BY 1 ORDER BY inbound DESC LIMIT 15;

-- 14. Duplicate relationships. Expect 0.
SELECT count(*) - count(DISTINCT src_key || '>' || dst_key) AS duplicate_references
FROM graph.edge
WHERE edge_type = 'references';
