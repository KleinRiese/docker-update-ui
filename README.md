# Docker Update UI

A web interface for maintaining containers on a single Docker host. From the
browser you can check for image updates, back up a container's data, create
restore points, roll back to any of them, switch a container to a different tag,
and schedule automatic update checks per container.

The interface runs as a small Alpine container (`kleinriese/docker-update-ui`,
available for `linux/amd64` and `linux/arm64`) and talks to the host through the
Docker socket. It is fully bilingual: German and English.

## Features

- **Container overview** with image, image ID, status, update hint and data
  directory. Containers with data backup disabled show `N/A` in the directory
  column.
- **Manual update check** that pulls the image and compares it with the image of
  the running container.
- **Snapshot on demand**: create a restore point (data archive, rollback image
  tag, container configuration, rollback script) without changing the image.
- **Data backup** (`tar.gz`) taken while the container is stopped, so the archive
  is consistent.
- **Rollback point**: the current image is tagged as
  `<repository>:rollback-<timestamp>` before anything changes.
- **Rollback script** (`rollback_<container>_<timestamp>.sh`) and a JSON snapshot
  of the container configuration next to every restore point.
- **Update without extra images**: the new image is pulled and the container is
  recreated through the Docker API from its stored configuration – same ports,
  mounts, environment, networks, labels and restart policy.
- **Tag switch**: enter a tag (`1.2.3`), a full reference in the same repository
  or a digest, and the container is recreated on that image – useful to move
  away from `latest` or back from a rollback tag.
- **Restore with one click**: stop and remove the container, extract the data
  archive, recreate the container from the stored snapshot with the rollback
  image and start it. Points without a data archive restore image and
  configuration only.
- **Optional data backup per container**: state-less containers can be updated
  with only the image as a restore point. Such points are marked as image-only.
- **Automatic update checks** per container: cron schedule, three actions
  (check only, check and notify, check and update), registry digest comparison
  without downloading layers, and webhook notifications.
- **Retention**: the newest `max_rollbacks` restore points per container are
  kept; older backups, scripts, snapshots and rollback images are deleted.
  Rollback images without a restore point can be removed from the interface.
- **Settings** for backup directory, folder-picker start directory, number of
  restore points and notification webhook, stored in `/config`.
- **Folder picker** in the browser instead of typing paths, with manual input as
  a fallback.

## Quick start

```bash
cp .env.example .env
# open .env and set WEB_PASSWORD
docker compose up -d --build
```

Open `http://<host>:8080` and sign in with the password from `WEB_PASSWORD`.

Without Compose (prebuilt image):

```bash
docker run -d --name docker-update-ui \
  --restart unless-stopped \
  -p 8080:8080 \
  -e WEB_PASSWORD='change-me' \
  -e TZ=Europe/Berlin \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /docker/backups:/docker/backups \
  -v "$PWD/data:/config" \
  -v /docker/homeassistant:/docker/homeassistant \
  kleinriese/docker-update-ui:latest
```

The Compose file builds the image from this repository. To use the published
image instead, replace the `image:` line in `docker-compose.yml` with
`kleinriese/docker-update-ui:latest` and drop the `build:` line, or use the
`docker run` command shown above.

## Volumes and paths

The application works with the paths it sees **inside its own container**.
Host paths therefore have to be mounted **1:1** – host path = container path:

| Host | Container | Purpose |
| --- | --- | --- |
| `/var/run/docker.sock` | `/var/run/docker.sock` | Docker API |
| `/docker/backups` | `/docker/backups` | Backups, scripts, snapshots |
| `./data` | `/config` | Settings, container options |
| `/docker/homeassistant` | `/docker/homeassistant` | Example data directory |

Every data directory that should be backed up or restored must be mounted as
well. Read access is enough for backups; restores and rollbacks need write
access. The interface warns when the backup directory is not on a mounted
volume, because backups would then disappear with the container layer.

## Using the interface

