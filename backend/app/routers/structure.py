from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.audit import audit_ctx
from app.deps import Ctx, get_tenant_ctx, require_staff
from app.errors import ServiceError
from app.serialize import dump

router = APIRouter(prefix="/api", tags=["structure"])


class SportIn(BaseModel):
    name: str = Field(min_length=2)
    description: str | None = None


class SessionIn(BaseModel):
    name: str
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False


class ClassIn(BaseModel):
    name: str
    section: str = ""
    session_id: str | None = None
    create_batch: bool = True


class SubjectIn(BaseModel):
    name: str


class TeacherLink(BaseModel):
    teacher_id: str
    subject_id: str


class BatchIn(BaseModel):
    name: str
    sport_id: str | None = None
    class_id: str | None = None
    weekdays: list[int] = [0, 1, 2, 3, 4]
    start_time: str = "07:00"
    late_after_minutes: int = 15
    start_date: str | None = None
    end_date: str | None = None


def _schedule(body: BatchIn) -> str:
    import json

    weekdays = [day for day in body.weekdays if 0 <= day <= 6]
    return json.dumps(
        {
            "weekdays": weekdays or [0, 1, 2, 3, 4],
            "start_time": body.start_time,
            "late_after_minutes": body.late_after_minutes,
        }
    )


