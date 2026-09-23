r"""The Settings window: one window, five sections.

  Services   which services to show; the bull and the intro on a first run
  Colors     each service's color, the Fable number's color
  Warnings   when bars turn amber and red, and the budget warning
  Checking   how often each service is asked, 1 to 15 minutes
  Layout     which monitors get a strip, the biscuit, Start with Windows,
             and the order services appear in, left to right

Opens on a first run, and from the tray's "Settings...". Every change is
made to a draft; the real strip, biscuit and panel only change when Save or
Done is pressed. Cancel or the X means no change, exactly as closing the old
setup screen did, and a first run closed that way leaves everything off.

A live preview under the sections draws the strip and the biscuit with the
same code as the real ones, from the draft, so what it shows is what Save
will give.

What is chosen under Services is the whole of what the app does: an
unticked service is never drawn, never polled, and its login file is never
opened or watched. Checking whether a login exists is done by file existence
only; this window never opens a credential file and never reads a token.
The list of services comes from registry.py and nowhere else, so nothing
here can add a service.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from PySide6 import QtCore, QtGui, QtWidgets

from . import appearance, appicon, autostart, budget, launcher, monitors, registry, settings
from . import biscuit as biscuit_mod
from . import strip as strip_mod
from .controls import ColourRow, FastCheckDialog, NumberControl
from .overlay import CHIP_BG, GLASS, GLASS_INK, GLASS_MUTED
from .providers.common import OK, OPEN_APP, Bucket, Reading

FOUND = "Found"
MISSING = "Not signed in"

SECTIONS = ("Services", "Colors", "Warnings", "Checking", "Layout")

INTRO = [
    ("What it does",
     "The Ox Tracker shows how much of each AI plan's allowance you have "
     "used, in your taskbar, a floating bar and a detail panel, so you do "
     "not have to open each site to find out."),
    ("How it works",
     "It reads the logins the official apps have already saved on this PC, "
     "and sends each one only to that company's own server. It never changes "
     "your accounts, never signs in for you, never refreshes or copies a "
     "login, and keeps everything on this PC. No server, no telemetry."),
    ("What it is for",
     "Two things: seeing your usage at a glance, and pacing yourself so you "
     "do not run out days before your weekly reset."),
    ("The budget warning",
     "Each weekly allowance is split into seven daily shares. A red outline "
     "means you have used more than the days so far allow. Unused days carry "
     "forward, so a quiet Monday leaves more for Wednesday. It is a pacing "
     "aid, not a judgement on how fast you go."),
    ("Not signed in?",
     "Any service showing 'Not signed in' can be signed in from here. The "
     "Sign in button opens that company's own sign-in, which you complete "
     "yourself."),
]

# Shown on its own, at the end of the first-run intro.
INTRO_RISK = ("Reads usage with each service's own sign-in, which may "
              "conflict with their terms. Use at your own risk.")

# The bull at the top of the Services section, in logical pixels.
LOGO_SIZE = 64

# The settings this window edits. Save copies exactly these from the draft
# into the live settings, and nothing else, so a biscuit dragged or a panel
# pinned while the window is open is never undone by pressing Save.
EDITABLE = (
    "services", "services_confirmed",
    "service_colors", appearance.FABLE_KEY, appearance.BUDGET_KEY,
    "amber_at", "red_at",
    "budget_warning", "day_start",
    "poll_minutes",
    "biscuit_visible", "service_order", "start_with_windows_choice",
)

GAP_NOTE = "Amber must stay at least 5 points below red."
SIZE_WANTED = QtCore.QSize(940, 780)
SIZE_SMALLEST = QtCore.QSize(600, 440)


def sample_readings(names) -> list[Reading]:
    """Made-up numbers for the preview: one of each colour, one over budget.

    Nothing here is read from anywhere. The week started a day and a bit ago
    so a 62% weekly is over its daily budget whatever day-start is chosen.
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=1, hours=6)
    end = start + timedelta(days=7)

    def weekly(percent: float) -> Bucket:
        return Bucket("Weekly", percent, end, window_start=start, budgeted=True)

    samples = {
        "Claude": Reading("Claude", OK, [Bucket("5 hour", 31.0, now + timedelta(hours=3)),
                                         weekly(62.0), Bucket("Fable", 8.0, end)],
                          last_success=now),
        "ChatGPT": Reading("ChatGPT", OK, [Bucket("5 hour", 86.0, now + timedelta(hours=2)),
                                           weekly(19.0)], last_success=now),
        "Grok": Reading("Grok", OK, [weekly(40.0)], last_success=now),
        "Grok Bot": Reading("Grok Bot", OPEN_APP, [],
                            detail="Check usage in the Grok Bot app."),
    }
    return [samples[name] for name in names if name in samples]


