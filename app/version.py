"""Versionsangaben für die Oberfläche."""

from __future__ import annotations

import os

from . import __version__

VERSION = __version__

# Wird beim Bauen des Images als Umgebungsvariable gesetzt (Dockerfile,
# Build-Argument GIT_COMMIT) und lässt sich zur Laufzeit über die Umgebung
# überschreiben, etwa aus der .env.
COMMIT_ENV = "GIT_COMMIT"


def commit() -> str:
    """Kurzer Commit-Hash des Builds – leer, wenn unbekannt."""
    value = (os.environ.get(COMMIT_ENV) or "").strip()
    return value[:12]
