# AI Schema Designer & Data Manager

AI-powered REST API that generates PostgreSQL schemas from plain English
and provides full CRUD data management — all running in Docker.

## Quick Start

```bash
 

# 1. Start all services
docker compose up --build -d

# 2. Open Swagger UI
open http://localhost:8000/docs
```

## Services

| Service  | URL                       | Purpose              |
|----------|---------------------------|----------------------|
| API      | http://localhost:8000     | FastAPI application  |
| Swagger  | http://localhost:8000/docs| Interactive API docs |
| pgAdmin  | http://localhost:5050     | DB visual manager    |
| Postgres | localhost:5432            | Database             |

## Key Endpoints

| Method | Endpoint           | Description                          |
|--------|--------------------|--------------------------------------|
| POST   | /schema/generate   | AI generates DDL from description    |
| POST   | /schema/apply      | Execute DDL on PostgreSQL            |
| GET    | /schema/list       | List all tables                      |
| GET    | /schema/{table}    | Get table column definitions         |
| POST   | /data/query        | Natural language SELECT query        |
| GET    | /data/{table}      | List all rows                        |
| POST   | /data/insert       | Insert a record                      |
| POST   | /data/update       | Update records                       |
| POST   | /data/delete       | Delete records                       |

## Example Workflow

```bash
# Step 1 — Generate schema
curl -X POST http://localhost:8000/schema/generate \
  -H "Content-Type: application/json" \
  -d '{"description": "A hospital with patients, doctors, appointments, and prescriptions"}'

# Step 2 — Apply the returned DDL
curl -X POST http://localhost:8000/schema/apply \
  -H "Content-Type: application/json" \
  -d '{"sql_ddl": "<paste DDL from step 1>"}'

# Step 3 — Insert data
curl -X POST http://localhost:8000/data/insert \
  -H "Content-Type: application/json" \
  -d '{"table": "patients", "data": {"name": "Arjun Sharma", "dob": "1985-03-10", "gender": "M"}}'

# Step 4 — Query in plain English
curl -X POST http://localhost:8000/data/query \
  -H "Content-Type: application/json" \
  -d '{"nl_query": "Show all patients who are male"}'
```

## Stop Services

```bash
docker compose down          # Stop containers
docker compose down -v       # Stop and remove data volumes
```
