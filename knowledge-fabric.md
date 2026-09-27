---
inclusion: always
---

# Knowledge Fabric: project context

## What this project is

The Knowledge Fabric is a connected, queryable map of a UK life and pensions data estate on AWS. It links tables, columns, pipelines, jobs, reports and business definitions from the source policy administration system (PAS) through the data layers to consumers. People use it to find data, understand how it is derived, reuse existing reports, assess the impact of change, and explain pipeline failures.

It stores metadata only. It never reads, copies or stores row-level data values.

## Estate

- **Source:** a third-party PAS on SQL Server. No full metadata extract is available and there is no live connection. The Source layer is built by F1D at table level from DMS table statistics. Flow 1 (F1) loads a real extract for a subset of tables where one exists, and F1 always wins over F1D.
- **Ingestion:** AWS DMS replicates PAS tables into an S3 Raw zone as CDC change logs.
- **Raw:** rarely queried, and often missing from the Glue catalogue. It matters only for operational lineage: which DMS task wrote what, and which merge job read it.
- **Distill (the anchor layer):** AWS Glue (PySpark) jobs and Lambda functions merge CDC records into Apache Hudi tables. Distill is the trusted PAS equivalent and carries all column-level PAS structure and meaning.
- **Published:** Glue jobs and Lambda functions apply business transformations from Distill into Published data stores.
- **Orchestration:** AWS Step Functions.
- **Catalogue:** AWS Glue Data Catalog registers the Distill and Published tables, and some Raw tables.
- **Consumers:** QuickSight dashboards, extracts, APIs and applications read from Published.
- **Code:** Glue and Lambda code lives in Bitbucket repositories.
- **Documentation:** business definitions come from an Excel data dictionary and Confluence (Flow 3).

## Architecture decisions (do not change without asking)

- **Lean architecture:** there is no OpenMetadata, Neo4j or other catalogue or graph product. Harvesters read sources directly and write into our own tables, which are the system of record for harvested metadata.
- **Store:** a DuckDB file for the PoC, Aurora PostgreSQL in production. All reads and writes go through the repository layer, so SQL must stay portable. Isolate any DuckDB-specific SQL in the repository implementation.
- **Single writer:** only one process writes to the DuckDB file at a time. Harvesters run as command-line jobs, one after another, never concurrently.
- **Distill first:** search, questions and the UI default to Distill and Published. Source and Raw appear in lineage and operational views.
- **Deterministic first:** lineage comes from catalogues, DMS configuration, orchestration definitions and parsed code. An LLM is never used to invent lineage, keys or relationships. Anything not resolved deterministically is recorded as an explicit gap, never guessed.
- **Read-only against AWS:** harvesters use read-only IAM permissions and never modify any AWS resource.
- **Stack:** Python 3.11 or later; Click for command-line interfaces; boto3; duckdb; pyarrow for bulk loads; pydantic 2 for configuration in new code; sqlglot for SQL parsing; pytest for tests; moto for AWS mocks. Add a dependency only when a task needs it.

## Graph data model

These tables already exist and are shared by every flow. Do not change their structure without asking. Each flow owns the rows it writes, identified by `attrs.flow`.

```sql
CREATE TABLE graph.node (
  node_key    VARCHAR PRIMARY KEY,
  node_type   VARCHAR NOT NULL CHECK (node_type IN (
                'table', 'column', 'dashboard', 'chart', 'data_model',
                'pipeline', 'pipeline_task', 'glue_job', 'lambda_function',
                'dms_task', 'api_endpoint', 'extract', 'glossary_term')),
  layer       VARCHAR NOT NULL DEFAULT 'none' CHECK (layer IN (
                'source', 'raw', 'distill', 'published', 'consumer', 'none')),
  parent_key  VARCHAR,
  name        VARCHAR NOT NULL,
  data_type   VARCHAR,
  attrs       JSON NOT NULL DEFAULT '{}',
  deleted     BOOLEAN NOT NULL DEFAULT false,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT current_timestamp
);

CREATE SEQUENCE graph.edge_seq;
CREATE TABLE graph.edge (
  edge_id         BIGINT PRIMARY KEY DEFAULT nextval('graph.edge_seq'),
  src_key         VARCHAR NOT NULL,
  dst_key         VARCHAR NOT NULL,
  edge_type       VARCHAR NOT NULL CHECK (edge_type IN (
                    'contains', 'references', 'derives_from',
                    'reads', 'writes', 'depends_on', 'consumes', 'means', 'related_term')),
  transform_class VARCHAR CHECK (transform_class IN (
                    'identity', 'meaning_preserving', 'derivation', 'unclassified')),
  expression      VARCHAR,
  origin          VARCHAR NOT NULL,
  tier            VARCHAR NOT NULL CHECK (tier IN ('certified', 'proposed', 'inferred')),
  job_key         VARCHAR,
  attrs           JSON NOT NULL DEFAULT '{}',
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT current_timestamp,
  CHECK (src_key <> dst_key),
  CHECK (edge_type <> 'derives_from' OR transform_class IS NOT NULL)
);
```

