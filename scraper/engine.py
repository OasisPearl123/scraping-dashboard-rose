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
    def __init__(self, data=None):
        self.data = data or []

# 2. SUPABASE & AI CLIENT
class SupabaseEngine:
    def __init__(self):
        self.url = os.environ.get('VITE_SUPABASE_URL')
        self.key = os.environ.get('SUPABASE_SERVICE_KEY') or os.environ.get('VITE_SUPABASE_ANON_KEY')
        self.db_pass = os.environ.get('PASSWORD_SUPABASE')
        self.groq_token = os.environ.get('token_groq')
        self.client = None
        self.use_db = False
        self.db_host = ""

        if self.url:
            project_ref = self.url.split('//')[1].split('.')[0]
            self.db_host = f"db.{project_ref}.supabase.co"
            if self.db_pass: self.use_db = True

        try:
            from supabase import create_client
            self.client = create_client(self.url, self.key)
            log("Supabase Connection Initialized", "SUCCESS")
        except Exception: self.client = None

    def query(self, table):
        return TableProxy(self, table)

    def ai_generate_massive_keywords(self, city, category):
        if not self.groq_token: return []
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self.groq_token}", "Content-Type": "application/json"}
            prompt = f"""Generate 40 unique and highly specific TikTok search keywords for finding local sellers in {city} for the {category} category.
            Include:
            - Product names in Indonesian
            - 'Jual [product] {city}'
            - '[product] murah {city}'
            - Local market/street names in {city}
            - Slang terms used by olshops.
            Format: JSON array of strings only."""

            data = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "temperature": 0.9}
            resp = requests.post(url, headers=headers, json=data, timeout=25).json()
            content = resp['choices'][0]['message']['content']
            match = re.search(r'\[.*\]', content, re.DOTALL)
            return json.loads(match.group(0)) if match else []
        except Exception as e:
            log(f"AI Massive Keyword Error: {e}", "WARNING")
            return []

    def ai_classify(self, username, bio, followers, category):
        clean_bio = (bio or "").strip()
        if clean_bio:
            foreign_script = re.compile(r'[^\x00-\x7F\s\d.,!?;:()\'"%-]')
            if len(foreign_script.findall(clean_bio)) > (len(clean_bio) * 0.05): return False

        if not self.groq_token: return True
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self.groq_token}", "Content-Type": "application/json"}
            prompt = f"Evaluate @{username} | Bio: {bio} | Category: {category}. Is this an Indonesian local SME/Seller? Answer YES or NO."
            data = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "temperature": 0.0}
            resp = requests.post(url, headers=headers, json=data, timeout=12).json()
            return "YES" in resp['choices'][0]['message']['content'].strip().upper()
        except Exception: return True

class TableProxy:
    def __init__(self, engine, table):
        self.engine = engine
        self.table = table
        self._filters = {}
        self._columns = "*"

    def select(self, cols="*"):
        self._columns = cols
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def execute(self):
        if self.engine.client:
            try:
                q = self.engine.client.table(self.table).select(self._columns)
                for k, v in self._filters.items(): q = q.eq(k, v)
                return q.execute()
            except Exception: pass
        return Res()

    def upsert(self, data, on_conflict='username'):
        if self.engine.client:
            try: return self.engine.client.table(self.table).upsert(data, on_conflict=on_conflict).execute()
            except Exception: pass
        return None

# 3. ENGINE LOGIC
class TiktokEngine:
    def __init__(self, db):
        self.db = db
        self.regions = self.load_regions()
        self.categories = ["Kuliner", "Fashion", "Beauty", "Skincare", "Gadget", "Elektronik", "Home Living", "Jasa"]

    def load_regions(self):
        try:
            if self.db.use_db:
                conn = psycopg2.connect(host=self.db.db_host, database='postgres', user='postgres', password=self.db.db_pass, port='5432')
                cur = conn.cursor()
                cur.execute("SELECT c.*, p.name as province_name FROM cities c JOIN provinces p ON c.province_id = p.id")
                cities = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
                cur.close(); conn.close()
                return cities
        except Exception: pass
        c = self.db.query('cities').select('*, provinces(name)').execute().data
        for item in c: item['province_name'] = item['provinces']['name']
        return c

    async def extract_profile(self, context, username, category, depth=0):
        if depth > 1: return [] # Limit recursive discovery

        existing = self.db.query('sellers').select('username').eq('username', username).execute()
        if existing.data: return []

        page = await context.new_page()
        suggested_users = []
        try:
            url = f"https://www.tiktok.com/@{username}"
            await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_selector('[data-e2e="user-title"]', timeout=8000)

            name = await page.inner_text('[data-e2e="user-title"]')
            bio = await page.inner_text('[data-e2e="user-bio"]') if await page.query_selector('[data-e2e="user-bio"]') else ""
            f_raw = await page.inner_text('[data-e2e="followers-count"]')
            f = f_raw.upper()
            followers = int(float(f.replace('M',''))*1e6) if 'M' in f else int(float(f.replace('K',''))*1e3) if 'K' in f else int(''.join(filter(str.isdigit, f)) or 0)

            if followers < 100000 and self.db.ai_classify(username, bio, followers, category):
                full = (name + " " + bio).lower()
                city, prov = "", ""
                for c in self.regions:
                    if c['name'].lower() in full: city, prov = c['name'], c['province_name']; break

                data = {
                    'platform': 'tiktok', 'username': username, 'display_name': name or username,
                    'bio': bio, 'followers_count': followers, 'phone_number': (re.search(r'(?:\+62|62|08)[0-9]{9,12}', bio.replace(" ","").replace("-","")) or ["N/A"])[0],
                    'category': category, 'province': prov or "Indonesia", 'city': city or "",
                    'potential_score': int(min((followers/5000)+50, 100)),
                    'potential_reason': f"AI Verified SME. Recursive depth {depth}.",
                    'tiktok_url': url, 'last_scraped': datetime.now().isoformat()
                }
                self.db.query('sellers').upsert(data)
                log(f"Saved SME @{username} in {city or 'ID'}", "SUCCESS")

                # DISCOVERY: Look for suggested accounts (the dropdown icon on mobile/web)
                # On web, similar accounts are sometimes visible or can be triggered.
                # We'll grab any other profile links on the page as potential candidates.
                links = await page.query_selector_all('a[href*="/@"]')
                for l in links:
                    href = await l.get_attribute('href')
                    match = re.search(r'@([\w.]+)', href)
                    if match and match.group(1) != username:
                        suggested_users.append(match.group(1))

            return list(set(suggested_users))
        except Exception: return []
        finally: await page.close()

