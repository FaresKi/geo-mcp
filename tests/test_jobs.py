from __future__ import annotations

import time

from geo_mcp.application.services.job_service import (
    InMemoryJobStore,
    JobService,
    JobStatus,
)


def test_job_lifecycle_success() -> None:
    service = JobService(InMemoryJobStore(), max_workers=2, ttl_s=60)

    def work(job):
        service.report(job, 50, 100, "halfway")
        return {"ok": True}

    job = service.submit("demo", work)
    deadline = time.time() + 2
    while time.time() < deadline:
        current = service.get(job.id)
        assert current is not None
        if current.status in {JobStatus.COMPLETED, JobStatus.FAILED}:
            break
        time.sleep(0.01)
    done = service.get(job.id)
    assert done is not None
    assert done.status == JobStatus.COMPLETED
    assert done.result == {"ok": True}
    service.close()


def test_job_failure() -> None:
    service = JobService(InMemoryJobStore(), max_workers=1, ttl_s=60)
    job = service.submit("boom", lambda _job: (_ for _ in ()).throw(RuntimeError("x")))
    deadline = time.time() + 2
    while time.time() < deadline:
        current = service.get(job.id)
        if current and current.status == JobStatus.FAILED:
            break
        time.sleep(0.01)
    failed = service.get(job.id)
    assert failed is not None
    assert failed.status == JobStatus.FAILED
    assert failed.error == "x"
    service.close()


def test_job_purge_expired() -> None:
    store = InMemoryJobStore()
    service = JobService(store, max_workers=1, ttl_s=0.01)
    job = service.submit("demo", lambda _job: {"a": 1})
    deadline = time.time() + 2
    while time.time() < deadline:
        current = service.get(job.id)
        if current and current.status == JobStatus.COMPLETED:
            break
        time.sleep(0.01)
    time.sleep(0.02)
    # Bypass store.update which refreshes updated_at
    done = store.get(job.id)
    assert done is not None
    done.updated_at = time.time() - 10
    store._jobs[done.id] = done
    purged = store.purge_expired(0.01)
    assert purged == 1
    assert store.get(job.id) is None
    service.close()
