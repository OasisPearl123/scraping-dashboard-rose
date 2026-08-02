import asyncio
from playwright.async_api import async_playwright
from pathlib import Path

async def run():
    async with async_playwright() as p:
        # Gunakan browser terlihat (Non-Headless)
        browser = await p.chromium.launch(headless=False)

        # Gunakan konteks Mac asli agar terlihat seperti user biasa
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        )

        page = await context.new_page()

        print("\n" + "="*60)
        print("🚀 METODE LOGIN QR CODE (PALING AMAN & ANTI-BLOKIR)")
        print("="*60)
        print("1. Jendela browser akan terbuka di halaman Login TikTok.")
        print("2. Pilih 'Use QR code'.")
        print("3. Buka aplikasi TikTok di HP Anda.")
        print("4. Pergi ke Profil -> Menu (Garis 3) -> My QR Code -> Klik ikon Scan.")
        print("5. Scan kode yang ada di layar komputer Anda.")
        print("6. Setelah masuk ke Beranda TikTok di komputer, kembali ke sini.")
        print("="*60 + "\n")

        await page.goto("https://www.tiktok.com/login")

        # Menunggu interaksi user untuk konfirmasi final
        input("---> JIKA SUDAH BERHASIL LOGIN DI BROWSER, TEKAN [ENTER] DI SINI... <---")

        # Simpan status autentikasi (Cookies, Token, Session)
        await context.storage_state(path="master_auth_state.json")

        print("\n✅ MASTER AUTH BERHASIL DISIMPAN!")
        print("Langkah selanjutnya:")
        print("1. Upload file 'master_auth_state.json' ke Google Colab.")
        print("2. Jalankan Scraper di Colab.")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())
