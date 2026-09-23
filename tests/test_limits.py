r"""What a server is allowed to make this app do.

Every number in a usage reply comes from a machine The Ox Tracker does not
control. None of it is hostile today. The point of these limits is that none
of it has to be trusted either:

  a reply is streamed and capped, so it cannot fill memory
  a reply has a wall-clock deadline, so a slow drip cannot hold a worker,
    tested against a real local server sending one byte at a time
  a service gets at most six buckets, however many it sends
  a label is cut to 24 characters, however long it arrives
  a window is never bigger than the screen it is on
  a date that is out of range, or outside 2000 to 2200, is "no date", and
    a reading that carries one anyway still draws
  a usage figure that is NaN, infinite or too large to be a number is an
    error reading, never 100%, and so is JSON nested too deep to read

Every value here is fabricated.
"""
from __future__ import annotations

import json
import pathlib
import socket
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

from PySide6 import QtWidgets

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from the_ox import logs, net, overlay, settings                  # noqa: E402
from the_ox import biscuit as biscuit_mod                        # noqa: E402
from the_ox import panel as panel_mod                            # noqa: E402
from the_ox.providers import chatgpt, claude, common, grok       # noqa: E402
from the_ox.providers.common import OK, Bucket, Reading          # noqa: E402

TEMP = pathlib.Path(tempfile.mkdtemp(prefix="theox-test-"))
settings.set_path_override(TEMP / "settings.json")
logs.set_path_override(TEMP / "the-ox.log")

fails: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


class StreamedResponse:
    """A response whose body arrives in chunks, optionally very slowly."""

    def __init__(self, body: bytes, chunk: int = 4096, delay: float = 0.0,
                 declared: int | None = None):
        self.status_code = 200
        self._body = body
        self._chunk = chunk
        self._delay = delay
        self.headers = {} if declared is None else {"Content-Length": str(declared)}
        self.closed = False
        self.request = type("Req", (), {"headers": {}})()
        self.chunks_read = 0

    def iter_content(self, chunk_size=1):
        for index in range(0, len(self._body), self._chunk):
            if self._delay:
                time.sleep(self._delay)
            self.chunks_read += 1
            yield self._body[index:index + self._chunk]

    def close(self):
        self.closed = True

    def json(self):
        # net.get attaches the capped bytes as _content, exactly as requests
        # would have. Decoding that here proves the body really did come
        # through the cap rather than around it.
        import json as _json
        return _json.loads(self._content.decode("utf-8"))


def with_transport(response):
    """Point the shared session at a canned response for one call."""
    original = net.session().get
    net.session().get = lambda url, **kwargs: response
    return original


def restore_transport(original):
    net.session().get = original


URL = "https://api.anthropic.com/api/oauth/usage"


