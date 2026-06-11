import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db import init_db, get_user_by_email, delete_user

print("=== Delete User ===\n")

email = input("Email of user to delete: ").strip()
if not email:
    print("Email cannot be empty.")
    sys.exit(1)

init_db()
user = get_user_by_email(email)
if not user:
    print(f"No user found with email '{email}'.")
    sys.exit(1)

print(f"\nFound: id={user['id']}, email={user['email']}, role={user['role']}")
confirm = input("Are you sure you want to delete this user? (yes/no): ").strip().lower()
if confirm != "yes":
    print("Cancelled.")
    sys.exit(0)

delete_user(user["id"])
print(f"\n✅ User '{email}' deleted successfully.")
