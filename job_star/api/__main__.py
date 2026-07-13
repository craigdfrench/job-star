"""Entry point: python -m job_star.api"""
from __future__ import annotations

import os

import uvicorn

host = os.environ.get("JOB_STAR_API_HOST", "127.0.0.1")
# 8000 is aperture-proxy (ai.craigdfrench.com); 8002 is ai-gateway-dev. Use 8003.
port = int(os.environ.get("JOB_STAR_API_PORT", "8003"))

uvicorn.run("job_star.api.app:app", host=host, port=port, reload=False)
