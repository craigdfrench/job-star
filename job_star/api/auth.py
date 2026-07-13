"""FastAPI auth: supports Tailscale identity (via Caddy), Basic Auth, Bearer token, and query-param token."""
from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials

from .models import UserIdentity


security_basic = HTTPBasic(auto_error=False)
security_bearer = HTTPBearer(auto_error=False)


def _get_env_creds():
    return {
        "user": os.environ.get("JOB_STAR_API_USER", "agent"),
        "password": os.environ.get("JOB_STAR_API_PASSWORD", ""),
        "token": os.environ.get("JOB_STAR_API_TOKEN", ""),
    }


async def get_current_user(
    request: Request,
    basic: Optional[HTTPBasicCredentials] = Depends(security_basic),
    bearer: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
    token: Optional[str] = None,
    x_tailscale_user: Optional[str] = Header(None, alias="X-Tailscale-User"),
) -> UserIdentity:
    """Authenticate via Tailscale (Caddy header), Basic Auth, Bearer token, or ?token= query param.

    Tailscale auth: Caddy's tailscale_auth directive authenticates the user and
    forwards their identity as X-Tailscale-User. We trust this header because
    it can only come from Caddy (which is on localhost). Direct access to the
    API port (8003) without going through Caddy won't have this header.
    """
    # Tailscale identity (forwarded by Caddy's tailscale_auth)
    if x_tailscale_user:
        return UserIdentity(user_id=x_tailscale_user, role="tailscale")

    creds = _get_env_creds()

    # Query-param token (for SSE/EventSource which can't set headers)
    if token and creds["token"]:
        if secrets.compare_digest(token, creds["token"]):
            return UserIdentity(user_id="agent")

    if bearer and bearer.credentials:
        if secrets.compare_digest(bearer.credentials, creds["token"]) and creds["token"]:
            return UserIdentity(user_id="agent")

    if basic and basic.username and basic.password:
        user_ok = secrets.compare_digest(basic.username, creds["user"])
        pass_ok = secrets.compare_digest(basic.password, creds["password"])
        if user_ok and pass_ok and creds["password"]:
            return UserIdentity(user_id="agent")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )