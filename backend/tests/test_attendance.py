import pytest

from tests.conftest import auth, login, make_org, make_user, super_token


async def _setup(client):
    token = await super_token(client)
    org = await make_org(client, token, "Harbor Academy")
    await make_user(client, token, org["id"], "admin@harbor.test")
    admin = await login(client, "admin@harbor.test", "org-admin-password")
    batch = await client.post(
        "/api/batches",
        json={"name": "Dawn", "weekdays": [0, 1, 2, 3, 4], "start_time": "00:00", "late_after_minutes": 1440},
        headers=auth(admin),
    )
    assert batch.status_code == 200, batch.text
    other = await client.post(
        "/api/batches",
        json={"name": "Evening", "weekdays": [0, 1, 2, 3, 4], "start_time": "18:00"},
        headers=auth(admin),
    )
    assert other.status_code == 200, other.text
    person = await client.post(
        "/api/persons",
        json={
            "person_type": "student",
            "full_name": "Meera Shah",
            "batch_ids": [batch.json()["id"], other.json()["id"]],
        },
        headers=auth(admin),
    )
    assert person.status_code == 200, person.text
    photos = await client.post(
        f"/api/persons/{person.json()['id']}/photos",
        files=[("files", ("m.jpg", b"FACEID:meera\n", "image/jpeg"))],
        headers=auth(admin),
    )
    assert photos.json()["accepted"]
    await client.post(f"/api/persons/{person.json()['id']}/confirm-enrollment", headers=auth(admin))
    return admin, person.json(), batch.json(), other.json()


@pytest.mark.asyncio
async def test_face_and_manual_checkins_do_not_duplicate(client):
    admin, person, batch, other = await _setup(client)
    first = await client.post(
        "/api/attendance/check-in",
        files={"file": ("m.jpg", b"FACEID:meera\n", "image/jpeg")},
        data={"batch_id": batch["id"]},
        headers=auth(admin),
    )
    assert first.status_code == 200, first.text
    assert first.json()["result"] == "known"
    assert first.json()["duplicate"] is False
    record_id = first.json()["attendance"]["id"]
    on_date = first.json()["attendance"]["attendance_date"]

    second = await client.post(
        "/api/attendance/check-in",
        files={"file": ("m.jpg", b"FACEID:meera\n", "image/jpeg")},
        data={"batch_id": batch["id"]},
        headers=auth(admin),
    )
    assert second.status_code == 200, second.text
    assert second.json()["duplicate"] is True
    assert second.json()["attendance"]["id"] == record_id

    other_batch = await client.post(
        "/api/attendance/manual",
        json={"person_id": person["id"], "batch_id": other["id"], "on_date": on_date[:10], "status": "present"},
        headers=auth(admin),
    )
    assert other_batch.status_code == 200, other_batch.text
    assert other_batch.json()["duplicate"] is False
    assert other_batch.json()["attendance"]["id"] != record_id

    repeated = await client.post(
        "/api/attendance/manual",
        json={"person_id": person["id"], "batch_id": other["id"], "on_date": on_date[:10], "status": "absent"},
        headers=auth(admin),
    )
    assert repeated.json()["duplicate"] is True
    assert repeated.json()["attendance"]["status"] == "present"

    listed = await client.get(f"/api/attendance?on_date={on_date[:10]}", headers=auth(admin))
    assert len(listed.json()["items"]) == 2

    corrected = await client.post(
        f"/api/attendance/{record_id}/corrections",
        json={"status": "late", "note": "Bus delay"},
        headers=auth(admin),
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["status"] == "late"
    still = await client.get(f"/api/attendance?on_date={on_date[:10]}&batch_id={batch['id']}", headers=auth(admin))
    assert len(still.json()["items"]) == 1
    assert still.json()["items"][0]["status"] == "late"

    audit = await client.get("/api/audit", headers=auth(admin))
    actions = [item["action"] for item in audit.json()["items"]]
    assert "attendance.correct" in actions
    correction = next(item for item in audit.json()["items"] if item["action"] == "attendance.correct")
    assert correction["before_data"]["status"] == "present"
    assert correction["after_data"]["status"] == "late"


@pytest.mark.asyncio
async def test_monthly_percentage_respects_policy(client):
    admin, person, batch, _other = await _setup(client)
    holiday = await client.post(
        "/api/holidays",
        json={"holiday_date": "2026-01-01", "name": "New Year"},
        headers=auth(admin),
    )
    assert holiday.status_code == 200, holiday.text
    leave = await client.post(
        "/api/leave",
        json={
            "person_id": person["id"],
            "start_date": "2026-01-02",
            "end_date": "2026-01-02",
            "status": "approved",
            "reason": "Family",
        },
        headers=auth(admin),
    )
    assert leave.status_code == 200, leave.text
    for on_date, status in (("2026-01-05", "present"), ("2026-01-06", "late"), ("2026-01-07", "absent")):
        saved = await client.post(
            "/api/attendance/manual",
            json={"person_id": person["id"], "batch_id": batch["id"], "on_date": on_date, "status": status},
            headers=auth(admin),
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["duplicate"] is False

    summary = await client.get(
        "/api/attendance/summary",
        params={"person_id": person["id"], "month": "2026-01", "as_of": "2026-01-31", "batch_id": batch["id"]},
        headers=auth(admin),
    )
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert body["scheduled_days"] == 20
    assert body["present_days"] == 2
    assert body["percentage"] == 10.0

    updated = await client.put(
        "/api/attendance/policy",
        json={
            "count_late_as_present": False,
            "exclude_holidays": True,
            "exclude_approved_leave": True,
            "late_after_minutes": 15,
            "match_threshold": 0.363,
            "ambiguity_margin": 0.05,
            "unknown_group_threshold": 0.55,
        },
        headers=auth(admin),
    )
    assert updated.status_code == 200, updated.text
    again = await client.get(
        "/api/attendance/summary",
        params={"person_id": person["id"], "month": "2026-01", "as_of": "2026-01-31", "batch_id": batch["id"]},
        headers=auth(admin),
    )
    assert again.json()["present_days"] == 1
    assert again.json()["percentage"] == 5.0

    exported = await client.get("/api/reports/attendance.csv", params={"month": "2026-01"}, headers=auth(admin))
    assert exported.status_code == 200
    assert "Meera Shah" in exported.text
    assert "present" in exported.text


@pytest.mark.asyncio
async def test_csv_import_does_not_enroll_faces(client):
    token = await super_token(client)
    org = await make_org(client, token, "Import Academy")
    await make_user(client, token, org["id"], "admin@import.test")
    admin = await login(client, "admin@import.test", "org-admin-password")
    await client.post("/api/batches", json={"name": "U12"}, headers=auth(admin))
    csv_text = "code,full_name,person_type,email,phone,dob,batch\nSTU-9,Import Kid,student,kid@import.test,555,2014-04-01,U12\n,,student,,,,,\n"
    response = await client.post(
        "/api/imports/persons",
        files={"file": ("people.csv", csv_text, "text/csv")},
        headers=auth(admin),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] == 1
    assert body["errors"]
    people = await client.get("/api/persons", headers=auth(admin))
    imported = people.json()["items"][0]
    assert imported["enrollment_status"] == "draft"
    assert imported["embedding_count"] == 0
