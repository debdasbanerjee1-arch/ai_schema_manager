# Requirements Document

## Introduction

The Knowledge Fabric harvests metadata from a production AWS data estate and from documents people maintain, and resolves it into a queryable graph of tables, columns, jobs, pipelines, consumers and business definitions. It has six layers:

1. **Harvesters** read AWS through read-only API calls and write inventory records. They are the only code that calls AWS.
2. **Inventory store** holds the harvested records with run tracking and snapshots. It is the source of truth for facts.
3. **Importers and parsers** read declared inputs (configuration, data dictionaries, Confluence, code) and write inventory records. They make no AWS calls.
4. **Resolver** builds the whole graph from one snapshot. It is pure code and makes no AWS calls.
5. **Knowledge graph** is the queryable output: DuckDB for the PoC, Aurora PostgreSQL in production.
6. **Exports** produce files for other catalogue tools (deferred).

Key principles:

- **Harvest once, resolve many times.** Changing the model never needs AWS calls.
- **Three kinds of fact, never mixed:**
  - observed facts, from AWS
  - declared facts, maintained by people
  - derived facts, the graph
- **Deterministic resolution.** An LLM never invents lineage, keys or relationships. Its only use is drafting column definitions from quoted Confluence text, and those drafts are labelled.
- **Audit trail.** Every node and edge traces back to the inventory records and the snapshot it came from.

`.kiro/steering/knowledge-fabric.md` is the authority for rules, key patterns and edge semantics. Where this document and the steering file disagree, the steering file wins. Each requirement names the stage of `inventory-restructure-prompt.md` it belongs to.

## Glossary

- **Harvester:** a module that reads one AWS service with read-only calls and writes inventory records.
- **Importer:** a module that reads a declared input file and writes inventory records.
- **Parser:** a module that reads code or Confluence and writes inventory records.
- **Inventory store:** the JSONL files under `<data_root>/inventory/`.
- **Run:** one execution of a harvester, importer or parser, identified by `run_id` (UTC, `YYYYMMDDTHHMMSSZ`).
- **Snapshot:** one chosen run per harvester, importer and parser, plus the hashes of the configuration files used.
- **Envelope:** the standard wrapper around each inventory record.
- **Kind:** the record type, `<service>.<object_type>`, for example `dms.replication_task`.
- **Natural id:** the unique id of an object within its kind: the ARN, or a unique name.
- **Resolver rule:** one of R1 to R11, each a separate module that creates nodes, edges and gaps.
- **Gap:** an unresolved reference or missing item, recorded with a reason instead of being guessed.
- **Location spine:** the normalisation of every S3 reference to a single key, then matching it to Glue tables.
- **Channel:** an ingestion or outbound route, meaning an SFTP user, a file share or an outbound recipient.
- **Code template:** a parsed script whose unresolved values are named placeholders, bound later to the parameters that run it.
- **Bound instance:** a code template combined with one config item or parameter set.
- **Data dictionary:** an Excel workbook describing a product's tables, one sheet per table, for example the Composer Data Dictionary.
- **Scope ladder:** the order in which definitions apply: system, product_table, product_column, generic.
- **Draft definition:** a definition an LLM wrote from quoted Confluence text: origin `llm_draft`, tier proposed.
- **`data_root`:** the configured root for all data files. It must sit outside OneDrive or any synced folder.

## Requirements

---

## PART 1: HARVESTERS (AWS, read-only)

Every harvester follows these rules:

- It calls only its allowed API actions.
- It redacts before writing or logging.
- It writes only inventory records, never graph rows.
- It makes calls one at a time and paginates.
- It backs off on throttling.
- In live mode, it stops with status `failed` on any denied or throttled call.
- It supports `--mode live` and `--mode fixtures`.

### Requirement 1: DMS Harvester (Stage 1)

**User story:** As a data engineer, I want DMS endpoints, tasks and table statistics harvested, so that I have a complete inventory of database replication.

**Allowed actions:** `dms:DescribeEndpoints`, `dms:DescribeReplicationTasks`, `dms:DescribeTableStatistics`, `dms:DescribeReplicationInstances`.

**Redaction:** endpoint server name, port, user name, password, secret ARNs and connection attributes. `EngineName`, `DatabaseName` and the S3 target settings (bucket and folder) are kept.

#### Acceptance criteria

1. THE harvester SHALL write every endpoint as a `dms.endpoint` record, and every task as a `dms.replication_task` record.
2. THE harvester SHALL call `DescribeTableStatistics` for every task, whatever its status or migration type, and write `dms.table_statistics` records.
3. IF `DescribeTableStatistics` returns an error for one task, THE harvester SHALL record the error in `run.json` and continue with the other tasks. A denied or throttled response instead ends the run as `failed`.
4. THE harvester SHALL write `dms.replication_instance` records.

