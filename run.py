r"""The Ox Tracker: run every surface.

    %USERPROFILE%\.venvs\the-ox\Scripts\python.exe run.py
    %USERPROFILE%\.venvs\the-ox\Scripts\python.exe run.py --shot 4

Runs the taskbar strips, the biscuit, the corner panel and the tray icon on
live data, polling every three minutes per service, watching the login files
so a sign-in shows up within seconds, pausing while the workstation is
locked, and backing off when a service is unhappy.

This is also the entry point of the installed app: packaging/build.ps1
freezes this file into "The Ox Tracker.exe".
"""
from __future__ import annotations

# FIRST, before anything else, and above all before PySide6 or ssl is
# imported: drop every environment variable that could make Qt, PySide6 or
# OpenSSL load a plugin, a library or a configuration from somewhere else.
# They are read when those start up, so this has to come before that moment
# or it does nothing. In the installed app PyInstaller's own hook imports
# QtCore even earlier, which is harmless; hardening.py explains why.
# tests/test_startup.py fails if anything is ever moved above these lines.
from the_ox import hardening
hardening.scrub_environment()
hardening.safe_import_path()

import argparse                                  # noqa: E402
import ctypes                                    # noqa: E402
import datetime                                  # noqa: E402
import os                                        # noqa: E402
import pathlib                                   # noqa: E402
import sys                                       # noqa: E402
import time                                      # noqa: E402
from ctypes import wintypes                      # noqa: E402

# DPI awareness is NOT set here. Qt 6 already sets per-monitor v2, and setting
# it first made Qt's own call fail with "Access is denied". The packaged .exe
# declares it in packaging/the-ox.manifest instead.

from PySide6 import QtCore, QtGui, QtWidgets     # noqa: E402

from the_ox import __version__ as APP_VERSION    # noqa: E402
from the_ox import biscuit as biscuit_mod        # noqa: E402
from the_ox import appicon, autostart, hovercard, launcher, logs, monitors, panel as panel_mod, paths, pointer, poller as poller_mod, registry, settings, setup_window, single_instance, strip, tray  # noqa: E402
from the_ox.providers.common import OK, OPEN_APP              # noqa: E402

APP_TITLE = "The Ox Tracker"

_user32 = ctypes.windll.user32
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short
VK_LBUTTON, VK_RBUTTON = 0x01, 0x02


def button_state(key: int) -> int:
    """GetAsyncKeyState for one mouse button.

    A function of its own, like pointer.position(), so a test can stand in
    for the mouse without pressing the real one.
    """
    return _user32.GetAsyncKeyState(key)


def drain_click_state() -> None:
    """Throw away any latched "was pressed" bit.

    GetAsyncKeyState's low bit means "pressed since you last asked", and it
    keeps latching while nobody asks. Without draining it when the panel
    opens, the very click that opened the panel is still sitting in that bit
    and closes it again on the next tick.
    """
    button_state(VK_LBUTTON)
    button_state(VK_RBUTTON)


def describe(readings) -> None:
    for reading in readings:
        buckets = ", ".join(
            f"{b.label} {b.percent_used:g}%" if b.percent_used is not None else f"{b.label} ?"
            for b in reading.buckets
        )
        flag = " STALE" if getattr(reading, "stale", False) else ""
        print(f"  {reading.service:<9} {reading.status:<9}{flag} "
              f"{buckets or reading.detail or ''}")


