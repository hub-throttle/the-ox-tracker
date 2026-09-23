<#
.SYNOPSIS
    Build The Ox Tracker: the one-folder app, then its installer.

.DESCRIPTION
    One command, repeatable:

        packaging\build.cmd

    (which runs this script with PowerShell's execution policy relaxed for
    this one script only). What it does, in order:

      1. Makes the build venv if it is missing, by default
         %USERPROFILE%\.venvs\the-ox-build, and installs
         requirements-build.txt then requirements.txt into it, both with
         --require-hashes and wheels only. The runtime venv is never touched.
         If either requirements file changes, the build venv is rebuilt.
      2. Reads the version from the_ox\__init__.py, the only place it lives.
      3. Stamps that version into a copy of packaging\the-ox.manifest in
         build\. The app icon is the_ox\assets\ox.ico, used as it is.
      4. Runs PyInstaller on packaging\the-ox.spec: one folder, no console
         window, no UPX, into dist\The Ox Tracker. The spec leaves out every
         Qt module, plugin and translation the app does not use, and stops
         the build if what is left needs anything that was left out. The
         list of what was left out goes to build\trimmed.txt.
      5. Compiles packaging\the-ox.iss with Inno Setup into
         dist\TheOxTracker-Setup-<version>.exe, and prints its SHA-256.

    Integrity checks, and the build stops if any fails. The tools live in
    folders this account can write to, and what they produce ends up in an
    installer that runs as administrator, so they are checked before use:
      - Python (the build venv's and the one it was made from) must carry a
        valid Python Software Foundation signature.
      - Every signed Inno Setup file must carry a valid signature from Inno
        Setup's own publisher, and the four it ships unsigned (the stubs
        that become the installer) must match the SHA-256 pinned below.
      - After PyInstaller, every DLL and .pyd in the app must be validly
        signed by the Python Software Foundation, The Qt Company or
        Microsoft, except the few known to ship unsigned, listed below.
        Anything unsigned that is not on that list stops the build.

    Everything it writes goes into build\ and dist\ (both ignored by git)
    and the build venv. Nothing is signed, tagged or published.

.PARAMETER BuildVenv
    Where the build venv lives. Default: %USERPROFILE%\.venvs\the-ox-build

.PARAMETER BasePython
    The Python 3.14 to make the build venv from. Default: whatever
    "py -3.14" finds, or "python" if that is 3.14.

.PARAMETER Iscc
    Path to Inno Setup's ISCC.exe. Default: found automatically.

.PARAMETER SkipInstaller
    Stop after the PyInstaller build.
