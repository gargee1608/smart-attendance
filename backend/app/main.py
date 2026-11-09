import logging
from contextlib import asynccontextmanager

import redis.asyncio as redis_async
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import __version__
from app.config import get_settings
from app.db import app_sessions, dispose_engines
from app.errors import ServiceError
from app.routers import attendance, auth, directory, external, extras, orgs, structure
from app.storage import storage_root

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("smartattendance")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.assert_production_safe()
    settings.encryption_key_bytes()
    storage_root()
    async with app_sessions()() as session:
        async with session.begin():
            row = (
                await session.execute(
                    text(
                        """
                        SELECT rolname, rolsuper, rolbypassrls
                        FROM pg_roles
                        WHERE rolname = current_user
                        """
                    )
                )
            ).one()
            if row.rolsuper or row.rolbypassrls:
                raise RuntimeError("API database role must not bypass row-level security")
            log.info("database role %s (super=%s bypassrls=%s)", row.rolname, row.rolsuper, row.rolbypassrls)
    app.state.redis = redis_async.from_url(settings.redis_url, decode_responses=True)
    yield
    await app.state.redis.aclose()
    await dispose_engines()


app = FastAPI(title="Smart Attendance", version=__version__, lifespan=lifespan)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.public_base_url, "http://localhost:18480", "https://localhost:18443"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ServiceError)
async def service_error(_: Request, exc: ServiceError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.detail})


@app.exception_handler(DBAPIError)
async def db_error(_: Request, exc: DBAPIError):
    message = str(exc).lower()
    log.exception("database error")
    if "does not belong" in message:
        return JSONResponse(status_code=400, content={"detail": "That record is not in this organization"})
    if "unique" in message or "duplicate" in message:
        return JSONResponse(status_code=409, content={"detail": "That record already exists"})
    return JSONResponse(status_code=500, content={"detail": "Database error"})


app.include_router(auth.router)
app.include_router(orgs.router)
app.include_router(directory.router)
app.include_router(structure.router)
app.include_router(attendance.router)
app.include_router(extras.router)
app.include_router(external.router)
