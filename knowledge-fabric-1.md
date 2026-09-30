---
inclusion: always
---

# Knowledge Fabric: project context

## What this project is

The Knowledge Fabric is a connected, queryable map of a UK life and pensions data estate on AWS. It links tables, columns, pipelines, jobs, reports and business definitions from the source systems through the data layers to consumers. People use it to find data, understand how it is derived, reuse existing reports, assess the impact of change, and explain pipeline failures.

It stores metadata only. It never reads, copies or stores row-level data values.

## Production AWS account (these rules override every other instruction)

The AWS account reachable through the MCP and the harvesters is **production**. Access is read-only, but follow these rules regardless of what the permissions allow.

- **Never write or change anything.** Only use `describe`, `list` and `get` calls that return configuration or metadata. Never call any create, put, update, delete, start, stop, run, invoke, tag, attach, modify or reset action. That includes starting DMS tasks, Glue jobs, crawlers or Step Functions executions, and invoking Lambda functions.
- **Never read data or secrets.** Do not run Athena queries, S3 Select or Redshift queries. Do not read S3 data files, CloudWatch Logs, or Secrets Manager or Parameter Store values. The only S3 objects that may be read are Hudi `.hoodie/hoodie.properties` metadata files. S3 listing is limited to prefixes (with a delimiter) and a few example object names; never list feed buckets in bulk, because file names can contain customer references.
- **Never record sensitive configuration values.** Lambda environment variable values, Glue job argument values not on the configured allow-list, and endpoint connection details (server names, ports, user names, secret ARNs) must not be printed, logged, stored in the graph or saved to fixtures. Redact them before saving any JSON.
- **Be gentle on the account.** Production jobs share its API limits. Make calls one at a time, paginate, sample rather than scan when exploring, and back off on throttling errors rather than retrying fast.
- **If a call is denied or throttled, stop and tell the user.** Never retry with a different call or work around it.
- **Before each batch of calls,** list the exact API actions to be used. If unsure whether an action is read-only, ask first.

### How these rules apply to the inventory

- Inventory files count as fixtures: the redaction rule applies before anything is written.
- **DynamoDB exception:** `dynamodb:DescribeTable` and `dynamodb:Scan` are allowed only on the job configuration tables listed in `config/harvest.yaml` under `dynamodb_config.tables`. Scan one table at a time, with a page size of 25 (never above 100), and back off on throttling. No other DynamoDB table may be read.
- **S3 in inbound and outbound feed prefixes:** record prefixes only, never object names.
- **The full list of allowed API actions per harvester** is in the Harvesters section below. An action not on that list needs the user's approval first.

## Estate

### Source systems

`config/source_systems.yaml` is the source of truth for systems, endpoints, databases and aliases. Five systems are PAS instances:

| system | product | aliases | engine | ingestion | production database |
| --- | --- | --- | --- | --- | --- |
| ARC1 | Composer | | SQL Server | DMS | PRD (`PRD_AGComposer`) |
| ARC2 | Composer | | SQL Server | DMS | PRA (`PRA_AGComposer`) |
| ARC3 | Composer | | SQL Server | DMS | PRW (`PRW_AGComposer`) |
| COMPASS | Compass | TargetPlan, Target Plan | Oracle | DMS | from its endpoint |
| ATOS | outsourced | | none | files over SFTP | none |

- ARC1, ARC2 and ARC3 are separate books of business on the same product, so they share table names.
- Non-PAS source systems (Pega, MI warehouse, ReportZone, adviser portal, ancillary databases and so on) have their own `system_type`.
- Ancillary databases that belong to an ARC instance carry that instance as their `system`.
- Aliases are used wherever names are matched: business-term text, Confluence pages, and questions.

### Ingestion routes into Raw

- **AWS DMS:** replicates the DMS-based systems into S3 Raw as CDC change logs. Some Composer endpoints replicate from backup databases to a PostgreSQL target instead of S3. Non-S3 targets are recorded, but their target tables are not modelled until the user decides.
- **AWS Transfer Family (SFTP):** partners such as ATOS deliver files to S3 through SFTP users, each with a home directory or directory mappings.
- **AWS Storage Gateway:** file shares write to S3 locations.

### Layers and consumers