async def main_loop():
    db = SupabaseEngine()
    engine = TiktokEngine(db)
    all_cities = engine.regions
    worker_id = int(os.environ.get('WORKER_INDEX', 0))
    total_workers = 15

    # Priority Regions Focus
    priority_names = ["Jakarta Selatan", "Jakarta Timur", "Jakarta Pusat", "Yogyakarta"]
    priority_cities = [c for c in all_cities if any(p.lower() in c['name'].lower() for p in priority_names)]
    other_cities = [c for c in all_cities if c not in priority_cities]

    if worker_id < 10: # 10 Workers on Priority
        chunk = len(priority_cities) // 10
        my_cities = priority_cities[worker_id*chunk : (worker_id+1)*chunk if worker_id < 9 else len(priority_cities)]
    else: # 5 Workers on Others
        rel_id = worker_id - 10
        chunk = len(other_cities) // 5
        my_cities = other_cities[rel_id*chunk : (rel_id+1)*chunk if rel_id < 4 else len(other_cities)]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        log(f"WORKER {worker_id+1} ONLINE | Targets: {len(my_cities)} cities")

        while True:
            # Sync Status
            try:
                db.query('system_status').upsert({'id': f'worker_{worker_id+1}', 'last_seen': datetime.now().isoformat(), 'status': 'online'}, on_conflict='id')
                db.query('system_status').upsert({'id': 'main_engine', 'last_seen': datetime.now().isoformat(), 'status': 'online'}, on_conflict='id')
            except Exception: pass

            city = random.choice(my_cities)
            cat = random.choice(engine.categories)

            # Massive Keyword Generation
            try:
                conn = psycopg2.connect(host=db.db_host, database='postgres', user='postgres', password=db.db_pass, port='5432')
                cur = conn.cursor()
                cur.execute("SELECT query FROM search_queries WHERE status='pending' AND query LIKE %s LIMIT 1 FOR UPDATE SKIP LOCKED", (f"%{city['name']}%",))
                row = cur.fetchone()
                if row:
                    q = row[0]
                    cur.execute("UPDATE search_queries SET status='processing' WHERE query=%s", (q,))
                else:
                    new_qs = db.ai_generate_massive_keywords(city['name'], cat)
                    for nq in new_qs: cur.execute("INSERT INTO search_queries (query, status) VALUES (%s, 'pending') ON CONFLICT DO NOTHING", (nq,))
                    q = f"{cat} {city['name']}"
                conn.commit(); cur.close(); conn.close()
            except Exception: q = f"{cat} {city['name']}"

            log(f"🔍 Deep Search: {q}")
            page = await context.new_page()
            try:
                await page.goto(f"https://www.tiktok.com/search/user?q={q}", timeout=60000)
                await asyncio.sleep(5)
                for _ in range(40): # Ultra Deep Scroll
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(0.5)

                links = await page.query_selector_all('a[href*="/@"]')
                users = list(set([re.search(r'@([\w.]+)', await l.get_attribute('href')).group(1) for l in links if re.search(r'@([\w.]+)', await l.get_attribute('href'))]))
                await page.close()

                for u in users[:100]:
                    # RECURSIVE DISCOVERY
                    suggested = await engine.extract_profile(context, u, cat, depth=0)
                    for su in suggested[:5]: # Explore 5 similar accounts per discovery
                        await engine.extract_profile(context, su, cat, depth=1)
                        await asyncio.sleep(0.2)

                # Mark Done
                try:
                    conn = psycopg2.connect(host=db.db_host, database='postgres', user='postgres', password=db.db_pass, port='5432')
                    cur = conn.cursor(); cur.execute("UPDATE search_queries SET status='completed' WHERE query=%s", (q,)); conn.commit(); cur.close(); conn.close()
                except Exception: pass
            except Exception:
                if not page.is_closed(): await page.close()

            await asyncio.sleep(1)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main_loop())
