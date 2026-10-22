<#
.SYNOPSIS
    Masaüstü Windows'ta ARCILOGGER için güç ayarları [power settings for ARCILOGGER on desktop Windows].

.DESCRIPTION
    A Windows 10/11 PC goes to sleep or hibernates when nobody uses it. Syslog arrives over UDP:
    whatever the devices send meanwhile is lost, and ARCILOGGER has no record of it. On a client
    Windows (ProductType 1) this script, run by Install-Arcilogger.ps1:
      - sets "Sleep after" (STANDBYIDLE) and "Hibernate after" (HIBERNATEIDLE) of the active power
        plan to never (0) when plugged in (AC); the battery (DC) values are not changed;
      - turns hibernation off (powercfg /hibernate off), which also turns Fast Startup off (Fast
        Startup needs the hibernation file);
      - sets HiberbootEnabled = 0 (HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power),
        so Fast Startup stays off even if hibernation is turned on again;
      - switches "Allow the computer to turn off this device to save power" off on the physical
        network adapters whose driver supports it, where this Windows offers it
        (Set-NetAdapterPowerManagement -AllowComputerToTurnOffDevice; applied with -NoRestart, so
        it takes effect when the adapter or Windows restarts and no session is cut off now).

    The values found before the first run are recorded in the state file
    (C:\ProgramData\ARCILOGGER\install\power-state.json): the active power plan (scheme GUID), both
    AC timeouts, the hibernation state (HibernateEnabled), HiberbootEnabled and the adapters that
    were changed. A later run (an upgrade) keeps that record and applies the settings again.
    -Remove restores the recorded values and deletes the state file (Install-Arcilogger.ps1
    -Uninstall runs it). On Windows Server nothing is changed unless -Force.

    Windows Update restarts are not changed here: docs/KURULUM.md, "Masaüstü Windows 11 Pro".

.EXAMPLE
    .\Set-ArciPower.ps1 -DryRun -PlanPath power-plan.json
.EXAMPLE
    .\Set-ArciPower.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$StateFile = $(if ($env:ProgramData) { Join-Path $env:ProgramData 'ARCILOGGER\install\power-state.json' } else { 'C:\ProgramData\ARCILOGGER\install\power-state.json' }),
    [switch]$Remove,
    [switch]$DryRun,
    [string]$PlanPath,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
Import-Module (Join-Path (Split-Path -Parent $PSScriptRoot) 'installer\ArciInstall.psm1') -Force
Initialize-ArciModulePath

$OnWindows = Test-ArciWindows
$PowerCfg = $(if ($env:SystemRoot) { Join-Path $env:SystemRoot 'System32\powercfg.exe' } else { 'powercfg.exe' })
$PowerKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Power'
$SessionPowerKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power'
$Settings = @('STANDBYIDLE', 'HIBERNATEIDLE')   # powercfg aliases in SUB_SLEEP

function Invoke-PowerCfg([string[]]$Arguments) {
    (Invoke-ArciProcess -FilePath $PowerCfg -ArgumentList $Arguments).StdOut
}

function Get-RegistryDword([string]$Path, [string]$Name) {
    <# The value, or $null when the key or the value does not exist. #>
    $item = Get-ItemProperty -LiteralPath $Path -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $item) { return $null }
    [int64]$item.$Name
}

function Get-AcIndex([string]$Scheme, [string]$Setting) {
    <# The AC value (seconds) of a sleep setting of the scheme, or $null when it cannot be read. #>
    $parsed = $null
    try { $parsed = ConvertFrom-ArciPowercfgQuery -Text (Invoke-PowerCfg @('/query', $Scheme, 'SUB_SLEEP', $Setting)) }
    catch { $parsed = $null }
    if ($null -eq $parsed) { return $null }
    [int64]$parsed.Ac
}

function Get-AdapterCommand {
    <# Set-NetAdapterPowerManagement when this Windows offers the parameters used here, else $null. #>
    $command = Get-Command Set-NetAdapterPowerManagement -ErrorAction SilentlyContinue
    if ($null -eq $command -or -not (Get-Command Get-NetAdapter -ErrorAction SilentlyContinue)) { return $null }
    foreach ($name in @('AllowComputerToTurnOffDevice', 'NoRestart')) {
        if (-not $command.Parameters.ContainsKey($name)) { return $null }
    }
    $command
}

