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
    # connect_timeout: an unreachable host fails fast instead of hanging a worker
    _connect_args = {"sslmode": "require", "connect_timeout": 5}
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
    _lock_down_public_tables()


def _lock_down_public_tables():
    """Turn on Row-Level Security for every table in the public schema.

    Supabase serves the public schema through its auto-generated REST API,
    reachable with the project's public "anon" key. With RLS off, anyone
    with the project URL can read, edit and delete those tables. RLS with no
    policies blocks that API completely. GradPath itself connects straight
    to Postgres as the database owner, which RLS doesn't restrict, so the
    app is unaffected. Runs on every start (it's a no-op once enabled), so a
    newly created table is covered too. Postgres only; skipped for SQLite.
    """
    if engine.dialect.name != "postgresql":
        return
    try:
        with engine.begin() as conn:
            tables = conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND NOT rowsecurity"
            )).scalars().all()
            for table in tables:
                # Each in its own savepoint: a table we don't own can't stop the rest
                try:
                    with conn.begin_nested():
                        conn.execute(text(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY'))
                except Exception:
                    pass
    except Exception:
        pass


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
