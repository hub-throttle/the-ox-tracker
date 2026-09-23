r"""What happens before anything else: the environment, and one copy only.

  Every Qt, PySide and OpenSSL variable that can steer what gets loaded is
  removed, and that happens in run.py before PySide6 is imported, not after.
  In the installed app, the current folder is kept off the import path.
  A second launch while a copy is running exits at once and starts nothing,
  leaving one line in the log to say why.

Nothing here reads a login, contacts a service, or touches the real
settings file, log or registry.
"""
from __future__ import annotations

import ast
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from the_ox import hardening, single_instance      # noqa: E402

PYTHON = sys.executable
fails: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


def child_env(**extra) -> dict[str, str]:
    """This process's environment plus some extras, for a child Python."""
    env = dict(os.environ)
    env.update(extra)
    return env


def environment() -> None:
    print("The environment is scrubbed")
    planted = {
        "QT_PLUGIN_PATH": r"C:\somewhere\plugins",
        "QT_QPA_PLATFORM_PLUGIN_PATH": r"C:\somewhere\platforms",
        "QT_QPA_PLATFORM": "offscreen",
        "QT_QPA_PLATFORMTHEME": "evil",
        "QT_STYLE_OVERRIDE": "evil",
        "QT_OPENGL": "software",
        "QML2_IMPORT_PATH": r"C:\somewhere\qml",
        "QML_IMPORT_PATH": r"C:\somewhere\qml",
        "QTWEBENGINEPROCESS_PATH": r"C:\somewhere\x.exe",
        "PYSIDE_DESIGNER_PLUGINS": r"C:\somewhere\py",
        "SHIBOKEN_ANYTHING": "x",
        "qt_plugin_path": r"C:\lower\case",       # Windows ignores case
        # OpenSSL: a config file can load a DLL into the process, and the
        # certificate ones would add certificates to trust.
        "OPENSSL_CONF": r"C:\somewhere\openssl.cnf",
        "OPENSSL_MODULES": r"C:\somewhere\modules",
        "OPENSSL_ENGINES": r"C:\somewhere\engines",
        "SSL_CERT_FILE": r"C:\somewhere\cert.pem",
        "SSL_CERT_DIR": r"C:\somewhere\certs",
        "openssl_conf": r"C:\lower\case.cnf",
    }
    kept = {
        "PATH": r"C:\Windows\System32",
        "USERPROFILE": r"D:\Profiles\someone",
        "LOCALAPPDATA": r"D:\Profiles\someone\AppData\Local",
        "QTDIR_NOT_A_PREFIX": "left alone",       # not a QT_ name
        "MY_QT_SETTING": "left alone",            # QT_ in the middle only
        "OPENSSL_CONFIG_NOTE": "left alone",      # not one of OpenSSL's names
    }
    env = dict(planted, **kept)
    removed = hardening.scrub_environment(env)
    check("every planted Qt, PySide and OpenSSL variable is gone",
          not any(name in env for name in planted), str(sorted(env)))
    check("the ones named in the brief are gone by name",
          "QT_PLUGIN_PATH" not in env and "QT_QPA_PLATFORM_PLUGIN_PATH" not in env
          and "OPENSSL_CONF" not in env)
    check("the earlier name still does the same thing",
          hardening.scrub_qt_environment is hardening.scrub_environment)
    check("everything else is left alone", env == kept, str(env))
    check("it reports what it removed, names only",
          sorted(removed) == sorted(planted), str(removed))
    check("the reported names carry no values",
          not any("somewhere" in name for name in removed))


