#!/bin/sh
# First start of an empty data volume only: the application's own role (not a superuser), its database, and the
# three extensions a superuser must enable. Everything after this — the schema — is Alembic's.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE neurocode LOGIN PASSWORD '${NEUROCODE_DB_PASSWORD}';
CREATE DATABASE neurocode OWNER neurocode;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname neurocode <<SQL
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;
ALTER SCHEMA public OWNER TO neurocode;
SQL
