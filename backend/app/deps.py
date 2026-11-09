from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text

from app.db import admin_sessions, app_sessions, apply_org
from app.security import decode_token
from app.serialize import dump

bearer = HTTPBearer(auto_error=False)


@dataclass
class Ctx:
    session: object
    user: dict
    org_id: str | None
    org_code: str | None
    ip: str | None


def _public_user(row) -> dict:
    data = dump(dict(row))
    data.pop("password_hash", None)
    return data


async def _load_user(user_id: str) -> dict | None:
    async with app_sessions()() as session:
        async with session.begin():
            row = (
                await session.execute(
                    text("SELECT * FROM app_find_user_by_id(CAST(:id AS uuid))"),
                    {"id": user_id},
                )
            ).mappings().first()
    if not row:
        return None
    return _public_user(row)


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


async def get_ctx(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    x_organization_id: str | None = Header(default=None),
):
    if creds is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(creds.credentials)
    user = await _load_user(payload.get("sub", ""))
    if not user or user.get("status") != "active":
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user["role"] != "super_admin" and user.get("org_status") == "suspended":
        raise HTTPException(status_code=403, detail="Organization is suspended")

    use_admin = False
    org_id = None
    if user["role"] == "super_admin":
        if x_organization_id:
            org_id = x_organization_id
        else:
            use_admin = True
    else:
        if x_organization_id and x_organization_id != user.get("org_id"):
            raise HTTPException(status_code=403, detail="Cannot act in another organization")
        org_id = user.get("org_id")

    factory = admin_sessions() if use_admin else app_sessions()
    async with factory() as session:
        async with session.begin():
            await apply_org(session, org_id)
            org_code = None
            if org_id:
                row = (
                    await session.execute(
                        text("SELECT id, code, status FROM organizations WHERE id = CAST(:id AS uuid)"),
                        {"id": org_id},
                    )
                ).mappings().first()
                if not row:
                    raise HTTPException(status_code=404, detail="Organization not found")
                if row["status"] == "suspended" and user["role"] != "super_admin":
                    raise HTTPException(status_code=403, detail="Organization is suspended")
                org_code = row["code"]
            yield Ctx(session=session, user=user, org_id=org_id, org_code=org_code, ip=_client_ip(request))


async def get_tenant_ctx(ctx: Ctx = Depends(get_ctx)) -> Ctx:
    if not ctx.org_id:
        raise HTTPException(
            status_code=400,
            detail="Choose an organization with the X-Organization-Id header",
        )
    return ctx


def require_roles(ctx: Ctx, *roles: str) -> None:
    allowed = set(roles)
    if ctx.user["role"] == "super_admin" and "super_admin" not in allowed:
        allowed.add("super_admin")
    if ctx.user["role"] not in allowed:
        raise HTTPException(status_code=403, detail="Not allowed")


def require_staff(ctx: Ctx) -> None:
    require_roles(ctx, "org_admin", "coach", "teacher", "super_admin")


def require_operator(ctx: Ctx) -> None:
    """Staff, or an organization API key acting for that tenant."""
    if ctx.user["role"] == "api_key":
        return
    require_staff(ctx)


def require_admin(ctx: Ctx) -> None:
    require_roles(ctx, "org_admin", "super_admin")