### Requirement 2: Transfer Family Harvester (Stage 3)

**User story:** As a data engineer, I want Transfer Family servers, users and connectors harvested, so that I know every SFTP ingestion and outbound route.

**Allowed actions:** `transfer:ListServers`, `DescribeServer`, `ListUsers`, `DescribeUser`, `ListConnectors`, `DescribeConnector`.

**Redaction:** SSH public keys, connector URLs, user secret ids and trusted host keys.

#### Acceptance criteria

1. THE harvester SHALL write `transfer.server`, `transfer.user` and `transfer.connector` records containing the full redacted responses, including home directory and home directory mappings.

### Requirement 3: Storage Gateway Harvester (Stage 3)

**User story:** As a data engineer, I want file shares harvested, so that I know every on-premises file ingestion route.

**Allowed actions:** `storagegateway:ListGateways`, `ListFileShares`, `DescribeNFSFileShares`, `DescribeSMBFileShares`.

**Redaction:** client lists, and the valid, invalid and admin user lists.

#### Acceptance criteria

1. THE harvester SHALL write `storagegateway.gateway` and `storagegateway.file_share` records containing the full redacted responses, including `LocationARN`.

### Requirement 4: S3 Prefix Harvester (Stage 3)

**User story:** As a data engineer, I want S3 prefixes enumerated, so that lineage can pass through locations the Glue catalogue doesn't register.

**Allowed actions:** `s3:ListObjectsV2` with a delimiter, on configured prefixes only; `s3:GetBucketNotificationConfiguration`, on configured buckets only.

#### Acceptance criteria

1. THE harvester SHALL read buckets, prefixes and their roles (`raw`, `inbound`, `outbound`) from `config/harvest.yaml`.
2. THE harvester SHALL list common prefixes to the maximum depth configured for each prefix, and SHALL stop at the configured maximum number of calls per run, recording `partial`.
3. Under `inbound` and `outbound` prefixes, THE harvester SHALL NOT write the `Contents` list (object names) at all. Only prefixes are recorded.
4. THE harvester SHALL write `s3.prefix` and `s3.bucket_notification` records.

### Requirement 5: Hudi Keys Harvester (Stage 1 from fixtures, Stage 3 live)

**User story:** As a data engineer, I want Hudi record keys harvested, so that primary keys are available for relationships.

**Allowed actions:** `s3:GetObject` on `<table location>/.hoodie/hoodie.properties` only.

#### Acceptance criteria

1. THE harvester SHALL take table locations from the Glue catalogue inventory, and build each properties path directly, never listing.
2. THE harvester SHALL write a `hudi.properties` record for each table.
3. A missing file SHALL be recorded as `not_hudi` or `missing`, not as an error.
4. THE harvester SHALL keep a cache and SHALL read only locations missing from it, unless `--refresh` is given.

### Requirement 6: Glue Catalogue Harvester (Stage 1)

**User story:** As a data engineer, I want the Glue Data Catalog harvested, so that I have every registered table and column.

**Allowed actions:** `glue:GetDatabases`, `glue:GetTables`.

#### Acceptance criteria

1. THE harvester SHALL write `glue.database` and `glue.table` records for every database, with columns, partition keys, parameters and view text, without filtering. Filtering is the resolver's job.

### Requirement 7: Glue Jobs Harvester (Stage 1, with triggers and workflows in Stage 3)

**User story:** As a data engineer, I want Glue jobs, triggers, workflows and connections harvested, so that I have a complete compute and scheduling inventory.

**Allowed actions:** `glue:GetJobs`, `GetTriggers`, `ListWorkflows`, `GetWorkflow` (with IncludeGraph), `GetConnections` (with HidePassword).

**Redaction:** default argument values whose keys are not on the allow-list in `config/harvest.yaml`, and connection properties.

#### Acceptance criteria

1. THE harvester SHALL write `glue.job`, `glue.trigger`, `glue.workflow` and `glue.connection` records.
2. `Command.ScriptLocation` SHALL be kept.

### Requirement 8: Lambda Harvester (Stage 1)

**User story:** As a data engineer, I want Lambda functions and their event sources harvested.

**Allowed actions:** `lambda:ListFunctions`, `GetFunctionConfiguration`, `ListEventSourceMappings`.

**Redaction:** environment variable values. Their names are kept.

#### Acceptance criteria

1. THE harvester SHALL write `lambda.function` and `lambda.event_source_mapping` records.
2. Deployment packages SHALL never be downloaded.

### Requirement 9: Step Functions Harvester (Stage 1)

**User story:** As a data engineer, I want state machines harvested with their definitions.

**Allowed actions:** `states:ListStateMachines`, `DescribeStateMachine`.

#### Acceptance criteria

1. THE harvester SHALL write `sfn.state_machine` records containing the raw definition.
2. Parsing the definition belongs to the resolver (R5), not the harvester.

