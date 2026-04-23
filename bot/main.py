"""
Entry point.
Starts the APScheduler in background and serves the Flask webhook.
"""
import os
from dotenv import load_dotenv

load_dotenv()

from database import init_db
from scheduler import start_scheduler
from sms_handler import app

if __name__ == "__main__":
    print("🤖 Starting TFSA Trading Bot…")

    # Initialize SQLite
    init_db()

    # Start background scheduler (morning summary + sell monitor)
    start_scheduler()

    # Start Flask webhook server
    port = int(os.getenv("PORT", 5001))
    print(f"🌐 Webhook server on port {port}")
    print(f"   POST /webhook  ← Twilio inbound SMS")
    print(f"   GET  /health   ← uptime check")
    app.run(host="0.0.0.0", port=port)
