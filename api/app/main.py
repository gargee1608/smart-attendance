"""Swappable internal AI service. v1 talks to Ollama; the HTTP contract stays here.

TODO: support a second backend (llama.cpp or a hosted model) without changing
the academy API. Suggestions are never official records.
"""

import os

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Smart Attendance AI Service", version="1.0.0")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/")
MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")


class CommentIn(BaseModel):
    prompt: str
    person_id: str | None = None


@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
        names = [item.get("name", "") for item in response.json().get("models", [])]
        ready = any(name.split(":")[0] == MODEL.split(":")[0] for name in names)
        return {"status": "ok" if ready else "model-missing", "model": MODEL, "installed": names}
    except httpx.HTTPError as exc:
        return {"status": "down", "model": MODEL, "detail": str(exc)}


@app.post("/v1/comment")
async def comment(body: CommentIn):
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": MODEL, "prompt": body.prompt, "stream": False},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Ollama is unavailable. Pull the model with: docker compose exec ollama ollama pull {MODEL}. {exc}",
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=503,
            detail=f"Ollama could not generate a suggestion ({response.status_code}). Is {MODEL} pulled?",
        )
    text = (response.json().get("response") or "").strip()
    return {
        "text": text,
        "model": MODEL,
        "kind": "suggestion",
        "disclaimer": "AI suggestion only. Not a coach comment.",
    }
