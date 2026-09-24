"""Phase 5.5 pre-flight: record sanitized BEFORE counts for all important tables.

Reads PG_* env vars + PG_PROXY_HOST/PG_PROXY_PORT. Never prints credentials.
"""
from __future__ import annotations

import os
import sys
import psycopg
import json

PG_PROXY_HOST = os.environ.get("PG_PROXY_HOST", "kodama.proxy.rlwy.net")
PG_PROXY_PORT = int(os.environ.get("PG_PROXY_PORT", "59050"))
PGUSER = os.environ["PGUSER"]
PGPASSWORD = os.environ["PGPASSWORD"]
PGDATABASE = os.environ.get("PGDATABASE", "railway")

TABLES_TO_COUNT = [
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
    "missed_forward_captures",
    "system_config",
    "research_news",
    "catchup_jobs",
    "system_sync_state",
    "predictions",
    "prediction_outcomes",
]


def main() -> int:
    dsn = (
        f"host={PG_PROXY_HOST} port={PG_PROXY_PORT} "
        f"user={PGUSER} password={PGPASSWORD} dbname={PGDATABASE} "
        f"sslmode=require connect_timeout=15"
    )
    out = {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # Alembic revision
        cur.execute("SELECT version_num FROM alembic_version")
        out["alembic_revision"] = cur.fetchone()[0]

        # Per-table counts
        out["tables"] = {}
        for tbl in TABLES_TO_COUNT:
            cur.execute(f'SELECT count(*) FROM "{tbl}"')
            out["tables"][tbl] = cur.fetchone()[0]

        # market_candles breakdown by (instrument, interval, is_historical)
        cur.execute(
            "SELECT instrument, interval, is_historical, count(*) "
            "FROM market_candles GROUP BY instrument, interval, is_historical "
            "ORDER BY instrument, interval"
        )
        out["candles_breakdown"] = [
            {"instrument": r[0], "interval": r[1], "is_historical": r[2], "count": r[3]}
            for r in cur.fetchall()
        ]

        # market_candles earliest/latest per (instrument, interval, is_historical)
        cur.execute(
            "SELECT instrument, interval, is_historical, min(timestamp), max(timestamp), count(*) "
            "FROM market_candles GROUP BY instrument, interval, is_historical "
            "ORDER BY instrument, interval"
        )
        out["candles_range"] = [
            {
                "instrument": r[0], "interval": r[1], "is_historical": r[2],
                "earliest": r[3].isoformat() if r[3] else None,
                "latest": r[4].isoformat() if r[4] else None,
                "count": r[5],
            }
            for r in cur.fetchall()
        ]

        # forward_validation_started_at
        cur.execute("SELECT key, value FROM system_config WHERE key = 'forward_validation_started_at'")
        row = cur.fetchone()
        out["forward_validation_started_at"] = row[1] if row else None

        # build_jobs summary
        cur.execute("SELECT status, count(*) FROM build_jobs GROUP BY status ORDER BY status")
        out["build_jobs_by_status"] = {r[0]: r[1] for r in cur.fetchall()}

        # historical_market_states by feature_version
        cur.execute("SELECT feature_version, count(*) FROM historical_market_states GROUP BY feature_version")
        out["states_by_feature_version"] = {r[0]: r[1] for r in cur.fetchall()}

        # historical_outcomes by outcome_version
        cur.execute("SELECT outcome_version, count(*) FROM historical_outcomes GROUP BY outcome_version")
        out["outcomes_by_version"] = {r[0]: r[1] for r in cur.fetchall()}

        # historical_outcomes by horizon
        cur.execute("SELECT horizon_minutes, count(*) FROM historical_outcomes GROUP BY horizon_minutes ORDER BY horizon_minutes")
        out["outcomes_by_horizon"] = {r[0]: r[1] for r in cur.fetchall()}

        # similarity_runs summary
        cur.execute("SELECT count(*) FROM similarity_runs")
        out["similarity_runs_total"] = cur.fetchone()[0]
        cur.execute("SELECT id, created_at, candidate_count, similarity_version FROM similarity_runs ORDER BY created_at DESC LIMIT 5")
        out["similarity_runs_recent"] = [
            {"id": str(r[0]), "created_at": r[1].isoformat() if r[1] else None, "candidate_count": r[2], "similarity_version": r[3]}
            for r in cur.fetchall()
        ]

    # Dump
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
