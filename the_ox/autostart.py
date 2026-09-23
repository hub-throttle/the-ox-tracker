r"""Start with Windows: a shortcut in the personal Startup folder.

    %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\The Ox Tracker.lnk
        runs    C:\Program Files\The Ox Tracker\The Ox Tracker.exe --autostart
        in      C:\Program Files\The Ox Tracker

Up to 0.2.0 this was a value under HKCU\...\CurrentVersion\Run. Windows
skipped that value at two sign-ins in a row with the entry correct, enabled
and nothing logged anywhere, so it is no longer used. Every start of the
installed app removes it (see migrate_run_value), but only a value with our
name that points at this exe.

The installer never creates the shortcut. The installed app creates it
itself the first time it runs, and only if no choice has been recorded yet;
it then records "on" in settings.json. After that, only the tray menu's
"Start with Windows" and the switch in Settings change the choice, and they
record every change. The uninstaller removes the shortcut, for the account
running the uninstall, but the recorded choice survives in
%LOCALAPPDATA%\TheOx, so turning it off once means a reinstall does not turn
it back on. See apply_startup_choice().

--autostart is how the app knows Windows started it, so it can log "Started
by Windows at sign-in".

Task Manager
------------
Task Manager's Startup apps page can switch the shortcut off without
deleting it. It records that under
    HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder
as a value named after the shortcut file; an odd first byte means off. Only
ticking the box clears that flag. Nothing the app does by itself at start-up
ever does, so something switched off there is never switched back on.

Installed app only
------------------
From source, sys.executable is python.exe, and a shortcut pointing at it
would start a bare Python interpreter at every sign-in. So nothing in this
module writes anything unless the app is the frozen build AND is running
from the folder the installer recorded under HKLM, which only an
administrator can change. A copy run from anywhere else, a download folder
or a copy someone planted next to a DLL of their own, cannot point the
Startup shortcut at itself. From source, or from such a copy, the tray item
is greyed out and set_enabled() refuses.

A shortcut that cannot be read is never overwritten, by start-up or by the
tray: nothing is known about what it is, so it is left as it is and the log
says so.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

SHORTCUT_NAME = "The Ox Tracker.lnk"   # must match packaging\the-ox.iss
ARGUMENT = "--autostart"
DESCRIPTION = "Starts The Ox Tracker when you sign in"

APPROVED_FOLDER_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"

# The Run value used up to 0.2.0, only ever removed now.
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "The Ox Tracker"          # must match RunValue in packaging\the-ox.iss
APPROVED_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"

CHOICE_KEY = "start_with_windows_choice"    # in settings.json; see settings.py

# Where the installer records the install folder. Must match AppId in
# packaging\the-ox.iss: Inno Setup names the key after it, with "_is1".
UNINSTALL_KEY = (r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
                 r"\{3C24632D-FD47-47F9-A5FC-0F59BA62A4B4}_is1")
INSTALL_PATH_VALUE = "Inno Setup: App Path"


@dataclass(frozen=True)
class Shortcut:
    target: str
    arguments: str
    working_dir: str


# What read() gives back for a shortcut that is there but cannot be read.
UNREADABLE = Shortcut("", "", "")


def is_unreadable(shortcut: Shortcut | None) -> bool:
    return shortcut is not None and not shortcut.target


def installed_location() -> Path | None:
    """The folder the installer put the app in, as recorded under HKLM.

    None when the app is not installed. HKLM can only be written by an
    administrator, so a normal program cannot make any other folder look
    like the installed one.
    """
    try:
        import winreg                           # noqa: PLC0415 - Windows only
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value, kind = winreg.QueryValueEx(key, INSTALL_PATH_VALUE)
    except (OSError, ImportError):
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    if kind == winreg.REG_EXPAND_SZ:
        value = os.path.expandvars(value)
    elif kind != winreg.REG_SZ:
        return None
    return Path(value.strip())


def is_installed_copy(exe) -> bool:
    """Is this exe the one in the folder the installer recorded?"""
    where = installed_location()
    return where is not None and _same_path(str(Path(exe).parent), where)


def startup_folder() -> Path:
    """This account's own Startup folder, as Windows reports it."""
    from win32com.shell import shell        # noqa: PLC0415 - pywin32, Windows only
    return Path(shell.SHGetKnownFolderPath(shell.FOLDERID_Startup, 0, None))


