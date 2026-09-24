"""Backup critical production tables to a local SQL file.

Dumps each row of the protected tables as INSERT statements so the
backup can be replayed if historical bootstrap damages anything.

The backup is written to /home/z/my-project/download/pg_backup_<ts>.sql.gz
(compressed to save space).
"""
from __future__ import annotations

import gzip
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

# Tables to back up (all forward-production + system_config + base state).
# Order matters: parent tables first to satisfy FK relationships on replay.
PROTECTED_TABLES = [
    "system_config",
    "system_sync_state",
    "catchup_jobs",
    "missed_forward_captures",
    "forward_heartbeats",
    "forward_audit_log",
    "forward_outcomes",
    "forward_observations",
    "predictions",
    "prediction_outcomes",
    "research_news",
    "market_ticks",
    "market_candles",
    "basis_observations",
    "historical_market_states",
    "historical_outcomes",
    "historical_feature_snapshots",
    "similarity_runs",
    "build_jobs",
]


def quote(v) -> str:
    """Render a Python value as a SQL literal."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    # datetime / date / time / etc → ISO string with quotes
    s = str(v).replace("'", "''")
    return f"'{s}'"


def main() -> int:
    dsn = (
        f"host={os.environ['PG_PROXY_HOST']} port={os.environ['PG_PROXY_PORT']} "
        f"user={os.environ['PGUSER']} password={os.environ['PGPASSWORD']} "
        f"dbname={os.environ.get('PGDATABASE', 'railway')} sslmode=require connect_timeout=15"
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path("/home/z/my-project/download")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"pg_backup_{ts}.sql.gz"

    total_rows = 0
    with psycopg.connect(dsn) as conn, conn.cursor() as cur, gzip.open(out_path, "wt") as f:
        f.write(f"-- Backup generated at {ts}\n")
        f.write("-- Protected production tables: forward_*, system_*, etc.\n")
        f.write("-- Replay: psql -h <host> -U <user> -d <db> -f <this_file>.sql\n\n")
        f.write("BEGIN;\n\n")

        for tbl in PROTECTED_TABLES:
            cur.execute(f"SELECT count(*) FROM \"{tbl}\"")
            n = cur.fetchone()[0]
            print(f"  {tbl:35s} {n:>6d} rows", flush=True)
            total_rows += n

            if n == 0:
                f.write(f"-- {tbl}: 0 rows\n\n")
                continue

            # Get columns
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (tbl,)
            )
            cols = [r[0] for r in cur.fetchall()]
            col_list = ", ".join(f'"{c}"' for c in cols)

            # Stream rows
            cur.execute(f"SELECT {col_list} FROM \"{tbl}\" ORDER BY 1")
            written = 0
            f.write(f"-- {tbl}: {n} rows\n")
            while True:
                rows = cur.fetchmany(500)
                if not rows:
                    break
                for row in rows:
                    vals = ", ".join(quote(v) for v in row)
                    f.write(f'INSERT INTO "{tbl}" ({col_list}) VALUES ({vals});\n')
                    written += 1
            f.write("\n")
            print(f"    → dumped {written} rows", flush=True)

        f.write("COMMIT;\n")

    print(f"\nTotal rows backed up: {total_rows}")
    print(f"Backup file: {out_path}")
    print(f"Compressed size: {out_path.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