### Flows

| Code | Flow | Writes | Runs after |
| --- | --- | --- | --- |
| F2A | Glue catalogue | Distill, Published and catalogued Raw tables and columns | none |
| F2C | Orchestration | State machines, states, Glue jobs, Lambda functions, dependencies | none |
| F1D | PAS tables from DMS (primary Source layer) | PAS `table` nodes, no columns | F2A |
| F1 | PAS extract (optional enrichment) | PAS tables, columns, keys, foreign keys | none |
| F2B | DMS lineage | DMS tasks, Raw location nodes, table-level reads and writes, `pas_equivalent` on Distill tables | F1D, F2A |
| F2D | Code lineage | Job reads and writes; column lineage Distill to Published; observed joins | F2A, F2B, F2C |
| F2R | Relationships on Distill | `references` edges between Distill columns | F2D |
| F2E | QuickSight | Datasets, fields, dashboards, lineage from Published | F2A |

Flow 1's own raw tables (`tables`, `columns`, `primary_keys`, `foreign_keys`, `load_history`, `drift_log`) are private to Flow 1. Every other flow reads PAS only from graph nodes with `attrs.flow` in (`F1`, `F1D`).

### Node keys

| Asset | Key pattern | Written by |
| --- | --- | --- |
| PAS table | `pas_mssql.<db>.<schema>.<table>` | F1D, F1 |
| PAS column (extract only) | `pas_mssql.<db>.<schema>.<table>.<column>` | F1 |
| Glue table, column | `glue.<database>.<table>[.<column>]` | F2A |
| Raw location, uncatalogued | `s3.<bucket>/<prefix>/<schema>/<table>` | F2B |
| DMS task | `aws.dms_task.<replication_task_identifier>` | F2B |
| Step Functions state machine | `aws.sfn.<state_machine_name>` | F2C |
| Step Functions state | `aws.sfn.<state_machine_name>.<state_path>` | F2C |
| Glue job | `aws.glue_job.<job_name>` | F2C |
| Lambda function | `aws.lambda.<function_name>` | F2C |
| QuickSight dataset, field | `quicksight.dataset.<dataset_id>[.<field_name>]` | F2E |
| QuickSight dashboard | `quicksight.dashboard.<dashboard_id>` | F2E |

Rules:

- PAS keys keep the source's original casing, as DMS reports it. Compare PAS names case-insensitively when matching F1 against F1D.
- `<state_path>` joins nested state names with `/`, for example `ProcessPlans/MergePlan`.
- S3 locations are normalised before keying or matching: `s3a://` and `s3n://` become `s3://`, and trailing slashes are removed.
- Each Distill table node carries `attrs.pas_equivalent`, the key of its PAS table, where it can be resolved.

### Edge semantics

For lineage, data flows from `src_key` to `dst_key`. This table is the source of truth for direction.

| edge_type | src_key | dst_key | Meaning |
| --- | --- | --- | --- |
| contains | parent | child | Table has column; state machine has state; dataset has field |
| references | child column | referenced column | A key relationship, declared or inferred; see origin |
| derives_from | upstream column | downstream column | dst is derived from src; `transform_class` required; `expression` holds the transform when known |
| reads | job, function or DMS task | table or Raw location | It reads the table |
| writes | job, function or DMS task | table or Raw location | It writes the table |
| depends_on | dependent | dependency | `attrs.relation`: `invokes` (state to job or function), `runs_after` (state to predecessor state), `starts` (state to child state machine) |
| consumes | consumer | table or dataset | Dashboard uses dataset; dataset reads a Glue table |

Lineage granularity:

- **Into and out of Raw:** table level only (`reads` and `writes`). No column-level lineage involves Raw.
- **PAS to Distill:** where F1 PAS columns exist, column-level `derives_from` by case-insensitive name, tier inferred, `attrs.via = 'dms+merge'`.
- **Distill to Published, and Published to consumers:** column level.

### Transform classes

