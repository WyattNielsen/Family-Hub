"""HTTP client for public APIs.

One process-wide client keeps TLS connections warm between polls. `client()`
is still an async context manager so callers stay the same, but leaving the
block does not close the shared client.

The app host often has a broken IPv6 route. Binding the local side to IPv4
avoids a hang of 10 seconds or more before the request fails over.
"""
import httpx

_shared: httpx.AsyncClient | None = None

_HTTP_METHODS = {
    "get", "post", "put", "patch", "delete", "head", "options", "request", "stream",
}


def _build() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=10.0,
        follow_redirects=True,
        trust_env=False,
        transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0"),
    )


def shared() -> httpx.AsyncClient:
    global _shared
    if _shared is None or _shared.is_closed:
        _shared = _build()
    return _shared


class _BoundClient:
    """Shared client with this call's timeout and default headers applied."""

    def __init__(self, client: httpx.AsyncClient, timeout, headers):
        self._client = client
        self._timeout = timeout
        self._headers = headers

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if name not in _HTTP_METHODS:
            return attr

        def call(*args, **kwargs):
            if "timeout" not in kwargs:
                kwargs["timeout"] = self._timeout
            if self._headers:
                merged = dict(self._headers)
                extra = kwargs.get("headers")
                if extra:
                    merged.update(extra)
                kwargs["headers"] = merged
            return attr(*args, **kwargs)

        return call


class _Borrow:
    def __init__(self, timeout, headers):
        self._timeout = timeout
        self._headers = headers

    async def __aenter__(self):
        return _BoundClient(shared(), self._timeout, self._headers)

    async def __aexit__(self, exc_type, exc, tb):
        return False


def client(timeout: float | None = 10.0, headers=None, follow_redirects: bool = True) -> _Borrow:
    # follow_redirects stays on the shared client (always on). The argument
    # remains so existing call sites keep working.
    del follow_redirects
    return _Borrow(timeout, headers)


async def aclose():
    global _shared
    if _shared is not None and not _shared.is_closed:
        await _shared.aclose()
    _shared = None
