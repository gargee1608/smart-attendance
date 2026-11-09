from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.config import get_settings

_app_engine: AsyncEngine | None = None
_admin_engine: AsyncEngine | None = None
_app_session = None
_admin_session = None


def _engine(url: str) -> AsyncEngine:
    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        connect_args={"application_name": "smartattendance"},
    )


def get_app_engine() -> AsyncEngine:
    global _app_engine
    if _app_engine is None:
        _app_engine = _engine(get_settings().database_url)
    return _app_engine


def get_admin_engine() -> AsyncEngine:
    global _admin_engine
    if _admin_engine is None:
        _admin_engine = _engine(get_settings().admin_database_url)
    return _admin_engine


def app_sessions():
    global _app_session
    if _app_session is None:
        _app_session = async_sessionmaker(get_app_engine(), expire_on_commit=False)
    return _app_session


def admin_sessions():
    global _admin_session
    if _admin_session is None:
        _admin_session = async_sessionmaker(get_admin_engine(), expire_on_commit=False)
    return _admin_session


async def apply_org(session, org_id: str | None) -> None:
    await session.execute(
        text("SELECT set_config('app.current_org_id', :value, true)"),
        {"value": org_id or ""},
    )


async def dispose_engines() -> None:
    global _app_engine, _admin_engine, _app_session, _admin_session
    if _app_engine is not None:
        await _app_engine.dispose()
    if _admin_engine is not None:
        await _admin_engine.dispose()
    _app_engine = None
    _admin_engine = None
    _app_session = None
    _admin_session = None
