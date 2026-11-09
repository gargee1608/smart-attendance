# AI Smart Attendance & Academy Management System v1: Installation Guide and Standard Operating Procedure

| | |
| --- | --- |
| Product | AI Smart Attendance & Academy Management System ("Smart Attendance"), version 1 |
| Repository | https://github.com/gargee1608/smart-attendance (private, branch `main`) |
| Install path | `/opt/smartattendance` |
| Audience | Owner (Gargee), the operations team and the IT administrator |
| Document date | 9 November 2026 |

This guide explains how to install Smart Attendance on a new Linux server and how to run it every day. Every command can be copied and pasted. Replace `SERVER_IP` with your server's IP address. Values in `ALL_CAPS` are placeholders you fill in.

---

## 1. Overview and architecture

Smart Attendance is a self-hosted attendance and academy register. One server can host several organizations (academies, schools, colleges, coaching institutes). Each organization is a separate tenant. Its photos, face data, attendance and reports can't be read by any other organization. This is enforced by an `org_id` on every table and by PostgreSQL row-level security.

Docker Compose runs the whole stack. The Compose project is called `smartattendance`, so containers are named `smartattendance-<service>-1`. For example, the Ollama container is `smartattendance-ollama-1`.

| Service (compose name) | What it does | Port |
| --- | --- | --- |
| `nginx` | Serves the React web app and proxies `/api/` to the backend. Applies rate limiting (30 requests/s per IP) and a 32 MB upload limit. Creates a self-signed TLS certificate on first start. | Host **18480** (HTTP), host **18443** (HTTPS) |
| React frontend | Built into the `nginx` image (Vite build). Not a separate container. | Served by nginx |
| `backend` | FastAPI application: login (JWT), organizations, people, batches, attendance, review queue, cameras, reports, external API under `/api/v1`. | 8000 (internal only) |
| `migrate` | One-shot job. Applies database migrations and creates the first super admin, then exits. | none |
| `worker` | Celery worker for background jobs. | none |
| `face-engine` | Face pipeline: **YuNet** detection (MIT), **SFace** 128-d embeddings (Apache-2.0), **MiniFASNet** V1SE/V2 liveness (Apache-2.0). InsightFace weights are not used. | 8090 (internal only) |
| `postgres` | PostgreSQL 16 with **pgvector**, used for data and vector search. | 5432 (internal only, never published) |
| `redis` | Redis 7, used as the cache and Celery broker. | 6379 (internal only) |
| `ai-service` | Internal AI service that turns attendance and notes into *suggested* comments. | 8091 (internal only) |
| `ollama` | Runs the local LLM **qwen2.5:7b**. The model is unloaded after each request (`OLLAMA_KEEP_ALIVE=0`). | 11434 (internal only) |

How a check-in works: photo or camera frame → face detection (YuNet) → quality and liveness check (MiniFASNet) → embedding (SFace) → pgvector search within the current organization only → **known** (name plus confidence) or **unknown**. A face is unknown when its score is below the threshold (default cosine 0.363) or within the ambiguity margin (0.05) of a second person. An unknown face is never given a guessed name. It goes to the review queue instead.

**Access URLs**

- `http://SERVER_IP:18480`: main app
- `https://SERVER_IP:18443`: HTTPS with a self-signed certificate. The browser shows a warning, which is expected.
- `http://SERVER_IP:18480/api/health`: health check (JSON)

### 1.1 Directory layout on the server

```text
/opt/smartattendance/
  application/        code copied from the repo by install.sh (backend, frontend, ai_engine, api,
                      nginx, scripts, docker-compose.yml, install.sh, update.sh, backup.sh)
    models/           face model files (*.onnx) downloaded by scripts/download_models.sh
    .env              symlink to ../config/.env
  config/
    .env                          all secrets (mode 600), never share or commit it
    initial_admin_password.txt    first super-admin password (mode 600)
  storage/
    organizations/ORG-0001/{academy_logo,coaches,students,face_images,unknown_faces,attendance,reports}
    imports/  exports/  temporary/
  database/           PostgreSQL data files
  redis/              Redis append-only data
  ollama/             downloaded LLM models (qwen2.5:7b is about 4.7 GB)
  backups/            encrypted backup archives and pre-update commit markers
  logs/               log folder (v1 services log to Docker; use it for cron logs)
```

---

## 2. Hardware and operating system requirements

| | Minimum | Recommended |
| --- | --- | --- |
| CPU | 4 cores | 8 cores |
| RAM | 16 GB | 32 GB |
| Disk | 64 GB | 200 GB SSD |
| GPU | not required | optional NVIDIA GPU (speeds up the AI suggestions only) |

- **Operating system:** officially tested on **Ubuntu 24.04 LTS**. Also confirmed working on **Ubuntu 26.04** with the workaround in section 4.
- The qwen2.5:7b model is the heaviest part. Attendance, enrollment and recognition don't need it.
- Internet access is needed during install to fetch Docker images, Python and npm packages, face models (from GitHub and Hugging Face) and the Ollama model.
- An IP camera, if you use one, must be reachable from the server on an `rtsp://` or `rtsps://` URL.
- You need a user account with `sudo` rights and the GitHub account **gargee1608**, or a collaborator account with access to the private repo.

