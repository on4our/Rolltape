"""Email capture for the landing page.

Two backends, chosen by whether `config.SIGNUP_URL` is set, because the two places this
runs want different things. On a laptop there is no list provider and no reason to have
one, so an address appends to a local file. On the demo container there is no durable
disk — the filesystem goes away with the instance — so the address has to leave the
process, and the provider that will eventually send the launch email is the right place
for it to land.

Deliberately append-only and deliberately not readable over HTTP. There is no endpoint
that lists what has been collected: the file is for `wc -l` and a one-line export, and
anything more than that is what the provider is for.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request

import config

_LOCK = threading.Lock()

# Long enough for real addresses, short enough that the file can't be grown by someone
# posting a megabyte. RFC 5321 puts the true ceiling at 254.
MAX_EMAIL = 254
TIMEOUT = 8


class SignupError(Exception):
    """Something the visitor should see. Anything else is a 500 and a log line."""


def clean_email(raw):
    """Enough validation to catch a typo, and no more.

    Deliberately not a full RFC 5322 pattern. The only thing that actually proves an
    address is sending to it, so anything stricter than "one @ with something either
    side" rejects real addresses to no benefit.
    """
    email = str(raw or "").strip()
    if not email:
        raise SignupError("Enter an email address.")
    if len(email) > MAX_EMAIL:
        raise SignupError("That address is too long.")
    local, sep, domain = email.partition("@")
    if not (sep and local and "." in domain and not domain.startswith(".")
            and not domain.endswith(".")):
        raise SignupError("That doesn't look like an email address.")
    if any(c.isspace() for c in email):
        raise SignupError("That doesn't look like an email address.")
    return email


def _forward(email, source):
    """POST to the configured provider.

    Their failures are ours to absorb: a provider being down is not something the visitor
    can act on, so it surfaces as a generic error rather than leaking a status code.
    """
    body = json.dumps({"email": email, "source": source}).encode()
    req = urllib.request.Request(
        config.SIGNUP_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status >= 400:
                raise SignupError("Couldn't save that just now — try again shortly.")
    except urllib.error.HTTPError as exc:
        # 409 is how most list providers spell "already subscribed", which is a success
        # from where the visitor is standing.
        if exc.code in (409, 422):
            return
        raise SignupError("Couldn't save that just now — try again shortly.") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SignupError("Couldn't save that just now — try again shortly.") from exc


def _append(email, source):
    """One JSON object per line, so a partial write costs one address rather than the list.

    A rewritten-and-renamed document like presets.py uses would be the wrong shape here:
    that file is small and edited, this one only ever grows.
    """
    line = json.dumps({"email": email, "source": source, "at": int(time.time())})
    directory = os.path.dirname(config.SIGNUPS_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(config.SIGNUPS_PATH, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def add(raw, source="landing"):
    email = clean_email(raw)
    with _LOCK:
        if config.SIGNUP_URL:
            _forward(email, source)
        else:
            try:
                _append(email, source)
            except OSError as exc:
                # A read-only filesystem with no provider configured means the address is
                # simply lost. Say so rather than showing a thank-you for nothing.
                raise SignupError("Signups aren't set up on this instance.") from exc
    return email
