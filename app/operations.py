"""Abläufe: Update, Umstellung auf reguläres Image, Rollback, Cleanup."""

from __future__ import annotations

import json
import os
import re
import shutil
import tarfile
import time
from datetime import datetime
from pathlib import Path

from . import config, docker_ops
from .config import Settings
from .docker_ops import OperationError

TIMESTAMP_FMT = "%Y%m%d-%H%M%S"
_TS_RE = r"(\d{8}-\d{6})"


# ---------------------------------------------------------------------------
# Pfade und Dateien
# ---------------------------------------------------------------------------


def container_backup_dir(settings: Settings, container: str) -> Path:
    return Path(settings.backup_dir) / container


def backup_paths(settings: Settings, container: str, timestamp: str) -> dict:
    base = container_backup_dir(settings, container)
    return {
        "dir": str(base),
        "archive": str(base / f"{container}-{timestamp}.tar.gz"),
        "script": str(base / f"rollback_{container}_{timestamp}.sh"),
        "snapshot": str(base / f"rollback_{container}_{timestamp}.json"),
    }


def format_size(size: int | float | None) -> str:
    value = float(size or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


# ---------------------------------------------------------------------------
# Backup / Restore der Daten
# ---------------------------------------------------------------------------


def backup_data_dir(data_dir: str, archive: str, log=print) -> int:
    source = Path(data_dir)
    if not source.is_dir():
        raise OperationError(f"Datenverzeichnis '{data_dir}' nicht gefunden.")

    target = Path(archive)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    if partial.exists():
        partial.unlink()

    log(f"Erstelle Daten-Backup: {target}")
    count = 0

    def _filter(info: tarfile.TarInfo):
        nonlocal count
        count += 1
        if count % 500 == 0:
            log(f"  ... {count} Einträge archiviert")
        return info

    try:
        with tarfile.open(partial, "w:gz", format=tarfile.PAX_FORMAT) as tar:
            tar.add(str(source), arcname=source.name, filter=_filter)
        os.replace(partial, target)
    except (OSError, tarfile.TarError) as exc:
        if partial.exists():
            partial.unlink()
        raise OperationError(f"Archivierung fehlgeschlagen: {exc}") from exc

    size = target.stat().st_size
    log(f"Backup fertig: {format_size(size)} ({count} Einträge)")
    return size


def _clear_directory(path: Path, log=print) -> None:
    for entry in path.iterdir():
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError as exc:
            log(f"WARNUNG: '{entry}' konnte nicht entfernt werden: {exc}")


def restore_data_dir(archive: str, data_dir: str, log=print) -> None:
    archive_path = Path(archive)
    if not archive_path.is_file():
        raise OperationError(f"Backup '{archive}' nicht gefunden.")

    target = Path(data_dir)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and not os.path.ismount(str(target)):
        moved = parent / f"{target.name}.before-rollback.{int(time.time())}"
        try:
            target.rename(moved)
            log(f"Vorherige Daten nach '{moved}' verschoben.")
        except OSError as exc:
            log(f"WARNUNG: Verschieben nicht möglich ({exc}), Inhalt wird ersetzt.")
            _clear_directory(target, log)
    elif target.exists():
        # Gemountetes Verzeichnis kann nicht umbenannt werden: Inhalt leeren,
        # das Backup bleibt als Sicherheitsnetz erhalten.
        log("Datenverzeichnis ist ein Mountpunkt – Inhalt wird vor dem Restore geleert.")
        _clear_directory(target, log)

    target.mkdir(parents=True, exist_ok=True)
    log(f"Stelle Daten aus '{archive_path}' wieder her ...")
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            try:
                tar.extractall(path=str(parent), filter="data")
            except TypeError:  # Python < 3.12
                tar.extractall(path=str(parent))
    except (OSError, tarfile.TarError) as exc:
        raise OperationError(f"Wiederherstellung fehlgeschlagen: {exc}") from exc
    log("Daten wiederhergestellt.")


# ---------------------------------------------------------------------------
# Rollback-Assets
# ---------------------------------------------------------------------------


def _snapshot_payload(
    attrs: dict,
    container: str,
    data_dir: str,
    archive: str,
    script: str,
    rollback_image: str,
    original_image: str,
) -> dict:
    networks = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
    return {
        "version": 1,
        "created": datetime.now().isoformat(timespec="seconds"),
        "container": container,
        "data_dir": data_dir,
        "backup_file": archive,
        "rollback_script": script,
        "rollback_image": rollback_image,
        "original_image": original_image,
        "inspect": {
            "Name": attrs.get("Name"),
            "Config": attrs.get("Config") or {},
            "HostConfig": attrs.get("HostConfig") or {},
            "Mounts": attrs.get("Mounts") or [],
            "NetworkSettings": {"Networks": networks},
        },
    }


def write_rollback_script(
    attrs: dict,
    container: str,
    data_dir: str,
    archive: str,
    rollback_image: str,
    script_path: str,
) -> None:
    run_command = docker_ops.build_run_command(attrs, rollback_image)
    content = f"""#!/bin/bash
# Automatisch erzeugt von Docker Update UI am {datetime.now():%Y-%m-%d %H:%M:%S}
# Auf dem Docker-Host ausführen, z. B.: sudo bash "{script_path}"
#
# Das Skript stoppt/entfernt den Container, sichert die aktuellen Daten,
# spielt das Backup zurück und erstellt den Container mit dem Rollback-Image.

set -euo pipefail

CONTAINER={json.dumps(container)}
DATA_DIR={json.dumps(data_dir)}
BACKUP_FILE={json.dumps(archive)}
ROLLBACK_IMAGE={json.dumps(rollback_image)}

log() {{ echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }}
die() {{ echo "[$(date '+%Y-%m-%d %H:%M:%S')] FEHLER: $*" >&2; exit 1; }}

log "Stoppe und entferne Container ..."
docker stop "$CONTAINER" 2>/dev/null || true
docker rm -f "$CONTAINER" 2>/dev/null || true

log "Stelle Daten wieder her ..."
if [ -d "$DATA_DIR" ]; then
    mv "$DATA_DIR" "${{DATA_DIR}}.before-rollback.$(date +%s)" || die "Konnte Datenverzeichnis nicht verschieben"
fi
tar -xzf "$BACKUP_FILE" -C "$(dirname "$DATA_DIR")" || die "Restore fehlgeschlagen"

log "Erstelle Container mit Rollback-Image neu ..."
{run_command}

log "Rollback abgeschlossen."
"""
    path = Path(script_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, "utf-8")
    path.chmod(0o755)


def save_snapshot(snapshot_path: str, payload: dict) -> None:
    path = Path(snapshot_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), "utf-8")