- **Raw and landing:** DMS target folders, for example `s3://<raw>/inbound/ARC1/dbo/policy`, and file landing prefixes. Often missing from the Glue catalogue.
- **Distill (the anchor layer):** Apache Hudi tables built by Glue (PySpark) jobs and Lambda functions. Many are built by generic, parameterised Glue jobs whose parameters live in DynamoDB configuration tables. Distill is the trusted source equivalent.
- **Published:** business transformations from Distill, by Glue jobs and Lambda functions.
- **Orchestration:** Step Functions, Glue triggers and workflows, EventBridge rules, EventBridge Scheduler, Lambda event sources and S3 notifications.
- **Consumers:**
  - QuickSight
  - APIs, through API Gateway to Lambda functions
  - outbound file extracts, written to S3 under prefixes that include `outbound` and sent to recipients over SFTP
- **Code** lives in Bitbucket.
- **Business definitions** come from an Excel glossary and Confluence.

## Architecture decisions (do not change without asking)

- **Lean architecture:** no OpenMetadata, Neo4j or other catalogue or graph product. Exports to Alation or OpenMetadata may be added later as thin adapters.
- **Harvest once, model many times.** The system has three stages, and only the first touches AWS:
  1. **Harvesters** read AWS and write inventory files. They are the only code that calls AWS.
  2. **Importers and parsers** read declared inputs (configuration, the glossary, Confluence, schema extracts, code) and also write inventory files. They make no AWS calls.
  3. **The resolver** reads one inventory snapshot and builds the whole graph from scratch. It makes no AWS calls, and it never writes to the inventory.
- **Three kinds of fact, never mixed:**
  - **Observed:** inventory records from AWS.
  - **Declared:** configuration and documents maintained by people.
  - **Derived:** the graph.

  A declared fact (for example which database is ARC1) lives only in configuration, never in code. A derived fact is never written back into the inventory or the configuration.
- **Store:** the graph is a DuckDB file for the PoC and Aurora PostgreSQL in production. All graph reads and writes go through the repository layer, so SQL stays portable.
- **Deterministic first:** lineage comes from configuration, catalogues, orchestration definitions, naming conventions and parsed code. An LLM never invents lineage, keys or relationships. Anything not resolved deterministically is an explicit gap, never a guess.
- **Stack:** Python 3.11 or later; Click; boto3; duckdb; pyarrow; pydantic 2; sqlglot; pytest; moto. Add a dependency only when a task needs it.
- **Decisions log:** confirmed decisions are in `docs/decisions/`. Read the relevant file before starting work.

## Inventory contract

### Layout

All paths sit under `paths.data_root` from `config/harvest.yaml`. The data root lives outside OneDrive or any synced folder. If it sits inside the repository, it is listed in `.gitignore`.

```
<data_root>/inventory/runs/<harvester>/<run_id>/<kind>.jsonl
<data_root>/inventory/runs/<harvester>/<run_id>/run.json
<data_root>/inventory/snapshots/<snapshot_id>.json
<data_root>/graph/graph.duckdb          current graph
<data_root>/graph/graph.prev.duckdb     previous graph
<data_root>/gaps/<snapshot_id>/*.csv
```

- `run_id` is a UTC timestamp, `YYYYMMDDTHHMMSSZ`.
- `kind` names the object type, for example `dms.replication_task`, `glue.table` or `transfer.user`.

### Record envelope

One JSON object per line, one line per AWS object or declared item:

```json
{
  "kind": "dms.replication_task",
  "natural_id": "<ARN, or the unique name where there is no ARN>",
  "region": "eu-west-2",
  "observed_at": "2026-09-30T12:00:00Z",
  "source_api": "dms:DescribeReplicationTasks",
  "harvester": "dms",
  "harvester_version": "1.0.0",
  "redacted_paths": ["$.TaskData"],
  "payload": { "...": "the full API response item, after redaction" }
}
```

- **Capture wide, model narrow.** `payload` is the whole response item, not just the fields today's resolver uses. Redaction is the only thing removed.
- **Redaction** is driven by `config/redaction.yaml`: JSON paths per kind, plus key-name patterns (password, secret, token, credential, apikey, privatekey, ssh). Redaction happens in memory, before anything is written or logged, and every redacted path is listed in `redacted_paths`.
- **Declared inputs** use the same envelope, with `harvester` set to the importer name and `source_api` set to the file path or page id and version.

