import json

import pytest

from app.services.recognition import decide_match
from app.services.unknowns import dbscan
from app.services.vectors import deterministic_vector, vector_at_cosine
from tests.conftest import auth, login, make_org, make_user, super_token


def test_threshold_never_returns_a_name_when_uncertain():
    ranked = [("p1", "Alice Isolated", "STU-0001", 0.20)]
    low = decide_match(ranked, threshold=0.363, margin=0.05)
    assert low.result == "unknown"
    assert low.person_name is None
    assert low.person_id is None
    assert low.confidence == pytest.approx(0.20)

    known = decide_match([("p1", "Alice Isolated", "STU-0001", 0.91)], 0.363, 0.05)
    assert known.result == "known"
    assert known.person_name == "Alice Isolated"
    assert known.confidence == pytest.approx(0.91)

    ambiguous = decide_match(
        [("p1", "Alice Isolated", "STU-0001", 0.91), ("p2", "Bob", "STU-0002", 0.90)],
        0.363,
        0.05,
    )
    assert ambiguous.result == "unknown"
    assert ambiguous.reason == "ambiguous"
    assert ambiguous.person_name is None
    assert ambiguous.person_id is None


def test_dbscan_groups_nearby_vectors_only():
    base = deterministic_vector("cluster")
    near = vector_at_cosine(base, 0.98, "cluster-near")
    far = deterministic_vector("other-person")
    labels = dbscan([base, near, far], eps=0.1, min_samples=2)
    assert labels[0] == labels[1]
    assert labels[2] == -1


async def _enrolled_org(client):
    token = await super_token(client)
    org = await make_org(client, token, "Recognition Academy")
    await make_user(client, token, org["id"], "coach@recognition.test", role="org_admin")
    admin = await login(client, "coach@recognition.test", "org-admin-password")
    return admin


async def _add_person(client, token, name, face_token, confirm=True):
    created = await client.post(
        "/api/persons",
        json={"person_type": "student", "full_name": name},
        headers=auth(token),
    )
    assert created.status_code == 200, created.text
    person_id = created.json()["id"]
    photos = await client.post(
        f"/api/persons/{person_id}/photos",
        files=[("files", ("p.jpg", f"FACEID:{face_token}\n".encode(), "image/jpeg"))],
        headers=auth(token),
    )
    assert photos.status_code == 200, photos.text
    assert photos.json()["accepted"], photos.text
    if confirm:
        response = await client.post(f"/api/persons/{person_id}/confirm-enrollment", headers=auth(token))
        assert response.status_code == 200, response.text
        assert response.json()["enrollment_status"] == "enrolled"
    return person_id


async def _identify(client, token, face_token):
    response = await client.post(
        "/api/attendance/check-in",
        files={"file": ("f.jpg", f"FACEID:{face_token}\n".encode(), "image/jpeg")},
        headers=auth(token),
    )
    assert response.status_code == 200, response.text
    return response


@pytest.mark.asyncio
async def test_unknown_until_confirmed_and_below_threshold_has_no_name(client):
    token = await _enrolled_org(client)
    person_id = await _add_person(client, token, "Alice Isolated", "alice", confirm=False)
    early = await _identify(client, token, "alice")
    early_body = early.json()
    assert early_body["result"] == "unknown"
    assert early_body["person_name"] is None
    assert "Alice" not in early.text

    confirmed = await client.post(f"/api/persons/{person_id}/confirm-enrollment", headers=auth(token))
    assert confirmed.status_code == 200

    matched = await _identify(client, token, "alice")
    body = matched.json()
    assert body["result"] == "known"
    assert body["person_name"] == "Alice Isolated"
    assert body["person_id"] == person_id
    assert body["confidence"] >= 0.95
    assert body["duplicate"] is False

    stranger = await _identify(client, token, "bob")
    stranger_body = stranger.json()
    assert stranger_body["result"] == "unknown"
    assert stranger_body["person_name"] is None
    assert stranger_body["person_id"] is None
    assert "Alice" not in stranger.text
    assert stranger_body["unknown_code"].startswith("UNK-")

    low = await _identify(client, token, "alice@0.20")
    low_body = low.json()
    assert low_body["result"] == "unknown"
    assert low_body["reason"] == "below_threshold"
    assert low_body["person_name"] is None
    assert low_body["person_id"] is None
    assert low_body["confidence"] == pytest.approx(0.20, abs=0.03)
    assert "Alice" not in low.text
    payload = json.dumps(low_body)
    assert "STU-" not in payload or low_body["person_code"] is None


@pytest.mark.asyncio
async def test_ambiguous_match_is_unknown_and_repeat_unknowns_group(client):
    token = await _enrolled_org(client)
    await _add_person(client, token, "Alice Isolated", "alice")
    await _add_person(client, token, "Bobby Close", "alice@0.99")
    ambiguous = await _identify(client, token, "alice")
    body = ambiguous.json()
    assert body["result"] == "unknown"
    assert body["reason"] == "ambiguous"
    assert body["person_name"] is None
    assert "Alice" not in ambiguous.text
    assert "Bobby" not in ambiguous.text

    again = await _identify(client, token, "stranger")
    first = again.json()["unknown_code"]
    second = (await _identify(client, token, "stranger")).json()
    assert second["unknown_code"] == first
    queue = await client.get("/api/unknown", headers=auth(token))
    grouped = [item for item in queue.json()["items"] if item["code"] == first]
    assert grouped and grouped[0]["observation_count"] >= 2

    # Assigning a review does not copy the snapshot into the recognition gallery.
    people = await client.get("/api/persons", headers=auth(token))
    alice = next(item for item in people.json()["items"] if item["full_name"] == "Alice Isolated")
    before = alice["embedding_count"]
    resolved = await client.post(
        f"/api/unknown/{grouped[0]['id']}/resolve",
        json={"action": "assign", "person_id": alice["id"]},
        headers=auth(token),
    )
    assert resolved.status_code == 200, resolved.text
    after = await client.get(f"/api/persons/{alice['id']}", headers=auth(token))
    assert after.json()["embedding_count"] == before
