# ==============================================================================
# The Sleepy Labs // Sleepy Proton Drive - Windows installer
# ==============================================================================
# Installs for the current user only (no admin rights needed):
#   %LOCALAPPDATA%\Sleepy Proton Drive\   app, private Python environment, commands
#   Start menu shortcut "Sleepy Proton Drive"
# Missing requirements (Python, rclone) are installed with winget.
# Double-click install.cmd, or run:  powershell -ExecutionPolicy Bypass -File install.ps1 [options]
#   -Yes               don't ask questions
#   -DesktopShortcut   also put a shortcut on the Desktop
#   -NoDeps            don't install Python/rclone (use what's there)
#   -Python <path>     use this python.exe
#   -NoLaunch          don't open the app at the end
# ==============================================================================
[CmdletBinding()]
param(
    [switch]$Yes,
    [switch]$DesktopShortcut,
    [switch]$NoDeps,
    [string]$Python = "",
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppName = "Sleepy Proton Drive"
$Base = Join-Path $env:LOCALAPPDATA $AppName
$AppDir = Join-Path $Base "app"
$Venv = Join-Path $Base "venv"
$BinDir = Join-Path $Base "bin"
$StartMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "$AppName.lnk"
$Icon = Join-Path $AppDir "assets\sleepy-proton-drive.ico"

function Say($msg, $color = "Cyan") { Write-Host $msg -ForegroundColor $color }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Ask($question) {
    if ($Yes) { return $true }
    $a = Read-Host "$question [Y/n]"
    return ($a -eq "" -or $a -match "^[Yy]")
}

# Run a program without letting its error-stream text stop this script
# (Windows PowerShell 5.1 turns redirected stderr into errors). Returns the exit code.
function Native {
    param([string]$Exe, [string[]]$ArgList, [switch]$Quiet)
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($Quiet) { & $Exe @ArgList 2>&1 | Out-Null } else { & $Exe @ArgList | Out-Host }
        return $LASTEXITCODE
    } finally { $ErrorActionPreference = $old }
}

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Test-Python($exe) {
    if (-not $exe) { return $false }
    if (-not (Test-Path $exe)) { return $false }
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $exe -c "import sys; print(sys.version_info >= (3, 10) and 'ok' or 'old')" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq "ok")
    } catch { return $false } finally { $ErrorActionPreference = $old }
}

function Find-Python {
    if ($Python) { if (Test-Python $Python) { return $Python } else { throw "$Python isn't Python 3.10 or newer." } }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $old = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $exe = (& $py.Source -3 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
        } catch { $exe = $null } finally { $ErrorActionPreference = $old }
        if ($exe -and (Test-Python "$exe".Trim())) { return "$exe".Trim() }
    }
    foreach ($c in @(Get-Command python -All -ErrorAction SilentlyContinue)) {
        # Skip the Microsoft Store stand-in (it only opens the Store).
        if ($c.Source -like "*\WindowsApps\*") { continue }
        if (Test-Python $c.Source) { return $c.Source }
    }
    $found = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "Programs\Python\Python3*\python.exe") -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending
    foreach ($f in $found) { if (Test-Python $f.FullName) { return $f.FullName } }
    return $null
}

function Find-Rclone {
    $c = Get-Command rclone -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $links = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\rclone.exe"
    if (Test-Path $links) { return $links }
    $pkg = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages\Rclone.Rclone*") -Recurse -Filter rclone.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pkg) { return $pkg.FullName }
    return $null
}

function Winget-Install($id, $what) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Warn "winget (App Installer) isn't available, so $what can't be installed automatically."
        Warn "Install App Installer from the Microsoft Store, or install $what yourself, then run this again."
        return $false
    }
    Say "    Installing $what with winget (this can take a few minutes)..." "Gray"
    $rc = Native $winget.Source @("install", "--id", $id, "--exact", "--silent", "--scope", "user", "--accept-package-agreements", "--accept-source-agreements")
    if ($rc -ne 0) {
        Native $winget.Source @("install", "--id", $id, "--exact", "--silent", "--accept-package-agreements", "--accept-source-agreements") | Out-Null
    }
    Refresh-Path
    return $true
}

Write-Host ""
Say "   *  SLEEPY PROTON DRIVE  *   Windows installer" "Magenta"
Write-Host "   by The Sleepy Labs"
Write-Host "=============================================================================="

# 1. Requirements -------------------------------------------------------------
Say ">>> Checking requirements..."
$pyExe = Find-Python
if (-not $pyExe -and -not $NoDeps) {
    if (Ask "Python 3 is needed. Install it now with winget?") {
        Winget-Install "Python.Python.3.12" "Python 3.12" | Out-Null
        $pyExe = Find-Python
    }
}
if (-not $pyExe) { throw "Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/ and run this again." }
Say "    Python: $pyExe" "Green"

$rclone = Find-Rclone
if (-not $rclone -and -not $NoDeps) {
    if (Ask "rclone (talks to Proton Drive) is needed. Install it now with winget?") {
        Winget-Install "Rclone.Rclone" "rclone" | Out-Null
        $rclone = Find-Rclone
    }
}
if ($rclone) { Say "    rclone: $rclone" "Green" } else { Warn "rclone isn't installed yet; syncing won't work until it is (run this installer again)." }

