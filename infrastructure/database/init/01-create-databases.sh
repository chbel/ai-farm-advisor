#!/bin/sh
# Create one database per microservice (database-per-service principle).
#
# The official Postgres image runs this automatically ONLY when the data volume
# is empty (first start). For an existing volume, run it manually; it is idempotent:
#   docker compose exec postgres sh /docker-entrypoint-initdb.d/01-create-databases.sh
set -eu

DATABASES="identity_db farm_db weather_db irrigation_db disease_db advisor_db"

for db in $DATABASES; do
  # \gexec runs the generated CREATE statement only when the database is missing.
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
	SELECT 'CREATE DATABASE $db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$db')\gexec
EOSQL
done

# pgvector is required by the AI Advisor knowledge base (RAG).
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname advisor_db \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"

echo "Service databases are ready: $DATABASES"
