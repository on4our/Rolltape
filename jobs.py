"""The render job registry.

One process with one worker thread can just hold jobs in a dict. A serverless host
can't: the instance that accepts /api/render may not be the one that answers the
/api/jobs poll, so the registry has to move somewhere shared. This module is the seam
where that swap happens.

A KV-backed implementation must throttle `update` — the progress callback fires once per
frame (~120 times for a short draft), which is fine against a dict and far too chatty
against a network store.
"""

import threading
from collections import OrderedDict

import config

_LOCK = threading.Lock()
_JOBS = OrderedDict()

KEEP = 25  # how many jobs the UI lists


def create(job):
    with _LOCK:
        _JOBS[job["id"]] = job
        # Unbounded growth is the only leak in a long-running local session.
        while len(_JOBS) > KEEP * 4:
            _JOBS.popitem(last=False)
    return job


def get(job_id):
    with _LOCK:
        return _JOBS.get(job_id)


def update(job_id, **fields):
    """Apply field changes to a job. In-memory this is just a dict update; a shared
    backend would write through here."""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job.update(fields)
        return job


def recent(limit=KEEP):
    with _LOCK:
        return list(_JOBS.values())[-limit:][::-1]


if config.JOBS != "memory":
    raise RuntimeError(
        f"Unknown ROLLTAPE_JOBS={config.JOBS!r}. Only 'memory' is implemented — a shared "
        "backend is still required before jobs survive across serverless instances."
    )
