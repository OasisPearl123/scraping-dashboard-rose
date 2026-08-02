import os
import re
import json
import asyncio
import random
import requests
import urllib.parse
from datetime import datetime, timedelta  # 🔥 PERBAIKAN: tambahkan timedelta
from playwright.async_api import async_playwright

# 🔐 AMAN: Gunakan getpass untuk input credentials
from getpass import getpass

print("="*60)
print("🔐 MASUKKAN CREDENTIALS ANDA")
print("="*60)

SUPABASE_URL = getpass("Supabase URL: ")
SUPABASE_KEY = getpass("Supabase Anon Key: ")
GROQ_TOKEN = getpass("Groq API Token: ")

# ============================================
# KONFIGURASI
# ============================================
CATEGORIES = ["Kuliner", "Fashion", "Beauty", "Skincare", "Gadget", "Elektronik", "Home Living", "Jasa"]

PRIORITY_REGIONS = [
    "Jakarta Selatan", "Jakarta Timur", "Jakarta Pusat", "Jakarta Barat", "Jakarta Utara",
    "Bogor", "Depok", "Tangerang", "Bekasi",
    "Bandung", "Yogyakarta", "Solo", "Semarang", "Surabaya", "Malang", "Bali"
]

# ============================================
# INDIKATOR DINAMIS
# ============================================
INDONESIAN_INDICATORS = [
    "wa", "order", "cod", "shopee", "tokopedia", "reseller", "grosir",
    "murah", "jual", "beli", "produk", "lokal", "ready", "ongkir",
    "pengiriman", "garansi", "original", "terpercaya", "toko", "distro",
    "umkm", "home industry", "katalog", "pesan", "dm", "direct message"
]

FOREIGN_INDICATORS = [
    "malaysia", "singapore", "philippines", "thailand", "india",
    "pakistan", "usa", "uk", "england", "dubai",
    "shipping worldwide", "international shipping"
]

