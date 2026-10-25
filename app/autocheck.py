"""Automatische Update-Prüfung: Zeitplan, Prüfung und Benachrichtigung.

Der Scheduler läuft als Daemon-Thread im Prozess. Der Container startet mit
``--workers 1``, es gibt also genau eine Instanz. Geprüft wird über
Registry-Digests (kein Download); erst wenn das nicht möglich ist, wird wie
bisher das Image gepullt.
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime

from . import config, cron, docker_ops, i18n, operations
from .jobs import jobs

TICK_SECONDS = max(5.0, float(os.environ.get("AUTO_CHECK_TICK", "30") or 30))
NOTIFY_TIMEOUT = float(os.environ.get("NOTIFY_TIMEOUT", "10") or 10)

# Zustand -> (Textschlüssel, CSS-Klasse für das Label)
_STATE_LABELS: dict[str, tuple[str, str]] = {
    "available": ("update.status.available", "updateable"),
    "current": ("update.status.current", "ok"),
    "unknown": ("update.status.unknown", "neutral"),
    "fixed": ("hint.fixed_digest", "fixed"),
    "updated": ("auto.state.updated", "ok"),
    "error": ("auto.state.error", "err"),
    "skipped": ("auto.state.skipped", "neutral"),
}


def state_label(state: str | None, lang: str | None = None) -> tuple[str, str]:
    key, css = _STATE_LABELS.get(
        state or "", ("auto.state.unknown", "neutral")
    )
    return i18n.t(key, lang=lang), css


def skip_reason(detail: dict) -> str | None:
    """Warum für diesen Container nicht automatisch geprüft wird."""
    if detail.get("is_self"):
        return i18n.t("auto.skip_self")
    if docker_ops.is_rollback_image(detail.get("image") or ""):
        return i18n.t("auto.skip_rollback")
    if detail.get("hint_class") != "updateable":
        return i18n.t("auto.skip_fixed")
    return None


def run_check(name: str, log=lambda message: None) -> dict:
    """Leichte Prüfung eines Containers (ohne Layer-Download)."""
    detail = docker_ops.container_detail(name)
    reason = skip_reason(detail)
    if reason:
        return {
            "state": "skipped",
            "updateable": False,
            "target": detail.get("image") or "",
            "detail": reason,
            "method": "none",
            "remote": "",
            "local": [],
        }
    log(i18n.t("auto.checking", ref=detail.get("image") or ""))
    result = docker_ops.quick_check(name, log)
    log(result.get("detail") or "")
    return result


def _last_entry(result: dict, now: float | None = None) -> dict:
    return {
        "at": round(now if now is not None else time.time(), 3),
        "state": result.get("state") or "unknown",
        "target": result.get("target") or "",
        "method": result.get("method") or "",
        "remote": result.get("remote") or "",
        "local": list(result.get("local") or []),
        "error": result.get("error") or "",
    }


def _log(name: str):
    def write(message: str) -> None:
        if message:
            print(f"[auto] {name}: {message}", flush=True)

    return write


def send_notification(
    url: str,
    container: str,
    state: str,
    target: str = "",
    error: str = "",
) -> None:
    """Webhook mit JSON-Nutzlast aufrufen."""
    label, _ = state_label(state)
    message = i18n.t(
        "auto.notify.message",
        container=container,
        status=label,
        target=target or "–",
    )
    if error:
        message = f"{message} ({error})"
    payload = {
        "container": container,
        "status": state,
        "label": label,
        "target": target,
        "message": message,
        "time": datetime.now().isoformat(timespec="seconds"),
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "docker-update-ui",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=NOTIFY_TIMEOUT) as response:
        status = getattr(response, "status", 200)
    if status >= 400:
        raise RuntimeError(f"HTTP {status}")


def run_once(
    name: str,
    mode: str | None = None,
    now: float | None = None,
) -> dict:
    """Eine Prüfung inklusive Folgeaktion ausführen und das Ergebnis ablegen."""
    settings = config.load_settings()
    auto = config.get_auto_check(name)
    mode = mode or auto["mode"]
    log = _log(name)

    try:
        detail = docker_ops.container_detail(name)
    except Exception as exc:  # noqa: BLE001 - Container weg oder Docker stumm
        log(str(exc))
        config.record_auto_check(
            name,
            _last_entry({"state": "error", "error": str(exc)}, now),
        )
        return {"state": "error", "error": str(exc)}

    reason = skip_reason(detail)
    if reason:
        log(reason)
        return {"state": "skipped", "detail": reason}

    try:
        result = run_check(name, log)
    except Exception as exc:  # noqa: BLE001 - Fehler werden gespeichert
        log(str(exc))
        result = {"state": "error", "error": str(exc), "target": ""}

    state = result.get("state") or "unknown"
    target = result.get("target") or ""
    config.record_auto_check(name, _last_entry(result, now))

    notify = False
    error = result.get("error") or ""
    if state == "available" and mode == "update":
        notify = True
        try:
            operations.execute_update(name, settings, log)
            state = "updated"
            config.record_auto_check(
                name,
                _last_entry(
                    {"state": "updated", "target": target},
                    now,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - Fehler werden angezeigt
            error = str(exc)
            state = "error"
            log(error)
            config.record_auto_check(
                name,
                _last_entry(
                    {"state": "error", "target": target, "error": error},
                    now,
                ),
            )
    elif state == "available" and mode == "notify":
        notify = True

    if notify:
        url = settings.notify_url
        if url:
            try:
                send_notification(url, name, state, target, error)
                log(i18n.t("auto.notified"))
            except (urllib.error.URLError, OSError, RuntimeError) as exc:
                log(i18n.t("auto.notify_failed", error=exc))
        else:
            log(i18n.t("auto.no_webhook"))

    return {"state": state, "target": target, "error": error}


def next_run_at(entry: dict, reference: float | None = None) -> float | None:
    """Nächster geplanter Lauf als Zeitstempel (None bei ungültigem Plan)."""
    try:
        schedule = cron.parse(entry.get("cron") or config.DEFAULT_AUTO_CHECK_CRON)
    except cron.CronError:
        return None
    moment = datetime.fromtimestamp(
        reference if reference is not None else time.time()
    )
    try:
        return schedule.next_after(moment).timestamp()
    except cron.CronError:
        return None


_running: set[str] = set()
_running_lock = threading.Lock()


def _claim(name: str) -> bool:
    with _running_lock:
        if name in _running:
            return False
        _running.add(name)
        return True


def _release(name: str) -> None:
    with _running_lock:
        _running.discard(name)


def is_running(name: str) -> bool:
    with _running_lock:
        return name in _running


def run_now(name: str) -> dict:
    """Manuelle Prüfung über den leichten Weg (ohne Auto-Update)."""
    if not _claim(name):
        raise RuntimeError(i18n.t("auto.busy"))
    try:
        return run_once(name, mode="check")
    finally:
        _release(name)


def _run_claimed(name: str, mode: str | None) -> None:
    try:
        run_once(name, mode=mode)
    except Exception:  # noqa: BLE001 - Scheduler darf nicht sterben
        traceback.print_exc()
    finally:
        _release(name)


def due_entries(now: float | None = None) -> list[tuple[str, str | None]]:
    """Fällige container mit ihrem Modus."""
    moment = time.time() if now is None else now
    due: list[tuple[str, str | None]] = []
    for name, options in list(config.load_container_options().items()):
        auto = options.get("auto_check") if isinstance(options, dict) else None
        if not isinstance(auto, dict) or not auto.get("enabled"):
            continue
        try:
            schedule = cron.parse(
                str(auto.get("cron") or config.DEFAULT_AUTO_CHECK_CRON)
            )
        except cron.CronError:
            continue
        last = auto.get("last") if isinstance(auto.get("last"), dict) else None
        reference = moment
        if last and isinstance(last.get("at"), (int, float)):
            reference = float(last["at"])
        try:
            scheduled = schedule.next_after(
                datetime.fromtimestamp(reference)
            ).timestamp()
        except cron.CronError:
            continue
        if scheduled <= moment:
            due.append((name, auto.get("mode")))
    return due


def tick(now: float | None = None, spawn: bool = True) -> list[str]:
    """Fällige Prüfungen starten (Rückgabe: gestartete Container)."""
    started: list[str] = []
    for name, mode in due_entries(now):
        if jobs.active_for(name):
            print(f"[auto] {name}: Job läuft bereits – Prüfung übersprungen")
            continue
        if not _claim(name):
            continue
        started.append(name)
        if spawn:
            threading.Thread(
                target=_run_claimed,
                args=(name, mode),
                name=f"auto-{name}",
                daemon=True,
            ).start()
        else:
            _run_claimed(name, mode)
    return started


class Scheduler:
    """Minimaler Scheduler: prüft regelmäßig, ob etwas fällig ist."""

    def __init__(self, interval: float = TICK_SECONDS) -> None:
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="auto-check", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                tick()
            except Exception:  # noqa: BLE001 - Schleife bleibt am Leben
                traceback.print_exc()


scheduler = Scheduler()
