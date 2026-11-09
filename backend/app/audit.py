from sqlalchemy import text

from app.serialize import dumps_json


async def write_audit(
    session,
    *,
    org_id: str | None,
    actor_id: str | None,
    actor_role: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    ip: str | None = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO audit_logs (
              org_id, actor_id, actor_role, action, entity_type, entity_id,
              before_data, after_data, ip_address
            )
            VALUES (
              CAST(:org_id AS uuid), CAST(:actor_id AS uuid), :actor_role, :action,
              :entity_type, :entity_id, CAST(:before_data AS jsonb), CAST(:after_data AS jsonb), :ip
            )
            """
        ),
        {
            "org_id": org_id,
            "actor_id": actor_id,
            "actor_role": actor_role,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "before_data": dumps_json(before),
            "after_data": dumps_json(after),
            "ip": ip,
        },
    )


async def audit_ctx(ctx, action: str, entity_type: str, entity_id: str | None, before=None, after=None) -> None:
    await write_audit(
        ctx.session,
        org_id=ctx.org_id,
        actor_id=ctx.user.get("id") if ctx.user.get("role") != "api_key" else None,
        actor_role=ctx.user.get("role"),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
        ip=ctx.ip,
    )
