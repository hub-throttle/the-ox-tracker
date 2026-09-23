r"""The Ox Tracker: the tray icon and its menu.

One small icon, as the spec asks. Everything else lives behind this menu:
which monitors show the strip, what happens when taskbar icons reach it,
opening apps that need a login, and quitting.

The Ox Tracker never signs in for the user. "Open anything that needs a login" only
starts the official apps.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import autostart, launcher, settings
from .providers.common import OK, OPEN_APP
from .strip import GREEN, ORDER, TINTS, TRACK

APP_TITLE = "The Ox Tracker"

COLLISION_LABELS = (
    (settings.COLLISION_STAY, "Stay on top"),
    (settings.COLLISION_SHRINK, "Shrink (drop labels)"),
    (settings.COLLISION_HIDE, "Hide and use biscuit"),
)


def make_icon() -> QtGui.QIcon:
    """A small mark in the same language as the strip: two level lines.

    The tray keeps this drawing. The bull (appicon.py) is the application
    icon everywhere else.
    """
    pixmap = QtGui.QPixmap(32, 32)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    painter.setBrush(QtGui.QColor("#23262c"))
    painter.setPen(QtCore.Qt.NoPen)
    painter.drawRoundedRect(1, 1, 30, 30, 7, 7)
    for index, (fill, colour) in enumerate(((0.62, GREEN), (0.34, TINTS["Claude"]))):
        y = 11 + index * 8
        painter.setBrush(QtGui.QColor(TRACK))
        painter.drawRoundedRect(6, y, 20, 4, 2, 2)
        painter.setBrush(QtGui.QColor(colour))
        painter.drawRoundedRect(6, y, int(20 * fill), 4, 2, 2)
    painter.end()
    return QtGui.QIcon(pixmap)


class Tray(QtCore.QObject):
    """Owns the tray icon. Emits what the rest of the app should do."""

    openPanel = QtCore.Signal()
    toggleBiscuit = QtCore.Signal(bool)
    refreshNow = QtCore.Signal()
    resetLayout = QtCore.Signal()
    openSetup = QtCore.Signal()
    quitRequested = QtCore.Signal()

    def __init__(self, values: dict, manager, parent=None):
        super().__init__(parent)
        self._settings = values
        self._manager = manager
        self._readings: list = []
        self._summary: list[str] = []

        self.icon = QtWidgets.QSystemTrayIcon(make_icon(), parent)
        self.icon.setToolTip(self.tooltip_text())
        self.icon.activated.connect(self._on_activated)

        self._menu = QtWidgets.QMenu()
        self._build_menu()
        # Re-read Start with Windows every time the menu opens, in case it
        # was changed somewhere else, such as Task Manager's Startup apps.
        self._menu.aboutToShow.connect(self._sync_start_with_windows)
        self.icon.setContextMenu(self._menu)
        self.icon.show()

    # ----- menu ------------------------------------------------------------
    def _build_menu(self) -> None:
        menu = self._menu
        menu.clear()

        menu.addAction("Open panel", self.openPanel.emit)

        self._biscuit_action = menu.addAction("Show biscuit")
        self._biscuit_action.setCheckable(True)
        self._biscuit_action.setChecked(bool(self._settings.get("biscuit_visible", True)))
        self._biscuit_action.toggled.connect(self._on_biscuit_toggled)

        menu.addAction("Refresh now", self.refreshNow.emit)
        menu.addAction("Reset size and position", self.resetLayout.emit)
        menu.addSeparator()
        settings_action = menu.addAction("Settings...", self.openSetup.emit)
        settings_action.setToolTip("Services, colours, warnings, how often it "
                                   "checks, and layout")

        self._monitor_menu = menu.addMenu("Show strip on")
        self._rebuild_monitor_menu()

        collision = menu.addMenu("When icons reach the strip")
        group = QtGui.QActionGroup(collision)
        group.setExclusive(True)
        current = self._settings.get("collision_mode", settings.COLLISION_STAY)
        for mode, label in COLLISION_LABELS:
            action = collision.addAction(label)
            action.setCheckable(True)
            action.setChecked(mode == current)
            action.triggered.connect(lambda _checked, m=mode: self._on_collision(m))
            group.addAction(action)

        menu.addSeparator()
        self._login_action = menu.addAction(
            "Open anything that needs a login", self._on_open_needing_login
        )
        self._login_action.setEnabled(False)

        # The Startup-folder shortcut is the record of this choice, so the
        # tick is read from there rather than from settings.json. From source
        # the item is greyed out: a shortcut starting python.exe would be
        # worse than none.
        self._start_action = menu.addAction(
            "Start with Windows" if autostart.is_available()
            else "Start with Windows (installed app only)")
        self._start_action.setCheckable(True)
        self._start_action.setEnabled(autostart.is_available())
        self._sync_start_with_windows()
        self._start_action.toggled.connect(self._on_start_with_windows)

        menu.addSeparator()
        menu.addAction("Quit", self.quitRequested.emit)

    def _rebuild_monitor_menu(self) -> None:
        """One checkbox per monitor, named the way a person would say it."""
        self._monitor_menu.clear()
        targets = self._manager.targets()
        if not targets:
            action = self._monitor_menu.addAction("No taskbars found")
            action.setEnabled(False)
            return
        for target in targets:
            action = self._monitor_menu.addAction(target.label)
            action.setCheckable(True)
            action.setChecked(
                settings.strip_enabled(self._settings, target.key, target.is_internal)
            )
            action.toggled.connect(
                lambda checked, key=target.key: self._manager.set_monitor_enabled(key, checked)
            )

    def set_biscuit_checked(self, shown: bool) -> None:
        """Keep the menu in step when the biscuit is closed by its own X."""
        was = self._biscuit_action.blockSignals(True)
        self._biscuit_action.setChecked(bool(shown))
        self._biscuit_action.blockSignals(was)

    def refresh_menu(self) -> None:
        """Called after monitors change so the checkbox list stays accurate."""
        self._rebuild_monitor_menu()

    # ----- state -----------------------------------------------------------
    def set_readings(self, readings: list) -> None:
        self._readings = readings
        needing = self._services_needing_login()
        self._login_action.setEnabled(bool(needing))
        summary = []
        for reading in readings:
            if reading.status == OPEN_APP:
                summary.append(f"{reading.service}: open the app")
            elif reading.status == OK and reading.buckets:
                worst = max(
                    (b.percent_used for b in reading.buckets if b.percent_used is not None),
                    default=None,
                )
                summary.append(
                    f"{reading.service}: {worst:g}% used" if worst is not None
                    else f"{reading.service}: ?"
                )
            else:
                summary.append(f"{reading.service}: needs a login")
        self._summary = summary
        self.icon.setToolTip(self.tooltip_text())

    def tooltip_text(self) -> str:
        """The tray tooltip, with "Fast checking on" while it is."""
        lines = [APP_TITLE]
        if settings.fast_checking(self._settings):
            lines.append("Fast checking on")
        lines.extend(self._summary)
        return "\n".join(lines)

    def refresh_tooltip(self) -> None:
        """Settings changed: say, or stop saying, "Fast checking on"."""
        self.icon.setToolTip(self.tooltip_text())

    def sync_from_settings(self) -> None:
        """The Settings window saved: bring every menu tick back in step."""
        self.set_biscuit_checked(bool(self._settings.get("biscuit_visible", True)))
        self._rebuild_monitor_menu()
        self._sync_start_with_windows()
        self.refresh_tooltip()

    def notify(self, text: str, seconds: int = 8) -> None:
        """A short balloon from the tray icon.

        Used when something happened that leaves nothing on screen to look
        at, so the app does not just appear to have done nothing.
        """
        self.icon.showMessage(APP_TITLE, text,
                              QtWidgets.QSystemTrayIcon.Information,
                              seconds * 1000)

    def _services_needing_login(self) -> list[str]:
        out = []
        for reading in self._readings:
            if reading.status in (OK, OPEN_APP):
                continue
            if launcher.is_available(reading.service):
                out.append(reading.service)
        return out

    # ----- handlers --------------------------------------------------------
    def _on_activated(self, reason) -> None:
        if reason == QtWidgets.QSystemTrayIcon.Trigger:
            self.openPanel.emit()

    def _on_biscuit_toggled(self, checked: bool) -> None:
        self._settings["biscuit_visible"] = bool(checked)
        settings.save(self._settings)
        self.toggleBiscuit.emit(bool(checked))

    def _on_collision(self, mode: str) -> None:
        self._manager.set_collision_mode(mode)

    def _on_open_needing_login(self) -> None:
        results = launcher.launch_all(self._services_needing_login())
        failed = [f"{k}: {v}" for k, v in results.items() if v != "launched"]
        if failed:
            self.icon.showMessage(APP_TITLE, "\n".join(failed),
                                  QtWidgets.QSystemTrayIcon.Warning, 5000)

    def _sync_start_with_windows(self) -> None:
        """Make the tick match the shortcut, without firing the toggle."""
        was = self._start_action.blockSignals(True)
        self._start_action.setChecked(autostart.is_available()
                                      and autostart.is_enabled())
        self._start_action.blockSignals(was)

    def _on_start_with_windows(self, checked: bool) -> None:
        """Create or remove the Startup-folder shortcut, and record the choice.

        This is the only place the recorded choice changes after the first
        run, and it records what actually took effect, so a failed write is
        never saved as a choice.
        """
        try:
            now_on = autostart.set_enabled(bool(checked))
        except OSError as exc:
            self.icon.showMessage(
                APP_TITLE,
                f"Could not change Start with Windows ({type(exc).__name__}).",
                QtWidgets.QSystemTrayIcon.Warning, 5000)
            now_on = None
        if now_on is not None and autostart.is_available():
            self._settings[autostart.CHOICE_KEY] = bool(now_on)
            settings.save(self._settings)
        else:
            now_on = autostart.is_enabled()
        self._sync_start_with_windows()
        if now_on != bool(checked) and autostart.is_available():
            self.icon.showMessage(
                APP_TITLE, "Start with Windows did not change.",
                QtWidgets.QSystemTrayIcon.Warning, 5000)
