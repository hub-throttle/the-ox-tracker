r"""The two guards that sit in front of everything else.

  Where The Ox Tracker is allowed to write.
  Which hosts it is allowed to reach at all.

Both are code, not configuration, and both fail closed.
"""
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from the_ox import net, paths

PROJECT = pathlib.Path(__file__).resolve().parent.parent

# The data folder goes somewhere temporary: startup_check() below creates it
# and writes a probe file, and the real one is not this test's to touch.
TEMP_DATA = pathlib.Path(tempfile.mkdtemp(prefix="theox-test-")) / "TheOx"
REAL_DATA = paths.real_local_appdata() / paths.APP_NAME
paths.set_data_dir_override(TEMP_DATA)

fails = []


def check(label, fn, expect_raise=None):
    try:
        result = fn()
        if expect_raise:
            fails.append(f"{label}: expected {expect_raise.__name__}, got {result!r}")
        else:
            print(f"  PASS  {label} -> {result!r}")
    except Exception as exc:
        if expect_raise and isinstance(exc, expect_raise):
            print(f"  PASS  {label} -> raised {type(exc).__name__}")
        else:
            fails.append(f"{label}: unexpected {type(exc).__name__}: {exc}")


def ok(label, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' -> ' + detail) if detail else ''}")
    if not condition:
        fails.append(label)


print("Cloud-folder write guard")
# Google Drive, where this project used to live.
check("project folder on G:", lambda: paths.assert_safe_write_path(r"G:\My Drive\Projects\x.json"), paths.UnsafeWritePath)
check("any My Drive path",    lambda: paths.assert_safe_write_path(r"D:\Profiles\someone\My Drive\x.log"), paths.UnsafeWritePath)
check("Shared drives path",   lambda: paths.assert_safe_write_path(r"G:\Shared drives\team\x.log"), paths.UnsafeWritePath)
check("GoogleDrive folder",   lambda: paths.assert_safe_write_path(r"D:\GoogleDrive\x.log"), paths.UnsafeWritePath)

# OneDrive, Dropbox and Box. On many Windows PCs the Desktop and Documents
# folders sync to OneDrive, so "put it on the Desktop" is not a local write at all.
for label, bad in (
    ("OneDrive Desktop",      r"D:\Profiles\someone\OneDrive\Desktop\review.zip"),
    ("OneDrive Documents",    r"D:\Profiles\someone\OneDrive\Documents\notes.txt"),
    ("OneDrive - a company",  r"D:\Profiles\someone\OneDrive - Example Company\x.txt"),
    ("Dropbox",               r"D:\Profiles\someone\Dropbox\x.log"),
    ("Dropbox (Personal)",    r"D:\Profiles\someone\Dropbox (Personal)\x.log"),
    ("Box",                   r"D:\Profiles\someone\Box\x.log"),
    ("Box Sync",              r"D:\Profiles\someone\Box Sync\x.log"),
):
    check(label, lambda p=bad: paths.assert_safe_write_path(p), paths.UnsafeWritePath)

# The error has to say WHICH service, or it is not actionable.
try:
    paths.assert_safe_write_path(r"D:\Profiles\someone\OneDrive\Desktop\x.zip")
    ok("the refusal names the service", False, "it did not refuse at all")
except paths.UnsafeWritePath as exc:
    ok("the refusal names the service", "OneDrive" in str(exc), str(exc).splitlines()[0])

print("\n  and ordinary local folders still work")
# A guard so broad it blocks normal folders is a broken guard. "box" and
# "dropbox" as SUBSTRINGS appear in plenty of innocent names.
check("LOCALAPPDATA TheOx",   lambda: str(paths.assert_safe_write_path(REAL_DATA)))
check("startup_check",        lambda: str(paths.startup_check()))
ok("startup_check used the temporary folder, not the real one",
   TEMP_DATA.is_dir() and paths.data_dir() == TEMP_DATA, str(paths.data_dir()))