class Surfaces(QtCore.QObject):
    """Keeps the biscuit and the panel on top and out of the way.

    The biscuit lives as long as the app. The panel is built when it opens
    and deleted when it closes, so a closed panel takes no memory. What it
    shows, the readings and when each last answered, Claude's breakdown, the
    lock and the "Show biscuit" link, is kept here and handed to each new
    one, and its clicks come out of the signals below, so what is connected
    once keeps working for every panel.
    """

    TICK_MS = 1000

    serviceClicked = QtCore.Signal(str)
    refreshRequested = QtCore.Signal()
    showBiscuitRequested = QtCore.Signal()
    settingsRequested = QtCore.Signal()

    def __init__(self, values: dict, manager=None) -> None:
        super().__init__()
        self.settings = values
        self.manager = manager
        self.biscuit = biscuit_mod.Biscuit(values)
        self._panel: panel_mod.Panel | None = None
        self._readings: list = []
        self._updated: dict = {}
        self._breakdown: list = []
        self._locked = False
        self._biscuit_hidden = False
        self._gear_card: hovercard.HoverCard | None = None
        self._opened_at = QtCore.QElapsedTimer()
        self._button_down: dict[int, bool] = {}
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self.TICK_MS)
        # Paced by where the pointer is; see the_ox/pointer.py.
        self._pointer = QtCore.QTimer(self)
        self._pointer.timeout.connect(self._tick_pointer)
        self._pointer.start(pointer.SLOW_MS)

    # ----- the panel's life ------------------------------------------------
    @property
    def panel(self) -> panel_mod.Panel | None:
        """The panel while it is open, otherwise None."""
        return self._panel

    def panel_visible(self) -> bool:
        return self._panel is not None and self._panel.isVisible()

    def _make_panel(self) -> panel_mod.Panel:
        if self._panel is None:
            panel = panel_mod.Panel(self.settings)
            panel.set_readings(self._readings, self._updated)
            if self._breakdown:
                panel.set_breakdown(self._breakdown)
            panel.set_locked(self._locked)
            panel.set_biscuit_hidden(self._biscuit_hidden)
            panel.serviceClicked.connect(self.serviceClicked)
            panel.refreshRequested.connect(self.refreshRequested)
            panel.showBiscuitRequested.connect(self.showBiscuitRequested)
            panel.settingsRequested.connect(self.settingsRequested)
            panel.closeRequested.connect(self.close_panel)
            self._panel = panel
        return self._panel

    def close_panel(self) -> None:
        """Close the panel and let it go: deleted, not just hidden."""
        panel, self._panel = self._panel, None
        self._hide_gear_tip()
        if panel is not None:
            panel.hide()
            panel.deleteLater()

    # ----- what the panel shows, kept for the next one ---------------------
    def set_panel_readings(self, readings) -> None:
        self._readings = list(readings)
        panel_mod.note_updated(self._updated, self._readings)
        if self._panel is not None:
            self._panel.set_readings(self._readings, self._updated)

    def set_panel_breakdown(self, rows) -> None:
        self._breakdown = list(rows)
        if self._panel is not None:
            self._panel.set_breakdown(self._breakdown)

    def set_panel_locked(self, locked: bool) -> None:
        self._locked = bool(locked)
        if self._panel is not None:
            self._panel.set_locked(self._locked)

    def set_panel_biscuit_hidden(self, hidden: bool) -> None:
        self._biscuit_hidden = bool(hidden)
        if self._panel is not None:
            self._panel.set_biscuit_hidden(self._biscuit_hidden)

    def refresh_panel_look(self) -> None:
        if self._panel is not None:
            self._panel.refresh_look()

    def adjust_panel_size(self) -> None:
        if self._panel is not None:
            self._panel.adjust_size()

    # ----- pointer: biscuit hover, the gear's tip, closing an unpinned panel
    def _our_windows(self) -> list[QtCore.QRect]:
        """Every rectangle that belongs to us, so a click there is not 'outside'."""
        rects = []
        if self.biscuit.isVisible():
            rects.append(self.biscuit.geometry())
        if self.manager is not None:
            for window in self.manager._windows.values():
                if window.isVisible():
                    rects.append(window.geometry())
        return rects

    def _begin_outside_watch(self) -> None:
        """Start the grace period and forget any press that came before it."""
        drain_click_state()
        self._button_down = {
            key: bool(button_state(key) & 0x8000)
            for key in (VK_LBUTTON, VK_RBUTTON)
        }
        self._opened_at.restart()

    def _button_clicked(self) -> bool:
        """Was a mouse button pressed since the last tick?

        Two signals, because neither alone is enough. The low bit of
        GetAsyncKeyState latches a press even if the button is already back
        up, which catches a quick click between two ticks. The high bit is
        the button's current state, and an up-to-down edge catches a press
        being held. A click held about a tenth of a second, which is what a
        real one is, shows up on both.
        """
        pressed = False
        for key in (VK_LBUTTON, VK_RBUTTON):
            value = button_state(key)
            if value & 1:
                pressed = True
            down = bool(value & 0x8000)
            if down and not self._button_down.get(key, False):
                pressed = True
            self._button_down[key] = down
        return pressed

    def _show_gear_tip(self, gear: QtCore.QRect) -> None:
        """"Settings" over the panel's gear. The panel never takes focus, so
        Qt's own tooltips do not work there; this is the strip's hover card."""
        if self._gear_card is None:
            self._gear_card = hovercard.HoverCard()
        screen = QtGui.QGuiApplication.screenAt(gear.center()) \
            or QtGui.QGuiApplication.primaryScreen()
        self._gear_card.show_for(panel_mod.GEAR_TIP, gear.center().x(), gear.top(),
                                 screen.geometry())

    def _hide_gear_tip(self) -> None:
        if self._gear_card is not None and self._gear_card.isVisible():
            self._gear_card.hide()

    def _tick_pointer(self) -> None:
        panel = self._panel
        panel_shown = panel is not None and panel.isVisible()
        wants_hover = self.biscuit.isVisible()
        wants_outside = panel_shown and not panel.is_pinned()
        point = pointer.position()
        # Fast only near the biscuit or panel, or while an unpinned panel is
        # open: that one closes on the next click anywhere, and should do so
        # at once. Otherwise twice a second is plenty.
        ours = [self.biscuit.geometry()] if wants_hover else []
        if panel_shown:
            ours.append(panel.geometry())
        pointer.pace(self._pointer, pointer.interval_for(point, ours, busy=wants_outside))

        gear = panel.gear_global_rect() if panel_shown else QtCore.QRect()
        if gear.contains(point):
            self._show_gear_tip(gear)
        else:
            self._hide_gear_tip()
        if not wants_hover and not wants_outside:
            return                       # nothing to do, so nothing is spent

        if wants_hover:
            self.biscuit.set_hovered(self.biscuit.geometry().contains(point))

        if not wants_outside:
            return
        # The low bit of GetAsyncKeyState means "pressed since last asked", so
        # this catches a click anywhere on the desktop without a global hook.
        clicked = self._button_clicked()
        if not clicked:
            return
        if self._opened_at.isValid() and self._opened_at.elapsed() < 400:
            return                       # that was the click that opened it
        if panel.contains_global(point):
            return
        if any(rect.contains(point) for rect in self._our_windows()):
            return                       # a click on our own strip or biscuit
        self.close_panel()

    def _windows(self) -> list:
        return [self.biscuit] + ([self._panel] if self._panel is not None else [])

    def own_windows(self) -> frozenset[int]:
        return frozenset(int(w.winId()) for w in self._windows() if w.isVisible())

    def set_biscuit_visible(self, visible: bool) -> None:
        """Show or hide the biscuit and remember the choice."""
        self.settings["biscuit_visible"] = bool(visible)
        settings.save(self.settings)
        self.show_biscuit(visible)
        self.set_panel_biscuit_hidden(not visible)

    def show_biscuit(self, visible: bool) -> None:
        if visible:
            self.biscuit.adjust_size()
            self.biscuit.restore_position()
            self.biscuit.show()
            self.biscuit.apply_window_styles()
        else:
            self.biscuit.hide()

    def toggle_panel(self) -> None:
        if self.panel_visible() and not self._panel.is_pinned():
            self.close_panel()
            return
        panel = self._make_panel()
        panel.adjust_size()
        if not panel.isVisible():
            panel.restore_position()
            self._lift_panel_clear_of_biscuit()
        panel.show()
        panel.apply_window_styles()
        self._begin_outside_watch()

    def open_panel_on(self, bar) -> None:
        """Open the panel above a specific monitor's strip."""
        panel = self._make_panel()
        avoid = self.biscuit.geometry() if self.biscuit.isVisible() else None
        panel.open_above(bar, avoid)
        panel.show()
        panel.apply_window_styles()
        self._begin_outside_watch()

    def reset_layout(self) -> None:
        self.biscuit.reset_to_defaults()
        if self._panel is not None:
            self._panel.reset_to_defaults()
        else:
            # Nothing open to reset, but the saved position and size still are.
            spare = panel_mod.Panel(self.settings)
            spare.reset_to_defaults()
            spare.deleteLater()
        if self.biscuit.isVisible():
            self.biscuit.show()
        if self.panel_visible():
            self._panel.show()

    def _lift_panel_clear_of_biscuit(self) -> None:
        """Do not open the panel on top of the biscuit.

        Only applies when the panel has no remembered position of its own;
        once it has been dragged somewhere, that is where it belongs.
        """
        if self.settings.get("panel_position"):
            return
        if not self.biscuit.isVisible() or self._panel is None:
            return
        if not self._panel.geometry().intersects(self.biscuit.geometry()):
            return
        self._panel.move(self._panel.x(),
                         self.biscuit.geometry().top() - self._panel.height() - 8)

    def _tick(self, own: frozenset[int] | None = None) -> None:
        own = own if own is not None else self.own_windows()
        # One EnumWindows pass answers for both windows and every monitor.
        bars = monitors.find_taskbars()
        busy = monitors.fullscreen_monitors(bars, own)

        def covered(window) -> bool:
            bar = window.screen_taskbar()
            return bar is not None and bar.hwnd in busy

        for window in self._windows():
            if window.isVisible():
                if covered(window):
                    window.setProperty("hidden_by_fullscreen", True)
                    window.hide()
                else:
                    window.restore_topmost_if_lost()
            elif window.property("hidden_by_fullscreen") and not covered(window):
                window.setProperty("hidden_by_fullscreen", False)
                window.show()
                window.apply_window_styles()