async def _insert_batch(ctx: Ctx, name: str, sport_id: str | None, class_id: str | None, schedule: str, start_date, end_date):
    try:
        async with ctx.session.begin_nested():
            row = (
                await ctx.session.execute(
                    text(
                        """
                        INSERT INTO batches (org_id, name, sport_id, class_id, schedule, start_date, end_date)
                        VALUES (
                          CAST(:org AS uuid), :name, CAST(:sport AS uuid), CAST(:class_id AS uuid),
                          CAST(:schedule AS jsonb), :start_date, :end_date
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "org": ctx.org_id,
                        "name": name,
                        "sport": sport_id,
                        "class_id": class_id,
                        "schedule": schedule,
                        "start_date": start_date,
                        "end_date": end_date,
                    },
                )
            ).mappings().one()
    except DBAPIError as exc:
        message = str(exc).lower()
        if "unique" in message:
            raise ServiceError(409, "A batch with that name already exists") from exc
        if "does not belong" in message:
            raise ServiceError(400, "Sport or class is not in this organization") from exc
        raise
    return dump(dict(row))


@router.get("/sports")
async def list_sports(ctx: Ctx = Depends(get_tenant_ctx)):
    rows = (await ctx.session.execute(text("SELECT * FROM sports ORDER BY name"))).mappings().all()
    return {"items": [dump(dict(row)) for row in rows]}


@router.post("/sports")
async def create_sport(body: SportIn, ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    try:
        async with ctx.session.begin_nested():
            row = (
                await ctx.session.execute(
                    text(
                        """
                        INSERT INTO sports (org_id, name, description)
                        VALUES (CAST(:org AS uuid), :name, :description) RETURNING *
                        """
                    ),
                    {"org": ctx.org_id, "name": body.name.strip(), "description": body.description},
                )
            ).mappings().one()
    except DBAPIError as exc:
        if "unique" in str(exc).lower():
            raise ServiceError(409, "That sport already exists") from exc
        raise
    item = dump(dict(row))
    await audit_ctx(ctx, "sport.create", "sport", item["id"], after={"name": item["name"]})
    return item


@router.get("/sessions")
async def list_sessions(ctx: Ctx = Depends(get_tenant_ctx)):
    rows = (await ctx.session.execute(text("SELECT * FROM academic_sessions ORDER BY start_date NULLS LAST"))).mappings().all()
    return {"items": [dump(dict(row)) for row in rows]}


@router.post("/sessions")
async def create_session(body: SessionIn, ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    if body.is_current:
        await ctx.session.execute(text("UPDATE academic_sessions SET is_current = false"))
    row = (
        await ctx.session.execute(
            text(
                """
                INSERT INTO academic_sessions (org_id, name, start_date, end_date, is_current)
                VALUES (CAST(:org AS uuid), :name, :start_date, :end_date, :is_current) RETURNING *
                """
            ),
            {
                "org": ctx.org_id,
                "name": body.name.strip(),
                "start_date": body.start_date,
                "end_date": body.end_date,
                "is_current": body.is_current,
            },
        )
    ).mappings().one()
    item = dump(dict(row))
    await audit_ctx(ctx, "session.create", "academic_session", item["id"], after={"name": item["name"]})
    return item


@router.get("/classes")
async def list_classes(ctx: Ctx = Depends(get_tenant_ctx)):
    rows = (await ctx.session.execute(text("SELECT * FROM school_classes ORDER BY name, section"))).mappings().all()
    return {"items": [dump(dict(row)) for row in rows]}


@router.post("/classes")
async def create_class(body: ClassIn, ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    row = (
        await ctx.session.execute(
            text(
                """
                INSERT INTO school_classes (org_id, session_id, name, section)
                VALUES (CAST(:org AS uuid), CAST(:session AS uuid), :name, :section) RETURNING *
                """
            ),
            {"org": ctx.org_id, "session": body.session_id, "name": body.name.strip(), "section": body.section.strip()},
        )
    ).mappings().one()
    item = dump(dict(row))
    if body.create_batch:
        label = f"{item['name']} {item['section']}".strip()
        item["batch"] = await _insert_batch(ctx, label, None, item["id"], _schedule(BatchIn(name=label)), None, None)
    await audit_ctx(ctx, "class.create", "school_class", item["id"], after={"name": item["name"], "section": item["section"]})
    return item


@router.get("/subjects")
async def list_subjects(ctx: Ctx = Depends(get_tenant_ctx)):
    rows = (await ctx.session.execute(text("SELECT * FROM subjects ORDER BY name"))).mappings().all()
    return {"items": [dump(dict(row)) for row in rows]}


@router.post("/subjects")
async def create_subject(body: SubjectIn, ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    row = (
        await ctx.session.execute(
            text("INSERT INTO subjects (org_id, name) VALUES (CAST(:org AS uuid), :name) RETURNING *"),
            {"org": ctx.org_id, "name": body.name.strip()},
        )
    ).mappings().one()
    return dump(dict(row))


@router.post("/classes/{class_id}/teachers")
async def add_teacher(class_id: str, body: TeacherLink, ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    try:
        async with ctx.session.begin_nested():
            row = (
                await ctx.session.execute(
                    text(
                        """
                        INSERT INTO class_subject_teachers (org_id, class_id, subject_id, teacher_id)
                        VALUES (CAST(:org AS uuid), CAST(:class_id AS uuid), CAST(:subject AS uuid), CAST(:teacher AS uuid))
                        RETURNING *
                        """
                    ),
                    {"org": ctx.org_id, "class_id": class_id, "subject": body.subject_id, "teacher": body.teacher_id},
                )
            ).mappings().one()
    except DBAPIError as exc:
        if "does not belong" in str(exc).lower():
            raise ServiceError(400, "Teacher, subject, or class is not in this organization") from exc
        raise
    return dump(dict(row))


@router.get("/batches")
async def list_batches(ctx: Ctx = Depends(get_tenant_ctx)):
    rows = (
        await ctx.session.execute(
            text(
                """
                SELECT b.*, s.name AS sport_name
                FROM batches b
                LEFT JOIN sports s ON s.id = b.sport_id
                ORDER BY b.name
                """
            )
        )
    ).mappings().all()
    return {"items": [dump(dict(row)) for row in rows]}


@router.post("/batches")
async def create_batch(body: BatchIn, ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    item = await _insert_batch(ctx, body.name.strip(), body.sport_id, body.class_id, _schedule(body), body.start_date, body.end_date)
    await audit_ctx(ctx, "batch.create", "batch", item["id"], after={"name": item["name"]})
    return item
