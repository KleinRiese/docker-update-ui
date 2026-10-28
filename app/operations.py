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

from . import config, docker_ops, i18n
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


def _point_timestamps(base: Path, container: str) -> set[str]:
    """Zeitstempel aller Sicherungspunkte (Archiv und/oder Snapshot)."""
    if not base.is_dir():
        return set()
    archive_re = re.compile(rf"^{re.escape(container)}-{_TS_RE}\.tar\.gz$")
    snapshot_re = re.compile(rf"^rollback_{re.escape(container)}_{_TS_RE}\.json$")
    stamps: set[str] = set()
    for path in base.iterdir():
        match = archive_re.match(path.name) or snapshot_re.match(path.name)
        if match:
            stamps.add(match.group(1))
    return stamps


# ---------------------------------------------------------------------------
# Backup / Restore der Daten
# ---------------------------------------------------------------------------


def backup_data_dir(data_dir: str, archive: str, log=print) -> int:
    source = Path(data_dir)
    if not source.is_dir():
        raise OperationError(i18n.t("op.data_dir_missing", path=data_dir))

    target = Path(archive)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    if partial.exists():
        partial.unlink()

    log(i18n.t("op.backup_creating", path=target))
    count = 0

    def _filter(info: tarfile.TarInfo):
        nonlocal count
        count += 1
        if count % 500 == 0:
            log(i18n.t("op.backup_entries", count=count))
        return info

    try:
        with tarfile.open(partial, "w:gz", format=tarfile.PAX_FORMAT) as tar:
            tar.add(str(source), arcname=source.name, filter=_filter)
        os.replace(partial, target)
    except (OSError, tarfile.TarError) as exc:
        if partial.exists():
            partial.unlink()
        raise OperationError(i18n.t("op.backup_failed", error=exc)) from exc

    size = target.stat().st_size
    log(i18n.t("op.backup_done", size=format_size(size), count=count))
    return size


def _clear_directory(path: Path, log=print) -> None:
    for entry in path.iterdir():
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError as exc:
            log(i18n.t("op.remove_warning", path=entry, error=exc))


def restore_data_dir(archive: str, data_dir: str, log=print) -> None:
    archive_path = Path(archive)
    if not archive_path.is_file():
        raise OperationError(i18n.t("op.backup_not_found", path=archive))

    target = Path(data_dir)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and not os.path.ismount(str(target)):
        moved = parent / f"{target.name}.before-rollback.{int(time.time())}"
        try:
            target.rename(moved)
            log(i18n.t("op.moved_aside", path=moved))
        except OSError as exc:
            log(i18n.t("op.move_failed", error=exc))
            _clear_directory(target, log)
    elif target.exists():
        # Gemountetes Verzeichnis kann nicht umbenannt werden: Inhalt leeren,
        # das Backup bleibt als Sicherheitsnetz erhalten.
        log(i18n.t("op.mountpoint_cleared"))
        _clear_directory(target, log)

    target.mkdir(parents=True, exist_ok=True)
    log(i18n.t("op.restoring", path=archive_path))
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            try:
                tar.extractall(path=str(parent), filter="data")
            except TypeError:  # Python < 3.12
                tar.extractall(path=str(parent))
    except (OSError, tarfile.TarError) as exc:
        raise OperationError(i18n.t("op.restore_failed", error=exc)) from exc
    log(i18n.t("op.restored"))


# ---------------------------------------------------------------------------
# Rollback-Assets
# ---------------------------------------------------------------------------


