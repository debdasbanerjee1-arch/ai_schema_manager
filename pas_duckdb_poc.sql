-- =====================================================================
-- PAS metadata: DuckDB data model and one-time load (PoC)
-- Scope: user tables, columns, keys, foreign keys and descriptions.
--
-- Run with the DuckDB CLI:
--   duckdb knowledge_poc.duckdb -f pas_duckdb_poc.sql
-- Edit the three variables below first. The script is re-runnable:
-- it reloads the raw tables and replaces the PAS part of the graph.
--
-- Two layers:
--   pas_raw  the five extract files as loaded, one table per file
--   graph    the Source layer as nodes and edges, built from pas_raw
-- =====================================================================

SET VARIABLE pas_db  = 'PAS';               -- PAS database name, used in node keys
SET VARIABLE pas_dir = 'C:/pas_extract/';   -- folder holding the .txt files (trailing slash)
SET VARIABLE pas_enc = 'utf-8';             -- 'utf-16' if SSMS saved the files as Unicode

CREATE SCHEMA IF NOT EXISTS pas_raw;
CREATE SCHEMA IF NOT EXISTS graph;

-- Reverses the escaping applied in the extraction SQL (\\ \t \r \n).
CREATE OR REPLACE MACRO pas_unescape(s) AS
  replace(replace(replace(replace(replace(s, '\\', chr(1)), '\t', chr(9)), '\r', chr(13)), '\n', chr(10)), chr(1), '\');

-- Renders a SQL Server type with its length, precision or scale.
CREATE OR REPLACE MACRO pas_render_type(base_type, max_length, prec, scl) AS
  CASE
    WHEN base_type IN ('varchar', 'char', 'varbinary', 'binary')
      THEN format('{}({})', base_type, CASE WHEN max_length = -1 THEN 'max' ELSE CAST(max_length AS VARCHAR) END)
    WHEN base_type IN ('nvarchar', 'nchar')
      THEN format('{}({})', base_type, CASE WHEN max_length = -1 THEN 'max' ELSE CAST(max_length // 2 AS VARCHAR) END)
    WHEN base_type IN ('decimal', 'numeric')
      THEN format('{}({},{})', base_type, prec, scl)
    WHEN base_type IN ('datetime2', 'time', 'datetimeoffset')
      THEN format('{}({})', base_type, scl)
    ELSE base_type
  END;

-- ---------------------------------------------------------------------
-- pas_raw: one table per extract file
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE pas_raw.tables (
  object_id    INTEGER NOT NULL,
  schema_name  VARCHAR NOT NULL,
  table_name   VARCHAR NOT NULL,
  create_date  TIMESTAMP,
  modify_date  TIMESTAMP,
  row_count    BIGINT,
  PRIMARY KEY (schema_name, table_name)
);

CREATE OR REPLACE TABLE pas_raw.columns (
  object_id           INTEGER NOT NULL,
  schema_name         VARCHAR NOT NULL,
  table_name          VARCHAR NOT NULL,
  column_id           INTEGER NOT NULL,
  column_name         VARCHAR NOT NULL,
  data_type           VARCHAR NOT NULL,   -- user type name if a user-defined type
  base_type           VARCHAR,
  max_length          SMALLINT,           -- bytes; -1 means (max)
  precision           SMALLINT,
  scale               SMALLINT,
  is_nullable         BOOLEAN NOT NULL,
  is_identity         BOOLEAN NOT NULL,
  is_computed         BOOLEAN NOT NULL,
  collation_name      VARCHAR,
  default_definition  VARCHAR,
  computed_definition VARCHAR,
  PRIMARY KEY (schema_name, table_name, column_name)
);

CREATE OR REPLACE TABLE pas_raw.keys (
  object_id         INTEGER NOT NULL,
  schema_name       VARCHAR NOT NULL,
  table_name        VARCHAR NOT NULL,
  key_name          VARCHAR NOT NULL,
  key_type          VARCHAR NOT NULL,     -- PRIMARY, UNIQUE_CONSTRAINT, UNIQUE_INDEX
  key_ordinal       SMALLINT NOT NULL,
  column_name       VARCHAR NOT NULL,
  filter_definition VARCHAR,
  PRIMARY KEY (schema_name, table_name, key_name, key_ordinal)
);

CREATE OR REPLACE TABLE pas_raw.foreign_keys (
  fk_object_id      INTEGER NOT NULL,
  fk_name           VARCHAR NOT NULL,
  parent_schema     VARCHAR NOT NULL,
  parent_table      VARCHAR NOT NULL,
  parent_column     VARCHAR NOT NULL,
  referenced_schema VARCHAR NOT NULL,
  referenced_table  VARCHAR NOT NULL,
  referenced_column VARCHAR NOT NULL,
  ordinal           SMALLINT NOT NULL,
  is_disabled       BOOLEAN NOT NULL,
  is_not_trusted    BOOLEAN NOT NULL,
  PRIMARY KEY (parent_schema, fk_name, ordinal)
);

CREATE OR REPLACE TABLE pas_raw.descriptions (
  object_id   INTEGER NOT NULL,
  schema_name VARCHAR NOT NULL,
  table_name  VARCHAR NOT NULL,
  column_name VARCHAR,                    -- NULL for a table description
  description VARCHAR NOT NULL
);

-- ---------------------------------------------------------------------
-- Load the files. Every file is read as text, then cast on insert.
-- ---------------------------------------------------------------------
INSERT INTO pas_raw.tables BY NAME
SELECT * FROM read_csv(getvariable('pas_dir') || 'tables.txt',
  delim = '\t', quote = '', escape = '', header = true, nullstr = 'NULL',
  all_varchar = true, encoding = getvariable('pas_enc'));

INSERT INTO pas_raw.columns BY NAME
SELECT * REPLACE (pas_unescape(default_definition) AS default_definition,
                  pas_unescape(computed_definition) AS computed_definition)
FROM read_csv(getvariable('pas_dir') || 'columns.txt',
  delim = '\t', quote = '', escape = '', header = true, nullstr = 'NULL',
  all_varchar = true, encoding = getvariable('pas_enc'));

INSERT INTO pas_raw.keys BY NAME
SELECT * REPLACE (pas_unescape(filter_definition) AS filter_definition)
FROM read_csv(getvariable('pas_dir') || 'keys.txt',
  delim = '\t', quote = '', escape = '', header = true, nullstr = 'NULL',
  all_varchar = true, encoding = getvariable('pas_enc'));

INSERT INTO pas_raw.foreign_keys BY NAME
SELECT * FROM read_csv(getvariable('pas_dir') || 'foreign_keys.txt',
  delim = '\t', quote = '', escape = '', header = true, nullstr = 'NULL',
  all_varchar = true, encoding = getvariable('pas_enc'));

INSERT INTO pas_raw.descriptions BY NAME
SELECT * REPLACE (pas_unescape(description) AS description)
FROM read_csv(getvariable('pas_dir') || 'descriptions.txt',
  delim = '\t', quote = '', escape = '', header = true, nullstr = 'NULL',
  all_varchar = true, encoding = getvariable('pas_enc'));

-- ---------------------------------------------------------------------
-- graph: nodes and edges (shared by every flow; the PAS load owns the
-- rows whose node_key starts with pas_mssql.<database>.)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS graph.node (
  node_key    VARCHAR PRIMARY KEY,        -- pas_mssql.<db>.<schema>.<table>[.<column>]
  node_type   VARCHAR NOT NULL CHECK (node_type IN (
                'table', 'column', 'dashboard', 'chart', 'data_model',
                'pipeline', 'pipeline_task', 'glue_job', 'lambda_function',
                'dms_task', 'api_endpoint', 'extract', 'glossary_term')),
  layer       VARCHAR NOT NULL DEFAULT 'none' CHECK (layer IN (
                'source', 'raw', 'distill', 'published', 'consumer', 'none')),
  parent_key  VARCHAR,                    -- a column's table
  name        VARCHAR NOT NULL,
  data_type   VARCHAR,                    -- columns only, e.g. nvarchar(50)
  attrs       JSON NOT NULL DEFAULT '{}',
  deleted     BOOLEAN NOT NULL DEFAULT false,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE SEQUENCE IF NOT EXISTS graph.edge_seq;
CREATE TABLE IF NOT EXISTS graph.edge (
  edge_id         BIGINT PRIMARY KEY DEFAULT nextval('graph.edge_seq'),
  src_key         VARCHAR NOT NULL,
  dst_key         VARCHAR NOT NULL,
  edge_type       VARCHAR NOT NULL CHECK (edge_type IN (
                    'contains', 'references', 'derives_from',
                    'reads', 'writes', 'depends_on', 'consumes', 'means', 'related_term')),
  transform_class VARCHAR CHECK (transform_class IN (
                    'identity', 'meaning_preserving', 'derivation', 'unclassified')),
  expression      VARCHAR,
  origin          VARCHAR NOT NULL,       -- connector, name_match, dms_mapping, parsed, declared, manual, inherited
  tier            VARCHAR NOT NULL CHECK (tier IN ('certified', 'proposed', 'inferred')),
  job_key         VARCHAR,
  attrs           JSON NOT NULL DEFAULT '{}',
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
  CHECK (src_key <> dst_key),
  CHECK (edge_type <> 'derives_from' OR transform_class IS NOT NULL)
);

-- Clear any previous PAS load.
DELETE FROM graph.edge WHERE json_extract_string(attrs, '$.flow') = 'F1';
DELETE FROM graph.node WHERE starts_with(node_key, 'pas_mssql.' || getvariable('pas_db') || '.');

-- Table nodes
INSERT INTO graph.node (node_key, node_type, layer, name, attrs)
SELECT 'pas_mssql.' || getvariable('pas_db') || '.' || t.schema_name || '.' || t.table_name,
       'table', 'source', t.table_name,
       json_object(
         'schema', t.schema_name,
         'object_id', t.object_id,
         'row_count', t.row_count,
         'create_date', t.create_date,
         'modify_date', t.modify_date,
         'description', d.description,
         'primary_key', pk.cols,
         'unique_keys', uk.keys)
FROM pas_raw.tables AS t
LEFT JOIN pas_raw.descriptions AS d
       ON d.schema_name = t.schema_name AND d.table_name = t.table_name AND d.column_name IS NULL
LEFT JOIN (SELECT schema_name, table_name, list(column_name ORDER BY key_ordinal) AS cols
           FROM pas_raw.keys WHERE key_type = 'PRIMARY'
           GROUP BY ALL) AS pk
       ON pk.schema_name = t.schema_name AND pk.table_name = t.table_name
LEFT JOIN (SELECT schema_name, table_name,
                  list({'name': key_name, 'type': key_type, 'columns': cols, 'filter': filter_definition}) AS keys
           FROM (SELECT schema_name, table_name, key_name, key_type, filter_definition,
                        list(column_name ORDER BY key_ordinal) AS cols
                 FROM pas_raw.keys WHERE key_type <> 'PRIMARY'
                 GROUP BY ALL)
           GROUP BY ALL) AS uk
       ON uk.schema_name = t.schema_name AND uk.table_name = t.table_name;

-- Column nodes
INSERT INTO graph.node (node_key, node_type, layer, parent_key, name, data_type, attrs)
SELECT 'pas_mssql.' || getvariable('pas_db') || '.' || c.schema_name || '.' || c.table_name || '.' || c.column_name,
       'column', 'source',
       'pas_mssql.' || getvariable('pas_db') || '.' || c.schema_name || '.' || c.table_name,
       c.column_name,
       pas_render_type(coalesce(c.base_type, c.data_type), c.max_length, c.precision, c.scale),
       json_object(
         'column_id', c.column_id,
         'user_type', CASE WHEN c.base_type IS DISTINCT FROM c.data_type THEN c.data_type END,
         'is_nullable', c.is_nullable,
         'is_identity', c.is_identity,
         'is_computed', c.is_computed,
         'collation', c.collation_name,
         'default', c.default_definition,
         'computed_definition', c.computed_definition,
         'pk_ordinal', k.key_ordinal,
         'description', d.description)
FROM pas_raw.columns AS c
LEFT JOIN pas_raw.keys AS k
       ON k.schema_name = c.schema_name AND k.table_name = c.table_name
      AND k.column_name = c.column_name AND k.key_type = 'PRIMARY'
LEFT JOIN pas_raw.descriptions AS d
       ON d.schema_name = c.schema_name AND d.table_name = c.table_name AND d.column_name = c.column_name;

-- contains: table to each of its columns
INSERT INTO graph.edge (src_key, dst_key, edge_type, origin, tier, attrs)
SELECT parent_key, node_key, 'contains', 'connector', 'certified', '{"flow": "F1"}'
FROM graph.node
WHERE node_type = 'column' AND starts_with(node_key, 'pas_mssql.' || getvariable('pas_db') || '.');

-- references (declared): foreign key column to the column it references
INSERT INTO graph.edge (src_key, dst_key, edge_type, origin, tier, attrs)
SELECT 'pas_mssql.' || getvariable('pas_db') || '.' || f.parent_schema || '.' || f.parent_table || '.' || f.parent_column,
       'pas_mssql.' || getvariable('pas_db') || '.' || f.referenced_schema || '.' || f.referenced_table || '.' || f.referenced_column,
       'references', 'connector',
       CASE WHEN f.is_not_trusted OR f.is_disabled THEN 'inferred' ELSE 'certified' END,
       json_object('flow', 'F1', 'method', 'declared_fk', 'fk_name', f.fk_name, 'ordinal', f.ordinal,
                   'is_disabled', f.is_disabled, 'is_not_trusted', f.is_not_trusted)
FROM pas_raw.foreign_keys AS f;

-- references (inferred, optional): vendor databases often declare few
-- foreign keys. A column is linked to another table's primary key when:
--   - the target table has a single-column primary key
--   - that key name is the primary key of exactly one table
--   - the names match (case-insensitive) and the base types match
--   - the name is longer than 3 characters and not a generic name
--   - no declared foreign key already covers the column
-- Tier is 'inferred'. Delete this block to keep declared keys only.
INSERT INTO graph.edge (src_key, dst_key, edge_type, origin, tier, attrs)
WITH single_pk AS (
  SELECT schema_name, table_name, any_value(column_name) AS column_name
  FROM pas_raw.keys WHERE key_type = 'PRIMARY'
  GROUP BY schema_name, table_name
  HAVING count(*) = 1
),
unique_pk AS (
  SELECT p.*, c.base_type
  FROM single_pk AS p
  JOIN pas_raw.columns AS c USING (schema_name, table_name, column_name)
  WHERE lower(p.column_name) IN (SELECT lower(column_name) FROM single_pk
                                 GROUP BY lower(column_name) HAVING count(*) = 1)
    AND length(p.column_name) > 3
    AND upper(p.column_name) NOT IN ('CODE', 'TYPE', 'STATUS', 'NAME', 'DESCRIPTION', 'VALUE',
                                     'SEQ_NO', 'VERSION', 'AMOUNT', 'DATE')
)
SELECT 'pas_mssql.' || getvariable('pas_db') || '.' || c.schema_name || '.' || c.table_name || '.' || c.column_name,
       'pas_mssql.' || getvariable('pas_db') || '.' || u.schema_name || '.' || u.table_name || '.' || u.column_name,
       'references', 'name_match', 'inferred',
       json_object('flow', 'F1', 'method', 'name_match')
FROM pas_raw.columns AS c
JOIN unique_pk AS u
  ON lower(u.column_name) = lower(c.column_name)
 AND u.base_type = c.base_type
 AND NOT (u.schema_name = c.schema_name AND u.table_name = c.table_name)
WHERE NOT EXISTS (
  SELECT 1 FROM pas_raw.foreign_keys AS f
  WHERE f.parent_schema = c.schema_name AND f.parent_table = c.table_name
    AND f.parent_column = c.column_name);

-- ---------------------------------------------------------------------
-- Check the load
-- ---------------------------------------------------------------------
SELECT 'raw tables' AS item, count(*) AS n FROM pas_raw.tables
UNION ALL SELECT 'raw columns', count(*) FROM pas_raw.columns
UNION ALL SELECT 'graph nodes: ' || node_type, count(*) FROM graph.node
          WHERE starts_with(node_key, 'pas_mssql.' || getvariable('pas_db') || '.') GROUP BY node_type
UNION ALL SELECT 'graph edges: ' || edge_type || ' (' || origin || ')', count(*) FROM graph.edge
          WHERE json_extract_string(attrs, '$.flow') = 'F1' GROUP BY edge_type, origin
ORDER BY item;
