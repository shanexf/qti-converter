"""
Run this once after deploying, to create your admin account and any invited
teacher's free-plan account. Public signup only ever creates "standard"
accounts, so special roles are created here instead.

Usage (locally):
    python create_account.py

Usage (on Railway, after deploy):
    railway run python create_account.py

It will prompt you interactively — nothing is hardcoded, so you can also
use this later to add more free-plan teachers.
"""
import getpass
import db
import auth

db.init_db()

print("Create an account\n" + "-" * 20)
email = input("Email: ").strip()

if db.get_user_by_email(email):
    print(f"An account with {email} already exists — nothing created.")
    raise SystemExit(0)

password = getpass.getpass("Password: ")
confirm = getpass.getpass("Confirm password: ")
if password != confirm:
    print("Passwords didn't match — nothing created.")
    raise SystemExit(1)
if len(password) < 8:
    print("Password should be at least 8 characters — nothing created.")
    raise SystemExit(1)

print("\nRole options:")
print("  1) admin        — unlimited, no charges ever")
print("  2) free_monthly — fixed number of free questions every month, then falls back to purchased credits")
print("  3) standard      — normal public-signup account (free trial credits, then must buy packs)")
choice = input("Choose 1, 2, or 3: ").strip()

role_map = {"1": "admin", "2": "free_monthly", "3": "standard"}
role = role_map.get(choice)
if not role:
    print("Not a valid choice — nothing created.")
    raise SystemExit(1)

monthly_quota_limit = 0
if role == "free_monthly":
    raw = input("Monthly free question quota (e.g. 100): ").strip()
    monthly_quota_limit = int(raw) if raw.isdigit() else 100

pw_hash = auth.hash_password(password)
user_id = db.create_user(email, pw_hash, role=role, monthly_quota_limit=monthly_quota_limit)

print(f"\nCreated {role} account for {email} (id={user_id}).")
if role == "free_monthly":
    print(f"They get {monthly_quota_limit} free questions per calendar month.")
print("They can log in on the site with this email and password right away.")