def cleanup_rollbacks(
    container: str, settings: Settings, repo: str, log=print
) -> None:
    """Alte Backup-Dateien und Rollback-Images auf max_rollbacks begrenzen."""
    base = container_backup_dir(settings, container)
    keep = settings.max_rollbacks

    if base.is_dir():
        archives = sorted(
            (
                path
                for path in base.iterdir()
                if path.is_file()
                and re.fullmatch(rf"{re.escape(container)}-{_TS_RE}\.tar\.gz", path.name)
            ),
            key=lambda path: path.name,
        )
        stale = archives[:-keep] if len(archives) > keep else []
        for archive in stale:
            timestamp = archive.name[len(container) + 1 : -len(".tar.gz")]
            log(f"Lösche altes Backup {archive.name}")
            for path in (
                archive,
                base / f"rollback_{container}_{timestamp}.sh",
                base / f"rollback_{container}_{timestamp}.json",
            ):
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    log(f"  WARNUNG: {path.name} nicht löschbar: {exc}")

    try:
        images = docker_ops.list_rollback_images(repo)
    except Exception as exc:  # Docker-Fehler dürfen den Ablauf nicht stoppen
        log(f"WARNUNG: Rollback-Images nicht prüfbar: {exc}")
        images = []

    for image in images[keep:]:
        log(f"Lösche altes Rollback-Image {image['ref']}")
        docker_ops.remove_image(image["ref"], log)

    docker_ops.prune_dangling()


# ---------------------------------------------------------------------------
# Backup-Übersicht
# ---------------------------------------------------------------------------


