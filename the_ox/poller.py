r"""Keeping the numbers current, without blocking the windows.

Each service is polled on its own schedule, every three minutes by default.
Fetches run on a worker thread: a provider call takes up to a second and the
strip repaints three times a second, so doing this on the UI thread would
stutter visibly.

What this handles:
  polling        every 3 minutes per service, staggered so they do not all
                 fire at once
  file watching  a new login in a login file refreshes that service within
                 a couple of seconds, so a sign-in shows up almost at once;
                 a file rewritten with the same login changes nothing
  lock pause     nothing is polled while the workstation is locked, and
                 everything refreshes on unlock
  backoff        429 and errors double the wait, up to 30 minutes, then reset
                 on the next success
  stale          the last good numbers are kept and go grey after 15 minutes

It never refreshes, rotates or writes a credential. It only reads.
"""
from __future__ import annotations

import ctypes
import os
import random
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from PySide6 import QtCore

from . import logs, registry
from .providers.common import ERROR, OK, OPEN_APP, Reading

_user32 = ctypes.windll.user32

POLL_SECONDS = 180               # the default: every 3 minutes
# The interval is a setting, 1 to 15 minutes. The floor is enforced here, in
# the poller, not just in the Settings window or the settings loader, so no
# value from anywhere can make it check more than once a minute.
MIN_POLL_SECONDS = 60
MAX_POLL_SECONDS = 15 * 60
# Error backoff is exactly what it always was, 3 minutes doubling to 30,
# whatever the interval: at 1 or 2 minutes a failing service still waits
# 3, 6, 12... It is never shorter than the chosen interval either, so above
# 3 minutes an error cannot make a service be asked MORE often.
MIN_BACKOFF = 180
MAX_BACKOFF = 30 * 60
STALE_AFTER = 15 * 60            # older than this and the value goes grey
# ...or, at a long interval, older than two missed checks. Without this a
# 15 minute interval would turn every reading grey just before each check.
STALE_MARGIN = 60


def poll_seconds(minutes) -> int:
    """Seconds between checks for a number of minutes, floor and cap applied.

    Zero, a negative number or a fraction of a minute gives the one minute
    floor. Something that is not a number at all gives the 3 minute default.
    Either way the answer is never under a minute.
    """
    try:
        seconds = float(minutes) * 60.0
    except (TypeError, ValueError, OverflowError):
        return POLL_SECONDS
    if seconds != seconds:                      # NaN
        return POLL_SECONDS
    return int(min(max(seconds, MIN_POLL_SECONDS), MAX_POLL_SECONDS))


FILE_DEBOUNCE_MS = 1500          # a login file is often written more than once
TICK_MS = 2000           # the lock probe is a syscall; 2s is responsive enough

# A change to a watched login file is looked at, at most, once a minute per
# service. Without this, anything that can write to a login file, or any
# tool that rewrites one in a loop, would set how often the file is read. A
# change inside the minute is not dropped, it is deferred to the end of it,
# so a real sign-in still shows up inside about a minute.
#
# And only a new login brings a check forward. A file rewritten with the
# same login in it changes nothing: the chosen interval and any error backoff
# stay exactly as they were. Before, every rewrite queued a check, and the
# folder watch counted every file the vendor's tool wrote next to its login
# (history, sessions, logs), so a busy tool had a service checked once a
# minute, even while it was backing off after a 401 or 429.
FILE_MIN_INTERVAL = 60.0

PROVIDERS = {spec.name: spec.provider for spec in registry.SERVICES}
ORDER = registry.ORDER


def _mark(path) -> tuple | None:
    """How a file looks from outside: modified time and size, or None if it
    is not there. The file is never opened."""
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        return None
    return (info.st_mtime_ns, info.st_size)