### Requirement 10: EventBridge Harvester (Stage 3)

**User story:** As a data engineer, I want rules and schedules harvested, so that I know what starts orchestration.

**Allowed actions:** `events:ListRules`, `ListTargetsByRule`, `scheduler:ListSchedules`, `GetSchedule`.

**Redaction:** target input payloads.

#### Acceptance criteria

1. THE harvester SHALL cover the default event bus and any custom buses configured.
2. THE harvester SHALL write `events.rule`, `events.target` and `scheduler.schedule` records.

### Requirement 11: DynamoDB Config Harvester (Stage 1)

**User story:** As a data engineer, I want the job configuration tables harvested, so that generic jobs can be bound to their tables.

**Allowed actions:** `dynamodb:DescribeTable`, `dynamodb:Scan`, on allow-listed tables only.

**Redaction:** values matching the patterns in `config/redaction.yaml`.

#### Acceptance criteria

1. THE harvester SHALL read the tables and their patterns from `config/harvest.yaml` under `dynamodb_config.tables`. The patterns are `sfn_parameter`, `target_source_mapping`, `data_mapping` and `generic_extract`.
2. THE harvester SHALL scan one table at a time, with a page size of 25 (never above 100), backing off on throttling.
3. THE harvester SHALL write every item as a `dynamodb.config_item` record carrying its table name and pattern, without filtering.

### Requirement 12: Code Objects Harvester (Stage 4)

**User story:** As a data engineer, I want the SQL files and Glue scripts that jobs actually run, so that code lineage matches production.

**Allowed actions:** `s3:GetObject`, following the code object exception in the steering file.

#### Acceptance criteria

1. THE harvester SHALL read only these exact keys:
   - SQL paths named in `generic_extract` config items (`action_config.sql_query`)
   - each Glue job's `ScriptLocation`

   It SHALL never list.
2. THE harvester SHALL read only buckets listed under `code_objects.buckets`, files ending `.sql` or `.py`, and files of 2 MB or less. Any other key SHALL be skipped and recorded as a gap.
3. THE harvester SHALL redact lines matching the redaction patterns, then write `code.object` records containing the bucket, key, ETag, size and content.

### Requirement 13: QuickSight Harvester (Stage 6)

**User story:** As a data engineer, I want QuickSight datasets, analyses and dashboards harvested.

**Allowed actions:** `quicksight:ListDataSets`, `DescribeDataSet`, `ListDataSources`, `DescribeDataSource`, `ListAnalyses`, `DescribeAnalysisDefinition`, `ListDashboards`, `DescribeDashboardDefinition`.

**Redaction:** data source connection details and credentials.

#### Acceptance criteria

1. THE harvester SHALL write `quicksight.*` records containing the full redacted responses, including `PhysicalTableMap` and `LogicalTableMap`.

### Requirement 14: API Gateway Harvester (Stage 6)

**User story:** As a data engineer, I want APIs harvested with their integrations.

**Allowed actions:** `apigateway:GetRestApis`, `GetResources`, `GetIntegration`; `apigatewayv2:GetApis`, `GetRoutes`, `GetIntegrations`.

**Redaction:** integration credentials.

#### Acceptance criteria

1. THE harvester SHALL write `apigateway.*` records for REST and HTTP APIs, including Lambda integration ARNs.

---

## PART 2: INVENTORY STORE

### Requirement 15: Inventory Format (Stage 1)

#### Acceptance criteria

1. Files SHALL be laid out as follows:
   - records: `<data_root>/inventory/runs/<harvester>/<run_id>/<kind>.jsonl`
   - run metadata: `<data_root>/inventory/runs/<harvester>/<run_id>/run.json`
   - snapshots: `<data_root>/inventory/snapshots/<snapshot_id>.json`
2. Each line SHALL be one envelope with these fields, as defined in the steering file: `kind`, `natural_id`, `region`, `observed_at`, `source_api`, `harvester`, `harvester_version`, `redacted_paths` and `payload`.
3. `payload` SHALL be the full response item after redaction (capture wide, model narrow).
4. Declared inputs SHALL use the same envelope, with `source_api` set to the file path or page id and version.

### Requirement 16: Run Tracking (Stage 1)

#### Acceptance criteria

1. `run.json` SHALL be created with status `running`, and updated at the end to `complete`, `partial` or `failed`.
2. `failed` SHALL carry a `failed_reason` of `denied`, `throttled` or `error`.
3. `run.json` SHALL record:
   - the run id (UTC, `YYYYMMDDTHHMMSSZ`)
   - the harvester and its version
   - the mode, account alias and region
   - the start and end times
   - the exact API actions used
   - record counts per kind
   - errors

### Requirement 17: Snapshots (Stage 1)

#### Acceptance criteria

