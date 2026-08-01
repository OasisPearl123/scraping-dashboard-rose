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

    def lock_keyword(self, keyword, worker_id):
        try:
            encoded_kw = urllib.parse.quote(keyword)
            existing = self.get("search_queries", f"query=eq.{encoded_kw}&select=status,locked_at")
            if existing:
                if existing[0]['status'] == 'completed':
                    # Re-scrape if older than 24h to get fresh data
                    la = datetime.fromisoformat(existing[0]['locked_at'].replace('Z', '+00:00'))
                    if (datetime.now(la.tzinfo) - la).total_seconds() < 86400: return False
                elif existing[0]['status'] == 'processing':
                    la = datetime.fromisoformat(existing[0]['locked_at'].replace('Z', '+00:00'))
                    if (datetime.now(la.tzinfo) - la).total_seconds() < 3600: return False

            data = {"query": keyword, "status": "processing", "locked_by": worker_id, "locked_at": datetime.now().isoformat()}
            headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=representation"}
            r = requests.post(f"{self.url}/rest/v1/search_queries?on_conflict=query", headers=headers, json=data, timeout=10)
            return r.status_code in [200, 201]
        except: return False

class FastTiktokEngine:
    def __init__(self, db, worker_id):
        self.db = db
        self.worker_id = worker_id
        self.total_workers = int(os.environ.get('WORKER_TOTAL', '4'))
        self.categories = ["Kuliner", "Fashion", "Beauty", "Skincare", "Gadget", "Elektronik", "Home Living", "Jasa"]
        self.priority_locations = ["Jakarta Selatan", "Jakarta Utara", "Jakarta Timur", "Jakarta Barat", "Bandung", "Yogyakarta", "Solo", "Semarang", "Surabaya", "Malang", "Bali", "Sulawesi", "Sumatera", "Kalimantan", "Makassar", "Bogor", "Depok", "Tangerang", "Bekasi"]
        self.cities = self.load_cities()
        self.processed_usernames = set()

    def load_cities(self):
        cities = self.db.get("cities", "select=name,province_id")
        provinces = self.db.get("provinces", "select=id,name")
        if not cities: return []
        p_map = {p['id']: p['name'] for p in provinces}
        for c in cities: c['province_name'] = p_map.get(c['province_id'], "Indonesia")

        # Priority sharding
        sorted_cities = []
        for loc in self.priority_locations:
            match = [c for c in cities if loc.lower() in c['name'].lower()]
            sorted_cities.extend(match)
        remaining = [c for c in cities if c not in sorted_cities]
        full_list = sorted_cities + remaining

        return [c for i, c in enumerate(full_list) if i % self.total_workers == self.worker_id]

    async def extract_profile(self, context, username, category):
        if username in self.processed_usernames: return
        page = await context.new_page()
        try:
            await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(500)
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
                log(f"✅ {username} ({count})", "SUCCESS")
        except: pass
        finally: await page.close()

    async def search_infinite(self, context, keyword, category):
        page = await context.new_page()
        try:
            await page.goto(f"https://www.tiktok.com/search/user?q={urllib.parse.quote(keyword)}", timeout=30000)
            await asyncio.sleep(2)

            last_h, same_c = 0, 0
            while same_c < 10: # Infinite scroll until no more accounts
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(1)
                new_h = await page.evaluate("document.body.scrollHeight")
                if new_h == last_h: same_c += 1
                else: same_c, last_h = 0, new_h

                # Dynamic batch extraction during scroll
                anchors = await page.query_selector_all('a[href*="/@"]')
                batch = []
                for a in anchors:
                    h = await a.get_attribute("href")
                    if h and "@" in h:
                        u = re.search(r'@([\w._]+)', h).group(1).lower()
                        if u not in self.processed_usernames: batch.append(u)

                if batch:
                    # Parallel extraction batch of 20
                    for i in range(0, len(batch), 20):
                        await asyncio.gather(*[self.extract_profile(context, user, category) for user in batch[i:i+20]])
                        if i > 100: break # Small break to prevent lockup
        except: pass
        finally: await page.close()

    async def run_session(self):
        log(f"🚀 Worker {self.worker_id} Session Start (5 Hours)")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent="Mozilla/5.0")

            end_time = datetime.now() + timedelta(hours=5)
            while datetime.now() < end_time:
                for city in self.cities:
                    for cat in self.categories:
                        if datetime.now() >= end_time: break
                        keyword = f"{cat} {city['name']}"
                        if self.db.lock_keyword(keyword, f"worker_{self.worker_id}"):
                            log(f"🔍 {keyword}")
                            await self.search_infinite(context, keyword, cat)
                            self.db.upsert('search_queries', {'query': keyword, 'status': 'completed', 'locked_at': datetime.now().isoformat()}, on_conflict='query')
                if datetime.now() < end_time:
                    log("♻️ All assigned cities processed, restarting cycle...")
                    await asyncio.sleep(60) # Wait a bit before restart
            await browser.close()

if __name__ == "__main__":
    db = SupabaseREST()
    worker_id = int(os.environ.get('WORKER_INDEX', 0))
    engine = FastTiktokEngine(db, worker_id)
    while True:
        asyncio.run(engine.run_session())
        log("😴 Resting for 5 minutes...", "WARNING")
        import time
        time.sleep(300)
