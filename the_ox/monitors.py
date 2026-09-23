r"""Finding the taskbars, their clocks, and what is covering them.

Windows 11 draws its taskbar with a XAML composition layer that paints over
any legacy child window, so The Ox cannot embed a child inside the taskbar.
Confirmed on Windows 11: SetParent into Shell_TrayWnd succeeds and the
window is never drawn, with or without forcing z-order above the XAML layer.
The strip is therefore an always-on-top overlay positioned over the taskbar.

No QScreen is kept. Qt deletes a QScreen when its monitor goes away, at a
sign-out, when a monitor is switched off, or when Windows rearranges the
displays, and a kept reference then raises "already deleted" on every use:
that was the stream of errors logged every five seconds during sign-out. A
Taskbar holds a snapshot of its screen instead (name, geometry, scale), and
anything that needs the live QScreen looks it up by name at that moment.

This module reads window geometry and screen pixels. It changes nothing.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field

from PySide6 import QtCore, QtGui

from . import logs

_user32 = ctypes.windll.user32
# Explicit argtypes throughout: without them ctypes narrows handles and
# sentinels to 32 bits on a 64-bit build and calls fail silently.
_user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.FindWindowW.restype = wintypes.HWND
_user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND,
                                  wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.FindWindowExW.restype = wintypes.HWND
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsIconic.restype = wintypes.BOOL
_user32.IsZoomed.argtypes = [wintypes.HWND]
_user32.IsZoomed.restype = wintypes.BOOL
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long

def _log():
    return logs.get_logger()


PRIMARY_CLASS = "Shell_TrayWnd"
SECONDARY_CLASS = "Shell_SecondaryTrayWnd"

# How much colour variation in a column counts as "something is drawn here".
_CONTENT_THRESHOLD = 40
# Gaps narrower than this inside a block are treated as part of that block,
# so the two lines of the clock do not read as two separate things.
_CLOCK_INTERNAL_GAP = 14


@dataclass
class Taskbar:
    """One taskbar, and a snapshot of the screen it sits on.

    The snapshot is taken when the taskbar is found. It is what every
    placement and full-screen check uses, so none of them can touch a
    QScreen that Qt has since deleted. See the module note.
    """

    hwnd: int
    rect: tuple[int, int, int, int]   # physical screen pixels
    screen_name: str
    is_primary: bool
    screen_geometry: QtCore.QRect = field(default_factory=QtCore.QRect)
    dpr: float = 1.0                  # the screen's device pixel ratio

    @classmethod
    def on_screen(cls, hwnd: int, rect, screen: QtGui.QScreen,
                  is_primary: bool) -> "Taskbar":
        """A Taskbar for a live screen, snapshotting it now."""
        return cls(hwnd, tuple(rect), screen.name() or "", is_primary,
                   QtCore.QRect(screen.geometry()), float(screen.devicePixelRatio()))

    @property
    def screen(self) -> QtGui.QScreen | None:
        """The live QScreen this bar is on, looked up now; None if it is gone.

        For the few things that need Qt's own object (grabbing pixels).
        Everything else uses screen_geometry and dpr.
        """
        return screen_named(self.screen_name, self.screen_geometry)

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]

    @property
    def key(self) -> str:
        """The same key monitor_key() gives the live screen."""
        g = self.screen_geometry
        return f"{self.screen_name}|{g.width()}x{g.height()}"


def screen_named(name: str, geometry: QtCore.QRect | None = None) -> QtGui.QScreen | None:
    """The live QScreen with this name, preferring one with this geometry.

    Two identical monitors report the same name; the geometry tells them
    apart. None if no screen by that name exists any more.
    """
    fallback = None
    for screen in QtGui.QGuiApplication.screens():
        if (screen.name() or "") != name:
            continue
        if geometry is None or screen.geometry() == geometry:
            return screen
        fallback = fallback or screen
    return fallback


def bar_bounds(bar: Taskbar) -> tuple[int, int, int, int]:
    """The bar's screen in physical pixels, from the snapshot."""
    g, ratio = bar.screen_geometry, bar.dpr
    return (g.x(), g.y(),
            g.x() + int(round(g.width() * ratio)),
            g.y() + int(round(g.height() * ratio)))


# Roughly what the strip needs between the pinned icons and the clock, with
# all four services showing. Measured: four services render at about 522
# physical pixels on a 1.0 scale monitor. Below this it either covers icons
# or does not fit, so the strip is left off and the biscuit used instead.
STRIP_MIN_FREE_PX = 520


