"""
WSGI entry point for gunicorn.
Runs setup (DB init, scheduler) at import time so gunicorn workers get it.
"""
import os
from dotenv import load_dotenv
load_dotenv()

from database import init_db
from scheduler import start_scheduler
from sms_handler import app  # noqa: F401 — gunicorn targets this

init_db()
_scheduler = start_scheduler()
