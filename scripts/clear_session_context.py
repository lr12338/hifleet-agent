#!/usr/bin/env python3
"""Delete persisted context rows for a session_id."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from storage.database.db import get_db_url

TABLE_SPECS = [
    ("memory.checkpoint_writes", "thread_id"),
    ("memory.checkpoint_blobs", "thread_id"),
    ("memory.checkpoints", "thread_id"),
    ("observability.tool_invocations", "session_id"),
    ("observability.agent_errors", "session_id"),
    ("observability.api_calls", "session_id"),
    ("observability.chat_debug_sessions", "meta_session_id"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Clear LangGraph memory and observability rows for a session_id. "
            "Also deletes the internal ':standard_agent' sub-thread automatically."
        )
    )
    parser.add_argument("session_id", help="Original external session_id")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print row counts without deleting anything",
    )
    parser.add_argument(
        "--memory-only",
        action="store_true",
        help="Only clear memory.* checkpoint tables and keep observability records",
    )
    return parser.parse_args()


def target_ids(session_id: str) -> list[str]:
    return [session_id, f"{session_id}:standard_agent"]


def selected_table_specs(memory_only: bool) -> list[tuple[str, str]]:
    if not memory_only:
        return list(TABLE_SPECS)
    return [spec for spec in TABLE_SPECS if spec[0].startswith("memory.")]


def count_rows(cur: psycopg.Cursor, table: str, column: str, ids: list[str]) -> int:
    cur.execute(f"SELECT count(*) FROM {table} WHERE {column} = ANY(%s)", (ids,))
    return int(cur.fetchone()[0])


def delete_rows(cur: psycopg.Cursor, table: str, column: str, ids: list[str]) -> int:
    cur.execute(f"DELETE FROM {table} WHERE {column} = ANY(%s)", (ids,))
    return cur.rowcount


def main() -> int:
    args = parse_args()
    ids = target_ids(args.session_id)
    specs = selected_table_specs(args.memory_only)

    db_url = get_db_url()
    if not db_url:
        print("PGDATABASE_URL is unavailable; cannot clear persisted context.", file=sys.stderr)
        return 1

    print("Target session ids:")
    for item in ids:
        print(f"- {item}")

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            before = {table: count_rows(cur, table, column, ids) for table, column in specs}

            if args.dry_run:
                print("\nDry run row counts:")
                for table, _column in specs:
                    print(f"- {table}: {before[table]}")
                return 0

            deleted: dict[str, int] = {}
            for table, column in specs:
                deleted[table] = delete_rows(cur, table, column, ids)
            conn.commit()

        with conn.cursor() as cur:
            remaining = {table: count_rows(cur, table, column, ids) for table, column in specs}

    print("\nDeleted rows:")
    for table, _column in specs:
        print(f"- {table}: {deleted[table]}")

    print("\nRemaining rows:")
    for table, _column in specs:
        print(f"- {table}: {remaining[table]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
