import os
import re
import json
import asyncio
import random
import requests
import urllib.parse
from datetime import datetime, timedelta
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
    prefix = f"[SCOUT-{worker_id}]" if os.environ.get('SCOUT_MODE') else f"[REAPER-{worker_id}]"
    print(f"[{timestamp}] {color}{type:7}{reset} | {prefix} {msg}", flush=True)

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

class FastTiktokEngine:
    def __init__(self, db, worker_id):
        self.db = db
        self.worker_id = worker_id
        self.total_workers = int(os.environ.get('WORKER_TOTAL', '4'))
        self.is_scout = os.environ.get('SCOUT_MODE') == "true"
        self.categories = ["Kuliner", "Fashion", "Beauty", "Skincare", "Gadget", "Elektronik", "Home Living", "Jasa"]
        self.processed_usernames = set()
        self.token = os.environ.get('token_groq')

    async def call_ai(self, model, prompt):
        if not self.token: return None
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
            data = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1, "response_format": {"type": "json_object"}}
            resp = requests.post(url, headers=headers, json=data, timeout=12).json()
            return json.loads(resp['choices'][0]['message']['content'])
        except: return None

    async def scout_search(self, page, keyword, category):
        """GITHUB SIDE: Mencari akun dan memasukkannya ke antrean"""
        try:
            log(self.worker_id, f"🔎 SCOUTING: {keyword}")
            await page.goto(f"https://www.tiktok.com/search/user?q={urllib.parse.quote(keyword)}", wait_until="networkidle", timeout=60000)
            await asyncio.sleep(8)

            anchors = await page.query_selector_all('a[href*="/@"]')
            added = 0
            for a in anchors[:15]:
                h = await a.get_attribute("href")
                if h and "@" in h:
                    u = re.search(r'@([\w._]+)', h).group(1).lower()
                    # Simpan ke search_queries sebagai antrean (status=pending)
                    if self.db.upsert('search_queries', {"query": u, "status": "pending", "locked_by": category}, on_conflict="query"):
                        added += 1
            log(self.worker_id, f"✅ Scouted {added} users for {keyword}", "SUCCESS")
        except Exception as e:
            log(self.worker_id, f"⚠️ Scout failed: {e}", "WARNING")

    async def reaper_extract(self, page, task):
        """LOCAL SIDE: Mengambil antrean dan membedahnya"""
        username = task['query']
        category = task.get('locked_by', 'Umum')
        try:
            log(self.worker_id, f"⚡ REAPING: @{username}")
            await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(5, 8))

            # Cek Followers & Bio
            f_el = await page.query_selector('[data-e2e="followers-count"]')
            if not f_el: return

            bio = await page.inner_text('[data-e2e="user-bio"]') if await page.query_selector('[data-e2e="user-bio"]') else ""

            # AI Verifikasi (Qwen-27B)
            res = await self.call_ai("qwen/qwen3.6-27b", f"Is @{username} a shop in Indonesia? Bio: {bio}. JSON: {{\"v\":true/false,\"r\":\"reason\"}}")

            if res and res.get("v"):
                data = {"username": username, "followers_count": 0, "category": category, "city": "Indonesia", "potential_score": 90, "potential_reason": res.get("r"), "last_scraped": datetime.now().isoformat(), "platform": "tiktok", "bio": bio}
                if self.db.upsert('sellers_v2', data):
                    log(self.worker_id, f"💎 HARVESTED: @{username}", "SUCCESS")

            # Tandai selesai
            self.db.upsert('search_queries', {"query": username, "status": "completed"}, on_conflict="query")
        except Exception as e:
            log(self.worker_id, f"⚠️ Reaper error @{username}: {e}", "WARNING")

    async def run_hybrid(self):
        # 1. Fetch cities safely
        cities = self.db.get("cities", "select=name")
        targets = [c['name'] for c in (cities or []) if 'name' in c]
        if not targets: targets = ["Jakarta", "Bandung", "Surabaya", "Yogyakarta"]

        random.shuffle(targets)
        my_targets = [t for i, t in enumerate(targets) if i % self.total_workers == self.worker_id]

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled', '--no-sandbox'])
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            page = await context.new_page()

            while True:
                if self.is_scout:
                    # GITHUB MODE: Fokus mencari
                    for target in my_targets[:5]: # Batasi biar gak kelamaan di satu run
                        for cat in self.categories:
                            await self.scout_search(page, f"{cat} murah {target}", cat)
                            await asyncio.sleep(random.uniform(30, 60))
                    break # Selesai 1 run GitHub
                else:
                    # LOCAL MODE: Fokus memanen profil dari DB
                    queue = self.db.get('search_queries', "status=eq.pending&limit=10")
                    if not queue:
                        log(self.worker_id, "🌾 No queue found. Resting 2m...")
                        await asyncio.sleep(120)
                        continue

                    for task in queue:
                        # Lock task
                        if self.db.upsert('search_queries', {"query": task['query'], "status": "processing"}, on_conflict="query"):
                            await self.reaper_extract(page, task)
                            await asyncio.sleep(random.uniform(10, 20))

                    await asyncio.sleep(30)

if __name__ == "__main__":
    idx = int(os.environ.get('WORKER_INDEX', 0))
    asyncio.run(FastTiktokEngine(SupabaseREST(), idx).run_hybrid())