def list_backups(container: str, settings: Settings) -> dict:
    base = container_backup_dir(settings, container)
    backups: list[dict] = []
    current_data_dir = config.get_data_dir(container)

    if base.is_dir():
        pattern = re.compile(
            rf"^{re.escape(container)}-{_TS_RE}\.tar\.gz$"
        )
        for path in sorted(base.iterdir(), key=lambda item: item.name, reverse=True):
            match = pattern.match(path.name)
            if not match:
                continue
            timestamp = match.group(1)
            script = base / f"rollback_{container}_{timestamp}.sh"
            snapshot = base / f"rollback_{container}_{timestamp}.json"
            rollback_image = None
            data_dir = None
            if snapshot.is_file():
                try:
                    payload = json.loads(snapshot.read_text("utf-8"))
                    rollback_image = payload.get("rollback_image")
                    data_dir = payload.get("data_dir")
                except (OSError, ValueError):
                    pass
            backups.append(
                {
                    "timestamp": timestamp,
                    "created": datetime.strptime(timestamp, TIMESTAMP_FMT),
                    "archive": str(path),
                    "archive_name": path.name,
                    "size": path.stat().st_size,
                    "size_text": format_size(path.stat().st_size),
                    "script": str(script) if script.is_file() else None,
                    "snapshot": str(snapshot) if snapshot.is_file() else None,
                    "has_snapshot": snapshot.is_file(),
                    "rollback_image": rollback_image,
                    "data_dir": data_dir,
                }
            )

    repo = None
    try:
        detail = docker_ops.container_detail(container)
        repo = docker_ops.image_repo(detail["image"])
    except Exception:
        for backup in backups:
            if backup["rollback_image"]:
                repo = docker_ops.image_repo(backup["rollback_image"])
                break

    images: list[dict] = []
    if repo:
        try:
            images = docker_ops.list_rollback_images(repo)
        except Exception:
            images = []

    points = {backup["timestamp"] for backup in backups}
    image_by_timestamp: dict[str, dict] = {}
    for image in images:
        try:
            image["point"] = datetime.strptime(image["timestamp"], TIMESTAMP_FMT)
        except ValueError:
            image["point"] = None
        image["orphan"] = image["timestamp"] not in points
        image_by_timestamp[image["timestamp"]] = image

    present = {image["ref"] for image in images}
    for backup in backups:
        # Punkte der alten Skript-Version haben keinen JSON-Snapshot. Das
        # Rollback-Image ergibt sich dann aus dem Tag-Namen.
        if not backup["rollback_image"]:
            match_image = image_by_timestamp.get(backup["timestamp"])
            if match_image:
                backup["rollback_image"] = match_image["ref"]
        if not backup["data_dir"]:
            backup["data_dir"] = current_data_dir

        backup["image_present"] = bool(backup["rollback_image"]) and (
            backup["rollback_image"] in present
        )

        problems: list[str] = []
        if not backup["rollback_image"]:
            problems.append("Zu diesem Zeitpunkt existiert kein Rollback-Image.")
        elif backup["rollback_image"] not in present:
            problems.append("Das Rollback-Image wurde gelöscht.")
        if not backup["data_dir"]:
            problems.append("Es ist kein Datenverzeichnis hinterlegt.")
        backup["restorable"] = not problems
        backup["restore_hint"] = " ".join(problems) or None

    return {
        "container": container,
        "backup_dir": str(base),
        "backups": backups,
        "images": images,
        "orphan_images": [image for image in images if image["orphan"]],
        "max_rollbacks": settings.max_rollbacks,
    }


def cleanup_orphan_images(
    container: str, settings: Settings, log=print
) -> list[str]:
    """Rollback-Images löschen, zu denen es keinen Sicherungspunkt gibt."""
    data = list_backups(container, settings)
    orphans = data["orphan_images"]
    if not orphans:
        log("Keine Rollback-Images ohne Sicherung gefunden.")
        return []

    removed: list[str] = []
    for image in orphans:
        log(f"Lösche verwaistes Rollback-Image {image['ref']}")
        if docker_ops.remove_image(image["ref"], log):
            removed.append(image["ref"])
    docker_ops.prune_dangling()
    log(f"{len(removed)} von {len(orphans)} Image(s) gelöscht.")
    return removed


def delete_backup(container: str, timestamp: str, settings: Settings, log=print) -> None:
    paths = backup_paths(settings, container, timestamp)
    removed = []
    for key in ("archive", "script", "snapshot"):
        path = Path(paths[key])
        if path.is_file():
            path.unlink()
            removed.append(path.name)
    if removed:
        log("Gelöscht: " + ", ".join(removed))
    else:
        log("Keine Dateien gefunden.")


def read_backup_script(container: str, timestamp: str, settings: Settings) -> str:
    path = Path(backup_paths(settings, container, timestamp)["script"])
    if not path.is_file():
        raise OperationError("Kein Rollback-Skript für diesen Zeitpunkt vorhanden.")
    return path.read_text("utf-8")


