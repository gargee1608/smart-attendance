import hashlib
import math
import struct


def normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def deterministic_vector(seed: str, dim: int = 128) -> list[float]:
    """Stable unit vector used by tests. Production embeddings come from SFace."""
    digest = hashlib.sha256(seed.encode()).digest()
    values: list[float] = []
    counter = 0
    while len(values) < dim:
        block = hashlib.sha256(digest + counter.to_bytes(4, "little")).digest()
        counter += 1
        for offset in range(0, 32, 4):
            raw = struct.unpack("<I", block[offset : offset + 4])[0] / 2**32
            values.append(raw * 2 - 1)
    return normalize(values[:dim])


def vector_at_cosine(base: list[float], cosine: float, salt: str) -> list[float]:
    base = normalize(base)
    other = deterministic_vector(salt)
    dot = sum(a * b for a, b in zip(base, other))
    ortho = normalize([b - dot * a for a, b in zip(base, other)])
    sine = math.sqrt(max(0.0, 1.0 - cosine * cosine))
    return normalize([cosine * a + sine * b for a, b in zip(base, ortho)])


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def vec_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{float(value):.8f}" for value in vector) + "]"


def parse_vec(value) -> list[float]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [float(item) for item in value]
    text = str(value).strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if not text:
        return []
    return [float(part) for part in text.split(",")]


def mean_normalize(old: list[float], count: int, new: list[float]) -> list[float]:
    mixed = [((old_value * count) + new_value) / (count + 1) for old_value, new_value in zip(old, new)]
    return normalize(mixed)