---

## 3. Installation SOP (Ubuntu 24.04)

Run these steps in order on the new server. Using Ubuntu 26.04? Do section 4 between steps 6 and 7.

**Step 1: Update the system.**

```bash
sudo apt update && sudo apt -y upgrade
```

**Step 2: Install git, curl and the GitHub CLI.**

```bash
sudo apt -y install git curl gh
```

**Step 3: Install Docker.** `install.sh` installs Docker Engine and the Compose plugin from Docker's official repository if they're missing, so on Ubuntu 24.04 you can skip this step. To install Docker yourself first:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo systemctl enable --now docker
sudo docker run --rm hello-world
```

**Step 4: Sign in to GitHub.**

```bash
gh auth login
```

Answer the prompts as follows:

- Where do you use GitHub? → **GitHub.com**
- Preferred protocol? → **HTTPS**
- Authenticate Git with your GitHub credentials? → **Yes**
- How would you like to authenticate? → **Login with a web browser**. Copy the one-time code, open https://github.com/login/device on any device, and sign in as **gargee1608**.

Check the login:

```bash
gh auth status
```

**Step 5: Clone the repository.**

```bash
cd ~
gh repo clone gargee1608/smart-attendance
```

**Step 6: Go into the folder.**

```bash
cd ~/smart-attendance
```

**Step 7: Run the installer and keep a log.**

```bash
sudo bash install.sh 2>&1 | tee ~/install.log
```

The installer:

1. Checks that the OS is Ubuntu 24.04.
2. Installs `ca-certificates curl gnupg rsync openssl`, and Docker if it's missing.
3. Opens the legacy iptables FORWARD policy if it's set to DROP, so containers can reach each other.
4. Creates `/opt/smartattendance` and copies the code to `/opt/smartattendance/application`.
5. Generates strong secrets in `/opt/smartattendance/config/.env`, but only on the first run.
6. Downloads and checksum-verifies the four face models.
7. Builds and starts all containers. This takes 10–30 minutes on first install.
8. Waits up to 5 minutes for `/api/health`.
9. Pulls `qwen2.5:7b` (about 4.7 GB).

When it finishes, it prints:

```text
Smart Attendance is running.
  HTTP   http://127.0.0.1:18480
  HTTPS  https://127.0.0.1:18443
  Email  admin@smartattendance.local
  Password file  /opt/smartattendance/config/initial_admin_password.txt
```

> If the only failure is "Ollama model pull failed", the app is still up and working. See troubleshooting item 9.4.

**Step 8: Read the first admin password.**

```bash
sudo cat /opt/smartattendance/config/initial_admin_password.txt
```

**Step 9: Find the server IP.**

```bash
hostname -I
```

Use the first address shown, for example `192.168.1.50`.

**Step 10: Log in.** From a computer on the same network, open `http://SERVER_IP:18480` and sign in:

- Email: `admin@smartattendance.local`. This is the default; it's different only if you exported `INITIAL_ADMIN_EMAIL` before the first install.
- Password: the value from step 8.

**Step 11: Change the admin password.** v1 has no "change password" screen, and the super-admin account can't be edited from the web app. Change it from the server with this command. It asks for the email and the new password, which won't be shown as you type it.

```bash
cd /opt/smartattendance/application
sudo docker compose exec backend python -c "
import getpass, psycopg
from app.config import get_settings
from app.security import hash_password
from app.cli import libpq
email = input('Admin email: ').strip().lower()
pw = getpass.getpass('New password (min 12 chars): ')
assert len(pw) >= 12, 'Password too short'
with psycopg.connect(libpq(get_settings().migration_database_url), autocommit=True) as conn:
    n = conn.execute('UPDATE users SET password_hash = %s WHERE lower(email) = %s', (hash_password(pw), email)).rowcount
print('Updated', n, 'account(s)')
"
```

Sign out and back in with the new password. Then delete the plain-text first password:

```bash
sudo shred -u /opt/smartattendance/config/initial_admin_password.txt
```

Keep the new password in a password manager. You don't need `INITIAL_ADMIN_PASSWORD` in `.env` again, because the admin account already exists. Leave it in place, though: in production the backend refuses to start if that value is missing or weak.

Organization-level accounts (org admin, coach, teacher) can have their password reset by an org admin or super admin through `PATCH /api/users/{user_id}` with `{"password": "..."}`. v1 has no button for this.

**Step 12: Check the installation.**

```bash
cd /opt/smartattendance/application
sudo docker compose ps
curl -s http://127.0.0.1:18480/api/health; echo
```

Expected result: all services `Up`, `backend` and `face-engine` showing `(healthy)`, `migrate` exited with code 0, and health returning `{"status":"ok","checks":{"database":"ok","redis":"ok","face_engine":"ok"}}`.