def network_limits() -> None:
    print("A reply that is too large is refused")
    # A megabyte cap on a reply that is normally a few kilobytes.
    huge = b'{"limits": [' + b'{"kind": "session", "percent": 1},' * 200000 + b']}'
    check("the sample really is oversized",
          len(huge) > net.MAX_RESPONSE_BYTES,
          f"{len(huge) // 1024} KB vs {net.MAX_RESPONSE_BYTES // 1024} KB cap")
    response = StreamedResponse(huge)
    original = with_transport(response)
    try:
        net.get(URL, headers={}, service="Claude")
        check("it was refused", False, "no error was raised")
    except net.ResponseTooLarge as exc:
        check("it was refused", True, str(exc))
    except Exception as exc:                # noqa: BLE001
        check("it was refused", False, f"{type(exc).__name__}: {exc}")
    finally:
        restore_transport(original)
    check("and the connection was closed", response.closed)
    check("and it stopped early rather than reading it all",
          response.chunks_read * 4096 <= net.MAX_RESPONSE_BYTES + 8192,
          f"read about {response.chunks_read * 4} KB")

    print("\n  a declared Content-Length over the cap is refused up front")
    lying = StreamedResponse(b"{}", declared=net.MAX_RESPONSE_BYTES * 50)
    original = with_transport(lying)
    try:
        net.get(URL, headers={}, service="Claude")
        check("refused before reading a byte", False)
    except net.ResponseTooLarge:
        check("refused before reading a byte", True)
    finally:
        restore_transport(original)
    check("and nothing was read", lying.chunks_read == 0, str(lying.chunks_read))

    print("\n  a body just under the cap still works")
    fine = StreamedResponse(b'{"limits": []}')
    original = with_transport(fine)
    try:
        response = net.get(URL, headers={}, service="Claude")
        check("a normal reply comes back", response.json() == {"limits": []})
    finally:
        restore_transport(original)

    print("\nA reply that drips slowly hits the deadline")
    # requests' own timeout only limits the gap BETWEEN bytes. A server that
    # sends one chunk every 50ms never trips it and can hold a worker for as
    # long as it likes. The wall-clock deadline is what stops that.
    drip = StreamedResponse(b"x" * 4000, chunk=100, delay=0.05)
    original = with_transport(drip)
    started = time.monotonic()
    try:
        net.get(URL, headers={}, service="Claude", deadline=0.25)
        check("the slow drip was cut off", False, "it was allowed to finish")
    except net.DeadlineExceeded as exc:
        check("the slow drip was cut off", True, str(exc))
    except Exception as exc:                # noqa: BLE001
        check("the slow drip was cut off", False, f"{type(exc).__name__}: {exc}")
    finally:
        restore_transport(original)
    elapsed = time.monotonic() - started
    check("and it gave up quickly", elapsed < 2.0, f"{elapsed:.2f}s")
    check("without reading the whole body",
          drip.chunks_read < 40, f"{drip.chunks_read} of 40 chunks")
    check("and the connection was closed", drip.closed)

    print("\n  the same body inside the deadline is fine")
    quick = StreamedResponse(b'{"ok": true}', chunk=4, delay=0.0)
    original = with_transport(quick)
    try:
        check("it comes back", net.get(URL, headers={}, service="Claude")
              .json() == {"ok": True})
    finally:
        restore_transport(original)


class DripServer:
    """A local server that answers one byte at a time, or not at all.

      headers  the status line at once, then the headers a byte at a time
      body     the headers at once, then a 5000 byte body a byte at a time
      tls      nothing readable: the start of a TLS record claiming 16 KB,
               then a byte at a time, so a handshake stays half done
      quick    a small JSON reply, at once
    """

    def __init__(self, mode: str, gap: float = 0.2) -> None:
        self.mode, self.gap = mode, gap
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        try:
            conn, _ = self.listener.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(1.0)
            if self.mode != "tls":
                try:
                    conn.recv(65536)            # the request
                except OSError:
                    pass
            if self.mode == "quick":
                body = b'{"ok": true}'
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                             b"Content-Length: " + str(len(body)).encode()
                             + b"\r\n\r\n" + body)
                return
            if self.mode == "headers":
                first, trickle = b"HTTP/1.1 200 OK\r\n", b"X-Slow: 1\r\n" * 200
            elif self.mode == "body":
                first = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                         b"Content-Length: 5000\r\n\r\n")
                trickle = b" " * 5000
            else:
                first, trickle = b"", b"\x16\x03\x03\x40\x00" + b"\x00" * 2000
            try:
                if first:
                    conn.sendall(first)
                for byte in trickle:
                    if self.stop.wait(self.gap):
                        return
                    conn.sendall(bytes([byte]))
            except OSError:
                return

    def close(self) -> None:
        self.stop.set()
        try:
            self.listener.close()
        except OSError:
            pass


