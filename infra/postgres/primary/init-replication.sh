#!/bin/bash
# Runs once, on first init of the Market Data PRIMARY.
# Creates the replication role + slot and enables streaming replication.
# Mounted into /docker-entrypoint-initdb.d/ so the official image runs it after initdb.
set -e

echo "[primary] creating replication user and slot"
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<-EOSQL
  CREATE USER ${REPLICATION_USER} WITH REPLICATION LOGIN PASSWORD '${REPLICATION_PASSWORD}';
  SELECT pg_create_physical_replication_slot('market_replica_slot');
EOSQL

echo "[primary] enabling WAL streaming"
cat >> "$PGDATA/postgresql.conf" <<-EOF

# --- streaming replication (Mini Stock Exchange) ---
wal_level = replica
max_wal_senders = 10
max_replication_slots = 10
hot_standby = on
synchronous_commit = off
EOF

# Replica connects from inside the compose network.
echo "host replication ${REPLICATION_USER} 0.0.0.0/0 md5" >> "$PGDATA/pg_hba.conf"

echo "[primary] replication configured"
