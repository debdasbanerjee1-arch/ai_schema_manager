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
- **Code object exception:** `s3:GetObject` is allowed on code files only, by the `code_objects` harvester, under all of these conditions:
  - **Which files:**
    - SQL files named by a DynamoDB config item (for example `action_config.sql_query`, a path under `pub/` or `dis/` in the scripts bucket)
    - each Glue job's `ScriptLocation`
  - **Where:** the bucket must be listed in `config/harvest.yaml` under `code_objects.buckets`.
  - **How:** exact keys only, never listed; suffix `.sql` or `.py` only; at most 2 MB per file.
  - **Handling:** the content is code, and it goes through redaction before it is written.

  Lambda deployment packages are never downloaded.
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

- **AWS DMS:** replicates the DMS-based systems into S3 Raw as CDC change logs.
  - Composer databases also replicate to an Aurora PostgreSQL target for low-latency reporting.
  - A task's target is decided by its target endpoint's `EngineName` (`s3` or `aurora-postgresql` / `postgres`), never by the task or endpoint name.
  - Non-S3 targets are recorded, but their target tables are not modelled until the user decides.
  - Observed topology (which endpoints and tasks serve which ARC instance) changes over time. It lives in `docs/estate/dms-topology.md`, not here; the inventory is the truth for it.
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
- **Business definitions** come from product data dictionaries (Excel, one sheet per table, for example the Composer dictionary) and from Confluence.

## Architecture decisions (do not change without asking)

- **Lean architecture:** no OpenMetadata, Neo4j or other catalogue or graph product. Exports to Alation or OpenMetadata may be added later as thin adapters.
- **Harvest once, model many times.** The system has three stages, and only the first touches AWS:
  1. **Harvesters** read AWS and write inventory files. They are the only code that calls AWS.
  2. **Importers and parsers** read declared inputs (configuration, data dictionaries, Confluence, code) and also write inventory files. They make no AWS calls.
  3. **The resolver** reads one inventory snapshot and builds the whole graph from scratch. It makes no AWS calls, and it never writes to the inventory.
- **Three kinds of fact, never mixed:**
  - **Observed:** inventory records from AWS.
  - **Declared:** configuration and documents maintained by people.
  - **Derived:** the graph.

  A declared fact (for example which database is ARC1) lives only in configuration, never in code. A derived fact is never written back into the inventory or the configuration.
- **Store:** the graph is a DuckDB file for the PoC and Aurora PostgreSQL in production. All graph reads and writes go through the repository layer, so SQL stays portable.
- **Deterministic first:** lineage comes from configuration, catalogues, orchestration definitions, naming conventions and parsed code. An LLM never invents lineage, keys or relationships. Anything not resolved deterministically is an explicit gap, never a guess. The one place an LLM is used in the build is drafting column definitions from Confluence excerpts (see "Definitions drafted from Confluence"). Those drafts are always labelled as drafts, and each one must quote the text it is based on.
- **Stack:** Python 3.11 or later; Click; boto3; duckdb; pyarrow; pydantic 2; sqlglot; pytest; moto. Add a dependency only when a task needs it.
- **Decisions log:** confirmed decisions are in `docs/decisions/`. Read the relevant file before starting work.

## Inventory contract

### Layout

All paths sit under `paths.data_root` from `config/harvest.yaml`. The data root lives outside OneDrive or any synced folder. If it sits inside the repository, it is listed in `.gitignore`.

