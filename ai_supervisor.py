import subprocess
import time
import sys
import os
import signal
import requests
import json
from pathlib import Path
from dotenv import load_dotenv

# Load for Strategic AI
load_dotenv(Path(__file__).parent / 'frontend' / '.env')
TOKEN_GROQ = os.environ.get('token_groq')

def log_ai(msg):
    print(f"\033[95m[🤖 STRATEGIC AI SUPERVISOR]\033[0m {msg}")

def cleanup():
    user = os.getlogin()
    os.system(f"ps -u {user} -o pid,command | grep -E 'chrome-headless-shell|playwright|engine_fast.py|run_swarm.py' | grep -v grep | awk '{{print $1}}' | xargs kill -9 2>/dev/null")

def consult_strategic_ai(log_snippet):
    """Menggunakan Llama-3.3-70B untuk mengambil keputusan taktis"""
    if not TOKEN_GROQ: return {"action": "restart", "wait": 15}
    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {TOKEN_GROQ}", "Content-Type": "application/json"}
        prompt = f"""LOG: {log_snippet}
        TikTok blocking detected. You are a stealth expert.
        Analyze the log and decide next action.
        JSON ONLY: {{"reason": "string", "action": "restart/wait/rotate", "wait_seconds": int}}"""

        data = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "response_format": {"type": "json_object"}}
        resp = requests.post(url, headers=headers, json=data, timeout=12).json()
        return json.loads(resp['choices'][0]['message']['content'])
    except:
        return {"action": "restart", "wait_seconds": 30}

def run_session():
    log_ai("🚀 Launching Swarm Ghost Mode...")
    process = subprocess.Popen([sys.executable, 'run_swarm.py'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, universal_newlines=True)

    logs = []
    try:
        for line in iter(process.stdout.readline, ''):
            print(line, end='')
            logs.append(line)
            if len(logs) > 30: logs.pop(0)

            if "BLOCKED" in line or "Captcha" in line or "Timeout" in line:
                log_ai("⚠️ Detection triggered! Consulting Strategic AI...")
                decision = consult_strategic_ai("".join(logs[-10:]))
                log_ai(f"🧠 AI DECISION: {decision.get('reason')}")

                process.terminate()
                cleanup()

                wait_time = decision.get('wait_seconds', 30)
                log_ai(f"⏳ Action: {decision.get('action')}. Cooling down for {wait_time}s...")
                time.sleep(wait_time)
                return False

    except KeyboardInterrupt:
        process.terminate()
        cleanup()
        sys.exit(0)
    return True

if __name__ == "__main__":
    cleanup()
    while True:
        if not run_session():
            log_ai("🔄 Self-Healing triggered. Re-initializing...")
