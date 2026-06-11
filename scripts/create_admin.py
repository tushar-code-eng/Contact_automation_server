import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import init_db, create_user, get_user_by_email
from auth import hash_password

init_db()

print("=== Create Admin User ===\n")

email = input("Email: ").strip()
if not email:
    print("Email cannot be empty.")
    sys.exit(1)

if get_user_by_email(email):
    print(f"A user with email '{email}' already exists.")
    sys.exit(1)

password = input("Password: ").strip()
if len(password) < 6:
    print("Password must be at least 6 characters.")
    sys.exit(1)

confirm = input("Confirm password: ").strip()
if password != confirm:
    print("Passwords do not match.")
    sys.exit(1)

uid = create_user(email, hash_password(password), role="admin")
print(f"\n✅ Admin created successfully! (id: {uid}, email: {email})")
