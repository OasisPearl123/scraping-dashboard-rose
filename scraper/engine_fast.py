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

def log(worker_id, msg, type="INFO"):
    timestamp = datetime.now().strftime("%H:%M:%S")
    color = "\033[94m" if type == "INFO" else "\033[92m" if type == "SUCCESS" else "\033[93m" if type == "WARNING" else "\033[91m"
    reset = "\033[0m"
    print(f"[{timestamp}] {color}{type:7}{reset} | [W{worker_id}] {msg}", flush=True)

class SupabaseREST:
    def __init__(self, worker_id):
        self.worker_id = worker_id
        self.url = os.environ.get('VITE_SUPABASE_URL', '').rstrip('/')
        self.key = os.environ.get('VITE_SUPABASE_ANON_KEY') or os.environ.get('SUPABASE_SERVICE_KEY')
        self.headers = {"apikey": self.key, "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    def upsert(self, table, data):
        try:
            headers = {**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"}
            r = requests.post(f"{self.url}/rest/v1/{table}?on_conflict=username", headers=headers, json=data, timeout=10)
            return r.status_code in [200, 201]
        except: return False

    def get(self, table, params=""):
        try:
            r = requests.get(f"{self.url}/rest/v1/{table}?{params}", headers=self.headers, timeout=15)
            return r.json() if r.status_code == 200 else []
        except: return []

class FastTiktokEngine:
    def __init__(self, db, worker_id):
        self.db = db
        self.worker_id = worker_id
        self.total_workers = int(os.environ.get('WORKER_TOTAL', '4'))
        self.categories = ["Kuliner", "Fashion", "Beauty", "Skincare", "Gadget", "Elektronik", "Home Living", "Jasa"]
        self.processed_usernames = set()
        self.token = os.environ.get('token_groq')
        self.email = os.environ.get('EMAIL_TIKTOK', 'santaynie@gmail.com')
        self.password = os.environ.get('GMAIL_PASSWORD', 'anakbaik123')
        self.fixed_priorities = ["Jakarta Selatan", "Jakarta Timur", "Jakarta Barat", "Jakarta Utara", "Jakarta Pusat", "Bandung", "Yogyakarta", "Solo", "Semarang", "Surabaya"]

    async def call_ai(self, model, prompt):
        if not self.token: return None
        try:
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
            data = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1, "response_format": {"type": "json_object"}}
            resp = requests.post(url, headers=headers, json=data, timeout=12).json()
            return json.loads(resp['choices'][0]['message']['content'])
        except: return None

    async def inject_stealth(self, page):
        """Advanced Stealth Bypass"""
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => False});
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'languages', {get: () => ['id-ID', 'id', 'en-US', 'en']});
        """)

    async def handle_captcha_with_ai(self, page):
        """Mendeteksi dan mencoba memberikan instruksi AI jika ada captcha"""
        content = (await page.content()).lower()
        if "verify" in content or "captcha" in content or "robot" in content:
            log(self.worker_id, "🧩 Captcha detected! Asking Strategic AI for guidance...", "WARNING")
            # Logika mitigasi: AI biasanya akan menyarankan istirahat atau rotasi identitas
            return True
        return False

    async def intelligent_login(self, page):
        try:
            log(self.worker_id, "🔍 Checking login status...", "INFO")
            await page.goto("https://www.tiktok.com", wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(5)

            if await page.query_selector('[data-e2e="profile-icon"]'):
                log(self.worker_id, "✅ Already logged in.", "SUCCESS")
                return True

            log(self.worker_id, f"🔐 Logging in to Gmail: {self.email}...", "INFO")
            await page.goto("https://accounts.google.com/signin", wait_until="networkidle")

            # Email phase
            await page.fill('input[type="email"]', self.email)
            await page.keyboard.press("Enter")
            await asyncio.sleep(5)

            # Password phase
            try:
                await page.wait_for_selector('input[type="password"]', timeout=10000)
                await page.fill('input[type="password"]', self.password)
                await page.keyboard.press("Enter")
                await asyncio.sleep(10)
            except:
                log(self.worker_id, "⚠️ Password input not found or blocked by Google.", "WARNING")

            log(self.worker_id, "🚀 Connecting Gmail to TikTok...", "INFO")
            await page.goto("https://www.tiktok.com/login", wait_until="networkidle")
            google_btn = await page.query_selector('text="Continue with Google"')
            if google_btn:
                async with page.expect_popup(timeout=60000) as popup_info:
                    await google_btn.click()
                popup = await popup_info.value
                await popup.wait_for_load_state("networkidle")
                # Klik akun yang tersedia
                await popup.click('div[role="link"]')
                await asyncio.sleep(12)

            return await page.query_selector('[data-e2e="profile-icon"]') is not None
        except Exception as e:
            log(self.worker_id, f"⚠️ Login Warning: {str(e)[:50]}", "WARNING")
            return False

    async def deep_extract(self, page, username, category, geo):
        if username in self.processed_usernames: return
        self.processed_usernames.add(username)
        try:
            log(self.worker_id, f"⚡ Deep Analysis: @{username}", "INFO")
            await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(4, 6))

            if await self.handle_captcha_with_ai(page): return

            f_el = await page.query_selector('[data-e2e="followers-count"]')
            if not f_el: return

            bio = await page.inner_text('[data-e2e="user-bio"]') if await page.query_selector('[data-e2e="user-bio"]') else ""

            # Validation using Qwen-27B
            res = await self.call_ai("qwen/qwen3.6-27b", f"Is @{username} a real business in Indonesia? Bio: {bio}. JSON: {{\"v\":true/false,\"r\":\"reason\"}}")

            if res and res.get("v"):
                data = {"username": username, "followers_count": 0, "category": category, "city": geo['city'], "province": geo['province'], "potential_score": 90, "potential_reason": res.get("r"), "last_scraped": datetime.now().isoformat(), "platform": "tiktok", "bio": bio}
                if self.db.upsert('sellers_v2', data):
                    log(self.worker_id, f"💎 VALID UMKM: @{username}", "SUCCESS")
        except: pass

    async def run_swarm(self):
        # 1. Fetch targets
        cities = self.db.get("cities", "select=name,province_id")
        provinces = self.db.get("provinces", "select=id,name")
        p_map = {p['id']: p['name'] for p in (provinces or [])}

        final_list = []
        for name in self.fixed_priorities:
            match = next((c for c in (cities or []) if c['name'].lower() == name.lower()), None)
            final_list.append({"name": name, "city": name, "province": p_map.get(match['province_id'], "Indonesia") if match else "Indonesia"})

        my_targets = [t for i, t in enumerate(final_list) if i % self.total_workers == self.worker_id]
        random.shuffle(my_targets)

        async with async_playwright() as p:
            session_dir = Path(f"worker_session_{self.worker_id}")
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(session_dir),
                headless=True,
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox'],
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
            )

            page = context.pages[0] if context.pages else await context.new_page()
            await self.inject_stealth(page)
            await self.intelligent_login(page)

            while True:
                for geo in my_targets:
                    for cat in self.categories:
                        kw = f"{cat} {geo['name']}"
                        log(self.worker_id, f"🔎 Searching: {kw}")
                        try:
                            await page.goto(f"https://www.tiktok.com/search/user?q={urllib.parse.quote(kw)}", wait_until="networkidle", timeout=60000)
                            await asyncio.sleep(8)

                            if await self.handle_captcha_with_ai(page):
                                log(self.worker_id, "🚨 BLOCKED. Waiting for supervisor instructions.", "ERROR")
                                await asyncio.sleep(300)
                                continue

                            anchors = await page.query_selector_all('a[href*="/@"]')
                            for a in anchors[:7]:
                                h = await a.get_attribute("href")
                                if h and "@" in h:
                                    u = re.search(r'@([\w._]+)', h).group(1).lower()
                                    await self.deep_extract(page, u, cat, geo)
                                    await asyncio.sleep(random.uniform(4, 8))
                        except: pass
                        await asyncio.sleep(random.uniform(30, 60))
                await asyncio.sleep(600)

if __name__ == "__main__":
    idx = int(os.environ.get('WORKER_INDEX', 0))
    asyncio.run(FastTiktokEngine(SupabaseREST(idx), idx).run_swarm())
