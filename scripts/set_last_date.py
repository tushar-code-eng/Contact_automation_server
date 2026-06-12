import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import init_db, get_user_by_email

init_db()

print("=== Set Last Scrape Date ===\n")

email = input("User email: ").strip()
user = get_user_by_email(email)
if not user:
    print(f"No user found with email '{email}'.")
    sys.exit(1)

date = input("Last scrape date (e.g. 6/8/2026): ").strip()
if not date:
    print("Date cannot be empty.")
    sys.exit(1)

data_dir = os.path.join("data", str(user["id"]))
os.makedirs(data_dir, exist_ok=True)

path = os.path.join(data_dir, "last_date.json")
with open(path, "w") as f:
    json.dump({"last_end_date": date}, f)

print(f"\n✅ Last date set to {date} for {email} (user id: {user['id']})")
print(f"   File: {path}")