def real_socket_limits() -> None:
    print("\nA real server that drips one byte at a time is cut off at the deadline")
    # The fake transport above yields small chunks by itself, which is not
    # what a real connection does: reading a 16 KB chunk from a real socket
    # blocks until 16 KB have arrived, so a clock checked between chunks
    # never gets to run. These go through requests and urllib3 for real,
    # against a server on this PC. net._exchange is net.get without the host
    # check, which would rightly refuse 127.0.0.1.
    for mode, scheme, what, want in (
            ("headers", "http", "while the headers trickle in", net.DeadlineExceeded),
            ("body", "http", "while the body trickles in", net.DeadlineExceeded),
            ("tls", "https", "during a TLS handshake that never finishes", Exception)):
        server = DripServer(mode)
        started = time.monotonic()
        try:
            net._exchange(f"{scheme}://127.0.0.1:{server.port}/", {},
                          timeout=5.0, deadline=1.0)
            check(f"{what}: cut off", False, "the request finished")
        except want as exc:
            elapsed = time.monotonic() - started
            check(f"{what}: cut off within about the deadline",
                  elapsed < 2.5, f"{elapsed:.2f}s, {type(exc).__name__}")
        except Exception as exc:            # noqa: BLE001
            check(f"{what}: cut off the right way", False,
                  f"{type(exc).__name__}: {exc}")
        finally:
            server.close()

    print("\n  a server that answers at once is fine")
    server = DripServer("quick")
    try:
        reply = net._exchange(f"http://127.0.0.1:{server.port}/", {},
                              timeout=5.0, deadline=5.0)
        check("its reply comes back", reply.json() == {"ok": True})
    except Exception as exc:                # noqa: BLE001
        check("its reply comes back", False, f"{type(exc).__name__}: {exc}")
    finally:
        server.close()
    free = net._EXCHANGE_LOCK.acquire(timeout=1)
    if free:
        net._EXCHANGE_LOCK.release()
    check("and nothing is left holding the one-request-at-a-time lock", free)


def date_ranges() -> None:
    print("\nA date outside 2000 to 2200 is no date")
    for label, value in (("1969", "1969-12-31T00:00:00Z"),
                         ("1999", "1999-12-31T23:59:59Z"),
                         ("year 1", "0001-01-01T00:00:00Z"),
                         ("2201", "2201-01-01T00:00:00Z"),
                         ("year 3500", "3500-01-01T00:00:00Z"),
                         ("unix 0", 0),
                         ("unix 10**10 (year 2286)", 10 ** 10)):
        result = common.parse_time(value)
        check(f"{label:<24} -> no date", result is None, str(result))
    for label, value in (("2000", "2000-01-01T00:00:00Z"),
                         ("2200", "2200-12-31T23:00:00Z"),
                         ("2026", "2026-09-27T12:00:00Z")):
        check(f"{label} still parses", common.parse_time(value) is not None)

    print("\n  and a reading that carries one anyway still draws")
    # Windows cannot put a moment before 1970 or after 3000 into local time,
    # and astimezone() raised OSError. A bad reset time used to stop every
    # surface updating. The dates here bypass parse_time on purpose.
    from the_ox import strip as strip_mod               # noqa: PLC0415
    early = datetime(1969, 12, 31, tzinfo=timezone.utc)
    late = datetime(3500, 1, 1, tzinfo=timezone.utc)
    reading = Reading("Claude", OK, [
        Bucket("5 hour", 10.0, early),
        Bucket("Weekly", 20.0, late, window_start=late - timedelta(days=7),
               budgeted=True)])
    try:
        view = strip_mod.build_view(reading, {"budget_warning": True})
        check("build_view does not raise", True)
        check("and still shows the numbers",
              [pct for pct, _colour in view.lines] == [10.0, 20.0],
              str(view.lines))
    except Exception as exc:                # noqa: BLE001 - the whole point
        check(f"build_view raised {type(exc).__name__}", False, str(exc))
    corner = panel_mod.Panel(settings.defaults())
    try:
        corner.set_readings([reading])
        rows = corner._rows_for("Claude")
        check("the panel's rows build too", len(rows) == 2, str(len(rows)))
        corner.grab()
        check("and it paints", True)
    except Exception as exc:                # noqa: BLE001
        check(f"the panel raised {type(exc).__name__}", False, str(exc))


