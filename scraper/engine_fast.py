import os
import re
import json
import asyncio
import random
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# Load environment
base_dir = Path(__file__).parent.parent
env_paths = [base_dir / 'frontend' / '.env', base_dir / '.env', Path('.env')]
for p in env_paths:
    if p.exists(): load_dotenv(p)

def log(worker_id, msg, type="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    color = "\033[94m" if type == "INFO" else "\033[92m" if type == "SUCCESS" else "\033[93m" if type == "WARNING" else "\033[91m"
    reset = "\033[0m"
    print(f"[{timestamp}] {color}{type:7}{reset} | [REAPER-{worker_id}] {msg}", flush=True)

class SupabaseREST:
    def __init__(self):
        self.url = os.environ.get('VITE_SUPABASE_URL', '').rstrip('/')
        self.key = os.environ.get('VITE_SUPABASE_ANON_KEY') or os.environ.get('SUPABASE_SERVICE_KEY')
        self.headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    def upsert(self, table, data, on_conflict="username"):
        try:
            headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
            r = requests.post(f"{self.url}/rest/v1/{table}?on_conflict={on_conflict}", headers=headers, json=data, timeout=10)
            return r.status_code in [200, 201]
        except: return False

    def get(self, table, params=""):
        try:
            r = requests.get(f"{self.url}/rest/v1/{table}?{params}", headers=self.headers, timeout=15)
            return r.json() if r.status_code == 200 else []
        except: return []

class LocalReaperEngine:
    def __init__(self, db, worker_id):
        self.db = db
        self.worker_id = worker_id
        self.token = os.environ.get('token_groq')
        self.master_session = base_dir / "master_auth_state.json"
        self.city_map = {} # Cache untuk mapping kota ke provinsi

    async def call_ai(self, prompt):
        if not self.token: return None
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
            # Gunakan Qwen-27B untuk bahasa Indonesia yang natural
            data = {"model": "qwen/qwen3.6-27b", "messages": [{"role": "user", "content": prompt}], "temperature": 0.1, "response_format": {"type": "json_object"}}
            resp = requests.post(url, headers=headers, json=data, timeout=12).json()
            return json.loads(resp['choices'][0]['message']['content'])
        except: return None

    async def process_account(self, page, task, city_info):
        username = task['query']
        category, city = city_info
        province = self.city_map.get(city.lower(), "Indonesia")

        log(self.worker_id, f"⚡ Membedah: @{username} ({city})")
        try:
            await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(5, 8))

            bio = await page.inner_text('[data-e2e="user-bio"]') if await page.query_selector('[data-e2e="user-bio"]') else ""

            # AI VALIDATION (Instruksi dalam Bahasa Indonesia)
            prompt = f"Apakah @{username} adalah toko/bisnis riil di Indonesia? Analisis Bio: {bio}. Berikan jawaban dalam JSON: {{\"v\":true/false, \"r\":\"alasan singkat dalam bahasa indonesia\"}}"
            res = await self.call_ai(prompt)

            if res and res.get("v"):
                data = {
                    "username": username, "category": category, "city": city, "province": province,
                    "potential_score": 95, "potential_reason": res.get("r"),
                    "last_scraped": datetime.now().isoformat(), "platform": "tiktok", "bio": bio
                }
                if self.db.upsert('sellers', data):
                    log(self.worker_id, f"💎 VALID ({city}): @{username} SAVED!", "SUCCESS")

            self.db.upsert('search_queries', {"query": username, "status": "completed"}, on_conflict="query")
        except Exception as e:
            log(self.worker_id, f"⚠️ Gagal: {e}", "WARNING")

    async def run(self):
        # Build City-Province Map
        log(self.worker_id, "📡 Membangun peta lokasi...")
        cities = self.db.get("cities", "select=name,province_id")
        provinces = self.db.get("provinces", "select=id,name")
        p_dict = {p['id']: p['name'] for p in (provinces or [])}
        self.city_map = {c['name'].lower(): p_dict.get(c['province_id'], "Indonesia") for c in (cities or [])}

        async with async_playwright() as p:
            launch_args = {'headless': True, 'args': ['--disable-blink-features=AutomationControlled', '--no-sandbox']}
            if self.master_session.exists():
                context = await p.chromium.launch_persistent_context(user_data_dir=f"reaper_session_{self.worker_id}", storage_state=str(self.master_session), **launch_args)
            else:
                context = await p.chromium.launch_persistent_context(user_data_dir=f"reaper_session_{self.worker_id}", **launch_args)

            page = context.pages[0] if context.pages else await context.new_page()

            while True:
                queue = self.db.get('search_queries', "status=eq.pending&limit=1")
                if not queue:
                    await asyncio.sleep(20)
                    continue

                task = queue[0]
                # Pecah info Category dan City
                info = task.get('locked_by', 'Umum|Jakarta').split('|')
                if len(info) < 2: info = [info[0], "Jakarta"]

                if self.db.upsert('search_queries', {"query": task['query'], "status": "processing"}, on_conflict="query"):
                    await self.process_account(page, task, info)

                await asyncio.sleep(random.uniform(10, 20))

if __name__ == "__main__":
    idx = int(os.environ.get('WORKER_INDEX', 0))
    asyncio.run(LocalReaperEngine(SupabaseREST(), idx).run())
