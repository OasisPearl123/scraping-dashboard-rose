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

        # 🔥 DINAMIS: Ambil kata-kata Indonesia dari database atau environment
        self.INDONESIA_KEYWORDS = self.load_indonesian_keywords()
        self.FOREIGN_WORDS = self.load_foreign_blacklist()

    def load_indonesian_keywords(self):
        """Load Indonesian keywords from database or use defaults"""
        # Coba ambil dari database
        try:
            keywords = self.db.get("indonesian_keywords", "select=keyword")
            if keywords:
                return set([k['keyword'].lower() for k in keywords])
        except:
            pass

        # Fallback ke environment variable
        env_keywords = os.environ.get('INDONESIA_KEYWORDS', '')
        if env_keywords:
            return set([k.strip().lower() for k in env_keywords.split(',')])

        # Default minimal jika tidak ada data
        return {
            "indonesia", "jakarta", "bandung", "surabaya", "medan", "semarang",
            "order", "pesan", "murah", "reseller", "grosir", "cod", "ready",
            "tokopedia", "shopee", "lazada", "blibli", "bukalapak",
            "wa", "ongkir", "pengiriman", "jual", "beli", "produk", "lokal"
        }

    def load_foreign_blacklist(self):
        """Load foreign country blacklist from database or environment"""
        # Coba ambil dari database
        try:
            countries = self.db.get("foreign_countries", "select=name")
            if countries:
                return set([c['name'].lower() for c in countries])
        except:
            pass

        # Fallback ke environment variable
        env_blacklist = os.environ.get('FOREIGN_BLACKLIST', '')
        if env_blacklist:
            return set([b.strip().lower() for b in env_blacklist.split(',')])

        # Default minimal
        return {
            "malaysia", "singapore", "philippines", "thailand", "india",
            "pakistan", "usa", "uk", "england", "dubai", "mexico",
            "france", "italy", "germany", "korea", "japan", "china",
            "shipping worldwide", "international shipping"
        }

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
        """Parse followers dengan support berbagai format"""
        if not text:
            return 0

        # Bersihkan text
        text = text.upper().strip()

        # Handle format "1.2M", "15.8K", "1.1B"
        if text.endswith("K"):
            try:
                return int(float(text[:-1].replace(",", ".")) * 1000)
            except:
                return 0
        if text.endswith("M"):
            try:
                return int(float(text[:-1].replace(",", ".")) * 1000000)
            except:
                return 0
        if text.endswith("B"):
            try:
                return int(float(text[:-1].replace(",", ".")) * 1000000000)
            except:
                return 0

        # Handle format "1,234" atau "1.234"
        try:
            # Hapus semua karakter non-digit kecuali koma/titik
            cleaned = re.sub(r'[^\d,.]', '', text)
            # Ganti koma dengan titik untuk parsing
            cleaned = cleaned.replace(',', '.')
            # Jika masih ada titik, berarti desimal atau ribuan
            if '.' in cleaned:
                # Jika format "1.234" (ribuan) -> "1234"
                parts = cleaned.split('.')
                if len(parts) == 2 and len(parts[1]) == 3:
                    cleaned = parts[0] + parts[1]
            return int(float(cleaned) or 0)
        except:
            return 0

    def is_indonesian_bio(self, bio):
        """Enhanced Indonesian bio detection with dynamic keywords"""
        if not bio:
            return False

        bio_lower = bio.lower()

        # 1. STRICT: Reject heavy non-latin script
        foreign_script = re.compile(r'[^\x00-\x7F\s\d.,!?;:()\'"%-]')
        if len(foreign_script.findall(bio)) > (len(bio) * 0.1):
            return False

        # 2. BLACKLIST: Reject foreign countries
        for word in self.FOREIGN_WORDS:
            if word in bio_lower:
                return False

        # 3. WHITELIST: Check for Indonesian cities/provinces
        for city in self.cities:
            if city['name'].lower() in bio_lower:
                return True
            if city['province_name'].lower() in bio_lower:
                return True

        # 4. Check for Indonesian commerce keywords
        matches = 0
        for keyword in self.INDONESIA_KEYWORDS:
            if keyword in bio_lower:
                matches += 1
                if matches >= 2:  # Need at least 2 keywords
                    return True

        return False

    async def extract_profile(self, context, username, category, max_retries=3):
        """Extract profile dengan retry dan improved parsing"""
        if not username:
            return

        existing = self.db.get("sellers", f"username=eq.{username}&select=username")
        if existing:
            return

        page = await context.new_page()

        for attempt in range(max_retries):
            try:
                await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(2500)  # Wait for dynamic content

                # Scroll to trigger lazy loading
                await page.mouse.wheel(0, 500)
                await asyncio.sleep(1)

                # Get display name
                name_el = await page.query_selector('[data-e2e="user-title"]')
                display_name = await name_el.inner_text() if name_el else username

                # Get bio
                bio_el = await page.query_selector('[data-e2e="user-bio"]')
                bio = await bio_el.inner_text() if bio_el else ""

                # Get followers
                f_el = await page.query_selector('[data-e2e="followers-count"]')
                followers_text = await f_el.inner_text() if f_el else "0"
                followers = self.parse_followers(followers_text)

                # Skip if followers too high (not SME)
                if followers >= 100000:
                    log(f"⏭️ @{username}: Too many followers ({followers:,})", "WARNING")
                    return

                # Check if Indonesian
                if not self.is_indonesian_bio(bio):
                    log(f"⏭️ @{username}: Not Indonesian", "WARNING")
                    return

                # Detect city from bio
                city_name, prov_name = "", "Indonesia"
                full_text = f"{display_name} {bio}".lower()
                for city in self.cities:
                    if city['name'].lower() in full_text:
                        city_name, prov_name = city['name'], city['province_name']
                        break

                # Save data
                data = {
                    "username": username,
                    "display_name": display_name or username,
                    "bio": bio,
                    "followers_count": followers,
                    "category": category,
                    "province": prov_name,
                    "city": city_name,
                    "tiktok_url": f"https://www.tiktok.com/@{username}",
                    "last_scraped": datetime.now().isoformat()
                }

                self.db.upsert('sellers', data)
                log(f"✅ Saved @{username} | Followers: {followers:,} | {city_name or 'Indonesia'}", "SUCCESS")
                return

            except Exception as e:
                log(f"Retry {attempt+1}/{max_retries} for @{username}: {str(e)[:50]}", "WARNING")
                await asyncio.sleep(random.uniform(2, 4))
                continue

        log(f"❌ Failed @{username} after {max_retries} attempts", "ERROR")
        await page.close()

    async def search_page(self, context, keyword, max_scrolls=80):
        """Search with infinite scroll until no new content"""
        page = await context.new_page()
        users = set()

        try:
            # Retry for page load
            for retry in range(2):
                try:
                    await page.goto(f"https://www.tiktok.com/search/user?q={keyword.lower().replace(' ', '+')}", timeout=45000)
                    break
                except:
                    if retry == 0:
                        await asyncio.sleep(3)
                    else:
                        return []

            await asyncio.sleep(random.uniform(2, 4))

            # INFINITE SCROLL
            last_height = 0
            same_count = 0
            scroll_attempts = 0

            while same_count < 5 and scroll_attempts < max_scrolls:
                # Smooth scroll
                await page.evaluate("""
                    window.scrollTo({
                        top: document.body.scrollHeight,
                        behavior: 'smooth'
                    });
                """)

                # Random delay like human
                await asyncio.sleep(random.uniform(1.5, 3.5))

                # Check if new content loaded
                new_height = await page.evaluate("document.body.scrollHeight")

                if new_height == last_height:
                    same_count += 1
                else:
                    same_count = 0
                    last_height = new_height

                scroll_attempts += 1

                # Extract usernames
                anchors = await page.query_selector_all('a[href*="/@"]')
                for a in anchors:
                    href = await a.get_attribute("href")
                    if href:
                        match = re.search(r'@([\w._]+)', href)
                        if match:
                            users.add(match.group(1).lower())

                # Log progress
                if len(users) > 0 and len(users) % 100 == 0:
                    log(f"Found {len(users)} usernames for '{keyword}'", "INFO")

            log(f"✅ Found {len(users)} unique users for '{keyword}'", "SUCCESS")
            return list(users)

        except Exception as e:
            log(f"Search failed for '{keyword}': {str(e)[:50]}", "ERROR")
            return []
        finally:
            await page.close()

    async def process_keyword(self, context, keyword, category, max_profiles=500):
        """Process keyword with configurable max profiles"""
        log(f"🔍 Searching: {keyword} (Category: {category})")

        users = await self.search_page(context, keyword)
        if not users:
            log(f"No users found for '{keyword}'", "WARNING")
            return

        # Process users with delay
        processed = 0
        for username in users[:max_profiles]:
            try:
                await self.extract_profile(context, username, category)
                processed += 1

                # Random delay between profiles
                await asyncio.sleep(random.uniform(1.5, 3.5))

                if processed % 20 == 0:
                    log(f"Processed {processed}/{min(len(users), max_profiles)} profiles", "INFO")

            except Exception as e:
                log(f"Error processing @{username}: {str(e)[:50]}", "ERROR")
                continue

        log(f"✅ Completed {keyword}: processed {processed} profiles", "SUCCESS")