# ---------------------------------------------------------------------------
# Update-Ablauf
# ---------------------------------------------------------------------------


def _unique_timestamp(settings: Settings, container: str) -> str:
    """Zeitstempel, der noch zu keinem Sicherungspunkt gehört."""
    while True:
        timestamp = datetime.now().strftime(TIMESTAMP_FMT)
        if not Path(backup_paths(settings, container, timestamp)["archive"]).exists():
            return timestamp
        time.sleep(1)


def _capture_rollback_point(
    container: str, detail: dict, settings: Settings, log=print
) -> dict:
    """Sicherungspunkt anlegen: Image taggen, Daten archivieren, Snapshot + Skript.

    Der Container wird dafür gestoppt und anschließend wieder gestartet, sofern
    er vorher lief.
    """
    data_dir = config.get_data_dir(container)
    if not data_dir:
        raise OperationError(
            "Für diesen Container ist kein Datenverzeichnis hinterlegt. "
            "Bitte zuerst das Datenverzeichnis auswählen."
        )
    if not os.path.isdir(data_dir):
        raise OperationError(
            f"Datenverzeichnis '{data_dir}' ist im Container nicht sichtbar. "
            f"Bitte '- {data_dir}:{data_dir}' als Volume einbinden."
        )

    current = detail["image"]
    attrs = detail["attrs"]
    repo = docker_ops.image_repo(current)
    timestamp = _unique_timestamp(settings, container)
    paths = backup_paths(settings, container, timestamp)
    rollback_image = f"{repo}:rollback-{timestamp}"
    was_running = detail["status"] == "running"

    Path(paths["dir"]).mkdir(parents=True, exist_ok=True)

    log(f"Container: {container}")
    log(f"Daten:     {data_dir}")
    log(f"Backup:    {paths['archive']}")
    log(f"Rollback:  {rollback_image}")

    if not config.path_is_persistent(settings.backup_dir):
        log(
            "WARNUNG: Das Backup-Verzeichnis liegt nicht auf einem eingebundenen "
            "Volume. Backups gehen beim Neuerstellen des Tool-Containers verloren."
        )

    log("Tagge aktuelles Image als Rollback-Punkt ...")
    docker_ops.tag_rollback_image(container, repo, f"rollback-{timestamp}")
    log(f"Image getaggt: {rollback_image}")

    try:
        docker_ops.stop_container(container, log)
        backup_data_dir(data_dir, paths["archive"], log)

        payload = _snapshot_payload(
            attrs,
            container,
            data_dir,
            paths["archive"],
            paths["script"],
            rollback_image,
            current,
        )
        save_snapshot(paths["snapshot"], payload)
        log(f"Container-Konfiguration gesichert: {paths['snapshot']}")

        write_rollback_script(
            attrs,
            container,
            data_dir,
            paths["archive"],
            rollback_image,
            paths["script"],
        )
        log(f"Rollback-Skript erstellt: {paths['script']}")

        cleanup_rollbacks(container, settings, repo, log)
    except Exception:
        if was_running:
            try:
                docker_ops.start_container(container, log)
            except Exception as start_error:  # noqa: BLE001
                log(f"WARNUNG: Container konnte nicht gestartet werden: {start_error}")
        raise

    return {
        "timestamp": timestamp,
        "paths": paths,
        "rollback_image": rollback_image,
        "data_dir": data_dir,
        "repo": repo,
        "was_running": was_running,
    }


def prepare_update(container: str, settings: Settings, log=print) -> dict:
    """Prüfen, ob ein Update bzw. eine Umstellung möglich ist."""
    detail = docker_ops.container_detail(container)
    if detail["is_self"]:
        raise OperationError(
            "Dieses Tool kann sich nicht selbst aktualisieren. "
            "Bitte den Container mit docker compose pull/up neu starten."
        )

    current = detail["image"]
    data_dir = config.get_data_dir(container)

    if docker_ops.is_rollback_image(current):
        repo = docker_ops.image_repo(current)
        result = {
            "mode": "switch",
            "status": "UMSTELLUNG AUF REGULÄRES IMAGE",
            "current": current,
            "target": f"{repo}:latest",
            "check": {
                "status": "Rollback-Image erkannt",
                "detail": "Der Container läuft auf einem Rollback-Image. Das "
                "Image wird nach dem Backup wieder auf ':latest' umgestellt.",
                "updateable": True,
                "target": f"{repo}:latest",
            },
        }
    else:
        log(f"Prüfe Update-Verfügbarkeit für '{current}' ...")
        check = docker_ops.check_update(container, log)
        log(f"Status: {check['status']}")
        result = {
            "mode": "update",
            "status": check["status"],
            "current": current,
            "target": check["target"],
            "check": check,
        }

    result.update(
        {
            "container": container,
            "data_dir": data_dir,
            "detail": detail,
            "backup_dir": str(container_backup_dir(settings, container)),
            "max_rollbacks": settings.max_rollbacks,
        }
    )
    return result


