from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from rbaccatalog.core.singleton import ThreadSafeSingleton

if TYPE_CHECKING:
    import asyncpg
    from azure.identity.aio import ManagedIdentityCredential

    from rbaccatalog.settings import Settings

logger = logging.getLogger(__name__)

# PostgreSQL scope for Azure AD authentication
POSTGRES_SCOPE: Final[str] = "https://ossrdbms-aad.database.windows.net/.default"


class ManagedIdentityAuthenticator:
    """Singleton async token provider for Azure PostgreSQL authentication."""

    _instance: ThreadSafeSingleton[ManagedIdentityAuthenticator] | None = None

    def __init__(self) -> None:
        self._credential: ManagedIdentityCredential | None = None

    @classmethod
    def get(cls) -> ManagedIdentityAuthenticator:
        """Get the singleton instance."""
        if cls._instance is None:
            cls._instance = ThreadSafeSingleton(cls)
        return cls._instance.get()

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing)."""
        if cls._instance is not None:
            cls._instance.reset()

    @classmethod
    async def close(cls) -> None:
        """Close the singleton authenticator on shutdown."""
        if cls._instance and cls._instance.is_initialized:
            await cls._instance.get()._close()
            cls.reset()

    @property
    def credential(self) -> ManagedIdentityCredential:
        """Lazy-load credential to avoid import at module level."""
        if self._credential is None:
            from azure.identity.aio import ManagedIdentityCredential

            self._credential = ManagedIdentityCredential()
        return self._credential

    async def get_token(self) -> str:
        """Fetch a fresh access token for PostgreSQL."""
        try:
            token_obj = await self.credential.get_token(POSTGRES_SCOPE)
            return token_obj.token
        except Exception:
            logger.exception("Failed to fetch Azure AD token")
            raise

    async def _close(self) -> None:
        """Close the credential (internal use)."""
        if self._credential is not None:
            await self._credential.close()
            self._credential = None


# Module-level engine singleton


class DBEngine:
    """Singleton wrapper for the AsyncEngine."""

    _instance: ThreadSafeSingleton[AsyncEngine] | None = None

    @classmethod
    def get(cls) -> AsyncEngine:
        """Get the singleton AsyncEngine."""
        if cls._instance is None:
            cls._instance = ThreadSafeSingleton(factory=EngineFactory.from_settings)
        return cls._instance.get()

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing)."""
        if cls._instance is not None:
            cls._instance.reset()

    @classmethod
    async def dispose(cls) -> None:
        """Dispose the engine and MSI authenticator on shutdown."""
        if cls._instance and cls._instance.is_initialized:
            await cls._instance.get().dispose()
            cls.reset()
        await ManagedIdentityAuthenticator.close()


class EngineFactory:
    """Factory for creating SQLAlchemy async engines."""

    # Default pool settings
    POOL_SIZE = 10
    MAX_OVERFLOW = 20
    POOL_RECYCLE_STANDARD = 3600  # 1 hour
    # MSI tokens expire after 1 hour; recycle connections at 45 minutes
    # to ensure tokens are always valid when reused from the pool
    POOL_RECYCLE_MSI = 2700

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> AsyncEngine:
        """Create engine from application settings."""
        if settings is None:
            from rbaccatalog.settings import Settings

            settings = Settings.get()

        if settings.use_managed_identity:
            return cls.from_managed_identity(
                host=settings.msi_db_host,
                database=settings.msi_db_name,
                user=settings.msi_db_user,
                port=settings.msi_db_port,
            )

        return cls.from_connection_string(settings.db_connection_string)

    @classmethod
    def from_connection_string(cls, connection_string: str) -> AsyncEngine:
        """Create engine from a database connection string."""
        logger.info("Creating engine from connection string")

        if connection_string.startswith("sqlite"):
            return create_async_engine(connection_string)

        return create_async_engine(
            connection_string,
            pool_size=cls.POOL_SIZE,
            max_overflow=cls.MAX_OVERFLOW,
            pool_recycle=cls.POOL_RECYCLE_STANDARD,
            pool_pre_ping=True,
        )

    @classmethod
    def from_managed_identity(
        cls,
        host: str,
        database: str,
        user: str,
        port: int = 5432,
    ) -> AsyncEngine:
        """Create engine using Azure Managed Identity authentication."""
        logger.info(
            "Creating engine with managed identity: host=%s, port=%s, database=%s, user=%s",
            host,
            port,
            database,
            user,
        )

        # Use singleton authenticator to avoid leaking HTTP sessions
        auth = ManagedIdentityAuthenticator.get()

        async def async_connect(**_: Any) -> asyncpg.Connection:
            """Create connection with fresh managed identity token."""
            import asyncpg

            token = await auth.get_token()
            return await asyncpg.connect(
                host=host,
                port=port,
                database=database,
                user=user,
                password=token,
                ssl="require",
            )

        # SQLAlchemy requires a URL for dialect detection and pool config,
        # but async_creator handles the actual connection with the MSI token
        dialect_url = f"postgresql+asyncpg://{user}@{host}:{port}/{database}"

        return create_async_engine(
            dialect_url,
            async_creator=async_connect,
            pool_size=cls.POOL_SIZE,
            max_overflow=cls.MAX_OVERFLOW,
            pool_recycle=cls.POOL_RECYCLE_MSI,
            pool_pre_ping=True,
        )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given engine."""
    return async_sessionmaker(engine, expire_on_commit=False)
