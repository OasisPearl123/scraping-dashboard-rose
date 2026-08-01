import multiprocessing
import os
import time
import subprocess
import sys

def run_worker(index, total):
    """Fungsi untuk menjalankan satu worker dalam process terpisah"""
    env = os.environ.copy()
    env["WORKER_INDEX"] = str(index)
    env["WORKER_TOTAL"] = str(total)
    env["PYTHONUNBUFFERED"] = "1"

    print(f"启动 Worker {index}/{total-1}...")
    # Menjalankan engine_fast.py menggunakan interpreter python yang sama
    subprocess.run([sys.executable, "scraper/engine_fast.py"], env=env)

if __name__ == "__main__":
    TOTAL_WORKERS = 4
    processes = []

    print("==================================================")
    print(f"🚀 SWARM MODE: Menjalankan {TOTAL_WORKERS} Worker Lokal")
    print("==================================================")

    try:
        # Membuat dan memulai 4 proses worker secara paralel
        for i in range(TOTAL_WORKERS):
            p = multiprocessing.Process(target=run_worker, args=(i, TOTAL_WORKERS))
            p.start()
            processes.append(p)
            # Jeda 5 detik antar worker agar tidak membebani browser sekaligus di awal
            time.sleep(5)

        # Menunggu semua proses selesai (loop selamanya)
        for p in processes:
            p.join()

    except KeyboardInterrupt:
        print("\n🛑 Memberhentikan semua worker...")
        for p in processes:
            p.terminate()
        print("✅ Semua worker telah berhenti.")
