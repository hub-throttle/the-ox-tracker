r"""The only module in The Ox Tracker that is allowed to make network requests.

Every outbound request goes through here and is checked twice: against a
hardcoded host allowlist, and against the calling service's own hosts. Any
other host raises DisallowedHost before a socket opens.

The Ox Tracker is read-only: this module exposes GET only. It never sends a
request body, never follows a redirect to another host, and never logs
headers, tokens or response bodies.

One token, one company
----------------------
get() must be told which service is calling, and the URL's host has to be one
of THAT service's hosts in registry.py. The shared allowlist alone is not
enough: it contains every service's hosts, so with only that check a changed
URL constant in providers/claude.py could send an Anthropic token to
chatgpt.com and the request would still go through. The service argument is
required, so a call site cannot forget it.

The environment is ignored on purpose
-------------------------------------
One module-level Session with trust_env = False, and an explicit empty proxy
mapping on every call. Without that, anything that can set an environment
variable for this process could quietly change where a Bearer token goes or
what is trusted to sign the connection:

  HTTPS_PROXY, HTTP_PROXY, ALL_PROXY  would route the request, token and all,
                                      through a machine of their choosing
  REQUESTS_CA_BUNDLE, CURL_CA_BUNDLE  would replace the trusted certificates,
                                      making that interception invisible
  NO_PROXY                            would change which hosts bypass a proxy
  ~/.netrc                            would attach credentials of its own,
                                      or replace our Authorization header

trust_env = False turns all of that off. The explicit proxies argument is
belt and braces: it survives anyone later handing this function a session
that was built differently.

The server does not get to decide how much we read
--------------------------------------------------
A reply is streamed and capped, and the whole exchange has a wall-clock
deadline. requests' own timeout only limits the gap BETWEEN bytes, so a
server that drips one byte every ten seconds never times out and holds a
worker thread for as long as it likes. Both limits are needed.

The deadline is enforced from outside the request, by a timer. Checking the
clock between chunks is not enough on its own: reading one 16 KB chunk
blocks until 16 KB have arrived, so a drip never gets back to the check.
When time is up the timer closes the connection's socket, which is the one
thing that wakes a read blocked in another thread on Windows (shutdown()
does not; measured). It covers sending the request, waiting for the headers
and reading the body. Connecting and the TLS handshake are each bounded by
the connect timeout, which is at most half the deadline, so together they
cannot outlast it either. Only the DNS lookup is left to the system.

Requests are made one at a time. Closing a socket from another thread is
only safe while nothing else in the process can be handed the same socket
handle, and one exchange at a time guarantees that.
"""
from __future__ import annotations

import _socket
import http.cookiejar
import threading
import time
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

# Hardcoded. Grok Bot's api2.cursor.sh was removed because The Ox Tracker no
# longer talks to it: Grok Bot usage is checked in its own desktop app.
# grok.com was removed too: Grok's usage comes from cli-chat-proxy.grok.com
# and nothing else, so allowing the parent domain widened the surface for no
# reason. tests/test_registry.py asserts this list matches what the services
# in registry.py actually need, in both directions.
ALLOWED_HOSTS = frozenset(
    {
        "api.anthropic.com",        # Claude usage
        "chatgpt.com",              # ChatGPT (Codex) usage
        "cli-chat-proxy.grok.com",  # Grok billing
    }
)

# The gap between bytes, which is all requests' own timeout controls.
DEFAULT_TIMEOUT = 15.0

# The whole exchange, connection to last byte. A slow-drip reply hits this.
DEADLINE_SECONDS = 30.0

# A usage reply is a few kilobytes. A megabyte is generous by a hundredfold
# and still small enough that a hostile or broken server cannot fill memory.
MAX_RESPONSE_BYTES = 1024 * 1024
CHUNK_BYTES = 16 * 1024

# No proxy, for either scheme. Passed on every call, not just configured once.
NO_PROXIES = {"http": None, "https": None}


class _RefuseCookies(http.cookiejar.CookiePolicy):
    """A cookie policy that says no to everything, in both directions.

    Nothing this app reads needs a cookie, and a stored one would be a piece
    of server-controlled state living in the process and travelling with
    later requests. Refusing to set them is better than clearing them after
    the fact, because there is then no window where one exists.
    """

    netscape = True
    rfc2965 = False
    hide_cookie2 = True

    def set_ok(self, cookie, request) -> bool:
        return False

    def return_ok(self, cookie, request) -> bool:
        return False

    def domain_return_ok(self, domain, request) -> bool:
        return False

    def path_return_ok(self, path, request) -> bool:
        return False


