r"""A small rolling log for The Ox Tracker, so a crash is recorded even
with no console open.

Writes to %LOCALAPPDATA%\TheOx\the-ox.log. At 1 MB it is renamed to
the-ox.log.1, replacing any older copy, and a new one is started, so the log
never takes more than about 2 MB. Never the Google Drive project folder.

Routine lines that repeat all day, such as a login file being rewritten by
its own tool, are written at most once an hour for each kind; see routine().
Warnings and errors are always written the first time. The same warning or
the same traceback again is held back for ten minutes and then written with
a count of how many were left out, so one fault that repeats every few
seconds cannot push everything else out of a 2 MB log. Any count still held
back when the app stops is written then.

What is NEVER logged: tokens, credential file contents, request headers, or
raw response bodies. Provider code already keeps tokens out of its return
values, and scrub() is a second line of defence over anything that reaches a
log record anyway.

What is kept out for privacy: the user profile folder is written as
%USERPROFILE%, and monitor model names are left out.
"""
from __future__ import annotations

import collections
import logging
import logging.handlers
import os
import re
import sys
import threading
import time
from typing import Any

from . import paths

LOG_NAME = "the-ox.log"
MAX_BYTES = 1024 * 1024      # then rolled over to the-ox.log.1
BACKUP_COUNT = 1             # one older copy: about 2 MB at most
LOGGER_NAME = "the_ox"
ROUTINE_EVERY = 60 * 60      # seconds between two routine lines of one kind
REPEAT_EVERY = 10 * 60       # seconds between two identical warnings or tracebacks
MAX_REPEAT_KINDS = 256       # distinct warnings remembered, oldest forgotten first
LINE_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

REDACTED = "[redacted]"

# Anything shaped like a credential is replaced before it reaches disk.
#
# Both audits found gaps in the first version, so this works three ways now:
# by field name, by vendor prefix, and by shape. The field-name rule matters
# most, because it catches a value whatever that value happens to look like.
_SENSITIVE_FIELDS = (
    "accesstoken", "access_token", "refreshtoken", "refresh_token",
    "id_token", "idtoken", "apikey", "api_key", "client_secret",
    "secret", "password", "token", "key", "cookie", "session",
)
_FIELDS = "|".join(_SENSITIVE_FIELDS)

# JSON style:  "accessToken": "....."   or   'key' : '.....'
# The field name is kept and only the quoted value is replaced, so a scrubbed
# line still says which field was dropped.
_JSON_FIELD = re.compile(
    r"""(?i)(["']?(?:""" + _FIELDS + r""")["']?\s*:\s*)(["'])[^"']*\2"""
)

# The same field names again, unquoted:  accessToken: abc   key: abc
#
# The quoted rule above runs first, so by the time this one sees the line a
# JSON value is already "[redacted]". The negative lookahead for a quote is
# what keeps this rule off those, and off a JSON line whose field name is
# quoted but whose value this rule has no business rewriting.
_BARE_FIELD = re.compile(
    r"(?i)\b(" + _FIELDS + r")\s*:[ \t]*(?![\"'\s])[^\s,;)\]}]+"
)

_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Query string or plain key=value:  ?access_token=xyz   refresh_token=rt_x
    re.compile(r"(?i)\b(?:" + _FIELDS + r")\s*=\s*[^\s&;,)\]}\"']+"),
    # Auth headers. The character class includes a tab: a header separated by
    # one slipped past the old rule, which only allowed spaces.
    re.compile(
        r"(?i)\b(?:authorization|proxy-authorization|bearer"
        r"|x-xai-token-auth|chatgpt-account-id|cookie|set-cookie)\b"
        r"[\s:=]*(?:bearer[\s:=]+)?\S+"
    ),
    # JWTs.
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    # Vendor prefixes. sk-ant-oat01-... is Anthropic's OAuth access token
    # shape and rt_... is a refresh token; both were missed before.
    re.compile(r"(?i)\bsk-ant-[a-z0-9]+-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)\b(?:sk|xai|sess|oat|rt|pk|ghp|gho)[-_][A-Za-z0-9_\-+/=]{12,}"),
    # Email addresses: the ChatGPT usage response carries one.
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    # UUIDs, which is what the account and user ids look like.
    re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        re.IGNORECASE),
)

