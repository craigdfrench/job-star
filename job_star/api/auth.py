"""FastAPI auth: supports Tailscale network trust, Basic Auth, Bearer token, and query-param token.

Tailscale network trust: requests from the Tailscale IP range (100.64.0.0/10)
or localhost are trusted as authenticated, since the Tailscale network itself
is the authentication boundary. Tagged machines (which have no user identity)
can still access the API this way. Direct access to port 8003 from outside
the Tailscale network requires the API token.
"""
from __future__ import annotations

import os
import secrets
import ipaddress
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials

from .models import UserIdentity


security_basic = HTTPBasic(auto_error=False)
security_bearer = HTTPBearer(auto_error=False)

# Tailscale CGNAT range — all Tailscale IPs are in 100.64.0.0/10
TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
LOCALHOST_NETS = [ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128")]


def _is_tailscale_or_localhost(client_ip: str) -> bool:
    """Check if the request comes from the Tailscale network or localhost."""
    try:
        ip = ipaddress.ip_address(client_ip)
        if ip in TAILSCALE_NET:
            return True
        for net in LOCALHOST_NETS:
            if ip in net:
                return True
    except (ValueError, ipaddress.AddressValueError):
        pass
    return False


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
) -> UserIdentity:
    """Authenticate via Tailscale network trust, Basic Auth, Bearer token, or ?token= query param.

    Tailscale trust: if the request comes from the Tailscale network (100.64.0.0/10)
    or localhost, it's trusted as authenticated. The Tailscale network is the
    authentication boundary — only machines on the tailnet can reach Caddy, and
    only Caddy can reach the API. Tagged machines (which have no user identity in
    Tailscale) are still trusted because they're on the network.
    """
    # Tailscale network trust
    client_ip = request.client.host if request.client else ""
    if _is_tailscale_or_localhost(client_ip):
        return UserIdentity(user_id=f"tailscale:{client_ip}", role="tailscale")

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