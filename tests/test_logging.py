r"""Tests for the rolling log, focused on the promise that matters.

The Ox must never write a credential to disk. These cases feed the scrubber
things shaped like the real tokens on this machine and assert none of them
survive into a log line.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from the_ox import logs, paths   # noqa: E402

# Never write to the real log. Without this the fabricated credential
# samples below were appended to %LOCALAPPDATA%/TheOx/the-ox.log on every
# test run, which then had to be cleaned out by hand. Same override that
# settings.py has, and for the same reason.
TEMP_LOG = pathlib.Path(tempfile.mkdtemp(prefix="theox-test-")) / "the-ox.log"
logs.set_path_override(TEMP_LOG)

# Shapes, not real values. Nothing here is a working credential.
# Both audits contributed cases; the first scrubber let most of them through.
SAMPLES = [
    ("JWT",
     "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
     "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
    ("bearer header", "Authorization: Bearer abc123def456ghi789jkl012mno345pqr678"),
    ("tab-separated header", "Authorization:\tBearer abc123def456ghi789jkl012"),
    ("xai key", "xai-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"),
    ("sk key", "sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"),
    ("sk-ant-oat01", "sk-ant-oat01-" + "A1b2C3d4E5f6G7h8I9j0" * 2),
    ("rt_ refresh token", "refresh_token=rt_AbCdEfGhIjKlMnOpQrSt"),
    ("32-char opaque", "value QWxhZGRpbjpvcGVuIHNlc2FtZQ12"),
    ("40-char hex key", "digest da39a3ee5e6b4b0d3255bfef95601890afd80709"),
    ("43-char url-safe base64", "t dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
    ("standard base64 with + and /", "blob a+b/c9QWxhZGRpbjpvcGVuIHNlc2Ft=="),
    ("UUID account id", "account 231fbed6-2872-44c2-8d2b-f7b056d3ba9c"),
    ("JSON accessToken", '{"accessToken": "abc123def456ghi789jkl"}'),
    ("JSON key field", '{"key": "Zm9vYmFyYmF6cXV4Zm9vYmFy"}'),
    ("JSON access_token", '{"access_token":"xyz987654321abcdefghij"}'),
    ("query parameter", "failed https://x/y?access_token=abc123def456ghi&z=1"),
    ("email address", 'email":"ada@example.com"'),
    ("long opaque blob", "A" * 80),
    ("grok auth key field",
     "key=" + "Zm9vYmFyYmF6cXV4" * 8),
    # Unquoted field forms. Both audits pointed at these: a value written
    # as "accessToken: abc" rather than as JSON went through untouched.
    ("unquoted accessToken", "accessToken: abc123def456ghi789jkl012"),
    ("unquoted key", "key: Zm9vYmFyYmF6cXV4Zm9vYmFy"),
    ("unquoted api_key", "api_key:   sk-proj-AbCdEfGhIjKlMnOpQrStUv"),
    ("unquoted with tab", "refresh_token:\trt_AbCdEfGhIjKlMnOpQrSt"),
    ("unquoted secret", "client_secret: 9f8e7d6c5b4a39281706f5e4d3c2b1a0"),
    ("unquoted in a sentence",
     "failed for token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhIn0.cccccccccc"),
]

KEEP = [
    ("percent", "Claude: 5 hour 31% weekly 14%"),
    ("reset time", "resets Sun 27 Sep 12:00 PM"),
    ("status", "ChatGPT: expired, open Codex to sign in again"),
    ("path", r"%LOCALAPPDATA%\TheOx\settings.json"),
    ("reading line", "reading Grok: ok Weekly 61%"),
    ("budget line", "day 3/7, 42.9% allowed, over=True"),
    ("monitor line", "monitor Right monitor 3440: taskbar 3440x48 free=1104px"),
    ("backoff line", "Grok failed (ConnectionError), retrying in 180s"),
    # The unquoted rule must not eat ordinary log lines, which are full
    # of colons. These are real lines from this app.
    ("startup line", "The Ox Tracker starting: python 3.14.7, Windows-11"),
    ("launch target", "launch target Claude: D:/Profiles/x/.local/bin/claude.exe"),
    ("services line", "services now: Claude, ChatGPT, Grok"),
    ("stopping line", "The Ox Tracker stopping: event loop exited"),
    ("service detail", "ChatGPT: HTTP 429, next try in 360s"),
    ("watch count", "watching 6 login paths"),
    ("monitor key line", "biscuit monitor: 3440 wide monitor 3440x1440"),
    # The two things the long-run rule used to eat, which the log is there
    # to show: the app's own folders under Program Files, and the names of
    # the environment variables it removed at startup.
    ("plugin folders",
     "Qt platform windows, plugin folders C:/Program Files/The Ox Tracker/"
     "_internal/PySide6/plugins; C:/Program Files/The Ox Tracker"),
    ("installed build", r"installed build 0.2.0 at C:\Program Files\The Ox Tracker"),
    ("variable names",
     "startup: ignored environment PYSIDE_DISABLE_INTERNAL_QT_CONF, "
     "QT_QPA_PLATFORM_PLUGIN_PATH, OPENSSL_CONF"),
]

# And the exemptions stop there: a long opaque token is still redacted,
# even on a Program Files line, and a long capitals-only run is not a name.
STILL_SCRUBBED = [
    ("a token on a Program Files line",
     r"C:\Program Files\The Ox Tracker failed: QWxhZGRpbjpvcGVuIHNlc2FtZQ12xyz",
     "QWxhZGRpbjpvcGVuIHNlc2FtZQ12xyz"),
    ("a slashed blob on a Program Files line",
     "C:/Program Files/The Ox Tracker/QWxhZGRpbjpvcGVuIHNlc2FtZQ12xyzAB",
     "QWxhZGRpbjpvcGVuIHNlc2FtZQ12xyzAB"),
    ("capitals and digits with no underscore",
     "key material ABCDEF0123456789ABCDEF0123456789",
     "ABCDEF0123456789ABCDEF0123456789"),
]

failures = []

print("Scrubbing credential-shaped text")
for name, sample in SAMPLES:
    cleaned = logs.scrub(sample)
    leaked = cleaned == sample
    # Also make sure no long run of the original survived verbatim.
    chunks = [sample[i:i + 24] for i in range(0, max(1, len(sample) - 24), 12)]
    residue = any(chunk in cleaned for chunk in chunks if len(chunk) == 24)
    if leaked or residue:
        failures.append(f"{name} survived scrubbing: {cleaned[:60]!r}")
        print(f"  FAIL  {name}")
    else:
        print(f"  PASS  {name} -> {cleaned[:52]}")

print("\nKeeping the information we actually need")
for name, sample in KEEP:
    cleaned = logs.scrub(sample)
    if cleaned != sample:
        failures.append(f"{name} was mangled: {cleaned!r}")
        print(f"  FAIL  {name} -> {cleaned!r}")
    else:
        print(f"  PASS  {name}")

print("\n  but only those")
for name, sample, secret in STILL_SCRUBBED:
    cleaned = logs.scrub(sample)
    if secret in cleaned:
        failures.append(f"{name} survived scrubbing: {cleaned!r}")
        print(f"  FAIL  {name} -> {cleaned!r}")
    else:
        print(f"  PASS  {name} -> {cleaned[:60]}")

print("\nPrivate but not secret: the profile folder and monitor models")
import os                                        # noqa: E402

profile = os.environ.get("USERPROFILE", "")
if len(profile) >= 4:
    for form in (profile, profile.replace("\\", "/"), profile.upper()):
        line = f"launch target Claude: {form}\\.local\\bin\\claude.exe"
        out = logs.private(line)
        if profile.lower() in out.lower() or "%USERPROFILE%" not in out:
            failures.append(f"the profile folder survived: {out!r}")
            print(f"  FAIL  {out!r}")
        else:
            print(f"  PASS  {out}")
    longer = profile + "son\\x"
    if logs.private(longer) != longer:
        failures.append("a longer name that only starts like the profile was changed")
        print(f"  FAIL  {logs.private(longer)!r}")
    else:
        print("  PASS  a folder that only starts like the profile is left alone")
logs.hide_words(["ACME-X27 Pro", "\\\\.\\DISPLAY1", "ab"])
out = logs.private("monitor ACME-X27 Pro at 1920x1080, laptop \\\\.\\DISPLAY1, ab")
if "ACME" in out or "(monitor)" not in out:
    failures.append(f"a monitor model survived: {out!r}")
    print(f"  FAIL  {out!r}")
else:
    print(f"  PASS  {out}")
if "DISPLAY1" not in out or not out.endswith(", ab"):
    failures.append(f"a generic or short name was hidden: {out!r}")
    print(f"  FAIL  generic names should be left: {out!r}")
else:
    print("  PASS  generic device names and very short words are left alone")

print("\nLog file")
logs.setup(console=False)
if logs.log_path() != TEMP_LOG:
    failures.append("the log path override was ignored")
    print(f"  FAIL  writing to {logs.log_path()}, not the temporary file")
else:
    print(f"  PASS  the override sends the log to a temporary folder")
logger = logs.get_logger()
for _name, sample in SAMPLES:
    logger.info("pretend a provider leaked this: %s", sample)
for handler in logger.handlers:
    handler.flush()

target = pathlib.Path(logs.log_path())
if not target.is_file():
    failures.append("log file was not created")
    print("  FAIL  log file missing")
else:
    text = target.read_text(encoding="utf-8", errors="replace")
    print(f"  PASS  written to {target}")
    if paths.is_google_drive_path(target):
        failures.append("log file is inside Google Drive")
        print("  FAIL  log is inside Google Drive")
    else:
        print("  PASS  log is outside Google Drive")
    real = paths.data_dir() / logs.LOG_NAME
    if target == real:
        failures.append("the test wrote to the real log")
        print("  FAIL  this run wrote to the real log file")
    else:
        print(f"  PASS  the real log at {real} was not touched")
    for name, sample in SAMPLES:
        core = sample.split()[-1][:40]
        if len(core) >= 20 and core in text:
            failures.append(f"{name} reached the log file")
            print(f"  FAIL  {name} reached disk")
    else:
        print("  PASS  no sample reached disk verbatim")

print("\nThe log's size is capped")
import logging                                   # noqa: E402

def ok(label, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {label}"
          f"{(' -> ' + detail) if detail else ''}")
    if not condition:
        failures.append(label)

ok("at 1 MB it rolls over", logs.MAX_BYTES == 1024 * 1024, str(logs.MAX_BYTES))
ok("keeping one older copy", logs.BACKUP_COUNT == 1)
line = "filler line for the size test, nothing to scrub here " + "x " * 30
for number in range(40_000):                     # about 4.7 MB of lines
    logger.info("%05d %s", number, line)
for handler in logger.handlers:
    handler.flush()
files = sorted(p.name for p in target.parent.iterdir())
sizes = {p.name: p.stat().st_size for p in target.parent.iterdir()}
ok("after about 4.7 MB of lines there are exactly two files",
   files == [logs.LOG_NAME, logs.LOG_NAME + ".1"], str(files))
ok("the current file stays within 1 MB",
   sizes[logs.LOG_NAME] <= logs.MAX_BYTES, str(sizes[logs.LOG_NAME]))
ok("and so does the older copy",
   sizes[logs.LOG_NAME + ".1"] <= logs.MAX_BYTES, str(sizes[logs.LOG_NAME + ".1"]))
ok("so the log never takes more than 2 MB",
   sum(sizes.values()) <= 2 * 1024 * 1024, f"{sum(sizes.values())} bytes")
ok("and the newest lines are the ones kept",
   "39999 filler" in target.read_text(encoding="utf-8"))

print("\nRoutine lines: at most once an hour for each kind")


class Caught(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append((record.levelname, record.getMessage()))


caught = Caught()
logger.addHandler(caught)
gate = logs.routine_filter()
clock = [1000.0]
real_clock = gate.clock
gate.clock = lambda: clock[0]
gate.reset()
try:
    for _ in range(5):
        logger.info("login file changed for %s (new login: %s)", "ChatGPT", "no",
                    extra=logs.routine("rewritten: ChatGPT"))
    ok("five of one kind in a moment: one line",
       len(caught.lines) == 1, str(caught.lines))
    ok("and it reads normally", caught.lines[0][1]
       == "login file changed for ChatGPT (new login: no)", caught.lines[0][1])
    logger.info("login file changed for %s (new login: %s)", "Grok", "no",
                extra=logs.routine("rewritten: Grok"))
    ok("another kind is not held back by the first", len(caught.lines) == 2)
    for _ in range(3):
        logger.info("an ordinary line, not marked routine")
    ok("lines not marked routine are all written", len(caught.lines) == 5)
    for level in (logging.WARNING, logging.ERROR):
        for _ in range(3):
            logger.log(level, "trouble reading %s", "ChatGPT",
                       extra=logs.routine("rewritten: ChatGPT"))
    ok("a warning and an error are written the first time, even marked routine",
       [lvl for lvl, _ in caught.lines[5:]] == ["WARNING", "ERROR"],
       str(caught.lines[5:]))
    clock[0] += 3599
    logger.info("login file changed for %s (new login: %s)", "ChatGPT", "no",
                extra=logs.routine("rewritten: ChatGPT"))
    ok("a second short of an hour: still held back", len(caught.lines) == 7)
    clock[0] += 1
    logger.info("login file changed for %s (new login: %s)", "ChatGPT", "no",
                extra=logs.routine("rewritten: ChatGPT"))
    ok("an hour on: written again", len(caught.lines) == 8)
    ok("saying how many were left out",
       caught.lines[-1][1] == "login file changed for ChatGPT (new login: no) "
                              "(5 more like this since the last one, not written)",
       caught.lines[-1][1])
    logger.info("login file changed for %s (new login: %s)", "ChatGPT", "no",
                extra=logs.routine("rewritten: ChatGPT"))
    ok("and the count starts again", len(caught.lines) == 8)
    for handler in logger.handlers:
        handler.flush()
    written = target.read_text(encoding="utf-8")
    ok("the file gets the same treatment as any handler",
       written.count("login file changed for ChatGPT") == 2,
       str(written.count("login file changed for ChatGPT")))
finally:
    logger.removeHandler(caught)
    gate.clock = real_clock
    gate.reset()

print("\nThe same warning, or the same traceback, again and again")
# The sign-out fault logged one traceback every five seconds for eighteen
# minutes: 216 of them. Each one is still seen, but once, then with a count.
def dead_screen():
    raise RuntimeError("Internal C++ object already deleted")


def something_else():
    raise ValueError("something else")


def log_crash(fault) -> None:
    try:
        fault()
    except Exception:                                # noqa: BLE001
        logger.critical("unhandled exception", exc_info=True)


caught = Caught()
logger.addHandler(caught)
clock = [5000.0]
gate.clock = lambda: clock[0]
gate.reset()
try:
    for _ in range(12):
        clock[0] += 5
        log_crash(dead_screen)
    ok("twelve identical tracebacks in a minute: one written",
       len(caught.lines) == 1, str(len(caught.lines)))
    log_crash(something_else)
    ok("a different traceback with the same words is written at once",
       len(caught.lines) == 2, str(len(caught.lines)))
    logger.warning("strip on %s could not be placed", "Left monitor")
    logger.warning("strip on %s could not be placed", "Right monitor")
    ok("warnings that differ are each written", len(caught.lines) == 4)
    clock[0] += logs.REPEAT_EVERY - 70
    log_crash(dead_screen)
    ok("inside ten minutes the same one is still held back", len(caught.lines) == 4)
    clock[0] += 60
    log_crash(dead_screen)
    ok("ten minutes on, it is written again", len(caught.lines) == 5)
    ok("saying how many were left out",
       caught.lines[-1][1].endswith("(12 more like this since the last one, not written)"),
       caught.lines[-1][1])
    logger.warning("strip on %s could not be placed", "Left monitor")
    logger.warning("strip on %s could not be placed", "Left monitor")
    logs.log_shutdown("test")
    summary = [line for _lvl, line in caught.lines if "not written:" in line]
    ok("at shutdown, what is still held back is written with its count",
       any(line.startswith("2 more like this") and "Left monitor" in line
           for line in summary), str(summary))
    ok("and the stopping line follows it",
       caught.lines[-1][1] == "The Ox Tracker stopping: test", caught.lines[-1][1])
finally:
    logger.removeHandler(caught)
    gate.clock = real_clock
    gate.reset()

print()
if failures:
    for line in failures:
        print("  FAIL ", line)
    sys.exit(1)
print("All logging tests passed.")
