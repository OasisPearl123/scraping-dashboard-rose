import os
import subprocess
from pathlib import Path

def run():
    print("\n" + "="*50)
    print("🚀 ACQUISITION-AI UNIFIED CONTROLLER")
    print("="*50)

    root = Path(__file__).parent
    scraper_dir = root / 'scraper'
    venv_python = scraper_dir / 'venv' / 'bin' / 'python3'

    # 1. CLEANUP REDUNDANT FILES
    print("\n🧹 Cleaning up redundant files...")
    redundant = [
        scraper_dir / 'main.py',
        scraper_dir / 'app.py',
        scraper_dir / 'supabase_client.py',
        scraper_dir / 'check_db.py',
        scraper_dir / 'migrate.py',
        scraper_dir / 'local_run.bat',
        root / 'supabase' / 'run_migrations.py'
    ]
    for f in redundant:
        if f.exists():
            f.unlink()
            print(f"   - Removed: {f.name}")

    # 2. GITHUB PUSH (Automated)
    print("\n📤 Pushing unified updates to GitHub...")
    try:
        subprocess.run(["git", "add", "."], cwd=root)
        subprocess.run(["git", "commit", "-m", "Unified Engine & Auto-Migration Update"], cwd=root)
        subprocess.run(["git", "push", "origin", "main"], cwd=root)
        print("   ✅ GitHub repository synced.")
    except Exception as e:
        print(f"   ❌ GitHub Push failed: {e}")

    # 3. START UNIFIED ENGINE
    print("\n🤖 LAUNCHING UNIFIED ENGINE...")
    print("   (Migrations, Heartbeat, Dashboard Queue, and WhatsApp)")

    if not venv_python.exists():
        print(f"❌ Error: Python Environment missing at {venv_python}")
        return

    env = os.environ.copy()
    env['PYTHONPATH'] = str(scraper_dir)
    env['PYTHONUNBUFFERED'] = '1'

    try:
        proc = subprocess.Popen(
            [str(venv_python), str(scraper_dir / 'engine.py')],
            cwd=scraper_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        print("\n✨ SYSTEM ONLINE!")
        print("💡 Monitor the dashboard at the provided frontend URL.\n")

        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                print(line, end='', flush=True)

        proc.wait()
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
        proc.terminate()

if __name__ == "__main__":
    run()
