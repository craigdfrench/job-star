"""Context sources (merged from v1 unique file).

Each source is a small callable that returns a dict of context data.
Keeping sources pluggable lets the gatherer compose context without
hard-coding environment reads.
"""

from __future__ import annotations

import os
from typing import Any, Dict


def env_source() -> Dict[str, Any]:
    """Capture relevant environment variables."""
    keys = ("JOBSTAR_ENV", "JOBSTAR_REGION", "JOBSTAR_TENANT", "PATH")
    return {k: os.environ.get(k, "") for k in keys}


def host_source() -> Dict[str, Any]:
    """Capture host identity info."""
    import socket

    return {"hostname": socket.gethostname()}


def runtime_source() -> Dict[str, Any]:
    """Capture runtime metadata."""
    import sys

    return {"python_version": sys.version.split()[0]}


SOURCES = {
    "env": env_source,
    "host": host_source,
    "runtime": runtime_source,
}
