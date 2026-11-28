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
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from webcam_aggregator.store_migrations import MIGRATIONS

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
# Older SQLite can corrupt a WAL database (the WAL-reset bug).
_MIN_SQLITE = (3, 51, 3)


class _UnusableError(Exception):
    """The DB at a path is one this release must not, or cannot, use."""


class _TooNewError(_UnusableError):
    """The DB's schema is newer than this release's: a rolled-back image."""


class _CorruptError(_UnusableError):
    """quick_check found damage."""


class _NotWalError(_UnusableError):
    """The filesystem won't do WAL."""


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


def _check(path: Path) -> None:
    """Refuse a newer or damaged DB, read-only: a writer's close deletes the -wal."""
    with contextlib.closing(
        sqlite3.connect(
            f"{path.absolute().as_uri()}?mode=ro", uri=True, autocommit=True
        )
    ) as conn:
        _pragmas(conn, _READ_BUSY_MS)
        (version,) = conn.execute("PRAGMA user_version").fetchone()
        if version > len(MIGRATIONS):
            msg = "it was written by a newer release; left untouched"
            raise _TooNewError(msg)
        if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            msg = "quick_check found damage"
            raise _CorruptError(msg)


def _use_wal(conn: sqlite3.Connection) -> None:
    (mode,) = conn.execute("PRAGMA journal_mode = WAL").fetchone()
    if mode != "wal":  # SQLite keeps the old mode rather than raising
        msg = f"its filesystem can't use WAL (journal_mode stayed {mode})"
        raise _NotWalError(msg)


class Store:
    """An open store: the migrated DB file and its write connection."""

    _path: Path
    _conn: sqlite3.Connection
    _lock: threading.Lock
    _tmp: tempfile.TemporaryDirectory[str] | None
    _failing: bool

    def __init__(
        self,
        path: Path,
        *,
        tmp: tempfile.TemporaryDirectory[str] | None = None,
    ) -> None:
        """Open the DB at `path`, creating and migrating it as needed.

        Raises _UnusableError, sqlite3.Error or OSError if it can't be used. `tmp` is
        the throwaway directory of a storeless store, removed on close.
        """
        self._path = path
        self._tmp = tmp
        self._lock = threading.Lock()
        self._failing = False
        if path.exists():
            _check(path)
        # autocommit=True, never False: False makes BEGIN IMMEDIATE impossible,
        # silently no-ops foreign_keys=ON and pins read snapshots open.
        self._conn = sqlite3.connect(path, autocommit=True, check_same_thread=False)
        try:
            _pragmas(self._conn, _WRITE_BUSY_MS)
            _use_wal(self._conn)
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._conn.execute(f"PRAGMA journal_size_limit = {_JOURNAL_SIZE_LIMIT}")
            self._migrate()
        except BaseException:
            self._conn.close()
            raise

    def _migrate(self) -> None:
        with self._transaction() as tx:
            (version,) = self._conn.execute("PRAGMA user_version").fetchone()
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

    @property
    def status(self) -> str:
        """`storeless`, `failing` while writes fail, else `ok`: /health's `store`."""
        if self._tmp is not None:
            return "storeless"
        return "failing" if self._failing else "ok"

    def write(self, label: str, fn: Callable[[Tx], None]) -> bool:
        """Run `fn` in one transaction; return whether it committed.

        A failure rolls back, logs a WARNING naming `label` and marks the store
        `failing` until a write commits again.
        """
        try:
            with self._transaction() as tx:
                fn(tx)
        except sqlite3.Error as exc:
            self._failing = True
            log.warning(
                "store: %s not saved, carrying on in memory — %s (%s): %s",
                label,
                type(exc).__name__,
                getattr(exc, "sqlite_errorname", "no SQLite code"),
                exc,
            )
            return False
        if self._failing:
            self._failing = False
            log.info("store: writing again")
        return True

    def read(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        """Return the rows of one query, or none (with a WARNING) if it fails."""
        try:
            # mode=rw: a read must never create an empty DB where the file was.
            with contextlib.closing(
                sqlite3.connect(
                    f"{self._path.absolute().as_uri()}?mode=rw",
                    uri=True,
                    autocommit=True,
                )
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
        self.discard()

    def discard(self) -> None:
        """Delete a storeless store's file, open or not; a real store is kept."""
        if self._tmp is not None:
            self._tmp.cleanup()


def open_storeless() -> Store:
    """Open a throwaway store in a temp directory, discarded on close or exit.

    A file, not :memory:, so the same code, connections and pragmas run either way:
    an in-memory DB can't be shared across connections under WAL.
    """
    tmp = tempfile.TemporaryDirectory(prefix="webcam-aggregator-")
    return Store(Path(tmp.name) / _DB_NAME, tmp=tmp)


_OPEN_ERRORS = (sqlite3.Error, OSError, _UnusableError)


def _is_corrupt(exc: Exception) -> bool:
    code = getattr(exc, "sqlite_errorcode", 0) & 0xFF  # the primary code
    return isinstance(exc, _CorruptError) or code in {
        sqlite3.SQLITE_CORRUPT,
        sqlite3.SQLITE_NOTADB,
    }


def _why(exc: Exception) -> str:
    if isinstance(exc, _UnusableError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def _move_aside(path: Path) -> str:
    """Add a `.corrupt-<time>` suffix to the DB, its -wal and -shm; return it."""
    suffix = time.strftime(".corrupt-%Y%m%dT%H%M%SZ", time.gmtime())
    for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        with contextlib.suppress(FileNotFoundError):
            p.rename(f"{p}{suffix}")
    return suffix


def _unusable(path: Path) -> str | None:
    """Say why the store at `path` can't be used, before opening it, if it can't."""
    if sqlite3.sqlite_version_info < _MIN_SQLITE:
        least = ".".join(map(str, _MIN_SQLITE))
        return (
            f"SQLite {sqlite3.sqlite_version} is older than {least}, which can "
            "corrupt a WAL database"
        )
    files = (path.parent, path, Path(f"{path}-wal"), Path(f"{path}-shm"))
    if path.parent.is_dir() and not all(
        os.access(f, os.W_OK) for f in files if f.exists()
    ):
        if os.statvfs(path.parent).f_flag & os.ST_RDONLY:
            return f"{path.parent} is on a read-only mount"
        uid, gid = os.getuid(), os.getgid()
        return (
            f"it isn't writable by this process (uid {uid}, gid {gid}: on the host, "
            f"run `chown -R {uid}:{gid}` on the directory mounted at {path.parent})"
        )
    return None


def _open(path: Path) -> Store | str:
    """Open the store at `path`, replacing a corrupt DB; else say why it can't."""
    why = _unusable(path)
    if why is not None:
        return why
    try:
        return Store(path)
    except _OPEN_ERRORS as exc:
        if not _is_corrupt(exc):
            return _why(exc)
        damage = _why(exc)
    try:
        suffix = _move_aside(path)
        s = Store(path)
    except _OPEN_ERRORS as exc:
        return _why(exc)
    log.warning(
        "store: %s is corrupt (%s), so it and its -wal and -shm were renamed with a "
        "%s suffix and a fresh store started; delete them once they are of no "
        "interest",
        path,
        damage,
        suffix,
    )
    return s


def open_store(path: Path) -> Store:
    """Open the store at `path`, or a storeless one if it can't be used.

    A corrupt DB is moved aside and a fresh one started in its place.
    """
    s = _open(path)
    if isinstance(s, str):
        log.warning(
            "store: can't use %s: %s; running storeless, so nothing learned "
            "survives a restart",
            path,
            s,
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
