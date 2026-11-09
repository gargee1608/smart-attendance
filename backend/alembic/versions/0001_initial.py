"""Initial schema with org_id row-level security."""

from pathlib import Path

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from psycopg.pq import ExecStatus

    sql = Path(__file__).resolve().parents[2].joinpath("app", "schema.sql").read_text()
    connection = op.get_bind()
    raw = connection.connection
    dbapi = getattr(raw, "driver_connection", None) or getattr(raw, "dbapi_connection", raw)
    result = dbapi.pgconn.exec_(sql.encode("utf-8"))
    if result.status not in (ExecStatus.COMMAND_OK, ExecStatus.TUPLES_OK):
        message = (result.error_message or b"schema script failed").decode()
        raise RuntimeError(message)


def downgrade() -> None:
    raise NotImplementedError("v1 has no downgrade. Restore a backup instead.")
