from sqlalchemy import create_engine
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
