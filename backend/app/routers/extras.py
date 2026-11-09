import csv
import hashlib
import io
import secrets
from datetime import date

import httpx
from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import __version__
from app.audit import audit_ctx
from app.config import get_settings
from app.deps import Ctx, get_ctx, get_tenant_ctx, require_admin, require_staff
from app.errors import ServiceError
from app.serialize import dump
from app.services.attendance import record_attendance
from app.services.face_client import FaceEngineClient, get_face_client
from app.services.gallery import identify_image
from app.services.unknowns import list_unknowns, regroup_dbscan, resolve_unknown
from app.storage import read_encrypted, storage_root

router = APIRouter(prefix="/api", tags=["operations"])


class CameraIn(BaseModel):
    name: str
    rtsp_url: str | None = None
    location: str | None = None
    batch_id: str | None = None


class ResolveIn(BaseModel):
    action: str
    person_id: str | None = None
    full_name: str | None = None
    person_type: str | None = "student"
    phone: str | None = None
    email: str | None = None
    note: str | None = None
    use_observations_as_enrollment: bool = False


class ApiKeyIn(BaseModel):
    name: str
    rate_limit_per_minute: int = Field(default=60, ge=1, le=10000)


class MetricIn(BaseModel):
    person_id: str
    sport_id: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    metrics: dict = {}


class CommentIn(BaseModel):
    person_id: str
    coach_id: str | None = None
    sport_id: str | None = None
    period: str | None = None
    comment: str


class AiIn(BaseModel):
    person_id: str


class AiReview(BaseModel):
    status: str


