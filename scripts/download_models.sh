#!/usr/bin/env bash
# Commercial-use face models only: OpenCV YuNet (MIT), OpenCV SFace (Apache-2.0),
# MiniFASNet V2 and V1SE (Apache-2.0). Checksums are verified before the files are kept.
set -euo pipefail

MODEL_DIR="${MODEL_DIR:-$(cd "$(dirname "$0")/.." && pwd)/models}"
mkdir -p "$MODEL_DIR"

download() {
  local name="$1" min_bytes="$2" sha="$3"
  shift 3
  local dest="$MODEL_DIR/$name"
  if [[ -f "$dest" ]]; then
    if echo "$sha  $dest" | sha256sum -c - >/dev/null 2>&1; then
      echo "$name already present"
      return 0
    fi
    echo "$name checksum mismatch, downloading again"
    rm -f "$dest"
  fi
  local url
  for url in "$@"; do
    echo "Fetching $name"
    if curl -fL --retry 3 --retry-delay 2 --max-time 180 -o "$dest.partial" "$url"; then
      local size
      size=$(wc -c < "$dest.partial")
      if [[ "$size" -ge "$min_bytes" ]] && echo "$sha  $dest.partial" | sha256sum -c -; then
        mv "$dest.partial" "$dest"
        return 0
      fi
    fi
    rm -f "$dest.partial"
  done
  echo "Could not download $name" >&2
  exit 1
}

download face_detection_yunet_2023mar.onnx 100000 \
  8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4 \
  "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx" \
  "https://huggingface.co/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx?download=true"

download face_recognition_sface_2021dec.onnx 1000000 \
  0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79 \
  "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx" \
  "https://huggingface.co/opencv/face_recognition_sface/resolve/main/face_recognition_sface_2021dec.onnx?download=true"

download MiniFASNetV1SE.onnx 100000 \
  ebab7f90c7833fbccd46d3a555410e78d969db5438e169b6524be444862b3676 \
  "https://github.com/yakhyo/face-anti-spoofing/releases/download/weights/MiniFASNetV1SE.onnx"

download MiniFASNetV2.onnx 100000 \
  b32929adc2d9c34b9486f8c4c7bc97c1b69bc0ea9befefc380e4faae4e463907 \
  "https://github.com/yakhyo/face-anti-spoofing/releases/download/weights/MiniFASNetV2.onnx"

echo "Models ready in $MODEL_DIR"
