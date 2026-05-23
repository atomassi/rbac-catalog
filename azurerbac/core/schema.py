from sqlalchemy import inspect
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine

from .models import Base


async def ensure_db(engine: AsyncEngine, *, sentinel_table: str = "roles") -> None:
    """Ensure database tables exist (safe for concurrent calls)."""
    try:
        async with engine.begin() as conn:

            def _table_exists(sync_conn: Connection) -> bool:
                return sentinel_table in inspect(sync_conn).get_table_names()

            if not await conn.run_sync(_table_exists):
                await conn.run_sync(Base.metadata.create_all)
    except (IntegrityError, ProgrammingError):
        pass  # Race condition: another process created tables
