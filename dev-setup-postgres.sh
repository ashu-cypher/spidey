#!/bin/bash
# Rebuilds the local SPIDEY dev database after a VM replacement.
# The Postgres data dir lives outside ~ (ephemeral), so re-run this if the DB is gone.
# Usage: bash ~/workspace/spidey/dev-setup-postgres.sh
set -e
echo "==> Installing PostgreSQL 16 + pgvector (needs root)..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql postgresql-contrib postgresql-16-pgvector
pg_ctlcluster 16 main start || service postgresql start || true
echo "==> Creating role and database..."
sudo -u postgres psql -c "DO \$\$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='spidey') THEN CREATE ROLE spidey LOGIN PASSWORD 'spidey_dev_local'; END IF; END \$\$;"
sudo -u postgres psql -c "SELECT 1 FROM pg_database WHERE datname='spidey'" | grep -q 1 || sudo -u postgres createdb -O spidey spidey
sudo -u postgres psql -d spidey -c "CREATE EXTENSION IF NOT EXISTS vector;"
echo "==> Verifying TCP connection..."
PGPASSWORD=spidey_dev_local psql -h 127.0.0.1 -U spidey -d spidey -c "SELECT extname, extversion FROM pg_extension WHERE extname='vector';"
echo "OK: spidey DB ready. Tables are auto-created by the backend on boot (Base.metadata.create_all)."