async def main_loop():
    db = SupabaseREST()
    engine = TiktokEngine(db)
    worker_idx = int(os.environ.get('WORKER_INDEX', 0))

    if not engine.cities:
        log("CRITICAL: Failed to load cities.", "ERROR")
        return

    # Priority cities from environment or database
    priority_names = os.environ.get('PRIORITY_CITIES', 'Jakarta Selatan,Jakarta Timur,Jakarta Pusat,Yogyakarta').split(',')
    priority_cities = [c for c in engine.cities if any(p.lower() in c['name'].lower() for p in priority_names)]
    other_cities = [c for c in engine.cities if c not in priority_cities]

    # Distribute cities
    if other_cities:
        split_point = len(other_cities) // 2
        my_cities = priority_cities + (other_cities[:split_point] if worker_idx == 0 else other_cities[split_point:])
    else:
        my_cities = priority_cities

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # Rotate user agents
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ]

        context = await browser.new_context(
            user_agent=random.choice(user_agents),
            viewport={'width': random.randint(1200, 1920), 'height': random.randint(800, 1080)}
        )

        log(f"🚀 WORKER {worker_idx} START | Cities: {len(my_cities)}")

        while True:
            # Update status
            db.upsert('system_status', {'id': f'worker_{worker_idx}', 'last_seen': datetime.now().isoformat(), 'status': 'online'}, on_conflict='id')
            db.upsert('system_status', {'id': 'main_engine', 'last_seen': datetime.now().isoformat(), 'status': 'online'}, on_conflict='id')

            # Select random city and category
            city = random.choice(my_cities)
            category = random.choice(engine.categories)

            # Get or generate keywords
            keyword = f"{category} {city['name']}"
            pending = db.get("search_queries", f"status=eq.pending&query=like.*{city['name']}*&limit=1")

            if pending:
                keyword = pending[0]['query']
                db.upsert('search_queries', {'query': keyword, 'status': 'processing'}, on_conflict='query')
            else:
                keywords = db.ai_generate_keywords(city['name'], category)
                if keywords:
                    for kw in keywords[:15]:
                        db.upsert('search_queries', {'query': kw, 'status': 'pending'}, on_conflict='query')
                    keyword = keywords[0]
                    db.upsert('search_queries', {'query': keyword, 'status': 'processing'}, on_conflict='query')

            # Process keyword
            await engine.process_keyword(context, keyword, category, max_profiles=int(os.environ.get('MAX_PROFILES', 500)))

            # Mark as completed
            db.upsert('search_queries', {'query': keyword, 'status': 'completed'}, on_conflict='query')

            # Random delay between keywords
            await asyncio.sleep(random.uniform(5, 15))

            # Rotate user agent occasionally
            if random.random() < 0.1:
                await context.set_extra_http_headers({"User-Agent": random.choice(user_agents)})

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        log("Shutting down gracefully...", "WARNING")