**Step 13: Tidy the clone (Ubuntu 26.04 only).** If you patched `install.sh` (section 4), see section 7.3 before your first update.

---

## 4. Ubuntu 26.04 notes (from the real install)

`install.sh` stops on anything other than Ubuntu 24.04 with "This installer is for Ubuntu 24.04. Detected …". Docker's own apt repository may also not have packages for the new release yet. On **Ubuntu 26.04**, do this after step 6 and before step 7.

**4.1 Install Docker from Ubuntu's own packages.** The installer then sees Docker and Compose and skips its own Docker setup.

```bash
sudo apt -y install docker.io docker-compose-v2 && sudo systemctl enable --now docker
sudo docker compose version
sudo docker run --rm hello-world
```

**4.2 Let the installer accept 26.04.** Run this inside `~/smart-attendance`:

```bash
sed -i 's/"${VERSION_ID:-}" != "24.04"/"${VERSION_ID:-}" != "24.04" \&\& "${VERSION_ID:-}" != "26.04"/' install.sh
```

Check the patch:

```bash
grep -n 'VERSION_ID:-}" !=' install.sh
```

Expected line:

```text
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "24.04" && "${VERSION_ID:-}" != "26.04" ]]; then
```

**4.3 Continue with step 7** (`sudo bash install.sh 2>&1 | tee ~/install.log`).

The patch changes a tracked file in your clone, and the change is copied to `/opt/smartattendance/application`. See section 7.3 before you run `update.sh`.

---

## 5. Monitoring the installation from a second terminal

The first install takes a while. Open a second SSH session and use these commands:

```bash
# Follow the installer output
tail -f ~/install.log

# Running containers and their health
sudo docker ps

# Images built or pulled so far
sudo docker images

# Disk space (watch / and /var/lib/docker)
df -h

# Memory
free -h

# Ollama download progress / errors
sudo docker logs -f smartattendance-ollama-1

# Models Ollama has finished downloading
sudo docker exec smartattendance-ollama-1 ollama list

# Any one service's logs
cd /opt/smartattendance/application
sudo docker compose logs -f --tail 80 backend
```

Press `Ctrl+C` to stop following a log. Stopping `tail` or `logs` doesn't stop the installer.

---

## 6. Day-1 setup SOP (in the web app)

The sidebar shows these pages, depending on role: **Today**, **Organizations**, **People**, **Batches & classes**, **Attendance**, **Review queue**, **Cameras**, **Reports & import**, **Notes & suggestions** and **Access & audit**.

### 6.1 Create the organization (super admin)

1. Sign in as `admin@smartattendance.local`.
2. Open **Organizations**.
3. Under **Onboard an organization**, fill in Name, Type (Sports academy / School / College / Coaching institute / Private organization), Contact person, Email, Phone and Address.
4. Press **Create organization**. It gets a code such as `ORG-0001`, and its storage folder is created under `/opt/smartattendance/storage/organizations/ORG-0001/`.
5. As super admin, choose the organization in the **Working organization** drop-down in the sidebar, or press **Work inside** on its card. Most pages show "Choose an organization" until you do.

### 6.2 Create staff login accounts

Open **Access & audit** → **Staff account**. Enter Name, Email, Password (10 or more characters) and a Role (`org_admin`, `coach`, `teacher`, `student`), then press **Create account**.

- Create at least one **org_admin** per organization, so that daily work doesn't need the super-admin account.
- Staff accounts are **logins**. The people whose attendance you take are **profiles** in **People** (next steps).

### 6.3 Sports, batches and classes

Open **Batches & classes**:

- **Sport**: type a name (for example "Cricket") and press **Add**.
- **Batch**: enter a Name, choose a Sport, set the Start time (`HH:MM`) and Weekdays (comma list, **Monday = 0 … Sunday = 6**, default `0,1,2,3,4`), then press **Add batch**.
- Schools: add an **Academic session** (name plus start and end dates) and press **Add session**. Then add a **Class** (name, section, session) with **Add class and attendance batch**, which also creates its attendance batch.

> **Time zone caution (v1):** the server compares check-in times with the batch start time in **UTC**. For India (IST = UTC+5:30), enter the start time in UTC if you want correct **Late** marking. For example, a 07:00 IST batch is entered as `01:30`. Check-ins between 00:00 and 05:30 IST are also dated on the previous (UTC) day.

### 6.4 Add coaches and students (profiles)

1. Open **People** → **Add person**.
2. Choose a Role (Student / Coach / Teacher / Visitor), then enter Full name, Phone, Email, Guardian, Guardian phone, Sport specialization and Batch.
3. Press **Save profile**. A code is assigned automatically, and the profile starts as **not enrolled**.

Coaches and teachers can add **students only**. Org admins can add any role.

For many people at once, use the CSV import (section 6.9).

### 6.5 Enroll faces (5–8 photos per person)

