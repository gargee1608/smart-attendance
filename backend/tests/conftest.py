import os
import shutil
from pathlib import Path

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient

# Rewrite connection settings before the application is imported.
_ORIGIN_MIGRATION = os.environ.get(
    "MIGRATION_DATABASE_URL",
    "postgresql+psycopg://postgres:devpostgres@postgres:5432/smartattendance",
)
_ORIGIN_APP = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://smartattendance_app:devappdb@postgres:5432/smartattendance",
)
_ORIGIN_ADMIN = os.environ.get(
    "ADMIN_DATABASE_URL",
    "postgresql+psycopg://smartattendance_admin:devadmindb@postgres:5432/smartattendance",
)


def _swap_database(url: str, database: str) -> str:
    base, _, query = url.partition("?")
    head, _old = base.rsplit("/", 1)
    swapped = f"{head}/{database}"
    return f"{swapped}?{query}" if query else swapped


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


os.environ["DATABASE_URL"] = _swap_database(_ORIGIN_APP, "smartattendance_test")
os.environ["ADMIN_DATABASE_URL"] = _swap_database(_ORIGIN_ADMIN, "smartattendance_test")
os.environ["MIGRATION_DATABASE_URL"] = _swap_database(_ORIGIN_MIGRATION, "smartattendance_test")
redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
os.environ["REDIS_URL"] = redis_url.rsplit("/", 1)[0] + "/15"
os.environ["STORAGE_ROOT"] = "/tmp/sa-test-storage"
os.environ["SMARTATTENDANCE_ENV"] = "test"
os.environ["JWT_SECRET"] = "test-jwt-secret-value-0123456789"
os.environ["INITIAL_ADMIN_PASSWORD"] = "not-used-by-tests-123"

_maintenance = _swap_database(_ORIGIN_MIGRATION, "postgres")
with psycopg.connect(_libpq(_maintenance), autocommit=True) as _conn:
    _conn.execute("DROP DATABASE IF EXISTS smartattendance_test WITH (FORCE)")
    _conn.execute("CREATE DATABASE smartattendance_test")

from app.cli import migrate  # noqa: E402
from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
migrate()

from app.main import app  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services.face_client import get_face_client  # noqa: E402
from tests.fake_face import FakeFace  # noqa: E402


def _truncate() -> None:
    with psycopg.connect(_libpq(os.environ["MIGRATION_DATABASE_URL"]), autocommit=True) as conn:
        conn.execute(
            """
            TRUNCATE TABLE
              audit_logs, notifications, ai_comments, coach_comments, performance_metrics,
              import_jobs, attendance_records, leave_requests, holidays, attendance_policies,
              unknown_observations, unknown_groups, face_embeddings, face_images,
              person_batches, class_subject_teachers, batches, subjects, school_classes,
              academic_sessions, sports, cameras, api_keys, persons, users, org_counters,
              organizations
            RESTART IDENTITY CASCADE
            """
        )
        conn.execute("ALTER SEQUENCE org_code_seq RESTART WITH 1")
    shutil.rmtree("/tmp/sa-test-storage", ignore_errors=True)
    Path("/tmp/sa-test-storage").mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def clean_database():
    _truncate()
    yield


@pytest.fixture
async def client():
    app.dependency_overrides[get_face_client] = lambda: FakeFace()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


def auth(token: str, org_id: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    if org_id:
        headers["X-Organization-Id"] = org_id
    return headers


async def login(client: AsyncClient, email: str, password: str) -> str:
    response = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def insert_superadmin(email: str = "root@example.com", password: str = "super-admin-password") -> None:
    with psycopg.connect(_libpq(os.environ["MIGRATION_DATABASE_URL"]), autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO users (email, password_hash, full_name, role, status)
            VALUES (%s, %s, 'Root Admin', 'super_admin', 'active')
            """,
            (email, hash_password(password)),
        )


async def super_token(client: AsyncClient) -> str:
    insert_superadmin()
    return await login(client, "root@example.com", "super-admin-password")


async def make_org(client: AsyncClient, token: str, name: str, org_type: str = "sports_academy") -> dict:
    response = await client.post(
        "/api/orgs",
        json={"name": name, "org_type": org_type, "contact_person": "Office", "email": f"{name}@example.com"},
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def make_user(client, token, org_id, email, role="org_admin", password="org-admin-password"):
    response = await client.post(
        "/api/users",
        json={"full_name": email.split("@")[0], "email": email, "password": password, "role": role},
        headers=auth(token, org_id),
    )
    assert response.status_code == 200, response.text
    return response.json()
