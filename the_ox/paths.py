r"""Where The Ox is allowed to write.

Hard rule: nothing written at runtime may land in a cloud-synced folder,
because anything there is copied off this machine. That started as a Google
Drive rule, when the project itself lived on Drive. It now covers OneDrive,
Dropbox and Box as well: on a great many Windows PCs the Desktop and
Documents folders sync to OneDrive, so "somewhere harmless like the
Desktop" is not a local folder at all.

All runtime state lives in %LOCALAPPDATA%\TheOx. Only source code, the
README and the mockup live anywhere else.

Matching is done on whole path components, never on substrings. "box" as a
substring appears in "sandbox", ".sandbox-bin" and plenty of ordinary
folder names, and refusing to write to any of those would break the app for
no reason.
"""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "TheOx"

# One whole path component equal to any of these, per service.
_EXACT_COMPONENTS = {
    "Google Drive": ("my drive", "google drive", "googledrive",
                     "shared drives", "drivefs", ".shortcut-targets-by-id"),
    "OneDrive": ("onedrive",),
    "Dropbox": ("dropbox", ".dropbox", ".dropbox.cache"),
    "Box": ("box", "box sync"),
}

# One whole path component STARTING with any of these, per service. This is
# what catches the tenant-named folders: "OneDrive - Example Company",
# "Dropbox (Personal)".
_PREFIX_COMPONENTS = {
    "OneDrive": ("onedrive -", "onedrive-"),
    "Dropbox": ("dropbox (", "dropbox -"),
}

# Windows records where OneDrive actually is. More reliable than any name
# rule, because a folder the user renamed still has its root recorded here.
_ROOT_VARIABLES = {
    "OneDrive": ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"),
    "Dropbox": ("DROPBOX",),
}


class UnsafeWritePath(RuntimeError):
    """Raised when something tries to write inside a synced folder."""