```
<data_root>/inventory/runs/<harvester>/<run_id>/<kind>.jsonl
<data_root>/inventory/runs/<harvester>/<run_id>/run.json
<data_root>/inventory/snapshots/<snapshot_id>.json
<data_root>/graph/graph.<snapshot_id>.duckdb   one graph per build (the last 3 are kept)
<data_root>/graph/CURRENT                     the file name of the graph in use
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
- `status`, one of:
  - `running` while in progress
  - `complete`
  - `partial`: finished, but some records are known to be missing, for example fixtures that don't cover every table
  - `failed`, with `failed_reason` set to `denied`, `throttled` or `error`. In live mode, a denied or throttled call always ends the run as `failed`, and stops `kf refresh`.

### Snapshots

- A snapshot file lists one run per harvester. `kf snapshot create` chooses the latest complete run of each harvester unless a run is named.
- It records the hash of every declared configuration file used.
- A snapshot that includes a partial run is flagged. The resolver treats anything missing from a partial run as **unknown**, never as deleted, and says so in the run report.

## Declared inputs

- `config/source_systems.yaml`: systems, with `system_type`, `product`, `aliases`, `environment_code`, endpoints and databases. It also holds `distill_databases` (Glue database to system or systems) and `distill_prefixes` (table name prefix such as `arc1_` to system). No code may hard-code a database, prefix or system name.
- `config/channels.yaml`: maps SFTP users, file shares and outbound connectors or prefixes to a system or recipient. Kiro may draft it from the Transfer Family and Storage Gateway inventories; the user confirms the system names.
- `config/harvest.yaml` holds:
  - data root and regions
  - allow-listed DynamoDB config tables, each with its pattern (see R4)
  - `code_objects.buckets`
  - S3 buckets and prefixes to list, each with its role (`raw`, `inbound`, `outbound`)
  - the Glue argument allow-list
  - the generic-names list
  - `transform_class_map`, which maps config transformation types to transform classes

  Harvester settings live in one file only. If `config/sources.yaml` still holds any, move them here rather than keeping two copies.
- `config/redaction.yaml`: the redaction rules.
- **Data dictionaries.** These are listed in `config/harvest.yaml` under `dictionaries`, each with a `path`, the `product` it describes (for example `Composer`) and a default `schema` (for example `dbo`). A dictionary may optionally name a `system`, for a dictionary that describes a single instance.

  The workbook has one sheet per table:

  | Where | Holds |
  | --- | --- |
  | Row 1, column A | the table name, as `<table> table` |
  | Row 2 | the table description (under the Description heading) and table notes (under the Instructions heading) |
  | Header row | `column_name`, `data_type`, `is_nullable`, `column_length`, `column_precision`, `column_scale`, `has_default_value`, `Description`, `Instructions` |
  | Rows below | one row per column, until the first empty `column_name` |

  Reading rules:
  - The header row is found by searching for the cell `column_name`. It is never a fixed row number.
  - The table name comes from cell A1, because sheet names are cut to 31 characters.
  - A sheet that doesn't match this layout is a gap, never a guess.

  The `dictionary` importer writes `dictionary.table` and `dictionary.column` records. Each carries every field above plus a source reference: file, file hash, sheet and row.
- **Confluence.** There is no data dictionary page. Definitions for columns the Excel dictionary doesn't cover are found by searching Confluence and drafting from what is found; see "Definitions drafted from Confluence". The Confluence MCP is read-only for this project: never create, edit or comment on pages.
- The Bitbucket code clone (its path comes from configuration).

There is no PAS schema extract. Source tables therefore have no column nodes.

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
| code_objects | GetObject on exact code keys only (see the code object exception) | lines matching redaction patterns |
| quicksight | ListDataSets, DescribeDataSet, ListDataSources, DescribeDataSource, ListAnalyses, DescribeAnalysisDefinition, ListDashboards, DescribeDashboardDefinition | data source connection details and credentials |
| api_gateway | GetRestApis, GetResources, GetIntegration, apigatewayv2 GetApis, GetRoutes, GetIntegrations | integration credentials |

The following harvest nothing from AWS and make no AWS calls:

- importers: `config`, `channels`, `dictionary` (Excel data dictionaries)
- parsers: `confluence` (through the Confluence MCP) and `code` (Bitbucket clone plus `code_objects` records)

### Inventory categories

| Category | Holds | Written by |
| --- | --- | --- |
| ingestion | DMS endpoints, tasks, table statistics; Transfer Family; Storage Gateway; S3 prefixes | harvesters |
| catalogue | Glue databases, tables, columns; Hudi record keys | harvesters |
| compute | Glue jobs, Lambda functions, code objects | harvesters |
| job_io | DynamoDB config items; code templates | harvesters, parsers |
| orchestration | Step Functions, Glue triggers and workflows, EventBridge, Scheduler, S3 notifications | harvesters |
| consumption | QuickSight, API Gateway, outbound prefixes | harvesters |
| business_terms | data dictionary tables and columns, Confluence extracts | importers, parsers |
| declared_config | validated configuration | importers |

## Command line

All commands start with `kf`.

| Command | Does |
| --- | --- |
| `kf harvest <name>` | runs one harvester |
| `kf harvest all` | runs every harvester in turn |
| `kf import <name>` | runs one importer or parser |
| `kf snapshot create` | builds a snapshot from the latest complete runs |
| `kf resolve [--snapshot <id>]` | builds, verifies and swaps in the graph |
| `kf verify` | runs the verification SQL on the current graph |
| `kf fixtures save <harvester> <run_id>` | copies a completed live run, already redacted, into `data/fixtures/` |
| `kf definitions worklist\|search\|batches\|ingest` | the steps for definitions drafted from Confluence |
| `kf channels draft` | drafts `config/channels.yaml` from the inventory |
| `kf export --format csv\|alation\|openmetadata` | writes exports (later) |
| `kf validate` | validates the configuration files |
| `kf refresh` | runs harvest all, import all, snapshot, resolve |

Safety rules:

- `--mode fixtures` is the default for every command.
- `--mode live` must be given explicitly. It prints the exact API actions for every harvester involved and waits for confirmation. A `--yes` flag skips the confirmation, and is only for scheduled runs the user has approved.
- Exit codes: 0 for success, 1 for completed with gaps, 2 for an error.

## Resolver

`kf resolve --snapshot <id>` builds a new file, `graph.<snapshot_id>.duckdb`, and runs verification on it. Only if the checks pass does it rewrite `CURRENT` to name the new file. A failed build or failed verification leaves `CURRENT` unchanged.

- The UI and question layer read `CURRENT` and open that file read-only.
- The previous graph is still there, so rolling back means pointing `CURRENT` at it.
- The live file is never renamed or overwritten: on Windows that fails while the UI holds it open.
- Older graph files are deleted only when nothing has them open. The last 3 are kept.

Rules run in this order. Each rule is a separate, tested module.

| Rule | Does |
| --- | --- |
| R1 Nodes | Creates nodes with stable keys. Original AWS identifiers (ARN, Glue database and table, S3 URI) are kept in `attrs.aws`. |
| R2 Location spine | Normalises every S3 reference, then matches locations to Glue tables. See below. |
| R3 Ingestion lineage | DMS: task `reads` source tables and `writes` Raw locations; table-level `derives_from` from source table to Raw. SFTP and file shares: channel `writes` the landing location. |
| R4 Job I/O | `reads` and `writes` edges for Glue jobs and Lambda functions, from code templates bound to config items and parameters (below). |
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

### R4 Job I/O: code and configuration combine

Every job's lineage comes from two kinds of evidence, which complement each other rather than compete:

- **Code** says *how*: what the script reads and writes, its joins, and which columns map to which. The same applies to bespoke and generic jobs.
- **Parameters** say *which*: the concrete tables and paths a generic script runs against.

Parameters come from three places:
- DynamoDB config items: origin `dynamodb_config`, certified
- literal Step Functions state parameters: origin `sfn_parameters`, certified
- allow-listed Glue default arguments

**Code templates.** The `code` parser produces one template per script. It is written to the inventory as these kinds:

| Kind | Holds |
| --- | --- |
| `code.script` | repo, path, commit, file hash, language |
| `code.io` | direction, target expression, line |
| `code.sql` | statement text |
| `code.column_map` | source columns, target column, expression |
| `code.config_access` | the config table and key expression a script reads at run time |

Values the parser can't resolve become named placeholders, such as `${arg:source_table}` or `${config:target_path}`. They are never guessed.

**Binding.** R4 binds each template to every parameter set that runs it, producing one bound instance per config item or parameter set. `code.config_access` is what links a generic job to its config items; where it is missing, R4 falls back to the job's name as it appears in the config items.

Bound instances are then treated as follows:

- **Code and parameters agree** (same job, direction and location): one edge, certified, citing both as evidence.
- **Code finds I/O the config doesn't list** (lookup tables, joins, audit tables): extra edges from code, origin `parsed` or `parsed_partial`.
- **They disagree:** keep both edges and write a gap.
- **A placeholder has no binding:** a gap with reason `unbound_parameter`.
- **Execution input:** a Step Functions value taken from execution input (a `.$` path) is a gap.

Column-level `derives_from` (R6) comes from two places: the data-mapping config and bound code.

### R4 DynamoDB config patterns

The config tables are allow-listed in `config/harvest.yaml`, each tagged with one of the patterns below. R4 handles each pattern as described.

| Pattern | Example table | What it gives |
| --- | --- | --- |
| `sfn_parameter` | `auk-prd-cdp-step-function-parameter-config-table` | a state machine name → `source_unique_ref`: the table that state machine processes |
| `target_source_mapping` | `auk-prd-cdp-target-source-mapping-config-table`, and the domain tables `auk-prd-{amd,ceas,comm,drp,scheme,dcm}-target-source-mapping-config-table` | `input_unique_ref` → `target_unique_ref`: table-level lineage |
| `data_mapping` | `auk-prd-cdp-data-mapping-config-table` | column-level mappings for Raw-to-Distill merge jobs: `source_unique_ref`, `source_column`, `target_column`, `target_datatype`, `transformation_type`, `transformation_name` |
| `generic_extract` | `auk-prd-cdp-generic-extract-mapping-config-table` | Distill-to-Published extracts: `action`, `action_config.sql_query` (the S3 path of a SQL file), `target_unique_ref`, `source_bucket_key` |

How each field is resolved:

- **`*_unique_ref` values** (for example `arc1_composer_dbo_member_account`) resolve to a Glue table by exact normalised table name, within the Glue databases configured for that layer.
  - No match: a gap with reason `unresolved_ref`.
  - A match in more than one database: a gap with reason `ambiguous_ref`, never a guess.
- **`source_bucket_key`** resolves through the location spine (R2).
- **`sfn_parameter` items** name a state machine, but edges start at jobs.
  - Using R5's `invokes` edges, R4 attaches the `reads` edge to the single generic Glue job that state machine invokes, with `attrs.via_state_machine`.
  - If the state machine invokes more than one candidate job, write a gap with reason `ambiguous_job` and record the table in the state machine's `attrs.processes_tables`. No edge from the state machine.
- **`data_mapping` items** give column-level `derives_from` edges: origin `dynamodb_config`, certified, `attrs.level = column`, `expression = transformation_name`.
  - `transform_class` comes from `transform_class_map`, which starts with `one2one` → identity and `cast` → meaning_preserving.
  - An unmapped `transformation_type` becomes `unclassified`, and its distinct values are listed in the gaps report for the user to classify. Never default an unmapped value to `derivation`.
- **`generic_extract` items:**
  - the SQL file named in `action_config.sql_query` is read by `code_objects`
  - it is parsed with sqlglot (dialect `spark`) into a code template, then bound to that item
  - a missing or unparseable file is a gap

**Script to job mapping.**
- A Glue job's `ScriptLocation` file name is matched to a repo path using the patterns in `config/harvest.yaml` under `code.script_map`.
- A Lambda function is matched by its handler and name.
- An unmatched job is a gap with reason `no_script`.
- The parser reads the branch or tag deployed to production (`code.ref`) and records the commit hash in `run.json`.

`reads` and `writes` always start at the Glue job or Lambda function, never at the state machine. For a generic job, each input and output pair from one bound instance also gets:

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

Domain, view and downstream tables have no `source_equivalent`. Their `derives_from` edges may cross systems, for example `pal_account_investment`, which is built from the ARC2 and ARC3 `party_attribute` and `attribute` tables. Tables built from several systems carry `attrs.systems` (a list) instead of `attrs.system`, and the name is always `systems`, never `source_systems`. The list is derived from the systems of their upstream tables.

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

**Data dictionary entries.** A product dictionary gives `product_table` scope, or `system` scope when the dictionary names a system.
- **Stored once.** Each dictionary column becomes one `glossary_term` node:
  - keyed `term.dict.<product>.<schema>.<table>.<column>`, in lower case
  - `attrs.term_kind = column_definition`
  - the description is the definition
  - the Instructions text is kept separately in `attrs.notes`, never merged into the definition, because it holds implementation notes rather than meaning
  - data type, nullability, length, precision and scale are kept in attrs
- **Linked to every instance.** The term is linked with `means` edges to the matching Distill replica column in every system on that product (ARC1, ARC2 and ARC3 for Composer). The match is through the table's `source_equivalent` and the normalised column name.
- **Table descriptions** become `table_definition` terms, linked to the replica tables.
- **Business terms** from Confluence (for example "drawdown") are `business_term` nodes keyed `term.<normalised term>.<hash>`.
- **Gaps:**
  - a dictionary table with no replica in a system (reason `dictionary_table_not_found`)
  - a dictionary column missing from the replica (reason `dictionary_column_not_found`)
- **Coverage report:** R11 reports Distill replica columns that have no definition.

Definitions attach to Distill replica columns (and to source columns, if any ever exist). Downstream columns inherit them at query time through `identity` and `meaning_preserving` lineage; the inherited links are not stored.

### Definitions drafted from Confluence

This fills the columns that the Excel dictionary doesn't cover, spending as few tokens as possible. It runs in four steps and caches every step, so a rerun doesn't search or call the LLM again for work already done. `--refresh` forces a rerun.

**1. Work list (code, no LLM).**
- Start from Distill replica columns that have no dictionary definition.
- Remove:
  - generic names
  - Hudi and DMS technical columns
  - discriminator columns
  - columns already in the cache
- Deduplicate at two levels:
  - product, table and column (one entry covers ARC1, ARC2 and ARC3)
  - the column name (one search covers every table that has that column)
- Order by use: first columns that feed Published or QuickSight, then the rest.
- Write `confluence.worklist` records and report the counts before searching anything.

**2. Search (Confluence MCP, no LLM).**
- One CQL search per distinct column name.
- Restricted to the spaces in `config/harvest.yaml` under `confluence.spaces`.
- Search the name variants in one query, as quoted phrases joined with OR:
  - the snake_case Distill name, for example `member_account_id`
  - its CamelCase form, for example `MemberAccountId`, which Composer uses
  - the upper-case form, which Oracle uses for COMPASS
- For COMPASS, add the system's aliases (TargetPlan).
- Limit each search to 10 results.

For each hit, record the page id, version, title and space as `confluence.search_hit`. Fetch each page at most once per version, whatever number of columns point to it, and store it as `confluence.page`.

**3. Excerpts (code, no LLM).** For each column and page, cut the text around each match: the sentence containing the match, plus one sentence either side. Also keep a table row when the match sits in a table. At most 3 excerpts per page and 5 pages per column, about 1,500 characters per column in total.

Columns with no excerpt are recorded as gaps with reason `no_confluence_match`. They never reach the LLM.

**4. Draft (LLM, batched).**
- Send batches of 25 columns. Each entry carries only the column name, its table names and the product, plus its excerpts with their page ids.
- The prompt is versioned in `prompts/definition_draft_v<n>.md`. It says: use only the excerpts; return null if they don't state what the column means; never invent.
- The answer is JSON only. Each entry has: `column`, `definition` (null or one to two sentences), `quote` (the exact supporting text), `page_id`, `system_mentioned` (or null), and `confidence` (high, medium or low).

Code then checks each draft:
- `quote` must appear word for word in the named page's excerpt. If it doesn't, the draft is rejected and written as a gap with reason `quote_not_found`.
- `system_mentioned` is mapped through the system aliases. An unknown value is ignored.

Accepted drafts are stored as `definition.draft` records carrying the input hash, prompt version and model. The cache key is the input hash plus the prompt version.

**Drafting backend.** There is no Bedrock yet, so for now Kiro's own model drafts. The code never calls an LLM directly:

1. `kf definitions batches` writes batch files to `<data_root>/definitions/batches/`, each with its prompt version and input hash.
2. Kiro reads one batch file at a time and writes the JSON answer to `<data_root>/definitions/answers/`, without echoing the content into the chat.
3. `kf definitions ingest` checks every answer (the JSON schema and the word-for-word quote match) and writes the accepted drafts to the inventory.

A Bedrock backend can later replace step 2 without changing steps 1 or 3.

**How R10 uses drafts.**
- A draft becomes a `glossary_term` with `attrs.term_kind = column_definition`, origin `llm_draft` and tier `proposed`.
- Its scope is `system` when it names a system, otherwise `product_table`.
- A dictionary definition always wins over a draft at the same or a wider scope. A system-specific draft is kept alongside the product-wide dictionary definition, as the more specific one.
- Every `means` edge from a draft carries the quote and page id.
- Answers and the UI label these definitions "drafted from Confluence, not verified".

**Pilot first.** Before any full run, run the four steps on 50 columns and report:
- searches made, pages fetched, excerpt characters
- LLM calls, and tokens in and out
- drafts accepted, null and rejected

Then wait for approval. The full run shows its estimated calls and tokens and asks before starting.

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

- `<engine>` is the DMS endpoint's `EngineName`, exactly as DMS reports it, in lower case: `sqlserver`, `oracle`, `postgres`. So a key reads `src.sqlserver.PRD_AGComposer.dbo.policy`, never `src.mssql...`. The database is always in the source key.
- Source keys keep the source's casing.
- `<state_path>` joins nested state names with `/`.
- Lambda names are normalised from ARNs, aliases and versions.

### Source table attributes

Source tables come from the DMS table statistics of every task, whatever its status. There is no "active only" filter at harvest time. Each source table carries:

- `system`: exactly as named in `source_systems.yaml`, for example `ARC1` or `COMPASS`
- `system_type`: `pas`, `ancillary`, `workflow`, `mi` or `other`. This is the system's role, not the database engine.
- `environment_code`: `PRD`, `PRA` or `PRW` for Composer
- `product`: `Composer` or `Compass`
- `product_table`: `<schema>.<table>` in lower case, for every Composer table
- `ingestion`
- `endpoints` and `dms_tasks`, both lists
- `replication_mode`: `ongoing` (CDC) or `one_off` (full load only)
- `task_status`
- `database`

Source tables have no column nodes: there is no source schema extract. Column structure comes from Distill (the Glue catalogue), and column meaning comes from the data dictionaries.

One-off systems such as MI_WAREHOUSE and ReportZone (full-load tasks that have finished) are included with `replication_mode = one_off`. They are never excluded, because Distill tables may still derive from them. The UI and question layer may hide them by default; that filter belongs to the view, not the harvest or the resolver.

### Edge semantics

For lineage, data flows from `src_key` to `dst_key`.

| edge_type | src_key | dst_key | Meaning |
| --- | --- | --- | --- |
| contains | parent | child | Table has column; state machine has state; dataset has field |
| references | child column | referenced column | A key relationship; see origin |
| derives_from | upstream table or column | downstream table or column | `transform_class` required; `attrs.level` = `table` or `column` |
| reads | job, function, DMS task or channel | table, location or extract | It reads it |
| writes | job, function, DMS task or channel | table, location or extract | It writes it |
| depends_on | dependent | dependency | `attrs.relation`: `invokes` (state → job or function), `runs_after` (job or state → predecessor), `starts` (state → child state machine), `triggered_by` (job, function or state machine → the S3 location whose notification starts it) |
| consumes | consumer | table, dataset or extract | A dashboard, API or outbound channel uses it |
| means | column | glossary term | See the R10 scope ladder |

EventBridge rules, schedules and Glue triggers are not nodes. They are stored on the target's `attrs.triggers` (rule or schedule name, schedule expression or event source summary).

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
| declared | Configuration or a data dictionary | certified |
| dictionary_fk | Foreign key stated in a data dictionary description | certified |
| llm_draft | Definition drafted by an LLM from quoted Confluence text | proposed |
| hudi_record_key | Hudi record key | certified |
| observed_join | A join seen in code or QuickSight | proposed |
| name_match | Normalised name and type match | inferred |

### Relationships (R8)

1. **Hudi record key:**
   - Read from `hoodie.table.recordkey.fields` and stored as `attrs.primary_key` (an ordered list) with `attrs.primary_key_origin` on the table, and `attrs.pk_ordinal` on each key column. Never as a self-referencing edge.
   - Columns on `key_discriminator_columns` (starting with `source_system`) go to `attrs.key_discriminators`.
2. **Foreign keys stated in a data dictionary.** A column description matching a configured pattern (starting with `Foreign key to the <table> table`) names a referenced table in the same product.
   - For each system on that product, the resolver finds the Distill replica of the referenced table.
   - The target is that table's primary key, and it must be a single column once discriminators are removed.
   - The edge runs from the column's Distill replica column to that key: origin `dictionary_fk`, certified, with `attrs.dictionary_ref`.
   - Any of the following is a gap, never a guess:
     - the referenced table isn't found in that system
     - the key is composite
     - the key's type differs from the column's
   - The edge never crosses systems.
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
  - a code template bound to two config items (two bound instances)
  - code and config that disagree on an output (both edges kept, plus a gap)
  - a placeholder with no binding (a gap)
  - a Step Functions parameter taken from execution input (a gap)
  - a `source_equivalent` that would cross systems (rejected)
  - COMPASS text written as TargetPlan (resolves)
  - a business term found at two scope levels (the more specific wins)
  - a partial run (unknown, not deleted)
  - an `sfn_parameter` item whose state machine invokes two generic jobs (a gap, no edge from the state machine)
  - a `data_mapping` item with an unmapped `transformation_type` (`unclassified`, plus a gap)
  - a `*_unique_ref` that matches tables in two databases (a gap)
  - a `generic_extract` item whose SQL file is missing (a gap)
  - a DMS task whose target is decided by the target endpoint's engine, even though its name suggests otherwise
  - a dictionary sheet whose header row is not row 3 (found by search)
  - a sheet name cut to 31 characters (the table name comes from A1)
  - a dictionary column linked to the replica columns in ARC1, ARC2 and ARC3 through one term node
  - `Foreign key to the member_account table` giving one edge per system, and a gap where the key is composite
  - a draft whose quote isn't in its excerpt (rejected)
  - a draft that names TargetPlan (system scope COMPASS)
  - a rerun that finds every column in the cache (no searches and no LLM calls)

## Exports (later)

- Exports are produced from the graph and the inventory by `kf export`. Harvesters never write them.
- They are written to `<data_root>/exports/<snapshot_id>/` as RFC 4180 CSV files, UTF-8, with ISO 8601 timestamps. The files are:
  - `source_inventory.csv`
  - `compute_inventory.csv`
  - `orchestration_inventory.csv`
  - `catalog_tables.csv`
  - `catalog_columns.csv`
  - `lineage_edges.csv`
  - `column_lineage.csv`
  - `consumer_inventory.csv`
  - `business_terms.csv`
- The Alation and OpenMetadata adapters export only what those tools cannot harvest themselves: ingestion and config lineage, source equivalents, and term links. Each row keeps the original AWS identifiers, so it can be mapped to the tool's own names.

## Question layer principles (later)

- The LLM classifies questions, resolves names (using system aliases) and phrases answers. Deterministic engines compute the answers.
- Core question families use fixed, tested tools. Free-form querying is a labelled fallback.
- Answers cite graph nodes and their tier, name any gaps, and state the business-term scope ("generic definition").

## How to work in this repository

- Show a plan before changing existing code, and wait for approval.
- Build one harvester or one resolver rule at a time. After each, show the run summary and the gaps report, then stop.
- Do not change the graph DDL, key patterns, edge semantics or this file without asking. Propose the change and explain why.
- Vendor extracts, inventory files, fixtures and gaps files are confidential and never committed.
