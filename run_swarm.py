import multiprocessing
import os
import time
import subprocess
import sys
import signal
import psutil # Menggunakan psutil untuk pembersihan mendalam jika tersedia

def run_worker(index, total):
    """Fungsi untuk menjalankan satu worker dalam process terpisah"""
    env = os.environ.copy()
    env["WORKER_INDEX"] = str(index)
    env["WORKER_TOTAL"] = str(total)
    env["PYTHONUNBUFFERED"] = "1"

    try:
        # Menjalankan engine_fast.py
        subprocess.run([sys.executable, "scraper/engine_fast.py"], env=env)
    except KeyboardInterrupt:
        pass # Akan ditangani oleh parent

def cleanup_all():
    """Fungsi sapu bersih: Mematikan SEMUA proses terkait scraper dan browser"""
    print("\n🧹 Melakukan pembersihan total (Sapu Bersih)...")

    # 1. Ambil PID saat ini untuk menghindari bunuh diri sebelum waktunya
    parent_pid = os.getpid()

    try:
        # 2. Cari semua proses chromium dan playwright yang dijalankan oleh user saat ini
        # Kita menggunakan shell command untuk memastikan proses zombie/orphaned mati
        # Menggunakan 'pkill' atau 'kill' dengan grep yang lebih spesifik
        user = os.getlogin()
        cmd = f"ps -u {user} -o pid,command | grep -E 'chrome-headless-shell|playwright|engine_fast.py' | grep -v grep | awk '{{print $1}}' | xargs kill -9"
        subprocess.run(cmd, shell=True, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)

        # 3. Matikan semua process group
        os.killpg(os.getpgrp(), signal.SIGKILL)
    except Exception as e:
        # Fallback jika os.killpg gagal
        print(f"⚠️ Pembersihan standar selesai. Sistem mungkin butuh waktu beberapa detik untuk melepas memori.")

if __name__ == "__main__":
    TOTAL_WORKERS = 4
    processes = []

    print("==================================================")
    print(f"🚀 SWARM MODE v8.4: Menjalankan {TOTAL_WORKERS} Worker Lokal")
    print("SISTEM KEAMANAN TERMINASI: AKTIF")
    print("Tekan Ctrl+C untuk MATI TOTAL (Bersih sampai browser)")
    print("==================================================")

    def signal_handler(sig, frame):
        print("\n🛑 Signal berhenti diterima. Mematikan seluruh pasukan...")
        for p in processes:
            try:
                p.terminate()
            except:
                pass
        cleanup_all()
        sys.exit(0)

    # Tangkap signal Ctrl+C (SIGINT) dan SIGTERM
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        for i in range(TOTAL_WORKERS):
            p = multiprocessing.Process(target=run_worker, args=(i, TOTAL_WORKERS))
            p.start()
            processes.append(p)
            time.sleep(8) # Jeda awal agar IP tidak kaget

        print("📡 Seluruh worker aktif. Memantau aliran data...")
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        signal_handler(None, None)
