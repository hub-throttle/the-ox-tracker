r"""Click to re-login: opens a service's own app so the user can sign in.

The Ox Tracker never signs in, never types a credential and never
refreshes a token.
All it does is start the vendor's official app and get out of the way.

Everything is launched with %LOCALAPPDATA%\TheOx\launch as the working
directory. The CLI tools ask "do you trust this folder?" the first time they
run somewhere new, so pointing them all at one small empty folder means that
prompt is answered once, for a folder that holds nothing, instead of for the
home folder.

Nothing here writes to the Google Drive project folder.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import logs, paths

# Console apps get their own window so the login prompt is visible.
CREATE_NEW_CONSOLE = 0x00000010
DETACHED_PROCESS = 0x00000008


class LaunchError(RuntimeError):
    """The app could not be found or started."""


# Variables that mark a process as living inside a Claude Code session. A CLI
# launched from The Ox must start clean, not as a child of whatever session
# happened to start The Ox, or it inherits that session's identity, sockets
# and one-off tokens.
_STRIP_PREFIXES = (
    "CLAUDE_",
    "CLAUDECODE",
    "ANTHROPIC_",
    "CODEX_",
    "GROK_",
    "XAI_",
    "OPENAI_",
    # Set by the PyInstaller bootloader of the installed app, for its own
    # use. A child that happened to be PyInstaller-built as well would read
    # them as its own and look for its files inside The Ox Tracker's folder.
    "_PYI_",
    "_MEIPASS",
)
_STRIP_EXACT = (
    "CLAUDECODE",
    "DISABLE_AUTOUPDATER",
    "DISABLE_UPDATES",
)


def clean_environment(base: dict[str, str] | None = None,
                      bundle: str | None = None) -> dict[str, str]:
    """A copy of the environment with session markers removed.

    Anything naming Claude, Anthropic, Codex, Grok, xAI or OpenAI is dropped,
    including API-key variables, so a launched CLI signs in as itself with
    its own saved login rather than picking up a key from this process.

    In the installed app, PATH is also given back without the app's own
    folder. PyInstaller's PySide6 hook puts that folder at the front of PATH
    so Qt can find its DLLs, and a launched CLI would otherwise inherit a
    PATH whose first stop is a folder of Python, Qt and OpenSSL DLLs that
    have nothing to do with it.
    """
    source = dict(os.environ if base is None else base)
    clean = {
        key: value
        for key, value in source.items()
        if key.upper() not in _STRIP_EXACT
        and not key.upper().startswith(_STRIP_PREFIXES)
    }
    if bundle is None:
        bundle = getattr(sys, "_MEIPASS", None) if getattr(sys, "frozen", False) else None
    if bundle:
        for key in [k for k in clean if k.upper() == "PATH"]:
            clean[key] = _path_without(clean[key], bundle)
    return clean


def _path_without(value: str, folder: str) -> str:
    """PATH with every entry at or under one folder removed."""
    root = os.path.normcase(os.path.abspath(folder)).rstrip("\\/")
    kept = []
    for entry in value.split(os.pathsep):
        if not entry:
            continue
        try:
            here = os.path.normcase(os.path.abspath(entry)).rstrip("\\/")
        except (OSError, ValueError):
            kept.append(entry)
            continue
        if here == root or here.startswith(root + os.sep):
            continue
        kept.append(entry)
    return os.pathsep.join(kept)


# The launch folder, decided once at startup like the launch targets are.
# Reading %LOCALAPPDATA% at click time would mean a change to the
# environment between startup and a click could point a CLI, and the
# "do you trust this folder?" answer that comes with it, somewhere else.
_LAUNCH_DIR: Path | None = None

README_NAME = "README.txt"
README_TEXT = (
    "This folder exists so that Claude Code, Codex and Grok can be\n"
    "launched from somewhere harmless. Trusting this folder does not\n"
    "give any tool access to your home folder or your projects.\n"
    "It is safe to delete. The Ox Tracker recreates it when needed.\n"
    "\n"
    "Keep it empty. The Ox Tracker refuses to launch anything if it\n"
    "finds any other file or folder here.\n"
)


def resolve_launch_dir(log=None) -> Path:
    r"""Fix the launch folder for the life of the process. Call at startup.

    Inside the app's own data folder, built from %USERPROFILE%; see
    paths.real_local_appdata() for what that does and does not avoid.
    """
    global _LAUNCH_DIR
    _LAUNCH_DIR = paths.data_dir() / "launch"
    if log is not None:
        log.info("launch folder: %s", _LAUNCH_DIR)
    return _LAUNCH_DIR


def launch_dir() -> Path:
    """The fixed launch folder, resolving it now if startup has not yet."""
    if _LAUNCH_DIR is None:
        return resolve_launch_dir()
    return _LAUNCH_DIR


def ensure_launch_dir() -> Path:
    # Validate the resolved path, but create the path as written. See the
    # matching note in paths.ensure_data_dir().
    target = launch_dir()
    paths.assert_safe_write_path(target)
    target.mkdir(parents=True, exist_ok=True)
    readme = target / README_NAME
    if not readme.exists():
        readme.write_text(README_TEXT, encoding="utf-8")
    return target


def launch_dir_intruders(folder: Path | None = None) -> list[str]:
    r"""Everything in the launch folder that should not be there.

    The whole point of this folder is that it is empty, so answering the
    CLIs' "do you trust this folder?" prompt costs nothing. A folder that
    is not empty is a different proposition: a .claude, .codex or .grok
    folder here is project-level configuration for whichever tool is
    started, and it is read as trusted because the folder is trusted.
    Settings, hooks and MCP servers all live in files like those.

    So: README.txt and nothing else. Any other name is reported, hidden
    ones included, and a launch is refused until the folder is clear.
    """
    target = folder if folder is not None else launch_dir()
    try:
        entries = list(target.iterdir())
    except FileNotFoundError:
        return []                     # not created yet is the same as empty
    except OSError as exc:
        return [f"unreadable ({type(exc).__name__})"]
    found = []
    for entry in entries:
        if entry.name == README_NAME and entry.is_file():
            continue
        found.append(entry.name + ("\\" if entry.is_dir() else ""))
    return sorted(found)


def check_launch_dir(folder: Path | None = None) -> None:
    """Raise LaunchError, with what to do about it, if anything is there."""
    target = folder if folder is not None else launch_dir()
    intruders = launch_dir_intruders(target)
    if not intruders:
        return
    listed = ", ".join(intruders[:8])
    if len(intruders) > 8:
        listed += f", and {len(intruders) - 8} more"
    raise LaunchError(
        "The Ox Tracker will not launch anything from a launch folder "
        "that is not empty.\n\n"
        f"Folder: {target}\n"
        f"Found:  {listed}\n\n"
        "That folder is answered 'trusted' once, for every CLI. Anything "
        "left in it, a .claude, .codex or .grok folder especially, is then "
        "read as trusted project configuration by whichever tool starts "
        "there.\n\n"
        "To fix: delete everything in that folder except README.txt, then "
        "try again."
    )


# Executables and sign-in commands live in registry.py, so adding a service
# does not mean editing this file.
#
# The sign-in arguments matter. Simply starting a CLI does NOT reliably
# refresh its login: measured on 2026-09-21, launching Grok with five hours
# left changed nothing, and launching Claude with sixteen minutes left
# changed nothing either. These tools refresh lazily, when the token is
# actually needed. So a click on a service that needs a login runs the
# vendor's own documented sign-in command instead of just opening the app.
#
# The Ox Tracker still never signs in, never types a credential and never
# writes a token. It starts the vendor's command; the user completes it.


# Resolved once, at startup, and reused for the life of the process.
#
# The paths are built from %USERPROFILE% and %LOCALAPPDATA%. Reading those at
# click time means a change to the environment between startup and a click
# could point a launch somewhere else. Resolving once removes that window,
# and gives a single place to log what will actually be run.
_RESOLVED: dict[str, tuple[Path, bool]] = {}


def resolve_targets(log=None) -> dict[str, tuple[Path, bool]]:
    """Work out every launch target now, and remember it. Call at startup."""
    from . import registry

    _RESOLVED.clear()
    for spec in registry.SERVICES:
        try:
            _RESOLVED[spec.name] = (spec.executable(), spec.console)
        except (KeyError, OSError) as exc:
            if log is not None:
                log.warning("could not resolve a path for %s: %s",
                            spec.name, type(exc).__name__)
    if log is not None:
        for name, (target, _console) in _RESOLVED.items():
            # Paths only. There is nothing secret in an executable path, and
            # having them in the log makes a wrong launch obvious.
            log.info("launch target %s: %s (present=%s)",
                     name, target, target.is_file())
    return dict(_RESOLVED)


def resolve(service: str) -> tuple[Path, bool]:
    """The startup-time location of one service's app. No fallback.

    There used to be one: a miss here re-read %USERPROFILE% and built the
    path again. That quietly gave back the guarantee the cache exists to
    provide, because the click-time environment is exactly what must not
    decide what gets run. A service missing from the cache is an error.
    """
    cached = _RESOLVED.get(service)
    if cached is None:
        raise LaunchError(
            f"{service}: no location was worked out when The Ox Tracker "
            f"started, so there is nothing safe to launch. Restart The Ox "
            f"Tracker."
        )
    return cached


def login_arguments(service: str) -> tuple[str, ...]:
    """The vendor's own sign-in arguments, or () if it has none."""
    from . import registry
    spec = registry.spec_for(service)
    return spec.sign_in_args if spec else ()