def _snapshot_payload(
    attrs: dict,
    container: str,
    data_dir: str | None,
    archive: str | None,
    script: str,
    rollback_image: str,
    original_image: str,
    data_backup: bool = True,
) -> dict:
    networks = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
    return {
        "version": 1,
        "created": datetime.now().isoformat(timespec="seconds"),
        "container": container,
        "data_dir": data_dir or "",
        "data_backup": bool(data_backup),
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
    data_dir: str | None,
    archive: str | None,
    rollback_image: str,
    script_path: str,
    data_backup: bool = True,
) -> None:
    run_command = docker_ops.build_run_command(attrs, rollback_image)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    comment = i18n.t("script.comment" if data_backup else "script.comment_nodata")

    lines: list[str] = [
        "#!/bin/bash",
        f"# {i18n.t('script.title', created=stamp)}",
        f"# {i18n.t('script.run_hint')} \"{script_path}\"",
        "#",
    ]
    for part in comment.splitlines():
        lines.append(part if part.lstrip().startswith("#") else f"# {part}")
    lines += [
        "",
        "set -euo pipefail",
        "",
        f"CONTAINER={json.dumps(container)}",
        f"DATA_DIR={json.dumps(data_dir or '')}",
        f"BACKUP_FILE={json.dumps(archive or '')}",
        f"ROLLBACK_IMAGE={json.dumps(rollback_image)}",
        "",
        "log() { echo \"[$(date '+%Y-%m-%d %H:%M:%S')] $*\"; }",
        "die() { echo \"[$(date '+%Y-%m-%d %H:%M:%S')] "
        f"{i18n.t('script.error_prefix')}: $*\" >&2; exit 1; }}",
        "",
        f"log \"{i18n.t('script.stop_remove')}\"",
        "docker stop \"$CONTAINER\" 2>/dev/null || true",
        "docker rm -f \"$CONTAINER\" 2>/dev/null || true",
        "",
    ]
    if data_backup:
        lines += [
            f"log \"{i18n.t('script.restore')}\"",
            "if [ -d \"$DATA_DIR\" ]; then",
            "    mv \"$DATA_DIR\" \"${DATA_DIR}.before-rollback.$(date +%s)\""
            f" || die \"{i18n.t('script.move_failed')}\"",
            "fi",
            "tar -xzf \"$BACKUP_FILE\" -C \"$(dirname \"$DATA_DIR\")\""
            f" || die \"{i18n.t('script.restore_failed')}\"",
            "",
        ]
    lines += [
        f"log \"{i18n.t('script.recreate')}\"",
        run_command,
        "",
        f"log \"{i18n.t('script.done')}\"",
        "",
    ]

    path = Path(script_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), "utf-8")
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
        ordered = sorted(_point_timestamps(base, container))
        stale = ordered[:-keep] if len(ordered) > keep else []
        for timestamp in stale:
            log(
                i18n.t(
                    "op.prune_backup", name=f"{container}-{timestamp}.tar.gz"
                )
            )
            for path in (
                base / f"{container}-{timestamp}.tar.gz",
                base / f"rollback_{container}_{timestamp}.sh",
                base / f"rollback_{container}_{timestamp}.json",
            ):
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    log(i18n.t("op.prune_file_warning", name=path.name, error=exc))

    try:
        images = docker_ops.list_rollback_images(repo)
    except Exception as exc:  # Docker-Fehler dürfen den Ablauf nicht stoppen
        log(i18n.t("op.prune_images_warning", error=exc))
        images = []

    for image in images[keep:]:
        log(i18n.t("op.prune_image", ref=image["ref"]))
        docker_ops.remove_image(image["ref"], log)

    docker_ops.prune_dangling()


# ---------------------------------------------------------------------------
# Backup-Übersicht
# ---------------------------------------------------------------------------