SHOT_MAX_AGE_SECONDS = 24 * 60 * 60


def shoot_window(window, name, out) -> None:
    """Photograph one of The Ox Tracker's own windows, and nothing else.

    QWidget.grab() renders the widget itself. The old version grabbed a
    rectangle of the SCREEN around the window, which meant whatever happened
    to be behind and beside it went into the file too: a document, an inbox,
    another app's window. These files are for looking at the app, so they
    hold the app and a transparent margin, nothing from the desktop.
    """
    shot = window.grab()
    path = out / f"{name}.png"
    shot.save(str(path))
    print(f"  saved {path} ({shot.width()}x{shot.height()}, window only)")


def _is_link(path) -> bool:
    """A symbolic link, a junction, or any other reparse point."""
    try:
        return (path.is_symlink() or os.path.isjunction(path)
                or bool(getattr(os.lstat(path), "st_reparse_tag", 0)))
    except (OSError, AttributeError):
        return True                      # cannot tell: treat it as a link


def prune_shots(out) -> int:
    """Delete screenshots older than a day. They are debugging scraps.

    Only real files in the real shots folder: if the folder, or an entry in
    it, is a link or a junction, it is left alone, so this can never delete
    pictures from wherever such a link points.
    """
    cutoff = time.time() - SHOT_MAX_AGE_SECONDS
    removed = 0
    if _is_link(out):
        return 0
    try:
        entries = list(out.glob("*.png"))
    except OSError:
        return 0
    for entry in entries:
        try:
            if _is_link(entry) or not entry.is_file():
                continue
            if entry.lstat().st_mtime < cutoff:
                entry.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def capture(manager, surfaces, seconds: int) -> None:
    out = paths.ensure_data_dir() / "shots"
    out.mkdir(exist_ok=True)
    gone = prune_shots(out)
    if gone:
        print(f"  removed {gone} screenshot(s) older than a day")
    if surfaces.panel_visible():
        shoot_window(surfaces.panel, "panel", out)
    if surfaces.biscuit.isVisible():
        shoot_window(surfaces.biscuit, "biscuit", out)
    # The strips are our own windows too, so each one is rendered rather than
    # photographed off the taskbar it sits on.
    for target in manager.targets():
        if target.is_internal:
            continue
        view = manager.window_for(target)
        if view is None or not view.isVisible():
            continue
        shoot_window(view, f"strip_{target.label.replace(' ', '_')}", out)


