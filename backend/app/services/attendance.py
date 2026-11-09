from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.audit import audit_ctx
from app.deps import Ctx, require_staff
from app.serialize import dump
from app.errors import ServiceError
from app.services.gallery import get_person, identify_image, policy


def _parse_time(value: str | None) -> time | None:
    if not value:
        return None
    hour, minute = value.split(":")[:2]
    return time(int(hour), int(minute))


def summarize_month(
    *,
    month_start: date,
    month_end: date,
    as_of: date,
    weekdays: set[int],
    holidays: set[date],
    leave_dates: set[date],
    records: dict[date, str],
    count_late_as_present: bool,
    exclude_holidays: bool,
    exclude_approved_leave: bool,
) -> dict:
    present_like = {"present", "manual"}
    if count_late_as_present:
        present_like.add("late")
    days = []
    present_days = 0
    scheduled = 0
    cursor = month_start
    while cursor <= month_end and cursor <= as_of:
        if cursor.weekday() in weekdays:
            reason = None
            in_denominator = True
            if exclude_holidays and cursor in holidays:
                in_denominator = False
                reason = "holiday"
            elif exclude_approved_leave and (cursor in leave_dates or records.get(cursor) == "leave"):
                in_denominator = False
                reason = "leave"
            status = records.get(cursor)
            if in_denominator:
                scheduled += 1
                if status in present_like:
                    present_days += 1
            days.append(
                {
                    "date": cursor.isoformat(),
                    "in_denominator": in_denominator,
                    "reason": reason,
                    "status": status,
                }
            )
        cursor += timedelta(days=1)
    percentage = None if scheduled == 0 else round(100.0 * present_days / scheduled, 1)
    return {
        "scheduled_days": scheduled,
        "present_days": present_days,
        "percentage": percentage,
        "days": days,
    }


async def _existing_attendance(ctx: Ctx, person_id: str, batch_id: str | None, on_date: date) -> dict | None:
    row = (
        await ctx.session.execute(
            text(
                """
                SELECT * FROM attendance_records
                WHERE person_id = CAST(:person AS uuid)
                  AND attendance_date = :on_date
                  AND COALESCE(batch_id, '00000000-0000-0000-0000-000000000000'::uuid)
                      = COALESCE(CAST(:batch AS uuid), '00000000-0000-0000-0000-000000000000'::uuid)
                """
            ),
            {"person": person_id, "batch": batch_id, "on_date": on_date},
        )
    ).mappings().first()
    return dump(dict(row)) if row else None


async def _late_status(ctx: Ctx, batch_id: str | None, when: datetime) -> str:
    rules = await policy(ctx)
    if not batch_id:
        return "present"
    row = (
        await ctx.session.execute(
            text("SELECT schedule FROM batches WHERE id = CAST(:id AS uuid)"),
            {"id": batch_id},
        )
    ).mappings().first()
    if not row:
        return "present"
    schedule = row["schedule"] or {}
    if isinstance(schedule, str):
        import json
        schedule = json.loads(schedule)
    start = _parse_time(schedule.get("start_time"))
    late_after = int(schedule.get("late_after_minutes") if schedule.get("late_after_minutes") is not None else rules["late_after_minutes"])
    if start is None:
        return "present"
    start_dt = datetime.combine(when.date(), start, tzinfo=when.tzinfo)
    if when > start_dt + timedelta(minutes=late_after):
        return "late"
    return "present"


