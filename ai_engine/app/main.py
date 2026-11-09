from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.pipeline import FacePipeline, grab_rtsp

app = FastAPI(title="Smart Attendance Face Engine", version="1.0.0")
pipeline = FacePipeline()


class RtspIn(BaseModel):
    url: str


def _image_or_400(payload: bytes):
    image = pipeline.decode(payload)
    if image is None:
        raise HTTPException(status_code=400, detail="Could not read that image")
    return image


@app.get("/health")
def health():
    return pipeline.status()


@app.post("/v1/detect")
async def detect(image: UploadFile = File(...)):
    if not pipeline.detector:
        raise HTTPException(status_code=503, detail="YuNet model is not loaded")
    frame = _image_or_400(await image.read())
    faces = pipeline.detect(frame)
    return {"faces": [{"bbox": item["bbox"], "det_score": item["det_score"]} for item in faces]}


@app.post("/v1/embed")
async def embed(image: UploadFile = File(...)):
    if not pipeline.recognizer:
        raise HTTPException(status_code=503, detail="SFace model is not loaded")
    frame = _image_or_400(await image.read())
    faces = pipeline.analyze(frame)
    return {"faces": [{"bbox": item["bbox"], "embedding": item["embedding"], "model": item["model"]} for item in faces]}


@app.post("/v1/quality")
async def quality(image: UploadFile = File(...)):
    frame = _image_or_400(await image.read())
    faces = pipeline.detect(frame)
    return {
        "faces": [
            {"bbox": item["bbox"], **pipeline.quality(frame, item["bbox"], item["det_score"])}
            for item in faces
        ]
    }


@app.post("/v1/liveness")
async def liveness(image: UploadFile = File(...)):
    frame = _image_or_400(await image.read())
    faces = pipeline.detect(frame)
    return {"faces": [{"bbox": item["bbox"], **pipeline.liveness(frame, item["bbox"])} for item in faces]}


@app.post("/v1/analyze")
async def analyze(image: UploadFile = File(...)):
    if not pipeline.detector or not pipeline.recognizer:
        raise HTTPException(status_code=503, detail="Face models are not loaded")
    frame = _image_or_400(await image.read())
    return {"faces": pipeline.analyze(frame)}


@app.post("/v1/capture-rtsp")
def capture(body: RtspIn):
    try:
        frame = grab_rtsp(body.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read the camera: {exc}") from exc
    from fastapi.responses import Response

    return Response(content=frame, media_type="image/jpeg")
