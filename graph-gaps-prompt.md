I profiled the current graph (snapshot 20261013T105815Z). Before any UI work, I need these investigated. **Report only: change no code and make no AWS calls.** For each item, give the count, five examples (keys and the relevant attrs), your diagnosis of the cause, and the fix you propose. Then stop.

1. **`R4 generic_extract_target_unresolved` (491).**
   - For five of them, show the write stage's `target_bucket` (logical), the physical bucket it resolved to through `common_config.buckets`, `target_bucket_key`, and the normalised location R2 tried to match.
   - Then show the nearest Glue table locations under that bucket.
   - Say which Glue databases hold the 1,362 Published-layer tables, and whether any generic_extract target falls under them.

2. **Domain tables have the wrong layer.** Domain tables (for example the adviser domain's `adviser_address`) belong to the **Published** layer. The graph has 177 tables with `distill_kind = domain` and layer `distill`, and 1,328 table-level `derives_from` edges from Distill to Distill.
   - Which Glue databases hold the 177 domain tables? Which setting in `config/` (`distill_databases`, `distill_prefixes` or the layer mapping) put them in Distill?
   - Which rule and config pattern produced the 1,328 Distill-to-Distill edges: `target_source_mapping` or generic_extract? How many of them end at a domain table?
   - Propose the config change that classifies the domain databases as Published, and what R7 should then do with `distill_kind` for them. Do not apply it.
   - After the reclassification, how many of the 491 unresolved generic_extract targets would you expect to resolve? Many may be domain targets that failed because of the layer the resolver searched.

3. **`R4 unresolved_ref` (1,083).** Group the cases by config table and by the Glue databases searched. Five examples.

4. **Tables with layer `none` (671).** List their Glue databases with counts, and say which `config/` setting would classify each.

5. **`R1 source_endpoint_unresolved` (2,260).** This equals the number of DMS `reads` edges. Is every DMS source endpoint unresolved, or is this an informational gap? What is missing?

6. **`R11 orphan_node` = exactly 500.** Is that a cap? Give the real count by node type.

7. **Table-level `derives_from` without `job_key` (144 of 1,808).** Which config patterns are they?

8. **Load times.** The UI needs "last loaded, as of snapshot" per hop. Neither DMS table statistics (`LastUpdateTime`) nor raw2dis bookmark timestamps (`bookmark_timestamp`, `last_job_start_time`) appear on any node or edge. Both are already in the inventory. Propose where R3 and R4 should store them (attribute names, on which node or edge).

9. **Triggers.** No node has `attrs.triggers`. Confirm that the events harvester has not run, and that Glue triggers from `glue_jobs` (`GetTriggers`) are not mapped by R5. List the API actions the events harvester would use, for my approval. Do not run it.

10. **`source_equivalent`.** In the sample, every `source_equivalent` has method `naming_convention`, none `dms+merge`, although raw2dis bindings should give `dms+merge`. Check whether R7 uses the raw2dis bindings. Also explain the 179 `source_equivalent_not_found`.
