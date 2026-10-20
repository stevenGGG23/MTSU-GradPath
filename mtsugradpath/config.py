import os
from pathlib import Path
from dotenv import load_dotenv

# Finds the main project directory
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
try:
    DATA_DIR.mkdir(exist_ok=True)
except OSError:
    # Serverless environments (Vercel) have a read-only filesystem;
    # fall back to /tmp which is always writable.
    DATA_DIR = Path("/tmp/data")
    DATA_DIR.mkdir(exist_ok=True)

# Loads optional .env values
load_dotenv(BASE_DIR / ".env")

# Catalog source (the public Acalog widget API on catalog.mtsu.edu)
DATABASE_URL = os.getenv("DATABASE_URL")
BASE_CATALOG_URL = os.getenv("MTSU_CATALOG_URL", "https://catalog.mtsu.edu")
PROGRAM_PREFIX = os.getenv("MTSU_PROGRAM_PREFIX", "CSCI")

# SQLAlchemy connection pool sizing. Kept small by default since each gunicorn
# worker gets its own pool (workers x (pool_size + max_overflow) must stay under
# the DB's max connection limit -- Supabase's free-tier pooler caps this low).
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "3"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "2"))

# Optional HTTP Basic Auth guard for the catalog re-export route (/sync).
# Unset in dev by default; set both in production so re-export is not public.
SYNC_ADMIN_USER = os.getenv("SYNC_ADMIN_USER")
SYNC_ADMIN_PASSWORD = os.getenv("SYNC_ADMIN_PASSWORD")

# Local SQLlite database when no external one is found
if DATABASE_URL is None:
    DATABASE_URL = f"sqlite:///{DATA_DIR / 'mtsu_courses.db'}"
