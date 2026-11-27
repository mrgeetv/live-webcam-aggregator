from __future__ import annotations

import contextlib
import logging
import sqlite3
from typing import TYPE_CHECKING

import pytest

from webcam_aggregator import store

if TYPE_CHECKING:
    from pathlib import Path


def _schema(path: Path) -> list[tuple[str, str, str]]:
    with contextlib.closing(sqlite3.connect(path)) as conn:
        return sorted(conn.execute("SELECT type, name, sql FROM sqlite_master"))


# One value per STRICT column type, so a seed row fits whatever a step declares.
_SEED = {"TEXT": "x", "INTEGER": 7, "REAL": 1.5, "BLOB": b"x", "ANY": "x"}


def _seed_every_table(conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
    seeded: dict[str, dict[str, object]] = {}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        cols: dict[str, object] = {
            name: _SEED[decl.upper()]
            for _cid, name, decl, *_ in conn.execute(f"PRAGMA table_info({table})")
        }
        marks = ", ".join("?" * len(cols))
        # Table and column names are read back from sqlite_master, not input.
        insert = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks})"  # noqa: S608
        conn.execute(insert, tuple(cols.values()))
        seeded[table] = cols
    return seeded


@pytest.mark.parametrize("version", range(len(store.MIGRATIONS) + 1))
def test_every_schema_version_upgrades_to_head(tmp_path: Path, version: int) -> None:
    """A DB left at any earlier version upgrades in place and keeps its rows."""
    fresh = tmp_path / "fresh.db"
    store.Store(fresh).close()

    old = tmp_path / "old.db"
    with contextlib.closing(sqlite3.connect(old, autocommit=True)) as conn:
        for step in store.MIGRATIONS[:version]:
            for stmt in step:
                conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {version}")
        seeded = _seed_every_table(conn)

    store.Store(old).close()

    assert _schema(old) == _schema(fresh)
    with contextlib.closing(sqlite3.connect(old)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone() == (
            len(store.MIGRATIONS),
        )
        for table, cols in seeded.items():
            where = " AND ".join(f"{c} = ?" for c in cols)
            count = f"SELECT count(*) FROM {table} WHERE {where}"  # noqa: S608
            n = conn.execute(count, tuple(cols.values())).fetchone()
            assert n == (1,), f"{table}'s row did not survive the upgrade"


def test_a_store_that_cannot_open_runs_storeless(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    missing = tmp_path / "no-such-dir" / "webcam-aggregator.db"
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.store"):
        s = store.open_store(missing)
    try:
        assert "running storeless" in caplog.text
        assert not missing.parent.exists()
        assert s.write("probe", lambda tx: tx.execute("SELECT 1"))
    finally:
        s.close()


def test_a_store_on_an_unmounted_directory_says_so(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.store"):
        s = store.open_store(tmp_path / "webcam-aggregator.db")
    s.close()
    assert (tmp_path / "webcam-aggregator.db").exists()
    assert "running storeless" not in caplog.text
    assert "container layer" in caplog.text


def test_a_failed_write_rolls_back_whole(tmp_path: Path) -> None:
    s = store.Store(tmp_path / "w.db")

    def half(tx: store.Tx) -> None:
        tx.execute(
            "INSERT INTO kept_cam (source, pos, title, source_page_url, "
            "target_url) VALUES ('a', 0, 't', 'p', 'u')"
        )
        tx.execute("INSERT INTO no_such_table VALUES (1)")

    try:
        assert not s.write("half", half)
        assert s.read("SELECT count(*) FROM kept_cam") == [(0,)]
        assert s.write("next", lambda tx: tx.execute("SELECT 1"))  # lock released
    finally:
        s.close()
