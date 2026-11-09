# Face models

Weights are downloaded by `scripts/download_models.sh`. They are not committed.

| File | Model | License | Source |
| --- | --- | --- | --- |
| face_detection_yunet_2023mar.onnx | YuNet face detector | MIT | opencv/opencv_zoo |
| face_recognition_sface_2021dec.onnx | SFace embeddings | Apache-2.0 | opencv/opencv_zoo |
| MiniFASNetV2.onnx | MiniFASNetV2 silent anti-spoof, crop scale 2.7 | Apache-2.0 | MiniVision Silent-Face-Anti-Spoofing, ONNX export |
| MiniFASNetV1SE.onnx | MiniFASNetV1SE silent anti-spoof, crop scale 4.0 | Apache-2.0 | MiniVision Silent-Face-Anti-Spoofing, ONNX export |

The language model is `qwen2.5:7b` via Ollama (Qwen2.5 is Apache-2.0).

InsightFace pretrained weights are not downloaded or loaded.
