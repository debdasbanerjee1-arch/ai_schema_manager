"""Knowledge Fabric graph profile.

Reads the graph named in <data_root>/graph/CURRENT (read-only) and writes a
JSON profile of its structure: counts, attrs keys and types, masked examples,
and readiness facts for the UI. No AWS calls. Nothing is written except the
output file.

Names are masked by default: every string becomes its "shape" (letters -> a,
capitals -> A, digits -> 9, punctuation kept), so separators and patterns are
visible but no names are. Short values under known enum-like keys (relation,
level, origin, ...) are kept, because the UI logic depends on them.

Usage (PowerShell):
    python kf_profile.py --data-root C:\\kf-data --out kf_profile.json
Options:
    --sample N        rows sampled per node or edge group (default 2000)
    --keep-names      do NOT mask strings (only if names may leave the environment)

Open the output and check it before sharing it.
"""

import argparse
import csv
import json
import platform
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb

# Keys whose short values are categories, not names. Kept unmasked.
ENUM_KEYS = {
    "relation", "level", "origin", "tier", "rule", "kind", "type", "layer",
    "system", "systems", "system_type", "environment_code", "product",
    "replication_mode", "task_status", "status", "family", "pattern",
    "transform_class", "distill_kind", "method", "trigger_type", "source",
    "primary_key_origin", "scope", "action", "mode", "enabled", "generated",
    "dms_added", "ingestion", "role", "engine", "transformation_type",
    "schedule_kind", "event_source", "detail_type", "state",
}
ENUM_MAX_LEN = 40
MAX_EXAMPLES = 3
MAX_LIST_ITEMS = 3


def shape(s: str) -> str:
    out = []
    for ch in s[:120]:
        if ch.isdigit():
            out.append("9")
        elif ch.isalpha():
            out.append("A" if ch.isupper() else "a")
        else:
            out.append(ch)
    # collapse long runs so length leaks less: aaaaaaa -> a+
    text = re.sub(r"(a{3,})", "a+", "".join(out))
    text = re.sub(r"(A{3,})", "A+", text)
    text = re.sub(r"(9{3,})", "9+", text)
    return text + ("…" if len(s) > 120 else "")


class Masker:
    def __init__(self, keep_names: bool):
        self.keep = keep_names

    def value(self, v, key=None):
        if isinstance(v, dict):
            return {k: self.value(x, k) for k, x in v.items()}
        if isinstance(v, list):
            items = [self.value(x, key) for x in v[:MAX_LIST_ITEMS]]
            if len(v) > MAX_LIST_ITEMS:
                items.append(f"… {len(v) - MAX_LIST_ITEMS} more")
            return items
        if isinstance(v, str):
            if self.keep:
                return v[:300]
            if key in ENUM_KEYS and len(v) <= ENUM_MAX_LEN:
                return v
            return "s:" + shape(v)
        return v  # numbers, booleans, null

    def text(self, s):
        if s is None:
            return None
        return s[:300] if self.keep else "s:" + shape(s)


def walk(obj, path, stats, enum_vals, masker):
    """Record type, presence and (for enum keys) values for every JSON path."""
    t = type(obj).__name__
    stats[path]["types"][t] += 1
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk(v, f"{path}.{k}", stats, enum_vals, masker)
    elif isinstance(obj, list):
        stats[path]["list_len"].append(len(obj))
        for v in obj[:20]:
            walk(v, f"{path}[]", stats, enum_vals, masker)
    elif isinstance(obj, str):
        key = re.sub(r"\[\]$", "", path.split(".")[-1])
        if key in ENUM_KEYS and len(obj) <= ENUM_MAX_LEN:
            enum_vals[path][obj] += 1
        else:
            stats[path]["shapes"][shape(obj)] += 1


