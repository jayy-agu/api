"""
Central rate limiter, in its own module so main.py (which registers the
limiter + the 429 handler) and the routers (which attach per-endpoint
limits) can import it without a circular import.

Per-IP limits and the per-account lockout are complementary, not redundant:
the lockout punishes attacks against ONE account, while rate limiting
throttles the SOURCE, which also stops password spraying (one attempt
across many accounts — no single lockout ever trips) and MFA code
brute-force amplification.
"""
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from jose import JWTError

from security import decode_access_token


def user_or_ip_key(request: Request) -> str:
    """Rate-limit authenticated actions per USER, falling back to per-IP
    when the caller can't be identified — so one account can't get another
    user throttled just by sharing an office IP."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            payload = decode_access_token(auth[7:].strip())
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except JWTError:
            pass
    return f"ip:{get_remote_address(request)}"


# default_limits=[] -> limits are opt-in per endpoint via @limiter.limit(...).
limiter = Limiter(key_func=get_remote_address, default_limits=[])