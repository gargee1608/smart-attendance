from datetime import date

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.audit import audit_ctx
from app.deps import Ctx, get_tenant_ctx, require_admin, require_staff
from app.errors import ServiceError
from app.serialize import dump
from app.services.attendance import check_in, correct, manual_mark, monthly_summary
from app.services.face_client import FaceEngineClient, get_face_client

router = APIRouter(prefix="/api", tags=["attendance"])

STATUSES = {"present", "absent", "late", "leave", "holiday", "manual"}


class ManualIn(BaseModel):
    person_id: str
    batch_id: str | None = None
    on_date: str
    status: str
    note: str | None = None


class CorrectionIn(BaseModel):
    status: str
    note: str | None = None


class PolicyIn(BaseModel):
    count_late_as_present: bool = True
    exclude_holidays: bool = True
    exclude_approved_leave: bool = True
    late_after_minutes: int = 15
    match_threshold: float = Field(default=0.363, ge=0, le=1)
    ambiguity_margin: float = Field(default=0.05, ge=0, le=1)
    unknown_group_threshold: float = Field(default=0.55, ge=0, le=1)


class HolidayIn(BaseModel):
    holiday_date: str
    name: str


class LeaveIn(BaseModel):
    person_id: str
    start_date: str
    end_date: str
    reason: str | None = None
    status: str = "approved"


@router.get("/attendance")
async def list_attendance(on_date: str | None = None, batch_id: str | None = None, ctx: Ctx = Depends(get_tenant_ctx)):
    params: dict = {"on_date": on_date or date.today().isoformat(), "batch": batch_id}
    rows = (
        await ctx.session.execute(
            text(
                """
                SELECT a.*, p.full_name, p.code AS person_code, b.name AS batch_name
                FROM attendance_records a
                JOIN persons p ON p.id = a.person_id
                LEFT JOIN batches b ON b.id = a.batch_id
                WHERE a.attendance_date = CAST(:on_date AS date)
                  AND (CAST(:batch AS uuid) IS NULL OR a.batch_id = CAST(:batch AS uuid))
                ORDER BY a.check_in_at NULLS LAST, p.full_name
                """
            ),
            params,
        )
    ).mappings().all()
    if ctx.user["role"] == "student":
        items = [dump(dict(row)) for row in rows if str(row.get("created_by") or "") == ctx.user["id"] or True]
        person_rows = (
            await ctx.session.execute(
                text("SELECT id FROM persons WHERE user_id = CAST(:id AS uuid)"),
                {"id": ctx.user["id"]},
            )
        ).scalars().all()
        allowed = {str(item) for item in person_rows}
        items = [item for item in items if item["person_id"] in allowed]
        return {"items": items}
    return {"items": [dump(dict(row)) for row in rows]}


@router.post("/attendance/check-in")
async def post_check_in(
    file: UploadFile = File(...),
    batch_id: str | None = Form(default=None),
    camera_id: str | None = Form(default=None),
    ctx: Ctx = Depends(get_tenant_ctx),
    face: FaceEngineClient = Depends(get_face_client),
):
    blob = await file.read()
    return await check_in(ctx, blob, face, batch_id=batch_id or None, camera_id=camera_id or None)


@router.post("/attendance/manual")
async def post_manual(body: ManualIn, ctx: Ctx = Depends(get_tenant_ctx)):
    if body.status not in STATUSES:
        raise ServiceError(400, "Attendance status is not recognized")
    saved = await manual_mark(
        ctx,
        person_id=body.person_id,
        batch_id=body.batch_id,
        on_date=date.fromisoformat(body.on_date),
        status=body.status,
        note=body.note,
    )
    return saved


@router.post("/attendance/{record_id}/corrections")
async def post_correction(record_id: str, body: CorrectionIn, ctx: Ctx = Depends(get_tenant_ctx)):
    if body.status not in STATUSES:
        raise ServiceError(400, "Attendance status is not recognized")
    return await correct(ctx, record_id, body.status, body.note)


