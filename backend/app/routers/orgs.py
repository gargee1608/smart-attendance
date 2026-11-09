from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.audit import audit_ctx, write_audit
from app.config import get_settings
from app.deps import Ctx, get_ctx, require_admin
from app.errors import ServiceError
from app.serialize import dump
from app.storage import ensure_org_layout, sha256, write_encrypted

router = APIRouter(prefix="/api/orgs", tags=["organizations"])

ORG_TYPES = ("sports_academy", "school", "college", "coaching_institute", "private_org")


class OrgIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    org_type: str
    address: str | None = None
    contact_person: str | None = None
    email: str | None = None
    phone: str | None = None


class OrgPatch(BaseModel):
    name: str | None = None
    address: str | None = None
    contact_person: str | None = None
    email: str | None = None
    phone: str | None = None
    status: str | None = None


def _clean(body: OrgIn) -> OrgIn:
    if body.org_type not in ORG_TYPES:
        raise ServiceError(400, "Organization type is not recognized")
    return body


@router.get("")
async def list_orgs(ctx: Ctx = Depends(get_ctx)):
    rows = (await ctx.session.execute(text("SELECT * FROM organizations ORDER BY code"))).mappings().all()
    return {"items": [dump(dict(row)) for row in rows]}


@router.post("")
async def create_org(body: OrgIn, ctx: Ctx = Depends(get_ctx)):
    if ctx.user["role"] != "super_admin":
        raise ServiceError(403, "Only a super admin can create an organization")
    _clean(body)
    settings = get_settings()
    code = (
        await ctx.session.execute(text("SELECT 'ORG-' || lpad(nextval('org_code_seq')::text, 4, '0')"))
    ).scalar_one()
    row = (
        await ctx.session.execute(
            text(
                """
                INSERT INTO organizations (code, name, org_type, address, contact_person, email, phone)
                VALUES (:code, :name, :org_type, :address, :contact_person, :email, :phone)
                RETURNING *
                """
            ),
            {
                "code": code,
                "name": body.name.strip(),
                "org_type": body.org_type,
                "address": body.address,
                "contact_person": body.contact_person,
                "email": body.email,
                "phone": body.phone,
            },
        )
    ).mappings().one()
    org = dump(dict(row))
    await ctx.session.execute(
        text("INSERT INTO org_counters (org_id) VALUES (CAST(:id AS uuid))"),
        {"id": org["id"]},
    )
    await ctx.session.execute(
        text(
            """
            INSERT INTO attendance_policies (
              org_id, match_threshold, ambiguity_margin, unknown_group_threshold
            )
            VALUES (CAST(:id AS uuid), :threshold, :margin, :group_threshold)
            """
        ),
        {
            "id": org["id"],
            "threshold": settings.face_match_threshold,
            "margin": settings.face_ambiguity_margin,
            "group_threshold": settings.unknown_group_threshold,
        },
    )
    ensure_org_layout(org["code"])
    await write_audit(
        ctx.session,
        org_id=org["id"],
        actor_id=ctx.user["id"],
        actor_role=ctx.user["role"],
        action="org.create",
        entity_type="organization",
        entity_id=org["id"],
        after={"code": org["code"], "name": org["name"]},
        ip=ctx.ip,
    )
    return org


@router.get("/{org_id}")
async def get_org(org_id: str, ctx: Ctx = Depends(get_ctx)):
    row = (
        await ctx.session.execute(
            text("SELECT * FROM organizations WHERE id = CAST(:id AS uuid)"),
            {"id": org_id},
        )
    ).mappings().first()
    if not row:
        raise ServiceError(404, "Organization not found")
    return dump(dict(row))


@router.patch("/{org_id}")
async def patch_org(org_id: str, body: OrgPatch, ctx: Ctx = Depends(get_ctx)):
    require_admin(ctx)
    if ctx.user["role"] != "super_admin" and body.status:
        raise ServiceError(403, "Only a super admin can change organization status")
    current = await get_org(org_id, ctx)
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return current
    assignments = ", ".join(f"{key} = :{key}" for key in fields)
    fields["id"] = org_id
    row = (
        await ctx.session.execute(
            text(f"UPDATE organizations SET {assignments} WHERE id = CAST(:id AS uuid) RETURNING *"),
            fields,
        )
    ).mappings().one()
    updated = dump(dict(row))
    await audit_ctx(ctx, "org.update", "organization", org_id, before=current, after=updated)
    return updated


@router.post("/{org_id}/logo")
async def upload_logo(org_id: str, file: UploadFile = File(...), ctx: Ctx = Depends(get_ctx)):
    require_admin(ctx)
    org = await get_org(org_id, ctx)
    blob = await file.read()
    if len(blob) > 2_000_000:
        raise ServiceError(400, "Logo must be under 2MB")
    relative = f"organizations/{org['code']}/academy_logo/logo.bin"
    write_encrypted(relative, blob)
    await ctx.session.execute(
        text("UPDATE organizations SET logo_path = :path WHERE id = CAST(:id AS uuid)"),
        {"path": relative, "id": org_id},
    )
    await audit_ctx(ctx, "org.logo", "organization", org_id, after={"sha256": sha256(blob)})
    return {"logo_path": relative}
