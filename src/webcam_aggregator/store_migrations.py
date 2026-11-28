"""The store's schema, as the steps that build it.

Step i, the tuple's i-th entry, takes `PRAGMA user_version` i to i + 1; `store` runs
every step after a DB's version in one transaction. Append only: a released step is
never edited, because a self-hoster's DB may sit at any earlier version.
"""

MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (  # 0 -> 1
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
