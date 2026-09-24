"""Query the live Railway Postgres for table count and contents.

Connects via the public TCP proxy. Prints a sanitized summary.
Never prints credentials or DSNs.
"""
from __future__ import annotations

import os
import sys

import psycopg

# Public TCP proxy endpoint — updated dynamically by caller
PROXY_HOST = os.environ.get("PG_PROXY_HOST", "iriguchi.proxy.rlwy.net")
PROXY_PORT = int(os.environ.get("PG_PROXY_PORT", "53294"))

# Pull credentials from environment (set by caller) — do not print
PGUSER = os.environ["PGUSER"]
PGPASSWORD = os.environ["PGPASSWORD"]
PGDATABASE = os.environ.get("PGDATABASE", "railway")


def main() -> int:
    dsn = (
        f"host={PROXY_HOST} port={PROXY_PORT} "
        f"user={PGUSER} password={PGPASSWORD} dbname={PGDATABASE} "
        f"sslmode=require connect_timeout=15"
    )
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # Alembic revision
            cur.execute("SELECT version_num FROM alembic_version")
            rev = cur.fetchone()
            print(f"ALEMBIC_REVISION={rev[0] if rev else 'NONE'}")

            # Total table count (excluding alembic_version)
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name != 'alembic_version'"
            )
            n = cur.fetchone()[0]
            print(f"TABLE_COUNT={n}")

            # List of tables
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name != 'alembic_version' "
                "ORDER BY table_name"
            )
            for (t,) in cur.fetchall():
                cur2 = conn.cursor()
                cur2.execute(f'SELECT count(*) FROM "{t}"')
                cnt = cur2.fetchone()[0]
                print(f"  - {t}: {cnt} rows")
                cur2.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
