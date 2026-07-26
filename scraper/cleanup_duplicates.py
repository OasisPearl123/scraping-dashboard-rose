import os
import psycopg2
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime

def log(msg, type="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {type:7} | {msg}", flush=True)

def remove_duplicates():
    # Load environment variables
    base_dir = Path(__file__).parent.parent
    load_dotenv(base_dir / 'frontend' / '.env')

    supabase_url = os.environ.get('VITE_SUPABASE_URL')
    db_password = os.environ.get('PASSWORD_SUPABASE')

    if not supabase_url or not db_password:
        log("Missing DB credentials in .env", "ERROR")
        return

    project_ref = supabase_url.split('//')[1].split('.')[0]
    db_host = f"db.{project_ref}.supabase.co"

    try:
        log(f"Connecting to database for duplicate cleanup: {db_host}...")
        conn = psycopg2.connect(
            host=db_host,
            database='postgres',
            user='postgres',
            password=db_password,
            port='5432'
        )
        conn.autocommit = True
        cur = conn.cursor()

        # Define tables and their unique identifying columns
        cleanup_configs = [
            {'table': 'sellers', 'unique_cols': ['username']},
            {'table': 'provinces', 'unique_cols': ['name']},
            {'table': 'cities', 'unique_cols': ['name', 'province_id']},
            {'table': 'profiles', 'unique_cols': ['username']},
            {'table': 'system_config', 'unique_cols': ['key']},
            {'table': 'search_queries', 'unique_cols': ['query']}
        ]

        for config in cleanup_configs:
            table = config['table']
            cols = ", ".join(config['unique_cols'])

            # SQL to delete duplicates keeping the one with the latest ctid (usually latest insert)
            # or we can use created_at if exists

            check_query = f"SELECT {cols}, COUNT(*) FROM {table} GROUP BY {', '.join(config['unique_cols'])} HAVING COUNT(*) > 1"
            cur.execute(check_query)
            dupes = cur.fetchall()

            if dupes:
                log(f"Found {len(dupes)} sets of duplicates in table '{table}'", "WARNING")

                delete_query = f"""
                    DELETE FROM {table} a
                    USING {table} b
                    WHERE a.ctid < b.ctid
                    AND {" AND ".join([f"a.{c} = b.{c}" for c in config['unique_cols']])}
                """
                cur.execute(delete_query)
                log(f"Cleaned up {cur.rowcount} duplicate rows from '{table}'", "SUCCESS")
            else:
                log(f"No duplicates found in table '{table}'", "INFO")

        cur.close()
        conn.close()
        log("Cleanup process completed successfully!", "SUCCESS")

    except Exception as e:
        log(f"Cleanup failed: {e}", "ERROR")

if __name__ == "__main__":
    remove_duplicates()
