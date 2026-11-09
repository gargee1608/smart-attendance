from fastapi import APIRouter, Depends, File, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.audit import audit_ctx
from app.deps import Ctx, get_tenant_ctx, require_admin, require_staff
from app.errors import ServiceError
from app.security import hash_password
from app.serialize import dump
from app.services.face_client import FaceEngineClient, get_face_client
from app.services.gallery import add_photos, confirm_enrollment, create_person, delete_biometrics, get_person
from app.storage import read_encrypted

router = APIRouter(prefix="/api", tags=["directory"])

ROLES = {"org_admin", "coach", "teacher", "student", "parent"}


class UserIn(BaseModel):
    full_name: str = Field(min_length=2)
    email: str
    password: str = Field(min_length=10)
    role: str
    phone: str | None = None


class UserPatch(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    status: str | None = None
    password: str | None = None


class PersonIn(BaseModel):
    person_type: str
    full_name: str = Field(min_length=2)
    code: str | None = None
    dob: str | None = None
    phone: str | None = None
    email: str | None = None
    guardian_name: str | None = None
    guardian_phone: str | None = None
    guardian_email: str | None = None
    sport_specialization: str | None = None
    external_id: str | None = None
    joining_date: str | None = None
    batch_ids: list[str] = []


class PersonPatch(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    email: str | None = None
    guardian_name: str | None = None
    guardian_phone: str | None = None
    sport_specialization: str | None = None
    status: str | None = None
    external_id: str | None = None


class BatchLink(BaseModel):
    batch_id: str
    role: str = "student"


def _media_type(blob: bytes) -> str:
    if blob.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if blob.startswith(b"\x89PNG"):
        return "image/png"
    if blob.startswith(b"RIFF"):
        return "image/webp"
    return "application/octet-stream"


@router.get("/users")
async def list_users(ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    rows = (
        await ctx.session.execute(text("SELECT id, org_id, email, full_name, role, phone, status, created_at FROM users ORDER BY full_name"))
    ).mappings().all()
    return {"items": [dump(dict(row)) for row in rows]}


@router.post("/users")
async def create_user(body: UserIn, ctx: Ctx = Depends(get_tenant_ctx)):
    require_admin(ctx)
    if body.role not in ROLES:
        raise ServiceError(400, "Role is not available")
    try:
        async with ctx.session.begin_nested():
            row = (
                await ctx.session.execute(
                    text(
                        """
                        INSERT INTO users (org_id, email, password_hash, full_name, role, phone)
                        VALUES (CAST(:org AS uuid), :email, :password_hash, :full_name, :role, :phone)
                        RETURNING id, org_id, email, full_name, role, phone, status, created_at
                        """
                    ),
                    {
                        "org": ctx.org_id,
                        "email": body.email.strip().lower(),
                        "password_hash": hash_password(body.password),
                        "full_name": body.full_name.strip(),
                        "role": body.role,
                        "phone": body.phone,
                    },
                )
            ).mappings().one()
    except DBAPIError as exc:
        if "unique" in str(exc).lower():
            raise ServiceError(409, "That email is already in use") from exc
        raise
    user = dump(dict(row))
    await audit_ctx(ctx, "user.create", "user", user["id"], after={"email": user["email"], "role": user["role"]})
    return user


@router.patch("/users/{user_id}")
async def patch_user(user_id: str, body: UserPatch, ctx: Ctx = Depends(get_tenant_ctx)):
    require_admin(ctx)
    fields = {}
    if body.full_name is not None:
        fields["full_name"] = body.full_name
    if body.phone is not None:
        fields["phone"] = body.phone
    if body.status is not None:
        if body.status not in {"active", "suspended"}:
            raise ServiceError(400, "Status must be active or suspended")
        fields["status"] = body.status
    if body.password:
        if len(body.password) < 10:
            raise ServiceError(400, "Password must be at least 10 characters")
        fields["password_hash"] = hash_password(body.password)
    if not fields:
        raise ServiceError(400, "Nothing to update")
    assignments = ", ".join(f"{key} = :{key}" for key in fields)
    fields["id"] = user_id
    row = (
        await ctx.session.execute(
            text(
                f"""
                UPDATE users SET {assignments}
                WHERE id = CAST(:id AS uuid)
                RETURNING id, org_id, email, full_name, role, phone, status, created_at
                """
            ),
            fields,
        )
    ).mappings().first()
    if not row:
        raise ServiceError(404, "User not found")
    updated = dump(dict(row))
    await audit_ctx(ctx, "user.update", "user", user_id, after={"status": updated["status"]})
    return updated


@router.get("/persons")
async def list_persons(person_type: str | None = None, ctx: Ctx = Depends(get_tenant_ctx)):
    sql = """
      SELECT p.*,
        COALESCE((
          SELECT json_agg(json_build_object('id', b.id, 'name', b.name, 'role', pb.role))
          FROM person_batches pb JOIN batches b ON b.id = pb.batch_id
          WHERE pb.person_id = p.id
        ), '[]'::json) AS batches,
        (SELECT count(*) FROM face_embeddings e WHERE e.person_id = p.id) AS embedding_count
      FROM persons p
      WHERE 1=1
    """
    params: dict = {}
    if person_type:
        sql += " AND p.person_type = :person_type"
        params["person_type"] = person_type
    if ctx.user["role"] == "student":
        sql += " AND p.user_id = CAST(:uid AS uuid)"
        params["uid"] = ctx.user["id"]
    sql += " ORDER BY p.full_name"
    rows = (await ctx.session.execute(text(sql), params)).mappings().all()
    return {"items": [dump(dict(row)) for row in rows]}


@router.post("/persons")
async def post_person(body: PersonIn, ctx: Ctx = Depends(get_tenant_ctx)):
    if body.person_type not in {"student", "coach", "teacher", "visitor"}:
        raise ServiceError(400, "Person type is not recognized")
    return await create_person(ctx, body.model_dump())


@router.get("/persons/{person_id}")
async def read_person(person_id: str, ctx: Ctx = Depends(get_tenant_ctx)):
    return await get_person(ctx, person_id)


@router.patch("/persons/{person_id}")
async def patch_person(person_id: str, body: PersonPatch, ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    await get_person(ctx, person_id)
    fields = {key: value for key, value in body.model_dump(exclude_unset=True).items()}
    if not fields:
        return await get_person(ctx, person_id)
    assignments = ", ".join(f"{key} = :{key}" for key in fields)
    fields["id"] = person_id
    await ctx.session.execute(
        text(f"UPDATE persons SET {assignments} WHERE id = CAST(:id AS uuid)"),
        fields,
    )
    await audit_ctx(ctx, "person.update", "person", person_id, after=fields)
    return await get_person(ctx, person_id)


@router.post("/persons/{person_id}/batches")
async def link_batch(person_id: str, body: BatchLink, ctx: Ctx = Depends(get_tenant_ctx)):
    require_staff(ctx)
    from app.services.gallery import assign_batch

    if body.role not in {"student", "coach", "teacher"}:
        raise ServiceError(400, "Batch role is not recognized")
    await assign_batch(ctx, person_id, body.batch_id, body.role)
    await audit_ctx(ctx, "person.batch", "person", person_id, after=body.model_dump())
    return await get_person(ctx, person_id)


@router.post("/persons/{person_id}/photos")
async def upload_photos(
    person_id: str,
    files: list[UploadFile] = File(...),
    ctx: Ctx = Depends(get_tenant_ctx),
    face: FaceEngineClient = Depends(get_face_client),
):
    payload = []
    for item in files:
        payload.append((item.filename or "photo", await item.read()))
    return await add_photos(ctx, person_id, payload, face)


@router.post("/persons/{person_id}/confirm-enrollment")
async def confirm(person_id: str, ctx: Ctx = Depends(get_tenant_ctx)):
    return await confirm_enrollment(ctx, person_id)


@router.delete("/persons/{person_id}/biometrics")
async def remove_biometrics(person_id: str, ctx: Ctx = Depends(get_tenant_ctx)):
    return await delete_biometrics(ctx, person_id)


@router.get("/persons/{person_id}/photos/{image_id}")
async def download_photo(person_id: str, image_id: str, ctx: Ctx = Depends(get_tenant_ctx)):
    await get_person(ctx, person_id)
    row = (
        await ctx.session.execute(
            text(
                """
                SELECT storage_path FROM face_images
                WHERE id = CAST(:image AS uuid) AND person_id = CAST(:person AS uuid)
                """
            ),
            {"image": image_id, "person": person_id},
        )
    ).mappings().first()
    if not row:
        raise ServiceError(404, "Photo not found")
    blob = read_encrypted(row["storage_path"])
    return Response(content=blob, media_type=_media_type(blob))