# Any long opaque run. The base64 alphabet is included and the threshold
# is 24 rather than 60: a 32 character key and a 43 character URL-safe
# base64 string both sailed straight through the old rule.
_LONG_RUN = re.compile(r"\b[A-Za-z0-9_\-+/]{24,}={0,2}(?![A-Za-z0-9_\-+/=])")

# Two things the long-run rule used to eat that the log exists to show: the
# app's own folders under Program Files (the startup line that proves Qt
# loads plugins only from there came out as "[redacted]"), and the names of
# environment variables (the line saying which ones were removed at startup
# did too). Neither is ever a secret. A name here means CAPITALS joined by
# underscores, like QT_QPA_PLATFORM_PLUGIN_PATH; a token never looks like
# that, and every other rule above still applies to these lines.
_PROGRAM_FILES = re.compile(
    r"(?i)\b[a-z]:[\\/]Program Files(?: \(x86\))?[\\/][^;,\"'|<>\r\n]*")
_ENV_NAME = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+")


def scrub(text: str) -> str:
    """Remove anything that looks like a credential from a log line."""
    text = _JSON_FIELD.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}{m.group(2)}", text)
    text = _BARE_FIELD.sub(lambda m: f"{m.group(1)}: {REDACTED}", text)
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    kept = [match.span() for match in _PROGRAM_FILES.finditer(text)]

    def long_run(match: re.Match) -> str:
        found = match.group(0)
        start, end = match.span()
        # Inside a Program Files path, and made of ordinary folder names:
        # every piece between slashes is short. One long opaque piece is
        # still redacted, even there.
        if any(low <= start and end <= high for low, high in kept) and \
                all(0 < len(piece) < 24 for piece in found.split("/")):
            return found
        if _ENV_NAME.fullmatch(found):
            return found
        return REDACTED

    return _LONG_RUN.sub(long_run, text)


# ---------------------------------------------------------------------------
# Privacy: what is personal rather than secret.
# ---------------------------------------------------------------------------

_hidden_words: set[str] = set()
_profile_pattern: re.Pattern[str] | None = None
_profile_for: str | None = None


def hide_words(words) -> None:
    """Names that must never appear in the log, such as monitor models.

    monitors.py registers every monitor's reported name here. Short or
    generic names (the laptop panel is just \\\\.\\DISPLAY1) are not kept.
    """
    for word in words:
        if isinstance(word, str) and len(word.strip()) >= 3 \
                and not word.startswith("\\\\.\\"):
            _hidden_words.add(word.strip())


def _profile_regex() -> re.Pattern[str] | None:
    """The user's profile folder, with either kind of slash, any case."""
    global _profile_pattern, _profile_for
    profile = os.environ.get("USERPROFILE", "").rstrip("\\/")
    if profile != _profile_for:
        _profile_for = profile
        if len(profile) < 4:
            _profile_pattern = None
        else:
            parts = re.split(r"[\\/]+", profile)
            body = r"[\\/]+".join(re.escape(part) for part in parts)
            # Not when it is only the start of a longer name: C:\Users\ann
            # must not turn C:\Users\anna into %USERPROFILE%a.
            _profile_pattern = re.compile(body + r"(?![A-Za-z0-9_.\-])",
                                          re.IGNORECASE)
    return _profile_pattern


def private(text: str) -> str:
    """The user profile folder as %USERPROFILE%, and hidden names left out."""
    pattern = _profile_regex()
    if pattern is not None:
        text = pattern.sub("%USERPROFILE%", text)
    for word in _hidden_words:
        text = re.sub(r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])",
                      "(monitor)", text)
    return text


class ScrubbingFormatter(logging.Formatter):
    """Formats the record, then scrubs the finished line."""

    def format(self, record: logging.LogRecord) -> str:
        return private(scrub(super().format(record)))


def routine(kind: str) -> dict:
    """Mark a log call as routine:  log.info("...", extra=logs.routine("kind")).

    A routine line is written at most once an hour for each kind. The next
    one written says how many were left out in between. Give each kind its
    own name, including anything that makes it a different event (the
    service, a count), so one noisy kind never hides another.
    """
    return {"routine_kind": kind}


