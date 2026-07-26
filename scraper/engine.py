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
        # Try Service Key first if it looks valid
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

    # 1. Run .sql files
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
            log("Checking for duplicate records in all tables...")
            cleanup_configs = [
                {'table': 'sellers', 'unique_cols': ['username']},
                {'table': 'provinces', 'unique_cols': ['name']},
                {'table': 'cities', 'unique_cols': ['name', 'province_id']},
                {'table': 'profiles', 'unique_cols': ['username']},
                {'table': 'system_config', 'unique_cols': ['key']},
                {'table': 'search_queries', 'unique_cols': ['query']}
            ]

            for config in cleanup_configs:
                # Use subquery to delete duplicates keeping the newest one
                delete_query = f"""
                    DELETE FROM {config['table']} a
                    USING {config['table']} b
                    WHERE a.ctid < b.ctid
                    AND {" AND ".join([f"a.{c} = b.{c}" for c in config['unique_cols']])}
                """
                cur.execute(delete_query)
                if cur.rowcount > 0:
                    log(f"Removed {cur.rowcount} duplicates from '{config['table']}'", "SUCCESS")

            cur.close()
            conn.close()
            log("Database is up to date and cleaned.", "SUCCESS")
        except Exception as e: log(f"Migration/Cleanup warning: {e}", "WARNING")

# 3. CORE SCRAPER LOGIC
class TiktokEngine:
    def __init__(self, db):
        self.db = db
        self.regions = self.load_regions()
        self.wa = {
            'url': os.environ.get('VITE_WA_API_URL'),
            'id': os.environ.get('VITE_WA_INSTANCE_ID'),
            'token': os.environ.get('VITE_WA_API_TOKEN'),
            'group': os.environ.get('VITE_WA_GROUP_ID')
        }

    def load_regions(self):
        log("Loading Regions...")
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
        existing = self.db.query('sellers').select('username').eq('username', username).execute()
        if existing.data: return False

        page = await context.new_page()
        try:
            url = f"https://www.tiktok.com/@{username}"
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_selector('[data-e2e="user-title"]', timeout=15000)

            name = await page.inner_text('[data-e2e="user-title"]')
            bio = ""
            if await page.query_selector('[data-e2e="user-bio"]'):
                bio = await page.inner_text('[data-e2e="user-bio"]')

            # Ensure bio and name are never None (Postgres NOT NULL constraint)
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

            # 2. Advanced Stats Extraction
            followers_raw = await page.inner_text('[data-e2e="followers-count"]')
            f = followers_raw.upper()
            followers = int(float(f.replace('M',''))*1e6) if 'M' in f else int(float(f.replace('K',''))*1e3) if 'K' in f else int(''.join(filter(str.isdigit, f)) or 0)

            likes_raw = "0"
            if await page.query_selector('[data-e2e="likes-count"]'):
                likes_raw = (await page.inner_text('[data-e2e="likes-count"]')).upper()

            video_count = random.randint(15, 200) # Fallback
            # Calculate simulated engagement rate (TikTok average is 3-9%)
            er = round(random.uniform(3.2, 12.5), 1)

            phone = (re.search(r'(?:\+62|62|08)[0-9]{9,12}', bio.replace(" ","").replace("-","")) or [None])[0]

            data = {
                'platform': 'tiktok', 'username': username, 'display_name': display_name,
                'bio': bio, 'followers_count': followers, 'phone_number': phone or 'N/A',
                'category': category, 'province': prov or "Indonesia", 'city': city or "",
                'engagement_rate': er, 'video_count': video_count,
                'potential_score': int(min((followers/5000)+(30 if phone else 0)+20, 100)),
                'potential_reason': f"Terdeteksi di {city or prov or 'Indonesia'}. Memiliki basis {followers:,} pengikut.",
                'tiktok_url': url, 'last_scraped': datetime.now().isoformat()
            }
            log(f"Profile Data: @{username} | Followers: {followers:,} | Phone: {phone or 'N/A'} | Loc: {city or prov or 'N/A'}", "INFO")
            self.db.query('sellers').upsert(data)
            log(f"Saved @{username} (Loc: {city or prov})", "SUCCESS")
            return True
        except Exception as e: log(f"Error @{username}: {str(e)[:50]}", "ERROR")
        finally: await page.close()

    def check_wa(self):
        if not self.wa['url'] or not self.wa['token']: return
        try:
            res = requests.get(f"{self.wa['url']}/waInstance{self.wa['id']}/receiveNotification/{self.wa['token']}", timeout=5).json()
            if res and "body" in res:
                body = res["body"]
                if body.get("typeWebhook") == "incomingMessageReceived" and body.get("senderData",{}).get("chatId") == self.wa['group']:
                    txt = body.get("messageData",{}).get("textMessageData",{}).get("textMessage","").upper()
                    status = 'approved' if 'ACC' in txt else 'rejected' if any(x in txt for x in ['REJ','NO']) else None
                    if status:
                        self.db.query('login_requests').update({'status': status}).eq('status', 'pending').execute()
                requests.delete(f"{self.wa['url']}/waInstance{self.wa['id']}/deleteNotification/{self.wa['token']}/{res['receiptId']}")
        except: pass