1. `kf snapshot create` SHALL choose the latest complete run of each harvester, importer and parser, unless a run is named with `--run <name>=<run_id>`.
2. A snapshot SHALL record its id, creation time, the chosen runs, and the hash of every configuration file.
3. A snapshot that includes a `partial` run SHALL carry `includes_partial: true`. The resolver SHALL treat anything missing from a partial run as unknown, never deleted.
4. A snapshot SHALL NOT include a `failed` run.

### Requirement 18: Redaction (Stage 1)

#### Acceptance criteria

1. Redaction SHALL follow `config/redaction.yaml`: JSON paths per kind, plus key-name patterns (password, secret, token, credential, apikey, privatekey, ssh).
2. Redaction SHALL happen in memory, before anything is written or logged.
3. Every redacted path SHALL be listed in `redacted_paths`.

---

## PART 3: IMPORTERS AND PARSERS (no AWS calls)

### Requirement 19: Configuration Validation (Stage 0 proposal, Stage 1 build)

#### Acceptance criteria

1. `kf validate` SHALL validate these files against pydantic schemas:
   - `config/source_systems.yaml`: all systems with `system_type`, `product`, `aliases`, `environment_code`, endpoints and databases, plus `distill_databases` and `distill_prefixes`
   - `config/harvest.yaml`: data root, regions, buckets and prefixes, DynamoDB tables and patterns, code object buckets, dictionaries, Confluence spaces, the Glue argument allow-list, generic names, `transform_class_map` and `script_map`
   - `config/channels.yaml`
   - `config/redaction.yaml`
2. Harvester settings SHALL live only in `config/harvest.yaml`. Settings in `config/sources.yaml` (`flow2.*`, `flow3.*`) SHALL move there.
3. Validation SHALL fail when:
   - there aren't exactly five systems with `system_type: pas`
   - an alias is shared by two systems
   - a Distill mapping names an unknown system
   - a JSON path or regex is invalid
   - `data_root` sits inside OneDrive or another synced folder
4. On failure, `kf validate` SHALL exit with code 2 and list every error. On success, it SHALL write `config.validated` records.

### Requirement 20: Channels Draft (Stage 3)

#### Acceptance criteria

1. `kf channels draft` SHALL propose `config/channels.yaml` from the Transfer Family and Storage Gateway inventories, listing each SFTP user, connector and file share with its S3 location, and leaving `system` or `recipient` blank for the user to fill in.

### Requirement 21: Data Dictionary Importer (Stage 5)

**User story:** As a data engineer, I want the Composer Data Dictionary imported, so that columns have definitions from the product documentation.

#### Acceptance criteria

1. THE importer SHALL read each workbook listed in `config/harvest.yaml` under `dictionaries`, with its `product`, its default `schema` and, optionally, a `system`.
2. For each sheet, THE importer SHALL:
   - take the table name from cell A1, removing the trailing ` table`
   - find the header row by searching for `column_name`
   - read the table description and table notes from row 2
   - read column rows until the first empty `column_name`
3. THE importer SHALL write `dictionary.table` and `dictionary.column` records containing every field:
   - `column_name`, `data_type`, `is_nullable`, `column_length`, `column_precision`, `column_scale` and `has_default_value`
   - Description and Instructions, kept separate
   - the source reference: file, file hash, sheet and row
4. A sheet that doesn't match the layout SHALL be recorded as a gap.
5. THE importer SHALL NOT enforce unique definitions. Conflicts are kept and flagged by R10.

### Requirement 22: Code Parser (Stage 4)

**User story:** As a data engineer, I want Glue and Lambda code parsed into templates, so that job I/O and column lineage come from code bound to its parameters.

#### Acceptance criteria

1. THE parser SHALL read Glue and Lambda code from the local Bitbucket clone at the configured path and ref, and SQL files and Glue scripts from `code.object` records. It SHALL never fetch from S3 itself.
2. THE parser SHALL use Python AST for PySpark and sqlglot (dialect `spark`) for SQL.
3. THE parser SHALL write these kinds:
   - `code.script`: repo, path, commit, hash, language
   - `code.io`: direction, target expression, line
   - `code.sql`
   - `code.column_map`: source columns, target column, expression
   - `code.config_access`: the config table and the key expression the script reads
4. Values it can't resolve SHALL become named placeholders, such as `${arg:source_table}` or `${config:target_path}`, never guesses.
5. Glue jobs SHALL be matched to scripts through `script_map`, and Lambda functions through their handler and name. A job without a match SHALL be a gap with reason `no_script`.
6. A parse error SHALL be a gap, not a failed run. The commit hash SHALL be recorded in `run.json`.

### Requirement 23: Definitions Drafted from Confluence (Stage 5)

**User story:** As a data engineer, I want definitions for columns the dictionary doesn't cover, drafted from Confluence with little token use and nothing invented.

#### Acceptance criteria

