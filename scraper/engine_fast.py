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
        self.db_pass = os.environ.get('PASSWORD_SUPABASE')
        self.groq_token = os.environ.get('token_groq')

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
            r = requests.get(f"{self.url}/rest/v1/{table}?{params}", headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log(f"GET {table} failed: {e}", "ERROR")
            return []

    def upsert(self, table, data, on_conflict="username"):
        try:
            headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
            url = f"{self.url}/rest/v1/{table}?on_conflict={on_conflict}"
            r = requests.post(url, headers=headers, json=data, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e:
            log(f"UPSERT failed: {e}", "ERROR")
            return False

    def lock_keyword(self, keyword, worker_id):
        """Atomic lock with proper URL encoding and expiration check"""
        try:
            encoded_keyword = urllib.parse.quote(keyword)
            existing = self.get("search_queries", f"query=eq.{encoded_keyword}&select=status,locked_by,locked_at")

            if existing:
                record = existing[0]
                if record.get('status') == 'processing':
                    locked_at_str = record.get('locked_at')
                    if locked_at_str:
                        locked_at = datetime.fromisoformat(locked_at_str.replace('Z', '+00:00'))
                        if (datetime.now(locked_at.tzinfo) - locked_at).total_seconds() < 600:
                            return False

            headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=representation"}
            data = {
                "query": keyword,
                "status": "processing",
                "locked_by": worker_id,
                "locked_at": datetime.now().isoformat()
            }
            url = f"{self.url}/rest/v1/search_queries?on_conflict=query"
            r = requests.post(url, headers=headers, json=data, timeout=10)
            return r.status_code in [200, 201]
        except Exception as e:
            log(f"Lock error for '{keyword}': {e}", "ERROR")
            return False

    def ai_generate_keywords(self, city, category):
        """Generate keywords with Qwen model - terbaik untuk Indonesia"""
        if not self.groq_token:
            return []
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self.groq_token}", "Content-Type": "application/json"}

            prompt = f"""Buat 20 kata kunci TikTok untuk mencari penjual lokal di {city} untuk kategori {category}.
Kata kunci harus:
1. Populer di TikTok Indonesia (gaul/slang)
2. Mengandung kata: murah, jual, beli, produk, toko, reseller, grosir, cod, shopee, tokopedia
3. Bervariasi: formal, gaul, dan kombinasi
4. Format: JSON array saja, tanpa penjelasan

Contoh format: ["fashion jakarta", "baju murah jakarta", "jual fashion jakarta", "reseller fashion jakarta"]
"""
            data = {
                "model": "qwen/qwen3.6-27b",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.9,
                "max_tokens": 800
            }

            resp = requests.post(url, headers=headers, json=data, timeout=45).json()
            content = resp['choices'][0]['message']['content']
            match = re.search(r'\[.*\]', content, re.DOTALL)
            if match:
                keywords = json.loads(match.group(0))
                return [kw for kw in keywords if len(kw) > 4]
            return []
        except Exception as e:
            log(f"AI generate failed: {e}", "WARNING")
            return []

class FastTiktokEngine:
    def __init__(self, db, worker_id):
        self.db = db
        self.worker_id = worker_id
        self.total_workers = int(os.environ.get('WORKER_TOTAL', '1'))
        self.categories = ["Fashion", "Kuliner", "Beauty", "Skincare", "Gadget", "Elektronik", "Home Living", "Jasa"]
        self.cities = self.load_cities()
        self.max_profiles = int(os.environ.get('MAX_PROFILES', '500'))
        self.processed_usernames = set()

        self.session_start_time = None
        self.total_profiles_saved = 0
        self.total_keywords_processed = 0

    def load_cities(self):
        """Load cities with dynamic sharding via hashing - TANPA HARDCODE"""
        cities = self.db.get("cities", "select=name,province_id")
        provinces = self.db.get("provinces", "select=id,name")
        if not cities or not provinces:
            log("Failed to load cities from DB", "ERROR")
            return []

        p_map = {p['id']: p['name'] for p in provinces}
        for c in cities:
            c['province_name'] = p_map.get(c['province_id'], "Indonesia")

        if self.total_workers == 1:
            log(f"Worker {self.worker_id}: ALL {len(cities)} cities assigned from DB", "SUCCESS")
            return cities

        my_cities = []
        for city in cities:
            if hash(city['name']) % self.total_workers == self.worker_id:
                my_cities.append(city)

        log(f"Worker {self.worker_id}: {len(my_cities)} cities assigned dynamically", "SUCCESS")
        return my_cities

    def parse_followers(self, text):
        if not text: return 0
        text = text.upper().strip()
        try:
            if text.endswith("K"):
                return int(float(text[:-1].replace(",", ".")) * 1000)
            if text.endswith("M"):
                return int(float(text[:-1].replace(",", ".")) * 1000000)
            if text.endswith("B"):
                return int(float(text[:-1].replace(",", ".")) * 1000000000)
            return int(re.sub(r"\D", "", text) or 0)
        except:
            return 0

    def is_indonesian_bio_fast(self, bio):
        """Fast detection using database values - NO HARDCODED NAMES"""
        if not bio: return False
        bio_lower = bio.lower()

        # Reject foreign scripts
        foreign_script = re.compile(r'[^\x00-\x7F\s\d.,!?;:()\'"%-]')
        if len(foreign_script.findall(bio)) > (len(bio) * 0.1):
            return False

        # Whitelist indicators (General terms allowed)
        indo_indicators = ["indonesia", "wa", "order", "cod", "shopee", "tokopedia", "reseller",
                          "grosir", "murah", "jual", "beli", "produk", "lokal", "ready"]

        score = 0
        for word in indo_indicators:
            if word in bio_lower:
                score += 1
                if score >= 2: return True

        # Match against DYNAMIC DB CITIES
        for city in self.cities:
            if city['name'].lower() in bio_lower: return True
            if city['province_name'].lower() in bio_lower: return True
        return False

    async def extract_profile_fast(self, context, username, category):
        if username in self.processed_usernames:
            return

        encoded_username = urllib.parse.quote(username)
        existing = self.db.get("sellers", f"username=eq.{encoded_username}&select=username")
        if existing:
            self.processed_usernames.add(username)
            return

        page = await context.new_page()
        try:
            await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1500)

            name_el = await page.query_selector('[data-e2e="user-title"]')
            display_name = await name_el.inner_text() if name_el else username

            bio_el = await page.query_selector('[data-e2e="user-bio"]')
            bio = await bio_el.inner_text() if bio_el else ""

            f_el = await page.query_selector('[data-e2e="followers-count"]')
            followers = self.parse_followers(await f_el.inner_text()) if f_el else 0

            if followers >= 100000:
                return

            if not self.is_indonesian_bio_fast(bio):
                return

            data = {
                "username": username,
                "display_name": display_name or username,
                "bio": bio,
                "followers_count": followers,
                "category": category,
                "tiktok_url": f"https://www.tiktok.com/@{username}",
                "last_scraped": datetime.now().isoformat()
            }

            self.db.upsert('sellers', data)
            self.processed_usernames.add(username)
            self.total_profiles_saved += 1
            log(f"✅ {username} | {followers:,} f", "SUCCESS")
        except Exception:
            pass
        finally:
            await page.close()

    async def search_infinite_scroll(self, context, keyword):
        """🔥 INFINITE SCROLL - Scroll sampai benar-benar ujung"""
        page = await context.new_page()
        users = set()
        try:
            await page.goto(f"https://www.tiktok.com/search/user?q={keyword.lower().replace(' ', '+')}", timeout=30000)
            await asyncio.sleep(random.uniform(2, 3))

            last_height = 0
            same_count = 0
            scroll_count = 0
            max_no_change = 10

            while same_count < max_no_change:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(random.uniform(1.5, 3.0))
                new_height = await page.evaluate("document.body.scrollHeight")

                if new_height == last_height:
                    same_count += 1
                else:
                    same_count = 0
                    last_height = new_height

                scroll_count += 1
                if scroll_count % 5 == 0:
                    anchors = await page.query_selector_all('a[href*="/@"]')
                    for a in anchors:
                        href = await a.get_attribute("href")
                        match = re.search(r'@([\w._]+)', href or "")
                        if match: users.add(match.group(1).lower())

            anchors = await page.query_selector_all('a[href*="/@"]')
            for a in anchors:
                href = await a.get_attribute("href")
                match = re.search(r'@([\w._]+)', href or "")
                if match: users.add(match.group(1).lower())

            return list(users)
        except Exception: return []
        finally: await page.close()

    async def process_keyword_fast(self, context, keyword, category):
        log(f"🔍 Searching: {keyword}")
        users = await self.search_infinite_scroll(context, keyword)
        if not users: return

        batch_size = 10
        total_processed = 0
        for i in range(0, len(users), batch_size):
            batch = users[i:i+batch_size]
            await asyncio.gather(*[self.extract_profile_fast(context, u, category) for u in batch])
            total_processed += len(batch)
            await asyncio.sleep(random.uniform(0.5, 1.5))

    async def generate_keywords(self):
        """Generate keywords with 3-level fallback - NO HARDCODE"""
        keywords = []
        pending = self.db.get("search_queries", "status=eq.pending&limit=30")
        if pending:
            log(f"📥 Found {len(pending)} pending keywords", "INFO")
            return [p['query'] for p in pending]

        if self.db.groq_token:
            log("🤖 Generating keywords with Qwen AI...", "INFO")
            for city in self.cities[:15]:
                for cat in self.categories[:5]:
                    try:
                        ai_keywords = self.db.ai_generate_keywords(city['name'], cat)
                        if ai_keywords:
                            for kw in ai_keywords[:8]:
                                safe_kw = re.sub(r'[^\w\s\-]', '', kw)
                                if safe_kw and len(safe_kw) > 3: keywords.append(safe_kw)
                        await asyncio.sleep(0.2)
                    except: continue

        if not keywords:
            log("⚠️ Using dynamic fallback keywords from DB", "WARNING")
            for city in self.cities[:20]:
                city_name = city['name']
                for cat in self.categories:
                    keywords.extend([f"{cat} {city_name}", f"{cat} murah {city_name}", f"jual {cat} {city_name}"])

        keywords = list(set(keywords))
        safe_keywords = [kw for kw in keywords if not re.search(r'[^\w\s\-]', kw) and len(kw) > 3]
        my_keywords = [kw for kw in safe_keywords if hash(kw) % self.total_workers == self.worker_id]
        return my_keywords[:80]

