"""Shared HTTP client with connection pooling and lifespan management."""
import asyncio
import ipaddress
import socket
import threading
from typing import Optional
from urllib.parse import urlparse

import httpx

# Private IP ranges to block for SSRF protection
PRIVATE_IP_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_private_ip(ip_str: str) -> bool:
    """Check if an IP address is in a private/internal range."""
    try:
        ip = ipaddress.ip_address(ip_str)
        for private_range in PRIVATE_IP_RANGES:
            if ip in private_range:
                return True
    except ValueError:
        pass
    return False


class SSRFProtectionTransport(httpx.AsyncHTTPTransport):
    """Custom transport that validates IP addresses at connection time to prevent SSRF/DNS rebinding."""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    async def _start_tls(self, host: str, stream: httpx.AsyncByteStream, ssl_context):
        """Override to validate IP before TLS handshake."""
        # Resolve the host to IP
        try:
            ip_str = await self._resolve_host(host)
            if _is_private_ip(ip_str):
                raise httpx.ConnectError(f"Connection to private IP address {ip_str} is not allowed")
        except Exception as e:
            raise httpx.ConnectError(f"SSRF validation failed for {host}: {e}")
        return await super()._start_tls(host, stream, ssl_context)
    
    async def _connect(self, host: str, port: int, timeout: httpx.Timeout, stream_factory):
        """Override to validate IP at connection time."""
        try:
            ip_str = await self._resolve_host(host)
            if _is_private_ip(ip_str):
                raise httpx.ConnectError(f"Connection to private IP address {ip_str} is not allowed")
        except Exception as e:
            raise httpx.ConnectError(f"SSRF validation failed for {host}: {e}")
        return await super()._connect(host, port, timeout, stream_factory)
    
    async def _resolve_host(self, host: str) -> str:
        """Resolve hostname to IP address asynchronously."""
        if host in ("localhost", "127.0.0.1", "::1"):
            return host
        try:
            # Use asyncio's getaddrinfo for non-blocking DNS resolution
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(host, None)
            for info in infos:
                ip_str = info[4][0]
                return ip_str
            raise ValueError(f"No IP address found for {host}")
        except socket.gaierror:
            # If we can't resolve, we should block for safety
            raise httpx.ConnectError(f"Could not resolve host: {host}")


_shared_client: Optional[httpx.AsyncClient] = None
_client_lock = threading.Lock()


def get_shared_client() -> Optional[httpx.AsyncClient]:
    """Get the shared HTTP client instance."""
    return _shared_client


def create_shared_client(
    timeout: float = 120.0,
    max_keepalive_connections: int = 20,
    max_connections: int = 100,
    proxy: Optional[str] = None,
) -> httpx.AsyncClient:
    """Create and set the shared HTTP client. Thread-safe."""
    global _shared_client
    with _client_lock:
        if _shared_client is not None:
            return _shared_client
        _shared_client = httpx.AsyncClient(
            timeout=timeout,
            proxy=proxy,
            limits=httpx.Limits(
                max_keepalive_connections=max_keepalive_connections,
                max_connections=max_connections,
            ),
            trust_env=False,
            transport=SSRFProtectionTransport(),
        )
        return _shared_client


async def close_shared_client() -> None:
    """Close the shared HTTP client. Thread-safe."""
    global _shared_client
    with _client_lock:
        if _shared_client is not None:
            await _shared_client.aclose()
            _shared_client = None