class ProviderResponse:
    """What net.get hands a provider: a status and a json() method."""

    def __init__(self, payload=None, raises: BaseException | None = None) -> None:
        self.status_code = 200
        self._payload = payload
        self._raises = raises
        self.request = type("Req", (), {"headers": {}})()

    def json(self):
        if self._raises is not None:
            raise self._raises
        return self._payload


def fetch_with(provider, payload=None, raises=None):
    """One provider fetch, with a fabricated login and a canned reply."""
    folder = pathlib.Path(tempfile.mkdtemp(prefix="theox-numbers-"))
    if provider is claude:
        login = folder / ".credentials.json"
        login.write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "fake-AAAAAAAAAAAAAAAAAAAA",
            "expiresAt": 1790000000000}}), encoding="utf-8")
        attr = "credentials_path"
    elif provider is chatgpt:
        login = folder / "auth.json"
        login.write_text(json.dumps({"tokens": {
            "access_token": "fake-AAAAAAAAAAAAAAAAAAAA"}}), encoding="utf-8")
        attr = "credentials_path"
    else:
        login = folder / "auth.json"
        login.write_text(json.dumps({"https://auth.x.ai::1": {
            "key": "fake-AAAAAAAAAAAAAAAAAAAA", "oidc_issuer": "https://auth.x.ai",
            "expires_at": "2030-01-01T00:00:00Z"}}), encoding="utf-8")
        attr = "auth_path"
    original_path, original_get = getattr(provider, attr), net.get
    setattr(provider, attr, lambda: login)
    net.get = lambda *a, **k: ProviderResponse(payload, raises)
    try:
        return provider.fetch()
    finally:
        setattr(provider, attr, original_path)
        net.get = original_get


def number_limits() -> None:
    print("\nA usage figure that is not a number is refused, never 100%")
    for label, value in (("NaN", float("nan")), ("infinity", float("inf")),
                         ("minus infinity", float("-inf")), ("10**400", 10 ** 400)):
        try:
            result = common.to_percent(value)
            check(f"{label}: refused", False, f"became {result}")
        except common.UnusableNumber:
            check(f"{label}: refused", True)
    check("a finite huge number is still simply 100",
          common.to_percent(1e300) == 100.0)
    check("text is no number, shown as '?'", common.to_percent("lots") is None)
    check("true and false are no number either", common.to_percent(True) is None)

    print("\n  and a reply carrying one is an error reading")
    for provider, payload in (
            (claude, {"limits": [{"kind": "session", "percent": float("nan")}]}),
            (chatgpt, {"rate_limit": {"primary_window": {"used_percent": 10 ** 400}}}),
            (grok, {"config": {"creditUsagePercent": float("inf")}})):
        reading = fetch_with(provider, payload)
        check(f"{provider.SERVICE}: an error, not a number",
              reading.status == common.ERROR and not reading.buckets,
              f"{reading.status}: {reading.detail}")
        check(f"{provider.SERVICE}: and it says so without the value",
              reading.detail == "The reply held a number that could not be used.",
              str(reading.detail))

    print("\n  JSON nested too deep to read is an error reading too")
    for provider in (claude, chatgpt, grok):
        reading = fetch_with(provider, raises=RecursionError("too deep"))
        check(f"{provider.SERVICE}: an error reading, not a crash",
              reading.status == common.ERROR, f"{reading.status}: {reading.detail}")
    deep = "[" * 100000
    try:
        json.loads(deep)
        check("(the sample really is too deep)", False)
    except RecursionError:
        check("(the sample really is too deep, and raises RecursionError, "
              "which is not a ValueError)", True)

    print("\n  and so is a reply that is not an object at all")
    for provider in (claude, chatgpt, grok):
        reading = fetch_with(provider, payload=[1, 2, 3])
        check(f"{provider.SERVICE}: a list is an error reading",
              reading.status == common.ERROR, f"{reading.status}: {reading.detail}")


