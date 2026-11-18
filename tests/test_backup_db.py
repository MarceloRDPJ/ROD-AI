import sqlite3

import pytest

from scripts.backup_db import backup_database


def make_database(path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('ok')")


def test_backup_is_consistent_and_keeps_data(tmp_path):
    database = tmp_path / "source.db"
    destination = tmp_path / "backups"
    make_database(database)

    backup = backup_database(database, destination, keep=2, allow_same_device=True)

    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT value FROM sample").fetchone() == ("ok",)


def test_backup_rejects_same_device_by_default(tmp_path):
    database = tmp_path / "source.db"
    make_database(database)

    with pytest.raises(RuntimeError, match="mesmo dispositivo"):
        backup_database(database, tmp_path / "backups", keep=2)
