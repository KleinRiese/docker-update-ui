"""Persistente Einstellungen und Zuordnung Container -> Datenverzeichnis."""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/config"))
SETTINGS_FILE = CONFIG_DIR / "settings.json"
DATA_DIRS_FILE = CONFIG_DIR / "data_dirs.json"

# Dateien des bisherigen Shell-Skripts, die beim ersten Start importiert werden.
LEGACY_SETTINGS_FILE = CONFIG_DIR / "settings.conf"
LEGACY_DATA_DIRS_FILE = CONFIG_DIR / "backup_paths.conf"


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "")))
    except (TypeError, ValueError):
        return default


DEFAULT_BACKUP_DIR = os.environ.get("BACKUP_DIR") or "/docker/backups"
DEFAULT_BROWSE_DIR = os.environ.get("BROWSE_DIR") or "/docker"
DEFAULT_WATCHTOWER_IMAGE = (
    os.environ.get("WATCHTOWER_IMAGE") or "nickfedor/watchtower"
)
DEFAULT_MAX_ROLLBACKS = _env_int("MAX_ROLLBACKS", 3)

_lock = threading.RLock()


@dataclass(frozen=True)
class Settings:
    backup_dir: str = DEFAULT_BACKUP_DIR
    browse_dir: str = DEFAULT_BROWSE_DIR
    watchtower_image: str = DEFAULT_WATCHTOWER_IMAGE
    max_rollbacks: int = DEFAULT_MAX_ROLLBACKS


def normalize_dir(path: str) -> str:
    """Absoluten Pfad normalisieren, ohne Symlinks aufzulösen."""
    raw = (path or "").strip()
    if not raw:
        raise ValueError("Bitte einen Pfad angeben.")
    if not raw.startswith("/"):
        raise ValueError(
            f"'{raw}' ist kein absoluter Pfad. Bitte mit / beginnen "
            "(Pfade im Container = Pfade auf dem Host)."
        )
    normalized = os.path.normpath(raw)
    if normalized == "//":
        normalized = "/"
    return normalized


def require_dir(path: str, label: str = "Verzeichnis") -> str:
    """Pfad validieren und sicherstellen, dass er im Container sichtbar ist."""
    normalized = normalize_dir(path)
    if not os.path.isdir(normalized):
        raise ValueError(
            f"{label} '{normalized}' ist im Container nicht vorhanden. "
            "Bitte den Ordner 1:1 als Volume einbinden "
            f"(- {normalized}:{normalized})."
        )
    return normalized


def ensure_writable_dir(path: str, label: str = "Verzeichnis") -> str:
    normalized = normalize_dir(path)
    try:
        Path(normalized).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(
            f"{label} '{normalized}' kann nicht angelegt werden: {exc}"
        ) from exc
    if not os.path.isdir(normalized):
        raise ValueError(f"{label} '{normalized}' ist kein Verzeichnis.")
    if not os.access(normalized, os.W_OK):
        raise ValueError(f"{label} '{normalized}' ist nicht beschreibbar.")
    return normalized


def _validate(settings: Settings) -> Settings:
    backup_dir = ensure_writable_dir(settings.backup_dir, "Backup-Verzeichnis")
    browse_dir = require_dir(settings.browse_dir, "Startverzeichnis")
    image = (settings.watchtower_image or "").strip()
    if not image or " " in image:
        raise ValueError("Bitte ein gültiges Watchtower-Image angeben.")
    try:
        max_rollbacks = int(settings.max_rollbacks)
    except (TypeError, ValueError) as exc:
        raise ValueError("Max. Rollbacks muss eine ganze Zahl sein.") from exc
    if max_rollbacks < 1:
        raise ValueError("Max. Rollbacks muss mindestens 1 sein.")
    return Settings(backup_dir, browse_dir, image, max_rollbacks)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_settings() -> Settings:
    with _lock:
        stored = _read_json(SETTINGS_FILE)
    merged = {
        "backup_dir": stored.get("backup_dir", DEFAULT_BACKUP_DIR),
        "browse_dir": stored.get("browse_dir", DEFAULT_BROWSE_DIR),
        "watchtower_image": stored.get(
            "watchtower_image", DEFAULT_WATCHTOWER_IMAGE
        ),
        "max_rollbacks": stored.get("max_rollbacks", DEFAULT_MAX_ROLLBACKS),
    }
    try:
        return _validate(Settings(**merged))
    except ValueError:
        # Gespeicherte Werte sind unbrauchbar (z. B. Mount entfernt) ->
        # Standardwerte verwenden, damit die Oberfläche erreichbar bleibt.
        return Settings()


def save_settings(settings: Settings) -> Settings:
    validated = _validate(settings)
    with _lock:
        _write_json(SETTINGS_FILE, asdict(validated))
    return validated


# ---------------------------------------------------------------------------
# Sichtbarkeit von Host-Mounts
# ---------------------------------------------------------------------------