def ordering() -> None:
    print("\nrun.py scrubs before PySide6 is imported")
    tree = ast.parse((ROOT / "run.py").read_text(encoding="utf-8"))
    body = [node for node in tree.body
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
            and not (isinstance(node, ast.ImportFrom) and node.module == "__future__")]

    def names(node) -> list[str]:
        if isinstance(node, ast.Import):
            return [alias.name for alias in node.names]
        if isinstance(node, ast.ImportFrom):
            return [node.module or ""]
        return []

    def is_scrub_call(node) -> bool:
        return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr in ("scrub_environment",
                                             "scrub_qt_environment"))

    first_import = body[0]
    check("the first thing run.py does is import hardening",
          isinstance(first_import, ast.ImportFrom)
          and first_import.module == "the_ox"
          and [a.name for a in first_import.names] == ["hardening"],
          ast.unparse(first_import))
    check("and the second is the scrub itself", is_scrub_call(body[1]),
          ast.unparse(body[1]))
    scrub_at = next(i for i, node in enumerate(body) if is_scrub_call(node))
    before = [n for node in body[:scrub_at] for n in names(node)]
    check("nothing else is imported before it", before == ["the_ox"], str(before))
    pyside_at = next(i for i, node in enumerate(body)
                     if any(n.startswith("PySide6") for n in names(node)))
    check("PySide6 comes after it", pyside_at > scrub_at,
          f"scrub at {scrub_at}, PySide6 at {pyside_at}")

    print("\n  and it really works, in a fresh process")
    planted_dir = tempfile.mkdtemp(prefix="theox-planted-")
    env = child_env(QT_PLUGIN_PATH=planted_dir,
                    QT_QPA_PLATFORM_PLUGIN_PATH=planted_dir,
                    PYSIDE_DESIGNER_PLUGINS=planted_dir)
    probe = (
        "import os, sys; sys.path.insert(0, {root!r}); {body}; "
        "from PySide6 import QtCore; "
        "norm = lambda p: os.path.normcase(os.path.abspath(p)); "
        "paths = [norm(p) for p in QtCore.QCoreApplication.libraryPaths()]; "
        # Only the three planted here. PySide6 sets PYSIDE6_OPTION_PYTHON_ENUM
        # for itself while it imports, after the scrub, and that is its own
        # enum switch rather than something the environment handed it.
        "print('LEFT', sorted(k for k in os.environ if k in "
        "('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'PYSIDE_DESIGNER_PLUGINS'))); "
        "print('PLANTED_IN_QT', norm({planted!r}) in paths)"
    )
    # Control: PySide6 on its own DOES honour the planted path, which is what
    # makes the scrub worth having and this test worth running.
    control = subprocess.run(
        [PYTHON, "-c", probe.format(root=str(ROOT), body="pass", planted=planted_dir)],
        env=env, capture_output=True, text=True, timeout=120)
    check("control: without the scrub, Qt would search the planted folder",
          "PLANTED_IN_QT True" in control.stdout,
          control.stdout.strip().replace("\n", " | ") or control.stderr[-200:])
    real = subprocess.run(
        [PYTHON, "-c", probe.format(root=str(ROOT), body="import run", planted=planted_dir)],
        env=env, capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    check("importing run.py removes them before Qt looks",
          "LEFT []" in real.stdout,
          real.stdout.strip().replace("\n", " | ") or real.stderr[-300:])
    check("and Qt never searches the planted folder",
          "PLANTED_IN_QT False" in real.stdout,
          real.stdout.strip().replace("\n", " | ") or real.stderr[-300:])

    print("\n  and still works when PySide6 was imported first")
    # In the installed app, PyInstaller's own PySide6 start-up hook imports
    # QtCore before run.py runs a single line, so "before PySide6 is
    # imported" cannot literally hold there. What matters is that Qt reads
    # these variables later, when the application object is created and
    # plugins are loaded, which is still after the scrub. This reproduces
    # that order: import QtCore, THEN import run, then build the app.
    # QT_QPA_PLATFORM=offscreen is a real, harmless plugin, so if the scrub
    # came too late Qt would visibly use it instead of "windows".
    frozen_order = (
        "import os, sys; sys.path.insert(0, {root!r}); "
        "from PySide6 import QtCore; "              # what the hook does
        "{body}; "
        "from PySide6 import QtWidgets; "
        "norm = lambda p: os.path.normcase(os.path.abspath(p)); "
        "app = QtWidgets.QApplication(['probe']); "
        "print('PLATFORM', app.platformName()); "
        "print('PLANTED_IN_QT', norm({planted!r}) in "
        "[norm(p) for p in QtCore.QCoreApplication.libraryPaths()])"
    )
    env2 = child_env(QT_PLUGIN_PATH=planted_dir, QT_QPA_PLATFORM="offscreen")
    control2 = subprocess.run(
        [PYTHON, "-c", frozen_order.format(root=str(ROOT), body="pass",
                                           planted=planted_dir)],
        env=env2, capture_output=True, text=True, timeout=120)
    check("control: left alone, Qt uses the planted platform and folder",
          "PLATFORM offscreen" in control2.stdout
          and "PLANTED_IN_QT True" in control2.stdout,
          control2.stdout.strip().replace("\n", " | ") or control2.stderr[-200:])
    late = subprocess.run(
        [PYTHON, "-c", frozen_order.format(root=str(ROOT), body="import run",
                                           planted=planted_dir)],
        env=env2, capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    check("scrubbed after QtCore is imported, Qt still uses 'windows'",
          "PLATFORM windows" in late.stdout,
          late.stdout.strip().replace("\n", " | ") or late.stderr[-300:])
    check("and still never searches the planted folder",
          "PLANTED_IN_QT False" in late.stdout,
          late.stdout.strip().replace("\n", " | ") or late.stderr[-300:])


def safe_path() -> None:
    print("\nThe installed app keeps the current folder off the import path")
    check("from source nothing is removed", hardening.safe_import_path() == [])
    saved_path, saved_frozen = list(sys.path), getattr(sys, "frozen", None)
    saved_meipass = getattr(sys, "_MEIPASS", None)
    bundle = tempfile.mkdtemp(prefix="theox-bundle-")
    here = os.getcwd()
    try:
        sys.frozen = True
        sys._MEIPASS = bundle
        sys.path[:] = ["", bundle, here, "."]
        dropped = hardening.safe_import_path()
        check("an empty entry is removed", "" not in sys.path)
        check("so is '.'", "." not in sys.path)
        check("so is the current folder", here not in sys.path, str(sys.path))
        check("the app's own bundle stays", bundle in sys.path, str(sys.path))
        check("and it says what it removed", set(dropped) == {"", ".", here},
              str(dropped))
    finally:
        sys.path[:] = saved_path
        if saved_frozen is None:
            del sys.frozen
        else:
            sys.frozen = saved_frozen
        if saved_meipass is None:
            del sys._MEIPASS
        else:
            sys._MEIPASS = saved_meipass


def one_copy() -> None:
    print("\nOne copy per user")
    name = f"Local\\TheOxTrackerTest-{uuid.uuid4().hex}"
    holder_code = (
        "import sys; sys.path.insert(0, {root!r}); "
        "from the_ox import single_instance as s; "
        "print(s.acquire({name!r}, installer_name=None), flush=True); "
        "sys.stdin.read()"
    ).format(root=str(ROOT), name=name)
    holder = subprocess.Popen([PYTHON, "-c", holder_code], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True)
    try:
        first = holder.stdout.readline().strip()
        check("the first copy gets it", first == "True", first)
        check("a second copy is refused",
              single_instance.acquire(name, installer_name=None) is False)
    finally:
        holder.stdin.close()
        holder.wait(timeout=30)
    check("once the first copy has gone, it is free again",
          single_instance.acquire(name, installer_name=None) is True)
    single_instance.release()

    real_name = single_instance.default_name()
    check("the real name is per user and in the session namespace",
          real_name.startswith("Local\\TheOxTracker-"), real_name)
    check("it is the same every time", real_name == single_instance.default_name())
    check("and does not spell out who the user is",
          os.environ.get("USERNAME", "\0").lower() not in real_name.lower())

    print("\n  and run.py leaves quietly when a copy is already running")
    # Hold the real guard, exactly as a running copy would. If the installed
    # app happens to be running already it holds it instead, which proves
    # the same thing.
    single_instance.acquire(installer_name=None)
    temp = pathlib.Path(tempfile.mkdtemp(prefix="theox-second-"))
    # If the guard ever failed, this second copy must not become a real one:
    # its settings and log are sent somewhere temporary, and reaching the
    # point where the windows would be created exits with code 99 instead.
    second = (
        "import sys; sys.path.insert(0, {root!r}); sys.argv = ['run.py']; "
        "import run; "
        "from the_ox import logs, settings; "
        "settings.set_path_override({settings!r}); logs.set_path_override({log!r}); "
        "run.QtWidgets.QApplication = lambda *a, **k: sys.exit(99); "
        "sys.exit(run.main())"
    ).format(root=str(ROOT), settings=str(temp / "settings.json"),
             log=str(temp / "the-ox.log"))
    began = time.monotonic()
    result = subprocess.run([PYTHON, "-c", second], capture_output=True,
                            text=True, timeout=120, cwd=str(ROOT))
    took = time.monotonic() - began
    single_instance.release()
    check("the second launch exits cleanly", result.returncode == 0,
          f"exit {result.returncode} {result.stderr[-200:]}")
    check("without getting as far as opening a window", result.returncode != 99)
    log_file = temp / "the-ox.log"
    lines = (log_file.read_text(encoding="utf-8").splitlines()
             if log_file.exists() else [])
    check("it leaves exactly one line in the log", len(lines) == 1, str(lines))
    check("saying another copy was already running",
          bool(lines) and "already running" in lines[0], lines[0] if lines else "")
    check("and it is quick about it", took < 30, f"{took:.1f}s")

    print("\n  and the refusal to write anywhere synced is a message, not a crash")
    import run                                   # noqa: PLC0415 - test only
    from the_ox import paths                     # noqa: PLC0415
    shown: list[str] = []
    text = run.explain_refusal(paths.UnsafeWritePath("Refusing to write inside a "
                                                     "Google Drive folder: X:\\y"),
                               show=shown.append)
    check("a plain message is shown", shown == [text])
    check("saying it cannot start and why",
          "cannot start" in text and "Google Drive" in text and "copied off" in text,
          text.replace("\n", " ")[:120])


def main() -> int:
    environment()
    ordering()
    safe_path()
    one_copy()
    print()
    if fails:
        for name in fails:
            print("  FAILED:", name)
        return 1
    print("all startup tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