1. `kf definitions worklist` SHALL list Distill replica columns with no dictionary definition, excluding:
   - generic names, and technical and discriminator columns
   - columns already in the cache

   It SHALL deduplicate by product, table and column, and by column name, order the list with used columns first (those feeding Published or QuickSight), and report the counts.
2. `kf definitions search` SHALL run through the Confluence MCP, read-only:
   - one CQL search per distinct column name
   - restricted to the configured spaces
   - quoted snake_case, CamelCase and upper-case forms joined with OR, plus system aliases for COMPASS
   - at most 10 results

   It SHALL fetch each page once per version, and write `confluence.search_hit` and `confluence.page` records.
3. Code SHALL cut excerpts (the matching sentence with one sentence either side, or the matching table row):
   - at most 3 per page and 5 pages per column
   - about 1,500 characters per column

   A column without excerpts SHALL be a gap with reason `no_confluence_match`, and SHALL NOT be sent to the LLM.
4. `kf definitions batches` SHALL write batches of 25 columns, each with the prompt version (`prompts/definition_draft_v<n>.md`) and input hash.
5. For now, Kiro SHALL draft one batch at a time into answer files without echoing their content into the chat. The code SHALL NOT call an LLM.
6. `kf definitions ingest` SHALL validate each answer:
   - the JSON fields: `column`, `definition` (or null), `quote`, `page_id`, `system_mentioned` and `confidence`
   - the `quote` SHALL appear word for word in the named page's excerpt, or the draft SHALL be rejected with reason `quote_not_found`

   It SHALL write accepted drafts as `definition.draft` records.
7. The cache key SHALL be the input hash plus the prompt version. A rerun with nothing changed SHALL make no searches and no drafts.
8. A 50-column pilot SHALL come first, reporting:
   - searches made, pages fetched and excerpt characters
   - batches drafted, and tokens where known
   - drafts accepted, null and rejected

   A full run SHALL show its estimated size and wait for the user's approval.

---

## PART 4: RESOLVER RULES (no AWS calls)

The resolver reads one snapshot and runs R1 to R11 in order. Every node and edge carries:

- `attrs.rule`
- `attrs.evidence`: a list of `<kind>:<natural_id>` references
- `attrs.snapshot`

`attrs.flow` is not used.

### Requirement 24: R1 Nodes (Stage 2)

#### Acceptance criteria

1. Nodes SHALL use the steering file's key patterns, for example:
   - `src.sqlserver.PRD_AGComposer.dbo.policy`: `<engine>` is the endpoint's `EngineName` in lower case; for Oracle, the database is the SID or service name and the schema is the owning user
   - `glue.<database>.<table>[.<column>]`
   - `s3.<bucket>/<prefix>`, only for locations that don't match a Glue table
   - `aws.dms_task.<id>`, `aws.sfn.<name>[.<state_path>]`, `aws.glue_job.<name>` and `aws.lambda.<name>`
   - `aws.api.<api_id>.<method>.<route>`
   - `channel.sftp.<server_id>.<user>`, `channel.file_share.<share_id>` and `channel.outbound.<recipient>`
   - `quicksight.dataset.<id>[.<field>]` and `quicksight.dashboard.<id>`
   - `term.dict.<product>.<schema>.<table>.<column>`, and `term.<normalised term>.<hash>`
2. The original AWS identifiers SHALL be kept in `attrs.aws`.
3. When two rules set the same attribute to different values, R1 SHALL keep the first value and record a gap.

### Requirement 25: R2 Location Spine (Stage 2)

#### Acceptance criteria

1. Every S3 reference SHALL be normalised:
   - `s3a://` and `s3n://` become `s3://`
   - the bucket is lower case
   - repeated and trailing slashes are removed
   - the key keeps its casing
2. A reference SHALL resolve to the Glue table whose normalised location is the longest prefix of it.
3. An `s3.` node SHALL be created only when no Glue table matches. A location that matches a Glue table SHALL NOT get a second node.
4. A location under a configured outbound prefix SHALL be an `extract` node.
5. The layer of an uncatalogued location SHALL come from the configured bucket and prefix roles.

### Requirement 26: R3 Ingestion Lineage (Stage 2 for DMS, Stage 3 for channels)

#### Acceptance criteria

1. Source table nodes SHALL come from the table statistics of every DMS task.
2. Each source table node SHALL carry:
   - `system`, exactly as in `source_systems.yaml` (for example `ARC1`)
   - `system_type`: `pas`, `ancillary`, `workflow`, `mi` or `other`
   - `environment_code`, `product`, and `product_table` (`<schema>.<table>` in lower case, for Composer)
   - `ingestion`, `endpoints` and `dms_tasks` (lists), `replication_mode` (`ongoing` or `one_off`), `task_status` and `database`

   Source tables SHALL have no column nodes.
