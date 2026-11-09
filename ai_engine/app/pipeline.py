"""YuNet + SFace + MiniFASNet. No InsightFace weights."""

from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

MODEL_DIR = Path(os.environ.get("MODEL_DIR", "/models"))
YUNET = MODEL_DIR / "face_detection_yunet_2023mar.onnx"
SFACE = MODEL_DIR / "face_recognition_sface_2021dec.onnx"
MINI_V2 = MODEL_DIR / "MiniFASNetV2.onnx"
MINI_V1 = MODEL_DIR / "MiniFASNetV1SE.onnx"


class FacePipeline:
    def __init__(self) -> None:
        self.detector = None
        self.recognizer = None
        self.liveness_models: list[tuple[ort.InferenceSession, float, str, str]] = []
        self.errors: list[str] = []
        self._load()

    def _load(self) -> None:
        if YUNET.exists() and SFACE.exists():
            self.detector = cv2.FaceDetectorYN.create(str(YUNET), "", (320, 320), 0.6, 0.3, 5000)
            self.recognizer = cv2.FaceRecognizerSF.create(str(SFACE), "")
        else:
            self.errors.append("YuNet or SFace model file is missing")
        for path, scale in ((MINI_V2, 2.7), (MINI_V1, 4.0)):
            if not path.exists():
                self.errors.append(f"missing {path.name}")
                continue
            session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
            self.liveness_models.append((session, scale, session.get_inputs()[0].name, session.get_outputs()[0].name))

    def ready(self) -> bool:
        return self.detector is not None and self.recognizer is not None and len(self.liveness_models) > 0

    def status(self) -> dict:
        return {
            "status": "ok" if self.ready() else "degraded",
            "models": {
                "yunet": YUNET.exists(),
                "sface": SFACE.exists(),
                "minifasnet_v2": MINI_V2.exists(),
                "minifasnet_v1se": MINI_V1.exists(),
            },
            "errors": self.errors,
            "licenses": {
                "yunet": "MIT",
                "sface": "Apache-2.0",
                "minifasnet": "Apache-2.0",
            },
        }

    def decode(self, payload: bytes):
        array = np.frombuffer(payload, dtype=np.uint8)
        image = cv2.imdecode(array, cv2.IMREAD_COLOR)
        return image

    def detect(self, image: np.ndarray) -> list[dict]:
        if self.detector is None:
            raise RuntimeError("YuNet is not loaded")
        height, width = image.shape[:2]
        self.detector.setInputSize((width, height))
        _ok, faces = self.detector.detect(image)
        found = []
        if faces is None:
            return found
        for row in faces:
            values = [float(item) for item in row]
            x, y, w, h = values[:4]
            score = values[-1]
            found.append({"raw": values, "bbox": [x, y, w, h], "det_score": score})
        found.sort(key=lambda item: item["det_score"], reverse=True)
        return found

    def embed(self, image: np.ndarray, raw_face: list[float]) -> list[float]:
        if self.recognizer is None:
            raise RuntimeError("SFace is not loaded")
        face_row = np.asarray(raw_face, dtype=np.float32)
        aligned = self.recognizer.alignCrop(image, face_row)
        feature = self.recognizer.feature(aligned)
        vector = feature.flatten().astype(np.float32)
        norm = float(np.linalg.norm(vector)) or 1.0
        return [float(value) for value in (vector / norm)]

    def quality(self, image: np.ndarray, bbox: list[float], det_score: float) -> dict:
        height, width = image.shape[:2]
        x, y, w, h = [int(round(value)) for value in bbox]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(width, x + max(w, 1)), min(height, y + max(h, 1))
        crop = image[y0:y1, x0:x1]
        notes = []
        if crop.size == 0 or min(w, h) < 60:
            notes.append("face is too small")
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.size else None
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var()) if gray is not None else 0.0
        brightness = float(gray.mean()) if gray is not None else 0.0
        if blur < 15:
            notes.append("blurry")
        if brightness < 30 or brightness > 230:
            notes.append("poor lighting")
        if det_score < 0.6:
            notes.append("low detection score")
        size_score = min(1.0, min(max(w, 0), max(h, 0)) / 160.0)
        blur_score = min(1.0, blur / 180.0)
        light_score = 1.0 if 40 <= brightness <= 210 else 0.25
        score = 0.35 * det_score + 0.25 * size_score + 0.25 * blur_score + 0.15 * light_score
        return {
            "quality": round(float(score), 4),
            "acceptable": not notes and score >= 0.45,
            "notes": notes,
            "blur": round(blur, 2),
            "brightness": round(brightness, 2),
        }

    def _crop_for_liveness(self, image: np.ndarray, bbox: list[float], scale: float, size: int) -> np.ndarray:
        height, width = image.shape[:2]
        x, y, box_w, box_h = bbox
        fitted = min(scale, (height - 1) / max(box_h, 1), (width - 1) / max(box_w, 1))
        new_w, new_h = box_w * fitted, box_h * fitted
        center_x, center_y = x + box_w / 2, y + box_h / 2
        x1 = max(0, int(center_x - new_w / 2))
        y1 = max(0, int(center_y - new_h / 2))
        x2 = min(width - 1, int(center_x + new_w / 2))
        y2 = min(height - 1, int(center_y + new_h / 2))
        crop = image[y1 : y2 + 1, x1 : x2 + 1]
        if crop.size == 0:
            crop = image
        return cv2.resize(crop, (size, size))

    def _softmax(self, logits: np.ndarray) -> np.ndarray:
        shifted = logits - np.max(logits, axis=1, keepdims=True)
        exp = np.exp(shifted)
        return exp / np.sum(exp, axis=1, keepdims=True)

    def liveness(self, image: np.ndarray, bbox: list[float]) -> dict:
        if not self.liveness_models:
            return {"liveness": 0.0, "live": False, "notes": ["liveness model is not loaded"]}
        real_scores = []
        for session, scale, input_name, output_name in self.liveness_models:
            shape = session.get_inputs()[0].shape
            size = 80
            if len(shape) >= 4 and isinstance(shape[2], int) and shape[2] > 0:
                size = int(shape[2])
            crop = self._crop_for_liveness(image, bbox, scale, size)
            tensor = crop.astype(np.float32).transpose(2, 0, 1)[None, ...]
            logits = session.run([output_name], {input_name: tensor})[0]
            logits = np.asarray(logits, dtype=np.float32)
            if logits.ndim == 1:
                logits = logits[None, :]
            total = float(np.sum(logits))
            probs = logits if 0.98 <= total <= 1.02 and np.all(logits >= 0) else self._softmax(logits)
            real_index = 1 if probs.shape[1] > 1 else 0
            real_scores.append(float(probs[0, real_index]))
        score = float(sum(real_scores) / len(real_scores))
        return {"liveness": round(score, 4), "live": score >= 0.5, "notes": [] if score >= 0.5 else ["failed liveness"]}

    def analyze(self, image: np.ndarray) -> list[dict]:
        faces = []
        for found in self.detect(image):
            quality = self.quality(image, found["bbox"], found["det_score"])
            live = self.liveness(image, found["bbox"])
            embedding = self.embed(image, found["raw"])
            faces.append(
                {
                    "bbox": [round(value, 2) for value in found["bbox"]],
                    "det_score": round(found["det_score"], 4),
                    "quality": quality["quality"],
                    "acceptable": quality["acceptable"] and live["live"],
                    "notes": quality["notes"] + live["notes"],
                    "blur": quality["blur"],
                    "brightness": quality["brightness"],
                    "liveness": live["liveness"],
                    "live": live["live"],
                    "embedding": embedding,
                    "model": "sface_2021dec",
                    "width": int(image.shape[1]),
                    "height": int(image.shape[0]),
                }
            )
        return faces


def grab_rtsp(url: str) -> bytes:
    if not url.startswith(("rtsp://", "rtsps://")):
        raise ValueError("Only rtsp:// and rtsps:// URLs are accepted")
    capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)
    capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000)
    ok, frame = capture.read()
    capture.release()
    if not ok or frame is None:
        raise RuntimeError("Could not read a frame from the camera")
    ok, encoded = cv2.imencode(".jpg", frame)
    if not ok:
        raise RuntimeError("Could not encode the camera frame")
    return encoded.tobytes()
