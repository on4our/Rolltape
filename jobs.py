"""The render job registry.

One process with one worker thread can just hold jobs in a dict, and that is the shape the
app is deployed in — hence `--workers 1` in the Dockerfile. Run a second worker process
and you get a second registry: renders start, finish, and never appear in the UI that
asked for them.

If jobs ever need to outlive the process or be shared across several, this module is the
seam to swap. A networked implementation would have to throttle `update` — the progress
callback fires once per frame, a few hundred times a render, which is nothing against a
dict and far too chatty against a network store.
"""

import threading
from collections import OrderedDict

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
