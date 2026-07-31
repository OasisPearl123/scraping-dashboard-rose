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
        """Fixed lock with proper URL encoding"""
        try:
            # 1. URL encode keyword untuk query
            encoded_keyword = urllib.parse.quote(keyword)

            # 2. Cek apakah keyword sudah di-lock
            existing = self.get("search_queries", f"query=eq.{encoded_keyword}&select=status,locked_by,locked_at")

            if existing:
                record = existing[0]
                if record.get('status') == 'processing':
                    locked_at_str = record.get('locked_at')
                    if locked_at_str:
                        locked_at = datetime.fromisoformat(locked_at_str)
                        if (datetime.now() - locked_at).seconds < 600:
                            log(f"🔒 {keyword} still locked by {record.get('locked_by')}", "WARNING")
                            return False

            # 3. Ambil lock
            headers = {**self.headers, "Prefer": "return=representation"}
            data = {
                "query": keyword,  # Data pakai keyword asli
                "status": "processing",
                "locked_by": worker_id,
                "locked_at": datetime.now().isoformat()
            }
            url = f"{self.url}/rest/v1/search_queries?on_conflict=query"
            r = requests.post(url, headers=headers, json=data, timeout=10)

            if r.status_code in [200, 201]:
                log(f"🔓 Lock acquired: {keyword}", "INFO")
                return True
            else:
                log(f"Lock failed with status {r.status_code}: {keyword}", "WARNING")
                return False

        except Exception as e:
            log(f"Lock error for '{keyword}': {e}", "ERROR")
            return False

    def ai_generate_keywords(self, city, category):
        if not self.groq_token:
            return []
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self.groq_token}", "Content-Type": "application/json"}
            prompt = f"Generate 10 TikTok search keywords for finding local sellers in {city} for {category}. JSON array only."
            data = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "temperature": 0.9}
            resp = requests.post(url, headers=headers, json=data, timeout=25).json()
            match = re.search(r'\[.*\]', resp['choices'][0]['message']['content'], re.DOTALL)
            return json.loads(match.group(0)) if match else []
        except Exception as e:
            log(f"AI generate failed: {e}", "WARNING")
            return []

