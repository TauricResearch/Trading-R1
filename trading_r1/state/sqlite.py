# Copyright 2026 Tauric Research
# SPDX-License-Identifier: Apache-2.0

"""Opening a SQLite store: the directory, WAL, the schema, owner-only access.

The schema's version is derived from its text and kept in ``PRAGMA user_version``, so changing
a ``CREATE`` statement is enough to move on from the files made from the old one. A store that
names tables in ``keep`` changes layout in place, in one transaction: those tables keep their rows
in the columns the two layouts share, and an opener that comes second waits and finds it done. If
the rows do not fit, nothing changes and ``StoreLayoutError`` says so: the user's rows are never set
aside for a layout that cannot hold them. Any other store of another version, a file that is not a
database and a malformed one are moved aside as ``<name>.old``, replacing any earlier one, and a
fresh file is made. A store that cannot be opened for another reason (locked, an I/O error) is left
where it is and the error is raised.

Versions have no order, so only the process that owns the state directory should open these stores:
two processes on different layouts would change the file back and forth.
"""

from __future__ import annotations

import logging
import sqlite3
import zlib
from pathlib import Path

_LOG = logging.getLogger(__name__)
_BUSY_WAIT_S = 10  # how long a write waits for another writer of the file
_RETIRED = "retired_"  # a table of the old layout, while the layout changes
_SEQUENCE = "sqlite_sequence"  # exists only once a table uses AUTOINCREMENT


class _OtherVersionError(sqlite3.DatabaseError):
    pass


class StoreLayoutError(sqlite3.DatabaseError):
    """The rows of a store do not fit its new layout. The file is left as it was."""


def _unusable(err: sqlite3.DatabaseError) -> bool:
    """Whether the file itself is the problem: another layout, not a database, or malformed."""
    return isinstance(err, _OtherVersionError) or getattr(err, "sqlite_errorcode", None) in (
        sqlite3.SQLITE_NOTADB,
        sqlite3.SQLITE_CORRUPT,
    )


def schema_version(schema: str) -> int:
    """A positive 31-bit number derived from the schema text."""
    return (zlib.crc32(schema.encode()) & 0x7FFFFFFF) or 1


def open_store(
    path: Path,
    schema: str,
    *,
    isolation_level: str | None = "",
    keep: tuple[str, ...] = (),
) -> sqlite3.Connection:
    """A connection to the store at ``path``, created from ``schema`` (``CREATE ... IF NOT
    EXISTS`` statements) when it is new. ``keep`` names the tables that hold what the user would
    lose: their rows survive a change of layout."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    version = schema_version(schema)
    try:
        db = _open(path, schema, version, isolation_level, keep)
    except sqlite3.DatabaseError as err:
        if not _unusable(err):
            raise  # locked, I/O, out of space: the file may be fine, so it stays where it is
        aside = path.with_name(path.name + ".old")
        _LOG.warning(
            "%s is not a store of the current layout (%s); it is kept as %s and a new one is made",
            path.name,
            "another version" if isinstance(err, _OtherVersionError) else type(err).__name__,
            aside.name,
        )
        for suffix in ("-wal", "-shm"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)
        path.replace(aside)
        db = _open(path, schema, version, isolation_level)
    path.chmod(0o600)
    return db


def _open(
    path: Path,
    schema: str,
    version: int,
    isolation_level: str | None,
    keep: tuple[str, ...] = (),
) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=_BUSY_WAIT_S, isolation_level=isolation_level)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        have = db.execute("PRAGMA user_version").fetchone()[0]
        if have != version:
            if have or db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
                if not keep:
                    raise _OtherVersionError("another schema version")
                _change_layout(db, schema, version, keep)
            else:
                db.executescript(schema)
                db.execute(f"PRAGMA user_version = {version}")
    except BaseException:
        db.close()
        raise
    return db


def _statements(script: str) -> list[str]:
    """The statements of ``script``, since ``executescript`` would commit a transaction first."""
    out, part = [], ""
    for piece in script.split(";"):
        if not part and not piece.strip():
            continue
        part += piece + ";"
        if sqlite3.complete_statement(part):
            out.append(part)
            part = ""
    tail = part[:-1]  # the split's ``;`` after the last piece
    return [*out, tail] if tail.strip() else out


def _change_layout(
    db: sqlite3.Connection, schema: str, version: int, keep: tuple[str, ...]
) -> None:
    """Move the file to ``schema``, all or nothing. The kept tables keep their rows and their
    next AUTOINCREMENT id; rows that do not fit raise ``_OtherVersionError``, leaving them as
    they were."""
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("PRAGMA user_version").fetchone()[0] != version:  # else another opener did
            old = "SELECT type, name, sql FROM sqlite_master WHERE substr(name, 1, 7) != 'sqlite_'"
            objects = db.execute(old).fetchall()
            for kind, name, _sql in objects:
                if kind != "table":
                    db.execute(f'DROP {kind} "{name}"')
            tables = [name for kind, name, _sql in objects if kind == "table"]
            sequence = _next_ids(db)
            for table in tables:
                db.execute(f'ALTER TABLE "{table}" RENAME TO "{_RETIRED}{table}"')
            for statement in _statements(schema):
                db.execute(statement)
            for table in keep:
                if table in tables:
                    _copy_rows(db, table)
            for table in tables:
                db.execute(f'DROP TABLE "{_RETIRED}{table}"')
            _restore_next_ids(db, sequence)
            db.execute(f"PRAGMA user_version = {version}")
        db.execute("COMMIT")
    except BaseException as err:
        if db.in_transaction:  # SQLite may have rolled back already
            db.execute("ROLLBACK")
        if isinstance(err, sqlite3.IntegrityError):
            name = db.execute("PRAGMA database_list").fetchone()[2]
            raise StoreLayoutError(f"{name}: its rows do not fit the new layout: {err}") from err
        raise


def _copy_rows(db: sqlite3.Connection, table: str) -> None:
    """The rows of the retired ``table``, in the columns it shares with the new one. A table the
    new layout dropped has nowhere to go, and rows that share no column do not fit."""
    ours = [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]
    if not ours:
        _LOG.warning("the new layout has no table %s: its rows are dropped", table)
        return
    theirs = {row[1] for row in db.execute(f'PRAGMA table_info("{_RETIRED}{table}")')}
    shared = ", ".join(f'"{c}"' for c in ours if c in theirs)
    if shared:
        db.execute(f'INSERT INTO "{table}" ({shared}) SELECT {shared} FROM "{_RETIRED}{table}"')
    elif db.execute(f'SELECT 1 FROM "{_RETIRED}{table}" LIMIT 1').fetchone():
        raise sqlite3.IntegrityError(f"{table} shares no column with its new layout")


def _next_ids(db: sqlite3.Connection) -> list[tuple[str, int]]:
    """The AUTOINCREMENT counters: an id a deleted row used stays used (a delivery is keyed on
    it)."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone():
        return []
    return db.execute(f"SELECT name, seq FROM {_SEQUENCE}").fetchall()


def _restore_next_ids(db: sqlite3.Connection, counters: list[tuple[str, int]]) -> None:
    new = "SELECT name FROM sqlite_master WHERE sql LIKE '%AUTOINCREMENT%'"
    tables = {row[0] for row in db.execute(new)}
    for name, seq in counters:
        if name in tables:
            db.execute(f"DELETE FROM {_SEQUENCE} WHERE name = ?", (name,))
            db.execute(f"INSERT INTO {_SEQUENCE} (name, seq) VALUES (?, ?)", (name, seq))
