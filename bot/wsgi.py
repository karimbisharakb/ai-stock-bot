"""
WSGI entry point for gunicorn.
Runs setup (DB init, scheduler) at import time so gunicorn workers get it.
"""
import os
import sys

# Ensure bot/ is on sys.path so bare imports work when invoked from root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from database import init_db
from scheduler import start_scheduler
from sms_handler import app  # noqa: F401 — gunicorn targets this

init_db()
_scheduler = start_scheduler()
