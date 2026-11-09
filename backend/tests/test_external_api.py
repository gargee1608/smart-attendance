import pytest

from tests.conftest import auth, login, make_org, make_user, super_token


@pytest.mark.asyncio
async def test_api_key_is_tenant_scoped_rate_limited_and_can_delete_biometrics(client):
    token = await super_token(client)
    org_a = await make_org(client, token, "Api North")
    org_b = await make_org(client, token, "Api South")
    await make_user(client, token, org_a["id"], "admin@api-north.test")
    await make_user(client, token, org_b["id"], "admin@api-south.test")
    admin_a = await login(client, "admin@api-north.test", "org-admin-password")
    admin_b = await login(client, "admin@api-south.test", "org-admin-password")

    created = await client.post(
        "/api/api-keys",
        json={"name": "Gate", "rate_limit_per_minute": 30},
        headers=auth(admin_a),
    )
    assert created.status_code == 200, created.text
    key_a = created.json()["api_key"]
    other = await client.post(
        "/api/api-keys",
        json={"name": "Other", "rate_limit_per_minute": 20},
        headers=auth(admin_b),
    )
    key_b = other.json()["api_key"]

    person = await client.post(
        "/api/v1/persons",
        json={"full_name": "Api Student", "person_type": "student"},
        headers={"X-API-Key": key_a},
    )
    assert person.status_code == 200, person.text
    person_id = person.json()["id"]
    assert person.json()["enrollment_status"] == "draft"

    photos = await client.post(
        f"/api/v1/persons/{person_id}/photos",
        files=[("files", ("a.jpg", b"FACEID:api-student\n", "image/jpeg"))],
        headers={"X-API-Key": key_a},
    )
    assert photos.status_code == 200, photos.text
    await client.post(f"/api/persons/{person_id}/confirm-enrollment", headers=auth(admin_a))

    hidden = await client.get(f"/api/v1/persons/{person_id}", headers={"X-API-Key": key_b})
    assert hidden.status_code == 404

    known = await client.post(
        "/api/v1/recognition/identify",
        files={"file": ("a.jpg", b"FACEID:api-student\n", "image/jpeg")},
        headers={"X-API-Key": key_a},
    )
    assert known.status_code == 200, known.text
    assert known.json()["result"] == "known"
    assert known.json()["person_name"] == "Api Student"

    removed = await client.delete(f"/api/v1/persons/{person_id}", headers={"X-API-Key": key_a})
    assert removed.status_code == 200, removed.text
    assert removed.json()["biometrics_deleted"] is True
    assert removed.json()["embeddings_removed"] == 1

    forgotten = await client.post(
        "/api/v1/recognition/identify",
        files={"file": ("a.jpg", b"FACEID:api-student\n", "image/jpeg")},
        headers={"X-API-Key": key_a},
    )
    assert forgotten.json()["result"] == "unknown"
    assert forgotten.json()["person_name"] is None
    assert "Api Student" not in forgotten.text

    limited_key = await client.post(
        "/api/api-keys",
        json={"name": "Tight", "rate_limit_per_minute": 2},
        headers=auth(admin_a),
    )
    tight = limited_key.json()["api_key"]
    assert (await client.get("/api/v1/unknown", headers={"X-API-Key": tight})).status_code == 200
    assert (await client.get("/api/v1/unknown", headers={"X-API-Key": tight})).status_code == 200
    limited = await client.get("/api/v1/unknown", headers={"X-API-Key": tight})
    assert limited.status_code == 429
    assert limited.json()["detail"] == "Rate limit exceeded"


def test_download_script_does_not_use_insightface():
    from pathlib import Path

    script = Path("/app/scripts/download_models.sh").read_text().lower()
    assert "insightface" not in script
    assert "yunet" in script
    assert "sface" in script
    assert "minifasnet" in script
    assert "buffalo" not in script
