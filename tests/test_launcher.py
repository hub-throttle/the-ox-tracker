r"""Launching a vendor's app, and refusing to when it would not be safe.

The Ox Tracker never signs in and never types a credential. All it does is
start the vendor's own command. Three things have to hold for that to stay
harmless:

  what gets run is decided at startup, never from the click-time environment
  the folder it runs in is decided at startup too
  that folder is empty, so the "do you trust this folder?" answer costs
  nothing

The last one is the one with teeth. Every CLI is launched from one folder and
that folder is trusted once. A .claude, .codex or .grok folder left there is
project-level configuration, and a trusted folder's configuration is read.
Settings, hooks and MCP servers all live in files like those.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from the_ox import launcher, registry, settings        # noqa: E402

settings.set_path_override(
    pathlib.Path(tempfile.mkdtemp(prefix="theox-test-")) / "settings.json")

fails: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


def with_env(**pairs):
    previous = {key: os.environ.get(key) for key in pairs}
    for key, value in pairs.items():
        os.environ[key] = value
    return previous


def restore_env(previous):
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def main() -> int:
    print("The four unused path helpers are gone")
    # They duplicated registry.py's executable() and were a second place a
    # launch path could be defined, which is exactly one too many.
    for name in ("claude_exe", "codex_exe", "grok_exe", "grok_bot_exe"):
        check(f"launcher.{name} no longer exists",
              not hasattr(launcher, name))
    check("and nothing else defines a launch path",
          not hasattr(launcher, "_home"))
    # Comments and docstrings may of course still discuss it. What must not
    # exist is code that reads it, so the check is on the syntax tree: every
    # string constant the module actually evaluates.
    import ast
    tree = ast.parse(pathlib.Path(launcher.__file__).read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    live = [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value not in docstrings]
    check("no code in the module reads USERPROFILE",
          not any("USERPROFILE" in text for text in live),
          next((text for text in live if "USERPROFILE" in text), ""))

    print("\nApp locations come from the startup cache, with no fallback")
    launcher._RESOLVED.clear()
    for spec in registry.SERVICES:
        try:
            launcher.resolve(spec.name)
            check(f"{spec.name} before startup is refused", False,
                  "it resolved anyway")
        except launcher.LaunchError as exc:
            check(f"{spec.name} before startup is refused", True,
                  str(exc).split(":")[1].strip()[:44])

    resolved = launcher.resolve_targets()
    check("startup resolves every service",
          len(resolved) == len(registry.SERVICES),
          f"{len(resolved)} of {len(registry.SERVICES)}")

    before = launcher.resolve("Claude")[0]
    moved = with_env(USERPROFILE=str(pathlib.Path(tempfile.mkdtemp())))
    try:
        check("a later USERPROFILE change moves nothing",
              launcher.resolve("Claude")[0] == before, str(before))
        check("and is_available still answers from the cache",
              launcher.is_available("Claude") is before.is_file())
    finally:
        restore_env(moved)

    print("\nThe launch folder is fixed at startup too")
    fixed = launcher.resolve_launch_dir()
    moved = with_env(LOCALAPPDATA=str(pathlib.Path(tempfile.mkdtemp())))
    try:
        check("a later LOCALAPPDATA change moves nothing",
              launcher.launch_dir() == fixed, str(fixed))
    finally:
        restore_env(moved)
    check("and it is inside the app's own data folder",
          fixed.parent.name == "TheOx", str(fixed))

    print("\nA launch folder holding anything else is refused")
    sandbox = pathlib.Path(tempfile.mkdtemp(prefix="theox-launch-"))

    check("an empty folder is fine", launcher.launch_dir_intruders(sandbox) == [],
          str(launcher.launch_dir_intruders(sandbox)))
    (sandbox / launcher.README_NAME).write_text(launcher.README_TEXT,
                                                encoding="utf-8")
    check("README.txt alone is fine",
          launcher.launch_dir_intruders(sandbox) == [])
    launcher.check_launch_dir(sandbox)          # must not raise
    check("and check_launch_dir passes it", True)

    # The ones that matter: a tool's own project configuration folder.
    for name in (".claude", ".codex", ".grok"):
        folder = sandbox / name
        folder.mkdir()
        found = launcher.launch_dir_intruders(sandbox)
        check(f"a {name} folder is spotted",
              any(entry.startswith(name) for entry in found), str(found))
        try:
            launcher.check_launch_dir(sandbox)
            check(f"and a launch from it is refused ({name})", False,
                  "it did not refuse")
        except launcher.LaunchError as exc:
            message = str(exc)
            check(f"and a launch from it is refused ({name})", True)
            check("the message says where", str(sandbox) in message)
            check("what was found", name in message)
            check("and how to clear it",
                  "delete everything in that folder except README.txt" in message)
        folder.rmdir()

    check("removing it clears the refusal",
          launcher.launch_dir_intruders(sandbox) == [])

    # Any other file counts too, not just the three known names.
    for name in ("settings.json", "notes.txt", ".hidden", "CLAUDE.md"):
        target = sandbox / name
        target.write_text("x", encoding="utf-8")
        check(f"{name:<14} is spotted",
              name in launcher.launch_dir_intruders(sandbox))
        target.unlink()

    print("\n  and launch() itself checks before starting anything")
    source = (pathlib.Path(launcher.__file__)).read_text(encoding="utf-8")
    body = source.split("def launch(")[1].split("\ndef ")[0]
    check("check_launch_dir is called in launch()",
          "check_launch_dir(" in body)
    check("before Popen", body.index("check_launch_dir(") < body.index("Popen"))

    print("\n  the real launch folder on this PC")
    real = launcher.launch_dir()
    found = launcher.launch_dir_intruders(real)
    check("is clean", found == [], str(found))

    print("\nA launched app gets a clean environment")
    bundle = r"C:\Program Files\The Ox Tracker\_internal"
    base = {
        "PATH": os.pathsep.join([bundle, bundle + r"\PySide6",
                                 r"C:\Windows\System32", r"C:\Tools"]),
        "USERPROFILE": r"D:\Profiles\someone",
        "ANTHROPIC_API_KEY": "fake-key-AAAA",
        "CLAUDECODE": "1",
        "_PYI_APPLICATION_HOME_DIR": bundle,
        "_PYI_ARCHIVE_FILE": bundle + r"\x.pkg",
        "_MEIPASS2": bundle,
    }
    env = launcher.clean_environment(base, bundle=bundle)
    check("session markers and API keys are gone",
          "ANTHROPIC_API_KEY" not in env and "CLAUDECODE" not in env)
    check("the PyInstaller bootloader's own variables are gone",
          not any(k.startswith(("_PYI_", "_MEIPASS")) for k in env), str(sorted(env)))
    check("PATH no longer leads into the app's own folder",
          env["PATH"] == os.pathsep.join([r"C:\Windows\System32", r"C:\Tools"]),
          env["PATH"])
    check("everything else is kept", env["USERPROFILE"] == r"D:\Profiles\someone")
    check("from source, PATH is left exactly as it was",
          launcher.clean_environment(base)["PATH"] == base["PATH"])
    check("a folder that merely starts with the same letters is kept",
          launcher._path_without(bundle + "2;" + bundle, bundle) == bundle + "2")

    print()
    if fails:
        for name in fails:
            print("  FAILED:", name)
        return 1
    print("all launcher tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