function Get-AdapterPowerSaving {
    <# Physical adapters and whether Windows may turn them off to save power (Enabled, Disabled, Unsupported). #>
    if ($null -eq (Get-AdapterCommand)) { return , @() }
    $adapters = New-Object System.Collections.Generic.List[object]
    foreach ($adapter in @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_ })) {
        $value = 'Unsupported'
        try {
            $setting = Get-NetAdapterPowerManagement -Name $adapter.Name -ErrorAction Stop
            if ($setting.PSObject.Properties['AllowComputerToTurnOffDevice']) { $value = [string]$setting.AllowComputerToTurnOffDevice }
        }
        catch { $value = 'Unsupported' }
        $adapters.Add([pscustomobject]@{ name = [string]$adapter.Name; interface_guid = [string]$adapter.InterfaceGuid; description = [string]$adapter.InterfaceDescription; allow_turn_off = $value })
    }
    , $adapters.ToArray()
}

function Read-CurrentPower {
    <# The settings as they are now (nothing on another platform: a dry run there shows the plan only). #>
    if (-not $OnWindows) { return $null }
    $scheme = $null
    try { $scheme = Get-ArciPowerSchemeGuid -Text (Invoke-PowerCfg @('/getactivescheme')) } catch { $scheme = $null }
    $standby = $null
    $hibernateIdle = $null
    if ($scheme) {
        $standby = Get-AcIndex $scheme 'STANDBYIDLE'
        $hibernateIdle = Get-AcIndex $scheme 'HIBERNATEIDLE'
    }
    # Explicit $null: Windows PowerShell 5.1 writes a function result of nothing as {} in JSON.
    if ($null -eq $standby) { $standby = $null }
    if ($null -eq $hibernateIdle) { $hibernateIdle = $null }
    $hibernate = Get-RegistryDword $PowerKey 'HibernateEnabled'
    if ($null -eq $hibernate) { $hibernate = $null }
    $hiberboot = Get-RegistryDword $SessionPowerKey 'HiberbootEnabled'
    if ($null -eq $hiberboot) { $hiberboot = $null }
    # Assigned first: the function returns its list as one object, which @(...) would wrap.
    $adapters = Get-AdapterPowerSaving
    [pscustomobject]@{
        scheme = $scheme; standby_timeout_ac = $standby; hibernate_timeout_ac = $hibernateIdle
        hibernate_enabled = $hibernate; hiberboot_enabled = $hiberboot
        adapters = $adapters
    }
}