#>
[CmdletBinding()]
param(
    [string]$BuildVenv = (Join-Path $env:USERPROFILE ".venvs\the-ox-build"),
    [string]$BasePython = "",
    [string]$Iscc = "",
    [switch]$SkipInstaller
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root      = Split-Path -Parent $PSScriptRoot
$BuildDir  = Join-Path $Root "build"
$DistDir   = Join-Path $Root "dist"
$AppName   = "The Ox Tracker"
$AppDir    = Join-Path $DistDir $AppName
$AppExe    = Join-Path $AppDir "$AppName.exe"
$ReqBuild  = Join-Path $Root "requirements-build.txt"
$ReqApp    = Join-Path $Root "requirements.txt"
$Stamp     = Join-Path $BuildVenv "theox-requirements.sha256"

function Step([string]$text) { Write-Host "`n== $text" -ForegroundColor Cyan }

# ---------------------------------------------------------------- integrity
# Who may sign what. Subjects as Windows reports them, first part only.
$PythonSigners = @("CN=Python Software Foundation")
$InnoSigners   = @("CN=Pyrsys B.V.", "E=mlaan@jrsoftware.org")
$BundleSigners = @("CN=Python Software Foundation", "CN=The QT Company Oy",
                   "CN=Microsoft Windows Software Compatibility Publisher",
                   "CN=Microsoft Corporation")

# The Inno Setup files that ship unsigned: the stubs that become the
# installer and its loader. Pinned from Inno Setup 6.7.3. A different Inno
# Setup, or a changed file, stops the build until these are checked and
# updated by hand.
$InnoStubPins = @{
    "Setup.e32"            = "A80D75BA8D8C336050C37F7707DE8BB017CC93597329E0EA18D474156B3E0DA5"
    "SetupCustomStyle.e32" = "9675A2A5C78C66B691CC80270031DA42BEBAD5725FBF60853CB4E5F90251BD5E"
    "SetupLdr.e32"         = "5475964893ADBB33CCC420AD4D88BBDFF27DFAACA7580CD6058EA2798893490D"
    "SetupLdr.e64"         = "E38F2140E6BFD86C6DDC69C98F46475080C75FD5EE4BE701CEFF9C842C6C5019"
}

# DLLs and .pyd files in the app that their makers ship unsigned: pywin32's
# and charset-normalizer's, from wheels whose SHA-256 requirements.txt pins.
# Any other unsigned binary in the app stops the build.
$KnownUnsigned = @(
    "_internal\charset_normalizer\cd.cp314-win_amd64.pyd",
    "_internal\charset_normalizer\md.cp314-win_amd64.pyd",
    "_internal\pythonwin\win32ui.pyd",
    "_internal\pywin32_system32\pythoncom314.dll",
    "_internal\pywin32_system32\pywintypes314.dll",
    "_internal\win32\_win32sysloader.pyd",
    "_internal\win32\win32api.pyd",
    "_internal\win32\win32event.pyd",
    "_internal\win32\win32security.pyd",
    "_internal\win32\win32trace.pyd",
    "_internal\win32com\shell\shell.pyd"
)

function Get-Signer([string]$Path) {
    $sig = Get-AuthenticodeSignature -LiteralPath $Path
    $subject = ""
    if ($sig.SignerCertificate) { $subject = ($sig.SignerCertificate.Subject -split ',')[0].Trim() }
    [pscustomobject]@{ Status = "$($sig.Status)"; Signer = $subject }
}

function Assert-Signed([string]$Path, [string[]]$Signers) {
    if (-not (Test-Path -LiteralPath $Path)) { throw "Integrity check failed: $Path is missing." }
    $s = Get-Signer $Path
    if ($s.Status -ne "Valid" -or $Signers -notcontains $s.Signer) {
        throw ("Integrity check failed: $Path is not validly signed by " +
               "$($Signers -join ' or ') (status $($s.Status), signer '$($s.Signer)').")
    }
}

function Assert-PythonIntact([string]$VenvDir) {
    # The base Python is read from pyvenv.cfg, a text file, rather than by
    # asking the interpreter being checked.
    $cfg = Get-Content -LiteralPath (Join-Path $VenvDir "pyvenv.cfg")
    $homeLine = $cfg | Where-Object { $_ -match '^\s*home\s*=\s*(.+?)\s*$' } | Select-Object -First 1
    if (-not $homeLine) { throw "Integrity check failed: no home in $VenvDir\pyvenv.cfg." }
    $baseDir = ($homeLine -replace '^\s*home\s*=\s*', '').Trim()
    $files = @((Join-Path $VenvDir "Scripts\python.exe"), (Join-Path $baseDir "python.exe"))
    $files += @(Get-ChildItem -LiteralPath $baseDir -Filter "python3*.dll" -File | ForEach-Object { $_.FullName })
    foreach ($file in $files) { Assert-Signed $file $PythonSigners }
    Write-Host "python: $($files.Count) files validly signed ($baseDir)"
}

function Assert-InnoIntact([string]$IsccExe) {
    $dir = Split-Path -Parent $IsccExe
    foreach ($name in $InnoStubPins.Keys) {
        $file = Join-Path $dir $name
        if (-not (Test-Path -LiteralPath $file)) { throw "Integrity check failed: $file is missing." }
        $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash
        if ($hash -ne $InnoStubPins[$name]) {
            throw "Integrity check failed: $file does not match its pinned SHA-256 (it is $hash)."
        }
    }
    $checked = 0
    foreach ($file in Get-ChildItem -LiteralPath $dir -File) {
        if ($file.Extension -notin @(".exe", ".dll", ".e32", ".e64")) { continue }
        if ($InnoStubPins.ContainsKey($file.Name)) { continue }
        Assert-Signed $file.FullName $InnoSigners
        $checked++
    }
    Write-Host "inno setup: $checked signed files valid, $($InnoStubPins.Count) stubs match their pins ($dir)"
}

function Assert-BundleIntact([string]$AppDir) {
    $problems = @()
    $signed = 0
    foreach ($file in Get-ChildItem -LiteralPath $AppDir -Recurse -File) {
        if ($file.Extension -notin @(".dll", ".pyd")) { continue }
        $rel = $file.FullName.Substring($AppDir.Length + 1)
        $s = Get-Signer $file.FullName
        if ($s.Status -eq "NotSigned") {
            if ($KnownUnsigned -notcontains $rel) {
                $problems += "$rel is not signed, and is not one of the files known to ship unsigned"
            }
        } elseif ($s.Status -ne "Valid" -or $BundleSigners -notcontains $s.Signer) {
            $problems += "$rel has a bad signature (status $($s.Status), signer '$($s.Signer)')"
        } else {
            $signed++
        }
    }
    foreach ($rel in $KnownUnsigned) {
        if (-not (Test-Path -LiteralPath (Join-Path $AppDir $rel))) {
            $problems += "$rel was expected in the app and is missing"
        }
    }
    if ($problems) {
        throw ("Integrity check failed for the built app:`n  " + ($problems -join "`n  "))
    }
    Write-Host "app: $signed DLL and .pyd files validly signed, $($KnownUnsigned.Count) known unsigned"
}

function Invoke-Checked {
    # Run a native command and stop the build if it fails.
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$([IO.Path]::GetFileName($Exe)) failed with exit code $LASTEXITCODE"
    }
}

