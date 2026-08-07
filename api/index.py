"""WSGI entrypoint for serverless hosts.

app.py stays the local entrypoint — its argparse lives under __main__, so importing it
here starts no server and touches no disk.
"""

import os
import sys

# Must be set before anything imports matplotlib. On first import matplotlib builds a font
# cache under $HOME, which is read-only on a serverless host — the resulting failure looks
# like a generic function crash with nothing useful in the response.
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
os.makedirs(os.environ["MPLCONFIGDIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402,F401
