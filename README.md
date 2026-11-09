# Smart Attendance

Install and daily operations: [Install guide and SOP](docs/INSTALL_AND_SOP.md).

Self-hosted attendance and academy register for one Ubuntu 24.04 server. Each organization is a tenant. Photos, face embeddings, attendance, and reports for one organization are not readable by another.

Version 1 runs the whole stack with Docker Compose: FastAPI, a separate face engine, React, Nginx, PostgreSQL 16 with pgvector and row-level security, Redis, Celery, and Ollama (`qwen2.5:7b`) behind an internal AI service.

Face models, all commercially usable:

- YuNet face detection (MIT), via OpenCV
- SFace face embeddings (Apache-2.0), via OpenCV DNN
- MiniFASNet V1SE and V2 liveness (Apache-2.0), via ONNX Runtime

InsightFace pretrained weights are not used.

## Hardware

| | Minimum | Comfortable |
| --- | --- | --- |
| CPU | 4 cores | 8 cores |
| RAM | 16 GB | 32 GB |
| Disk | 64 GB SSD | 256 GB SSD |
| GPU | none | optional, for Ollama |

The 7B model is the heavy part. Attendance, enrollment, and recognition run without it. A GPU is optional; CPU inference is supported and the model is unloaded after each suggestion (`OLLAMA_KEEP_ALIVE=0`).

An IP camera, if you use one, must be reachable from the server on an `rtsp://` or `rtsps://` URL.

## Install

On a fresh Ubuntu 24.04 machine, from this directory:

```bash
sudo bash install.sh
```

`install.sh` installs Docker Engine and the Compose plugin if they are missing, creates `/opt/smartattendance`, generates secrets, downloads the face models, builds the containers, runs migrations, creates the first super admin, and pulls `qwen2.5:7b`.

Layout:

```text
/opt/smartattendance/
  application/          backend, frontend, ai_engine, api, compose file
  storage/organizations/ORG-xxxx/{academy_logo,coaches,students,face_images,unknown_faces,attendance,reports}
  storage/{imports,exports,temporary}
  database/
  backups/
  logs/
  config/.env
  config/initial_admin_password.txt
```

Face photos are AES-GCM encrypted on disk (`SAENC1` files). Embeddings are stored in `face_embeddings`, separate from those files, with an encrypted copy in `embedding_sealed`. The `vector(128)` column stays readable by Postgres so pgvector can search it. That column is protected by row-level security, the database port is not published, and backups of the database are encrypted. Keep port 5432 off the public network.

Re-running `install.sh` keeps the existing `.env` and database volume.

### First login

```bash
sudo cat /opt/smartattendance/config/initial_admin_password.txt
```

Open:

- http://SERVER:18480
- https://SERVER:18443 (self-signed certificate; the browser will warn you)

Email default: `admin@smartattendance.local`, unless you set `INITIAL_ADMIN_EMAIL` before the first install.

Sign in, create an organization, choose it in the sidebar if you are the super admin, then add a batch and a person. Upload more than one photo. The profile stays unenrolled until you press **Confirm enrollment**.

### Add an RTSP camera

1. Sign in as an organization admin, coach, or teacher.
2. Open **Cameras**.
3. Name the camera and paste a URL such as `rtsp://user:password@192.168.1.20:554/stream`.
4. Press **Capture one frame**.

The server opens the stream with OpenCV, runs detection, liveness, and SFace, then checks in each known person. A face under the confidence threshold is queued as an unnamed `UNK-` group. It is not labeled with a guess, and it is not described as a threat.

You can also check someone in from **Attendance** with a phone or webcam photo. A second check-in for the same person, batch, and day does not create a second row.

## Day to day

Backup (encrypted `pg_dump` plus the storage tree):

```bash
sudo bash /opt/smartattendance/application/backup.sh
```

Restore (destructive):

```bash
sudo RESTORE_CONFIRM=yes bash /opt/smartattendance/application/backup.sh restore /opt/smartattendance/backups/smartattendance-YYYYMMDDHHMMSS.tar.gz.enc
```

Update from a git checkout installed under `/opt/smartattendance/application`. If the health check fails, the previous commit is checked out and rebuilt.

```bash
sudo bash /opt/smartattendance/application/update.sh
```

Tests, from the application directory, with the stack up:

```bash
docker compose run --rm --entrypoint pytest backend -q
```

The tests cover tenant isolation (API and row-level security), recognition threshold and unknown results, and attendance de-duplication.

## What version 1 does

Working end to end:

