<#
.SYNOPSIS
    ARCILOGGER sunucu kurulumu, yükseltme, geri alma ve kaldırma (çevrimdışı paketten)
    [ARCILOGGER server install, upgrade, rollback and removal from the offline bundle].

.DESCRIPTION
    Runs ON the ARCILOGGER server as Administrator, without internet access: everything comes from
    the bundle built by Build-ArciBundle.ps1 (ARCILOGGER-<version>\, this script is in its
    installer\ folder). Safe to run again (idempotent).

    Install (default):
      preflight -> bundle checksums -> folders -> Python runtime -> venv -> offline wheels -> web
      interface, Caddy, WinSW -> arcilogger.toml (only if absent) -> secret store -> NTFS rights ->
      PostgreSQL role and database -> services (virtual accounts) -> time sync -> power settings
      (desktop Windows only) -> firewall -> scheduled tasks -> arcilogger-db upgrade (as late as
      possible) -> start -> health check. An installation that stopped while copying program
      files is resumed by running it again. At the end of a first installation, once the log is
      closed, the one-time setup token of the first administrator is shown on the console only
      and the Kamu SM credentials can be stored (both only when a person sits at the console).

    Defaults that need no parameter (ARCILOGGER-Setup.exe passes -Auto, plus -LicenseFile and
    -AllowUncoveredUpgrade only when the setup was started with /LICENSEFILE="<file>" or
    /ALLOWUNCOVEREDUPGRADE):
      -DataRoot       data_dir of an existing arcilogger.toml; otherwise D:\ARCILOGGER on a fixed
                      NTFS D:, else <system drive>\ARCILOGGER (with a warning: the evidence then
                      shares the disk of Windows).
      -ServerAddress  the value install\state.json remembers from an earlier run, else [collector]
                      bind of an existing arcilogger.toml, else the address of the only network
                      adapter with a default gateway (never guessed: the preflight stops when it
                      is not clear). -ServerName: remembered, else arcilogger.internal. A given
                      parameter wins and is remembered. A new arcilogger.toml gets the address as
                      [collector] bind.
      Windows 11 Pro/Enterprise/Education and Windows Server 2022/2025 are supported; Windows 10
      and Windows 11 Home stop the preflight unless -AllowUnsupportedWindows. On desktop Windows
      Set-ArciPower.ps1 switches sleep, hibernation and Fast Startup off (-SkipPower: not).

    Values of the organisation and its network have no default. When one is missing, the installer
    asks for it if a person sits at the console (the setup .exe's window); otherwise the
    preflight stops (or, for the time source, warns). A given parameter wins:
      -SiteName       the organisation name (site_name of arcilogger.toml; reports, e-mails,
                      evidence packages). Required on a first installation; an upgrade keeps the
                      configured name unless -SiteName is given.
      -NtpServer      the local time source ([clock] ntp_server; usually the core router), asked on
                      a first installation, kept on an upgrade. Empty: Windows Time and the hourly
                      clock check use only [clock] fallback_server and the preflight warns.
      -AdminAddress   the admin PC's address(es) or network(s), e.g. 10.0.0.10 or 10.0.0.0/24:
                      443 and 3389 are opened only for them. Remembered by Set-ArciFirewall.ps1,
                      so an upgrade needs it only to change it (not needed with -SkipFirewall).
      -SiteName and -NtpServer go into the new arcilogger.toml; given on an upgrade, they are
      written into the existing one (only those keys change; the previous file is kept as
      arcilogger.toml.before-<time>).
    The log sources ([[sources]]) take no parameter: a new arcilogger.toml has none (the example
    lists the devices of its fictional network as comments only), so the firewall opens 514 to
    no sender until the organisation adds its own; the preflight warns and the next steps say
    what to add (Invoke-SourcesPreflight).

    Licence (docs\LISANS.md / docs\LICENSING.md): the installer installs every program file,
    service, task and table whatever the licence says; the product decides at run time which paid
    modules run, and the 5651 functions always run. At the end it prints the request code to
    e-mail to the licensor (step 6 of the next steps).
      -LicenseFile  a licence file (.lic) to install right after the database schema migration
                    ("arcilogger-db license install"). Read before any change without Python
                    (licence id, maintenance and validity dates); unreadable: the preflight stops.
                    A licence the product refuses (another server, a damaged file, ...) is a
                    warning: the installation goes on and ends with exit code 1.
      Maintenance rule (preflight check "license", install and upgrade): a bundle whose release
      date (bundle-manifest.json release_date) is after the maintenance date of -LicenseFile, or
      of the installed licence (<data_dir>\state\license-status.json), stops the run before any
      change. -AllowUncoveredUpgrade installs it anyway with 5651 Temel only (the paid modules
      are then off until a licence whose maintenance covers it is installed), so a fix for a
      5651 obligation can always be installed. The rule is advisory: the product enforces it
      itself at run time (state version_not_covered). An upgrade without any licence whose vault
      holds sealed days older than 14 days warns that the trial counts as over.

    Browser administration (docs\adr\0014-browser-administration.md, docs\AYARLAR.md /
    docs\SETTINGS.md): the service ArciAdmin (NT SERVICE\ArciAdmin; no listener, no administrator
    rights) applies what is changed under Ayarlar in the web interface, and the task
    \ARCILOGGER\ApplySystem (SYSTEM) derives the firewall's syslog senders, the strict outbound
    rules and the Windows Time peers from the validated configuration. Their guard rails go into
    admin\policy.json, which only administrators write:
      -SourceNetwork  the networks a log source address must lie in (default RFC 1918:
                      10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16).
      -AdminChangeHoldHours  how long the most consequential changes wait before they apply
                      (enabling or shortening retention, disabling or retargeting the replica);
                      default 0, at most 168.
      -ZamaneHoldHours  how long an uploaded Zamane client that is not a known release waits
                      before an administrator may approve it (approve-jar); default 24, 1-168.
      -DisableBrowserAdministration  ArciAdmin refuses every request and the web interface names
                      the console commands instead (-DisableBrowserAdministration:$false switches
                      it on again). No 5651 function depends on it.
      The four are remembered in install\state.json: a later run (an upgrade) keeps them unless
      they are given again. Every run also records the programs the tasks run from outside the
      trusted folders (tsa.java, backup.pg_dump, backup.pg_restore) with their SHA-256 in
      policy.json, copies a tsa.zamane_jar from outside zamane\ into it and records its SHA-256 in
      zamane\zamane-jar.sha256, so sealing never stops because of the checks the tasks make
      before they run a program; a program in a folder that someone other than administrators
      and SYSTEM can write stops the preflight instead (move it or fix the folder's rights).

    -Upgrade:   pg_dump backup first (a failed backup changes nothing), keep the configuration
                as arcilogger.toml.before-upgrade-<time>, pause the scheduled tasks, stop the
                services (ArciAdmin first), keep the old program folder as
                <InstallDir>.previous, install the new version, migrate the database last, start
                (ArciAdmin last). If anything fails, the previous version is put back and started
                again automatically. If the failure happened while the database schema was being
                migrated, only the collector and the web server are started on the previous
                version (the evidence path needs no database) and the command that restores the
                pre-upgrade dump is printed.
    -Rollback:  put <InstallDir>.previous back; with -RestoreDatabase also the last pre-upgrade
                dump (or -DatabaseDump). -Rollback -RestoreDatabase without a previous version
                folder restores only the database. Before the previous version's services start
                (also after a failed upgrade), its Python must load arcilogger.toml; a
                configuration the browser changed in a way it does not know (enabled = false of
                a log source) is replaced by the copy kept before the upgrade, and the installer
                says so: the later browser changes are undone, the audit log keeps them. Without
                -RestoreDatabase, before the swap, this version sets every FortiGate DHCP lease to
                low confidence (arcilogger-db leases prepare-rollback: a version from before
                FortiGate DHCP cannot read those lines) and prints the count; a failure or
                -SkipDatabase only warns. The next arcilogger-db upgrade restores the leases
                the previous version left as they were and prints the days to rebuild for the
                rest. Then, without -RestoreDatabase and when this version's schema has
                0021_db_window (daily partitions) and the previous version's does not (both read
                from their migrations folders) and the database is at 0021_db_window or later
                (arcilogger-db current), "arcilogger-db schema downgrade --to <revision> --json"
                takes the database to the previous version's head, never below the revision 0021
                revises, before the swap (issue #122). A failed downgrade stops the rollback with
                3 before the swap: this version stays installed; run -Rollback again (it goes on
                where it stopped) or -Rollback -RestoreDatabase. A previous schema that cannot be
                told, or -SkipDatabase when the downgrade is needed, stops it with 2 before any
                change. -RestoreDatabase restores the dump into a new database and renames it into
                place; the replaced database is kept as <name>_replaced_<time> (install\state.json,
                replaced_databases). It needs about the current database's size free on the
                PostgreSQL data drive. A restore that fails after the swap names this version's
                installer for the retry (<InstallDir>.rolled-back-<time>\installer\), not the one
                in <InstallDir>\installer\ (the previous version's).
    -Uninstall: removes services, scheduled tasks, the Event Log source ARCILOGGER-Admin, firewall
                rules and the ARCILOGGER program folders (only folders that this installer
                created) and restores the power settings Set-ArciPower.ps1 recorded. It NEVER
                deletes the data folder (spool, vault, exports, logs, backups), the configuration,
                the secret store, the administration folder (admin\), Caddy's CA or the database.
    -Auto:      install, or upgrade when another bundle is installed (used by ARCILOGGER-Setup.exe);
                the console window then waits for Enter before it closes.
    -DryRun / -WhatIf: checks and prints the plan, changes nothing (-PlanPath writes it as JSON).

    Secrets: the postgres superuser password is asked once (or -PgSuperuserCredential) and is never
    written to disk, logs or a command line. The generated ARCILOGGER database password goes to
    the encrypted secret store on stdin; PostgreSQL only receives its SCRAM-SHA-256 verifier. The
    optional Kamu SM customer number and password and the setup token are handled only after the
    transcript has been closed: they reach neither the log nor the plan.

    Exit codes: 0 done, 1 done with warnings (see the health check; also a -LicenseFile the
    product refused or could not install), 2 stopped before any change (usage, bundle or
    preflight problem, the maintenance rule, an unreadable -LicenseFile; also a dry run whose
    real run would stop there), 3 failed while changing the system (the output says what was
    restored and what is left).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -DryRun
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -Upgrade
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File "C:\Program Files\ARCILOGGER\installer\Install-Arcilogger.ps1" -Uninstall
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Upgrade,
    [switch]$Uninstall,
    [switch]$Rollback,
    [switch]$Auto,
    [switch]$DryRun,
    [string]$PlanPath,
    # Default: the folder above installer\ (set below - Windows PowerShell 5.1 leaves $PSScriptRoot
    # empty while it evaluates parameter defaults of a script started with "powershell -File").
    [string]$BundleRoot,
    [string]$InstallDir = $(if ($env:ProgramFiles) { Join-Path $env:ProgramFiles 'ARCILOGGER' } else { 'C:\Program Files\ARCILOGGER' }),
    [string]$ProgramDataDir = $(if ($env:ProgramData) { Join-Path $env:ProgramData 'ARCILOGGER' } else { 'C:\ProgramData\ARCILOGGER' }),
    # Defaults decided below (see .DESCRIPTION): an existing configuration, install\state.json,
    # the drives and network adapters of this computer.
    [string]$DataRoot,
    [string]$ServerAddress,
    [string]$ServerName,
    # No defaults: asked at the console when missing (Resolve-SetupValues), see .DESCRIPTION.
    [string]$SiteName,
    [string]$NtpServer,
    [string[]]$AdminAddress,
    [System.Management.Automation.PSCredential]$PgSuperuserCredential,
    [string]$PgServiceName,
    [string]$PgBinDir,
    [string]$ZamaneJar,
    [string]$ZamaneJarSha256,
    [string]$DatabaseDump,
    [int]$MinFreeGB = 100,
    [int]$TaskWaitMinutes = 30,
    [int]$HealthWaitSeconds = 90,
    [switch]$ResetDatabasePassword,
    [switch]$RestoreDatabase,
    [switch]$SkipDatabase,
    [switch]$SkipFirewall,
    [switch]$SkipTimeSetup,
    [switch]$SkipPower,
    [switch]$AllowUnsupportedWindows,
    [switch]$StrictOutbound,
    # Licence (see .DESCRIPTION): a .lic to install, and the override of the maintenance rule.
    [string]$LicenseFile,
    [switch]$AllowUncoveredUpgrade,
    # Browser administration (see .DESCRIPTION): remembered in install\state.json when given.
    [string[]]$SourceNetwork,
    [int]$AdminChangeHoldHours,
    [int]$ZamaneHoldHours,
    [switch]$DisableBrowserAdministration,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
Import-Module (Join-Path $PSScriptRoot 'ArciInstall.psm1') -Force
# Started by a program that PowerShell 7 started (the setup .exe from a pwsh window, a CI step),
# Windows PowerShell would load PowerShell 7's modules.
Initialize-ArciModulePath

# --- state -----------------------------------------------------------------------------------------

$script:IsDryRun = [bool]($DryRun -or $WhatIfPreference)
# Firewall choices are passed on only when given; otherwise Set-ArciFirewall.ps1 keeps the ones
# recorded in its state file (an upgrade never resets them silently).
$script:StrictOutboundGiven = $PSBoundParameters.ContainsKey('StrictOutbound')
$script:AdminAddressGiven = $PSBoundParameters.ContainsKey('AdminAddress')
$script:PromptedAdminAddress = @()    # typed at the console (Resolve-SetupValues)
# -SiteName / -NtpServer: given, else configured, else asked (Resolve-SetupValues). Sources:
# parameter, config (an existing arcilogger.toml), prompt, none.
$script:SiteNameGiven = $PSBoundParameters.ContainsKey('SiteName') -and [bool]([string]$SiteName).Trim()
$script:NtpServerGiven = $PSBoundParameters.ContainsKey('NtpServer')
$script:SiteNameValue = $(if ($script:SiteNameGiven) { $SiteName.Trim() } else { '' })
$script:SiteNameSource = $(if ($script:SiteNameGiven) { 'parameter' } else { 'none' })
$script:NtpServerValue = $(if ($script:NtpServerGiven) { ([string]$NtpServer).Trim() } else { '' })
$script:NtpServerSource = $(if ($script:NtpServerGiven) { 'parameter' } else { 'none' })
$script:FallbackServer = 'time.ume.tubitak.gov.tr'   # [clock] fallback_server of the configuration in use
$script:ConfigReadError = $null        # site_name or [clock] of the configuration is not readable
$script:SourceCount = $null           # [[sources]] of the configuration in use; $null: not read (Invoke-SourcesPreflight)
$script:SourceSenders = @()           # their enabled sender addresses (Invoke-AdminPolicyPreflight checks them)
# -ServerAddress / -ServerName: given, else remembered or detected (Resolve-ServerIdentity).
$script:ServerAddressGiven = $PSBoundParameters.ContainsKey('ServerAddress') -and [bool]$ServerAddress
$script:ServerNameGiven = $PSBoundParameters.ContainsKey('ServerName') -and [bool]$ServerName
$script:ServerAddressSource = $(if ($script:ServerAddressGiven) { 'parameter' } else { 'none' })
# The browser administration's choices: given ones win and are remembered (Resolve-AdminPolicyChoice).
$script:SourceNetworkGiven = $PSBoundParameters.ContainsKey('SourceNetwork')
$script:HoldHoursGiven = $PSBoundParameters.ContainsKey('AdminChangeHoldHours')
$script:JarHoldHoursGiven = $PSBoundParameters.ContainsKey('ZamaneHoldHours')
$script:BrowserAdministrationGiven = $PSBoundParameters.ContainsKey('DisableBrowserAdministration')
$script:ServerDetection = $null
$script:DataRootSource = 'parameter'   # parameter, config (arcilogger.toml) or auto (Select-ArciDataRoot)
$script:DataRootChoice = $null
$script:OsInfo = $null                 # set by the preflight (Get-ArciOsSupport)
$script:IsClientOs = $false            # Windows 10/11 (ProductType 1): Set-ArciPower.ps1 runs
$script:FreshInstall = $false          # this run installs the program files for the first time
# Licence (Invoke-LicensePreflight): the bundle's release date, the -LicenseFile fields read
# without Python, and the reason the preflight stops the run before any change (also a dry run).
$script:ReleaseDate = $null
$script:LicenseInfo = $null
$script:LicenseStop = $null
# Browser administration: the guard rails of admin\policy.json, given or remembered
# (Resolve-AdminPolicyChoice), and what Invoke-ExecutablePreflight found to record: programs the
# tasks run from outside the trusted folders and the Zamane client (Get-ArciZamaneJarPlan).
$script:AdminPolicyChoice = $null
$script:ExecutableRecords = $null
$script:ZamaneJarPlan = $null
$script:Plan = New-Object System.Collections.Generic.List[object]
$script:Warnings = New-Object System.Collections.Generic.List[string]
$script:Failures = New-Object System.Collections.Generic.List[string]
# A dry run that got past the preflight with failures and plans on (the real run stops there).
$script:PlannedPastPreflight = $false
$script:PgCredential = $PgSuperuserCredential
$script:PgBin = $PgBinDir
$script:BundleInfo = $null
$script:Manifest = $null
$script:ExitCode = 0
$script:DataRootFromConfig = $false
# What an upgrade, repair or rollback has changed so far (used to put the system back on failure).
$script:PausedTasks = $null          # names of the tasks that were enabled before they were paused
$script:OperatorDisabledTasks = $null   # tasks the operator had switched off: they stay off
$script:ProgramMoved = $false        # the old program folder is now <InstallDir>.previous
$script:TasksRegistered = $false     # the tasks were registered from the new program folder
$script:MigrationStarted = $false    # arcilogger-db upgrade was started (the schema may have changed)
$script:RestoreStarted = $false      # a restore step was started: on a failure the database services stay stopped
$script:RestoreChangeStarted = $false   # ... and reached its first change (timescaledb_pre_restore() or CREATE DATABASE)
$script:DatabaseServicesBefore = $null  # a database-only restore: the database services as they were before it stopped them
$script:ServicesNotPutBack = @()        # ... those of them Restore-ArciServiceState could not put back so
$script:RestoreCancelled = $false       # the operator answered no at the restore's question
$script:RestoreDump = $null          # the dump Invoke-DatabaseRestore restores (a retry names it)
$script:RestoreCommitted = $null     # its rename committed: the database, the replaced one, post_restore still due
$script:RestoreCurrentRestoring = $false   # pre_restore ran in the current database, its post_restore not yet
$script:RolledBackDir = $null        # a rollback's swap moved this version to <InstallDir>.rolled-back-<time>
$script:FailedDir = $null            # an upgrade's swap-back moved this version to <InstallDir>.failed-<time>
$script:LastDump = $null
$script:ConfigRestored = $false      # Restore-ConfigForProgram put the copy from before the upgrade back
$script:LeasesLowered = $false       # a rollback lowered the FortiGate leases and this version still runs
$script:SchemaDowngraded = $false    # a rollback took the database schema down and this version still runs
$script:SchemaDowngradeFailed = $false   # the schema downgrade of a rollback failed before the swap
$script:SchemaDowngradeExit = $null  # ... and the exit code of its command (2: it refused the target)
$script:SchemaChain = @()            # this version's Alembic revisions, oldest first (Get-SchemaRollbackPlan)
$PreviousDir = $null
$ConfigPath = $null
$transcript = $null
$script:LogDir = $null

$modeCount = [int][bool]$Upgrade + [int][bool]$Uninstall + [int][bool]$Rollback
$Mode = 'install'
if ($Upgrade) { $Mode = 'upgrade' }
if ($Uninstall) { $Mode = 'uninstall' }
if ($Rollback) { $Mode = 'rollback' }

$ServiceNames = Get-ArciServiceNames
# Services that need PostgreSQL, and ArciAdmin with them: it needs no database, but without the
# API it has nothing to apply and it must not change the configuration of an installation whose
# schema is half migrated. The collector and Caddy need neither: after a failed schema migration
# they keep running on the previous version, so no syslog message is lost.
$DatabaseServiceNames = @('ArciIngest', 'ArciApi', 'ArciWorker', 'ArciAdmin')
$EvidenceServiceNames = @('ArciCollector', 'ArciWeb')
$ReleaseRoot = Split-Path -Parent $PSScriptRoot   # bundle root or installed program folder
$script:ThisInstaller = $PSCommandPath            # this script (Get-RestoreRetryInstaller)

# --- plan and output ---------------------------------------------------------------------------------

function Add-PlanItem {
    param([string]$Id, [string]$Status, [string]$Text, $Detail = $null)
    $item = [pscustomobject]@{ id = $Id; status = $Status; text = $Text; detail = $Detail }
    $script:Plan.Add($item)
}

function Invoke-Step {
    <# Run one step, or only record it in a dry run. Steps set shared state through $script: variables. #>
    param([string]$Id, [string]$Text, [scriptblock]$Action, $Detail = $null)
    if ($script:IsDryRun) {
        Add-PlanItem -Id $Id -Status 'planned' -Text $Text -Detail $Detail
        Write-Host ('[PLAN] ' + $Text)
        return
    }
    Write-ArciStep $Text
    & $Action
    Add-PlanItem -Id $Id -Status 'done' -Text $Text -Detail $Detail
}

function Add-Check {
    param([string]$Id, [ValidateSet('ok', 'warn', 'fail', 'info', 'unknown')][string]$Status, [string]$Text)
    Add-PlanItem -Id ('preflight.' + $Id) -Status $Status -Text $Text
    switch ($Status) {
        'ok' { Write-ArciInfo ('[OK]    ' + $Text) }
        'warn' { Write-ArciWarn $Text; $script:Warnings.Add($Text) }
        'fail' { Write-Host ('    [HATA]  ' + $Text) -ForegroundColor Red; $script:Failures.Add($Text) }
        'info' { Write-ArciInfo ('[BİLGİ] ' + $Text) }
        default { Write-ArciInfo ('[?]     ' + $Text) }
    }
}

function Save-Plan {
    if (-not $PlanPath) { return }
    $bundle = $null
    if ($script:BundleInfo) {
        $bundle = [pscustomobject]@{
            root         = $BundleRoot
            version      = $(if ($script:Manifest) { $script:Manifest.version } else { $null })
            release_date = $script:ReleaseDate
            fingerprint  = $script:BundleInfo.Fingerprint
            files        = $script:BundleInfo.Files
        }
    }
    $document = [pscustomobject]@{
        product    = 'ARCILOGGER'
        mode       = $Mode
        dry_run    = $script:IsDryRun
        would_stop = ($script:Failures.Count -gt 0)
        bundle     = $bundle
        paths      = [pscustomobject]@{ install_dir = $InstallDir; previous_dir = $PreviousDir; program_data_dir = $ProgramDataDir; data_root = $DataRoot; config = $ConfigPath }
        items      = $script:Plan.ToArray()
        warnings   = $script:Warnings.ToArray()
        failures   = $script:Failures.ToArray()
        exit_code  = $script:ExitCode
    }
    Write-ArciUtf8File -Path $PlanPath -Text ($document | ConvertTo-Json -Depth 8)
}

function Wait-ForOperator {
    <#
    ARCILOGGER-Setup.exe (-Auto) runs this script in a console window of its own, which closes as
    soon as the script ends: keep it open until the operator has read the result. Never when
    nobody sits at the console (Test-ArciInteractiveConsole), so CI and scripted runs never hang.
    #>
    if (-not $Auto -or -not (Test-ArciInteractiveConsole)) { return }
    Write-Host ''
    try { [void](Read-Host 'Kapatmak için Enter''a basın [press Enter to close]') } catch { Write-Verbose 'no console input' }
}

function Stop-Installer([int]$Code, [string]$Message) {
    if ($Message) {
        if ($Code -eq 0) { Write-ArciStep $Message } else { Write-Host $Message -ForegroundColor Red }
    }
    $script:ExitCode = $Code
    Save-Plan
    if (-not $script:IsDryRun -and $transcript) {
        try { Stop-Transcript | Out-Null } catch { Write-Verbose 'no transcript' }
        # Keep the transcript next to the service logs (it never contains a password). Before the
        # data folder exists (a stop in the preflight) it stays only in %TEMP%.
        $log = $transcript
        if ($script:LogDir -and (Test-Path -LiteralPath $script:LogDir) -and (Test-Path -LiteralPath $transcript)) {
            $copy = Join-Path $script:LogDir ([System.IO.Path]::GetFileName($transcript))
            try {
                Copy-Item -LiteralPath $transcript -Destination $copy -Force
                $log = $copy
            }
            catch { Write-Verbose 'transcript not copied' }
        }
        if (Test-Path -LiteralPath $log) { Write-Host ('Kurulum günlüğü [install log]: {0}' -f $log) }
    }
    # Nothing below is recorded: the transcript is closed and the plan is written.
    if (-not $script:IsDryRun -and $script:FreshInstall -and $Code -le 1 -and (Get-Command Complete-FirstInstall -ErrorAction SilentlyContinue)) {
        Complete-FirstInstall
    }
    Wait-ForOperator
    exit $Code
}

function ConvertTo-FullPath([string]$Path) {
    <# Relative to the PowerShell location (not the process directory, which PowerShell does not move). #>
    if (-not $Path) { return $Path }
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)
}

function Resolve-InstallPath([string]$Path, [string]$Name) {
    <# Absolute path without a trailing separator (a trailing "\" before a closing quote breaks Windows argument parsing). #>
    $full = ConvertTo-FullPath $Path
    $root = [System.IO.Path]::GetPathRoot($full)
    $trimmed = $full.TrimEnd([char[]]@([char]92, [char]47))
    if ($trimmed -eq $root.TrimEnd([char[]]@([char]92, [char]47))) {
        Stop-Installer 2 ("{0} bir sürücü kökü olamaz [cannot be a drive root]: {1}" -f $Name, $Path)
    }
    $trimmed
}

# --- paths -------------------------------------------------------------------------------------------

$PlanPath = ConvertTo-FullPath $PlanPath
if (-not $BundleRoot) { $BundleRoot = $ReleaseRoot }
$BundleRoot = ConvertTo-FullPath $BundleRoot
$ZamaneJar = ConvertTo-FullPath $ZamaneJar
$DatabaseDump = ConvertTo-FullPath $DatabaseDump
$LicenseFile = ConvertTo-FullPath $LicenseFile
if ($modeCount -gt 1) { Stop-Installer 2 '-Upgrade, -Uninstall ve -Rollback birlikte kullanılamaz [use only one mode].' }
if (($LicenseFile -or $AllowUncoveredUpgrade) -and ($Uninstall -or $Rollback)) {
    Stop-Installer 2 '-LicenseFile ve -AllowUncoveredUpgrade yalnızca kurulum ve yükseltmede kullanılır [only for an installation or an upgrade].'
}

$InstallDir = Resolve-InstallPath $InstallDir 'InstallDir'
$ProgramDataDir = Resolve-InstallPath $ProgramDataDir 'ProgramDataDir'
$PreviousDir = $InstallDir + '.previous'
$ConfigPath = Join-Path $ProgramDataDir 'arcilogger.toml'
$StateDir = Join-Path $ProgramDataDir 'install'
$StatePath = Join-Path $StateDir 'state.json'
$FirewallStatePath = Join-Path $StateDir 'firewall-state.json'
$PowerStatePath = Join-Path $StateDir 'power-state.json'
# The browser administration (Config.admin_dir; contract 2.2), the Zamane client folder, the
# secret store's values and the record of the data root every task and service checks the
# configuration's data_dir against (config.DATA_ROOT_RECORD, written on every run).
$AdminDir = Join-Path $ProgramDataDir 'admin'
$AdminKeysDir = Join-Path $AdminDir 'keys'
$PolicyPath = Join-Path $AdminDir 'policy.json'
$ZamaneDir = Join-Path $ProgramDataDir 'zamane'
$SecretsFile = Join-Path $ProgramDataDir 'secrets.json'
$DataRootRecord = Join-Path $ProgramDataDir 'data-root.txt'
$CaddyRootSource = Join-Path $ProgramDataDir 'caddy\data\caddy\pki\authorities\local\root.crt'
$CaddyRootCopy = Join-Path $ProgramDataDir 'caddy-root.crt'
$TarExe = $(if ($env:SystemRoot) { Join-Path $env:SystemRoot 'System32\tar.exe' } else { 'tar' })

function Set-DataRootPaths {
    $script:DataRoot = Resolve-InstallPath $script:DataRoot 'DataRoot'
    $script:BackupDir = Join-Path $script:DataRoot 'backups\db'
    $script:LogDir = Join-Path $script:DataRoot 'logs'
}

function Get-ConfiguredDataRoot {
    <# data_dir of an existing arcilogger.toml (no Python may be installed yet: Get-ArciConfiguredDataDir). #>
    Get-ArciConfiguredDataDir -ConfigPath $ConfigPath
}

$separators = [char[]]@([char]92, [char]47)
$configured = Get-ConfiguredDataRoot
$dataRootGiven = $PSBoundParameters.ContainsKey('DataRoot') -and [bool]$DataRoot
# The record (data-root.txt) first: ArciAdmin can write data_dir, so a run never takes a changed
# one from the configuration and never moves the record unless -DataRoot says so (Day 0 note
# D0-2). A problem stops the run in the preflight, before any change (Invoke-Preflight).
$script:DataRootRecordFacts = Read-ArciDataRootRecord -Path $DataRootRecord
$script:DataRootResolution = Resolve-ArciDataRoot -Record $script:DataRootRecordFacts `
    -Configured $(if ($configured) { (ConvertTo-FullPath $configured).TrimEnd($separators) } else { '' }) `
    -Given $(if ($dataRootGiven) { (ConvertTo-FullPath $DataRoot).TrimEnd($separators) } else { '' }) `
    -AdminInstalled:(Test-Path -LiteralPath $AdminDir)
if ($script:DataRootResolution.Path) {
    $DataRoot = $script:DataRootResolution.Path
    $script:DataRootSource = $script:DataRootResolution.Source
    $script:DataRootFromConfig = $script:DataRootSource -in @('record', 'config')
}
elseif ($script:DataRootResolution.Problem) {
    # Only to finish the preflight, which stops the run: the record when it can be read, else data_dir.
    $fallback = @([string]$script:DataRootRecordFacts.Value, [string]$configured, [string]$DataRoot) | Where-Object { $_ } | Select-Object -First 1
    if ($fallback) {
        $DataRoot = $fallback
        $script:DataRootFromConfig = $true
        $script:DataRootSource = 'record'
    }
}
if (-not $DataRoot) {
    # A new installation without -DataRoot (the setup .exe never passes it): a fixed NTFS D:,
    # otherwise the system drive; the preflight checks the drive and warns about the latter.
    $script:DataRootChoice = Select-ArciDataRoot -DataDrive (Get-ArciDriveFacts -Root 'D:\') -SystemDrive $env:SystemDrive
    $DataRoot = $script:DataRootChoice.Path
    $script:DataRootSource = 'auto'
}
Set-DataRootPaths

$Venv = Join-Path $InstallDir 'venv'
$VenvPython = Join-Path $Venv 'Scripts\python.exe'
$BasePython = Join-Path $InstallDir 'python\python.exe'
$CoreExe = Join-Path $Venv 'Scripts\arcilogger-core.exe'
$DbExe = Join-Path $Venv 'Scripts\arcilogger-db.exe'
# The schema downgrade of a rollback (issue #122): this version's command (arcilogger-db <command>
# --to <revision> --json), and the revision of the daily partitions a version before it cannot handle.
$SchemaDowngradeCommand = @('schema', 'downgrade')
$DailyPartitionRevision = '0021_db_window'
# Issue #164: the revisions a rollback may take down, newest first: those above the floor (the
# revision 0021_db_window revises, #122-5), each judged lossless for 5651 data (raw logs, log rows,
# leases and the audit log keep every row; what goes is derived, or a request). Every revision
# above the floor is judged before it ships, and listed here or in $LossyDowngrades (a test
# compares both lists with the migrations folder and fails until then); a rollback whose way down
# crosses a revision not in $LosslessDowngrades is refused before any change
# (Get-SchemaRollbackPlan).
$LosslessDowngrades = @(
    # Turns the usage rollups' period (work, break, off) back into off_hours (off -> true, else
    # false); a day with break rows is first deleted from the seven rollup tables and usage_days:
    # usage analytics, derived data only (raw logs, log rows, leases and the audit log keep every
    # row). The older program rebuilds by itself only the last usage.auto_rebuild_days days of
    # them; an older day needs a rebuild request, and a day before the database window its logs
    # back from the vault first (archive reload; v0023_usage_period.py, KULLANIM.md 2.5).
    '0023_usage_period',
    # Drops leases.moves, nothing else: the missed moves the lease job recorded, derived data (the
    # FortiGate DHCP raw logs keep the ACKs; the database keeps fg_dhcp rows for its window only).
    # Not rebuilt: a later upgrade brings the column back NULL (no backfill, contract #127), the
    # lease job records the moves it sees from its next run on, and a lease's moves from before the
    # window are then gone from the database. The "vd" keys stay in leases.evidence, raw logs, log
    # rows and every other lease column as they are (v0022_lease_moves.py).
    '0022_lease_moves',
    # Merges the daily partitions into monthly ones with every row, a step per table and month;
    # drops archive_reloads (the reload requests: the sealed days stay in the vault),
    # db.reload_keep_days and the window's status rows; db.retention_days 30 -> 400, which the
    # command audits (a longer window, nothing deleted; v0021_db_window.py, contract #122).
    '0021_db_window'
)
# The revisions above the floor whose downgrade loses 5651 data, each with what it loses in a
# comment: none today. Never in $LosslessDowngrades, so a rollback across one is refused; the way
# back is the pre-upgrade dump (-Rollback -RestoreDatabase). Read by the test, not by the plan.
$LossyDowngrades = @(
)
$ApiExe = Join-Path $Venv 'Scripts\arcilogger-api.exe'
$ServicesDir = Join-Path $InstallDir 'services'
$ScriptsDir = Join-Path $ReleaseRoot 'scripts'

# --- small helpers ---------------------------------------------------------------------------------

function Read-JsonFile([string]$Path) {
    [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
}

function Read-InstallState {
    if (Test-Path -LiteralPath $StatePath) { return (Read-JsonFile $StatePath) }
    [pscustomobject]@{ database_provisioned = $false }
}

function Write-InstallState([hashtable]$Changes) {
    $state = @{}
    $current = Read-InstallState
    foreach ($property in $current.PSObject.Properties) { $state[$property.Name] = $property.Value }
    foreach ($key in $Changes.Keys) { $state[$key] = $Changes[$key] }
    $state['updated_at'] = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    Write-ArciUtf8File -Path $StatePath -Text (([pscustomobject]$state) | ConvertTo-Json -Depth 4)
}

function Get-InstalledVersion {
    if ((Get-ArciProgramFolderState -Path $InstallDir) -ne 'installed') { return $null }
    Read-JsonFile (Join-Path $InstallDir 'VERSION.json')
}

function Test-PathInside([string]$Path, [string]$Folder) {
    <# True when $Path is $Folder itself or lies below it (case-insensitive, either separator). #>
    $p = $Path.TrimEnd($separators)
    $f = $Folder.TrimEnd($separators)
    if ($p -ieq $f) { return $true }
    foreach ($separator in @('\', '/')) {
        if ($p.StartsWith($f + $separator, [System.StringComparison]::OrdinalIgnoreCase)) { return $true }
    }
    $false
}

function Get-SystemFolders {
    <# Windows folders that must never be deleted, even through a mistyped -InstallDir. #>
    $folders = New-Object System.Collections.Generic.List[string]
    foreach ($name in @('SystemRoot', 'windir', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramW6432', 'ProgramData', 'CommonProgramFiles', 'USERPROFILE', 'PUBLIC', 'ALLUSERSPROFILE')) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) { $folders.Add([System.IO.Path]::GetFullPath($value).TrimEnd($separators)) }
    }
    if ($env:USERPROFILE) { $folders.Add((Split-Path -Parent ([System.IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd($separators)))) }
    $folders.ToArray()
}

function Assert-SafeToDelete([string]$Path) {
    <#
    Only ARCILOGGER program folders can be deleted: <InstallDir> or <InstallDir>.<suffix>, holding
    this installer's VERSION.json or INSTALL-IN-PROGRESS.json (or nothing at all). Never the data
    root, the configuration folder, a drive root or a Windows folder.
    #>
    $full = [System.IO.Path]::GetFullPath($Path).TrimEnd($separators)
    if ($full -ieq ([System.IO.Path]::GetPathRoot($full)).TrimEnd($separators)) {
        throw ("Güvenlik: sürücü kökü silinmez [refusing to delete a drive root]: {0}" -f $full)
    }
    $protected = @($DataRoot, $ProgramDataDir) | ForEach-Object { [System.IO.Path]::GetFullPath($_).TrimEnd($separators) }
    foreach ($keep in $protected) {
        if ((Test-PathInside $full $keep) -or (Test-PathInside $keep $full)) {
            throw ("Güvenlik: veri/yapılandırma klasörü silinmez [refusing to delete protected folder]: {0}" -f $full)
        }
    }
    foreach ($system in Get-SystemFolders) {
        if (Test-PathInside $system $full) {
            throw ("Güvenlik: Windows klasörü silinmez [refusing to delete a system folder]: {0}" -f $full)
        }
    }
    if (-not ($full -ieq $InstallDir -or $full.StartsWith($InstallDir + '.', [System.StringComparison]::OrdinalIgnoreCase))) {
        throw ("Güvenlik: yalnızca program klasörü silinir [only the program folder may be deleted]: {0}" -f $full)
    }
    $state = Get-ArciProgramFolderState -Path $full
    if ($state -eq 'foreign') {
        throw ("Güvenlik: {0} bir ARCILOGGER program klasörü değil (VERSION.json yok); silinmedi [not an ARCILOGGER program folder, refusing to delete]." -f $full)
    }
}

function Get-ProgramFolders {
    <# Existing program folders: <InstallDir>, .previous and those an upgrade or rollback set aside. #>
    $folders = New-Object System.Collections.Generic.List[string]
    foreach ($folder in @($InstallDir, $PreviousDir)) {
        if (Test-Path -LiteralPath $folder) { $folders.Add($folder) }
    }
    $parent = Split-Path -Parent $InstallDir
    $leaf = Split-Path -Leaf $InstallDir
    if ($parent -and (Test-Path -LiteralPath $parent)) {
        foreach ($item in @(Get-ChildItem -LiteralPath $parent -Directory -Force -ErrorAction SilentlyContinue)) {
            foreach ($suffix in @('.failed-', '.rolled-back-')) {
                if ($item.Name.StartsWith($leaf + $suffix, [System.StringComparison]::OrdinalIgnoreCase)) { $folders.Add($item.FullName) }
            }
        }
    }
    $folders.ToArray()
}

function Get-BundleComponent([string]$Name) {
    $found = @($script:Manifest.components | Where-Object { $_.name -eq $Name })
    if ($found.Count -ne 1) { Stop-Installer 2 ("Pakette bileşen yok [component missing in bundle]: {0}" -f $Name) }
    $found[0]
}

function Get-BundleFile([string]$Relative) {
    Join-Path $BundleRoot ($Relative.Replace('/', '\'))
}

# --- PostgreSQL ------------------------------------------------------------------------------------

function Find-PostgresService {
    if (-not (Get-Command Get-CimInstance -ErrorAction SilentlyContinue)) { return $null }
    $services = @(Get-CimInstance -ClassName Win32_Service -Filter "Name LIKE 'postgresql%'" -ErrorAction SilentlyContinue)
    if ($PgServiceName) { $services = @($services | Where-Object { $_.Name -eq $PgServiceName }) }
    $preferred = @($services | Where-Object { $_.Name -match '-16$' -or $_.PathName -match '\\16\\bin\\' })
    if ($preferred.Count -gt 0) { return $preferred[0] }
    if ($services.Count -gt 0) { return $services[0] }
    $null
}

function Get-PostgresBinDir($Service) {
    if ($script:PgBin) { return $script:PgBin }
    if ($null -eq $Service) { return $null }
    $match = [regex]::Match([string]$Service.PathName, '^\s*"?(?<exe>[^"]+?pg_ctl\.exe)"?')
    if ($match.Success) { return (Split-Path -Parent $match.Groups['exe'].Value) }
    $null
}

function Get-PostgresDataDir($Service) {
    if ($null -eq $Service) { return $null }
    $match = [regex]::Match([string]$Service.PathName, '-D\s+"(?<dir>[^"]+)"')
    if ($match.Success) { return $match.Groups['dir'].Value }
    $null
}

function Get-PgCredential {
    if ($script:PgCredential) { return $script:PgCredential }
    Write-Host ''
    Write-Host 'PostgreSQL süper kullanıcısının (postgres) parolası gerekiyor. Bir kez sorulur; diske, günlüğe ve komut satırına yazılmaz.'
    Write-Host '[The postgres superuser password is asked once and never stored, logged or put on a command line.]'
    $secure = Read-Host -AsSecureString 'postgres parolası [password]'
    $script:PgCredential = New-Object System.Management.Automation.PSCredential -ArgumentList 'postgres', $secure
    $script:PgCredential
}

function Get-DatabaseSettings {
    $settings = [ordered]@{ host = '127.0.0.1'; port = 5432; name = 'arcilogger'; user = 'arcilogger' }
    if ((Test-Path -LiteralPath $ConfigPath) -and (Test-Path -LiteralPath $VenvPython)) {
        $config = Read-ArciTomlFile -Python $VenvPython -ConfigPath $ConfigPath
        foreach ($key in @('host', 'port', 'name', 'user')) {
            $value = Get-ArciConfigValue -Config $config -Path ('database.' + $key)
            if ($null -ne $value) { $settings[$key] = $value }
        }
    }
    foreach ($key in @('name', 'user')) {
        if ([string]$settings[$key] -notmatch '^[a-z_][a-z0-9_]{0,62}$') {
            throw ("[database] {0} yalnızca küçük harf, rakam ve _ olabilir [invalid identifier]: {1}" -f $key, $settings[$key])
        }
    }
    [pscustomobject]$settings
}

function Get-ApiEndpoint {
    <#
    Where ArciApi listens: [api] host/port of arcilogger.toml (Get-ArciApiEndpoint), default
    127.0.0.1:8000 while there is no configuration or no Python yet (the example configuration
    uses the same values). Caddy's upstream (ArciWeb.xml), the health check and the preflight
    port probe follow it.
    #>
    $config = $null
    if ((Test-Path -LiteralPath $ConfigPath) -and (Test-Path -LiteralPath $VenvPython)) {
        $config = Read-ArciTomlFile -Python $VenvPython -ConfigPath $ConfigPath
    }
    Get-ArciApiEndpoint -Config $config
}

function Invoke-PgTool {
    <# psql / pg_dump / pg_restore as the superuser; the password only in the child's environment. #>
    param([string]$Tool, [string[]]$ArgumentList, [string]$StdinText, [string[]]$Redact = @())
    $credential = Get-PgCredential
    $plain = $credential.GetNetworkCredential().Password
    $exe = Join-Path $script:PgBin $Tool
    $environment = @{ PGPASSWORD = $plain; PGCLIENTENCODING = 'UTF8'; PGCONNECT_TIMEOUT = '15' }
    $parameters = @{ FilePath = $exe; ArgumentList = $ArgumentList; Environment = $environment; Redact = (@($plain) + $Redact) }
    if ($PSBoundParameters.ContainsKey('StdinText')) { $parameters['StdinText'] = $StdinText }
    Invoke-ArciProcess @parameters
}

function Invoke-Psql {
    param([string]$Database, [string]$Sql, [string[]]$Redact = @(), [switch]$TuplesOnly)
    $db = Get-DatabaseSettings
    $credential = Get-PgCredential
    $arguments = @('-X', '-q', '-v', 'ON_ERROR_STOP=1', '-h', [string]$db.host, '-p', [string]$db.port, '-U', $credential.UserName, '-d', $Database, '-f', '-')
    if ($TuplesOnly) { $arguments = @('-t', '-A') + $arguments }
    Invoke-PgTool -Tool 'psql.exe' -ArgumentList $arguments -StdinText $Sql -Redact $Redact
}

function Test-DatabaseSecret {
    if (-not (Test-Path -LiteralPath $CoreExe)) { return $false }
    $result = Invoke-ArciProcess -FilePath $CoreExe -ArgumentList @('--config', $ConfigPath, 'secret', 'list') -AllowFailure
    ($result.ExitCode -eq 0) -and ($result.StdOut -match '(?m)^database\.password\b')
}

function Invoke-DatabaseProvision {
    $db = Get-DatabaseSettings
    $state = Read-InstallState
    $provisioned = ($state.PSObject.Properties.Name -contains 'database_provisioned') -and [bool]$state.database_provisioned
    if ($provisioned -and (Test-DatabaseSecret) -and -not $ResetDatabasePassword) {
        Write-ArciInfo ("Veritabanı '{0}' ve rol '{1}' zaten hazır [already provisioned]." -f $db.name, $db.user)
        return
    }
    $password = New-ArciRandomPassword -Length 40
    $verifier = ConvertTo-ArciScramVerifier -Password $password
    # 1) Store first: proves this core version accepts the secret before PostgreSQL is changed.
    [void](Invoke-ArciProcess -FilePath $CoreExe -ArgumentList @('--config', $ConfigPath, 'secret', 'set', 'database.password') -StdinText ($password + "`n") -Redact @($password))
    # 2) Role and database. Only the SCRAM verifier reaches the server; names are validated above.
    $sql = @(
        '\set ON_ERROR_STOP on',
        ("\set role_name '{0}'" -f $db.user),
        ("\set db_name '{0}'" -f $db.name),
        ("\set role_verifier '{0}'" -f $verifier),
        "SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L', :'role_name', :'role_verifier') WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role_name') \gexec",
        "SELECT format('ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L', :'role_name', :'role_verifier') \gexec",
        # A database this installer creates gets the C locale (an existing one is never altered):
        # clusters created with the Windows locale failed to start after a Windows update renamed
        # it (Turkish_Turkey -> Turkish_Türkiye, PostgreSQL BUG #18196); C is not renamed.
        "SELECT format('CREATE DATABASE %I OWNER %I TEMPLATE template0 ENCODING %L LOCALE %L', :'db_name', :'role_name', 'UTF8', 'C') WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db_name') \gexec",
        # No TimescaleDB telemetry (ADR-0005, spec 21): set for the database BEFORE the extension
        # exists, because CREATE EXTENSION starts its scheduler and the telemetry job runs at
        # once. Accepted as a placeholder while TimescaleDB is not loaded (superuser), so it also
        # covers TimescaleDB installed later (docs/KURULUM.md 2.1).
        "SELECT format('ALTER DATABASE %I SET timescaledb.telemetry_level = %L', :'db_name', 'off') \gexec",
        "SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db_name') \gexec",
        "SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', :'db_name', :'role_name') \gexec"
    ) -join "`n"
    [void](Invoke-Psql -Database 'postgres' -Sql ($sql + "`n") -Redact @($verifier))
    # 3) TimescaleDB is optional (ARCILOGGER runs on plain PostgreSQL); creating it needs a superuser.
    $available = (Invoke-Psql -Database $db.name -TuplesOnly -Sql "SELECT count(*) FROM pg_available_extensions WHERE name = 'timescaledb';`n").StdOut.Trim()
    $preload = (Invoke-Psql -Database $db.name -TuplesOnly -Sql "SHOW shared_preload_libraries;`n").StdOut
    if ($available -eq '1' -and $preload -match 'timescaledb') {
        # This session already runs with telemetry off (database setting above). Once the
        # extension is loaded the parameter is known, so it is also switched off server-wide
        # (postgresql.auto.conf), for every other database of this server.
        $timescaleSql = @(
            'CREATE EXTENSION IF NOT EXISTS timescaledb;',
            "ALTER SYSTEM SET timescaledb.telemetry_level = 'off';",
            'SELECT pg_reload_conf();'
        ) -join "`n"
        [void](Invoke-Psql -Database $db.name -Sql ($timescaleSql + "`n"))
        Write-ArciInfo 'TimescaleDB eklentisi etkin, telemetri kapalı [TimescaleDB extension enabled, telemetry off].'
    }
    else {
        Write-ArciInfo 'TimescaleDB yok ya da yüklenmemiş; düz PostgreSQL kullanılıyor [TimescaleDB not loaded, using plain PostgreSQL].'
    }
    Write-InstallState @{ database_provisioned = $true; database_name = $db.name; database_user = $db.user }
    Write-ArciInfo ("Rol '{0}', veritabanı '{1}' hazır; parola gizli depoda (database.password) [role and database ready]." -f $db.user, $db.name)
}

function Invoke-DatabaseBackup {
    $db = Get-DatabaseSettings
    $state = Read-InstallState
    if (-not (($state.PSObject.Properties.Name -contains 'database_provisioned') -and [bool]$state.database_provisioned)) {
        Write-ArciWarn 'Veritabanı henüz kurulmamış; yedek atlandı [no database yet, backup skipped].'
        return
    }
    [void](New-Item -ItemType Directory -Path $script:BackupDir -Force)
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
    $target = Join-Path $script:BackupDir ("{0}-{1}Z-before-{2}.dump" -f $db.name, $stamp, $script:Manifest.version)
    $credential = Get-PgCredential
    try {
        [void](Invoke-PgTool -Tool 'pg_dump.exe' -ArgumentList @('-h', [string]$db.host, '-p', [string]$db.port, '-U', $credential.UserName, '-Fc', '-f', $target, [string]$db.name))
        # Proves the archive is complete and readable before anything is changed.
        [void](Invoke-PgTool -Tool 'pg_restore.exe' -ArgumentList @('--list', $target))
    }
    catch {
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue }
        throw
    }
    Write-ArciInfo ("Yedek [backup]: {0} (SHA-256 {1})" -f $target, (Get-ArciFileSha256 -Path $target))
    $script:LastDump = $target
    Write-InstallState @{ last_dump = $target }
}

