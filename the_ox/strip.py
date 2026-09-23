r"""The taskbar strip: thin level lines drawn over the Windows 11 taskbar.

It is an always-on-top overlay rather than a child of the taskbar, because
embedding is not possible on Windows 11. See the note at the top of
monitors.py for what was tested and how it failed.

Geometry and colours follow the approved mockup: a 40px right-aligned label
in the service's tint, then stacked 46x3 level lines with the percent at the
end of each. Claude and ChatGPT get two lines, five hour on top and weekly
underneath. Grok gets one. Grok Bot shows an "open" marker instead of a
percent, because The Ox does not read its usage.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets

from . import appearance
from . import budget as budget_mod
from . import hovercard, logs, monitors, pointer, registry, settings
from .providers.common import EXPIRED, NO_LOGIN, OK, OPEN_APP, Reading

_user32 = ctypes.windll.user32

# argtypes matter here. Without them ctypes passes the HWND_TOPMOST sentinel
# as a 32-bit int, which does not sign-extend to a 64-bit HWND, so the call
# quietly does nothing and the strip never comes back on top.
_user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_uint,
]
_user32.SetWindowPos.restype = wintypes.BOOL
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
_user32.SetWindowLongW.restype = ctypes.c_long

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


_user32.WindowFromPoint.argtypes = [_POINT]
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
_user32.GetAncestor.restype = wintypes.HWND
_user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
_user32.GetWindow.restype = wintypes.HWND
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL

HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
GWL_EXSTYLE = -20
GA_ROOT = 2
GW_HWNDPREV = 3          # the window above this one in z-order
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_TOPMOST = 0x00000080, 0x08000000, 0x00000008

# Muted palette, taskbar only. The biscuit and panel use brighter colours.
# The track is lighter than the mockup's #3b3f46 so that an empty line still
# reads as a line: at 0% there is no fill, and the track is all you see.
# The status colours and the percentages they start at, the service colours,
# the budget outline's colour and the service order all come from a Look
# (appearance.py), so the Settings window can change them and its preview is
# drawn exactly as the real strip is.
TRACK = "#555b66"
GREEN, AMBER, RED = appearance.STRIP_GREEN, appearance.STRIP_AMBER, appearance.STRIP_RED
GREY = appearance.STRIP_GREY
VALUE_INK = "#e6e9ee"
# The over-budget outline is deliberately a different shape of signal from a
# line that is simply red because it is 80% or more used. That is a red FILL
# in the muted palette; this is a bright, saturated OUTLINE around the track
# with a gap of clear space, so the two never read as the same thing. Its
# colour is a setting; the tick marking today's allowance stays neutral.
BUDGET_TICK = appearance.BUDGET_TICK

# The defaults. The tray icon uses TINTS and GREEN as they are; everything
# drawn from readings goes through a Look instead.
TINTS = registry.TINTS
SHORT_NAMES = registry.SHORT_NAMES
ORDER = registry.ORDER

# Layout, in logical pixels. Grown from the mockup's metrics after seeing it
# at real size on a 48px taskbar: the labels and numbers were too small to
# read at a glance and the strip used less than half the available height.
TRACK_W, TRACK_H = 46, 4
ROW_H = 15                  # a level line's row: number, plus the budget outline
LINE_GAP = 4                # between the stacked rows of one service
SERVICE_GAP = 5             # between services; was 12, which was far too airy
LABEL_GAP = 5               # between a label and its bars
VALUE_GAP = 5               # between a bar and its percent
SIDE_PAD = 5
CLOCK_MARGIN = 16           # clear space between the strip and the clock

# Font sizes in points. The label is about 30% larger than the first cut and
# noticeably heavier; the numbers are up too.
LABEL_POINT = 9.0
VALUE_POINT = 8.0

# A login this close to expiring dims the service and changes its tooltip.
EARLY_WARNING = QtCore.QTime(0, 30)
EARLY_WARNING_SECONDS = 30 * 60


def status_colour(percent: float | None, look: "appearance.Look | None" = None) -> str:
    """Green, amber or red by the Look's thresholds (50 and 80 by default)."""
    return (look or appearance.look_from(None)).strip_status(percent)