async def record_attendance(
    ctx: Ctx,
    *,
    person_id: str,
    batch_id: str | None,
    on_date: date,
    status: str,
    method: str,
    confidence: float | None,
    camera_id: str | None,
    note: str | None,
    when: datetime | None,
) -> dict:
    existing = await _existing_attendance(ctx, person_id, batch_id, on_date)
    if existing:
        return {"attendance": existing, "duplicate": True}
    try:
        async with ctx.session.begin_nested():
            row = (
                await ctx.session.execute(
                    text(
                        """
                        INSERT INTO attendance_records (
                          org_id, person_id, batch_id, camera_id, attendance_date, status, method,
                          confidence, check_in_at, note, created_by
                        )
                        VALUES (
                          CAST(:org AS uuid), CAST(:person AS uuid), CAST(:batch AS uuid), CAST(:camera AS uuid),
                          :on_date, :status, :method, :confidence, :check_in_at, :note, CAST(:actor AS uuid)
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "org": ctx.org_id,
                        "person": person_id,
                        "batch": batch_id,
                        "camera": camera_id,
                        "on_date": on_date,
                        "status": status,
                        "method": method,
                        "confidence": confidence,
                        "check_in_at": when,
                        "note": note,
                        "actor": ctx.user["id"] if ctx.user["role"] != "api_key" else None,
                    },
                )
            ).mappings().one()
    except DBAPIError as exc:
        message = str(exc).lower()
        if "does not belong" in message:
            raise ServiceError(400, "That person or batch is not in this organization") from exc
        if "unique" in message or "duplicate" in message:
            existing = await _existing_attendance(ctx, person_id, batch_id, on_date)
            if existing:
                return {"attendance": existing, "duplicate": True}
        raise
    record = dump(dict(row))
    await audit_ctx(ctx, "attendance.record", "attendance", record["id"], after={"status": status, "date": on_date.isoformat(), "person_id": person_id})
    return {"attendance": record, "duplicate": False}


async def check_in(ctx: Ctx, image: bytes, face, *, batch_id: str | None, camera_id: str | None) -> dict:
    require_staff(ctx)
    analysis = await face.analyze(image)
    faces = analysis.get("faces") or []
    if len(faces) != 1:
        return {
            "result": "no_face" if not faces else "multiple_faces",
            "confidence": None,
            "person_id": None,
            "person_name": None,
            "person_code": None,
            "reason": "no_face" if not faces else "multiple_faces",
            "unknown_group_id": None,
            "unknown_code": None,
            "duplicate": False,
            "attendance": None,
            "face_count": len(faces),
        }
    identity = await identify_image(
        ctx, image, face, camera_id=camera_id, persist_unknown=True, analysis=analysis
    )
    identity["duplicate"] = False
    identity["attendance"] = None
    if identity["result"] == "multiple":
        identity["result"] = "multiple_faces"
        return identity
    if identity["result"] != "known":
        return identity
    if batch_id:
        member = (
            await ctx.session.execute(
                text(
                    """
                    SELECT 1 FROM person_batches
                    WHERE person_id = CAST(:person AS uuid) AND batch_id = CAST(:batch AS uuid)
                    """
                ),
                {"person": identity["person_id"], "batch": batch_id},
            )
        ).first()
        if not member:
            identity["result"] = "not_in_batch"
            identity["reason"] = "not_in_batch"
            identity["person_name"] = identity["person_name"]
            return identity
    else:
        batch_id = (
            await ctx.session.execute(
                text(
                    """
                    SELECT batch_id FROM person_batches
                    WHERE person_id = CAST(:person AS uuid)
                    ORDER BY created_at LIMIT 1
                    """
                ),
                {"person": identity["person_id"]},
            )
        ).scalar()
        batch_id = str(batch_id) if batch_id else None
    when = datetime.now(timezone.utc)
    status = await _late_status(ctx, batch_id, when)
    saved = await record_attendance(
        ctx,
        person_id=identity["person_id"],
        batch_id=batch_id,
        on_date=when.date(),
        status=status,
        method="face",
        confidence=identity["confidence"],
        camera_id=camera_id,
        note=None,
        when=when,
    )
    identity["duplicate"] = saved["duplicate"]
    identity["attendance"] = saved["attendance"]
    return identity


async def manual_mark(ctx: Ctx, *, person_id: str, batch_id: str | None, on_date: date, status: str, note: str | None) -> dict:
    require_staff(ctx)
    await get_person(ctx, person_id)
    if batch_id:
        member = (
            await ctx.session.execute(
                text("SELECT 1 FROM person_batches WHERE person_id = CAST(:p AS uuid) AND batch_id = CAST(:b AS uuid)"),
                {"p": person_id, "b": batch_id},
            )
        ).first()
        if not member:
            raise ServiceError(400, "That person is not in the selected batch")
    saved = await record_attendance(
        ctx,
        person_id=person_id,
        batch_id=batch_id,
        on_date=on_date,
        status=status,
        method="manual",
        confidence=None,
        camera_id=None,
        note=note,
        when=datetime.now(timezone.utc) if on_date == date.today() else None,
    )
    return saved


async def correct(ctx: Ctx, record_id: str, status: str, note: str | None) -> dict:
    require_staff(ctx)
    current = (
        await ctx.session.execute(
            text("SELECT * FROM attendance_records WHERE id = CAST(:id AS uuid)"),
            {"id": record_id},
        )
    ).mappings().first()
    if not current:
        raise ServiceError(404, "Attendance record not found")
    before = dump(dict(current))
    row = (
        await ctx.session.execute(
            text(
                """
                UPDATE attendance_records
                SET status = :status, note = :note, method = 'correction', updated_at = now()
                WHERE id = CAST(:id AS uuid)
                RETURNING *
                """
            ),
            {"status": status, "note": note, "id": record_id},
        )
    ).mappings().one()
    after = dump(dict(row))
    await audit_ctx(
        ctx,
        "attendance.correct",
        "attendance",
        record_id,
        before={"status": before["status"], "note": before.get("note")},
        after={"status": after["status"], "note": after.get("note")},
    )
    return after


async def monthly_summary(ctx: Ctx, person_id: str, month: str, as_of: date | None, batch_id: str | None) -> dict:
    person = await get_person(ctx, person_id)
    if ctx.user["role"] == "student" and person.get("user_id") != ctx.user["id"]:
        raise ServiceError(404, "Person not found")
    rules = await policy(ctx)
    year, mon = (int(part) for part in month.split("-"))
    start = date(year, mon, 1)
    end = date(year + 1, 1, 1) - timedelta(days=1) if mon == 12 else date(year, mon + 1, 1) - timedelta(days=1)
    today = as_of or date.today()
    batch_rows = (
        await ctx.session.execute(
            text(
                """
                SELECT b.id, b.schedule
                FROM person_batches pb
                JOIN batches b ON b.id = pb.batch_id
                WHERE pb.person_id = CAST(:person AS uuid)
                  AND (CAST(:batch AS uuid) IS NULL OR b.id = CAST(:batch AS uuid))
                """
            ),
            {"person": person_id, "batch": batch_id},
        )
    ).mappings().all()
    weekdays: set[int] = set()
    for row in batch_rows:
        schedule = row["schedule"] or {}
        if isinstance(schedule, str):
            import json
            schedule = json.loads(schedule)
        weekdays.update(int(day) for day in schedule.get("weekdays", [0, 1, 2, 3, 4]))
    if not weekdays:
        weekdays = {0, 1, 2, 3, 4}
    holiday_rows = (
        await ctx.session.execute(
            text("SELECT holiday_date FROM holidays WHERE holiday_date BETWEEN :start AND :end"),
            {"start": start, "end": end},
        )
    ).scalars().all()
    leave_rows = (
        await ctx.session.execute(
            text(
                """
                SELECT start_date, end_date FROM leave_requests
                WHERE person_id = CAST(:person AS uuid) AND status = 'approved'
                  AND end_date >= :start AND start_date <= :end
                """
            ),
            {"person": person_id, "start": start, "end": end},
        )
    ).mappings().all()
    leave_dates: set[date] = set()
    for row in leave_rows:
        cursor = row["start_date"]
        while cursor <= row["end_date"]:
            leave_dates.add(cursor)
            cursor += timedelta(days=1)
    record_rows = (
        await ctx.session.execute(
            text(
                """
                SELECT attendance_date, status FROM attendance_records
                WHERE person_id = CAST(:person AS uuid)
                  AND attendance_date BETWEEN :start AND :end
                  AND (CAST(:batch AS uuid) IS NULL OR batch_id = CAST(:batch AS uuid))
                """
            ),
            {"person": person_id, "start": start, "end": end, "batch": batch_id},
        )
    ).mappings().all()
    records: dict[date, str] = {}
    rank = {"present": 5, "manual": 4, "late": 3, "leave": 2, "holiday": 1, "absent": 0}
    for row in record_rows:
        current = records.get(row["attendance_date"])
        if current is None or rank.get(row["status"], 0) > rank.get(current, 0):
            records[row["attendance_date"]] = row["status"]
    summary = summarize_month(
        month_start=start,
        month_end=end,
        as_of=today,
        weekdays=weekdays,
        holidays=set(holiday_rows),
        leave_dates=leave_dates,
        records=records,
        count_late_as_present=bool(rules["count_late_as_present"]),
        exclude_holidays=bool(rules["exclude_holidays"]),
        exclude_approved_leave=bool(rules["exclude_approved_leave"]),
    )
    summary.update(
        {
            "person_id": person_id,
            "person_name": person["full_name"],
            "month": month,
            "as_of": today.isoformat(),
            "policy": {
                "count_late_as_present": rules["count_late_as_present"],
                "exclude_holidays": rules["exclude_holidays"],
                "exclude_approved_leave": rules["exclude_approved_leave"],
                "match_threshold": rules["match_threshold"],
                "ambiguity_margin": rules["ambiguity_margin"],
            },
        }
    )
    return summary