def workstation_locked() -> bool:
    r"""Is the workstation locked right now?

    OpenInputDesktop fails while the secure desktop is up, which is the
    simplest reliable signal and needs no window or message loop. The handle
    is closed immediately when the call succeeds.
    """
    handle = _user32.OpenInputDesktop(0, False, 0x0001)   # DESKTOP_READOBJECTS
    if not handle:
        return True
    _user32.CloseDesktop(handle)
    return False


@dataclass
class ServiceState:
    """What we know about one service between polls."""

    name: str
    reading: Reading | None = None
    last_success: datetime | None = None
    failures: int = 0
    next_due: float = 0.0            # monotonic seconds
    in_flight: bool = False
    last_error: str | None = None
    # File watching. last_file_refresh is when a file change last caused a
    # check; known_expiry is the login expiry we have already seen, so a
    # rewrite that does not change it is not treated as a new sign-in.
    last_file_refresh: float = -FILE_MIN_INTERVAL
    known_expiry: datetime | None = None
    expiry_seen: bool = False

    interval: float = POLL_SECONDS   # the chosen time between checks

    def backoff_seconds(self) -> float:
        if not self.failures:
            return self.interval
        backoff = min(MAX_BACKOFF, MIN_BACKOFF * (2 ** min(self.failures - 1, 6)))
        return max(backoff, self.interval)

    def age_seconds(self) -> float | None:
        if self.last_success is None:
            return None
        return (datetime.now(timezone.utc) - self.last_success).total_seconds()

    def stale_after(self) -> float:
        return max(STALE_AFTER, 2 * self.interval + STALE_MARGIN)

    def is_stale(self) -> bool:
        age = self.age_seconds()
        return age is not None and age > self.stale_after()


class _FetchSignals(QtCore.QObject):
    done = QtCore.Signal(str, object, object)    # service, Reading|None, error


class _FetchJob(QtCore.QRunnable):
    """One provider call, off the UI thread."""

    def __init__(self, service: str, signals: _FetchSignals) -> None:
        super().__init__()
        self.service = service
        self.signals = signals

    def run(self) -> None:
        try:
            reading = PROVIDERS[self.service].fetch()
            self.signals.done.emit(self.service, reading, None)
        except Exception as exc:                     # noqa: BLE001
            # Only the type name crosses this boundary. A provider exception
            # can carry a URL, and a URL can carry a token.
            self.signals.done.emit(self.service, None, type(exc).__name__)


