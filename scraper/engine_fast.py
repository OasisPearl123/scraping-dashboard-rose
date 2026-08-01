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
        self.groq_token = os.environ.get('token_groq')
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
            if existing and existing[0]['status'] == 'processing':
                locked_at = datetime.fromisoformat(existing[0]['locked_at'].replace('Z', '+00:00'))
                if (datetime.now(locked_at.tzinfo) - locked_at).total_seconds() < 1800: return False

            data = {"query": keyword, "status": "processing", "locked_by": worker_id, "locked_at": datetime.now().isoformat()}
            headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=representation"}
            r = requests.post(f"{self.url}/rest/v1/search_queries?on_conflict=query", headers=headers, json=data, timeout=10)
            return r.status_code in [200, 201]
        except: return False

class FastTiktokEngine:
    def __init__(self, db, worker_id):
        self.db = db
        self.worker_id = worker_id
        self.total_workers = int(os.environ.get('WORKER_TOTAL', '1'))
        self.categories = ["Kuliner", "Fashion", "Beauty", "Skincare", "Gadget", "Elektronik", "Home Living", "Jasa"]
        self.priority_locations = ["Jakarta Selatan", "Jakarta Utara", "Jakarta Timur", "Jakarta Barat", "Bandung", "Yogyakarta", "Solo", "Semarang", "Surabaya", "Malang", "Bali", "Sulawesi", "Sumatera", "Kalimantan", "Makassar", "Bogor", "Depok", "Tangerang", "Bekasi"]
        self.cities = self.load_cities()
        self.processed_usernames = set()

    def load_cities(self):
        cities = self.db.get("cities", "select=name,province_id")
        provinces = self.db.get("provinces", "select=id,name")
        if not cities or not provinces: return []
        p_map = {p['id']: p['name'] for p in provinces}
        for c in cities: c['province_name'] = p_map.get(c['province_id'], "Indonesia")

        # Sort cities: Priority first
        sorted_cities = []
        for loc in self.priority_locations:
            match = [c for c in cities if loc.lower() in c['name'].lower()]
            sorted_cities.extend(match)

        remaining = [c for c in cities if c not in sorted_cities]
        full_list = sorted_cities + remaining

        # Sharding
        return [c for i, c in enumerate(full_list) if i % self.total_workers == self.worker_id]

    async def extract_profile(self, context, username, category):
        if username in self.processed_usernames: return
        page = await context.new_page()
        try:
            await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1000)
            f_el = await page.query_selector('[data-e2e="followers-count"]')
            if not f_el: return

            # Fast Parse Followers
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
                log(f"✅ {username} ({count} f)", "SUCCESS")
        except: pass
        finally: await page.close()

    async def search_massive(self, context, keyword, category):
        page = await context.new_page()
        try:
            await page.goto(f"https://www.tiktok.com/search/user?q={urllib.parse.quote(keyword)}", timeout=30000)
            for _ in range(15): # Deep scroll
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(0.5)

            anchors = await page.query_selector_all('a[href*="/@"]')
            users = list(set([re.search(r'@([\w._]+)', (await a.get_attribute("href"))).group(1).lower() for a in anchors if "@" in (await a.get_attribute("href"))]))

            # Parallel Extraction batch 20
            for i in range(0, len(users), 20):
                await asyncio.gather(*[self.extract_profile(context, u, category) for u in users[i:i+20]])
        except: pass
        finally: await page.close()

    async def run(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent="Mozilla/5.0")

            while True:
                for city in self.cities:
                    for cat in self.categories:
                        keyword = f"{cat} {city['name']}"
                        if self.db.lock_keyword(keyword, f"worker_{self.worker_id}"):
                            log(f"🚀 Processing: {keyword} ({city['province_name']})")
                            await self.search_massive(context, keyword, cat)
                            self.db.upsert('search_queries', {'query': keyword, 'status': 'completed'}, on_conflict='query')
                await asyncio.sleep(300)

if __name__ == "__main__":
    db = SupabaseREST()
    worker_id = int(os.environ.get('WORKER_INDEX', 0))
    asyncio.run(FastTiktokEngine(db, worker_id).run())
