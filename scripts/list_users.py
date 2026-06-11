import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import init_db, get_all_users

init_db()
users = get_all_users()

if not users:
    print("No users found.")
    sys.exit(0)

print(f"\n{'ID':<5} {'Email':<35} {'Role':<8} {'PRPT Username':<25} {'Created'}")
print("-" * 95)
for u in users:
    print(f"{u['id']:<5} {u['email']:<35} {u['role']:<8} {(u['prpt_username'] or '—'):<25} {u['created_at']}")
print()
