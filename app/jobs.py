"""Hintergrund-Jobs mit Live-Log für die Weboberfläche."""

from __future__ import annotations

import contextvars
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field

from . import i18n


@dataclass
class Job:
    id: str
    title: str
    meta: dict = field(default_factory=dict)
    status: str = "running"  # running | success | error
    lines: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        with self._lock:
            for line in str(message).splitlines() or [""]:
                self.lines.append(f"[{stamp}] {line}")

    @property
    def duration(self) -> float:
        return (self.finished or time.time()) - self.started

    def finish(self, result: dict | None = None) -> None:
        with self._lock:
            self.status = "success"
            self.result = result or {}
            self.finished = time.time()

    def fail(self, message: str) -> None:
        with self._lock:
            self.status = "error"
            self.error = message
            self.finished = time.time()


class JobManager:
    def __init__(self, retention: float = 3600, max_jobs: int = 50) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._retention = retention
        self._max_jobs = max_jobs

    def start(self, title: str, target, meta: dict | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], title=title, meta=meta or {})
        with self._lock:
            self._jobs[job.id] = job
            self._cleanup()
        # Sprachwahl des auslösenden Requests in den Job-Thread übernehmen.
        context = contextvars.copy_context()
        thread = threading.Thread(
            target=context.run,
            args=(self._run, job, target),
            name=f"job-{job.id}",
            daemon=True,
        )
        thread.start()
        return job

    def failed(self, title: str, message: str, meta: dict | None = None) -> Job:
        """Sofort fehlgeschlagenen Job anlegen (z. B. Vorprüfung)."""
        job = Job(id=uuid.uuid4().hex[:12], title=title, meta=meta or {})
        job.fail(message)
        with self._lock:
            self._jobs[job.id] = job
            self._cleanup()
        return job

    def _run(self, job: Job, target) -> None:
        try:
            result = target(job)
            job.finish(result if isinstance(result, dict) else None)
        except Exception as exc:  # noqa: BLE001 - Fehler werden im UI angezeigt
            traceback.print_exc()
            message = str(exc) or exc.__class__.__name__
            job.log(f"{i18n.t('job.log_error')}: {message}")
            job.fail(message)

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def active_for(self, container: str) -> bool:
        """Läuft für diesen Container gerade ein Job?"""
        with self._lock:
            return any(
                job.status == "running" and job.meta.get("container") == container
                for job in self._jobs.values()
            )

    def _cleanup(self) -> None:
        now = time.time()
        finished = [
            job
            for job in self._jobs.values()
            if job.finished and now - job.finished > self._retention
        ]
        for job in finished:
            self._jobs.pop(job.id, None)
        if len(self._jobs) > self._max_jobs:
            ordered = sorted(
                self._jobs.values(), key=lambda item: item.started
            )
            for job in ordered[: len(self._jobs) - self._max_jobs]:
                if job.status != "running":
                    self._jobs.pop(job.id, None)


jobs = JobManager()
