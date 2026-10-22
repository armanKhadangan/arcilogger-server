#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Registers the ARCILOGGER evidence jobs in Windows Task Scheduler (folder \ARCILOGGER\).

.DESCRIPTION
    Each XML file in ..\tasks describes one job; {{PYTHON}} is replaced by the Python of the
    ARCILOGGER virtual environment (the one the ARCILOGGER packages are installed in).
    The jobs run as LOCAL SERVICE and call "python -I -m arcilogger_core <command>"; they do not
    need the database or the web interface. The exception is Backup ("python -I -m arcilogger_db
    backup run", the scheduled database dump of docs/adr/0009-database-backup.md), which needs
    PostgreSQL. -I (isolated) before -m: no PYTHON* environment variable (PYTHONPATH,
    PYTHONSTARTUP, ...) and no user site-packages can put a module on the path of a job.
    Run again after an upgrade (existing tasks are replaced, new definitions such as Backup added).
    Install-Arcilogger.ps1 runs this script itself.

    The server time zone must be "Turkey Standard Time" (UTC+03:00): the trigger times
    (00:10 sealing, ...) are local times. Set-ArciTime.ps1 sets it.

.EXAMPLE
    .\Register-ArciTasks.ps1 -Python "C:\Program Files\ARCILOGGER\venv\Scripts\python.exe"
#>
param(
    [string]$Python = (Join-Path $env:ProgramFiles "ARCILOGGER\venv\Scripts\python.exe"),
    # Default: ..\tasks next to this script (set below - Windows PowerShell 5.1 leaves $PSScriptRoot
    # empty while it evaluates parameter defaults of a script started with "powershell -File").
    [string]$TaskDirectory
)

$ErrorActionPreference = "Stop"
if (-not $TaskDirectory) { $TaskDirectory = Join-Path $PSScriptRoot "..\tasks" }

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python bulunamadı [not found]: $Python"
}
if ((Get-TimeZone).Id -ne "Turkey Standard Time") {
    Write-Warning "Sunucu saat dilimi $((Get-TimeZone).Id); önce Set-ArciTime.ps1 çalıştırın [run Set-ArciTime.ps1 first]."
}

Get-ChildItem -LiteralPath $TaskDirectory -Filter *.xml | ForEach-Object {
    $xml = (Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8).Replace("{{PYTHON}}", $Python)
    Register-ScheduledTask -TaskName $_.BaseName -TaskPath "\ARCILOGGER\" -Xml $xml -Force | Out-Null
    Write-Host "kaydedildi [registered] \ARCILOGGER\$($_.BaseName)"
}

Get-ScheduledTask -TaskPath "\ARCILOGGER\" | Format-Table TaskName, State
