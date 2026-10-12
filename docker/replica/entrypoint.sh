#!/bin/bash
set -e
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  until pg_isready -h primary -U postgres; do sleep 1; done
  pg_basebackup -h primary -U replicator -D "$PGDATA" -R -X stream -S replica_slot -P
  chmod 700 "$PGDATA"
fi
exec docker-entrypoint.sh postgres -c hot_standby=on