1. **Before photographing anyone, get consent.** For students under 18, get verifiable consent from a parent or guardian (section 10).
2. In **People**, press **Photos** on the person's card.
3. Select **5–8 photos** in one upload. The server accepts at most 8 photos per upload. Good photos:
   - show one face only, filling a good part of the frame
   - are sharp and well lit; include a mix of lighting (indoor, outdoor, morning, evening)
   - include a mix of angles (front, slight left, slight right, slight up and down), with and without spectacles or caps if the person wears them
4. A photo is kept only if **exactly one live face passes the quality check**. Rejected files are listed with the reason. Replace them and upload again.
5. When the card shows embeddings, press **Confirm enrollment**. **The person isn't recognised until enrollment is confirmed.** Confirming is a deliberate staff step. Nothing enrolls a person automatically, and that includes CSV import and the review queue.

### 6.6 Photo check-in

1. Open **Attendance** → **Photo check-in**.
2. Choose the **Batch**, or leave "Their first batch".
3. Take or upload **one photo of one person**. On a phone, the file button opens the camera.
4. **Last result** shows one of:
   - **name · confidence % · marked present/late**: recorded
   - **already recorded today**: duplicate; no second row is created for the same person, batch and day
   - **No name assigned (unknown / low quality / failed liveness / no face / multiple faces / not in batch)**: nobody is marked. Unknown faces are queued as `UNK-…`.
5. The **Register** list shows everyone recorded for the chosen date.

**Manual mark** (same page): choose Person, Batch, Date and Status (`present`, `late`, `absent`, `leave`, `holiday`, `manual`), then press **Save**.

### 6.7 Add an RTSP IP camera

1. Make sure the server can reach the camera. From the server:

   ```bash
   sudo apt -y install ffmpeg
   ffprobe -v error -rtsp_transport tcp "rtsp://USER:PASSWORD@CAMERA_IP:554/stream"
   ```

2. Open **Cameras**, enter Name, **RTSP URL** (must start with `rtsp://` or `rtsps://`, for example `rtsp://user:password@192.168.1.20:554/stream`) and Location, then press **Add camera**.
3. Press **Capture one frame**. The server grabs one frame and checks in every known face. The message reads "Read one frame. N known, M unnamed." Unnamed faces go to the review queue.

v1 captures **on demand** (one frame per button press). It doesn't stream continuously.

### 6.8 Review the unknown-faces queue

Open **Review queue**, or follow the link on **Today**. Repeat sightings of the same unknown face are grouped under one provisional code (`UNK-…`) with the count, camera and last-seen time. For each open item:

| Action | When to use it | Effect |
| --- | --- | --- |
| **Assign** (choose an existing person) | It's someone already on the roll | Closes the item as `assigned` to that person. It doesn't mark attendance or add the snapshots to their face gallery; mark attendance manually if needed |
| **Register new** | A genuine new student who should be on the roll | Creates a new student profile (not enrolled). Then upload photos and **Confirm enrollment** in **People** |
| **Mark visitor** | An authorised visitor (parent, guest, vendor) | Creates a visitor profile (not enrolled) and closes the item |
| **Dismiss** | Not relevant (poster, passer-by, bad capture) | Closes the item |
| **Delete** | The snapshots shouldn't be kept | Deletes the item and its snapshots |

The queue never names anyone and never accuses anyone. Reviewing an item does **not** train the face gallery.

### 6.9 Attendance corrections and audit trail

1. In **Attendance** → **Register**, pick the date.
2. Change the status in the drop-down next to the person.
3. A prompt asks for a **note for the audit log**. Always write the reason, for example "Arrived late, confirmed by coach Ravi".
4. The record is marked with method `correction`. The old and new status and note are written to the audit log.
5. To review changes, open **Access & audit** → **Audit**, which shows the latest 200 events with time, role and action (for example `attendance.correct`).

**Policy** (org admin, bottom of Attendance): Late counts as present, Exclude holidays, Exclude approved leave, Match threshold and Ambiguity margin. Don't lower the match threshold to force names. That causes wrong matches.

**Monthly percentage:** choose a person and month, then press **Calculate**.

### 6.10 CSV import and export

**Export:** open **Reports & import**, choose the Month and press **Download attendance CSV**. Columns: `date, person_code, person_name, batch, status, method, confidence, check_in_at`.

**Import people:** in **Reports & import** → **Import people**, paste CSV text into the box (header row first) and press **Import CSV**.

```csv
code,full_name,person_type,email,phone,dob,batch,external_id
,Asha Rao,student,asha@example.com,9800000000,2012-03-01,U14 Morning,
```

- `full_name` is required. `person_type` defaults to `student`. Leave `code` empty to auto-generate one. `dob` uses the `YYYY-MM-DD` format. `batch` must match an existing batch name exactly.
- Rows with a duplicate email or code, or an unknown batch, are reported as errors. The other rows are still created.
- Imported people are **not enrolled**. Add photos and confirm each one (section 6.5).
- The box is a text box, so open your `.csv` file in a text editor and paste its contents. A copy of each import is kept in `/opt/smartattendance/storage/imports/`.

