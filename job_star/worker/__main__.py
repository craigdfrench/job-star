"""Entry point: python -m job_star.worker"""
from __future__ import annotations

import asyncio

from . import main

asyncio.run(main())