def list_backups(container: str, settings: Settings) -> dict:
    base = container_backup_dir(settings, container)
    backups: list[dict] = []
    current_data_dir = config.get_data_dir(container)

    for timestamp in sorted(_point_timestamps(base, container), reverse=True):
        archive = base / f"{container}-{timestamp}.tar.gz"
        script = base / f"rollback_{container}_{timestamp}.sh"
        snapshot = base / f"rollback_{container}_{timestamp}.json"

        payload: dict = {}
        if snapshot.is_file():
            try:
                payload = json.loads(snapshot.read_text("utf-8"))
            except (OSError, ValueError):
                payload = {}

        has_data_backup = archive.is_file()
        data_backup = bool(payload.get("data_backup", True))
        size = archive.stat().st_size if has_data_backup else 0
        backups.append(
            {
                "timestamp": timestamp,
                "created": datetime.strptime(timestamp, TIMESTAMP_FMT),
                "archive": str(archive) if has_data_backup else None,
                "archive_name": archive.name,
                "size": size,
                "size_text": format_size(size) if has_data_backup else "–",
                "script": str(script) if script.is_file() else None,
                "snapshot": str(snapshot) if snapshot.is_file() else None,
                "has_snapshot": snapshot.is_file(),
                "has_data_backup": has_data_backup,
                "data_backup": data_backup,
                "rollback_image": payload.get("rollback_image"),
                "data_dir": payload.get("data_dir"),
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

    present = {docker_ops.normalize_image_ref(image["ref"]) for image in images}
    for backup in backups:
        # Punkte der alten Skript-Version haben keinen JSON-Snapshot. Das
        # Rollback-Image ergibt sich dann aus dem Tag-Namen.
        if not backup["rollback_image"]:
            match_image = image_by_timestamp.get(backup["timestamp"])
            if match_image:
                backup["rollback_image"] = match_image["ref"]
        if not backup["data_dir"]:
            backup["data_dir"] = current_data_dir

        ref = docker_ops.normalize_image_ref(backup["rollback_image"])
        if ref and ref not in present:
            # Snapshots älterer Versionen können den Tag mit Registry-Präfix
            # oder führendem Schrägstrich gespeichert haben. Der Tag existiert
            # dann trotzdem, nur unter anderem Namen.
            try:
                existing = docker_ops.find_rollback_image(backup["timestamp"])
            except Exception:  # noqa: BLE001 - Docker-Fehler nicht durchreichen
                existing = None
            if existing:
                ref = docker_ops.normalize_image_ref(existing)
                present.add(ref)
        if ref:
            backup["rollback_image"] = ref

        backup["image_present"] = bool(ref) and ref in present

        problems: list[str] = []
        if not ref:
            problems.append(i18n.t("err.no_rollback_image"))
        elif ref not in present:
            problems.append(i18n.t("err.rollback_image_deleted"))
        if backup["data_backup"] and not backup["has_data_backup"]:
            problems.append(i18n.t("err.backup_missing"))
        if backup["data_backup"] and not backup["data_dir"]:
            problems.append(i18n.t("err.no_data_dir"))
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
        log(i18n.t("op.orphans_none"))
        return []

    removed: list[str] = []
    for image in orphans:
        log(i18n.t("op.orphan_delete", ref=image["ref"]))
        if docker_ops.remove_image(image["ref"], log):
            removed.append(image["ref"])
    docker_ops.prune_dangling()
    log(i18n.t("op.orphans_deleted", removed=len(removed), total=len(orphans)))
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
        log(i18n.t("op.deleted_files", files=", ".join(removed)))
    else:
        log(i18n.t("op.no_files"))


def read_backup_script(container: str, timestamp: str, settings: Settings) -> str:
    path = Path(backup_paths(settings, container, timestamp)["script"])
    if not path.is_file():
        raise OperationError(i18n.t("op.no_script"))
    return path.read_text("utf-8")


# ---------------------------------------------------------------------------
# Update-Ablauf
# ---------------------------------------------------------------------------


def _unique_timestamp(settings: Settings, container: str) -> str:
    """Zeitstempel, der noch zu keinem Sicherungspunkt gehört."""
    while True:
        timestamp = datetime.now().strftime(TIMESTAMP_FMT)
        paths = backup_paths(settings, container, timestamp)
        if not Path(paths["archive"]).exists() and not Path(
            paths["snapshot"]
        ).exists():
            return timestamp
        time.sleep(1)


def _capture_rollback_point(
    container: str,
    detail: dict,
    settings: Settings,
    log=print,
    backup_data: bool = True,
) -> dict:
    """Sicherungspunkt anlegen: Image taggen, Daten archivieren, Snapshot + Skript.

    Ohne ``backup_data`` wird nur das Image getaggt; Datenverzeichnis und
    Datenarchiv bleiben unangetastet. Der Container wird in beiden Fällen
    gestoppt und anschließend wieder gestartet, sofern er vorher lief.
    """
    data_dir = config.get_data_dir(container)
    if backup_data:
        if not data_dir:
            raise OperationError(i18n.t("op.no_data_dir"))
        if not os.path.isdir(data_dir):
            raise OperationError(
                i18n.t("op.data_dir_invisible", path=data_dir)
            )
    elif data_dir and not os.path.isdir(data_dir):
        log(i18n.t("op.data_warning", path=data_dir))

    current = detail["image"]
    attrs = detail["attrs"]
    repo = docker_ops.image_repo(current)
    timestamp = _unique_timestamp(settings, container)
    paths = backup_paths(settings, container, timestamp)
    rollback_image = f"{repo}:rollback-{timestamp}"
    was_running = detail["status"] == "running"

    Path(paths["dir"]).mkdir(parents=True, exist_ok=True)

    log(i18n.t("op.container", name=container))
    if backup_data:
        log(i18n.t("op.data", path=data_dir))
        log(i18n.t("op.backup_path", path=paths["archive"]))
    log(i18n.t("op.rollback_image", ref=rollback_image))

    if not config.path_is_persistent(settings.backup_dir):
        log(i18n.t("op.backup_not_persistent_warning"))

    log(i18n.t("op.tag_image"))
    docker_ops.tag_rollback_image(container, repo, f"rollback-{timestamp}")
    log(i18n.t("op.image_tagged", ref=rollback_image))

    try:
        docker_ops.stop_container(container, log)
        if backup_data:
            backup_data_dir(data_dir or "", paths["archive"], log)

        payload = _snapshot_payload(
            attrs,
            container,
            data_dir,
            paths["archive"] if backup_data else None,
            paths["script"],
            rollback_image,
            current,
            data_backup=backup_data,
        )
        save_snapshot(paths["snapshot"], payload)
        log(i18n.t("op.snapshot_saved", path=paths["snapshot"]))

        write_rollback_script(
            attrs,
            container,
            data_dir,
            paths["archive"] if backup_data else None,
            rollback_image,
            paths["script"],
            data_backup=backup_data,
        )
        log(i18n.t("op.script_written", path=paths["script"]))

        cleanup_rollbacks(container, settings, repo, log)
    except Exception:
        if was_running:
            try:
                docker_ops.start_container(container, log)
            except Exception as start_error:  # noqa: BLE001
                log(i18n.t("op.start_failed", error=start_error))
        raise

    return {
        "timestamp": timestamp,
        "paths": paths,
        "rollback_image": rollback_image,
        "data_dir": data_dir if backup_data else None,
        "data_backup": backup_data,
        "repo": repo,
        "was_running": was_running,
    }


def prepare_update(container: str, settings: Settings, log=print) -> dict:
    """Prüfen, ob ein Update bzw. eine Umstellung möglich ist."""
    detail = docker_ops.container_detail(container)
    if detail["is_self"]:
        raise OperationError(i18n.t("op.self_update"))

    current = detail["image"]
    data_dir = config.get_data_dir(container)
    backup_data = config.get_backup_data(container)

    if docker_ops.is_rollback_image(current):
        repo = docker_ops.image_repo(current)
        result = {
            "mode": "switch",
            "status": i18n.t("op.switch_status"),
            "current": current,
            "target": f"{repo}:latest",
            "check": {
                "status": i18n.t("op.rollback_detected"),
                "detail": i18n.t("op.rollback_detected_detail"),
                "updateable": True,
                "target": f"{repo}:latest",
            },
        }
    else:
        log(i18n.t("op.checking", ref=current))
        check = docker_ops.check_update(container, log)
        log(i18n.t("op.status", status=check["status"]))
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
            "backup_data": backup_data,
            "detail": detail,
            "backup_dir": str(container_backup_dir(settings, container)),
            "max_rollbacks": settings.max_rollbacks,
        }
    )
    return result