def bucket_limits() -> None:
    print("\nFive thousand buckets become six")
    payload = {"limits": [{"kind": "weekly_scoped", "percent": 1,
                           "scope": {"model": {"display_name": f"Model {n}"}}}
                          for n in range(5000)]}
    buckets = claude._buckets_from(payload)
    check("Claude keeps at most six", len(buckets) <= common.MAX_BUCKETS,
          f"{len(buckets)} from 5000")

    print("\n  and the same cap applies whoever sends them")
    many = [Bucket(f"B{n}", 1.0) for n in range(5000)]
    check("cap_buckets caps", len(common.cap_buckets(many)) == common.MAX_BUCKETS,
          str(len(common.cap_buckets(many))))

    print("\nA two-hundred-thousand-character name is cut")
    monster = "N" * 200000
    payload = {"limits": [{"kind": "weekly_scoped", "percent": 1,
                           "scope": {"model": {"display_name": monster}}}]}
    bucket = claude._buckets_from(payload)[0]
    check("the label is short", len(bucket.label) <= common.MAX_LABEL,
          f"{len(bucket.label)} characters")
    check("and it is one line", "\n" not in bucket.label)

    check("safe_label cuts anything", len(common.safe_label(monster)) <= common.MAX_LABEL)
    messy = "a" + chr(0) + "b" + chr(27) + "c" + chr(10) + "d"
    check("control characters are dropped", common.safe_label(messy) == "abcd",
          repr(common.safe_label(messy)))
    check("an empty name falls back", common.safe_label("   ") == "?")
    check("a non-string name falls back", common.safe_label(12345) == "12345")
    check("None falls back", common.safe_label(None) == "?")

    print("\n  Claude's breakdown is capped too")
    rows = {"seven_day_breakdown": {"rows": [
        {"display_name": "X" * 5000, "percent": 1.0} for _ in range(500)]}}
    out = claude._breakdown_from(rows)
    check("at most MAX_BREAKDOWN rows", len(out) <= common.MAX_BREAKDOWN, str(len(out)))
    check("with short names", all(len(name) <= common.MAX_LABEL for name, _ in out))

    print("\n  and ChatGPT's and Grok's labels go through the same cut")
    bucket = chatgpt._window(
        {"w": {"used_percent": 5, "reset_at": "2030-01-01T00:00:00+00:00"}},
        "w", "W" * 500)
    check("ChatGPT label is short", len(bucket.label) <= common.MAX_LABEL,
          f"{len(bucket.label)}")


def date_limits() -> None:
    print("\nA date that cannot be a date is None, not a crash")
    # Item 4. Each of these reached datetime.fromtimestamp and raised, which
    # killed that service's poll on every attempt until the value changed.
    for label, value in (
        ("1e300", 1e300),
        ("-1e300", -1e300),
        ("10**20", 10 ** 20),
        ("-10**20", -(10 ** 20)),
        ("nan", float("nan")),
        ("inf", float("inf")),
        ("-inf", float("-inf")),
        ("a huge digit string", "9" * 40),
        ("nonsense text", "not a date"),
        ("empty", ""),
        ("a list", [1, 2]),
        ("True", True),
        ("year 10000 iso", "+010000-01-01T00:00:00Z"),
    ):
        try:
            result = common.parse_time(value)
            check(f"{label:<20} -> {result}", result is None or hasattr(result, "year"))
        except Exception as exc:            # noqa: BLE001 - the whole point
            check(f"{label:<20} raised {type(exc).__name__}", False, str(exc))

    print("\n  and a real date still parses")
    for label, value in (("unix seconds", 1790000000),
                         ("unix millis", 1790000000000),
                         ("iso with Z", "2030-01-01T00:00:00Z"),
                         ("iso with offset", "2030-01-01T00:00:00+00:00")):
        result = common.parse_time(value)
        check(f"{label} parses", result is not None and result.year == 2030
              or (label.startswith("unix") and result is not None),
              str(result))

    print("\n  including inside a login file's expiry")
    folder = pathlib.Path(tempfile.mkdtemp(prefix="theox-dates-"))
    bad = folder / ".credentials.json"
    import json
    for value in (1e300, 10 ** 20, float("nan"), "not a date"):
        bad.write_text(json.dumps({"claudeAiOauth": {
            "accessToken": "fake-AAAAAAAAAAAAAAAAAAAA",
            "expiresAt": value if value == value and abs(value if isinstance(
                value, (int, float)) else 0) < 1e308 else str(value)}}),
            encoding="utf-8")
        original = claude.credentials_path
        claude.credentials_path = lambda: bad
        try:
            token, expiry = claude.read_login()
            check(f"expiry {str(value)[:14]:<14} did not raise",
                  token is not None, f"expiry={expiry}")
        except Exception as exc:            # noqa: BLE001
            check(f"expiry {str(value)[:14]:<14} raised {type(exc).__name__}",
                  False, str(exc))
        finally:
            claude.credentials_path = original

    print("\n  and in a Grok auth file")
    grok_file = folder / "grok-auth.json"
    grok_file.write_text(json.dumps({
        "https://auth.x.ai::1111": {"key": "fake-AAAAAAAAAAAAAAAAAAAA",
                                    "expires_at": "9" * 40},
    }), encoding="utf-8")
    original = grok.auth_path
    grok.auth_path = lambda: grok_file
    try:
        token, expiry, _note = grok.load_login()
        check("an absurd Grok expiry did not raise", token is not None, str(expiry))
    except Exception as exc:                # noqa: BLE001
        check(f"Grok raised {type(exc).__name__}", False, str(exc))
    finally:
        grok.auth_path = original


