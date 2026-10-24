"""Weboberfläche für Docker-Updates (FastAPI + HTMX)."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import docker
from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth, config, docker_ops, operations, paths
from .jobs import jobs

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["filesize"] = operations.format_size


def _format_datetime(value) -> str:
    if value in (None, "", 0):
        return "–"
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value).strftime("%d.%m.%Y %H:%M")
    text = str(value)
    match = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})", text)
    if match:
        return f"{match.group(1).split('-')[2]}.{match.group(1).split('-')[1]}.{match.group(1).split('-')[0]} {match.group(2)}"
    return text


templates.env.filters["dt"] = _format_datetime
templates.env.filters["statuslabel"] = lambda value: {
    "running": "läuft",
    "exited": "gestoppt",
    "created": "erstellt",
    "paused": "pausiert",
    "restarting": "startet neu",
    "dead": "defekt",
}.get(str(value), str(value))


def render(
    template: str,
    request: Request,
    status_code: int = 200,
    headers: dict | None = None,
    **context,
):
    context.setdefault("request", request)
    return templates.TemplateResponse(
        request=request,
        name=template,
        context=context,
        status_code=status_code,
        headers=headers,
    )


def redirect_to(url: str, msg: str | None = None, err: str | None = None):
    params = {}
    if msg:
        params["msg"] = msg
    if err:
        params["err"] = err
    if params:
        url = f"{url}?{urlencode(params)}"
    return RedirectResponse(url, status_code=303)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not auth.password_configured():
        raise RuntimeError(
            "WEB_PASSWORD ist nicht gesetzt. Bitte die Umgebungsvariable "
            "WEB_PASSWORD setzen – ohne Passwort startet die Oberfläche nicht."
        )
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config.import_legacy_config(log=lambda message: print(f"[config] {message}"))
    yield


app = FastAPI(
    title="Docker Update UI",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Middleware: Sicherheits-Header, CSRF-Herkunftsprüfung, Login-Schutz
# ---------------------------------------------------------------------------


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        allowed, info = auth.origin_check(request)
        if not allowed:
            print(
                "[security] CSRF-Prüfung abgelehnt: "
                f"Origin={info['origin']!r} Host={info['host']!r} "
                f"X-Forwarded-Host={info['forwarded_host']!r}"
            )
            message = (
                "Die Herkunft der Anfrage passt nicht zum aufgerufenen Host. "
                f"Origin: {info['origin'] or '–'} · Host: {info['host'] or '–'}"
                + (
                    f" · X-Forwarded-Host: {info['forwarded_host']}"
                    if info["forwarded_host"]
                    else ""
                )
                + ". Läuft die Oberfläche hinter einem Reverse Proxy, trage die "
                "öffentliche Adresse in ALLOWED_ORIGINS ein (z. B. "
                "https://docker.example.com) und starte den Container neu. "
                "Alternativ lässt sich die Prüfung mit CSRF_PROTECTION=0 "
                "abschalten – dann muss der Zugriff anderweitig abgesichert sein."
            )
            return render(
                "error.html",
                request,
                title="Anfrage abgelehnt (CSRF-Schutz)",
                message=message,
                status_code=403,
            )
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'",
    )
    if not request.url.path.startswith("/static"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


PUBLIC_PATHS = {"/login", "/healthz"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/static"):
        return await call_next(request)
    if not auth.is_authenticated(request):
        if request.headers.get("hx-request") == "true":
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return redirect_to("/login")
    return await call_next(request)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/"):
    if auth.is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return render("login.html", request, error=None, next=next)


@app.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    password: str = Form(""),
    next: str = Form("/"),
):
    if auth.is_rate_limited(request):
        return render(
            "login.html",
            request,
            error="Zu viele Fehlversuche. Bitte ein paar Minuten warten.",
            next=next,
            status_code=429,
        )
    if not auth.check_password(password):
        auth.register_failure(request)
        return render(
            "login.html",
            request,
            error="Falsches Passwort.",
            next=next,
            status_code=401,
        )
    token = auth.create_session()
    destination = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(destination, status_code=303)
    response.set_cookie(value=token, **auth.cookie_params())
    return response


@app.post("/logout")
def logout(request: Request):
    auth.destroy_session(request.cookies.get(auth.SESSION_COOKIE))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return response


# ---------------------------------------------------------------------------
# Übersicht
# ---------------------------------------------------------------------------


def _rows_with_data_dirs() -> list[dict]:
    rows = docker_ops.list_containers()
    mapping = config.load_data_dirs()
    for row in rows:
        row["data_dir"] = mapping.get(row["name"])
    return rows


@app.get("/", response_class=HTMLResponse)
def overview(
    request: Request,
    msg: str | None = None,
    err: str | None = None,
):
    settings = config.load_settings()
    docker_error = None
    rows: list[dict] = []
    try:
        rows = _rows_with_data_dirs()
    except Exception as exc:  # noqa: BLE001
        docker_error = str(exc)
    return render(
        "containers.html",
        request,
        active="containers",
        rows=rows,
        settings=settings,
        backup_persistent=config.path_is_persistent(settings.backup_dir),
        docker_error=docker_error,
        msg=msg,
        err=err,
    )


@app.get("/partials/containers", response_class=HTMLResponse)
def containers_partial(request: Request):
    docker_error = None
    rows: list[dict] = []
    try:
        rows = _rows_with_data_dirs()
    except Exception as exc:  # noqa: BLE001
        docker_error = str(exc)
    return render(
        "_container_table.html",
        request,
        rows=rows,
        docker_error=docker_error,
    )


# ---------------------------------------------------------------------------
# Container-Details
# ---------------------------------------------------------------------------


def _not_found(request: Request, message: str):
    return render(
        "error.html",
        request,
        active="containers",
        title="Nicht gefunden",
        message=message,
        status_code=404,
    )


@app.get("/containers/{name}", response_class=HTMLResponse)
def container_page(
    request: Request,
    name: str,
    msg: str | None = None,
    err: str | None = None,
):
    settings = config.load_settings()
    try:
        detail = docker_ops.container_detail(name)
    except docker.errors.NotFound:
        return _not_found(request, f"Container '{name}' existiert nicht.")
    except Exception as exc:  # noqa: BLE001
        return render(
            "error.html",
            request,
            active="containers",
            title="Docker-Fehler",
            message=str(exc),
            status_code=500,
        )
    data = operations.list_backups(name, settings)
    return render(
        "container_detail.html",
        request,
        active="containers",
        detail=detail,
        settings=settings,
        data_dir=config.get_data_dir(name),
        data_persistent=config.path_is_persistent(config.get_data_dir(name) or ""),
        backup_persistent=config.path_is_persistent(settings.backup_dir),
        container=name,
        data=data,
        max_rollbacks=settings.max_rollbacks,
        msg=msg,
        err=err,
    )


@app.get("/containers/{name}/partials/summary", response_class=HTMLResponse)
def summary_partial(request: Request, name: str):
    try:
        detail = docker_ops.container_detail(name)
    except docker.errors.NotFound:
        return HTMLResponse(
            '<div class="notice err">Container existiert nicht mehr.</div>'
        )
    except Exception as exc:  # noqa: BLE001
        return HTMLResponse(f'<div class="notice err">{exc}</div>')
    return render("_summary.html", request, detail=detail)


@app.get("/containers/{name}/check", response_class=HTMLResponse)
def container_check(request: Request, name: str):
    settings = config.load_settings()
    try:
        plan = operations.prepare_update(
            name, settings, log=lambda message: print(f"[check] {message}")
        )
    except Exception as exc:  # noqa: BLE001
        return render(
            "_check.html",
            request,
            container=name,
            error=str(exc),
        )
    return render(
        "_check.html",
        request,
        container=name,
        plan=plan,
        data_dir=config.get_data_dir(name),
        max_rollbacks=settings.max_rollbacks,
        backup_persistent=config.path_is_persistent(settings.backup_dir),
    )


@app.post("/containers/{name}/update", response_class=HTMLResponse)
def container_update(request: Request, name: str):
    settings = config.load_settings()
    job = jobs.start(
        f"Update: {name}",
        lambda current: operations.execute_update(name, settings, current.log),
        meta={"container": name, "kind": "update"},
    )
    return render("_job.html", request, job=job)


@app.post("/containers/{name}/snapshot", response_class=HTMLResponse)
def container_snapshot(request: Request, name: str):
    settings = config.load_settings()
    job = jobs.start(
        f"Snapshot: {name}",
        lambda current: operations.execute_snapshot(name, settings, current.log),
        meta={"container": name, "kind": "snapshot"},
    )
    return render("_job.html", request, job=job)


@app.post("/containers/{name}/rollback", response_class=HTMLResponse)
def container_rollback(
    request: Request,
    name: str,
    timestamp: str = Form(...),
):
    settings = config.load_settings()
    try:
        plan = operations.rollback_plan(name, timestamp, settings)
    except Exception as exc:  # noqa: BLE001
        job = jobs.failed(f"Rollback: {name}", str(exc), meta={"container": name})
        return render("_job.html", request, job=job)

    job = jobs.start(
        f"Rollback: {name} ({timestamp})",
        lambda current: operations.execute_rollback(
            name, timestamp, settings, current.log
        ),
        meta={"container": name, "kind": "rollback"},
    )
    return render(
        "_job.html",
        request,
        job=job,
        rollback_plan=plan,
    )


@app.get("/partials/jobs/{job_id}", response_class=HTMLResponse)
def job_partial(request: Request, job_id: str):
    job = jobs.get(job_id)
    if not job:
        return HTMLResponse(
            '<div class="notice err">Dieser Auftrag ist nicht mehr verfügbar.</div>'
        )
    return render("_job.html", request, job=job)


# ---------------------------------------------------------------------------
# Datenverzeichnis
# ---------------------------------------------------------------------------


@app.post("/containers/{name}/data-dir")
def save_data_dir(request: Request, name: str, path: str = Form("")):
    try:
        saved = config.set_data_dir(name, path)
    except ValueError as exc:
        return redirect_to(f"/containers/{name}", err=str(exc))
    return redirect_to(
        f"/containers/{name}", msg=f"Datenverzeichnis gespeichert: {saved}"
    )


@app.post("/containers/{name}/data-dir/delete")
def delete_data_dir(request: Request, name: str):
    config.remove_data_dir(name)
    return redirect_to(f"/containers/{name}", msg="Datenverzeichnis entfernt.")


@app.get("/browse", response_class=HTMLResponse)
def browse_directory(request: Request, path: str = "/", hidden: int = 0):
    return render("_browse.html", request, **paths.browse(path, bool(hidden)))


# ---------------------------------------------------------------------------
# Backups & Rollbacks
# ---------------------------------------------------------------------------


@app.get("/containers/{name}/backups", response_class=HTMLResponse)
def backups_page(
    request: Request,
    name: str,
    msg: str | None = None,
    err: str | None = None,
):
    settings = config.load_settings()
    try:
        detail = docker_ops.container_detail(name)
    except docker.errors.NotFound:
        return _not_found(request, f"Container '{name}' existiert nicht.")
    except Exception as exc:  # noqa: BLE001
        return render(
            "error.html",
            request,
            active="containers",
            title="Docker-Fehler",
            message=str(exc),
            status_code=500,
        )
    return render(
        "backups.html",
        request,
        active="containers",
        detail=detail,
        container=name,
        data=operations.list_backups(name, settings),
        max_rollbacks=settings.max_rollbacks,
        settings=settings,
        msg=msg,
        err=err,
    )


@app.get("/containers/{name}/partials/backups", response_class=HTMLResponse)
def backups_partial(request: Request, name: str):
    settings = config.load_settings()
    return render(
        "_backups.html",
        request,
        container=name,
        data=operations.list_backups(name, settings),
        max_rollbacks=settings.max_rollbacks,
    )


@app.get("/containers/{name}/backups/{timestamp}/script")
def download_rollback_script(request: Request, name: str, timestamp: str):
    settings = config.load_settings()
    try:
        content = operations.read_backup_script(name, timestamp, settings)
    except Exception as exc:  # noqa: BLE001
        return redirect_to(
            f"/containers/{name}/backups", err=str(exc)
        )
    return Response(
        content,
        media_type="text/x-shellscript",
        headers={
            "Content-Disposition": (
                f'attachment; filename="rollback_{name}_{timestamp}.sh"'
            )
        },
    )


@app.post("/containers/{name}/images/cleanup")
def cleanup_orphan_images(request: Request, name: str):
    settings = config.load_settings()
    messages: list[str] = []
    try:
        removed = operations.cleanup_orphan_images(
            name, settings, log=messages.append
        )
    except Exception as exc:  # noqa: BLE001
        return redirect_to(f"/containers/{name}/backups", err=str(exc))
    if removed:
        message = f"{len(removed)} Rollback-Image(s) ohne Sicherung gelöscht."
    else:
        message = "Keine Rollback-Images ohne Sicherung gefunden."
    return redirect_to(f"/containers/{name}/backups", msg=message)


@app.post("/containers/{name}/backups/{timestamp}/delete")
def delete_backup_files(request: Request, name: str, timestamp: str):
    settings = config.load_settings()
    messages: list[str] = []
    try:
        operations.delete_backup(
            name, timestamp, settings, log=messages.append
        )
    except Exception as exc:  # noqa: BLE001
        return redirect_to(f"/containers/{name}/backups", err=str(exc))
    return redirect_to(
        f"/containers/{name}/backups", msg=" · ".join(messages) or "Gelöscht."
    )


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------


@app.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    msg: str | None = None,
    err: str | None = None,
):
    settings = config.load_settings()
    return render(
        "settings.html",
        request,
        active="settings",
        settings=settings,
        backup_persistent=config.path_is_persistent(settings.backup_dir),
        data_dirs=config.load_data_dirs(),
        defaults={
            "backup_dir": config.DEFAULT_BACKUP_DIR,
            "browse_dir": config.DEFAULT_BROWSE_DIR,
            "watchtower_image": config.DEFAULT_WATCHTOWER_IMAGE,
            "max_rollbacks": config.DEFAULT_MAX_ROLLBACKS,
        },
        msg=msg,
        err=err,
    )


@app.post("/settings")
def save_settings_form(
    request: Request,
    backup_dir: str = Form(...),
    browse_dir: str = Form(...),
    watchtower_image: str = Form(...),
    max_rollbacks: str = Form("3"),
):
    try:
        config.save_settings(
            config.Settings(
                backup_dir=backup_dir,
                browse_dir=browse_dir,
                watchtower_image=watchtower_image,
                max_rollbacks=int(max_rollbacks),
            )
        )
    except ValueError as exc:
        return redirect_to("/settings", err=str(exc))
    return redirect_to("/settings", msg="Einstellungen gespeichert.")


@app.post("/settings/data-dirs/{name}/delete")
def settings_remove_data_dir(request: Request, name: str):
    config.remove_data_dir(name)
    return redirect_to("/settings", msg=f"Zuordnung für '{name}' entfernt.")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz():
    try:
        docker_ops.get_client()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            {"status": "degraded", "docker": False, "error": str(exc)},
            status_code=503,
        )
    return JSONResponse({"status": "ok", "docker": True})
