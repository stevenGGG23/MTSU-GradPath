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

# Catalog IDs to sync.  catoid=36 is the most complete undergrad catalog
# accessible via the public widget API (catoids 44-49 require an auth token).
# catoid=33 and 40/41/43 are graduate-only catalogs.
# Override with the MTSU_CATALOG_IDS env var once an API key is in place.
_catalog_ids_env = os.getenv("MTSU_CATALOG_IDS")
if _catalog_ids_env:
    CATALOG_IDS = [int(cid.strip()) for cid in _catalog_ids_env.split(",") if cid.strip()]
else:
    CATALOG_IDS = [36]

# Local SQLlite database when no external one is found
if DATABASE_URL is None:
    DATABASE_URL = f"sqlite:///{DATA_DIR / 'mtsu_courses.db'}"

# Cache file for last successful catalog sync (used as fallback when API is unreachable)
CACHE_FILE = DATA_DIR / "courses_cache.json"

# HTML catalog fallback — used when the widget API returns 401.
# catoid and navoid come from the URL the admin bookmarked in the catalog browser.
# Example: https://catalog.mtsu.edu/content.php?catoid=33&navoid=7425&filter[item_type]=3&expand=1
CATALOG_HTML_CATOID = int(os.getenv("MTSU_HTML_CATOID", "33"))
CATALOG_HTML_NAVOID = int(os.getenv("MTSU_HTML_NAVOID", "7425"))