1. **Open a container** (overview → details).
2. Choose the **data directory** once and save it. To keep no data at all, tick
   **Do not back up data (image only as rollback)** – then no directory is
   needed. The settings page shows **No data backup** for such containers
   instead of a path.
3. **Check for update** pulls the image and compares it with the running one.
4. **Create backup & update** (or **Save image & update**) starts the flow with a
   live log:
   - tag the current image as a rollback point,
   - stop the container,
   - archive the data (skipped without data backup),
   - write the snapshot and the rollback script,
   - delete outdated restore points,
   - pull the new image and recreate the container,
   - start the container.
5. **Snapshot** next to *Check for update* creates a restore point at any time
   without touching the image. The container is stopped briefly and started
   again afterwards.
6. **Switch tag** in the update panel moves the container to another tag of the
   same repository, for example `1.2.3`, `latest` or a full reference. A restore
   point is created first; without data backup only the image is saved.
7. The **Backups & rollbacks** page lists every restore point. **Restore** in a
   row brings back exactly that state: data plus container configuration with
   the rollback image.
8. The **Automatic update check** panel on the same page holds the schedule for
   this container.

Every restore point also contains a standalone script that can be executed on
the host, independently of the web interface:

```bash
sudo bash /docker/backups/<container>/rollback_<container>_<timestamp>.sh
```

## Automatic update checks

Each container can be checked automatically:

- **Schedule (cron)**: five fields `minute hour day month weekday`
  (0 = Sunday). Examples: `0 */6 * * *` every six hours, `30 4 * * *` daily at
  04:30, `0 5 * * 1` Mondays at 05:00. Invalid expressions are rejected when
  saving, including ones that never match (`0 0 30 2 *`).
- **Action** when an update is available:
  - *Check only* – record the result,
  - *Check and notify* – send a webhook,
  - *Check and update automatically* – run the same flow as a manual update,
    including the restore point and the container's data-backup setting.
- **Check now** runs the lightweight check immediately, without waiting for the
  schedule.

The check compares the registry manifest digest with the digest of the image
the container is **running** – not with the image the tag currently points to.
A new image that was already pulled locally therefore still counts as an
update. For multi-architecture images the digest of the matching platform is
used; when the running image has no repository digest (for example a locally
built image) or the daemon cannot provide a comparable value, the interface
falls back to the regular pull-based check. Private registries use the
credentials the Docker daemon already has.

The scheduler runs as a thread inside the tool container: it looks every 30
seconds for due entries, skips containers with a running job, the tool's own
container, rollback tags and fixed tags. Missed runs are caught up once after a
restart, not collected. Times use the container's time zone (`TZ`).

Results appear on the container page (last run, method, compared digests, next
run) and in the overview table. Notifications are sent as JSON via POST to the
webhook URL from the settings:

```json
{
  "container": "homeassistant",
  "status": "available",
  "label": "UPDATE AVAILABLE",
  "target": "ghcr.io/home-assistant/home-assistant:latest",
  "message": "Docker update: homeassistant – UPDATE AVAILABLE (...)",
  "time": "2026-10-25T14:00:00"
}
```

## Settings

Everything can be changed in the **Settings** page; the environment variables
only provide defaults:

| Variable | Default | Meaning |
| --- | --- | --- |
| `WEB_PASSWORD` | – (**required**) | Password for signing in |
| `BACKUP_DIR` | `/docker/backups` | Base directory for backups |
| `BROWSE_DIR` | `/docker` | Start directory for the folder picker |
| `MAX_ROLLBACKS` | `3` | Restore points kept per container |
| `CONFIG_DIR` | `/config` | Persistent settings |
| `TZ` | `Europe/Berlin` | Time zone for logs, timestamps and schedules |
| `NOTIFY_URL` | – | Default webhook URL for notifications |
| `AUTO_CHECK_CRON` | `0 4 * * *` | Suggested schedule for new entries |
| `AUTO_CHECK_TICK` | `30` | Scheduler interval in seconds (minimum 5) |
| `NOTIFY_TIMEOUT` | `10` | Timeout for the webhook call in seconds |
| `SESSION_TTL` | `43200` | Session lifetime in seconds |
| `COOKIE_SECURE` | `0` | `1` when the UI is only reachable via HTTPS |
| `ALLOWED_ORIGINS` | – | Additional host names for the origin check (reverse proxy) |
| `CSRF_PROTECTION` | `1` | `0` disables the origin check (only with other protection) |
| `STOP_TIMEOUT` | `30` | Grace period when stopping a container |
| `DOCKER_TIMEOUT` | `600` | HTTP timeout for Docker calls |
| `SELF_CONTAINER` | hostname | Name/ID of the tool container (self-update guard) |
| `UI_PORT` | `8080` | Host port mapped by `docker-compose.yml` |

