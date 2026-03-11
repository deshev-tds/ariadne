from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from open_webui.env import FORK_MEMORY_DATABASE_URL

log = logging.getLogger(__name__)

ForkMemoryBase = declarative_base()

_DATABASE_URL = FORK_MEMORY_DATABASE_URL
_engine = None
_SessionLocal = None
_AVAILABLE = False


def _create_fork_memory_engine():
    if not _DATABASE_URL:
        return None

    if _DATABASE_URL.startswith("sqlite"):
        engine = create_engine(
            _DATABASE_URL,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )

        def _on_connect(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        event.listen(engine, "connect", _on_connect)
        return engine

    return create_engine(_DATABASE_URL, pool_pre_ping=True)


try:
    _engine = _create_fork_memory_engine()
    if _engine is not None:
        _SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=_engine,
            expire_on_commit=False,
        )
except Exception as exc:
    log.warning("Failed to initialize fork memory DB engine: %s", exc)
    _engine = None
    _SessionLocal = None


def initialize_fork_memory_db() -> bool:
    global _AVAILABLE, _engine, _SessionLocal

    if _engine is None:
        try:
            _engine = _create_fork_memory_engine()
            if _engine is not None:
                _SessionLocal = sessionmaker(
                    autocommit=False,
                    autoflush=False,
                    bind=_engine,
                    expire_on_commit=False,
                )
        except Exception as exc:
            log.warning("Failed to initialize fork memory DB engine: %s", exc)
            _engine = None
            _SessionLocal = None
            _AVAILABLE = False
            return False

    if _engine is None:
        _AVAILABLE = False
        return False

    try:
        # Import models lazily to avoid circular imports during startup.
        import open_webui.models.ledger  # noqa: F401

        ForkMemoryBase.metadata.create_all(bind=_engine)
        _AVAILABLE = True
        return True
    except Exception as exc:
        log.warning("Failed to initialize fork memory tables: %s", exc)
        _AVAILABLE = False
        return False


def is_fork_memory_available() -> bool:
    return bool(_AVAILABLE and _engine is not None and _SessionLocal is not None)


@contextmanager
def get_fork_db_context(db: Optional[Session] = None):
    if isinstance(db, Session):
        yield db
        return

    if _SessionLocal is None:
        raise RuntimeError("Fork memory DB is not initialized")

    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()