@router.get("/dashboard")
async def dashboard(ctx: Ctx = Depends(get_tenant_ctx)):
    today = date.today().isoformat()
    counts = (
        await ctx.session.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM persons) AS people,
                  (SELECT count(*) FROM persons WHERE enrollment_status = 'enrolled') AS enrolled,
                  (SELECT count(*) FROM attendance_records WHERE attendance_date = CAST(:today AS date) AND status IN ('present','late','manual')) AS present_today,
                  (SELECT count(*) FROM unknown_groups WHERE status = 'open') AS open_unknowns,
                  (SELECT count(*) FROM batches WHERE status = 'active') AS batches,
                  (SELECT count(*) FROM notifications WHERE read_at IS NULL) AS unread
                """
            ),
            {"today": today},
        )
    ).mappings().one()
    notes = (
        await ctx.session.execute(
            text("SELECT * FROM notifications ORDER BY created_at DESC LIMIT 8")
        )
    ).mappings().all()
    return {"counts": dump(dict(counts)), "notifications": [dump(dict(row)) for row in notes]}


@router.post("/notifications/{note_id}/read")
async def read_note(note_id: str, ctx: Ctx = Depends(get_tenant_ctx)):
    await ctx.session.execute(
        text("UPDATE notifications SET read_at = now() WHERE id = CAST(:id AS uuid)"),
        {"id": note_id},
    )
    return {"ok": True}


@router.get("/unknown")
async def get_unknown(status: str | None = "open", ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    return {"items": await list_unknowns(ctx, status if status != "all" else None)}


@router.post("/unknown/{group_id}/resolve")
async def post_resolve(group_id: str, body: ResolveIn, ctx: Ctx = Depends(get_tenant_ctx)):
    return await resolve_unknown(ctx, group_id, body.model_dump())


@router.post("/unknown/regroup")
async def post_regroup(ctx: Ctx = Depends(get_tenant_ctx)):
    return await regroup_dbscan(ctx)


@router.get("/unknown/{group_id}/snapshots/{image_id}")
async def snapshot(group_id: str, image_id: str, ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    row = (
        await ctx.session.execute(
            text(
                """
                SELECT i.storage_path
                FROM unknown_observations o
                JOIN face_images i ON i.id = o.face_image_id
                WHERE o.group_id = CAST(:group AS uuid) AND i.id = CAST(:image AS uuid)
                """
            ),
            {"group": group_id, "image": image_id},
        )
    ).mappings().first()
    if not row:
        raise ServiceError(404, "Snapshot not found")
    blob = read_encrypted(row["storage_path"])
    media = "image/jpeg" if blob.startswith(b"\xff\xd8") else "application/octet-stream"
    return Response(content=blob, media_type=media)


@router.get("/cameras")
async def list_cameras(ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    rows = (await ctx.session.execute(text("SELECT id, org_id, name, rtsp_url, location, batch_id, status, created_at FROM cameras ORDER BY name"))).mappings().all()
    return {"items": [dump(dict(row)) for row in rows]}


@router.post("/cameras")
async def create_camera(body: CameraIn, ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    if body.rtsp_url and not (body.rtsp_url.startswith("rtsp://") or body.rtsp_url.startswith("rtsps://")):
        raise ServiceError(400, "Camera URL must start with rtsp:// or rtsps://")
    row = (
        await ctx.session.execute(
            text(
                """
                INSERT INTO cameras (org_id, name, rtsp_url, location, batch_id)
                VALUES (CAST(:org AS uuid), :name, :rtsp_url, :location, CAST(:batch AS uuid))
                RETURNING *
                """
            ),
            {
                "org": ctx.org_id,
                "name": body.name.strip(),
                "rtsp_url": body.rtsp_url,
                "location": body.location,
                "batch": body.batch_id,
            },
        )
    ).mappings().one()
    item = dump(dict(row))
    await audit_ctx(ctx, "camera.create", "camera", item["id"], after={"name": item["name"]})
    return item


@router.post("/cameras/{camera_id}/capture")
async def capture_camera(
    camera_id: str,
    ctx: Ctx = Depends(get_tenant_ctx),
    face: FaceEngineClient = Depends(get_face_client),
):
    require_staff(ctx)
    camera = (
        await ctx.session.execute(
            text("SELECT * FROM cameras WHERE id = CAST(:id AS uuid)"),
            {"id": camera_id},
        )
    ).mappings().first()
    if not camera or not camera["rtsp_url"]:
        raise ServiceError(404, "Camera not found or it has no RTSP URL")
    frame = await face.capture_rtsp(camera["rtsp_url"])
    analysis = await face.analyze(frame)
    faces = analysis.get("faces") or []
    results = []
    for found in faces:
        identity = await identify_image(
            ctx,
            frame,
            face,
            camera_id=camera_id,
            persist_unknown=True,
            analysis={"faces": [found]},
        )
        identity["duplicate"] = False
        identity["attendance"] = None
        if identity["result"] == "known":
            saved = await record_attendance(
                ctx,
                person_id=identity["person_id"],
                batch_id=str(camera["batch_id"]) if camera["batch_id"] else None,
                on_date=date.today(),
                status="present",
                method="face",
                confidence=identity["confidence"],
                camera_id=camera_id,
                note=None,
                when=None,
            )
            identity["duplicate"] = saved["duplicate"]
            identity["attendance"] = saved["attendance"]
        results.append(identity)
    return {"camera_id": camera_id, "faces": results}


@router.get("/api-keys")
async def list_keys(ctx: Ctx = Depends(get_tenant_ctx)):
    require_admin(ctx)
    rows = (
        await ctx.session.execute(
            text("SELECT id, name, key_prefix, rate_limit_per_minute, revoked_at, created_at FROM api_keys ORDER BY created_at DESC")
        )
    ).mappings().all()
    return {"items": [dump(dict(row)) for row in rows]}


@router.post("/api-keys")
async def create_key(body: ApiKeyIn, ctx: Ctx = Depends(get_tenant_ctx)):
    require_admin(ctx)
    raw = "sa_" + secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    row = (
        await ctx.session.execute(
            text(
                """
                INSERT INTO api_keys (org_id, name, key_prefix, key_hash, rate_limit_per_minute, created_by)
                VALUES (CAST(:org AS uuid), :name, :prefix, :hash, :limit, CAST(:actor AS uuid))
                RETURNING id, name, key_prefix, rate_limit_per_minute, created_at
                """
            ),
            {
                "org": ctx.org_id,
                "name": body.name.strip(),
                "prefix": raw[:11],
                "hash": digest,
                "limit": body.rate_limit_per_minute,
                "actor": ctx.user["id"],
            },
        )
    ).mappings().one()
    item = dump(dict(row))
    item["api_key"] = raw
    await audit_ctx(ctx, "apikey.create", "api_key", item["id"], after={"name": item["name"], "rate_limit_per_minute": body.rate_limit_per_minute})
    return item


@router.delete("/api-keys/{key_id}")
async def revoke_key(key_id: str, ctx: Ctx = Depends(get_tenant_ctx)):
    require_admin(ctx)
    row = (
        await ctx.session.execute(
            text("UPDATE api_keys SET revoked_at = now() WHERE id = CAST(:id AS uuid) AND revoked_at IS NULL RETURNING id"),
            {"id": key_id},
        )
    ).first()
    if not row:
        raise ServiceError(404, "API key not found")
    await audit_ctx(ctx, "apikey.revoke", "api_key", key_id)
    return {"id": key_id, "revoked": True}


@router.get("/audit")
async def list_audit(ctx: Ctx = Depends(get_tenant_ctx)):
    require_admin(ctx)
    rows = (
        await ctx.session.execute(text("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 200"))
    ).mappings().all()
    return {"items": [dump(dict(row)) for row in rows]}


@router.get("/reports/overview")
async def overview(ctx: Ctx = Depends(get_tenant_ctx)):
    return await dashboard(ctx)


@router.get("/reports/attendance.csv")
async def attendance_csv(month: str, ctx: Ctx = Depends(get_tenant_ctx)):
    # TODO M10: PDF and Excel exports. CSV is the v1 download.
    rows = (
        await ctx.session.execute(
            text(
                """
                SELECT a.attendance_date, p.code, p.full_name, b.name AS batch, a.status, a.method,
                       a.confidence, a.check_in_at
                FROM attendance_records a
                JOIN persons p ON p.id = a.person_id
                LEFT JOIN batches b ON b.id = a.batch_id
                WHERE to_char(a.attendance_date, 'YYYY-MM') = :month
                ORDER BY a.attendance_date, p.full_name
                """
            ),
            {"month": month},
        )
    ).mappings().all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "person_code", "person_name", "batch", "status", "method", "confidence", "check_in_at"])
    for row in rows:
        writer.writerow(
            [
                row["attendance_date"].isoformat(),
                row["code"],
                row["full_name"],
                row["batch"] or "",
                row["status"],
                row["method"],
                "" if row["confidence"] is None else row["confidence"],
                row["check_in_at"].isoformat() if row["check_in_at"] else "",
            ]
        )
    await audit_ctx(ctx, "report.csv", "report", month, after={"rows": len(rows)})
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="attendance-{month}.csv"'},
    )


@router.post("/imports/persons")
async def import_persons(
    file: UploadFile = File(...),
    ctx: Ctx = Depends(get_tenant_ctx),
):
    """CSV import. Does not approve face enrollment. TODO M11: Excel, column mapping UI, bulk photos."""
    require_staff(ctx)
    raw = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    created = []
    errors = []
    from app.services.gallery import create_person

    for index, row in enumerate(reader, start=2):
        try:
            person_type = (row.get("person_type") or "student").strip()
            full_name = (row.get("full_name") or "").strip()
            if not full_name:
                raise ServiceError(400, "full_name is required")
            batch_ids = []
            batch_name = (row.get("batch") or "").strip()
            if batch_name:
                found = (
                    await ctx.session.execute(
                        text("SELECT id FROM batches WHERE name = :name"),
                        {"name": batch_name},
                    )
                ).scalar()
                if not found:
                    raise ServiceError(400, f"Unknown batch {batch_name}")
                batch_ids.append(str(found))
            if row.get("email"):
                existing = (
                    await ctx.session.execute(
                        text("SELECT id FROM persons WHERE lower(email) = lower(:email)"),
                        {"email": row["email"].strip()},
                    )
                ).first()
                if existing:
                    raise ServiceError(409, "Duplicate email")
            if row.get("code"):
                existing = (
                    await ctx.session.execute(
                        text("SELECT id FROM persons WHERE code = :code"),
                        {"code": row["code"].strip()},
                    )
                ).first()
                if existing:
                    raise ServiceError(409, "Duplicate code")
            person = await create_person(
                ctx,
                {
                    "person_type": person_type,
                    "full_name": full_name,
                    "code": (row.get("code") or "").strip() or None,
                    "email": (row.get("email") or "").strip() or None,
                    "phone": (row.get("phone") or "").strip() or None,
                    "dob": (row.get("dob") or "").strip() or None,
                    "external_id": (row.get("external_id") or "").strip() or None,
                    "batch_ids": batch_ids,
                },
            )
            if person["enrollment_status"] == "enrolled":
                raise ServiceError(500, "Import must not auto-enroll faces")
            created.append({"row": index, "id": person["id"], "code": person["code"]})
        except ServiceError as exc:
            errors.append({"row": index, "error": exc.detail})
    report = {"created": len(created), "errors": errors, "rows": created}
    job = (
        await ctx.session.execute(
            text(
                """
                INSERT INTO import_jobs (org_id, filename, status, report, created_by)
                VALUES (CAST(:org AS uuid), :filename, :status, CAST(:report AS jsonb), CAST(:actor AS uuid))
                RETURNING id
                """
            ),
            {
                "org": ctx.org_id,
                "filename": file.filename,
                "status": "completed_with_errors" if errors else "completed",
                "report": __import__("json").dumps(report),
                "actor": ctx.user["id"],
            },
        )
    ).scalar_one()
    saved = storage_root() / "imports" / f"{job}.csv"
    saved.write_text(raw)
    await audit_ctx(ctx, "import.persons", "import_job", str(job), after={"created": len(created), "errors": len(errors)})
    return {"id": str(job), **report}


@router.get("/imports")
async def list_imports(ctx: Ctx = Depends(get_tenant_ctx)):
    rows = (await ctx.session.execute(text("SELECT * FROM import_jobs ORDER BY created_at DESC LIMIT 50"))).mappings().all()
    return {"items": [dump(dict(row)) for row in rows]}


@router.get("/performance/metrics")
async def list_metrics(ctx: Ctx = Depends(get_tenant_ctx)):
    # TODO M08: per-sport metric templates (cricket batting/bowling, fitness, discipline) and evaluations.
    rows = (await ctx.session.execute(text("SELECT * FROM performance_metrics ORDER BY created_at DESC"))).mappings().all()
    return {"items": [dump(dict(row)) for row in rows], "stub": True}


@router.post("/performance/metrics")
async def create_metric(body: MetricIn, ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    row = (
        await ctx.session.execute(
            text(
                """
                INSERT INTO performance_metrics (org_id, person_id, sport_id, period_start, period_end, metrics, created_by)
                VALUES (
                  CAST(:org AS uuid), CAST(:person AS uuid), CAST(:sport AS uuid),
                  :period_start, :period_end, CAST(:metrics AS jsonb), CAST(:actor AS uuid)
                )
                RETURNING *
                """
            ),
            {
                "org": ctx.org_id,
                "person": body.person_id,
                "sport": body.sport_id,
                "period_start": body.period_start,
                "period_end": body.period_end,
                "metrics": __import__("json").dumps(body.metrics),
                "actor": ctx.user["id"],
            },
        )
    ).mappings().one()
    return dump(dict(row))


@router.get("/performance/comments")
async def list_comments(ctx: Ctx = Depends(get_tenant_ctx)):
    rows = (await ctx.session.execute(text("SELECT * FROM coach_comments ORDER BY created_at DESC"))).mappings().all()
    return {"items": [dump(dict(row)) for row in rows]}


@router.post("/performance/comments")
async def create_comment(body: CommentIn, ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    row = (
        await ctx.session.execute(
            text(
                """
                INSERT INTO coach_comments (org_id, person_id, coach_id, sport_id, period, comment)
                VALUES (CAST(:org AS uuid), CAST(:person AS uuid), CAST(:coach AS uuid), CAST(:sport AS uuid), :period, :comment)
                RETURNING *
                """
            ),
            {
                "org": ctx.org_id,
                "person": body.person_id,
                "coach": body.coach_id,
                "sport": body.sport_id,
                "period": body.period,
                "comment": body.comment,
            },
        )
    ).mappings().one()
    item = dump(dict(row))
    await audit_ctx(ctx, "coach_comment.create", "coach_comment", item["id"], after={"person_id": body.person_id})
    return item


@router.get("/ai/comments")
async def list_ai(ctx: Ctx = Depends(get_tenant_ctx)):
    rows = (await ctx.session.execute(text("SELECT * FROM ai_comments ORDER BY created_at DESC"))).mappings().all()
    return {
        "items": [dump(dict(row)) for row in rows],
        "disclaimer": "AI comments are suggestions only. They are not coach comments and do not change attendance.",
    }


@router.post("/ai/comments")
async def create_ai(body: AiIn, ctx: Ctx = Depends(get_tenant_ctx)):
    """TODO M09: richer context windows and a human approval workflow before anything is shown to families."""
    require_staff(ctx)
    person = (
        await ctx.session.execute(
            text("SELECT id, full_name, code FROM persons WHERE id = CAST(:id AS uuid)"),
            {"id": body.person_id},
        )
    ).mappings().first()
    if not person:
        raise ServiceError(404, "Person not found")
    attendance = (
        await ctx.session.execute(
            text(
                """
                SELECT status, count(*) AS n FROM attendance_records
                WHERE person_id = CAST(:id AS uuid)
                GROUP BY status
                """
            ),
            {"id": body.person_id},
        )
    ).mappings().all()
    comments = (
        await ctx.session.execute(
            text("SELECT comment FROM coach_comments WHERE person_id = CAST(:id AS uuid) ORDER BY created_at DESC LIMIT 5"),
            {"id": body.person_id},
        )
    ).scalars().all()
    settings = get_settings()
    prompt = (
        "You are assisting an academy administrator. Write a short suggestion about this student. "
        "Do not invent scores. Do not claim this is an official evaluation. "
        f"Student: {person['full_name']} ({person['code']}). "
        f"Attendance counts: {dump([dict(row) for row in attendance])}. "
        f"Recent coach comments: {list(comments) or 'none'}."
    )
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                f"{settings.ai_service_url}/v1/comment",
                json={"prompt": prompt, "person_id": str(person["id"])},
            )
    except httpx.HTTPError as exc:
        raise ServiceError(503, f"AI service unavailable: {exc}") from exc
    if response.status_code >= 400:
        detail = response.text[:400]
        raise ServiceError(503, f"AI service unavailable: {detail}")
    suggestion = response.json().get("text") or ""
    model = response.json().get("model") or settings.ollama_model
    row = (
        await ctx.session.execute(
            text(
                """
                INSERT INTO ai_comments (org_id, person_id, suggestion, status, model)
                VALUES (CAST(:org AS uuid), CAST(:person AS uuid), :suggestion, 'suggestion', :model)
                RETURNING *
                """
            ),
            {"org": ctx.org_id, "person": body.person_id, "suggestion": suggestion, "model": model},
        )
    ).mappings().one()
    item = dump(dict(row))
    item["disclaimer"] = "Suggestion only. Not a coach comment. It requires a person's approval before it is treated as guidance."
    await audit_ctx(ctx, "ai.suggestion", "ai_comment", item["id"], after={"status": "suggestion", "model": model})
    return item


@router.post("/ai/comments/{comment_id}/review")
async def review_ai(comment_id: str, body: AiReview, ctx: Ctx = Depends(get_tenant_ctx)):
    require_admin(ctx)
    if body.status not in {"approved", "rejected"}:
        raise ServiceError(400, "Review must approve or reject the suggestion")
    row = (
        await ctx.session.execute(
            text(
                """
                UPDATE ai_comments
                SET status = :status, reviewed_by = CAST(:actor AS uuid), reviewed_at = now()
                WHERE id = CAST(:id AS uuid)
                RETURNING *
                """
            ),
            {"status": body.status, "actor": ctx.user["id"], "id": comment_id},
        )
    ).mappings().first()
    if not row:
        raise ServiceError(404, "Suggestion not found")
    item = dump(dict(row))
    await audit_ctx(ctx, "ai.review", "ai_comment", comment_id, after={"status": body.status})
    return item


@router.get("/system/version")
async def version(_: Ctx = Depends(get_ctx)):
    return {
        "version": __version__,
        "modules": {
            "M01": "organization onboarding",
            "M02": "users and roles",
            "M03": "sports, batches, classes",
            "M04": "enrollment",
            "M05": "face engine",
            "M06": "unknown review",
            "M07": "attendance",
            "M08": "stub — performance metrics accept free-form JSON; sport templates are TODO",
            "M09": "stub — Ollama suggestions only, never official comments",
            "M10": "stub — CSV export works; PDF and Excel are TODO",
            "M11": "stub — CSV person import works; Excel, mapping UI, and bulk photos are TODO",
            "M12": "backup.sh — pg_dump plus encrypted file archive; restic is optional",
            "M13": "external API",
            "M14": "audit log",
            "M15": "update.sh — git pull, rebuild, health check, rollback",
        },
    }


@router.get("/health")
async def health(request: Request):
    checks = {"database": "down", "redis": "down", "face_engine": "down"}
    try:
        from app.db import app_sessions

        async with app_sessions()() as session:
            async with session.begin():
                await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "down"
    try:
        client = getattr(request.app.state, "redis", None)
        if client is None:
            import redis.asyncio as redis_async

            client = redis_async.from_url(get_settings().redis_url, decode_responses=True)
            request.app.state.redis = client
        await client.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "down"
    try:
        checks["face_engine"] = (await get_face_client().health()).get("status", "down")
    except Exception:
        checks["face_engine"] = "down"
    status = "ok" if checks["database"] == "ok" and checks["redis"] == "ok" else "degraded"
    code = 200 if checks["database"] == "ok" else 503
    return JSONResponse(status_code=code, content={"status": status, "checks": checks})
