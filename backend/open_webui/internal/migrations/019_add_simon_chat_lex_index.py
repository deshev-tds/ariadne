"""Peewee migrations -- 018_add_function_is_global.py."""

from contextlib import suppress

import peewee as pw
from peewee_migrate import Migrator

with suppress(ImportError):
    import playhouse.postgres_ext as pw_pext


def _is_sqlite(database: pw.Database) -> bool:
    try:
        return "sqlite" in database.__class__.__name__.lower()
    except Exception:
        return False


def migrate(migrator: Migrator, database: pw.Database, *, fake=False):
    """Create Simon lexical index tables and enqueue backfill jobs (SQLite only)."""
    if not _is_sqlite(database):
        return

    migrator.sql(
        """
        CREATE TABLE IF NOT EXISTS simon_chat_lex (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            parent_id TEXT,
            role TEXT,
            content_text TEXT,
            content_hash TEXT,
            extractor_version INTEGER NOT NULL DEFAULT 1,
            created_at BIGINT,
            updated_at BIGINT,
            UNIQUE(chat_id, message_id)
        )
        """
    )

    migrator.sql(
        """
        CREATE TABLE IF NOT EXISTS simon_chat_lex_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            available_at BIGINT NOT NULL,
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL,
            UNIQUE(chat_id, message_id)
        )
        """
    )

    migrator.sql(
        """
        CREATE INDEX IF NOT EXISTS idx_simon_chat_lex_chat_updated
        ON simon_chat_lex(chat_id, updated_at DESC)
        """
    )

    migrator.sql(
        """
        CREATE INDEX IF NOT EXISTS idx_simon_chat_lex_queue_available
        ON simon_chat_lex_queue(available_at, priority, updated_at)
        """
    )

    migrator.sql(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS simon_chat_lex_fts USING fts5(
            content_text,
            message_id UNINDEXED,
            chat_id UNINDEXED,
            parent_id UNINDEXED,
            role UNINDEXED
        )
        """
    )

    table_exists = database.execute_sql(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = 'chat_message'
        LIMIT 1
        """
    ).fetchone()

    if table_exists:
        migrator.sql(
            """
            INSERT INTO simon_chat_lex_queue(
                chat_id,
                message_id,
                priority,
                attempts,
                last_error,
                available_at,
                created_at,
                updated_at
            )
            SELECT
                chat_id,
                substr(id, length(chat_id) + 2) AS message_id,
                CASE
                    WHEN role = 'assistant' THEN 5
                    WHEN role = 'user' THEN 3
                    ELSE 1
                END AS priority,
                0,
                NULL,
                CAST(strftime('%s', 'now') AS INTEGER),
                CAST(strftime('%s', 'now') AS INTEGER),
                CAST(strftime('%s', 'now') AS INTEGER)
            FROM chat_message
            WHERE role = 'user'
               OR (role = 'assistant' AND COALESCE(done, 1) = 1)
            ON CONFLICT(chat_id, message_id) DO NOTHING
            """
        )


def rollback(migrator: Migrator, database: pw.Database, *, fake=False):
    """Drop Simon lexical index tables (SQLite only)."""
    if not _is_sqlite(database):
        return

    migrator.sql("DROP TABLE IF EXISTS simon_chat_lex_fts")
    migrator.sql("DROP TABLE IF EXISTS simon_chat_lex_queue")
    migrator.sql("DROP TABLE IF EXISTS simon_chat_lex")