# ---------------------------------------------------------------------------
# The deadline, enforced from outside the request.
# ---------------------------------------------------------------------------

# One exchange at a time; see "Requests are made one at a time" above.
_EXCHANGE_LOCK = threading.Lock()

# The watch for the exchange in progress on this thread, so the connection
# can hand it its socket the moment one exists.
_ACTIVE = threading.local()


def _cut(sock) -> None:
    """Close a socket at once, even while another thread is reading it.

    socket.close() waits until every file made from the socket is closed,
    and http.client always has one open, so it would do nothing here. The
    C-level close is immediate, and a read blocked on the socket returns
    with an error straight away.
    """
    try:
        _socket.socket.close(sock)
    except (OSError, TypeError):
        pass


class _Watch:
    """One exchange's deadline. When it passes, the connection is cut."""

    def __init__(self, seconds: float) -> None:
        self.expired = False
        self._done = False
        self._lock = threading.Lock()
        self._sockets: list = []
        self._timer = threading.Timer(max(0.0, seconds), self._expire)
        self._timer.daemon = True

    def start(self) -> None:
        self._timer.start()

    def adopt(self, sock) -> None:
        """A socket this exchange is now using. Cut at once if time is up."""
        if sock is None:
            return
        with self._lock:
            if self._done:
                return
            if self.expired:
                _cut(sock)
                return
            self._sockets.append(sock)

    def _expire(self) -> None:
        with self._lock:
            if self._done:
                return
            self.expired = True
            for sock in self._sockets:
                _cut(sock)

    def finish(self) -> None:
        with self._lock:
            self._done = True
        self._timer.cancel()


def _adopt_socket(sock) -> None:
    watch = getattr(_ACTIVE, "watch", None)
    if watch is not None:
        watch.adopt(sock)


class _WatchedHTTPConnection(HTTPConnection):
    """urllib3's connection, reporting its socket to the deadline."""

    def connect(self) -> None:
        super().connect()
        _adopt_socket(self.sock)


class _WatchedHTTPSConnection(HTTPSConnection):
    """The same for HTTPS. The TLS handshake and every certificate check are
    urllib3's own, untouched: this only learns the socket once they pass."""

    def connect(self) -> None:
        super().connect()
        _adopt_socket(self.sock)


class _WatchedHTTPPool(HTTPConnectionPool):
    ConnectionCls = _WatchedHTTPConnection


class _WatchedHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _WatchedHTTPSConnection


class _WatchedAdapter(HTTPAdapter):
    """requests' own adapter, with pools that use the connections above."""

    def init_poolmanager(self, *args, **kwargs) -> None:
        super().init_poolmanager(*args, **kwargs)
        # A copy for this pool manager only; urllib3's module-level mapping
        # is left alone.
        self.poolmanager.pool_classes_by_scheme = {
            "http": _WatchedHTTPPool,
            "https": _WatchedHTTPSPool,
        }


def _build_session() -> requests.Session:
    session = requests.Session()
    # The single most important line in this module.
    session.trust_env = False
    session.proxies = {}
    session.cookies.set_policy(_RefuseCookies())
    session.max_redirects = 0
    session.mount("https://", _WatchedAdapter())
    session.mount("http://", _WatchedAdapter())
    return session


_SESSION = _build_session()


class DisallowedHost(RuntimeError):
    """Raised when a URL points somewhere outside the allowlist."""


class ResponseTooLarge(RuntimeError):
    """The server sent more than MAX_RESPONSE_BYTES."""


class DeadlineExceeded(RuntimeError):
    """The whole exchange took longer than DEADLINE_SECONDS."""


def session() -> requests.Session:
    """The one session every request uses. Exposed so tests can inspect it."""
    return _SESSION


def hosts_for(service: str) -> frozenset[str]:
    """The hosts one service is allowed to reach, from registry.py.

    Imported inside the function: registry imports the providers and the
    providers import this module, so a module-level import would be circular.
    """
    from . import registry

    spec = registry.spec_for(service)
    if spec is None:
        raise DisallowedHost(f"Unknown service: {service!r}")
    return frozenset(spec.hosts)


