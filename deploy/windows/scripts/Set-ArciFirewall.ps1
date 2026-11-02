<#
.SYNOPSIS
    ARCILOGGER için Windows Defender Güvenlik Duvarı kuralları [firewall allow-lists].

.DESCRIPTION
    Inbound (all profiles, default action Block; spec 13 "allow 514 from the senders, 443 and
    remote administration only from the admin PC, nothing else"):
      - UDP and TCP 514 (syslog) only from the senders in arcilogger.toml [[sources]]
        (addresses and networks such as 10.0.1.0/24). Without any (a new installation has none
        until the organisation adds its devices) there is no syslog rule: 514 stays closed and
        the script says so. Add the [[sources]] and run it again.
      - TCP 443 (web interface) and TCP/UDP 3389 (remote desktop) only from the admin PC
        (-AdminAddress, e.g. 10.0.0.10 or 10.0.0.0/24; no default: asked at the console when
        neither given nor remembered, otherwise the script stops); with -AllowWinRM also TCP
        5985 (PowerShell remoting, Server Manager) from the admin PC.
      - Every OTHER enabled inbound allow rule is disabled, because an allow rule always wins over
        the default Block action: Windows' own "Remote Desktop", "Windows Remote Management
        (HTTP-In)" (TCP 5985, enabled by default on Windows Server), "File and Printer Sharing"
        and any rule another program added. Kept are only the rule groups in -KeepRuleGroup
        (default: "Core Networking" - DHCP, IPv6 neighbour discovery, ICMP errors) and the rules
        named in -KeepRule. Every rule disabled here is recorded in the state file; -Remove
        enables them again. The script stops before changing anything if no rule of the
        -KeepRuleGroup groups exists (the group name is an assumption: verify on site).

    Outbound (-StrictOutbound, optional; default action becomes Block):
      NTP (UDP 123) to [clock] ntp_server and fallback_server, DNS (53) to the configured DNS
      servers, Kamu SM (zd/tzd.kamusm.gov.tr and the host of [tsa] url, on [tsa] port; root
      certificates and CRLs from sertifikalar.kamusm.gov.tr TCP 80/443, docs/kamusm.md), SMTP (the
      server of [smtp] in arcilogger.toml when enabled = true and host is set, on [smtp] port,
      default 587; and the host:port of -SmtpServer, if given, as a rule of its own) and the
      replica share (TCP 445 to the [replica] target host). Enabled outbound
      allow rules outside these and -KeepRuleGroup are disabled and recorded like the inbound ones.
      Windows Defender Firewall rules take IP addresses, so host names are resolved now: run the
      script again if a provider changes its addresses. Windows Update and Defender signature
      updates are blocked in strict mode: switch it off for maintenance (-StrictOutbound:$false).

    Choices are remembered in the state file: -StrictOutbound, -AdminAddress, -AllowWinRM,
    -KeepRule and -SmtpServer given once stay in force for later runs that do not name them (for
    example the installer during an upgrade, which passes none of them). Name them again to change
    them (-StrictOutbound:$false, -AllowWinRM:$false, -KeepRule @(), -SmtpServer ''). An empty
    value does not reach the script through powershell.exe -File (Windows PowerShell 5.1 drops an
    empty argument to a program, cmd.exe passes '' as two characters): pass it with -Command
    instead (docs/INSTALL.md section 12):
        powershell -ExecutionPolicy Bypass -Command "& '<path>\Set-ArciFirewall.ps1' -SmtpServer ''"

    -Remove deletes the ARCILOGGER rules, enables the recorded rules again and restores the
    profile settings (enabled, default inbound and outbound action) recorded at the first run.

    -SyncFromConfig is the input-free step of the SYSTEM task \ARCILOGGER\ApplySystem
    (Sync-ArciSystem.ps1, docs/adr/0014-browser-administration.md): -SourceAddress (the enabled
    senders system-plan derived, may be empty) and, in strict outbound mode, -Outbound (rule,
    host, port) are checked against admin\policy.json (-PolicyPath: the networks a sender must lie
    in and the widest network) before anything changes. It then updates the two syslog rules in
    place (Set-NetFirewallAddressFilter), creates them when the first sender appears and removes
    them when the last one goes (an empty RemoteAddress would mean any address), and in strict
    mode updates the Out NTP / Kamu SM / SMTP / Replica rules in place (a host that does not
    resolve keeps its rule as it is). It never disables or removes another rule, never changes a
    profile, needs no -AdminAddress, skips the remote desktop check and never prompts. Exit codes:
    0 done, 2 refused (nothing changed), 3 not managed (no state file: an installation with
    -SkipFirewall). The full mode above stays for the installer and the console.
    -KeepSourceAddress: enabled sources the policy leaves out (system-plan's
    source_outside_policy). One that both syslog rules hold now stays in them (an administrator
    opened it, and narrowing it away would drop its logs); one they do not hold is never added.

    All ARCILOGGER rules are in the rule group "ARCILOGGER". Safe to run again (idempotent).

.PARAMETER Force
    Apply the remote desktop restriction even when a session from another address is open
    (that session would be cut off).

.EXAMPLE
    .\Set-ArciFirewall.ps1
.EXAMPLE
    .\Set-ArciFirewall.ps1 -AdminAddress 10.0.0.10 -StrictOutbound -SmtpServer mail.example.com:587
.EXAMPLE
    .\Set-ArciFirewall.ps1 -DryRun -PlanPath plan.json
.EXAMPLE
    .\Set-ArciFirewall.ps1 -SyncFromConfig -SourceAddress 172.20.10.1, 172.20.20.0/24 -DryRun -PlanPath sync.json
#>
[CmdletBinding()]
param(
    [string]$ConfigPath = $(if ($env:ProgramData) { Join-Path $env:ProgramData 'ARCILOGGER\arcilogger.toml' } else { 'C:\ProgramData\ARCILOGGER\arcilogger.toml' }),
    [string]$Python = $(if ($env:ProgramFiles) { Join-Path $env:ProgramFiles 'ARCILOGGER\venv\Scripts\python.exe' } else { 'C:\Program Files\ARCILOGGER\venv\Scripts\python.exe' }),
    [string[]]$SourceAddress,
    [string[]]$AdminAddress,
    [int]$SyslogPort = 514,
    [int]$HttpsPort = 443,
    [int]$RdpPort = 3389,
    [switch]$AllowWinRM,
    [int]$WinRMPort = 5985,
    [string[]]$KeepRuleGroup = @('@FirewallAPI.dll,-25000', 'Core Networking'),
    [string[]]$KeepRule = @(),
    [switch]$StrictOutbound,
    [string[]]$DnsServer,
    [string]$SmtpServer,
    [string[]]$KamuSmHost = @('zd.kamusm.gov.tr', 'tzd.kamusm.gov.tr'),
    [string]$KamuSmCertificateHost = 'sertifikalar.kamusm.gov.tr',
    [string]$StateFile = $(if ($env:ProgramData) { Join-Path $env:ProgramData 'ARCILOGGER\install\firewall-state.json' } else { 'C:\ProgramData\ARCILOGGER\install\firewall-state.json' }),
    [switch]$Remove,
    # ApplySystem's in-place update (see .DESCRIPTION): -SourceAddress and -Outbound come from
    # system-plan, admin\policy.json bounds them.
    [switch]$SyncFromConfig,
    [object[]]$Outbound,
    [string[]]$KeepSourceAddress,
    # Tests only (refused without -SyncFromConfig -DryRun): the addresses the syslog rules hold now.
    [string[]]$LiveSourceAddress,
    [string]$PolicyPath = $(if ($env:ProgramData) { Join-Path $env:ProgramData 'ARCILOGGER\admin\policy.json' } else { 'C:\ProgramData\ARCILOGGER\admin\policy.json' }),
    [switch]$DryRun,
    [string]$PlanPath,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
Import-Module (Join-Path (Split-Path -Parent $PSScriptRoot) 'installer\ArciInstall.psm1') -Force
Initialize-ArciModulePath

$Group = 'ARCILOGGER'

function Resolve-HostAddresses([string]$HostName) {
    if (Test-ArciAddressSpec $HostName) { return , @($HostName) }
    try {
        $found = [System.Net.Dns]::GetHostAddresses($HostName) | Where-Object { $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork } | ForEach-Object { $_.ToString() }
        return , @($found)
    }
    catch {
        Write-ArciWarn ("Ad çözümlenemedi [cannot resolve]: {0}" -f $HostName)
        return , @()
    }
}

function Add-Unique([System.Collections.Generic.List[string]]$List, [string[]]$Values) {
    foreach ($value in $Values) { if ($value -and -not $List.Contains($value)) { $List.Add($value) } }
}

function New-RuleSpec([string]$Name, [string]$Direction, [string]$Protocol, [string[]]$Ports, [string[]]$RemoteAddresses, [string]$Purpose) {
    [pscustomobject]@{
        name           = $Name
        direction      = $Direction
        protocol       = $Protocol
        ports          = @($Ports)
        remote_address = @($RemoteAddresses)
        action         = 'Allow'
        purpose        = $Purpose
    }
}

function Read-FirewallState {
    <# The state file: rules ARCILOGGER disabled, profile settings before ARCILOGGER, strict mode. #>
    $state = [pscustomobject]@{
        schema = 1; strict_outbound = $false; admin_address = @(); allow_winrm = $false; keep_rules = @()
        smtp_server = $null; disabled_rules = @(); previous_profiles = @()
    }
    if (-not (Test-Path -LiteralPath $StateFile)) { return $state }
    # Assign first: Windows PowerShell 5.1 passes a JSON array on as one object.
    $document = [System.IO.File]::ReadAllText($StateFile, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($document -is [System.Array]) {
        # First format: a plain list of the inbound rules that were disabled.
        $state.disabled_rules = @($document | Where-Object { $_ } | ForEach-Object { [pscustomobject]@{ name = [string]$_; display_name = [string]$_; direction = 'Inbound' } })
        return $state
    }
    foreach ($name in @('strict_outbound', 'admin_address', 'allow_winrm', 'keep_rules', 'smtp_server', 'disabled_rules', 'previous_profiles')) {
        if ($null -ne $document -and $document.PSObject.Properties.Name -contains $name -and $null -ne $document.$name) { $state.$name = $document.$name }
    }
    foreach ($name in @('admin_address', 'keep_rules', 'disabled_rules', 'previous_profiles')) {
        $state.$name = @($state.$name | Where-Object { $_ })
    }
    $state
}

function Write-FirewallState($State) {
    $State | Add-Member -NotePropertyName 'updated_at' -NotePropertyValue ([DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')) -Force
    Write-ArciUtf8File -Path $StateFile -Text ($State | ConvertTo-Json -Depth 5)
}

function Test-KeptRule($Rule) {
    if ($Rule.Group -eq $Group) { return $true }
    if ($KeepRule -contains [string]$Rule.Name -or $KeepRule -contains [string]$Rule.DisplayName) { return $true }
    ($KeepRuleGroup -contains [string]$Rule.Group) -or ($KeepRuleGroup -contains [string]$Rule.DisplayGroup)
}

function Get-KeptGroupRules {
    <# All rules (enabled or not) of the -KeepRuleGroup groups: none means the group name is wrong. #>
    @(Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_ -and (($KeepRuleGroup -contains [string]$_.Group) -or ($KeepRuleGroup -contains [string]$_.DisplayGroup)) })
}

function Get-OtherAllowRules([string]$Direction) {
    <# Enabled allow rules of this direction that ARCILOGGER does not keep (local rules only). #>
    @(Get-NetFirewallRule -Direction $Direction -Action Allow -Enabled True -ErrorAction SilentlyContinue | Where-Object { $_ -and -not (Test-KeptRule $_) })
}

# --- -SyncFromConfig (ApplySystem, ADR-0014) --------------------------------------------------------

function ConvertFrom-RuleAddress([string]$Value) {
    <# A RemoteAddress of a firewall rule ("172.20.20.0/255.255.255.0", "172.20.10.1") as system-plan writes it, or $null (Any, a range, IPv6). #>
    $match = [regex]::Match($Value, '^(?<ip>[0-9.]+)/(?<mask>[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)$')
    if ($match.Success) {
        $mask = $null
        if (-not [System.Net.IPAddress]::TryParse($match.Groups['mask'].Value, [ref]$mask)) { return $null }
        $bits = (@($mask.GetAddressBytes() | ForEach-Object { [Convert]::ToString([int]$_, 2).PadLeft(8, '0') }) -join '')
        if ($bits -notmatch '^1*0*$') { return $null }
        $prefix = $bits.IndexOf('0')
        $Value = $(if ($prefix -lt 0) { $match.Groups['ip'].Value } else { '{0}/{1}' -f $match.Groups['ip'].Value, $prefix })
    }
    ConvertTo-ArciSyslogSender -Value $Value
}

function Get-LiveSyslogSenders {
    <# The senders both ARCILOGGER syslog rules hold now (-LiveSourceAddress in a test's dry run); none off Windows. #>
    if ($script:LiveGiven) {
        return , @($LiveSourceAddress | Where-Object { $_ } | ForEach-Object { ConvertFrom-RuleAddress $_ } | Where-Object { $_ })
    }
    if (-not (Test-ArciWindows)) { return , @() }
    $held = $null
    foreach ($name in @('ARCILOGGER Syslog UDP', 'ARCILOGGER Syslog TCP')) {
        $rules = @(Get-NetFirewallRule -Group $Group -ErrorAction SilentlyContinue | Where-Object { $_ -and [string]$_.DisplayName -eq $name })
        $addresses = @($rules | Get-NetFirewallAddressFilter | ForEach-Object { @($_.RemoteAddress) } | ForEach-Object { ConvertFrom-RuleAddress ([string]$_) } | Where-Object { $_ })
        if ($null -eq $held) { $held = $addresses } else { $held = @($held | Where-Object { $addresses -contains $_ }) }
    }
    , @($held)
}

function Invoke-SyncFromConfig {
    <#
    The input-free firewall step of ApplySystem (contract 2.8.3 step 4). The syslog senders
    (-SourceAddress, may be empty) and, in strict outbound mode, the outbound hosts (-Outbound:
    rule, host, port) come from system-plan; every value is checked against admin\policy.json
    (-PolicyPath) before any change, and a refused one changes nothing. The two syslog rules are
    updated in place (Set-NetFirewallAddressFilter), created when the list becomes non-empty and
    removed when it becomes empty (an empty RemoteAddress would mean any address); in strict mode
    the Out NTP / Kamu SM / SMTP / Replica rules of the plan are updated in place, a rule whose
    host does not resolve now keeps its addresses. Nothing else is touched: no other rule is
    disabled or removed, no profile changes, the 443, 3389 and 5985 rules and the operator's
    choices of the state file stay as they are. Never prompts. Returns the exit code: 0 done,
    2 refused (nothing changed), 3 not managed (no state file: -SkipFirewall).
    #>
    $policy = Read-ArciAdminPolicy -Path $PolicyPath
    $managed = Test-Path -LiteralPath $StateFile -PathType Leaf
    $strict = $false
    if ($managed) { $strict = [bool](Read-FirewallState).strict_outbound }
    $senders = @($SourceAddress | Where-Object { $_ })
    $problems = New-Object System.Collections.Generic.List[string]
    $warnings = New-Object System.Collections.Generic.List[string]
    if (-not $policy.Valid -or -not $policy.Enabled) {
        $problems.Add(('Tarayıcı yönetimi kapalı ya da policy.json geçersiz [browser administration off or policy.json invalid]: {0}; tam kipi kullanın [use the full mode]' -f $(if ($policy.Problem) { $policy.Problem } else { 'enabled = false' })))
    }
    else {
        foreach ($value in $senders) {
            $why = Get-ArciSyslogSenderProblem -Value $value -SourceNetwork @($policy.SourceNetworks) -MinPrefixV4 $policy.MinPrefixV4
            if ($why) { $problems.Add(('{0}: {1}' -f $value, $why)) }
        }
        if (@($senders | Select-Object -Unique).Count -ne $senders.Count) { $problems.Add('aynı gönderici iki kez [a sender given twice]') }
    }
    $outbound = @($Outbound | Where-Object { $null -ne $_ })
    foreach ($entry in $outbound) {
        $why = Get-ArciOutboundProblem -Rule ([string]$entry.rule) -HostName ([string]$entry.host) -Port $entry.port
        if ($why) { $problems.Add($why) }
    }
    # Sources outside the policy: kept where both rules hold them now, never added.
    $wanted = @($KeepSourceAddress | Where-Object { $_ } | ForEach-Object { ConvertTo-ArciSyslogSender -Value $_ } | Where-Object { $_ -and $senders -notcontains $_ } | Select-Object -Unique)
    $kept = @()
    $notKept = @()
    if ($wanted.Count -gt 0) {
        $live = Get-LiveSyslogSenders
        $kept = @($wanted | Where-Object { $live -contains $_ })
        $notKept = @($wanted | Where-Object { $live -notcontains $_ })
    }
    foreach ($value in $kept) { $warnings.Add(('{0}: politika ağlarının dışında, kuralda zaten açık; açık bırakıldı [outside the policy networks, open in the rule already: kept]' -f $value)) }
    foreach ($value in $notKept) { $warnings.Add(('{0}: politika ağlarının dışında, açılmadı [outside the policy networks: not opened]' -f $value)) }
    $remote = @($senders) + @($kept)
    $changes = New-Object System.Collections.Generic.List[object]
    foreach ($pair in @(@('ARCILOGGER Syslog UDP', 'UDP'), @('ARCILOGGER Syslog TCP', 'TCP'))) {
        $operation = $(if ($remote.Count -gt 0) { 'set' } else { 'remove' })
        $changes.Add([pscustomobject]@{ rule = $pair[0]; direction = 'Inbound'; protocol = $pair[1]; ports = @("$SyslogPort"); remote_address = $remote; hosts = @(); operation = $operation; reason = $null })
    }
    if ($strict) {
        foreach ($name in @($outbound | ForEach-Object { [string]$_.rule } | Select-Object -Unique)) {
            $entries = @($outbound | Where-Object { [string]$_.rule -eq $name })
            $hosts = @($entries | ForEach-Object { [string]$_.host } | Select-Object -Unique)
            $ports = @($entries | ForEach-Object { [string]$_.port } | Select-Object -Unique)
            $addresses = New-Object System.Collections.Generic.List[string]
            $unresolved = @()
            foreach ($target in $hosts) {
                # Assigned first: the list comes back as one object.
                $found = Resolve-HostAddresses $target
                if (@($found).Count -eq 0) { $unresolved += $target }
                Add-Unique $addresses @($found)
            }
            $protocol = $(if ($name -eq 'ARCILOGGER Out NTP') { 'UDP' } else { 'TCP' })
            $change = [pscustomobject]@{ rule = $name; direction = 'Outbound'; protocol = $protocol; ports = $ports; remote_address = $addresses.ToArray(); hosts = $hosts; operation = 'set'; reason = $null }
            if ($unresolved.Count -gt 0) {
                # Offline DNS: the rule keeps the addresses it has (contract 2.18 R8).
                $change.operation = 'keep'
                $change.reason = 'çözümlenemedi [not resolved]: {0}' -f ($unresolved -join ', ')
                $warnings.Add(('{0}: {1}' -f $name, $change.reason))
            }
            $changes.Add($change)
        }
    }
    elseif ($outbound.Count -gt 0) {
        $warnings.Add('Sıkı giden kip kapalı: giden kurallar değişmez [strict outbound mode is off: no outbound rule changes]')
    }
    $code = 0
    if (-not $managed) { $code = 3 }
    elseif ($problems.Count -gt 0) { $code = 2 }
    $plan = [pscustomobject]@{
        group           = $Group
        mode            = 'sync'
        managed         = $managed
        strict_outbound = $strict
        policy          = [pscustomobject]@{ valid = $policy.Valid; enabled = $policy.Enabled; source_networks = @($policy.SourceNetworks); min_prefix_v4 = $policy.MinPrefixV4 }
        changes         = @(if ($code -eq 0) { $changes.ToArray() })
        kept            = @(if ($code -eq 0) { $kept })
        not_kept        = @($notKept)
        profiles        = 'unchanged'
        other_rules     = 'unchanged'
        would_disable   = $null
        problems        = $problems.ToArray()
        warnings        = $warnings.ToArray()
        exit_code       = $code
        state_file      = $StateFile
    }
    if ($PlanPath) { Write-ArciUtf8File -Path $PlanPath -Text ($plan | ConvertTo-Json -Depth 6) }
    foreach ($warning in $warnings) { Write-ArciWarn $warning }
    if ($code -eq 3) {
        Write-ArciInfo ('Güvenlik duvarı ARCILOGGER tarafından yönetilmiyor (durum dosyası yok, -SkipFirewall) [firewall not managed by ARCILOGGER]: {0}' -f $StateFile)
        return 3
    }
    if ($code -eq 2) {
        foreach ($problem in $problems) { Write-Host ('    [HATA]  ' + $problem) }
        Write-ArciStep 'Reddedildi, hiçbir kural değişmedi [refused, no rule changed].'
        return 2
    }
    foreach ($change in $changes) {
        Write-ArciStep ("{0}: {1} {2} {3} <- {4}" -f $change.rule, $change.operation, $change.protocol, ($change.ports -join ','), ($change.remote_address -join ', '))
    }
    if ($DryRun) {
        Write-ArciInfo 'Deneme çalıştırması; güvenlik duvarı değiştirilmedi [dry run, firewall unchanged].'
        return 0
    }
    if (-not (Test-ArciWindows)) { throw 'Bu betik yalnızca Windows üzerinde çalışır [Windows only].' }
    foreach ($change in $changes) {
        $existing = @(Get-NetFirewallRule -Group $Group -ErrorAction SilentlyContinue | Where-Object { $_ -and [string]$_.DisplayName -eq $change.rule })
        if ($change.operation -eq 'keep') { continue }
        if ($change.operation -eq 'remove') {
            foreach ($rule in $existing) { $rule | Remove-NetFirewallRule }
            continue
        }
        if ($existing.Count -gt 0) {
            # In place: the rule never disappears, so no datagram meets a moment without it.
            foreach ($rule in $existing) {
                $rule | Get-NetFirewallAddressFilter | Set-NetFirewallAddressFilter -RemoteAddress $change.remote_address
                if ($change.direction -eq 'Outbound') { $rule | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -RemotePort $change.ports }
            }
            continue
        }
        $arguments = @{
            DisplayName = $change.rule; Group = $Group; Direction = $change.direction; Protocol = $change.protocol
            RemoteAddress = $change.remote_address; Action = 'Allow'; Profile = 'Any'; Enabled = 'True'
            Description = ('ARCILOGGER: {0}' -f $(if ($change.direction -eq 'Inbound') { 'syslog from configured senders' } else { 'strict outbound (ApplySystem)' }))
        }
        if ($change.direction -eq 'Inbound') { $arguments['LocalPort'] = $change.ports } else { $arguments['RemotePort'] = $change.ports }
        New-NetFirewallRule @arguments | Out-Null
    }
    Write-ArciStep 'Güvenlik duvarı eşitlendi [firewall in step with the configuration].'
    0
}

$script:LiveGiven = $PSBoundParameters.ContainsKey('LiveSourceAddress')
if ($script:LiveGiven -and -not ($SyncFromConfig -and $DryRun)) {
    throw '-LiveSourceAddress yalnızca -SyncFromConfig -DryRun ile [only with -SyncFromConfig -DryRun]: kuralların adresleri her zaman kuralların kendisinden okunur [the rules'' addresses are always read from the rules].'
}

if ($SyncFromConfig) {
    $syncCode = Invoke-SyncFromConfig
    exit $syncCode
}

# --- configuration and mode ------------------------------------------------------------------------

$state = Read-FirewallState
$strictSource = 'parameter'
if ($PSBoundParameters.ContainsKey('StrictOutbound')) { $strict = [bool]$StrictOutbound }
elseif ([bool]$state.strict_outbound) { $strict = $true; $strictSource = 'state file' }
else { $strict = $false; $strictSource = 'default' }
# The other operator choices: a parameter wins, then the state file, then the default.
if (-not $PSBoundParameters.ContainsKey('AdminAddress') -and @($state.admin_address).Count -gt 0) { $AdminAddress = @($state.admin_address) }
# The admin PC's address has no default: neither given nor remembered, it is asked at the console.
$AdminAddress = @($AdminAddress | Where-Object { $_ })
if (-not $Remove -and $AdminAddress.Count -eq 0 -and (Test-ArciInteractiveConsole)) { $AdminAddress = @(Read-ArciAdminAddress) }
if (-not $PSBoundParameters.ContainsKey('AllowWinRM') -and [bool]$state.allow_winrm) { $AllowWinRM = [switch]$true }
if (-not $PSBoundParameters.ContainsKey('KeepRule') -and @($state.keep_rules).Count -gt 0) { $KeepRule = @($state.keep_rules) }
if (-not $PSBoundParameters.ContainsKey('SmtpServer') -and $state.smtp_server) { $SmtpServer = [string]$state.smtp_server }

$config = $null
if (-not $Remove -and ((-not $SourceAddress) -or $strict)) {
    if (-not (Test-Path -LiteralPath $ConfigPath)) { throw ("Yapılandırma yok [config missing]: {0}" -f $ConfigPath) }
    if (-not (Test-Path -LiteralPath $Python)) { throw ("Python yok [python missing]: {0}" -f $Python) }
    $config = Read-ArciTomlFile -Python $Python -ConfigPath $ConfigPath
}
if (-not $SourceAddress -and $null -ne $config) { $SourceAddress = @(Get-ArciSourceAddresses -Config $config) }
# Assigned through the filter: @($null) would be one (invalid) address.
$SourceAddress = @($SourceAddress | Where-Object { $_ })

# --- what to allow ---------------------------------------------------------------------------------

$rules = New-Object System.Collections.Generic.List[object]
if (-not $Remove) {
    if (@($AdminAddress).Count -eq 0) {
        throw 'Yönetici PC''sinin adresi yok [no admin PC address]: 443 (web) ve 3389 (uzak masaüstü) yalnızca ondan açılır. -AdminAddress ile verin, örnek -AdminAddress 10.0.0.10 ya da bir ağ: -AdminAddress 10.0.0.0/24; değer hatırlanır [443 and 3389 are opened only for it: give -AdminAddress, e.g. 10.0.0.10 or a network such as 10.0.0.0/24; it is remembered].'
    }
    foreach ($value in @($SourceAddress) + @($AdminAddress)) {
        if (-not (Test-ArciAddressSpec $value)) { throw ("Geçersiz adres [invalid address]: {0} (örnek 10.0.0.10 veya 10.0.0.0/24)" -f $value) }
    }
    if ($SourceAddress.Count -gt 0) {
        $rules.Add((New-RuleSpec 'ARCILOGGER Syslog UDP' 'Inbound' 'UDP' @("$SyslogPort") $SourceAddress 'syslog from configured senders'))
        $rules.Add((New-RuleSpec 'ARCILOGGER Syslog TCP' 'Inbound' 'TCP' @("$SyslogPort") $SourceAddress 'syslog from configured senders'))
    }
    else {
        # No log source yet (a new installation): a valid state, but no device can send. The
        # default Block action keeps 514 closed; nothing else is opened in its place.
        Write-ArciWarn ("Hiç syslog göndericisi yok ([[sources]] boş): {0} kapalı kalır, hiçbir log kaydedilmez. Güvenlik duvarınızın ve ana yönlendiricinizin [[sources]] kayıtlarını {1} dosyasına ekleyip bu betiği yeniden çalıştırın (docs\DEVICE-SETUP.md bölüm 4) [no syslog sender configured: {0} stays closed and no log is recorded; add the [[sources]] of your firewall and core router and run this script again]." -f "UDP/TCP $SyslogPort", $ConfigPath)
    }
    $rules.Add((New-RuleSpec 'ARCILOGGER HTTPS' 'Inbound' 'TCP' @("$HttpsPort") $AdminAddress 'web interface from the admin PC'))
    $rules.Add((New-RuleSpec 'ARCILOGGER RDP' 'Inbound' 'TCP' @("$RdpPort") $AdminAddress 'remote desktop from the admin PC'))
    $rules.Add((New-RuleSpec 'ARCILOGGER RDP UDP' 'Inbound' 'UDP' @("$RdpPort") $AdminAddress 'remote desktop (UDP transport) from the admin PC'))
    if ($AllowWinRM) {
        $rules.Add((New-RuleSpec 'ARCILOGGER WinRM' 'Inbound' 'TCP' @("$WinRMPort") $AdminAddress 'PowerShell remoting / Server Manager from the admin PC'))
    }

    if ($strict) {
        $ntp = New-Object System.Collections.Generic.List[string]
        foreach ($name in @((Get-ArciConfigValue -Config $config -Path 'clock.ntp_server' -Default ''), (Get-ArciConfigValue -Config $config -Path 'clock.fallback_server' -Default 'time.ume.tubitak.gov.tr'))) {
            if ($name) { Add-Unique $ntp (Resolve-HostAddresses $name) }
        }
        if ($ntp.Count -gt 0) { $rules.Add((New-RuleSpec 'ARCILOGGER Out NTP' 'Outbound' 'UDP' @('123') $ntp.ToArray() 'time')) }

        if (-not $DnsServer -and (Get-Command Get-DnsClientServerAddress -ErrorAction SilentlyContinue)) {
            $DnsServer = Get-DnsClientServerAddress -AddressFamily IPv4 | ForEach-Object { $_.ServerAddresses } | Sort-Object -Unique
        }
        if ($DnsServer) {
            $rules.Add((New-RuleSpec 'ARCILOGGER Out DNS UDP' 'Outbound' 'UDP' @('53') $DnsServer 'name resolution'))
            $rules.Add((New-RuleSpec 'ARCILOGGER Out DNS TCP' 'Outbound' 'TCP' @('53') $DnsServer 'name resolution'))
        }

        # Kamu SM: both servers (test and production, so switching [tsa] url needs no firewall
        # change) plus whatever [tsa] url names, on the port the time-stamp client uses.
        $tsaHosts = New-Object System.Collections.Generic.List[string]
        Add-Unique $tsaHosts $KamuSmHost
        $tsaPorts = New-Object System.Collections.Generic.List[string]
        $tsaUrl = [string](Get-ArciConfigValue -Config $config -Path 'tsa.url' -Default '')
        $uri = $null
        if ($tsaUrl -and [System.Uri]::TryCreate($tsaUrl, [System.UriKind]::Absolute, [ref]$uri) -and $uri.Host) {
            Add-Unique $tsaHosts @($uri.Host)
            Add-Unique $tsaPorts @([string]$uri.Port)
        }
        Add-Unique $tsaPorts @([string](Get-ArciConfigValue -Config $config -Path 'tsa.port' -Default 80))
        $kamusm = New-Object System.Collections.Generic.List[string]
        foreach ($name in $tsaHosts) { Add-Unique $kamusm (Resolve-HostAddresses $name) }
        if ($kamusm.Count -gt 0) { $rules.Add((New-RuleSpec 'ARCILOGGER Out Kamu SM' 'Outbound' 'TCP' $tsaPorts.ToArray() $kamusm.ToArray() 'time stamps (hash only)')) }
        $certificates = Resolve-HostAddresses $KamuSmCertificateHost
        if (@($certificates).Count -gt 0) { $rules.Add((New-RuleSpec 'ARCILOGGER Out Kamu SM certificates' 'Outbound' 'TCP' @('80', '443') $certificates 'roots and CRLs')) }

        # SMTP: the server the alert e-mail uses ([smtp] of arcilogger.toml, read on every run, so
        # an upgrade keeps the rule) and the host:port of -SmtpServer (remembered). One rule per
        # distinct server; a host name is resolved now, like the other named hosts.
        $smtpTargets = New-Object System.Collections.Generic.List[object]
        $smtpHost = ([string](Get-ArciConfigValue -Config $config -Path 'smtp.host' -Default '')).Trim()
        if ((Get-ArciConfigValue -Config $config -Path 'smtp.enabled' -Default $false) -eq $true -and $smtpHost) {
            $smtpTargets.Add([pscustomobject]@{ host = $smtpHost; port = [string](Get-ArciConfigValue -Config $config -Path 'smtp.port' -Default 587); purpose = 'alert e-mail ([smtp])' })
        }
        if ($SmtpServer) {
            $smtpParts = $SmtpServer.Split(':')
            $smtpPort = if ($smtpParts.Count -gt 1) { $smtpParts[1] } else { '587' }
            $same = @($smtpTargets | Where-Object { $_.host -eq $smtpParts[0] -and $_.port -eq $smtpPort })
            if ($same.Count -eq 0) { $smtpTargets.Add([pscustomobject]@{ host = $smtpParts[0]; port = $smtpPort; purpose = 'e-mail (-SmtpServer)' }) }
        }
        $smtpRules = 0
        foreach ($target in $smtpTargets) {
            $port = 0
            if (-not [int]::TryParse($target.port, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
                throw ("SMTP portu geçersiz [invalid SMTP port]: {0} ({1})" -f $target.port, $target.purpose)
            }
            $smtp = Resolve-HostAddresses $target.host
            if (@($smtp).Count -eq 0) { continue }
            $smtpRules++
            $name = $(if ($smtpRules -eq 1) { 'ARCILOGGER Out SMTP' } else { 'ARCILOGGER Out SMTP {0}' -f $smtpRules })
            $rules.Add((New-RuleSpec $name 'Outbound' 'TCP' @([string]$port) $smtp $target.purpose))
        }

        $replicaTarget = Get-ArciConfigValue -Config $config -Path 'replica.target'
        if ((Get-ArciConfigValue -Config $config -Path 'replica.enabled' -Default $false) -and $replicaTarget) {
            $target = ([string]$replicaTarget).Replace('\', '/').TrimStart('/')
            $replicaHost = $target.Split('/')[0]
            $replica = Resolve-HostAddresses $replicaHost
            if (@($replica).Count -gt 0) { $rules.Add((New-RuleSpec 'ARCILOGGER Out Replica' 'Outbound' 'TCP' @('445') $replica 'second copy (SMB)')) }
        }
    }
}

# Which existing rules would be switched off, and how many rules the kept groups have (read-only;
# needs the NetSecurity module, so only on Windows).
$wouldDisable = $null
$keptGroupRuleCount = $null
if (-not $Remove -and (Get-Command Get-NetFirewallRule -ErrorAction SilentlyContinue)) {
    try {
        $directions = @('Inbound') + $(if ($strict) { @('Outbound') } else { @() })
        $wouldDisable = @(foreach ($direction in $directions) {
                Get-OtherAllowRules $direction | ForEach-Object {
                    [pscustomobject]@{ name = $_.Name; display_name = $_.DisplayName; group = $_.Group; direction = $direction }
                }
            })
        $keptGroupRuleCount = @(Get-KeptGroupRules).Count
    }
    catch { $wouldDisable = $null }
}

$plan = [pscustomobject]@{
    group                  = $Group
    remove                 = [bool]$Remove
    strict_outbound        = $strict
    strict_outbound_source = $strictSource
    admin_address          = @($AdminAddress)
    allow_winrm            = [bool]$AllowWinRM
    default_inbound        = 'Block'
    default_outbound       = $(if ($strict) { 'Block' } else { 'Allow' })
    other_inbound_rules    = 'disabled (recorded for -Remove), except -KeepRuleGroup and -KeepRule'
    other_outbound_rules   = $(if ($strict) { 'disabled (recorded for -Remove), except -KeepRuleGroup and -KeepRule' } else { 'unchanged' })
    keep_rule_groups       = @($KeepRuleGroup)
    keep_rules             = @($KeepRule)
    smtp_server            = $(if ($SmtpServer) { $SmtpServer } else { $null })
    rules                  = $rules.ToArray()
    would_disable          = $wouldDisable
    kept_group_rules       = $keptGroupRuleCount
    state_file             = $StateFile
}
if ($PlanPath) { Write-ArciUtf8File -Path $PlanPath -Text ($plan | ConvertTo-Json -Depth 6) }

foreach ($rule in $rules) {
    Write-ArciStep ("{0}: {1} {2} {3} <- {4}" -f $rule.name, $rule.direction, $rule.protocol, ($rule.ports -join ','), ($rule.remote_address -join ', '))
}
if (-not $Remove) {
    Write-ArciInfo ("Giden trafik [outbound]: {0} ({1})" -f $(if ($strict) { 'yalnızca izin verilenler [strict]' } else { 'serbest [allowed]' }), $strictSource)
}
if ($DryRun) {
    Write-ArciInfo 'Deneme çalıştırması; güvenlik duvarı değiştirilmedi [dry run, firewall unchanged].'
    exit 0
}

if (-not (Test-ArciWindows)) { throw 'Bu betik yalnızca Windows üzerinde çalışır [Windows only].' }
if (-not (Test-ArciAdministrator)) { throw 'Yönetici olarak çalıştırın [run as Administrator].' }

# --- remove --------------------------------------------------------------------------------------

if ($Remove) {
    Get-NetFirewallRule -Group $Group -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    foreach ($entry in @($state.disabled_rules)) {
        $name = if ($entry -is [string]) { $entry } else { [string]$entry.name }
        $rule = Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
        if ($rule) { $rule | Enable-NetFirewallRule; Write-ArciInfo ("yeniden açıldı [re-enabled]: {0}" -f $rule.DisplayName) }
    }
    foreach ($saved in @($state.previous_profiles)) {
        Set-NetFirewallProfile -Name $saved.name -Enabled $saved.enabled -DefaultInboundAction $saved.default_inbound -DefaultOutboundAction $saved.default_outbound
        Write-ArciInfo ("profil geri yüklendi [profile restored]: {0} (inbound {1}, outbound {2})" -f $saved.name, $saved.default_inbound, $saved.default_outbound)
    }
    if (Test-Path -LiteralPath $StateFile) { Remove-Item -LiteralPath $StateFile -Force }
    Write-ArciStep 'ARCILOGGER güvenlik duvarı kuralları kaldırıldı [rules removed].'
    exit 0
}

# --- checks before any change ----------------------------------------------------------------------

if (-not $Force) {
    $foreign = @(Get-ArciForeignRdpSession -AdminAddress $AdminAddress -Port $RdpPort)
    if ($foreign.Count -gt 0) {
        throw ("Açık uzak masaüstü oturumu {0} adresinden; bu kural onu keserdi. -AdminAddress'e ekleyin veya -Force kullanın. [An RDP session from {0} would be cut off.]" -f ($foreign -join ', '))
    }
}
if (@(Get-KeptGroupRules).Count -eq 0) {
    throw ("-KeepRuleGroup hiçbir kurala uymuyor: {0}. 'Core Networking' grubunun adını denetleyin: Get-NetFirewallRule -DisplayGroup '<ad>' | Select-Object -First 1 Group, DisplayGroup [no rule matches the groups to keep; nothing was changed]." -f ($KeepRuleGroup -join ', '))
}

# --- apply ---------------------------------------------------------------------------------------

if (@($state.previous_profiles).Count -eq 0) {
    $state.previous_profiles = @(Get-NetFirewallProfile -All | ForEach-Object {
            [pscustomobject]@{ name = [string]$_.Name; enabled = [string]$_.Enabled; default_inbound = [string]$_.DefaultInboundAction; default_outbound = [string]$_.DefaultOutboundAction }
        })
}
$disabled = New-Object System.Collections.Generic.List[object]
foreach ($entry in @($state.disabled_rules)) {
    if ($entry -is [string]) { $disabled.Add([pscustomobject]@{ name = $entry; display_name = $entry; direction = 'Inbound' }) }
    else { $disabled.Add($entry) }
}
$state.strict_outbound = $strict
$state.admin_address = @($AdminAddress)
$state.allow_winrm = [bool]$AllowWinRM
$state.keep_rules = @($KeepRule)
$state.smtp_server = $(if ($SmtpServer) { $SmtpServer } else { $null })
# Recorded before the first change, so -Remove can always undo what follows.
$state.disabled_rules = $disabled.ToArray()
Write-FirewallState $state

Set-NetFirewallProfile -All -Enabled True -DefaultInboundAction Block
Get-NetFirewallRule -Group $Group -ErrorAction SilentlyContinue | Remove-NetFirewallRule
foreach ($rule in $rules) {
    $arguments = @{
        DisplayName   = $rule.name
        Group         = $Group
        Direction     = $rule.direction
        Protocol      = $rule.protocol
        RemoteAddress = $rule.remote_address
        Action        = 'Allow'
        Profile       = 'Any'
        Enabled       = 'True'
        Description   = ('ARCILOGGER: ' + $rule.purpose)
    }
    if ($rule.direction -eq 'Inbound') { $arguments['LocalPort'] = $rule.ports } else { $arguments['RemotePort'] = $rule.ports }
    New-NetFirewallRule @arguments | Out-Null
}

function Disable-OtherRules([string]$Direction) {
    foreach ($other in (Get-OtherAllowRules $Direction)) {
        $other | Disable-NetFirewallRule
        if (-not @($disabled | Where-Object { $_.name -eq $other.Name }).Count) {
            $disabled.Add([pscustomobject]@{ name = [string]$other.Name; display_name = [string]$other.DisplayName; direction = $Direction })
        }
        Write-ArciInfo ("kapatıldı [disabled] ({0}): {1}" -f $Direction, $other.DisplayName)
    }
    $state.disabled_rules = $disabled.ToArray()
    Write-FirewallState $state
}

Disable-OtherRules 'Inbound'
if ($strict) {
    Disable-OtherRules 'Outbound'
    Set-NetFirewallProfile -All -DefaultOutboundAction Block
}
else {
    # Strict mode off: the outbound rules it disabled come back, the default is as before ARCILOGGER.
    $keep = New-Object System.Collections.Generic.List[object]
    foreach ($entry in $disabled) {
        if ($entry.direction -eq 'Outbound') {
            $rule = Get-NetFirewallRule -Name $entry.name -ErrorAction SilentlyContinue
            if ($rule) { $rule | Enable-NetFirewallRule; Write-ArciInfo ("yeniden açıldı [re-enabled]: {0}" -f $rule.DisplayName) }
        }
        else { $keep.Add($entry) }
    }
    $disabled = $keep
    $state.disabled_rules = $disabled.ToArray()
    foreach ($saved in @($state.previous_profiles)) {
        Set-NetFirewallProfile -Name $saved.name -DefaultOutboundAction $saved.default_outbound
    }
    Write-FirewallState $state
}

Write-ArciStep 'Güvenlik duvarı kuralları uygulandı [firewall rules applied].'
Get-NetFirewallRule -Group $Group | Format-Table DisplayName, Direction, Enabled, Action -AutoSize