- **identity:** the value passes through unchanged, with the same meaning; for example `SELECT col`.
- **meaning_preserving:** the value keeps its meaning, but its name, type or format changes. The allow-list is: rename or alias, CAST, TRIM, LTRIM, RTRIM, UPPER, LOWER, and date or timestamp truncation to day. Keep the list in configuration.
- **derivation:** anything else, including arithmetic, CASE, COALESCE, aggregation, concatenation, lookups and window functions.
- **unclassified:** lineage is known but the expression was not available.

### Origin and tier

| origin | Meaning | Default tier |
| --- | --- | --- |
| connector | Read from a catalogue or API (Glue, QuickSight, a PAS extract) | certified |
| dms_mapping | Derived from DMS table mappings and statistics | certified |
| dms_derived | PAS table reconstructed from DMS table statistics | inferred |
| sfn_definition | Derived from a Step Functions definition | certified |
| parsed | Extracted by the code parser, with every name resolved | certified |
| parsed_partial | Extracted by the parser, with some names resolved by heuristic | inferred |
| declared | Taken from a lineage manifest written by an engineer | certified |
| hudi_record_key | Primary key taken from a Hudi table's record key | certified |
| observed_join | A join seen in parsed SQL or a QuickSight dataset; `attrs.occurrences` and `attrs.seen_in` record the evidence | proposed |
| name_match | Inferred by name and type matching | inferred |

### Relationships on Distill (F2R)

There are no declared foreign keys for most PAS tables, so relationships between Distill columns come from three sources, in order of strength:

1. **The Hudi record key** of each Distill table, as its primary key.
2. **Observed joins** in parsed Spark SQL (F2D) and QuickSight dataset joins (F2E), counted across jobs and datasets.
3. **Name-match inference**, using the record key as the primary key. A match requires all of these: a single-column key; a key name that is the primary key of exactly one table; a case-insensitive name match with the same base type; a name longer than 3 characters and not on the configurable list of generic names; and no stronger relationship already present for the column.

## Loading and performance rules

- Each harvester builds its full lists of nodes and edges in memory, then calls `GraphRepository.reload_flow(flow, nodes, edges)` once.
- `reload_flow` bulk-loads from Arrow tables in one transaction: it deletes the flow's edges and nodes, then runs `INSERT ... SELECT` from the Arrow tables. Never insert rows one at a time, and never use `executemany` for graph rows.
- Each harvester is re-runnable and never modifies rows owned by another flow.
- The DuckDB file lives outside OneDrive or any synced folder. Its path comes from configuration. There is one database file for the whole graph.
- Never kill Python processes by name (for example `Stop-Process -Name python`). If the database is locked, report it and ask.

## Harvester conventions

- Configuration lives in one YAML file, validated at start-up. No AWS account IDs, resource names or database names are hard-coded.
- Every harvester supports two input modes: `live` (boto3 with a read-only role) and `files` (JSON saved beforehand with the AWS CLI commands documented in the README). Both modes produce identical graph output.
- **Glue:** a database with no layer mapping in configuration is skipped and written to the gaps report. It is never loaded with `layer = none`.
- **Exclusions:** Hudi meta-columns are excluded (`_hoodie_commit_time`, `_hoodie_commit_seqno`, `_hoodie_record_key`, `_hoodie_partition_path`, `_hoodie_file_name`), as are DMS-added columns (`Op` and the configured timestamp column). Technical columns added by merge jobs (a configurable list) are excluded when reconstructing PAS structure.
- **Reporting:** every harvester prints counts of nodes and edges written by type, and writes unresolved items to `gaps/<flow_code>.csv` with the columns `flow, object, location, reason, detail`, instead of failing.
- **Tests** use recorded JSON fixtures and moto; no test calls real AWS. Every rule needs a positive and a negative case.

## Question layer principles (for later flows)

- The LLM classifies questions, resolves names and phrases answers. Deterministic engines compute the answers.
- Core question families use fixed, tested tools chosen by a routing table. Free-form SQL or graph querying by the LLM is a labelled fallback only.
- Business definitions are curated onto glossary terms in advance (Flow 3), not retrieved from documents at question time.
- Every answer cites the graph nodes it used and their tier, and names any gaps.

## Security and data handling

- Never log or store Lambda environment variable values, Glue job argument values not on the configured allow-list, credentials, or any data values.
- Vendor schema extracts and harvested JSON under `data/` are confidential. Keep them in `.gitignore`.

## How to work in this repository

- Show a plan before changing existing code, and wait for approval.
- Implement one flow or harvester at a time. After each, show the run summary and the gaps report.
- Do not change the graph DDL, node key patterns or edge semantics without asking. Propose the change and explain why.