# Diese Mountpunkte sind Infrastruktur und nicht als Daten-Volume gemeint.
_IGNORED_MOUNTS = {
    "/",
    "/proc",
    "/sys",
    "/dev",
    "/etc/hosts",
    "/etc/hostname",
    "/etc/resolv.conf",
}


def container_mounts() -> list[str]:
    """Mountpunkte des eigenen Containers aus /proc/self/mountinfo lesen."""
    mounts: list[str] = []
    try:
        with open("/proc/self/mountinfo", "r", encoding="utf-8") as handle:
            for line in handle:
                fields = line.split()
                if len(fields) > 4:
                    mounts.append(fields[4].replace("\\040", " "))
    except OSError:
        return []
    return mounts


def path_is_persistent(path: str) -> bool:
    """Prüfen, ob ein Pfad unter einem eingebundenen Volume liegt.

    Nur eine Warnung: Backups in der Container-Schicht gehen beim Neuerstellen
    des Containers verloren.
    """
    try:
        normalized = normalize_dir(path)
    except ValueError:
        return False
    for mount in container_mounts():
        if mount in _IGNORED_MOUNTS or mount.endswith(("/hosts", "/hostname", "/resolv.conf")):
            continue
        prefix = mount.rstrip("/")
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return True
    return False


# ---------------------------------------------------------------------------
# Datenverzeichnisse pro Container
# ---------------------------------------------------------------------------


def load_data_dirs() -> dict[str, str]:
    with _lock:
        raw = _read_json(DATA_DIRS_FILE)
    return {
        str(name): str(path)
        for name, path in raw.items()
        if isinstance(name, str) and isinstance(path, str)
    }


def get_data_dir(container: str) -> str | None:
    return load_data_dirs().get(container)


def set_data_dir(container: str, path: str) -> str:
    normalized = require_dir(path, "Datenverzeichnis")
    with _lock:
        mapping = load_data_dirs()
        mapping[container] = normalized
        _write_json(DATA_DIRS_FILE, mapping)
    return normalized


def remove_data_dir(container: str) -> None:
    with _lock:
        mapping = load_data_dirs()
        if mapping.pop(container, None) is not None:
            _write_json(DATA_DIRS_FILE, mapping)


# ---------------------------------------------------------------------------
# Import der Konfiguration des bisherigen Shell-Skripts
# ---------------------------------------------------------------------------


def _parse_legacy_settings(text: str) -> dict:
    keys = {
        "BACKUP_BASE_DIR": "backup_dir",
        "BROWSE_DIR": "browse_dir",
        "WATCHTOWER_IMAGE": "watchtower_image",
        "MAX_ROLLBACKS": "max_rollbacks",
    }
    values: dict = {}
    for line in text.splitlines():
        match = re.match(r"\s*([A-Z_]+)=\"?([^\"\n]*)\"?\s*$", line)
        if not match:
            continue
        target = keys.get(match.group(1))
        if target:
            values[target] = match.group(2).strip()
    return values


def import_legacy_config(log=print) -> None:
    """settings.conf / backup_paths.conf einmalig importieren."""
    with _lock:
        if not SETTINGS_FILE.exists() and LEGACY_SETTINGS_FILE.exists():
            try:
                data = _parse_legacy_settings(
                    LEGACY_SETTINGS_FILE.read_text("utf-8")
                )
                current = load_settings()
                merged = Settings(
                    backup_dir=data.get("backup_dir", current.backup_dir),
                    browse_dir=data.get("browse_dir", current.browse_dir),
                    watchtower_image=data.get(
                        "watchtower_image", current.watchtower_image
                    ),
                    max_rollbacks=int(
                        data.get("max_rollbacks", current.max_rollbacks)
                    ),
                )
                save_settings(merged)
                log(f"Einstellungen aus {LEGACY_SETTINGS_FILE} importiert.")
            except (OSError, ValueError) as exc:
                log(f"Import von {LEGACY_SETTINGS_FILE} fehlgeschlagen: {exc}")

        if not DATA_DIRS_FILE.exists() and LEGACY_DATA_DIRS_FILE.exists():
            try:
                mapping: dict[str, str] = {}
                for line in LEGACY_DATA_DIRS_FILE.read_text("utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    name, _, path = line.partition("=")
                    name, path = name.strip(), path.strip()
                    if name and path:
                        mapping[name] = path
                if mapping:
                    _write_json(DATA_DIRS_FILE, mapping)
                    log(
                        f"{len(mapping)} Datenverzeichnisse aus "
                        f"{LEGACY_DATA_DIRS_FILE} importiert."
                    )
            except OSError as exc:
                log(f"Import von {LEGACY_DATA_DIRS_FILE} fehlgeschlagen: {exc}")

        # Unbekannte Container-Einträge nicht löschen - der Benutzer kann sie
        # in den Einstellungen selbst entfernen.
