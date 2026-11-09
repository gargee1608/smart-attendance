import asyncio

from celery import Celery

from app.config import get_settings

settings = get_settings()
celery = Celery("smartattendance", broker=settings.redis_url)
celery.conf.task_ignore_result = True
celery.conf.task_default_queue = "smartattendance"
celery.conf.broker_connection_retry_on_startup = True


@celery.task(name="smartattendance.regroup_unknowns")
def regroup_unknowns_task(org_id: str) -> dict:
    """Cron entry point for DBSCAN regrouping. The API also runs this inline."""

    async def _run() -> dict:
        from app.db import app_sessions, apply_org
        from app.deps import Ctx
        from app.services.unknowns import regroup_dbscan

        async with app_sessions()() as session:
            async with session.begin():
                await apply_org(session, org_id)
                org = None
                from sqlalchemy import text

                row = (
                    await session.execute(
                        text("SELECT code FROM organizations WHERE id = CAST(:id AS uuid)"),
                        {"id": org_id},
                    )
                ).first()
                if row:
                    org = row[0]
                ctx = Ctx(
                    session=session,
                    user={"id": None, "role": "super_admin", "org_id": org_id, "full_name": "worker"},
                    org_id=org_id,
                    org_code=org,
                    ip="worker",
                )
                # The worker is an internal job. It uses the app role with the org GUC set,
                # so RLS still confines the regroup to one organization.
                ctx.user["role"] = "org_admin"
                return await regroup_dbscan(ctx)

    return asyncio.run(_run())


@celery.task(name="smartattendance.ping")
def ping() -> str:
    return "ok"
