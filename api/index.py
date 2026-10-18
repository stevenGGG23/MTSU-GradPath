import sys
import os

# Ensure project root is on the Python path so `app` and `mtsugradpath` are importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