def window_limits(app) -> None:
    print("\nA window is never bigger than the screen it is on")
    values = settings.load()
    values["services"] = ["claude", "chatgpt", "grok", "grokbot"]
    # Full-length labels on every service, at maximum scale: enough to want a
    # window wider than this machine's smallest screen, so the clamp is
    # actually exercised rather than merely present.
    wide = [Reading(name, OK,
                    [Bucket("L" * common.MAX_LABEL, 10.0),
                     Bucket("W" * common.MAX_LABEL, 20.0),
                     Bucket("F" * common.MAX_LABEL, 30.0)])
            for name in ("Claude", "ChatGPT", "Grok", "Grok Bot")]

    for name, window in (("biscuit", biscuit_mod.Biscuit(values)),
                         ("panel", panel_mod.Panel(values))):
        window.set_readings(wide)
        window.apply_scale(overlay.MAX_SCALE, remember=False)
        screen = window.nearest_screen()
        area = screen.availableGeometry()
        natural = window.natural_size()
        wide_wanted = round(natural.width() * overlay.MAX_SCALE)
        tall_wanted = round(natural.height() * overlay.MAX_SCALE)
        # The biscuit grows sideways and the panel downwards, so each one is
        # checked against the dimension it actually runs out of.
        check(f"{name} wanted more than the screen",
              wide_wanted > area.width() or tall_wanted > area.height(),
              f"wanted {wide_wanted}x{tall_wanted}, "
              f"screen is {area.width()}x{area.height()}")
        check(f"{name} fits across", window.width() <= area.width(),
              f"{window.width()}px on a {area.width()}px screen")
        check(f"{name} fits down", window.height() <= area.height(),
              f"{window.height()}px on a {area.height()}px screen")

    # And the clamp is a clamp, not a resize: it never makes a window bigger.
    bis = biscuit_mod.Biscuit(values)
    bis.set_readings([Reading("Claude", OK, [Bucket("5 hour", 10.0),
                                             Bucket("Weekly", 20.0)])])
    bis.apply_scale(1.0, remember=False)
    small = bis.width()
    natural = bis.natural_size().width()
    check("a window that already fits is untouched", small == natural,
          f"{small} vs {natural}")


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    network_limits()
    real_socket_limits()
    bucket_limits()
    date_limits()
    date_ranges()
    number_limits()
    window_limits(app)

    print()
    if fails:
        for name in fails:
            print("  FAILED:", name)
        return 1
    print("all limit tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