def profile_group(rows, masker):
    """rows: list of dicts with 'attrs' (JSON text) and other fields."""
    stats = defaultdict(lambda: {"types": Counter(), "list_len": [], "shapes": Counter()})
    enum_vals = defaultdict(Counter)
    parsed = []
    for r in rows:
        try:
            a = json.loads(r["attrs"]) if isinstance(r["attrs"], str) else (r["attrs"] or {})
        except (TypeError, ValueError):
            a = {"__unparseable__": True}
        parsed.append(a)
        walk(a, "attrs", stats, enum_vals, masker)
    n = max(len(rows), 1)
    paths = {}
    for p, s in sorted(stats.items()):
        entry = {
            "present_pct": round(100 * sum(s["types"].values()) / n, 1) if "[]" not in p else None,
            "types": dict(s["types"]),
        }
        if s["list_len"]:
            ll = s["list_len"]
            entry["list_len"] = {"min": min(ll), "max": max(ll), "avg": round(sum(ll) / len(ll), 1)}
        if p in enum_vals:
            entry["values"] = dict(enum_vals[p].most_common(25))
        elif s["shapes"] and not masker.keep:
            entry["top_shapes"] = [k for k, _ in s["shapes"].most_common(3)]
        paths[p] = entry
    # pick examples with the most keys, so they show the richest structure
    order = sorted(range(len(parsed)), key=lambda i: -len(json.dumps(parsed[i])))
    examples = []
    for i in order[:MAX_EXAMPLES]:
        ex = {k: v for k, v in rows[i].items() if k != "attrs"}
        ex = {k: (masker.text(v) if k in ("node_key", "name", "src_key", "dst_key", "job_key",
                                          "parent_key", "expression") else v)
              for k, v in ex.items()}
        ex["attrs"] = masker.value(parsed[i])
        examples.append(ex)
    return {"sampled_rows": len(rows), "paths": paths, "examples": examples}


SAFE_SECOND = {"aws", "channel", "quicksight", "term", "src"}  # second segment is a type or engine


def key_prefix(k, keep):
    parts = k.split(".")
    if keep or len(parts) < 2:
        return ".".join(parts[:2])
    if parts[0] in SAFE_SECOND:
        return ".".join(parts[:2])
    return parts[0] + ".s:" + shape(parts[1])


TABLES = {"graph.node": "graph.node", "graph.edge": "graph.edge"}


