"""
AI-Based Schema Designer & Data Manager
Uses OpenAI GPT to generate SQL schemas from natural language,
then manages data via PostgreSQL.....
"""

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Any, Dict
import openai
import psycopg2
import psycopg2.extras
import os
import re
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI Schema Designer & Data Manager",
    description="Design database schemas using natural language and manage your data via REST API.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Config ─────────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
DB_HOST        = os.getenv("DB_HOST", "db")
DB_PORT        = os.getenv("DB_PORT", "5432")
DB_NAME        = os.getenv("DB_NAME", "schemadb")
DB_USER        = os.getenv("DB_USER", "admin")
DB_PASS        = os.getenv("DB_PASS", "admin123")

# ── DB connection ──────────────────────────────────────────────────────────────
def get_db():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS
    )
    try:
        yield conn
    finally:
        conn.close()

# ── Pydantic models ────────────────────────────────────────────────────────────
class SchemaRequest(BaseModel):
    description: str          # e.g. "A hospital with patients, doctors, and appointments"
    db_type: str = "postgresql"

class SchemaResponse(BaseModel):
    description: str
    sql_ddl: str
    tables: List[str]
    explanation: str

class QueryRequest(BaseModel):
    nl_query: str             # e.g. "Show all patients admitted this month"

class InsertRequest(BaseModel):
    table: str
    data: Dict[str, Any]

class UpdateRequest(BaseModel):
    table: str
    data: Dict[str, Any]
    where: Dict[str, Any]

class DeleteRequest(BaseModel):
    table: str
    where: Dict[str, Any]

# ── AI helpers ─────────────────────────────────────────────────────────────────
def call_openai(system_prompt: str, user_prompt: str) -> str:
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set")
    client = openai.OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=2000,
    )
    return response.choices[0].message.content.strip()