3. Each DMS task SHALL `read` its source tables and `write` its Raw locations, with origin `dms_mapping` and tier certified. The Raw path SHALL come from the target endpoint's bucket and folder, plus any table mapping rules.
4. Each source table SHALL get a table-level `derives_from` edge to its Raw table, with:
   - `transform_class = identity`
   - `job_key` set to the DMS task
   - `attrs.level = table`
5. A task's target type SHALL come from the target endpoint's `EngineName`. Non-S3 targets SHALL be recorded but not modelled.
6. Channels SHALL `write` their landing locations, with origin `transfer_config` or `gateway_config` and tier certified.

### Requirement 27: R4 Job I/O (Stage 2 for config, Stage 4 for code)

#### Acceptance criteria

1. `reads` and `writes` edges SHALL start at the Glue job or Lambda function, never at the state machine.
2. Config items SHALL be handled by pattern:
   - **`target_source_mapping`:** table-level lineage from `input_unique_ref` to `target_unique_ref`.
   - **`sfn_parameter`:** the `reads` edge SHALL go to the single generic job the state machine invokes (through R5), with `attrs.via_state_machine`. If there are several candidate jobs, record a gap with reason `ambiguous_job`, add the table to the state machine's `attrs.processes_tables`, and create no edge.
   - **`data_mapping`:** column-level `derives_from` (see R6).
   - **`generic_extract`:** the SQL file from `code.object`, parsed into a template and bound to the item.
3. `*_unique_ref` values SHALL resolve by exact normalised table name within the Glue databases configured for the layer. No match is a gap with reason `unresolved_ref`, and several matches are a gap with reason `ambiguous_ref`.
4. Literal Step Functions parameters SHALL give origin `sfn_parameters`, certified. A value taken from execution input (a `.$` path) SHALL be a gap.
5. Code templates SHALL be bound to each parameter set that runs them:
   - code and parameters agree: one certified edge that cites both
   - I/O found only in code: an extra edge with origin `parsed` or `parsed_partial`
   - code and parameters disagree: both edges kept, plus a gap
   - an unbound placeholder: a gap with reason `unbound_parameter`
6. Every table-level `derives_from` edge from a generic job SHALL carry:
   - `transform_class = unclassified`
   - `job_key`
   - `attrs.level = table`
   - `attrs.config_table` and `attrs.config_key`

### Requirement 28: R5 Orchestration (Stage 2 for Step Functions, Stage 3 for triggers)

#### Acceptance criteria

1. `contains` edges SHALL run from each state machine to its states, including Parallel branches and Map `ItemProcessor` or `Iterator`. State paths SHALL be joined with `/`.
2. `depends_on` edges SHALL use these relations:
   - `invokes`: state to job or function
   - `runs_after`: between sequential states, and from Glue conditional triggers and workflows, with the condition state in attrs
   - `starts`: state to child state machine
   - `triggered_by`: job, function or state machine to the S3 location whose notification starts it
3. Catch paths SHALL be marked `via: catch`. Lambda names SHALL be normalised from ARNs, aliases and versions. A dynamic (`.$`) job or function name SHALL be a gap.
4. EventBridge rules, schedules and Glue triggers SHALL NOT be nodes. They SHALL be stored on the target's `attrs.triggers` (and schedules also on `attrs.schedules`).

### Requirement 29: R6 Lineage (Stage 2 for tables, Stages 4 and 5 for columns)

#### Acceptance criteria

1. Table-level `derives_from` edges SHALL come first, from R3 and R4.
2. Column-level `derives_from` edges SHALL come from:
   - `data_mapping` config items: origin `dynamodb_config`, certified, with `expression = transformation_name`
   - bound code: origin `parsed` or `parsed_partial`
3. `transform_class` for config SHALL come from `transform_class_map`. An unmapped `transformation_type` SHALL be `unclassified`, and its distinct values SHALL be listed in the gaps report. It SHALL never default to `derivation`.
4. Every `derives_from` edge SHALL carry `attrs.level` (`table` or `column`).
5. Expressions SHALL be stored in full. An expression over 1,000 characters SHALL set `attrs.expression_truncated = true` and `attrs.expression_ref`, pointing to its inventory record.

### Requirement 30: R7 Distill Classification (Stage 2)

#### Acceptance criteria

1. `distill_kind` SHALL be `replica`, `domain`, `view`, `downstream` or `unknown`.
2. `system` SHALL come from `distill_databases` or `distill_prefixes`. A table built from several systems SHALL carry `attrs.systems` (a list, derived from its upstream tables) instead.
3. `source_equivalent` SHALL be set on replicas only, in this order:
   1. `dms+merge` (certified)
   2. `naming_convention` (certified): parsed with `distill_prefixes` and verified against that system's DMS table statistics
   3. `name_match` (inferred): within one source database

   A disagreement between 1 and 2 SHALL be a gap.
4. `source_equivalent` SHALL never cross systems, for any system, and SHALL never point to a missing node. Either case SHALL be a gap.