# 2. Stop a running sync before replacing files -------------------------------
if ((Test-Path (Join-Path $Venv "Scripts\python.exe")) -and (Test-Path (Join-Path $AppDir "sleepy_proton"))) {
    Native (Join-Path $Venv "Scripts\python.exe") @("-m", "sleepy_proton", "sync", "--stop") -Quiet | Out-Null
}

# 3. Program files --------------------------------------------------------------
Say ">>> Installing to $Base"
New-Item -ItemType Directory -Force -Path $Base, $BinDir | Out-Null
if (Test-Path $AppDir) { Remove-Item -Recurse -Force $AppDir }
New-Item -ItemType Directory -Force -Path $AppDir, (Join-Path $AppDir "assets") | Out-Null
Copy-Item -Recurse -Force (Join-Path $Here "sleepy_proton") $AppDir
Get-ChildItem -Path $AppDir -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Copy-Item -Force (Join-Path $Here "assets\sleepy-proton-drive.ico"), (Join-Path $Here "assets\sleepy-proton-drive.svg") (Join-Path $AppDir "assets")
foreach ($f in @("README.md", "CHANGELOG.md", "LICENSE")) {
    if (Test-Path (Join-Path $Here $f)) { Copy-Item -Force (Join-Path $Here $f) $AppDir }
}

# 4. Private Python environment with the app window toolkit (PySide6) --------
$vpy = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Python $vpy)) {
    Say ">>> Making a private Python environment"
    if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
    if ((Native $pyExe @("-m", "venv", $Venv)) -ne 0) { throw "Couldn't make the Python environment in $Venv." }
}
if ((Native $vpy @("-c", "import PySide6.QtWidgets") -Quiet) -ne 0) {
    Say ">>> Installing the app window toolkit (PySide6, about 200 MB)..."
    Native $vpy @("-m", "pip", "install", "--disable-pip-version-check", "--quiet", "--upgrade", "pip") | Out-Null
    if ((Native $vpy @("-m", "pip", "install", "--disable-pip-version-check", "PySide6>=6.5")) -ne 0) { throw "Couldn't install PySide6. Check your internet connection and run this again." }
}
# Let the environment find the app (no PYTHONPATH needed anywhere).
$site = (& $vpy -c "import sysconfig; print(sysconfig.get_paths()['purelib'])").Trim()
[IO.File]::WriteAllText((Join-Path $site "sleepy-proton-drive.pth"), "$AppDir`r`n")

# 5. Commands (sleepy-proton-sync, -photos, -gui, -check) ---------------------
foreach ($t in @("sync", "photos", "gui", "check")) {
    $exe = "python.exe"
    if ($t -eq "gui") { $exe = "pythonw.exe" }
    $cmd = "@`"%~dp0..\venv\Scripts\$exe`" -m sleepy_proton $t %*`r`n"
    [IO.File]::WriteAllText((Join-Path $BinDir "sleepy-proton-$t.cmd"), $cmd)
}
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
if (($userPath -split ";") -notcontains $BinDir) {
    $newPath = ($userPath.TrimEnd(";") + ";" + $BinDir).TrimStart(";")
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    Say "    Added $BinDir to your PATH (new terminals can run sleepy-proton-sync etc.)" "Gray"
}

# 6. Start menu (and Desktop) shortcut ----------------------------------------
Say ">>> Adding the Start menu shortcut"
function Make-Shortcut($path) {
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($path)
    $s.TargetPath = Join-Path $Venv "Scripts\pythonw.exe"
    $s.Arguments = "-m sleepy_proton gui"
    $s.WorkingDirectory = $AppDir
    $s.IconLocation = "$Icon,0"
    $s.Description = "Automatic backups to Proton Drive (The Sleepy Labs)"
    $s.Save()
}
Make-Shortcut $StartMenu
if ($DesktopShortcut) {
    Make-Shortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "$AppName.lnk")
    Say "    Desktop shortcut added." "Gray"
}

# 7. Check + update an existing background timer -----------------------------
$version = (& $vpy -m sleepy_proton sync --version).Trim()
if ($LASTEXITCODE -ne 0) { throw "The installed app doesn't start. Please report this." }
$timer = (& $vpy -m sleepy_proton sync --timer-status).Trim()
if ($timer -like "active *") {
    $interval = $timer.Substring(7)
    if ($interval) {
        Say ">>> Updating your background sync (every $interval)"
        Native $vpy @("-m", "sleepy_proton", "sync", "--enable-timer", $interval) | Out-Null
    }
}

Write-Host "=============================================================================="
Say "* Installed! * ($version)" "Green"
Write-Host "Open 'Sleepy Proton Drive' from the Start menu. The first time, the Setup Wizard"
Write-Host "signs you in to Proton Drive and picks the folders to back up."
if (-not $NoLaunch) {
    Start-Process -FilePath (Join-Path $Venv "Scripts\pythonw.exe") -ArgumentList "-m", "sleepy_proton", "gui" -WorkingDirectory $AppDir
}
