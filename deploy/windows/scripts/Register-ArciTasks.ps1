#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Registers the ARCILOGGER evidence jobs in Windows Task Scheduler (folder \ARCILOGGER\).

.DESCRIPTION
    Each XML file in ..\tasks describes one job; {{PYTHON}} is replaced by the Python of the
    ARCILOGGER virtual environment (the one the ARCILOGGER packages are installed in) and
    {{SCRIPTS}} by the folder of the ARCILOGGER scripts (-ScriptDirectory, ..\scripts).
    The jobs run as LOCAL SERVICE and call "python -I -m arcilogger_core <command>"; they do not
    need the database or the web interface. The exception is Backup ("python -I -m arcilogger_db
    backup run", the scheduled database dump of docs/adr/0009-database-backup.md), which needs
    PostgreSQL. -I (isolated) before -m: no PYTHON* environment variable (PYTHONPATH,
    PYTHONSTARTUP, ...) and no user site-packages can put a module on the path of a job.
    Requests ("requests run") answers what the web interface asks for (credit query, time-stamp
    and replica test) and Seal also wakes on "Şimdi mühürle", both through an Event Log entry of
    ARCILOGGER-Admin (docs/adr/0014-browser-administration.md). ApplySystem is the one SYSTEM task:
    Windows PowerShell runs {{SCRIPTS}}\Sync-ArciSystem.ps1 from the program folder.
    Run again after an upgrade (existing tasks are replaced, new definitions such as Backup added).
    Install-Arcilogger.ps1 runs this script itself.

    The definitions of ..\desktop (-DesktopDirectory) go to \ARCILOGGER\Desktop\: StatusWindow,
    the read-only status window "ARCILOGGER Durum" that starts at the logon of a member of
    Administrators (Wave 5 contract section 5, I3). {{PYTHONW}} is the venv's pythonw.exe (no
    console window). They live in their own folder: the installer pauses and waits for the
    evidence jobs of \ARCILOGGER\ before an upgrade, and a window never ends by itself.

    The server time zone must be "Turkey Standard Time" (UTC+03:00): the trigger times
    (00:10 sealing, ...) are local times. Set-ArciTime.ps1 sets it.

.EXAMPLE
    .\Register-ArciTasks.ps1 -Python "C:\Program Files\ARCILOGGER\venv\Scripts\python.exe"
#>
param(
    [string]$Python = (Join-Path $env:ProgramFiles "ARCILOGGER\venv\Scripts\python.exe"),
    # Default: ..\tasks next to this script (set below - Windows PowerShell 5.1 leaves $PSScriptRoot
    # empty while it evaluates parameter defaults of a script started with "powershell -File").
    [string]$TaskDirectory,
    # Default: ..\scripts next to this script, which is its own folder (set below as well).
    [string]$ScriptDirectory,
    # Default: ..\desktop next to this script (set below); a program without it registers none.
    [string]$DesktopDirectory
)

$ErrorActionPreference = "Stop"
if (-not $TaskDirectory) { $TaskDirectory = Join-Path $PSScriptRoot "..\tasks" }
if (-not $ScriptDirectory) { $ScriptDirectory = Join-Path $PSScriptRoot "..\scripts" }
if (-not $DesktopDirectory) { $DesktopDirectory = Join-Path $PSScriptRoot "..\desktop" }
# A full path without "..": it is written into the task definition SYSTEM runs.
$ScriptDirectory = [System.IO.Path]::GetFullPath($ScriptDirectory).TrimEnd([char[]]@([char]92, [char]47))

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python bulunamadı [not found]: $Python"
}
if ((Get-TimeZone).Id -ne "Turkey Standard Time") {
    Write-Warning "Sunucu saat dilimi $((Get-TimeZone).Id); önce Set-ArciTime.ps1 çalıştırın [run Set-ArciTime.ps1 first]."
}

Get-ChildItem -LiteralPath $TaskDirectory -Filter *.xml | ForEach-Object {
    $raw = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8
    # A task that runs a script of the program folder (ApplySystem) must find it there.
    foreach ($used in [regex]::Matches($raw, '\{\{SCRIPTS\}\}\\([A-Za-z0-9.-]+\.ps1)')) {
        $script = Join-Path $ScriptDirectory $used.Groups[1].Value
        if (-not (Test-Path -LiteralPath $script)) { throw "Betik bulunamadı [script not found]: $script ($($_.Name))" }
    }
    $xml = $raw.Replace("{{PYTHON}}", $Python).Replace("{{SCRIPTS}}", $ScriptDirectory)
    Register-ScheduledTask -TaskName $_.BaseName -TaskPath "\ARCILOGGER\" -Xml $xml -Force | Out-Null
    Write-Host "kaydedildi [registered] \ARCILOGGER\$($_.BaseName)"
}

# The desktop status window: pythonw.exe next to python.exe (a venv has both).
if (Test-Path -LiteralPath $DesktopDirectory) {
    $PythonW = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
    if (-not (Test-Path -LiteralPath $PythonW)) {
        # A convenience window never fails the installation: the evidence jobs above are in place.
        Write-Warning "pythonw.exe bulunamadı, durum penceresi kaydedilmedi [not found, status window not registered]: $PythonW"
    }
    else {
        Get-ChildItem -LiteralPath $DesktopDirectory -Filter *.xml | ForEach-Object {
            $xml = (Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8).Replace("{{PYTHONW}}", $PythonW)
            Register-ScheduledTask -TaskName $_.BaseName -TaskPath "\ARCILOGGER\Desktop\" -Xml $xml -Force | Out-Null
            Write-Host "kaydedildi [registered] \ARCILOGGER\Desktop\$($_.BaseName)"
        }
    }
}

Get-ScheduledTask -TaskPath "\ARCILOGGER\" | Format-Table TaskName, State
