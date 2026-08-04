import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import init_db, get_user_by_email, update_user_password
from auth import hash_password

init_db()

print("=== Reset User Password ===\n")

email = input("Email: ").strip()
user = get_user_by_email(email)
if not user:
    print(f"No user found with email '{email}'.")
    sys.exit(1)

password = input("New password: ").strip()
if len(password) < 6:
    print("Password must be at least 6 characters.")
    sys.exit(1)

confirm = input("Confirm password: ").strip()
if password != confirm:
    print("Passwords do not match.")
    sys.exit(1)

update_user_password(user["id"], hash_password(password))
print(f"\n✅ Password reset successfully for {email}.")