## Security

Access to `/var/run/docker.sock` equals root on the host. Therefore:

- always set `WEB_PASSWORD` and use a strong password,
- never expose the port to the internet unprotected (VPN/LAN or a reverse proxy
  with TLS),
- set `COOKIE_SECURE=1` once HTTPS is in front,
- consider `cap_drop`/`read_only` for the tool container.

The application ships with password login, server-side session cookies
(`HttpOnly`, `SameSite=Strict`), a brute-force brake, an origin check for
writing requests and a restrictive content security policy.

### “Request rejected (CSRF protection)”

The origin check compares the `Origin` header with the requested host. It accepts
the same host name on a different port and honours `X-Forwarded-Host`, so reverse
proxies work. If the interface still rejects a request:

1. the error message contains `Origin` and `Host` – the cause is visible there;
2. add the public address to `ALLOWED_ORIGINS`, e.g.
   `ALLOWED_ORIGINS=https://docker.example.com`, and restart the container;
3. only if an authenticating proxy protects access anyway: set
   `CSRF_PROTECTION=0`.

## Limitations

- Single containers only; no Docker Swarm or stack updates.
- Compose files are not modified – the tool updates images and containers. After
  an update or tag switch, a later `docker compose up -d` restores the image
  configured in the Compose file.
- The Docker daemon must be able to reach the registry; private registries need
  credentials in the daemon.
- Backups stop the container briefly, so the data is consistent.
- Automatic checks run only while the tool container is running; notifications
  are webhook-only (no SMTP). Automatic updates appear in the container log
  (`docker logs`), their result is stored and shown in the interface.
- A multi-part `ENTRYPOINT` cannot be expressed as a single `--entrypoint`
  argument in the generated rollback script; the script then contains a note.
  Restoring through the interface is not affected.

## Project structure

```
app/
  main.py          FastAPI routes, auth middleware, templates
  auth.py          password login, sessions, rate limit, origin check
  config.py        settings, data directories, per-container options
  cron.py          cron expressions: validation and next run
  autocheck.py     scheduler, lightweight check, webhook notification
  docker_ops.py    Docker API: list, pull, tagging, recreate, digest check
  i18n.py          German and English texts, language selection
  operations.py    update, snapshot, rollback and backup flows
  jobs.py          background jobs with live log
  paths.py         folder picker
  templates/       Jinja2 + HTMX
  static/          CSS, JS, bundled HTMX
tests/             pytest suite (runs without a Docker daemon)
README.md
Dockerfile         python:3.12-alpine, non-interactive Uvicorn
docker-compose.yml socket, backups, config, example mounts
```

## Development and tests

```bash
python3 -m pip install --target .devdeps fastapi uvicorn jinja2 python-multipart docker httpx pytest
PYTHONPATH=.devdeps:. CONFIG_DIR=/tmp/dui-config WEB_PASSWORD=dev \
  BACKUP_DIR=/tmp/dui-backups BROWSE_DIR=/tmp \
  python3 -m uvicorn app.main:app --reload --port 8080

PYTHONPATH=.devdeps:. python3 -m pytest -q tests
```

The tests cover login and permissions, page rendering, settings, folder picker,
backup and restore, rollback scripts, cleanup, the job flow, cron parsing, digest
checks and the scheduler. They do not need a Docker daemon.
