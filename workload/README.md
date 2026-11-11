# Workload Profiles for pgagent Lab

This directory provides realistic background workloads and fault generators designed to test `pgagent` detection, policy gating, and autonomous healing on PostgreSQL 17 against the `bluebox` database.

## Architecture

Per environment instructions, all `pgbench` and `psql` executions run inside the primary PostgreSQL container via `docker compose exec`. No database client tools are installed on the host.

## Profiles

1. **`steady`**:
   - **Target**: Core `bluebox` tables (`film`, `customer`, `rental`).
   - **Role**: `bluebox_owner` (`application_name = 'workload_steady'`).
   - **Mechanism**: Custom `pgbench` script (`steady.sql`) simulating customer lookups, catalog browsing, and rental history queries.
   - **Expected Behavior**: Realistic OLTP baseline query traffic without generating abnormal bloat or uncommitted transaction blocks.

2. **`churn`**:
   - **Target**: Dedicated churn table `workload.churn_items` in dedicated schema `workload`.
   - **Role**: `bluebox_owner` (`application_name = 'workload_churn'`).
   - **Mechanism**: High-frequency UPDATE, DELETE, and INSERT loops with `autovacuum_enabled = false` on `workload.churn_items`.
   - **Expected Findings**: Rapidly accumulates dead tuples and modifications, triggering `table_bloat` (`bloat_dead_ratio >= 0.2`, `dead >= 10000`) and `stale_stats` (`mod_since_analyze >= 1000`) on `workload.churn_items` without corrupting real Bluebox tables.

3. **`chaos`**:
   - **Target**: Session-level activity and `workload.chaos_lock`.
   - **Role**: `bluebox_owner` (`application_name = 'workload_chaos'`).
   - **Mechanism**: Spawns long-running transactions and idle-in-transaction sessions. Some sessions hold row-level locks on `workload.chaos_lock` causing other sessions to block in `wait_event_type = 'Lock'`; other sessions remain idle in transaction without blocking anyone.
   - **Expected Findings**: Triggers `idle_in_txn` findings (> 30s idle time) and `lock_wait` findings (> 5s query wait).

## Usage

```bash
# Start a specific profile
./workload/run.sh start steady
./workload/run.sh start churn
./workload/run.sh start chaos

# Check status of processes, active sessions, and churn table metrics
./workload/run.sh status

# Stop all running workloads and terminate their database sessions
./workload/run.sh stop

# Reset workload tables and restore defaults
./workload/run.sh reset
```