@dataclass
class ServiceView:
    """What the strip needs to draw one service. No tokens, just numbers."""

    service: str
    lines: list[tuple[float | None, str]]   # (percent, colour) top to bottom
    budgets: list[object]                   # one per line: a Budget or None
    marker: str | None                      # "open" or "?" instead of lines
    dimmed: bool                            # login expiring soon
    stale: bool                             # expired, no login, or no data
    tooltip: str


def _format_reset(reading: Reading, index: int) -> str:
    if index >= len(reading.buckets):
        return ""
    moment = reading.buckets[index].resets_at
    if moment is None:
        return ""
    try:
        return moment.astimezone().strftime("resets %a %I:%M %p").replace(" 0", " ")
    except (OSError, OverflowError, ValueError):
        # Windows cannot put a moment before 1970 or after 3000 into local
        # time. Providers already refuse such dates; this is the backstop.
        return ""


def build_view(reading: Reading, values: dict | None = None) -> ServiceView:
    """Turn one provider reading into what the strip should show."""
    values = values if values is not None else {}
    look = appearance.look_from(values)
    service = reading.service
    seconds_left = reading.login_seconds_left
    dimmed = (
        seconds_left is not None
        and 0 < seconds_left <= EARLY_WARNING_SECONDS
        and reading.status == OK
    )

    if reading.status == OPEN_APP:
        return ServiceView(service, [], [], "open", False, False,
                           f"{service}\nCheck usage in the Grok Bot app.\nClick to open it.")

    if reading.status in (NO_LOGIN, EXPIRED) or reading.status != OK:
        detail = reading.detail or "No recent data."
        return ServiceView(service, [], [], "?", False, True,
                           f"{service}\n{detail}\nClick to open the app and sign in.")

    stale = bool(getattr(reading, "stale", False))
    lines: list[tuple[float | None, str]] = []
    budgets: list[object] = []
    for bucket in reading.buckets[:2]:
        # Stale keeps the last good numbers but greys them, so an old value
        # can never be mistaken for a fresh one.
        colour = GREY if stale else look.strip_status(bucket.percent_used)
        lines.append((bucket.percent_used, colour))
        budgets.append(None if stale else budget_mod.for_bucket(bucket, values))

    parts = [service]
    for index, bucket in enumerate(reading.buckets):
        percent = "?" if bucket.percent_used is None else f"{bucket.percent_used:g}%"
        reset = _format_reset(reading, index)
        parts.append(f"  {bucket.label}: {percent}" + (f"   {reset}" if reset else ""))
    for bucket in reading.buckets:
        pacing = None if stale else budget_mod.for_bucket(bucket, values)
        if pacing is not None:
            parts.append(budget_mod.describe(pacing))
    if stale and reading.detail:
        parts.append(reading.detail)
    if dimmed:
        parts.append(f"Open {service} to refresh")
    tooltip = "\n".join(parts)
    return ServiceView(service, lines, budgets, None, dimmed, stale, tooltip)


