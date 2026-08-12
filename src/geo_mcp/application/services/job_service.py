from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Callable, Protocol


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Job:
    id: str
    kind: str
    status: JobStatus = JobStatus.QUEUED
    progress: float = 0.0
    total: float | None = 100.0
    message: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    cancel_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "kind": self.kind,
            "status": self.status.value,
            "progress": self.progress,
            "total": self.total,
            "message": self.message,
            "result": self.result,
            "error": self.error,
            "created_at": datetime.fromtimestamp(
                self.created_at, tz=timezone.utc
            ).isoformat(),
            "updated_at": datetime.fromtimestamp(
                self.updated_at, tz=timezone.utc
            ).isoformat(),
            "cancel_requested": self.cancel_requested,
        }


class JobStore(Protocol):
    def create(self, kind: str) -> Job: ...

    def get(self, job_id: str) -> Job | None: ...

    def update(self, job: Job) -> None: ...

    def list_jobs(self, *, limit: int = 50) -> list[Job]: ...

    def purge_expired(self, ttl_s: float) -> int: ...


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()

    def create(self, kind: str) -> Job:
        job = Job(id=str(uuid.uuid4()), kind=kind)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job: Job) -> None:
        job.updated_at = time.time()
        with self._lock:
            self._jobs[job.id] = job

    def list_jobs(self, *, limit: int = 50) -> list[Job]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return jobs[:limit]

    def purge_expired(self, ttl_s: float) -> int:
        cutoff = time.time() - ttl_s
        with self._lock:
            expired = [
                jid
                for jid, job in self._jobs.items()
                if job.updated_at < cutoff
                and job.status
                in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
            ]
            for jid in expired:
                del self._jobs[jid]
            return len(expired)


class JobService:
    """Deferred background execution with cooperative cancel + progress."""

    def __init__(
        self,
        store: JobStore,
        *,
        max_workers: int = 4,
        ttl_s: float = 3600.0,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._store = store
        self._ttl_s = ttl_s
        self._executor = executor or ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="geo-job"
        )
        self._owns_executor = executor is None
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def submit(
        self,
        kind: str,
        fn: Callable[[Job], dict[str, Any]],
    ) -> Job:
        self._store.purge_expired(self._ttl_s)
        job = self._store.create(kind)

        def _runner() -> None:
            job.status = JobStatus.RUNNING
            job.message = "started"
            self._store.update(job)
            try:
                if job.cancel_requested:
                    job.status = JobStatus.CANCELLED
                    job.message = "cancelled before start"
                    self._store.update(job)
                    return
                result = fn(job)
                if job.cancel_requested:
                    job.status = JobStatus.CANCELLED
                    job.message = "cancelled"
                else:
                    job.result = result
                    job.status = JobStatus.COMPLETED
                    job.progress = job.total or 100.0
                    job.message = "completed"
                self._store.update(job)
            except Exception as exc:
                job.status = JobStatus.FAILED
                job.error = str(exc)
                job.message = "failed"
                self._store.update(job)

        future = self._executor.submit(_runner)
        with self._lock:
            self._futures[job.id] = future
        return job

    def get(self, job_id: str) -> Job | None:
        return self._store.get(job_id)

    def cancel(self, job_id: str) -> Job | None:
        job = self._store.get(job_id)
        if job is None:
            return None
        job.cancel_requested = True
        if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
            job.message = "cancel requested"
            self._store.update(job)
        with self._lock:
            future = self._futures.get(job_id)
        if future is not None and not future.done():
            future.cancel()
        return job

    def list_jobs(self, *, limit: int = 50) -> list[Job]:
        return self._store.list_jobs(limit=limit)

    def report(self, job: Job, progress: float, total: float | None, message: str) -> None:
        if job.cancel_requested:
            raise RuntimeError("Job cancelled")
        job.progress = progress
        job.total = total
        job.message = message
        self._store.update(job)