function Get-RestoreDump {
    <# -DatabaseDump, else the dump of the failed upgrade, else the newest dump in the backup folder. #>
    if ($DatabaseDump) {
        if (-not (Test-Path -LiteralPath $DatabaseDump -PathType Leaf)) { throw ("Yedek dosyası yok [dump not found]: {0}" -f $DatabaseDump) }
        return $DatabaseDump
    }
    $state = Read-InstallState
    foreach ($key in @('pending_database_restore', 'last_dump')) {
        if ($state.PSObject.Properties.Name -contains $key -and $state.$key -and (Test-Path -LiteralPath ([string]$state.$key) -PathType Leaf)) {
            return [string]$state.$key
        }
    }
    $latest = Get-ChildItem -LiteralPath $script:BackupDir -Filter '*.dump' -ErrorAction SilentlyContinue | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    if ($null -eq $latest) { throw 'Geri yüklenecek yedek yok [no dump found]; -DatabaseDump verin.' }
    $latest.FullName
}

function Get-RestoreDatabaseName([string]$Name, [string]$Tag, [string]$Stamp) {
    <# <name>_<tag>_<stamp>, the name cut so that it fits PostgreSQL's 63 characters. #>
    $suffix = '_{0}_{1}' -f $Tag, $Stamp
    $Name.Substring(0, [Math]::Min($Name.Length, 63 - $suffix.Length)) + $suffix
}

function Get-RestoreDatabaseSpace([string]$Names, [string]$DbHost) {
    <#
    What the restore needs and what the drive of PostgreSQL's data directory has free: CREATE
    DATABASE puts the new database there (pg_default) while the current one is kept. Need is the
    size of the current database (pg_database_size) and a quarter more, plus max_wal_size for the
    WAL pg_restore writes on the same drive. The quarter is a margin, not a measure: the dump's
    TimescaleDB chunks of its last days come back uncompressed where the current database has
    compressed them since, and the index builds spill to pgsql_tmp (verify on site,
    docs/KURULUM.md section 16). The data directory is the server's own (data_directory, readable
    by the superuser), else the -D of the PostgreSQL service. Free is $null when neither is known,
    the drive cannot be read (Get-ArciDriveFacts, as the preflight's data drive check) or the
    server is not this computer (Local: [database] host is not loopback, its data directory is a
    path on that server).
    #>
    $sql = $Names + "`nSELECT pg_database_size(:'db_name'), pg_size_bytes(current_setting('max_wal_size')), coalesce((SELECT setting FROM pg_settings WHERE name = 'data_directory'), '');`n"
    $size, $wal, $directory = @(([string](Invoke-Psql -Database 'postgres' -TuplesOnly -Sql $sql).StdOut).Trim() -split '\|', 3)
    $address = $null
    $local = ($DbHost -eq 'localhost') -or ([System.Net.IPAddress]::TryParse($DbHost, [ref]$address) -and [System.Net.IPAddress]::IsLoopback($address))
    if (-not $directory -and $local) { $directory = Get-PostgresDataDir (Find-PostgresService) }
    $free = $null
    if ($directory -and $local) {
        $drive = Get-ArciDriveFacts -Root ([System.IO.Path]::GetPathRoot([string]$directory))
        if ($null -ne $drive) { $free = $drive.FreeBytes }
    }
    $need = [int64]$size + [int64][Math]::Ceiling([int64]$size / 4) + [int64]$wal
    [pscustomobject]@{ Size = [int64]$size; Need = $need; Directory = $directory; Free = $free; Local = $local }
}

function Get-OpenDatabaseSession([string]$Names) {
    <#
    The other sessions on the current or the new database (pg_stat_activity) in one line, for the
    rename's "being accessed by other users"; empty when there are none or they cannot be read.
    #>
    $sql = $Names + "`nSELECT string_agg(format('pid %s %s (%s)', pid, quote_literal(coalesce(nullif(application_name, ''), backend_type)), datname), ', ' ORDER BY pid) FROM pg_stat_activity WHERE datname IN (:'db_name', :'new_name') AND pid <> pg_backend_pid();`n"
    try { return ([string](Invoke-Psql -Database 'postgres' -TuplesOnly -Sql $sql).StdOut).Trim() }
    catch { return '' }
}

function Add-ReplacedDatabase([string]$Name, [string]$Dump, [bool]$Restoring) {
    <#
    Right after the rename commits (before anything else can fail): the replaced database is named
    on the console, then goes into install\state.json (replaced_databases, every restore's, oldest
    first: a later restore never drops an earlier one from the record). A record that cannot be
    written is a warning that names the database again: the restore goes on (post_restore of the
    database in place). A TimescaleDB one is still in restore mode: timescaledb_pre_restore() set
    timescaledb.restoring for the database, and the rename kept it. Its
    timescaledb_post_restore() is not run here: it would start that database's background jobs.
    #>
    Write-ArciWarn ("Önceki veritabanı '{0}' adıyla saklandı: yedekten sonra yazılan satırlar yalnızca onda; diskte yer tutar, silmek yöneticinin kararıdır (docs\YEDEKLEME.md bölüm 5) [the replaced database is kept as '{0}': the rows written after the dump are only there; it takes disk space, removing it is the administrator's decision (docs\BACKUP.md section 5)]" -f $Name)
    if ($Restoring) {
        Write-ArciWarn ("'{0}' TimescaleDB geri yükleme kipinde kaldı (timescaledb.restoring = on): yeniden kullanmadan önce onda SELECT timescaledb_post_restore(); çalıştırın [it stays in TimescaleDB restore mode: run SELECT timescaledb_post_restore(); in it before putting it back]" -f $Name)
    }
    try {
        $state = Read-InstallState
        $kept = @()
        if ($state.PSObject.Properties.Name -contains 'replaced_databases') { $kept = @($state.replaced_databases | Where-Object { $null -ne $_ }) }
        $entry = [pscustomobject]@{ name = $Name; dump = $Dump; replaced_at = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'); timescaledb_restoring = $Restoring }
        Write-InstallState @{ replaced_databases = @($kept + $entry) }
    }
    catch {
        Write-ArciWarn ("Yerine geçilen veritabanı '{0}' install\state.json dosyasına yazılamadı; adını not edin [the replaced database could not be recorded in install\state.json: note its name]: {1}" -f $Name, $_.Exception.Message)
    }
}

function Invoke-DatabaseRestore {
    <#
    The dump replaces the database (docs/YEDEKLEME.md section 5: a restore always goes into a new
    database). pg_restore runs into a new database <name>_restore_<stamp>, with the owner,
    encoding, locale, database settings and rights of the current one, one transaction per object
    and stopping at the first error, as arcilogger-db restore: not --single-transaction, whose
    locks on every table, index and TOAST table a TimescaleDB database's chunks outgrow
    (max_locks_per_transaction, "out of shared memory"); a failed restore is dropped anyway. Only
    then, in one transaction, the current database is renamed aside to <name>_replaced_<stamp>
    (kept: the rows written after the dump are only there; Add-ReplacedDatabase records it at
    once) and the new one into its place. A restore into the current database (pg_restore
    --clean) cannot drop what the dump does not know: a newer schema's objects that depend on the
    dump's (issue #122: 0021_db_window's archive_reloads on lookups) made it fail. Before any
    change, the PostgreSQL data drive must have the current database's size, a quarter more and
    max_wal_size free (Get-RestoreDatabaseSpace). A failure before the rename drops the new
    database and leaves the current one as it was. The database services are stopped (the rename
    needs no other session on either database: one that is open, a pgAdmin window, is named).
    The caller sets $script:RestoreStarted first; $script:RestoreChangeStarted (the first
    change: timescaledb_pre_restore() or CREATE DATABASE; a refusal before it changed nothing),
    $script:RestoreCommitted and $script:RestoreCurrentRestoring tell Complete-FailedChange where
    a failure left the database.
    #>
    $db = Get-DatabaseSettings
    $dump = Get-RestoreDump
    $script:RestoreDump = $dump
    if (-not $Force) {
        $answer = Read-Host ("'{0}' veritabanı {1} yedeğiyle değiştirilecek. Devam? (E/H) [replace the database with this dump?]" -f $db.name, $dump)
        if ($answer -notmatch '^(e|E|y|Y)') { $script:RestoreCancelled = $true; throw 'İptal edildi [cancelled].' }
    }
    $credential = Get-PgCredential
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddHHmmss')
    $restore = Get-RestoreDatabaseName -Name ([string]$db.name) -Tag 'restore' -Stamp $stamp
    $replaced = Get-RestoreDatabaseName -Name ([string]$db.name) -Tag 'replaced' -Stamp $stamp
    $names = @(("\set db_name '{0}'" -f $db.name), ("\set new_name '{0}'" -f $restore), ("\set old_name '{0}'" -f $replaced)) -join "`n"
    $provider = (Invoke-Psql -Database 'postgres' -TuplesOnly -Sql ($names + "`nSELECT datlocprovider FROM pg_database WHERE datname = :'db_name';`n")).StdOut.Trim()
    if ($provider -ne 'c') {
        # The locale of a database PostgreSQL's ICU provides is not copied: restore it by hand.
        throw ("Veritabanı '{0}' bulunamadı ya da yerel ayarı libc değil (datlocprovider '{1}'); yedeği docs\YEDEKLEME.md bölüm 5'e göre elle geri yükleyin [database not found, or its locale is not libc: restore the dump by hand, docs\BACKUP.md section 5]" -f $db.name, $provider)
    }
    # The new database is about as large as the current one (no bloat, the same rows up to the
    # dump), and the current one is kept.
    $space = Get-RestoreDatabaseSpace -Names $names -DbHost ([string]$db.host)
    $needMb = [Math]::Ceiling($space.Need / 1MB)
    if (-not $space.Local) {
        Write-ArciWarn ("PostgreSQL başka bir sunucuda ({0}): veri diskinin boş alanı denetlenmedi; geri yükleme orada yaklaşık {1} MB ister [PostgreSQL runs on another server: the free space of its data drive was not checked; the restore needs about {1} MB there]" -f $db.host, $needMb)
    }
    elseif ($null -eq $space.Free) {
        Write-ArciWarn ("PostgreSQL veri diskinin boş alanı okunamadı ({0}); geri yükleme yaklaşık {1} MB ister [the free space of the PostgreSQL data drive could not be read: the restore needs about {1} MB]" -f $(if ($space.Directory) { $space.Directory } else { 'data_directory ?' }), $needMb)
    }
    elseif ($space.Free -lt $space.Need) {
        throw ("PostgreSQL veri diskinde yeterli boş alan yok, veritabanı değiştirilmedi: {0} MB boş, geri yükleme yaklaşık {1} MB ister (şimdiki veritabanının boyutu ve dörtte biri, artı WAL için max_wal_size; şimdiki de saklanır); yer açın ya da yedeği docs\YEDEKLEME.md bölüm 5'e göre elle geri yükleyin [not enough free space on the PostgreSQL data drive, the database was not changed: {0} MB free, the restore needs about {1} MB (the current database's size and a quarter more, plus max_wal_size for the WAL; the current one is kept too); free some space or restore the dump by hand, docs\BACKUP.md section 5]: {2}" -f [Math]::Floor($space.Free / 1MB), $needMb, $space.Directory)
    }
    # TimescaleDB needs its restore mode around pg_restore (verify on site, docs/KURULUM.md): the
    # current database's stops its background workers (they would keep it open for the rename).
    # The new database gets the extension at the current one's version, as arcilogger-db restore
    # pins the dump's (the dump's catalog rows are of that version).
    $extension = ([string](Invoke-Psql -Database $db.name -TuplesOnly -Sql "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';`n").StdOut).Trim()
    $timescale = [bool]$extension
    $version = $(if ($extension -match '^[0-9A-Za-z.\-]+$') { " VERSION '{0}'" -f $extension } else { '' })
    $create = @(
        '\set ON_ERROR_STOP on',
        $names,
        "SELECT format('CREATE DATABASE %I OWNER %I TEMPLATE template0 ENCODING %L LOCALE_PROVIDER libc LC_COLLATE %L LC_CTYPE %L', :'new_name', pg_get_userbyid(datdba), pg_encoding_to_char(encoding), datcollate, datctype) FROM pg_database WHERE datname = :'db_name' \gexec"
    ) -join "`n"
    # The database's own settings (timescaledb.telemetry_level 'off' among them, before the
    # extension exists) and rights, as Invoke-DatabaseProvision gave them. A run of their own,
    # after the new database counts as created: one that fails leaves no empty database behind.
    $copy = @(
        '\set ON_ERROR_STOP on',
        $names,
        "SELECT format('ALTER DATABASE %I SET %s = %L', :'new_name', array_to_string(ARRAY(SELECT quote_ident(part) FROM unnest(string_to_array(split_part(c, '=', 1), '.')) AS part), '.'), substr(c, strpos(c, '=') + 1)) FROM pg_db_role_setting s CROSS JOIN LATERAL unnest(s.setconfig) AS c WHERE s.setrole = 0 AND s.setdatabase = (SELECT oid FROM pg_database WHERE datname = :'db_name') AND split_part(c, '=', 1) <> 'timescaledb.restoring' \gexec",
        "SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'new_name') FROM pg_database WHERE datname = :'db_name' AND datacl IS NOT NULL \gexec",
        "SELECT format('GRANT %s ON DATABASE %I TO %s', a.privilege_type, :'new_name', CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE quote_ident(pg_get_userbyid(a.grantee)) END) FROM pg_database d CROSS JOIN LATERAL aclexplode(d.datacl) AS a WHERE d.datname = :'db_name' \gexec"
    ) -join "`n"
    $created = $false
    $renamed = $false
    # The first change: everything above only read (a cancel, a missing dump, the locale and the
    # space refusal leave the database as it was).
    $script:RestoreChangeStarted = $true
    if ($timescale) {
        $script:RestoreCurrentRestoring = $true
        [void](Invoke-Psql -Database $db.name -Sql "SELECT timescaledb_pre_restore();`n")
    }
    try {
        [void](Invoke-Psql -Database 'postgres' -Sql ($create + "`n"))
        $created = $true
        [void](Invoke-Psql -Database 'postgres' -Sql ($copy + "`n"))
        if ($timescale) { [void](Invoke-Psql -Database $restore -Sql ("CREATE EXTENSION IF NOT EXISTS timescaledb{0};`nSELECT timescaledb_pre_restore();`n" -f $version)) }
        # A failed restore leaves a half-filled new database, dropped below.
        [void](Invoke-PgTool -Tool 'pg_restore.exe' -ArgumentList @('-h', [string]$db.host, '-p', [string]$db.port, '-U', $credential.UserName, '--exit-on-error', '-d', $restore, $dump))
        $swap = @('\set ON_ERROR_STOP on', $names, 'BEGIN;', 'ALTER DATABASE :"db_name" RENAME TO :"old_name";', 'ALTER DATABASE :"new_name" RENAME TO :"db_name";', 'COMMIT;') -join "`n"
        try { [void](Invoke-Psql -Database 'postgres' -Sql ($swap + "`n")) }
        catch {
            $swapError = $_
            # psql can report an error for a COMMIT that reached the server (a lost connection):
            # the rename counts when the current database is already under its new name.
            $committed = ''
            try { $committed = ([string](Invoke-Psql -Database 'postgres' -TuplesOnly -Sql ($names + "`nSELECT count(*) FROM pg_database WHERE datname = :'old_name';`n")).StdOut).Trim() }
            catch { $committed = '' }
            if ($committed -ne '1') {
                $open = Get-OpenDatabaseSession -Names $names
                if (-not $open) { throw $swapError }
                throw ("Veritabanı yeniden adlandırılamadı: üzerinde başka bir oturum açık ({0}), örneğin bir pgAdmin ya da psql penceresi; onu kapatıp geri yüklemeyi yeniden çalıştırın (yeni veritabanı silinir, şimdiki olduğu gibi kalır) [the database could not be renamed: another session is open on it ({0}), for example a pgAdmin or psql window; close it and run the restore again (the new database is dropped, the current one stays as it was)]: {1}" -f $open, $swapError.Exception.Message)
            }
            Write-ArciWarn ("psql bir hata bildirdi ama yeniden adlandırma tamamlanmış [psql reported an error, but the rename committed]: {0}" -f $swapError.Exception.Message)
        }
        $renamed = $true
        $script:RestoreCurrentRestoring = $false
        $script:RestoreCommitted = [pscustomobject]@{ database = [string]$db.name; replaced = $replaced; host = [string]$db.host; port = [string]$db.port; user = $credential.UserName; post_restore_due = $timescale }
        Add-ReplacedDatabase -Name $replaced -Dump $dump -Restoring $timescale
    }
    finally {
        if (-not $renamed) {
            if ($created) {
                try { [void](Invoke-Psql -Database 'postgres' -Sql ($names + "`n" + 'DROP DATABASE IF EXISTS :"new_name" WITH (FORCE);' + "`n")) }
                catch { Write-ArciWarn ("Yarım kalan '{0}' veritabanı silinemedi; elle silin [the half-restored database could not be dropped, drop it by hand]: {1}" -f $restore, $_.Exception.Message) }
            }
            if ($timescale) {
                try {
                    [void](Invoke-Psql -Database $db.name -Sql "SELECT timescaledb_post_restore();`n")
                    $script:RestoreCurrentRestoring = $false
                }
                catch { Write-ArciWarn ("'{0}' için timescaledb_post_restore() çalışmadı; elle çalıştırın [run it by hand]: {1}" -f $db.name, $_.Exception.Message) }
            }
        }
    }
    if ($timescale) {
        [void](Invoke-Psql -Database $db.name -Sql "SELECT timescaledb_post_restore();`n")
        $script:RestoreCommitted.post_restore_due = $false
    }
    Write-InstallState @{ pending_database_restore = $null; last_restore = $dump }
    Write-ArciInfo ("Veritabanı geri yüklendi [database restored]: {0}" -f $dump)
}