class FastTiktokEngine:
    def __init__(self, db, worker_id):
        self.db = db
        self.worker_id = worker_id
        self.total_workers = int(os.environ.get('WORKER_TOTAL', '2'))
        self.categories = ["Fashion", "Kuliner", "Beauty", "Skincare", "Gadget", "Elektronik", "Home Living"]
        self.cities = self.load_cities()

        # Mode dari environment
        self.max_profiles = int(os.environ.get('MAX_PROFILES', '200'))
        self.max_scrolls = int(os.environ.get('MAX_SCROLLS', '30'))

        # Cache untuk menghindari duplikasi
        self.processed_usernames = set()

    def load_cities(self):
        """Load cities with improved sharding - semua kota di-hash"""
        cities = self.db.get("cities", "select=name,province_id")
        provinces = self.db.get("provinces", "select=id,name")

        if not cities or not provinces:
            log("Failed to load cities", "ERROR")
            return []

        p_map = {p['id']: p['name'] for p in provinces}
        for c in cities:
            c['province_name'] = p_map.get(c['province_id'], "Indonesia")

        # SHARDING: Distribusi SEMUA kota dengan hash (termasuk priority)
        my_cities = []
        for city in cities:
            # Gunakan hash dari nama kota untuk distribusi merata
            hash_val = hash(city['name']) % self.total_workers
            if hash_val == self.worker_id:
                my_cities.append(city)

        log(f"Worker {self.worker_id}: {len(my_cities)} cities assigned", "SUCCESS")
        return my_cities

    def parse_followers(self, text):
        """Fast follower parsing"""
        if not text:
            return 0
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
        """Fast Indonesian bio detection"""
        if not bio:
            return False

        bio_lower = bio.lower()

        # Quick reject for non-Indonesian
        foreign_indicators = ["malaysia", "singapore", "philippines", "thailand", "india",
                             "pakistan", "usa", "uk", "england", "dubai", "shipping worldwide"]
        for word in foreign_indicators:
            if word in bio_lower:
                return False

        # Quick accept for Indonesian
        indo_indicators = ["indonesia", "jakarta", "bandung", "surabaya", "medan",
                          "wa", "order", "cod", "shopee", "tokopedia", "reseller",
                          "grosir", "murah", "jual", "beli", "produk", "lokal"]
        score = 0
        for word in indo_indicators:
            if word in bio_lower:
                score += 1
                if score >= 2:
                    return True

        return False

    async def extract_profile_fast(self, context, username, category):
        """Extract profile dengan kecepatan maksimal"""
        if username in self.processed_usernames:
            return

        # Cek duplikat di database
        encoded_username = urllib.parse.quote(username)
        existing = self.db.get("sellers", f"username=eq.{encoded_username}&select=username")
        if existing:
            self.processed_usernames.add(username)
            return

        page = await context.new_page()
        try:
            # Fast load dengan timeout pendek
            await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1500)

            # Ambil data cepat
            name_el = await page.query_selector('[data-e2e="user-title"]')
            display_name = await name_el.inner_text() if name_el else username

            bio_el = await page.query_selector('[data-e2e="user-bio"]')
            bio = await bio_el.inner_text() if bio_el else ""

            f_el = await page.query_selector('[data-e2e="followers-count"]')
            followers = self.parse_followers(await f_el.inner_text()) if f_el else 0

            # Filter cepat
            if followers >= 100000:
                return

            if not self.is_indonesian_bio_fast(bio):
                return

            # Simpan
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
            log(f"✅ {username} | {followers:,} f", "SUCCESS")

        except Exception:
            # Silent fail untuk kecepatan
            pass
        finally:
            await page.close()

    async def search_fast(self, context, keyword):
        """Fast search dengan scroll terbatas tapi lebih baik"""
        page = await context.new_page()
        users = set()

        try:
            await page.goto(f"https://www.tiktok.com/search/user?q={keyword.lower().replace(' ', '+')}", timeout=30000)
            await asyncio.sleep(random.uniform(1, 2))

            # Scroll dengan deteksi konten baru
            last_height = 0
            same_count = 0
            scroll_count = 0

            while same_count < 3 and scroll_count < self.max_scrolls:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(random.uniform(0.5, 1.0))

                new_height = await page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    same_count += 1
                else:
                    same_count = 0
                    last_height = new_height

                scroll_count += 1

                # Extract usernames setiap beberapa scroll
                if scroll_count % 5 == 0:
                    anchors = await page.query_selector_all('a[href*="/@"]')
                    for a in anchors:
                        href = await a.get_attribute("href")
                        if href:
                            match = re.search(r'@([\w._]+)', href)
                            if match:
                                users.add(match.group(1).lower())

            # Final extraction
            anchors = await page.query_selector_all('a[href*="/@"]')
            for a in anchors[:self.max_profiles]:
                href = await a.get_attribute("href")
                if href:
                    match = re.search(r'@([\w._]+)', href)
                    if match:
                        users.add(match.group(1).lower())

            return list(users)

        except:
            return []
        finally:
            await page.close()

    async def process_keyword_fast(self, context, keyword, category):
        """Process keyword dengan kecepatan tinggi"""
        log(f"🔍 {keyword}")

        users = await self.search_fast(context, keyword)
        if not users:
            return

        # Proses paralel dengan batch
        batch_size = 5
        for i in range(0, min(len(users), self.max_profiles), batch_size):
            batch = users[i:i+batch_size]
            tasks = [self.extract_profile_fast(context, u, category) for u in batch]
            await asyncio.gather(*tasks)

            # Delay minimal antar batch
            await asyncio.sleep(random.uniform(0.5, 1))

    async def generate_keywords(self):
        """Generate keywords dengan filter karakter aman"""
        keywords = []

        # 1. Coba ambil pending keywords dari database
        pending = self.db.get("search_queries", "status=eq.pending&limit=20")
        if pending:
            return [p['query'] for p in pending]

        # 2. Generate dari AI
        if self.db.groq_token:
            for city in self.cities[:5]:
                for cat in self.categories[:3]:
                    ai_keywords = self.db.ai_generate_keywords(city['name'], cat)
                    if ai_keywords:
                        # Filter keyword yang aman (hanya alphanumeric + spasi)
                        for kw in ai_keywords[:5]:
                            # Hanya karakter aman
                            safe_kw = re.sub(r'[^\w\s\-]', '', kw)
                            if safe_kw and len(safe_kw) > 3:
                                keywords.append(safe_kw)
                    await asyncio.sleep(0.1)

        # 3. Fallback manual dengan keyword aman
        if not keywords:
            for city in self.cities[:10]:
                for cat in self.categories[:3]:
                    # Keyword dengan format aman
                    keywords.append(f"{cat} {city['name']}")
                    keywords.append(f"{cat} murah {city['name']}")
                    keywords.append(f"jual {cat} {city['name']}")

        # Filter berdasarkan worker
        my_keywords = []
        for kw in keywords:
            # Skip keyword dengan karakter aneh
            if re.search(r'[^\w\s\-]', kw):
                continue
            if hash(kw) % self.total_workers == self.worker_id:
                my_keywords.append(kw)

        return my_keywords[:20]

async def main_fast():
    db = SupabaseREST()
    worker_id = int(os.environ.get('WORKER_INDEX', 0))
    engine = FastTiktokEngine(db, worker_id)

    if not engine.cities:
        log("No cities loaded", "ERROR")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 720}
        )

        log(f"🚀 Worker {worker_id} START | {len(engine.cities)} cities", "SUCCESS")

        # Generate keywords
        keywords = await engine.generate_keywords()
        log(f"📝 Generated {len(keywords)} keywords for worker {worker_id}", "INFO")

        for keyword in keywords:
            # Lock keyword untuk menghindari duplikasi
            if not db.lock_keyword(keyword, f"worker_{worker_id}"):
                log(f"⏭️ {keyword} locked by another worker", "WARNING")
                continue

            category = keyword.split()[0]
            if category not in engine.categories:
                category = random.choice(engine.categories)

            await engine.process_keyword_fast(context, keyword, category)

            # Update status
            db.upsert('search_queries', {'query': keyword, 'status': 'completed'}, on_conflict='query')

        log(f"✅ Worker {worker_id} FINISHED", "SUCCESS")

if __name__ == "__main__":
    asyncio.run(main_fast())