def is_available(service: str) -> bool:
    try:
        target, _ = resolve(service)
    except LaunchError:
        return False
    return target.is_file()


def launch(service: str, sign_in: bool = False) -> Path:
    """Start the official app for one service. Returns the exe launched.

    With sign_in=True the vendor's own sign-in command is run, which is what
    a click on a dimmed or grey service does. Without it the app just opens.

    Console tools open in their own window, with the shared launch folder as
    the working directory. The Grok Bot desktop app is started normally: The
    Ox Tracker reads nothing from it and only brings it up so usage can be
    checked there.
    """
    target, console = resolve(service)
    if not target.is_file():
        raise LaunchError(f"{service}: not installed at {target}")

    arguments = list(login_arguments(service)) if sign_in else []
    try:
        workdir = ensure_launch_dir()
    except paths.UnsafeWritePath as exc:
        # A refusal to write is a reason not to launch, said the same way.
        raise LaunchError(f"{service}: {exc}") from None
    check_launch_dir(workdir)         # refuses if anything else is there
    flags = CREATE_NEW_CONSOLE if console else DETACHED_PROCESS
    try:
        subprocess.Popen(
            [str(target), *arguments],
            cwd=str(workdir),
            creationflags=flags,
            close_fds=True,
            env=clean_environment(),
        )
    except OSError as exc:
        raise LaunchError(f"{service}: {type(exc).__name__}") from None
    # Paths only: which app, whether it was the sign-in command, and the
    # folder it was started in, so a launch from the wrong place is obvious.
    logs.get_logger().info("launched %s%s from %s", service,
                           " (sign-in)" if arguments else "", workdir)
    return target


def launch_all(services: list[str], sign_in: bool = True) -> dict[str, str]:
    """Open every app in the list. Used by the 'needs a login' menu item.

    Defaults to the sign-in command, since that menu item exists precisely
    for services that need a login. One failure does not stop the others.
    """
    results: dict[str, str] = {}
    for service in services:
        try:
            launch(service, sign_in=sign_in)
            results[service] = "launched"
        except LaunchError as exc:
            results[service] = str(exc)
    return results
