import os
import re
import time
import json
import asyncio
import random
import requests
import psycopg2
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# 1. LOAD ENVIRONMENT
base_dir = Path(__file__).parent.parent
env_paths = [base_dir / 'frontend' / '.env', base_dir / '.env', Path('.env')]
for p in env_paths:
    if p.exists(): load_dotenv(p)

def log(msg, type="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    color = "\033[94m" if type == "INFO" else "\033[92m" if type == "SUCCESS" else "\033[93m" if type == "WARNING" else "\033[91m"
    reset = "\033[0m"
    print(f"[{timestamp}] {color}{type:7}{reset} | {msg}", flush=True)

class Res:
    def __init__(self, data=None): self.data = data or []

class SupabaseREST:
    def __init__(self):
        self.url = os.environ.get('VITE_SUPABASE_URL', '').rstrip('/')
        self.key = os.environ.get('VITE_SUPABASE_ANON_KEY') or os.environ.get('SUPABASE_SERVICE_KEY')
        self.db_pass = os.environ.get('PASSWORD_SUPABASE')
        self.groq_token = os.environ.get('token_groq')

        if not self.url or not self.key:
            log("CRITICAL: Supabase URL or Key missing!", "ERROR")

        self.headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json"
        }

        self.db_host = ""
        if self.url:
            project_ref = self.url.split('//')[-1].split('.')[0]
            self.db_host = f"db.{project_ref}.supabase.co"

    def get(self, table, params=""):
        try:
            r = requests.get(f"{self.url}/rest/v1/{table}?{params}", headers=self.headers, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log(f"REST GET {table} failed: {e}", "ERROR")
            return []

    def upsert(self, table, data, on_conflict="username"):
        try:
            # FIX: Added on_conflict parameter to the URL for PostgREST upsert
            headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
            url = f"{self.url}/rest/v1/{table}?on_conflict={on_conflict}"
            r = requests.post(url, headers=headers, json=data, timeout=15)
            r.raise_for_status()
            return True
        except Exception as e:
            log(f"REST UPSERT {table} failed: {e}", "ERROR")
            return False

    def ai_generate_keywords(self, city, category):
        if not self.groq_token: return []
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self.groq_token}", "Content-Type": "application/json"}
            prompt = f"Generate 50 TikTok search keywords for finding local sellers in {city} for {category}. Mixed slang/formal. JSON array only."
            data = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "temperature": 0.9}
            resp = requests.post(url, headers=headers, json=data, timeout=25).json()
            match = re.search(r'\[.*\]', resp['choices'][0]['message']['content'], re.DOTALL)
            return json.loads(match.group(0)) if match else []
        except Exception: return []

class TiktokEngine:
    def __init__(self, db):
        self.db = db
        self.categories = ["Kuliner", "Fashion", "Beauty", "Skincare", "Gadget", "Elektronik", "Home Living", "Jasa"]
        self.cities = self.load_cities()

    def load_cities(self):
        log("Fetching cities and provinces...")
        if self.db.db_pass and self.db.db_host:
            try:
                conn = psycopg2.connect(host=self.db.db_host, database='postgres', user='postgres', password=self.db.db_pass, port='5432', connect_timeout=5)
                cur = conn.cursor()
                cur.execute("SELECT c.name, p.name as province_name FROM cities c JOIN provinces p ON c.province_id = p.id")
                data = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
                cur.close(); conn.close()
                if data:
                    log(f"Loaded {len(data)} cities via Direct DB.", "SUCCESS")
                    return data
            except Exception: pass

        cities = self.db.get("cities", "select=name,province_id")
        provinces = self.db.get("provinces", "select=id,name")
        if not cities or not provinces:
            log("REST fallback failed to load metadata.", "ERROR")
            return []

        p_map = {p['id']: p['name'] for p in provinces}
        for c in cities:
            c['province_name'] = p_map.get(c['province_id'], "Indonesia")

        log(f"Loaded {len(cities)} cities via REST API.", "SUCCESS")
        return cities

    def parse_followers(self, text):
        text = text.upper().replace(",", ".").strip()
        if text.endswith("K"): return int(float(text[:-1]) * 1000)
        if text.endswith("M"): return int(float(text[:-1]) * 1000000)
        if text.endswith("B"): return int(float(text[:-1]) * 1000000000)
        return int(re.sub(r"\D", "", text) or 0)

    def is_indonesian_bio(self, bio):
        """Check if bio is Indonesian using dynamic city/province names from database"""
        if not bio: return False
        bio_lower = bio.lower()

        # 1. HEURISTIC: Strict non-latin script rejection
        foreign_script = re.compile(r'[^\x00-\x7F\s\d.,!?;:()\'"%-]')
        if len(foreign_script.findall(bio)) > (len(bio) * 0.1): return False

        # 2. MATCH AGAINST DB DATA (Cities & Provinces)
        # We also check for common Indonesian olshop terms
        id_terms = {"wa", "order", "pesan", "murah", "reseller", "grosir", "cod", "tokopedia", "shopee", "ready", "ongkir", "jual"}

        # Check if bio mentions any city or province from our DB
        for city in self.cities:
            if city['name'].lower() in bio_lower: return True
            if city['province_name'].lower() in bio_lower: return True

        # Check for common ID commerce terms
        matches = 0
        for term in id_terms:
            if term in bio_lower:
                matches += 1
                if matches >= 2: return True

        return False

    async def extract_profile(self, context, username, category):
        if not username: return
        existing = self.db.get("sellers", f"username=eq.{username}&select=username")
        if existing: return

        page = await context.new_page()
        try:
            for retry in range(2):
                try:
                    await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(2000)

                    name_el = await page.query_selector('[data-e2e="user-title"]')
                    display_name = await name_el.inner_text() if name_el else username

                    bio_el = await page.query_selector('[data-e2e="user-bio"]')
                    bio = await bio_el.inner_text() if bio_el else ""

                    f_el = await page.query_selector('[data-e2e="followers-count"]')
                    followers = self.parse_followers(await f_el.inner_text()) if f_el else 0

                    if followers >= 100000: return
                    if not self.is_indonesian_bio(bio):
                        log(f"⏭️ Skipping @{username}: Not relevant", "WARNING")
                        return

                    city_name, prov_name = "", "Indonesia"
                    full = (display_name + " " + bio).lower()
                    for c in self.cities:
                        if c['name'].lower() in full: city_name, prov_name = c['name'], c['province_name']; break

                    data = {
                        "username": username, "display_name": display_name or username,
                        "bio": bio, "followers_count": followers, "category": category,
                        "province": prov_name, "city": city_name,
                        "tiktok_url": f"https://www.tiktok.com/@{username}",
                        "last_scraped": datetime.now().isoformat()
                    }
                    self.db.upsert('sellers', data)
                    log(f"✅ Saved @{username} | {city_name or 'ID'}", "SUCCESS")
                    return
                except Exception:
                    await asyncio.sleep(2)
                    continue
        except Exception: pass
        finally: await page.close()

    async def search_page(self, context, keyword):
        page = await context.new_page()
        users = set()
        try:
            await page.goto(f"https://www.tiktok.com/search/user?q={keyword.lower().replace(' ', '+')}", timeout=45000)
            await asyncio.sleep(4)
            for _ in range(30):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(0.5)

            anchors = await page.query_selector_all('a[href*="/@"]')
            for a in anchors:
                href = await a.get_attribute("href")
                match = re.search(r'@([\w._]+)', href or "")
                if match: users.add(match.group(1).lower())
            return list(users)
        except Exception: return []
        finally: await page.close()

    async def process_keyword(self, context, keyword, category):
        log(f"🔍 Searching: {keyword}")
        users = await self.search_page(context, keyword)
        if not users: return
        for username in users[:100]:
            await self.extract_profile(context, username, category)
            await asyncio.sleep(random.uniform(0.5, 1.5))

async def main_loop():
    db = SupabaseREST()
    engine = TiktokEngine(db)
    worker_idx = int(os.environ.get('WORKER_INDEX', 0))

    if not engine.cities:
        log("CRITICAL: Failed to load cities.", "ERROR")
        return

    priority_names = ["Jakarta Selatan", "Jakarta Timur", "Jakarta Pusat", "Yogyakarta"]
    priority_cities = [c for c in engine.cities if any(p.lower() in c['name'].lower() for p in priority_names)]
    other_cities = [c for c in engine.cities if c not in priority_cities]
    my_cities = priority_cities + (other_cities[:len(other_cities)//2] if worker_idx == 0 else other_cities[len(other_cities)//2:])

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        log(f"🚀 WORKER {worker_idx} START")

        while True:
            db.upsert('system_status', {'id': f'worker_{worker_idx}', 'last_seen': datetime.now().isoformat(), 'status': 'online'}, on_conflict='id')
            db.upsert('system_status', {'id': 'main_engine', 'last_seen': datetime.now().isoformat(), 'status': 'online'}, on_conflict='id')

            city = random.choice(my_cities)
            cat = random.choice(engine.categories)

            keyword = f"{cat} {city['name']}"
            pending = db.get("search_queries", f"status=eq.pending&query=like.*{city['name']}*&limit=1")
            if pending:
                keyword = pending[0]['query']
                db.upsert('search_queries', {'query': keyword, 'status': 'processing'}, on_conflict='query')
            else:
                keywords = db.ai_generate_keywords(city['name'], cat)
                for kw in keywords[:15]: db.upsert('search_queries', {'query': kw, 'status': 'pending'}, on_conflict='query')
                if keywords: keyword = keywords[0]

            await engine.process_keyword(context, keyword, cat)
            db.upsert('search_queries', {'query': keyword, 'status': 'completed'}, on_conflict='query')
            await asyncio.sleep(random.uniform(2, 5))

if __name__ == "__main__":
    asyncio.run(main_loop())