def _repeat_key(record: logging.LogRecord) -> tuple:
    """What makes two warnings "the same": level, text, and for a traceback
    the exception and the line it was raised on."""
    try:
        message = record.getMessage()
    except Exception:           # noqa: BLE001 - a bad format string is still a line
        message = str(record.msg)
    raised = None
    if record.exc_info and record.exc_info[1] is not None:
        kind, value, trace = record.exc_info
        where = None
        while trace is not None:
            where = (trace.tb_frame.f_code.co_filename, trace.tb_lineno)
            trace = trace.tb_next
        raised = (getattr(kind, "__name__", str(kind)), str(value)[:200], where)
    return (record.levelno, record.name, message[:500], raised)


class RoutineFilter(logging.Filter):
    """Keeps lines that repeat from drowning out the rest.

    A routine kind (see routine()) passes at most once every ROUTINE_EVERY
    seconds. A warning or error always passes the first time; the same one
    again passes at most once every REPEAT_EVERY seconds. Either way the next
    line written says how many were left out. Every other line passes.
    """

    def __init__(self, every: float = ROUTINE_EVERY, clock=time.monotonic,
                 repeat_every: float = REPEAT_EVERY) -> None:
        super().__init__()
        self.every = every
        self.repeat_every = repeat_every
        self.clock = clock
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}
        self._skipped: dict[str, int] = {}
        # key -> [when last written, how many held back since, the text]
        self._repeats: collections.OrderedDict = collections.OrderedDict()

    def reset(self) -> None:
        with self._lock:
            self._last.clear()
            self._skipped.clear()
            self._repeats.clear()

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "repeat_summary", False):
            return True             # the shutdown summary itself
        if record.levelno >= logging.WARNING:
            return self._repeat_filter(record)
        kind = getattr(record, "routine_kind", None)
        if kind is None:
            return True
        now = self.clock()
        with self._lock:
            last = self._last.get(kind)
            if last is not None and now - last < self.every:
                self._skipped[kind] = self._skipped.get(kind, 0) + 1
                return False
            self._last[kind] = now
            skipped = self._skipped.pop(kind, 0)
        if skipped:
            _note_skipped(record, skipped)
        return True

    def _repeat_filter(self, record: logging.LogRecord) -> bool:
        key = _repeat_key(record)
        now = self.clock()
        with self._lock:
            entry = self._repeats.get(key)
            if entry is not None and now - entry[0] < self.repeat_every:
                entry[1] += 1
                return False
            skipped = entry[1] if entry is not None else 0
            self._repeats[key] = [now, 0, key[2]]
            self._repeats.move_to_end(key)
            while len(self._repeats) > MAX_REPEAT_KINDS:
                self._repeats.popitem(last=False)
        if skipped:
            _note_skipped(record, skipped)
        return True

    def held_back(self) -> list[tuple[int, str]]:
        """(count, text) for every warning with repeats not yet written."""
        with self._lock:
            return [(entry[1], entry[2]) for entry in self._repeats.values()
                    if entry[1]]

    def forget_held_back(self) -> None:
        with self._lock:
            for entry in self._repeats.values():
                entry[1] = 0


def _note_skipped(record: logging.LogRecord, skipped: int) -> None:
    # Plain text only: the message is still a %-format string.
    record.msg = (f"{record.msg} ({skipped} more like this "
                  f"since the last one, not written)")


_routine_filter = RoutineFilter()


def routine_filter() -> RoutineFilter:
    """The filter in use, so tests can reset it or give it a clock."""
    return _routine_filter


_configured = False

# Tests point this somewhere temporary, exactly as settings.py does. Without
# it a test run writes its own sample lines into the real log, which then
# has to be cleaned out by hand: that happened, and the fabricated
# credential samples from tests/test_logging.py ended up in the live file.
_path_override = None


def set_path_override(path) -> None:
    """Send the log somewhere else, for tests.

    Any handler already installed is removed, so the next setup() call
    actually opens the new file rather than being skipped as configured.
    """
    global _path_override, _configured
    _path_override = None if path is None else __import__("pathlib").Path(path)
    logger = get_logger()
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:       # noqa: BLE001 - tidying up must not fail
            pass
    _configured = False


def get_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    # On the logger, not a handler, so it applies to every handler at once.
    if _routine_filter not in logger.filters:
        logger.addFilter(_routine_filter)
    return logger