### 6.11 Optional: notes, AI suggestions and the external API

- **Notes & suggestions:** save a metrics JSON (stub), a coach comment, or **Draft an AI suggestion**, which needs qwen2.5:7b. AI text is always labelled "Suggestion only" and is never a coach comment.
- **External API key:** in **Access & audit**, press **Issue a key**. The key is shown **once**, so copy it right away. The default rate limit is 60 calls per minute. Send it as the `X-API-Key` header to `/api/v1/...` (see the README for endpoints).

---

## 7. Daily, weekly and monthly operations

Run all `docker compose` commands from the application folder:

```bash
cd /opt/smartattendance/application
```

### 7.1 Health checks

**Daily (2 minutes):**

```bash
cd /opt/smartattendance/application
sudo docker compose ps
curl -s http://127.0.0.1:18480/api/health; echo
df -h /
```

- Health `"status":"ok"` means the database and Redis are up. HTTP 503 means the database is down.
- `face_engine` must be `"ok"` for face check-ins to work.
- Open **Today** in the app: check "Present today" and "Open reviews". Clear the **Review queue**.

### 7.2 Backups

**Manual backup:**

```bash
sudo bash /opt/smartattendance/application/backup.sh
```

- This runs `pg_dump` of the database and archives `/opt/smartattendance/storage`. It encrypts both with AES-256 using `BACKUP_ENCRYPTION_KEY` from `.env`.
- Output: `/opt/smartattendance/backups/smartattendance-YYYYMMDDHHMMSS.tar.gz.enc`. The timestamp is in **UTC**.
- Archives older than **14 days** are deleted automatically on each run.
- Not included: `config/.env`, Ollama models, Docker images. **Keep a copy of `/opt/smartattendance/config/.env` somewhere safe and offline.** Without it, backups can't be decrypted and encrypted face photos can't be read.
- If `RESTIC_REPOSITORY` is set and `restic` is installed, the archive and `.env` are also sent to restic. Otherwise the script prints "restic is not configured…", which is normal.

**Daily automatic backup (recommended):** v1 has no built-in scheduler, so use root's cron:

```bash
sudo crontab -e
```

Add this line. It runs every day at 02:00 server time:

```cron
0 2 * * * bash /opt/smartattendance/application/backup.sh >> /opt/smartattendance/logs/backup.log 2>&1
```

**Weekly:** copy the newest archive off the server (to another machine, NAS or encrypted cloud storage):

```bash
ls -lh /opt/smartattendance/backups/
scp /opt/smartattendance/backups/smartattendance-YYYYMMDDHHMMSS.tar.gz.enc USER@BACKUP_HOST:/path/
```

**Restore (destructive; replaces the database and storage):**

```bash
sudo RESTORE_CONFIRM=yes bash /opt/smartattendance/application/backup.sh restore /opt/smartattendance/backups/smartattendance-YYYYMMDDHHMMSS.tar.gz.enc
cd /opt/smartattendance/application
sudo docker compose --env-file /opt/smartattendance/config/.env up -d
sudo docker compose restart backend worker
```

**Restore on a new server:**

1. Create the config folder: `sudo mkdir -p /opt/smartattendance/config`
2. Copy the **old** `.env` to `/opt/smartattendance/config/.env` and run `sudo chmod 600 /opt/smartattendance/config/.env`.
3. Run the normal installation (section 3). It keeps an existing `.env`.
4. Copy the archive to `/opt/smartattendance/backups/` and run the restore commands above.

**Monthly:** do a test restore on a spare server or VM to prove the backups work.

### 7.3 Updates

```bash
sudo bash /opt/smartattendance/application/update.sh
```

What it does: saves the current commit to `/opt/smartattendance/backups/pre-update-<UTC time>.sha`, runs `git pull --ff-only` inside `/opt/smartattendance/application`, rebuilds and restarts with `docker compose up -d --build`, and waits up to 3 minutes for `/api/health`.

- **Healthy:** prints "Update is healthy (…)".
- **Unhealthy:** prints "Update failed the health check." It checks out the previous commit and rebuilds it (automatic rollback), then exits with an error. The checkout is then on a detached HEAD at the old commit. Fix the problem, then return to `main` with `sudo git -C /opt/smartattendance/application checkout main` before the next update.

Before updating:

1. **Take a backup first:** `sudo bash /opt/smartattendance/application/backup.sh`
2. **GitHub access for root.** `update.sh` runs `git pull` as root, and root doesn't share your `gh` login. Run this once:

   ```bash
   sudo gh auth login        # GitHub.com → HTTPS → Yes → Login with a web browser (as gargee1608)
   sudo gh auth setup-git
   ```

3. **Ubuntu 26.04 servers only.** The patched `install.sh` is a local change and can block `git pull`. Discard it before updating, then re-apply the patch afterwards if `install.sh` still only accepts 24.04:

   ```bash
   sudo git -C /opt/smartattendance/application checkout -- install.sh
   ```

