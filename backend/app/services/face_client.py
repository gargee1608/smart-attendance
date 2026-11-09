import httpx
from fastapi import HTTPException

from app.config import get_settings


class FaceEngineClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or get_settings().face_engine_url).rstrip("/")

    async def analyze(self, image: bytes) -> dict:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{self.base_url}/v1/analyze",
                    files={"image": ("frame.jpg", image, "application/octet-stream")},
                )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"Face engine unavailable: {exc}") from exc
        if response.status_code >= 400:
            detail = response.text[:300]
            raise HTTPException(status_code=503, detail=f"Face engine error: {detail}")
        return response.json()

    async def capture_rtsp(self, url: str) -> bytes:
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                response = await client.post(f"{self.base_url}/v1/capture-rtsp", json={"url": url})
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"Face engine unavailable: {exc}") from exc
        if response.status_code >= 400:
            raise HTTPException(status_code=400, detail=response.json().get("detail", "Could not read the camera"))
        return response.content

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/health")
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError:
            return {"status": "down"}
        return {"status": "down"}


def get_face_client() -> FaceEngineClient:
    return FaceEngineClient()