class _Page(QtWidgets.QScrollArea):
    """One section: a column of content that scrolls when the window is small.

    QScrollArea does not pass height-for-width on to what it holds, so a
    column of word-wrapped paragraphs would be given one line's height each
    and clip. fit() works out the real height for the current width.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.body = QtWidgets.QWidget()
        self.body.setObjectName("page")
        self.column = QtWidgets.QVBoxLayout(self.body)
        self.column.setContentsMargins(16, 12, 16, 12)
        self.column.setSpacing(10)
        self.setWidget(self.body)

    def heading(self, text: str, hint: str = "") -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName("title")
        self.column.addWidget(label)
        if hint:
            note = QtWidgets.QLabel(hint)
            note.setObjectName("hint")
            note.setWordWrap(True)
            self.column.addWidget(note)
        return label

    def fit(self) -> None:
        layout = self.body.layout()
        width = self.viewport().width()
        if layout is not None and layout.hasHeightForWidth() and width > 0:
            self.body.setMinimumHeight(layout.totalHeightForWidth(width))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.fit()


class SettingsWindow(QtWidgets.QWidget):
    """Services, Colors, Warnings, Checking and Layout, with a live preview."""

    chosen = QtCore.Signal(list)          # the list of service keys, on Save or Done
    # Closed with the X, Cancel, Esc or Alt+F4. Nothing further is saved and
    # nothing is switched on, so whoever is waiting on this window must carry
    # on with what was already confirmed rather than waiting for ever.
    dismissed = QtCore.Signal()
    applied = QtCore.Signal()             # Save or Done wrote the settings
    # The window has gone away: Done, or any of the ways of closing it. The
    # SettingsHolder below deletes it then, so a closed window holds nothing.
    finished = QtCore.Signal()

    def __init__(self, values: dict, targets=None, readings=None) -> None:
        super().__init__(None)
        self._settings = values                    # the live settings
        self._draft = copy.deepcopy(values)        # what this window edits
        self._targets_provider = targets           # callable -> MonitorTargets
        # callable -> the latest readings, for "each service's reset" times.
        self._readings_provider = readings
        self._boxes: dict[str, QtWidgets.QCheckBox] = {}
        self._status: dict[str, QtWidgets.QLabel] = {}
        self._buttons: dict[str, QtWidgets.QPushButton] = {}
        self._colour_rows: dict[str, ColourRow] = {}
        self._monitor_boxes: dict[str, QtWidgets.QCheckBox] = {}
        self._first_run = not registry.has_been_set_up(values)
        self._unconfirmed: list[str] = []
        self._crowded: list[str] = []
        self._intro_bodies: list[QtWidgets.QLabel] = []
        self._accepted_poll = settings.poll_minutes(values)
        self._asking = False                       # the fast-checking pop-up is open
        self._loading = False

        self.setWindowTitle("The Ox Tracker: Settings")
        self.setWindowFlags(QtCore.Qt.Window | QtCore.Qt.WindowStaysOnTopHint)
        self.setStyleSheet(_STYLE)

        self._preview_values: dict = {}
        self._preview_biscuit = biscuit_mod.Biscuit(self._preview_values)
        self._build()
        self._load_draft()

    # ======================================================================
    # Building
    # ======================================================================
    def _build(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 10)
        outer.setSpacing(0)

        body = QtWidgets.QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self._nav = QtWidgets.QListWidget()
        self._nav.setObjectName("nav")
        self._nav.setFixedWidth(150)
        self._nav.setAccessibleName("Settings sections")
        self._nav.setToolTip("Choose a section. Up and Down move between them.")
        tips = {
            "Services": "Which services to show, and signing in",
            "Colors": "Each service's color, and the Fable number's",
            "Warnings": "When bars turn amber and red, and the budget warning",
            "Checking": "How often each service is checked",
            "Layout": "Strips, the biscuit, Start with Windows, and the order",
        }
        for name in SECTIONS:
            item = QtWidgets.QListWidgetItem(name)
            item.setToolTip(tips[name])
            self._nav.addItem(item)
        body.addWidget(self._nav)

        self._stack = QtWidgets.QStackedWidget()
        self._pages: dict[str, _Page] = {}
        for name, build in (("Services", self._build_services),
                            ("Colors", self._build_colours),
                            ("Warnings", self._build_warnings),
                            ("Checking", self._build_checking),
                            ("Layout", self._build_layout)):
            page = _Page()
            self._pages[name] = page
            build(page)
            page.column.addStretch(1)
            self._stack.addWidget(page)
        body.addWidget(self._stack, 1)
        self._nav.currentRowChanged.connect(self._show_section_index)
        outer.addLayout(body, 1)

        outer.addWidget(self._build_preview())

        footer = QtWidgets.QHBoxLayout()
        footer.setContentsMargins(16, 8, 16, 0)
        footer.addStretch(1)
        self._cancel = QtWidgets.QPushButton("&Cancel")
        self._cancel.setToolTip("Close without saving anything changed since "
                                "the last Save (Esc)")
        self._cancel.clicked.connect(self.close)
        self._save = QtWidgets.QPushButton("&Save")
        self._save.setToolTip("Apply the changes and keep this window open (Ctrl+S)")
        self._save.clicked.connect(self._on_save)
        self._done_button = QtWidgets.QPushButton("&Done")
        self._done_button.setObjectName("done")
        self._done_button.setToolTip("Apply the changes and close. The defaults "
                                     "are fine as they are, so this works from "
                                     "any section.")
        self._done_button.clicked.connect(self._done)
        for button in (self._cancel, self._save, self._done_button):
            footer.addWidget(button)
        outer.addLayout(footer)

        self._shortcut(QtGui.QKeySequence(QtGui.QKeySequence.Save), self, self._on_save)
        self._shortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape), self, self.close)

    # ----- Services --------------------------------------------------------
    def _build_services(self, page: _Page) -> None:
        # The bull, at the top, above the title and the intro. This window is
        # the only place it appears inside the app; there is no splash.
        self._logo = QtWidgets.QLabel()
        self._logo.setObjectName("logo")
        self._logo.setAlignment(QtCore.Qt.AlignHCenter)
        ratio = self.screen().devicePixelRatio() if self.screen() else 1.0
        pixmap = appicon.logo_pixmap(LOGO_SIZE, ratio)
        if not pixmap.isNull():
            self._logo.setPixmap(pixmap)
            page.column.addWidget(self._logo)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Which services should The Ox Tracker show?")
        title.setObjectName("title")
        header.addWidget(title, 1)
        self._about = QtWidgets.QPushButton("About")
        self._about.setCheckable(True)
        self._about.setToolTip("Show or hide what The Ox Tracker does and how")
        self._about.toggled.connect(self._toggle_intro)
        header.addWidget(self._about)
        page.column.addLayout(header)

        self._intro = QtWidgets.QFrame()
        self._intro.setObjectName("intro")
        intro = QtWidgets.QVBoxLayout(self._intro)
        intro.setContentsMargins(12, 10, 12, 10)
        intro.setSpacing(5)
        for heading, body in INTRO:
            head = QtWidgets.QLabel(heading)
            head.setObjectName("introHead")
            intro.addWidget(head)
            text = QtWidgets.QLabel(body)
            text.setObjectName("introBody")
            text.setWordWrap(True)
            self._intro_bodies.append(text)
            intro.addWidget(text)
        self._risk = QtWidgets.QLabel(INTRO_RISK)
        self._risk.setObjectName("introHead")
        self._risk.setWordWrap(True)
        self._intro_bodies.append(self._risk)
        intro.addSpacing(4)
        intro.addWidget(self._risk)
        page.column.addWidget(self._intro)
        # Shown by itself on a first run, and from About at any time after.
        self._intro.setVisible(self._first_run)
        self._about.setChecked(self._first_run)

        # Only ever shown when settings.json switched something on that this
        # app did not. Hidden the rest of the time.
        self._warning = QtWidgets.QLabel()
        self._warning.setObjectName("warning")
        self._warning.setWordWrap(True)
        self._warning.setVisible(False)
        page.column.addWidget(self._warning)

        # Shown when a monitor's taskbar has too little room for the strip.
        self._room = QtWidgets.QLabel()
        self._room.setObjectName("room")
        self._room.setWordWrap(True)
        self._room.setVisible(False)
        page.column.addWidget(self._room)

        hint = QtWidgets.QLabel(
            "Only the ticked services are shown, and only they are ever read. "
            "The rest are not checked and their login files are not opened.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        page.column.addWidget(hint)

        for spec in registry.SERVICES:
            row = QtWidgets.QFrame()
            row.setObjectName("row")
            line = QtWidgets.QHBoxLayout(row)
            line.setContentsMargins(10, 8, 10, 8)
            line.setSpacing(10)
            box = QtWidgets.QCheckBox(spec.name)
            box.setMinimumWidth(110)
            box.setToolTip(f"Show {spec.name}. Unticked, it is never checked "
                           f"and its login file is never opened.")
            box.toggled.connect(self._draft_changed)
            self._boxes[spec.key] = box
            line.addWidget(box)
            status = QtWidgets.QLabel("")
            status.setStyleSheet(f"color: {GLASS_MUTED};")
            # Plain text: a refused launch puts a path here, and none of it
            # may ever be read as markup.
            status.setTextFormat(QtCore.Qt.PlainText)
            self._status[spec.key] = status
            line.addWidget(status, 1)
            if spec.sign_in_args:
                button = QtWidgets.QPushButton("Sign in")
                button.setToolTip(f"Open {spec.name}'s own sign-in in a new "
                                  f"window, to finish there yourself")
                button.clicked.connect(lambda _c=False, k=spec.key: self._sign_in(k))
                self._buttons[spec.key] = button
                line.addWidget(button)
            page.column.addWidget(row)

        recheck = QtWidgets.QPushButton("Check again")
        recheck.setToolTip("Look again for each service's saved login. Only "
                           "whether the file exists is checked.")
        recheck.clicked.connect(self.refresh)
        again = QtWidgets.QHBoxLayout()
        again.addWidget(recheck)
        again.addStretch(1)
        page.column.addLayout(again)

    # ----- Colors ------------------------------------------------------------
    def _build_colours(self, page: _Page) -> None:
        page.heading(
            "Colors",
            "Each service's color, used for its name and edge. The biscuit and "
            "the panel show it at full strength; the taskbar strip shows a "
            "quieter version next to the clock. Both are shown for each color, "
            "and in the preview below. The green, amber and red of the bars "
            "themselves never change.")
        quick = QtWidgets.QHBoxLayout()
        reset_all = QtWidgets.QPushButton("Reset all colors")
        reset_all.setToolTip("Every service and the Fable number back to their "
                             "original colors")
        reset_all.clicked.connect(self._reset_all_colours)
        quick.addWidget(reset_all)
        quick.addStretch(1)
        page.column.addLayout(quick)

        for spec in registry.SERVICES:
            row = ColourRow(spec.name, spec.identity)
            row.changed.connect(lambda _c, k=spec.key: self._colour_changed(k))
            self._colour_rows[spec.key] = row
            page.column.addWidget(row)
        fable = ColourRow("Fable number", appearance.DEFAULT_FABLE, show_muted=False)
        fable.changed.connect(lambda _c: self._colour_changed("fable"))
        self._colour_rows["fable"] = fable
        page.column.addWidget(fable)

    # ----- Warnings ----------------------------------------------------------
    def _build_warnings(self, page: _Page) -> None:
        page.column.addWidget(self._build_day_start())

        page.heading("When bars change color",
                     "A bar is green until its service reaches the amber "
                     "percentage, amber until the red one, then red.")
        self._amber = NumberControl(
            appearance.AMBER_MIN, appearance.AMBER_MAX, appearance.DEFAULT_AMBER_AT,
            unit="%", tooltip="Percent used at which a bar turns amber. Always "
                              "at least 5 below red.")
        self._red = NumberControl(
            appearance.RED_MIN, appearance.RED_MAX, appearance.DEFAULT_RED_AT,
            unit="%", tooltip="Percent used at which a bar turns red.")
        page.column.addWidget(self._labelled("Turns amber at", self._amber))
        page.column.addWidget(self._labelled("Turns red at", self._red))
        self._amber.valueChanged.connect(self._amber_moved)
        self._red.valueChanged.connect(self._red_moved)

        page.heading("Budget warning",
                     "Each weekly allowance is split into seven daily shares. "
                     "A tick marks how much the days so far allow, and an "
                     "outline appears around the bar when usage has passed "
                     "it, on the strip, the biscuit and the panel alike.")
        self._budget_on = QtWidgets.QCheckBox("Show the budget warning")
        self._budget_on.setToolTip("The outline and the tick on weekly bars. "
                                   "Off hides both everywhere.")
        self._budget_on.toggled.connect(self._draft_changed)
        page.column.addWidget(self._budget_on)
        self._budget_colour = ColourRow("Budget outline", appearance.DEFAULT_BUDGET,
                                        show_muted=False)
        self._budget_colour.changed.connect(lambda _c: self._colour_changed("budget"))
        page.column.addWidget(self._budget_colour)

    def _build_day_start(self) -> QtWidgets.QFrame:
        """When your day starts: four choices, and a line saying what it means.

        Each weekly allowance is split into seven daily shares; this decides
        where one day's share ends and the next begins. The budget maths is
        the same for every choice; only the moment a day starts differs.
        """
        group = QtWidgets.QFrame()
        group.setObjectName("row")
        group.setAccessibleName("When your day starts")
        column = QtWidgets.QVBoxLayout(group)
        column.setContentsMargins(12, 10, 12, 10)
        column.setSpacing(4)
        title = QtWidgets.QLabel("When your day starts")
        title.setObjectName("title")
        column.addWidget(title)
        intro = QtWidgets.QLabel("Each weekly allowance is split into seven daily "
                                 "shares. This is when one day's share ends and "
                                 "the next begins.")
        intro.setObjectName("hint")
        intro.setWordWrap(True)
        column.addWidget(intro)

        self._day_group = QtWidgets.QButtonGroup(group)
        self._day_3am = QtWidgets.QRadioButton("3:00 AM (default)")
        self._day_3am.setToolTip("Each day starts at 3:00 AM")
        column.addWidget(self._day_3am)
        column.addWidget(self._indented(
            "Work you do before 3 AM counts toward the day before, so a late "
            "night doesn't use up tomorrow's share."))

        self._day_midnight = QtWidgets.QRadioButton("Midnight")
        self._day_midnight.setToolTip("Each day starts at 12:00 AM")
        column.addWidget(self._day_midnight)

        other = QtWidgets.QHBoxLayout()
        self._day_other = QtWidgets.QRadioButton("Another time...")
        self._day_other.setToolTip("Choose your own time for each day to start")
        other.addWidget(self._day_other)
        self._day_time = QtWidgets.QTimeEdit()
        self._day_time.setDisplayFormat("h:mm AP")
        self._day_time.setTime(QtCore.QTime(budget.DEFAULT_HOUR, 0))
        self._day_time.setToolTip("The time each day starts. Type it, or use "
                                  "the arrows and Up and Down keys.")
        self._day_time.setAccessibleName("Day start time")
        self._day_time.setFixedWidth(110)
        other.addWidget(self._day_time)
        other.addStretch(1)
        column.addLayout(other)

        self._day_reset = QtWidgets.QRadioButton(
            "Every 24 hours from each service's weekly reset")
        self._day_reset.setToolTip("Each service's days start at the time of day "
                                   "its own week resets")
        column.addWidget(self._day_reset)
        column.addWidget(self._indented(
            "Each service's days line up with its own reset time, so the days "
            "can start at different times for Claude, ChatGPT and Grok."))

        for button in (self._day_3am, self._day_midnight, self._day_other,
                       self._day_reset):
            self._day_group.addButton(button)
        self._day_group.buttonToggled.connect(self._day_choice_changed)
        self._day_time.timeChanged.connect(self._day_time_changed)

        self._day_line = QtWidgets.QLabel("")
        self._day_line.setObjectName("dayline")
        self._day_line.setWordWrap(True)
        self._day_line.setToolTip("Updates as you choose")
        column.addSpacing(4)
        column.addWidget(self._day_line)
        return group

    @staticmethod
    def _indented(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName("hint")
        label.setWordWrap(True)
        label.setContentsMargins(26, 0, 0, 4)
        return label

    # ----- Checking ----------------------------------------------------------
    def _build_checking(self, page: _Page) -> None:
        page.heading(
            "How often it checks",
            "How many minutes between checks of each service. Every 3 minutes "
            "is the default. Whatever is chosen, nothing is checked while the "
            "PC is locked, errors back off (3 minutes, then 6, 12 and so on up "
            "to 30), and a service that answers 'not signed in' or 'too many "
            "requests' is slowed down in the same way.")
        self._poll = NumberControl(
            settings.POLL_MIN, settings.POLL_MAX, settings.POLL_DEFAULT, unit="min",
            tooltip="Minutes between checks, 1 to 15. Under 3 asks you to "
                    "accept a risk first.")
        self._poll.committed.connect(self._poll_committed)
        page.column.addWidget(self._labelled("Check every", self._poll))
        self._fast_note = QtWidgets.QLabel("Fast checking on.")
        self._fast_note.setObjectName("warning")
        self._fast_note.setWordWrap(True)
        self._fast_note.setVisible(False)
        page.column.addWidget(self._fast_note)

    # ----- Layout ------------------------------------------------------------
    def _build_layout(self, page: _Page) -> None:
        page.heading("Taskbar strips",
                     "One switch per monitor, named by where it is. A monitor "
                     f"needs {monitors.STRIP_MIN_FREE_PX} pixels of free taskbar "
                     "between its icons and the clock for the strip.")
        self._monitor_area = QtWidgets.QVBoxLayout()
        page.column.addLayout(self._monitor_area)

        page.heading("Other")
        self._biscuit_on = QtWidgets.QCheckBox("Show the biscuit")
        self._biscuit_on.setToolTip("The floating bar of battery meters")
        self._biscuit_on.toggled.connect(self._draft_changed)
        page.column.addWidget(self._biscuit_on)
        self._start_on = QtWidgets.QCheckBox("Start with Windows")
        self._start_on.setToolTip("Start The Ox Tracker when you sign in to "
                                  "Windows. The same switch as in the tray menu.")
        page.column.addWidget(self._start_on)
        self._start_note = QtWidgets.QLabel("")
        self._start_note.setObjectName("hint")
        self._start_note.setWordWrap(True)
        page.column.addWidget(self._start_note)

        page.heading("Order, left to right",
                     "Applies to the strip, the biscuit and the panel.")
        order_row = QtWidgets.QHBoxLayout()
        self._order = QtWidgets.QListWidget()
        self._order.setObjectName("order")
        self._order.setAccessibleName("Service order")
        self._order.setToolTip("Pick a service, then move it with the buttons, "
                               "or with Alt+Up and Alt+Down")
        order_row.addWidget(self._order, 1)
        buttons = QtWidgets.QVBoxLayout()
        self._up = QtWidgets.QPushButton("Move &up")
        self._up.setToolTip("Move the selected service one place to the left (Alt+Up)")
        self._up.clicked.connect(lambda: self._move_service(-1))
        self._down = QtWidgets.QPushButton("Move do&wn")
        self._down.setToolTip("Move the selected service one place to the right "
                              "(Alt+Down)")
        self._down.clicked.connect(lambda: self._move_service(1))
        buttons.addWidget(self._up)
        buttons.addWidget(self._down)
        buttons.addStretch(1)
        order_row.addLayout(buttons)
        page.column.addLayout(order_row)
        self._shortcut(QtGui.QKeySequence("Alt+Up"), self._order,
                       lambda: self._move_service(-1))
        self._shortcut(QtGui.QKeySequence("Alt+Down"), self._order,
                       lambda: self._move_service(1))

    # ----- Preview -----------------------------------------------------------
    def _build_preview(self) -> QtWidgets.QFrame:
        frame = QtWidgets.QFrame()
        frame.setObjectName("preview")
        column = QtWidgets.QVBoxLayout(frame)
        column.setContentsMargins(16, 8, 16, 8)
        column.setSpacing(4)
        head = QtWidgets.QLabel("Preview, with made-up numbers. Nothing changes "
                                "for real until you press Save or Done.")
        head.setObjectName("hint")
        column.addWidget(head)

        line = QtWidgets.QVBoxLayout()
        line.setSpacing(4)
        strip_box = QtWidgets.QVBoxLayout()
        strip_box.addWidget(self._caption("Taskbar strip (quieter colors)"))
        self._preview_taskbar = QtWidgets.QFrame()
        self._preview_taskbar.setObjectName("taskbar")
        bar = QtWidgets.QHBoxLayout(self._preview_taskbar)
        bar.setContentsMargins(8, 0, 8, 0)
        # The real strip widget, the one each taskbar window holds.
        self._preview_strip = strip_mod.StripWidget()
        self._preview_strip.setFixedHeight(48)
        self._preview_strip.setToolTip("The taskbar strip, drawn by the same "
                                       "code as the real one")
        bar.addWidget(self._preview_strip)
        bar.addStretch(1)
        self._preview_taskbar.setSizePolicy(QtWidgets.QSizePolicy.Maximum,
                                            QtWidgets.QSizePolicy.Fixed)
        strip_box.addWidget(self._preview_taskbar)
        line.addLayout(strip_box, 0)

        biscuit_box = QtWidgets.QVBoxLayout()
        biscuit_box.addWidget(self._caption("Biscuit (full colors)"))
        # The real biscuit, never shown, drawn into a picture.
        self._preview_biscuit_view = QtWidgets.QLabel()
        self._preview_biscuit_view.setToolTip("The biscuit, drawn by the same "
                                              "code as the real one")
        biscuit_box.addWidget(self._preview_biscuit_view)
        line.addLayout(biscuit_box, 0)
        column.addLayout(line)
        return frame

    @staticmethod
    def _shortcut(keys: QtGui.QKeySequence, owner, action) -> QtGui.QShortcut:
        shortcut = QtGui.QShortcut(keys, owner)
        shortcut.activated.connect(action)
        return shortcut

    @staticmethod
    def _caption(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName("hint")
        return label

    @staticmethod
    def _labelled(text: str, control: QtWidgets.QWidget) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        line = QtWidgets.QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        label = QtWidgets.QLabel(text)
        label.setMinimumWidth(130)
        label.setBuddy(control.slider if isinstance(control, NumberControl) else control)
        line.addWidget(label)
        line.addWidget(control, 1)
        return row

    # ======================================================================
    # Draft <-> controls
    # ======================================================================
    def _load_draft(self) -> None:
        """Start again from the live settings: every control shows them."""
        self._loading = True
        try:
            self._draft = copy.deepcopy(self._settings)
            colours = self._draft.get("service_colors") or {}
            for spec in registry.SERVICES:
                self._colour_rows[spec.key].set_value(colours.get(spec.key))
            self._colour_rows["fable"].set_value(self._draft.get(appearance.FABLE_KEY))
            self._budget_colour.set_value(self._draft.get(appearance.BUDGET_KEY))

            look = appearance.look_from(self._draft)
            self._amber.setValue(look.amber_at)
            self._red.setValue(look.red_at)
            self._budget_on.setChecked(bool(self._draft.get("budget_warning", True)))
            self._show_day_choice(self._draft.get(budget.DAY_START_KEY))

            self._accepted_poll = settings.poll_minutes(self._draft)
            self._poll.setValue(self._accepted_poll)
            self._show_fast_note()

            self._biscuit_on.setChecked(bool(self._draft.get("biscuit_visible", True)))
            available = autostart.is_available()
            self._start_on.setEnabled(available)
            self._start_on.setChecked(available and autostart.is_enabled())
            self._start_note.setText(
                "" if available else
                "Only the installed app can start with Windows. This copy is "
                "running from source, so this switch is off and greyed out.")
            self._load_monitors()
            self._load_order(look)
            self.refresh()
            for note in (self._amber, self._red, self._poll):
                note.set_note("")
        finally:
            self._loading = False
        self._update_preview()

    def _load_monitors(self) -> None:
        while self._monitor_area.count():
            item = self._monitor_area.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Off the page now, not at the next event loop turn: until
                # then a deleteLater() widget is still drawn where it was.
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._monitor_boxes = {}
        try:
            targets = (self._targets_provider() if self._targets_provider
                       else monitors.discover())
        except Exception:       # noqa: BLE001 - never block the window
            targets = []
        if not targets:
            none = QtWidgets.QLabel("No taskbars found right now.")
            none.setObjectName("hint")
            self._monitor_area.addWidget(none)
        for target in targets:
            place = target.place or "monitor"
            box = QtWidgets.QCheckBox(f"Strip on your {place}")
            box.setChecked(settings.strip_enabled(self._draft, target.key,
                                                  target.is_internal))
            box.setToolTip(f"Show the taskbar strip on your {place}")
            if not target.has_room_for_strip:
                box.setEnabled(False)
                reason = (f"Its taskbar has {target.free_width} pixels free "
                          f"between the icons and the clock, and the strip needs "
                          f"{monitors.STRIP_MIN_FREE_PX}. The biscuit and the "
                          f"panel work there as normal.")
                box.setToolTip(reason)
                self._monitor_area.addWidget(box)
                why = QtWidgets.QLabel(reason)
                why.setObjectName("hint")
                why.setWordWrap(True)
                self._monitor_area.addWidget(why)
            else:
                self._monitor_area.addWidget(box)
            box.toggled.connect(self._draft_changed)
            self._monitor_boxes[target.key] = box

    def _load_order(self, look: appearance.Look) -> None:
        self._order.clear()
        for name in look.order:
            item = QtWidgets.QListWidgetItem(name)
            item.setData(QtCore.Qt.UserRole, registry.BY_NAME[name].key)
            item.setToolTip(f"{name}: select, then Move up or Move down")
            self._order.addItem(item)
        self._order.setCurrentRow(0)
        # Tall enough for every service, so none hides behind a scroll bar.
        rows = sum(self._order.sizeHintForRow(i) for i in range(self._order.count()))
        self._order.setFixedHeight(rows + 2 * self._order.frameWidth() + 4)
        self._order.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

    def _checked_keys(self) -> list[str]:
        return [key for key, box in self._boxes.items() if box.isChecked()]

    def _draft_changed(self, *_args) -> None:
        """A switch changed: copy what the controls say into the draft."""
        if self._loading:
            return
        self._draft["budget_warning"] = self._budget_on.isChecked()
        self._draft["biscuit_visible"] = self._biscuit_on.isChecked()
        monitors_now = dict(self._draft.get("strip_monitors") or {})
        for key, box in self._monitor_boxes.items():
            if box.isEnabled():
                monitors_now[key] = box.isChecked()
        self._draft["strip_monitors"] = monitors_now
        self._update_preview()

    def _colour_changed(self, which: str) -> None:
        if self._loading:
            return
        if which == "fable":
            self._draft[appearance.FABLE_KEY] = self._colour_rows["fable"].value()
        elif which == "budget":
            self._draft[appearance.BUDGET_KEY] = self._budget_colour.value()
        else:
            colours = dict(self._draft.get("service_colors") or {})
            chosen = self._colour_rows[which].value()
            if chosen:
                colours[which] = chosen
            else:
                colours.pop(which, None)
            self._draft["service_colors"] = colours
        self._update_preview()

    def _reset_all_colours(self) -> None:
        for row in self._colour_rows.values():
            row.set_value(None, emit=True)

    # ----- amber and red: always 5 apart ----------------------------------
    def _amber_moved(self, value: int) -> None:
        limit = self._red.value() - appearance.MIN_GAP
        if value > limit:
            self._amber.setValue(limit)
            self._amber.set_note(GAP_NOTE)
            value = limit
        self._draft["amber_at"] = value
        self._draft["red_at"] = self._red.value()
        self._update_preview()

    def _red_moved(self, value: int) -> None:
        limit = self._amber.value() + appearance.MIN_GAP
        if value < limit:
            self._red.setValue(limit)
            self._red.set_note(GAP_NOTE)
            value = limit
        self._draft["red_at"] = value
        self._draft["amber_at"] = self._amber.value()
        self._update_preview()

    # ----- day start -------------------------------------------------------
    def _show_day_choice(self, choice) -> None:
        """Select the button for a saved choice; an invalid one means 3:00 AM."""
        if budget.parse_day_start(choice) is None:
            choice = budget.DAY_START_DEFAULT
        _mode, hour, minute = budget.parse_day_start(choice)
        button = {budget.CHOICE_3AM: self._day_3am,
                  budget.CHOICE_MIDNIGHT: self._day_midnight,
                  budget.CHOICE_RESET: self._day_reset}.get(choice, self._day_other)
        was = self._day_group.blockSignals(True)
        button.setChecked(True)
        self._day_group.blockSignals(was)
        if button is self._day_other:
            was = self._day_time.blockSignals(True)
            self._day_time.setTime(QtCore.QTime(hour, minute))
            self._day_time.blockSignals(was)
        self._day_time.setEnabled(button is self._day_other)
        self._show_day_line()

    def day_choice(self) -> str:
        """The choice the buttons show, as it will be saved."""
        if self._day_midnight.isChecked():
            return budget.CHOICE_MIDNIGHT
        if self._day_other.isChecked():
            time = self._day_time.time()
            return f"{time.hour():02d}:{time.minute():02d}"
        if self._day_reset.isChecked():
            return budget.CHOICE_RESET
        return budget.CHOICE_3AM

    def _day_choice_changed(self, _button, checked: bool) -> None:
        if not checked:
            return
        self._day_time.setEnabled(self._day_other.isChecked())
        if self._loading:
            return
        self._draft[budget.DAY_START_KEY] = self.day_choice()
        self._show_day_line()
        self._update_preview()

    def _day_time_changed(self, _time) -> None:
        if self._loading or not self._day_other.isChecked():
            return
        self._draft[budget.DAY_START_KEY] = self.day_choice()
        self._show_day_line()
        self._update_preview()

    def _show_day_line(self) -> None:
        """The line under the choices: when today's share started, and the next."""
        try:
            readings = self._readings_provider() if self._readings_provider else []
        except Exception:       # noqa: BLE001 - never break the window over it
            readings = []
        values = dict(self._draft)
        values[budget.DAY_START_KEY] = self.day_choice()
        self._day_line.setText("\n".join(budget.share_lines(values, readings)))

    # ----- checking --------------------------------------------------------
    def _poll_committed(self, minutes: int) -> None:
        """A new interval settled. Under 3 needs the risk accepted, every time."""
        if self._loading or self._asking or minutes == self._accepted_poll:
            return
        if minutes < settings.POLL_WARN_BELOW:
            self._asking = True
            try:
                accepted = self.ask_fast_checking(minutes)
            finally:
                self._asking = False
            if not accepted:
                self._poll.setValue(self._accepted_poll)     # Cancel: as it was
                self._poll.set_note("")
                return
        self._accepted_poll = minutes
        self._draft["poll_minutes"] = minutes
        self._show_fast_note()

    def ask_fast_checking(self, minutes: int) -> bool:
        """The risk warning. Tests replace this to answer it without a click."""
        dialog = FastCheckDialog(minutes, self)
        self._fast_dialog = dialog
        return dialog.exec() == QtWidgets.QDialog.Accepted

    def _show_fast_note(self) -> None:
        fast = self._accepted_poll < settings.POLL_WARN_BELOW
        self._fast_note.setText(
            f"Fast checking on: every {self._accepted_poll} minute"
            f"{'s' if self._accepted_poll != 1 else ''}. You accepted the risk "
            f"of each service limiting, suspending or banning your account.")
        self._fast_note.setVisible(fast)

    # ----- order -----------------------------------------------------------
    def _move_service(self, step: int) -> None:
        row = self._order.currentRow()
        target = row + step
        if row < 0 or not 0 <= target < self._order.count():
            return
        item = self._order.takeItem(row)
        self._order.insertItem(target, item)
        self._order.setCurrentRow(target)
        self._draft["service_order"] = [
            self._order.item(i).data(QtCore.Qt.UserRole)
            for i in range(self._order.count())]
        self._update_preview()

    # ----- preview and warnings ------------------------------------------
    def _update_preview(self) -> None:
        if self._loading:
            return
        look = appearance.look_from(self._draft)
        chosen = [registry.BY_KEY[k].name for k in self._checked_keys()]
        names = look.ordered(chosen or registry.ORDER)
        readings = sample_readings(names)

        views = {r.service: strip_mod.build_view(r, self._draft) for r in readings}
        self._preview_strip.set_look(look)
        self._preview_strip.set_views(views)

        self._preview_values.clear()
        self._preview_values.update(copy.deepcopy(self._draft))
        self._preview_values["biscuit_scale"] = 1.0
        self._preview_biscuit.refresh_look()
        self._preview_biscuit.set_readings(readings)
        picture = self._preview_biscuit.grab()
        # Only ever shrunk, never cut off, when the window is narrower than
        # the biscuit; same drawing, same colours, just smaller.
        room = max(200, (self.width() or SIZE_WANTED.width()) - 40)
        if picture.width() / picture.devicePixelRatio() > room:
            ratio = picture.devicePixelRatio()
            picture = picture.scaledToWidth(int(room * ratio),
                                            QtCore.Qt.SmoothTransformation)
            picture.setDevicePixelRatio(ratio)
        self._preview_biscuit_view.setPixmap(picture)

        for spec in registry.SERVICES:
            row = self._colour_rows[spec.key]
            row.set_muted(look.tint[spec.name])
            row.set_warning(appearance.too_close(look.identity[spec.name], look, spec.name))
        self._colour_rows["fable"].set_warning(
            appearance.too_close(look.fable, look, "Fable"))
        self._budget_colour.set_warning(
            appearance.too_close(look.budget_color, look, "Budget"))
        for spec in registry.SERVICES:
            self._boxes[spec.key].setStyleSheet(
                f"QCheckBox {{ color: {look.identity[spec.name]}; font-weight: 700; }}")

    # ======================================================================
    # The old setup screen's jobs, unchanged
    # ======================================================================
    def check_taskbar_room(self, targets=None) -> list[str]:
        r"""Leave the strip off where the taskbar is too full, and say so.

        Run on a first run, before any strip is built. A monitor the user
        has already decided about is never touched. This writes to the live
        settings straight away, as it always did: it is a decision made
        before the window opens, not one of its edits.
        """
        if targets is None:
            try:
                targets = monitors.discover()
            except Exception:       # noqa: BLE001 - never block setup
                return []
        turned_off = monitors.reserve_strip_space(self._settings, targets)
        self._crowded = [t.label for t in turned_off]
        if not turned_off:
            self._room.setVisible(False)
            return []
        settings.save(self._settings)
        self._draft["strip_monitors"] = copy.deepcopy(self._settings["strip_monitors"])
        # Named by position, never by the model name the monitor reports.
        lines = [
            f"The taskbar strip is off on your {t.place or 'monitor'}. Its "
            f"taskbar has {t.free_width} pixels free between the icons and the "
            f"clock, and the strip needs {monitors.STRIP_MIN_FREE_PX}. The "
            f"biscuit and the panel work there as normal."
            for t in turned_off
        ]
        self._room.setText(
            "\n\n".join(lines)
            + "\n\nYou can turn the strip on yourself later under Layout here, "
            "or Show strip on in the tray menu."
        )
        self._room.setVisible(True)
        return list(self._crowded)

    def set_unconfirmed(self, keys: list[str]) -> None:
        """Warn that settings.json switched these on, and show why."""
        self._unconfirmed = list(keys)
        if not keys:
            self._warning.setVisible(False)
            return
        names = ", ".join(registry.BY_KEY[k].name for k in keys
                          if k in registry.BY_KEY)
        self._warning.setText(
            f"settings.json has {names} switched on, but The Ox Tracker "
            f"did not put it there.\n\n"
            f"Nothing has been checked for it and its login file has not "
            f"been opened, and it is shown unticked below. Tick it and press "
            f"Done to allow it; leave it unticked to keep it off."
        )
        self._warning.setVisible(True)

    def refresh(self) -> None:
        """Re-check which logins exist. File existence only, never contents.

        A service settings.json switched on without this app saving it
        starts unticked: allowing it takes a tick, and pressing Done, the
        default, never allows it by itself.
        """
        already = registry.has_been_set_up(self._draft)
        chosen = ({spec.key for spec in registry.confirmed_specs(self._draft)}
                  if already else set(registry.default_enabled_keys()))
        was, self._loading = self._loading, True
        try:
            for spec in registry.SERVICES:
                status = self._status[spec.key]
                # The location worked out at startup, not one rebuilt from the
                # environment now. Same cache the launcher itself uses.
                installed = launcher.is_available(spec.name)
                if spec.setup_note:
                    status.setText(spec.setup_note if installed
                                   else f"{spec.setup_note} Not installed.")
                else:
                    status.setText(FOUND if spec.login_exists() else MISSING)
                button = self._buttons.get(spec.key)
                if button is not None:
                    button.setEnabled(installed)
                    button.setText("Sign in again" if spec.login_exists() else "Sign in")
                self._boxes[spec.key].setChecked(spec.key in chosen)
        finally:
            self._loading = was
        self._update_preview()

    def _sign_in(self, key: str) -> None:
        spec = registry.BY_KEY[key]
        try:
            launcher.launch(spec.name, sign_in=True)
            self._status[key].setText("Signing in, finish in the window that opened")
        except launcher.LaunchError as exc:
            # One line here; the full explanation goes to the log.
            self._status[key].setText(str(exc).splitlines()[0])

    def _fit_intro(self) -> None:
        """Re-measure the wrapped paragraphs for the current width."""
        for page in self._pages.values():
            page.fit()

    def _toggle_intro(self, shown: bool) -> None:
        self._intro.setVisible(bool(shown))
        self._fit_intro()

    # ======================================================================
    # Sections, Save, Done, Cancel
    # ======================================================================
    def _show_section_index(self, index: int) -> None:
        self._stack.setCurrentIndex(max(0, index))
        self._pages[SECTIONS[max(0, index)]].fit()

    def show_section(self, name: str) -> None:
        self._nav.setCurrentRow(SECTIONS.index(name))

    def current_section(self) -> str:
        return SECTIONS[self._stack.currentIndex()]

    def _apply(self) -> list[str]:
        """Write the draft's settings into the live ones, and save."""
        keys = self._checked_keys()
        clean = registry.set_enabled_keys(self._draft, keys)
        checked = settings.from_stored(copy.deepcopy(self._draft))
        for key in EDITABLE:
            self._settings[key] = copy.deepcopy(checked[key])
        # Only the monitors this window showed a working switch for; any
        # other monitor's choice, from the tray, is left as it is.
        live = self._settings.setdefault("strip_monitors", {})
        for key, box in self._monitor_boxes.items():
            if box.isEnabled():
                live[key] = box.isChecked()

        if autostart.is_available() and self._start_on.isChecked() != autostart.is_enabled():
            try:
                now_on = autostart.set_enabled(self._start_on.isChecked())
                self._settings[autostart.CHOICE_KEY] = bool(now_on)
            except OSError:
                self._start_on.setChecked(autostart.is_enabled())

        settings.save(self._settings)
        self.set_unconfirmed([])          # the choice is now on record
        self._draft = copy.deepcopy(self._settings)
        self.applied.emit()
        self.chosen.emit(clean)
        return clean

    def _on_save(self) -> None:
        self._apply()

    def _done(self) -> None:
        self._apply()
        # hide(), not close(): closing would deliver a close event and look
        # like the window was dismissed, on top of the choice just made.
        self.hide()
        self.finished.emit()

    def closeEvent(self, event) -> None:
        """Cancel, the X, Esc or Alt+F4.

        Nothing is saved and nothing is switched on. The draft is thrown
        away; the next opening starts again from the live settings. dismissed
        says "no change", which is what closing without saving means.
        """
        super().closeEvent(event)
        self.dismissed.emit()
        self.finished.emit()

    def open_centred(self, section: str | None = None) -> None:
        """Open on this screen, sized to fit it, with the settings as they are."""
        self._load_draft()
        first = not registry.has_been_set_up(self._settings)
        self.show_section(section or ("Services" if first else self.current_section()))
        area = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        width = min(SIZE_WANTED.width(), area.width() - 40)
        height = min(SIZE_WANTED.height(), area.height() - 40)
        self.setMinimumSize(min(SIZE_SMALLEST.width(), width),
                            min(SIZE_SMALLEST.height(), height))
        self.resize(width, height)
        self.move(area.center().x() - width // 2, area.center().y() - height // 2)
        self.show()
        self._fit_intro()
        self.raise_()
        self.activateWindow()
        self._nav.setFocus()


# The old name, for anything still using it.
SetupWindow = SettingsWindow


class SettingsHolder(QtCore.QObject):
    """Opens the Settings window when asked, and lets it go when it closes.

    A new window is built each time Settings is opened and deleted as soon as
    it finishes, by Done, Cancel, the X, Esc or Alt+F4, so a closed Settings
    window takes no memory. Its signals are passed on from here, so anything
    connected to the holder stays connected across new windows.
    """

    chosen = QtCore.Signal(list)
    dismissed = QtCore.Signal()
    applied = QtCore.Signal()

    def __init__(self, values: dict, targets=None, readings=None) -> None:
        super().__init__()
        self._values = values
        self._targets = targets
        self._readings = readings
        self._unconfirmed: list[str] = []
        self._window: SettingsWindow | None = None

    def window(self) -> SettingsWindow | None:
        """The window, while there is one."""
        return self._window

    def _ensure(self) -> SettingsWindow:
        if self._window is None:
            window = SettingsWindow(self._values, targets=self._targets,
                                    readings=self._readings)
            window.chosen.connect(self.chosen)
            window.dismissed.connect(self.dismissed)
            window.applied.connect(self._on_applied)
            window.finished.connect(self._release)
            if self._unconfirmed:
                window.set_unconfirmed(self._unconfirmed)
            self._window = window
        return self._window

    def _on_applied(self) -> None:
        self._unconfirmed = []            # the choice is now on record
        self.applied.emit()

    def _release(self) -> None:
        window, self._window = self._window, None
        if window is not None:
            window.deleteLater()

    def open_centred(self, section: str | None = None) -> None:
        self._ensure().open_centred(section)

    def check_taskbar_room(self, targets=None) -> list[str]:
        """First run: see SettingsWindow.check_taskbar_room. The window it
        builds for the message is the one the first run then opens."""
        return self._ensure().check_taskbar_room(targets)

    def set_unconfirmed(self, keys: list[str]) -> None:
        self._unconfirmed = list(keys)
        if self._window is not None:
            self._window.set_unconfirmed(self._unconfirmed)


_STYLE = f"""
    QWidget {{ background: {GLASS.name()}; color: {GLASS_INK}; }}
    QLabel, QCheckBox {{ background: transparent; }}
    QToolTip {{ background: #2b2f36; color: {GLASS_INK}; border: 1px solid #555b66;
               padding: 4px; }}
    QListWidget#nav {{ background: #1c1f24; border: none; padding-top: 10px;
                       font-size: 13px; }}
    QListWidget#nav::item {{ padding: 8px 14px; }}
    QListWidget#nav::item:selected {{ background: #2e3542; color: {GLASS_INK};
                                      border-left: 3px solid #2fb35a; }}
    QListWidget#order {{ background: {CHIP_BG}; border-radius: 6px; }}
    QListWidget#order::item {{ padding: 4px 8px; }}
    QListWidget#order::item:selected {{ background: #2e3542; }}
    QLabel#title {{ font-size: 15px; font-weight: 700; margin-top: 4px; }}
    QLabel#hint {{ color: {GLASS_MUTED}; }}
    QLabel#introHead {{ font-weight: 700; }}
    QLabel#introBody {{ color: {GLASS_MUTED}; }}
    QFrame#intro {{ background: #23272e; border-radius: 8px; }}
    QFrame#preview {{ background: #1c1f24; border-top: 1px solid #2e3542; }}
    QLabel#dayline {{ color: {GLASS_INK}; font-weight: 600; margin-left: 2px; }}
    QRadioButton {{ font-size: 13px; padding: 2px; background: transparent; }}
    QRadioButton::indicator {{ width: 12px; height: 12px; border-radius: 8px;
                               border: 2px solid #8a909a; background: transparent; }}
    QRadioButton::indicator:hover {{ border-color: {GLASS_INK}; }}
    QRadioButton::indicator:checked {{
        border-color: #2fb35a;
        background: qradialgradient(cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5,
                                    stop: 0 #2fb35a, stop: 0.55 #2fb35a,
                                    stop: 0.65 transparent, stop: 1 transparent); }}
    QTimeEdit {{ background: #3a4049; border: none; border-radius: 6px;
                 padding: 3px 6px; color: {GLASS_INK}; }}
    QTimeEdit:disabled {{ color: #6b7079; }}
    QFrame#taskbar {{ background: #202226; border-radius: 4px; }}
    QLabel#room {{ background: #2e3542; border-radius: 8px; padding: 10px 12px;
                   color: #cfd8e3; }}
    QLabel#warning {{ background: #4a2f2f; border-radius: 8px; padding: 10px 12px;
                      color: #ffd9d9; }}
    QFrame#row {{ background: {CHIP_BG}; border-radius: 8px; }}
    QCheckBox {{ font-size: 13px; padding: 2px; }}
    QCheckBox:disabled {{ color: #6b7079; }}
    QSpinBox {{ background: #3a4049; border: none; border-radius: 6px;
                padding: 3px 6px; color: {GLASS_INK}; }}
    QPushButton {{ background: #3a4049; border: none; border-radius: 6px;
                   padding: 5px 12px; color: {GLASS_INK}; }}
    QPushButton:hover {{ background: #464d58; }}
    QPushButton:focus, QToolButton:focus, QCheckBox:focus, QRadioButton:focus,
    QSlider:focus, QTimeEdit:focus {{
        outline: 1px solid #56b4e9; }}
    QPushButton:disabled {{ color: #6b7079; }}
    QPushButton#done {{ background: #2fb35a; font-weight: 700; }}
    QPushButton#done:hover {{ background: #37c667; }}
    QScrollArea {{ border: none; }}
"""