- Organization onboarding and tenant isolation (`org_id` plus forced row-level security)
- Users and JWT roles: super admin, org admin, coach, teacher, student (parent is reserved)
- Sports, batches, classes, sessions
- Enrollment with several photos; embeddings stored apart from encrypted images; enrollment only after staff confirmation
- Face engine: detect, quality, liveness, embed, pgvector search, confidence threshold, unknown when unsure or ambiguous
- Unknown-face grouping and a review queue (assign, register, visitor, dismiss, delete). Review does not train the gallery by itself
- Attendance from a photo or an RTSP frame, duplicate prevention, corrections with an audit log, monthly percentage with a configurable policy
- External API under `/api/v1` with API keys and per-key rate limits
- Audit log

Present, with a clear next step rather than a finished product:

- Performance metrics accept a JSON blob. Per-sport templates are still to do.
- AI comments call Ollama and are stored as suggestions. They are not coach comments.
- Reports export CSV. PDF and Excel are still to do.
- CSV import creates people and does not enroll faces. Excel, a mapping screen, and bulk photos are still to do.
- Backup is `pg_dump` plus an encrypted archive. `restic` runs only if `RESTIC_REPOSITORY` is set and `restic` is installed.
- Updates pull the current git branch, rebuild, and roll back on a failed health check. Release tags are still to do.

## External API

Send `X-API-Key`. The key is shown once when an org admin creates it. Calls are limited per key per minute (default 60) and can only see that organization's data.

- `POST /api/v1/persons`
- `POST /api/v1/persons/{id}/photos`
- `POST /api/v1/recognition/identify`
- `POST /api/v1/recognition/batch`
- `GET /api/v1/persons/{id}`
- `GET /api/v1/unknown`
- `POST /api/v1/unknown/{id}/resolve`
- `DELETE /api/v1/persons/{id}` removes biometric data and does not invent a replacement identity

A result below the match threshold (default cosine 0.363, the OpenCV SFace operating point) or within the ambiguity margin of a second person is `unknown`. The response includes confidence and does not include a name.

## Troubleshooting

**The site does not load.** `docker compose -f /opt/smartattendance/application/docker-compose.yml ps` and `docker compose -f /opt/smartattendance/application/docker-compose.yml logs --tail 80 backend nginx`.

**Health says the database is down.** The migrate container may have failed. `docker compose -f /opt/smartattendance/application/docker-compose.yml logs migrate`. If the error is a connection timeout and `sudo iptables-legacy -S FORWARD` shows a DROP policy, the legacy firewall is blocking the Docker bridge. `install.sh` opens that policy on Ubuntu. You can also run `sudo iptables-legacy -P FORWARD ACCEPT`.

**Face check-in says the face engine is unavailable.** Models may still be downloading, or a checksum failed. `docker compose -f /opt/smartattendance/application/docker-compose.yml logs face-engine`. Re-run `sudo bash /opt/smartattendance/application/scripts/download_models.sh`.

**Every face is unknown.** The person is not confirmed as enrolled, the photo is small or blurry, liveness failed, or the score is under the organization's match threshold. Open **Attendance → Policy** to see the threshold. Do not lower it just to force a name.

**A person in another organization was recognized.** That should not happen. Recognition searches only the current organization's enrolled embeddings, and the database role used by the API cannot bypass row-level security. Check `GET /api/health` and the audit log, and do not publish port 5432.

**The camera capture fails.** From the server, the RTSP URL has to connect. Test with `ffprobe` or VLC on the server. The URL must start with `rtsp://` or `rtsps://`.

**AI suggestions return 503.** Pull the model: `docker compose -f /opt/smartattendance/application/docker-compose.yml exec ollama ollama pull qwen2.5:7b`. On a 16 GB machine, close other heavy jobs first. A failed suggestion is not stored as a coach comment.

**Browser warns on HTTPS.** The installer makes a self-signed certificate. Use HTTP on port 18480 inside a trusted network, or replace `/etc/nginx/certs` in the nginx container with a real certificate.

**Forgot the super admin password.** Set a new `INITIAL_ADMIN_PASSWORD` only helps before the account exists. After that, change it in the database with a new argon2 hash, or create another super admin from `python -m app.cli` inside the backend container after updating the email. Do not share the `.env` file.

## Develop without install.sh

From this directory, with Docker already running:

```bash
docker compose up -d --build
docker compose run --rm --entrypoint pytest backend -q
```

Development defaults are in `docker-compose.yml` and `.env.example`. Do not expose that stack on a public network. `install.sh` is what generates production secrets and refuses the default ones.