def _env_roots() -> list[tuple[str, Path]]:
    """Cloud roots the environment names, as (service, path)."""
    found: list[tuple[str, Path]] = []
    for service, names in _ROOT_VARIABLES.items():
        for name in names:
            value = os.environ.get(name)
            if not value:
                continue
            try:
                found.append((service, Path(value).expanduser().resolve()))
            except (OSError, ValueError):
                continue
    return found


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def synced_service(path: os.PathLike[str] | str) -> str | None:
    """The cloud service this path syncs to, or None if it is local.

    Returns the name so the error can say which one, which matters:
    "inside OneDrive" is actionable, "inside a synced folder" is not.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, ValueError):
        return None

    for service, root in _env_roots():
        if resolved == root or _is_within(resolved, root):
            return service

    parts = [part.lower() for part in resolved.parts]
    for service, markers in _EXACT_COMPONENTS.items():
        if any(part in markers for part in parts):
            return service
    for service, prefixes in _PREFIX_COMPONENTS.items():
        if any(part.startswith(prefixes) for part in parts):
            return service

    # A whole drive that is Google Drive: Google Drive for desktop mounts
    # itself as a drive letter with the volume label "Google Drive". This is
    # decided by the label, not by looking for a "My Drive" folder at the
    # drive root: any account on the PC may create a folder in C:\, and one
    # named "My Drive" there used to make every path on C: look like Google
    # Drive, so the app refused to write its log and would not start for
    # anyone. Changing a drive's label needs an administrator.
    if volume_label(resolved.anchor) == GOOGLE_DRIVE_LABEL:
        return "Google Drive"
    return None


GOOGLE_DRIVE_LABEL = "google drive"

_kernel32 = None


def volume_label(root: str) -> str:
    r"""The volume label of a drive root such as "C:\", lower case, or "".

    Anything that fails to answer, a disconnected network drive for
    instance, is simply "no label".
    """
    global _kernel32
    if not root:
        return ""
    if not root.endswith(("\\", "/")):
        root += "\\"
    try:
        import ctypes                               # noqa: PLC0415 - Windows only
        from ctypes import wintypes                 # noqa: PLC0415
        if _kernel32 is None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetVolumeInformationW.argtypes = [
                wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
            kernel32.GetVolumeInformationW.restype = wintypes.BOOL
            _kernel32 = kernel32
        label = ctypes.create_unicode_buffer(261)
        if not _kernel32.GetVolumeInformationW(root, label, 261, None, None,
                                               None, None, 0):
            return ""
        return label.value.strip().lower()
    except (OSError, AttributeError, ValueError):
        return ""


def real_local_appdata() -> Path:
    r"""The user's AppData\Local, built from %USERPROFILE%.

    Built from %USERPROFILE% rather than %LOCALAPPDATA%, so the path the
    app uses, logs and shows is the ordinary one, not a packaged host's
    rewritten %LOCALAPPDATA%.

    That does NOT escape a packaged host's redirection. When The Ox runs
    inside an MSIX-packaged app's container (started from a Claude session,
    for instance), Windows redirects what it writes under AppData\Local, and
    what it writes to HKCU, into that package's private copy, whatever
    string the path was built from. Only a copy started outside any such
    container, from the Start menu or by Windows at sign-in, writes the real
    folder. See is_redirected().
    """
    profile = os.environ.get("USERPROFILE")
    if profile:
        candidate = Path(profile) / "AppData" / "Local"
        if candidate.is_dir():
            return candidate
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise RuntimeError("Cannot locate AppData\\Local for this user")
    return Path(base)


# Tests point this somewhere temporary, as settings.py and logs.py already do
# for their own files. Without it, a test that creates the data folder or the
# launch folder creates the real one.
_data_dir_override: Path | None = None


def set_data_dir_override(path) -> None:
    """Send the data folder (and the launch folder inside it) elsewhere, for tests."""
    global _data_dir_override
    _data_dir_override = None if path is None else Path(path)


def data_dir() -> Path:
    """The one directory The Ox writes to."""
    if _data_dir_override is not None:
        return _data_dir_override
    return real_local_appdata() / APP_NAME


def is_google_drive_path(path: os.PathLike[str] | str) -> bool:
    """True if this path lives inside Google Drive specifically."""
    return synced_service(path) == "Google Drive"


def is_synced_path(path: os.PathLike[str] | str) -> bool:
    """True if this path syncs to any cloud service The Ox knows about."""
    return synced_service(path) is not None


def assert_safe_write_path(path: os.PathLike[str] | str) -> Path:
    """Raise unless this path is safe to write to. Call before every write."""
    resolved = Path(path).expanduser().resolve()
    service = synced_service(resolved)
    if service is not None:
        raise UnsafeWritePath(
            f"Refusing to write inside a {service} folder: {resolved}\n"
            f"Anything written there is copied off this PC.\n"
            f"All runtime state belongs in {data_dir()}"
        )
    return resolved


def is_redirected(path: os.PathLike[str] | str | None = None) -> bool:
    r"""True if LOCALAPPDATA points inside a packaged-app container.

    Windows redirects LOCALAPPDATA for MSIX/Store-packaged processes to
    ...\AppData\Local\Packages\<id>\LocalCache\Local. The Ox ships as a plain
    .exe so it sees the real path, but a run launched from inside a packaged
    host does not, and would read and write a different directory.
    """
    target = Path(path) if path is not None else data_dir()
    return any(part.lower() == "packages" for part in target.parts)


def ensure_data_dir() -> Path:
    """Create and return the data directory, after checking it is safe.

    The guard checks the fully resolved path, because that is what catches a
    symlink or junction pointing into Drive. The directory is then created at
    the path as written, so a container's filesystem redirection cannot move
    where The Ox thinks its data lives.
    """
    target = data_dir()
    assert_safe_write_path(target)
    target.mkdir(parents=True, exist_ok=True)
    return target


def startup_check() -> Path:
    """Run at launch. Fails loudly rather than syncing state to a cloud."""
    target = ensure_data_dir()
    probe = target / ".write-test"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink()
    return target
