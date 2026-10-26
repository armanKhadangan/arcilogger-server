<#
.SYNOPSIS
    ARCILOGGER klasör izinleri ve kasa denetimi [NTFS rights for the service accounts, vault auditing].

.DESCRIPTION
    Gives every ARCILOGGER account only the rights it needs (docs/adr/0007-windows-installer.md):

      Folder                              Rights
      ---------------------------------   ------------------------------------------------------------
      <data>\spool                        ArciCollector read + write, no delete (cannot delete or rename
                                          raw files); ArciIngest read; ArciApi read (raw lines,
                                          5651 report and sealing calendar of days not sealed yet);
                                          LOCAL SERVICE (scheduled tasks) modify: sealing removes closed days
      <data>\spool\_audit                 protected: ArciApi read + write, no delete (audit log of the
                                          day); LOCAL SERVICE modify (sealing); ArciIngest read;
                                          nothing for the collector
      <data>\vault                        LOCAL SERVICE modify (seal, retention); ArciApi, ArciWorker,
                                          ArciIngest read; auditing (SACL) of every write/delete/permission change
      <data>\state                        collector, ingest, worker, tasks modify (status, alerts);
                                          ArciApi read
      <data>\state\seal, \replica,        protected: LOCAL SERVICE modify (only the evidence jobs write
        \verify, \clock                   them); ArciApi, ArciWorker read (ArciIngest also state\seal)
      <data>\exports                      ArciApi, ArciWorker modify
      <data>\logs                         every ARCILOGGER account modify
      <data>\backups, \backups\db         administrators only (the installer's dumps before upgrades)
      <data>\backups\scheduled            protected: LOCAL SERVICE modify (the Backup task writes and
                                          prunes the scheduled dumps, ADR-0009); no ARCILOGGER
                                          service may read them
      C:\ProgramData\ARCILOGGER           services and tasks read (config, Kamu SM roots, Zamane jar)
      ...\secret.key                      ArciIngest, ArciApi, ArciWorker, LOCAL SERVICE read - not the collector
      ...\caddy                           ArciWeb modify (Caddy's internal CA), nobody else
      C:\Program Files\ARCILOGGER         everyone of the above read

    SYSTEM and Administrators keep full control everywhere. Users get nothing on the data and
    configuration folders. Accounts are written as SIDs (NT SERVICE\<name> is computed from the
    service name), so the script works before the services exist and on a Turkish Windows.

    Sealed files keep the deny entries that arcilogger_core.immutability puts on them (ADR-0003):
    only the explicit entries of the folders listed above are replaced; Windows recomputes the
    inherited entries of the files below them. File contents are never touched.

    Run again at any time (idempotent), e.g. after restoring the server.

.PARAMETER PlanOnly
    Print the planned rights (and write them as JSON to -PlanPath) without changing anything.

.PARAMETER DataRoot
    Default: the data root the installer recorded in <ProgramDataDir>\data-root.txt (D:\ARCILOGGER,
    or C:\ARCILOGGER on a computer without a data disk), as the installer resolves it
    (Resolve-ArciDataRoot): ArciAdmin can write arcilogger.toml, so a data_dir that differs from
    the record, or an unusable record, stops the script before any change. Without a record (an
    installation from before the browser administration) data_dir of arcilogger.toml.

.EXAMPLE
    .\Set-ArciVaultAcl.ps1
.EXAMPLE
    .\Set-ArciVaultAcl.ps1 -DataRoot E:\ARCILOGGER -PlanOnly
#>
[CmdletBinding()]
param(
    [string]$DataRoot,
    [string]$ProgramDataDir = $(if ($env:ProgramData) { Join-Path $env:ProgramData 'ARCILOGGER' } else { 'C:\ProgramData\ARCILOGGER' }),
    [string]$InstallDir = $(if ($env:ProgramFiles) { Join-Path $env:ProgramFiles 'ARCILOGGER' } else { 'C:\Program Files\ARCILOGGER' }),
    [switch]$SkipAudit,
    [switch]$PlanOnly,
    [string]$PlanPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
Import-Module (Join-Path (Split-Path -Parent $PSScriptRoot) 'installer\ArciInstall.psm1') -Force
Initialize-ArciModulePath

if (-not $DataRoot) {
    $configPath = Join-Path $ProgramDataDir 'arcilogger.toml'
    $record = Read-ArciDataRootRecord -Path (Join-Path $ProgramDataDir 'data-root.txt')
    $configured = Get-ArciConfiguredDataDir -ConfigPath $configPath
    $adminInstalled = Test-Path -LiteralPath (Join-Path $ProgramDataDir 'admin') -PathType Container
    $resolved = Resolve-ArciDataRoot -Record $record -Configured $configured -AdminInstalled:$adminInstalled
    if ($resolved.Problem) { throw $resolved.Problem }
    $DataRoot = $resolved.Path
    if (-not $DataRoot) { throw ("-DataRoot verin: {0} içinde data_dir yok [give -DataRoot: no data_dir in the configuration]" -f $configPath) }
}

$plan = Get-ArciAclPlan -DataRoot $DataRoot -ProgramDataDir $ProgramDataDir -InstallDir $InstallDir

if ($PlanPath) {
    $document = [pscustomobject]@{
        data_root        = $DataRoot
        program_data_dir = $ProgramDataDir
        install_dir      = $InstallDir
        audit_policy     = (-not $SkipAudit)
        entries          = $plan
    }
    Write-ArciUtf8File -Path $PlanPath -Text ($document | ConvertTo-Json -Depth 6)
}

foreach ($entry in $plan) {
    $protect = if ($entry.Protect) { 'kalıtım kapalı [inheritance off]' } else { 'kalıtım açık [inherits]' }
    Write-ArciStep ("{0}  ({1})" -f $entry.Path, $protect)
    foreach ($ace in $entry.Aces) {
        $scope = if (-not $ace.Inherit) { ' (yalnızca bu klasör) [this folder only]' }
        elseif ($ace.FoldersOnly) { ' (bu klasör ve alt klasörler, dosyalar değil) [this folder and sub-folders, not files]' }
        else { '' }
        Write-ArciInfo ("{0,-28} {1}{2}" -f $ace.Who, $ace.Rights, $scope)
    }
    if ($entry.Audit) { Write-ArciInfo 'Denetim: yazma/silme/izin değişikliği [audit: write/delete/permission change]' }
}

if ($PlanOnly) {
    Write-ArciInfo 'Yalnızca plan; değişiklik yapılmadı [plan only, nothing changed].'
    exit 0
}

if (-not (Test-ArciWindows)) { throw 'Bu betik yalnızca Windows üzerinde çalışır [Windows only].' }
if (-not (Test-ArciAdministrator)) { throw 'Yönetici olarak çalıştırın [run as Administrator].' }

foreach ($entry in $plan) {
    if (-not (Test-Path -LiteralPath $entry.Path)) {
        if ($entry.Kind -eq 'File') {
            Write-ArciWarn ("Dosya yok, atlandı [file missing, skipped]: {0}" -f $entry.Path)
            continue
        }
        [void](New-Item -ItemType Directory -Path $entry.Path -Force)
    }
    Set-ArciAclEntry -Entry $entry
}

if (-not $SkipAudit) {
    $vault = Join-Path $DataRoot 'vault'
    Set-ArciVaultAudit -VaultPath $vault
    # "File System" audit subcategory, addressed by GUID because its name is localised.
    $fileSystemSubcategory = '/subcategory:{0CCE921D-69AE-11D9-BED3-505054503030}'
    & auditpol.exe /set $fileSystemSubcategory /success:enable /failure:enable | Out-Null
    if ($LASTEXITCODE -ne 0) {
        # The same command by hand works on every edition; Windows 11 Home has no Local Security
        # Policy (secpol.msc), and a domain Group Policy can override a local audit setting.
        Write-ArciWarn ('auditpol başarısız (çıkış kodu {0}) [auditpol failed]: "Dosya Sistemi" denetimini yönetici PowerShell''inde açın [enable File System auditing in an elevated PowerShell]: auditpol /set /subcategory:"{1}" /success:enable /failure:enable ; denetim [check]: auditpol /get /subcategory:"{1}". Yerel Güvenlik İlkesi (secpol.msc) Windows Home''da yoktur; bir etki alanı Grup İlkesi bu ayarı geçersiz kılabilir [no secpol.msc on Home; a domain Group Policy can override it].' -f $LASTEXITCODE, '{0CCE921D-69AE-11D9-BED3-505054503030}')
    }
    else {
        & auditpol.exe /get $fileSystemSubcategory
    }
}

Write-ArciStep 'İzinler uygulandı [rights applied].'
