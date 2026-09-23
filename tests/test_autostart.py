r"""Start with Windows, without going anywhere near the real Startup folder
or the real registry.

  The installed app creates and removes one shortcut in the Startup folder,
  starting itself with --autostart in its own folder. It creates it by
  itself only on its first run, when no choice has been recorded, and
  records "on". After that only the tray checkbox and the Settings switch
  change the choice, and a choice of "off" survives a reinstall.
  A shortcut left by a copy installed elsewhere does not count as "on".
  Every start removes the old Run value, but only ours: our name AND this
  exe. If Task Manager had switched that value off, off is what is kept.
  Nothing the app does at start-up ever clears Task Manager's switch for the
  shortcut, so nothing switched off there is ever switched back on.
  Run from source, nothing is ever written or removed, and the tray item is
  greyed out. The same goes for any copy of the app that is not running
  from the folder the installer recorded under HKLM.
  A shortcut that cannot be read is never overwritten.

Settings go to a temporary file, never the real settings.json. The real
store is tested against a temporary folder and a fake winreg; the real
Startup folder and registry are replaced for the whole run by stand-ins that
fail the test if they are ever reached. Where the installer recorded the app
is answered by a stand-in too, so this test does not depend on the app being
installed on this PC.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PySide6 import QtWidgets                       # noqa: E402

from the_ox import autostart, settings, tray        # noqa: E402

TEMP = pathlib.Path(tempfile.mkdtemp(prefix="theox-test-"))
settings.set_path_override(TEMP / "settings.json")

INSTALLED = r"C:\Program Files\The Ox Tracker\The Ox Tracker.exe"
ELSEWHERE = r"D:\Old copy\The Ox Tracker.exe"
RIGHT = autostart.expected(INSTALLED)
OFF = b"\x03" + b"\x00" * 11
ON = b"\x02" + b"\x00" * 11

# Where the installer "recorded" the app, for the whole run. The real answer
# comes from HKLM, and depends on what is installed on this PC.
recorded = [pathlib.Path(INSTALLED).parent]
autostart.installed_location = lambda: recorded[0]

fails: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


class FakeStore:
    """The shortcut, its flag and the old Run value, in memory.

    Counts every write so a test can insist on none."""

    def __init__(self, shortcut=None, disabled=False, run=None, run_disabled=False):
        self.shortcut = shortcut
        self.disabled = disabled            # Task Manager's switch for the shortcut
        self.run = run                      # the old Run value
        self.run_disabled = run_disabled    # Task Manager's switch for that
        self.writes = 0
        self.run_deletes = 0

    def read(self):
        return self.shortcut

    def write(self, exe, clear_flag=True):
        self.writes += 1
        self.shortcut = autostart.expected(exe)
        if clear_flag:
            self.disabled = False

    def delete(self):
        self.writes += 1
        self.shortcut = None
        self.disabled = False

    def disabled_by_windows(self):
        return self.disabled

    def old_run_value(self):
        return self.run

    def old_run_disabled(self):
        return self.run_disabled

    def delete_old_run_value(self):
        self.run_deletes += 1
        self.run = None
        self.run_disabled = False


class RealThingTouched(AssertionError):
    pass


class ForbiddenStore:
    """Stands in for the real store. Building one is a test failure."""

    def __init__(self, *_args, **_kwargs) -> None:
        raise RealThingTouched("the real Startup folder or registry was about to be used")


def forbidden_folder():
    raise RealThingTouched("the real Startup folder was about to be looked up")


class FakeManager:
    def targets(self):
        return []


def run(values, store, frozen=True, exe=INSTALLED):
    saves: list[dict] = []
    said = autostart.apply_startup_choice(values, store, exe, frozen=frozen,
                                          save=lambda v: saves.append(dict(v)))
    return said, saves


# ---------------------------------------------------------------------------
def module_level() -> None:
    print("The installed app adds and removes its own shortcut")
    check("the shortcut starts the exe with --autostart, in its own folder",
          RIGHT == autostart.Shortcut(INSTALLED, "--autostart",
                                      r"C:\Program Files\The Ox Tracker"), str(RIGHT))
    store = FakeStore()
    check("off to begin with", not autostart.is_enabled(store, INSTALLED))
    check("ticking it turns it on",
          autostart.set_enabled(True, store, INSTALLED, frozen=True) is True)
    check("the shortcut is exactly right", store.shortcut == RIGHT)
    check("unticking it turns it off",
          autostart.set_enabled(False, store, INSTALLED, frozen=True) is False)
    check("and the shortcut is gone", store.shortcut is None)

    print("\nOnly a shortcut that starts THIS exe, with the argument, counts")
    for label, shortcut in (
            ("another copy", autostart.expected(ELSEWHERE)),
            ("no argument", autostart.Shortcut(INSTALLED, "", RIGHT.working_dir)),
            ("another folder to start in", autostart.Shortcut(INSTALLED, "--autostart", r"C:\\")),
            ("unreadable", autostart.Shortcut("", "", ""))):
        check(f"{label}: not 'on'",
              not autostart.is_enabled(FakeStore(shortcut), INSTALLED))
    check("a different letter case still counts",
          autostart.is_enabled(FakeStore(autostart.Shortcut(
              INSTALLED.upper(), "--autostart", RIGHT.working_dir.lower())), INSTALLED))
    store = FakeStore(autostart.expected(ELSEWHERE))
    autostart.set_enabled(True, store, INSTALLED, frozen=True)
    check("ticking the box points it here", store.shortcut == RIGHT)

    print("\nTask Manager's switch for the shortcut is respected")
    store = FakeStore(RIGHT, disabled=True)
    check("a shortcut Task Manager switched off is not 'on'",
          not autostart.is_enabled(store, INSTALLED))
    check("ticking the box is the one thing that switches it back on",
          autostart.set_enabled(True, store, INSTALLED, frozen=True) is True
          and store.disabled is False)

    print("\nFrom source nothing is ever written")
    store = FakeStore()
    existing = FakeStore(RIGHT, run=f'"{INSTALLED}"')
    check("it says it cannot manage it", autostart.is_available(frozen=False) is False)
    check("ticking does nothing",
          autostart.set_enabled(True, store, sys.executable, frozen=False) is False)
    autostart.set_enabled(False, existing, INSTALLED, frozen=False)
    check("unticking removes nothing: not the shortcut, not the old Run value",
          existing.shortcut == RIGHT and existing.run is not None)
    check("no write of any kind happened",
          store.writes == existing.writes == existing.run_deletes == 0)
    check("this test run really is from source", autostart.is_available() is False)

    print("\nA copy that is not the installed one changes nothing")
    # Copy the app anywhere, add a DLL of your own, run it once: it must not
    # be able to point the Startup shortcut at itself.
    store = FakeStore(RIGHT, run=f'"{INSTALLED}"')
    check("it cannot manage Start with Windows",
          autostart.is_available(frozen=True, exe=ELSEWHERE) is False)
    check("ticking from it does nothing",
          autostart.set_enabled(True, store, ELSEWHERE, frozen=True) is False)
    said, saves = run({autostart.CHOICE_KEY: True}, store, exe=ELSEWHERE)
    check("and at its start-up the shortcut is left pointing at the installed app",
          store.shortcut == RIGHT and store.writes == 0 and not saves, said)
    check("the old Run value is not touched from there either",
          store.run is not None and store.run_deletes == 0)
    check("and the log says why", "not the installed" in said
          or "not running from the folder the installer recorded" in said, said)
    recorded[0] = None
    check("with nothing installed at all, no copy manages it",
          autostart.is_available(frozen=True, exe=INSTALLED) is False)
    recorded[0] = pathlib.Path(INSTALLED).parent
    check("the installed copy itself can",
          autostart.is_available(frozen=True, exe=INSTALLED) is True)
    check("and the stand-in above is what answered, not this PC's HKLM",
          autostart.installed_location() == pathlib.Path(INSTALLED).parent)


def startup_choice() -> None:
    print("\nAt startup")
    store, values = FakeStore(), {autostart.CHOICE_KEY: None}
    said, saves = run(values, store)
    check("first run: the app creates the shortcut itself",
          store.shortcut == RIGHT, said)
    check("records the choice as on, and saves that once",
          values[autostart.CHOICE_KEY] is True and len(saves) == 1)

    writes = store.writes
    said, saves = run(values, store)
    check("later runs: a correct shortcut is left alone",
          store.writes == writes and not saves, said)
    check("and the log says it is correct", "is correct" in said, said)

    store = FakeStore(disabled=True)
    said, _ = run({autostart.CHOICE_KEY: None}, store)
    check("first run never clears a Task Manager 'off' left from before",
          store.disabled is True and not autostart.is_enabled(store, INSTALLED), said)

    for label, shortcut, words in (
            ("missing", None, "missing and has been created"),
            ("for another copy", autostart.expected(ELSEWHERE), "wrong"),
            ("without the argument", autostart.Shortcut(INSTALLED, "", RIGHT.working_dir), "wrong")):
        store = FakeStore(shortcut)
        said, saves = run({autostart.CHOICE_KEY: True}, store)
        check(f"chosen on, shortcut {label}: made right", store.shortcut == RIGHT, said)
        check(f"chosen on, shortcut {label}: the log says so, nothing re-recorded",
              words in said and not saves, said)
        store = FakeStore(shortcut, disabled=True)
        run({autostart.CHOICE_KEY: True}, store)
        check(f"chosen on, shortcut {label}, off in Task Manager: made right, still off",
              store.shortcut == RIGHT and store.disabled is True)

    for shortcut, words in (
            (autostart.expected(ELSEWHERE), "(its target, its start-in folder)"),
            (autostart.Shortcut(INSTALLED, "", RIGHT.working_dir), "(its argument)")):
        said, _ = run({autostart.CHOICE_KEY: True}, FakeStore(shortcut))
        check(f"the log names what was wrong: {words}", words in said, said)

    print("\n  a shortcut that cannot be read is never overwritten")
    for choice in (True, None):
        store = FakeStore(autostart.UNREADABLE)
        values = {autostart.CHOICE_KEY: choice}
        said, saves = run(values, store)
        check(f"recorded {choice}: left exactly as it is",
              store.shortcut == autostart.UNREADABLE and store.writes == 0, said)
        check(f"recorded {choice}: the log says it could not be read",
              "could not be read" in said, said)
        check(f"recorded {choice}: and nothing new is recorded",
              values[autostart.CHOICE_KEY] is choice and not saves)
    store = FakeStore(autostart.UNREADABLE)
    check("not even ticking the box overwrites it",
          autostart.set_enabled(True, store, INSTALLED, frozen=True) is False
          and store.shortcut == autostart.UNREADABLE and store.writes == 0)

    store = FakeStore()
    said, saves = run({autostart.CHOICE_KEY: False}, store)
    check("chosen off: nothing is created, nothing saved",
          store.shortcut is None and store.writes == 0 and not saves, said)
    store = FakeStore(RIGHT)
    run({autostart.CHOICE_KEY: False}, store)
    check("chosen off: a shortcut already there is not touched either",
          store.writes == 0 and store.shortcut == RIGHT)

    store, values = FakeStore(), {autostart.CHOICE_KEY: None}
    said, saves = run(values, store, frozen=False)
    check("from source nothing is written or recorded",
          store.writes == 0 and values[autostart.CHOICE_KEY] is None and not saves, said)


def migration() -> None:
    print("\nThe old Run value is removed, but only ours")
    for label, value in (("quoted", f'"{INSTALLED}"'), ("unquoted", INSTALLED),
                         ("in another letter case", f'"{INSTALLED.lower()}"')):
        store = FakeStore(run=value)
        said, _ = run({autostart.CHOICE_KEY: True}, store)
        check(f"{label}: removed", store.run is None and store.run_deletes == 1, said)
        check(f"{label}: and the shortcut takes over", store.shortcut == RIGHT)
        check(f"{label}: the log says so", "old Run entry was removed" in said, said)
    for label, value in (("pointing at another copy", f'"{ELSEWHERE}"'),
                         ("not plain text", ""),
                         ("some other program", r'"C:\Tools\other.exe" --x')):
        store = FakeStore(run=value)
        run({autostart.CHOICE_KEY: True}, store)
        check(f"a value with our name {label}: left alone",
              store.run == value and store.run_deletes == 0)

    print("\n  an old entry Task Manager had switched off stays off")
    for choice in (None, True):
        store, values = FakeStore(run=f'"{INSTALLED}"', run_disabled=True), \
            {autostart.CHOICE_KEY: choice}
        said, saves = run(values, store)
        check(f"recorded {choice}: the old value goes",
              store.run is None and store.run_disabled is False)
        check(f"recorded {choice}: no shortcut is made", store.shortcut is None, said)
        check(f"recorded {choice}: off is recorded and saved",
              values[autostart.CHOICE_KEY] is False and len(saves) == 1)
    store = FakeStore(run=f'"{INSTALLED}"', run_disabled=True)
    said, saves = run({autostart.CHOICE_KEY: False}, store)
    check("already off: just the old value goes, nothing else",
          store.run is None and store.shortcut is None and not saves, said)
    store = FakeStore(run=f'"{INSTALLED}"')
    autostart.set_enabled(True, store, INSTALLED, frozen=True)
    check("ticking the box removes an old value too", store.run is None)


class FakeWinreg:
    """Just enough of winreg for StartupStore, over in-memory dicts."""
    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ, REG_EXPAND_SZ, REG_BINARY = 1, 2, 3

    def __init__(self, keys) -> None:
        self.keys = {name: dict(values) for name, values in keys.items()}

    class _Key:
        def __init__(self, values) -> None:
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def OpenKey(self, _root, sub, *_rest):                  # noqa: N802
        if sub not in self.keys:
            raise FileNotFoundError(sub)
        return self._Key(self.keys[sub])

    def QueryValueEx(self, key, name):                      # noqa: N802
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name]

    def DeleteValue(self, key, name):                       # noqa: N802
        if name not in key.values:
            raise FileNotFoundError(name)
        del key.values[name]


def real_store() -> None:
    print("\nThe real store, in a temporary folder with a fake registry")
    folder = TEMP / "Startup"
    folder.mkdir()
    name = autostart.SHORTCUT_NAME
    reg = FakeWinreg({
        autostart.APPROVED_FOLDER_KEY: {name: (OFF, 3), "Someone else.lnk": (OFF, 3)},
        autostart.RUN_KEY: {autostart.VALUE_NAME: (f'"{INSTALLED}"', FakeWinreg.REG_SZ),
                            "OneApp": ("x.exe", FakeWinreg.REG_SZ)},
        autostart.APPROVED_RUN_KEY: {autostart.VALUE_NAME: (ON, 3), "OneApp": (OFF, 3)},
    })
    store = autostart.StartupStore(folder, reg)
    check("the shortcut would go in the given folder, named The Ox Tracker.lnk",
          store.path() == folder / "The Ox Tracker.lnk")
    check("nothing there yet", store.read() is None)
    store.write(INSTALLED, clear_flag=False)
    check("a real .lnk file is written", store.path().is_file())
    back = store.read()
    check("and reads back exactly: exe, --autostart, its folder", back == RIGHT, str(back))
    check("a start-up write leaves Task Manager's 'off' in place",
          store.disabled_by_windows() is True)
    store.write(INSTALLED)
    check("ticking the box clears only our flag",
          name not in reg.keys[autostart.APPROVED_FOLDER_KEY]
          and "Someone else.lnk" in reg.keys[autostart.APPROVED_FOLDER_KEY])
    check("which reads as enabled", autostart.is_enabled(store, INSTALLED))

    # Windows may keep a Program Files target as %ProgramFiles%\...; that is
    # still this exe, and must not be taken for a wrong shortcut.
    pythoncom, _shell, link = store._shell_link()
    link.SetPath(r"%ProgramFiles%\The Ox Tracker\The Ox Tracker.exe")
    link.SetArguments("--autostart")
    link.SetWorkingDirectory(r"%ProgramFiles%\The Ox Tracker")
    link.QueryInterface(pythoncom.IID_IPersistFile).Save(str(store.path()), 0)
    back = store.read()
    check("a shortcut stored as %ProgramFiles%\\... reads as this exe",
          autostart.is_correct(back, INSTALLED), str(back))
    said, _ = run({autostart.CHOICE_KEY: True}, store)
    check("so start-up calls it correct and leaves it", "is correct" in said, said)

    # Reading a shortcut needs pywin32's win32timezone, imported from C where
    # PyInstaller cannot see it; the installed app lacked it and could not
    # read its own shortcut. Prove the need, and that the build bundles it.
    # A fresh process, because pywin32 keeps the module once it has loaded it.
    import subprocess                            # noqa: PLC0415
    probe = (
        "import sys; sys.modules['win32timezone'] = None\n"
        "sys.path.insert(0, sys.argv[2])\n"
        "from the_ox import autostart\n"
        "class R:\n"
        "    HKEY_CURRENT_USER = 1\n"
        "    def OpenKey(self, *a): raise FileNotFoundError()\n"
        "s = autostart.StartupStore(sys.argv[1], R())\n"
        "print(s.read() == autostart.Shortcut('', '', ''))\n")
    blocked = subprocess.run(
        [sys.executable, "-c", probe, str(folder),
         str(pathlib.Path(__file__).resolve().parent.parent)],
        capture_output=True, text=True, timeout=60).stdout.strip()
    check("without win32timezone a shortcut cannot be read", blocked == "True", blocked)
    spec = (pathlib.Path(__file__).resolve().parent.parent / "packaging" / "the-ox.spec")
    check("so the build spec bundles it",
          'hiddenimports=["win32timezone"]' in spec.read_text(encoding="utf-8"))
    check("and with it, the same shortcut reads fine", autostart.is_correct(store.read(), INSTALLED))

    store.path().write_bytes(b"not a shortcut at all")
    damaged = store.read()
    check("a damaged file reads as there but unreadable",
          autostart.is_unreadable(damaged) and not autostart.is_correct(damaged, INSTALLED),
          str(damaged))
    said, _ = run({autostart.CHOICE_KEY: True}, store)
    check("and start-up leaves it exactly as it was",
          store.path().read_bytes() == b"not a shortcut at all", said)
    check("saying it could not be read", "could not be read" in said, said)
    check("the same start-up removed our old Run value and its flag, nothing else",
          autostart.VALUE_NAME not in reg.keys[autostart.RUN_KEY]
          and autostart.VALUE_NAME not in reg.keys[autostart.APPROVED_RUN_KEY]
          and "OneApp" in reg.keys[autostart.RUN_KEY]
          and "OneApp" in reg.keys[autostart.APPROVED_RUN_KEY], said)

    reg.keys[autostart.RUN_KEY][autostart.VALUE_NAME] = ("%x%", FakeWinreg.REG_EXPAND_SZ)
    check("an old value that is not plain text reads as damaged", store.old_run_value() == "")
    store.delete()
    check("unticking deletes the file", not store.path().exists())
    store.write(INSTALLED)
    check("and ticking again after that writes a good one", store.read() == RIGHT)
    store.delete()

    synced = autostart.StartupStore(TEMP / "Dropbox" / "Startup", reg)
    try:
        synced.write(INSTALLED)
        check("a Startup folder inside a synced folder is refused", False)
    except OSError as exc:
        check("a Startup folder inside a synced folder is refused, as OSError",
              not synced.path().exists(), str(exc))


def tray_item(app) -> None:
    print("\nThe tray checkbox")
    real_available = autostart.is_available
    store = FakeStore()

    values: dict = settings.load()
    before = dict(values)
    icon = tray.Tray(values, FakeManager())
    action = icon._start_action
    check("from source it is greyed out", not action.isEnabled())
    check("and says why", "installed app only" in action.text(), action.text())
    check("and is not ticked", not action.isChecked())
    check("and nothing is recorded", values == before)
    icon.icon.hide()

    autostart.is_available = lambda frozen=None, exe=None: True
    autostart.StartupStore = lambda *a, **k: store
    try:
        icon = tray.Tray(values, FakeManager())
        action = icon._start_action
        check("installed, it can be used", action.isEnabled())
        check("it starts unticked when there is no shortcut", not action.isChecked())
        action.setChecked(True)
        app.processEvents()
        check("ticking it creates the shortcut",
              store.shortcut == autostart.expected(sys.executable), str(store.shortcut))
        check("and records the choice as on",
              values[autostart.CHOICE_KEY] is True
              and settings.load()[autostart.CHOICE_KEY] is True)
        action.setChecked(False)
        app.processEvents()
        check("unticking it removes the shortcut", store.shortcut is None)
        check("and records the choice as off",
              values[autostart.CHOICE_KEY] is False
              and settings.load()[autostart.CHOICE_KEY] is False)
        store.shortcut = autostart.expected(sys.executable)
        store.disabled = True
        icon._menu.aboutToShow.emit()
        check("switched off in Task Manager shows as off", not action.isChecked())
        writes = store.writes
        store.disabled = False
        icon._menu.aboutToShow.emit()
        check("a change made elsewhere is picked up", action.isChecked())
        check("and syncing the tick never writes", store.writes == writes)
        icon.icon.hide()
    finally:
        autostart.is_available = real_available
        autostart.StartupStore = ForbiddenStore


def main() -> int:
    real_store_class, real_folder = autostart.StartupStore, autostart.startup_folder
    autostart.StartupStore = ForbiddenStore
    autostart.startup_folder = forbidden_folder
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    try:
        module_level()
        startup_choice()
        migration()
        # The real class, but only ever with a temporary folder and fake winreg.
        autostart.StartupStore = real_store_class
        real_store()
        autostart.StartupStore = ForbiddenStore
        tray_item(app)
    except RealThingTouched as exc:
        check("the real Startup folder and registry were never touched", False, str(exc))
    else:
        check("the real Startup folder and registry were never touched", True)
    finally:
        autostart.StartupStore, autostart.startup_folder = real_store_class, real_folder

    print()
    if fails:
        for name in fails:
            print("  FAILED:", name)
        return 1
    print("all Start with Windows tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