def q(con, sql, params=None):
    for k, v in TABLES.items():
        sql = sql.replace(k, v)
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--out", default="kf_profile.json")
    ap.add_argument("--sample", type=int, default=2000)
    ap.add_argument("--keep-names", action="store_true")
    args = ap.parse_args()

    root = Path(args.data_root)
    current = (root / "graph" / "CURRENT").read_text(encoding="utf-8").strip()
    graph_path = root / "graph" / current
    snapshot_id = re.sub(r"^graph\.|\.duckdb$", "", current)
    masker = Masker(args.keep_names)
    # Attach under an alias: a file named graph.<id>.duckdb clashes with a schema named graph.
    con = duckdb.connect()
    con.execute(f"ATTACH '{graph_path.as_posix()}' AS kfg (READ_ONLY)")
    found = con.execute("""
        SELECT table_schema, table_name FROM information_schema.tables
        WHERE table_catalog = 'kfg' AND table_name IN ('node', 'edge')""").fetchall()
    schemas = {t: sc for sc, t in found}
    if "node" not in schemas or "edge" not in schemas:
        sys.exit(f"No node and edge tables found in {graph_path}: found {found}")
    TABLES["graph.node"] = f'kfg."{schemas["node"]}".node'
    TABLES["graph.edge"] = f'kfg."{schemas["edge"]}".edge'

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "masked": not args.keep_names,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "duckdb": duckdb.__version__,
        "graph_file": current,
        "snapshot_id": snapshot_id,
    }

    # 1. What is in the graph file
    out["tables"] = q(con, """
        SELECT table_schema, table_name, table_type
        FROM information_schema.tables
        WHERE table_catalog = 'kfg' AND table_schema NOT IN ('information_schema', 'pg_catalog')
        ORDER BY 1, 2""")
    out["columns"] = {
        f"{r['table_schema']}.{r['table_name']}": q(con, """
            SELECT column_name, data_type FROM information_schema.columns
            WHERE table_catalog = 'kfg' AND table_schema = ? AND table_name = ? ORDER BY ordinal_position""",
            [r["table_schema"], r["table_name"]])
        for r in out["tables"]
    }

    # 2. Counts
    out["counts"] = {
        "nodes_by_type_layer": q(con, """
            SELECT node_type, layer, count(*) AS n, sum(CASE WHEN deleted THEN 1 ELSE 0 END) AS deleted
            FROM graph.node GROUP BY 1, 2 ORDER BY 1, 2"""),
        "edges_by_type_origin_tier": q(con, """
            SELECT edge_type, origin, tier, transform_class, count(*) AS n
            FROM graph.edge GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4"""),
        "edge_endpoint_types": q(con, """
            SELECT e.edge_type, s.node_type AS src_type, s.layer AS src_layer,
                   d.node_type AS dst_type, d.layer AS dst_layer, count(*) AS n
            FROM graph.edge e
            LEFT JOIN graph.node s ON s.node_key = e.src_key
            LEFT JOIN graph.node d ON d.node_key = e.dst_key
            GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 6 DESC"""),
        "dangling_edges": q(con, """
            SELECT edge_type, count(*) AS n FROM graph.edge e
            WHERE NOT EXISTS (SELECT 1 FROM graph.node n WHERE n.node_key = e.src_key)
               OR NOT EXISTS (SELECT 1 FROM graph.node n WHERE n.node_key = e.dst_key)
            GROUP BY 1"""),
    }

    # 3. Readiness facts the UI depends on
    out["readiness"] = {
        "derives_from": q(con, """
            SELECT origin,
                   json_extract_string(attrs, '$.level') AS level,
                   count(*) AS n,
                   sum(CASE WHEN job_key IS NOT NULL THEN 1 ELSE 0 END) AS with_job_key,
                   sum(CASE WHEN json_extract(attrs, '$.binding') IS NOT NULL THEN 1 ELSE 0 END) AS with_binding
            FROM graph.edge WHERE edge_type = 'derives_from'
            GROUP BY 1, 2 ORDER BY 1, 2"""),
        "reads_writes_binding": q(con, """
            SELECT edge_type, origin, count(*) AS n,
                   sum(CASE WHEN json_extract(attrs, '$.binding') IS NOT NULL THEN 1 ELSE 0 END) AS with_binding
            FROM graph.edge WHERE edge_type IN ('reads', 'writes')
            GROUP BY 1, 2 ORDER BY 1, 2"""),
        "writes_without_derives_from": q(con, """
            SELECT count(*) AS n FROM graph.edge w
            WHERE w.edge_type = 'writes'
              AND NOT EXISTS (SELECT 1 FROM graph.edge d
                              WHERE d.edge_type = 'derives_from' AND d.dst_key = w.dst_key
                                AND d.job_key = w.src_key)"""),
        "depends_on_relations": q(con, """
            SELECT json_extract_string(attrs, '$.relation') AS relation, count(*) AS n
            FROM graph.edge WHERE edge_type = 'depends_on' GROUP BY 1 ORDER BY 2 DESC"""),
        "nodes_with_triggers": q(con, """
            SELECT node_type, count(*) AS n FROM graph.node
            WHERE json_extract(attrs, '$.triggers') IS NOT NULL GROUP BY 1"""),
        "nodes_with_source_equivalent": q(con, """
            SELECT node_type, layer, count(*) AS n FROM graph.node
            WHERE json_extract(attrs, '$.source_equivalent') IS NOT NULL GROUP BY 1, 2"""),
        "nodes_with_product_table": q(con, """
            SELECT node_type, layer, count(*) AS n FROM graph.node
            WHERE json_extract(attrs, '$.product_table') IS NOT NULL GROUP BY 1, 2"""),
        "consumes_and_means": q(con, """
            SELECT edge_type, count(*) AS n FROM graph.edge
            WHERE edge_type IN ('consumes', 'means', 'related_term', 'references') GROUP BY 1"""),
        "column_nodes_by_layer": q(con, """
            SELECT layer, count(*) AS n FROM graph.node WHERE node_type = 'column' GROUP BY 1"""),
    }

    # 4. attrs structure per node type and per edge type and origin
    node_types = [r["node_type"] for r in q(con, "SELECT DISTINCT node_type FROM graph.node ORDER BY 1")]
    out["node_attrs"] = {}
    for nt in node_types:
        rows = q(con, f"""
            SELECT node_key, layer, parent_key, name, data_type, CAST(attrs AS VARCHAR) AS attrs
            FROM graph.node WHERE node_type = ?
            ORDER BY hash(node_key) LIMIT {int(args.sample)}""", [nt])
        out["node_attrs"][nt] = profile_group(rows, masker)

    groups = q(con, "SELECT DISTINCT edge_type, origin FROM graph.edge ORDER BY 1, 2")
    out["edge_attrs"] = {}
    for g in groups:
        rows = q(con, f"""
            SELECT src_key, dst_key, transform_class, expression, tier, job_key,
                   CAST(attrs AS VARCHAR) AS attrs
            FROM graph.edge WHERE edge_type = ? AND origin = ?
            ORDER BY hash(edge_id) LIMIT {int(args.sample)}""", [g["edge_type"], g["origin"]])
        out["edge_attrs"][f"{g['edge_type']}|{g['origin']}"] = profile_group(rows, masker)

    # 5. Key patterns (masked shapes of node keys, per node type)
    out["key_shapes"] = {}
    for nt in node_types:
        keys = [r["node_key"] for r in q(con, f"""
            SELECT node_key FROM graph.node WHERE node_type = ?
            ORDER BY hash(node_key) LIMIT 500""", [nt])]
        out["key_shapes"][nt] = {
            "prefixes": dict(Counter(key_prefix(k, args.keep_names) for k in keys).most_common(10)),
            "examples": [masker.text(k) for k in keys[:3]],
        }
    con.close()

    # 6. Gaps (rule and reason are categories; objects are not exported)
    gaps_dir = root / "gaps" / snapshot_id
    gap_counts = Counter()
    gap_columns = None
    if gaps_dir.is_dir():
        for f in sorted(gaps_dir.glob("*.csv")):
            with f.open(encoding="utf-8", newline="") as fh:
                rdr = csv.DictReader(fh)
                gap_columns = gap_columns or rdr.fieldnames
                for row in rdr:
                    gap_counts[(row.get("rule", f.stem), row.get("reason", ""))] += 1
    out["gaps"] = {
        "folder_found": gaps_dir.is_dir(),
        "columns": gap_columns,
        "by_rule_reason": [{"rule": r, "reason": s, "n": n} for (r, s), n in sorted(gap_counts.items())],
    }

    # 7. Snapshot: which harvester runs it used, their status and record counts
    snap = root / "inventory" / "snapshots" / f"{snapshot_id}.json"
    runs = []
    if snap.is_file():
        try:
            sdata = json.loads(snap.read_text(encoding="utf-8"))
        except ValueError:
            sdata = {}
        text = json.dumps(sdata)
        for run_json in (root / "inventory" / "runs").glob("*/*/run.json"):
            run_id = run_json.parent.name
            if run_id not in text:
                continue
            try:
                rj = json.loads(run_json.read_text(encoding="utf-8"))
            except ValueError:
                continue
            runs.append({
                "harvester": run_json.parent.parent.name,
                "run_id": run_id,
                "mode": rj.get("mode"),
                "status": rj.get("status"),
                "record_counts": rj.get("record_counts") or rj.get("counts"),
                "api_actions": rj.get("api_actions") or rj.get("actions"),
            })
        out["snapshot"] = {"found": True, "top_level_keys": sorted(sdata.keys()) if isinstance(sdata, dict) else None,
                           "runs": sorted(runs, key=lambda r: r["harvester"])}
    else:
        out["snapshot"] = {"found": False}

    Path(args.out).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(f"Wrote {args.out} for {current} ({'masked' if not args.keep_names else 'NOT masked'}).")
    print("Open it and check it before sharing.")


if __name__ == "__main__":
    main()
