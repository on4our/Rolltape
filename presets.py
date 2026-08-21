"""Named brand kits — theme, footer and default title, saved between runs.

Render jobs are deliberately in-memory and vanish on restart. A brand kit is the opposite:
the whole point of naming one is that it is still there next week, so this is the one piece
of state that touches the disk. It stays a single small JSON document because there is one
user and a handful of kits — a database would be more machinery than the problem has.

On a serverless host the file lands in /tmp and does not outlive the instance, so kits
there are effectively per-instance. Making them durable needs the same kind of shared
backend that `jobs.py` describes for job state.
"""

import json
import os
import threading

import config

# Reentrant because save() and delete() read under the same lock they write under.
_LOCK = threading.RLock()

# `fit` belongs here for the same reason theme does: which app a channel posts to is
# picked once and then never thought about again.
FIELDS = ("theme", "footer", "title_format", "fit")

MAX_KITS = 50
MAX_NAME = 40
MAX_FIELD = 120


def _read():
    """Every failure here returns {} rather than raising.

    A brand kit is a convenience: if the file is missing, corrupt or unreadable the right
    behaviour is an empty list the user can save over, not an app that won't start.
    """
    try:
        with open(config.PRESETS_PATH, encoding="utf-8") as fh:
            saved = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(saved, dict):
        return {}
    return {name: kit for name, kit in saved.items() if isinstance(kit, dict)}


def _write(kits):
    os.makedirs(os.path.dirname(config.PRESETS_PATH), exist_ok=True)
    tmp = f"{config.PRESETS_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(kits, fh, indent=2, sort_keys=True)
    # Rename rather than write in place, so a crash mid-save can't leave a truncated file
    # where a valid one used to be.
    os.replace(tmp, config.PRESETS_PATH)


def _clean(name, kit):
    name = str(name or "").strip()
    if not name:
        raise ValueError("Name the brand kit before saving it.")
    if len(name) > MAX_NAME:
        raise ValueError(f"Keep the kit name under {MAX_NAME} characters.")

    cleaned = {}
    for field in FIELDS:
        value = str(kit.get(field) or "").strip()
        if len(value) > MAX_FIELD:
            raise ValueError(f"That {field.replace('_', ' ')} is too long.")
        cleaned[field] = value
    return name, cleaned


def all_kits():
    with _LOCK:
        return _read()


def save(name, kit):
    name, cleaned = _clean(name, kit)
    with _LOCK:
        kits = _read()
        if name not in kits and len(kits) >= MAX_KITS:
            raise ValueError(f"That's {MAX_KITS} brand kits already — delete one first.")
        kits[name] = cleaned
        _write(kits)
    return name, cleaned


def delete(name):
    with _LOCK:
        kits = _read()
        if name not in kits:
            return False
        del kits[name]
        _write(kits)
        return True
