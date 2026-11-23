import sqlite3

import pytest

from jarvis.database.persistence import Persistence


class DuplicateCursor:
    def execute(self, sql):
        raise sqlite3.OperationalError("duplicate column name: action")


class BrokenCursor:
    def execute(self, sql):
        raise sqlite3.OperationalError("database is locked")


def test_add_column_ignores_duplicate_column_only():
    Persistence._add_column_if_missing(DuplicateCursor(), "tasks", "action TEXT")


def test_add_column_raises_real_operational_errors():
    with pytest.raises(sqlite3.OperationalError):
        Persistence._add_column_if_missing(BrokenCursor(), "tasks", "action TEXT")


def test_existing_devices_table_is_migrated_without_losing_names(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE devices (mac TEXT PRIMARY KEY, name TEXT, updated_at TEXT)")
        conn.execute(
            "INSERT INTO devices VALUES (?, ?, ?)",
            ("00:11:22:33:44:55", "Geladeira", "2026-01-01T10:00:00+00:00"),
        )
    Persistence.set_db_path(str(db_path))

    Persistence.init_db()
    profile = Persistence.get_device_profile("00:11:22:33:44:55")

    assert profile["name"] == "Geladeira"
    assert profile["first_seen"] == "2026-01-01T10:00:00+00:00"
    assert profile["seen_count"] == 1