function Read-PowerState {
    if (-not (Test-Path -LiteralPath $StateFile)) { return $null }
    [System.IO.File]::ReadAllText($StateFile, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
}

function Write-PowerState($State) {
    $State | Add-Member -NotePropertyName 'updated_at' -NotePropertyValue ([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')) -Force
    Write-ArciUtf8File -Path $StateFile -Text ($State | ConvertTo-Json -Depth 5)
}

function Get-Field($Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    $property.Value
}

# --- plan ----------------------------------------------------------------------------------------

$state = Read-PowerState
$current = Read-CurrentPower
$target = [pscustomobject]@{
    standby_timeout_ac = 0; hibernate_timeout_ac = 0; hibernate_enabled = 0; hiberboot_enabled = 0
    network_adapter_power_saving = 'Disabled'
}
$plan = [pscustomobject]@{
    remove     = [bool]$Remove
    state_file = $StateFile
    # The values -Remove restores: those recorded at the first run, else the current ones.
    previous   = $(if ($null -ne $state) { Get-Field $state 'previous' } else { $current })
    current    = $current
    target     = $(if ($Remove) { $null } else { $target })
}
if ($PlanPath) { Write-ArciUtf8File -Path $PlanPath -Text ($plan | ConvertTo-Json -Depth 6) }

if ($Remove) {
    Write-ArciStep ('Güç ayarları kurulum öncesine dönecek [power settings to restore]: {0}' -f $StateFile)
}
else {
    Write-ArciStep 'Güç ayarları [power settings]: prize takılıyken uyku ve hazırda bekletme yok (0), hazırda bekletme ve Hızlı Başlatma kapalı, ağ bağdaştırıcılarında güç tasarrufu kapalı'
}
if ($DryRun) {
    Write-ArciInfo 'Deneme çalıştırması; güç ayarları değiştirilmedi [dry run, nothing changed].'
    exit 0
}

if (-not $OnWindows) { throw 'Bu betik yalnızca Windows üzerinde çalışır [Windows only].' }
if (-not (Test-ArciAdministrator)) { throw 'Yönetici olarak çalıştırın [run as Administrator].' }

# --- remove --------------------------------------------------------------------------------------

if ($Remove) {
    if ($null -eq $state) {
        Write-ArciInfo ('Kayıt yok, geri alınacak bir şey yok [nothing recorded, nothing to restore]: {0}' -f $StateFile)
        exit 0
    }
    $previous = Get-Field $state 'previous'
    # Problems keep the record (running -Remove again can finish the job); manual notes do not,
    # since another run could not do more than this one.
    $problems = New-Object System.Collections.Generic.List[string]
    $manual = New-Object System.Collections.Generic.List[string]
    $scheme = [string](Get-Field $previous 'scheme')
    if ($scheme) {
        foreach ($pair in @(@('STANDBYIDLE', 'standby_timeout_ac'), @('HIBERNATEIDLE', 'hibernate_timeout_ac'))) {
            $value = Get-Field $previous $pair[1]
            if ($null -eq $value) {
                $manual.Add(('{0}: önceki değer okunamamıştı, Ayarlar > Sistem > Güç bölümünden elle ayarlayın [previous value unknown, set it by hand]' -f $pair[0]))
                continue
            }
            try { [void](Invoke-PowerCfg @('/setacvalueindex', $scheme, 'SUB_SLEEP', $pair[0], [string]$value)) }
            catch { $problems.Add(('{0}: {1}' -f $pair[0], $_.Exception.Message)) }
        }
        try {
            [void](Invoke-PowerCfg @('/setactive', $scheme))
            Write-ArciInfo ('güç planı geri yüklendi [power plan restored]: {0}' -f $scheme)
        }
        catch { $problems.Add(('powercfg /setactive {0}: {1}' -f $scheme, $_.Exception.Message)) }
    }
    $hibernate = Get-Field $previous 'hibernate_enabled'
    if ([string]$hibernate -eq '1') {
        try { [void](Invoke-PowerCfg @('/hibernate', 'on')); Write-ArciInfo 'hazırda bekletme yeniden açıldı [hibernation on again]' }
        catch { $problems.Add(('powercfg /hibernate on: {0}' -f $_.Exception.Message)) }
    }
    elseif ($null -eq $hibernate) {
        $manual.Add('hazırda bekletme: önceki durum okunamamıştı, kapalı kaldı; gerekirse yönetici komut isteminde "powercfg /hibernate on" [previous state unknown, left off]')
    }
    $hiberboot = Get-Field $previous 'hiberboot_enabled'
    try {
        if ($null -ne $hiberboot) {
            [void](New-ItemProperty -LiteralPath $SessionPowerKey -Name 'HiberbootEnabled' -PropertyType DWord -Value ([int]$hiberboot) -Force)
        }
        else {
            # The value did not exist before: Windows' default applies again.
            Remove-ItemProperty -LiteralPath $SessionPowerKey -Name 'HiberbootEnabled' -ErrorAction SilentlyContinue
        }
    }
    catch { $problems.Add(('HiberbootEnabled: {0}' -f $_.Exception.Message)) }
    $changed = @(Get-Field $state 'adapters_changed' | Where-Object { $_ })
    if ($changed.Count -gt 0) {
        if ($null -eq (Get-AdapterCommand)) { $problems.Add('Set-NetAdapterPowerManagement yok [not available]: ağ bağdaştırıcılarının güç tasarrufunu Aygıt Yöneticisi''nde açın') }
        else {
            foreach ($entry in $changed) {
                $adapter = @(Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_ -and [string]$_.InterfaceGuid -eq [string]$entry.interface_guid })
                if ($adapter.Count -eq 0) { continue }
                try {
                    Set-NetAdapterPowerManagement -Name $adapter[0].Name -AllowComputerToTurnOffDevice Enabled -NoRestart
                    Write-ArciInfo ('güç tasarrufu yeniden açıldı [power saving on again]: {0}' -f $adapter[0].Name)
                }
                catch { $problems.Add(('{0}: {1}' -f $adapter[0].Name, $_.Exception.Message)) }
            }
        }
    }
    foreach ($note in $manual) { Write-ArciWarn $note }
    if ($problems.Count -gt 0) {
        foreach ($problem in $problems) { Write-ArciWarn $problem }
        # The record stays, so running -Remove again (or by hand) can finish the job.
        throw ('Güç ayarları tam geri yüklenemedi [not completely restored]; kayıt [record]: {0}' -f $StateFile)
    }
    Remove-Item -LiteralPath $StateFile -Force
    Write-ArciStep 'Güç ayarları kurulum öncesine döndü [power settings restored].'
    exit 0
}

# --- apply ---------------------------------------------------------------------------------------

if (-not $Force) {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    if ([int]$os.ProductType -ne 1) {
        Write-ArciInfo 'Windows Server: güç ayarlarına dokunulmadı [server Windows, nothing changed] (-Force ile uygulanır).'
        exit 0
    }
}
if ($null -eq $current.scheme) { throw 'Etkin güç planı okunamadı [active power plan not readable]: powercfg /getactivescheme' }

if ($null -eq $state) {
    # Recorded before the first change, so -Remove can always undo what follows.
    $state = [pscustomobject]@{ schema = 1; recorded_at = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'); previous = $current; adapters_changed = @() }
    Write-PowerState $state
}
foreach ($setting in $Settings) {
    try { [void](Invoke-PowerCfg @('/setacvalueindex', $current.scheme, 'SUB_SLEEP', $setting, '0')) }
    catch { Write-ArciWarn ('{0} ayarlanamadı [not set]: {1}' -f $setting, $_.Exception.Message) }
}
# The changed values apply to the active plan only once it is activated again.
[void](Invoke-PowerCfg @('/setactive', $current.scheme))
Write-ArciInfo ('prize takılıyken uyku ve hazırda bekletme: hiçbir zaman [never on AC]: {0}' -f $current.scheme)
[void](Invoke-PowerCfg @('/hibernate', 'off'))
Write-ArciInfo 'hazırda bekletme ve Hızlı Başlatma kapalı [hibernation and Fast Startup off]'
[void](New-ItemProperty -LiteralPath $SessionPowerKey -Name 'HiberbootEnabled' -PropertyType DWord -Value 0 -Force)

$changed = New-Object System.Collections.Generic.List[object]
foreach ($entry in @(Get-Field $state 'adapters_changed' | Where-Object { $_ })) { $changed.Add($entry) }
$adapters = @($current.adapters | Where-Object { $_ })
if ($null -eq (Get-AdapterCommand)) {
    Write-ArciInfo 'Ağ bağdaştırıcısı güç tasarrufu bu Windows''ta komutla değiştirilemiyor; Aygıt Yöneticisi > bağdaştırıcı > Güç Yönetimi > "Güç tasarrufu için bilgisayar bu aygıtı kapatabilir" kutusunu kaldırın [untick it in Device Manager]'
}
foreach ($adapter in $adapters) {
    if ($adapter.allow_turn_off -ne 'Enabled') { continue }
    # One adapter refusing must not lose the record of those already changed (written below).
    try { Set-NetAdapterPowerManagement -Name $adapter.name -AllowComputerToTurnOffDevice Disabled -NoRestart }
    catch {
        Write-ArciWarn ('güç tasarrufu kapatılamadı [power saving not switched off]: {0}: {1}' -f $adapter.name, $_.Exception.Message)
        continue
    }
    if (-not @($changed | Where-Object { [string]$_.interface_guid -eq $adapter.interface_guid }).Count) { $changed.Add($adapter) }
    Write-ArciInfo ('güç tasarrufu kapalı [power saving off] (bağdaştırıcı yeniden başlayınca [after the adapter restarts]): {0}' -f $adapter.name)
}
$state | Add-Member -NotePropertyName 'adapters_changed' -NotePropertyValue $changed.ToArray() -Force
Write-PowerState $state
Write-ArciStep 'Güç ayarları uygulandı [power settings applied].'
