import asyncio
from playwright.async_api import async_playwright
from pathlib import Path

async def run():
    async with async_playwright() as p:
        # Jalankan browser terlihat agar Anda bisa login manual
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        print("\n==================================================")
        print("🚀 PEMBUATAN SESI LOGIN (MASTER KEY)")
        print("1. Silakan login ke TikTok secara MANUAL di jendela yang muncul.")
        print("2. Gunakan metode apapun (Google/Email/QR).")
        print("3. Setelah masuk ke Beranda TikTok, jangan tutup tabnya.")
        print("4. KEMBALI KE SINI DAN TEKAN ENTER.")
        print("==================================================\n")

        await page.goto("https://www.tiktok.com/login")

        # Menunggu user menekan enter di terminal setelah login selesai
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input, "Tekan ENTER di sini jika sudah berhasil login di browser...")

        # Simpan status autentikasi (Cookies, LocalStorage, dll)
        await context.storage_state(path="master_auth_state.json")
        print("\n✅ SESI BERHASIL DISIMPAN ke 'master_auth_state.json'!")
        print("Sekarang Anda bisa menjalankan swarm tanpa perlu login lagi.")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