def log_path():
    if _path_override is not None:
        return _path_override
    return paths.data_dir() / LOG_NAME


def setup(console: bool = True) -> logging.Logger:
    """Start logging. Safe to call more than once."""
    global _configured
    logger = get_logger()
    if _configured:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    target = log_path()
    paths.assert_safe_write_path(target)
    if _path_override is None:
        paths.ensure_data_dir()
    else:
        target.parent.mkdir(parents=True, exist_ok=True)

    formatter = ScrubbingFormatter(LINE_FORMAT, datefmt=DATE_FORMAT)
    handler = logging.handlers.RotatingFileHandler(
        target, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        logger.addHandler(stream)

    _configured = True
    return logger


def install_crash_handlers(on_fatal=None) -> None:
    """Record unhandled exceptions from any thread, and from Qt.

    Without this, closing the console window loses everything. The handlers
    log and then defer to the previous handler, so nothing is swallowed.
    """
    logger = get_logger()
    previous_hook = sys.excepthook

    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            logger.info("stopped by keyboard interrupt")
        else:
            logger.critical("unhandled exception", exc_info=(exc_type, exc, tb))
            if on_fatal is not None:
                try:
                    on_fatal(exc)
                except Exception:       # noqa: BLE001 - never fail inside a crash handler
                    logger.exception("crash handler itself failed")
        previous_hook(exc_type, exc, tb)

    sys.excepthook = hook

    def thread_hook(args: Any) -> None:
        if issubclass(args.exc_type, SystemExit):
            return
        logger.critical(
            "unhandled exception in thread %s",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = thread_hook


def install_qt_handler() -> None:
    """Send Qt's own warnings into the same log."""
    try:
        from PySide6 import QtCore
    except ImportError:
        return

    logger = get_logger()
    levels = {
        QtCore.QtMsgType.QtDebugMsg: logging.DEBUG,
        QtCore.QtMsgType.QtInfoMsg: logging.INFO,
        QtCore.QtMsgType.QtWarningMsg: logging.WARNING,
        QtCore.QtMsgType.QtCriticalMsg: logging.ERROR,
        QtCore.QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def handler(mode, _context, message):
        logger.log(levels.get(mode, logging.INFO), "Qt: %s", message)

    QtCore.qInstallMessageHandler(handler)


def log_environment() -> None:
    """One line of context at startup, to make a later crash readable."""
    import platform

    logger = get_logger()
    logger.info(
        "The Ox Tracker starting: python %s, %s, data dir %s",
        platform.python_version(),
        platform.platform(),
        paths.data_dir(),
    )
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        logger.info(
            "memory at start: %d%% used, %d MB physical free, %d MB commit free",
            status.dwMemoryLoad,
            status.ullAvailPhys // (1024 * 1024),
            status.ullAvailPageFile // (1024 * 1024),
        )
    except Exception:       # noqa: BLE001 - diagnostics must never break startup
        logger.debug("could not read memory status", exc_info=True)


def note_second_instance() -> None:
    """One line saying this copy is leaving because another one is running.

    Appended straight to the file and closed again at once: no handler, no
    rotation, nothing held open, so it cannot upset the log of the copy that
    is already running. Written only where that copy's log already is.
    Never raises; a copy that cannot say why it left still leaves.
    """
    try:
        target = log_path()
        if not target.parent.is_dir():
            return
        paths.assert_safe_write_path(target)
        record = logging.LogRecord(
            LOGGER_NAME, logging.INFO, __file__, 0,
            "another copy of The Ox Tracker is already running; this one "
            "is leaving without doing anything", None, None)
        line = ScrubbingFormatter(LINE_FORMAT, datefmt=DATE_FORMAT).format(record)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:           # noqa: BLE001 - leaving must never fail
        pass


def write_held_back() -> None:
    """Say how many repeats of each warning were held back and not written."""
    logger = get_logger()
    for count, text in _routine_filter.held_back():
        logger.info("%d more like this since the last one, not written: %s",
                    count, text[:200], extra={"repeat_summary": True})
    _routine_filter.forget_held_back()


def log_shutdown(reason: str) -> None:
    write_held_back()
    get_logger().info("The Ox Tracker stopping: %s", reason)
