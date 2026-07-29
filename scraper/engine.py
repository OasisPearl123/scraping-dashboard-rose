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

        if self.db_pass and self.url:
            project_ref = self.url.split('//')[1].split('.')[0]
            self.db_host = f"db.{project_ref}.supabase.co"
            self.use_db = True

        try:
            from supabase import create_client
            self.client = create_client(self.url, self.key)
            self.client.table('system_status').select('id').limit(1).execute()
            log("Supabase REST API Active", "SUCCESS")
        except Exception:
            self.client = None

    def query(self, table):
        return TableProxy(self, table)

    def ai_classify(self, username, bio, followers, category):
        if not self.groq_token: return True
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self.groq_token}", "Content-Type": "application/json"}
            # Using Llama 3.3 70B for high intelligence as requested
            prompt = f"Analyze TikTok profile: @{username}. Bio: {bio}. Followers: {followers}. Target Category: {category}. Is this an Indonesian SME/Seller selling products or services in the {category} category? Answer ONLY 'YES' or 'NO'."
            data = {
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1
            }
            resp = requests.post(url, headers=headers, json=data, timeout=12).json()
            answer = resp['choices'][0]['message']['content'].strip().upper()
            return "YES" in answer
        except Exception as e:
            log(f"AI Error: {e}", "WARNING")
            return True

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
        if self.engine.use_db:
            try:
                conn = psycopg2.connect(host=self.engine.db_host, database='postgres', user='postgres', password=self.engine.db_pass, port='5432')
                cur = conn.cursor()
                where = ""
                if self._filters:
                    where = " WHERE " + " AND ".join([f"{k} = %s" for k in self._filters.keys()])
                cur.execute(f"SELECT {self._columns} FROM {self.table}{where}", list(self._filters.values()))
                desc = cur.description
                data = [dict(zip([d[0] for d in desc], r)) for r in cur.fetchall()]
                cur.close(); conn.close()
                return Res(data)
            except Exception: pass
        return Res()

    def upsert(self, data, on_conflict='username'):
        if self.engine.client:
            try: return self.engine.client.table(self.table).upsert(data, on_conflict=on_conflict).execute()
            except Exception: pass
        if self.engine.use_db:
            try:
                conn = psycopg2.connect(host=self.engine.db_host, database='postgres', user='postgres', password=self.engine.db_pass, port='5432')
                conn.autocommit = True
                cur = conn.cursor()
                cols = list(data.keys())
                query = f"INSERT INTO {self.table} ({', '.join(cols)}) VALUES ({', '.join(['%s']*len(cols))}) ON CONFLICT ({on_conflict}) DO UPDATE SET {', '.join([f'{c}=EXCLUDED.{c}' for c in cols if c != on_conflict])} RETURNING id"
                cur.execute(query, [data[c] for c in cols])
                res = cur.fetchone()[0]
                cur.close(); conn.close()
                return res
            except Exception: pass
        return None

# 3. ENGINE LOGIC
class TiktokEngine:
    def __init__(self, db):
        self.db = db
        self.regions = self.load_regions()
        self.categories = ["Kuliner", "Fashion", "Beauty", "Skincare", "Gadget", "Elektronik", "Home Living", "Jasa"]

    def load_regions(self):
        provinces = self.db.query('provinces').select('*').execute().data
        cities = self.db.query('cities').select('*, provinces(name)').execute().data
        return {'provinces': provinces, 'cities': cities}

    async def extract_profile(self, context, username, category):
        existing = self.db.query('sellers').select('username').eq('username', username).execute()
        if existing.data: return False

        page = await context.new_page()
        try:
            url = f"https://www.tiktok.com/@{username}"
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_selector('[data-e2e="user-title"]', timeout=10000)

            name = await page.inner_text('[data-e2e="user-title"]')
            bio = await page.inner_text('[data-e2e="user-bio"]') if await page.query_selector('[data-e2e="user-bio"]') else ""

            f_raw = await page.inner_text('[data-e2e="followers-count"]')
            f = f_raw.upper()
            followers = int(float(f.replace('M',''))*1e6) if 'M' in f else int(float(f.replace('K',''))*1e3) if 'K' in f else int(''.join(filter(str.isdigit, f)) or 0)

            # RULE: MUST BE < 100K FOLLOWERS
            if followers > 100000:
                log(f"Skipping @{username}: {followers} followers (too big)", "WARNING")
                return False

            # AI VALIDATION
            if not self.db.ai_classify(username, bio, followers, category):
                log(f"Skipping @{username}: AI rejected as non-SME/wrong category", "WARNING")
                return False

            full = (name + " " + bio).lower()
            city, prov = "", ""
            for c in self.regions['cities']:
                if c['name'].lower() in full: city, prov = c['name'], c['provinces']['name']; break
            if not city:
                for p in self.regions['provinces']:
                    if p['name'].lower() in full: prov = p['name']; break

            data = {
                'platform': 'tiktok', 'username': username, 'display_name': name or username,
                'bio': bio, 'followers_count': followers, 'phone_number': (re.search(r'(?:\+62|62|08)[0-9]{9,12}', bio.replace(" ","").replace("-","")) or ["N/A"])[0],
                'category': category, 'province': prov or "Indonesia", 'city': city or "",
                'potential_score': int(min((followers/5000)+50, 100)),
                'potential_reason': f"AI Verified SME in {category}. Located in {city or prov or 'Indonesia'}.",
                'tiktok_url': url, 'last_scraped': datetime.now().isoformat()
            }
            self.db.query('sellers').upsert(data)
            log(f"Saved SME @{username} in {category}", "SUCCESS")
            return True
        except Exception: return False
        finally: await page.close()

async def main_loop():
    db = SupabaseEngine()
    engine = TiktokEngine(db)

    all_cities = engine.regions['cities']
    worker_index = int(os.environ.get('WORKER_INDEX', 0))
    total_workers = 15

    # Slice cities for this worker
    chunk_size = len(all_cities) // total_workers
    start = worker_index * chunk_size
    end = start + chunk_size if worker_index < total_workers - 1 else len(all_cities)
    my_cities = all_cities[start:end]

    is_gh = os.environ.get('GITHUB_ACTIONS') == 'true'
    start_time = datetime.now()
    limit = timedelta(hours=5) if is_gh else None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

        log(f"ENGINE START: Worker {worker_index+1}/15 | Cities: {len(my_cities)}")

        while True:
            if limit and (datetime.now() - start_time > limit): break

            city = random.choice(my_cities)
            cat = random.choice(engine.categories)
            search_q = f"{cat.lower()} {city['name'].lower()}"
            log(f"🔍 Searching: {search_q}")

            page = await context.new_page()
            try:
                await page.goto(f"https://www.tiktok.com/search/user?q={search_q}", timeout=60000)
                await asyncio.sleep(5)
                for _ in range(10):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(1)

                links = await page.query_selector_all('a[href*="/@"]')
                users = list(set([re.search(r'@([\w.]+)', await l.get_attribute('href')).group(1) for l in links if re.search(r'@([\w.]+)', await l.get_attribute('href'))]))
                await page.close()

                for u in users[:40]:
                    await engine.extract_profile(context, u, cat)
                    await asyncio.sleep(random.uniform(0.5, 1.5))
            except Exception:
                if not page.is_closed(): await page.close()

            await asyncio.sleep(2)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main_loop())