def _store_check_state(container: str, image: str) -> None:
    """Prüfergebnis nach einem Update auf den neuen Stand setzen.

    Ohne diesen Schritt bliebe der alte Befund („Update verfügbar“) in der
    Übersicht stehen, bis die nächste Prüfung läuft.
    """
    config.record_auto_check(
        container,
        {
            "at": round(time.time(), 3),
            "state": "current",
            "target": image,
            "method": "pull",
            "remote": "",
            "local": [],
            "error": "",
        },
    )


def _forget_check_state(container: str) -> None:
    """Prüfergebnis verwerfen, nachdem das Image absichtlich gewechselt wurde."""
    config.record_auto_check(container, None)


def execute_update(container: str, settings: Settings, log=print) -> dict:
    """Kompletter Update-Ablauf inklusive Rollback-Punkt und Update."""
    detail = docker_ops.container_detail(container)
    if detail["is_self"]:
        raise OperationError(i18n.t("op.self_update"))

    current = detail["image"]
    attrs = detail["attrs"]
    backup_data = config.get_backup_data(container)
    data_dir = config.get_data_dir(container)
    if backup_data:
        if not data_dir:
            raise OperationError(i18n.t("op.no_data_dir"))
        if not os.path.isdir(data_dir):
            raise OperationError(i18n.t("op.data_dir_invisible", path=data_dir))

    if docker_ops.is_rollback_image(current):
        mode = "switch"
        target = f"{docker_ops.image_repo(current)}:latest"
        status = i18n.t("op.switch_status")
    else:
        mode = "update"
        target = current
        log(i18n.t("op.checking", ref=current))
        check = docker_ops.check_update(container, log)
        if not check["updateable"]:
            raise OperationError(
                i18n.t("op.no_update", status=check["status"])
            )
        status = check["status"]

    log(i18n.t("op.container", name=container))
    log(i18n.t("op.action", status=status))

    point = _capture_rollback_point(
        container, detail, settings, log, backup_data=backup_data
    )
    timestamp = point["timestamp"]
    paths = point["paths"]
    rollback_image = point["rollback_image"]

    try:
        if mode == "switch":
            log(i18n.t("op.pull_regular", ref=target))
        else:
            log(i18n.t("op.pull_target", ref=target))
        docker_ops.pull_image(target, log)
        docker_ops.recreate_container(attrs, target, log)
    except Exception:
        # Container läuft nach Möglichkeit wieder starten, damit ein Fehler
        # nicht zu unnötiger Ausfallzeit führt.
        try:
            if not docker_ops.container_is_running(container):
                docker_ops.start_container(container, log)
        except Exception as start_error:  # noqa: BLE001
            log(i18n.t("op.start_failed", error=start_error))
        raise

    if not docker_ops.container_is_running(container):
        docker_ops.start_container(container, log)

    new_image = docker_ops.get_container_attrs(container)["Config"]["Image"]
    log(i18n.t("op.finished"))
    log(i18n.t("op.summary.container", name=container))
    log(i18n.t("op.summary.action", status=status))
    log(i18n.t("op.summary.image", ref=new_image))
    log(i18n.t("op.summary.rollback", ref=rollback_image))
    if backup_data:
        log(i18n.t("op.summary.backup", path=paths["archive"]))
    else:
        log(i18n.t("op.summary.no_backup"))
    log(i18n.t("op.summary.script", path=paths["script"]))

    _store_check_state(container, new_image)

    return {
        "container": container,
        "mode": mode,
        "status": status,
        "image": new_image,
        "rollback_image": rollback_image,
        "backup_file": paths["archive"] if backup_data else None,
        "rollback_script": paths["script"],
        "timestamp": timestamp,
        "data_backup": backup_data,
    }


