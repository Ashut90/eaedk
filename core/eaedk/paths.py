"""Filesystem locations for data (templates / seed) and the local database.

Data files live in ``packages/`` at the repo root. We locate the repo root by walking
up from this file. ``EAEDK_DATA_DIR`` overrides the data root; ``EAEDK_DB`` / ``--db``
override the database path.
"""
from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    # core/eaedk/paths.py -> repo root is two parents up from the package dir.
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    override = os.environ.get("EAEDK_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return repo_root() / "packages"


def templates_dir() -> Path:
    return data_dir() / "templates"


def seed_dir() -> Path:
    return data_dir() / "knowledge-seed"


def default_db_path() -> Path:
    override = os.environ.get("EAEDK_DB")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".eaedk" / "eaedk.db"
