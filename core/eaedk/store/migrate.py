"""Forward-only migration runner.

Applies any ``NNNN_*.sql`` whose leading number exceeds ``PRAGMA user_version``, inside
a transaction, then bumps ``user_version``. No down-migrations in the MVP (spec §1.9): a
corrupt local DB is recreated and reseeded.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .db import user_version

_MIGRATIONS = Path(__file__).resolve().parent / "migrations"
_NAME_RE = re.compile(r"^(\d{4})_.*\.sql$")


def _migration_files() -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    for p in sorted(_MIGRATIONS.glob("*.sql")):
        m = _NAME_RE.match(p.name)
        if not m:
            raise ValueError(f"migration filename must be NNNN_*.sql: {p.name}")
        out.append((int(m.group(1)), p))
    return out


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply pending migrations. Returns the list of versions applied."""
    current = user_version(conn)
    applied: list[int] = []
    for version, path in _migration_files():
        if version <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        with conn:  # transaction
            conn.executescript(sql)
            # executescript() commits any open txn first, so set version after.
            conn.execute(f"PRAGMA user_version = {version};")
        applied.append(version)
    return applied