# ---------------------------------------------------------------------------
# Tag-Wechsel
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def resolve_target_image(current: str, value: str) -> str:
    """Ziel-Image aus Tag oder vollständiger Referenz ableiten.

    Akzeptiert ``1.2.3``, ``repo:1.2.3`` und ``repo@sha256:...``; das Repository
    muss dem des laufenden Containers entsprechen.
    """
    raw = (value or "").strip()
    if not raw:
        raise OperationError(i18n.t("err.tag_required"))
    if any(character.isspace() for character in raw):
        raise OperationError(i18n.t("err.tag_invalid", value=raw))

    repo = docker_ops.image_repo(current)

    if "@" in raw:
        base, _, digest = raw.partition("@")
        if base and base != repo:
            raise OperationError(
                i18n.t("err.tag_other_repo", repo=repo, value=raw)
            )
        if not _DIGEST_RE.match(digest):
            raise OperationError(i18n.t("err.tag_invalid", value=raw))
        return f"{repo}@{digest}"

    if "/" in raw:
        candidate_repo, candidate_tag = docker_ops.split_image_ref(raw)
        if candidate_repo != repo:
            raise OperationError(
                i18n.t("err.tag_other_repo", repo=repo, value=raw)
            )
        if not _TAG_RE.match(candidate_tag):
            raise OperationError(i18n.t("err.tag_invalid", value=raw))
        return f"{repo}:{candidate_tag}"

    if not _TAG_RE.match(raw):
        raise OperationError(i18n.t("err.tag_invalid", value=raw))
    return f"{repo}:{raw}"