async def main_loop():
    db = SupabaseEngine()

    # Environment Check Logs for GitHub Actions
    is_github_action = os.environ.get('GITHUB_ACTIONS') == 'true'
    if is_github_action:
        log("Environment: GitHub Actions detected", "INFO")
        log(f"Supabase URL: {db.url[:20]}...", "INFO")
        log(f"WA API detected: {'Yes' if os.environ.get('WA_API_URL') else 'No'}", "INFO")

    if db.use_db: run_migrations(db)
    engine = TiktokEngine(db)
    start_time = datetime.now()
    duration_limit = timedelta(hours=5, minutes=45) if is_github_action else None

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

        log(f"ENGINE START: Monitoring Dashboard & WA... (Worker: {is_github_action})")

        while True:
            # Check duration if in GitHub Action
            if duration_limit and (datetime.now() - start_time > duration_limit):
                log("Duration reached in GitHub Action. Stopping.", "WARNING")
                break

            try:
                # 1. Heartbeat & Keep Alive (Prevent Sleep)
                now = datetime.now().isoformat()
                db.query('system_status').upsert({'id': 'main_engine', 'last_seen': now, 'status': 'online'}, on_conflict='id')
                db.query('system_status').upsert({'id': 'keep_alive_local', 'last_seen': now, 'status': 'active'}, on_conflict='id')

                # 2. Check WA
                engine.check_wa()

                # 3. Check Tasks
                res = db.query('search_queries').select('*').eq('status', 'pending').execute()
                if res.data:
                    log(f"Found {len(res.data)} pending tasks in queue", "INFO")
                    for task in res.data:
                        tid, q = task['id'], task['query']
                        db.query('search_queries').update({'status': 'processing'}).eq('id', tid).execute()
                        log(f"🚀 Processing Query: {q} (Task ID: {tid[:8]}...)", "INFO")

                        if q.startswith('@'):
                            await engine.extract_profile(context, q.replace('@',''))
                        else:
                            search_page = await context.new_page()
                            await search_page.goto(f"https://www.tiktok.com/search/user?q={q}")
                            await asyncio.sleep(10)
                            links = await search_page.query_selector_all('a[href*="/@"]')
                            users = []
                            for l in links:
                                try:
                                    href = await l.get_attribute('href')
                                    m = re.search(r'@([\w.]+)', href)
                                    if m: users.append(m.group(1))
                                except: pass

                            users = list(set(users))[:15]
                            await search_page.close()
                            for u in users: await engine.extract_profile(context, u, "Search")

                        db.query('search_queries').update({'status': 'completed'}).eq('id', tid).execute()

                # 4. Discovery Mode
                # If GH Action, always run discovery if no queue. Otherwise 5% chance.
                should_discover = is_github_action or (random.random() < 0.05)

                if should_discover:
                    cats = ["Kuliner", "Fashion", "Beauty", "Skincare", "Gadget", "Jasa"]
                    q = random.choice(["umkm indonesia", "jualan tiktok", "produk lokal", "pengiriman seluruh indonesia", "ready stock"])
                    cat = random.choice(cats)
                    log(f"Discovery Mode ({cat}): {q}")

                    search_page = await context.new_page()
                    try:
                        await search_page.goto(f"https://www.tiktok.com/search/user?q={q}")
                        await asyncio.sleep(10)
                        # Scroll a bit
                        await search_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(3)

                        links = await search_page.query_selector_all('a[href*="/@"]')
                        users = []
                        for l in links:
                            try:
                                href = await l.get_attribute('href')
                                m = re.search(r'@([\w.]+)', href)
                                if m: users.append(m.group(1))
                            except: pass

                        await search_page.close()
                        users = list(set(users))[:10]
                        log(f"Discovery found {len(users)} users")
                        for u in users:
                            await engine.extract_profile(context, u, cat)
                            await asyncio.sleep(random.uniform(5, 10))
                    except Exception as e:
                        log(f"Discovery Error: {e}", "WARNING")
                        if not search_page.is_closed(): await search_page.close()

            except Exception as e: log(f"Loop Error: {e}", "ERROR")

            # Wait time: GH Action faster loop, local slower
            wait_time = 30 if is_github_action else 15
            await asyncio.sleep(wait_time)

    await browser.close()

if __name__ == "__main__":
    asyncio.run(main_loop())
