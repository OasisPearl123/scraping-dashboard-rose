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

class TiktokEngine:
    def __init__(self, db):
        self.db = db
        self.categories = ["Kuliner", "Fashion", "Beauty", "Skincare", "Gadget", "Elektronik", "Home Living", "Jasa"]
        self.cities = self.load_cities()

        # Indonesian keywords for bio filtering
        self.INDONESIA_KEYWORDS = {
            "jakarta", "bandung", "medan", "surabaya",
            "indonesia", "wa", "order", "pesan", "murah",
            "reseller", "grosir", "cod", "tokopedia", "shopee"
        }

        # Foreign country blacklist
        self.FOREIGN_WORDS = {
            "shipping worldwide", "malaysia", "singapore",
            "philippines", "thailand", "india", "usa", "uk"
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
        """Parse follower count from text like '1.2M', '15.8K', '900'"""
        text = text.upper().replace(",", ".").strip()

        if text.endswith("K"):
            return int(float(text[:-1]) * 1000)
        if text.endswith("M"):
            return int(float(text[:-1]) * 1000000)
        if text.endswith("B"):
            return int(float(text[:-1]) * 1000000000)

        return int(re.sub(r"\D", "", text) or 0)

    def is_indonesian_bio(self, bio):
        """Check if bio is Indonesian using keyword matching"""
        if not bio:
            return False

        bio_lower = bio.lower()

        # Check for foreign country indicators first
        for word in self.FOREIGN_WORDS:
            if word in bio_lower:
                return False

        # Count Indonesian keywords
        score = 0
        for word in self.INDONESIA_KEYWORDS:
            if word in bio_lower:
                score += 1
                if score >= 2:  # Need at least 2 Indonesian keywords
                    return True

        return False

    async def extract_profile(self, context, username, category):
        """Extract profile data with retry and improved filtering"""
        if not username:
            return

        existing = self.db.get("sellers", f"username=eq.{username}&select=username")
        if existing:
            return

        page = await context.new_page()

        try:
            for retry in range(3):
                try:
                    await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(2500)  # Wait for page to stabilize

                    # Scroll a bit to trigger lazy loading
                    await page.mouse.wheel(0, 500)
                    await asyncio.sleep(1)

                    # Get display name
                    name_element = await page.query_selector('[data-e2e="user-title"]')
                    display_name = await name_element.inner_text() if name_element else username

                    # Get bio
                    bio_element = await page.query_selector('[data-e2e="user-bio"]')
                    bio = await bio_element.inner_text() if bio_element else ""

                    # Get followers
                    followers_text = await page.inner_text('[data-e2e="followers-count"]') if await page.query_selector('[data-e2e="followers-count"]') else "0"
                    followers = self.parse_followers(followers_text)

                    # Skip if bio is not Indonesian
                    if not self.is_indonesian_bio(bio):
                        log(f"⏭️ Skipping @{username}: Bio not Indonesian", "WARNING")
                        return

                    # Save only essential data
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
                    log(f"✅ Saved @{username} | Followers: {followers:,}", "SUCCESS")
                    return

                except Exception as e:
                    log(f"Retry {retry+1}/3 for @{username}: {str(e)[:50]}", "WARNING")
                    await asyncio.sleep(3)
                    continue

        except Exception as e:
            log(f"Failed @{username}: {str(e)[:50]}", "ERROR")
        finally:
            await page.close()

    async def search_page(self, context, keyword):
        """Search users with infinite scroll"""
        page = await context.new_page()
        users = set()

        try:
            # Build search URL
            search_url = f"https://www.tiktok.com/search/user?q={keyword.lower().replace(' ', '+')}"
            await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)

            # Initial wait
            await asyncio.sleep(random.uniform(2, 4))

            # Infinite scroll until no new content
            last_height = 0
            same_count = 0
            scroll_attempts = 0
            max_scrolls = 100  # Safety limit

            while same_count < 5 and scroll_attempts < max_scrolls:
                # Smooth scroll
                await page.evaluate("""
                    window.scrollTo({
                        top: document.body.scrollHeight,
                        behavior: 'smooth'
                    });
                """)

                # Random delay between scrolls
                await asyncio.sleep(random.uniform(1.5, 3.5))

                # Check if new content loaded
                new_height = await page.evaluate("document.body.scrollHeight")

                if new_height == last_height:
                    same_count += 1
                else:
                    same_count = 0
                    last_height = new_height

                scroll_attempts += 1

                # Extract usernames after each scroll
                anchors = await page.query_selector_all('a[href*="/@"]')
                for anchor in anchors:
                    href = await anchor.get_attribute("href")
                    if href:
                        match = re.search(r'@([\w._]+)', href)
                        if match:
                            users.add(match.group(1).lower())

                # Log progress
                if len(users) > 0 and len(users) % 50 == 0:
                    log(f"Found {len(users)} usernames so far...", "INFO")

            log(f"✅ Found {len(users)} unique users for '{keyword}'", "SUCCESS")
            return list(users)

        except Exception as e:
            log(f"Search failed for '{keyword}': {str(e)[:50]}", "ERROR")
            return []
        finally:
            await page.close()

    async def process_keyword(self, context, keyword, category, max_profiles=500):
        """Process a single search keyword"""
        log(f"🔍 Searching: {keyword} (Category: {category})")

        # Search for users
        users = await self.search_page(context, keyword)

        if not users:
            log(f"No users found for '{keyword}'", "WARNING")
            return

        # Process users (limit to max_profiles)
        processed = 0
        for username in users[:max_profiles]:
            try:
                await self.extract_profile(context, username, category)
                processed += 1

                # Random delay between profiles
                await asyncio.sleep(random.uniform(1, 3))

                # Log progress
                if processed % 10 == 0:
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
        log("CRITICAL: Failed to load cities. Check environment and database.", "ERROR")
        return

    # Define priority cities
    priority_names = ["Jakarta Selatan", "Jakarta Timur", "Jakarta Pusat", "Yogyakarta"]
    priority_cities = [c for c in engine.cities if any(p.lower() in c['name'].lower() for p in priority_names)]
    other_cities = [c for c in engine.cities if c not in priority_cities]

    # Distribute cities across workers
    my_cities = priority_cities + (other_cities[:len(other_cities)//2] if worker_idx == 0 else other_cities[len(other_cities)//2:])

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        log(f"🚀 WORKER {worker_idx} ONLINE | Cities: {len(my_cities)}")

        while True:
            # Update status
            db.upsert('system_status', {'id': f'worker_{worker_idx}', 'last_seen': datetime.now().isoformat(), 'status': 'online'}, on_conflict='id')
            db.upsert('system_status', {'id': 'main_engine', 'last_seen': datetime.now().isoformat(), 'status': 'online'}, on_conflict='id')

            # Select city and category
            city = random.choice(my_cities)
            category = random.choice(engine.categories)

            # Get or generate keywords for this city-category combination
            pending = db.get("search_queries", f"status=eq.pending&query=like.*{city['name']}*&limit=1")

            if pending:
                keyword = pending[0]['query']
                db.upsert('search_queries', {'query': keyword, 'status': 'processing'}, on_conflict='query')
            else:
                # Generate keywords with AI
                keywords = db.ai_generate_keywords(city['name'], category)
                if keywords:
                    for kw in keywords[:10]:  # Limit to 10 keywords per city-category
                        db.upsert('search_queries', {'query': kw, 'status': 'pending'}, on_conflict='query')

                    keyword = keywords[0]
                    db.upsert('search_queries', {'query': keyword, 'status': 'processing'}, on_conflict='query')
                else:
                    # Fallback keywords
                    fallback_keywords = [
                        f"{category.lower()} {city['name']}",
                        f"{category.lower()} {city['name']} murah",
                        f"jual {category.lower()} {city['name']}",
                        f"beli {category.lower()} {city['name']}",
                        f"{category.lower()} lokal {city['name']}"
                    ]
                    keyword = random.choice(fallback_keywords)
                    db.upsert('search_queries', {'query': keyword, 'status': 'processing'}, on_conflict='query')

            # Process keyword
            await engine.process_keyword(context, keyword, category, max_profiles=500)

            # Mark as completed
            db.upsert('search_queries', {'query': keyword, 'status': 'completed'}, on_conflict='query')

            # Random delay between keywords
            await asyncio.sleep(random.uniform(5, 15))

            # Rotate user agent occasionally
            if random.random() < 0.1:
                user_agents = [
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"
                ]
                await context.set_extra_http_headers({"User-Agent": random.choice(user_agents)})

if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        log("Shutting down gracefully...", "WARNING")