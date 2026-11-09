from fastapi import HTTPException

from app.services.vectors import deterministic_vector, vector_at_cosine


class FakeFace:
    """Deterministic stand-in for YuNet/SFace/MiniFASNet.

    Production traffic uses the face-engine service. Tests pass image bytes
    whose first line is FACEID:<seed> or FACEID:<seed>@<cosine>.
    """

    async def analyze(self, image: bytes) -> dict:
        if not image.startswith(b"FACEID:"):
            return {"faces": []}
        token = image.split(b"\n", 1)[0].decode()[7:]
        if token == "NONE":
            return {"faces": []}
        if token == "MULTI":
            return {"faces": [self._pack(deterministic_vector("multi-a")), self._pack(deterministic_vector("multi-b"))]}
        if token == "BLUR":
            face = self._pack(deterministic_vector("blur"))
            face["acceptable"] = False
            face["quality"] = 0.05
            face["notes"] = ["blurry"]
            return {"faces": [face]}
        if token == "SPOOF":
            face = self._pack(deterministic_vector("spoof"))
            face["live"] = False
            face["liveness"] = 0.05
            return {"faces": [face]}
        if "@" in token:
            seed, cosine = token.split("@", 1)
            vector = vector_at_cosine(deterministic_vector(seed), float(cosine), seed + "~ortho")
        else:
            vector = deterministic_vector(token)
        return {"faces": [self._pack(vector)]}

    def _pack(self, vector: list[float]) -> dict:
        return {
            "bbox": [8, 8, 140, 160],
            "det_score": 0.99,
            "quality": 0.96,
            "acceptable": True,
            "notes": [],
            "blur": 220.0,
            "brightness": 128.0,
            "liveness": 0.98,
            "live": True,
            "embedding": vector,
            "model": "sface_2021dec",
            "width": 140,
            "height": 160,
        }

    async def capture_rtsp(self, url: str) -> bytes:
        if not url.startswith(("rtsp://", "rtsps://")):
            raise HTTPException(status_code=400, detail="Camera URL must be RTSP")
        return b"FACEID:camera-person\n"

    async def health(self) -> dict:
        return {"status": "ok", "models": {"yunet": True, "sface": True, "minifasnet": True}}