### run.json

Each run records:

- run id, harvester, harvester version, mode (`live` or `fixtures`)
- account alias, region, start and end time
- the exact API actions used
- record counts per kind
- errors
- `status`: `complete`, or `partial` when any call was denied, throttled or failed

### Snapshots

- A snapshot file lists one run per harvester. `kf snapshot create` chooses the latest complete run of each harvester unless a run is named.
- It records the hash of every declared configuration file used.
- A snapshot that includes a partial run is flagged. The resolver treats anything missing from a partial run as **unknown**, never as deleted, and says so in the run report.

## Declared inputs

- `config/source_systems.yaml`: systems, with `system_type`, `product`, `aliases`, `environment_code`, endpoints and databases. It also holds `distill_databases` (Glue database to system or systems) and `distill_prefixes` (table name prefix such as `arc1_` to system). No code may hard-code a database, prefix or system name.
- `config/channels.yaml`: maps SFTP users, file shares and outbound connectors or prefixes to a system or recipient. Kiro may draft it from the Transfer Family and Storage Gateway inventories; the user confirms the system names.
- `config/harvest.yaml`: data root, regions, allow-listed DynamoDB tables, S3 buckets and prefixes to list (with their role: `raw`, `inbound`, `outbound`), the Glue argument allow-list, and the generic-name list.
- `config/redaction.yaml`: the redaction rules.
- The glossary spreadsheet and Confluence pages, imported as `term.candidate` records with these fields: term, definition, column, table hint, system hint (raw text), product hint, source type, and source reference (sheet and row, or page id and version).
- The optional PAS schema extract and the Bitbucket code clone (its path comes from configuration).

## Harvesters and allowed API actions

Every harvester supports `--mode live` (boto3, read-only role) and `--mode fixtures` (converts saved JSON into a run). Both produce identical inventory records apart from `observed_at` and the run metadata.

| Harvester | Allowed actions | Redact |
| --- | --- | --- |
| dms | DescribeEndpoints, DescribeReplicationTasks, DescribeTableStatistics, DescribeReplicationInstances | endpoint server name, port, user name, secret ARNs, connection attributes |
| transfer | ListServers, DescribeServer, ListUsers, DescribeUser, ListConnectors, DescribeConnector | SSH public keys, connector URL, user secret ids, trusted host keys |
| storage_gateway | ListGateways, ListFileShares, DescribeNFSFileShares, DescribeSMBFileShares | client lists, valid, invalid and admin user lists |
| s3_prefixes | ListObjectsV2 with Delimiter on configured prefixes only, GetBucketNotificationConfiguration on configured buckets | object names under inbound and outbound prefixes |
| hudi_keys | GetObject on `<table location>/.hoodie/hoodie.properties` only; the path is built directly, never listed | none |
| glue_catalog | GetDatabases, GetTables | none (view SQL is metadata and is kept) |
| glue_jobs | GetJobs, GetTriggers, ListWorkflows, GetWorkflow (IncludeGraph), GetConnections (HidePassword) | argument values not on the allow-list; connection properties |
| lambda | ListFunctions, GetFunctionConfiguration, ListEventSourceMappings | environment variable values (keep names) |
| step_functions | ListStateMachines, DescribeStateMachine | none |
| events | ListRules, ListTargetsByRule, scheduler ListSchedules, GetSchedule | target input payloads |
| dynamodb_config | DescribeTable, Scan (allow-listed tables only) | values matching redaction patterns |
| quicksight | ListDataSets, DescribeDataSet, ListDataSources, DescribeDataSource, ListAnalyses, DescribeAnalysisDefinition, ListDashboards, DescribeDashboardDefinition | data source connection details and credentials |
| api_gateway | GetRestApis, GetResources, GetIntegration, apigatewayv2 GetApis, GetRoutes, GetIntegrations | integration credentials |

The following harvest nothing from AWS and make no AWS calls:

- importers: `config`, `channels`, `glossary` (spreadsheet), `pas_extract` (F1)
- parsers: `confluence` (through the Confluence MCP) and `code` (Bitbucket clone)

## Resolver

