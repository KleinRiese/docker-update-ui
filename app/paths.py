"""Verzeichnis-Navigation innerhalb des Containers (1:1 Host-Mounts)."""

from __future__ import annotations

import os

from .config import normalize_dir, require_dir


def browse(path: str | None, show_hidden: bool = False) -> dict:
    """Verzeichnisinhalt für den Pfad-Browser liefern."""
    try:
        current = normalize_dir(path or "/")
        if not os.path.isdir(current):
            current = "/"
    except ValueError:
        current = "/"

    parent = os.path.dirname(current) or None
    if parent == current:
        parent = None

    entries: list[dict] = []
    try:
        with os.scandir(current) as iterator:
            for entry in iterator:
                if not show_hidden and entry.name.startswith("."):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                entries.append(
                    {
                        "name": entry.name,
                        "path": os.path.join(current, entry.name),
                    }
                )
    except PermissionError:
        entries = []

    entries.sort(key=lambda item: item["name"].lower())
    return {
        "path": current,
        "parent": parent,
        "entries": entries,
        "show_hidden": show_hidden,
    }


def validate_data_dir(path: str) -> str:
    """Datenverzeichnis prüfen; wirft ValueError mit verständlichem Text."""
    return require_dir(path, "label.data_dir")
