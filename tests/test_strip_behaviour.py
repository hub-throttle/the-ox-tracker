r"""Behaviour tests for the taskbar strip.

Checks the things that cannot be seen in a screenshot: that the strip comes
back on top after something draws over it, that it never steals focus, and
that it gets out of the way of a full-screen app and returns afterwards.

Opens real windows on real monitors, so it needs a desktop session.
"""
import ctypes, sys
from ctypes import wintypes
ctypes.windll.shcore.SetProcessDpiAwareness(2)
from PySide6 import QtCore, QtGui, QtWidgets
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from the_ox import monitors, settings, strip
from the_ox.providers.common import OK, Bucket, Reading

u = ctypes.windll.user32
HWND_BOTTOM, HWND_TOPMOST = 1, -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10

app = QtWidgets.QApplication(sys.argv)
app.setQuitOnLastWindowClosed(False)
fake = [Reading("Claude", OK, [Bucket("5 hour", 31.0), Bucket("Weekly", 14.0)]),
        Reading("ChatGPT", OK, [Bucket("5 hour", 0.0), Bucket("Weekly", 19.0)]),
        Reading("Grok", OK, [Bucket("Weekly", 56.0)]),
        Reading("Grok Bot", "open_app", [])]
values = settings.load()
mgr = strip.StripManager(values)
mgr.set_readings(fake); mgr.rebuild()
results = []

def is_topmost(hwnd):
    return bool(u.GetWindowLongW(hwnd, -20) & 0x8)   # WS_EX_TOPMOST

def step1():
    print("TEST 1: strip recovers when something draws over it")
    win = next(iter(mgr._windows.values()))
    hwnd = int(win.winId())
    focus_before = u.GetForegroundWindow()
    u.SetWindowPos(hwnd, HWND_BOTTOM, 0,0,0,0, SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE)
    print(f"  pushed to bottom. topmost flag now: {is_topmost(hwnd)}")
    def check():
        back = is_topmost(hwnd)
        focus_after = u.GetForegroundWindow()
        ours = {int(w.winId()) for w in mgr._windows.values()}
        stole = focus_after in ours
        print(f"  after 1.5s: topmost={back}  foreground_is_ours={stole}"
              f"  (foreground changed: {focus_before != focus_after})")
        results.append(("recovers topmost", back))
        results.append(("never takes focus", not stole))
        step2()
    QtCore.QTimer.singleShot(1500, check)

def step2():
    print("\nTEST 2: full-screen app hides the strip")
    target = next(t for t in mgr.targets() if not t.is_internal)
    g = target.taskbar.screen.geometry()
    global full
    full = QtWidgets.QWidget()
    full.setWindowTitle("fullscreen test")
    full.setStyleSheet("background:#101418")
    full.setGeometry(g)
    full.showFullScreen()
    full.raise_(); full.activateWindow()
    def check():
        detected = monitors.fullscreen_app_on(target.taskbar, mgr._own_handles())
        print(f"  fullscreen detected on {target.label}: {detected}")
        mgr.rebuild()
        win = mgr._windows.get(target.key)
        hidden = (win is None) or (not win.isVisible())
        print(f"  strip hidden there: {hidden}")
        results.append(("detects fullscreen", detected))
        results.append(("hides on fullscreen", hidden))
        full.close()
        QtCore.QTimer.singleShot(900, step3)
    QtCore.QTimer.singleShot(1200, check)

def step3():
    target = next(t for t in mgr.targets() if not t.is_internal)
    mgr.rebuild()
    win = mgr._windows.get(target.key)
    back = win is not None and win.isVisible()
    print(f"  strip returned after closing it: {back}")
    results.append(("returns after fullscreen", back))
    print("\nRESULTS")
    ok = True
    for name, passed in results:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
        ok = ok and passed
    print("\nall passed" if ok else "\nSOME FAILED")
    app.quit()

QtCore.QTimer.singleShot(1200, step1)
app.exec()
