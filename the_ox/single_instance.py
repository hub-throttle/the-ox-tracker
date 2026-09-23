r"""One copy of The Ox Tracker per user.

Start with Windows launches it at sign-in, and a click on the Start menu
shortcut launches it again. Without a guard those are two copies: two tray
icons, two sets of strips drawn over each other, and every service polled
twice with the same token. The second copy should simply notice the first
and leave.

How
---
A named mutex, created at startup and held for the life of the process.
Windows releases it when the process ends, however it ends, so a crash can
never leave a stale lock behind the way a lock file can.

The name is per user: it carries the user's security identifier, so a second
person signed in to the same PC runs their own copy and is not blocked by
somebody else's. It lives in the Local\ namespace, which is this sign-in
session, and so needs no special privilege.

The installer is told about a second, fixed name (INSTALLER_MUTEX). Inno
Setup cannot work out a user's SID, so it looks for that one instead, to ask
for The Ox Tracker to be closed before files are replaced or removed.
"""
from __future__ import annotations

import ctypes
import hashlib
from ctypes import wintypes

ERROR_ALREADY_EXISTS = 183

# Must match AppMutex in packaging/the-ox.iss.
INSTALLER_MUTEX = "TheOxTrackerRunning"

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

# Held for the life of the process. Dropping these handles would release the
# mutex, and the next launch would think nothing was running.
_held: list[int] = []


def _user_sid() -> str:
    """This user's SID as a string, or a stable fallback if it cannot be read."""
    try:
        import win32api        # noqa: PLC0415 - pywin32, Windows only
        import win32security   # noqa: PLC0415
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        return win32security.ConvertSidToStringSid(sid)
    except Exception:          # noqa: BLE001 - fall back, never fail startup
        import getpass         # noqa: PLC0415
        try:
            return "user-" + getpass.getuser()
        except Exception:      # noqa: BLE001
            return "user-unknown"


def default_name() -> str:
    r"""Local\TheOxTracker-<hash of the SID>.

    Hashed only so the name is short and uniform. A SID is not secret, but it
    is also not something that needs to be spelled out in a mutex name.
    """
    digest = hashlib.sha256(_user_sid().encode("utf-8")).hexdigest()[:24]
    return f"Local\\TheOxTracker-{digest}"


def _create(name: str) -> tuple[int, bool]:
    """(handle, already_existed). A handle of 0 means the call failed."""
    handle = _kernel32.CreateMutexW(None, False, name)
    existed = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    return int(handle or 0), existed


def acquire(name: str | None = None, installer_name: str | None = INSTALLER_MUTEX) -> bool:
    """Claim this user's single instance. False if another copy has it.

    If the mutex cannot be created at all, which should not happen, the app
    is allowed to run: failing to detect a second copy is a nuisance, while
    refusing to start at all would be a broken app.
    """
    handle, existed = _create(name or default_name())
    if existed:
        if handle:
            _kernel32.CloseHandle(handle)
        return False
    if handle:
        _held.append(handle)
    if installer_name:
        extra, _ = _create(installer_name)
        if extra:
            _held.append(extra)
    return True


def release() -> None:
    """Let go of everything acquire() took. Tests use this; the app never does."""
    while _held:
        _kernel32.CloseHandle(_held.pop())