### Requirement 31: R8 Keys and Relationships (Stage 2, with dictionary foreign keys in Stage 5)

#### Acceptance criteria

1. The Hudi record key SHALL set:
   - on the table: `attrs.primary_key` (an ordered list) and `attrs.primary_key_origin = hudi_record_key`
   - on each key column: `attrs.pk_ordinal`
   - on the table: `attrs.key_discriminators`, for columns on `key_discriminator_columns`

   There SHALL be no self-referencing edges.
2. Foreign keys stated in the dictionary (`Foreign key to the <table> table`, from configured patterns) SHALL give, for each system on the product, an edge from the column's replica column to the referenced replica table's single-column key:
   - origin `dictionary_fk`, certified
   - `attrs.dictionary_ref`

   A missing table, a composite key or a type mismatch SHALL be a gap. The edge SHALL never cross systems.
3. Observed joins from parsed code and QuickSight SHALL give edges with tier proposed, carrying `attrs.occurrences` and `attrs.seen_in`. For Composer, a join may be proposed for the other ARC instances through `product_table`.
4. Name-match edges SHALL be created only when all of these hold:
   - the key is a single column once discriminators are removed
   - the key name is the primary key of exactly one table in the same source database
   - the normalised name matches, with the same base type
   - the name is longer than 3 characters and not on the generic-names list
   - the column has no stronger relationship
5. There SHALL be no duplicate `references` edges for the same column pair.
6. Names SHALL be compared in lower case with underscores removed, and any collisions this creates SHALL be reported.

### Requirement 32: R9 Consumption (Stage 6)

#### Acceptance criteria

1. Each QuickSight dataset SHALL `consume` the Glue tables in its `PhysicalTableMap`, and each dashboard SHALL `consume` its datasets.
2. Each API endpoint SHALL `consume` the tables its Lambda integration reads, with `attrs.via` set to the Lambda key.
3. For outbound extracts:
   - the job `writes` the extract
   - lineage runs from the source tables to the extract through `derives_from`
   - the outbound channel `consumes` the extract
4. Consumer nodes SHALL carry `attrs.consumer_type`: `dashboard`, `dataset`, `api` or `extract`.

### Requirement 33: R10 Business Terms (Stage 5)

#### Acceptance criteria

1. Each dictionary column SHALL become one `glossary_term` node:
   - `attrs.term_kind = column_definition`
   - the Description is the definition
   - the Instructions text is kept in `attrs.notes`, never merged into the definition
   - the data type, nullability, length, precision and scale are kept in attrs
2. Each dictionary table description SHALL become a `table_definition` term.
3. `means` edges SHALL run from column to term: from the matching replica column in every system on the product, matched through `source_equivalent` and the normalised column name.
4. Scope SHALL follow the ladder: `system`, then `product_table`, then `product_column`, then `generic`, where the most specific wins. A dictionary gives `product_table` scope, or `system` scope when it names a system. A draft gives `system` scope when `system_mentioned` resolves, otherwise `product_table`.
5. A dictionary definition SHALL win over a draft at the same or a wider scope. Drafts SHALL have origin `llm_draft` and tier `proposed`, and SHALL carry the quote and page id.
6. `means` edges SHALL carry `attrs.term_scope`, `attrs.term_source` and `attrs.conflict`. On a conflict, all candidates SHALL be kept.
7. Generic-level matches SHALL NOT be made for names on the generic-names list.
8. These SHALL be gaps:
   - a dictionary table without a replica, with reason `dictionary_table_not_found`
   - a dictionary column missing from a replica, with reason `dictionary_column_not_found`
9. Downstream columns SHALL inherit definitions at query time through `identity` and `meaning_preserving` lineage. The inherited links SHALL NOT be stored.

### Requirement 34: R11 Verification (Stage 2)

#### Acceptance criteria

1. The verification SQL SHALL live in `scripts/sql/` and run on every build.
2. These checks SHALL be blocking. Any failure means the new graph is not made current:
   - `source_equivalent` crossing systems, or pointing to a missing node
   - `source_equivalent` on a non-replica table
   - wrong key patterns, including `pas_mssql.` and `src.mssql.`
   - duplicate `references` edges
   - edges whose `src_key` or `dst_key` has no node
3. All other checks SHALL be reported. These include required attributes, gap counts by rule and reason, and coverage of replica columns without a definition.
4. Gaps SHALL be written to `<data_root>/gaps/<snapshot_id>/<rule>.csv` with the columns `rule, object, location, reason, detail, evidence`.
5. A run report SHALL be written to `<data_root>/reports/resolver/<snapshot_id>.json` with node and edge counts by type, origin and tier.

---

## PART 5: KNOWLEDGE GRAPH

### Requirement 35: Graph Storage and Switching (Stage 2)

#### Acceptance criteria

