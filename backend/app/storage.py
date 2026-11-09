import hashlib
import os
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

MAGIC = b"SAENC1"
ORG_KINDS = (
    "academy_logo",
    "coaches",
    "students",
    "face_images",
    "unknown_faces",
    "attendance",
    "reports",
)


def storage_root() -> Path:
    root = Path(get_settings().storage_root)
    root.mkdir(parents=True, exist_ok=True)
    for name in ("imports", "exports", "temporary"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def ensure_org_layout(org_code: str) -> None:
    base = storage_root() / "organizations" / org_code
    for kind in ORG_KINDS:
        (base / kind).mkdir(parents=True, exist_ok=True)


def _aes() -> AESGCM:
    return AESGCM(get_settings().encryption_key_bytes())


def encrypt_bytes(data: bytes) -> bytes:
    nonce = os.urandom(12)
    ciphertext = _aes().encrypt(nonce, data, None)
    return MAGIC + nonce + ciphertext


def decrypt_bytes(blob: bytes) -> bytes:
    if not blob.startswith(MAGIC) or len(blob) < 6 + 12 + 16:
        raise ValueError("Face file is not an encrypted Smart Attendance blob")
    nonce = blob[6:18]
    return _aes().decrypt(nonce, blob[18:], None)


def write_encrypted(relative_path: str, data: bytes) -> str:
    path = storage_root() / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypt_bytes(data))
    return relative_path


def read_encrypted(relative_path: str) -> bytes:
    path = storage_root() / relative_path
    return decrypt_bytes(path.read_bytes())


def delete_file(relative_path: str | None) -> None:
    if not relative_path:
        return
    path = storage_root() / relative_path
    if path.exists():
        path.unlink()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def seal_embedding(vector: list[float]) -> bytes:
    payload = struct.pack(f"<{len(vector)}f", *[float(value) for value in vector])
    return encrypt_bytes(payload)


def open_embedding(blob: bytes) -> list[float]:
    payload = decrypt_bytes(blob)
    count = len(payload) // 4
    return list(struct.unpack(f"<{count}f", payload[: count * 4]))
