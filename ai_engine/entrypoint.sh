#!/bin/sh
set -eu
mkdir -p /models
if [ ! -s /models/face_detection_yunet_2023mar.onnx ] || [ ! -s /models/face_recognition_sface_2021dec.onnx ] || [ ! -s /models/MiniFASNetV1SE.onnx ] || [ ! -s /models/MiniFASNetV2.onnx ]; then
  MODEL_DIR=/models /app/download_models.sh || echo "Model download did not finish; starting with whatever verified files are present."
fi
if [ ! -s /models/face_detection_yunet_2023mar.onnx ] || [ ! -s /models/face_recognition_sface_2021dec.onnx ]; then
  echo "YuNet and SFace are required." >&2
  exit 1
fi
if [ ! -s /models/MiniFASNetV1SE.onnx ] && [ ! -s /models/MiniFASNetV2.onnx ]; then
  echo "At least one MiniFASNet ONNX file is required." >&2
  exit 1
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8090
