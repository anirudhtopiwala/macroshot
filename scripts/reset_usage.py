#!/usr/bin/env python3
"""Reset per-user daily usage counters in the usage_tracking table.

Caps are enforced per (user_id, usage_type, period) where period is the user's
local ISO date (YYYY-MM-DD). Deleting a row makes that user's count zero again
for that day.

Usage:
  ./scripts/reset_usage.py --user-id 42                    # today, all features
  ./scripts/reset_usage.py --user-id 42 --feature image_query
  ./scripts/reset_usage.py --user-id 42 --date 2026-04-19
  ./scripts/reset_usage.py --user-id 42 --all-dates        # wipe all history
  ./scripts/reset_usage.py --all-users                     # everyone, today
  ./scripts/reset_usage.py --all-users --all-dates         # nuke the table

Runs PRAGMA wal_checkpoint(TRUNCATE) after the delete so the live uvicorn
doesn't shadow/revert the change via its own WAL view (see CLAUDE.md §
"SQLite WAL / Daily-Bugfix Pipeline").

Defaults DB to ./macro_app.db relative to the repo root; override with
--db /path/to.db or env MACRO_DB.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

FEATURES = ("image_query", "text_meal", "chat_session")
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "macro_app.db"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--user-id", type=int, help="Reset for this user_id")
    target.add_argument("--all-users", action="store_true", help="Reset for every user")

    p.add_argument(
        "--feature",
        choices=(*FEATURES, "all"),
        default="all",
        help="Which usage_type to clear (default: all)",
    )

    date_grp = p.add_mutually_exclusive_group()
    date_grp.add_argument("--date", help="ISO date (YYYY-MM-DD). Default: today UTC")
    date_grp.add_argument("--all-dates", action="store_true", help="Clear every period row")

    p.add_argument(
        "--db",
        default=os.environ.get("MACRO_DB", str(DEFAULT_DB)),
        help=f"SQLite path (default: {DEFAULT_DB} or $MACRO_DB)",
    )
    p.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    return p.parse_args()


def build_query(args: argparse.Namespace) -> tuple[str, list]:
    where = []
    params: list = []

    if not args.all_users:
        where.append("user_id = ?")
        params.append(args.user_id)

    if args.feature != "all":
        where.append("usage_type = ?")
        params.append(args.feature)

    if not args.all_dates:
        date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        where.append("period = ?")
        params.append(date_str)

    clause = " WHERE " + " AND ".join(where) if where else ""
    return f"DELETE FROM usage_tracking{clause}", params


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        return 1

    query, params = build_query(args)
    count_query = query.replace("DELETE FROM usage_tracking", "SELECT COUNT(*) FROM usage_tracking")

    conn = sqlite3.connect(str(db_path))
    try:
        (n,) = conn.execute(count_query, params).fetchone()
        print(f"DB: {db_path}")
        print(f"Matching rows: {n}")
        print(f"Query: {query}")
        print(f"Params: {params}")

        if n == 0:
            print("Nothing to delete.")
            return 0

        if not args.yes:
            resp = input("Proceed with DELETE? [y/N] ").strip().lower()
            if resp != "y":
                print("Aborted.")
                return 1

        conn.execute(query, params)
        conn.commit()

        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        print(f"Deleted {n} row(s). WAL checkpointed.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
