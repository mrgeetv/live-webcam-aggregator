from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

import pytest

from webcam_aggregator import store
from webcam_aggregator.store_migrations import MIGRATIONS

if TYPE_CHECKING:
    from collections.abc import Callable


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


@pytest.mark.parametrize("version", range(len(MIGRATIONS) + 1))
def test_every_schema_version_upgrades_to_head(tmp_path: Path, version: int) -> None:
    """A DB left at any earlier version upgrades in place and keeps its rows."""
    fresh = tmp_path / "fresh.db"
    store.Store(fresh).close()

    old = tmp_path / "old.db"
    with contextlib.closing(sqlite3.connect(old, autocommit=True)) as conn:
        for step in MIGRATIONS[:version]:
            for stmt in step:
                conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {version}")
        seeded = _seed_every_table(conn)

    store.Store(old).close()

    assert _schema(old) == _schema(fresh)
    with contextlib.closing(sqlite3.connect(old)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone() == (len(MIGRATIONS),)
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


def test_a_db_from_a_newer_release_is_left_untouched(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A rolled-back image runs storeless and leaves the newer DB for re-upgrading."""
    path = tmp_path / "webcam-aggregator.db"
    store.Store(path).close()
    with contextlib.closing(sqlite3.connect(path, autocommit=True)) as conn:
        conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    before = path.read_bytes()

    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.store"):
        s = store.open_store(path)
    s.close()

    assert s.status == "storeless"
    assert "newer release" in caplog.text
    assert path.read_bytes() == before


def _null_in_a_not_null_column(path: Path) -> None:
    """Make a DB that opens fine but whose quick_check reports a row, not `ok`."""
    with contextlib.closing(sqlite3.connect(path, autocommit=True)) as conn:
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (NULL)")
        conn.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, False)  # noqa: FBT003
        conn.execute("PRAGMA writable_schema = ON")
        conn.execute(
            "UPDATE sqlite_master SET sql = 'CREATE TABLE t (x NOT NULL)' "
            "WHERE name = 't'"
        )


def _not_a_database(path: Path) -> None:
    path.write_bytes(b"not sqlite" * 500)
    for suffix in ("-wal", "-shm"):
        Path(f"{path}{suffix}").write_bytes(b"junk" * 100)


@pytest.mark.parametrize("corrupt", [_not_a_database, _null_in_a_not_null_column])
def test_a_corrupt_db_is_moved_aside_for_a_fresh_one(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    corrupt: Callable[[Path], None],
) -> None:
    path = tmp_path / "webcam-aggregator.db"
    corrupt(path)
    aside = {p.name: p.read_bytes() for p in tmp_path.iterdir()}

    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.store"):
        s = store.open_store(path)
    try:
        assert s.status == "ok"
        assert "corrupt" in caplog.text
        assert s.write("probe", lambda tx: tx.execute("DELETE FROM kept_cam"))
    finally:
        s.close()
    moved = {
        p.name.partition(".corrupt-")[0]: p.read_bytes()
        for p in tmp_path.iterdir()
        if ".corrupt-" in p.name
    }
    assert moved.keys() == aside.keys()
    # SQLite rewrites -shm, an index; the DB and its -wal must be as they were.
    for name in (path.name, f"{path.name}-wal"):
        assert moved.get(name) == aside.get(name)
    with contextlib.closing(sqlite3.connect(path)) as conn:
        assert conn.execute("PRAGMA quick_check").fetchall() == [("ok",)]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_an_unwritable_directory_says_how_to_fix_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    mode = data.stat().st_mode
    data.chmod(0o555)
    try:
        with caplog.at_level(logging.WARNING, logger="webcam-aggregator.store"):
            s = store.open_store(data / "webcam-aggregator.db")
    finally:
        data.chmod(mode)
    s.close()
    assert s.status == "storeless"
    assert f"chown -R {os.getuid()}:{os.getgid()}" in caplog.text


def test_a_sqlite_with_the_wal_reset_bug_runs_storeless(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sqlite3, "sqlite_version", "3.51.2")
    monkeypatch.setattr(sqlite3, "sqlite_version_info", (3, 51, 2))
    path = tmp_path / "webcam-aggregator.db"
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.store"):
        s = store.open_store(path)
    s.close()
    assert s.status == "storeless"
    assert "3.51.2" in caplog.text
    assert not path.exists()


class _NoWal(sqlite3.Connection):
    """A connection whose filesystem can't do WAL: SQLite then keeps the old mode."""

    @override
    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        if sql.startswith("PRAGMA journal_mode"):
            return super().execute("SELECT 'delete'")
        return super().execute(sql, parameters)


def test_a_db_that_cannot_use_wal_runs_storeless(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "webcam-aggregator.db"
    connect = sqlite3.connect

    def on_a_no_wal_filesystem(
        db: Path | str,
        *,
        autocommit: bool,
        check_same_thread: bool = True,
        uri: bool = False,
    ) -> sqlite3.Connection:
        return connect(
            db,
            autocommit=autocommit,
            check_same_thread=check_same_thread,
            uri=uri,
            factory=_NoWal if db == path else sqlite3.Connection,
        )

    monkeypatch.setattr(sqlite3, "connect", on_a_no_wal_filesystem)
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.store"):
        s = store.open_store(path)
    s.close()
    assert s.status == "storeless"
    assert "WAL" in caplog.text


def test_a_failing_store_heals_on_the_next_good_write(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    s = store.Store(tmp_path / "w.db")

    def big(tx: store.Tx) -> None:
        tx.execute(
            "INSERT INTO kept_cam (source, pos, title, source_page_url, "
            "target_url) VALUES ('a', 0, ?, 'p', 'u')",
            ("x" * 1_000_000,),
        )

    try:
        # A full disk: the file can't grow.
        assert s.write("cap", lambda tx: tx.execute("PRAGMA max_page_count = 1"))
        assert s.status == "ok"
        with caplog.at_level(logging.INFO, logger="webcam-aggregator.store"):
            assert not s.write("fill", big)
            assert s.status == "failing"
            assert "SQLITE_FULL" in caplog.text
            assert s.read("SELECT count(*) FROM kept_cam") == [(0,)]

            uncap = "PRAGMA max_page_count = 1073741823"
            assert s.write("uncap", lambda tx: tx.execute(uncap))
            assert s.status == "ok"
            assert "writing again" in caplog.text
    finally:
        s.close()


def test_a_path_with_uri_characters_reads_its_own_db(tmp_path: Path) -> None:
    path = tmp_path / "we#ird?%20" / "webcam-aggregator.db"
    path.parent.mkdir()
    store.Store(path).close()
    s = store.open_store(path)
    try:
        assert s.status == "ok"
        assert s.read("SELECT count(*) FROM kept_cam") == [(0,)]
    finally:
        s.close()


def test_a_read_only_mount_says_so_not_chown(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    statvfs = os.statvfs

    def read_only(p: str | Path) -> os.statvfs_result:
        st = statvfs(p)
        fields = list(st)
        fields[8] |= os.ST_RDONLY  # f_flag
        return os.statvfs_result(fields)

    monkeypatch.setattr(os, "statvfs", read_only)
    monkeypatch.setattr(os, "access", lambda *_a, **_k: False)
    with caplog.at_level(logging.WARNING, logger="webcam-aggregator.store"):
        s = store.open_store(tmp_path / "webcam-aggregator.db")
    s.close()
    assert s.status == "storeless"
    assert "read-only" in caplog.text
    assert "chown" not in caplog.text
