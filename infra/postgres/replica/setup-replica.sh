#!/bin/sh
# Entrypoint override for the Market Data REPLICA.
#
# The replica must NEVER initdb — it is a byte-for-byte base backup of the
# primary. That is why its compose service has no POSTGRES_DB env var.
# Guarded on PG_VERSION so a restart does not re-clone.
set -e

if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "[replica] empty data dir, waiting for primary"
  until pg_isready -h postgres-market-primary -p 5432 -U "$POSTGRES_USER" >/dev/null 2>&1; do
    sleep 2
  done

  echo "[replica] taking base backup from primary"
  rm -rf "${PGDATA:?}"/*
  PGPASSWORD="$REPLICATION_PASSWORD" pg_basebackup \
      -h postgres-market-primary -p 5432 -U "$REPLICATION_USER" \
      -D "$PGDATA" -Fp -Xs -P -R -S market_replica_slot
  # -R writes standby.signal + primary_conninfo for us (Postgres >= 12)

  chmod 0700 "$PGDATA"
  echo "[replica] base backup complete, starting as hot standby"
else
  echo "[replica] existing data dir found, starting as hot standby"
fi

exec docker-entrypoint.sh postgres