`kf resolve --snapshot <id>` builds a new DuckDB file from the snapshot, runs verification, and only then swaps it in as `graph.duckdb`, keeping the previous file as `graph.prev.duckdb`. A failed build or failed verification leaves the current graph untouched.

Rules run in this order. Each rule is a separate, tested module.

| Rule | Does |
| --- | --- |
| R1 Nodes | Creates nodes with stable keys. Original AWS identifiers (ARN, Glue database and table, S3 URI) are kept in `attrs.aws`. |
| R2 Location spine | Normalises every S3 reference, then matches locations to Glue tables. See below. |
| R3 Ingestion lineage | DMS: task `reads` source tables and `writes` Raw locations; table-level `derives_from` from source table to Raw. SFTP and file shares: channel `writes` the landing location. |
| R4 Job I/O | `reads` and `writes` edges for Glue jobs and Lambda functions, from ranked evidence (below). |
| R5 Orchestration | `depends_on` edges: `invokes`, `runs_after`, `starts`, `triggered_by`. |
| R6 Lineage | Table-level `derives_from` through each job's inputs and outputs, then column level where parsed code allows. |
| R7 Distill classification | `distill_kind`, `system` or `systems`, and `source_equivalent`. |
| R8 Keys and relationships | Hudi record keys, declared foreign keys, observed joins, name-match inference. |
| R9 Consumption | QuickSight, API and outbound extract lineage from Published. |
| R10 Business terms | Links columns to glossary terms using the scope ladder. |
| R11 Verify | Runs the verification SQL and writes the gaps and run reports. |

Every node and edge carries:

- `attrs.rule`: the rule that created it
- `attrs.evidence`: a list of `<kind>:<natural_id>` references to inventory records
- `attrs.snapshot`: the snapshot id

`attrs.flow` is retired.

### R2 Location spine

Every S3 reference is normalised to one key, whether it comes from a DMS target, an SFTP home directory, a file share, a Glue table location, a DynamoDB config value, a Step Functions parameter or parsed code:

- `s3a://` and `s3n://` become `s3://`
- the bucket is lower case
- repeated slashes are removed, and so is any trailing slash
- the key keeps its original casing

A location resolves to the Glue table whose normalised location is the longest prefix of it. With no match, it becomes an uncatalogued location node. A location under a configured outbound prefix is an `extract` node.

### R4 Job I/O evidence, in order of trust

1. **DynamoDB config item** for a generic job: origin `dynamodb_config`, certified.
2. **Step Functions state parameters** with literal values: origin `sfn_parameters`, certified. Values taken from execution input (`.$` paths) are gaps.
3. **Parsed code:** origin `parsed` (certified) or `parsed_partial` (inferred).

Where two sources disagree for the same job and table, keep both edges and write a gap.

`reads` and `writes` always start at the Glue job or Lambda function, never at the state machine. For a generic job, each input and output pair taken from one config item also gets:

- a table-level `derives_from` edge
- `transform_class = unclassified`
- `job_key` set to the job
- `attrs.level = table`
- `attrs.config_table` and `attrs.config_key`

### R7 source_equivalent (replica tables only)

Evaluated in this order:

1. **`dms+merge` (certified):** exactly one source table reaches this Distill table through a DMS task, a Raw location and a job that writes the Distill table.
2. **`naming_convention` (certified):**
   - The name is parsed using `distill_prefixes`, for example `arc{n}_{db}_dbo_{table}`, split at the first `_dbo_`.
   - The result must be verified against the DMS table statistics of that system's database.
   - It must agree with rule 1 when rule 1 applies; disagreement is a gap.
3. **`name_match` (inferred):** a normalised name match within one source database.

`source_equivalent` must never cross systems: the Distill table's `system` (from `distill_databases` or `distill_prefixes`) must equal the source table's `system`. A candidate that fails this becomes a gap, not a link, and so does a candidate whose target node does not exist.

Domain, view and downstream tables have no `source_equivalent`. Their `derives_from` edges may cross systems, for example a unified customer view built from ARC1, ARC2 and ARC3. Tables built from several systems carry `attrs.systems` (a list) instead of `attrs.system`.

### R10 business-term scope ladder

Each column gets the most specific definition available:

1. **`system`:** the text names this column's system, including aliases (TargetPlan means COMPASS).
2. **`product_table`:** it names the product, table and column, for example Composer `dbo.policy.policy_no`.
3. **`product_column`:** it names the product and column, for example `policy_no` anywhere in Composer.
4. **`generic`:** the normalised column name only.

A column links to its terms with a `means` edge, carrying:

- `attrs.term_scope`
- `attrs.term_source`: the source reference
- `attrs.conflict`: true when the same level offers different definitions

On a conflict, all candidates are kept. The resolver never picks one. Generic-level matches are not made for names on the generic-names list unless the source text names the table.

Definitions attach to source columns and Distill replica columns. Downstream columns inherit them at query time through `identity` and `meaning_preserving` lineage; the inherited links are not stored.

## Graph data model

These tables are shared by every rule. Do not change their structure without asking.

```sql
CREATE TABLE graph.node (
  node_key    VARCHAR PRIMARY KEY,
  node_type   VARCHAR NOT NULL CHECK (node_type IN (
                'table', 'column', 'dashboard', 'chart', 'data_model',
                'pipeline', 'pipeline_task', 'glue_job', 'lambda_function',
                'dms_task', 'api_endpoint', 'extract', 'glossary_term', 'channel')),
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

`channel` is new: an SFTP user, a file share, or an outbound recipient. There is no `system` node type; systems are attributes.

### Node keys

| Asset | Key pattern |
| --- | --- |
| Source table, column | `src.<engine>.<DatabaseName>.<schema>.<table>[.<column>]` |
| Glue table, column | `glue.<database>.<table>[.<column>]` |
| Uncatalogued location or extract | `s3.<bucket>/<prefix>` |
| DMS task | `aws.dms_task.<replication_task_identifier>` |
| Step Functions state machine, state | `aws.sfn.<state_machine_name>[.<state_path>]` |
| Glue job | `aws.glue_job.<job_name>` |
| Lambda function | `aws.lambda.<function_name>` |
| API endpoint | `aws.api.<api_id>.<method>.<route>` |
| Channel | `channel.sftp.<server_id>.<user>`, `channel.file_share.<share_id>`, `channel.outbound.<recipient>` |
| QuickSight dataset, field, dashboard | `quicksight.dataset.<id>[.<field>]`, `quicksight.dashboard.<id>` |
| Glossary term | `term.<normalised term>.<8-character hash of the definition>` |

Key rules:

- `<engine>` comes from the DMS endpoint's EngineName. The database is always in the source key.
- Source keys keep the source's casing.
- `<state_path>` joins nested state names with `/`.
- Lambda names are normalised from ARNs, aliases and versions.

### Source table attributes

Source tables come from the DMS table statistics of every task, whatever its status. There is no "active only" filter at harvest time. Each source table carries:

- `system`, `system_type`, `environment_code`, `product`
- `product_table`: `<schema>.<table>` in lower case, for every Composer table
- `ingestion`
- `endpoints` and `dms_tasks`, both lists
- `replication_mode`: `ongoing` (CDC) or `one_off` (full load only)
- `task_status`
- `database`

Columns exist only where a PAS schema extract provides them.

### Edge semantics

For lineage, data flows from `src_key` to `dst_key`.

| edge_type | src_key | dst_key | Meaning |
| --- | --- | --- | --- |
| contains | parent | child | Table has column; state machine has state; dataset has field |
| references | child column | referenced column | A key relationship; see origin |
| derives_from | upstream table or column | downstream table or column | `transform_class` required; `attrs.level` = `table` or `column` |
| reads | job, function, DMS task or channel | table, location or extract | It reads it |
| writes | job, function, DMS task or channel | table, location or extract | It writes it |
| depends_on | dependent | dependency | `attrs.relation`: `invokes`, `runs_after`, `starts`, `triggered_by` |
| consumes | consumer | table, dataset or extract | A dashboard, API or outbound channel uses it |
| means | column | glossary term | See the R10 scope ladder |

Transform classes:

- **identity:** the value passes through unchanged.
- **meaning_preserving:** the value keeps its meaning but its name, type or format changes. The allow-list is rename or alias, CAST, TRIM, LTRIM, RTRIM, UPPER, LOWER, and date truncation. It is kept in configuration.
- **derivation:** anything else.
- **unclassified:** lineage is known but the expression is not.

### Origin and tier

| origin | Meaning | Default tier |
| --- | --- | --- |
| connector | Read from a catalogue or API | certified |
| dms_mapping | DMS task configuration and table statistics | certified |
| dms_derived | Source table reconstructed from DMS statistics | inferred |
| transfer_config | SFTP user home directory or mapping | certified |
| gateway_config | Storage Gateway file share location | certified |
| dynamodb_config | Generic job configuration item | certified |
| sfn_definition | Step Functions structure | certified |
| sfn_parameters | Literal parameters in a Step Functions state | certified |
| naming_convention | Verified Distill naming convention | certified |
| parsed | Code or view SQL parsed, every name resolved | certified |
| parsed_partial | Parsed, some names resolved by heuristic | inferred |
| declared | Configuration, glossary or schema extract | certified |
| hudi_record_key | Hudi record key | certified |
| observed_join | A join seen in code or QuickSight | proposed |
| name_match | Normalised name and type match | inferred |

### Relationships (R8)

1. **Hudi record key:**
   - Read from `hoodie.table.recordkey.fields` and stored as `attrs.primary_key` (an ordered list) with `attrs.primary_key_origin` on the table, and `attrs.pk_ordinal` on each key column. Never as a self-referencing edge.
   - Columns on `key_discriminator_columns` (starting with `source_system`) go to `attrs.key_discriminators`.
2. **Declared foreign keys** from a schema extract: one edge per column pair. Every constraint name for that pair is kept in `attrs.constraint_names`.
3. **Observed joins** in parsed code and QuickSight, counted across jobs and datasets. For Composer, a join seen in one ARC instance may be proposed for the others through `product_table`.
4. **Name match**, only when all of these hold:
   - the key is a single column once discriminators are removed
   - the key name is the primary key of exactly one table in the same source database
   - the normalised name matches, with the same base type
   - the name is longer than 3 characters and not on the generic-names list
   - the column has no stronger relationship already

Name matching never crosses source databases.

### Name normalisation

Names are compared in lower case with underscores removed. Report any collisions this creates.

## Loading, performance and safety

- The resolver builds its node and edge lists in memory and bulk-loads them from Arrow tables with `INSERT ... SELECT`. Never insert graph rows one at a time.
- Only one process writes to a DuckDB file at a time.
- Never kill Python processes by name (for example `Stop-Process -Name python`). If a file is locked, report it and ask.
- Commit to Git after every approved change. Inventory, graph and gaps files are never committed.

## Verification

- The verification SQL lives in `scripts/sql/`. R11 runs it on every build.
- These checks must return 0, or the new graph is not swapped in:
  - `source_equivalent` crossing systems, or pointing at a missing node
  - old or wrong key patterns
  - duplicate `references` edges
  - `source_equivalent` on a non-replica table
- Every other check is reported in the run report.
- Every unresolved item goes to `gaps/<snapshot_id>/<rule>.csv` with the columns `rule, object, location, reason, detail, evidence`.

## Tests

- Tests use recorded, redacted fixtures and moto. No test calls real AWS.
- Every resolver rule has a positive and a negative case. Required cases:
  - a location that matches two Glue tables (the longest prefix wins)
  - a generic job driven by a config item
  - a Step Functions parameter taken from execution input (a gap)
  - a `source_equivalent` that would cross systems (rejected)
  - COMPASS text written as TargetPlan (resolves)
  - a business term found at two scope levels (the more specific wins)
  - a partial run (unknown, not deleted)

## Question layer principles (later)

- The LLM classifies questions, resolves names (using system aliases) and phrases answers. Deterministic engines compute the answers.
- Core question families use fixed, tested tools. Free-form querying is a labelled fallback.
- Answers cite graph nodes and their tier, name any gaps, and state the business-term scope ("generic definition").

## How to work in this repository

- Show a plan before changing existing code, and wait for approval.
- Build one harvester or one resolver rule at a time. After each, show the run summary and the gaps report, then stop.
- Do not change the graph DDL, key patterns, edge semantics or this file without asking. Propose the change and explain why.
- Vendor extracts, inventory files, fixtures and gaps files are confidential and never committed.
