"""Record sanitized BEFORE/AFTER snapshot of production PostgreSQL state.

Connects via Railway TCP proxy (PG_PROXY_HOST/PG_PROXY_PORT env vars).
Reads PGUSER/PGPASSWORD/PGDATABASE from environment.
NEVER prints credentials or DSNs.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import psycopg

TABLES = [
    "market_ticks",
    "market_candles",
    "historical_market_states",
    "historical_outcomes",
    "historical_feature_snapshots",
    "similarity_runs",
    "basis_observations",
    "build_jobs",
    "forward_observations",
    "forward_outcomes",
    "forward_audit_log",
    "forward_heartbeats",
    "system_config",
    "research_news",
    "catchup_jobs",
    "missed_forward_captures",
    "system_sync_state",
    "prediction_outcomes",
    "predictions",
]


def main() -> int:
    dsn = (
        f"host={os.environ['PG_PROXY_HOST']} port={os.environ['PG_PROXY_PORT']} "
        f"user={os.environ['PGUSER']} password={os.environ['PGPASSWORD']} "
        f"dbname={os.environ.get('PGDATABASE', 'railway')} sslmode=require connect_timeout=15"
    )
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "tables": {}}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # Alembic revision
        cur.execute("SELECT version_num FROM alembic_version")
        out["alembic_revision"] = cur.fetchone()[0]

        # Per-table counts
        for tbl in TABLES:
            cur.execute(f'SELECT count(*) FROM "{tbl}"')
            out["tables"][tbl] = cur.fetchone()[0]

        # market_candles breakdown by (interval, instrument, derivation, is_historical)
        cur.execute(
            "SELECT interval, instrument, derivation, is_historical, count(*) "
            "FROM market_candles GROUP BY interval, instrument, derivation, is_historical "
            "ORDER BY interval, instrument, derivation, is_historical"
        )
        out["candle_breakdown"] = [
            {"interval": r[0], "instrument": r[1], "derivation": r[2],
             "is_historical": r[3], "count": r[4]}
            for r in cur.fetchall()
        ]

        # Per-interval depth (earliest, latest, count)
        cur.execute(
            "SELECT interval, min(timestamp), max(timestamp), count(*) "
            "FROM market_candles GROUP BY interval ORDER BY interval"
        )
        out["candle_depth"] = [
            {"interval": r[0],
             "earliest": r[1].isoformat() if r[1] else None,
             "latest": r[2].isoformat() if r[2] else None,
             "count": r[3]}
            for r in cur.fetchall()
        ]

        # Forward-data preservation markers
        cur.execute("SELECT key, value FROM system_config ORDER BY key")
        out["system_config"] = {k: v for k, v in cur.fetchall()}

        # Forward observation ID range
        cur.execute("SELECT min(observation_id), max(observation_id), count(*) FROM forward_observations")
        r = cur.fetchone()
        out["forward_observations_range"] = {
            "min_id": r[0], "max_id": r[1], "count": r[2],
        }

        # Forward outcome ID range
        cur.execute("SELECT min(id), max(id), count(*) FROM forward_outcomes")
        r = cur.fetchone()
        out["forward_outcomes_range"] = {
            "min_id": r[0], "max_id": r[1], "count": r[2],
        }

        # Latest similarity run
        cur.execute("SELECT run_id, created_at, feature_version, similarity_version, "
                    "candidate_count, independent_neighbor_count, effective_days, "
                    "probability_calibrated, outcome_version "
                    "FROM similarity_runs ORDER BY created_at DESC LIMIT 5")
        out["latest_similarity_runs"] = [
            {"run_id": str(r[0]), "created_at": r[1].isoformat() if r[1] else None,
             "feature_version": r[2], "similarity_version": r[3],
             "candidate_count": r[4], "independent_neighbor_count": r[5],
             "effective_days": r[6], "probability_calibrated": r[7],
             "outcome_version": r[8]}
            for r in cur.fetchall()
        ]

        # Build jobs (latest 5)
        cur.execute("SELECT job_id, status, eligible_total, built, skipped_existing, "
                    "excluded_roll, excluded_gaps, excluded_insufficient_future, "
                    "remaining, percent_complete, started_at, completed_at, "
                    "feature_version, earliest_state, latest_state, error_message "
                    "FROM build_jobs ORDER BY started_at DESC LIMIT 5")
        out["latest_build_jobs"] = [
            {"job_id": str(r[0]), "status": r[1], "eligible_total": r[2],
             "built": r[3], "skipped_existing": r[4],
             "excluded_roll": r[5], "excluded_gaps": r[6],
             "excluded_insufficient_future": r[7],
             "remaining": r[8], "percent_complete": r[9],
             "started_at": r[10].isoformat() if r[10] else None,
             "completed_at": r[11].isoformat() if r[11] else None,
             "feature_version": r[12],
             "earliest_state": r[13].isoformat() if r[13] else None,
             "latest_state": r[14].isoformat() if r[14] else None,
             "error_message": r[15]}
            for r in cur.fetchall()
        ]

        # Outcomes by horizon
        cur.execute("SELECT horizon_minutes, count(*) FROM historical_outcomes "
                    "GROUP BY horizon_minutes ORDER BY horizon_minutes")
        out["outcomes_by_horizon"] = [
            {"horizon_minutes": r[0], "count": r[1]} for r in cur.fetchall()
        ]

    # Print as JSON to stdout
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
