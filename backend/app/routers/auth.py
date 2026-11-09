from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.audit import write_audit
from app.db import admin_sessions, app_sessions, apply_org
from app.security import create_token, verify_password
from app.serialize import dump

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str = Field(min_length=1)


def _user_out(row) -> dict:
    data = dump(dict(row))
    data.pop("password_hash", None)
    return data


@router.post("/login")
async def login(body: LoginIn, request: Request):
    email = body.email.strip().lower()
    async with app_sessions()() as session:
        async with session.begin():
            row = (
                await session.execute(
                    text("SELECT * FROM app_find_user_by_email(:email)"),
                    {"email": email},
                )
            ).mappings().first()
    if not row or not verify_password(row["password_hash"], body.password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if row["status"] != "active":
        raise HTTPException(status_code=403, detail="Account is suspended")
    if row["role"] != "super_admin" and row["org_status"] == "suspended":
        raise HTTPException(status_code=403, detail="Organization is suspended")
    user = _user_out(row)
    ip = request.client.host if request.client else None
    if user["role"] == "super_admin":
        async with admin_sessions()() as session:
            async with session.begin():
                await write_audit(
                    session,
                    org_id=None,
                    actor_id=user["id"],
                    actor_role=user["role"],
                    action="auth.login",
                    entity_type="user",
                    entity_id=user["id"],
                    ip=ip,
                )
    else:
        async with app_sessions()() as session:
            async with session.begin():
                await apply_org(session, user["org_id"])
                await write_audit(
                    session,
                    org_id=user["org_id"],
                    actor_id=user["id"],
                    actor_role=user["role"],
                    action="auth.login",
                    entity_type="user",
                    entity_id=user["id"],
                    ip=ip,
                )
    return {"access_token": create_token(user), "token_type": "bearer", "user": user}
