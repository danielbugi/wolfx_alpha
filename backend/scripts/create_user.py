# File: backend/scripts/create_user.py
"""
Emergency-capable CLI for dashboard_users: bootstrap the first Owner account, or recover access if you're
locked out. There is no self-signup or "forgot password" flow in the web app itself (by design — only the
Owner and one Collaborator are meant to exist), so this script — run directly on the machine with DB
access — is the one recovery path. It never takes a password as a CLI argument (that would land in shell
history / `ps`); it always prompts.

Usage (from backend/):
    python scripts/create_user.py --email you@example.com --role owner
        Creates a new account. Refuses if the email already exists.

    python scripts/create_user.py --email you@example.com --reset-password
        EMERGENCY: locked out (forgot the password, or the 2FA email never arrives) — resets that
        account's password, reactivates it if it had been deactivated, and signs out every existing
        session for it (so a compromised old session can't linger after a reset).
"""
import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ itself, so `main`/`auth` import like the app does

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from auth.security import hash_password  # noqa: E402
from auth.store import AuthStore  # noqa: E402
from main import get_database_connection  # noqa: E402


def _prompt_new_password() -> str:
    password = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match.", file=sys.stderr)
        sys.exit(1)
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)
    return password


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a dashboard user, or reset one's password in an emergency.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", choices=["owner", "collaborator"], help="Required when creating a new account.")
    parser.add_argument("--reset-password", action="store_true", help="Reset an EXISTING account's password instead of creating a new one.")
    args = parser.parse_args()

    store = AuthStore(get_database_connection)
    existing = store.get_user_by_email(args.email)

    if args.reset_password:
        if existing is None:
            print(f"No account with email {args.email!r} exists — nothing to reset.", file=sys.stderr)
            sys.exit(1)
        password = _prompt_new_password()
        store.set_password(existing["id"], hash_password(password))
        if not existing["is_active"]:
            store.set_user_active(existing["id"], True)
        store.revoke_all_sessions_for_user(existing["id"])
        store.audit("user_created", user_id=existing["id"], detail="password_reset_via_emergency_script")
        print(f"Password reset for {args.email!r} (role: {existing['role']}). All existing sessions were signed out.")
        return

    if existing is not None:
        print(f"A user with email {args.email!r} already exists. Use --reset-password if you're locked out.", file=sys.stderr)
        sys.exit(1)
    if not args.role:
        print("--role is required when creating a new account (owner or collaborator).", file=sys.stderr)
        sys.exit(1)

    password = _prompt_new_password()
    user_id = store.create_user(args.email, hash_password(password), args.role)
    store.audit("user_created", user_id=user_id, detail=f"bootstrap:{args.role}")
    print(f"Created {args.role} user #{user_id} ({args.email}).")


if __name__ == "__main__":
    main()