function Get-PythonVersion([string]$exe) {
    try { (& $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null | Out-String).Trim() }
    catch { "" }
}

function Find-BasePython {
    if ($BasePython) { return $BasePython }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $exe = (& $py.Source -3.14 -c "import sys; print(sys.executable)" 2>$null | Out-String).Trim()
        if ($exe -and (Test-Path $exe)) { return $exe }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -and (Get-PythonVersion $python.Source) -eq "3.14") { return $python.Source }
    throw "Python 3.14 was not found. Install it, or pass -BasePython <path to python.exe>."
}

function Find-Iscc {
    if ($Iscc) { return $Iscc }
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"))) {
        if ($candidate -and (Test-Path $candidate)) { return $candidate }
    }
    throw "Inno Setup 6 (ISCC.exe) was not found. Install it, pass -Iscc <path>, or use -SkipInstaller."
}

# ---------------------------------------------------------------- 1. venv
Step "Build venv: $BuildVenv"
$wanted = (Get-FileHash -Algorithm SHA256 -InputStream ([IO.MemoryStream]::new(
    [Text.Encoding]::UTF8.GetBytes((Get-Content -Raw $ReqBuild) + (Get-Content -Raw $ReqApp))))).Hash
$VenvPython = Join-Path $BuildVenv "Scripts\python.exe"
$have = if (Test-Path $Stamp) { (Get-Content -Raw $Stamp).Trim() } else { "" }