def log(msg, type="INFO"):
    color = "\033[94m" if type == "INFO" else "\033[92m" if type == "SUCCESS" else "\033[93m" if type == "WARNING" else "\033[91m"
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {color}{type:7}\033[0m | {msg}")

def parse_followers(text):
    """Parse follower count dari teks (1.2K, 10M, 900)"""
    if not text:
        return 0
    text = text.upper().replace(',', '.').strip()
    try:
        if text.endswith("K"):
            return int(float(text[:-1]) * 1000)
        if text.endswith("M"):
            return int(float(text[:-1]) * 1000000)
        if text.endswith("B"):
            return int(float(text[:-1]) * 1000000000)
        return int(re.sub(r"\D", "", text) or 0)
    except:
        return 0

def is_indonesian_bio(bio):
    """Filter cepat: cek apakah bio Indonesia"""
    if not bio or len(bio) < 10:
        return False
    
    bio_lower = bio.lower()
    
    # Cek foreign indicators dulu
    for word in FOREIGN_INDICATORS:
        if word in bio_lower:
            return False
    
    # Cek Indonesian indicators
    score = 0
    for word in INDONESIAN_INDICATORS:
        if word in bio_lower:
            score += 1
            if score >= 2:
                return True
    
    return False

class SmartTikTokScraper:
    def __init__(self):
        self.processed_usernames = set()
        self.total_profiles = 0
        self.total_errors = 0
        
    async def call_ai_with_retry(self, model, prompt, max_retries=3):
        """Panggil AI dengan retry mechanism"""
        for attempt in range(max_retries):
            try:
                url = "https://api.groq.com/openai/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {GROQ_TOKEN}",
                    "Content-Type": "application/json"
                }
                data = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": 200
                }
                resp = requests.post(url, headers=headers, json=data, timeout=20)
                resp.raise_for_status()
                result = resp.json()
                content = result['choices'][0]['message']['content']
                return json.loads(content)
            except Exception as e:
                log(f"AI attempt {attempt+1} failed: {e}", "WARNING")
                await asyncio.sleep(2 ** attempt)
        return None

    async def process_account(self, page, username, category, city):
        """Proses satu akun dengan multi-layer filtering"""
        if username in self.processed_usernames:
            return
        
        self.processed_usernames.add(username)
        
        try:
            # --- 1. LOAD PROFILE ---
            await page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(2, 3))
            
            # --- 2. GET DATA ---
            name_el = await page.query_selector('[data-e2e="user-title"]')
            display_name = await name_el.inner_text() if name_el else username
            
            bio_el = await page.query_selector('[data-e2e="user-bio"]')
            bio = await bio_el.inner_text() if bio_el else ""
            
            f_el = await page.query_selector('[data-e2e="followers-count"]')
            followers = parse_followers(await f_el.inner_text()) if f_el else 0
            
            # --- 3. FILTER CEPAT (Tanpa AI) ---
            if followers < 100:
                log(f"⏭️ @{username}: Followers {followers} (terlalu kecil)", "WARNING")
                return
            if followers > 100000:
                log(f"⏭️ @{username}: Followers {followers:,} (terlalu besar)", "WARNING")
                return
            
            if not is_indonesian_bio(bio):
                log(f"⏭️ @{username}: Bio bukan Indonesia", "WARNING")
                return
            
            # --- 4. AI VERIFICATION (Layer 1: Fast) ---
            l1_prompt = f"""Analyze if this TikTok account is an Indonesian UMKM/SME business.
Bio: "{bio}"
Display Name: "{display_name}"
Username: @{username}

Rules:
- Must have Indonesian language or commerce keywords
- Must show signs of selling products/services
- Personal accounts with no business indicators should be rejected

Return JSON: {{"is_umkm": true/false, "confidence": 0-100, "reason": "brief reason"}}"""

            l1 = await self.call_ai_with_retry("llama-3.1-8b-instant", l1_prompt)
            
            if not l1 or not l1.get("is_umkm", False):
                log(f"⏭️ @{username}: AI rejected (L1)", "WARNING")
                return
            
            if l1.get("confidence", 0) < 50:
                log(f"⏭️ @{username}: Low confidence ({l1.get('confidence')})", "WARNING")
                return
            
            # --- 5. AI VERIFICATION (Layer 2: Deep) ---
            l2_prompt = f"""Deep analysis of UMKM seller indicators for @{username}.
Bio: "{bio}"
Display Name: "{display_name}"

Look for:
1. Contact information (WA/phone, email, DM instructions)
2. Shop references (Shopee, Tokopedia, offline store)
3. Product/service mentions
4. Location/city mentions in Indonesia
5. Business keywords (reseller, grosir, cod, order, pesan)

Return JSON: {{"has_contact": true/false, "has_shop": true/false, "has_products": true/false, 
"has_location": true/false, "score": 0-100, "is_valid_seller": true/false}}"""

            l2 = await self.call_ai_with_retry("qwen/qwen3.6-27b", l2_prompt)
            
            if not l2 or not l2.get("is_valid_seller", False):
                log(f"⏭️ @{username}: AI rejected (L2)", "WARNING")
                return
            
            # --- 6. FINAL DECISION ---
            final_score = (l1.get("confidence", 0) + l2.get("score", 0)) / 2
            
            # --- 7. SAVE TO DATABASE ---
            data = {
                "username": username,
                "display_name": display_name or username,
                "bio": bio,
                "followers_count": followers,
                "category": category,
                "city": city,
                "province": "Indonesia",
                "platform": "tiktok",
                "potential_score": int(final_score),
                "tiktok_url": f"https://www.tiktok.com/@{username}",
                "last_scraped": datetime.now().isoformat()
            }
            
            try:
                response = requests.post(
                    f"{SUPABASE_URL}/rest/v1/sellers",
                    headers={
                        "apikey": SUPABASE_KEY,
                        "Authorization": f"Bearer {SUPABASE_KEY}",
                        "Content-Type": "application/json",
                        "Prefer": "resolution=merge-duplicates,return=minimal"
                    },
                    json=data,
                    timeout=10
                )
                if response.status_code in [200, 201]:
                    self.total_profiles += 1
                    log(f"✅ @{username} | {followers:,} f | Score: {final_score:.0f}", "SUCCESS")
                else:
                    log(f"❌ DB Error @{username}: {response.status_code}", "ERROR")
            except Exception as e:
                log(f"❌ DB Error @{username}: {e}", "ERROR")
                
        except Exception as e:
            self.total_errors += 1
            log(f"❌ Error @{username}: {str(e)[:100]}", "ERROR")

    async def scroll_infinite(self, page, keyword):
        """Infinite scroll sampai ujung"""
        found_users = set()
        log(f"🔍 Searching: {keyword}", "INFO")
        
        try:
            await page.goto(
                f"https://www.tiktok.com/search/user?q={urllib.parse.quote(keyword)}",
                wait_until="domcontentloaded",
                timeout=30000
            )
            await asyncio.sleep(random.uniform(2, 3))
            
            last_height = 0
            same_count = 0
            scroll_count = 0
            max_no_change = 10
            
            while same_count < max_no_change:
                await page.evaluate("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})")
                await asyncio.sleep(random.uniform(1.5, 3.0))
                
                new_height = await page.evaluate("document.body.scrollHeight")
                
                if new_height == last_height:
                    same_count += 1
                else:
                    same_count = 0
                    last_height = new_height
                
                scroll_count += 1
                
                if scroll_count % 3 == 0:
                    anchors = await page.query_selector_all('a[href*="/@"]')
                    for a in anchors:
                        href = await a.get_attribute("href")
                        if href:
                            match = re.search(r'@([\w._-]+)', href)
                            if match:
                                found_users.add(match.group(1).lower())
                    
                    log(f"📊 Found {len(found_users)} users so far", "INFO")
            
            # Final extraction
            anchors = await page.query_selector_all('a[href*="/@"]')
            for a in anchors:
                href = await a.get_attribute("href")
                if href:
                    match = re.search(r'@([\w._-]+)', href)
                    if match:
                        found_users.add(match.group(1).lower())
            
            log(f"✅ Found {len(found_users)} unique users for '{keyword}'", "SUCCESS")
            return list(found_users)
            
        except Exception as e:
            log(f"❌ Search error: {str(e)[:100]}", "ERROR")
            return []

    async def generate_variations(self, category, city):
        """Generate keyword variations"""
        variations = [
            f"{category} {city}",
            f"{category} murah {city}",
            f"jual {category} {city}",
            f"beli {category} {city}",
            f"toko {category} {city}",
            f"reseller {category} {city}",
            f"grosir {category} {city}",
            f"cod {category} {city}",
            f"{category} lokal {city}",
            f"produk {category} {city}"
        ]
        return variations

    async def run_autonomous(self, max_hours=5):
        """Main loop dengan batas waktu 5 jam"""
        log("="*60, "INFO")
        log("🚀 SMART TIKTOK SCRAPER STARTED", "SUCCESS")
        log(f"📊 Categories: {len(CATEGORIES)}", "INFO")
        log(f"📍 Cities: {len(PRIORITY_REGIONS)}", "INFO")
        log(f"⏰ Max runtime: {max_hours} hours", "INFO")
        log("="*60, "INFO")
        
        start_time = datetime.now()
        end_time = start_time + timedelta(hours=max_hours)  # 🔥 Sekarang timedelta terdefinisi
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage']
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={'width': 1280, 'height': 720}
            )
            page = await context.new_page()
            
            session_count = 0
            
            while datetime.now() < end_time:
                session_count += 1
                log(f"📊 SESSION {session_count} START", "SUCCESS")
                
                random.shuffle(CATEGORIES)
                random.shuffle(PRIORITY_REGIONS)
                
                for city in PRIORITY_REGIONS:
                    for category in CATEGORIES:
                        if datetime.now() >= end_time:
                            log("⏰ Time limit reached!", "WARNING")
                            break
                        
                        variations = await self.generate_variations(category, city)
                        random.shuffle(variations)
                        
                        for kw in variations[:3]:
                            if datetime.now() >= end_time:
                                break
                            
                            users = await self.scroll_infinite(page, kw)
                            
                            if users:
                                batch_size = 10
                                for i in range(0, len(users), batch_size):
                                    batch = users[i:i+batch_size]
                                    tasks = [
                                        self.process_account(page, user, category, city)
                                        for user in batch
                                    ]
                                    await asyncio.gather(*tasks)
                                    await asyncio.sleep(random.uniform(1, 3))
                            
                            await asyncio.sleep(random.uniform(5, 15))
                        
                        log(f"📊 Progress: {self.total_profiles} profiles, {self.total_errors} errors", "INFO")
                        
                        # Update status ke Supabase
                        try:
                            requests.patch(
                                f"{SUPABASE_URL}/rest/v1/system_status?id=eq.worker_main",
                                headers={
                                    "apikey": SUPABASE_KEY,
                                    "Authorization": f"Bearer {SUPABASE_KEY}",
                                    "Content-Type": "application/json"
                                },
                                json={
                                    "last_seen": datetime.now().isoformat(),
                                    "status": "online",
                                    "profiles_saved": self.total_profiles,
                                    "errors": self.total_errors,
                                    "session": session_count
                                },
                                timeout=5
                            )
                        except:
                            pass
                
                log(f"📊 SESSION {session_count} COMPLETE", "SUCCESS")
                log(f"📈 Total profiles: {self.total_profiles}", "SUCCESS")
                log(f"📈 Total errors: {self.total_errors}", "WARNING")
                
                if datetime.now() < end_time:
                    log("⏳ Pausing for 5 minutes...", "WARNING")
                    await asyncio.sleep(300)
            
            elapsed = (datetime.now() - start_time).total_seconds() / 60
            log("="*60, "INFO")
            log(f"🏁 SCRAPER FINISHED", "SUCCESS")
            log(f"⏱️  Duration: {elapsed:.0f} minutes", "INFO")
            log(f"📈 Total profiles saved: {self.total_profiles}", "SUCCESS")
            log(f"📈 Total errors: {self.total_errors}", "WARNING")
            log("="*60, "INFO")
            
            await browser.close()

# ============================================
# RUN
# ============================================
if __name__ == "__main__":
    scraper = SmartTikTokScraper()
    await scraper.run_autonomous(max_hours=5)