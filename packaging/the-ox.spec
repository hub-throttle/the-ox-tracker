# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for The Ox Tracker. Built by packaging\\build.ps1.

One folder, no console, no UPX. The rest of this file is about leaving
things out: PyInstaller's Qt hooks copy in every plugin, translation and
module PySide6 might need, and this app needs very few of them. It imports
only QtCore, QtGui and QtWidgets, draws everything itself, loads one .ico,
and does its networking with requests, not Qt.

Each removal below was tested by building without it and running the app.
After filtering, check_dependencies() reads the import table of every DLL
and .pyd that is left, and stops the build if any of them needs a DLL that
was removed, so a removal can never quietly break a load.
"""
import os
import re

ROOT = os.path.dirname(SPECPATH)            # noqa: F821 - defined by PyInstaller
APP = "The Ox Tracker"

# Python modules nothing in the app uses. Several are only here because a
# standard-library module mentions them in a code path the app never takes.
EXCLUDES = [
    # Build tools. setuptools is in the build venv, so PyInstaller adds its
    # runtime hook and with it setuptools itself and packaging.
    "setuptools", "_distutils_hack", "pkg_resources", "packaging",
    # Qt modules the app never imports. QtNetwork brings Qt6Network.dll and
    # the tls and networkinformation plugins.
    "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtSvg", "PySide6.QtPdf",
    # Interactive and developer tools.
    "_pyrepl", "pydoc", "pydoc_data", "pdb", "doctest", "unittest", "tkinter",
    "lib2to3", "turtle", "curses",
    # Unused parts of the standard library. multiprocessing brings
    # _multiprocessing.pyd; xml brings pyexpat.pyd; the compressors their
    # .pyd files. urllib3 treats zstd as optional and simply does not offer it.
    "multiprocessing", "xmlrpc", "xml", "http.server", "sqlite3", "tomllib",
    "bz2", "lzma", "compression.zstd", "_zstd",
    # pywin32's event-log modules, only for a logging handler not used here.
    "win32evtlog", "win32evtlogutil",
]

# Files the Qt hooks collect that the app does not use, by their path inside
# the app folder. Matched case-insensitively against forward-slash paths.
DROP = [
    r"pyside6/opengl32sw\.dll",                    # software OpenGL; no OpenGL here
    r"pyside6/qt6(quick|qml|virtualkeyboard|pdf|svg|opengl|network)[^/]*\.dll",
    r"pyside6/translations/.*",                    # Qt's own UI translations
    r"pyside6/plugins/platforminputcontexts/.*",   # the on-screen keyboard
    r"pyside6/plugins/imageformats/(?!qico\.dll$).*",   # only .ico is loaded
    r"pyside6/plugins/iconengines/.*",             # SVG icons
    r"pyside6/plugins/generic/.*",                 # TUIO touch input
    r"pyside6/plugins/platforms/(?!qwindows\.dll$).*",  # only the Windows one
    r"pyside6/plugins/tls/.*",                     # Qt networking's TLS
    r"pyside6/plugins/networkinformation/.*",      # Qt networking
    r"setuptools/.*",                              # in case its data slips in
]
_DROP = re.compile("^(?:" + "|".join(DROP) + ")$", re.IGNORECASE)


def _kept(toc, removed):
    out = []
    for entry in toc:
        dest = entry[0].replace("\\", "/")
        if _DROP.match(dest):
            removed.append(dest)
        else:
            out.append(entry)
    return out


def check_dependencies(binaries):
    """Stop the build if a kept DLL or .pyd imports a DLL that is not there.

    A DLL counts as there if it is being shipped, or is part of Windows.
    """
    import pefile

    shipped = {os.path.basename(dest).lower() for dest, *_ in binaries}
    system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    missing = []
    for dest, source, *_ in binaries:
        if not dest.lower().endswith((".dll", ".pyd")):
            continue
        pe = pefile.PE(source, fast_load=True)
        pe.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
        for table in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
            for item in getattr(pe, table, []):
                name = item.dll.decode("ascii", "replace").lower()
                if (name in shipped or name.startswith(("api-ms-win-", "ext-ms-"))
                        or os.path.exists(os.path.join(system, name))):
                    continue
                missing.append(f"{dest} needs {name}")
        pe.close()
    if missing:
        raise SystemExit("Trimmed too far; a kept file needs a removed one:\n  "
                         + "\n  ".join(sorted(set(missing))))


a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[],
    binaries=[],
    datas=[(os.path.join(ROOT, "the_ox", "assets"), os.path.join("the_ox", "assets"))],
    # pywin32 imports win32timezone from C whenever it hands back a file time,
    # which reading the Startup-folder shortcut's path does; PyInstaller
    # cannot see that import. Without it every read failed in the installed
    # app, so its own shortcut looked wrong at each start.
    hiddenimports=["win32timezone"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

if not any(name == "win32timezone" for name, *_ in a.pure):
    raise SystemExit("win32timezone is missing; the Startup shortcut could not be read.")

removed = []
a.binaries = _kept(a.binaries, removed)
a.datas = _kept(a.datas, removed)
check_dependencies(a.binaries)
with open(os.path.join(ROOT, "build", "trimmed.txt"), "w", encoding="utf-8") as out:
    out.write("\n".join(sorted(removed)) + "\n")
print(f"trimmed {len(removed)} files; list in build\\trimmed.txt")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[os.path.join(ROOT, "the_ox", "assets", "ox.ico")],
    manifest=os.path.join(ROOT, "build", "the-ox.manifest"),   # stamped by build.ps1
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP,
)