**Alternative update method:** pull in your own clone and re-run the installer. It's safe to re-run and keeps `.env` and data, but it has no automatic rollback.

```bash
cd ~/smart-attendance && git pull
sudo bash install.sh 2>&1 | tee ~/update.log
```

Update during a quiet time, not during batch hours. Updates come from the current `main` branch; v1 doesn't use release tags.

### 7.4 Logs

| What | Where / command |
| --- | --- |
| All services | `cd /opt/smartattendance/application && sudo docker compose logs --tail 100` |
| One service, live | `sudo docker compose logs -f backend` (also `nginx`, `face-engine`, `worker`, `postgres`, `redis`, `ai-service`, `ollama`, `migrate`) |
| Installer | `~/install.log` (if you used `tee`) |
| Cron backups | `/opt/smartattendance/logs/backup.log` |
| Docker daemon (installer fallback) | `/var/log/dockerd.log`, or `sudo journalctl -u docker` |
| Raw container log files | `/var/lib/docker/containers/<id>/<id>-json.log` |
| Who did what in the app | **Access & audit** → **Audit** |

To stop Docker logs from filling the disk, check `df -h` weekly. If needed, set log rotation in `/etc/docker/daemon.json` (`"log-opts": {"max-size": "50m", "max-file": "3"}`) and restart Docker.

### 7.5 Restarting services

```bash
cd /opt/smartattendance/application

# Restart one service
sudo docker compose restart backend

# Restart everything (keeps data)
sudo docker compose restart

# Stop / start the whole stack
sudo docker compose down
sudo docker compose --env-file /opt/smartattendance/config/.env up -d

# Rebuild after changing .env (e.g. OLLAMA_MODEL)
sudo docker compose --env-file /opt/smartattendance/config/.env up -d --build
```

All services use `restart: unless-stopped`, so they come back after a server reboot as long as Docker is enabled (`sudo systemctl enable docker`).

**Never** run `docker compose down -v` or delete `/opt/smartattendance/database`. That destroys the data.

### 7.6 Operations checklist

| Frequency | Task |
| --- | --- |
| Daily | `docker compose ps`, health endpoint, disk space; clear the review queue; check that the nightly backup file exists |
| Weekly | Copy the newest backup off the server; review **Audit** for unexpected corrections or logins; check `df -h` and `free -h`; apply Ubuntu security updates (`sudo apt update && sudo apt -y upgrade`) |
| Monthly | Test restore; `update.sh` (after a backup); review staff accounts and remove leavers; revoke unused API keys; delete biometrics for people who have left (section 10); export the monthly attendance CSV |

---

## 8. Configuration reference (`/opt/smartattendance/config/.env`)

| Key | Meaning |
| --- | --- |
| `SMARTATTENDANCE_ENV=production` | Production mode. The backend refuses to start with default or weak secrets |
| `DATA_ROOT=/opt/smartattendance` | Where database, storage, redis and ollama data live |
| `PUBLIC_HTTP_PORT=18480`, `PUBLIC_HTTPS_PORT=18443` | Host ports |
| `POSTGRES_PASSWORD`, `APP_DB_PASSWORD`, `ADMIN_DB_PASSWORD` | Database passwords |
| `JWT_SECRET` | Signs login tokens (12-hour sessions) |
| `FILE_ENCRYPTION_KEY` | Encrypts face photos and sealed embeddings. **Never change or lose it** |
| `BACKUP_ENCRYPTION_KEY` | Encrypts backup archives. **Never change or lose it** |
| `INITIAL_ADMIN_EMAIL`, `INITIAL_ADMIN_PASSWORD` | Used only to create the first super admin |
| `OLLAMA_MODEL=qwen2.5:7b` | LLM used for suggestions |

---

## 9. Troubleshooting (issues seen during real installs)

### 9.1 apt/dpkg lock held by another process

Symptom: `Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 1234 (…)`

```bash
ps -fp 1234                 # see what it is (often unattended-upgrades on a fresh server)
```

If it's `unattended-upgr`, waiting 5–15 minutes is the safest fix. If it's stuck or is an old apt session:

```bash
sudo kill 1234              # use kill -9 only if it will not exit
sudo dpkg --configure -a
sudo apt update
```

Then re-run the installer. It's safe to re-run.

### 9.2 Hang at "Processing triggers for man-db" or during docker.io setup

The man-db trigger can take several minutes, so give it 10 minutes. If nothing moves (check `top` in the second terminal), press `Ctrl+C` and run:

```bash
sudo dpkg --configure -a
sudo systemctl restart docker
sudo systemctl status docker --no-pager
sudo docker run --rm hello-world
```

When `hello-world` prints "Hello from Docker!", re-run `sudo bash install.sh 2>&1 | tee ~/install.log`.

### 9.3 "Waiting for the API" followed by `curl: (56) Connection reset by peer`