check("local project folder", lambda: str(paths.assert_safe_write_path(r"D:\Profiles\someone\Projects\The Ox Tracker\x.json")))
check("a sandbox folder",     lambda: str(paths.assert_safe_write_path(r"D:\Profiles\someone\sandbox\x.json")))
check("codex .sandbox-bin",   lambda: str(paths.assert_safe_write_path(r"D:\Profiles\someone\.codex\.sandbox-bin\x")))
check("a file named dropbox", lambda: str(paths.assert_safe_write_path(r"D:\Profiles\someone\notes\my-dropbox-notes.txt")))
check("a folder named boxes", lambda: str(paths.assert_safe_write_path(r"D:\Profiles\someone\boxes\x.json")))

print("\nA whole drive is Google Drive by its label, not by a folder in it")
# Any account on a PC may create a folder in C:\. The old rule treated a
# drive whose root held a "My Drive" folder as Google Drive, so one such
# folder made every path on C: look synced, and the app, refusing to write
# its log, would not start for anyone. Only the volume label decides now,
# and changing a drive's label needs an administrator.
real_label = paths.volume_label
try:
    paths.volume_label = lambda root: "google drive"
    check("a drive labelled Google Drive is refused",
          lambda: paths.assert_safe_write_path(r"X:\Projects\x.json"), paths.UnsafeWritePath)
    ok("and the refusal says Google Drive",
       paths.synced_service(r"X:\Projects\x.json") == "Google Drive")
    paths.volume_label = lambda root: "windows"
    check("a drive with another label is fine", lambda: str(
        paths.assert_safe_write_path(r"X:\Profiles\someone\AppData\Local\TheOx\x.log")))
    ok("even when that drive has a 'My Drive' folder at its root",
       paths.synced_service(r"X:\Profiles\someone\AppData\Local\TheOx\x.log") is None)
    check("but a path through 'My Drive' itself is still refused",
          lambda: paths.assert_safe_write_path(r"X:\My Drive\x.log"), paths.UnsafeWritePath)
    check("and so is OneDrive, as before",
          lambda: paths.assert_safe_write_path(r"X:\Profiles\someone\OneDrive\x.log"),
          paths.UnsafeWritePath)
finally:
    paths.volume_label = real_label
source = (PROJECT / "the_ox" / "paths.py").read_text(encoding="utf-8")
ok("nothing lists the drive root to look for 'My Drive' any more",
   "iterdir" not in source)
label = paths.volume_label(str(pathlib.Path(sys.executable).anchor))
ok("this PC's system drive is not labelled Google Drive",
   label != paths.GOOGLE_DRIVE_LABEL, repr(label))

print("\nHost allowlist, per service")
# Each service may reach its own hosts and nothing else. The allowlist alone
# is not the whole check any more: see tests/test_security.py for the cases
# where one service's token is aimed at another service's host.
for service, url in (
    ("Claude", "https://api.anthropic.com/api/oauth/usage"),
    ("ChatGPT", "https://chatgpt.com/backend-api/wham/usage"),
    ("Grok", "https://cli-chat-proxy.grok.com/v1/billing?format=credits"),
):
    check(f"{service} -> {url.split('/')[2]}",
          lambda u=url, s=service: net.check_url(u, s))

for bad in ("https://api2.cursor.sh/x",          # removed on purpose
            "https://evil.example.com/steal",
            "https://api.anthropic.com.evil.com/x",
            "http://api.anthropic.com/x"):        # http, not https
    check(f"block {bad}", lambda u=bad: net.check_url(u, "Claude"), net.DisallowedHost)

check("an unknown service is refused",
      lambda: net.check_url("https://api.anthropic.com/x", "Nobody"),
      net.DisallowedHost)