def _disabled(value) -> bool:
    """Task Manager's flag: binary, and an odd first byte means off."""
    return isinstance(value, (bytes, bytearray)) and len(value) > 0 and bool(value[0] & 1)


class StartupStore:
    """The Startup folder shortcut, Task Manager's flag for it, and the old
    Run value. Tests pass a temporary folder and a fake winreg."""

    def __init__(self, folder: os.PathLike | str | None = None, reg=None) -> None:
        self._folder = Path(folder) if folder is not None else None
        self._reg = reg

    # ----- where ------------------------------------------------------------
    def folder(self) -> Path:
        if self._folder is None:
            self._folder = startup_folder()
        return self._folder

    def path(self) -> Path:
        return self.folder() / SHORTCUT_NAME

    def _winreg(self):
        if self._reg is None:
            import winreg                   # noqa: PLC0415 - Windows only
            self._reg = winreg
        return self._reg

    # ----- the shortcut -----------------------------------------------------
    @staticmethod
    def _shell_link():
        import pythoncom                    # noqa: PLC0415 - pywin32
        from win32com.shell import shell    # noqa: PLC0415
        return pythoncom, shell, pythoncom.CoCreateInstance(
            shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER,
            shell.IID_IShellLink)

    def read(self) -> Shortcut | None:
        """The shortcut, or None if there is none.

        One that cannot be read comes back with empty fields, so it counts as
        there but damaged, and is rewritten.
        """
        path = self.path()
        if not path.is_file():
            return None
        try:
            pythoncom, _shell, link = self._shell_link()
            link.QueryInterface(pythoncom.IID_IPersistFile).Load(str(path), 0)
            # Windows stores a Program Files target as %ProgramFiles%\...,
            # so ask for the ordinary path and expand anything left over.
            return Shortcut(os.path.expandvars(link.GetPath(0)[0]),
                            link.GetArguments(),
                            os.path.expandvars(link.GetWorkingDirectory()))
        except Exception as exc:        # noqa: BLE001 - a damaged file is not a crash
            from . import logs          # noqa: PLC0415
            logs.get_logger().warning("start with windows: the shortcut could not be "
                                      "read (%s)", type(exc).__name__)
            return Shortcut("", "", "")

    def write(self, exe, clear_flag: bool = True) -> None:
        """Write the shortcut. clear_flag also removes Task Manager's flag,
        which only ticking the box may do."""
        from . import paths                 # noqa: PLC0415
        path = self.path()
        exe = Path(exe)
        try:
            # The same guard as every other write: never into a synced folder.
            paths.assert_safe_write_path(path)
            pythoncom, _shell, link = self._shell_link()
            link.SetPath(str(exe))
            link.SetArguments(ARGUMENT)
            link.SetWorkingDirectory(str(exe.parent))
            link.SetIconLocation(str(exe), 0)
            link.SetDescription(DESCRIPTION)
            path.parent.mkdir(parents=True, exist_ok=True)
            link.QueryInterface(pythoncom.IID_IPersistFile).Save(str(path), 0)
        except OSError:
            raise
        except Exception as exc:        # noqa: BLE001 - COM and the guard, as OSError
            # Callers handle OSError; a COM error or a refused path is the
            # same thing to them: the shortcut could not be written.
            raise OSError(f"could not write the shortcut ({type(exc).__name__})") from exc
        if clear_flag:
            self._delete_value(APPROVED_FOLDER_KEY, SHORTCUT_NAME)

    def delete(self) -> None:
        try:
            self.path().unlink()
        except FileNotFoundError:
            pass
        self._delete_value(APPROVED_FOLDER_KEY, SHORTCUT_NAME)

    def disabled_by_windows(self) -> bool:
        """Has Task Manager's Startup apps switched the shortcut off?"""
        return _disabled(self._read_value(APPROVED_FOLDER_KEY, SHORTCUT_NAME)[0])

    # ----- the old Run value ------------------------------------------------
    def old_run_value(self) -> str | None:
        """The old Run value, or None. Anything but plain text reads as ""."""
        value, kind = self._read_value(RUN_KEY, VALUE_NAME)
        if value is None:
            return None
        return value if kind == self._winreg().REG_SZ and isinstance(value, str) else ""

    def old_run_disabled(self) -> bool:
        return _disabled(self._read_value(APPROVED_RUN_KEY, VALUE_NAME)[0])

    def delete_old_run_value(self) -> None:
        """Remove our Run value and its Task Manager flag. Nothing else."""
        self._delete_value(RUN_KEY, VALUE_NAME)
        self._delete_value(APPROVED_RUN_KEY, VALUE_NAME)

    # ----- registry ---------------------------------------------------------
    def _read_value(self, key_path: str, name: str):
        reg = self._winreg()
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, key_path) as key:
                return reg.QueryValueEx(key, name)
        except FileNotFoundError:
            return None, None

    def _delete_value(self, key_path: str, name: str) -> None:
        """Only ever one of our own values, and only ever removed."""
        reg = self._winreg()
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, key_path, 0,
                             reg.KEY_SET_VALUE) as key:
                reg.DeleteValue(key, name)
        except FileNotFoundError:
            pass


