import argparse

import psycopg

from app.config import get_settings
from app.security import hash_password


def libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1).replace("postgresql+asyncpg://", "postgresql://", 1)


def sync_passwords() -> None:
    settings = get_settings()
    with psycopg.connect(libpq(settings.migration_database_url), autocommit=True) as conn:
        for role, password in (
            ("smartattendance_app", settings.app_db_password),
            ("smartattendance_admin", settings.admin_db_password),
        ):
            escaped = password.replace("'", "''")
            conn.execute(f"ALTER ROLE {role} PASSWORD '{escaped}'")


def migrate() -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    settings = get_settings()
    settings.assert_production_safe()
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(cfg, "head")
    sync_passwords()
    print("migrations applied")


def ensure_admin() -> None:
    settings = get_settings()
    settings.assert_production_safe()
    email = settings.initial_admin_email.strip().lower()
    with psycopg.connect(libpq(settings.migration_database_url), autocommit=True) as conn:
        row = conn.execute("SELECT id FROM users WHERE lower(email) = %s", (email,)).fetchone()
        if row:
            print(f"super admin already exists: {email}")
            return
        conn.execute(
            """
            INSERT INTO users (email, password_hash, full_name, role, status)
            VALUES (%s, %s, %s, 'super_admin', 'active')
            """,
            (email, hash_password(settings.initial_admin_password), "Super Admin"),
        )
    print(f"created super admin {email}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Smart Attendance maintenance")
    parser.add_argument("command", choices=["migrate", "ensure-admin", "migrate-and-ensure-admin"])
    args = parser.parse_args()
    if args.command in {"migrate", "migrate-and-ensure-admin"}:
        migrate()
    if args.command in {"ensure-admin", "migrate-and-ensure-admin"}:
        ensure_admin()


if __name__ == "__main__":
    main()