def extract_sql(text: str) -> str:
    """Pull SQL out of markdown code fences if present."""
    match = re.search(r"```(?:sql)?\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else text.strip()

def extract_tables_from_ddl(ddl: str) -> List[str]:
    return re.findall(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", ddl, re.IGNORECASE)

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "service": "AI Schema Designer & Data Manager",
        "version": "1.0.0",
        "endpoints": [
            "POST /schema/generate  — Generate schema from description",
            "POST /schema/apply     — Apply DDL to PostgreSQL",
            "GET  /schema/list      — List all tables",
            "GET  /schema/{table}   — Get table schema",
            "POST /data/query       — Natural language SELECT query",
            "POST /data/insert      — Insert a record",
            "POST /data/update      — Update records",
            "POST /data/delete      — Delete records",
            "GET  /data/{table}     — List all rows in table",
        ]
    }

@app.get("/health")
def health():
    return {"status": "ok"}

# ── Schema endpoints ───────────────────────────────────────────────────────────

@app.post("/schema/generate", response_model=SchemaResponse)
def generate_schema(req: SchemaRequest):
    """Use AI to generate a PostgreSQL DDL from a natural language description."""
    system = (
        "You are a senior database architect. "
        "Given a business description, generate clean, normalized PostgreSQL DDL. "
        "Include primary keys, foreign keys, appropriate data types, and constraints. "
        "Output ONLY the SQL DDL inside a ```sql code block, followed by a brief explanation."
    )
    user = f"Design a {req.db_type} schema for: {req.description}"
    raw = call_openai(system, user)

    sql = extract_sql(raw)
    explanation_match = re.search(r"```\s*\n?(.*)", raw, re.DOTALL)
    explanation = explanation_match.group(1).strip() if explanation_match else "Schema generated successfully."
    tables = extract_tables_from_ddl(sql)

    return SchemaResponse(
        description=req.description,
        sql_ddl=sql,
        tables=tables,
        explanation=explanation
    )

@app.post("/schema/apply")
def apply_schema(payload: dict, conn=Depends(get_db)):
    """Execute DDL SQL against the connected PostgreSQL database."""
    ddl = payload.get("sql_ddl", "")
    if not ddl:
        raise HTTPException(status_code=400, detail="sql_ddl is required")
    try:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()
        tables = extract_tables_from_ddl(ddl)
        return {"status": "success", "tables_created": tables, "message": f"Created {len(tables)} table(s)"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/schema/list")
def list_tables(conn=Depends(get_db)):
    """List all user tables in the current database."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT table_name, pg_size_pretty(pg_total_relation_size(quote_ident(table_name))) AS size
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)
        return {"tables": cur.fetchall()}

@app.get("/schema/{table}")
def get_table_schema(table: str, conn=Depends(get_db)):
    """Return column definitions for a given table."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT column_name, data_type, character_maximum_length,
                   is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
        """, (table,))
        cols = cur.fetchall()
        if not cols:
            raise HTTPException(status_code=404, detail=f"Table '{table}' not found")
        return {"table": table, "columns": cols}

# ── Data endpoints ─────────────────────────────────────────────────────────────

@app.post("/data/query")
def nl_query(req: QueryRequest, conn=Depends(get_db)):
    """Convert a natural language question to SQL and execute it."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT table_name, string_agg(column_name || ' ' || data_type, ', ') AS columns
            FROM information_schema.columns
            WHERE table_schema = 'public'
            GROUP BY table_name
        """)
        schema_info = "\n".join(
            f"  {r['table_name']}({r['columns']})" for r in cur.fetchall()
        )

    system = (
        "You are a PostgreSQL expert. Given the database schema and a natural language question, "
        "output ONLY the SQL SELECT query inside a ```sql block. No explanation."
    )
    user = f"Schema:\n{schema_info}\n\nQuestion: {req.nl_query}"
    raw = call_openai(system, user)
    sql = extract_sql(raw)

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        return {"nl_query": req.nl_query, "sql": sql, "row_count": len(rows), "results": rows}
    except Exception as e:
        raise HTTPException(status_code=400, detail={"error": str(e), "sql": sql})

@app.get("/data/{table}")
def list_rows(table: str, limit: int = 50, conn=Depends(get_db)):
    """Fetch all rows from a table (up to limit)."""
    safe_table = re.sub(r"[^a-zA-Z0-9_]", "", table)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"SELECT * FROM {safe_table} LIMIT %s", (limit,))
        return {"table": safe_table, "rows": cur.fetchall()}

@app.post("/data/insert")
def insert_row(req: InsertRequest, conn=Depends(get_db)):
    """Insert a single row into a table."""
    safe_table = re.sub(r"[^a-zA-Z0-9_]", "", req.table)
    cols = list(req.data.keys())
    vals = list(req.data.values())
    placeholders = ", ".join(["%s"] * len(cols))
    col_str = ", ".join(cols)
    sql = f"INSERT INTO {safe_table} ({col_str}) VALUES ({placeholders}) RETURNING *"
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, vals)
            row = cur.fetchone()
        conn.commit()
        return {"status": "inserted", "row": row}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/data/update")
def update_row(req: UpdateRequest, conn=Depends(get_db)):
    """Update rows in a table matching the where clause."""
    safe_table = re.sub(r"[^a-zA-Z0-9_]", "", req.table)
    set_clause  = ", ".join([f"{k} = %s" for k in req.data.keys()])
    where_clause = " AND ".join([f"{k} = %s" for k in req.where.keys()])
    vals = list(req.data.values()) + list(req.where.values())
    sql = f"UPDATE {safe_table} SET {set_clause} WHERE {where_clause}"
    try:
        with conn.cursor() as cur:
            cur.execute(sql, vals)
            cnt = cur.rowcount
        conn.commit()
        return {"status": "updated", "rows_affected": cnt}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/data/delete")
def delete_row(req: DeleteRequest, conn=Depends(get_db)):
    """Delete rows from a table matching the where clause."""
    safe_table = re.sub(r"[^a-zA-Z0-9_]", "", req.table)
    where_clause = " AND ".join([f"{k} = %s" for k in req.where.keys()])
    vals = list(req.where.values())
    sql = f"DELETE FROM {safe_table} WHERE {where_clause}"
    try:
        with conn.cursor() as cur:
            cur.execute(sql, vals)
            cnt = cur.rowcount
        conn.commit()
        return {"status": "deleted", "rows_affected": cnt}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(e))