if ((Test-Path $VenvPython) -and $have -ne $wanted) {
    # The pins changed. Start again rather than install on top, so nothing
    # left over from the old pins ends up frozen into the app.
    if (-not (Test-Path (Join-Path $BuildVenv "pyvenv.cfg"))) {
        throw "$BuildVenv exists but is not a venv; refusing to delete it."
    }
    Write-Host "requirements changed; rebuilding the build venv"
    Remove-Item -Recurse -Force $BuildVenv
}
if (-not (Test-Path $VenvPython)) {
    $base = Find-BasePython
    Assert-Signed $base $PythonSigners
    Write-Host "creating it from $base"
    Invoke-Checked $base @("-m", "venv", $BuildVenv)
    $pipArgs = @("-m", "pip", "install", "--require-hashes", "--only-binary", ":all:",
                 "--disable-pip-version-check", "--no-input")
    Invoke-Checked $VenvPython ($pipArgs + @("-r", $ReqBuild))
    Invoke-Checked $VenvPython ($pipArgs + @("-r", $ReqApp))
    Set-Content -Path $Stamp -Value $wanted -Encoding ascii
}
$venvVersion = Get-PythonVersion $VenvPython
if ($venvVersion -ne "3.14") { throw "The build venv is Python $venvVersion; 3.14 is required." }
Assert-PythonIntact $BuildVenv

# ---------------------------------------------------------------- 2. version
Step "Version"
$init = Get-Content -Raw (Join-Path $Root "the_ox\__init__.py")
if ($init -notmatch '__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"') {
    throw "Could not read __version__ from the_ox\__init__.py"
}
$Version = "$($Matches[1]).$($Matches[2]).$($Matches[3])"
Write-Host "The Ox Tracker $Version"

# ---------------------------------------------------------------- 3. manifest and icon
Step "Manifest and icon"
New-Item -ItemType Directory -Force $BuildDir | Out-Null
$manifestIn  = Join-Path $PSScriptRoot "the-ox.manifest"
$manifestOut = Join-Path $BuildDir "the-ox.manifest"
$manifest = Get-Content -Raw $manifestIn
if ($manifest -notmatch 'version="0\.0\.0\.0"') {
    throw "packaging\the-ox.manifest no longer has the 0.0.0.0 placeholder to stamp."
}
$manifest = $manifest -replace 'version="0\.0\.0\.0"', "version=""$Version.0"""
[IO.File]::WriteAllText($manifestOut, $manifest, [Text.UTF8Encoding]::new($false))
Write-Host "manifest: $manifestOut ($Version.0)"
# The bull, used exactly as it is, for the .exe (and so the Start menu
# shortcut and Installed apps) and for the installer. It also ships inside
# the app, for the window icons and the setup screen; see the_ox\appicon.py.
$AssetsDir = Join-Path $Root "the_ox\assets"
$IconFile  = Join-Path $AssetsDir "ox.ico"
if (-not (Test-Path $IconFile)) { throw "The app icon is missing: $IconFile" }
Write-Host "icon: $IconFile"

# ---------------------------------------------------------------- 4. PyInstaller
Step "PyInstaller (packaging\the-ox.spec: one folder, no console, no UPX)"
# The spec reads the manifest stamped above from build\the-ox.manifest.
Invoke-Checked $VenvPython @(
    "-m", "PyInstaller",
    "--noconfirm", "--clean", "--log-level", "WARN",
    "--distpath", $DistDir,
    "--workpath", (Join-Path $BuildDir "pyinstaller"),
    (Join-Path $PSScriptRoot "the-ox.spec"))
if (-not (Test-Path $AppExe)) { throw "PyInstaller finished but $AppExe is missing." }
Write-Host "app: $AppDir"
Assert-BundleIntact $AppDir

if ($SkipInstaller) {
    Write-Host "`nSkipping the installer (-SkipInstaller)."
    exit 0
}

# ---------------------------------------------------------------- 5. installer
Step "Inno Setup installer"
$isccExe = Find-Iscc
Assert-InnoIntact $isccExe
Invoke-Checked $isccExe @(
    "/Q",
    "/DAppVersion=$Version",
    "/DSourceDir=$AppDir",
    "/DIconFile=$IconFile",
    "/O$DistDir",
    (Join-Path $PSScriptRoot "the-ox.iss"))
$Installer = Join-Path $DistDir "TheOxTracker-Setup-$Version.exe"
if (-not (Test-Path $Installer)) { throw "Inno Setup finished but $Installer is missing." }

Step "Done"
Write-Host "installer: $Installer"
Write-Host "SHA-256:   $((Get-FileHash -Algorithm SHA256 $Installer).Hash)"
