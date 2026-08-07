"""WSGI entrypoint for serverless hosts.

app.py stays the local entrypoint — its argparse lives under __main__, so importing it
here starts no server and touches no disk.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402,F401
