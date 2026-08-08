"""Render one clip in a child process.

Rendering used to happen inside the Flask process, under the same lock that guards the
preview — `DRAW_LOCK` in `app.py`, which covered both. matplotlib's pyplot state is global,
so the lock is real, but sharing it meant a preview issued during a render waited for the
entire render. At 1080p60 that is roughly seventy seconds of a UI that fires a preview on
every control change.

Rendering out-of-process retires the problem rather than tuning it: the child gets its own
pyplot state, so the parent's lock no longer has to cover the render at all. Two things
fall out of it for free — an ffmpeg or a matplotlib that gets OOM-killed takes down a
child rather than the server, and a render is now interruptible by killing a pid.

The protocol is one JSON object per line on the child's stdout:

    {"progress": i, "total": n}   once per frame
    {"error": "..."}              the render failed, with a message meant for the user
    {"ok": true}                  the render finished; the file is at the path we gave it

stderr is merged into stdout deliberately. Draining a single pipe cannot deadlock the way
two can, and any line that isn't our JSON is kept as a diagnostic tail — which is the only
thing there is to go on when a child dies without managing to report anything itself.
"""

import json
import os
import signal
import subprocess
import sys
from collections import deque

import config
import data as datasrc
import renderers

HERE = os.path.dirname(os.path.abspath(__file__))

# Enough of the child's chatter to explain a death, not so much that a noisy dependency
# pushes the real cause out of the window.
TAIL_LINES = 8


_OOM_HINT = (" The host most likely ran out of memory — try the draft quality tier, or "
             "give the container more RAM.")


class RenderError(Exception):
    """A render that failed, carrying a message fit to show the user."""


def _signal_name(num):
    try:
        return signal.Signals(num).name
    except ValueError:
        return f"signal {num}"


def describe_error(exc):
    """Turn a render exception into something a user can act on.

    matplotlib surfaces an ffmpeg death as CalledProcessError, whose str() buries the
    ffmpeg command line in the message and, for a signal death, says only
    "died with <Signals.SIGKILL: 9>". A SIGKILLed ffmpeg is almost always the memory
    limit, so say that instead of making the user decode signal numbers.
    """
    if isinstance(exc, subprocess.CalledProcessError):
        if exc.returncode < 0:
            return f"ffmpeg was killed by {_signal_name(-exc.returncode)}.{_OOM_HINT}"
        err = exc.stderr or b""
        if isinstance(err, bytes):
            err = err.decode(errors="replace")
        tail = err.strip().splitlines()[-1] if err.strip() else ""
        return f"ffmpeg failed (exit {exc.returncode})" + (f": {tail}" if tail else ".")
    return str(exc)


# ---------------------------------------------------------------------------
# Parent side
# ---------------------------------------------------------------------------
def _died(code, tail):
    """Explain a child that exited without reporting anything itself.

    A render that merely raises catches its own exception and sends a message, so getting
    here means the process was killed outright or died before it could speak. The last
    line of its output is the useful one: for a Python death that is the exception itself.
    """
    if code < 0:
        detail = f"The renderer was killed by {_signal_name(-code)}."
        if -code == signal.SIGKILL:
            detail += _OOM_HINT
    else:
        detail = f"The renderer exited with status {code}."
    return f"{detail} {tail[-1]}" if tail else detail


def run(cfg, out_path, progress=None, demo=False):
    """Render `cfg` to `out_path` in a child process, reporting progress per frame.

    Blocks until the child is done, so the caller is expected to be the worker thread.
    Raises RenderError, whose message is meant to be shown to the user as-is.
    """
    env = dict(os.environ)
    # The child reads its configuration from the environment, so a `--demo` run has to say
    # so here — otherwise the flag stops at the server and children go to the network for
    # real prices behind a UI that says it is running on generated data.
    env["ROLLTAPE_DEMO"] = "1" if demo else "0"

    reported, ok = None, False
    tail = deque(maxlen=TAIL_LINES)

    # The `with` is what closes the pipes; draining stdout to EOF first means the wait it
    # does on the way out has nothing left to wait for.
    with subprocess.Popen(
        [sys.executable, os.path.join(HERE, "render_job.py"), out_path],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        env=env, text=True, bufsize=1,
    ) as child:
        try:
            json.dump(cfg, child.stdin)
            child.stdin.close()
        except (BrokenPipeError, OSError):
            pass  # died on startup; the exit code and tail explain it better than this

        for line in child.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                msg = None
            if not isinstance(msg, dict):
                tail.append(line)  # a stray print or a traceback — it may explain a death
            elif "progress" in msg and "total" in msg:
                if progress:
                    progress(msg["progress"], msg["total"])
            elif "error" in msg:
                reported = msg["error"]
            elif "ok" in msg:
                ok = True

        code = child.wait()

    if reported:
        raise RenderError(reported)
    if code != 0:
        raise RenderError(_died(code, tail))
    if not ok:
        raise RenderError(_died(code, tail) if tail else
                          "The renderer finished without producing a file.")
    return out_path


# ---------------------------------------------------------------------------
# Child side
# ---------------------------------------------------------------------------
def _emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _child(argv):
    if len(argv) < 2:
        _emit({"error": "render_job.py needs an output path."})
        return 2
    out_path = argv[1]
    try:
        cfg = json.load(sys.stdin)
    except ValueError as exc:
        _emit({"error": f"Could not read the render config: {exc}"})
        return 2

    datasrc.set_demo(config.DEMO)

    try:
        renderers.render(cfg, out_path, progress=lambda i, n: _emit({"progress": i,
                                                                    "total": n}))
    except Exception as exc:  # noqa: BLE001
        _emit({"error": describe_error(exc)})
        return 1
    _emit({"ok": True})
    return 0


if __name__ == "__main__":
    sys.exit(_child(sys.argv))
