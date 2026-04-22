import schedule
import time
import subprocess
import os
from datetime import datetime

def run_bot():
    print(f"\n⏰ [{datetime.now().strftime('%Y-%m-%d %H:%M')}] Running daily stock analysis...")
    os.chdir("/Users/karimbishara/investing-agent")
    subprocess.run(["python3", "stocks.py"])
    print("✅ Done. Next run tomorrow at 9:00am.")

# Run every day at 9:00am before market open
schedule.every().day.at("09:00").do(run_bot)

print("🤖 Scheduler started. Bot will run every day at 9:00am.")
print("Press CTRL+C to stop.\n")

# Run once immediately on start
run_bot()

while True:
    schedule.run_pending()
    time.sleep(60)