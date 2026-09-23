r"""Things that must happen before anything else is imported.

run.py calls scrub_environment() as its very first statement, before it
imports PySide6, ssl or anything else. Qt reads these variables when it
builds its list of plugin folders and when the application object is created
and its platform plugin loads, and OpenSSL reads its own when it first starts;
once that has happened, removing them changes nothing, so the scrub has to
come first.

In the installed app one thing does come earlier: PyInstaller's own PySide6
start-up hook imports QtCore before run.py runs at all (and itself sets
QT_PLUGIN_PATH and QML2_IMPORT_PATH to folders inside the app). Importing
QtCore reads none of the variables below, so the scrub still lands in time;
tests/test_startup.py reproduces exactly that order and checks that Qt ends
up on the "windows" platform with none of the planted folders. The app
also logs the platform and plugin folders Qt actually used, at startup.

Why it matters
--------------
A handful of environment variables tell Qt, or PySide6, where to load code
from:

  QT_PLUGIN_PATH                 extra folders searched for Qt plugins
  QT_QPA_PLATFORM_PLUGIN_PATH    where the platform plugin (qwindows.dll) is
  QT_QPA_PLATFORM                which platform plugin to load
  QT_QPA_PLATFORMTHEME           a theme plugin to load on top of it
  QT_QPA_GENERIC_PLUGINS         input plugins to load at startup
  QT_STYLE_OVERRIDE              a style plugin to load in place of ours
  QT_OPENGL                      which OpenGL library to load
  QML2_IMPORT_PATH, QML_IMPORT_PATH   folders QML code is imported from
  PYSIDE_DESIGNER_PLUGINS        Python files PySide6 tools will run

A plugin is a DLL, and loading one runs its code inside this process, the
process that holds the login tokens. So anything that could set one of these
for The Ox Tracker, a stray user variable, another program's installer, a
shortcut with its own environment, could run code of its choosing here.

Rather than keep a list of the dangerous ones up to date, every variable in
the Qt, QML, Qt WebEngine, PySide and Shiboken families is removed. None of
them is needed: the installed app finds its own plugins next to itself, and
from source PySide6 finds them inside its own package. The price is that a
Qt setting someone deliberately put in the environment, a scale factor for
instance, is ignored by this app. That is the right way round.

OpenSSL, which carries every request, is steered the same way:

  OPENSSL_CONF       a configuration file, which can load a provider or an
                     engine: a DLL, run inside this process
  OPENSSL_MODULES    where those providers are looked for
  OPENSSL_ENGINES    where those engines are looked for
  SSL_CERT_FILE      extra certificates to trust
  SSL_CERT_DIR       a folder of them

Measured with the Python and OpenSSL this app ships: OPENSSL_CONF alone
changed the TLS settings of a fresh connection. They go too. None is needed:
requests brings its own certificate list.
"""
from __future__ import annotations

import os
import sys

# Every variable whose name starts with one of these is removed.
SCRUB_PREFIXES = (
    "QT_",
    "QML_",
    "QML2_",
    "QTWEBENGINE",
    "PYSIDE",
    "SHIBOKEN",
)

# Named individually as well, so a reader searching for them finds this file,
# and so a test can prove the ones that matter most are covered by name.
MUST_SCRUB = (
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QT_QPA_PLATFORM",
    "OPENSSL_CONF",
    "OPENSSL_MODULES",
    "OPENSSL_ENGINES",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)

# What was removed, names only, so it can be logged once logging exists.
removed: list[str] = []


def is_scrubbed_name(name: str) -> bool:
    upper = name.upper()
    return upper in MUST_SCRUB or upper.startswith(SCRUB_PREFIXES)


def scrub_environment(environ=None) -> list[str]:
    """Remove every variable that can steer what Qt, PySide or OpenSSL loads.

    Returns the names removed, sorted. Values are never returned or logged:
    there is nothing secret about them, but there is no reason to keep them.
    """
    env = os.environ if environ is None else environ
    names = sorted(name for name in list(env) if is_scrubbed_name(name))
    for name in names:
        del env[name]
    if environ is None:
        removed[:] = names
    return names


# The earlier name, from when only Qt's variables were removed.
scrub_qt_environment = scrub_environment


def is_frozen() -> bool:
    """True when running as the installed, PyInstaller-built app."""
    return bool(getattr(sys, "frozen", False))


def safe_import_path() -> list[str]:
    r"""Keep the current folder off the import path in the installed app.

    Python's "safe path" rule, the -P switch, applied by hand because the
    PyInstaller bootloader does not take that switch. A frozen app imports
    only from its own bundle, but if an empty entry or the current working
    directory ever appeared on sys.path, a file dropped into whatever folder
    the app was started from could be imported in place of a real module.

    Only applied when frozen. From source, run.py's own folder has to stay
    on the path, because that is where the_ox package lives.

    Returns the entries removed.
    """
    if not is_frozen():
        return []
    try:
        cwd = os.path.normcase(os.path.abspath(os.getcwd()))
    except OSError:
        cwd = None
    home = os.path.normcase(os.path.abspath(getattr(sys, "_MEIPASS", "") or "."))
    dropped = []
    for entry in list(sys.path):
        if entry in ("", "."):
            dropped.append(entry)
            continue
        if cwd is not None and os.path.normcase(os.path.abspath(entry)) == cwd \
                and cwd != home:
            dropped.append(entry)
    for entry in dropped:
        while entry in sys.path:
            sys.path.remove(entry)
    return dropped
