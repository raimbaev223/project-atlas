"""In-process job registry for the desktop backend.

One build per project at a time; the UI polls ``/job/<id>``.  Jobs are short-lived
(on the order of a model call per node), so an in-memory registry is honest: a
gateway restart loses the live progress, while ``atlas.yaml`` and the changelog
keep everything that was actually written.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import model

_LOCK = threading.Lock()
_JOBS: dict[str, "Job"] = {}
_RUNNING: dict[str, str] = {}


@dataclass
class Job:
    id: str
    project: str
    scope: str
    status: str = "queued"
    done: int = 0
    total: int = 0
    node: str = ""
    phase: str = ""
    message: str = ""
    error: str | None = None
    result: dict[str, Any] | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    log: list[str] = field(default_factory=list)

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id, "project": self.project, "scope": self.scope,
            "status": self.status, "done": self.done, "total": self.total,
            "node": self.node, "phase": self.phase, "message": self.message,
            "error": self.error, "result": self.result,
            "started": self.started, "finished": self.finished,
            "log": self.log[-40:],
        }


def get(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        job = _JOBS.get(job_id)
        return job.payload() if job else None


def latest(project: str | None = None) -> dict[str, Any] | None:
    with _LOCK:
        candidates = [j for j in _JOBS.values() if not project or j.project == project]
        if not candidates:
            return None
        return max(candidates, key=lambda j: j.started).payload()


def running(project: str) -> str | None:
    with _LOCK:
        return _RUNNING.get(project)


def start(project: str, scope: str, runner: Callable[["Job"], dict[str, Any]]) -> dict[str, Any]:
    project = str(Path(project).expanduser().resolve())
    with _LOCK:
        if project in _RUNNING:
            raise RuntimeError(f"a build is already running for {project}")
        job = Job(id=f"atlas-{int(time.time())}-{secrets.token_hex(3)}", project=project, scope=scope)
        _JOBS[job.id] = job
        _RUNNING[project] = job.id
        # keep the registry bounded
        if len(_JOBS) > 60:
            for stale in sorted(_JOBS.values(), key=lambda j: j.started)[:20]:
                if stale.status in {"done", "error"}:
                    _JOBS.pop(stale.id, None)
    thread = threading.Thread(target=_run, args=(job, runner), name=f"atlas-job-{job.id}", daemon=True)
    thread.start()
    return job.payload()


def _run(job: Job, runner: Callable[[Job], dict[str, Any]]) -> None:
    with _LOCK:
        job.status = "running"
        job.message = "старт"
    try:
        result = runner(job)
        with _LOCK:
            job.status = "done"
            job.result = result
            job.message = "готово"
    except Exception as exc:  # noqa: BLE001 - the UI needs the text, not a stack
        with _LOCK:
            job.status = "error"
            job.error = str(exc)
            job.message = "ошибка"
    finally:
        with _LOCK:
            job.finished = time.time()
            _RUNNING.pop(job.project, None)
        _persist(job)


def update(job: Job, **payload: Any) -> None:
    with _LOCK:
        for key, value in payload.items():
            if key == "log":
                job.log.append(str(value))
            elif hasattr(job, key):
                setattr(job, key, value)


def _persist(job: Job) -> None:
    path = model.data_dir(job.project) / model.JOBS_DIR / "last.json"
    try:
        model.write_json(path, job.payload())
    except OSError:
        pass


def progress_for(job: Job) -> Callable[..., None]:
    def report(**payload: Any) -> None:
        update(job, **payload)
    return report


def dump(job_id: str) -> str:
    payload = get(job_id)
    return json.dumps(payload, ensure_ascii=False)
