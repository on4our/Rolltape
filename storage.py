"""Where a finished render ends up.

Two questions, kept behind two functions: where does the renderer write, and what URL
plays the result. Right now both answers are "the outputs directory", because the app runs
as one long-lived process with a real filesystem — locally or in a container with a volume
mounted. Object storage would slot in here rather than in `app.py`, which is why the
indirection survives having only one implementation.
"""

import os

import config


def ensure_out_dir():
    """Create the output dir on first write rather than at import.

    Import-time makedirs is what breaks the app on a read-only filesystem — the module
    can't even load far enough to report the problem.
    """
    os.makedirs(config.OUT_DIR, exist_ok=True)
    return config.OUT_DIR


def render_target(name):
    """Absolute path the renderer should write to."""
    return os.path.join(ensure_out_dir(), name)


def publish(path, name):
    """Make the rendered file reachable and return the URL that plays it."""
    return f"/outputs/{name}"


# Only what a render writes. Anything else in the directory was put there by a person, and
# enforcing a disk ceiling is not the place to start guessing about that.
RENDER_SUFFIXES = (".mp4", ".mov")


def prune(keep=None):
    """Delete the oldest renders until the directory is under config.OUT_MAX_GB.

    Off unless a ceiling is set — see config.OUT_MAX_GB for why that is the right default
    on a laptop and the wrong one on a host anybody else can reach.

    `keep` is the render that has just finished, and it survives even when it alone is over
    the ceiling. The job is about to report a URL, and one that 404s the moment it appears
    is a worse outcome than a directory briefly above its limit — the next render brings it
    back down.

    Returns the names removed, which is what makes it checkable from a test.
    """
    limit = config.OUT_MAX_GB * 1e9
    if limit <= 0:
        return []
    try:
        names = os.listdir(config.OUT_DIR)
    except OSError:
        return []

    files = []
    for name in names:
        if not name.endswith(RENDER_SUFFIXES):
            continue
        path = os.path.join(config.OUT_DIR, name)
        try:
            info = os.stat(path)
        except OSError:      # vanished between listing and stat
            continue
        files.append((info.st_mtime, info.st_size, name, path))

    total = sum(size for _, size, _, _ in files)
    removed = []
    for _, size, name, path in sorted(files):     # oldest first
        if total <= limit:
            break
        if name == keep:
            continue
        try:
            os.remove(path)
        except OSError:
            continue
        total -= size
        removed.append(name)
    return removed
