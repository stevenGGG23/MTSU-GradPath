"""SQLAlchemy engine and session factory.

DATABASE_URL is read from the environment (see config.py).  SQLite is used
for local development; Supabase Postgres is used in production.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from .config import DATABASE_URL, DB_POOL_SIZE, DB_MAX_OVERFLOW

# Supabase (and most hosted Postgres) requires SSL.  Append sslmode=require
# when connecting to a non-SQLite database so the connection is encrypted.
_connect_args = {}
_pool_kwargs = {}
if not DATABASE_URL.startswith("sqlite"):
    _connect_args = {"sslmode": "require"}
    # SQLite's default pool (SingletonThreadPool) doesn't accept pool_size /
    # max_overflow, so they're only passed for real Postgres connections.
    _pool_kwargs = {"pool_size": DB_POOL_SIZE, "max_overflow": DB_MAX_OVERFLOW}

# pool_pre_ping avoids "SSL connection has been closed unexpectedly" errors
# from stale pooled connections (Supabase's pgbouncer drops idle connections).
engine = create_engine(
    DATABASE_URL,
    future=True,
    connect_args=_connect_args,
    pool_pre_ping=True,
    **_pool_kwargs,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)


def init_db():
    """Create missing database tables and apply any pending schema patches."""
    from .models import Base
    Base.metadata.create_all(bind=engine)
    _ensure_sync_status_started_at_column()


def _ensure_sync_status_started_at_column():
    """Add the started_at column to sync_status if it doesn't exist yet.

    create_all() only creates missing *tables*, never alters an existing one,
    so a production DB that predates this column needs it added manually.
    The ALTER is attempted on every startup and fails harmlessly (already
    exists) after the first successful run.  No Alembic for a one-column patch.
    """
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE sync_status ADD COLUMN started_at FLOAT"))
    except Exception:
        pass
