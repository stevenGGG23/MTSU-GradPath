import os
from pathlib import Path
from dotenv import load_dotenv

# Finds the main project directory
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Lodas optional .env values
load_dotenv(BASE_DIR / ".env")

# Scraper source
DATABASE_URL = os.getenv("DATABASE_URL")
BASE_CATALOG_URL = os.getenv("MTSU_CATALOG_URL", "https://catalog.mtsu.edu")
PROGRAM_PREFIX = os.getenv("MTSU_PROGRAM_PREFIX", "CSCI")

# This is an undergraduate degree planner (B.S. Computer Science), so only
# the undergraduate catalog (46) is synced by default.
_catalog_ids_env = os.getenv("MTSU_CATALOG_IDS")
if _catalog_ids_env:
    CATALOG_IDS = [int(cid.strip()) for cid in _catalog_ids_env.split(",") if cid.strip()]
else:
    CATALOG_IDS = [46]

# Local SQLlite database when no external one is found
if DATABASE_URL is None:
    DATABASE_URL = f"sqlite:///{DATA_DIR / 'mtsu_courses.db'}"
