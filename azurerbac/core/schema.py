from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from .models import Base


async def ensure_db(engine: AsyncEngine, *, sentinel_table: str = "role_snapshots") -> None:
    """Ensure database tables exist.

    Uses a single sentinel table check because `metadata.create_all()` creates all
    tables together.

    This is safe to call concurrently across multiple processes; expected races
    are handled by catching common DDL errors.
    """
    try:
        async with engine.begin() as conn:

            def _table_exists(sync_conn: Connection) -> bool:
                inspector = inspect(sync_conn)
                return sentinel_table in inspector.get_table_names()

            table_exists = await conn.run_sync(_table_exists)
            if not table_exists:
                await conn.run_sync(Base.metadata.create_all)
    except (IntegrityError, ProgrammingError):
        # Race condition: another process already created the tables.
        return
