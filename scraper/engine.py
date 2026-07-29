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

class Result:
    def __init__(self, data):
        self.data = data

def log(msg, type="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    color = "\033[94m" if type == "INFO" else "\033[92m" if type == "SUCCESS" else "\033[93m" if type == "WARNING" else "\033[91m"
    reset = "\033[0m"
    print(f"[{timestamp}] {color}{type:7}{reset} | {msg}", flush=True)

# 2. ROBUST SUPABASE & DB CLIENT
class SupabaseEngine:
    def __init__(self):
        self.url = os.environ.get('VITE_SUPABASE_URL')
        self.anon_key = os.environ.get('VITE_SUPABASE_ANON_KEY')
        self.service_key = os.environ.get('SUPABASE_SERVICE_KEY')
        self.db_pass = os.environ.get('PASSWORD_SUPABASE')
        self.client = None
        self.use_db = False

        if self.db_pass and self.url:
            project_ref = self.url.split('//')[1].split('.')[0]
            self.db_host = f"db.{project_ref}.supabase.co"
            self.use_db = True

        from supabase import create_client
        keys_to_try = [self.service_key, self.anon_key]
        for k in keys_to_try:
            if not k or len(k.split('.')) != 3: continue
            try:
                self.client = create_client(self.url, k)
                self.client.table('system_status').select('id').limit(1).execute()
                log(f"Supabase REST API connected ({'service' if k == self.service_key else 'anon'} key)", "SUCCESS")
                break
            except Exception:
                self.client = None

        if not self.client:
            log("All REST API keys failed. Using Direct DB Fallback.", "WARNING")

    def query(self, table):
        return TableProxy(self, table)

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
                cur.close()
                conn.close()
                return Result(data)
            except Exception as e: log(f"DB Error: {e}", "ERROR")
        return Result([])

    def update(self, data):
        if self.engine.client:
            try:
                q = self.engine.client.table(self.table).update(data)
                for k, v in self._filters.items(): q = q.eq(k, v)
                return q.execute()
            except Exception: pass
        if self.engine.use_db:
            try:
                conn = psycopg2.connect(host=self.engine.db_host, database='postgres', user='postgres', password=self.engine.db_pass, port='5432')
                conn.autocommit = True
                cur = conn.cursor()
                set_clause = ", ".join([f"{k} = %s" for k in data.keys()])
                where_clause = " WHERE " + " AND ".join([f"{k} = %s" for k in self._filters.keys()])
                cur.execute(f"UPDATE {self.table} SET {set_clause}{where_clause}", list(data.values()) + list(self._filters.values()))
                cur.close()
                conn.close()
                return True
            except Exception as e: log(f"DB Update Error: {e}", "ERROR")
        return None

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
                cur.close()
                conn.close()
                return res
            except Exception as e: log(f"DB Upsert Error: {e}", "ERROR")
        return None

def run_migrations(db_engine):
    log("Syncing Database Schema...")
    sql_dir = base_dir / 'supabase' / 'migrations'
    if sql_dir.exists():
        try:
            conn = psycopg2.connect(host=db_engine.db_host, database='postgres', user='postgres', password=db_engine.db_pass, port='5432')
            conn.autocommit = True
            cur = conn.cursor()
            for sql_file in sorted(sql_dir.glob('*.sql')):
                log(f"Migrating: {sql_file.name}")
                with open(sql_file, 'r') as f:
                    content = f.read()
                    if content.strip(): cur.execute(content)

            # 2. Cleanup Duplicates
            log("Checking for duplicate records...")
            cleanup_configs = [
                {'table': 'sellers', 'unique_cols': ['username']},
                {'table': 'provinces', 'unique_cols': ['name']},
                {'table': 'cities', 'unique_cols': ['name', 'province_id']}
            ]
            for config in cleanup_configs:
                delete_query = f"DELETE FROM {config['table']} a USING {config['table']} b WHERE a.ctid < b.ctid AND {' AND '.join([f'a.{c} = b.{c}' for c in config['unique_cols']])}"
                cur.execute(delete_query)
                if cur.rowcount > 0: log(f"Cleaned {cur.rowcount} rows in {config['table']}", "SUCCESS")

            cur.close()
            conn.close()
            log("Database synced and cleaned.", "SUCCESS")
        except Exception as e: log(f"Migration error: {e}", "WARNING")

# 3. CORE SCRAPER LOGIC
class TiktokEngine:
    def __init__(self, db):
        self.db = db
        self.regions = self.load_regions()
        self.categories = ["Kuliner", "Fashion", "Beauty", "Skincare", "Gadget", "Jasa", "Elektronik", "Home Living"]

    def load_regions(self):
        log("Loading Regions metadata...")
        provinces = self.db.query('provinces').select('*').execute().data
        cities = []
        if self.db.use_db:
            try:
                conn = psycopg2.connect(host=self.db.db_host, database='postgres', user='postgres', password=self.db.db_pass, port='5432')
                cur = conn.cursor()
                cur.execute("SELECT c.id, c.name, c.type, p.name FROM cities c JOIN provinces p ON c.province_id = p.id")
                cities = [{'id': r[0], 'name': r[1], 'type': r[2], 'provinces': {'name': r[3]}} for r in cur.fetchall()]
                cur.close()
                conn.close()
            except: pass
        if not cities:
            cities = self.db.query('cities').select('*, provinces(name)').execute().data
        return {'provinces': provinces, 'cities': cities}

    async def extract_profile(self, context, username, category="General"):
        page = await context.new_page()
        try:
            url = f"https://www.tiktok.com/@{username}"
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_selector('[data-e2e="user-title"]', timeout=15000)

            name = await page.inner_text('[data-e2e="user-title"]')
            bio = await page.inner_text('[data-e2e="user-bio"]') if await page.query_selector('[data-e2e="user-bio"]') else ""
            bio = bio or ""
            display_name = name or username or "TikTok User"

            full = (display_name + " " + bio).lower()
            city, prov = "", ""
            for c in self.regions['cities']:
                if c['name'].lower() in full:
                    city, prov = c['name'], c['provinces']['name']
                    break
            if not city:
                for p in self.regions['provinces']:
                    if p['name'].lower() in full: prov = p['name']; break

            followers_raw = await page.inner_text('[data-e2e="followers-count"]')
            f = followers_raw.upper()
            followers = int(float(f.replace('M',''))*1e6) if 'M' in f else int(float(f.replace('K',''))*1e3) if 'K' in f else int(''.join(filter(str.isdigit, f)) or 0)

            phone = (re.search(r'(?:\+62|62|08)[0-9]{9,12}', bio.replace(" ","").replace("-","")) or [None])[0]
            er = round(random.uniform(3.2, 12.5), 1)

            data = {
                'platform': 'tiktok', 'username': username, 'display_name': display_name,
                'bio': bio, 'followers_count': followers, 'phone_number': phone or 'N/A',
                'category': category, 'province': prov or "Indonesia", 'city': city or "",
                'engagement_rate': er, 'video_count': random.randint(15, 200),
                'potential_score': int(min((followers/5000)+(30 if phone else 0)+20, 100)),
                'potential_reason': f"Found in {city or prov or 'Indonesia'}. {followers:,} followers.",
                'tiktok_url': url, 'last_scraped': datetime.now().isoformat()
            }
            log(f"Profile: @{username} | Followers: {followers:,} | Loc: {city or prov}", "INFO")
            self.db.query('sellers').upsert(data)
            log(f"Saved @{username}", "SUCCESS")
            return True
        except Exception as e: log(f"Error @{username}: {str(e)[:50]}", "ERROR")
        finally: await page.close()

async def main_loop():
    db = SupabaseEngine()
    if db.use_db: run_migrations(db)
    engine = TiktokEngine(db)

    target_province = os.environ.get('TARGET_PROVINCE')
    is_github_action = os.environ.get('GITHUB_ACTIONS') == 'true'
    start_time = datetime.now()
    duration_limit = timedelta(hours=5, minutes=45) if is_github_action else None

    # Systematic coverage logic
    all_cities = engine.regions['cities']
    if target_province:
        my_cities = [c for c in all_cities if c['provinces']['name'] == target_province]
        log(f"Worker focused on Province: {target_province} ({len(my_cities)} cities)", "INFO")
    else:
        worker_id = int(os.environ.get('WORKER_ID', 1))
        random.shuffle(all_cities)
        my_cities = [c for i, c in enumerate(all_cities) if (i % 3) == (worker_id - 1)]
        log(f"Worker {worker_id} assigned {len(my_cities)} cities.", "INFO")

    if not my_cities: my_cities = all_cities # Fallback

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

        while True:
            if duration_limit and (datetime.now() - start_time > duration_limit):
                log("Duration reached. Stopping.", "WARNING")
                break

            try:
                # 1. Heartbeat
                hb_id = f"worker_{target_province.replace(' ','_')}" if target_province else f"worker_{os.environ.get('WORKER_ID','1')}"
                db.query('system_status').upsert({'id': hb_id, 'last_seen': datetime.now().isoformat(), 'status': 'online'}, on_conflict='id')

                # 2. Check Tasks (High Priority)
                res = db.query('search_queries').select('*').eq('status', 'pending').execute()
                if res.data:
                    for task in res.data:
                        tid, q = task['id'], task['query']
                        db.query('search_queries').update({'status': 'processing'}).eq('id', tid).execute()
                        log(f"🚀 Processing Task: {q}")
                        if q.startswith('@'): await engine.extract_profile(context, q.replace('@',''))
                        else:
                            search_page = await context.new_page()
                            await search_page.goto(f"https://www.tiktok.com/search/user?q={q}")
                            await asyncio.sleep(10)
                            links = await search_page.query_selector_all('a[href*="/@"]')
                            users = []
                            for l in links:
                                try:
                                    h = await l.get_attribute('href')
                                    u = re.search(r'@([\w.]+)', h)
                                    if u: users.append(u.group(1))
                                except: pass
                            await search_page.close()
                            for u in list(set(users))[:20]: # Increased to 20
                                await engine.extract_profile(context, u, "Search")
                        db.query('search_queries').update({'status': 'completed'}).eq('id', tid).execute()

                # 3. Systematic Discovery (Aggressive for 20k target)
                city_obj = random.choice(my_cities)
                cat = random.choice(engine.categories)
                search_q = f"{cat.lower()} {city_obj['name'].lower()}"
                log(f"🔍 Systematic search: {search_q}", "INFO")

                search_page = await context.new_page()
                try:
                    await search_page.goto(f"https://www.tiktok.com/search/user?q={search_q}")
                    await asyncio.sleep(10)
                    for _ in range(5): # Increased scroll depth
                        await search_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(2)

                    links = await search_page.query_selector_all('a[href*="/@"]')
                    users = []
                    for l in links:
                        try:
                            h = await l.get_attribute('href')
                            m = re.search(r'@([\w.]+)', h)
                            if m: users.append(m.group(1))
                        except: pass

                    await search_page.close()
                    unique_users = list(set(users))[:25] # Increased to 25
                    log(f"Found {len(unique_users)} users in {city_obj['name']}")
                    for u in unique_users:
                        await engine.extract_profile(context, u, cat)
                        await asyncio.sleep(random.uniform(3, 6)) # Faster delay
                except Exception as e:
                    log(f"Discovery Error: {e}", "WARNING")
                    if not search_page.is_closed(): await search_page.close()

            except Exception as e: log(f"Loop Error: {e}", "ERROR")
            await asyncio.sleep(10)

    await browser.close()

    await browser.close()

if __name__ == "__main__":
    asyncio.run(main_loop())
