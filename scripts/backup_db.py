import os
import sys
import glob
import shutil
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

DB_PATH    = os.getenv("DB_PATH", "db.sqlite")
BACKUP_DIR = "backups"
KEEP_DAYS  = 10

os.makedirs(BACKUP_DIR, exist_ok=True)

if not os.path.exists(DB_PATH):
    print(f"DB not found at {DB_PATH}")
    sys.exit(1)

ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_path = os.path.join(BACKUP_DIR, f"db_{ts}.sqlite")
shutil.copy2(DB_PATH, backup_path)
print(f"✅ Backup saved: {backup_path}")

# Remove backups older than KEEP_DAYS
all_backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "db_*.sqlite")))
if len(all_backups) > KEEP_DAYS:
    for old in all_backups[:-KEEP_DAYS]:
        os.remove(old)
        print(f"🗑️  Removed old backup: {old}")
