r"""The application icon: the bull.

the_ox/assets/ox.ico is used exactly as it is: six sizes (16, 32, 48, 64,
128 and 256 pixels), nothing redrawn. It has no metadata to strip; every
image in it holds only its size, its pixels and an end marker.

Where it is used:
  the .exe itself, and so the Start menu shortcut and the Installed apps
  entry                                  build.ps1 passes it to PyInstaller
  the installer                          build.ps1 passes it to Inno Setup
  every window's icon, and so the taskbar button of any window that has one
                                         run.py, QApplication.setWindowIcon
  the top of the setup screen            setup_window.py

Not the tray icon, which stays the two level lines drawn in tray.py.

From source the file is read from this folder. In the installed app,
PyInstaller copies it to the same place relative to the_ox package, inside
the app's own folder, so the same path works in both.
"""
from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui

ICON_PATH = Path(__file__).resolve().parent / "assets" / "ox.ico"


def app_icon() -> QtGui.QIcon:
    """The bull, at every size the .ico holds. Empty if the file is missing."""
    return QtGui.QIcon(str(ICON_PATH)) if ICON_PATH.is_file() else QtGui.QIcon()


def logo_pixmap(size: int, device_pixel_ratio: float = 1.0) -> QtGui.QPixmap:
    """The bull for showing inside a window, sharp on a scaled screen.

    QIcon picks the image in the .ico closest to the size actually needed
    and only ever scales that one; at 100% a 64 pixel logo is the .ico's own
    64 pixel image, untouched.
    """
    return app_icon().pixmap(QtCore.QSize(size, size), device_pixel_ratio)