print("\nPinned dependencies")
# Item 16: every pin carries a hash, so pip --require-hashes can refuse a
# package that was replaced upstream or swapped in transit.
text = (PROJECT / "requirements.txt").read_text(encoding="utf-8")
pins = re.findall(r"(?m)^([A-Za-z0-9_.\-]+)==([^\s\\]+)", text)
hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", text)
ok("requirements.txt has pins", len(pins) > 5, f"{len(pins)} pins")
ok("every pin has a hash", len(hashes) == len(pins), f"{len(hashes)} hashes, {len(pins)} pins")
ok("--require-hashes is documented", "--require-hashes" in text)
ok("cryptography is gone",
   not any(name.lower() == "cryptography" for name, _ in pins))
ok("and so are its dependencies",
   not any(name.lower() in ("cffi", "pycparser") for name, _ in pins))

# Nothing in the shipped code imports it, which is why it went.
sources = list((PROJECT / "the_ox").rglob("*.py")) + [PROJECT / "run.py"]
importers = [p.name for p in sources
             if re.search(r"(?m)^\s*(import|from)\s+cryptography\b",
                          p.read_text(encoding="utf-8"))]
ok("nothing imports cryptography", not importers, str(importers))

# The build tools live in requirements-build.txt and only there. If a package
# were pinned in both files, the build venv could be asked for two versions
# of the same thing, and the runtime venv would carry a build tool it never
# needs.
build_text = (PROJECT / "requirements-build.txt").read_text(encoding="utf-8")
build_pins = re.findall(r"(?m)^([A-Za-z0-9_.\-]+)==([^\s\\]+)", build_text)
build_hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", build_text)
ok("requirements-build.txt has every pin hashed",
   build_pins and len(build_hashes) == len(build_pins),
   f"{len(build_hashes)} hashes, {len(build_pins)} pins")
ok("PyInstaller is pinned for the build",
   any(name.lower() == "pyinstaller" for name, _ in build_pins))
ok("and is not in the runtime requirements",
   not any(name.lower().startswith("pyinstaller") for name, _ in pins))
both = sorted({n.lower() for n, _ in pins} & {n.lower() for n, _ in build_pins})
ok("no package is pinned in both files", not both, str(both))

# urllib3 2.8.0 fixed two streaming bugs this app's way of reading replies
# would hit: an unbounded chunk-size line, and a loop in chunked deflate.
versions = {name.lower(): version for name, version in pins}
urllib3_pin = tuple(int(part) for part in re.findall(r"\d+", versions.get("urllib3", "0"))[:3])
ok("urllib3 is pinned at 2.8.0 or newer", urllib3_pin >= (2, 8, 0),
   versions.get("urllib3", "not pinned"))
try:
    import urllib3                                  # noqa: PLC0415
    installed = tuple(int(part) for part in re.findall(r"\d+", urllib3.__version__)[:3])
    ok("and the urllib3 actually installed is too", installed >= (2, 8, 0),
       urllib3.__version__)
except ImportError:
    ok("urllib3 is installed", False)

print("\nThe build checks its own tools")
build = (PROJECT / "packaging" / "build.ps1").read_text(encoding="utf-8")
for name in ("Assert-PythonIntact", "Assert-InnoIntact", "Assert-BundleIntact"):
    ok(f"build.ps1 defines and calls {name}",
       build.count(name) >= 2, f"{build.count(name)} mentions")
pins_found = re.findall(r'"(Setup[A-Za-z]*\.e(?:32|64))"\s*=\s*"([0-9A-F]{64})"', build)
ok("the unsigned Inno Setup stubs are pinned by SHA-256",
   {name for name, _ in pins_found} >= {"Setup.e32", "SetupLdr.e32", "SetupLdr.e64"},
   str(sorted(name for name, _ in pins_found)))
ok("and signatures are checked with Get-AuthenticodeSignature",
   "Get-AuthenticodeSignature" in build)

print()
if fails:
    for f in fails:
        print("  FAIL ", f)
    sys.exit(1)
print("All self-tests passed.")
