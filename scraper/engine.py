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

# 2. SUPABASE ENGINE
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
        except Exception: self.client = None

    def query(self, table): return TableProxy(self, table)

    def ai_generate_massive_keywords(self, city, category):
        if not self.groq_token: return []
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self.groq_token}", "Content-Type": "application/json"}
            prompt = f"Generate 50 highly specific TikTok keywords for local sellers in {city} for {category}. Mixed slang/formal. JSON array only."
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

class TableProxy:
    def __init__(self, engine, table):
        self.engine = engine
        self.table = table
        self._filters = {}

    def select(self, cols="*"):
        self._columns = cols
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def execute(self):
        if self.engine.client:
            try:
                q = self.engine.client.table(self.table).select("*")
                for k, v in self._filters.items(): q = q.eq(k, v)
                return q.execute()
            except Exception: pass
        return Res()

    def upsert(self, data, on_conflict='username'):
        if self.engine.client:
            try: return self.engine.client.table(self.table).upsert(data, on_conflict=on_conflict).execute()
            except Exception: pass
        return None

# 3. ENGINE
class TiktokEngine:
    def __init__(self, db):
        self.db = db
        self.categories = ["Kuliner", "Fashion", "Beauty", "Skincare", "Gadget", "Elektronik", "Home Living", "Jasa"]
        self.cities = self.load_cities()

    def load_cities(self):
        try:
            if self.db.use_db:
                conn = psycopg2.connect(host=self.db.db_host, database='postgres', user='postgres', password=self.db.db_pass, port='5432')
                cur = conn.cursor()
                cur.execute("SELECT c.name, p.name as province_name FROM cities c JOIN provinces p ON c.province_id = p.id")
                data = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
                cur.close(); conn.close()
                if data: return data
        except Exception: pass
        # Fallback to REST
        try:
            c = self.db.query('cities').select('*').execute().data
            if not c: return []
            # Provinces mapping
            p_data = self.db.query('provinces').select('*').execute().data
            p_map = {item['id']: item['name'] for item in p_data}
            for item in c: item['province_name'] = p_map.get(item['province_id'], "Indonesia")
            return c
        except Exception: return []

    async def extract_profile(self, context, username, category, depth=0):
        if depth > 1: return []
        existing = self.db.query('sellers').select('username').eq('username', username).execute()
        if existing.data: return []

        page = await context.new_page()
        try:
            await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_selector('[data-e2e="user-title"]', timeout=5000)

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
                self.db.query('sellers').upsert(data)
                log(f"Saved @{username} | Follower: {followers}", "SUCCESS")

                links = await page.query_selector_all('a[href*="/@"]')
                return [re.search(r'@([\w.]+)', await l.get_attribute('href')).group(1) for l in links if re.search(r'@([\w.]+)', await l.get_attribute('href'))]
            return []
        except Exception: return []
        finally: await page.close()

async def main_loop():
    db = SupabaseEngine()
    engine = TiktokEngine(db)
    worker_idx = int(os.environ.get('WORKER_INDEX', 0))

    all_cities = engine.cities
    if not all_cities:
        log("CRITICAL: No cities loaded. Engine cannot start.", "ERROR")
        return

    # Focus logic
    priority_names = ["Jakarta Selatan", "Jakarta Timur", "Jakarta Pusat", "Yogyakarta"]
    priority_cities = [c for c in all_cities if any(p.lower() in c['name'].lower() for p in priority_names)]
    other_cities = [c for c in all_cities if c not in priority_cities]

    # Dynamic partitioning based on total workers (usually 2 now)
    # We'll just use a simple split if there are only 2 workers
    if worker_idx == 0:
        # Worker A: All priority cities + half of others
        my_cities = priority_cities + other_cities[:len(other_cities)//2]
    else:
        # Worker B: All priority cities + other half of others
        # Both workers help with priority cities to ensure 2000+ data per city
        my_cities = priority_cities + other_cities[len(other_cities)//2:]

    if not my_cities: my_cities = all_cities # Safety

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        log(f"WORKER {worker_idx} ACTIVE | Assigned {len(my_cities)} cities")

        while True:
            # Sync Status
            try:
                db.query('system_status').upsert({'id': f'worker_{worker_idx}', 'last_seen': datetime.now().isoformat(), 'status': 'online'}, on_conflict='id')
                db.query('system_status').upsert({'id': 'main_engine', 'last_seen': datetime.now().isoformat(), 'status': 'online'}, on_conflict='id')
            except Exception: pass

            city = random.choice(my_cities)
            cat = random.choice(engine.categories)

            q = f"{cat} {city['name']}"
            try:
                if db.use_db:
                    conn = psycopg2.connect(host=db.db_host, database='postgres', user='postgres', password=db.db_pass, port='5432')
                    cur = conn.cursor()
                    cur.execute("SELECT query FROM search_queries WHERE status='pending' LIMIT 1 FOR UPDATE SKIP LOCKED")
                    row = cur.fetchone()
                    if row: q = row[0]; cur.execute("UPDATE search_queries SET status='processing' WHERE query=%s", (q,))
                    else:
                        for aq in db.ai_generate_massive_keywords(city['name'], cat):
                            cur.execute("INSERT INTO search_queries (query, status) VALUES (%s, 'pending') ON CONFLICT DO NOTHING", (aq,))
                    conn.commit(); cur.close(); conn.close()
            except Exception: pass

            log(f"🚀 Scanning: {q}")
            page = await context.new_page()
            try:
                await page.goto(f"https://www.tiktok.com/search/user?q={q}", timeout=60000)
                await asyncio.sleep(4)
                for _ in range(40): await page.evaluate("window.scrollTo(0, document.body.scrollHeight)"); await asyncio.sleep(0.3)

                links = await page.query_selector_all('a[href*="/@"]')
                users = list(set([re.search(r'@([\w.]+)', await l.get_attribute('href')).group(1) for l in links if re.search(r'@([\w.]+)', await l.get_attribute('href'))]))
                await page.close()

                for u in users[:60]:
                    discovered = await engine.extract_profile(context, u, cat, depth=0)
                    for du in discovered[:4]:
                        await engine.extract_profile(context, du, cat, depth=1)
            except Exception:
                if not page.is_closed(): await page.close()
            await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main_loop())
