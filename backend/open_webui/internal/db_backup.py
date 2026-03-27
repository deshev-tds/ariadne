from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


log = logging.getLogger(__name__)


def _resolve_local_sqlite_database(
    database_url: str,
) -> tuple[str, Path] | None:
    prefixes = (
        ("sqlite+sqlcipher:///", "sqlite+sqlcipher"),
        ("sqlite:///", "sqlite"),
    )

    for prefix, engine in prefixes:
        if database_url.startswith(prefix):
            raw_path = database_url.replace(prefix, "", 1).split("?", 1)[0]
            return engine, Path(raw_path).expanduser().resolve()

    return None


def _escape_sqlcipher_literal(value: str) -> str:
    return value.replace("'", "''")


def _copy_sqlite_sidecar_files(source_path: Path, backup_path: Path) -> list[str]:
    copied_files = [backup_path.name]

    shutil.copy2(source_path, backup_path)

    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{source_path}{suffix}")
        if sidecar.exists():
            shutil.copy2(sidecar, Path(f"{backup_path}{suffix}"))
            copied_files.append(f"{backup_path.name}{suffix}")

    return copied_files


def _backup_plain_sqlite(source_path: Path, backup_path: Path) -> str:
    source_conn = sqlite3.connect(str(source_path))
    try:
        source_conn.execute("PRAGMA busy_timeout = 5000")

        backup_conn = sqlite3.connect(str(backup_path))
        try:
            source_conn.backup(backup_conn)
        finally:
            backup_conn.close()
    finally:
        source_conn.close()

    return "sqlite-backup-api"


def _backup_sqlcipher(
    source_path: Path, backup_path: Path, database_password: Optional[str]
) -> str:
    if not database_password:
        raise ValueError(
            "DATABASE_PASSWORD is required for SQLCipher pre-migration backups"
        )

    try:
        import sqlcipher3
    except ModuleNotFoundError:
        _copy_sqlite_sidecar_files(source_path, backup_path)
        return "file-copy-fallback"

    escaped_password = _escape_sqlcipher_literal(database_password)
    source_conn = sqlcipher3.connect(str(source_path), check_same_thread=False)
    try:
        source_conn.execute(f"PRAGMA key = '{escaped_password}'")
        source_conn.execute("PRAGMA busy_timeout = 5000")

        backup_conn = sqlcipher3.connect(str(backup_path), check_same_thread=False)
        try:
            backup_conn.execute(f"PRAGMA key = '{escaped_password}'")
            source_conn.backup(backup_conn)
        finally:
            backup_conn.close()
    finally:
        source_conn.close()

    return "sqlcipher-backup-api"


def create_pre_migration_backup(
    database_url: str,
    backup_root: Path | str,
    database_password: Optional[str] = None,
) -> Path | None:
    resolved = _resolve_local_sqlite_database(database_url)
    if resolved is None:
        log.info(
            "Skipping automatic pre-migration backup: only local sqlite databases are backed up automatically"
        )
        return None

    engine, source_path = resolved
    if not source_path.exists():
        log.info(
            "Skipping automatic pre-migration backup: source database does not exist at %s",
            source_path,
        )
        return None

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = Path(backup_root).expanduser().resolve() / "backups" / "migrations"
    backup_dir.mkdir(parents=True, exist_ok=True)

    backup_path = backup_dir / f"{source_path.stem}-pre-migration-{timestamp}{source_path.suffix}"

    if engine == "sqlite":
        method = _backup_plain_sqlite(source_path, backup_path)
    else:
        method = _backup_sqlcipher(source_path, backup_path, database_password)

    manifest_path = backup_path.with_suffix(f"{backup_path.suffix}.json")
    manifest_path.write_text(
        json.dumps(
            {
                "created_at": timestamp,
                "database_engine": engine,
                "source_path": str(source_path),
                "backup_path": str(backup_path),
                "backup_method": method,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    log.info("Created pre-migration database backup at %s", backup_path)
    return backup_path