def is_available(frozen: bool | None = None, exe=None) -> bool:
    """Can this copy of the app manage Start with Windows at all?

    Only the frozen build, running from the installed folder. See the
    module note, "Installed app only".
    """
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        return False
    return is_installed_copy(exe if exe is not None else sys.executable)


def expected(exe) -> Shortcut:
    """Exactly the shortcut the app writes for this exe."""
    exe = Path(exe)
    return Shortcut(str(exe), ARGUMENT, str(exe.parent))


def _same_path(a: str, b) -> bool:
    try:
        return (os.path.normcase(os.path.abspath(a.strip().strip('"').strip()))
                == os.path.normcase(os.path.abspath(str(b))))
    except (OSError, ValueError):
        return False


def is_correct(shortcut: Shortcut | None, exe) -> bool:
    """Does this shortcut start this exe, with the argument, in its folder?"""
    if shortcut is None or not shortcut.target:
        return False
    want = expected(exe)
    return (_same_path(shortcut.target, want.target)
            and shortcut.arguments.strip() == want.arguments
            and _same_path(shortcut.working_dir, want.working_dir))


def what_is_wrong(shortcut: Shortcut | None, exe) -> str:
    """Which parts of a shortcut differ from what the app writes, for the log."""
    if shortcut is None:
        return "missing"
    if not shortcut.target:
        return "unreadable"
    want = expected(exe)
    parts = [label for label, ok in (
        ("its target", _same_path(shortcut.target, want.target)),
        ("its argument", shortcut.arguments.strip() == want.arguments),
        ("its start-in folder", _same_path(shortcut.working_dir, want.working_dir)),
    ) if not ok]
    return ", ".join(parts) or "nothing"


def is_enabled(store=None, exe=None) -> bool:
    """True if Windows will start THIS executable at sign-in.

    That needs the shortcut to be there and correct, and Task Manager not to
    have switched it off. A shortcut left by a copy installed somewhere else
    does not count, so ticking the box rewrites it.
    """
    store = store if store is not None else StartupStore()
    exe = exe if exe is not None else sys.executable
    try:
        return is_correct(store.read(), exe) and not store.disabled_by_windows()
    except OSError:
        return False


def _run_value_is_ours(command: str, exe) -> bool:
    """Does an old Run value start this exe, quoted or not?"""
    if not command:
        return False
    text = command.strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        text = text[1:end] if end > 0 else text[1:]
        return _same_path(text, exe)
    return _same_path(text, exe) or _same_path(text.split(" ", 1)[0], exe)


def migrate_run_value(store, exe) -> tuple[bool, bool]:
    """Remove the old Run value if it is ours: our name AND this exe.

    Returns (removed, it was switched off in Task Manager). A value with our
    name pointing somewhere else is left alone.
    """
    current = store.old_run_value()
    if current is None or not _run_value_is_ours(current, exe):
        return False, False
    was_off = store.old_run_disabled()
    store.delete_old_run_value()
    return True, was_off