@dataclass
class MonitorTarget:
    """A place the strip can be shown, with a name a person would recognise."""

    key: str                 # stable-ish id used in settings.json
    label: str               # "Right monitor 3440", "Laptop"
    taskbar: Taskbar
    is_internal: bool
    clock_left: int | None   # physical x of the clock's left edge
    icons_right: int | None  # physical x of the rightmost taskbar icon
    place: str = ""          # "left monitor", "laptop screen": position only

    @property
    def free_width(self) -> int:
        if self.clock_left is None:
            return 0
        left = self.icons_right if self.icons_right is not None else self.taskbar.rect[0]
        return max(0, self.clock_left - left)

    @property
    def free_width_known(self) -> bool:
        """Did the clock's left edge get measured at all?

        When it did not, nothing is known about the free space, and "unknown"
        must not be read as "none": that would switch the strip off on a
        monitor where it would have been perfectly happy.
        """
        return self.clock_left is not None

    @property
    def has_room_for_strip(self) -> bool:
        """Is there enough taskbar between the icons and the clock?"""
        if not self.free_width_known:
            return True                  # unknown, so leave it alone
        return self.free_width >= STRIP_MIN_FREE_PX


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def _screen_bounds(screen: QtGui.QScreen) -> tuple[int, int, int, int]:
    """A screen's bounds in physical pixels: (left, top, right, bottom)."""
    g = screen.geometry()
    ratio = screen.devicePixelRatio()
    return (g.x(), g.y(),
            g.x() + int(round(g.width() * ratio)),
            g.y() + int(round(g.height() * ratio)))


def _screen_for_bar(rect: tuple[int, int, int, int]) -> QtGui.QScreen | None:
    r"""Match a taskbar to its monitor by overlap area, then by width.

    Matching on the centre point alone is not safe. Right after a resume or a
    reboot the monitors come up in stages and Qt can briefly report
    provisional geometry, so a centre point can land inside the wrong
    screen's rectangle. That is how the 3440 monitor was handed the laptop's
    taskbar and reported the laptop's 21px of free space.

    A Windows taskbar is always exactly as wide as the monitor it belongs to,
    so that width match is used as a check. If nothing matches, this returns
    None and the caller skips the taskbar rather than pairing it wrongly.
    """
    best: tuple[int, QtGui.QScreen] | None = None
    bar_width = rect[2] - rect[0]

    for screen in QtGui.QGuiApplication.screens():
        left, top, right, bottom = _screen_bounds(screen)
        overlap_x = min(rect[2], right) - max(rect[0], left)
        overlap_y = min(rect[3], bottom) - max(rect[1], top)
        if overlap_x <= 0 or overlap_y <= 0:
            continue
        area = overlap_x * overlap_y
        if best is None or area > best[0]:
            best = (area, screen)

    if best is None:
        return None
    screen = best[1]
    left, _top, right, _bottom = _screen_bounds(screen)
    # Allow a pixel or two of rounding, but not a different monitor's width.
    if abs((right - left) - bar_width) > 4:
        return None
    return screen


def find_taskbars() -> list[Taskbar]:
    """Every taskbar currently on screen, primary first."""
    found: list[Taskbar] = []
    primary = _user32.FindWindowW(PRIMARY_CLASS, None)
    handles = [(primary, True)] if primary else []
    hwnd = 0
    while True:
        hwnd = _user32.FindWindowExW(0, hwnd, SECONDARY_CLASS, None)
        if not hwnd:
            break
        handles.append((hwnd, False))

    for hwnd, is_primary in handles:
        rect = _rect(hwnd)
        screen = _screen_for_bar(rect)
        if screen is None:
            # Better to show no strip on this bar than to place one using
            # another monitor's numbers. The next rebuild will retry.
            _log().warning(
                "taskbar %s at %s matched no monitor by width; skipping this pass",
                hwnd, rect,
            )
            continue
        # A monitor's model name never reaches the log; see logs.hide_words.
        logs.hide_words([screen.name()])
        found.append(Taskbar.on_screen(hwnd, rect, screen, is_primary))
    return found


def _sample_rows(top: int, height: int) -> list[int]:
    """Every row of the bar, minus a small margin at top and bottom.

    Full coverage matters: sampling only a few rows broke the clock's text
    into fragments and the measurement latched onto the last fragment rather
    than the whole block. Speed comes from scanning lazily right to left
    instead of from reading fewer rows.
    """
    return list(range(top + 4, top + height - 4))


