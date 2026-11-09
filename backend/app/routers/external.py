import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.audit import audit_ctx
from app.config import get_settings
from app.db import app_sessions, apply_org
from app.deps import Ctx
from app.serialize import dump
from app.services.face_client import FaceEngineClient, get_face_client
from app.services.gallery import add_photos, create_person, delete_biometrics, get_person, identify_image
from app.services.unknowns import list_unknowns, resolve_unknown

router = APIRouter(prefix="/api/v1", tags=["external"])


class ExternalPerson(BaseModel):
    full_name: str = Field(min_length=2)
    person_type: str = "student"
    code: str | None = None
    email: str | None = None
    phone: str | None = None
    external_id: str | None = None
    dob: str | None = None


class ResolveIn(BaseModel):
    action: str
    person_id: str | None = None
    full_name: str | None = None
    person_type: str | None = "student"
    note: str | None = None
    use_observations_as_enrollment: bool = False


async def _rate_limit(request: Request, key_id: str, limit: int) -> None:
    import redis.asyncio as redis_async

    client = getattr(request.app.state, "redis", None)
    if client is None:
        client = redis_async.from_url(get_settings().redis_url, decode_responses=True)
        request.app.state.redis = client
    bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    redis_key = f"rl:{key_id}:{bucket}"
    try:
        count = await client.incr(redis_key)
        if count == 1:
            await client.expire(redis_key, 90)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Rate limiter unavailable") from exc
    if int(count) > limit:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")


async def get_api_ctx(
    request: Request,
    x_api_key: str | None = Header(default=None),
):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    digest = hashlib.sha256(x_api_key.encode()).hexdigest()
    async with app_sessions()() as session:
        async with session.begin():
            row = (
                await session.execute(text("SELECT * FROM app_find_api_key(:digest)"), {"digest": digest})
            ).mappings().first()
    if not row or row["revoked_at"] is not None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    await _rate_limit(request, str(row["id"]), int(row["rate_limit_per_minute"]))
    org_id = str(row["org_id"])
    async with app_sessions()() as session:
        async with session.begin():
            await apply_org(session, org_id)
            org = (
                await session.execute(
                    text("SELECT code, status FROM organizations WHERE id = CAST(:id AS uuid)"),
                    {"id": org_id},
                )
            ).mappings().first()
            if not org or org["status"] != "active":
                raise HTTPException(status_code=403, detail="Organization is suspended")
            user = {
                "id": str(row["id"]),
                "role": "api_key",
                "org_id": org_id,
                "full_name": row["name"],
                "email": None,
                "status": "active",
            }
            ip = request.client.host if request.client else None
            yield Ctx(session=session, user=user, org_id=org_id, org_code=org["code"], ip=ip)


def _public_identity(identity: dict) -> dict:
    if identity.get("result") != "known":
        identity = dict(identity)
        identity["person_id"] = None
        identity["person_name"] = None
        identity["person_code"] = None
        identity.pop("faces", None)
    return identity


@router.post("/persons")
async def create(body: ExternalPerson, ctx: Ctx = Depends(get_api_ctx)):
    person = await create_person(ctx, body.model_dump())
    person.pop("photo_path", None)
    return person


@router.post("/persons/{person_id}/photos")
async def photos(
    person_id: str,
    files: list[UploadFile] = File(...),
    ctx: Ctx = Depends(get_api_ctx),
    face: FaceEngineClient = Depends(get_face_client),
):
    payload = [(item.filename or "photo", await item.read()) for item in files]
    return await add_photos(ctx, person_id, payload, face)


@router.get("/persons/{person_id}")
async def read_person(person_id: str, ctx: Ctx = Depends(get_api_ctx)):
    person = await get_person(ctx, person_id)
    person.pop("photo_path", None)
    return person


@router.delete("/persons/{person_id}")
async def delete_person(person_id: str, ctx: Ctx = Depends(get_api_ctx)):
    return await delete_biometrics(ctx, person_id)


@router.post("/recognition/identify")
async def identify(
    file: UploadFile = File(...),
    ctx: Ctx = Depends(get_api_ctx),
    face: FaceEngineClient = Depends(get_face_client),
):
    identity = _public_identity(await identify_image(ctx, await file.read(), face, persist_unknown=True))
    await audit_ctx(
        ctx,
        "recognition.identify",
        "recognition",
        identity.get("person_id"),
        after={"result": identity.get("result"), "confidence": identity.get("confidence")},
    )
    return identity


@router.post("/recognition/batch")
async def identify_batch(
    files: list[UploadFile] = File(...),
    ctx: Ctx = Depends(get_api_ctx),
    face: FaceEngineClient = Depends(get_face_client),
):
    results = []
    for item in files:
        identity = _public_identity(await identify_image(ctx, await item.read(), face, persist_unknown=True))
        identity["file"] = item.filename
        results.append(identity)
    return {"results": results}


@router.get("/unknown")
async def unknowns(ctx: Ctx = Depends(get_api_ctx)):
    items = await list_unknowns(ctx, "open")
    for item in items:
        item.pop("latest_snapshot", None)
    return {"items": items}


@router.post("/unknown/{group_id}/resolve")
async def resolve(group_id: str, body: ResolveIn, ctx: Ctx = Depends(get_api_ctx)):
    result = await resolve_unknown(ctx, group_id, body.model_dump())
    if isinstance(result, dict):
        result.pop("centroid", None)
        result.pop("latest_snapshot", None)
    return result