MB_OK, MB_ICONWARNING = 0x0, 0x30


def explain_refusal(exc, show=None) -> str:
    """A plain message box for a start-up refusal to write. Returns the text.

    Shown with Windows' own MessageBoxW, because this happens before Qt, or
    anything else of the app, exists. show replaces the box for tests.
    """
    text = (f"The Ox Tracker cannot start.\n\n{exc}\n\n"
            "It will not write anything there, because files in a synced "
            "folder are copied off this PC. Nothing has been changed.")
    if show is None:
        _user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR,
                                        wintypes.LPCWSTR, wintypes.UINT]
        _user32.MessageBoxW.restype = ctypes.c_int
        show = lambda message: _user32.MessageBoxW(None, message, APP_TITLE,  # noqa: E731
                                                   MB_OK | MB_ICONWARNING)
    try:
        show(text)
    except Exception:           # noqa: BLE001 - explaining must not fail
        pass
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--shot", type=int, default=0,
                        help="screenshot every surface after N seconds, then quit")
    # Passed only by the Startup-folder shortcut (the_ox/autostart.py), so
    # the log can say that Windows, not a person, started this copy.
    parser.add_argument("--autostart", action="store_true",
                        help="started by Windows at sign-in")
    args = parser.parse_args()

    # One copy per user. Start with Windows plus a click on the shortcut
    # would otherwise run two: two tray icons, strips drawn twice, and every
    # service polled twice with the same token. A second launch leaves at
    # once. It writes one line saying so, appended and closed straight away,
    # but never sets up logging of its own, so it cannot interleave lines
    # with, or rotate the log of, the copy that is already running.
    if not single_instance.acquire():
        logs.note_second_instance()
        return 0

    # The installed app has no console, so there is nowhere for console
    # logging to go; from source it is still useful.
    try:
        logs.setup(console=sys.stderr is not None)
    except paths.UnsafeWritePath as exc:
        # The data folder looks like a synced folder, so nothing may be
        # written there, the log included. Say so plainly instead of dying
        # with a traceback, and stop.
        explain_refusal(exc)
        return 1
    logs.install_crash_handlers()
    logs.log_environment()
    log = logs.get_logger()
    if args.autostart:
        log.info("Started by Windows at sign-in")
    if hardening.removed:
        # Names only. None of these is secret, but none is needed either.
        log.info("startup: ignored environment %s", ", ".join(hardening.removed))
    if hardening.is_frozen():
        log.info("installed build %s at %s", APP_VERSION,
                 pathlib.Path(sys.executable).parent)
    else:
        for source in (pathlib.Path(__file__), pathlib.Path(strip.__file__)):
            try:
                stamp = datetime.datetime.fromtimestamp(source.stat().st_mtime)
                log.info("source %s modified %s", source.name,
                         stamp.strftime("%Y-%m-%d %H:%M:%S"))
            except OSError:
                log.warning("could not stat %s", source)

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setQuitOnLastWindowClosed(False)
    # The bull, as the icon of every window the app opens, and so of the
    # taskbar button of any window that has one (the setup screen, a message
    # box). The tray icon is set separately and stays the level lines.
    app.setWindowIcon(appicon.app_icon())
    logs.install_qt_handler()
    # What Qt actually ended up using, which is the proof that the scrub at
    # the top of this file worked: "windows", and only the app's own plugin
    # folder, whatever the environment asked for.
    log.info("Qt platform %s, plugin folders %s", app.platformName(),
             "; ".join(QtCore.QCoreApplication.libraryPaths()) or "(none)")
    app.aboutToQuit.connect(lambda: logs.log_shutdown("event loop exited"))
    for handler in log.handlers:
        handler.flush()
    print(f"{APP_TITLE}\nLog: {logs.log_path()}")

    # Decide every launch target now, while the environment is the one
    # we started in. resolve() reads this cache from here on, so a later
    # change to %USERPROFILE% cannot redirect a click.
    launcher.resolve_targets(log)
    launcher.resolve_launch_dir(log)
    # And the login files, for the same reason: neither what gets launched
    # nor what gets read may be decided by the environment at the moment of
    # a click or a poll.
    registry.resolve_login_paths(log)
    intruders = launcher.launch_dir_intruders()
    if intruders:
        log.warning("launch folder is not empty: %s", ", ".join(intruders))

    values = settings.load()
    # Start with Windows: the Startup-folder shortcut. The installed app
    # removes its old Run value, then turns it on the first time it runs, if
    # nobody has chosen yet, and records that; from then on only the tray
    # checkbox and the Settings switch change it. From source this does
    # nothing at all.
    try:
        log.info("start with windows: %s", autostart.apply_startup_choice(values))
    except OSError as exc:
        log.warning("start with windows could not be set: %s", type(exc).__name__)
    manager = strip.StripManager(values)
    surfaces = Surfaces(values, manager)
    tray_icon = tray.Tray(values, manager)
    poller = poller_mod.Poller(values)
    log.info("checking every %d min%s", int(poller.interval_seconds // 60),
             " (fast checking on)" if settings.fast_checking(values) else "")
    # Built when Settings is opened, deleted when it closes; see SettingsHolder.
    setup = setup_window.SettingsHolder(values, targets=manager.targets,
                                        readings=poller.readings)

    # A service switched on in settings.json that The Ox Tracker itself never
    # saved is not polled, and its login file is not opened, until the setup
    # screen has been shown and the choice confirmed.
    pending_keys = registry.unconfirmed_keys(values)
    if pending_keys:
        confirmed = [spec.name for spec in registry.confirmed_specs(values)]
        poller.set_enabled(confirmed)
        setup.set_unconfirmed(pending_keys)
        log.warning("settings.json switched on services this app did not save: %s",
                    ", ".join(pending_keys))

    def apply(readings) -> None:
        """New readings to every surface, each on its own.

        One surface failing, over a value it cannot draw for instance, must
        not stop the others: that is how a single bad date once froze the
        strips, the biscuit, the panel and the tray together.
        """
        def breakdown() -> None:
            for reading in readings:
                if reading.service == "Claude" and reading.breakdown:
                    surfaces.set_panel_breakdown(reading.breakdown)

        for name, update in (
                ("strips", lambda: manager.set_readings(readings)),
                ("biscuit", lambda: surfaces.biscuit.set_readings(readings)),
                ("panel", lambda: surfaces.set_panel_readings(readings)),
                ("panel breakdown", breakdown),
                ("tray", lambda: tray_icon.set_readings(readings))):
            try:
                update()
            except Exception:           # noqa: BLE001 - the others still update
                log.exception("the %s could not show the new readings", name)

    def refresh(reason: str) -> None:
        poller.refresh_all(reason)

    def refresh_soon(reason: str) -> None:
        """Nudge one service after opening its app.

        The login file watcher normally catches a sign-in within a couple of
        seconds; this is a belt-and-braces poll in case the file is written
        somewhere we are not watching.
        """
        for delay in (5, 20, 45):
            QtCore.QTimer.singleShot(delay * 1000, lambda r=reason: poller.refresh_all(r))

    def on_service(service: str, monitor_key: str = "") -> None:
        view = manager._views.get(service)
        needs_login = bool(view and view.stale)
        if view and (view.stale or view.marker):
            try:
                print(f"  opening {service} {'sign-in' if needs_login else 'app'}")
                launcher.launch(service, sign_in=needs_login)
                refresh_soon(f"{service} launch")
            except launcher.LaunchError as exc:
                # Printing this was not enough: the console is usually
                # closed, so a refused launch looked like nothing happened.
                print(f"  {exc}")
                log.warning("%s launch refused: %s", service,
                            str(exc).splitlines()[0])
                box = QtWidgets.QMessageBox()
                box.setWindowTitle(APP_TITLE)
                box.setIcon(QtWidgets.QMessageBox.Warning)
                # Plain text: the message carries paths and file names,
                # and none of it may ever be read as markup.
                box.setTextFormat(QtCore.Qt.PlainText)
                box.setText(str(exc))
                box.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
                box.exec()
            return
        # A healthy service opens the panel, on the monitor that was clicked.
        target = manager.target_for(monitor_key) if monitor_key else None
        if target is not None and not surfaces.panel_visible():
            surfaces.open_panel_on(target.taskbar)
        else:
            surfaces.toggle_panel()

    def show_biscuit_on(target=None) -> None:
        surfaces.set_biscuit_visible(True)
        tray_icon.set_biscuit_checked(True)
        if target is not None:
            surfaces.biscuit.move_to_monitor(target.taskbar)

    def on_strip_double_click(monitor_key: str) -> None:
        """Bring the biscuit to the monitor whose strip was double-clicked.

        This never opens the panel: the single click that would have done so
        is cancelled once the second click arrives.
        """
        target = manager.target_for(monitor_key)
        if target is None:
            return
        show_biscuit_on(target)
        print(f"  biscuit shown on {target.label}")

    manager.serviceClicked.connect(on_service)
    manager.stripDoubleClicked.connect(on_strip_double_click)
    surfaces.biscuit.serviceClicked.connect(on_service)
    # Whichever panel is open; see Surfaces.
    surfaces.serviceClicked.connect(on_service)
    surfaces.refreshRequested.connect(lambda: refresh("panel button"))
    surfaces.showBiscuitRequested.connect(lambda: show_biscuit_on(None))
    surfaces.settingsRequested.connect(lambda: setup.open_centred())

    tray_icon.quitRequested.connect(app.quit)
    tray_icon.refreshNow.connect(lambda: refresh("tray"))
    tray_icon.openPanel.connect(surfaces.toggle_panel)
    tray_icon.toggleBiscuit.connect(surfaces.set_biscuit_visible)

    def on_biscuit_closed() -> None:
        surfaces.set_biscuit_visible(False)
        tray_icon.set_biscuit_checked(False)
        print("  biscuit hidden (The Ox Tracker is still running)")

    surfaces.biscuit.closeRequested.connect(on_biscuit_closed)
    tray_icon.resetLayout.connect(surfaces.reset_layout)

    def apply_services(_keys=None) -> None:
        """A new choice of services, applied without a restart.

        Always the CONFIRMED services, never simply whatever settings.json
        has ticked. Pressing Done writes both lists, so after a real choice
        the two are the same. They differ only when settings.json switched
        something on by itself and the screen was closed without answering,
        and in that case the honest reading of "closed without answering" is
        that nothing new was allowed.
        """
        names = [spec.name for spec in registry.confirmed_specs(values)]
        poller.set_enabled(names)
        poller.watch_login_files()
        apply(poller.readings())
        surfaces.biscuit.adjust_size()
        surfaces.adjust_panel_size()
        manager.rebuild()
        print(f"  services: {', '.join(names) or '(none chosen)'}")

    def apply_settings() -> None:
        """Settings pressed Save or Done: every surface takes the new values.

        Colours, amber and red, the budget warning, the order, the check
        interval, the biscuit and the strips. Services are applied by
        apply_services, which the same Save triggers straight after this.
        """
        poller.set_interval_minutes(settings.poll_minutes(values))
        manager.refresh_look()
        surfaces.biscuit.refresh_look()
        surfaces.refresh_panel_look()
        visible = bool(values.get("biscuit_visible", True))
        if visible != surfaces.biscuit.isVisible():
            surfaces.show_biscuit(visible)
        surfaces.set_panel_biscuit_hidden(not visible)
        tray_icon.sync_from_settings()
        manager.rebuild()
        print(f"  settings saved; checking every "
              f"{int(poller.interval_seconds // 60)} min")

    setup.applied.connect(apply_settings)
    setup.chosen.connect(apply_services)
    tray_icon.openSetup.connect(setup.open_centred)

    poller.lockChanged.connect(surfaces.set_panel_locked)

    def on_lock(locked: bool) -> None:
        print(f"  workstation {'locked, polling paused' if locked else 'unlocked, refreshing'}")

    poller.lockChanged.connect(on_lock)
    poller.watch_login_files()

    # Before a single strip is built: on a first run, or when settings.json
    # switched something on by itself, leave the strip off on any monitor
    # whose taskbar has too little room for it. Done here rather than after
    # rebuild() so a strip never appears on a crowded taskbar and then
    # vanishes again a moment later.
    needs_setup = not registry.has_been_set_up(values) or bool(pending_keys)
    if needs_setup:
        crowded = setup.check_taskbar_room()
        for line in crowded:
            print(f"  {line}: taskbar too full for the strip, biscuit on instead")
        if crowded:
            log.info("strip left off where the taskbar is too full: %s",
                     ", ".join(crowded))

    manager.rebuild()
    if values.get("biscuit_visible", True):
        surfaces.show_biscuit(True)
    surfaces.set_panel_biscuit_hidden(not surfaces.biscuit.isVisible())

    print("\nStrips on:")
    for target in manager.targets():
        on = settings.strip_enabled(values, target.key, target.is_internal)
        print(f"  {target.label:<22} showing={on}  free={target.free_width}px")

    if needs_setup:
        if pending_keys:
            print(f"\nsettings.json switched on {', '.join(pending_keys)}, which "
                  f"The Ox Tracker did not save.\nNothing has been read for it. "
                  f"Confirm on the screen that just opened.")
        else:
            print("\nFirst run: choose which services to show.")
        setup.open_centred()

        # Wait for an answer, but treat closing the window as an answer too.
        # It used to be waited on for ever: nothing fired when the X was
        # clicked, so closing it left the app running with nothing on screen
        # and no way to tell what had happened. dismissed covers every way
        # of closing it.
        waiting = QtCore.QEventLoop()
        was_dismissed = {"yes": False}

        def on_chosen(_keys) -> None:
            waiting.quit()

        def on_dismissed() -> None:
            was_dismissed["yes"] = True
            waiting.quit()

        setup.chosen.connect(on_chosen)
        setup.dismissed.connect(on_dismissed)
        waiting.exec()
        setup.chosen.disconnect(on_chosen)
        setup.dismissed.disconnect(on_dismissed)

        if was_dismissed["yes"]:
            log.info("setup screen closed without a choice; "
                     "carrying on with the confirmed services")
            print("  setup closed without a choice; nothing new switched on")
        apply_services()

        # A first run that was closed without choosing anything leaves every
        # surface empty, and an empty screen is not an explanation.
        if not registry.confirmed_specs(values):
            tray_icon.notify("No services chosen. Open Services... from the "
                             "tray icon to choose.")

    print("Reading usage (first poll):")
    first = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(20000, first.quit)          # do not hang forever

    def first_batch(readings):
        # Readings arrive one service at a time, so wait until every one has
        # actually answered rather than quitting on the first to land.
        if readings and all(r.status != "error" or "Waiting" not in (r.detail or "")
                            for r in readings):
            first.quit()

    poller.readingsChanged.connect(first_batch)
    poller.readingsChanged.connect(apply)
    if poller.enabled():
        first.exec()
    poller.readingsChanged.disconnect(first_batch)
    describe(poller.readings())
    apply(poller.readings())

    app.aboutToQuit.connect(poller.stop)

    if args.shot:
        def finish():
            surfaces.toggle_panel()
            QtCore.QTimer.singleShot(800, lambda: (capture(manager, surfaces, args.shot),
                                                   app.quit()))
        QtCore.QTimer.singleShot(args.shot * 1000, finish)
    else:
        print("\nRunning. Tray icon has the menu. Ctrl+C here to stop.")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