class StripWidget(QtWidgets.QWidget):
    """Draws the row of services. Emits which service was clicked."""

    serviceClicked = QtCore.Signal(str)
    doubleClicked = QtCore.Signal()

    # A single click opens the panel and a double click moves the biscuit, so
    # the first click of a double must not act. Qt's own interval is used, so
    # this matches whatever double-click speed Windows is set to.
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending_click: str | None = None
        self._click_timer = QtCore.QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._fire_pending_click)
        self._views: dict[str, ServiceView] = {}
        self._hide_labels = False
        self._metrics_cache: tuple[int, int] | None = None
        self._order: list[str] = []
        self._look = appearance.look_from(None)
        self._hovered: str | None = None
        self.setMouseTracking(True)
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent)
        # Qt suppresses tooltips for windows that are not active. The strip is
        # never active by design (WS_EX_NOACTIVATE), so without this no
        # tooltip ever appears, which is exactly what happened on both
        # monitors. The tooltip is also shown explicitly below rather than
        # left to Qt's hover timer, which the same inactivity suppresses.
        self.setAttribute(QtCore.Qt.WA_AlwaysShowToolTips, True)

    def set_views(self, views: dict[str, ServiceView]) -> None:
        self._views = views
        # Only the chosen services are drawn, in the chosen order, so the
        # strip shrinks or grows with the choice.
        self._order = self._look.ordered(views)
        self._metrics_cache = None
        self.setFixedWidth(self.preferred_width())
        self.update()

    def set_look(self, look: "appearance.Look") -> None:
        """New colours, thresholds or order. Views are rebuilt by the caller."""
        self._look = look
        self._order = look.ordered(self._views)
        self._metrics_cache = None
        self.setFixedWidth(self.preferred_width())
        self.update()

    def set_hide_labels(self, hide: bool) -> None:
        if hide != self._hide_labels:
            self._hide_labels = hide
            self.setFixedWidth(self.preferred_width())
            self.update()

    def _label_font(self) -> QtGui.QFont:
        font = QtGui.QFont(self.font())
        font.setPointSizeF(LABEL_POINT)
        font.setWeight(QtGui.QFont.Black)
        font.setLetterSpacing(QtGui.QFont.PercentageSpacing, 102)
        return font

    def _value_font(self) -> QtGui.QFont:
        font = QtGui.QFont(self.font())
        font.setPointSizeF(VALUE_POINT)
        font.setWeight(QtGui.QFont.Bold)
        return font

    def _metrics(self) -> tuple[int, int]:
        """(label column width, percent column width), measured not guessed.

        Measured from the actual fonts so the columns still fit after the
        sizes changed, and so they stay right at any DPI.
        """
        if self._metrics_cache is None:
            label = QtGui.QFontMetrics(self._label_font())
            value = QtGui.QFontMetrics(self._value_font())
            shown = [SHORT_NAMES[name] for name in self._order] or list(SHORT_NAMES.values())
            label_w = max(label.horizontalAdvance(text) for text in shown)
            value_w = max(
                value.horizontalAdvance("100%"),
                value.horizontalAdvance("open"),
            )
            self._metrics_cache = (label_w + 2, value_w + 2)
        return self._metrics_cache

    def _label_width(self) -> int:
        return 0 if self._hide_labels else self._metrics()[0] + LABEL_GAP

    def _service_width(self, view: ServiceView | None) -> int:
        value_w = self._metrics()[1]
        if view is not None and view.marker is not None:
            return self._label_width() + value_w
        return self._label_width() + TRACK_W + VALUE_GAP + value_w

    def preferred_width(self) -> int:
        if not self._order:
            return 1
        total = SIDE_PAD * 2
        for index, name in enumerate(self._order):
            total += self._service_width(self._views.get(name))
            if index < len(self._order) - 1:
                total += SERVICE_GAP
        return total

    def _service_spans(self) -> list[tuple[str, int, int]]:
        spans, x = [], SIDE_PAD
        for index, name in enumerate(self._order):
            width = self._service_width(self._views.get(name))
            spans.append((name, x, width))
            x += width + (SERVICE_GAP if index < len(self._order) - 1 else 0)
        return spans

    def paintEvent(self, _event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing)
        # Alpha 4, not 0. On a translucent (layered) window Windows hit-tests
        # by alpha, so a fully transparent pixel is click-through: the pointer
        # reaches the taskbar instead of the strip. That is why no tooltip
        # ever appeared, and why an occlusion probe saw the taskbar. Alpha 4
        # is invisible to the eye but solid to hit-testing.
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_Source)
        painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 4))
        painter.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)

        label_font = self._label_font()
        value_font = self._value_font()
        label_w, value_w = self._metrics()
        mid = self.height() // 2
        if not self._order:
            return

        for name, x, _width in self._service_spans():
            view = self._views.get(name)
            tint = QtGui.QColor(self._look.tint.get(name, TINTS.get(name, GREY)))
            if view is None or view.stale:
                tint = QtGui.QColor(GREY)
            if view is not None and view.dimmed:
                tint.setAlpha(140)

            if not self._hide_labels:
                painter.setFont(label_font)
                painter.setPen(tint)
                painter.drawText(
                    QtCore.QRect(x, 0, label_w, self.height()),
                    QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                    SHORT_NAMES[name],
                )
            bar_x = x + self._label_width()
            painter.setFont(value_font)

            if view is None or view.marker is not None:
                painter.setPen(QtGui.QColor(GREY) if view is None or view.stale else tint)
                painter.drawText(
                    QtCore.QRect(bar_x, 0, value_w, self.height()),
                    QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                    view.marker if view else "?",
                )
                continue

            count = max(1, len(view.lines))
            block = count * ROW_H + (count - 1) * LINE_GAP
            top = mid - block // 2
            for index, (percent, colour) in enumerate(view.lines):
                pacing = view.budgets[index] if index < len(view.budgets) else None
                row_y = top + index * (ROW_H + LINE_GAP)
                track_y = row_y + (ROW_H - TRACK_H) // 2
                track = QtGui.QColor(TRACK)
                if view.dimmed:
                    track.setAlpha(170)
                painter.fillRect(bar_x, track_y, TRACK_W, TRACK_H, track)
                if percent:
                    filled = int(round(TRACK_W * max(0.0, min(100.0, percent)) / 100.0))
                    fill = QtGui.QColor(colour)
                    if view.dimmed:
                        fill.setAlpha(150)
                    painter.fillRect(bar_x, track_y, filled, TRACK_H, fill)
                if pacing is not None:
                    # A thin tick showing where today's budget sits, and a
                    # bright outline standing clear of the track when usage
                    # has passed it.
                    mark = bar_x + int(round(
                        TRACK_W * max(0.0, min(100.0, pacing.allowed_percent)) / 100.0))
                    painter.setPen(QtGui.QPen(QtGui.QColor(BUDGET_TICK), 1))
                    painter.drawLine(mark, track_y - 2, mark, track_y + TRACK_H + 1)
                    if pacing.is_over:
                        painter.setPen(QtGui.QPen(QtGui.QColor(self._look.budget_color), 1))
                        painter.setBrush(QtCore.Qt.NoBrush)
                        painter.drawRect(bar_x - 2, track_y - 3,
                                         TRACK_W + 3, TRACK_H + 5)

                ink = QtGui.QColor(VALUE_INK)
                if view.dimmed:
                    ink.setAlpha(150)
                painter.setPen(ink)
                text = "?" if percent is None else f"{percent:g}%"
                painter.drawText(
                    QtCore.QRect(bar_x + TRACK_W + VALUE_GAP, row_y, value_w, ROW_H),
                    QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                    text,
                )

    def _service_at(self, x: int) -> str | None:
        for name, start, width in self._service_spans():
            if start <= x < start + width + SERVICE_GAP // 2:
                return name
        return None

    def service_at(self, x: int) -> str | None:
        """Which service sits at this x, in widget coordinates."""
        return self._service_at(x)

    def tooltip_for(self, name: str | None) -> str:
        view = self._views.get(name) if name else None
        return view.tooltip if view else ""

    def service_centre(self, name: str) -> int:
        """Centre of a service's column, in widget coordinates."""
        for candidate, x, width in self._service_spans():
            if candidate == name:
                return x + width // 2
        return self.width() // 2

    def _fire_pending_click(self) -> None:
        name, self._pending_click = self._pending_click, None
        if name:
            self.serviceClicked.emit(name)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            return
        name = self._service_at(int(event.position().x()))
        if not name:
            return
        self._pending_click = name
        self._click_timer.start(QtWidgets.QApplication.doubleClickInterval())

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton:
            return
        # Cancel the waiting single click: this was a double all along.
        self._click_timer.stop()
        self._pending_click = None
        self.doubleClicked.emit()


