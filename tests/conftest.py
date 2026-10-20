import os

# app.py reads SECRET_KEY at import time
os.environ.setdefault("SECRET_KEY", "test-secret-key")


def pytest_configure(config):
    # Test clients make hundreds of requests a minute; the per-IP rate limit
    # is for production traffic, not the suite
    from app import limiter
    limiter.enabled = False