**This is normal** while the backend starts. Nginx only starts after the backend is healthy, and the backend waits for migrations and Redis. The installer retries every 5 seconds for up to 5 minutes. If it gives up with "The API did not become healthy":

```bash
cd /opt/smartattendance/application
sudo docker compose ps
sudo docker compose logs --tail 80 migrate backend nginx
```

Common causes: the migrate job failed (see the database item in 9.7), or the backend refused weak secrets. After fixing the cause, re-run the installer.

### 9.4 Slow qwen2.5:7b download (about 4.7 GB)

- **Attendance, enrollment and recognition work without it.** Only "Draft an AI suggestion" needs it (it returns 503 until the model is present).
- Watch progress with `sudo docker logs -f smartattendance-ollama-1`.
- If the installer ends with "Ollama model pull failed", or you stopped it with `Ctrl+C` during the pull, the rest of the stack is already up. Pull the model later:

```bash
sudo docker exec -it smartattendance-ollama-1 ollama pull qwen2.5:7b
```

- **Smaller option (16 GB RAM servers or slow links):** `qwen2.5:3b` (about 1.9 GB):

```bash
sudo docker exec -it smartattendance-ollama-1 ollama pull qwen2.5:3b
sudo sed -i 's/^OLLAMA_MODEL=.*/OLLAMA_MODEL=qwen2.5:3b/' /opt/smartattendance/config/.env
cd /opt/smartattendance/application
sudo docker compose --env-file /opt/smartattendance/config/.env up -d
```

### 9.5 The installer is safe to re-run

Re-running `sudo bash install.sh` keeps the existing `/opt/smartattendance/config/.env` (it prints "Keeping existing …/.env") and the database. It re-copies the code, re-checks the models, rebuilds and restarts. It does **not** create a second admin, and it doesn't change passwords.

### 9.6 Face check-in says the face engine is unavailable

The models may not have downloaded, or a checksum may have failed.

```bash
cd /opt/smartattendance/application
sudo docker compose logs --tail 80 face-engine
sudo bash /opt/smartattendance/application/scripts/download_models.sh
sudo docker compose restart face-engine
```

### 9.7 Health says the database is down

```bash
sudo docker compose -f /opt/smartattendance/application/docker-compose.yml logs migrate
sudo iptables-legacy -S FORWARD
```

If the migrate error is a connection timeout and the iptables output shows `-P FORWARD DROP`, run `sudo iptables-legacy -P FORWARD ACCEPT`, then re-run the installer.

### 9.8 Everyone is "unknown"

Check these in order:

- Was **Confirm enrollment** pressed?
- Are the photos small or blurry?
- Did liveness fail (for example, a photo of a photo or of a screen)?
- Is the score under the threshold (see **Attendance → Policy**)?

Add better enrollment photos rather than lowering the threshold.

### 9.9 Camera capture fails

The URL must start with `rtsp://` or `rtsps://`, and the server itself must reach the camera. Test it with `ffprobe` (section 6.7). Also check the camera's username, password and stream path, and any firewall between the server and the camera.

### 9.10 Browser warns about HTTPS

This is expected with the self-signed certificate. Use `http://SERVER_IP:18480` inside a trusted network. To use a real certificate, place it in `/etc/nginx/certs/server.crt` and `server.key` inside the nginx container, or put a reverse proxy with a real certificate in front.

---

## 10. Security and compliance

**Accounts and secrets**

- Change the default admin password on day one (step 11), then delete `initial_admin_password.txt`.
- Give each staff member their own account with the lowest role that works. Suspend leavers with `PATCH /api/users/{id}` and `{"status":"suspended"}`.
- **Keep the GitHub repository private.** Only add trusted collaborators.
- **Never commit `.env`** or anything from `/opt/smartattendance/config`, `backups/` or `storage/`. The repo's `.gitignore` already excludes `.env`, `data/`, `backups/` and `*.tar.gz.enc`. Don't override it.
- Protect `/opt/smartattendance/config/.env` (mode 600). It holds the database passwords and the file and backup encryption keys.

**Firewall (ufw)**

```bash
sudo ufw allow 22/tcp       # SSH
sudo ufw allow 80/tcp       # only needed if you add a reverse proxy on 80
sudo ufw allow 443/tcp      # only needed if you add a reverse proxy on 443
sudo ufw allow 18480/tcp    # Smart Attendance HTTP
sudo ufw allow 18443/tcp    # Smart Attendance HTTPS (self-signed)
sudo ufw enable
sudo ufw status verbose
```

- The stack itself listens on 18480 and 18443 only. Ports 5432 (Postgres), 6379 (Redis), 8000, 8090, 8091 and 11434 are internal and must **never** be published.
- Docker-published ports can bypass ufw rules. Keep the server on a private or trusted network, or restrict 18480 and 18443 at your router or cloud firewall. Don't expose the app directly to the internet without HTTPS and a real certificate.

**Face data protection (built in)**

