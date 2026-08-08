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