1. The graph SHALL use the `graph.node` and `graph.edge` tables in the steering file, including the `channel` node type.
2. Each build SHALL write a new file, `<data_root>/graph/graph.<snapshot_id>.duckdb`, loaded in bulk from Arrow tables.
3. Only when the blocking checks pass SHALL `<data_root>/graph/CURRENT` be rewritten to name the new file.
4. The graph in use SHALL never be deleted, overwritten or renamed. Readers SHALL open the file named in `CURRENT` read-only.
5. The last 3 graph files SHALL be kept, and older ones deleted only when nothing has them open.
6. A full build SHALL finish in under 10 minutes for the current estate, printing progress and final counts.

### Requirement 36: Legacy Comparison (Stage 2, one-off)

#### Acceptance criteria

1. THE system SHALL compare the first new graph with `graph.legacy.duckdb`, covering:
   - counts by node type and layer, and by edge type and origin
   - keys found in only one of the two graphs, with 10 examples each
   - `source_equivalent` links that are the same, changed or removed

   Each difference SHALL be explained. The F1 source columns and foreign keys are expected to be missing, because the PAS extract is retired.

---

## PART 6: EXPORTS (deferred)

### Requirement 37: CSV Export (Stage 7)

#### Acceptance criteria

1. `kf export --format csv` SHALL write files to `<data_root>/exports/<snapshot_id>/`, with these contents:
   - `source_inventory`, `compute_inventory`, `orchestration_inventory`
   - `catalog_tables`, `catalog_columns`
   - `lineage_edges`, `column_lineage`
   - `consumer_inventory`, `business_terms`
2. The files SHALL be RFC 4180 CSV, UTF-8, with ISO 8601 timestamps.
3. Each row SHALL carry the original AWS identifiers and `snapshot_id`.
4. A manifest SHALL list each file with its row count.

### Requirement 38: Alation and OpenMetadata Exports (deferred)

#### Acceptance criteria

1. Nothing SHALL be built for these tools until the user decides.
2. When built, they SHALL export only what the tools cannot harvest themselves: ingestion and config lineage, source equivalents and term links.

---

## PART 7: OPERATIONS AND SAFETY

### Requirement 39: Command Line (Stage 1)

#### Acceptance criteria

1. THE CLI SHALL provide the commands in the steering file's command table.
2. `--mode fixtures` SHALL be the default.
3. `--mode live` SHALL print the exact API actions for every harvester involved and wait for confirmation. `--yes` SHALL skip the confirmation, and is only for approved scheduled runs.
4. Exit codes SHALL be 0 for success, 1 for completed with gaps and 2 for an error.

### Requirement 40: Full Pipeline (Stage 2)

#### Acceptance criteria

1. `kf refresh` SHALL run all harvesters, all importers, snapshot creation and the resolver, in that order.
2. It SHALL stop at the first run that ends `failed`. That always includes a denied or throttled call in live mode.
3. It SHALL continue past `partial` runs, and past runs completed with gaps.
4. It SHALL print progress and a final summary.

### Requirement 41: Fixtures (Stage 1)

#### Acceptance criteria

1. Fixtures SHALL only be created by `kf fixtures save <harvester> <run_id>` from a completed live run, which is already redacted.
2. THE README SHALL NOT tell anyone to create fixtures with the AWS CLI or the console.
3. Fixtures mode SHALL produce the same inventory records as live mode, apart from `observed_at` and the run metadata.
4. Existing fixtures under `data/fixtures/` SHALL be converted once into inventory runs during stage 1.

### Requirement 42: Security and Compliance (all stages)

#### Acceptance criteria

1. The production account rules in the steering file SHALL apply to every AWS call.
2. Only the allowed actions SHALL be used, and no mutating action ever.
3. `s3:GetObject` SHALL be limited to `hoodie.properties` files and the code object exception, as the steering file words them.
4. DynamoDB SHALL be read only on allow-listed tables, under the scan limits.
5. A denied or throttled call SHALL stop the run and be reported, never worked around.
6. Inventory, fixtures, graph, gaps, definitions and export files SHALL live under `data_root` and never be committed.
7. The Confluence MCP SHALL be used read-only.

### Requirement 43: No Hard-coded Names (Stage 1)

#### Acceptance criteria

1. A test SHALL fail if any database name, Distill prefix, system name, bucket or DynamoDB table name appears in code outside configuration, fixtures and tests.

### Requirement 44: Tests (all stages)

#### Acceptance criteria

1. Tests SHALL use recorded, redacted fixtures and moto. No test SHALL call AWS or Confluence.
2. Each harvester SHALL have a redaction test. Its fixture SHALL contain every sensitive field listed for that harvester, and the test SHALL assert that none of them appear in the output or the logs.
3. Every resolver rule SHALL have positive and negative cases, including every case listed in the steering file's Tests section.
