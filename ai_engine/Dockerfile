FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libglib2.0-0 libgl1 libgomp1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY ai_engine/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY ai_engine /app
COPY scripts/download_models.sh /app/download_models.sh
COPY ai_engine/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /app/download_models.sh
ENV PYTHONUNBUFFERED=1 MODEL_DIR=/models
EXPOSE 8090
ENTRYPOINT ["/entrypoint.sh"]