class Poller(QtCore.QObject):
    """Drives every provider and publishes readings as they arrive."""

    readingsChanged = QtCore.Signal(list)
    lockChanged = QtCore.Signal(bool)

    def __init__(self, values: dict | None = None, parent=None) -> None:
        super().__init__(parent)
        self._log = logs.get_logger()
        self._settings = values if values is not None else {}
        self._enabled: list[str] = registry.enabled_names(self._settings)
        self._interval = float(poll_seconds(
            self._settings.get("poll_minutes", POLL_SECONDS / 60)))
        self._states = {name: ServiceState(name, interval=self._interval)
                        for name in self._enabled}
        self._locked = False
        self._pool = QtCore.QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._signals = _FetchSignals()
        self._signals.done.connect(self._on_fetched)

        self._clock = QtCore.QElapsedTimer()
        self._clock.start()

        # Stagger the first polls so several network calls do not land together.
        for index, name in enumerate(self._enabled):
            self._states[name].next_due = index * 0.4

        self._watcher = QtCore.QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._watched_for: dict[str, str] = {}
        # Each service's login file, and how it looked when last seen, so a
        # change to the folder can be told apart from a change to the login.
        self._login_files: dict[str, str] = {}
        self._file_marks: dict[str, tuple | None] = {}
        self._debounce = QtCore.QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._flush_file_changes)
        self._pending_files: set[str] = set()

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(TICK_MS)

    # ----- which services are in play --------------------------------------
    def set_enabled(self, names: list[str]) -> None:
        """Apply a new choice of services without a restart.

        A service that is switched off is dropped entirely: its state is
        forgotten, it is never polled again, and its login file is unwatched
        so it is not even opened.
        """
        wanted = [name for name in ORDER if name in set(names)]
        if wanted == self._enabled:
            return
        removed = [name for name in self._enabled if name not in wanted]
        added = [name for name in wanted if name not in self._enabled]
        self._enabled = wanted
        for name in removed:
            self._states.pop(name, None)
        for index, name in enumerate(added):
            self._states[name] = ServiceState(name, interval=self._interval)
            self._states[name].next_due = self._now() + index * 0.4
        self._log.info("services now: %s", ", ".join(wanted) or "(none)")
        self._rewatch()
        self.readingsChanged.emit(self.readings())

    def enabled(self) -> list[str]:
        return list(self._enabled)

    # ----- how often -------------------------------------------------------
    def set_interval_minutes(self, minutes) -> float:
        """Change the time between checks. Returns the seconds now in use.

        The floor of one minute and the cap of fifteen are applied here,
        whatever is asked for. A service already scheduled further out than
        the new interval is brought forward to it; nothing is checked early.
        """
        self._interval = float(poll_seconds(minutes))
        now = self._now()
        for state in self._states.values():
            state.interval = self._interval
            if not state.failures and state.next_due > now + self._interval:
                state.next_due = now + self._interval
        self._log.info("checking every %d min", int(self._interval // 60))
        return self._interval

    @property
    def interval_seconds(self) -> float:
        return self._interval

    # ----- login files -----------------------------------------------------
    def _rewatch(self) -> None:
        """Drop every watch and re-add only the chosen services."""
        for path in list(self._watcher.files()):
            self._watcher.removePath(path)
        for path in list(self._watcher.directories()):
            self._watcher.removePath(path)
        self._watched_for.clear()
        self._login_files.clear()
        self._file_marks.clear()
        self.watch_login_files()

    def watch_login_files(self) -> None:
        r"""Watch each chosen service's login file.

        A sign-in then lands within seconds instead of waiting for the next
        poll. The parent directory is watched too, because these tools
        replace the file rather than rewriting it, which silently drops a
        watch on the path itself. A change in that folder only counts when
        the login file itself changed; see _login_file_moved().

        Only chosen services are watched, and a service with no credential
        path, such as Grok Bot, is never touched at all.
        """
        wanted = {}
        for spec in registry.enabled_specs(self._settings):
            if spec.name in self._enabled and spec.credential_path is not None:
                wanted[spec.name] = spec.credential_path()
        for service, path in wanted.items():
            try:
                folder = str(path.parent)
                self._login_files[service] = str(path)
                if str(path) not in self._file_marks:
                    self._file_marks[str(path)] = _mark(path)
                if path.is_file() and str(path) not in self._watcher.files():
                    self._watcher.addPath(str(path))
                    self._watched_for[str(path)] = service
                if path.parent.is_dir() and folder not in self._watcher.directories():
                    self._watcher.addPath(folder)
                    self._watched_for[folder] = service
            except OSError:
                self._log.debug("could not watch the login file for %s", service)
        count = len(self._watcher.files()) + len(self._watcher.directories())
        # Runs after every login file change; a different count is news.
        self._log.info("watching %d login paths", count,
                       extra=logs.routine(f"watching {count} login paths"))

    def _on_file_changed(self, path: str) -> None:
        self._pending_files.add(path)
        self._debounce.start(FILE_DEBOUNCE_MS)

    def _on_directory_changed(self, path: str) -> None:
        self._pending_files.add(path)
        self._debounce.start(FILE_DEBOUNCE_MS)

    def _login_file_moved(self, service: str) -> bool:
        """Has this service's login file changed since it was last seen?

        Its size and modified time, or whether it exists at all. Anything
        else happening in the same folder does not count.
        """
        path = self._login_files.get(service)
        if path is None:
            return False
        now = _mark(path)
        if now == self._file_marks.get(path):
            return False
        self._file_marks[path] = now
        return True

    @staticmethod
    def _login_expiry(service: str):
        """This service's current login expiry, or None. Never raises."""
        spec = registry.spec_for(service)
        reader = getattr(spec.provider, "login_expiry", None) if spec else None
        if reader is None:
            return None
        try:
            return reader()
        except Exception:       # noqa: BLE001 - a bad login file is not a crash
            return None

    def _flush_file_changes(self) -> None:
        changed_paths, self._pending_files = self._pending_files, set()
        services = set()
        for path in changed_paths:
            service = self._watched_for.get(path)
            if service is None:
                continue
            if path == self._login_files.get(service):
                # The watcher reported the login file itself. Note how it
                # looks now, so the folder event that usually comes with it
                # does not count twice.
                self._file_marks[path] = _mark(path)
                services.add(service)
            elif self._login_file_moved(service):
                services.add(service)       # replaced, created or removed
        if not services:
            self.watch_login_files()      # a replace can drop the file's own watch
            return

        now = self._now()
        deferred: list[str] = []
        for service in sorted(services):
            state = self._states.get(service)
            if state is None:
                continue
            since = now - state.last_file_refresh
            if since < FILE_MIN_INTERVAL:
                # Inside the minute. Put the paths back and come round again
                # when the minute is up, so the change is delayed, not lost.
                deferred.append(service)
                continue

            # Did the login actually change, or was the file merely rewritten?
            # Only a new expiry counts as a sign-in, and only a sign-in brings
            # a check forward or clears the error backoff. A file rewritten
            # with the same login in it leaves the schedule exactly as it was.
            expiry = self._login_expiry(service)
            is_new = (not state.expiry_seen) or expiry != state.known_expiry
            state.known_expiry = expiry
            state.expiry_seen = True
            state.last_file_refresh = now
            # A real sign-in is always worth a line; a tool rewriting its
            # own login file (Codex does, all day) is routine.
            self._log.info("login file changed for %s (new login: %s)",
                           service, "yes" if is_new else "no",
                           extra=None if is_new else
                           logs.routine(f"login file rewritten: {service}"))
            if is_new:
                self.request(service, reason="login file changed")

        if deferred:
            wait = max(
                FILE_MIN_INTERVAL - (now - self._states[s].last_file_refresh)
                for s in deferred if s in self._states
            )
            for path, service in self._watched_for.items():
                if service in deferred:
                    self._pending_files.add(path)
            self._debounce.start(int(max(1.0, wait) * 1000) + FILE_DEBOUNCE_MS)
            self._log.info("login file changed for %s, deferred %.0fs "
                           "(one check a minute)", ", ".join(deferred), wait,
                           extra=logs.routine("login file deferred: "
                                              + ", ".join(deferred)))

        self.watch_login_files()          # re-add any watch dropped by a replace

    # ----- scheduling ------------------------------------------------------
    def _now(self) -> float:
        return self._clock.elapsed() / 1000.0

    def request(self, service: str, reason: str = "",
                clear_backoff: bool = True, routine: bool = False) -> None:
        """Poll one service as soon as possible.

        For what someone asked for (Refresh now, unlocking the PC, a click
        that opened a sign-in) and for a genuinely new login. With
        clear_backoff False nothing is brought forward: the service keeps its
        interval and its backoff, and only the line is logged.
        """
        state = self._states.get(service)
        if state is None:
            return
        if clear_backoff:
            state.next_due = self._now()
            state.failures = 0            # a deliberate request clears backoff
        if reason:
            self._log.info("%s queued: %s", service, reason,
                           extra=logs.routine(f"queued: {service}: {reason}")
                           if routine else None)

    def refresh_all(self, reason: str = "manual") -> None:
        for name in self._enabled:
            self.request(name, reason=reason)

    def _tick(self) -> None:
        locked = workstation_locked()
        if locked != self._locked:
            self._locked = locked
            self._log.info("workstation %s", "locked" if locked else "unlocked")
            self.lockChanged.emit(locked)
            if not locked:
                self.refresh_all(reason="unlocked")
        if locked:
            return                        # paused: nothing is polled while locked

        now = self._now()
        for name in self._enabled:
            state = self._states.get(name)
            if state is None:
                continue
            if state.in_flight or now < state.next_due:
                continue
            state.in_flight = True
            self._pool.start(_FetchJob(name, self._signals))

        if any(state.is_stale() for state in self._states.values()):
            self.readingsChanged.emit(self.readings())

    # ----- results ---------------------------------------------------------
    def _on_fetched(self, service: str, reading, error) -> None:
        state = self._states.get(service)
        if state is None:
            return                        # switched off while in flight
        state.in_flight = False
        jitter = random.uniform(0, 5)     # keep services from locking in step
        if reading is not None:
            # The login this check was made with. A later rewrite of the file
            # that keeps this same expiry is then not taken for a new login.
            state.known_expiry = reading.login_expires_at
            state.expiry_seen = True

        if reading is None:
            state.failures += 1
            state.last_error = error
            state.next_due = self._now() + state.backoff_seconds() + jitter
            self._log.warning("%s failed (%s), retrying in %.0fs",
                              service, error, state.backoff_seconds())
            self.readingsChanged.emit(self.readings())
            return

        if reading.status in (OK, OPEN_APP):
            # One line when a service first answers, or comes back, and not
            # every three minutes after that. Labels are already capped to 24
            # printable characters by the provider, and percents are numbers.
            previous = state.reading.status if state.reading is not None else None
            if previous != reading.status or state.failures:
                shown = " ".join(
                    f"{b.label} {b.percent_used:g}%" if b.percent_used is not None
                    else f"{b.label} ?"
                    for b in reading.buckets)
                self._log.info("reading %s: %s%s", service,
                               "ok" if reading.status == OK else "open the app",
                               f" {shown}" if shown else "")
            state.reading = reading
            state.last_success = datetime.now(timezone.utc)
            state.failures = 0
            state.last_error = None
            state.next_due = self._now() + self._interval + jitter
        else:
            # A 401 or a 429 is a real answer, not a crash. Keep the last good
            # numbers and back off, but remember why.
            state.failures += 1
            state.last_error = reading.detail
            state.next_due = self._now() + state.backoff_seconds() + jitter
            if state.reading is None or reading.status != ERROR:
                state.reading = reading
                state.last_success = datetime.now(timezone.utc) \
                    if reading.status == OPEN_APP else state.last_success
            self._log.info("%s: %s, next try in %.0fs",
                           service, reading.detail, state.backoff_seconds())
        self.readingsChanged.emit(self.readings())

    def readings(self) -> list[Reading]:
        """Current view of every service, in the fixed display order."""
        out: list[Reading] = []
        for name in self._enabled:
            state = self._states.get(name)
            if state is None:
                continue
            reading = state.reading
            if reading is None:
                out.append(Reading(name, ERROR,
                                   detail=state.last_error or "Waiting for the first reading."))
                continue
            # A copy, never the stored Reading. This method is called on
            # every tick, so mutating the stored object accumulated: the
            # staleness note was written over the real reason a service
            # stopped answering, and then over itself again and again.
            stale = state.is_stale()
            detail = reading.detail
            if stale:
                age = int((state.age_seconds() or 0) // 60)
                note = f"No update for {age} min. Last known numbers."
                # Keep why it stopped. "401. Open Claude Code to sign in
                # again." is the useful half; the age is the other half.
                reason = state.last_error or reading.detail
                detail = f"{reason} {note}" if reason else note
            out.append(replace(reading, stale=stale,
                               last_success=state.last_success, detail=detail))
        return out

    @property
    def locked(self) -> bool:
        return self._locked

    def stop(self) -> None:
        self._timer.stop()
        self._pool.waitForDone(2000)