def _block_left_edge(busy, start: int, max_gap: int) -> int:
    """Walk left from `start` to the left edge of one visual block.

    Gaps narrower than `max_gap` are treated as part of the same block, so
    the spaces inside "4:16 PM" do not end it. Anything wider ends the block.
    """
    x = start
    while x >= 0:
        if busy(x):
            x -= 1
            continue
        gap_end = x
        while x >= 0 and not busy(x) and (gap_end - x) < max_gap:
            x -= 1
        if x < 0 or not busy(x):
            return gap_end + 1
    return 0


def _column_is_busy(pixel, x: int, rows: list[int]) -> bool:
    """True if this column varies vertically, meaning something is drawn.

    Compared within the column rather than against one background colour,
    because the taskbar is a subtle vertical gradient. Measuring against a
    single background value read almost every column as content.
    """
    values = [pixel(x, y) & 0x00FFFFFF for y in rows]
    first = values[0]
    for value in values[1:]:
        if (
            abs(((value >> 16) & 0xFF) - ((first >> 16) & 0xFF))
            + abs(((value >> 8) & 0xFF) - ((first >> 8) & 0xFF))
            + abs((value & 0xFF) - (first & 0xFF))
        ) > _CONTENT_THRESHOLD:
            return True
    return False


def measure_taskbar(
    bar: Taskbar, ignore: tuple[tuple[int, int], ...] = ()
) -> tuple[int | None, int | None]:
    r"""Find the clock's left edge and the rightmost icon, in physical pixels.

    Done by reading pixels because the Windows 11 clock is part of the XAML
    layer and has no window handle of its own to query. Secondary taskbars do
    not even have a TrayNotifyWnd child.

    `ignore` is a tuple of (left, right) physical x ranges to treat as empty.
    The Ox's own strips sit on the taskbar, so without this the strip reads
    itself as taskbar content and the free space collapses to zero on every
    re-measure.

    Returns (clock_left, icons_right), either of which may be None.
    """
    screen = bar.screen                  # the live one, looked up now
    if screen is None:
        return None, None                # its monitor has just gone
    g = bar.screen_geometry
    ratio = bar.dpr
    width, height = bar.width, bar.height

    # Grab only the taskbar, not the whole screen. A full 3440x1440 grab is
    # about 20 MB per monitor; the bar alone is well under one. That matters
    # on a machine that is already short of memory.
    shot = screen.grabWindow(
        0,
        int((bar.rect[0] - g.x()) / ratio),
        int((bar.rect[1] - g.y()) / ratio),
        int(width / ratio) + 1,
        int(height / ratio) + 1,
    )
    if shot.isNull():
        return None, None
    image = shot.toImage().convertToFormat(QtGui.QImage.Format_RGB32)
    local_x, local_y = 0, 0          # the grab starts at the bar's own corner
    if image.width() < width or image.height() < height:
        return None, None

    rows = _sample_rows(local_y, height)
    pixel = image.pixel
    skip = [
        (max(0, start - bar.rect[0]), min(width, end - bar.rect[0]))
        for start, end in ignore
    ]

    cache: dict[int, bool] = {}

    def busy(x: int) -> bool:
        """Lazy, so the scan stops as soon as it has what it needs.

        Everything this function is looking for sits at the right-hand end of
        the taskbar, so scanning the whole 3440px bar was wasted work.
        """
        if x in cache:
            return cache[x]
        for start, end in skip:
            if start <= x < end:
                cache[x] = False
                return False
        value = _column_is_busy(pixel, local_x + x, rows)
        cache[x] = value
        return value

    x = width - 1
    while x >= 0 and not busy(x):
        x -= 1
    if x < 0:
        return None, None            # taskbar is entirely empty

    clock_left_local = _block_left_edge(busy, x, _CLOCK_INTERNAL_GAP)
    clock_left = bar.rect[0] + clock_left_local

    x = clock_left_local - 1
    while x >= 0 and not busy(x):
        x -= 1
    icons_right = (bar.rect[0] + x) if x >= 0 else None
    return clock_left, icons_right


def _is_internal_name(name: str) -> bool:
    r"""Best guess at whether a screen of this name is the laptop's own.

    External monitors report a model name. The internal panel usually comes
    back as a bare device path like \\.\DISPLAY1.
    """
    name = name or ""
    return name.startswith("\\\\.\\DISPLAY") or name.strip() == ""


def _is_internal(screen: QtGui.QScreen) -> bool:
    """The same guess, for a live QScreen."""
    return _is_internal_name(screen.name() or "")