@router.get("/attendance/summary")
async def get_summary(
    person_id: str,
    month: str,
    as_of: str | None = None,
    batch_id: str | None = None,
    ctx: Ctx = Depends(get_tenant_ctx),
):
    return await monthly_summary(
        ctx,
        person_id,
        month,
        date.fromisoformat(as_of) if as_of else None,
        batch_id,
    )


@router.get("/attendance/policy")
async def get_policy(ctx: Ctx = Depends(get_tenant_ctx)):
    row = (await ctx.session.execute(text("SELECT * FROM attendance_policies"))).mappings().first()
    return dump(dict(row)) if row else {}


@router.put("/attendance/policy")
async def put_policy(body: PolicyIn, ctx: Ctx = Depends(get_tenant_ctx)):
    require_admin(ctx)
    before = await get_policy(ctx)
    row = (
        await ctx.session.execute(
            text(
                """
                UPDATE attendance_policies SET
                  count_late_as_present = :count_late_as_present,
                  exclude_holidays = :exclude_holidays,
                  exclude_approved_leave = :exclude_approved_leave,
                  late_after_minutes = :late_after_minutes,
                  match_threshold = :match_threshold,
                  ambiguity_margin = :ambiguity_margin,
                  unknown_group_threshold = :unknown_group_threshold
                WHERE org_id = CAST(:org AS uuid)
                RETURNING *
                """
            ),
            {**body.model_dump(), "org": ctx.org_id},
        )
    ).mappings().one()
    updated = dump(dict(row))
    await audit_ctx(ctx, "attendance.policy", "attendance_policy", ctx.org_id, before=before, after=updated)
    return updated


@router.get("/holidays")
async def list_holidays(ctx: Ctx = Depends(get_tenant_ctx)):
    rows = (await ctx.session.execute(text("SELECT * FROM holidays ORDER BY holiday_date"))).mappings().all()
    return {"items": [dump(dict(row)) for row in rows]}


@router.post("/holidays")
async def create_holiday(body: HolidayIn, ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    row = (
        await ctx.session.execute(
            text(
                """
                INSERT INTO holidays (org_id, holiday_date, name)
                VALUES (CAST(:org AS uuid), :holiday_date, :name)
                ON CONFLICT (org_id, holiday_date) DO UPDATE SET name = EXCLUDED.name
                RETURNING *
                """
            ),
            {"org": ctx.org_id, "holiday_date": body.holiday_date, "name": body.name},
        )
    ).mappings().one()
    item = dump(dict(row))
    await audit_ctx(ctx, "holiday.upsert", "holiday", item["id"], after={"date": body.holiday_date, "name": body.name})
    return item


@router.get("/leave")
async def list_leave(ctx: Ctx = Depends(get_tenant_ctx)):
    rows = (await ctx.session.execute(text("SELECT * FROM leave_requests ORDER BY start_date DESC"))).mappings().all()
    return {"items": [dump(dict(row)) for row in rows]}


@router.post("/leave")
async def create_leave(body: LeaveIn, ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    if body.status not in {"pending", "approved", "rejected"}:
        raise ServiceError(400, "Leave status is not recognized")
    row = (
        await ctx.session.execute(
            text(
                """
                INSERT INTO leave_requests (org_id, person_id, start_date, end_date, status, reason)
                VALUES (CAST(:org AS uuid), CAST(:person AS uuid), :start_date, :end_date, :status, :reason)
                RETURNING *
                """
            ),
            {
                "org": ctx.org_id,
                "person": body.person_id,
                "start_date": body.start_date,
                "end_date": body.end_date,
                "status": body.status,
                "reason": body.reason,
            },
        )
    ).mappings().one()
    item = dump(dict(row))
    await audit_ctx(ctx, "leave.create", "leave", item["id"], after={"person_id": body.person_id, "status": body.status})
    return item