def execute_switch_tag(
    container: str, tag: str, settings: Settings, log=print
) -> dict:
    """Container auf ein anderes Tag desselben Repositories umstellen."""
    detail = docker_ops.container_detail(container)
    if detail["is_self"]:
        raise OperationError(i18n.t("op.self_update"))

    current = detail["image"]
    target = resolve_target_image(current, tag)
    if target == current:
        raise OperationError(i18n.t("op.tag_same", ref=current))

    backup_data = config.get_backup_data(container)
    data_dir = config.get_data_dir(container)
    if backup_data:
        if not data_dir:
            raise OperationError(i18n.t("op.no_data_dir"))
        if not os.path.isdir(data_dir):
            raise OperationError(i18n.t("op.data_dir_invisible", path=data_dir))

    log(i18n.t("op.container", name=container))
    log(i18n.t("op.tag_from_to", current=current, target=target))

    point = _capture_rollback_point(
        container, detail, settings, log, backup_data=backup_data
    )

    try:
        log(i18n.t("op.pull_target", ref=target))
        docker_ops.pull_image(target, log)
        docker_ops.recreate_container(detail["attrs"], target, log)
    except Exception:
        try:
            if not docker_ops.container_is_running(container):
                docker_ops.start_container(container, log)
        except Exception as start_error:  # noqa: BLE001
            log(i18n.t("op.start_failed", error=start_error))
        raise

    if not docker_ops.container_is_running(container):
        docker_ops.start_container(container, log)

    new_image = docker_ops.get_container_attrs(container)["Config"]["Image"]
    log(i18n.t("op.finished"))
    log(i18n.t("op.summary.container", name=container))
    log(i18n.t("op.summary.action", status=i18n.t("op.tag_status")))
    log(i18n.t("op.summary.image", ref=new_image))
    log(i18n.t("op.summary.rollback", ref=point["rollback_image"]))
    if backup_data:
        log(i18n.t("op.summary.backup", path=point["paths"]["archive"]))
    else:
        log(i18n.t("op.summary.no_backup"))
    log(i18n.t("op.summary.script", path=point["paths"]["script"]))

    _forget_check_state(container)

    return {
        "container": container,
        "mode": "tag",
        "status": i18n.t("op.tag_status"),
        "image": new_image,
        "target": target,
        "rollback_image": point["rollback_image"],
        "backup_file": point["paths"]["archive"] if backup_data else None,
        "rollback_script": point["paths"]["script"],
        "timestamp": point["timestamp"],
        "data_backup": backup_data,
    }


def execute_snapshot(container: str, settings: Settings, log=print) -> dict:
    """Sicherungspunkt anlegen, ohne das Image zu aktualisieren."""
    detail = docker_ops.container_detail(container)
    if detail["is_self"]:
        raise OperationError(i18n.t("op.self_snapshot"))

    backup_data = config.get_backup_data(container)
    point = _capture_rollback_point(
        container, detail, settings, log, backup_data=backup_data
    )

    if point["was_running"] and not docker_ops.container_is_running(container):
        docker_ops.start_container(container, log)

    log(i18n.t("op.snapshot_done"))
    log(i18n.t("op.summary.rollback", ref=point["rollback_image"]))
    if point["data_backup"]:
        log(i18n.t("op.summary.backup", path=point["paths"]["archive"]))
    else:
        log(i18n.t("op.summary.no_backup"))

    return {
        "container": container,
        "mode": "snapshot",
        "status": i18n.t("op.snapshot_status"),
        "image": detail["image"],
        "rollback_image": point["rollback_image"],
        "backup_file": point["paths"]["archive"] if point["data_backup"] else None,
        "rollback_script": point["paths"]["script"],
        "timestamp": point["timestamp"],
        "data_backup": point["data_backup"],
    }


