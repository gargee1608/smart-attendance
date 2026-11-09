import psycopg
import pytest

from tests.conftest import auth, login, make_org, make_user, super_token


def _libpq(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _app_url() -> str:
    import os

    return _libpq(os.environ["DATABASE_URL"])


async def _academy(client):
    token = await super_token(client)
    org_a = await make_org(client, token, "North Academy")
    org_b = await make_org(client, token, "South Academy")
    await make_user(client, token, org_a["id"], "aisha@north.test")
    await make_user(client, token, org_b["id"], "ben@south.test")
    token_a = await login(client, "aisha@north.test", "org-admin-password")
    token_b = await login(client, "ben@south.test", "org-admin-password")
    person = await client.post(
        "/api/persons",
        json={"person_type": "student", "full_name": "Alice Isolated", "batch_ids": []},
        headers=auth(token_a),
    )
    assert person.status_code == 200, person.text
    person_id = person.json()["id"]
    photos = await client.post(
        f"/api/persons/{person_id}/photos",
        files=[("files", ("alice.jpg", b"FACEID:alice\nsecret-face-bytes", "image/jpeg"))],
        headers=auth(token_a),
    )
    assert photos.status_code == 200, photos.text
    assert photos.json()["accepted"], photos.text
    confirmed = await client.post(f"/api/persons/{person_id}/confirm-enrollment", headers=auth(token_a))
    assert confirmed.status_code == 200, confirmed.text
    return {
        "org_a": org_a,
        "org_b": org_b,
        "token_a": token_a,
        "token_b": token_b,
        "person_id": person_id,
        "image_id": photos.json()["accepted"][0]["image_id"],
    }


@pytest.mark.asyncio
async def test_api_and_rls_keep_organizations_apart(client):
    world = await _academy(client)
    listed = await client.get("/api/persons", headers=auth(world["token_b"]))
    assert listed.status_code == 200
    assert listed.json()["items"] == []
    assert "Alice Isolated" not in listed.text

    missing = await client.get(f"/api/persons/{world['person_id']}", headers=auth(world["token_b"]))
    assert missing.status_code == 404

    photo = await client.get(
        f"/api/persons/{world['person_id']}/photos/{world['image_id']}",
        headers=auth(world["token_b"]),
    )
    assert photo.status_code == 404

    forged = await client.get(
        "/api/persons",
        headers=auth(world["token_b"], world["org_a"]["id"]),
    )
    assert forged.status_code == 403

    check = await client.post(
        "/api/attendance/check-in",
        files={"file": ("frame.jpg", b"FACEID:alice\n", "image/jpeg")},
        headers=auth(world["token_b"]),
    )
    assert check.status_code == 200, check.text
    body = check.json()
    assert body["result"] == "unknown"
    assert body["person_name"] is None
    assert body["person_id"] is None
    assert "Alice" not in check.text

    cross = await client.post(
        "/api/attendance/manual",
        json={"person_id": world["person_id"], "on_date": "2026-01-05", "status": "present"},
        headers=auth(world["token_b"]),
    )
    assert cross.status_code == 404

    visible = await client.get("/api/persons", headers=auth(world["token_a"]))
    assert len(visible.json()["items"]) == 1
    assert visible.json()["items"][0]["full_name"] == "Alice Isolated"
    assert "embedding_sealed" not in visible.json()["items"][0]

    with psycopg.connect(_app_url()) as conn:
        role = conn.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        assert role == (False, False)
        with pytest.raises(psycopg.Error):
            conn.execute("SET ROLE smartattendance_admin")

    with psycopg.connect(_app_url()) as conn:
        conn.execute("SELECT set_config('app.bypass_rls', 'on', true)")
        conn.execute("SELECT set_config('app.current_org_id', %s, true)", (world["org_b"]["id"],))
        assert conn.execute("SELECT count(*) FROM persons").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM face_embeddings").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM attendance_records").fetchone()[0] == 0
        own_images = conn.execute("SELECT storage_path, person_id FROM face_images").fetchall()
        assert all(row[1] is None for row in own_images)
        assert all(world["person_id"] not in (row[0] or "") for row in own_images)
        with pytest.raises(psycopg.Error):
            conn.execute(
                """
                INSERT INTO attendance_records (org_id, person_id, attendance_date, status, method)
                VALUES (%s, %s, %s, 'present', 'manual')
                """,
                (world["org_b"]["id"], world["person_id"], "2026-01-05"),
            )

    with psycopg.connect(_app_url()) as conn:
        conn.execute("SELECT set_config('app.current_org_id', %s, true)", (world["org_a"]["id"],))
        assert conn.execute("SELECT count(*) FROM persons").fetchone()[0] == 1
        path, sealed = conn.execute(
            """
            SELECT i.storage_path, e.embedding_sealed
            FROM face_images i
            JOIN face_embeddings e ON e.face_image_id = i.id
            """
        ).fetchone()
        from pathlib import Path

        blob = (Path("/tmp/sa-test-storage") / path).read_bytes()
        assert blob.startswith(b"SAENC1")
        assert b"FACEID:alice" not in blob
        from app.storage import decrypt_bytes, open_embedding

        assert decrypt_bytes(blob).startswith(b"FACEID:alice")
        opened = open_embedding(bytes(sealed))
        assert len(opened) == 128