- Face photos are stored **AES-GCM encrypted** on disk (`SAENC1` files) under each organization's folder.
- Embeddings are stored separately from the photos, in the `face_embeddings` table. An encrypted copy is kept in `embedding_sealed`. The searchable `vector(128)` column is protected by row-level security and is never exposed outside the server.
- Backups are encrypted. Every sign-in, enrollment, correction, export and deletion is written to the audit log.

**Consent: India's Digital Personal Data Protection (DPDP) Act, 2023**

- Facial images and embeddings are personal data. Collect them only for attendance and academy management, and say so in a clear written notice.
- Get **free, specific, informed consent** from each adult before enrollment. For **children (under 18), get verifiable consent from a parent or lawful guardian**. Keep signed consent forms (paper or digital) on file.
- Offer a non-biometric alternative (manual marking) to anyone who doesn't consent.
- Honour withdrawal of consent and erasure requests promptly. Delete biometrics when a person leaves (below).
- Keep data only as long as needed. Appoint a contact for data-protection questions. Have legal counsel review your notice and consent forms against the current DPDP rules.

**Deleting a person's biometric data**

v1 has no delete button. Use the API. Both methods delete all of the person's face photos and embeddings, reset the profile to draft (not enrolled) and record the action in the audit log as `person.biometrics_delete`. Attendance history is kept.

*Option A: with an external API key (Access & audit → Issue a key):*

```bash
curl -X DELETE -H "X-API-Key: YOUR_API_KEY" http://SERVER_IP:18480/api/v1/persons/PERSON_ID
```

*Option B: with a staff login.* First get a token and look up the person's `id`:

```bash
TOKEN=$(curl -s -X POST http://SERVER_IP:18480/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"ADMIN_EMAIL","password":"ADMIN_PASSWORD"}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

# Find the person's id (org admins can omit X-Organization-Id; super admins must send the org's id)
curl -s -H "Authorization: Bearer $TOKEN" -H "X-Organization-Id: ORG_UUID" \
  http://SERVER_IP:18480/api/persons | python3 -m json.tool | grep -E '"(id|code|full_name)"'

curl -X DELETE -H "Authorization: Bearer $TOKEN" -H "X-Organization-Id: ORG_UUID" \
  http://SERVER_IP:18480/api/persons/PERSON_ID/biometrics
```

The response looks like `{"biometrics_deleted": true, "embeddings_removed": N, "images_removed": N}`. Old encrypted backups still contain the data until they expire (14 days on the server). Remove off-server copies as well. To re-enroll the person later, create a new profile.

**Face matches are never evidence of wrongdoing**

A face match is a statistical estimate for attendance only. Unknown faces are labelled `UNK-…`, and no name is guessed. **Never** treat a match or an unknown face as proof that someone is a criminal, a thief or an intruder. Never use it to accuse, punish or report anyone. A person always verifies before acting, and a disputed record is corrected with a note in the audit trail.

---

## 11. Known v1 limitations

| Area | Status in v1 |
| --- | --- |
| Performance (M08) | **Stub.** Metrics are saved as a free-form JSON object. Per-sport templates (batting, bowling, fitness, …) are not built yet |
| Reports (M10) | **CSV export only.** PDF and Excel reports are not built yet |
| Import (M11) | **CSV person import only** (pasted text). **Excel import**, a column-mapping screen and **bulk photo upload** are not built yet |
| AI suggestions (M09) | Works with Ollama. Suggestions only, never official coach comments |
| Backups (M12) | `pg_dump` plus encrypted archive. Scheduling is by cron (section 7.2). restic is optional. There's no per-organization export screen |
| Updates (M15) | Pulls the current `main` branch. Signed release tags are not used yet |
| Cameras | One frame per **Capture one frame** press. There's no continuous stream monitoring |
| Accounts | No change-password or user-edit screens (use section 3 step 11 or the API). The Parent role is reserved |
| Biometric deletion | API only. There's no button in People |
| Time zone | Batch start times and attendance dates use UTC (see section 6.3) |
| OS | Installer is written for Ubuntu 24.04. 26.04 needs the patch in section 4 |

Check what the running server reports with `GET /api/system/version` (requires login).

---

## 12. Quick reference

```bash
# Status and health
cd /opt/smartattendance/application && sudo docker compose ps
curl -s http://127.0.0.1:18480/api/health; echo

# Backup / restore
sudo bash /opt/smartattendance/application/backup.sh
sudo RESTORE_CONFIRM=yes bash /opt/smartattendance/application/backup.sh restore /opt/smartattendance/backups/smartattendance-YYYYMMDDHHMMSS.tar.gz.enc

# Update (with automatic rollback)
sudo bash /opt/smartattendance/application/update.sh

# Logs and restart
sudo docker compose logs -f --tail 100 backend
sudo docker compose restart backend

# AI model
sudo docker exec -it smartattendance-ollama-1 ollama pull qwen2.5:7b
```

App: `http://SERVER_IP:18480` · HTTPS: `https://SERVER_IP:18443` · Admin: `admin@smartattendance.local`