def apply_startup_choice(values: dict, store=None, exe=None,
                         frozen: bool | None = None, save=None) -> str:
    """At startup: make the shortcut match the person's recorded choice.

    Returns a short description for the log. From source it does nothing.

    First the old Run value is removed, if it is ours. Then:

      Old Run value was        Off is recorded and no shortcut is made,
      switched off in          whatever was recorded before: Windows was
      Task Manager             not starting it, and must not start now.
      No choice recorded yet   This is the first run of the installed app.
                               The shortcut is created and True is recorded,
                               the one time the app decides.
      Off                      Nothing is created.
      On                       The shortcut is put back if it is missing and
                               rewritten if it is wrong, so it always starts
                               this exe with --autostart.
      On, but Task Manager     The shortcut is still kept correct, but the
      has it switched off      flag is left as it is, so it stays off.
      A shortcut that cannot   Left exactly as it is, whatever the choice,
      be read                  and the log says so.

    None of this ever clears Task Manager's flag. And none of it happens at
    all unless this is the installed copy; see the module note.
    """
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    if not frozen:
        return "not managed from source"
    exe = exe if exe is not None else sys.executable
    if not is_installed_copy(exe):
        return ("not managed: this copy is not running from the folder the "
                "installer recorded, so the Startup-folder shortcut is left alone")
    store = store if store is not None else StartupStore()
    if save is None:
        from . import settings      # noqa: PLC0415 - settings imports nothing of ours
        save = settings.save

    notes = []
    removed, was_off = migrate_run_value(store, exe)
    if removed:
        notes.append("the old Run entry was removed"
                     + (" (it was switched off in Task Manager)" if was_off else ""))
    choice = values.get(CHOICE_KEY)
    unreadable = ("the Startup-folder shortcut is there but could not be read, "
                  "so it was left as it is")

    if was_off and choice is not False:
        # Windows was skipping the old entry because of that switch, so it
        # was off in effect. A new shortcut would switch it back on.
        values[CHOICE_KEY] = False
        save(values)
        said = "off: the old entry was switched off in Task Manager, so off is recorded"
    elif choice is None:
        if is_unreadable(store.read()):
            # Nothing is decided and nothing recorded: next start, again.
            said = f"not decided yet: {unreadable}"
        else:
            store.write(exe, clear_flag=False)
            values[CHOICE_KEY] = True
            save(values)
            said = "on: turned on at first run, choice recorded"
    elif choice is False:
        said = "off: chosen, left off"
    else:
        current = store.read()
        if current is None:
            store.write(exe, clear_flag=False)
            said = "on: chosen; the Startup-folder shortcut was missing and has been created"
        elif is_unreadable(current):
            said = f"on: chosen; {unreadable}"
        elif is_correct(current, exe):
            said = "on: chosen; the Startup-folder shortcut is correct"
        else:
            wrong = what_is_wrong(current, exe)
            store.write(exe, clear_flag=False)
            said = (f"on: chosen; the Startup-folder shortcut was wrong ({wrong}) "
                    f"and has been rewritten")
        if store.disabled_by_windows():
            said += "; switched off in Task Manager, so left off"
    return "; ".join([said] + notes)


def set_enabled(on: bool, store=None, exe=None, frozen: bool | None = None) -> bool:
    """Create or remove the shortcut. Returns the state actually in effect.

    This is the person's own choice, from the tray or Settings, so ticking
    also clears Task Manager's flag. Refuses to write anything from source,
    or from any copy but the installed one; see the module note. A shortcut
    that cannot be read is not overwritten, even by ticking the box.
    """
    exe = exe if exe is not None else sys.executable
    if not is_available(frozen, exe):
        return False
    store = store if store is not None else StartupStore()
    migrate_run_value(store, exe)
    if on:
        if is_unreadable(store.read()):
            return is_enabled(store, exe)
        store.write(exe, clear_flag=True)
    else:
        store.delete()
    return is_enabled(store, exe)