def _position(bar: Taskbar, externals: list[Taskbar]) -> int:
    """Where this bar's screen is among the external ones, left to right."""
    ordered = sorted(externals, key=lambda b: b.screen_geometry.x())
    return next((i for i, b in enumerate(ordered) if b.hwnd == bar.hwnd), 0)


def _place_for(bar: Taskbar, externals: list[Taskbar]) -> str:
    """Where a monitor is, the way a person would say it: "left monitor".

    Position only, never the model name the monitor reports, so it reads the
    same on anyone's desk and says nothing about what hardware is on it.
    """
    if _is_internal_name(bar.screen_name):
        return "laptop screen"
    if len(externals) == 1:
        return "monitor"
    position = _position(bar, externals)
    if len(externals) == 2:
        return "left monitor" if position == 0 else "right monitor"
    names = ["left", "middle", "right"]
    word = names[position] if position < len(names) else f"number {position + 1}"
    return f"{word} monitor"


def _label_for(bar: Taskbar, externals: list[Taskbar]) -> str:
    """A name a person would recognise, per the spec's examples."""
    if _is_internal_name(bar.screen_name):
        return "Laptop"
    width = bar.screen_geometry.width()
    if len(externals) == 1:
        return f"External monitor {width}"
    position = _position(bar, externals)
    if len(externals) == 2:
        return f"{'Left' if position == 0 else 'Right'} monitor {width}"
    names = ["Left", "Middle", "Right"]
    word = names[position] if position < len(names) else f"Monitor {position + 1}"
    return f"{word} monitor {width}"


def monitor_key(screen: QtGui.QScreen) -> str:
    """Identify a monitor across restarts without relying on plug order."""
    g = screen.geometry()
    return f"{screen.name()}|{g.width()}x{g.height()}"


def discover(
    measure: bool = True, ignore: tuple[tuple[int, int], ...] = ()
) -> list[MonitorTarget]:
    """Every monitor the strip could be shown on, left to right.

    `ignore` passes through to measure_taskbar so The Ox's own strips do not
    count as taskbar content.
    """
    bars = find_taskbars()
    externals = [b for b in bars if not _is_internal_name(b.screen_name)]
    targets: list[MonitorTarget] = []
    for bar in bars:
        clock_left, icons_right = measure_taskbar(bar, ignore) if measure else (None, None)
        targets.append(
            MonitorTarget(
                key=bar.key,
                label=_label_for(bar, externals),
                place=_place_for(bar, externals),
                taskbar=bar,
                is_internal=_is_internal_name(bar.screen_name),
                clock_left=clock_left,
                icons_right=icons_right,
            )
        )
    targets.sort(key=lambda t: t.taskbar.rect[0])
    for t in targets:
        # By position and size only: never the model name a monitor reports.
        _log().info(
            "monitor %s: screen %dx%d dpr=%.2f | taskbar %dx%d at %s "
            "| clock=%s icons=%s free=%dpx | ignored spans=%s",
            t.label, t.taskbar.screen_geometry.width(),
            t.taskbar.screen_geometry.height(), t.taskbar.dpr,
            t.taskbar.width, t.taskbar.height, t.taskbar.rect[:2],
            t.clock_left, t.icons_right, t.free_width, list(ignore),
        )
    return targets


_SHELL_CLASSES = ("Progman", "WorkerW", PRIMARY_CLASS, SECONDARY_CLASS)

_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020   # click-through overlay
_WS_EX_TOOLWINDOW = 0x00000080    # not a real application window
_WS_EX_LAYERED = 0x00080000
_DWMWA_CLOAKED = 14

_dwmapi = ctypes.windll.dwmapi


def _is_cloaked(hwnd: int) -> bool:
    """DWM-cloaked windows are present but not shown to the user.

    Background UWP windows such as "Windows Input Experience" sit at full
    monitor size while cloaked, and would otherwise look like a full-screen
    app forever.
    """
    value = ctypes.c_int(0)
    result = _dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd), ctypes.c_uint(_DWMWA_CLOAKED),
        ctypes.byref(value), ctypes.sizeof(value),
    )
    return result == 0 and value.value != 0


def _is_overlay(hwnd: int) -> bool:
    r"""True for transparent or tool-window overlays, which are not apps.

    Found the hard way: some desktop apps keep a click-through overlay window
    stretched across the whole virtual desktop. It covers every monitor, so
    without this filter the strip treated it as a permanent full-screen app
    and never appeared.
    """
    ex_style = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    if ex_style & _WS_EX_TOOLWINDOW:
        return True
    return bool(ex_style & _WS_EX_TRANSPARENT and ex_style & _WS_EX_LAYERED)

_ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _top_level_windows() -> list[int]:
    found: list[int] = []

    def callback(hwnd, _param):
        found.append(hwnd)
        return True

    _user32.EnumWindows(_ENUM_PROC(callback), 0)
    return found


def fullscreen_monitors(bars, own_windows: frozenset[int] = frozenset()) -> set[int]:
    """Which of these taskbars are covered by a full-screen app.

    One EnumWindows pass answers for every monitor at once. Asking per window
    meant several full passes a second, each with a DWM call per top-level
    window, and that was most of the app's idle CPU.
    """
    if not bars:
        return set()
    bounds = [(bar, *bar_bounds(bar)) for bar in bars]
    covered: set[int] = set()
    buf = ctypes.create_unicode_buffer(256)

    for hwnd in _top_level_windows():
        if hwnd in own_windows:
            continue
        if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
            continue
        if _user32.IsZoomed(hwnd):
            continue
        if _is_overlay(hwnd) or _is_cloaked(hwnd):
            continue
        _user32.GetClassNameW(hwnd, buf, 256)
        if buf.value in _SHELL_CLASSES:
            continue
        left, top, right, bottom = _rect(hwnd)
        if right - left <= 0 or bottom - top <= 0:
            continue
        for bar, sx, sy, sr, sb in bounds:
            if bar.hwnd in covered:
                continue
            if left <= sx and top <= sy and right >= sr and bottom >= sb:
                covered.add(bar.hwnd)
        if len(covered) == len(bounds):
            break
    return covered


def fullscreen_app_on(bar: Taskbar, own_windows: frozenset[int] = frozenset()) -> bool:
    """Is a full-screen app covering this monitor right now?

    Every visible top-level window is checked, not just the foreground one.
    Checking only the foreground window misses the common case: a video
    playing full screen on one monitor while the user types on another, which
    is exactly when the strip most needs to get out of the way.

    A maximized window is NOT full screen, but its rect does not prove that:
    Windows inflates a maximized window past the monitor edges by the resize
    border, so it covers the monitor on paper while the taskbar stays visible
    on screen. Maximized windows are therefore excluded by IsZoomed, which is
    what separates "maximized" from a genuine full-screen window.

    `own_windows` holds The Ox's own overlay handles. They are skipped by
    handle rather than by process id, because excluding a whole process would
    also hide a genuine full-screen window that happened to share it.
    """
    g = bar.screen_geometry
    ratio = bar.dpr
    sx, sy = g.x(), g.y()
    sw, sh = int(g.width() * ratio), int(g.height() * ratio)

    buf = ctypes.create_unicode_buffer(256)

    for hwnd in _top_level_windows():
        if hwnd in own_windows:
            continue
        if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
            continue
        if _user32.IsZoomed(hwnd):
            continue                 # maximized, not full screen
        if _is_overlay(hwnd) or _is_cloaked(hwnd):
            continue
        _user32.GetClassNameW(hwnd, buf, 256)
        if buf.value in _SHELL_CLASSES:
            continue
        left, top, right, bottom = _rect(hwnd)
        if right - left <= 0 or bottom - top <= 0:
            continue
        if left <= sx and top <= sy and right >= sx + sw and bottom >= sy + sh:
            return True
    return False


def reserve_strip_space(values: dict, targets) -> list["MonitorTarget"]:
    r"""Turn the strip off on any monitor whose taskbar is too full for it.

    Run once, on a first run, before any strip is built. The strip needs
    STRIP_MIN_FREE_PX between the pinned icons and the clock; on a laptop
    screen, or a taskbar with a lot of pinned apps, there simply is not that
    much room and the strip either covers icons or does not appear at all.
    Better to start with it off and the biscuit on, and say so, than to have
    it look broken.

    Only monitors with no setting of their own are touched, so a choice made
    deliberately from the tray menu is never overridden. Internal panels are
    skipped because they already default to off.

    Returns the monitors it switched off, so the caller can say which.
    """
    from . import settings

    chosen = values.get("strip_monitors")
    chosen = chosen if isinstance(chosen, dict) else {}
    turned_off: list[MonitorTarget] = []
    for target in targets:
        if target.key in chosen:
            continue                     # already decided, one way or other
        if target.is_internal:
            continue                     # off by default already
        if target.has_room_for_strip:
            continue
        settings.set_strip_enabled(values, target.key, False)
        turned_off.append(target)
    return turned_off
