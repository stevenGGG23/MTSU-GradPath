from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from .config import DATABASE_URL, DB_POOL_SIZE, DB_MAX_OVERFLOW

# Supabase (and most hosted Postgres) requires SSL. Append sslmode=require
# when connecting to a non-SQLite database so the connection is encrypted.
_connect_args = {}
_pool_kwargs = {}
if not DATABASE_URL.startswith("sqlite"):
    _connect_args = {"sslmode": "require"}
    # SQLite's default pool (SingletonThreadPool) doesn't accept these kwargs,
    # so they're only passed for real (Postgres) connections.
    _pool_kwargs = {"pool_size": DB_POOL_SIZE, "max_overflow": DB_MAX_OVERFLOW}

# pool_pre_ping avoids "SSL connection has been closed unexpectedly" errors from
# stale pooled connections (Supabase's pgbouncer drops idle connections).
engine = create_engine(
    DATABASE_URL,
    future=True,
    connect_args=_connect_args,
    pool_pre_ping=True,
    **_pool_kwargs,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)

# Creates missing database tables
def init_db():
    from .models import Base

    Base.metadata.create_all(bind=engine)
    _ensure_sync_status_started_at_column()


# create_all() only creates missing tables, it never alters an existing one --
# so a production DB whose sync_status table predates the started_at column
# needs it added by hand. Not using Alembic for a single-column, single-table
# app like this, so this is a plain "add column if missing" instead: the
# ALTER fails harmlessly (already exists) on every startup after the first.
def _ensure_sync_status_started_at_column():
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE sync_status ADD COLUMN started_at FLOAT"))
    except Exception:
        pass