def execute_update(container: str, settings: Settings, log=print) -> dict:
    """Kompletter Update-Ablauf inklusive Backup, Rollback-Punkt und Update."""
    detail = docker_ops.container_detail(container)
    if detail["is_self"]:
        raise OperationError(
            "Dieses Tool kann sich nicht selbst aktualisieren."
        )

    current = detail["image"]
    attrs = detail["attrs"]
    data_dir = config.get_data_dir(container)
    if not data_dir:
        raise OperationError(
            "Für diesen Container ist kein Datenverzeichnis hinterlegt. "
            "Bitte zuerst das Datenverzeichnis auswählen."
        )
    if not os.path.isdir(data_dir):
        raise OperationError(
            f"Datenverzeichnis '{data_dir}' ist im Container nicht sichtbar. "
            f"Bitte '- {data_dir}:{data_dir}' als Volume einbinden."
        )

    if docker_ops.is_rollback_image(current):
        mode = "switch"
        target = f"{docker_ops.image_repo(current)}:latest"
        status = "UMSTELLUNG AUF REGULÄRES IMAGE"
    else:
        mode = "update"
        target = current
        log(f"Prüfe Update-Verfügbarkeit für '{current}' ...")
        check = docker_ops.check_update(container, log)
        if not check["updateable"]:
            raise OperationError(
                f"Kein Update verfügbar (Status: {check['status']}). "
                "Es wurde kein Backup erstellt."
            )
        status = check["status"]

    log(f"Container: {container}")
    log(f"Aktion:    {status}")

    point = _capture_rollback_point(container, detail, settings, log)
    timestamp = point["timestamp"]
    paths = point["paths"]
    rollback_image = point["rollback_image"]
    data_dir = point["data_dir"]
    repo = point["repo"]

    try:
        if mode == "switch":
            log(f"Pulle reguläres Image '{target}' ...")
            docker_ops.pull_image(target, log)
            docker_ops.recreate_container(attrs, target, log)
        else:
            docker_ops.watchtower_update(container, settings.watchtower_image, log)
    except Exception:
        # Container läuft nach Möglichkeit wieder starten, damit ein Fehler
        # nicht zu unnötiger Ausfallzeit führt.
        try:
            if not docker_ops.container_is_running(container):
                docker_ops.start_container(container, log)
        except Exception as start_error:  # noqa: BLE001
            log(f"WARNUNG: Container konnte nicht gestartet werden: {start_error}")
        raise

    if not docker_ops.container_is_running(container):
        docker_ops.start_container(container, log)

    new_image = docker_ops.get_container_attrs(container)["Config"]["Image"]
    log("Fertig.")
    log(f"  Container:       {container}")
    log(f"  Aktion:          {status}")
    log(f"  Neues Image:     {new_image}")
    log(f"  Rollback-Image:  {rollback_image}")
    log(f"  Daten-Backup:    {paths['archive']}")
    log(f"  Rollback-Skript: {paths['script']}")

    return {
        "container": container,
        "mode": mode,
        "status": status,
        "image": new_image,
        "rollback_image": rollback_image,
        "backup_file": paths["archive"],
        "rollback_script": paths["script"],
        "timestamp": timestamp,
    }


def execute_snapshot(container: str, settings: Settings, log=print) -> dict:
    """Sicherungspunkt anlegen, ohne das Image zu aktualisieren."""
    detail = docker_ops.container_detail(container)
    if detail["is_self"]:
        raise OperationError("Für dieses Tool selbst wird kein Snapshot erstellt.")

    point = _capture_rollback_point(container, detail, settings, log)

    if point["was_running"] and not docker_ops.container_is_running(container):
        docker_ops.start_container(container, log)

    log("Snapshot fertig – das Image wurde nicht verändert.")
    log(f"  Rollback-Image: {point['rollback_image']}")
    log(f"  Daten-Backup:   {point['paths']['archive']}")

    return {
        "container": container,
        "mode": "snapshot",
        "status": "SNAPSHOT",
        "image": detail["image"],
        "rollback_image": point["rollback_image"],
        "backup_file": point["paths"]["archive"],
        "rollback_script": point["paths"]["script"],
        "timestamp": point["timestamp"],
    }