def check_url(url: str, service: str) -> str:
    """Return the host if this service may reach it, else raise.

    Two gates, deliberately. The allowlist says the host is one The Ox
    Tracker talks to at all; the service's own host list says this caller's
    token belongs there.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise DisallowedHost(f"Only https is allowed, got {parts.scheme!r}")
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise DisallowedHost(f"Host not in allowlist: {host!r}")
    allowed = hosts_for(service)
    if host not in allowed:
        raise DisallowedHost(
            f"{service} may not send its login to {host!r}; "
            f"it is only allowed {sorted(allowed) or 'no hosts at all'}"
        )
    return host


def _read_capped(response, watch: _Watch, ends_at: float, budget: float) -> bytes:
    """Read the body under both the size cap and the wall-clock deadline.

    iter_content decompresses as it goes, so the cap is on what the body
    actually expands to, not on what arrived over the wire. That is the
    number that matters: a small compressed reply can expand to gigabytes.

    The clock is checked between chunks as well as by the timer, so a reply
    that trickles in whole small chunks stops at the deadline too.
    """
    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            if int(declared) > MAX_RESPONSE_BYTES:
                raise ResponseTooLarge(
                    f"declared {int(declared)} bytes, limit {MAX_RESPONSE_BYTES}")
        except ValueError:
            pass                    # an unparseable header just means we stream

    total = 0
    chunks: list[bytes] = []
    for chunk in response.iter_content(chunk_size=CHUNK_BYTES):
        if watch.expired or time.monotonic() > ends_at:
            raise DeadlineExceeded(
                f"reply not finished within {budget:g}s")
        if not chunk:
            continue
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ResponseTooLarge(
                f"over {MAX_RESPONSE_BYTES} bytes, stopped reading")
        chunks.append(chunk)
    return b"".join(chunks)


def get(url: str, headers: dict[str, str], service: str,
        timeout: float = DEFAULT_TIMEOUT,
        deadline: float = DEADLINE_SECONDS):
    """GET an allowlisted URL on behalf of one named service.

    Redirects are not followed, so a 302 to an unlisted host cannot leak a
    token. The caller sees the 3xx response and decides what to do.

    The body is streamed, capped and read under a deadline, then attached to
    the response so callers use .json() exactly as before.
    """
    check_url(url, service)
    return _exchange(url, headers, timeout, deadline)


def _exchange(url: str, headers: dict[str, str], timeout: float, deadline: float):
    """The request itself, once the URL has been checked.

    Kept apart from get() so a test can point it at a local server; nothing
    in the app calls it except get().
    """
    budget = float(deadline)
    # Connecting and the TLS handshake are each bounded by this; at half the
    # deadline, the two together cannot outlast it. See the module note.
    connect_timeout = max(0.1, min(float(timeout), budget / 2))
    with _EXCHANGE_LOCK:
        watch = _Watch(budget)
        ends_at = time.monotonic() + budget
        _ACTIVE.watch = watch
        watch.start()
        try:
            try:
                response = _SESSION.get(
                    url,
                    headers=headers,
                    timeout=(connect_timeout, float(timeout)),
                    allow_redirects=False,
                    proxies=NO_PROXIES,
                    stream=True,
                )
            except Exception:
                if watch.expired:
                    raise DeadlineExceeded(
                        f"no reply within {budget:g}s") from None
                raise
            try:
                body = _read_capped(response, watch, ends_at, budget)
            except (ResponseTooLarge, DeadlineExceeded):
                raise
            except Exception:
                if watch.expired:
                    raise DeadlineExceeded(
                        f"reply not finished within {budget:g}s") from None
                raise
            finally:
                response.close()
                # Nothing should ever land here, but a jar that a future
                # change lets through must not survive into the next request.
                _SESSION.cookies.clear()
        finally:
            watch.finish()
            _ACTIVE.watch = None
    # Hand the capped bytes back as if they had been read normally, so
    # response.json() and response.text work for every caller.
    response._content = body
    response._content_consumed = True
    return response


def forget_request_headers(response) -> None:
    """Drop the outgoing headers requests keeps on the response object.

    response.request.headers holds a copy of what was sent, Authorization
    included. The caller's own dict is deleted as soon as the call returns,
    but this copy would otherwise live as long as the response does, which is
    long enough to end up in a traceback or a debugger.
    """
    request = getattr(response, "request", None)
    headers = getattr(request, "headers", None)
    if headers is None:
        return
    try:
        headers.clear()
    except Exception:       # noqa: BLE001 - never fail while tidying up
        pass


def safe_error(exc: BaseException) -> str:
    """A description of a failure that cannot contain a token.

    requests exceptions can embed the full request URL and occasionally
    header material, so only the exception type is ever surfaced.
    """
    return type(exc).__name__
