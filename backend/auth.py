"""Authentication utilities for IP-based access control with Basic Auth fallback."""
import os
import secrets
from typing import Optional, Set
from ipaddress import ip_address, ip_network
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


# ─── Configuration ────────────────────────────────────────────────────────────

# Private IP ranges (used for SSRF protection and local network detection)
PRIVATE_IP_RANGES = [
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("127.0.0.0/8"),
    ip_network("169.254.0.0/16"),
    ip_network("::1/128"),
    ip_network("fc00::/7"),
    ip_network("fe80::/10"),
]

# Load ALLOWED_IPS from environment (comma-separated)
# These IPs bypass authentication entirely
ALLOWED_IPS: Set[str] = set(
    ip.strip() for ip in os.getenv("SSRF_ALLOWED_IPS", "192.168.31.66").split(",") if ip.strip()
)

# Basic auth credentials (optional - if not set, non-allowed IPs get 403)
BASIC_AUTH_USERNAME = os.getenv("BASIC_AUTH_USERNAME")
BASIC_AUTH_PASSWORD = os.getenv("BASIC_AUTH_PASSWORD")

# Security scheme for Basic Auth
security = HTTPBasic(auto_error=False)


# ─── Helper Functions ─────────────────────────────────────────────────────────

def _parse_client_ip(request: Request) -> str:
    """Extract client IP from request, considering reverse proxy headers."""
    # Check X-Forwarded-For header (first IP is the original client)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # Take the first IP in the chain
        return forwarded_for.split(",")[0].strip()
    
    # Check X-Real-IP header (set by some proxies)
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    
    # Fall back to direct client
    if request.client:
        return request.client.host
    
    return "unknown"


def _is_ip_allowed(client_ip: str) -> bool:
    """Check if client IP is in the allowed list or is a private/local IP."""
    if client_ip in ("unknown", "::1", "127.0.0.1"):
        return True
    
    # Check explicit allowed IPs
    if client_ip in ALLOWED_IPS:
        return True
    
    # Check if it's a private IP (local network access)
    try:
        ip = ip_address(client_ip)
        for private_range in PRIVATE_IP_RANGES:
            if ip in private_range:
                return True
    except ValueError:
        pass
    
    return False


def _verify_credentials(credentials: Optional[HTTPBasicCredentials]) -> bool:
    """Verify basic auth credentials against environment variables."""
    if not BASIC_AUTH_USERNAME or not BASIC_AUTH_PASSWORD:
        # No credentials configured - auth not available
        return False
    
    if not credentials:
        return False
    
    # Use constant-time comparison to prevent timing attacks
    username_ok = secrets.compare_digest(credentials.username, BASIC_AUTH_USERNAME)
    password_ok = secrets.compare_digest(credentials.password, BASIC_AUTH_PASSWORD)
    
    return username_ok and password_ok


# ─── FastAPI Dependencies ─────────────────────────────────────────────────────

async def require_auth(
    request: Request,
    credentials: Optional[HTTPBasicCredentials] = Depends(security)
) -> None:
    """
    Dependency that enforces authentication.
    
    - Allows requests from ALLOWED_IPs without auth
    - Requires valid Basic Auth for other IPs (if credentials configured)
    - Returns 401 if auth required but not provided/invalid
    - Returns 403 if auth required but not configured
    """
    client_ip = _parse_client_ip(request)
    
    # Allow if IP is in allowed list or private range
    if _is_ip_allowed(client_ip):
        return
    
    # IP not allowed - require authentication
    if not _verify_credentials(credentials):
        # No credentials or invalid credentials
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic realm=\"LLM Council\""},
        )


# ─── Optional: Middleware for Global Protection ───────────────────────────────

class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce authentication on all routes."""
    
    def __init__(self, app, excluded_paths: Optional[list[str]] = None):
        super().__init__(app)
        self.excluded_paths = excluded_paths or ["/", "/health", "/docs", "/redoc", "/openapi.json"]
    
    async def dispatch(self, request: Request, call_next):
        # Skip auth for excluded paths
        if request.url.path in self.excluded_paths:
            return await call_next(request)
        
        # Skip auth for static files if any
        if request.url.path.startswith("/static/"):
            return await call_next(request)
        
        client_ip = _parse_client_ip(request)
        
        # Allow if IP is allowed
        if _is_ip_allowed(client_ip):
            return await call_next(request)
        
        # Check basic auth
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Basic "):
            return Response(
                content="Authentication required",
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Basic realm=\"LLM Council\""},
            )
        
        # Decode and verify credentials
        import base64
        try:
            encoded = auth_header[6:]  # Remove "Basic "
            decoded = base64.b64decode(encoded).decode("utf-8")
            username, password = decoded.split(":", 1)
            
            if not _verify_credentials(
                HTTPBasicCredentials(username=username, password=password)
            ):
                return Response(
                    content="Invalid credentials",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    headers={"WWW-Authenticate": "Basic realm=\"LLM Council\""},
                )
        except Exception:
            return Response(
                content="Invalid authentication format",
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={"WWW-Authenticate": "Basic realm=\"LLM Council\""},
            )
        
        return await call_next(request)


# ─── Export for backward compatibility ──────────────────────��─────────────────

def get_allowed_ips() -> Set[str]:
    """Get the set of allowed IPs (for use in other modules)."""
    return ALLOWED_IPS