import json
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID


def dump(value):
    if isinstance(value, dict):
        return {key: dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [dump(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, memoryview)):
        return None
    return value


def dumps_json(value) -> str | None:
    if value is None:
        return None
    return json.dumps(dump(value))


def parse_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value