class StripWindow(QtWidgets.QWidget):
    """One overlay window, sitting over one taskbar."""

    serviceClicked = QtCore.Signal(str)
    doubleClicked = QtCore.Signal()

    def __init__(self, target: monitors.MonitorTarget):
        super().__init__(None)
        self.target = target
        self.setWindowTitle(f"The Ox Tracker: {target.label}")
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.Tool
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.strip = StripWidget(self)
        self.strip.serviceClicked.connect(self.serviceClicked)
        self.strip.doubleClicked.connect(self.doubleClicked)
        layout.addWidget(self.strip)

    def apply_window_styles(self) -> None:
        """Tool window, never takes focus, always above the taskbar."""
        hwnd = int(self.winId())
        current = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        _user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, current | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        )

    def has_topmost_style(self) -> bool:
        """Whether WS_EX_TOPMOST is set. NOT the same as being on top."""
        hwnd = int(self.winId())
        return bool(_user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)

    def is_covered(self) -> bool:
        r"""Is the taskbar currently sitting above the strip in z-order?

        Testing WS_EX_TOPMOST does not answer this: when the shell raises the
        taskbar, our window keeps that style and merely drops below the
        taskbar within the topmost band, so a style check happily reports
        "still on top" while the strip is invisible. That is exactly why the
        strip vanished and never came back.

        Walking the z-order upward from our window and looking for the
        taskbar is the check that matches what the eye sees.
        """
        hwnd = int(self.winId())
        bar = self.target.taskbar.hwnd
        walker = hwnd
        # 60 is generous: the taskbar sits within a handful of windows of us
        # in the topmost band. Walking 400 every third of a second was pure
        # cost for no extra correctness.
        for _ in range(60):
            walker = _user32.GetWindow(walker, GW_HWNDPREV)
            if not walker:
                return False            # reached the top without meeting it
            if walker == bar:
                return True             # taskbar is above us
        return False

    def reassert_topmost(self, force: bool = False) -> None:
        """Put the window back on top without stealing focus.

        SWP_NOACTIVATE plus the WS_EX_NOACTIVATE style means the strip never
        takes focus from whatever the user is typing in. `force` is accepted
        for callers that want to re-assert unconditionally; the call is cheap
        either way and is never skipped based on window style.
        """
        hwnd = int(self.winId())
        _user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def place(self) -> None:
        """Sit directly left of the clock, at the far right of the taskbar."""
        bar = self.target.taskbar
        ratio = bar.dpr
        width_physical = int(self.strip.preferred_width() * ratio)
        right_edge = self.target.clock_left
        if right_edge is None:
            right_edge = bar.rect[2] - int(CLOCK_MARGIN * ratio)
        left = right_edge - width_physical - int(CLOCK_MARGIN * ratio)

        geo = bar.screen_geometry
        logical_x = geo.x() + (left - geo.x()) / ratio
        logical_y = geo.y() + (bar.rect[1] - geo.y()) / ratio
        self.setGeometry(
            int(round(logical_x)),
            int(round(logical_y)),
            self.strip.preferred_width(),
            int(round(bar.height / ratio)),
        )
        self.strip.setFixedHeight(int(round(bar.height / ratio)))


