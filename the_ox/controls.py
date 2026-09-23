r"""The building blocks of the Settings window.

  NumberControl    a slider and a number box that always agree. A typed
                   number outside the allowed range snaps to the nearest end
                   of it, and a short note beside the box says so.
  ColourRow        preset swatches, "Custom...", "Reset to default", the
                   bright and muted versions side by
                   side, and a plain warning when the colour is too close to
                   another.
  FastCheckDialog  the warning shown every time checking is set more often
                   than every 3 minutes. OK stays disabled until the box is
                   ticked.

Every control is reachable by keyboard and has a short tooltip.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import appearance

NOTE_STYLE = "color: #f0c36a;"          # amber-ish, readable on the dark window


class NumberControl(QtWidgets.QWidget):
    """A slider with a number box beside it. Moving either updates the other.

    valueChanged fires as the value moves (for the live preview).
    committed fires once a value is settled: the slider let go, a key
    pressed on it, or the box's number entered. The fast-checking warning
    waits for committed, so dragging past 3 does not open it half way.
    """

    valueChanged = QtCore.Signal(int)
    committed = QtCore.Signal(int)

    def __init__(self, low: int, high: int, value: int, unit: str = "",
                 tooltip: str = "", parent=None) -> None:
        super().__init__(parent)
        self._low, self._high = low, high
        self._value = min(max(value, low), high)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(low, high)
        self.slider.setValue(self._value)
        self.slider.setPageStep(max(1, (high - low) // 10))
        self.slider.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.slider.setMinimumWidth(160)

        # The box accepts any whole number while typing, so an out-of-range
        # value can be caught and snapped with an explanation, rather than
        # the keystroke simply being refused with no word as to why.
        self.box = QtWidgets.QSpinBox()
        self.box.setRange(-99999, 99999)
        self.box.setValue(self._value)
        self.box.setKeyboardTracking(False)      # act on Enter, not each digit
        self.box.setButtonSymbols(QtWidgets.QAbstractSpinBox.UpDownArrows)
        self.box.setFixedWidth(96)
        if unit:
            self.box.setSuffix(f" {unit}")

        self.note = QtWidgets.QLabel("")
        self.note.setStyleSheet(NOTE_STYLE)
        self.note.setWordWrap(True)

        if tooltip:
            self.slider.setToolTip(tooltip)
            self.box.setToolTip(tooltip + f" Type {low} to {high}.")
            self.setToolTip(tooltip)

        line = QtWidgets.QHBoxLayout(self)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(10)
        line.addWidget(self.slider, 1)
        line.addWidget(self.box)
        line.addWidget(self.note, 1)

        self.slider.valueChanged.connect(self._from_slider)
        self.slider.sliderReleased.connect(lambda: self.committed.emit(self._value))
        self.box.valueChanged.connect(self._from_box)

    # ----- public ----------------------------------------------------------
    def value(self) -> int:
        return self._value

    def setValue(self, value: int, quiet: bool = True) -> None:
        """Set both halves. quiet: no signals, as when loading settings."""
        value = min(max(int(value), self._low), self._high)
        self._value = value
        for widget in (self.slider, self.box):
            was = widget.blockSignals(True)
            widget.setValue(value)
            widget.blockSignals(was)
        if not quiet:
            self.valueChanged.emit(value)

    def setRange(self, low: int, high: int) -> None:
        """Change what is allowed. A value now outside it is pulled back in."""
        self._low, self._high = low, high
        was = self.slider.blockSignals(True)
        self.slider.setRange(low, high)
        self.slider.blockSignals(was)
        if not low <= self._value <= high:
            self.setValue(self._value, quiet=False)
            self.committed.emit(self._value)
        else:
            self.setValue(self._value)

    def range(self) -> tuple[int, int]:
        return self._low, self._high

    def set_note(self, text: str) -> None:
        self.note.setText(text)

    # ----- the two halves --------------------------------------------------
    def _from_slider(self, value: int) -> None:
        self._value = value
        was = self.box.blockSignals(True)
        self.box.setValue(value)
        self.box.blockSignals(was)
        self.note.setText("")
        self.valueChanged.emit(value)
        if not self.slider.isSliderDown():       # keys or a click, not a drag
            self.committed.emit(value)

    def _from_box(self, typed: int) -> None:
        snapped = min(max(typed, self._low), self._high)
        if snapped != typed:
            end = "most" if typed > self._high else "least"
            self.note.setText(f"Snapped to {snapped}, the {end} allowed.")
            was = self.box.blockSignals(True)
            self.box.setValue(snapped)
            self.box.blockSignals(was)
        else:
            self.note.setText("")
        self._value = snapped
        was = self.slider.blockSignals(True)
        self.slider.setValue(snapped)
        self.slider.blockSignals(was)
        self.valueChanged.emit(snapped)
        self.committed.emit(snapped)


def _swatch_icon(colour: str, size: int = 16) -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtGui.QColor(colour))
    painter = QtGui.QPainter(pixmap)
    painter.setPen(QtGui.QColor(255, 255, 255, 90))
    painter.drawRect(0, 0, size - 1, size - 1)
    painter.end()
    return QtGui.QIcon(pixmap)


class ColourRow(QtWidgets.QFrame):
    """Choose one colour: presets, custom, or reset to the default.

    changed carries the chosen "#rrggbb", or "" for "the default".
    """

    changed = QtCore.Signal(str)

    def __init__(self, title: str, default: str, show_muted: bool = True,
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("row")
        self._title = title
        self._default = default
        self._chosen: str | None = None           # None: the default
        self._show_muted = show_muted

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)

        top = QtWidgets.QHBoxLayout()
        self.name = QtWidgets.QLabel(title)
        self.name.setMinimumWidth(110)
        top.addWidget(self.name)
        self.bright = QtWidgets.QLabel()
        self.bright.setFixedSize(34, 16)
        self.bright.setToolTip("How it looks in the biscuit and the panel")
        top.addWidget(self.bright)
        self.muted = QtWidgets.QLabel()
        self.muted.setFixedSize(34, 16)
        self.muted.setToolTip("How it looks on the taskbar strip, where colors "
                              "are quieter")
        self.muted.setVisible(show_muted)
        top.addWidget(self.muted)
        self.hex = QtWidgets.QLabel("")
        self.hex.setMinimumWidth(70)
        top.addWidget(self.hex)
        top.addStretch(1)
        self.custom = QtWidgets.QPushButton("Custom...")
        self.custom.setToolTip(f"Pick any color for {title}")
        self.custom.clicked.connect(self._pick)
        top.addWidget(self.custom)
        self.reset = QtWidgets.QPushButton("Reset to default")
        self.reset.setToolTip(f"Go back to {title}'s original color, {default}")
        self.reset.clicked.connect(lambda: self.set_value(None, emit=True))
        top.addWidget(self.reset)
        outer.addLayout(top)

        swatches = QtWidgets.QHBoxLayout()
        swatches.setSpacing(4)
        self.buttons: list[QtWidgets.QToolButton] = []
        for name, colour in appearance.PRESETS:
            button = QtWidgets.QToolButton()
            button.setIcon(_swatch_icon(colour))
            button.setIconSize(QtCore.QSize(16, 16))
            button.setAutoRaise(True)
            button.setFocusPolicy(QtCore.Qt.StrongFocus)
            button.setToolTip(f"{name} {colour}")
            button.setAccessibleName(f"{title}: {name}")
            button.clicked.connect(lambda _c=False, c=colour: self.set_value(c, emit=True))
            button.setProperty("colour", colour)
            self.buttons.append(button)
            swatches.addWidget(button)
        swatches.addStretch(1)
        outer.addLayout(swatches)

        self.warning = QtWidgets.QLabel("")
        self.warning.setStyleSheet(NOTE_STYLE)
        self.warning.setWordWrap(True)
        self.warning.setVisible(False)
        outer.addWidget(self.warning)
        self._repaint()

    # ----- public ----------------------------------------------------------
    def value(self) -> str | None:
        """The chosen colour, or None for the default."""
        return self._chosen

    def colour(self) -> str:
        """What is actually in use: the chosen colour, or the default."""
        return self._chosen or self._default

    def set_value(self, colour: str | None, emit: bool = False) -> None:
        if colour is not None and not appearance.is_hex(colour):
            colour = None
        if colour is not None and colour.lower() == self._default.lower():
            colour = None                      # choosing the default IS the default
        self._chosen = colour.lower() if colour else None
        self._repaint()
        if emit:
            self.changed.emit(self._chosen or "")

    def set_muted(self, colour: str) -> None:
        """The strip colour to show beside the bright one."""
        self.muted.setStyleSheet(f"background: {colour}; border-radius: 3px;")

    def set_warning(self, notes: list[str]) -> None:
        if notes:
            self.warning.setText(f"{self._title} is " + ", and ".join(notes)
                                 + ". It may be hard to tell apart; you can "
                                 "still use it.")
        self.warning.setVisible(bool(notes))

    # ----- inside ----------------------------------------------------------
    def _repaint(self) -> None:
        current = self.colour()
        self.bright.setStyleSheet(f"background: {current}; border-radius: 3px;")
        self.name.setStyleSheet(f"color: {current}; font-weight: 700;")
        self.hex.setText(current + ("" if self._chosen else " (default)"))
        self.reset.setEnabled(self._chosen is not None)
        for button in self.buttons:
            picked = button.property("colour").lower() == current.lower()
            button.setStyleSheet("QToolButton { border: 2px solid #f2f4f7; }"
                                 if picked else "")

    def _pick(self) -> None:
        colour = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(self.colour()), self, f"Color for {self._title}")
        if colour.isValid():
            self.set_value(colour.name(), emit=True)


FAST_WARNING = (
    "Checking more often than every 3 minutes sends more automated requests "
    "to each service. Services may detect frequent automated access and could "
    "limit, suspend or ban your account. The Ox Tracker can't protect you from "
    "that. Only continue if you accept that risk."
)


class FastCheckDialog(QtWidgets.QDialog):
    """The warning for checking more often than every 3 minutes."""

    def __init__(self, minutes: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Check more often?")
        self.setModal(True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)

        heading = QtWidgets.QLabel(
            f"Check every {minutes} minute{'s' if minutes != 1 else ''}?")
        heading.setStyleSheet("font-weight: 700; font-size: 14px;")
        layout.addWidget(heading)

        self.text = QtWidgets.QLabel(FAST_WARNING)
        self.text.setWordWrap(True)
        self.text.setMinimumWidth(380)
        layout.addWidget(self.text)

        self.accept_box = QtWidgets.QCheckBox("I understand and accept this risk")
        self.accept_box.setToolTip("Tick this to allow checking this often")
        layout.addWidget(self.accept_box)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        self.ok = buttons.button(QtWidgets.QDialogButtonBox.Ok)
        self.cancel = buttons.button(QtWidgets.QDialogButtonBox.Cancel)
        self.ok.setEnabled(False)
        self.ok.setToolTip("Available once the box above is ticked")
        self.cancel.setToolTip("Go back to the previous setting")
        self.cancel.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.accept_box.toggled.connect(self.ok.setEnabled)
        layout.addWidget(buttons)
