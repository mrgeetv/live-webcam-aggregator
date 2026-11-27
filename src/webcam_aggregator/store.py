"""The SQLite store: what each source learned, kept across a restart.

Everything in it can be learned again, so losing it costs a cold start, never
correctness. This module owns every connection, pragma and transaction; nothing else
imports sqlite3.

One write connection sits behind a lock held for the whole transaction, so writes
from different threads never interleave. Each read opens its own short-lived
query-only connection, which under WAL never waits on a writer. No transaction may
span a network fetch: fetch first, then write. Playback never reads the store.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable

log = logging.getLogger("webcam-aggregator.store")

_DB_NAME = "webcam-aggregator.db"
# Hard-coded, with no env var: where the data lives on the host is the bind mount's
# business (compose's DATA_DIR), not the app's.
DB_PATH = Path("/data") / _DB_NAME

# Bounds the -wal file left behind after a checkpoint: a few times the largest
# transaction, which is one source's whole kept set.
_JOURNAL_SIZE_LIMIT = 32 * 1024 * 1024
_WRITE_BUSY_MS = 5000
_READ_BUSY_MS = 1000

# The schema, one numbered step per version: step i takes `user_version` i to i + 1.
# Append only. A released step is never edited, because a self-hoster's DB may sit at
# any earlier version and is upgraded by running every step after it.
MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        # One row per source: its last finished refresh's raw outcome and the
        # empty-guard's state. No foreign key from kept_cam: rows for a source the
        # code no longer has stay on disk untouched.
        """CREATE TABLE source (
            name TEXT PRIMARY KEY,
            last_refresh_at INTEGER NOT NULL,
            last_refresh_seconds REAL NOT NULL,
            discovered INTEGER NOT NULL,
            raw_kept INTEGER NOT NULL,
            crashed INTEGER NOT NULL,
            status TEXT NOT NULL,
            guard_last_count INTEGER,
            guard_shrink_streak INTEGER NOT NULL
        ) STRICT""",
        # Each source's kept set, replaced whole after each refresh. `pos`
        # keeps the set's order, which the merge's ties break on.
        """CREATE TABLE kept_cam (
            source TEXT NOT NULL,
            pos INTEGER NOT NULL,
            title TEXT NOT NULL,
            angle_key TEXT,
            category TEXT,
            source_page_url TEXT NOT NULL,
            target_url TEXT NOT NULL,
            predisc_key TEXT,
            name_rank INTEGER,
            PRIMARY KEY (source, pos)
        ) STRICT""",
    ),
)


class Tx:
    """The statements one write transaction may run."""

    _conn: sqlite3.Connection

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Wrap the write connection for the length of one transaction."""
        self._conn = conn

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        """Run one statement."""
        self._conn.execute(sql, params)

    def executemany(self, sql: str, rows: Iterable[tuple[Any, ...]]) -> None:
        """Run one statement per row."""
        self._conn.executemany(sql, rows)


def _pragmas(conn: sqlite3.Connection, busy_ms: int) -> None:
    conn.execute(f"PRAGMA busy_timeout = {busy_ms}")
    conn.execute("PRAGMA foreign_keys = ON")


class Store:
    """An open store: the migrated DB file and its write connection."""

    _path: Path
    _conn: sqlite3.Connection
    _lock: threading.Lock
    _tmp: tempfile.TemporaryDirectory[str] | None

    def __init__(
        self,
        path: Path,
        *,
        tmp: tempfile.TemporaryDirectory[str] | None = None,
    ) -> None:
        """Open the DB at `path`, creating and migrating it as needed.

        Raises sqlite3.Error or OSError if it can't be opened or migrated. `tmp` is the
        throwaway directory of a storeless store, removed on close.
        """
        self._path = path
        self._tmp = tmp
        self._lock = threading.Lock()
        # autocommit=True, never False: False makes BEGIN IMMEDIATE impossible,
        # silently no-ops foreign_keys=ON and pins read snapshots open.
        self._conn = sqlite3.connect(path, autocommit=True, check_same_thread=False)
        try:
            _pragmas(self._conn, _WRITE_BUSY_MS)
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._conn.execute(f"PRAGMA journal_size_limit = {_JOURNAL_SIZE_LIMIT}")
            self._migrate()
        except BaseException:
            self._conn.close()
            raise

    def _migrate(self) -> None:
        with self._transaction() as tx:
            (version,) = self._conn.execute("PRAGMA user_version").fetchone()
            if version > len(MIGRATIONS):
                msg = f"schema version {version} is newer than this release's"
                raise sqlite3.DatabaseError(msg)
            for step in MIGRATIONS[version:]:
                for stmt in step:
                    tx.execute(stmt)
            # user_version is part of the DB header, so it commits or rolls back
            # with the steps: a failed upgrade leaves the old version whole.
            tx.execute(f"PRAGMA user_version = {len(MIGRATIONS)}")

    @contextlib.contextmanager
    def _transaction(self) -> Generator[Tx]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield Tx(self._conn)
                # Inside the try: a COMMIT that fails can leave the transaction
                # open, and every later BEGIN would then fail.
                self._conn.execute("COMMIT")
            except BaseException:
                # Some errors (a full disk) already rolled SQLite back.
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def write(self, label: str, fn: Callable[[Tx], None]) -> bool:
        """Run `fn` in one transaction; return whether it committed.

        A failure rolls the whole transaction back and logs a WARNING naming `label`;
        the caller carries on with what it holds in memory.
        """
        try:
            with self._transaction() as tx:
                fn(tx)
        except sqlite3.Error as exc:
            log.warning(
                "store: %s not saved, carrying on in memory — %s: %s",
                label,
                type(exc).__name__,
                exc,
            )
            return False
        return True

    def read(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        """Return the rows of one query, or none (with a WARNING) if it fails."""
        try:
            # mode=rw: a read must never create an empty DB where the file was.
            with contextlib.closing(
                sqlite3.connect(f"file:{self._path}?mode=rw", uri=True, autocommit=True)
            ) as conn:
                _pragmas(conn, _READ_BUSY_MS)
                conn.execute("PRAGMA query_only = ON")
                return conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            log.warning("store: read failed — %s: %s", type(exc).__name__, exc)
            return []

    def close(self) -> None:
        """Close the write connection; a storeless store also deletes its file."""
        self._conn.close()
        if self._tmp is not None:
            self._tmp.cleanup()


def open_storeless() -> Store:
    """Open a throwaway store in a temp directory, discarded on close or exit.

    A file, not :memory:, so the same code, connections and pragmas run either way:
    an in-memory DB can't be shared across connections under WAL.
    """
    tmp = tempfile.TemporaryDirectory(prefix="webcam-aggregator-")
    return Store(Path(tmp.name) / _DB_NAME, tmp=tmp)


def open_store(path: Path) -> Store:
    """Open the store at `path`, or a storeless one if it can't be used."""
    try:
        s = Store(path)
    except (sqlite3.Error, OSError) as exc:
        log.warning(
            "store: can't use %s (%s: %s); running storeless, so nothing learned "
            "survives a restart",
            path,
            type(exc).__name__,
            exc,
        )
        return open_storeless()
    if not os.path.ismount(path.parent):
        log.warning(
            "store: %s is not a mounted directory, so the store lives in the container "
            "layer: it survives a restart but not a recreate (see the README's "
            "Persistent data)",
            path.parent,
        )
    return s
