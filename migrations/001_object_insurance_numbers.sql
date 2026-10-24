-- Run against the existing PostgreSQL database before deploying this version.
-- create_all() only creates missing tables; it does not add columns.
BEGIN;
ALTER TABLE objects ADD COLUMN IF NOT EXISTS insurance_numbers JSONB;
COMMIT;
