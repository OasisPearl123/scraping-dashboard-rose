import os
import re
import json
import asyncio
import random
import requests
from datetime import datetime
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
        """Atomic lock untuk menghindari duplikasi"""
        try:
            # Coba claim keyword
            headers = {**self.headers, "Prefer": "return=representation"}
            data = {
                "query": keyword,
                "status": "processing",
                "locked_by": worker_id,
                "locked_at": datetime.now().isoformat()
            }
            url = f"{self.url}/rest/v1/search_queries?on_conflict=query"
            r = requests.post(url, headers=headers, json=data, timeout=10)
            return r.status_code == 200 or r.status_code == 201
        except:
            return False

class FastTiktokEngine:
    def __init__(self, db, worker_id):
        self.db = db
        self.worker_id = worker_id
        self.categories = ["Fashion", "Kuliner", "Beauty", "Skincare", "Gadget", "Elektronik", "Home Living"]
        self.cities = self.load_cities()

        # Mode dari environment
        self.mode = os.environ.get('SCRAPE_MODE', 'fast')
        self.max_profiles = int(os.environ.get('MAX_PROFILES', '200'))
        self.max_scrolls = int(os.environ.get('MAX_SCROLLS', '30'))

        # Cache untuk menghindari duplikasi
        self.processed_usernames = set()

    def load_cities(self):
        """Load cities with sharding"""
        cities = self.db.get("cities", "select=name,province_id")
        provinces = self.db.get("provinces", "select=id,name")

        if not cities or not provinces:
            log("Failed to load cities", "ERROR")
            return []

        p_map = {p['id']: p['name'] for p in provinces}
        for c in cities:
            c['province_name'] = p_map.get(c['province_id'], "Indonesia")

        # SHARDING: Bagi kota berdasarkan worker
        total_workers = int(os.environ.get('WORKER_TOTAL', '2'))
        worker_idx = int(os.environ.get('WORKER_INDEX', '0'))

        # Prioritaskan kota besar
        priority = ["Jakarta", "Bandung", "Surabaya", "Medan", "Semarang", "Yogyakarta"]
        priority_cities = [c for c in cities if any(p.lower() in c['name'].lower() for p in priority)]
        other_cities = [c for c in cities if c not in priority_cities]

        # Distribusi merata
        chunk_size = max(1, len(other_cities) // total_workers)
        start_idx = worker_idx * chunk_size
        end_idx = start_idx + chunk_size if worker_idx < total_workers - 1 else len(other_cities)

        my_cities = priority_cities + other_cities[start_idx:end_idx]
        log(f"Worker {worker_idx}: {len(my_cities)} cities assigned", "SUCCESS")
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
                          "wa", "order", "cod", "shopee", "tokopedia", "reseller"]
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
        existing = self.db.get("sellers", f"username=eq.{username}&select=username")
        if existing:
            self.processed_usernames.add(username)
            return

        page = await context.new_page()
        try:
            # Fast load dengan timeout pendek
            await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1500)  # Minimal wait

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
        """Fast search dengan scroll terbatas"""
        page = await context.new_page()
        users = set()

        try:
            await page.goto(f"https://www.tiktok.com/search/user?q={keyword.lower().replace(' ', '+')}", timeout=30000)
            await asyncio.sleep(random.uniform(1, 2))

            # Scroll cepat
            for _ in range(self.max_scrolls):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(0.3)

            # Ambil username
            anchors = await page.query_selector_all('a[href*="/@"]')
            for a in anchors[:self.max_profiles]:  # Batasi
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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={'width': 1280, 'height': 720}
        )

        log(f"🚀 Worker {worker_id} START | {len(engine.cities)} cities", "SUCCESS")

        # Ambil keyword dari database atau generate
        keywords = []
        for city in engine.cities:
            for cat in engine.categories:
                keywords.append(f"{cat} {city['name']}")

        random.shuffle(keywords)

        for keyword in keywords[:40]:  # Sesuaikan jumlah keyword per sesi
            # Lock keyword untuk menghindari duplikasi
            if not db.lock_keyword(keyword, f"worker_{worker_id}"):
                log(f"⏭️ {keyword} locked by another worker", "WARNING")
                continue

            category = keyword.split()[0]
            await engine.process_keyword_fast(context, keyword, category)

            # Update status
            db.upsert('search_queries', {'query': keyword, 'status': 'completed'}, on_conflict='query')

if __name__ == "__main__":
    asyncio.run(main_fast())
