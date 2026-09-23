r"""Does the strip come back after the things a person actually does?

The earlier behaviour test pushed the window to the bottom of the z-order
with SetWindowPos, which clears WS_EX_TOPMOST. Real shell activity does not
clear that style; it just reorders windows inside the topmost band. So the
old test passed while the strip vanished in real use.

This test drives real mouse and keyboard input instead: clicking the taskbar,
pressing the Windows key, opening the notification flyout and switching apps.
After each one it asks Windows which window owns the pixel in the middle of
the strip, which is the only check that matches what the eye sees.

It moves the pointer and presses keys on the real desktop, then puts the
pointer back. Run it when you are not mid-sentence in something.
"""
from __future__ import annotations

import ctypes
import pathlib
import sys
from ctypes import wintypes

from PySide6 import QtCore, QtWidgets

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from the_ox import logs, monitors, settings, strip           # noqa: E402
from the_ox.providers.common import OK, OPEN_APP, Bucket, Reading   # noqa: E402

# No test writes to the real log. logs.set_path_override does for the
# log what settings.set_path_override does for settings.json.
import tempfile
logs.set_path_override(
    pathlib.Path(tempfile.mkdtemp(prefix="theox-test-")) / "the-ox.log")

_user32 = ctypes.windll.user32
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]

MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
KEYEVENTF_KEYUP = 0x0002
VK_LWIN, VK_ESCAPE, VK_MENU, VK_TAB, VK_N = 0x5B, 0x1B, 0x12, 0x09, 0x4E

SAMPLE = [
    Reading("Claude", OK, [Bucket("5 hour", 31.0), Bucket("Weekly", 14.0)]),
    Reading("ChatGPT", OK, [Bucket("5 hour", 0.0), Bucket("Weekly", 19.0)]),
    Reading("Grok", OK, [Bucket("Weekly", 56.0)]),
    Reading("Grok Bot", OPEN_APP, []),
]


def cursor() -> tuple[int, int]:
    point = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def click(x: int, y: int) -> None:
    _user32.SetCursorPos(x, y)
    _user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    _user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def tap(vk: int) -> None:
    _user32.keybd_event(vk, 0, 0, 0)
    _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def combo(modifier: int, vk: int) -> None:
    _user32.keybd_event(modifier, 0, 0, 0)
    tap(vk)
    _user32.keybd_event(modifier, 0, KEYEVENTF_KEYUP, 0)


def main() -> int:
    logs.setup(console=False)
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    manager = strip.StripManager(settings.load())
    manager.set_readings(SAMPLE)
    manager.rebuild()

    if not manager._windows:
        print("No strips were created; nothing to test.")
        return 1

    home = cursor()
    results: list[tuple[str, bool]] = []
    steps = []

    def check(label: str) -> None:
        ok = True
        for key, window in manager._windows.items():
            short = key.split("|")[0]
            visible = window.isVisible() and bool(_user32.IsWindowVisible(int(window.winId())))
            covered = window.is_covered()
            good = visible and not covered
            ok = ok and good
            print(f"    {short:<16} visible={visible!s:<5} covered={covered!s:<5} "
                  f"{'OK' if good else 'BAD'}")
        results.append((label, ok))

    def step(label: str, action, settle_ms: int = 1000):
        def run():
            print(f"\n{label}")
            action()
            QtCore.QTimer.singleShot(settle_ms, lambda: (check(label), advance()))
        steps.append(run)

    def advance():
        if steps:
            steps.pop(0)()
        else:
            finish()

    # The actions reported from real use, in order.
    target = next(t for t in manager.targets() if not t.is_internal)
    bar = target.taskbar
    empty_x = bar.rect[0] + 150          # empty stretch at the left of the bar
    empty_y = (bar.rect[1] + bar.rect[3]) // 2

    step("baseline, nothing touched", lambda: None, 800)
    step("click an empty part of the taskbar", lambda: click(empty_x, empty_y))
    step("press the Windows key (opens Start)", lambda: tap(VK_LWIN))
    step("press Escape (closes Start)", lambda: tap(VK_ESCAPE))
    step("open the notification flyout (Win+N)", lambda: combo(VK_LWIN, VK_N))
    step("press Escape (closes the flyout)", lambda: tap(VK_ESCAPE))
    step("switch apps (Alt+Tab)", lambda: combo(VK_MENU, VK_TAB))

    def finish():
        _user32.SetCursorPos(*home)
        print("\nOwn memory use")
        try:
            import os

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            psapi = ctypes.windll.psapi
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            if not psapi.GetProcessMemoryInfo(
                    kernel32.GetCurrentProcess(),
                    ctypes.byref(counters), counters.cb):
                raise OSError("GetProcessMemoryInfo failed")
            print(f"    working set      {counters.WorkingSetSize // (1024*1024)} MB")
            print(f"    peak working set {counters.PeakWorkingSetSize // (1024*1024)} MB")
            print(f"    commit           {counters.PagefileUsage // (1024*1024)} MB")
        except Exception as exc:       # noqa: BLE001
            print("    could not read:", type(exc).__name__)

        print("\nRESULTS")
        failed = [name for name, ok in results if not ok]
        for name, ok in results:
            print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        manager.hide_all()
        print("\nall passed" if not failed else f"\n{len(failed)} FAILED")
        QtCore.QTimer.singleShot(200, lambda: app.exit(0 if not failed else 1))

    QtCore.QTimer.singleShot(1500, advance)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
