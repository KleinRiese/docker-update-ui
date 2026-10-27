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
from jinja2 import pass_context

from . import auth, autocheck, config, docker_ops, i18n, operations, paths
from .jobs import jobs

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["filesize"] = operations.format_size


@pass_context
def _template_t(context, key: str, **values) -> str:
    """Übersetzung mit der Sprache des Requests (nicht des Render-Zeitpunkts)."""
    return i18n.t(key, lang=context.get("lang"), **values)


templates.env.globals["t"] = _template_t


@pass_context
def _format_datetime(context, value) -> str:
    pattern = i18n.date_format(context.get("lang"))
    if value in (None, "", 0):
        return "–"
    if isinstance(value, datetime):
        return value.strftime(pattern)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value).strftime(pattern)
    text = str(value)
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", text)
    if match:
        parsed = datetime(*(int(part) for part in match.groups()))
        return parsed.strftime(pattern)
    return text


templates.env.filters["dt"] = _format_datetime
@pass_context
def _status_label(context, value) -> str:
    key = f"status.{value}"
    label = i18n.t(key, lang=context.get("lang"))
    return str(value) if label == key else label


templates.env.filters["statuslabel"] = _status_label


def render(
    template: str,
    request: Request,
    status_code: int = 200,
    headers: dict | None = None,
    **context,
):
    context.setdefault("request", request)
    context.setdefault("lang", i18n.get_language())
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
        raise RuntimeError(i18n.t("error.no_password"))
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config.import_legacy_config(log=lambda message: print(f"[config] {message}"))
    autocheck.scheduler.start()
    try:
        yield
    finally:
        autocheck.scheduler.stop()


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
            message = i18n.t(
                "error.csrf.body",
                origin=info["origin"] or "–",
                host=info["host"] or "–",
                forwarded=(
                    f" · X-Forwarded-Host: {info['forwarded_host']}"
                    if info["forwarded_host"]
                    else ""
                ),
            )
            return render(
                "error.html",
                request,
                title=i18n.t("error.csrf.title"),
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


PUBLIC_PATHS = {"/login", "/healthz", "/language"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith(("/static", "/language/")):
        return await call_next(request)
    if not auth.is_authenticated(request):
        if request.headers.get("hx-request") == "true":
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return redirect_to("/login")
    return await call_next(request)


# ---------------------------------------------------------------------------
# Sprache (Cookie, sonst Accept-Language)
# ---------------------------------------------------------------------------


@app.middleware("http")
async def language_middleware(request: Request, call_next):
    token = i18n.set_language(i18n.detect(request))
    try:
        return await call_next(request)
    finally:
        i18n.reset_language(token)


@app.get("/language/{code}")
def switch_language(code: str, next: str = "/"):
    language = i18n.normalize(code)
    if not language:
        return redirect_to("/")
    destination = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(destination, status_code=303)
    response.set_cookie(
        i18n.LANGUAGE_COOKIE,
        language,
        max_age=i18n.LANGUAGE_COOKIE_MAX_AGE,
        samesite="lax",
        path="/",
    )
    return response


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
            error=i18n.t("login.rate_limited"),
            next=next,
            status_code=429,
        )
    if not auth.check_password(password):
        auth.register_failure(request)
        return render(
            "login.html",
            request,
            error=i18n.t("login.wrong"),
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
    options = config.load_container_options()
    for row in rows:
        entry = options.get(row["name"]) or {}
        row["backup_data"] = entry.get("backup_data", True) is not False
        # Ohne Datenbackup gibt es kein relevantes Verzeichnis – die Tabelle
        # zeigt dafür "N/A" statt des nur noch gespeicherten Pfads.
        row["data_dir"] = mapping.get(row["name"]) if row["backup_data"] else None
        auto = entry.get("auto_check")
        last = auto.get("last") if isinstance(auto, dict) else None
        check = None
        if isinstance(last, dict) and last.get("state"):
            label, css = autocheck.state_label(last["state"])
            check = {"label": label, "class": css, "at": last.get("at")}
        row["check"] = check
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
        title=i18n.t("error.not_found"),
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
        return _not_found(
            request, i18n.t("error.container_missing", name=name)
        )
    except Exception as exc:  # noqa: BLE001
        return render(
            "error.html",
            request,
            active="containers",
            title=i18n.t("error.docker"),
            message=str(exc),
            status_code=500,
        )
    data = operations.list_backups(name, settings)
    data_dir = config.get_data_dir(name)
    auto = config.get_auto_check(name)
    auto_last = auto.get("last") or {}
    auto_label, auto_class = autocheck.state_label(auto_last.get("state"))
    if auto["enabled"]:
        auto["next_at"] = autocheck.next_run_at(auto)
    local_digests = auto_last.get("local") or []
    auto_digests = {
        "remote": str(auto_last.get("remote") or "").replace("sha256:", "")[:12],
        "local": (
            str(local_digests[-1]).replace("sha256:", "")[:12]
            if local_digests
            else ""
        ),
    }
    return render(
        "container_detail.html",
        request,
        active="containers",
        detail=detail,
        auto=auto,
        auto_last=auto_last,
        auto_label=auto_label,
        auto_class=auto_class,
        auto_digests=auto_digests,
        auto_skip=autocheck.skip_reason(detail),
        default_cron=config.DEFAULT_AUTO_CHECK_CRON,
        settings=settings,
        data_dir=data_dir,
        data_persistent=config.path_is_persistent(data_dir or ""),
        backup_persistent=config.path_is_persistent(settings.backup_dir),
        backup_data=config.get_backup_data(name),
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
            f'<div class="notice err">{i18n.t("error.container_gone")}</div>'
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
        backup_data=config.get_backup_data(name),
        max_rollbacks=settings.max_rollbacks,
        backup_persistent=config.path_is_persistent(settings.backup_dir),
    )


@app.post("/containers/{name}/update", response_class=HTMLResponse)
def container_update(request: Request, name: str):
    settings = config.load_settings()
    job = jobs.start(
        f"{i18n.t('msg.job_prefix')}: {name}",
        lambda current: operations.execute_update(name, settings, current.log),
        meta={"container": name, "kind": "update"},
    )
    return render("_job.html", request, job=job)


@app.post("/containers/{name}/snapshot", response_class=HTMLResponse)
def container_snapshot(request: Request, name: str):
    settings = config.load_settings()
    job = jobs.start(
        f"{i18n.t('msg.job_snapshot')}: {name}",
        lambda current: operations.execute_snapshot(name, settings, current.log),
        meta={"container": name, "kind": "snapshot"},
    )
    return render("_job.html", request, job=job)


@app.post("/containers/{name}/tag", response_class=HTMLResponse)
def container_switch_tag(request: Request, name: str, tag: str = Form("")):
    settings = config.load_settings()
    job = jobs.start(
        f"{i18n.t('msg.job_tag')}: {name}",
        lambda current: operations.execute_switch_tag(
            name, tag, settings, current.log
        ),
        meta={"container": name, "kind": "tag"},
    )
    return render("_job.html", request, job=job)


@app.post("/containers/{name}/auto-check")
def save_auto_check(
    request: Request,
    name: str,
    enabled: str | None = Form(None),
    cron: str = Form(""),
    mode: str = Form("check"),
):
    try:
        config.set_auto_check(
            name, enabled=bool(enabled), mode=mode, cron=cron
        )
    except ValueError as exc:
        return redirect_to(f"/containers/{name}", err=str(exc))
    return redirect_to(f"/containers/{name}", msg=i18n.t("msg.auto_saved"))


@app.post("/containers/{name}/auto-check/run")
def run_auto_check(request: Request, name: str):
    try:
        result = autocheck.run_now(name)
    except Exception as exc:  # noqa: BLE001 - Meldung in der Oberfläche
        return redirect_to(f"/containers/{name}", err=str(exc))
    if result.get("state") == "error":
        return redirect_to(
            f"/containers/{name}",
            err=result.get("error") or i18n.t("auto.state.error"),
        )
    label, _ = autocheck.state_label(result.get("state"))
    return redirect_to(
        f"/containers/{name}", msg=i18n.t("msg.auto_checked", status=label)
    )


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
        job = jobs.failed(
            f"{i18n.t('msg.job_rollback')}: {name}",
            str(exc),
            meta={"container": name},
        )
        return render("_job.html", request, job=job)

    job = jobs.start(
        f"{i18n.t('msg.job_rollback')}: {name} ({timestamp})",
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
            f'<div class="notice err">{i18n.t("job.gone")}</div>'
        )
    return render("_job.html", request, job=job)


# ---------------------------------------------------------------------------
# Datenverzeichnis
# ---------------------------------------------------------------------------


@app.post("/containers/{name}/data-dir")
def save_data_dir(
    request: Request,
    name: str,
    path: str = Form(""),
    backup_disabled: str | None = Form(None),
    backup_data_set: str | None = Form(None),
):
    # Die Checkbox heißt „Keine Daten sichern“ – angehakt bedeutet also, dass
    # kein Datenbackup erstellt wird. Ohne das Marker-Feld (z. B. ältere
    # Clients) bleibt die Einstellung unverändert.
    if backup_data_set is None:
        backup_enabled = config.get_backup_data(name)
    else:
        backup_enabled = backup_disabled is None
    try:
        saved = ""
        if backup_enabled:
            saved = config.set_data_dir(name, path)
        elif path.strip():
            saved = config.set_data_dir(name, path)
        config.set_backup_data(name, backup_enabled)
    except ValueError as exc:
        return redirect_to(f"/containers/{name}", err=str(exc))
    if saved:
        message = i18n.t("msg.data_saved", path=saved)
        if not backup_enabled:
            message = f"{message} · {i18n.t('msg.backup_disabled')}"
    else:
        message = i18n.t("msg.backup_disabled")
    return redirect_to(f"/containers/{name}", msg=message)


@app.post("/containers/{name}/data-dir/delete")
def delete_data_dir(request: Request, name: str):
    config.remove_data_dir(name)
    return redirect_to(f"/containers/{name}", msg=i18n.t("msg.data_removed"))


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
        return _not_found(
            request, i18n.t("error.container_missing", name=name)
        )
    except Exception as exc:  # noqa: BLE001
        return render(
            "error.html",
            request,
            active="containers",
            title=i18n.t("error.docker"),
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
        message = i18n.t("msg.orphans_removed", count=len(removed))
    else:
        message = i18n.t("msg.no_orphans")
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
        f"/containers/{name}/backups",
        msg=" · ".join(messages) or i18n.t("msg.deleted"),
    )


# ---------------------------------------------------------------------------
# Einstellungen
# ---------------------------------------------------------------------------


def _data_dir_rows() -> list[dict]:
    """Datenverzeichnis-Zuordnungen mit Info, ob gesichert wird."""
    mapping = config.load_data_dirs()
    options = config.load_container_options()
    rows = []
    for name, path in sorted(mapping.items(), key=lambda item: item[0].lower()):
        entry = options.get(name) or {}
        rows.append(
            {
                "name": name,
                "path": path,
                "backup_data": entry.get("backup_data", True) is not False,
            }
        )
    return rows


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
        data_dirs=_data_dir_rows(),
        defaults={
            "backup_dir": config.DEFAULT_BACKUP_DIR,
            "browse_dir": config.DEFAULT_BROWSE_DIR,
            "max_rollbacks": config.DEFAULT_MAX_ROLLBACKS,
            "smtp_port": config.DEFAULT_SMTP_PORT,
            "smtp_security": config.DEFAULT_SMTP_SECURITY,
        },
        smtp_security_modes=config.SMTP_SECURITY_MODES,
        msg=msg,
        err=err,
    )


@app.post("/settings")
def save_settings_form(
    request: Request,
    backup_dir: str = Form(...),
    browse_dir: str = Form(...),
    max_rollbacks: str = Form("3"),
    smtp_host: str = Form(""),
    smtp_port: str = Form(""),
    smtp_user: str = Form(""),
    smtp_from: str = Form(""),
    smtp_to: str = Form(""),
    smtp_security: str = Form("starttls"),
):
    try:
        config.save_settings(
            config.Settings(
                backup_dir=backup_dir,
                browse_dir=browse_dir,
                max_rollbacks=int(max_rollbacks),
                smtp_host=smtp_host,
                smtp_port=smtp_port or config.DEFAULT_SMTP_PORT,
                smtp_user=smtp_user,
                smtp_from=smtp_from,
                smtp_to=smtp_to,
                smtp_security=smtp_security,
            )
        )
    except ValueError as exc:
        return redirect_to("/settings", err=str(exc))
    return redirect_to("/settings", msg=i18n.t("msg.settings_saved"))


@app.post("/settings/data-dirs/{name}/delete")
def settings_remove_data_dir(request: Request, name: str):
    config.remove_data_dir(name)
    return redirect_to(
        "/settings", msg=i18n.t("msg.data_mapping_removed", name=name)
    )


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
