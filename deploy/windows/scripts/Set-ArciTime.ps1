#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Time zone and time synchronisation for the ARCILOGGER server.

.DESCRIPTION
    - Time zone: Türkiye (UTC+03:00, no daylight saving).
    - Windows Time service: -Primary, the local time source ([clock] ntp_server of
      arcilogger.toml, usually the core router), as the source and -Fallback
      (time.ume.tubitak.gov.tr) only as a fallback; polling every 64-1024 s instead of the
      Windows default. Without a local time source (-Primary empty, the default) the fallback is
      the only source.
    ARCILOGGER additionally measures the offset every hour itself (ClockCheck task) and records
    it in each daily manifest.
.EXAMPLE
    .\Set-ArciTime.ps1 -Primary 10.0.0.1
#>
param(
    [string]$Primary = "",
    [string]$Fallback = "time.ume.tubitak.gov.tr"
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path (Split-Path -Parent $PSScriptRoot) 'installer\ArciInstall.psm1') -Force
Initialize-ArciModulePath

# Checked before anything is changed: an empty list would leave Windows Time without a source.
$peers = Get-ArciTimePeerList -Primary $Primary -Fallback $Fallback
if (-not $Primary.Trim()) {
    Write-ArciWarn ("Yerel saat kaynağı yok; saat yalnızca {0} ile eşitlenir. Ana yönlendiricinin ya da yerel bir NTP sunucusunun adresini -Primary ile verin [no local time source: the clock follows only the fallback; give the core router or a local NTP server with -Primary]." -f $Fallback)
}

Set-TimeZone -Id "Turkey Standard Time"

# 0x8 = client mode, 0xA = client mode + use only as fallback (Get-ArciTimePeerList)
w32tm /config /manualpeerlist:"$peers" /syncfromflags:manual /reliable:no /update | Out-Null

$config = "HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Config"
Set-ItemProperty -Path $config -Name MinPollInterval -Value 6    # 2^6  = 64 s
Set-ItemProperty -Path $config -Name MaxPollInterval -Value 10   # 2^10 = 1024 s
Set-Service -Name w32time -StartupType Automatic
Restart-Service -Name w32time
w32tm /resync /force | Out-Null
w32tm /query /status