# ---------------------------------------------------------------------------
# Rollback-Ablauf
# ---------------------------------------------------------------------------


def rollback_plan(container: str, timestamp: str, settings: Settings) -> dict:
    paths = backup_paths(settings, container, timestamp)
    archive = Path(paths["archive"])
    snapshot = Path(paths["snapshot"])
    if not archive.is_file():
        raise OperationError(f"Backup '{archive}' nicht gefunden.")

    legacy = False
    if snapshot.is_file():
        try:
            payload = json.loads(snapshot.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise OperationError(
                f"Snapshot '{snapshot}' ist nicht lesbar: {exc}"
            ) from exc
        image = payload.get("rollback_image")
        if not image:
            raise OperationError("Im Snapshot ist kein Rollback-Image hinterlegt.")
    else:
        # Punkt der alten Skript-Version: Die Container-Konfiguration wird vom
        # laufenden Container übernommen, das Image über den Tag-Namen gesucht.
        legacy = True
        try:
            detail = docker_ops.container_detail(container)
        except Exception as exc:  # noqa: BLE001
            raise OperationError(
                "Zur Sicherung fehlt die Container-Konfiguration "
                f"({snapshot.name}) und der Container ist nicht lesbar: {exc}"
            ) from exc
        image = docker_ops.find_rollback_image(timestamp) or (
            f"{docker_ops.image_repo(detail['image'])}:rollback-{timestamp}"
        )
        payload = _snapshot_payload(
            detail["attrs"],
            container,
            config.get_data_dir(container) or "",
            paths["archive"],
            paths["script"],
            image,
            detail["image"],
        )

    if not docker_ops.image_exists(image):
        raise OperationError(
            f"Rollback-Image '{image}' ist nicht mehr vorhanden. "
            "Ein Rollback ist nicht möglich."
        )

    return {
        "container": container,
        "timestamp": timestamp,
        "archive": str(archive),
        "snapshot": str(snapshot),
        "image": image,
        "data_dir": payload.get("data_dir"),
        "original_image": payload.get("original_image"),
        "created": payload.get("created"),
        "script": paths["script"],
        "legacy": legacy,
        "has_snapshot": snapshot.is_file(),
        "payload": payload,
    }


def execute_rollback(
    container: str, timestamp: str, settings: Settings, log=print
) -> dict:
    plan = rollback_plan(container, timestamp, settings)
    data_dir = plan["data_dir"]
    if not data_dir:
        raise OperationError("Im Snapshot ist kein Datenverzeichnis hinterlegt.")
    if not os.path.isdir(data_dir):
        raise OperationError(
            f"Datenverzeichnis '{data_dir}' ist im Container nicht sichtbar. "
            f"Bitte '- {data_dir}:{data_dir}' als Volume einbinden."
        )

    log(f"Rollback für '{container}' auf Stand {plan['created'] or timestamp}")
    log(f"Rollback-Image: {plan['image']}")
    log(f"Backup:         {plan['archive']}")

    payload = plan["payload"]
    attrs = payload["inspect"]
    if plan["legacy"]:
        log(
            "Für diesen Punkt gibt es keinen Snapshot – die Konfiguration wird "
            "vom laufenden Container übernommen."
        )

    docker_ops.stop_container(container, log)
    restore_data_dir(plan["archive"], data_dir, log)
    docker_ops.recreate_container(attrs, plan["image"], log)

    if not plan["has_snapshot"]:
        try:
            save_snapshot(plan["snapshot"], payload)
            log(f"Konfiguration als Snapshot nachgetragen: {plan['snapshot']}")
        except OSError as exc:
            log(f"WARNUNG: Snapshot konnte nicht gespeichert werden: {exc}")

    if not docker_ops.container_is_running(container):
        docker_ops.start_container(container, log)

    log("Rollback abgeschlossen.")
    return {
        "container": container,
        "image": plan["image"],
        "data_dir": data_dir,
        "backup_file": plan["archive"],
        "timestamp": timestamp,
        "legacy": plan["legacy"],
    }