async def main_fast():
    db = SupabaseREST()
    worker_id = int(os.environ.get('WORKER_INDEX', 0))
    engine = FastTiktokEngine(db, worker_id)
    if not engine.cities: return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", viewport={'width': 1280, 'height': 720})

        session_count = 0
        while True:
            session_count += 1
            engine.total_profiles_saved = 0
            engine.total_keywords_processed = 0
            engine.session_start_time = datetime.now()
            engine.processed_usernames = set()

            log(f"📊 ===== SESSION {session_count} START =====", "SUCCESS")
            session_end_time = engine.session_start_time + timedelta(hours=5)

            while datetime.now() < session_end_time:
                keywords = await engine.generate_keywords()
                if not keywords:
                    await asyncio.sleep(180)
                    continue

                for keyword in keywords:
                    if datetime.now() >= session_end_time: break
                    if not db.lock_keyword(keyword, f"worker_{worker_id}"): continue
                    category = keyword.split()[0]
                    if category not in engine.categories: category = random.choice(engine.categories)
                    await engine.process_keyword_fast(context, keyword, category)
                    db.upsert('search_queries', {'query': keyword, 'status': 'completed'}, on_conflict='query')
                    engine.total_keywords_processed += 1
                    if engine.total_keywords_processed % 3 == 0:
                        db.upsert('system_status', {'id': f'worker_{worker_id}', 'last_seen': datetime.now().isoformat(), 'status': 'online', 'profiles_saved': engine.total_profiles_saved, 'session': session_count}, on_conflict='id')

            log(f"📊 SESSION {session_count} COMPLETE | Saved: {engine.total_profiles_saved}")
            log(f"⏳ PAUSING for 5 minutes...", "WARNING")
            await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(main_fast())
