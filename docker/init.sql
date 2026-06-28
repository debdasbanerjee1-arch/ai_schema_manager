-- Initial setup for AI Schema Manager DB
-- This runs once on first container startup

-- Enable useful extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

-- Audit log table (tracks all schema changes)
CREATE TABLE IF NOT EXISTS _schema_audit (
    id          SERIAL PRIMARY KEY,
    action      VARCHAR(50)  NOT NULL,
    table_name  VARCHAR(255),
    description TEXT,
    executed_at TIMESTAMPTZ  DEFAULT NOW()
);

-- Insert initial audit entry
INSERT INTO _schema_audit (action, description)
VALUES ('INIT', 'Database initialised by AI Schema Manager');
