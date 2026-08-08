"""Where a finished MP4 ends up.

A local run keeps the file in outputs/ and serves it itself. A serverless host has no
persistent disk, so the file has to be pushed somewhere durable and referenced by URL.
Both answer the same two questions: where do I render to, and what URL plays the result?
"""

import json
import os
import urllib.request

import config


def ensure_out_dir():
    """Create the output dir on first write rather than at import.

    Import-time makedirs is what breaks the app on a read-only filesystem — the module
    can't even load far enough to report the problem.
    """
    os.makedirs(config.OUT_DIR, exist_ok=True)
    return config.OUT_DIR


# ---------------------------------------------------------------------------
# local — the default, and the only path a local run takes
# ---------------------------------------------------------------------------
def _local_target(name):
    return os.path.join(ensure_out_dir(), name)


def _local_publish(path, name):
    return f"/outputs/{name}"


# ---------------------------------------------------------------------------
# blob — Vercel Blob over its REST API (stdlib only, no SDK)
# ---------------------------------------------------------------------------
def _blob_target(name):
    # /tmp is the only writable location on a serverless host, and it does not persist
    # past the invocation — hence the upload in _blob_publish.
    tmp = os.path.join("/tmp", "rolltape")
    os.makedirs(tmp, exist_ok=True)
    return os.path.join(tmp, name)


def _blob_mime(name):
    """A transparent render is ProRes in a .mov, so the type can't be assumed."""
    return "video/quicktime" if name.lower().endswith(".mov") else "video/mp4"


def _blob_publish(path, name):
    token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if not token:
        raise RuntimeError(
            "ROLLTAPE_STORAGE=blob needs BLOB_READ_WRITE_TOKEN in the environment."
        )
    with open(path, "rb") as fh:
        body = fh.read()
    mime = _blob_mime(name)
    req = urllib.request.Request(
        f"https://blob.vercel-storage.com/{name}",
        data=body,
        method="PUT",
        headers={
            "authorization": f"Bearer {token}",
            "x-api-version": "7",
            "x-content-type": mime,
            "content-type": mime,
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        url = json.loads(resp.read().decode())["url"]
    os.remove(path)  # /tmp is small and shared across invocations on a warm instance
    return url


_BACKENDS = {
    "local": (_local_target, _local_publish),
    "blob": (_blob_target, _blob_publish),
}


def _backend():
    try:
        return _BACKENDS[config.STORAGE]
    except KeyError:
        raise RuntimeError(
            f"Unknown ROLLTAPE_STORAGE={config.STORAGE!r}. "
            f"Options: {', '.join(sorted(_BACKENDS))}."
        )


def render_target(name):
    """Absolute path the renderer should write the MP4 to."""
    return _backend()[0](name)


def publish(path, name):
    """Make the rendered file reachable and return the URL that plays it."""
    return _backend()[1](path, name)
