r"""The polling engine: backoff, staleness, the lock pause and file watching.

Nothing here waits three minutes. The schedule is driven by a monotonic clock
the test can read, and the interesting cases are exercised by setting the
state directly rather than by sleeping.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

from PySide6 import QtCore, QtWidgets

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from the_ox import logs, poller as poller_mod, registry, settings   # noqa: E402
from the_ox.providers.common import ERROR, OK, OPEN_APP, Bucket, Reading   # noqa: E402

# No test writes to the real log. logs.set_path_override does for the
# log what settings.set_path_override does for settings.json.
logs.set_path_override(
    pathlib.Path(tempfile.mkdtemp(prefix="theox-test-")) / "the-ox.log")

# Never touch the real settings file.
settings.set_path_override(
    pathlib.Path(tempfile.mkdtemp(prefix="theox-test-")) / "settings.json")

# Every service on, so the scheduling cases have something to schedule.
ALL_SERVICES = {"services": list(registry.KEYS)}

fails: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


def folder_changes(poller) -> None:
    print("\n  a change elsewhere in the login file's folder is not a login change")
    # The folder is watched because these tools replace their login file,
    # which drops a watch on the file itself. But Claude Code and Codex write
    # plenty of other files in the same folders, all day, and each one used
    # to count as a login change: a busy tool had its service checked every
    # minute. Here the login file is a fabricated one in a temporary folder.
    import json                                      # noqa: PLC0415
    import os                                        # noqa: PLC0415
    from the_ox.providers import common              # noqa: PLC0415
    folder = pathlib.Path(tempfile.mkdtemp(prefix="theox-watch-"))
    login = folder / "auth.json"

    stamp = [1_700_000_000]

    def write_login(expires: str) -> None:
        login.write_text(json.dumps({"https://auth.x.ai::1": {
            "key": "fake-AAAAAAAAAAAAAAAAAAAA", "oidc_issuer": "https://auth.x.ai",
            "expires_at": expires}}), encoding="utf-8")
        # A distinct modified time each write, however quickly they come.
        stamp[0] += 10
        os.utime(login, (stamp[0], stamp[0]))

    write_login("2030-01-01T00:00:00Z")
    common.resolve_login_path("Grok", lambda: login)
    try:
        poller._rewatch()
        state = poller._states["Grok"]
        check("the fabricated login file is the one watched",
              str(login) in poller._watcher.files() and str(folder)
              in poller._watcher.directories(),
              str(poller._watcher.files()))

        def folder_event() -> None:
            state.last_file_refresh = poller._now() - poller_mod.FILE_MIN_INTERVAL - 1
            poller._pending_files = {str(folder)}
            poller._flush_file_changes()

        state.expiry_seen = True
        state.known_expiry = poller._login_expiry("Grok")
        state.failures = 3
        state.next_due = poller._now() + 1200.0
        due_before = state.next_due
        (folder / "history.jsonl").write_text("another line\n", encoding="utf-8")
        folder_event()
        check("another file written in the folder: nothing is checked",
              state.next_due == due_before and state.failures == 3,
              f"due in {state.next_due - poller._now():.0f}s")
        check("and the login file was not even read for it",
              state.last_file_refresh < poller._now() - poller_mod.FILE_MIN_INTERVAL)

        write_login("2030-01-01T00:00:00Z")          # rewritten, same login
        folder_event()
        check("the login file rewritten with the same login: nothing is checked",
              state.next_due == due_before and state.failures == 3)

        write_login("2031-06-01T00:00:00Z")          # a genuinely new login
        folder_event()
        check("a new login in it: checked at once",
              state.next_due <= poller._now() + 0.01,
              f"due in {state.next_due - poller._now():.1f}s")
        check("and its backoff is cleared", state.failures == 0)
    finally:
        common.reset_login_paths()
        poller._rewatch()

    print("\n  a request someone makes still brings a check forward")
    state = poller._states["Grok"]
    state.next_due = poller._now() + 1200.0
    state.failures = 5
    poller.request("Grok", reason="test")
    check("Refresh now checks at once and clears the backoff",
          state.next_due <= poller._now() + 0.01 and state.failures == 0)
    state.next_due = poller._now() + 1200.0
    state.failures = 5
    poller.request("Grok", reason="test", clear_backoff=False)
    check("but a request that keeps the backoff moves nothing",
          state.next_due > poller._now() + 1000 and state.failures == 5)


def main() -> int:
    logs.setup(console=False)
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    print("Backoff")
    state = poller_mod.ServiceState("Grok")
    check("no failures means the normal interval",
          state.backoff_seconds() == poller_mod.POLL_SECONDS,
          f"{state.backoff_seconds():.0f}s")
    seen = []
    for _ in range(9):
        state.failures += 1
        seen.append(state.backoff_seconds())
    check("each failure at least doubles the wait, up to the cap",
          all(b <= c for b, c in zip(seen, seen[1:])) and max(seen) == poller_mod.MAX_BACKOFF,
          " ".join(f"{int(v)}s" for v in seen))
    state.failures = 0
    check("a success clears the backoff",
          state.backoff_seconds() == poller_mod.POLL_SECONDS)

    print("\nStaleness")
    fresh = poller_mod.ServiceState("Claude")
    fresh.last_success = datetime.now(timezone.utc)
    check("a fresh reading is not stale", not fresh.is_stale())
    old = poller_mod.ServiceState("Claude")
    old.last_success = datetime.now(timezone.utc) - timedelta(
        seconds=poller_mod.STALE_AFTER + 60)
    check("older than the limit is stale", old.is_stale(),
          f"{poller_mod.STALE_AFTER // 60} min limit")

    print("\nLast good numbers are kept when a poll fails")
    poller = poller_mod.Poller(dict(ALL_SERVICES))
    poller._timer.stop()               # drive it by hand
    good = Reading("Claude", OK, [Bucket("5 hour", 42.0), Bucket("Weekly", 17.0)])
    poller._on_fetched("Claude", good, None)
    poller._on_fetched("Claude", None, "ConnectionError")
    current = {r.service: r for r in poller.readings()}["Claude"]
    check("the numbers survive a failed poll",
          [b.percent_used for b in current.buckets] == [42.0, 17.0],
          str([b.percent_used for b in current.buckets]))
    check("the failure counted toward backoff",
          poller._states["Claude"].failures == 1)

    print("\nStale marking reaches the surfaces")
    poller._states["Claude"].last_success = datetime.now(timezone.utc) - timedelta(
        seconds=poller_mod.STALE_AFTER + 120)
    marked = {r.service: r for r in poller.readings()}["Claude"]
    check("the reading is flagged stale", marked.stale is True)
    check("it still carries the last numbers",
          marked.buckets and marked.buckets[0].percent_used == 42.0)
    from the_ox import strip
    view = strip.build_view(marked)
    check("the strip greys a stale service", view.stale is True)
    check("but keeps the numbers on screen",
          [line[0] for line in view.lines] == [42.0, 17.0],
          str([line[0] for line in view.lines]))
    check("and greys the level lines",
          all(colour == strip.GREY for _pct, colour in view.lines))

    print("\nA 401 keeps the old numbers and backs off")
    poller._states["ChatGPT"].reading = Reading(
        "ChatGPT", OK, [Bucket("Weekly", 19.0)])
    poller._states["ChatGPT"].last_success = datetime.now(timezone.utc)
    poller._on_fetched("ChatGPT", Reading("ChatGPT", "expired", detail="401"), None)
    check("backoff engaged after a 401",
          poller._states["ChatGPT"].failures >= 1)

    print("\nWorkstation lock")
    locked = poller_mod.workstation_locked()
    check("lock state reads as a boolean", isinstance(locked, bool), str(locked))
    check("not locked while this test runs", locked is False)

    print("\nLogin file watching")
    poller.watch_login_files()
    watched = poller._watcher.files() + poller._watcher.directories()
    check("login paths are being watched", len(watched) >= 3, f"{len(watched)} paths")

    grok_paths = {p for p in poller._watched_for
                  if poller._watched_for[p] == "Grok"}

    def touch_grok():
        poller._pending_files = set(grok_paths)
        poller._flush_file_changes()

    state = poller._states["Grok"]
    state.next_due = 10_000.0
    state.failures = 4
    touch_grok()
    check("a login file change queues that service now",
          state.next_due <= poller._now() + 0.01,
          f"next due {state.next_due:.1f}s")
    check("and clears its backoff because the login was new",
          state.failures == 0)

    print("\n  but a file change cannot force more than one check a minute")
    # Without this, anything that can write to a login file sets the polling
    # rate: touch it a hundred times and The Ox sends a hundred requests,
    # each carrying a token.
    first_refresh = state.last_file_refresh
    state.next_due = 10_000.0
    for _ in range(50):
        touch_grok()
    check("fifty more changes did not queue another check",
          state.next_due > poller._now() + 1.0,
          f"next due in {state.next_due - poller._now():.1f}s")
    check("and the last check time did not move",
          state.last_file_refresh == first_refresh)

    print("\n  the change is deferred, not dropped")
    # A real sign-in inside that minute still has to show up, so the paths go
    # back on the pending list and the debounce timer is re-armed.
    check("the paths are pending again",
          bool(poller._pending_files & grok_paths),
          f"{len(poller._pending_files)} pending")
    check("and a timer is set to come back",
          poller._debounce.isActive() and poller._debounce.remainingTime() > 0,
          f"{poller._debounce.remainingTime()} ms")
    check("within about a minute",
          poller._debounce.remainingTime() <= (poller_mod.FILE_MIN_INTERVAL
                                               + 5) * 1000,
          f"{poller._debounce.remainingTime()} ms")

    print("\n  and a rewrite with no new login changes nothing at all")
    # A failing service whose login file is rewritten in the background used
    # to have its backoff reset each time, so it retried at full rate forever.
    # Then it kept its backoff but was still checked at once on every
    # rewrite. Now a rewrite with the same login leaves the schedule alone.
    state.last_file_refresh = poller._now() - poller_mod.FILE_MIN_INTERVAL - 1
    state.failures = 4
    state.next_due = poller._now() + 900.0
    due_before = state.next_due
    state.expiry_seen = True
    state.known_expiry = poller._login_expiry("Grok")   # unchanged since
    touch_grok()
    check("the backoff survives a rewrite", state.failures == 4,
          f"{state.failures} failures")
    check("and the next check is not brought forward",
          state.next_due == due_before,
          f"due in {state.next_due - poller._now():.0f}s, not "
          f"{due_before - poller._now():.0f}s")

    print("\n  a genuinely new expiry does clear it")
    state.last_file_refresh = poller._now() - poller_mod.FILE_MIN_INTERVAL - 1
    state.failures = 4
    state.known_expiry = datetime(2001, 1, 1, tzinfo=timezone.utc)   # stale
    touch_grok()
    check("a new login clears the backoff", state.failures == 0,
          f"{state.failures} failures")

    print("\n  a login file rewritten all day is logged once an hour")
    import logging                                   # noqa: PLC0415

    class Caught(logging.Handler):
        def __init__(self):
            super().__init__()
            self.lines = []

        def emit(self, record):
            self.lines.append(record.getMessage())

    caught = Caught()
    logs.get_logger().addHandler(caught)
    logs.routine_filter().reset()
    try:
        for _ in range(50):          # fifty rewrites, each a minute apart
            state.last_file_refresh = poller._now() - poller_mod.FILE_MIN_INTERVAL - 1
            state.known_expiry = poller._login_expiry("Grok")
            touch_grok()
        for _ in range(20):          # and twenty more inside the minute
            touch_grok()
        rewritten = [l for l in caught.lines if l.startswith("login file changed for Grok (")]
        queued = [l for l in caught.lines if l == "Grok queued: login file changed"]
        deferred = [l for l in caught.lines if "deferred" in l]
        watching = [l for l in caught.lines if l.startswith("watching ")]
        check("fifty rewrites: one 'login file changed' line", len(rewritten) == 1,
              str(len(rewritten)))
        check("and not one of them queued a check", len(queued) == 0, str(len(queued)))
        check("one 'deferred' line", len(deferred) == 1, str(len(deferred)))
        check("one 'watching' line", len(watching) == 1, str(len(watching)))
        check("every rewrite was still looked at, once a minute",
              state.last_file_refresh >= poller._now() - 5)
        for _ in range(3):           # three genuine sign-ins
            state.last_file_refresh = poller._now() - poller_mod.FILE_MIN_INTERVAL - 1
            state.known_expiry = datetime(2001, 1, 1, tzinfo=timezone.utc)
            touch_grok()
        signed_in = [l for l in caught.lines
                     if l == "login file changed for Grok (new login: yes)"]
        check("a real new sign-in is always written", len(signed_in) == 3,
              str(len(signed_in)))
        queued = [l for l in caught.lines if l == "Grok queued: login file changed"]
        check("and each one queues a check", len(queued) == 3, str(len(queued)))
    finally:
        logs.get_logger().removeHandler(caught)
        logs.routine_filter().reset()

    folder_changes(poller)

    print("\nA stale reading keeps the reason it went stale")
    # This used to be overwritten with the staleness note, losing "401. Open
    # Claude Code to sign in again." and, because readings() ran on every
    # tick, writing over itself again and again.
    gpt = poller._states["ChatGPT"]
    gpt.reading = Reading("ChatGPT", OK, [Bucket("Weekly", 19.0)],
                          detail="401. Open Codex to sign in again.")
    gpt.last_error = "401. Open Codex to sign in again."
    gpt.last_success = datetime.now(timezone.utc) - timedelta(
        seconds=poller_mod.STALE_AFTER + 600)
    out = {r.service: r for r in poller.readings()}["ChatGPT"]
    check("it is marked stale", out.stale is True)
    check("the reason survived", "401" in (out.detail or ""), out.detail or "")
    check("and the age is there too", "No update for" in (out.detail or ""))

    before = gpt.reading.detail
    for _ in range(5):
        poller.readings()
    check("calling readings() again does not pile up text",
          gpt.reading.detail == before, gpt.reading.detail or "")
    again = {r.service: r for r in poller.readings()}["ChatGPT"]
    check("and the answer is the same every time", again.detail == out.detail)
    check("the stored reading was never mutated",
          gpt.reading.stale is False and gpt.reading.detail == before)

    print("\nGrok Bot is never polled over the network")
    bot = {r.service: r for r in poller.readings()}["Grok Bot"]
    check("Grok Bot has no network status",
          bot.status in (ERROR, OPEN_APP), bot.status)

    print("\nA reading is logged when it first arrives, not every poll")
    import logging                                 # noqa: PLC0415 - test only
    lines: list[str] = []

    class Catch(logging.Handler):
        def emit(self, record) -> None:
            lines.append(record.getMessage())

    catcher = Catch()
    logs.get_logger().addHandler(catcher)
    try:
        fresh = poller_mod.Poller({"services": ["grok"]})
        good = Reading("Grok", OK, [Bucket("Weekly", 61.0)])
        fresh._on_fetched("Grok", good, None)
        fresh._on_fetched("Grok", Reading("Grok", OK, [Bucket("Weekly", 62.0)]), None)
        fresh._on_fetched("Grok", None, "ConnectionError")
        fresh._on_fetched("Grok", Reading("Grok", OK, [Bucket("Weekly", 63.0)]), None)
        fresh.stop()
    finally:
        logs.get_logger().removeHandler(catcher)
    readings = [line for line in lines if line.startswith("reading Grok")]
    check("the first answer is logged",
          readings[:1] == ["reading Grok: ok Weekly 61%"], str(readings))
    check("an unchanged second answer is not",
          not any("62%" in line for line in readings), str(readings))
    check("coming back after a failure is logged again",
          readings[-1:] == ["reading Grok: ok Weekly 63%"], str(readings))
    check("and that line survives the scrubber unchanged",
          logs.scrub(readings[0]) == readings[0] if readings else False)

    poller.stop()
    print()
    if fails:
        for name in fails:
            print("  FAILED:", name)
        return 1
    print("all poller tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
