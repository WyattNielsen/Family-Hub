"""HTTP client for public APIs.

The app host often has a broken IPv6 route. Binding the local side to IPv4
avoids a hang of 10 seconds or more before the request fails over.
"""
import httpx


def client(timeout: float = 10.0, headers=None, follow_redirects: bool = True) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=follow_redirects,
        trust_env=False,
        headers=headers,
        transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
    )