# ---------------------------------------------------------------------------
# Rollback-Ablauf
# ---------------------------------------------------------------------------


def rollback_plan(container: str, timestamp: str, settings: Settings) -> dict:
    paths = backup_paths(settings, container, timestamp)
    archive = Path(paths["archive"])
    snapshot = Path(paths["snapshot"])
    has_archive = archive.is_file()

    legacy = False
    if snapshot.is_file():
        try:
            payload = json.loads(snapshot.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            raise OperationError(
                i18n.t("op.snapshot_unreadable", path=snapshot, error=exc)
            ) from exc
        image = payload.get("rollback_image")
        if not image:
            raise OperationError(i18n.t("op.rollback_image_missing"))
        image = docker_ops.normalize_image_ref(image)
        data_backup = bool(payload.get("data_backup", True))
    else:
        # Punkt der alten Skript-Version: Die Container-Konfiguration wird vom
        # laufenden Container übernommen, das Image über den Tag-Namen gesucht.
        legacy = True
        data_backup = True
        if not has_archive:
            raise OperationError(i18n.t("op.backup_not_found", path=archive))
        try:
            detail = docker_ops.container_detail(container)
        except Exception as exc:  # noqa: BLE001
            raise OperationError(
                i18n.t(
                    "op.rollback_config_missing", name=snapshot.name, error=exc
                )
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
            data_backup=True,
        )

    if data_backup and not has_archive:
        raise OperationError(i18n.t("op.backup_not_found", path=archive))

    if not docker_ops.image_exists(image):
        # Fallback für Snapshots, deren Tag-Schreibweise Docker nicht auflöst:
        # der Rollback-Tag wird notfalls über seinen Zeitstempel gesucht.
        try:
            alternative = docker_ops.find_rollback_image(timestamp)
        except Exception:  # noqa: BLE001 - Docker-Fehler nicht durchreichen
            alternative = None
        if alternative and alternative != image and docker_ops.image_exists(
            alternative
        ):
            image = alternative
        else:
            raise OperationError(i18n.t("op.rollback_image_gone", ref=image))

    payload["rollback_image"] = image

    return {
        "container": container,
        "timestamp": timestamp,
        "archive": str(archive) if has_archive else None,
        "snapshot": str(snapshot),
        "image": image,
        "data_dir": payload.get("data_dir"),
        "data_backup": data_backup,
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
    if plan["data_backup"]:
        if not data_dir:
            raise OperationError(i18n.t("op.no_data_dir_snapshot"))
        if not os.path.isdir(data_dir):
            raise OperationError(i18n.t("op.data_dir_invisible", path=data_dir))

    log(
        i18n.t(
            "op.rollback_start",
            name=container,
            created=plan["created"] or timestamp,
        )
    )
    log(i18n.t("op.rollback_image_line", ref=plan["image"]))
    if plan["data_backup"]:
        log(i18n.t("op.rollback_backup_line", path=plan["archive"]))

    payload = plan["payload"]
    attrs = payload["inspect"]
    if plan["legacy"]:
        log(i18n.t("op.legacy_point"))

    docker_ops.stop_container(container, log)
    if plan["data_backup"]:
        restore_data_dir(plan["archive"], data_dir, log)
    else:
        log(i18n.t("op.no_data_backup"))
    docker_ops.recreate_container(attrs, plan["image"], log)

    if not plan["has_snapshot"]:
        try:
            save_snapshot(plan["snapshot"], payload)
            log(i18n.t("op.snapshot_added", path=plan["snapshot"]))
        except OSError as exc:
            log(i18n.t("op.snapshot_save_failed", error=exc))

    if not docker_ops.container_is_running(container):
        docker_ops.start_container(container, log)

    log(i18n.t("op.rollback_done"))
    _forget_check_state(container)
    return {
        "container": container,
        "image": plan["image"],
        "data_dir": data_dir if plan["data_backup"] else None,
        "backup_file": plan["archive"],
        "timestamp": timestamp,
        "legacy": plan["legacy"],
        "data_backup": plan["data_backup"],
    }
