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

def log(msg, type="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    color = "\033[94m" if type == "INFO" else "\033[92m" if type == "SUCCESS" else "\033[93m" if type == "WARNING" else "\033[91m"
    reset = "\033[0m"
    print(f"[{timestamp}] {color}{type:7}{reset} | {msg}", flush=True)

class SupabaseREST:
    def __init__(self):
        self.url = os.environ.get('VITE_SUPABASE_URL', '').rstrip('/')
        self.key = os.environ.get('VITE_SUPABASE_ANON_KEY') or os.environ.get('SUPABASE_SERVICE_KEY')
        self.headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    def get(self, table, params=""):
        try:
            r = requests.get(f"{self.url}/rest/v1/{table}?{params}", headers=self.headers, timeout=10)
            return r.json() if r.status_code == 200 else []
        except: return []

    def upsert(self, table, data, on_conflict="username"):
        try:
            headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
            r = requests.post(f"{self.url}/rest/v1/{table}?on_conflict={on_conflict}", headers=headers, json=data, timeout=10)
            return r.status_code in [200, 201]
        except: return False

class FastTiktokEngine:
    def __init__(self, db, worker_id):
        self.db = db
        self.worker_id = worker_id
        self.total_workers = int(os.environ.get('WORKER_TOTAL', '4'))
        self.categories = ["Kuliner", "Fashion", "Beauty", "Skincare", "Gadget", "Elektronik", "Home Living", "Jasa"]
        self.priority_list = [
            "Jakarta Selatan", "Jakarta Utara", "Jakarta Timur", "Jakarta Barat", "Jakarta Pusat",
            "Bandung", "Yogyakarta", "Solo", "Semarang", "Surabaya", "Malang", "Bali",
            "Sulawesi", "Sumatera", "Kalimantan", "Makassar",
            "Bogor", "Depok", "Tangerang", "Bekasi"
        ]
        self.processed_usernames = set()
        self.extraction_semaphore = asyncio.Semaphore(10) # Max 10 parallel extractions per worker

    def load_targets(self):
        cities = self.db.get("cities", "select=name")
        provinces = self.db.get("provinces", "select=name")
        targets = []
        for p in self.priority_list: targets.append({"name": p, "type": "priority"})
        p_names = [p.lower() for p in self.priority_list]
        for c in cities:
            if c['name'].lower() not in p_names: targets.append({"name": c['name'], "type": "city"})
        for pr in provinces: targets.append({"name": pr['name'], "type": "province"})
        return [t for i, t in enumerate(targets) if i % self.total_workers == self.worker_id]

    async def extract_profile(self, context, username, category):
        async with self.extraction_semaphore:
            if username in self.processed_usernames: return
            page = await context.new_page()
            try:
                await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=15000)
                f_el = await page.query_selector('[data-e2e="followers-count"]')
                if not f_el: return
                f_text = (await f_el.inner_text()).upper()
                mult = 1000 if 'K' in f_text else 1000000 if 'M' in f_text else 1
                count = int(float(re.sub(r'[^\d.]', '', f_text.replace(',', '.')) or 0) * mult)

                if count < 100000:
                    bio_el = await page.query_selector('[data-e2e="user-bio"]')
                    bio = await bio_el.inner_text() if bio_el else ""
                    name_el = await page.query_selector('[data-e2e="user-title"]')
                    name = await name_el.inner_text() if name_el else username

                    data = {"username": username, "display_name": name, "bio": bio, "followers_count": count, "category": category, "last_scraped": datetime.now().isoformat()}
                    self.db.upsert('sellers_v2', data)
                    self.processed_usernames.add(username)
                    log(f"🔥 INSTANT: {username} ({count} f) -> sellers_v2", "SUCCESS")
            except: pass
            finally: await page.close()

    async def search_massive(self, context, keyword, category):
        page = await context.new_page()
        queued_for_kw = set()
        try:
            await page.goto(f"https://www.tiktok.com/search/user?q={urllib.parse.quote(keyword)}", timeout=30000)
            await asyncio.sleep(2)

            last_h, same_c = 0, 0
            while same_c < 10:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1)
                new_h = await page.evaluate("document.body.scrollHeight")
                if new_h == last_h: same_c += 1
                else: same_c, last_h = 0, new_h

                # Immediate extraction during scroll
                anchors = await page.query_selector_all('a[href*="/@"]')
                for a in anchors:
                    h = await a.get_attribute("href")
                    if h and "@" in h:
                        u = re.search(r'@([\w._]+)', h).group(1).lower()
                        if u not in self.processed_usernames and u not in queued_for_kw:
                            queued_for_kw.add(u)
                            # Start extraction task immediately in background
                            asyncio.create_task(self.extract_profile(context, u, category))
        except: pass
        finally: await page.close()

    async def run_swarm(self):
        targets = self.load_targets()
        log(f"🚀 Worker {self.worker_id} Online: Real-Time Mode Enabled.")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent="Mozilla/5.0")
            while True:
                end_cycle = datetime.now() + timedelta(hours=5)
                for t in targets:
                    if datetime.now() > end_cycle: break
                    for cat in self.categories:
                        kw = f"{cat} murah {t['name']}"
                        log(f"🔍 Deep Scanning: {kw}")
                        await self.search_massive(context, kw, cat)
                log("😴 Cycle done. Resting 5 minutes...", "WARNING")
                await asyncio.sleep(300)

if __name__ == "__main__":
    db = SupabaseREST()
    worker_id = int(os.environ.get('WORKER_INDEX', 0))
    asyncio.run(FastTiktokEngine(db, worker_id).run_swarm())