function Get-RestoreRetryInstaller {
    <#
    The installer to run a failed restore again with: this version's. After a rollback's swap or
    an upgrade's swap-back <InstallDir>\installer\ holds the previous version's, whose
    -RestoreDatabase writes the dump over the live database (pg_restore --clean, which failed on
    a newer schema: issue #122), so the copy in the folder this version was moved to
    (<InstallDir>.rolled-back-<time> or .failed-<time>), else this script (the bundle's).
    #>
    foreach ($folder in @($script:RolledBackDir, $script:FailedDir)) {
        if (-not $folder) { continue }
        $copy = Join-Path $folder 'installer\Install-Arcilogger.ps1'
        if (Test-Path -LiteralPath $copy -PathType Leaf) { return $copy }
    }
    $script:ThisInstaller
}

# --- licence -------------------------------------------------------------------------------------------

function Invoke-DbLicense {
    <#
    "arcilogger-db license <arguments> --config <arcilogger.toml>". Exit codes of the command:
    0 done, 1 refused (or the licence component could not be loaded), 2 usage or configuration,
    3 database unreachable. Throws only when it cannot be started or does not finish in time.
    #>
    param([string[]]$ArgumentList, [int]$TimeoutSeconds)
    Invoke-ArciProcess -FilePath $DbExe -ArgumentList (@('license') + $ArgumentList + @('--config', $ConfigPath)) -AllowFailure -TimeoutSeconds $TimeoutSeconds
}

function Get-DbLicenseProblem($Result) {
    <# Why an "arcilogger-db license" call did not succeed, in one line. #>
    if ($Result.ExitCode -eq 3) { return 'veritabanına ulaşılamadı [database unreachable]' }
    $lines = @((([string]$Result.StdErr) + "`n" + ([string]$Result.StdOut)) -split '\r?\n' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($lines.Count -eq 0) { return ('çıkış kodu {0} [exit code]' -f $Result.ExitCode) }
    $first = [string]$lines[0]
    if ($first.Length -gt 300) { $first = $first.Substring(0, 300) + '...' }
    'çıkış kodu {0} [exit code]: {1}' -f $Result.ExitCode, $first
}

function Get-LicenseInstallCommand {
    '& "{0}" license install "{1}" --config "{2}"' -f $DbExe, $LicenseFile, $ConfigPath
}

function Install-LicenseFile {
    <#
    -LicenseFile into the database: "arcilogger-db license install <file> --json" (which checks
    the signature, the server id, valid_until and the maintenance as the web upload does). Never
    throws: a refused licence or an unreachable database is a warning, the installation goes on
    (exit code 1) and the product runs in its current licence state; the 5651 functions are
    never affected.
    #>
    $warn = {
        param([string]$Reason, [switch]$Later)
        $message = 'Lisans dosyası yüklenmedi [licence file not installed]: {0}' -f ($Reason -replace '\s+', ' ').Trim()
        if ($Later) { $message += ('. Daha sonra yönetici PowerShell''inde [later, in an administrator PowerShell]: {0}' -f (Get-LicenseInstallCommand)) }
        Write-ArciWarn $message
        $script:Warnings.Add($message)
    }
    try {
        if (-not (Test-Path -LiteralPath $DbExe)) { & $warn ('{0} yok [missing]' -f $DbExe) -Later; return }
        $result = Invoke-DbLicense -ArgumentList @('install', $LicenseFile, '--json') -TimeoutSeconds 120
        $answer = $null
        try { $answer = $result.StdOut | ConvertFrom-Json } catch { $answer = $null }
        if ($result.ExitCode -eq 0) {
            $status = Get-ArciJsonField -Object $answer -Name 'status'
            $state = Get-ArciJsonField -Object $status -Name 'status'
            Write-ArciInfo ('Lisans yüklendi [licence installed]: {0}; durum [state]: {1}' -f $(if ($script:LicenseInfo) { $script:LicenseInfo.LicenseId } else { $LicenseFile }), $(if ($state) { $state } else { '?' }))
            return
        }
        if ($result.ExitCode -eq 1) {
            # The product refused the file (code, message_tr, message_en): the same file will not
            # be accepted later either, so no command to run again.
            $tr = Get-ArciJsonField -Object $answer -Name 'message_tr'
            $en = Get-ArciJsonField -Object $answer -Name 'message_en'
            if ($tr -is [string] -and $en -is [string]) { & $warn ('{0} [{1}]' -f $tr, $en) }
            else { & $warn (Get-DbLicenseProblem $result) }
            return
        }
        & $warn (Get-DbLicenseProblem $result) -Later
    }
    catch { & $warn $_.Exception.Message -Later }
}

# --- services and tasks ------------------------------------------------------------------------------

function Get-ServiceExecutable([string]$Name) {
    $file = Join-Path $ServicesDir ($Name + '.xml')
    if (-not (Test-Path -LiteralPath $file)) { return $null }
    $xml = New-Object System.Xml.XmlDocument
    $xml.Load($file)
    $xml.service.executable
}

function Stop-ArciServices([string[]]$Only = $ServiceNames) {
    $reverse = @($ServiceNames | Where-Object { $Only -contains $_ })
    [array]::Reverse($reverse)
    foreach ($name in $reverse) {
        $service = Get-Service -Name $name -ErrorAction SilentlyContinue
        if ($service -and $service.Status -ne 'Stopped') {
            Stop-Service -Name $name -Force
            $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(90))
            Write-ArciInfo ("durdu [stopped]: {0}" -f $name)
        }
    }
}

function Remove-ArciServices {
    foreach ($name in $ServiceNames) {
        if (Get-Service -Name $name -ErrorAction SilentlyContinue) {
            Invoke-ArciExe -FilePath 'sc.exe' -ArgumentList @('delete', $name)
            $deadline = [DateTime]::UtcNow.AddSeconds(60)
            while ((Get-Service -Name $name -ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 500 }
            if (Get-Service -Name $name -ErrorAction SilentlyContinue) {
                throw ("{0} silinmek üzere işaretli; Hizmetler penceresini kapatıp tekrar deneyin [marked for deletion: close services.msc and retry]." -f $name)
            }
        }
    }
}

function Install-ArciServices([switch]$MissingOnly) {
    <#
    Register the services of the program in $InstallDir. A service whose WinSW wrapper or program
    the folder does not hold is skipped with a warning: a rollback to a version from before
    ArciAdmin has neither (Remove-ArciServices has removed its registration).
    #>
    foreach ($name in $ServiceNames) {
        if ($MissingOnly -and (Get-Service -Name $name -ErrorAction SilentlyContinue)) { continue }
        $wrapper = Join-Path $ServicesDir ($name + '.exe')
        $target = Get-ServiceExecutable $name
        if (-not (Test-Path -LiteralPath $wrapper)) {
            $message = "{0}: hizmet sarmalayıcısı bu sürümde yok, hizmet kurulmadı [no WinSW wrapper in this version, service skipped]: {1}" -f $name, $wrapper
            Write-ArciWarn $message
            $script:Warnings.Add($message)
            continue
        }
        if (-not $target -or -not (Test-Path -LiteralPath $target)) {
            $message = "{0}: program bu sürümde yok, hizmet kurulmadı [not part of this version, service skipped]: {1}" -f $name, $target
            Write-ArciWarn $message
            $script:Warnings.Add($message)
            continue
        }
        Invoke-ArciExe -FilePath $wrapper -ArgumentList @('install')
        # Virtual account NT SERVICE\<name>: no password, its own SID for the NTFS rights.
        Invoke-ArciExe -FilePath 'sc.exe' -ArgumentList @('sidtype', $name, 'unrestricted')
        Invoke-ArciExe -FilePath 'sc.exe' -ArgumentList @('config', $name, 'obj=', ('NT SERVICE\' + $name))
        Write-ArciInfo ("kuruldu [installed]: {0} (NT SERVICE\{0})" -f $name)
    }
}

function Set-ArciServiceStartMode([string[]]$Name, [ValidateSet('demand', 'delayed-auto')][string]$Mode) {
    <# sc.exe "start=" values: demand = manual start; delayed-auto = what the service XMLs request. #>
    foreach ($service in $Name) {
        if (Get-Service -Name $service -ErrorAction SilentlyContinue) {
            Invoke-ArciExe -FilePath 'sc.exe' -ArgumentList @('config', $service, 'start=', $Mode)
        }
    }
}

function Get-ArciServiceState([string[]]$Name) {
    <#
    Each of these services that exists: whether it ran (Running, or StartPending as at boot; one
    that was stopping or paused did not) and its start type (Automatic also for delayed-auto).
    #>
    $state = @{}
    foreach ($service in $Name) {
        $found = Get-Service -Name $service -ErrorAction SilentlyContinue
        if ($found) {
            $state[$service] = [pscustomobject]@{
                Running   = @('Running', 'StartPending') -contains [string]$found.Status
                StartType = [string]$found.StartType
            }
        }
    }
    $state
}

function Restore-ArciServiceState([hashtable]$State) {
    <#
    Put services back as Get-ArciServiceState found them: one that ran starts again (with automatic
    start: delayed-auto again, what the service XMLs request; with manual start it keeps it); one
    that was stopped stays stopped with its start type (on manual start after a failed upgrade,
    Complete-FailedChange). Each is attempted, also the start of one whose start type could not
    be set; $script:ServicesNotPutBack names those left otherwise, and so does the error.
    #>
    $ran = @($ServiceNames | Where-Object { $State.ContainsKey($_) -and $State[$_].Running })
    $script:ServicesNotPutBack = $ran   # until they are
    $failed = New-Object System.Collections.Generic.List[string]
    foreach ($name in @($ran | Where-Object { $State[$_].StartType -eq 'Automatic' })) {
        try { Set-ArciServiceStartMode -Name $name -Mode 'delayed-auto' }
        catch {
            $failed.Add($name)
            Write-ArciWarn ('{0}: otomatik başlatmaya alınamadı [not set to automatic start]: {1}' -f $name, $_.Exception.Message)
        }
    }
    Start-ArciServices -Only $ran
    foreach ($name in $ran) {
        if ([string](Get-Service -Name $name -ErrorAction SilentlyContinue).Status -ne 'Running' -and -not $failed.Contains($name)) { $failed.Add($name) }
    }
    $script:ServicesNotPutBack = @($ran | Where-Object { $failed.Contains($_) })
    if ($failed.Count -gt 0) { throw ('önceki durumlarına getirilemedi [not put back as they were]: {0}' -f ($script:ServicesNotPutBack -join ', ')) }
}

function Start-ArciServices([string[]]$Only = $ServiceNames) {
    foreach ($name in $ServiceNames) {
        if ($Only -notcontains $name) { continue }
        $service = Get-Service -Name $name -ErrorAction SilentlyContinue
        if (-not $service) { continue }
        if ([string]$service.Status -eq 'Running') { continue }   # e.g. the collector, started early
        try {
            Start-Service -Name $name
            (Get-Service -Name $name).WaitForStatus('Running', [TimeSpan]::FromSeconds(60))
            Write-ArciInfo ("çalışıyor [running]: {0}" -f $name)
        }
        catch {
            $message = "{0} başlatılamadı [failed to start]; günlük: {1}\{0}.*.log" -f $name, $script:LogDir
            Write-ArciWarn $message
            $script:Warnings.Add($message)
        }
    }
}

# The read-only desktop status window "ARCILOGGER Durum" (Wave 5 contract section 5, I3-I4): a logon
# task in \ARCILOGGER\Desktop\, registered by Register-ArciTasks.ps1 from the program's desktop\
# folder, and a Start-menu shortcut for every user.

function Get-StatusWindowFolder {
    <# The all-users Start-menu folder of the shortcut; none without a ProgramData folder (not Windows). #>
    if (-not $env:ProgramData) { return $null }
    Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\ARCILOGGER'
}

function Get-ArciStatusWindowTask {
    <#
    The logon task of the status window. The TaskPath test keeps any other task out; it reads the
    property only where it exists (StrictMode 2.0 refuses a missing one).
    #>
    @(Get-ScheduledTask -TaskPath '\ARCILOGGER\Desktop\' -ErrorAction SilentlyContinue |
            Where-Object { $_ -and $_.PSObject.Properties['TaskPath'] -and $_.TaskPath -eq '\ARCILOGGER\Desktop\' })
}

function Stop-ArciStatusWindow {
    <#
    Close the status windows that run Python from the program folder: they stay open until someone
    closes them, so they are ended, never waited for. The window changes nothing, so nothing is
    lost. Its logon task is switched off first, so an administrator who logs on meanwhile does
    not start it again from the folder being replaced; registering the program again (or
    Enable-ArciTasks after a failure) switches it on again.
    #>
    foreach ($task in (Get-ArciStatusWindowTask)) {
        [void]($task | Disable-ScheduledTask)
        if ([string]$task.State -eq 'Running') { [void]($task | Stop-ScheduledTask) }
    }
    if (-not (Get-Command Get-CimInstance -ErrorAction SilentlyContinue)) { return }
    $program = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\') + '\'
    Get-CimInstance -ClassName Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -like '*arcilogger_core.desktop*' -and $_.ExecutablePath -and
            $_.ExecutablePath.StartsWith($program, [System.StringComparison]::OrdinalIgnoreCase)
        } |
        ForEach-Object {
            Write-ArciInfo ("durum penceresi kapatılıyor [closing the status window] (PID {0})" -f $_.ProcessId)
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

function Set-ArciStatusWindowShortcut([string]$Root) {
    <# The Start-menu shortcut when the program in $Root has the status window; otherwise none. #>
    $folder = Get-StatusWindowFolder
    if (-not $folder) { return }
    $shortcut = Join-Path $folder 'ARCILOGGER Durum.lnk'
    $pythonw = Join-Path $Root 'venv\Scripts\pythonw.exe'
    if (-not (Test-Path -LiteralPath (Join-Path $Root 'desktop')) -or -not (Test-Path -LiteralPath $pythonw)) {
        Remove-ArciStatusWindowShortcut
        return
    }
    # A convenience only: without it the window still opens at logon (Windows Script Host may be
    # switched off by policy), so a failure is a warning, never a failed installation.
    try {
        [void](New-Item -ItemType Directory -Path $folder -Force)
        $link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
        $link.TargetPath = $pythonw
        $link.Arguments = '-I -m arcilogger_core.desktop'
        $link.WorkingDirectory = $Root
        $link.Description = 'ARCILOGGER Durum: salt okunur durum penceresi [read-only status window]'
        $link.Save()
    }
    catch {
        Write-ArciWarn ("Başlat menüsü kısayolu oluşturulamadı [Start-menu shortcut not created]: {0}" -f $_.Exception.Message)
    }
}

function Remove-ArciStatusWindowShortcut {
    $folder = Get-StatusWindowFolder
    if (-not $folder) { return }
    $shortcut = Join-Path $folder 'ARCILOGGER Durum.lnk'
    if (Test-Path -LiteralPath $shortcut) { Remove-Item -LiteralPath $shortcut -Force }
    if ((Test-Path -LiteralPath $folder) -and -not (Get-ChildItem -LiteralPath $folder -Force)) {
        Remove-Item -LiteralPath $folder -Force
    }
}

function Disable-ArciTasks {
    <#
    Pause the evidence jobs and wait until none is running (they run python.exe from the program
    folder, which is about to be moved). The names of the tasks that were enabled are kept, so a
    recovery enables exactly those again. The status window is closed first: it runs Python from
    the same folder and never ends by itself (its task lives in \ARCILOGGER\Desktop\, so it is
    not waited for here).
    #>
    Stop-ArciStatusWindow
    $tasks = @(Get-ScheduledTask -TaskPath '\ARCILOGGER\' -ErrorAction SilentlyContinue | Where-Object { $_ })
    Save-TaskChoices
    if ($null -eq $script:PausedTasks) {
        $script:PausedTasks = @($tasks | Where-Object { [string]$_.State -ne 'Disabled' } | ForEach-Object { $_.TaskName })
    }
    foreach ($task in $tasks) { if ([string]$task.State -ne 'Disabled') { [void]($task | Disable-ScheduledTask) } }
    $deadline = [DateTime]::UtcNow.AddMinutes($TaskWaitMinutes)
    while ($true) {
        $running = @(Get-ScheduledTask -TaskPath '\ARCILOGGER\' -ErrorAction SilentlyContinue | Where-Object { $_ -and [string]$_.State -eq 'Running' })
        if ($running.Count -eq 0) { break }
        if ([DateTime]::UtcNow -gt $deadline) {
            throw ("Görev hâlâ çalışıyor [task still running]: {0}. Daha sonra tekrar deneyin." -f (($running | ForEach-Object { $_.TaskName }) -join ', '))
        }
        Write-ArciInfo ("bekleniyor [waiting for]: {0}" -f (($running | ForEach-Object { $_.TaskName }) -join ', '))
        Start-Sleep -Seconds 10
    }
}

function Enable-ArciTasks {
    <# Enable again the tasks Disable-ArciTasks paused (those that still exist). #>
    foreach ($task in (Get-ArciStatusWindowTask)) { [void]($task | Enable-ScheduledTask) }
    if ($null -eq $script:PausedTasks) { return }
    foreach ($name in @($script:PausedTasks)) {
        $task = Get-ScheduledTask -TaskPath '\ARCILOGGER\' -TaskName $name -ErrorAction SilentlyContinue
        if ($task) { [void]($task | Enable-ScheduledTask) }
    }
}

function Save-TaskChoices {
    <# Remember once, before anything is paused or registered, which tasks the operator switched off. #>
    if ($null -ne $script:OperatorDisabledTasks) { return }
    $script:OperatorDisabledTasks = @(Get-ScheduledTask -TaskPath '\ARCILOGGER\' -ErrorAction SilentlyContinue |
            Where-Object { $_ -and [string]$_.State -eq 'Disabled' } | ForEach-Object { $_.TaskName })
}

function Register-ArciTaskSet([string]$Root, [switch]$Replace) {
    <# With -Replace, tasks that the program in $Root does not define are removed first. #>
    if ($Replace) {
        Get-ScheduledTask -TaskPath '\ARCILOGGER\' -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
        # A program without the status window (a rollback to an older version) keeps no task for it.
        Get-ScheduledTask -TaskPath '\ARCILOGGER\Desktop\' -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
    }
    & (Join-Path $Root 'scripts\Register-ArciTasks.ps1') -Python (Join-Path $Root 'venv\Scripts\python.exe') -TaskDirectory (Join-Path $Root 'tasks')
    Set-ArciStatusWindowShortcut -Root $Root
}

function Register-ArciTasksKeepingChoices([string]$Root) {
    <# Register the task definitions of $Root; tasks the operator had switched off stay off. #>
    Save-TaskChoices
    Register-ArciTaskSet -Root $Root -Replace
    foreach ($name in @($script:OperatorDisabledTasks)) {
        $task = Get-ScheduledTask -TaskPath '\ARCILOGGER\' -TaskName $name -ErrorAction SilentlyContinue
        if ($task) {
            [void]($task | Disable-ScheduledTask)
            Write-ArciInfo ("kapalı kalıyor [stays disabled]: \ARCILOGGER\{0}" -f $name)
        }
    }
}

# --- program files -----------------------------------------------------------------------------------

function Get-TemplateValues {
    $sites = New-Object System.Collections.Generic.List[string]
    foreach ($site in @($ServerAddress, $ServerName, 'localhost')) {
        if ($site -and -not $sites.Contains('https://' + $site)) { $sites.Add('https://' + $site) }
    }
    @{
        INSTALL_DIR     = $InstallDir
        CONFIG          = $ConfigPath
        LOG_DIR         = $script:LogDir
        LOG_DIR_FORWARD = $script:LogDir.Replace('\', '/')
        PROGRAM_DATA    = $ProgramDataDir
        SITE_ADDRESSES  = ($sites.ToArray() -join ', ')
        WEB_ROOT        = (Join-Path $InstallDir 'web').Replace('\', '/')
        # Caddy forwards /api to where ArciApi listens ([api] host/port of arcilogger.toml).
        API_UPSTREAM    = (Get-ApiEndpoint).Address
    }
}

function Install-ProgramFiles {
    $python = Get-BundleComponent 'python'
    $winsw = Get-BundleComponent 'winsw'
    $caddy = Get-BundleComponent 'caddy'
    $requirements = Get-BundleFile 'wheels/requirements.txt'
    $depsDir = Get-BundleFile 'wheels/deps'
    $appDir = Get-BundleFile 'wheels/arcilogger'

    Invoke-Step 'program.create' ("Program klasörü [program folder]: {0}" -f $InstallDir) {
        [void](New-Item -ItemType Directory -Path $InstallDir -Force)
        # Marks the folder as ARCILOGGER's until VERSION.json exists: an interrupted installation
        # can then be resumed (and removed) safely by running the installer again.
        $marker = [pscustomobject]@{
            product     = 'ARCILOGGER'
            version     = $script:Manifest.version
            fingerprint = $script:BundleInfo.Fingerprint
            started_at  = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
        }
        Write-ArciUtf8File -Path (Join-Path $InstallDir (Get-ArciInstallMarkerName)) -Text ($marker | ConvertTo-Json)
    }
    Invoke-Step 'python.extract' ("Python {0} çıkarılıyor [extracting]" -f $python.version) {
        Invoke-ArciExe -FilePath $TarExe -ArgumentList @('-xzf', (Get-BundleFile $python.path), '-C', $InstallDir)
        if (-not (Test-Path -LiteralPath $BasePython)) { throw ("python.exe yok [missing]: {0}" -f $BasePython) }
    } $python.path
    Invoke-Step 'venv.create' 'Sanal ortam [virtual environment] venv' {
        Invoke-ArciExe -FilePath $BasePython -ArgumentList @('-I', '-m', 'venv', $Venv)
    } $Venv
    # --isolated: no pip.ini, no PIP_* variables of this machine; --no-index: never the network.
    $pip = @('-I', '-m', 'pip', '--isolated', '--no-input', '--disable-pip-version-check')
    Invoke-Step 'wheels.deps' 'Bağımlılıklar çevrimdışı, özet denetimli [dependencies offline, hash-checked]' {
        Invoke-ArciExe -FilePath $VenvPython -ArgumentList ($pip + @('install', '--no-index', '--no-cache-dir', '--require-hashes', '--no-deps', '--find-links', $depsDir, '-r', $requirements))
    } 'pip install --no-index --require-hashes -r wheels/requirements.txt'
    Invoke-Step 'wheels.app' 'ARCILOGGER paketleri [ARCILOGGER wheels]' {
        $wheels = @(Get-ChildItem -LiteralPath $appDir -Filter '*.whl' | ForEach-Object { $_.FullName })
        if ($wheels.Count -eq 0) { throw 'ARCILOGGER wheel dosyası yok [no ARCILOGGER wheels].' }
        Invoke-ArciExe -FilePath $VenvPython -ArgumentList ($pip + @('install', '--no-index', '--no-cache-dir', '--no-deps') + $wheels)
        Invoke-ArciExe -FilePath $VenvPython -ArgumentList ($pip + @('check'))
    } 'pip install --no-index --no-deps wheels/arcilogger/*.whl; pip check'
    Invoke-Step 'web.copy' 'Web arayüzü [web interface]' {
        $web = Join-Path $InstallDir 'web'
        [void](New-Item -ItemType Directory -Path $web -Force)
        Copy-Item -Path (Join-Path (Get-BundleFile 'web') '*') -Destination $web -Recurse -Force
        if (-not (Test-Path -LiteralPath (Join-Path $web 'index.html'))) { throw 'web\index.html yok [missing].' }
    }
    Invoke-Step 'caddy.install' ("Caddy {0}" -f $caddy.version) {
        $caddyDir = Join-Path $InstallDir 'caddy'
        Expand-Archive -LiteralPath (Get-BundleFile $caddy.path) -DestinationPath $caddyDir -Force
        Copy-Item -LiteralPath (Get-BundleFile 'caddy/Caddyfile') -Destination (Join-Path $caddyDir 'Caddyfile') -Force
        # "adapt" only parses; "validate" would already create a CA and log files as the admin user.
        [void](Invoke-ArciProcess -FilePath (Join-Path $caddyDir 'caddy.exe') -ArgumentList @('adapt', '--config', (Join-Path $caddyDir 'Caddyfile'), '--adapter', 'caddyfile'))
    } $caddy.path
    $values = Get-TemplateValues
    Invoke-Step 'winsw.install' ("Hizmet tanımları [service definitions] (WinSW {0})" -f $winsw.version) {
        [void](New-Item -ItemType Directory -Path $ServicesDir -Force)
        foreach ($name in $ServiceNames) {
            Copy-Item -LiteralPath (Get-BundleFile $winsw.path) -Destination (Join-Path $ServicesDir ($name + '.exe')) -Force
            $template = [System.IO.File]::ReadAllText((Get-BundleFile ('services/' + $name + '.xml')), [System.Text.Encoding]::UTF8)
            Write-ArciUtf8File -Path (Join-Path $ServicesDir ($name + '.xml')) -Text (Expand-ArciTemplate -Text $template -Values $values -Xml)
        }
    } ([pscustomobject]$values)
    Invoke-Step 'support.copy' 'Betikler, görevler, belgeler, lisanslar [scripts, tasks, docs, licences]' {
        foreach ($folder in @('installer', 'scripts', 'tasks', 'desktop', 'config', 'docs', 'licenses')) {
            $source = Get-BundleFile $folder
            if (Test-Path -LiteralPath $source) {
                Copy-Item -LiteralPath $source -Destination $InstallDir -Recurse -Force
            }
        }
        Copy-Item -LiteralPath (Get-BundleFile 'components.json') -Destination (Join-Path $InstallDir 'installer\components.json') -Force
        # The licence texts travel with the programs they cover (WinSW is installed once per service).
        $notices = Get-BundleFile 'THIRD-PARTY-NOTICES.txt'
        if (Test-Path -LiteralPath $notices) { Copy-Item -LiteralPath $notices -Destination (Join-Path $InstallDir 'THIRD-PARTY-NOTICES.txt') -Force }
    }
    Invoke-Step 'version.write' ("Sürüm kaydı [version record] {0}" -f $script:Manifest.version) {
        $record = [pscustomobject]@{
            product      = 'ARCILOGGER'
            version      = $script:Manifest.version
            fingerprint  = $script:BundleInfo.Fingerprint
            git_commit   = $script:Manifest.git_commit
            built_at     = $script:Manifest.built_at
            # The day the licence's maintenance must cover (bundle-manifest.json release_date).
            release_date = $script:ReleaseDate
            installed_at = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
        }
        Write-ArciUtf8File -Path (Join-Path $InstallDir 'VERSION.json') -Text ($record | ConvertTo-Json)
        Remove-Item -LiteralPath (Join-Path $InstallDir (Get-ArciInstallMarkerName)) -Force -ErrorAction SilentlyContinue
    }
}

# --- data, configuration, secrets ----------------------------------------------------------------------

function Get-DataDirectories {
    @(
        $DataRoot,
        (Join-Path $DataRoot 'spool'), (Join-Path $DataRoot 'spool\_audit'), (Join-Path $DataRoot 'vault'),
        (Join-Path $DataRoot 'exports'), (Join-Path $DataRoot 'state'), (Join-Path $DataRoot 'logs'),
        (Join-Path $DataRoot 'state\seal'), (Join-Path $DataRoot 'state\replica'),
        (Join-Path $DataRoot 'state\verify'), (Join-Path $DataRoot 'state\clock'),
        # Signed task requests of the browser administration (ADR-0014) and the tasks' answers.
        (Join-Path $DataRoot 'state\requests'), (Join-Path $DataRoot 'state\requests\seal'),
        (Join-Path $DataRoot 'state\requests\tasks'), (Join-Path $DataRoot 'state\requests\results'),
        (Join-Path $DataRoot 'state\requests\seen'),
        (Join-Path $DataRoot 'backups'), (Join-Path $DataRoot 'backups\db'),
        $ProgramDataDir, (Join-Path $ProgramDataDir 'kamusm-roots'), (Join-Path $ProgramDataDir 'smtp-ca'),
        $ZamaneDir, (Join-Path $ZamaneDir 'web'), (Join-Path $ZamaneDir 'approved'),
        (Join-Path $ProgramDataDir 'caddy'), (Join-Path $ProgramDataDir 'caddy\data'), (Join-Path $ProgramDataDir 'caddy\config'),
        $StateDir,
        $AdminDir, $AdminKeysDir, (Join-Path $AdminDir 'inbox'), (Join-Path $AdminDir 'inbox\uploads'),
        (Join-Path $AdminDir 'work'), (Join-Path $AdminDir 'pending'), (Join-Path $AdminDir 'state'),
        (Join-Path $AdminDir 'versions'), (Join-Path $AdminDir 'status'), (Join-Path $AdminDir 'journal'),
        (Join-Path $AdminDir 'system')
    )
}

function Get-ConfiguredCollectorBind {
    <#
    [collector] bind of the existing arcilogger.toml ('0.0.0.0', the collector's default, when the
    key is not there), or $null without a configuration. Read with tomllib when this
    installation's Python exists, otherwise with the narrow pattern of Get-ArciCollectorBind.
    #>
    if (-not (Test-Path -LiteralPath $ConfigPath)) { return $null }
    if (Test-Path -LiteralPath $VenvPython) {
        $config = Read-ArciTomlFile -Python $VenvPython -ConfigPath $ConfigPath
        return [string](Get-ArciConfigValue -Config $config -Path 'collector.bind' -Default '0.0.0.0')
    }
    $bind = Get-ArciCollectorBind -Text ([System.IO.File]::ReadAllText($ConfigPath, [System.Text.Encoding]::UTF8))
    if ($null -eq $bind) { return '0.0.0.0' }
    $bind
}

function Set-ExampleCollectorBind([string]$Text, [string]$Address) {
    <# The single "bind = ..." line in [collector] of the example configuration, set to -ServerAddress (other sections untouched). #>
    $section = [regex]::Match($Text, '(?ms)^[ \t]*\[collector\][ \t]*(?:#[^\r\n]*)?\r?$(?<body>.*?)(?=^[ \t]*\[|\z)')
    $pattern = '(?m)^bind\s*=\s*"[^"]*"'
    $count = 0
    if ($section.Success) { $count = [regex]::Matches($section.Groups['body'].Value, $pattern).Count }
    if ($count -ne 1) {
        throw ("Örnek yapılandırmada [collector] içinde tek bir bind satırı bekleniyordu [expected one bind line in [collector]]: {0}" -f $count)
    }
    $body = $section.Groups['body']
    $Text.Substring(0, $body.Index) + [regex]::Replace($body.Value, $pattern, ('bind = "{0}"' -f $Address)) + $Text.Substring($body.Index + $body.Length)
}

function Set-ConfigSetupValues {
    <#
    -Text (arcilogger.toml) with the organisation name (site_name) and the local time source
    ([clock] ntp_server) written into it, each only when given ($null leaves the key alone).
    #>
    param([string]$Text, $SiteName = $null, $NtpServer = $null)
    if ($null -ne $SiteName) { $Text = Set-ArciTomlString -Text $Text -Key 'site_name' -Value ([string]$SiteName) }
    if ($null -ne $NtpServer) { $Text = Set-ArciTomlString -Text $Text -Section 'clock' -Key 'ntp_server' -Value ([string]$NtpServer) }
    $Text
}

function Get-ConfigUpdate([string]$Text) {
    <#
    What this run changes in an existing arcilogger.toml (-Text): site_name when -SiteName or the
    name typed at the console differs from it, [clock] ntp_server when -NtpServer differs from it.
    Returns Text (the new content) and Keys, or $null when nothing changes. Throws when the file
    has these keys in a form Set-ArciTomlString does not rewrite (the preflight reports it).
    #>
    $keys = New-Object System.Collections.Generic.List[string]
    $site = $null
    $ntp = $null
    if (@('parameter', 'prompt') -contains $script:SiteNameSource -and $script:SiteNameValue -cne [string](Get-ArciTomlString -Text $Text -Key 'site_name')) {
        $site = $script:SiteNameValue
        $keys.Add('site_name')
    }
    if ($script:NtpServerSource -eq 'parameter' -and $script:NtpServerValue -cne [string](Get-ArciTomlString -Text $Text -Section 'clock' -Key 'ntp_server')) {
        $ntp = $script:NtpServerValue
        $keys.Add('clock.ntp_server')
    }
    if ($keys.Count -eq 0) { return $null }
    [pscustomobject]@{ Text = (Set-ConfigSetupValues -Text $Text -SiteName $site -NtpServer $ntp); Keys = $keys.ToArray() }
}

function Invoke-ConfigSteps {
    if (Test-Path -LiteralPath $ConfigPath) {
        Add-PlanItem -Id 'config.keep' -Status 'kept' -Text ("Mevcut yapılandırma korunuyor [existing configuration kept]: {0}" -f $ConfigPath)
        Write-ArciInfo ("Mevcut yapılandırma korunuyor [existing configuration kept]: {0}" -f $ConfigPath)
        # A given -SiteName / -NtpServer (or a name typed for an empty site_name) is written into
        # it; the preflight has checked that this works, a dry run only plans it.
        $update = $null
        try { $update = Get-ConfigUpdate ([System.IO.File]::ReadAllText($ConfigPath, [System.Text.Encoding]::UTF8)) }
        catch { if (-not $script:IsDryRun) { throw } }
        if ($null -ne $update) {
            $backup = '{0}.before-{1}' -f $ConfigPath, [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
            Invoke-Step 'config.update' ("Yapılandırma güncelleniyor [updating configuration]: {0} (önceki dosya [previous file]: {1})" -f ($update.Keys -join ', '), $backup) {
                Copy-Item -LiteralPath $ConfigPath -Destination $backup -Force
                Write-ArciUtf8File -Path $ConfigPath -Text $update.Text
            } ([pscustomobject]@{ keys = $update.Keys; backup = $backup })
        }
    }
    else {
        $dataDirValue = $DataRoot.Replace('\', '/')
        # The collector listens on the server address (the preflight checked that it is one of
        # this computer's): the example's address belongs to its fictional example network.
        $bindValue = [string]$ServerAddress
        $siteValue = $script:SiteNameValue
        $ntpValue = $script:NtpServerValue
        Invoke-Step 'config.copy' ("Örnek yapılandırma kopyalanıyor [copying example configuration]: {0}" -f $ConfigPath) {
            $text = [System.IO.File]::ReadAllText((Get-BundleFile 'config/arcilogger.example.toml'), [System.Text.Encoding]::UTF8)
            $pattern = '(?m)^data_dir\s*=\s*"[^"]*"'
            if (-not [regex]::IsMatch($text, $pattern)) { throw 'Örnek yapılandırmada data_dir satırı yok [no data_dir line].' }
            $text = [regex]::Replace($text, $pattern, ('data_dir = "{0}"' -f $dataDirValue))
            $text = Set-ExampleCollectorBind -Text $text -Address $bindValue
            $text = Set-ConfigSetupValues -Text $text -SiteName $siteValue -NtpServer $ntpValue
            Write-ArciUtf8File -Path $ConfigPath -Text $text
        } ([pscustomobject]@{ data_dir = $dataDirValue; collector_bind = $bindValue; site_name = $siteValue; ntp_server = $ntpValue })
    }
    # The data root every task and service checks the configuration's data_dir against
    # (data-root.txt, Day 0 note D0-2): written on every run from the record itself, -DataRoot
    # or the choice of a first installation (Resolve-ArciDataRoot), before ArciAdmin gets its
    # right to create files in this folder (Invoke-AclSteps). Deleted and created anew, never
    # rewritten in place: a record someone else created would keep its owner, and an owner can
    # change the rights. Nothing the browser changes can move it.
    Invoke-Step 'config.data-root' ("Veri klasörü kaydı [data root record]: {0}" -f $DataRootRecord) {
        if (Test-Path -LiteralPath $DataRootRecord) { Remove-Item -LiteralPath $DataRootRecord -Force }
        Write-ArciUtf8File -Path $DataRootRecord -Text ($DataRoot + "`n")
    } $DataRoot
    Invoke-Step 'config.database-section' '[database] bölümü (bu sürüm destekliyorsa ve yoksa) [added if supported and missing]' {
        $supported = (Invoke-ArciProcess -FilePath $VenvPython -ArgumentList @('-I', '-c', 'from arcilogger_core.config import Config; print(''database'' in Config.model_fields)')).StdOut.Trim()
        $text = [System.IO.File]::ReadAllText($ConfigPath, [System.Text.Encoding]::UTF8)
        if ($supported -eq 'True' -and $text -notmatch '(?m)^\s*\[database\]') {
            $section = "`n[database]`n# Password: encrypted secret store, name database.password (set by the installer).`nhost = `"127.0.0.1`"`nport = 5432`nname = `"arcilogger`"`nuser = `"arcilogger`"`n"
            Write-ArciUtf8File -Path $ConfigPath -Text ($text.TrimEnd() + "`n" + $section)
            Write-ArciInfo '[database] eklendi [added].'
        }
    }
    # Before the validation: a Zamane client from outside zamane\ is copied in and tsa.zamane_jar
    # names the copy (Update-ExecutableRecords).
    $recordDetail = $null
    if ($null -ne $script:ZamaneJarPlan -or $null -ne $script:ExecutableRecords) {
        $recordDetail = [pscustomobject]@{ zamane_jar = $script:ZamaneJarPlan; trusted_executables = $script:ExecutableRecords }
    }
    Invoke-Step 'executables.record' 'Görevlerin çalıştırdığı programlar kaydediliyor [recording the programs the tasks run] (tsa.java, tsa.zamane_jar, backup.pg_dump, backup.pg_restore)' {
        Update-ExecutableRecords
    } $recordDetail
    Invoke-Step 'config.validate' 'Yapılandırma doğrulanıyor [validating configuration]' {
        $code = 'import sys; from arcilogger_core.config import load_config; c = load_config(__import__(''pathlib'').Path(sys.argv[1])); print(c.data_dir, c.collector.bind, c.tsa.provider)'
        $result = Invoke-ArciProcess -FilePath $VenvPython -ArgumentList @('-I', '-c', $code, $ConfigPath)
        Write-ArciInfo ("data_dir, bind, tsa: {0}" -f $result.StdOut.Trim())
    }
    Invoke-Step 'secrets.init' 'Gizli depo anahtarı [secret store key] (secret init)' {
        $result = Invoke-ArciProcess -FilePath $CoreExe -ArgumentList @('--config', $ConfigPath, 'secret', 'init')
        Write-ArciInfo $result.StdOut.Trim()
    }
    # ArciAdmin writes secrets.json through an explicit entry on the file (Invoke-AclSteps): the
    # file must exist, with no stored value yet, before that step.
    Invoke-Step 'secrets.file' ("Gizli değerler dosyası [secret values file]: {0}" -f $SecretsFile) {
        if (-not (Test-Path -LiteralPath $SecretsFile)) { Write-ArciUtf8File -Path $SecretsFile -Text "{}`n" }
    }
    if ($ZamaneJar) {
        Invoke-Step 'zamane.jar' ("Zamane istemcisi [client]: {0}" -f $ZamaneJar) { Install-ZamaneJar }
    }
    Invoke-AdminSteps
}

function Install-ZamaneJar {
    <#
    -ZamaneJar: the Zamane client into zamane\ after its SHA-256 was compared with the value on
    the Kamu SM page, and its line appended to zamane\zamane-jar.sha256 (the lines already there
    stay: the tasks accept every recorded client, arcilogger_core.execpolicy). A different file of
    the same name in zamane\ is never overwritten.
    #>
    if (-not (Test-Path -LiteralPath $ZamaneJar -PathType Leaf)) { throw ("Dosya yok [file missing]: {0}" -f $ZamaneJar) }
    $actual = Get-ArciFileSha256 -Path $ZamaneJar
    Write-Host ("    Zamane jar SHA-256: {0}" -f $actual)
    if ($ZamaneJarSha256) {
        if ($actual -ne $ZamaneJarSha256.Trim().ToLowerInvariant()) {
            throw 'SHA-256 Kamu SM sayfasındaki değerle aynı değil; dosya kullanılmadı [checksum mismatch, jar refused].'
        }
    }
    else {
        $answer = Read-Host 'Bu değer Kamu SM indirme sayfasındaki SHA-256 ile aynı mı? (E/H) [same as on the Kamu SM page?]'
        if ($answer -notmatch '^(e|E|y|Y)') { throw 'Zamane jar onaylanmadı [jar not confirmed].' }
    }
    $target = Join-Path $ZamaneDir ([System.IO.Path]::GetFileName($ZamaneJar))
    if ((Test-Path -LiteralPath $target) -and (Get-ArciFileSha256 -Path $target) -ne $actual) {
        throw ("{0} başka bir dosya; üzerine yazılmaz, farklı bir dosya adı kullanın [a different file, not overwritten: use another file name]" -f $target)
    }
    if (-not (Test-Path -LiteralPath $target)) { Copy-Item -LiteralPath $ZamaneJar -Destination $target }
    Add-ZamaneJarRecord -Sha256 $actual -Name ([System.IO.Path]::GetFileName($ZamaneJar)) -Note ('confirmed {0} by {1}' -f [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'), [Environment]::UserName)
}

function Add-ZamaneJarRecord([string]$Sha256, [string]$Name, [string]$Note) {
    <# Append "<sha256>  <file name>" and a comment line to zamane\zamane-jar.sha256 unless the hash is recorded already. #>
    $record = Join-Path $ZamaneDir 'zamane-jar.sha256'
    $recorded = Read-ArciInstallerHashes -Path $record
    if (@($recorded) -contains $Sha256) { return }
    $existing = ''
    if (Test-Path -LiteralPath $record) { $existing = [System.IO.File]::ReadAllText($record) }
    if ($existing -and -not $existing.EndsWith("`n")) { $existing += "`n" }
    Write-ArciUtf8File -Path $record -Text ($existing + ("{0}  {1}`n# {2}`n" -f $Sha256, $Name, $Note))
}

# --- browser administration ----------------------------------------------------------------------

function Read-RememberedAdminPolicy {
    <# The choices of the browser administration install\state.json remembers (admin_policy), or $null. #>
    $state = $null
    try { $state = Read-InstallState } catch { $state = $null }
    if ($null -eq $state -or -not ($state.PSObject.Properties.Name -contains 'admin_policy') -or $null -eq $state.admin_policy) { return $null }
    $state.admin_policy
}

function Resolve-AdminPolicyChoice {
    <#
    The guard rails of admin\policy.json for this run (contract 2.4.1). For the four choices a
    given parameter wins (-DisableBrowserAdministration, -SourceNetwork, -AdminChangeHoldHours,
    -ZamaneHoldHours; remembered at the end of the run); then a valid existing policy.json (an
    administrator may have edited it by hand); then what install\state.json remembers; then the
    defaults. The keys without a parameter (min_prefix_v4, max_sources, executable_roots,
    tsa_test_per_day) and the recorded programs come from a valid existing policy.json or are the
    defaults. Invoke-AdminPolicyPreflight checks the result.
    #>
    $existing = Read-ArciAdminPolicy -Path $PolicyPath
    $remembered = Read-RememberedAdminPolicy
    $pick = {
        param([bool]$Given, $Value, [string]$Policy, [string]$State, $Default)
        if ($Given) { return , $Value }
        if ($existing.Valid) { return , $existing.$Policy }
        if ($null -ne $remembered -and $remembered.PSObject.Properties.Name -contains $State -and $null -ne $remembered.$State) { return , $remembered.$State }
        , $Default
    }
    $kept = { param([string]$Name, $Default) if ($existing.Valid) { , $existing.$Name } else { , $Default } }
    # "-SourceNetwork 10.0.0.0/8,172.16.0.0/12" reaches a script run with -File as one text.
    $given = @($SourceNetwork | ForEach-Object { ([string]$_) -split '[,;\s]+' } | Where-Object { $_ })
    # Assigned first: a list returned as one object (", $list") would be wrapped by @(...).
    $networks = & $pick $script:SourceNetworkGiven $given 'SourceNetworks' 'source_networks' (Get-ArciDefaultSourceNetworks)
    $roots = & $kept 'ExecutableRoots' @('C:\Program Files', 'C:\Program Files (x86)')
    $trusted = & $kept 'TrustedExecutables' @()
    $script:AdminPolicyChoice = [pscustomobject]@{
        Existing           = $existing
        Enabled            = [bool](& $pick $script:BrowserAdministrationGiven (-not $DisableBrowserAdministration) 'Enabled' 'enabled' $true)
        SourceNetworks     = @($networks | ForEach-Object { [string]$_ })
        HoldHours          = (& $pick $script:HoldHoursGiven $AdminChangeHoldHours 'HoldHours' 'hold_hours' 0)
        JarHoldHours       = (& $pick $script:JarHoldHoursGiven $ZamaneHoldHours 'JarHoldHours' 'jar_hold_hours' 24)
        MinPrefixV4        = (& $kept 'MinPrefixV4' 16)
        MaxSources         = (& $kept 'MaxSources' 500)
        ExecutableRoots    = @($roots | ForEach-Object { [string]$_ })
        TsaTestPerDay      = (& $kept 'TsaTestPerDay' 3)
        TrustedExecutables = @($trusted | Where-Object { $null -ne $_ })
    }
}

function Invoke-AdminPolicyPreflight {
    <# The guard rails (Resolve-AdminPolicyChoice): a value policy.json cannot hold stops the run before any change. #>
    $choice = $script:AdminPolicyChoice
    if ($null -eq $choice) { return }
    $problems = New-Object System.Collections.Generic.List[string]
    foreach ($value in @($choice.SourceNetworks)) {
        $checked = ConvertTo-ArciSourceNetwork -Value ([string]$value)
        if ($checked.Problem) { $problems.Add(("-SourceNetwork '{0}': {1}" -f $value, $checked.Problem)) }
    }
    if (@($choice.SourceNetworks).Count -eq 0) { $problems.Add('-SourceNetwork boş [empty]: en az bir ağ gerekli [at least one network]') }
    $hold = 0
    if (-not [int]::TryParse([string]$choice.HoldHours, [ref]$hold) -or $hold -lt 0 -or $hold -gt 168) { $problems.Add(("-AdminChangeHoldHours {0}: 0-168 saat [hours]" -f $choice.HoldHours)) }
    if (-not [int]::TryParse([string]$choice.JarHoldHours, [ref]$hold) -or $hold -lt 1 -or $hold -gt 168) { $problems.Add(("-ZamaneHoldHours {0}: 1-168 saat [hours]" -f $choice.JarHoldHours)) }
    if ($problems.Count -gt 0) {
        Add-Check 'admin-policy' 'fail' ("Tarayıcı yönetimi ayarları geçersiz [invalid browser administration settings]: {0}" -f ($problems -join '; '))
        return
    }
    # Every enabled log source must lie in these networks: ApplySystem gives the firewall's 514
    # rule only the senders the policy allows (contract 2.8.3), so a source outside them would
    # lose its opening at its first run, without any change in the browser. Checked before any
    # change; where ApplySystem cannot narrow the rule (browser administration off, a firewall
    # ARCILOGGER does not manage) it is a warning.
    $outside = New-Object System.Collections.Generic.List[string]
    $networks = New-Object System.Collections.Generic.List[string]
    foreach ($network in @($choice.SourceNetworks)) { $networks.Add([string]$network) }
    foreach ($value in @($script:SourceSenders)) {
        # As system-plan reads it (host bits of a network cleared, a /32 as the address).
        $sender = ConvertTo-ArciSyslogSender -Value ([string]$value)
        $why = Get-ArciSyslogSenderProblem -Value $(if ($sender) { $sender } else { [string]$value }) -SourceNetwork @($choice.SourceNetworks) -MinPrefixV4 $choice.MinPrefixV4
        if (-not $why) { continue }
        $outside.Add(('{0}: {1}' -f $value, $why))
        $parsed = ConvertTo-ArciNetwork -Value $sender
        if ($null -ne $parsed -and $parsed.Prefix -ge $choice.MinPrefixV4 -and -not $networks.Contains($sender)) { $networks.Add($sender) }
    }
    if ($outside.Count -gt 0) {
        $text = "Log kaynakları tarayıcı yönetiminin kaynak ağlarının dışında [log sources outside the browser administration's source networks] ({0}): ApplySystem bu göndericileri güvenlik duvarının 514 kuralından çıkarırdı, logları kaybolurdu [ApplySystem would take these senders out of the firewall's 514 rule and their logs would be lost]. Kurulumu ağlarıyla yeniden çalıştırın (liste varsayılanın yerine geçer) [run the installer again with their networks; the list replaces the default]: -SourceNetwork {1}. /{2} ağından geniş bir kaynağı daraltın ya da admin\policy.json içindeki min_prefix_v4 değerini elle küçültün [narrow a source wider than /{2}, or lower min_prefix_v4 in admin\policy.json by hand]." -f ($outside -join '; '), ($networks -join ','), $choice.MinPrefixV4
        $narrows = $choice.Enabled -and ((-not $SkipFirewall) -or (Test-Path -LiteralPath $FirewallStatePath))
        Add-Check 'admin-policy-sources' $(if ($narrows) { 'fail' } else { 'warn' }) $text
    }
    if ($choice.Existing.Exists -and -not $choice.Existing.Valid) {
        Add-Check 'admin-policy-file' 'warn' ("{0} geçersiz, yeniden yazılacak; elle yazılan değerler varsayılana döner [invalid, rewritten with the defaults]: {1}" -f $PolicyPath, $choice.Existing.Problem)
    }
    $state = $(if ($choice.Enabled) { 'açık [on]' } else { 'kapalı [off] (-DisableBrowserAdministration)' })
    Add-Check 'admin-policy' 'ok' ("Tarayıcı yönetimi [browser administration] {0}; kaynak ağları [source networks] {1}; bekletme [hold] {2} saat, Zamane istemcisi [Zamane client] {3} saat" -f $state, (@($choice.SourceNetworks) -join ', '), $choice.HoldHours, $choice.JarHoldHours)
}

function Find-SystemPathProgram([string]$Name) {
    <# The file the system PATH (the machine's, which the tasks see) finds for a bare program name such as java, or $null. #>
    $names = @($Name)
    if (-not [System.IO.Path]::HasExtension($Name)) { $names += ($Name + '.exe') }
    foreach ($folder in ([string][Environment]::GetEnvironmentVariable('Path', [EnvironmentVariableTarget]::Machine)).Split(';')) {
        if (-not $folder.Trim()) { continue }
        foreach ($candidate in $names) {
            $file = Join-Path ([Environment]::ExpandEnvironmentVariables($folder.Trim())) $candidate
            if (Test-Path -LiteralPath $file -PathType Leaf) { return $file }
        }
    }
    $null
}

function Get-PathWriters([string]$Path) {
    <# Who other than administrators and SYSTEM could change $Path or a folder above it (Get-ArciUnsafeWriters); nobody where Windows rights cannot be read (a dry run off Windows). #>
    $facts = Get-ArciPathAclFacts -Path $Path
    if ($null -eq $facts) { return , @() }
    $writers = Get-ArciUnsafeWriters -Facts $facts
    , @($writers)
}

function Get-ExecutableRecordPlan($Config) {
    <#
    What this run records for the configuration $Config (Read-ArciTomlFile) so that the tasks
    keep running its programs (arcilogger_core.execpolicy, contract 2.16): Records (what, path,
    sha256, argv) for policy.trusted_executables, Jar (Get-ArciZamaneJarPlan), Warnings and
    Problems (each stops the run). A program, or a Zamane client copied into zamane\, is recorded
    only from a folder that only administrators and SYSTEM can write (Day 0 note D0-3): anyone
    else who can write it could replace the file the record trusts.
    #>
    $records = New-Object System.Collections.Generic.List[object]
    $warnings = New-Object System.Collections.Generic.List[string]
    $problems = New-Object System.Collections.Generic.List[string]
    $fix = 'programı Program Files altına taşıyın ya da klasörün yazma haklarını yalnızca yöneticilere ve SYSTEM''e bırakın, sonra kurulumu yeniden çalıştırın [move the program below Program Files or leave the write rights of its folders to administrators and SYSTEM only, then run the installer again]'
    $candidates = Get-ArciExecutableRecords -Config $Config -ExecutableRoot @($script:AdminPolicyChoice.ExecutableRoots)
    foreach ($candidate in $candidates) {
        $file = [string]$candidate.Path
        if (-not ($file.Contains('\') -or $file.Contains('/'))) { $file = Find-SystemPathProgram $file }
        if (-not $file -or -not (Test-Path -LiteralPath $file -PathType Leaf)) {
            $warnings.Add(("{0} bulunamadı, kaydedilmedi [not found, not recorded]: {1}" -f $candidate.What, $candidate.Path))
            continue
        }
        $writers = Get-PathWriters $file
        if (@($writers).Count -gt 0) {
            $problems.Add(("{0} {1}: yöneticiler ve SYSTEM dışında yazabilenler var [others can write it] ({2}); {3}" -f $candidate.What, $candidate.Path, (@($writers) -join '; '), $fix))
            continue
        }
        $records.Add([pscustomobject]@{ what = $candidate.What; path = [string]$candidate.Path; sha256 = (Get-ArciFileSha256 -Path $file); argv = $candidate.Argv })
    }
    $jar = [string](Get-ArciConfigValue -Config $Config -Path 'tsa.zamane_jar' -Default '')
    $sha256 = $null
    if ($jar -and (Test-Path -LiteralPath $jar -PathType Leaf)) { $sha256 = Get-ArciFileSha256 -Path $jar }
    $installer = Read-ArciInstallerHashes -Path (Join-Path $ZamaneDir 'zamane-jar.sha256')
    $recorded = @($installer)
    $jars = Join-Path $AdminDir 'system\jars.json'
    if (Test-Path -LiteralPath $jars) {
        try { $recorded += @((Read-JsonFile $jars).approved | ForEach-Object { [string]$_.sha256 }) } catch { Write-Verbose 'jars.json unreadable: nothing approved' }
    }
    $target = $null
    if ($jar) {
        $name = $jar.Replace('/', '\').Split('\')[-1]
        $existing = Join-Path $ZamaneDir $name
        if ($name -and (Test-Path -LiteralPath $existing -PathType Leaf)) { $target = Get-ArciFileSha256 -Path $existing }
    }
    $plan = Get-ArciZamaneJarPlan -Jar $jar -Sha256 $sha256 -ZamaneDir $ZamaneDir -Recorded $recorded -TargetSha256 $target
    if ($plan.Action -eq 'refuse') { $problems.Add(('tsa.zamane_jar {0}: {1}' -f $jar, $plan.Reason)) }
    if ($plan.Action -eq 'copy') {
        $writers = Get-PathWriters $jar
        if (@($writers).Count -gt 0) { $problems.Add(("tsa.zamane_jar {0}: yöneticiler ve SYSTEM dışında yazabilenler var [others can write it] ({1}); istemciyi -ZamaneJar ile kurun [install the client with -ZamaneJar]" -f $jar, (@($writers) -join '; '))) }
    }
    [pscustomobject]@{ Records = $records.ToArray(); Jar = $plan; Warnings = $warnings.ToArray(); Problems = $problems.ToArray() }
}

function Invoke-ExecutablePreflight {
    <#
    The programs the tasks run, checked before any change (contract 2.16): what this run will
    record (Get-ExecutableRecordPlan) and what stops it. Needs this installation's Python to read
    an existing arcilogger.toml (an upgrade or repair: the version still installed); without one
    (a new configuration, or a new installation over a kept one) the same checks run in the
    executables.record step with the new Python.
    #>
    if (-not (Test-Path -LiteralPath $ConfigPath) -or -not (Test-Path -LiteralPath $VenvPython)) { return }
    try { $config = Read-ArciTomlFile -Python $VenvPython -ConfigPath $ConfigPath }
    catch {
        Add-Check 'executables' 'fail' ("arcilogger.toml okunamadı [unreadable]: {0}" -f $_.Exception.Message)
        return
    }
    $result = Get-ExecutableRecordPlan $config
    $script:ExecutableRecords = $result.Records
    $script:ZamaneJarPlan = $result.Jar
    foreach ($warning in $result.Warnings) { Add-Check 'executables' 'warn' $warning }
    foreach ($problem in $result.Problems) { Add-Check 'executables' 'fail' $problem }
    if ($result.Problems.Count -eq 0) {
        $what = @($result.Records | ForEach-Object { '{0} {1}' -f $_.what, $_.path })
        if ($result.Jar.Action -ne 'none') { $what += ('tsa.zamane_jar {0} ({1})' -f $result.Jar.Jar, $result.Jar.Action) }
        if ($what.Count -gt 0) { Add-Check 'executables' 'ok' ("Kaydedilecek programlar [programs to record]: {0}" -f ($what -join '; ')) }
        else { Add-Check 'executables' 'ok' 'Görevlerin programları güvenilen klasörlerde [the tasks'' programs are in the trusted folders]' }
    }
}

function Update-ExecutableRecords {
    <#
    The executables.record step: read the configuration with this installation's Python, check
    it (Get-ExecutableRecordPlan; the preflight did so already where it could), keep the
    programs for policy.json (Invoke-AdminSteps) and carry out the Zamane client's record: its
    line appended to zamane-jar.sha256 and, for a client outside zamane\, the copy, with
    tsa.zamane_jar pointed at it (the previous configuration kept as arcilogger.toml.before-<time>).
    #>
    $result = Get-ExecutableRecordPlan (Read-ArciTomlFile -Python $VenvPython -ConfigPath $ConfigPath)
    if ($result.Problems.Count -gt 0) { throw ($result.Problems -join ' | ') }
    foreach ($warning in $result.Warnings) { Write-ArciWarn $warning; $script:Warnings.Add($warning) }
    $script:ExecutableRecords = $result.Records
    $script:ZamaneJarPlan = $result.Jar
    $plan = $result.Jar
    if ($plan.Action -eq 'copy') {
        $copied = $false
        if (-not (Test-Path -LiteralPath $plan.Target)) {
            Copy-Item -LiteralPath $plan.Jar -Destination $plan.Target
            $copied = $true
        }
        if ((Get-ArciFileSha256 -Path $plan.Target) -ne $plan.Sha256) {
            if ($copied) { Remove-Item -LiteralPath $plan.Target -Force }
            throw ("Zamane istemcisinin kopyası tutmuyor [the copy of the Zamane client does not match]: {0}" -f $plan.Target)
        }
        $text = [System.IO.File]::ReadAllText($ConfigPath, [System.Text.Encoding]::UTF8)
        $backup = '{0}.before-{1}' -f $ConfigPath, [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
        Copy-Item -LiteralPath $ConfigPath -Destination $backup -Force
        Write-ArciUtf8File -Path $ConfigPath -Text (Set-ArciTomlString -Text $text -Section 'tsa' -Key 'zamane_jar' -Value $plan.Target.Replace('\', '/'))
        Add-ZamaneJarRecord -Sha256 $plan.Sha256 -Name ([System.IO.Path]::GetFileName($plan.Target)) -Note ('copied from {0} on {1} by the installer' -f $plan.Jar, [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'))
        $message = 'tsa.zamane_jar {0} -> {1}: {2} (önceki yapılandırma [previous configuration]: {3})' -f $plan.Jar, $plan.Target, $plan.Reason, $backup
        Write-ArciWarn $message
        $script:Warnings.Add($message)
    }
    elseif ($plan.Action -eq 'record') {
        Add-ZamaneJarRecord -Sha256 $plan.Sha256 -Name ([System.IO.Path]::GetFileName($plan.Jar)) -Note ('recorded on {0} by the installer' -f [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'))
        Write-ArciInfo ('{0}: {1}' -f $plan.Jar, $plan.Reason)
    }
    foreach ($record in $script:ExecutableRecords) {
        $message = '{0} {1} güvenilen programlara eklendi [recorded as a trusted program] (SHA-256 {2}); program değişirse kurulumu yeniden çalıştırın [run the installer again when the program changes]' -f $record.what, $record.path, $record.sha256
        Write-ArciWarn $message
        $script:Warnings.Add($message)
    }
}

function Get-AdminPolicyText([object[]]$Records) {
    <#
    The text of admin\policy.json this run writes: the choices of Resolve-AdminPolicyChoice and
    the trusted programs, those of policy.json kept unless $Records has a program of the same
    path (the new record replaces it).
    #>
    $choice = $script:AdminPolicyChoice
    $new = @($Records | Where-Object { $null -ne $_ })
    $trusted = New-Object System.Collections.Generic.List[object]
    foreach ($entry in @($choice.TrustedExecutables)) {
        if ($null -eq $entry) { continue }
        if (@($new | Where-Object { [string]$_.path -ieq [string]$entry.path }).Count -eq 0) { $trusted.Add($entry) }
    }
    foreach ($record in $new) { $trusted.Add([pscustomobject]@{ path = $record.path; sha256 = $record.sha256; argv = $record.argv }) }
    New-ArciAdminPolicy -Enabled $choice.Enabled -SourceNetwork @($choice.SourceNetworks) -MinPrefixV4 $choice.MinPrefixV4 `
        -MaxSources $choice.MaxSources -HoldHours $choice.HoldHours -JarHoldHours $choice.JarHoldHours `
        -ExecutableRoot @($choice.ExecutableRoots) -TrustedExecutable $trusted.ToArray() -TsaTestPerDay $choice.TsaTestPerDay
}

function Invoke-AdminSteps {
    <#
    The browser administration's part of the configuration (ADR-0014, contract 2.16), safe to
    run again: admin\ and admin\keys\ protected (administrators and SYSTEM only) before
    "keys init" creates the channel's keys (it never replaces one), admin\policy.json with the
    remembered choices and the recorded programs, and the Event Log source ARCILOGGER-Admin.
    Invoke-AclSteps then grants every reader its one right.
    #>
    $aclPlan = Get-ArciAclPlan -DataRoot $DataRoot -ProgramDataDir $ProgramDataDir -InstallDir $InstallDir
    $protected = @($aclPlan | Where-Object { $_.Path -eq $AdminDir -or $_.Path -eq $AdminKeysDir })
    Invoke-Step 'admin.protect' ("Yönetim klasörü korunuyor [protecting the administration folder]: {0}" -f $AdminDir) {
        # The inheritable read of the configuration folder for the services and LOCAL SERVICE
        # must never reach a private key, even if this run stops before its ACL step (D0-7).
        foreach ($entry in $protected) {
            [void](New-Item -ItemType Directory -Path $entry.Path -Force)
            Set-ArciAclEntry -Entry $entry
        }
    } $protected
    Invoke-Step 'admin.keys' 'Yönetim kanalının anahtarları [administration channel keys] (keys init; var olan bir anahtar değişmez [an existing key is never replaced])' {
        $result = Invoke-ArciProcess -FilePath $VenvPython -ArgumentList @('-I', '-m', 'arcilogger_core.admin', 'keys', 'init', '--config', $ConfigPath)
        if ($result.StdOut.Trim()) { Write-ArciInfo $result.StdOut.Trim() }
    } $AdminKeysDir
    $planned = $null
    if ($script:IsDryRun) {
        try { $planned = (Get-AdminPolicyText -Records $script:ExecutableRecords) | ConvertFrom-Json }
        catch { $planned = $_.Exception.Message }
    }
    $state = $(if ($script:AdminPolicyChoice.Enabled) { 'açık [on]' } else { 'kapalı [off]' })
    Invoke-Step 'admin.policy' ("Tarayıcı yönetimi kuralları [browser administration policy] ({0}): {1}" -f $state, $PolicyPath) {
        Write-ArciUtf8File -Path $PolicyPath -Text (Get-AdminPolicyText -Records $script:ExecutableRecords)
    } $planned
    Invoke-Step 'admin.event-source' 'Olay günlüğü kaynağı [Event Log source] ARCILOGGER-Admin (Application)' {
        Write-ArciInfo ('ARCILOGGER-Admin: {0}' -f (Register-ArciEventSource))
    }
}

function Save-ConfigBeforeUpgrade {
    <#
    Keep arcilogger.toml as arcilogger.toml.before-upgrade-<UTC time> and remember its name in
    install\state.json (config_before_upgrade): a rollback to this version puts it back when the
    version cannot load what the browser changed later (Restore-ConfigForProgram).
    #>
    if (-not (Test-Path -LiteralPath $ConfigPath)) { return }
    $copy = '{0}.before-upgrade-{1}' -f $ConfigPath, [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
    Copy-Item -LiteralPath $ConfigPath -Destination $copy -Force
    Write-InstallState @{ config_before_upgrade = $copy }
    Write-ArciInfo ('Yapılandırmanın kopyası [copy of the configuration]: {0}' -f $copy)
}

function Restore-ConfigForProgram {
    <#
    Before the services of the program in $InstallDir start again after a rollback or a failed
    upgrade (contract 2.16): its Python must load arcilogger.toml (Test-ArciProgramConfig). A
    version from before the browser administration refuses what the browser added, such as
    enabled = false of a log source: the copy kept before the upgrade (config_before_upgrade of
    install\state.json) is put back then, the refused file kept beside it as
    arcilogger.toml.refused-<time>, and the operator is told. Only the content is written, so
    arcilogger.toml keeps its rights.
    #>
    if (-not (Test-Path -LiteralPath $ConfigPath) -or -not (Test-Path -LiteralPath $VenvPython)) { return }
    $problem = Test-ArciProgramConfig -Python $VenvPython -ConfigPath $ConfigPath
    if (-not $problem) { return }
    $state = Read-InstallState
    $copy = $null
    if ($state.PSObject.Properties.Name -contains 'config_before_upgrade') { $copy = [string]$state.config_before_upgrade }
    if (-not $copy -or -not (Test-Path -LiteralPath $copy -PathType Leaf)) {
        throw ("arcilogger.toml bu sürümle yüklenmiyor ve yükseltme öncesi kopyası yok; dosyayı elle düzeltin [arcilogger.toml does not load with this version and there is no copy from before the upgrade: fix it by hand]: {0}" -f $problem)
    }
    $refused = '{0}.refused-{1}' -f $ConfigPath, [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
    Copy-Item -LiteralPath $ConfigPath -Destination $refused -Force
    [System.IO.File]::WriteAllBytes($ConfigPath, [System.IO.File]::ReadAllBytes($copy))
    $script:ConfigRestored = $true
    $still = Test-ArciProgramConfig -Python $VenvPython -ConfigPath $ConfigPath
    if ($still) { throw ("Yükseltme öncesi kopya da yüklenmiyor [the copy from before the upgrade does not load either]: {0}" -f $still) }
    $message = 'Tarayıcıdan yapılan sonraki değişiklikler geri alındı; denetim kaydında duruyorlar [later browser changes were rolled back; they remain in the audit log]. Önceki sürüm arcilogger.toml''u yükleyemedi [the previous version could not load arcilogger.toml] ({0}); yükseltme öncesi kopya geri kondu [the copy from before the upgrade is back]: {1}; reddedilen dosya [the refused file]: {2}. Güvenlik duvarı ve Windows Saati bu yapılandırmaya göre yeniden ayarlanır [the firewall and Windows Time are set again from this configuration]' -f $problem, $copy, $refused
    Write-ArciWarn $message
    $script:Warnings.Add($message)
}

function Sync-SystemAfterRollback {
    <#
    After a rollback, or a failed upgrade whose configuration had to be put back: the firewall's
    syslog and outbound rules and the Windows Time peers follow the configuration as the program
    in $InstallDir reads it. ApplySystem set them from the newer configuration and is gone with
    the newer tasks; a configuration put back by Restore-ConfigForProgram lists sources again (a
    source switched off or removed in the browser) that the rules no longer let through. The
    restored program's own scripts run: Set-ArciFirewall.ps1 in its full mode with the choices
    its state file remembers (only where ARCILOGGER manages the firewall), Set-ArciTime.ps1 with
    [clock]. A failure is a warning naming the command to run by hand, never a failed rollback:
    the services and the evidence path come back either way.
    #>
    $scripts = Join-Path $InstallDir 'scripts'
    if (-not $SkipFirewall -and (Test-Path -LiteralPath $FirewallStatePath)) {
        $firewall = Join-Path $scripts 'Set-ArciFirewall.ps1'
        try { & $firewall -ConfigPath $ConfigPath -Python $VenvPython -StateFile $FirewallStatePath -Force:$Force }
        catch {
            $message = 'Güvenlik duvarı geri alınan yapılandırmayla eşitlenemedi, 514 kuralı eski olabilir [firewall not brought in line with the restored configuration, the 514 rule may be stale]: {0}. Yönetici PowerShell''inde çalıştırın [run in an administrator PowerShell]: powershell -ExecutionPolicy Bypass -File "{1}"' -f $_.Exception.Message, $firewall
            Write-ArciWarn $message
            $script:Warnings.Add($message)
        }
    }
    if (-not $SkipTimeSetup) {
        $time = Join-Path $scripts 'Set-ArciTime.ps1'
        try {
            $config = Read-ArciTomlFile -Python $VenvPython -ConfigPath $ConfigPath
            & $time -Primary (Get-ArciConfigValue -Config $config -Path 'clock.ntp_server' -Default '') -Fallback (Get-ArciConfigValue -Config $config -Path 'clock.fallback_server' -Default 'time.ume.tubitak.gov.tr')
        }
        catch {
            $message = 'Windows Saati geri alınan yapılandırmayla eşitlenemedi [Windows Time not brought in line with the restored configuration]: {0}. Çalıştırın [run]: powershell -ExecutionPolicy Bypass -File "{1}" -Primary <[clock] ntp_server>' -f $_.Exception.Message, $time
            Write-ArciWarn $message
            $script:Warnings.Add($message)
        }
    }
}

function Test-JavaForZamane {
    if (-not (Test-Path -LiteralPath $VenvPython) -or -not (Test-Path -LiteralPath $ConfigPath)) { return }
    $config = Read-ArciTomlFile -Python $VenvPython -ConfigPath $ConfigPath
    if ((Get-ArciConfigValue -Config $config -Path 'tsa.provider' -Default 'mock') -ne 'zamane') { return }
    $java = @(Get-ArciConfigValue -Config $config -Path 'tsa.java' -Default @())
    $jar = Get-ArciConfigValue -Config $config -Path 'tsa.zamane_jar'
    if ($java.Count -eq 0 -or -not (Test-Path -LiteralPath ([string]$java[0]))) {
        $found = @(Get-ChildItem -Path (Join-Path $env:ProgramFiles 'Eclipse Adoptium\jre-8*\bin\java.exe') -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
        $hint = if ($found.Count -gt 0) { " Bulunan [found]: $($found[0]) -> tsa.java ayarını buna çevirin." } else { ' Temurin 8 JRE kurun (docs\KURULUM.md).' }
        $message = ("tsa.java bulunamadı [not found]: {0}.{1}" -f ($java -join ' '), $hint)
        Write-ArciWarn $message
        $script:Warnings.Add($message)
    }
    if (-not $jar -or -not (Test-Path -LiteralPath ([string]$jar))) {
        $message = ("Zamane jar yok [missing]: {0} -> Kamu SM'den indirip -ZamaneJar ile kurun." -f $jar)
        Write-ArciWarn $message
        $script:Warnings.Add($message)
    }
}

function Set-PostgresDataAccess {
    <#
    The data root's inheritance is cut off. If the PostgreSQL data folder lies inside it
    (D:\ARCILOGGER\db), make sure PostgreSQL's own service account keeps full control there.
    #>
    $service = Find-PostgresService
    $dataDir = Get-PostgresDataDir $service
    if (-not $dataDir) { return }
    $full = [System.IO.Path]::GetFullPath($dataDir)
    if (-not $full.StartsWith($DataRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) { return }
    $account = [string]$service.StartName
    $sid = (New-Object System.Security.Principal.NTAccount -ArgumentList $account).Translate([System.Security.Principal.SecurityIdentifier]).Value
    $item = Get-Item -LiteralPath $full -Force
    $security = Get-ArciAccessControl -Item $item
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule -ArgumentList (New-Object System.Security.Principal.SecurityIdentifier -ArgumentList $sid), ([System.Security.AccessControl.FileSystemRights]::FullControl), ([System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'), ([System.Security.AccessControl.PropagationFlags]::None), ([System.Security.AccessControl.AccessControlType]::Allow)
    $security.AddAccessRule($rule)
    Set-ArciAccessControl -Item $item -Security $security
    Write-ArciInfo ("PostgreSQL veri klasörü erişimi korundu [kept access for {0}]: {1}" -f $account, $full)
}

function Invoke-AclSteps {
    $aclPlan = Get-ArciAclPlan -DataRoot $DataRoot -ProgramDataDir $ProgramDataDir -InstallDir $InstallDir
    Invoke-Step 'acl.apply' 'NTFS izinleri ve kasa denetimi [NTFS rights, vault auditing]' {
        & (Join-Path $ScriptsDir 'Set-ArciVaultAcl.ps1') -DataRoot $DataRoot -ProgramDataDir $ProgramDataDir -InstallDir $InstallDir
        Set-PostgresDataAccess
    } $aclPlan
}

function Restore-ConfigFileAccess {
    <#
    The entries of arcilogger.toml and secrets.json of the ACL plan once more, after this run
    stored a secret (contract 2.2): "arcilogger-core secret set" writes a new secrets.json and
    renames it over the old one, and the new file carries only the inherited entries. Without
    the explicit ArciAdmin entry the agent could not replace the file it applies the browser's
    changes to (acl_drift after the first change). Off Windows (the tests) there is nothing to set.
    #>
    if (-not (Test-ArciWindows)) { return }
    $entries = @(Get-ArciAclPlan -DataRoot $DataRoot -ProgramDataDir $ProgramDataDir -InstallDir $InstallDir | Where-Object { $_.Path -eq $ConfigPath -or $_.Path -eq $SecretsFile })
    foreach ($entry in $entries) {
        if (Test-Path -LiteralPath $entry.Path) { Set-ArciAclEntry -Entry $entry }
    }
}

# --- health ------------------------------------------------------------------------------------------

function Invoke-HealthCheck {
    $healthy = $true
    foreach ($name in $ServiceNames) {
        $service = Get-Service -Name $name -ErrorAction SilentlyContinue
        if ($null -eq $service) { continue }
        if ($service.Status -ne 'Running') {
            $healthy = $false
            $script:Warnings.Add(("{0}: {1}" -f $name, $service.Status))
            Write-ArciWarn ("{0} çalışmıyor [not running]: {1}" -f $name, $service.Status)
        }
    }
    # The services report "Running" as soon as WinSW has started its child; Python still imports and
    # Caddy issues its certificate after that. Poll before calling anything a failure.
    $apiOk = $false
    if (Get-Service -Name 'ArciApi' -ErrorAction SilentlyContinue) {
        $apiError = 'no answer'
        # A health check never fails the run: an unreadable [api] section falls back to the default.
        try { $endpoint = Get-ApiEndpoint }
        catch {
            Write-ArciWarn ("[api] okunamadı, varsayılan adres denetleniyor [unreadable, checking the default]: {0}" -f $_.Exception.Message)
            $endpoint = Get-ArciApiEndpoint -Config $null
        }
        $healthUrl = 'http://{0}/api/v1/health' -f $endpoint.Address
        $deadline = [DateTime]::UtcNow.AddSeconds($HealthWaitSeconds)
        while ($true) {
            try {
                $api = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 10
                $body = $api.Content | ConvertFrom-Json
                if ($body.status -ne 'ok') { throw ('status=' + $body.status) }
                Write-ArciInfo ("API: ok (sürüm {0})" -f $body.version)
                $apiOk = $true
                break
            }
            catch { $apiError = $_.Exception.Message }
            if ([DateTime]::UtcNow -gt $deadline) { break }
            Start-Sleep -Seconds 3
        }
        if (-not $apiOk) {
            $healthy = $false
            $script:Warnings.Add('API health: ' + $apiError)
            Write-ArciWarn ("API yanıt vermiyor [API not answering]: {0}" -f $apiError)
        }
    }
    else {
        $healthy = $false
        $script:Warnings.Add('API health: ArciApi service not installed')
        Write-ArciWarn 'ArciApi hizmeti kurulu değil; API denetlenmedi [API service not installed, not checked].'
    }
    $deadline = [DateTime]::UtcNow.AddSeconds($HealthWaitSeconds)
    while (-not (Test-Path -LiteralPath $CaddyRootSource) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Seconds 2 }
    if (Test-Path -LiteralPath $CaddyRootSource) {
        Copy-Item -LiteralPath $CaddyRootSource -Destination $CaddyRootCopy -Force
        $certificate = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList $CaddyRootCopy
        $script:RootSha256 = Get-ArciFileSha256 -Path $CaddyRootCopy
        $script:RootThumbprint = $certificate.Thumbprint
        # With the API down Caddy answers 502: that still proves TLS and the web server, so wait
        # only until Caddy answers at all and report the API separately.
        $httpsError = 'no answer'
        $httpsOk = $false
        while ($true) {
            try {
                $https = Test-ArciHttpsHealth -HostName 'localhost' -RootCertificatePath $CaddyRootCopy
                if ($https.Status -eq 200 -and $https.Body -match '"status"\s*:\s*"ok"') {
                    Write-ArciInfo 'https://localhost/api/v1/health: ok'
                    $httpsOk = $true
                    break
                }
                $httpsError = 'HTTP {0}' -f $https.Status
                if (-not $apiOk -and $https.Status -ge 500) {
                    Write-ArciInfo ("https://localhost: Caddy ve TLS çalışıyor, API yok [Caddy and TLS work, API down]: HTTP {0}" -f $https.Status)
                    break
                }
            }
            catch { $httpsError = $_.Exception.Message }
            if ([DateTime]::UtcNow -gt $deadline) { break }
            Start-Sleep -Seconds 3
        }
        if (-not $httpsOk) {
            $healthy = $false
            $script:Warnings.Add('HTTPS health: ' + $httpsError)
            Write-ArciWarn ("HTTPS denetimi başarısız [HTTPS check failed]: {0}" -f $httpsError)
        }
    }
    else {
        $healthy = $false
        $script:Warnings.Add('Caddy root certificate not created')
        Write-ArciWarn ("Caddy kök sertifikası oluşmadı [root certificate missing]: {0}" -f $CaddyRootSource)
    }
    if (-not $healthy -and $script:ExitCode -eq 0) { $script:ExitCode = 1 }
}

function Show-NextSteps {
    $url = 'https://' + $ServerAddress
    Write-Host ''
    Write-Host ('ARCILOGGER {0} hazır [ready].' -f $script:Manifest.version) -ForegroundColor Green
    Write-Host ('  Web arayüzü [web interface]: {0}   (https://{1})' -f $url, $ServerName)
    Write-Host '  Sonraki adımlar [next steps]:'
    Write-Host ('   1. Kök sertifikayı yönetici PC''sine kurun [install the root certificate on the admin PC]: {0}' -f $CaddyRootCopy)
    if (Get-Variable -Name RootSha256 -Scope Script -ErrorAction SilentlyContinue) {
        Write-Host ('      SHA-256 {0}' -f $script:RootSha256)
        Write-Host ('      Parmak izi [thumbprint] {0}' -f $script:RootThumbprint)
    }
    $atConsole = $script:FreshInstall -and (Test-ArciInteractiveConsole)
    Write-Host '   2. İlk yönetici kullanıcıyı oluşturun [create the first admin user] (docs\KURULUM.md, "İlk giriş"):'
    if ($atConsole -and -not $SkipDatabase) {
        Write-Host '      Henüz yönetici yoksa kurulum anahtarı bu pencerede, kurulum günlüğü kapandıktan sonra gösterilir [unless an administrator exists, the setup token is shown in this window once the log is closed].'
    }
    Write-Host '      Yönetici PowerShell''inde tek kullanımlık, 24 saat geçerli kurulum anahtarı alın [one-time setup token, 24 h]:'
    Write-Host ('      & "{0}" setup-token --config "{1}"' -f $ApiExe, $ConfigPath)
    Write-Host ('      Sonra {0}/setup adresini açıp anahtarı, kullanıcı adını ve parolayı girin [then open {0}/setup].' -f $url)
    $browserAdministration = ($null -eq $script:AdminPolicyChoice) -or $script:AdminPolicyChoice.Enabled
    # Step 3 follows the configured provider (contract 2.4): TÜRKTRUST's account needs no Java
    # and no Zamane client. An unreadable configuration keeps Kamu SM's text, a new
    # installation's. The secrets are never asked here and never written into the log.
    $provider = $null
    if ((Test-Path -LiteralPath $VenvPython) -and (Test-Path -LiteralPath $ConfigPath)) {
        try {
            $config = Read-ArciTomlFile -Python $VenvPython -ConfigPath $ConfigPath
            $provider = Get-ArciConfigValue -Config $config -Path 'tsa.provider' -Default 'mock'
        }
        catch { $provider = $null }
    }
    if ($provider -eq 'turktrust') {
        Write-Host '   3. TÜRKTRUST zaman damgası hesabı [time-stamp account]:'
        Write-Host '      Kullanıcı adı ve parolayı web arayüzünde Ayarlar → Zaman damgası''nda girin; konsoldan da girilebilir [enter the user name and password under Settings → Time stamp in the web interface; or at the console] (docs\AYARLAR.md):'
        if (-not $browserAdministration) {
            Write-Host '      Tarayıcı yönetimi kapalı: yalnız konsoldan [browser administration is off: at the console only].'
        }
        Write-Host ('      & "{0}" secret set turktrust.username' -f $CoreExe)
        Write-Host ('      & "{0}" secret set turktrust.password' -f $CoreExe)
        Write-Host ('      & "{0}"   # secret set / secret delete sonrası: ArciAdmin''in secrets.json hakkı geri gelir [after a console secret set / secret delete: gives ArciAdmin its right on secrets.json back]' -f (Join-Path $InstallDir 'scripts\Set-ArciVaultAcl.ps1'))
        Write-Host ('      Kök sertifikalar [roots]: TÜRKTRUST''un kök sertifikalarını TÜRKTRUST''tan alın; Ayarlar → Zaman damgası''ndan yükleyin ya da {0} içine koyun, SHA-256 parmak izlerini TÜRKTRUST''un yayımladığı değerlerle karşılaştırın [get TÜRKTRUST''s root certificates from TÜRKTRUST; upload them under Settings → Time stamp or put them in {0}, and compare the fingerprints with the values TÜRKTRUST publishes].' -f (Join-Path $ProgramDataDir 'kamusm-roots'))
        Write-Host ('      & "{0}" tsa-test   # damgayı bu köklerle doğrular [verifies the stamp with these roots]' -f $CoreExe)
    }
    else {
        Write-Host '   3. Kamu SM test hesabı [test account]:'
        if ($browserAdministration) {
            Write-Host '      Web arayüzünde Ayarlar → Kamu SM sihirbazı bunu adım adım yapar (Java, Zamane istemcisi, kök sertifikalar, müşteri bilgileri, damga testi); konsoldan da yapılabilir [the Kamu SM wizard under Settings in the web interface does it step by step; or at the console] (docs\AYARLAR.md):'
        }
        Write-Host ('      Önce kök sertifikalar [roots first]: Kamu SM kök sertifikalarını {0} içine koyun, SHA-256 parmak izlerini Kamu SM''nin yayımladığı değerlerle karşılaştırın [compare the fingerprints] (docs\KURULUM.md bölüm 10).' -f (Join-Path $ProgramDataDir 'kamusm-roots'))
        Write-Host '      tsa-test 1 kontör harcar ve damgayı bu köklerle doğrular [tsa-test spends 1 credit, then verifies with these roots].'
        if ($atConsole) {
            Write-Host '      Müşteri numarası ve parola, günlük kapandıktan sonra bu pencerede de girilebilir [can also be entered in this window once the log is closed]:'
        }
        Write-Host ('      & "{0}" secret set kamusm.customer' -f $CoreExe)
        Write-Host ('      & "{0}" secret set kamusm.password' -f $CoreExe)
        Write-Host ('      & "{0}"   # secret set / secret delete sonrası: ArciAdmin''in secrets.json hakkı geri gelir [after a console secret set / secret delete: gives ArciAdmin its right on secrets.json back]' -f (Join-Path $InstallDir 'scripts\Set-ArciVaultAcl.ps1'))
        Write-Host ('      & "{0}" credit ; & "{0}" tsa-test' -f $CoreExe)
    }
    Write-Host '   4. Cihaz ayarları [device setup]: docs\DEVICE-SETUP.md'
    if ($script:SourceCount -eq 0) {
        Write-Host ('      Log kaynağı yok [no log source yet]: güvenlik duvarınızın ve ana yönlendiricinizin (DHCP sunucusu) [[sources]] kayıtlarını {0} dosyasına ekleyin; dosyadaki örnekler yorum satırıdır (docs\DEVICE-SETUP.md bölüm 4) [add the [[sources]] of your firewall and core router (the DHCP server) to this file; its examples are commented out].' -f $ConfigPath)
        if ($SkipFirewall) {
            Write-Host '      O zamana kadar her gönderici bilinmeyen gönderici sayılır (unknown-<ip>) ve hiçbir kaynak sessizlik için izlenmez (R15); ekledikten sonra hizmetleri yeniden başlatın [until then every sender counts as unknown and no source is watched for silence (R15); restart the services after adding them].'
        }
        else {
            Write-Host '      O zamana kadar güvenlik duvarı 514''ü hiçbir göndericiye açmaz, hiçbir log kaydedilmez; ekledikten sonra kurulumu yeniden çalıştırın [until then the firewall opens 514 to no sender and no log is recorded; run the installer again after adding them].'
        }
        if ($browserAdministration) {
            Write-Host '      Kaynaklar web arayüzünde Ayarlar → Kaynaklar''dan da eklenir; toplayıcı ve güvenlik duvarı yeniden başlatma olmadan izler [or add them under Settings → Sources in the web interface: the collector and the firewall follow without a restart].'
        }
    }
    if (-not $browserAdministration) {
        Write-Host '      Tarayıcı yönetimi kapalı (-DisableBrowserAdministration): ayarlar konsoldan değişir [browser administration is off: settings change at the console] (docs\AYARLAR.md).'
    }
    Write-Host '   5. Kurulumdan sonraki ilk adımlar [first steps]: docs\ILK-ADIMLAR.md (EN: docs\FIRST-STEPS.md)'
    # 6. The licence: its state when one is installed, otherwise the request code to e-mail to the
    # licensor ("arcilogger-db license request-code", 5 s each). The code is not secret (server
    # id, installation id, organisation): it may reach the transcript.
    $mail = @(
        '      Bu kodu arman@khadangan.com adresine e-postayla gönderin; gelen .lic dosyasını web arayüzünde Ayarlar → Lisans''tan yükleyin ya da kurulumu -LicenseFile ile yeniden çalıştırın (docs\LISANS.md).',
        '      [E-mail this code to arman@khadangan.com; upload the .lic you receive under Settings → Licence, or run the installer again with -LicenseFile (docs\LICENSING.md).]'
    )
    $licenseState = $null
    $licensee = $null
    $requestCode = $null
    $licenseProblem = $null
    if ($SkipDatabase) { $licenseProblem = '-SkipDatabase' }
    elseif (-not (Test-Path -LiteralPath $DbExe)) { $licenseProblem = '{0} yok [missing]' -f $DbExe }
    else {
        try {
            $status = Invoke-DbLicense -ArgumentList @('status', '--json') -TimeoutSeconds 5
            if ($status.ExitCode -eq 0) {
                $document = $status.StdOut | ConvertFrom-Json
                $licenseState = Get-ArciJsonField -Object $document -Name 'status'
                $licensee = Get-ArciJsonField -Object $document -Name 'licensee'
            }
        }
        catch { $licenseState = $null }
        if ($licenseState -ne 'licensed') {
            try {
                $answer = Invoke-DbLicense -ArgumentList @('request-code') -TimeoutSeconds 5
                $line = ([string]$answer.StdOut).Trim()
                if ($answer.ExitCode -eq 0 -and $line -cmatch '^ARCIREQ1-[0-9A-Z-]+$') { $requestCode = $line }
                else { $licenseProblem = Get-DbLicenseProblem $answer }
            }
            catch { $licenseProblem = $_.Exception.Message }
        }
    }
    if ($licenseState -eq 'licensed') {
        Write-Host ('   6. Lisans [licence]: lisanslı [licensed]: {0}' -f $licensee)
    }
    elseif ($requestCode) {
        if ($licenseState -eq 'trial') { $head = 'deneme süresi 14 gündür [14-day trial]. İstek kodu [request code]:' }
        elseif ($licenseState -eq 'trial_expired') { $head = 'deneme süresi doldu [trial over]. İstek kodu [request code]:' }
        elseif ($licenseState) { $head = 'lisans durumu [licence state] {0}. İstek kodu [request code]:' -f $licenseState }
        else { $head = 'İstek kodu [request code]:' }
        Write-Host ('   6. Lisans [licence]: ' + $head)
        Write-Host ('      ' + $requestCode)
        foreach ($line in $mail) { Write-Host $line }
    }
    else {
        $licenseProblem = ([string]$licenseProblem -replace '\s+', ' ').Trim()
        Write-Host ('   6. Lisans [licence]: istek kodu alınamadı [request code not available] ({0}); daha sonra yönetici PowerShell''inde çalıştırın [run it later in an administrator PowerShell]:' -f $licenseProblem)
        Write-Host ('      & "{0}" license request-code --config "{1}"' -f $DbExe, $ConfigPath)
        foreach ($line in $mail) { Write-Host $line }
    }
    if ($Mode -eq 'upgrade') {
        Write-Host ('  Önceki sürüm [previous version]: {0} (geri almak için [to roll back]: -Rollback)' -f $PreviousDir)
    }
}

function Show-SetupToken {
    <#
    The one-time setup token of the first administrator (arcilogger-api setup-token), on the
    console only. The indented token line goes to the console handle ([Console]::Out), past the
    PowerShell host: Write-Host output is recorded by a transcript or a redirection started outside
    the installer (Start-Transcript, transcription by Group Policy, *>, 6>, Tee-Object), the
    console handle is not. The text around it stays Write-Host (the console host writes Unicode,
    whatever the console code page). Hosts without a console window (the ISE) would show nothing
    there: no token is created then, the command is printed instead. Exit code 4: an
    administrator exists already (a reinstallation over a kept database), no token is needed.
    #>
    if (-not (Test-Path -LiteralPath $ApiExe)) { return }
    $command = '& "{0}" setup-token --config "{1}"' -f $ApiExe, $ConfigPath
    Write-Host ''
    Write-Host '==> İlk yönetici [first administrator]' -ForegroundColor Green
    if ($Host.Name -ne 'ConsoleHost') {
        Write-Host ('Kurulum anahtarı yalnızca bir konsol penceresinde gösterilir; yönetici PowerShell konsolunda çalıştırın [the setup token is shown in a console window only; run in an administrator PowerShell console]: {0}' -f $command)
        return
    }
    $result = Invoke-ArciProcess -FilePath $ApiExe -ArgumentList @('setup-token', '--config', $ConfigPath) -AllowFailure
    if ($result.StdOut.Trim()) {
        foreach ($line in ($result.StdOut.TrimEnd() -split '\r?\n')) {
            if ($line -match '^\s+\S') {
                [Console]::Out.WriteLine($line)
                [Console]::Out.Flush()
            }
            else { Write-Host $line }
        }
    }
    if ($result.ExitCode -eq 0) {
        Write-Host ('Tarayıcıda https://{0}/setup adresini açın (bu bilgisayarda: https://localhost/setup); anahtarı, kullanıcı adını ve parolayı girin [open it in a browser and enter the token, a user name and a password].' -f $ServerAddress)
        Write-Host 'Anahtar kurulum günlüğüne, plana ve PowerShell kayıtlarına (transcript, yönlendirme) yazılmadı, yalnızca bu pencerede görünür [not in the install log, the plan or a PowerShell transcript or redirection; shown in this window only].'
        Write-Host ('Bu pencerenin çıktısını kaydeden bir araç varsa yeni anahtar alın, eskisi geçersiz olur [if a tool records this window, get a new token; the old one stops working]: {0}' -f $command)
    }
    elseif ($result.ExitCode -eq 4) {
        Write-Host 'Kurulum anahtarı gerekmiyor: bu veritabanında bir yönetici hesabı zaten var; o hesapla giriş yapın, kimse giriş yapamıyorsa: create-admin (docs\KURULUM.md bölüm 9) [an administrator exists already: sign in with it, or recover with create-admin].'
    }
    else {
        if ($result.StdErr.Trim()) { Write-Host $result.StdErr.TrimEnd() }
        Write-Host ('Kurulum anahtarı alınamadı (çıkış kodu {0}) [no setup token]; sorunu giderip yönetici PowerShell''inde çalıştırın [fix it and run]: {1}' -f $result.ExitCode, $command) -ForegroundColor Yellow
    }
}

function Read-KamuSmCredentials {
    <#
    Optional: the Kamu SM customer number and password, straight into the encrypted secret store
    on stdin of "arcilogger-core secret set" (as database.password; *** in any output), asked only
    while neither is stored. The customer number is typed visibly (Read-Host), the password hidden
    (-AsSecureString); neither is kept after the two calls.
    #>
    if (-not (Test-Path -LiteralPath $CoreExe)) { return }
    $listed = Invoke-ArciProcess -FilePath $CoreExe -ArgumentList @('--config', $ConfigPath, 'secret', 'list') -AllowFailure
    if ($listed.ExitCode -ne 0 -or $listed.StdOut -match '(?m)^kamusm\.(customer|password)\b') { return }
    $later = '    Atlandı [skipped]; daha sonra [later]: docs\KURULUM.md bölüm 10 (secret set kamusm.customer, kamusm.password).'
    Write-Host ''
    $answer = [string](Read-Host 'Kamu SM müşteri numarası ve parolasını şimdi girmek ister misiniz? [E/h] [store the Kamu SM customer number and password now?]')
    if ($answer.Trim() -match '^(h|n)') { Write-Host $later; return }
    $customer = ([string](Read-Host 'Kamu SM müşteri numarası [customer number]')).Trim()
    $secure = $null
    $password = $null
    try {
        if ($customer) { $secure = Read-Host -AsSecureString 'Kamu SM parolası, görünmez [password, hidden]' }
        if ($null -ne $secure -and $secure.Length -gt 0) {
            $password = (New-Object System.Management.Automation.PSCredential -ArgumentList 'kamusm', $secure).GetNetworkCredential().Password
        }
        if (-not $customer -or -not $password) { Write-Host $later; return }
        $stored = 0
        foreach ($pair in @(@('kamusm.customer', $customer), @('kamusm.password', $password))) {
            $result = Invoke-ArciProcess -FilePath $CoreExe -ArgumentList @('--config', $ConfigPath, 'secret', 'set', $pair[0]) -StdinText ($pair[1] + "`n") -Redact @($customer, $password) -AllowFailure
            if ($result.StdOut.Trim()) { Write-Host ('    ' + $result.StdOut.Trim()) }
            if ($result.ExitCode -ne 0) { Write-Host ('    {0}: çıkış kodu {1} [exit code]: {2}' -f $pair[0], $result.ExitCode, $result.StdErr.Trim()) -ForegroundColor Yellow }
            else { $stored++ }
        }
        # Each "secret set" replaced secrets.json: ArciAdmin's entry back on it (contract 2.2).
        if ($stored -gt 0) { Restore-ConfigFileAccess }
        if ($stored -eq 2) {
            Write-Host '    Bağlantı testinden önce Kamu SM kök sertifikalarını koyun, sonra credit ve tsa-test (docs\KURULUM.md bölüm 10) [roots first, then credit and tsa-test].'
        }
    }
    finally {
        $customer = $null
        $password = $null
        $secure = $null
    }
}

function Complete-FirstInstall {
    <#
    The end of a first installation, run by Stop-Installer after Stop-Transcript: nothing here
    reaches the installer's transcript or the plan (already written), and the token line also
    bypasses the PowerShell host. Only when a person sits at the console
    (Test-ArciInteractiveConsole): a token printed to a redirected output would land in whatever
    captures it, and nobody could answer the question. Never changes the exit code.
    #>
    if (-not (Test-ArciInteractiveConsole)) { return }
    try {
        if (-not $SkipDatabase) { Show-SetupToken }
        Read-KamuSmCredentials
    }
    catch { Write-Host ('Kurulum sonu adımı yapılamadı [final step failed]: {0}' -f $_.Exception.Message) -ForegroundColor Yellow }
}

# --- preflight ---------------------------------------------------------------------------------------

function Get-PortOwners([string]$Protocol, [int]$Port) {
    $owners = New-Object System.Collections.Generic.List[object]
    $endpoints = @()
    if ($Protocol -eq 'UDP' -and (Get-Command Get-NetUDPEndpoint -ErrorAction SilentlyContinue)) {
        $endpoints = @(Get-NetUDPEndpoint -LocalPort $Port -ErrorAction SilentlyContinue)
    }
    elseif ($Protocol -eq 'TCP' -and (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
        $endpoints = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    }
    foreach ($endpoint in $endpoints) {
        if ($null -eq $endpoint) { continue }
        $process = Get-Process -Id $endpoint.OwningProcess -ErrorAction SilentlyContinue
        $path = $null
        $name = 'pid ' + $endpoint.OwningProcess
        if ($process) {
            $name = $process.ProcessName
            try { $path = $process.Path } catch { $path = $null }
        }
        $owners.Add([pscustomobject]@{ Pid = $endpoint.OwningProcess; Name = $name; Path = $path })
    }
    , $owners.ToArray()
}

function Get-RememberedAdminAddress {
    <# The admin addresses Set-ArciFirewall.ps1 remembers in its state file (none: nothing). #>
    if (-not (Test-Path -LiteralPath $FirewallStatePath)) { return }
    $saved = $null
    try { $saved = Read-JsonFile $FirewallStatePath } catch { Write-Verbose 'firewall state unreadable' }
    if ($null -ne $saved -and $saved -isnot [System.Array] -and $saved.PSObject.Properties.Name -contains 'admin_address') {
        @($saved.admin_address | Where-Object { $_ })
    }
}

function Get-EffectiveAdminAddress {
    <# -AdminAddress when given, else the addresses typed at the console, else the remembered ones; there is no default. #>
    if ($script:AdminAddressGiven) { return @($AdminAddress | Where-Object { $_ }) }
    if (@($script:PromptedAdminAddress).Count -gt 0) { return $script:PromptedAdminAddress }
    @(Get-RememberedAdminAddress)
}

function Invoke-FirewallPreflight {
    <#
    Everything the firewall step could refuse is checked here (the [[sources]] addresses in
    Invoke-SourcesPreflight), before any change: a refusal in the middle of an upgrade would
    otherwise leave a half-changed system behind.
    #>
    $admins = @(Get-EffectiveAdminAddress)
    $invalid = @($admins | Where-Object { -not (Test-ArciAddressSpec $_) })
    if ($admins.Count -eq 0) {
        Add-Check 'admin-address' 'fail' 'Yönetici PC''sinin adresi gerekli [admin PC address required]: güvenlik duvarı 443 (web) ve 3389''u (uzak masaüstü) yalnızca ona açar. -AdminAddress ile verin, örnek -AdminAddress 10.0.0.10 ya da bir ağ: -AdminAddress 10.0.0.0/24; değer hatırlanır [the firewall opens 443 and 3389 only for it: give -AdminAddress, e.g. 10.0.0.10 or a network such as 10.0.0.0/24; it is remembered]'
    }
    elseif ($invalid.Count -gt 0) {
        Add-Check 'admin-address' 'fail' ("-AdminAddress geçersiz [invalid]: '{0}' (örnek 10.0.0.10 veya 10.0.0.0/24)" -f ($invalid -join "', '"))
    }
    else {
        Add-Check 'admin-address' 'ok' ("443/3389 yalnızca [only from] {0}" -f ($admins -join ', '))
        if (-not $Force) {
            $foreign = @(Get-ArciForeignRdpSession -AdminAddress $admins)
            if ($foreign.Count -gt 0) {
                Add-Check 'rdp-session' 'fail' ("Açık uzak masaüstü oturumu [open RDP session] {0}: güvenlik duvarı bu oturumu keserdi. Adresi -AdminAddress'e ekleyin ya da -Force kullanın [would be cut off]." -f ($foreign -join ', '))
            }
        }
    }
    # The senders of 514 ([[sources]]) are checked by Invoke-SourcesPreflight.
}

function Invoke-SourcesPreflight {
    <#
    The log sources ([[sources]]) of the configuration in use. A new arcilogger.toml has none: it
    is the bundle's example, which lists the devices of its fictional network as comments only,
    so a first installation configures no device the organisation does not have. No source is a
    valid state, but no device is recorded yet: the firewall opens 514 to no sender (without the
    firewall step every sender becomes unknown-<ip>) and R15 watches nothing. That is a warning
    here and an instruction in the next steps (Show-NextSteps), never a stop. An existing
    configuration is read with tomllib when the installed venv is there. Without it (a new
    installation over the arcilogger.toml a removal kept: the program folder is gone, the
    configuration folder stays) the file is read with the narrow pattern of Get-ArciSourceTables,
    like the example a new file is made from. With the firewall step an invalid sender address
    stops the run here, before any change (Set-ArciFirewall.ps1 would refuse it halfway).
    #>
    $existing = Test-Path -LiteralPath $ConfigPath
    $unread = 0   # [[sources]] whose address the narrow pattern does not see
    if ($existing -and (Test-Path -LiteralPath $VenvPython)) {
        try { $senders = @(Get-ArciSourceAddresses -Config (Read-ArciTomlFile -Python $VenvPython -ConfigPath $ConfigPath)) }
        catch {
            Add-Check 'sources' 'fail' ("arcilogger.toml okunamadı [unreadable]: {0}" -f $_.Exception.Message)
            return
        }
        $script:SourceCount = $senders.Count
    }
    else {
        # No Python yet: the [[sources]] tables of the kept file, or of the bundle's example (none:
        # the tests check it). Commented-out lines do not count.
        try {
            $path = $(if ($existing) { $ConfigPath } else { Get-BundleFile 'config/arcilogger.example.toml' })
            $tables = Get-ArciSourceTables -Text ([System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8))
        }
        catch {
            $what = $(if ($existing) { 'arcilogger.toml okunamadı [unreadable]' } else { 'Örnek yapılandırma okunamadı [example configuration unreadable]' })
            Add-Check 'sources' 'fail' ("{0}: {1}" -f $what, $_.Exception.Message)
            return
        }
        $senders = @($tables.Addresses)
        $script:SourceCount = $tables.Tables
        $unread = $tables.Tables - $senders.Count
    }
    $script:SourceSenders = @($senders)
    if ($script:SourceCount -eq 0) {
        $where = $(if ($existing) { '{0} içinde [[sources]] yok [no [[sources]] in it]' -f $ConfigPath } else { 'yeni arcilogger.toml [[sources]] olmadan oluşturulur; örnekteki cihazlar yalnızca yorum satırıdır [the new arcilogger.toml has no [[sources]]; the example''s devices are comments only]' })
        $effect = $(if ($SkipFirewall) { 'her gönderici bilinmeyen gönderici sayılır (unknown-<ip>) ve hiçbir kaynak sessizlik için izlenmez (R15) [every sender counts as unknown, no source is watched for silence]' } else { 'güvenlik duvarı 514''ü hiçbir göndericiye açmaz, hiçbir log kaydedilmez [the firewall opens 514 to no sender, no log is recorded]' })
        Add-Check 'sources' 'warn' ("Log kaynağı yok [no log source]: {0}. Güvenlik duvarınızın ve ana yönlendiricinizin [[sources]] kayıtlarını ekleyip kurulumu yeniden çalıştırın [add the [[sources]] of your firewall and core router and run the installer again] (docs\DEVICE-SETUP.md bölüm 4); o zamana kadar {1}." -f $where, $effect)
        return
    }
    if ($existing -and -not $SkipFirewall) {
        $bad = @($senders | Where-Object { -not (Test-ArciAddressSpec $_) })
        if ($bad.Count -gt 0) {
            Add-Check 'sources' 'fail' ("[[sources]] address geçersiz [invalid]: {0}" -f ($bad -join ', '))
            return
        }
        $text = "514 yalnızca [only from] {0} gönderici [senders]" -f $senders.Count
        if ($unread -gt 0) {
            $text += " ({0} kaynağın adresi Python olmadan okunamadı, kurulum sırasında denetlenir [{0} source address(es) not readable without Python, checked during the installation])" -f $unread
        }
        Add-Check 'sources' 'ok' $text
        return
    }
    Add-Check 'sources' 'ok' ("Log kaynakları [log sources]: {0}" -f $script:SourceCount)
}

function Invoke-LicensePreflight {
    <#
    The maintenance rule (check "license", install and upgrade): a bundle released after the
    maintenance date of the licence stops the run before any change, unless
    -AllowUncoveredUpgrade (then the paid modules are off until a covering licence is installed;
    the 5651 functions always run). The licence is -LicenseFile, else the installed one as the
    product's status file (<data_dir>\state\license-status.json, states licensed and
    version_not_covered) reports it. Both are read without Python and without trusting them:
    the rule is advisory, the product enforces it at run time (version_not_covered). An
    unreadable -LicenseFile or a bundle without release_date stops the run as well
    ($script:LicenseStop, also in a dry run, before any change is planned).
    #>
    if (-not $script:ReleaseDate) {
        $text = 'Paket yayın tarihi yok [bundle has no release_date]'
        Add-Check 'license' 'fail' $text
        $script:LicenseStop = $text
        return
    }
    $version = [string]$script:Manifest.version
    $until = $null
    $origin = $null
    $statusRead = $false
    $statusProblem = $null
    if ($LicenseFile) {
        try { $script:LicenseInfo = Read-ArciLicenseFile -Path $LicenseFile }
        catch {
            $text = 'Lisans dosyası okunamadı [licence file unreadable]: {0}' -f $_.Exception.Message
            Add-Check 'license' 'fail' $text
            $script:LicenseStop = $text
            return
        }
        $until = $script:LicenseInfo.MaintenanceUntil
        $origin = '-LicenseFile {0}' -f $script:LicenseInfo.LicenseId
    }
    else {
        $statusPath = Join-Path $DataRoot 'state\license-status.json'
        $status = $null
        try { $status = Read-ArciLicenseStatus -Path $statusPath }
        catch { $statusProblem = '{0}: {1}' -f $statusPath, $_.Exception.Message }
        $statusRead = ($null -ne $status)
        if ($statusRead -and @('licensed', 'version_not_covered') -ccontains $status.Status) {
            $until = $status.MaintenanceUntil
            $origin = '{0} ({1})' -f $(if ($status.LicenseId) { $status.LicenseId } else { 'license-status.json' }), $status.Status
        }
    }
    if ($null -eq $until) {
        $text = 'Kurulu lisans yok; lisans durumu kurulumdan sonra geçerli [no licence installed]'
        if ($statusProblem) { $text += (' (lisans durum dosyası okunamadı [licence status file unreadable]: {0})' -f $statusProblem) }
        # An upgrade of an installation without a licence (no -LicenseFile, no readable status
        # file): sealed days older than the trial mean the trial counts as over at once. Usually a
        # version without licensing: it has no request code and no installation id before the
        # migration, so the warning sends the operator to the licence page after the upgrade. The
        # paid modules come back with the licence; usage.reattribute then rebuilds the missing
        # rollups of the last usage.auto_rebuild_days days (default 7, the installation may have
        # changed it, so the text says "default") by itself; older days need Yeniden hesapla.
        $oldest = $null
        if ($Mode -eq 'upgrade' -and -not $LicenseFile -and -not $statusRead) {
            try { $oldest = Get-ArciOldestVaultDay -VaultPath (Join-Path $DataRoot 'vault') } catch { $oldest = $null }
        }
        if ($oldest -and (Get-ArciDaysBetween -From $oldest -To (Get-ArciSiteToday)) -gt 14) {
            $warning = @(
                $text,
                'Bu kurulumun lisansı yok ve deneme süresi kasadaki eski mühürlü günler nedeniyle dolmuş sayılacak: Kullanım analizi, Zamanlanmış raporlar ve CSV içe aktarma kapanacak; etkin kullanıcı sayısı 3''ün üzerindeyse yeni kullanıcı eklenemez. 5651 işlevleri etkilenmez. Yükseltmeden sonra web arayüzünde Ayarlar → Lisans sayfası istek kodunu gösterir (kurulumun sonunda da yazılır); kodu lisans verene gönderin, gelen .lic dosyasını aynı sayfadan yükleyin. Lisans yüklenene kadar ücretli modüller kapalı kalır; lisans yüklenip Kullanım analizi açılınca son günlerin (varsayılan 7 gün, usage.auto_rebuild_days) kullanım özetleri kendiliğinden, daha eskileri Kullanım sayfasındaki Yeniden hesapla ile yeniden hesaplanır.',
                '[This installation has no licence and its trial counts as over because of older sealed days: usage analytics, scheduled reports and CSV import will be off; with more than 3 active users no new user can be added. 5651 functions are not affected. After the upgrade the web interface shows the request code under Settings → Licence (the end of the installation prints it too); send it to the licensor and upload the .lic you receive on the same page. Until the licence is installed the paid modules stay off; once it is installed and usage analytics is on, the usage rollups of the recent days (7 by default, usage.auto_rebuild_days) are rebuilt by themselves, older days with Recompute on the Usage page.]'
            ) -join [Environment]::NewLine
            Add-Check 'license' 'warn' $warning
        }
        else {
            Add-Check 'license' 'info' $text
        }
        return
    }
    $cover = Test-ArciMaintenanceCovers -ReleaseDate $script:ReleaseDate -MaintenanceUntil $until -Version $version
    if ($cover.Covered) {
        Add-Check 'license' 'ok' ('{0}: {1}' -f $origin, $cover.Text)
        return
    }
    if ($AllowUncoveredUpgrade) {
        Add-Check 'license' 'warn' ($cover.Text + [Environment]::NewLine + '-AllowUncoveredUpgrade: ücretli modüller kapanacak [paid modules will be off]')
        return
    }
    # Running the bundle that is installed already (a repair, or -Upgrade -Force) installs no
    # newer version: the rule warns only, so the running version can always be repaired.
    $installed = $null
    try { $installed = Get-InstalledVersion } catch { $installed = $null }
    if ($null -ne $installed -and $installed.PSObject.Properties['fingerprint'] -and $installed.fingerprint -eq $script:BundleInfo.Fingerprint) {
        Add-Check 'license' 'warn' ($cover.Text + [Environment]::NewLine + 'Aynı paket zaten kurulu: yalnızca onarım yapılıyor [the same bundle is installed: repair only]')
        return
    }
    Add-Check 'license' 'fail' $cover.Text
    $script:LicenseStop = $cover.Text
}

function Invoke-Preflight {
    Write-ArciStep 'Ön denetim [preflight]'
    $onWindows = Test-ArciWindows
    if ($onWindows) { Add-Check 'windows' 'ok' 'Windows' } else { Add-Check 'windows' 'fail' 'Windows gerekli [Windows required]' }
    if (Test-ArciAdministrator) { Add-Check 'admin' 'ok' 'Yönetici hakları [administrator]' }
    else { Add-Check 'admin' $(if ($script:IsDryRun) { 'warn' } else { 'fail' }) 'Yönetici olarak çalıştırın [run as Administrator]' }

    if ($PSVersionTable.PSVersion -ge [version]'5.1') { Add-Check 'powershell' 'ok' ("PowerShell {0}" -f $PSVersionTable.PSVersion) }
    else { Add-Check 'powershell' 'fail' 'PowerShell 5.1 veya üstü gerekli [5.1+ required]' }

    if ($onWindows -and (Get-Command Get-CimInstance -ErrorAction SilentlyContinue)) {
        $os = Get-CimInstance -ClassName Win32_OperatingSystem
        $editionId = $null
        try { $editionId = [string](Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -Name EditionID -ErrorAction Stop).EditionID }
        catch { $editionId = $null }
        $architecture = $null
        try {
            # Win32_Processor.Architecture is a number (0 x86, 5 ARM, 9 x64, 12 ARM64), not a
            # localised text. That it names the processor and not the emulated x64 of this
            # process on ARM64 is to be verified on such a machine (docs/ASSUMPTIONS.md I5).
            $processor = @(Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop)[0]
            switch ([int]$processor.Architecture) {
                0 { $architecture = 'x86' }
                5 { $architecture = 'ARM' }
                9 { $architecture = 'x64' }
                12 { $architecture = 'ARM64' }
                default { $architecture = [string]$processor.Architecture }
            }
        }
        catch { $architecture = $null }
        $script:OsInfo = [pscustomobject]@{
            ProductType = [int]$os.ProductType; Build = [int]$os.BuildNumber; EditionId = $editionId
            Architecture = $architecture; Caption = [string]$os.Caption
        }
        $script:IsClientOs = ($script:OsInfo.ProductType -eq 1)
        $support = Get-ArciOsSupport -ProductType $script:OsInfo.ProductType -Build $script:OsInfo.Build -EditionId $editionId `
            -Architecture $architecture -Caption $script:OsInfo.Caption -AllowUnsupportedWindows:$AllowUnsupportedWindows
        foreach ($check in $support) { Add-Check $check.Id $check.Status $check.Text }
        if ($script:IsClientOs -and ($Mode -eq 'install' -or $Mode -eq 'upgrade')) {
            if ($SkipPower) { Add-Check 'power' 'info' '-SkipPower: güç ayarlarına dokunulmuyor; bilgisayar uykuya geçerse o sırada gelen UDP syslog kaybolur [power settings unchanged: syslog sent while the computer sleeps is lost]' }
            else { Add-Check 'power' 'info' ('Masaüstü Windows: prize takılıyken uyku ve hazırda bekletme, Hızlı Başlatma kapatılır; önceki değerler {0} dosyasına yazılır ve kaldırmada geri gelir (-SkipPower: dokunma) [sleep, hibernation and Fast Startup off; restored on removal]' -f $PowerStatePath) }
        }
        $release = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full' -Name Release -ErrorAction SilentlyContinue)
        if ($release -and [int]$release.Release -ge 394254) { Add-Check 'dotnet' 'ok' ('.NET Framework 4.6.1+ (WinSW)') }
        else { Add-Check 'dotnet' 'fail' '.NET Framework 4.6.1 veya üstü gerekli (WinSW) [required]' }
        if (Test-Path -LiteralPath $TarExe) { Add-Check 'tar' 'ok' $TarExe } else { Add-Check 'tar' 'fail' ("tar.exe yok [missing]: {0}" -f $TarExe) }
        $timeZone = (Get-TimeZone).Id
        if ($timeZone -eq 'Turkey Standard Time') { Add-Check 'timezone' 'ok' $timeZone }
        elseif ($SkipTimeSetup) { Add-Check 'timezone' 'warn' ("Saat dilimi [time zone] {0}; görev saatleri yerel saattir, Set-ArciTime.ps1 çalıştırın" -f $timeZone) }
        else { Add-Check 'timezone' 'ok' ("{0} -> Turkey Standard Time (Set-ArciTime.ps1)" -f $timeZone) }
    }
    else {
        Add-Check 'os' 'unknown' 'İşletim sistemi denetlenemedi [OS not checked]'
    }
    if ($Mode -eq 'install' -or $Mode -eq 'upgrade') {
        Invoke-AddressPreflight
        Invoke-SetupPreflight
    }

    # Data drive: a fixed NTFS disk with enough space (the vault grows for at least two years).
    $root = [System.IO.Path]::GetPathRoot($DataRoot)
    $driveFacts = Get-ArciDriveFacts -Root $root
    $busType = $null
    if ($onWindows -and $null -ne $driveFacts -and $driveFacts.DriveType -eq 'Fixed') { $busType = Get-ArciDiskBusType -Root $root }
    # Assigned first: these helpers return their list as one object (, $list), which @(...) would wrap.
    $driveChecks = Get-ArciDataDriveChecks -Root $root -Drive $driveFacts -BusType $busType -SystemDrive $env:SystemDrive -MinFreeGB $MinFreeGB -Mode $Mode
    foreach ($check in $driveChecks) { Add-Check $check.Id $check.Status $check.Text }

    if ($SkipDatabase) {
        Add-Check 'postgresql' 'warn' 'Veritabanı adımları atlanıyor [-SkipDatabase]'
    }
    else {
        $service = Find-PostgresService
        if ($null -eq $service) {
            $help = @(
                'PostgreSQL 16 bulunamadı [not found]. Önce kurun [install it first]:',
                '  1. EDB PostgreSQL 16 Windows x64 yükleyicisi: https://www.enterprisedb.com/downloads/postgres-postgresql-downloads',
                ('  2. Yönetici olarak çalıştırın; port 5432, yerel ayar (locale) "C", veri klasörü {0}, Stack Builder gerekmez.' -f (Join-Path $DataRoot 'db')),
                '  3. postgres parolasını güvenli bir yerde saklayın (ARCILOGGER kaydetmez).',
                '  4. Bu betiği yeniden çalıştırın. Ayrıntı: docs\KURULUM.md, "PostgreSQL".'
            ) -join [Environment]::NewLine
            Add-Check 'postgresql' 'fail' $help
        }
        else {
            $script:PgBin = Get-PostgresBinDir $service
            $versionOk = ($service.Name -match '-16$' -or [string]$service.PathName -match '\\16\\bin\\')
            if (-not $versionOk) { Add-Check 'postgresql-version' 'warn' ("PostgreSQL 16 bekleniyordu [expected]: {0}" -f $service.Name) }
            if ($service.State -eq 'Running') { Add-Check 'postgresql' 'ok' ("{0} çalışıyor [running]; bin: {1}" -f $service.Name, $script:PgBin) }
            else { Add-Check 'postgresql' 'fail' ("{0} çalışmıyor [not running]: Start-Service {0}" -f $service.Name) }
            if (-not $script:PgBin -or -not (Test-Path -LiteralPath (Join-Path $script:PgBin 'psql.exe'))) {
                Add-Check 'psql' 'fail' ("psql.exe bulunamadı; -PgBinDir verin [psql.exe not found]: {0}" -f $script:PgBin)
            }
        }
    }

    # The API port ([api] port of arcilogger.toml): a foreign program on it would leave ArciApi
    # restarting in a loop and Caddy answering 502.
    $probes = @(@('UDP', 514), @('TCP', 514), @('TCP', 443))
    try {
        $apiEndpoint = Get-ApiEndpoint
        Add-Check 'api-endpoint' 'ok' ("API {0} (Caddy /api -> bu adres [this address])" -f $apiEndpoint.Address)
        # Caddy connects from the API's address; uvicorn believes the forwarded client address only
        # from [api] forwarded_allow_ips. Otherwise the audit log, the sessions and the per-IP
        # limits would record this server for every user, silently. A rollback only warns: it puts
        # back a version that worked and must not wait for a configuration change.
        switch ($apiEndpoint.ProxyTrust) {
            'trusted' {
                Add-Check 'api-proxy' 'ok' ("[api] forwarded_allow_ips Caddy'nin adresini kapsıyor [covers Caddy's address] {0}" -f $apiEndpoint.HostName)
            }
            'unknown' {
                Add-Check 'api-proxy' 'warn' ("[api] host bir ad [is a name] ({0}): Caddy'nin bağlanacağı adres denetlenemedi; IP adresi yazın ve [api] forwarded_allow_ips'e ekleyin [use an IP address that forwarded_allow_ips covers]" -f $apiEndpoint.HostName)
            }
            default {
                Add-Check 'api-proxy' $(if ($Mode -eq 'rollback') { 'warn' } else { 'fail' }) ("[api] forwarded_allow_ips ('{0}') Caddy'nin bağlandığı {1} adresini kapsamıyor: API her isteği {1} adresinden gelmiş sayardı (denetim kaydı, oturumlar, IP başına sınırlar). {1} adresini forwarded_allow_ips'e ekleyin [does not cover the address Caddy connects from; every request would be recorded with it]" -f $apiEndpoint.ForwardedAllowIps, $apiEndpoint.HostName)
            }
        }
        if (@($probes | Where-Object { $_[0] -eq 'TCP' -and $_[1] -eq $apiEndpoint.Port }).Count -eq 0) {
            $probes += , @('TCP', $apiEndpoint.Port)
        }
    }
    catch {
        Add-Check 'api-endpoint' 'fail' ("[api] host/port okunamadı [unreadable]: {0}" -f $_.Exception.Message)
    }
    foreach ($probe in $probes) {
        $owners = Get-PortOwners $probe[0] $probe[1]
        $foreign = @($owners | Where-Object { -not ($_.Path -and ($_.Path.StartsWith($InstallDir + '\', [System.StringComparison]::OrdinalIgnoreCase))) })
        if (-not (Get-Command Get-NetUDPEndpoint -ErrorAction SilentlyContinue)) {
            Add-Check ('port-' + $probe[0].ToLowerInvariant() + $probe[1]) 'unknown' ("{0} {1} denetlenemedi [not checked]" -f $probe[0], $probe[1])
        }
        elseif ($foreign.Count -eq 0) {
            Add-Check ('port-' + $probe[0].ToLowerInvariant() + $probe[1]) 'ok' ("{0} {1} boş ya da ARCILOGGER'da [free or ours]" -f $probe[0], $probe[1])
        }
        else {
            Add-Check ('port-' + $probe[0].ToLowerInvariant() + $probe[1]) 'fail' ("{0} {1} kullanımda [in use]: {2}" -f $probe[0], $probe[1], (($foreign | ForEach-Object { "$($_.Name) (pid $($_.Pid))" }) -join ', '))
        }
    }

    if (-not $SkipFirewall -and ($Mode -eq 'install' -or $Mode -eq 'upgrade')) { Invoke-FirewallPreflight }
    if ($Mode -eq 'install' -or $Mode -eq 'upgrade') {
        Invoke-SourcesPreflight
        Invoke-AdminPolicyPreflight
        Invoke-ExecutablePreflight
        Invoke-LicensePreflight
    }

    if (-not $script:IsDryRun) {
        $defaultProgramData = if ($env:ProgramData) { Join-Path $env:ProgramData 'ARCILOGGER' } else { $null }
        if ($defaultProgramData -and $ProgramDataDir -ne $defaultProgramData.TrimEnd('\')) {
            Add-Check 'programdata' 'fail' ("Zamanlanmış görevler yapılandırmayı {0} içinde arar; -ProgramDataDir yalnızca deneme için [scheduled tasks read the default config path]" -f $defaultProgramData)
        }
    }
    # The data root record (Resolve-ArciDataRoot): a data_dir that differs from it, or a record
    # that cannot be used, stops the run; -DataRoot moves it deliberately.
    if ($script:DataRootResolution.Problem) {
        Add-Check 'data-root' 'fail' $script:DataRootResolution.Problem
    }
    elseif ($script:DataRootResolution.Replaces) {
        Add-Check 'data-root' 'warn' ("Kayıtlı veri klasörü [recorded data root] {0} -> {1} (-DataRoot, {2}); hizmetler ve görevler yalnızca yeni klasörü kabul eder [the services and tasks accept only the new folder]" -f $script:DataRootResolution.Replaces, $DataRoot, $DataRootRecord)
    }
    elseif ($script:DataRootSource -eq 'record') {
        Add-Check 'data-root' 'ok' ("Veri klasörü [data folder] {0} ({1})" -f $DataRoot, $DataRootRecord)
    }
    elseif (-not $script:DataRootFromConfig -and $Mode -eq 'install') {
        if ($script:DataRootSource -eq 'auto' -and $null -ne $script:DataRootChoice -and $script:DataRootChoice.Fallback) {
            Add-Check 'data-root' 'info' ("Veri klasörü otomatik seçildi [data folder chosen automatically]: {0} ({1}); başka bir disk için -DataRoot [another disk: -DataRoot]" -f $DataRoot, $script:DataRootChoice.Reason)
        }
        elseif ($script:DataRootSource -eq 'auto') {
            Add-Check 'data-root' 'ok' ("Veri klasörü otomatik seçildi [data folder chosen automatically]: {0} (sabit NTFS D: sürücüsü [fixed NTFS drive D:]; yeni arcilogger.toml'a yazılır)" -f $DataRoot)
        }
        else {
            Add-Check 'data-root' 'ok' ("Veri klasörü [data folder] {0} (yeni arcilogger.toml'a yazılır [written to the new config])" -f $DataRoot)
        }
    }
}

function Resolve-ServerIdentity {
    <#
    -ServerAddress and -ServerName for an installation or upgrade. A given parameter wins (and is
    remembered at the end of the run); then the values install\state.json remembers; then, for the
    address only, [collector] bind of an existing arcilogger.toml when it names one address (an
    installation from before the address was remembered); then the address of the only network
    adapter that is up and has an IPv4 default gateway (Select-ArciServerAddress). Nothing is
    guessed beyond that: the preflight stops and asks for -ServerAddress. -AdminAddress is not
    involved (Set-ArciFirewall.ps1 remembers it).
    #>
    $state = $null
    try { $state = Read-InstallState } catch { $state = $null }
    $remembered = @{}
    foreach ($key in @('server_address', 'server_name')) {
        if ($null -ne $state -and $state.PSObject.Properties.Name -contains $key -and $state.$key) { $remembered[$key] = [string]$state.$key }
    }
    if (-not $script:ServerAddressGiven) {
        if ($remembered.ContainsKey('server_address')) {
            $script:ServerAddress = $remembered['server_address']
            $script:ServerAddressSource = 'remembered'
        }
        else {
            $bind = $null
            try { $bind = Get-ConfiguredCollectorBind } catch { $bind = $null }
            $configured = Test-ArciBindAddress -Bind $bind
            if ($bind -and @('foreign', 'local') -contains $configured.Kind) {
                $script:ServerAddress = $configured.Bind
                $script:ServerAddressSource = 'config'
            }
            else {
                $script:ServerDetection = Select-ArciServerAddress
                $script:ServerAddress = $script:ServerDetection.Address
                $script:ServerAddressSource = $(if ($script:ServerAddress) { 'detected' } else { 'none' })
            }
        }
    }
    if (-not $script:ServerNameGiven) {
        if ($remembered.ContainsKey('server_name')) { $script:ServerName = $remembered['server_name'] }
        else { $script:ServerName = 'arcilogger.internal' }
    }
}

function Read-SetupAnswer([string]$Prompt) {
    <# One line typed at the console; $null when the console cannot be read. #>
    try { return [string](Read-Host $Prompt) }
    catch { return $null }
}

function Read-SiteNameAnswer {
    <# The organisation name typed at the console, asked again while it is not valid (three times at most); $null without one. #>
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $answer = Read-SetupAnswer 'Kuruluş adı; raporlarda, e-postalarda ve kanıt paketlerinde görünür [organisation name, shown in reports, e-mails and evidence packages]'
        if ($null -eq $answer) { return $null }
        $problem = Get-ArciSiteNameProblem -Value $answer
        if (-not $problem) { return $answer.Trim() }
        Write-Host ('    Kuruluş adı {0}' -f $problem)
    }
    $null
}

function Read-NtpServerAnswer {
    <# The local time source typed at the console: '' (Enter) for none; $null when the console cannot be read or no valid answer came. #>
    $prompt = 'Yerel saat kaynağı: ana yönlendiricinin ya da yerel bir NTP sunucusunun IP adresi veya adı; boş bırakılırsa yalnızca {0} kullanılır [local time source, usually the core router; empty: only {0}]' -f $script:FallbackServer
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $answer = Read-SetupAnswer $prompt
        if ($null -eq $answer) { return $null }
        $problem = Get-ArciTimeServerProblem -Value $answer
        if (-not $problem) { return $answer.Trim() }
        Write-Host ('    Geçersiz [invalid]: {0}' -f $problem)
    }
    $null
}

function Resolve-SetupValues {
    <#
    The organisation name (-SiteName), the local time source (-NtpServer) and the admin PC's
    address (-AdminAddress) of an installation or upgrade. A given parameter wins; then what this
    computer has: site_name and [clock] ntp_server of an existing arcilogger.toml (a new one is
    made from the bundle's example, which has neither) and the admin addresses
    Set-ArciFirewall.ps1 remembers. What is still missing is asked for when a person sits at the
    console (Test-ArciInteractiveConsole: the setup .exe's window, never CI or a redirected run);
    the time source only for a new configuration, an upgrade keeps what the file says. Nothing
    is refused here: Invoke-SetupPreflight and Invoke-FirewallPreflight check the result.
    #>
    $configExists = Test-Path -LiteralPath $ConfigPath
    $source = $(if ($configExists) { $ConfigPath } else { Get-BundleFile 'config/arcilogger.example.toml' })
    $configuredSite = $null
    $configuredNtp = $null
    try {
        $text = [System.IO.File]::ReadAllText($source, [System.Text.Encoding]::UTF8)
        $configuredSite = Get-ArciTomlString -Text $text -Key 'site_name'
        $configuredNtp = Get-ArciTomlString -Text $text -Section 'clock' -Key 'ntp_server'
        $fallback = Get-ArciTomlString -Text $text -Section 'clock' -Key 'fallback_server'
        if ($null -ne $fallback) { $script:FallbackServer = $fallback.Trim() }
    }
    catch {
        $script:ConfigReadError = '{0}: {1}' -f $source, $_.Exception.Message
        return
    }
    if (-not $script:SiteNameGiven -and $configExists -and ([string]$configuredSite).Trim()) {
        $script:SiteNameValue = $configuredSite.Trim()
        $script:SiteNameSource = 'config'
    }
    if (-not $script:NtpServerGiven -and $configExists) {
        $script:NtpServerValue = ([string]$configuredNtp).Trim()
        $script:NtpServerSource = 'config'
    }
    $askAdmin = -not $script:AdminAddressGiven -and -not $SkipFirewall -and @(Get-RememberedAdminAddress).Count -eq 0
    if (($script:SiteNameSource -ne 'none' -and $script:NtpServerSource -ne 'none' -and -not $askAdmin) -or -not (Test-ArciInteractiveConsole)) { return }
    Write-ArciStep 'Kurulum bilgileri [setup values]'
    if ($script:SiteNameSource -eq 'none') {
        $answer = Read-SiteNameAnswer
        if ($null -ne $answer) {
            $script:SiteNameValue = $answer
            $script:SiteNameSource = 'prompt'
        }
    }
    if ($script:NtpServerSource -eq 'none') {
        $answer = Read-NtpServerAnswer
        if ($null -ne $answer) {
            $script:NtpServerValue = $answer
            $script:NtpServerSource = 'prompt'
        }
    }
    if ($askAdmin) { $script:PromptedAdminAddress = @(Read-ArciAdminAddress) }
}

function Invoke-SetupPreflight {
    <#
    The organisation name and the time source (Resolve-SetupValues). Without a name the
    installation stops: reports, e-mails and evidence packages carry it. Without a local time
    source it only warns: Windows Time and the clock check then use [clock] fallback_server. The
    admin address is checked with the other firewall inputs (Invoke-FirewallPreflight).
    #>
    if ($script:ConfigReadError) {
        Add-Check 'setup-values' 'fail' ("site_name ve [clock] okunamadı [cannot be read]: {0}" -f $script:ConfigReadError)
        return
    }
    if ($script:SiteNameSource -eq 'none') {
        if (Test-Path -LiteralPath $ConfigPath) {
            Add-Check 'site-name' 'fail' ("Kuruluş adı yok [no organisation name]: {0} içinde site_name boş. -SiteName `"<kuruluş adı>`" ile verin ya da dosyaya yazın [site_name is empty: give -SiteName or set it in the file]" -f $ConfigPath)
        }
        else {
            Add-Check 'site-name' 'fail' 'Kuruluş adı gerekli [organisation name required]: raporlarda, e-postalarda ve kanıt paketlerinde görünür. İlk kurulumda -SiteName "<kuruluş adı>" ile verin [it appears in reports, e-mails and evidence packages: give -SiteName on a first installation]'
        }
    }
    else {
        $problem = Get-ArciSiteNameProblem -Value $script:SiteNameValue
        $origin = @{ parameter = '-SiteName'; config = 'arcilogger.toml'; prompt = 'girildi [entered]' }[$script:SiteNameSource]
        if ($problem) { Add-Check 'site-name' 'fail' ("Kuruluş adı geçersiz [invalid organisation name] ({0}): {1}" -f $origin, $problem) }
        else { Add-Check 'site-name' 'ok' ("Kuruluş adı [organisation name]: {0} ({1})" -f $script:SiteNameValue, $origin) }
    }
    $origin = @{ parameter = '-NtpServer'; config = '[clock] ntp_server'; prompt = 'girildi [entered]'; none = '-NtpServer' }[$script:NtpServerSource]
    $problem = Get-ArciTimeServerProblem -Value $script:NtpServerValue
    if ($problem) {
        Add-Check 'ntp-server' 'fail' ("Saat kaynağı geçersiz [invalid time source] ({0}) '{1}': {2}" -f $origin, $script:NtpServerValue, $problem)
    }
    elseif ($script:NtpServerValue) {
        Add-Check 'ntp-server' 'ok' ("Saat kaynağı [time source]: {0} ({1}); yedek [fallback]: {2}" -f $script:NtpServerValue, $origin, $(if ($script:FallbackServer) { $script:FallbackServer } else { '-' }))
    }
    elseif ($script:FallbackServer) {
        Add-Check 'ntp-server' 'warn' ("Yerel saat kaynağı ayarlanmamış [no local time source is set]: saat yalnızca {0} ile eşitlenir ve ölçülür. Ana yönlendiricinin ya da yerel bir NTP sunucusunun adresini -NtpServer ile verin [the clock follows only this public server: give the core router or a local NTP server with -NtpServer]" -f $script:FallbackServer)
    }
    else {
        Add-Check 'ntp-server' 'fail' 'Saat sunucusu yok [no time server]: [clock] ntp_server ve fallback_server boş; -NtpServer ile verin [both are empty: give -NtpServer]'
    }
}

function Invoke-AddressPreflight {
    <#
    The server address (given, remembered or detected) and the address the collector listens on:
    [collector] bind of the existing arcilogger.toml, or -ServerAddress, which a new
    arcilogger.toml gets as its bind. A bind that is not an address of this computer fails here:
    the collector would stop at once, be restarted in a loop and never receive a syslog message.
    #>
    $local = Get-ArciLocalAddresses
    # What to offer in the messages: this computer's IPv4 addresses (and 0.0.0.0 for a bind).
    $own = @($local | Where-Object { $_ -notmatch ':' -and -not $_.StartsWith('127.') -and -not $_.StartsWith('169.254.') })
    $offered = $(if ($own.Count -gt 0) { $own -join ', ' } else { '-' })
    $offeredBind = (@($own) + @('0.0.0.0')) -join ', '
    $addressValid = $false
    if ($script:ServerAddress) { $addressValid = ((Test-ArciBindAddress -Bind $script:ServerAddress).Kind -ne 'invalid') -and ($script:ServerAddress -ne '0.0.0.0') -and ($script:ServerAddress -ne '::') }
    switch ($script:ServerAddressSource) {
        'none' {
            $candidates = @()
            if ($null -ne $script:ServerDetection) { $candidates = @($script:ServerDetection.Candidates) }
            if ($candidates.Count -eq 0) {
                Add-Check 'server-address' 'fail' ("Sunucu adresi bulunamadı [server address not found]: varsayılan ağ geçidi olan, çalışan bir ağ bağdaştırıcısı yok. -ServerAddress <bu bilgisayarın IPv4 adresi> verin (bu bilgisayarda [on this computer]: {0}); değer hatırlanır [give -ServerAddress; it is remembered]" -f $offered)
            }
            else {
                Add-Check 'server-address' 'fail' ("Sunucu adresi kendiliğinden seçilemedi [server address not clear]: {0}. Cihazların syslog göndereceği adresi -ServerAddress ile verin; değer hatırlanır [give the address the devices send syslog to with -ServerAddress; it is remembered]" -f ($candidates -join '; '))
            }
            return
        }
        'detected' {
            $detected = $script:ServerDetection
            $text = "Sunucu adresi bulundu [server address detected]: {0} ({1}, ağ geçidi [gateway] {2}); hatırlanır, değiştirmek için -ServerAddress [remembered; change it with -ServerAddress]" -f $script:ServerAddress, $detected.Interface, $detected.Gateway
            if ($detected.Dhcp -eq $true) {
                Add-Check 'server-address' 'warn' ($text + ". Adres DHCP ile alınmış [assigned by DHCP]: değişirse cihazlar logları eski adrese gönderir ve hiçbiri kaydedilmez. Bu bilgisayara sabit bir IP verin ya da DHCP sunucusunda bu adresi rezerve edin [give it a static IP or a DHCP reservation]")
            }
            else { Add-Check 'server-address' 'ok' $text }
        }
        'remembered' { Add-Check 'server-address' 'ok' ("Sunucu adresi [server address] {0} (hatırlanan [remembered]: {1}; değiştirmek için -ServerAddress)" -f $script:ServerAddress, $StatePath) }
        'config' { Add-Check 'server-address' 'ok' ("Sunucu adresi [server address] {0} (arcilogger.toml [collector] bind)" -f $script:ServerAddress) }
        default { Add-Check 'server-address' 'ok' ("Sunucu adresi [server address] {0} (-ServerAddress; hatırlanır [remembered])" -f $script:ServerAddress) }
    }
    if (-not $addressValid) {
        Add-Check 'server-address-format' 'fail' ("-ServerAddress geçersiz [invalid]: '{0}'; bu bilgisayarın bir IP adresi gerekir (bu bilgisayarda [on this computer]: {1})" -f $script:ServerAddress, $offered)
        return
    }
    $configExists = Test-Path -LiteralPath $ConfigPath
    if ($configExists) {
        if ((Test-ArciBindAddress -Bind $script:ServerAddress -LocalAddress $local).Status -eq 'ok') { Add-Check 'address' 'ok' ("{0} bu bilgisayarın adresi [an address of this computer]" -f $script:ServerAddress) }
        else { Add-Check 'address' 'warn' ("{0} bu bilgisayarda yok [address not on this computer]: web adresi ve sertifika bu adrese yapılır; -ServerAddress ile düzeltin [fix it with -ServerAddress]" -f $script:ServerAddress) }
        try { $bind = Get-ConfiguredCollectorBind }
        catch {
            Add-Check 'collector-bind' 'fail' ("arcilogger.toml okunamadı [unreadable]: {0}" -f $_.Exception.Message)
            return
        }
    }
    else { $bind = $script:ServerAddress }
    $result = Test-ArciBindAddress -Bind $bind -LocalAddress $local
    switch ($result.Kind) {
        'wildcard' { Add-Check 'collector-bind' 'ok' ("Toplayıcı tüm adreslerde dinler [collector listens on all addresses] ({0}); 514'e yalnızca [[sources]] adresleri güvenlik duvarından geçer" -f $result.Bind) }
        'local' { Add-Check 'collector-bind' 'ok' ("Toplayıcı [collector] {0} adresinde dinler [listens on this address]" -f $result.Bind) }
        'loopback' {
            $fix = "-ServerAddress ile bu bilgisayarın adreslerinden birini verin [give one of this computer's addresses with -ServerAddress]: {0}" -f $offered
            if ($configExists) { $fix = "{0} dosyasında [collector] bind değerini şunlardan biri yapın [set it to one of]: {1}" -f $ConfigPath, $offeredBind }
            Add-Check 'collector-bind' 'warn' ("Toplayıcı yalnızca bu bilgisayarda dinler [collector listens on this computer only] ({0}): cihazlar log gönderemez [devices cannot reach it]; {1}" -f $result.Bind, $fix)
        }
        default {
            if ($configExists) {
                Add-Check 'collector-bind' 'fail' ("[collector] bind = `"{0}`" bu bilgisayarın adresi değil [not an address of this computer]: toplayıcı başlayamaz ve hiç syslog alınmaz. Düzeltme [fix]: {1} dosyasında [collector] bind değerini şunlardan biri yapın [set it to one of]: {2}; sonra kurulumu yeniden çalıştırın [then run the installer again]" -f $result.Bind, $ConfigPath, $offeredBind)
            }
            else {
                $origin = ''
                if ($script:ServerAddressSource -eq 'remembered') { $origin = (' ({0} içinde hatırlanan değer [remembered value])' -f $StatePath) }
                Add-Check 'collector-bind' 'fail' ("-ServerAddress {0}{1} bu bilgisayarın adresi değil [not an address of this computer]: yeni arcilogger.toml'a [collector] bind olarak yazılırdı, toplayıcı başlayamaz ve hiç syslog alınmaz. Düzeltme [fix]: kurulumu -ServerAddress <adres> ile yeniden çalıştırın, adres şunlardan biri [run again with -ServerAddress set to one of]: {2}" -f $result.Bind, $origin, $offered)
            }
        }
    }
}

function Invoke-BundleCheck {
    Write-ArciStep ("Paket denetleniyor [verifying bundle]: {0}" -f $BundleRoot)
    if (-not (Test-Path -LiteralPath (Join-Path $BundleRoot 'bundle-manifest.json'))) {
        Stop-Installer 2 ("Paket bulunamadı [bundle not found]: {0} (bundle-manifest.json yok)" -f $BundleRoot)
    }
    $script:BundleInfo = Test-ArciBundle -BundleRoot $BundleRoot
    foreach ($warning in $script:BundleInfo.Warnings) { Write-ArciWarn $warning; $script:Warnings.Add($warning) }
    if (-not $script:BundleInfo.Ok) {
        foreach ($problem in $script:BundleInfo.Errors) { $script:Failures.Add($problem); Write-Host ('    [HATA]  ' + $problem) -ForegroundColor Red }
        Add-PlanItem -Id 'bundle.verify' -Status 'fail' -Text 'Paket özetleri tutmuyor [bundle checksums do not match]' -Detail $script:BundleInfo.Errors
        Stop-Installer 2 'Paket bozuk ya da değiştirilmiş; kurulum yapılmadı [bundle damaged or modified, nothing installed].'
    }
    $script:Manifest = Read-JsonFile (Join-Path $BundleRoot 'bundle-manifest.json')
    # The release date (RELEASE_DATE of the build), not the build machine's clock (built_at):
    # what the licence's maintenance must cover. Missing: the licence preflight stops the run.
    $script:ReleaseDate = ConvertTo-ArciIsoDate (Get-ArciJsonField -Object $script:Manifest -Name 'release_date')
    $components = (Read-JsonFile (Join-Path $BundleRoot 'components.json')).components
    foreach ($name in @('python', 'winsw', 'caddy')) {
        $entry = Get-BundleComponent $name
        $expected = @($components | Where-Object { $_.name -eq $name })
        if ($expected.Count -ne 1 -or -not $expected[0].sha256 -or $expected[0].sha256 -ne $entry.sha256) {
            Stop-Installer 2 ("{0}: components.json ile paket özeti farklı [checksum differs from components.json]" -f $name)
        }
        if ((Get-ArciFileSha256 -Path (Get-BundleFile $entry.path)) -ne $entry.sha256) {
            Stop-Installer 2 ("{0}: dosya özeti tutmuyor [file checksum mismatch]" -f $name)
        }
    }
    Add-PlanItem -Id 'bundle.verify' -Status 'ok' -Text ("Paket {0}: {1} dosya, parmak izi [fingerprint] {2}" -f $script:Manifest.version, $script:BundleInfo.Files, $script:BundleInfo.Fingerprint)
    Write-ArciInfo ("Sürüm [version] {0}; {1} dosya doğrulandı [files verified]" -f $script:Manifest.version, $script:BundleInfo.Files)
    Write-ArciInfo ("Paket parmak izi [bundle fingerprint] (SHA-256 of SHA256SUMS): {0}" -f $script:BundleInfo.Fingerprint)
}

# --- modes -------------------------------------------------------------------------------------------

function Invoke-CommonTail {
    <#
    Everything after the program files. The order matters for upgrades: every step that can fail
    for a reason of its own (configuration, rights, services, time, firewall, tasks) runs BEFORE the
    database schema is migrated, so up to the migration a failed upgrade returns to the previous
    version without the database having changed. The migration is the last change.
    #>
    Invoke-ConfigSteps
    Invoke-AclSteps
    if (-not $SkipDatabase) {
        # Versions without the database package (arcilogger-db) need no database yet.
        Invoke-Step 'database.provision' 'PostgreSQL rolü ve veritabanı [role and database]' {
            if (Test-Path -LiteralPath $DbExe) {
                Invoke-DatabaseProvision
                # "secret set database.password" replaced secrets.json after Invoke-AclSteps.
                Restore-ConfigFileAccess
            }
            else {
                $message = 'arcilogger-db bu sürümde yok; veritabanı adımları atlandı [no database package in this version, database steps skipped].'
                Write-ArciWarn $message
                $script:Warnings.Add($message)
            }
        }
    }
    Invoke-Step 'services.install' 'Windows hizmetleri, sanal hesaplarla [Windows services, virtual accounts]' { Install-ArciServices } ($ServiceNames -join ', ')
    # The collector and Caddy need no database: they run again before the remaining steps and
    # the schema migration, so UDP syslog is lost only while the program itself is replaced. A
    # later failure stops them again with everything else (Restore-ArciRuntime).
    Invoke-Step 'services.start-collector' ('Toplayıcı ve web başlatılıyor [starting collector and web]: {0}' -f ($EvidenceServiceNames -join ', ')) {
        Start-ArciServices -Only $EvidenceServiceNames
    }
    if (-not $SkipTimeSetup) {
        Invoke-Step 'time.configure' 'Saat dilimi ve zaman eşitleme [time zone and time sync] (Set-ArciTime.ps1)' {
            $config = Read-ArciTomlFile -Python $VenvPython -ConfigPath $ConfigPath
            & (Join-Path $ScriptsDir 'Set-ArciTime.ps1') -Primary (Get-ArciConfigValue -Config $config -Path 'clock.ntp_server' -Default '') -Fallback (Get-ArciConfigValue -Config $config -Path 'clock.fallback_server' -Default 'time.ume.tubitak.gov.tr')
        }
    }
    if ($script:IsClientOs -and -not $SkipPower) {
        $powerDetail = [pscustomobject]@{
            state_file = $PowerStatePath; standby_timeout_ac = 0; hibernate_timeout_ac = 0
            hibernate = 'off'; hiberboot_enabled = 0; network_adapter_power_saving = 'off'
        }
        Invoke-Step 'power.configure' 'Güç ayarları [power settings]: prize takılıyken uyku ve hazırda bekletme yok, Hızlı Başlatma kapalı (Set-ArciPower.ps1)' {
            # Worth a warning, not a rollback of the installation: the operator can apply it later.
            try { & (Join-Path $ScriptsDir 'Set-ArciPower.ps1') -StateFile $PowerStatePath }
            catch {
                $message = 'Güç ayarları uygulanamadı [power settings not applied]: {0}. Elle [manually]: powershell -ExecutionPolicy Bypass -File "{1}"' -f $_.Exception.Message, (Join-Path $InstallDir 'scripts\Set-ArciPower.ps1')
                Write-ArciWarn $message
                $script:Warnings.Add($message)
            }
        } $powerDetail
    }
    if (-not $SkipFirewall) {
        # -StrictOutbound is passed only when given: otherwise Set-ArciFirewall.ps1 keeps the mode
        # recorded in its state file, so an upgrade never switches strict mode off silently.
        $firewall = @{ ConfigPath = $ConfigPath; Python = $VenvPython; StateFile = $FirewallStatePath; Force = [bool]$Force }
        if ($script:StrictOutboundGiven) { $firewall['StrictOutbound'] = [bool]$StrictOutbound }
        # The admin address likewise: passed on when given or typed in this run.
        if ($script:AdminAddressGiven -or @($script:PromptedAdminAddress).Count -gt 0) { $firewall['AdminAddress'] = @(Get-EffectiveAdminAddress) }
        $detail = [pscustomobject]@{
            state_file      = $FirewallStatePath
            strict_outbound = $(if ($firewall.ContainsKey('StrictOutbound')) { $firewall['StrictOutbound'] } else { 'kept' })
            admin_address   = @(Get-EffectiveAdminAddress)
        }
        Invoke-Step 'firewall.apply' ("Güvenlik duvarı [firewall]: 514 yalnızca [[sources]], 443/3389 yalnızca {0}, diğer gelen trafik kapalı [all other inbound blocked]" -f (@(Get-EffectiveAdminAddress) -join ', ')) {
            & (Join-Path $ScriptsDir 'Set-ArciFirewall.ps1') @firewall
        } $detail
    }
    Invoke-Step 'tasks.register' 'Zamanlanmış görevler [scheduled tasks] \ARCILOGGER\' {
        Save-TaskChoices
        $script:TasksRegistered = $true
        Register-ArciTasksKeepingChoices -Root $InstallDir
    }
    if (-not $SkipDatabase) {
        Invoke-Step 'database.upgrade' 'Veritabanı şeması [database schema]: arcilogger-db upgrade' {
            if (Test-Path -LiteralPath $DbExe) {
                $script:MigrationStarted = $true
                Invoke-ArciExe -FilePath $DbExe -ArgumentList @('upgrade', '--config', $ConfigPath)
            }
        }
    }
    if ($LicenseFile) {
        # Right after the migration (the licence tables exist then). Never a failure of the
        # installation: Install-LicenseFile turns every problem into a warning.
        $licenseText = 'Lisans dosyası [licence file] {0}' -f $(if ($script:LicenseInfo) { $script:LicenseInfo.LicenseId } else { $LicenseFile })
        $licenseDetail = [pscustomobject]@{
            file              = $LicenseFile
            license_id        = $(if ($script:LicenseInfo) { $script:LicenseInfo.LicenseId } else { $null })
            maintenance_until = $(if ($script:LicenseInfo) { $script:LicenseInfo.MaintenanceUntil } else { $null })
            valid_until       = $(if ($script:LicenseInfo) { $script:LicenseInfo.ValidUntil } else { $null })
        }
        if ($SkipDatabase) {
            Add-PlanItem -Id 'license.install' -Status 'skipped' -Text $licenseText -Detail $licenseDetail
            $message = 'Lisans dosyası yüklenmedi [licence file not installed] (-SkipDatabase); veritabanı hazır olunca yönetici PowerShell''inde çalıştırın [run it in an administrator PowerShell once the database is ready]: {0}' -f (Get-LicenseInstallCommand)
            Write-ArciWarn $message
            $script:Warnings.Add($message)
        }
        else {
            Invoke-Step 'license.install' $licenseText { Install-LicenseFile } $licenseDetail
        }
    }
    # The database services after the migration (and the collector or web again if their early
    # start failed; running services are left alone).
    Invoke-Step 'services.start' 'Hizmetler başlatılıyor [starting services]' { Start-ArciServices }
    Invoke-Step 'health.check' 'Sağlık denetimi [health check]: hizmetler, API, https://localhost/api/v1/health' {
        Invoke-HealthCheck
        try { Test-JavaForZamane } catch { Write-ArciWarn ("Zamane denetlenemedi [not checked]: {0}" -f $_.Exception.Message) }
    }
}

function Restore-ArciRuntime {
    <#
    Bring back a working program in <InstallDir> with its services and scheduled tasks after a
    failed upgrade, repair or rollback. Every step is attempted even when an earlier one fails;
    the failed ones are returned so the operator sees exactly what is left to do.
      -SwapBack       the new program folder is set aside as <InstallDir>.failed-<time> and
                      <InstallDir>.previous becomes <InstallDir> again.
      -CollectorOnly  the database may have changed: ArciIngest, ArciApi and ArciWorker are set to
                      manual start and stay stopped. The collector, Caddy and the evidence jobs
                      need no database, so no syslog message is lost and sealing goes on.
      -PutBack        the database services as Get-ArciServiceState recorded them before a
                      database-only restore that changed nothing: they are put back so
                      (Restore-ArciServiceState) instead of all being started.
    #>
    param([switch]$SwapBack, [switch]$CollectorOnly, [hashtable]$PutBack)
    $problems = New-Object System.Collections.Generic.List[string]
    $step = {
        param([string]$Text, [scriptblock]$Action)
        try {
            # Shown, never returned: the function's only output is the list of problems.
            & $Action | Out-Host
            Write-ArciInfo ('[OK] ' + $Text)
        }
        catch {
            $problems.Add(('{0}: {1}' -f $Text, $_.Exception.Message))
            Write-ArciWarn ('{0}: {1}' -f $Text, $_.Exception.Message)
        }
    }
    if ($SwapBack) {
        # The folders can only be swapped while nothing runs from them.
        & $step 'Görevler duraklatıldı [tasks paused]' { Disable-ArciTasks }
        & $step 'Hizmetler durduruldu [services stopped]' { Stop-ArciServices }
        & $step 'Hizmet kayıtları kaldırıldı [service registrations removed]' { Remove-ArciServices }
        & $step ('Önceki sürüm geri getirildi [previous version restored]: {0}' -f $InstallDir) {
            if (-not (Test-Path -LiteralPath $PreviousDir)) { throw ('{0} yok [missing]' -f $PreviousDir) }
            if (Test-Path -LiteralPath $InstallDir) {
                $aside = '{0}.failed-{1}' -f $InstallDir, [DateTime]::UtcNow.ToString('yyyyMMddHHmmss')
                Rename-ArciFolder -LiteralPath $InstallDir -NewName (Split-Path -Leaf $aside)
                $script:FailedDir = $aside
                Write-ArciInfo ('Başarısız sürüm [failed version]: {0}' -f $aside)
            }
            Rename-ArciFolder -LiteralPath $PreviousDir -NewName (Split-Path -Leaf $InstallDir)
            $script:ProgramMoved = $false
        }
        # Before its services start: the previous version must load the configuration.
        & $step 'Yapılandırma önceki sürümle yükleniyor [the configuration loads with the previous version]' { Restore-ConfigForProgram }
        if ($script:ConfigRestored) {
            & $step 'Güvenlik duvarı ve saat geri konan yapılandırmayla [firewall and time follow the configuration put back]' { Sync-SystemAfterRollback }
        }
    }
    & $step 'Hizmetler kayıtlı [services registered]' { Install-ArciServices -MissingOnly }
    if ($script:TasksRegistered) {
        # The failed run registered its own task definitions: take those of the program in place.
        & $step 'Görevler yeniden kaydedildi [tasks registered again]' { Register-ArciTasksKeepingChoices -Root $InstallDir }
    }
    else {
        & $step 'Görevler yeniden etkin [tasks enabled again]' { Enable-ArciTasks }
    }
    if ($CollectorOnly) {
        & $step ('Durduruldu, elle başlatmaya alındı [stopped, set to manual start]: {0}' -f ($DatabaseServiceNames -join ', ')) {
            Stop-ArciServices -Only $DatabaseServiceNames
            Set-ArciServiceStartMode -Name $DatabaseServiceNames -Mode 'demand'
        }
        & $step 'ArciCollector ve ArciWeb başlatıldı [started]' { Start-ArciServices -Only @('ArciCollector', 'ArciWeb') }
    }
    elseif ($PutBack) {
        & $step ('Önceki durumlarına getirildi [put back as they were]: {0}' -f ($DatabaseServiceNames -join ', ')) { Restore-ArciServiceState -State $PutBack }
        & $step 'ArciCollector ve ArciWeb başlatıldı [started]' { Start-ArciServices -Only @('ArciCollector', 'ArciWeb') }
    }
    else {
        & $step 'Hizmetler başlatıldı [services started]' { Start-ArciServices }
    }
    & $step 'Toplayıcı çalışıyor [collector running]' {
        $collector = Get-Service -Name 'ArciCollector' -ErrorAction SilentlyContinue
        if ($null -eq $collector) { throw 'ArciCollector kayıtlı değil [not registered]' }
        if ($collector.Status -ne 'Running') { throw ('ArciCollector: {0}' -f $collector.Status) }
    }
    # One string per problem: call it as @(Restore-ArciRuntime ...).
    $problems.ToArray()
}

function Complete-FailedChange {
    <#
    A step failed after the system had been changed: bring back a working ARCILOGGER, say exactly
    which state it is in and what is left to do, record it in install\state.json and exit with 3.
    #>
    param([ValidateSet('upgrade', 'repair', 'rollback')][string]$Operation, [string]$Reason)
    Write-Host ''
    Write-Host ("HATA [error] ({0}): {1}" -f $Operation, $Reason) -ForegroundColor Red
    $swapBack = ($Operation -eq 'upgrade') -and $script:ProgramMoved
    if ($swapBack) { Write-Host 'Önceki sürüm geri getiriliyor [restoring the previous version] ...' -ForegroundColor Yellow }
    else { Write-Host 'Hizmetler ve görevler geri getiriliyor [bringing services and tasks back] ...' -ForegroundColor Yellow }
    # Before this version's services start again: they need this version's schema, and their
    # jobs would rewrite the lowered leases. Its "arcilogger-db upgrade" puts back both, or the
    # leases alone; a schema it cannot put back keeps the database services stopped.
    $schemaProblem = $(if ($Operation -eq 'rollback' -and $script:SchemaDowngraded) { Restore-DowngradedSchema })
    $leaseProblem = $(if ($Operation -eq 'rollback' -and $script:LeasesLowered -and -not $schemaProblem) { Restore-LoweredLeases })
    # A restore whose rename and post_restore are done left the pre-upgrade dump's database, which
    # the program in place runs on: a later step's failure keeps no database service stopped.
    $committed = $script:RestoreCommitted
    $restored = ($null -ne $committed) -and -not $committed.post_restore_due
    # A database-only restore (Invoke-DatabaseOnlyRestore) that failed before its first change, a
    # cancel or a refusal: the database is as it was, so the database services go back as they
    # were (issue #155). After a rollback's swap the previous program runs here: no such record.
    $putBack = ($Operation -eq 'rollback') -and ($null -ne $script:DatabaseServicesBefore) -and -not $script:RestoreChangeStarted
    $databaseUncertain = [bool]($script:MigrationStarted -or ($script:RestoreStarted -and -not $restored) -or $schemaProblem) -and -not $putBack
    # Put back, what install\state.json says of the database stays as it was too: a failed
    # upgrade's migration left it uncertain, and its pending_database_restore is kept (below).
    $recordedUncertain = $databaseUncertain
    if ($putBack) {
        $previousState = Read-InstallState
        $previousFailure = $(if ($previousState.PSObject.Properties['last_failure']) { $previousState.last_failure })
        $recordedUncertain = [bool]($previousFailure -and $previousFailure.PSObject.Properties['database_uncertain'] -and $previousFailure.database_uncertain)
    }
    $runtime = @{ SwapBack = $swapBack; CollectorOnly = $databaseUncertain }
    if ($putBack) { $runtime['PutBack'] = $script:DatabaseServicesBefore }
    $problems = @(Restore-ArciRuntime @runtime)
    if ($schemaProblem) { $problems += $schemaProblem }
    if ($leaseProblem) { $problems += $leaseProblem }
    $dump = $script:LastDump
    $failure = @{ last_failure = [pscustomobject]@{ operation = $Operation; reason = $Reason; at = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'); database_uncertain = $recordedUncertain; manual_steps = $problems } }
    # (Put back: a failed upgrade's pending_database_restore stays as it was too.)
    if (-not $putBack) { $failure['pending_database_restore'] = $(if ($databaseUncertain -and $Operation -eq 'upgrade') { $dump } else { $null }) }
    Write-InstallState $failure
    Write-Host ''
    if ($swapBack) { Write-Host ('Program: önceki sürüm geri getirildi [previous version is back]: {0}' -f $InstallDir) -ForegroundColor Yellow }
    # After a swap <InstallDir>\installer\ holds the previous version's installer (Get-RestoreRetryInstaller).
    $notThePrevious = '  {0} içindekini değil: o önceki sürümün betiği, geri yüklemesi yedeği canlı veritabanının üzerine yazar (pg_restore --clean) [not the one in {0}: it is the previous version''s, whose restore writes the dump over the live database (pg_restore --clean)].' -f (Join-Path $InstallDir 'installer')
    if ($committed) {
        # The restore is done but for what is said here: running it again would replace the
        # restored database once more (another copy's space, one more replaced database).
        Write-Host ("Veritabanı yedekten geri yüklendi [the database was restored from the dump]: {0}" -f $script:RestoreDump) -ForegroundColor Yellow
        Write-Host ("  Yerine geçilen veritabanı '{0}' adıyla saklandı (docs\YEDEKLEME.md bölüm 5) [the replaced database is kept as '{0}' (docs\BACKUP.md section 5)]." -f $committed.replaced)
        if ($committed.post_restore_due) {
            Write-Host ("  '{0}' içinde timescaledb_post_restore() çalışmadı: veritabanı TimescaleDB geri yükleme kipinde, işleri çalışmıyor; onu çalıştırın [timescaledb_post_restore() did not run in it: the database is in TimescaleDB restore mode, its jobs do not run; run it]:" -f $committed.database)
            Write-Host ('    psql -h {0} -p {1} -U {2} -d "{3}" -c "SELECT timescaledb_post_restore();"' -f $committed.host, $committed.port, $committed.user, $committed.database)
            Write-Host '  sonra veritabanı hizmetlerini otomatik başlatmaya alıp başlatın [then set the database services to automatic start and start them]:'
            Write-Host ('    foreach ($n in ''{0}'') {{ if (Get-Service $n -ErrorAction SilentlyContinue) {{ sc.exe config $n start= delayed-auto; Start-Service $n }} }}' -f ($DatabaseServiceNames -join ''', '''))
        }
        Write-Host '  Geri yüklemeyi yeniden çalıştırmayın: geri yüklenen veritabanını bir kez daha değiştirir ve bir kopya daha yer tutar [do not run the restore again: it would replace the restored database once more and take the space of another copy].'
    }
    if ($putBack) {
        # Only what Restore-ArciServiceState did: a service it could not put back is named, with
        # the command that does it.
        $notPutBack = @($script:ServicesNotPutBack)
        if ($notPutBack.Count -eq 0) {
            Write-Host 'Veritabanı değiştirilmedi; veritabanı hizmetleri önceki durumlarında [the database was not changed; the database services are as they were]:' -ForegroundColor Yellow
        }
        else {
            Write-Host ('Veritabanı değiştirilmedi; şu veritabanı hizmetleri önceki durumlarına getirilemedi [the database was not changed; these database services could not be put back as they were]: {0}' -f ($notPutBack -join ', ')) -ForegroundColor Yellow
            Write-Host '  Önceki durumları [as they were]:'
        }
        foreach ($name in $DatabaseServiceNames) {
            $before = $script:DatabaseServicesBefore[$name]
            if ($null -eq $before) { continue }
            $status = $(if ($before.Running) { 'çalışıyor', 'running' } else { 'durdurulmuş', 'stopped' })
            $start = $(switch ($before.StartType) { 'Automatic' { 'otomatik başlatma', 'automatic start' } 'Manual' { 'elle başlatma', 'manual start' } 'Disabled' { 'devre dışı', 'disabled' } default { $before.StartType, $before.StartType } })
            Write-Host ('  {0}: {1}, {2} [{3}, {4}]' -f $name, $status[0], $start[0], $status[1], $start[1])
        }
        if ($notPutBack.Count -gt 0) {
            $automatic = @($notPutBack | Where-Object { $script:DatabaseServicesBefore[$_].StartType -eq 'Automatic' })
            $manual = @($notPutBack | Where-Object { $automatic -notcontains $_ })
            Write-Host ('  Günlüklerine ({0}\<hizmet>.*.log) bakıp onları önceki durumlarına getirin [read their logs ({0}\<service>.*.log) and put them back as they were]:' -f $script:LogDir)
            if ($automatic.Count -gt 0) { Write-Host ('    foreach ($n in ''{0}'') {{ sc.exe config $n start= delayed-auto; Start-Service $n }}' -f ($automatic -join ''', ''')) }
            if ($manual.Count -gt 0) { Write-Host ('    Start-Service ''{0}''' -f ($manual -join ''', ''')) }
        }
        $retry = '    powershell -ExecutionPolicy Bypass -File "{0}" -Rollback -RestoreDatabase' -f (Get-RestoreRetryInstaller)
        if ($script:RestoreDump) { $retry += (' -DatabaseDump "{0}"' -f $script:RestoreDump) }
        # After a cancel there is no error to fix.
        if ($script:RestoreCancelled) { Write-Host '  Geri yüklemeyi bu sürümün kurulum betiğiyle yeniden çalıştırmak için [to run the restore again, with this version''s installer]:' }
        else { Write-Host '  Hatayı giderip geri yüklemeyi bu sürümün kurulum betiğiyle yeniden çalıştırın [fix the error and run the restore again with this version''s installer]:' }
        Write-Host $retry
    }
    if ($databaseUncertain) {
        if ($Operation -eq 'rollback' -and $script:RestoreStarted -and -not $committed -and -not $script:RestoreCurrentRestoring -and -not $schemaProblem) {
            # Refused or failed before the rename (the new database dropped, the current one out
            # of TimescaleDB's restore mode again): only the program may not match it.
            Write-Host 'Veritabanı değiştirilmedi; geri yükleme bitmeden veritabanı hizmetleri başlatılmaz [the database was not changed; the database services do not start before the restore is done].' -ForegroundColor Yellow
        }
        elseif (-not $committed) {
            Write-Host 'Veritabanı değiştirilirken hata oluştu; şema ya da içerik yarım kalmış olabilir [the database may be half-changed].' -ForegroundColor Yellow
        }
        Write-Host '  ArciCollector, ArciWeb ve zamanlanmış kanıt görevleri çalışıyor: syslog kaybı yok [collector, web and evidence jobs run: no syslog is lost].'
        Write-Host ('  {0} elle başlatmaya alındı ve durduruldu [set to manual start, stopped].' -f ($DatabaseServiceNames -join ', '))
        $installerPath = Join-Path $InstallDir 'installer\Install-Arcilogger.ps1'
        if ($Operation -eq 'upgrade' -and $dump) {
            # This version's installer: after the swap-back the one in <InstallDir>\installer\ is
            # the previous version's (Get-RestoreRetryInstaller).
            $swappedBack = $swapBack -and -not $script:ProgramMoved
            $restoreWith = $(if ($swappedBack) { Get-RestoreRetryInstaller } else { $installerPath })
            Write-Host '  Yükseltme öncesi yedeği bu sürümün kurulum betiğiyle geri yüklemek için [to restore the pre-upgrade dump, with this version''s installer]:'
            Write-Host ('    powershell -ExecutionPolicy Bypass -File "{0}" -Rollback -RestoreDatabase -DatabaseDump "{1}"' -f $restoreWith, $dump)
            if ($swappedBack) { Write-Host $notThePrevious }
            Write-Host '  ya da hatayı giderip yükseltmeyi yeniden çalıştırın [or fix the error and run -Upgrade again].'
        }
        elseif ($Operation -eq 'rollback' -and -not $committed) {
            # This version's installer: after the swap the one in <InstallDir>\installer\ is the
            # previous version's (Get-RestoreRetryInstaller).
            $retry = '    powershell -ExecutionPolicy Bypass -File "{0}" -Rollback -RestoreDatabase' -f (Get-RestoreRetryInstaller)
            if ($script:RestoreDump) { $retry += (' -DatabaseDump "{0}"' -f $script:RestoreDump) }
            Write-Host '  Hatayı giderip geri yüklemeyi bu sürümün kurulum betiğiyle yeniden çalıştırın [fix the error and run the restore again with this version''s installer]:'
            Write-Host $retry
            if ($script:RolledBackDir) { Write-Host $notThePrevious }
        }
        elseif ($Operation -ne 'rollback') {
            # (A rollback whose restore committed: what is left is said above, not a restore.)
            Write-Host ('  Hatayı giderip yeniden çalıştırın [fix the error and run again]: powershell -ExecutionPolicy Bypass -File "{0}"' -f $installerPath)
        }
    }
    if ($Operation -eq 'rollback' -and $script:SchemaDowngradeFailed) {
        # Issue #122: stopped before the swap, the previous version never runs on this schema.
        $installerPath = Join-Path $InstallDir 'installer\Install-Arcilogger.ps1'
        Write-Host 'Geri alma durduruldu: program değiştirilmedi, bu sürüm kurulu kaldı [the rollback stopped: the program was not swapped, this version stays installed].' -ForegroundColor Yellow
        if ($script:SchemaDowngradeExit -ne 2) {
            Write-Host '  -Rollback komutunu yeniden çalıştırın: şemanın indirilmesi kaldığı yerden sürer [run -Rollback again: the downgrade goes on where it stopped].'
            Write-Host '  Yine olmazsa veritabanını yükseltme öncesi yedekten geri yükleyin [if it keeps failing, restore the database from the pre-upgrade backup] (docs\YEDEKLEME.md bölüm 5, docs\BACKUP.md section 5):'
        }
        else {
            # The command refused the revision itself: running it again changes nothing.
            Write-Host '  Veritabanını yükseltme öncesi yedekten geri yükleyin [restore the database from the pre-upgrade backup] (docs\YEDEKLEME.md bölüm 5, docs\BACKUP.md section 5):'
        }
        Write-Host ('    powershell -ExecutionPolicy Bypass -File "{0}" -Rollback -RestoreDatabase' -f $installerPath)
    }
    if ($problems.Count -gt 0) {
        Write-Host ''
        Write-Host 'ELLE YAPILMASI GEREKENLER [manual action needed]:' -ForegroundColor Red
        foreach ($problem in $problems) { Write-Host ('  - ' + $problem) -ForegroundColor Red }
        Write-Host '  Ayrıntı [details]: docs\KURULUM.md, "Sorun giderme".' -ForegroundColor Red
    }
    $summary = if ($problems.Count -gt 0) { 'geri dönüş eksik, yukarıya bakın [recovery incomplete, see above]' }
    elseif ($databaseUncertain) { 'yalnızca toplayıcı ve web çalışıyor [only collector and web running]' }
    else { 'önceki durum geri getirildi [previous state restored]' }
    Stop-Installer 3 ("{0} başarısız [failed]: {1}." -f $Operation, $summary)
}

function Invoke-Install {
    $installed = Get-InstalledVersion
    $folderState = Get-ArciProgramFolderState -Path $InstallDir
    $repair = $false
    if ($installed) {
        if ($installed.fingerprint -eq $script:BundleInfo.Fingerprint) {
            $repair = $true
            Add-PlanItem -Id 'program.keep' -Status 'kept' -Text ("Aynı paket zaten kurulu, program dosyaları korunuyor [same bundle installed, program files kept]: {0}" -f $installed.version)
            Write-ArciInfo 'Aynı paket zaten kurulu; yapılandırma adımları yeniden uygulanıyor [re-applying configuration].'
        }
        else {
            Stop-Installer 2 ("Kurulu sürüm [installed] {0}; yeni paket [bundle] {1}: -Upgrade kullanın [use -Upgrade]." -f $installed.version, $script:Manifest.version)
        }
    }
    elseif ($folderState -eq 'partial') {
        Add-PlanItem -Id 'program.resume' -Status 'ok' -Text ("Yarım kalmış kurulum bulundu; program klasörü yeniden oluşturulacak [interrupted installation, program folder is rebuilt]: {0}" -f $InstallDir)
        Write-ArciInfo 'Yarım kalmış kurulum bulundu; kaldığı yerden sürüyor [resuming an interrupted installation].'
    }
    elseif ($folderState -eq 'foreign' -and -not $Force) {
        Stop-Installer 2 ("{0} boş değil ama ARCILOGGER kaydı yok; -Force ile üzerine kurun [not empty and not ARCILOGGER's, use -Force]." -f $InstallDir)
    }
    Invoke-Step 'dirs.create' 'Klasörler [folders]' { foreach ($folder in Get-DataDirectories) { [void](New-Item -ItemType Directory -Path $folder -Force) } } (Get-DataDirectories)
    if (-not $repair) {
        # The end of a successful first installation shows the setup token (Complete-FirstInstall).
        $script:FreshInstall = -not $script:IsDryRun
        if ($folderState -eq 'partial') {
            Invoke-Step 'program.clean-partial' ("Yarım kalan program klasörü siliniyor [removing the interrupted program folder]: {0}" -f $InstallDir) {
                Assert-SafeToDelete $InstallDir
                Remove-Item -LiteralPath $InstallDir -Recurse -Force
            } $InstallDir
        }
        Install-ProgramFiles
        Invoke-CommonTail
        return
    }
    # Repair: the same program, everything else applied again; on failure the services come back.
    try {
        Invoke-Step 'services.stop' 'Hizmetler durduruluyor [stopping services]' { Stop-ArciServices }
        Invoke-Step 'services.remove' 'Hizmet kayıtları yenileniyor [re-creating service registrations]' { Remove-ArciServices }
        Invoke-CommonTail
    }
    catch {
        if ($script:IsDryRun) { throw }
        Complete-FailedChange -Operation 'repair' -Reason $_.Exception.Message
    }
}

function Invoke-Upgrade {
    $installed = Get-InstalledVersion
    if (-not $installed) { Stop-Installer 2 ("Kurulu ARCILOGGER yok [nothing installed]: {0}" -f $InstallDir) }
    if ($installed.fingerprint -eq $script:BundleInfo.Fingerprint -and -not $Force) {
        Stop-Installer 2 ("Bu paket zaten kurulu [already installed] ({0}); yeniden uygulamak için -Upgrade olmadan çalıştırın." -f $installed.version)
    }
    if ((Get-ArciProgramFolderState -Path $PreviousDir) -eq 'foreign') {
        Stop-Installer 2 ("{0} ARCILOGGER'a ait değil; taşıyın ya da silin [not an ARCILOGGER folder, move it away]." -f $PreviousDir)
    }
    Add-PlanItem -Id 'upgrade.versions' -Status 'ok' -Text ("{0} -> {1}" -f $installed.version, $script:Manifest.version)
    if (-not $SkipDatabase) {
        # Before any change: no backup, no upgrade.
        try {
            Invoke-Step 'database.backup' ("Veritabanı yedeği [database backup] (pg_dump) -> {0}" -f $script:BackupDir) { Invoke-DatabaseBackup }
        }
        catch {
            if ($script:IsDryRun) { throw }
            Stop-Installer 2 ("Veritabanı yedeği alınamadı; hiçbir şey değiştirilmedi [database backup failed, nothing changed]: {0}" -f $_.Exception.Message)
        }
    }
    # Before any change too: the configuration the previous version loads (Restore-ConfigForProgram
    # puts it back when a rollback cannot load what the browser changed afterwards).
    try {
        Invoke-Step 'config.before-upgrade' ("Yapılandırmanın yükseltme öncesi kopyası [copy of the configuration before the upgrade]: {0}.before-upgrade-<UTC>" -f $ConfigPath) { Save-ConfigBeforeUpgrade }
    }
    catch {
        if ($script:IsDryRun) { throw }
        Stop-Installer 2 ("Yapılandırma kopyalanamadı; hiçbir şey değiştirilmedi [configuration not copied, nothing changed]: {0}" -f $_.Exception.Message)
    }
    try {
        Invoke-Step 'tasks.disable' 'Zamanlanmış görevler duraklatılıyor, çalışanlar bekleniyor [pausing scheduled tasks]' { Disable-ArciTasks }
        Invoke-Step 'services.stop' 'Hizmetler durduruluyor [stopping services]' { Stop-ArciServices }
        # The folder is moved while the services are still registered: if Windows refuses the move
        # (an open Explorer window, a virus scanner), nothing else has been changed yet.
        Invoke-Step 'program.move-previous' ("Önceki sürüm saklanıyor [keeping previous version]: {0}" -f $PreviousDir) {
            if (Test-Path -LiteralPath $PreviousDir) {
                Assert-SafeToDelete $PreviousDir
                Remove-Item -LiteralPath $PreviousDir -Recurse -Force
            }
            Rename-ArciFolder -LiteralPath $InstallDir -NewName (Split-Path -Leaf $PreviousDir)
            $script:ProgramMoved = $true
        }
        Invoke-Step 'services.remove' 'Hizmet kayıtları kaldırılıyor [removing service registrations]' { Remove-ArciServices }
        Invoke-Step 'dirs.create' 'Klasörler [folders]' { foreach ($folder in Get-DataDirectories) { [void](New-Item -ItemType Directory -Path $folder -Force) } }
        Install-ProgramFiles
        Invoke-CommonTail
    }
    catch {
        if ($script:IsDryRun) { throw }
        Complete-FailedChange -Operation 'upgrade' -Reason $_.Exception.Message
    }
}

function Invoke-DatabaseOnlyRestore {
    <#
    -Rollback -RestoreDatabase without a previous program folder, e.g. after an upgrade whose
    schema migration failed (the previous program is already back). The collector keeps running.
    #>
    Add-PlanItem -Id 'rollback.database-only' -Status 'ok' -Text 'Önceki program klasörü yok; yalnızca veritabanı geri yüklenecek [no previous program folder: database only]'
    try {
        # As they were (running, start type): a restore that fails before its first change (a
        # cancel, a missing dump, the locale or the space refusal) puts them back so.
        Invoke-Step 'services.stop' ("Veritabanı kullanan hizmetler durduruluyor [stopping the database services]: {0}" -f ($DatabaseServiceNames -join ', ')) {
            $script:DatabaseServicesBefore = Get-ArciServiceState -Name $DatabaseServiceNames
            Stop-ArciServices -Only $DatabaseServiceNames
        }
        # The program in place may not match the database (an upgrade whose migration failed): a
        # restore that fails after its first change keeps the database services stopped
        # (Complete-FailedChange), as after a rollback's swap.
        Invoke-Step 'database.restore' 'Veritabanı yedekten geri yükleniyor [restoring the dump]' { $script:RestoreStarted = $true; Invoke-DatabaseRestore } $(if ($DatabaseDump) { $DatabaseDump } else { 'pending_database_restore / last_dump' })
        Invoke-Step 'services.start-mode' 'Otomatik başlatma yeniden açılıyor [automatic start again]' { Set-ArciServiceStartMode -Name $DatabaseServiceNames -Mode 'delayed-auto' }
        Invoke-Step 'services.start' 'Hizmetler başlatılıyor [starting services]' { Start-ArciServices }
        Invoke-Step 'health.check' 'Sağlık denetimi [health check]' { Invoke-HealthCheck }
    }
    catch {
        if ($script:IsDryRun) { throw }
        Complete-FailedChange -Operation 'rollback' -Reason $_.Exception.Message
    }
}

function Test-PreviousLeaseRollbackModule([string]$Module) {
    <#
    Whether the program a rollback returns to has arcilogger_db\lease_rollback.py (-Module, in
    its venv where pip installed the wheel): the step and its restore came with FortiGate DHCP,
    so a version with the module reads those lines itself. Throws when the check cannot decide:
    the package's __init__.py must be seen first, else a missing or unreadable folder would pass
    for a version from before FortiGate DHCP.
    #>
    $package = Split-Path -Parent $Module
    if (-not (Test-Path -LiteralPath (Join-Path $package '__init__.py') -PathType Leaf)) {
        throw ("arcilogger_db paketi bulunamadı ya da okunamadı [package not found or not readable]: {0}" -f $package)
    }
    Test-Path -LiteralPath $Module -PathType Leaf
}

function Invoke-LeaseRollbackStep {
    <#
    Contract 2.2.1: a previous version from before FortiGate DHCP cannot read those lines, so
    while this version's program is still in place and PostgreSQL runs, "arcilogger-db leases
    prepare-rollback" sets every FortiGate lease to low confidence (derived rows only) and prints
    the count. A previous version that reads them too (its arcilogger_db has lease_rollback.py;
    review note round 7 (a), issue #81) needs no step: it would only give false lows until the
    next upgrade, so it is skipped. When the check cannot decide, the step runs (lowering is the
    safe side), and the plan's detail keeps why (previous_check). A failure does not stop the
    rollback, nor does -SkipDatabase: either is a warning that says what it means for the lookups.
    #>
    $module = Join-Path $PreviousDir 'venv\Lib\site-packages\arcilogger_db\lease_rollback.py'
    $found = $null
    $unsure = $null
    try { $found = Test-PreviousLeaseRollbackModule $module }
    catch { $unsure = $_.Exception.Message }
    $detail = [pscustomobject]@{ command = 'arcilogger-db leases prepare-rollback'; lowered = $null; previous_module = $module; previous_has_module = $found; previous_check = $unsure }
    $text = 'FortiGate kiraları düşük güvene indiriliyor [lowering FortiGate leases to low confidence] (arcilogger-db leases prepare-rollback)'
    $notLowered = 'FortiGate kiraları düşük güvene indirilemedi [FortiGate leases not lowered] ({0}). Geri alınan sürümdeki FortiGate zincirleri bir adresin önceki sahibini gösterebilir; geri alma sonrasındaki anlar için FortiGate-DHCP sorgularını yeniden yükseltmeden sonra yapın [FortiGate chains in the previous version may name a former holder of an address; make FortiGate-DHCP lookups of moments after the rollback after the re-upgrade].'
    if ($found) {
        # Also with -SkipDatabase: the restored version reads the FortiGate DHCP lines itself.
        $notNeeded = 'FortiGate kiraları düşük güvene indirilmiyor: geri alınan sürüm FortiGate DHCP kayıtlarını zaten okuyor [FortiGate leases not lowered: the restored version already reads FortiGate DHCP] ({0})' -f $module
        Add-PlanItem -Id 'leases.prepare-rollback' -Status 'skipped' -Text $notNeeded -Detail $detail
        Write-ArciStep $notNeeded
        return
    }
    if ($SkipDatabase) {
        Add-PlanItem -Id 'leases.prepare-rollback' -Status 'skipped' -Text ($text + ' (-SkipDatabase)') -Detail $detail
        $message = $notLowered -f '-SkipDatabase'
        Write-ArciWarn $message
        $script:Warnings.Add($message)
        return
    }
    Invoke-Step 'leases.prepare-rollback' $text {
        if ($unsure) { Write-ArciInfo ('Geri alınan sürümün FortiGate DHCP okuyup okumadığı anlaşılamadı, kiralar yine de indiriliyor [could not tell whether the restored version reads FortiGate DHCP: the leases are lowered anyway] ({0})' -f $unsure) }
        $result = $null
        $problem = $null
        # The command's own limits (lock 30 s, statement 10 min) end it first.
        try { $result = Invoke-ArciProcess -FilePath $DbExe -ArgumentList @('leases', 'prepare-rollback', '--config', $ConfigPath) -AllowFailure -TimeoutSeconds 900 }
        catch { $problem = $_.Exception.Message }
        if ($result -and $result.ExitCode -eq 0) {
            $script:LeasesLowered = $true
            $line = @(([string]$result.StdOut) -split '\r?\n' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) | Select-Object -Last 1
            if ($line) { Write-ArciInfo $line }
            if ($line -match ':\s*([\d.,]+)$') { $detail.lowered = [long]($Matches[1] -replace '[.,]', '') }
            return
        }
        if ($result) { $problem = Get-DbLicenseProblem $result }
        $message = $notLowered -f $problem
        Write-ArciWarn $message
        $script:Warnings.Add($message)
    } $detail
}

function Restore-LoweredLeases {
    <#
    A rollback lowered the FortiGate leases and then failed before the swap: this version runs on.
    Its "arcilogger-db upgrade" (the schema is at its head already) runs the seed, which restores
    the leases the step lowered and prints the counts (contract 2.2.1). Returns the manual step
    when that did not work.
    #>
    $command = '& "{0}" upgrade --config "{1}"' -f $DbExe, $ConfigPath
    try {
        $result = Invoke-ArciProcess -FilePath $DbExe -ArgumentList @('upgrade', '--config', $ConfigPath) -AllowFailure -TimeoutSeconds 900
        if ($result.ExitCode -eq 0) {
            $script:LeasesLowered = $false
            foreach ($line in @(([string]$result.StdOut) -split '\r?\n' | Where-Object { $_.Trim() })) { Write-ArciInfo $line.Trim() }
            return
        }
        $problem = Get-DbLicenseProblem $result
    }
    catch { $problem = $_.Exception.Message }
    'FortiGate kiraları düşük güvende kaldı ({0}); yönetici PowerShell''inde çalıştırın, sonra yazdırdığı günlerin kiralarını yeniden oluşturun [FortiGate leases stay at low confidence: run this in an administrator PowerShell, then rebuild the leases of the days it prints]: {1}' -f $problem, $command
}

function Get-SchemaRollbackPlan {
    <#
    Issue #122, generalised by #164: whether a rollback without -RestoreDatabase takes the
    database schema down before the swap, and to which revision, decided before any change. The
    revisions of both programs are read from their migrations folders, without Python and without
    the database (Get-ArciAlembicChain; a capability check as Test-PreviousLeaseRollbackModule's,
    #81). The previous version's revisions are the first ones of this version's, so it gets its
    own schema back: a version from before 0021_db_window creates and drops monthly partitions
    only, and one from 0021 on fails its own "arcilogger-db upgrade" (its repair run, an -Upgrade
    to another bundle) on a revision it does not ship ("Can't locate revision", #164). The target
    is its head, but never below the revision 0021_db_window revises (#122-5): the revisions
    before it were made for a previous version to run on (a rollback has never undone them: the
    NAT rows, the FortiGate leases of contract 2.2.1, which the next upgrade restores), and their
    downgrades delete data (0017's the FortiGate DHCP table; a question to the manager on #122).
    The way down, from this version's head (the database is not above it) to the target, crosses
    only revisions of $LosslessDowngrades. Nothing to do when this version has no 0021_db_window
    or the previous version knows this version's head (the same revisions, or more after them).
    The database's own revision is read in the step (Invoke-SchemaDowngradeStep). Stops the run
    (2) when the previous head cannot be determined or its revisions are not the first ones of
    this version's (never a guess; -RestoreDatabase needs no downgrade), when the way down
    crosses a revision not on the list, and when the downgrade is needed under -SkipDatabase.
    #>
    $ours = Join-Path $Venv 'Lib\site-packages\arcilogger_db\migrations\versions'
    $theirs = Join-Path $PreviousDir 'venv\Lib\site-packages\arcilogger_db\migrations\versions'
    $detail = [pscustomobject]@{ command = ('arcilogger-db ' + ($SchemaDowngradeCommand -join ' ')); needed = $false; to = $null; installed_head = $null; previous_head = $null; previous_migrations = $theirs; database_head = $null; result = $null }
    $installerPath = Join-Path $InstallDir 'installer\Install-Arcilogger.ps1'
    $refuse = {
        param([string]$Message)
        Add-PlanItem -Id 'database.downgrade' -Status 'fail' -Text $Message -Detail $detail
        Stop-Installer 2 $Message
    }
    $unknown = 'Geri alınan sürümün veritabanı şeması anlaşılamadı, hiçbir şey değiştirilmedi [could not tell the restored version''s database schema, nothing changed] ({0}). -RestoreDatabase ile yükseltme öncesi yedek veritabanının yerine geçer, şema indirilmez [with -RestoreDatabase the pre-upgrade dump replaces the database, no downgrade]: powershell -ExecutionPolicy Bypass -File "{1}" -Rollback -RestoreDatabase'
    try {
        $chain = Get-ArciAlembicChain -Folder $ours
        $detail.installed_head = $chain[-1]
        $window = [array]::IndexOf($chain, $DailyPartitionRevision)
        if ($window -lt 0) { return $detail }
        $previous = Get-ArciAlembicChain -Folder $theirs
        $detail.previous_head = $previous[-1]
        # The revisions both programs have, from the base on, in the same order (issue #164).
        $same = 0
        while ($same -lt [Math]::Min($chain.Count, $previous.Count) -and $previous[$same] -ceq $chain[$same]) { $same++ }
        if ($same -eq $chain.Count) { return $detail }
        $at = [array]::IndexOf($chain, $detail.previous_head)
        if ($at -lt 0 -or $window -eq 0) {
            throw ('önceki sürümün şema başı bu sürümün geçişlerinde yok [the previous version''s head is not one of this version''s revisions]: {0}' -f $detail.previous_head)
        }
        if ($same -lt $previous.Count) {
            throw ('önceki sürümün revizyonları bu sürümünkilerin ilkleri değil [the previous version''s revisions are not the first ones of this version''s]: {0}. revizyon {1}, bu sürümde {2} [revision {0} is {1}, here {2}]' -f ($same + 1), $previous[$same], $chain[$same])
        }
    }
    catch { & $refuse ($unknown -f $_.Exception.Message, $installerPath) }
    $script:SchemaChain = $chain
    $detail.needed = $true
    $detail.to = $chain[[Math]::Max($at, $window - 1)]
    $lossy = @($chain[([array]::IndexOf($chain, $detail.to) + 1)..($chain.Count - 1)] | Where-Object { $LosslessDowngrades -cnotcontains $_ })
    if ($lossy.Count -gt 0) {
        & $refuse ('Veritabanı şeması geri alınan sürümünkine kayıpsız indirilemez, hiçbir şey değiştirilmedi [the database schema cannot go down to the restored version''s without loss, nothing changed] ({0} -> {1}; kayıpsız indirilen revizyonlar listesinde değil [not on the list of lossless downgrades]: {2}). -RestoreDatabase ile yükseltme öncesi yedek veritabanının yerine geçer, şema indirilmez [with -RestoreDatabase the pre-upgrade dump replaces the database, no downgrade]: powershell -ExecutionPolicy Bypass -File "{3}" -Rollback -RestoreDatabase' -f $detail.installed_head, $detail.to, ($lossy -join ', '), $installerPath)
    }
    if ($SkipDatabase) {
        & $refuse ('Veritabanı şeması geri alınan sürümünkine indirilmeli ({0} -> {1}), -SkipDatabase ile geri alma yapılmadı, hiçbir şey değiştirilmedi [the database schema must go down to the restored version''s: no rollback with -SkipDatabase, nothing changed]. -SkipDatabase olmadan ya da -RestoreDatabase ile çalıştırın [run it without -SkipDatabase, or with -RestoreDatabase].' -f $detail.installed_head, $detail.to)
    }
    $detail
}

function Get-DatabaseSchemaRevision {
    <#
    The database's Alembic revision, from this version's read-only "arcilogger-db current" (exit
    0 at this version's head, 1 below it; the line "Veritabanı: <revision>"). Throws when the
    command fails or does not name one revision (no schema, several heads).
    #>
    $result = Invoke-ArciProcess -FilePath $DbExe -ArgumentList @('current', '--config', $ConfigPath) -AllowFailure -TimeoutSeconds 300
    $match = [regex]::Match([string]$result.StdOut, '(?m)^Veritaban\S*\s*:\s*([A-Za-z0-9_]+)\s*$')
    if ($result.ExitCode -notin @(0, 1) -or -not $match.Success) {
        throw ('veritabanının şema sürümü okunamadı [could not read the database''s schema revision] ({0})' -f (Get-DbLicenseProblem $result))
    }
    $match.Groups[1].Value
}

function Invoke-SchemaDowngradeStep($Plan) {
    <#
    Issue #122 (Get-SchemaRollbackPlan decided it): after the services stopped and after the lease
    step, which works on this version's schema, and before the swap, this version's
    "arcilogger-db schema downgrade --to <revision> --json" takes the database down to the
    previous version's schema (#164: 0022_lease_moves' downgrade drops leases.moves); down to
    0020, 0021_db_window's downgrade merges the daily partitions into monthly ones, a step per
    table and month, and goes on where an interrupted run stopped (W5-G-15). First the database's
    own revision (Get-DatabaseSchemaRevision): one at the target or before it (an -Upgrade
    -SkipDatabase left it there; one from before 0021_db_window has no daily partitions) is left
    as it is, nothing is done. The command takes its own lock and writes the settings.change
    entry of db.retention_days (30 -> 400) itself; its JSON is kept in the plan's detail (result). A
    failure stops the rollback before the swap (Complete-FailedChange): this version stays
    installed on its own schema (the committed steps leave a valid layout of it, and its
    "arcilogger-db upgrade" runs first unless the command certainly changed nothing), and the
    output names the restore of the pre-upgrade dump as the way back.
    #>
    if (-not $Plan.needed) {
        $previous = $(if ($Plan.previous_head) { $Plan.previous_head } else { '-' })
        $skipped = 'Veritabanı şeması indirilmiyor: geri alınan sürüm bu sürümün şemasını biliyor ya da bu sürümün şeması {0} öncesinde [database schema not downgraded: the restored version knows this version''s schema, or this version''s schema is from before {0}] ({1}; {2})' -f $DailyPartitionRevision, $Plan.installed_head, $previous
        Add-PlanItem -Id 'database.downgrade' -Status 'skipped' -Text $skipped -Detail $Plan
        Write-ArciStep $skipped
        return
    }
    $text = 'Veritabanı geri alınan sürümün şemasına indiriliyor [downgrading the database to the restored version''s schema] ({0} -> {1}, arcilogger-db {2} --to {1})' -f $Plan.installed_head, $Plan.to, ($SchemaDowngradeCommand -join ' ')
    Invoke-Step 'database.downgrade' $text {
        $problem = $null
        try {
            $Plan.database_head = Get-DatabaseSchemaRevision
            $at = [array]::IndexOf($script:SchemaChain, $Plan.database_head)
            if ($at -lt 0) {
                throw ('veritabanı bu sürümün bilmediği bir şema sürümünde [the database is at a revision this version does not know]: {0}' -f $Plan.database_head)
            }
        }
        catch { $problem = $_.Exception.Message }
        if (-not $problem -and $at -lt [array]::IndexOf($script:SchemaChain, $DailyPartitionRevision)) {
            Write-ArciInfo ('Veritabanı şeması {0}, {1} öncesinde: birleştirilecek günlük bölüm yok, indirilmedi [the database schema is at {0}, from before {1}: no daily partitions, not downgraded]' -f $Plan.database_head, $DailyPartitionRevision)
            return
        }
        if (-not $problem -and $at -le [array]::IndexOf($script:SchemaChain, $Plan.to)) {
            Write-ArciInfo ('Veritabanı şeması {0}, hedef {1} ya da öncesinde: indirilmedi [the database schema is at {0}, the target {1} or before it: not downgraded]' -f $Plan.database_head, $Plan.to)
            return
        }
        $result = $null
        if (-not $problem) {
            # Before the call: a command that committed the version's move and then failed to
            # say so (the connection dropped at the commit, the process was stopped) leaves the
            # database at the previous version's schema; this version's upgrade (a no-op at its
            # head) then brings its own back before its services start (Restore-DowngradedSchema).
            $script:SchemaDowngraded = $true
            # No time limit, as the upgrade: a month of a large table takes as long as it takes;
            # the command's lock timeout ends a wait for a lock.
            try { $result = Invoke-ArciProcess -FilePath $DbExe -ArgumentList (@($SchemaDowngradeCommand) + @('--to', $Plan.to, '--json', '--config', $ConfigPath)) -AllowFailure }
            catch { $problem = $_.Exception.Message }
        }
        if ($result) { $script:SchemaDowngradeExit = $result.ExitCode }
        if ($result -and $result.ExitCode -eq 0) {
            $answer = $null
            try { $answer = ([string]$result.StdOut) | ConvertFrom-Json } catch { $answer = $null }
            $Plan.result = $answer
            if ((Get-ArciJsonField -Object $answer -Name 'to') -ceq $Plan.to) {
                $line = 'Veritabanı şeması [database schema]: {0} -> {1}' -f (Get-ArciJsonField -Object $answer -Name 'from'), $Plan.to
                $days = Get-ArciJsonField -Object $answer -Name 'retention_days'
                $audit = Get-ArciJsonField -Object $answer -Name 'audit_id'
                $pending = Get-ArciJsonField -Object $answer -Name 'upgrade_audit_id'
                $before = Get-ArciJsonField -Object $days -Name 'before'
                $after = Get-ArciJsonField -Object $days -Name 'after'
                # Down to 0021 only 0022's drop runs: the window stays, nothing to print.
                if ($null -ne $days -and "$before" -cne "$after") { $line += ('; db.retention_days {0} -> {1}' -f $before, $after) }
                if ($null -ne $audit) { $line += (', denetim kaydı [audit entry] {0}' -f $audit) }
                if ($null -ne $pending) { $line += ('; yükseltmenin değişikliği [the upgrade''s change]: denetim kaydı [audit entry] {0}' -f $pending) }
                Write-ArciInfo $line
                return
            }
            $problem = 'beklenmeyen yanıt [unexpected answer]: {0}' -f ((([string]$result.StdOut) -replace '\s+', ' ').Trim())
        }
        elseif ($result -and $result.ExitCode -in @(2, 3)) {
            # Checked before the command changes anything: a revision it refuses (2), a database
            # it cannot reach (3). This version's schema is as it was.
            $script:SchemaDowngraded = $false
            $problem = Get-DbLicenseProblem $result
            if ($result.ExitCode -eq 2) { $problem = Get-CommandSaid $result }
        }
        elseif ($result) { $problem = Get-CommandSaid $result }
        $script:SchemaDowngradeFailed = $true
        throw ('Veritabanı geri alınan sürümün şemasına indirilemedi [the database could not be downgraded to the restored version''s schema] ({0})' -f $problem)
    } $Plan
}

function Get-CommandSaid($Result) {
    <# A command's own message, Turkish and English (its lines), in one line. #>
    $said = (@((([string]$Result.StdErr) + "`n" + ([string]$Result.StdOut)) -split '\r?\n' | ForEach-Object { $_.Trim() } | Where-Object { $_ }) -join ' ')
    if ($said.Length -gt 1000) { $said = $said.Substring(0, 1000) + '...' }
    $(if ($said) { 'çıkış kodu {0} [exit code]: {1}' -f $Result.ExitCode, $said } else { Get-DbLicenseProblem $Result })
}

function Restore-DowngradedSchema {
    <#
    A rollback took the schema down (or its downgrade may have moved alembic's version before it
    failed) and then failed before the swap: this version runs on, so its "arcilogger-db upgrade"
    brings its schema back (a no-op for the version at its head; with the seed, the leases the
    lease step lowered). No time limit, unlike Restore-LoweredLeases's 900 s on a schema at its
    head: after a whole downgrade 0021_db_window's upgrade runs again, as long as it took the
    first time. Returns the manual step when that did not work; the database services then stay
    stopped (Complete-FailedChange).
    #>
    $command = '& "{0}" upgrade --config "{1}"' -f $DbExe, $ConfigPath
    try {
        $result = Invoke-ArciProcess -FilePath $DbExe -ArgumentList @('upgrade', '--config', $ConfigPath) -AllowFailure
        if ($result.ExitCode -eq 0) {
            $script:SchemaDowngraded = $false
            $script:LeasesLowered = $false
            Write-ArciInfo 'Veritabanı şeması yeniden bu sürümünkü [the database schema is this version''s again] (arcilogger-db upgrade)'
            foreach ($line in @(([string]$result.StdOut) -split '\r?\n' | Where-Object { $_.Trim() })) { Write-ArciInfo $line.Trim() }
            return
        }
        $problem = Get-DbLicenseProblem $result
    }
    catch { $problem = $_.Exception.Message }
    'Veritabanı şeması bu sürümünkine geri getirilemedi ({0}); yönetici PowerShell''inde çalıştırın, sonra hizmetleri başlatın, ya da yükseltme öncesi yedeği geri yükleyin (-Rollback -RestoreDatabase) [the database schema is not this version''s again: run this in an administrator PowerShell, then start the services, or restore the pre-upgrade dump (-Rollback -RestoreDatabase)]: {1}' -f $problem, $command
}

function Invoke-RollbackMode {
    $previousState = Get-ArciProgramFolderState -Path $PreviousDir
    if ($previousState -eq 'foreign') {
        Stop-Installer 2 ("{0} ARCILOGGER'a ait değil [not an ARCILOGGER folder]." -f $PreviousDir)
    }
    if ($previousState -ne 'installed') {
        if ($RestoreDatabase) { Invoke-DatabaseOnlyRestore; return }
        Stop-Installer 2 ("Önceki sürüm yok [no previous version]: {0}" -f $PreviousDir)
    }
    if ((Get-ArciProgramFolderState -Path $InstallDir) -eq 'foreign') {
        Stop-Installer 2 ("{0} ARCILOGGER'a ait değil [not an ARCILOGGER folder]." -f $InstallDir)
    }
    # Before any change: a schema the previous version cannot tell stops the run here.
    $schema = $(if (-not $RestoreDatabase) { Get-SchemaRollbackPlan })
    try {
        Invoke-Step 'tasks.disable' 'Zamanlanmış görevler duraklatılıyor [pausing scheduled tasks]' { Disable-ArciTasks }
        # All of them (ArciIngest and ArciWorker too): the downgrade merges the partitions they write.
        Invoke-Step 'services.stop' 'Hizmetler durduruluyor [stopping services]' { Stop-ArciServices }
        # With -RestoreDatabase the dump replaces the database: nothing to lower, nothing to take
        # down. The lease step first: it works on this version's schema, which the downgrade
        # changes (a failed downgrade then puts the leases back with Restore-LoweredLeases).
        if (-not $RestoreDatabase) {
            Invoke-LeaseRollbackStep
            Invoke-SchemaDowngradeStep $schema
        }
        Invoke-Step 'program.swap' ("{0} geri getiriliyor [restoring]" -f $PreviousDir) {
            $aside = '{0}.rolled-back-{1}' -f $InstallDir, [DateTime]::UtcNow.ToString('yyyyMMddHHmmss')
            $movedAside = $false
            if (Test-Path -LiteralPath $InstallDir) {
                Rename-ArciFolder -LiteralPath $InstallDir -NewName (Split-Path -Leaf $aside)
                $movedAside = $true
            }
            try { Rename-ArciFolder -LiteralPath $PreviousDir -NewName (Split-Path -Leaf $InstallDir) }
            catch {
                # Both or neither: put the current version back where it was.
                if ($movedAside) { Rename-ArciFolder -LiteralPath $aside -NewName (Split-Path -Leaf $InstallDir) }
                throw
            }
            if ($movedAside) { $script:RolledBackDir = $aside }
            # From here on the previous version runs on the lowered leases and on its own schema:
            # nothing to undo.
            $script:LeasesLowered = $false
            $script:SchemaDowngraded = $false
        }
        Invoke-Step 'services.remove' 'Hizmet kayıtları kaldırılıyor [removing service registrations]' { Remove-ArciServices }
        if ($RestoreDatabase) {
            # After the swap the previous version must not run on this version's database (a
            # schema from 0021_db_window on, issue #122): a restore that fails at any point, also
            # before pg_restore, keeps the database services stopped (Complete-FailedChange).
            Invoke-Step 'database.restore' 'Veritabanı yükseltme öncesi yedekten geri yükleniyor [restoring pre-upgrade dump]' { $script:RestoreStarted = $true; Invoke-DatabaseRestore }
        }
        # A version from before the browser administration refuses what the browser added.
        Invoke-Step 'config.compatible' 'Yapılandırma önceki sürümle yükleniyor, gerekirse yükseltme öncesi kopya geri konuyor [the configuration must load with the previous version, else the copy from before the upgrade is put back]' { Restore-ConfigForProgram }
        # ApplySystem leaves with the newer tasks: the rules and the time peers it set follow the
        # configuration as the restored program reads it, before the collector starts again.
        $restoreDetail = [pscustomobject]@{
            firewall   = $(if (-not $SkipFirewall -and (Test-Path -LiteralPath $FirewallStatePath)) { Join-Path $InstallDir 'scripts\Set-ArciFirewall.ps1' } else { $null })
            time       = $(if (-not $SkipTimeSetup) { Join-Path $InstallDir 'scripts\Set-ArciTime.ps1' } else { $null })
            state_file = $FirewallStatePath
        }
        Invoke-Step 'system.restore' 'Güvenlik duvarı ve Windows Saati geri alınan sürümün betikleriyle yapılandırmaya göre [firewall and Windows Time from the configuration, with the restored version''s scripts]' { Sync-SystemAfterRollback } $restoreDetail
        # Services the previous version does not have (ArciAdmin) stay removed.
        Invoke-Step 'services.install' 'Windows hizmetleri [Windows services]' { Install-ArciServices }
        Invoke-Step 'tasks.register' 'Zamanlanmış görevler [scheduled tasks]' {
            Save-TaskChoices
            $script:TasksRegistered = $true
            Register-ArciTasksKeepingChoices -Root $InstallDir
        }
        Invoke-Step 'services.start' 'Hizmetler başlatılıyor [starting services]' { Start-ArciServices }
        Invoke-Step 'health.check' 'Sağlık denetimi [health check]' { Invoke-HealthCheck }
    }
    catch {
        if ($script:IsDryRun) { throw }
        Complete-FailedChange -Operation 'rollback' -Reason $_.Exception.Message
    }
}

function Remove-ArciProgramFolder([string]$Folder) {
    <#
    Delete a program folder, its installer\ folder and its VERSION.json / INSTALL-IN-PROGRESS.json
    last: if Windows refuses a file (a program still holding it open), the script that finishes
    the removal and the record that marks the folder as ARCILOGGER's are still there, so running
    -Uninstall again (or the setup's uninstaller, which calls installer\Install-Arcilogger.ps1)
    completes it.
    #>
    $last = @('installer', 'VERSION.json', (Get-ArciInstallMarkerName))
    foreach ($item in @(Get-ChildItem -LiteralPath $Folder -Force)) {
        if ($last -contains $item.Name) { continue }
        Remove-Item -LiteralPath $item.FullName -Recurse -Force
    }
    Remove-Item -LiteralPath $Folder -Recurse -Force
}

function Invoke-UninstallMode {
    # Decide what may be deleted before anything is changed: only ARCILOGGER program folders.
    $folders = @(Get-ProgramFolders)
    foreach ($folder in $folders) {
        try { Assert-SafeToDelete $folder }
        catch {
            Add-PlanItem -Id 'program.refuse' -Status 'fail' -Text $_.Exception.Message -Detail $folder
            Stop-Installer 2 ("{0} Hiçbir şey kaldırılmadı [nothing removed]." -f $_.Exception.Message)
        }
    }
    # The evidence jobs run python.exe from the program folder (sealing every 5 minutes, monthly
    # verification for hours): Windows would refuse to delete the files they hold open and leave
    # half a program folder behind. Pause them and wait first, as an upgrade does; a job still
    # running after -TaskWaitMinutes stops the removal before any change (the jobs run on).
    try {
        Invoke-Step 'tasks.disable' 'Zamanlanmış görevler duraklatılıyor, çalışanlar bekleniyor [pausing scheduled tasks]' { Disable-ArciTasks }
    }
    catch {
        if ($script:IsDryRun) { throw }
        $reason = $_.Exception.Message
        try { Enable-ArciTasks } catch { Write-ArciWarn ("Görevler yeniden etkinleştirilemedi [tasks not enabled again]: {0}" -f $_.Exception.Message) }
        Stop-Installer 2 ("{0} Hiçbir şey kaldırılmadı [nothing removed]." -f $reason)
    }
    Invoke-Step 'services.stop' 'Hizmetler durduruluyor [stopping services]' { Stop-ArciServices }
    Invoke-Step 'services.remove' 'Hizmetler kaldırılıyor [removing services]' { Remove-ArciServices } ($ServiceNames -join ', ')
    Invoke-Step 'tasks.unregister' 'Zamanlanmış görevler kaldırılıyor [removing scheduled tasks] \ARCILOGGER\' {
        Get-ScheduledTask -TaskPath '\ARCILOGGER\' -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
        Get-ArciStatusWindowTask | Unregister-ScheduledTask -Confirm:$false
        Remove-ArciStatusWindowShortcut
    }
    # The events already written stay in the Application log.
    Invoke-Step 'eventlog.remove' 'Olay günlüğü kaynağı kaldırılıyor [removing the Event Log source] ARCILOGGER-Admin' {
        Write-ArciInfo ('ARCILOGGER-Admin: {0}' -f (Unregister-ArciEventSource))
    }
    Invoke-Step 'firewall.remove' 'Güvenlik duvarı kuralları kaldırılıyor, önceki ayarlar geri yükleniyor [removing firewall rules, restoring previous settings]' {
        & (Join-Path $ScriptsDir 'Set-ArciFirewall.ps1') -Remove -StateFile $FirewallStatePath
    }
    # Only where Set-ArciPower.ps1 changed something (desktop Windows): the values it recorded.
    if (Test-Path -LiteralPath $PowerStatePath) {
        Invoke-Step 'power.restore' 'Güç ayarları kurulum öncesine döndürülüyor [restoring the power settings found before the installation] (Set-ArciPower.ps1 -Remove)' {
            try { & (Join-Path $ScriptsDir 'Set-ArciPower.ps1') -Remove -StateFile $PowerStatePath }
            catch {
                $message = 'Güç ayarları geri yüklenemedi [power settings not restored]: {0}. Önceki değerler [previous values]: {1}' -f $_.Exception.Message, $PowerStatePath
                Write-ArciWarn $message
                $script:Warnings.Add($message)
            }
        } $PowerStatePath
    }
    foreach ($folder in $folders) {
        Invoke-Step 'program.remove' ("Program klasörü siliniyor [removing program folder]: {0}" -f $folder) {
            Set-Location -LiteralPath $(if ($env:SystemRoot) { $env:SystemRoot } else { [System.IO.Path]::GetTempPath() })
            Remove-ArciProgramFolder $folder
        } $folder
    }
    foreach ($kept in @(
            @('keep.data', $DataRoot, 'Veri klasörü korunuyor (ham loglar, kasa, dışa aktarımlar, yedekler) [data folder kept]'),
            @('keep.config', $ProgramDataDir, 'Yapılandırma, gizli depo, yönetim klasörü (admin\), Kamu SM kökleri, Caddy CA korunuyor [configuration, secrets and the administration folder kept]'),
            @('keep.database', 'PostgreSQL', 'Veritabanı silinmedi [database kept]; istenirse elle: DROP DATABASE arcilogger; DROP ROLE arcilogger;')
        )) {
        Add-PlanItem -Id $kept[0] -Status 'kept' -Text ("{0}: {1}" -f $kept[2], $kept[1]) -Detail $kept[1]
        Write-ArciInfo ("{0}: {1}" -f $kept[2], $kept[1])
    }
}

# --- main --------------------------------------------------------------------------------------------

# Dot-sourced (". .\Install-Arcilogger.ps1 ..."): only define the functions. The tests use this to
# run the real upgrade, rollback and recovery code against simulated Windows services and tasks.
if ($MyInvocation.InvocationName -eq '.') { return }

if (-not $script:IsDryRun) {
    if (-not (Test-ArciWindows)) { Stop-Installer 2 'Bu betik yalnızca Windows üzerinde çalışır [Windows only]; denemek için -DryRun kullanın.' }
    $transcript = Join-Path ([System.IO.Path]::GetTempPath()) ('ARCILOGGER-{0}-{1}.log' -f $Mode, [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'))
    try { Start-Transcript -LiteralPath $transcript -WhatIf:$false | Out-Null } catch { Write-Verbose 'transcript unavailable' }
}

Write-Host ''
Write-Host ('ARCILOGGER kurulum [installer] - kip [mode]: {0}{1}' -f $Mode, $(if ($script:IsDryRun) { ' (DENEME / DRY RUN: hiçbir şey değişmez)' } else { '' }))
Write-Host ('  Program: {0}' -f $InstallDir)
Write-Host ('  Yapılandırma [config]: {0}' -f $ProgramDataDir)
Write-Host ('  Veri [data]: {0}' -f $DataRoot)

try {
    if ($Mode -eq 'install' -or $Mode -eq 'upgrade') {
        Invoke-BundleCheck
        if ($Auto -and $Mode -eq 'install') {
            $installed = Get-InstalledVersion
            if ($installed -and $installed.fingerprint -ne $script:BundleInfo.Fingerprint) {
                $Mode = 'upgrade'
                Write-ArciInfo ("Kurulu sürüm [installed] {0}: yükseltme yapılacak [upgrading]." -f $installed.version)
            }
        }
        Resolve-ServerIdentity
        Resolve-SetupValues
        Resolve-AdminPolicyChoice
    }
    if ($Mode -ne 'uninstall') {
        Invoke-Preflight
    }
    else {
        if (Test-ArciAdministrator) { Add-Check 'admin' 'ok' 'Yönetici hakları [administrator]' }
        else { Add-Check 'admin' $(if ($script:IsDryRun) { 'warn' } else { 'fail' }) 'Yönetici olarak çalıştırın [run as Administrator]' }
    }
    # The licence preflight stops before any change is made or planned, a dry run included
    # (Invoke-LicensePreflight has printed why).
    if ($script:LicenseStop) {
        if ($script:IsDryRun) {
            Stop-Installer 2 'Deneme tamam; gerçek çalıştırma ön denetimde dururdu, hiçbir şey değiştirilmedi [dry run complete; a real run would stop at the preflight].'
        }
        Stop-Installer 2 'Ön denetim başarısız; hiçbir şey değiştirilmedi [preflight failed, nothing changed].'
    }
    if ($script:Failures.Count -gt 0) {
        if ($script:IsDryRun) {
            Write-Host ('Gerçek kurulum bu sorunlarda durur [a real run would stop here]: {0}' -f $script:Failures.Count) -ForegroundColor Yellow
            $script:PlannedPastPreflight = $true
        }
        else {
            Stop-Installer 2 'Ön denetim başarısız; hiçbir şey değiştirilmedi [preflight failed, nothing changed].'
        }
    }
    switch ($Mode) {
        'install' { Invoke-Install }
        'upgrade' { Invoke-Upgrade }
        'rollback' { Invoke-RollbackMode }
        'uninstall' { Invoke-UninstallMode }
    }
}
catch {
    Write-Host ("HATA [error]: {0}" -f $_.Exception.Message) -ForegroundColor Red
    Write-Host ($_.ScriptStackTrace)
    if ($script:PlannedPastPreflight) {
        # A dry run plans on past its preflight failures and can then trip over what they reported
        # (an arcilogger.toml the venv's Python cannot read): the real run stops in the preflight
        # (exit 2) and never gets here, so the dry run returns that too, not 3 (a failed change).
        # An error in the preflight itself stops both runs with 3, failures recorded before it or not.
        Add-PlanItem -Id 'plan.incomplete' -Status 'fail' -Text $_.Exception.Message
        Stop-Installer 2 'Deneme planı bu hatada durdu; gerçek çalıştırma ön denetimde dururdu, hiçbir şey değiştirilmedi [dry run stopped at this error; a real run would stop at the preflight, nothing changed].'
    }
    Stop-Installer 3 'Kurulum yarıda kaldı; yukarıdaki hatayı giderip yeniden çalıştırın (betik tekrar çalıştırılabilir) [failed; fix the error and run again].'
}

if ($script:IsDryRun) {
    if ($script:Failures.Count -gt 0) {
        # Same code as the real run would return: it would stop in the preflight.
        Stop-Installer 2 'Deneme tamam; gerçek çalıştırma ön denetimde dururdu, hiçbir şey değiştirilmedi [dry run complete; a real run would stop at the preflight].'
    }
    Stop-Installer 0 'Deneme tamam; hiçbir şey değiştirilmedi [dry run complete, nothing changed].'
}
if ($Mode -eq 'install' -or $Mode -eq 'upgrade') {
    # The server address and name and the browser administration's choices are remembered for the
    # next run (an upgrade, the setup .exe).
    $choice = $script:AdminPolicyChoice
    Write-InstallState @{
        version = $script:Manifest.version; fingerprint = $script:BundleInfo.Fingerprint; last_mode = $Mode
        pending_database_restore = $null; last_failure = $null
        server_address = $ServerAddress; server_name = $ServerName
        admin_policy = [pscustomobject]@{
            enabled = $choice.Enabled; source_networks = @($choice.SourceNetworks)
            hold_hours = $choice.HoldHours; jar_hold_hours = $choice.JarHoldHours
        }
    }
    Show-NextSteps
}
if ($script:Warnings.Count -gt 0) {
    Write-Host ('Uyarılar [warnings]: {0}' -f $script:Warnings.Count) -ForegroundColor Yellow
    foreach ($warning in $script:Warnings) { Write-Host ('  - ' + $warning) -ForegroundColor Yellow }
    if ($script:ExitCode -eq 0) { $script:ExitCode = 1 }
}
Stop-Installer $script:ExitCode ''
