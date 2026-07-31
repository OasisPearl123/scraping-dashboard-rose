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
        # Use VITE_SUPABASE_ANON_KEY as primary because it's verified working (200 OK)
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
            headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
            r = requests.post(f"{self.url}/rest/v1/{table}", headers=headers, json=data, timeout=15)
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

    def ai_classify(self, username, bio, followers, category):
        clean_bio = (bio or "").strip()
        if clean_bio:
            foreign_script = re.compile(r'[^\x00-\x7F\s\d.,!?;:()\'"%-]')
            if len(foreign_script.findall(clean_bio)) > (len(clean_bio) * 0.05): return False
        if not self.groq_token: return True
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self.groq_token}", "Content-Type": "application/json"}
            prompt = f"Is @{username} | Bio: {bio} | Cat: {category} an Indonesian SME? YES/NO."
            data = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "temperature": 0.0}
            resp = requests.post(url, headers=headers, json=data, timeout=12).json()
            return "YES" in resp['choices'][0]['message']['content'].strip().upper()
        except Exception: return True

class TiktokEngine:
    def __init__(self, db):
        self.db = db
        self.categories = ["Kuliner", "Fashion", "Beauty", "Skincare", "Gadget", "Elektronik", "Home Living", "Jasa"]
        self.cities = self.load_cities()

    def load_cities(self):
        log("Fetching cities and provinces...")
        # Direct DB attempt
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

        # REST fallback
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

    async def extract_profile(self, context, username, category, depth=0):
        if depth > 1: return []
        existing = self.db.get("sellers", f"username=eq.{username}&select=username")
        if existing: return []

        page = await context.new_page()
        try:
            await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_selector('[data-e2e="user-title"]', timeout=6000)

            name = await page.inner_text('[data-e2e="user-title"]')
            bio = await page.inner_text('[data-e2e="user-bio"]') if await page.query_selector('[data-e2e="user-bio"]') else ""
            f_raw = await page.inner_text('[data-e2e="followers-count"]')
            f = f_raw.upper()
            followers = int(float(f.replace('M',''))*1e6) if 'M' in f else int(float(f.replace('K',''))*1e3) if 'K' in f else int(''.join(filter(str.isdigit, f)) or 0)

            if followers < 100000 and self.db.ai_classify(username, bio, followers, category):
                city_name, prov_name = "", "Indonesia"
                full = (name + " " + bio).lower()
                for c in self.cities:
                    if c['name'].lower() in full: city_name, prov_name = c['name'], c['province_name']; break

                data = {
                    'platform': 'tiktok', 'username': username, 'display_name': name or username,
                    'bio': bio, 'followers_count': followers, 'phone_number': (re.search(r'(?:\+62|62|08)[0-9]{9,12}', bio.replace(" ","").replace("-","")) or ["N/A"])[0],
                    'category': category, 'province': prov_name, 'city': city_name,
                    'potential_score': int(min((followers/5000)+50, 100)),
                    'tiktok_url': f"https://www.tiktok.com/@{username}", 'last_scraped': datetime.now().isoformat()
                }
                self.db.upsert('sellers', data)
                log(f"Saved @{username} | Followers: {followers}", "SUCCESS")

                links = await page.query_selector_all('a[href*="/@"]')
                return [re.search(r'@([\w.]+)', await l.get_attribute('href')).group(1) for l in links if re.search(r'@([\w.]+)', await l.get_attribute('href'))]
            return []
        except Exception: return []
        finally: await page.close()

async def main_loop():
    db = SupabaseREST()
    engine = TiktokEngine(db)
    worker_idx = int(os.environ.get('WORKER_INDEX', 0))

    if not engine.cities:
        log("CRITICAL: Failed to load cities. Check environment and database.", "ERROR")
        return

    priority_names = ["Jakarta Selatan", "Jakarta Timur", "Jakarta Pusat", "Yogyakarta"]
    priority_cities = [c for c in engine.cities if any(p.lower() in c['name'].lower() for p in priority_names)]
    other_cities = [c for c in engine.cities if c not in priority_cities]
    my_cities = priority_cities + (other_cities[:len(other_cities)//2] if worker_idx == 0 else other_cities[len(other_cities)//2:])

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        log(f"WORKER {worker_idx} ONLINE | Cities: {len(my_cities)}")

        while True:
            db.upsert('system_status', {'id': f'worker_{worker_idx}', 'last_seen': datetime.now().isoformat(), 'status': 'online'}, on_conflict='id')
            db.upsert('system_status', {'id': 'main_engine', 'last_seen': datetime.now().isoformat(), 'status': 'online'}, on_conflict='id')

            city = random.choice(my_cities)
            cat = random.choice(engine.categories)

            q = f"{cat} {city['name']}"
            pending = db.get("search_queries", f"status=eq.pending&query=like.*{city['name']}*&limit=1")
            if pending:
                q = pending[0]['query']
                db.upsert('search_queries', {'query': q, 'status': 'processing'}, on_conflict='query')
            else:
                ai_qs = db.ai_generate_keywords(city['name'], cat)
                for nq in ai_qs: db.upsert('search_queries', {'query': nq, 'status': 'pending'}, on_conflict='query')

            log(f"🚀 Scanning: {q}")
            page = await context.new_page()
            try:
                await page.goto(f"https://www.tiktok.com/search/user?q={q}", timeout=60000)
                await asyncio.sleep(5)
                for _ in range(40): await page.evaluate("window.scrollTo(0, document.body.scrollHeight)"); await asyncio.sleep(0.3)

                links = await page.query_selector_all('a[href*="/@"]')
                users = list(set([re.search(r'@([\w.]+)', await l.get_attribute('href')).group(1) for l in links if re.search(r'@([\w.]+)', await l.get_attribute('href'))]))
                await page.close()

                for u in users[:60]:
                    discovered = await engine.extract_profile(context, u, cat, depth=0)
                    for du in discovered[:5]:
                        await engine.extract_profile(context, du, cat, depth=1)
                        await asyncio.sleep(0.1)

                db.upsert('search_queries', {'query': q, 'status': 'completed'}, on_conflict='query')
            except Exception:
                if not page.is_closed(): await page.close()
            await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main_loop())