class StripManager(QtCore.QObject):
    """Keeps one strip per enabled monitor, and keeps them correct.

    Handles monitors being plugged in or unplugged, resolution and DPI
    changes, full-screen apps, the taskbar repainting over the strip, and the
    three collision behaviours, all without a restart.
    """

    # (service, monitor key) so the panel can open on the screen that was
    # clicked rather than always on the primary one.
    serviceClicked = QtCore.Signal(str, str)
    stripDoubleClicked = QtCore.Signal(str)

    TOPMOST_INTERVAL_MS = 330
    RECHECK_INTERVAL_MS = 5000

    def __init__(self, values: dict, parent=None):
        super().__init__(parent)
        self._settings = values
        self._windows: dict[str, StripWindow] = {}
        self._views: dict[str, ServiceView] = {}
        self._targets: list[monitors.MonitorTarget] = []
        self._readings_cache: list[Reading] = []    # to redraw on a new Look

        # A burst of screen changes (a monitor waking, a sign-in bringing the
        # displays up in stages) becomes one rebuild, 400 ms after the last.
        self._rebuild_soon = QtCore.QTimer(self)
        self._rebuild_soon.setSingleShot(True)
        self._rebuild_soon.setInterval(400)
        self._rebuild_soon.timeout.connect(self.rebuild)

        app = QtGui.QGuiApplication.instance()
        app.screenAdded.connect(self._on_screen_added)
        app.screenRemoved.connect(self._on_screens_changed)
        # Each screen's own signals are connected once, when it first
        # appears. They used to be connected again on every change, so each
        # change after that set off one more rebuild than the last.
        for screen in app.screens():
            self._watch(screen)

        self._topmost = QtCore.QTimer(self)
        self._topmost.timeout.connect(self._tick_topmost)
        self._topmost.start(self.TOPMOST_INTERVAL_MS)

        self._layout_signature: tuple | None = None
        self._card: hovercard.HoverCard | None = None
        self._hovered: str | None = None
        self._recheck = QtCore.QTimer(self)
        self._recheck.timeout.connect(self._tick_recheck)
        self._recheck.start(self.RECHECK_INTERVAL_MS)
        # The hover card has its own timer, paced by where the pointer is:
        # twice a second while it is nowhere near a strip, ten times once
        # it comes close. See pointer.py.
        self._hover_timer = QtCore.QTimer(self)
        self._hover_timer.timeout.connect(self._tick_hover)
        self._hover_timer.start(pointer.SLOW_MS)

    def _watch(self, screen: QtGui.QScreen) -> None:
        screen.geometryChanged.connect(self._on_screens_changed)
        screen.physicalDotsPerInchChanged.connect(self._on_screens_changed)
        screen.logicalDotsPerInchChanged.connect(self._on_screens_changed)

    def _on_screen_added(self, screen: QtGui.QScreen) -> None:
        self._watch(screen)                # the new screen only, once
        self._on_screens_changed()

    def _on_screens_changed(self, *_args) -> None:
        self._rebuild_soon.start()         # restarts if one is already due

    # ----- what the strips show -------------------------------------------
    def refresh_look(self) -> None:
        """Settings were saved: redraw every strip with the new Look.

        Colours, thresholds, the budget warning and the order all come from
        settings, so the views are rebuilt from the last readings too.
        """
        self._views = self._build_views(self._readings_cache)
        self._push_views()

    def set_readings(self, readings: list[Reading]) -> None:
        self._readings_cache = list(readings)
        self._views = self._build_views(readings)
        self._push_views()

    def _build_views(self, readings) -> dict[str, ServiceView]:
        """One view per reading, each on its own.

        A reading that cannot be drawn becomes a "?" for that service and a
        log line; it no longer stops the strip from showing the others.
        """
        views: dict[str, ServiceView] = {}
        for reading in readings:
            try:
                views[reading.service] = build_view(reading, self._settings)
            except Exception:           # noqa: BLE001 - the others still draw
                logs.get_logger().exception(
                    "the strip could not show %s's reading", reading.service)
                views[reading.service] = ServiceView(
                    reading.service, [], [], "?", False, True,
                    f"{reading.service}\nThis reading could not be shown.")
        return views

    def _push_views(self) -> None:
        """Hand the current views and look to every strip, each on its own."""
        look = appearance.look_from(self._settings)
        for key, window in list(self._windows.items()):
            try:
                window.strip.set_look(look)
                window.strip.set_views(self._views)
                window.place()
            except Exception:           # noqa: BLE001 - the other strips still update
                logs.get_logger().exception(
                    "the strip on %s could not be updated", window.target.label)

    def targets(self) -> list[monitors.MonitorTarget]:
        return list(self._targets)

    def window_for(self, target) -> "StripWindow | None":
        """The strip window on one monitor, or None if it is not showing."""
        key = getattr(target, "key", target)
        return self._windows.get(key)

    # ----- window lifecycle ------------------------------------------------
    def _own_spans(self) -> tuple[tuple[int, int], ...]:
        """Physical x ranges our own strips occupy, so measuring skips them."""
        spans = []
        for window in self._windows.values():
            if not window.isVisible():
                continue
            ratio = window.target.taskbar.dpr
            geo = window.geometry()
            screen_geo = window.target.taskbar.screen_geometry
            left = screen_geo.x() + (geo.x() - screen_geo.x()) * ratio
            spans.append((int(left) - 2, int(left + geo.width() * ratio) + 2))
        return tuple(spans)

    def _own_handles(self) -> frozenset[int]:
        """Our overlay handles, so they never count as a full-screen app."""
        return frozenset(int(w.winId()) for w in self._windows.values())

    def rebuild(self) -> None:
        """Re-discover monitors and bring the set of windows back in line.

        Every strip, new or already there, is handed the current readings.
        An existing strip used to keep whatever it last had, so one that
        had missed an update, or was built before the first readings, stayed
        blank until the next check came round.

        The layout fingerprint is saved only once all of this has worked, so
        a rebuild that fails is tried again on the next check instead of
        being taken as done.
        """
        targets = monitors.discover(ignore=self._own_spans())
        self._targets = targets
        wanted = {
            t.key: t
            for t in targets
            if settings.strip_enabled(self._settings, t.key, t.is_internal)
        }

        for key in list(self._windows):
            if key not in wanted:
                window = self._windows.pop(key)
                window.hide()
                window.deleteLater()

        mode = self._settings.get("collision_mode", settings.COLLISION_STAY)
        look = appearance.look_from(self._settings)
        own = self._own_handles()
        failed = False
        for key, target in wanted.items():
            try:
                window = self._windows.get(key)
                if window is None:
                    window = StripWindow(target)
                    window.serviceClicked.connect(
                        lambda service, k=key: self.serviceClicked.emit(service, k))
                    window.doubleClicked.connect(
                        lambda k=key: self.stripDoubleClicked.emit(k))
                    self._windows[key] = window
                    own = self._own_handles()
                    fresh = True
                else:
                    window.target = target
                    fresh = False
                window.strip.set_look(look)
                window.strip.set_views(self._views)
                if fresh:
                    window.show()
                    window.apply_window_styles()

                if not self._apply_collision(window, target, mode):
                    continue
                window.place()
                if not monitors.fullscreen_app_on(target.taskbar, own):
                    window.show()
                    window.reassert_topmost()
                else:
                    window.hide()
            except Exception:           # noqa: BLE001 - the other strips still go up
                failed = True
                logs.get_logger().exception("the strip on %s could not be placed",
                                            target.label)
        if not failed:
            self._layout_signature = self._layout_fingerprint()

    def _apply_collision(self, window: StripWindow, target, mode: str) -> bool:
        """Decide what to do when icons reach the strip. True to keep showing.

        Stay on top is the default: the strip never moves and icons simply
        pass underneath it.
        """
        needed = window.strip.preferred_width() * target.taskbar.dpr
        crowded = target.free_width and needed > target.free_width

        if mode == settings.COLLISION_STAY:
            window.strip.set_hide_labels(False)
            return True
        if mode == settings.COLLISION_SHRINK:
            window.strip.set_hide_labels(bool(crowded))
            return True
        if mode == settings.COLLISION_HIDE:
            window.strip.set_hide_labels(False)
            if crowded:
                window.hide()
                return False
            return True
        return True

    def _tick_hover(self) -> None:
        """Show our own hover card for the service under the pointer.

        Deliberately not QToolTip. See the note at the top of hovercard.py:
        Qt suppresses and drops tooltips belonging to windows that never
        activate, and the strip is exactly such a window.
        """
        point = pointer.position()
        shown = [w for w in self._windows.values() if w.isVisible()]
        pointer.pace(self._hover_timer,
                     pointer.interval_for(point, [w.geometry() for w in shown]))
        for window in shown:
            geometry = window.geometry()
            if not geometry.contains(point):
                continue
            name = window.strip.service_at(point.x() - geometry.x())
            text = window.strip.tooltip_for(name)
            if not text:
                break
            if self._card is None:
                self._card = hovercard.HoverCard()
            anchor = geometry.x() + window.strip.service_centre(name)
            self._card.show_for(text, anchor, geometry.y(),
                                window.target.taskbar.screen_geometry)
            self._hovered = name
            return
        self._hide_card()

    def _hide_card(self) -> None:
        self._hovered = None
        if self._card is not None and self._card.isVisible():
            self._card.hide()

    def _layout_fingerprint(self) -> tuple:
        """Cheap summary of the taskbar layout. No pixel reading."""
        return tuple(
            (bar.hwnd, bar.rect, bar.screen_name, bar.dpr,
             bar.screen_geometry.getRect())
            for bar in monitors.find_taskbars()
        )

    def _tick_recheck(self) -> None:
        """Only do the expensive re-measure when something actually moved.

        Measuring reads the taskbar's pixels, which costs real time on a
        wide monitor. Running it on a blind timer stalled the UI thread and
        made the topmost timer miss its beat, so it now runs on change only.
        Full-screen state is cheap, so that is still checked every tick.

        rebuild() saves the new fingerprint itself, and only once it has
        worked: a rebuild that fails is tried again here next time, rather
        than the change being taken as dealt with.
        """
        signature = self._layout_fingerprint()
        if signature != self._layout_signature:
            self.rebuild()
            return
        self._tick_fullscreen()

    def _tick_fullscreen(self) -> None:
        """Hide a strip while a full-screen app covers its monitor.

        From a fresh list of taskbars every time, never the one the last
        rebuild kept. That old list held the screens as they were then, and
        after a sign-out or a monitor going to sleep it pointed at screens Qt
        had already deleted: this raised every five seconds, for as long as
        the app kept running.
        """
        own = self._own_handles()
        bars = monitors.find_taskbars()
        busy = monitors.fullscreen_monitors(bars, own)     # one pass for all
        for window in self._windows.values():
            covered = window.target.taskbar.hwnd in busy
            if covered and window.isVisible():
                window.hide()
            elif not covered and not window.isVisible():
                window.show()
                window.reassert_topmost(force=True)

    def _tick_topmost(self) -> None:
        """Bring the strip back whenever something is drawn over it.

        Runs three times a second and asks Windows what actually owns the
        pixel under the strip, so clicking the taskbar, pressing the Windows
        key, opening Start or a flyout, and switching apps all recover within
        well under a second. The hover card is not checked here; it has its
        own timer, which slows down when the pointer is far away.
        """
        log = logs.get_logger()
        for window in self._windows.values():
            if not window.isVisible():
                continue
            # The monitor by position ("Left monitor 2560"), never by the
            # key, which carries the model name the monitor reports.
            where = window.target.label
            hwnd = int(window.winId())
            if not _user32.IsWindowVisible(hwnd):
                # Something hid our window outright. Put it back.
                log.info("strip on %s was hidden by the shell; reshowing", where)
                window.show()
                window.reassert_topmost(force=True)
                continue
            # Unconditional. SetWindowPos with NOMOVE|NOSIZE|NOACTIVATE on a
            # window that is already on top is a no-op and does not flicker,
            # and any conditional guard here is what let the strip stay
            # buried after the shell raised the taskbar.
            was_covered = window.is_covered()
            window.reassert_topmost(force=True)
            if was_covered and window.is_covered():
                log.info("strip on %s still behind the taskbar after re-assert", where)

    def target_for(self, key: str):
        """The MonitorTarget for a key, or None if that monitor is gone."""
        return next((t for t in self._targets if t.key == key), None)

    def set_monitor_enabled(self, key: str, enabled: bool) -> None:
        settings.set_strip_enabled(self._settings, key, enabled)
        settings.save(self._settings)
        self.rebuild()

    def set_collision_mode(self, mode: str) -> None:
        if mode in settings.COLLISION_MODES:
            self._settings["collision_mode"] = mode
            settings.save(self._settings)
            self.rebuild()

    def hide_all(self) -> None:
        self._hide_card()
        for window in self._windows.values():
            window.hide()
