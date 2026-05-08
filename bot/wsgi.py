"""
WSGI entry point for gunicorn.
Runs setup (DB init, scheduler) at import time so gunicorn workers get it.
"""
import atexit
import os
import sys

# Ensure bot/ is on sys.path so bare imports work when invoked from root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

# In Railway production RAILWAY_ENVIRONMENT is always set ("production").
# It is never set in local dev, so the check is skipped there.
# Failing here aborts gunicorn startup before any requests are served,
# leaving the previous Railway deploy active until the env var is added.
_railway_env = os.getenv("RAILWAY_ENVIRONMENT", "").strip()
if _railway_env and not os.getenv("API_SECRET", "").strip():
    raise RuntimeError(
        f"API_SECRET must be set in Railway (RAILWAY_ENVIRONMENT={_railway_env!r}). "
        "Generate one: python3 -c \"import secrets; print(secrets.token_hex(32))\""
    )

from database import init_db, run_migrations
from scheduler import start_scheduler, release_scheduler_lease
from sms_handler import app  # noqa: F401 — gunicorn targets this

init_db()
run_migrations()
_scheduler = start_scheduler()


def _on_shutdown() -> None:
    # Release the scheduler lease before the process exits so the next worker
    # or redeploy can claim it immediately rather than waiting for staleness.
    release_scheduler_lease()
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)


atexit.register(_on_shutdown)
