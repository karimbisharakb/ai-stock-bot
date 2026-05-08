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
