import os
import time
import subprocess
import json
from datetime import datetime

def log(msg, type="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {type:7} | {msg}", flush=True)

def run_command(cmd):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return result.stdout.strip()
    except Exception as e:
        return f"Error: {e}"

def check_and_fix_workers():
    log("Starting Worker Self-Healing Monitor...")

    while True:
        try:
            # 1. Get status of all 15 workers
            raw_runs = run_command("gh run list --limit 50 --json name,status,conclusion,databaseId")
            if not raw_runs or "Error" in raw_runs:
                log("Failed to fetch runs, retrying in 30s...", "WARNING")
                time.sleep(30)
                continue

            runs = json.loads(raw_runs)

            # Map worker name to its latest run
            worker_status = {}
            for run in runs:
                name = run['name']
                if "TikTok Scraper Worker" in name:
                    if name not in worker_status:
                        worker_status[name] = run

            # 2. Re-trigger failed or missing workers
            for i in range(1, 16):
                name = f"TikTok Scraper Worker {i:02d}"
                filename = f"worker_{i:02d}.yml"

                status = worker_status.get(name, {})
                current_status = status.get('status')
                conclusion = status.get('conclusion')

                should_run = False
                reason = ""

                if not status:
                    should_run = True
                    reason = "No existing run found"
                elif current_status == "completed" and conclusion in ["failure", "cancelled", "timed_out"]:
                    should_run = True
                    reason = f"Last run {conclusion}"

                if should_run:
                    log(f"Triggering {name} | Reason: {reason}", "WARNING")
                    run_command(f"gh workflow run {filename}")
                    time.sleep(5) # Small gap between triggers
                else:
                    log(f"{name} is currently {current_status or 'pending'}...", "INFO")

            log("All workers checked. Sleeping for 5 minutes...", "SUCCESS")
            time.sleep(300) # Check every 5 minutes

        except KeyboardInterrupt:
            log("Monitor stopped by user.")
            break
        except Exception as e:
            log(f"Monitor Loop Error: {e}", "ERROR")
            time.sleep(60)

if __name__ == "__main__":
    check_and_fix_workers()
