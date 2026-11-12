# ==============================================================================
# The Sleepy Labs // Sleepy Proton Drive - Windows uninstaller
# ==============================================================================
# Removes the app, its Start menu/Desktop shortcuts, its commands and the
# background sync task. Your settings, log and Proton Photos tool are kept
# (so a reinstall picks up where you left off) unless you add -Purge.
# Your files on this PC and in Proton Drive are never touched.
# Double-click uninstall.cmd, or run:  powershell -ExecutionPolicy Bypass -File uninstall.ps1 [-Purge] [-Yes]
# ==============================================================================
[CmdletBinding()]
param(
    [switch]$Purge,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$AppName = "Sleepy Proton Drive"
$Base = Join-Path $env:LOCALAPPDATA $AppName
$Roaming = Join-Path $env:APPDATA $AppName
$AppDir = Join-Path $Base "app"
$Venv = Join-Path $Base "venv"
$BinDir = Join-Path $Base "bin"
$vpy = Join-Path $Venv "Scripts\python.exe"

function Say($msg, $color = "Cyan") { Write-Host $msg -ForegroundColor $color }
function Native {
    param([string]$Exe, [string[]]$ArgList)
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Exe @ArgList 2>&1 | Out-Host; return $LASTEXITCODE } finally { $ErrorActionPreference = $old }
}

Write-Host ""
Say "   *  SLEEPY PROTON DRIVE  *   uninstaller" "Magenta"
Write-Host "=============================================================================="
if (-not $Yes) {
    $what = "Remove Sleepy Proton Drive (settings are kept)?"
    if ($Purge) { $what = "Remove Sleepy Proton Drive AND its settings, log and history?" }
    $a = Read-Host "$what [y/N]"
    if ($a -notmatch "^[Yy]") { Write-Host "Nothing changed."; exit 0 }
}

# 1. Stop any sync and remove the background task.
$haveApp = (Test-Path $vpy) -and (Test-Path (Join-Path $AppDir "sleepy_proton"))
if ($haveApp) {
    Say ">>> Stopping any running sync"
    Native $vpy @("-m", "sleepy_proton", "sync", "--stop") | Out-Null
    Native $vpy @("-m", "sleepy_proton", "photos", "--stop") | Out-Null
    Say ">>> Removing the background sync task"
    Native $vpy @("-m", "sleepy_proton", "sync", "--disable-timer") | Out-Null
}
# In case the app was already half removed.
$old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
& schtasks.exe /Delete /TN "Sleepy Proton Drive Sync" /F 2>&1 | Out-Null
$ErrorActionPreference = $old

# 2. Close the app window if it's open (only our own pythonw from the venv).
Get-Process -Name pythonw, python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($Venv, [StringComparison]::OrdinalIgnoreCase) } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

# 3. Shortcuts, commands, program files.
Say ">>> Removing shortcuts and program files"
foreach ($lnk in @((Join-Path ([Environment]::GetFolderPath("Programs")) "$AppName.lnk"),
                   (Join-Path ([Environment]::GetFolderPath("Desktop")) "$AppName.lnk"))) {
    if (Test-Path $lnk) { Remove-Item -Force $lnk }
}
Get-ChildItem -Path $BinDir -Filter "sleepy-proton-*.cmd" -ErrorAction SilentlyContinue | Remove-Item -Force
foreach ($d in @($AppDir, $Venv)) { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath) {
    $parts = $userPath -split ";" | Where-Object { $_ -and ($_.TrimEnd("\") -ne $BinDir) }
    $newPath = $parts -join ";"
    if ($newPath -ne $userPath) { [Environment]::SetEnvironmentVariable("Path", $newPath, "User") }
}

# 4. Settings, log, history (only with -Purge).
if ($Purge) {
    Say ">>> Removing settings, log and history (-Purge)"
    foreach ($d in @($Base, $Roaming)) { if (Test-Path $d) { Remove-Item -Recurse -Force $d } }
} else {
    if ((Test-Path $BinDir) -and -not (Get-ChildItem -Path $BinDir -Force)) { Remove-Item -Force $BinDir }
}

Write-Host "=============================================================================="
Say "* Sleepy Proton Drive is removed. *" "Green"
if (-not $Purge) {
    Write-Host "Kept: your settings ($Roaming), the log and sync history ($Base)."
    if (Test-Path (Join-Path $BinDir "proton-drive.exe")) { Write-Host "Kept: the Proton Photos tool ($BinDir\proton-drive.exe)." }
    Write-Host "Run uninstall.cmd -Purge to remove those too."
}
Write-Host "Not touched: your files, your Proton Drive, rclone and its sign-in (rclone config)."
