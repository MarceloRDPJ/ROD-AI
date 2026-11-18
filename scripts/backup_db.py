#!/usr/bin/env python3
"""Cria e verifica um backup consistente do SQLite em outro dispositivo."""

import argparse
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DB = PROJECT_DIR / "src" / "jarvis" / "database" / "homebot.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(os.environ["ROD_BACKUP_DIR"]) if os.getenv("ROD_BACKUP_DIR") else None,
        help="diretório em um dispositivo montado separadamente",
    )
    parser.add_argument("--keep", type=int, default=7, help="quantidade de backups a manter")
    parser.add_argument(
        "--allow-same-device",
        action="store_true",
        help="permite cópia no mesmo dispositivo (não protege contra falha do disco)",
    )
    return parser.parse_args()


def backup_database(database: Path, destination: Path, keep: int, allow_same_device: bool = False) -> Path:
    database = database.resolve(strict=True)
    destination.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve(strict=True)

    if keep < 1:
        raise ValueError("--keep deve ser maior que zero")
    if not allow_same_device and database.stat().st_dev == destination.stat().st_dev:
        raise RuntimeError(
            "origem e destino estão no mesmo dispositivo; monte o SD/USB e use --destination, "
            "ou assuma o risco com --allow-same-device"
        )

    timestamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    final_path = destination / f"homebot-{timestamp}.db"
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(prefix=".homebot-", suffix=".db", dir=destination, delete=False) as handle:
            temporary_path = Path(handle.name)
        with closing(sqlite3.connect(database)) as source, closing(
            sqlite3.connect(temporary_path)
        ) as target:
            source.backup(target)
        with closing(sqlite3.connect(temporary_path)) as check:
            result = check.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"integrity_check falhou: {result}")
        temporary_path.replace(final_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

    backups = sorted(destination.glob("homebot-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    for old_backup in backups[keep:]:
        old_backup.unlink()
    return final_path


def main() -> int:
    args = parse_args()
    if args.destination is None:
        raise SystemExit("informe --destination ou ROD_BACKUP_DIR")
    path = backup_database(args.database, args.destination, args.keep, args.allow_same_device)
    print(f"Backup verificado: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
