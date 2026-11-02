<#
.SYNOPSIS
    ARCILOGGER sistem eşitlemesi: güvenlik duvarı, Windows Saati, Zamane istemcisi
    [system sync of the firewall, Windows Time and the Zamane client] (\ARCILOGGER\ApplySystem).

.DESCRIPTION
    Run as SYSTEM by the task \ARCILOGGER\ApplySystem (Event 4101 of ARCILOGGER-Admin and every
    5 minutes; docs/adr/0014-browser-administration.md, contract 2.8.3). It takes no input from a
    request: everything is derived from the validated arcilogger.toml (through the read-only
    "python -I -m arcilogger_core.admin system-plan" of the installed program), the
    administrator-only admin\policy.json and files it only hashes.

      1. admin\policy.json missing, invalid or enabled = false: nothing to do (exit 0).
      2. system-plan; a failure is reported (status.json ok = false, system_plan_failed,
         Event 4106 error) and ends the run (exit 1). When the configuration's SHA-256, the
         collector's acknowledgement, the policy's SHA-256 and the staged Zamane clients equal
         those of the last successful run, the run ends here (fast path, exit 0): only
         status.json is written again, with a new time (ArciAdmin's start-up check).
      3. Every address, host and file of the plan is checked again (policy networks, the host
         pattern, zamane\web\<sha256>.jar of this installation); one that fails stops the run
         before any change (system_plan_invalid).
      4. Firewall: Set-ArciFirewall.ps1 -SyncFromConfig with the plan's syslog senders and, in
         strict outbound mode, its outbound hosts (in place; never another rule, never a profile).
         An enabled source the policy leaves out (the plan's problems: source_outside_policy)
         stays in the syslog rules while they hold it (an administrator opened it), is never
         added, and is reported: status.json code source_outside_policy, an R20-admin alert.
         Without install\firewall-state.json (-SkipFirewall) the step is skipped
         (firewall_not_managed).
      5. Windows Time: Set-ArciTime.ps1 when the plan says the peers changed.
      6. Zamane clients: a staged client that is a known release is copied into zamane\approved
         after its hash is checked (a reparse point is refused; Copy-ArciApprovedJar checks the
         copy) and recorded in jars.json (approved_by "known"); a newly staged client is recorded
         with first_seen (Event 4104): it runs only after "approve-jar" on the console. A staged
         file whose SHA-256 is not its name is not recorded (payload_mismatch, R20-admin).
         jars.json is read again right before it is written and both lists are merged
         (Merge-ArciJarsDocument): an approval approve-jar wrote meanwhile stays.
      7. admin\system\status.json, a journal line per change (journal.jsonl, imported into the
         audit log as admin.system), Event 4106 (and 4107 per promoted client), an R20-admin
         alert when a step failed. A failed step leaves its own rules or files as they were
         (system_sync_failed) and the next run tries again.

    The script never reads a key the browser can change to find a folder: every path comes from
    the folder of the fixed -ConfigPath (Day 0 note D0-2), whose program, scripts and module are in
    the program folder that only administrators write.

.PARAMETER DryRun
    Change nothing; -ReportPath receives what the run would do (JSON). With -PlanPath a
    system-plan document is read instead of running system-plan (tests only: refused without
    -DryRun).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\Sync-ArciSystem.ps1 -DryRun -ReportPath sync.json
#>
[CmdletBinding()]
param(
    [string]$ConfigPath = $(if ($env:ProgramData) { Join-Path $env:ProgramData 'ARCILOGGER\arcilogger.toml' } else { 'C:\ProgramData\ARCILOGGER\arcilogger.toml' }),
    [string]$Python,
    [switch]$DryRun,
    [string]$PlanPath,
    [string]$ReportPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
Import-Module (Join-Path (Split-Path -Parent $PSScriptRoot) 'installer\ArciInstall.psm1') -Force
Initialize-ArciModulePath

# The program this script belongs to (its scripts folder is in the program folder, which only
# administrators write); set here, not as a parameter default: Windows PowerShell 5.1 leaves
# $PSScriptRoot empty there under -File (ADR-0007 section 7).
if (-not $Python) { $Python = Join-Path (Split-Path -Parent $PSScriptRoot) 'venv\Scripts\python.exe' }
if ($PlanPath -and -not $DryRun) { throw '-PlanPath yalnızca -DryRun ile [only with -DryRun]: ApplySystem planı her zaman system-plan''dan alır [always takes the plan from system-plan].' }

# Every path from the folder of the fixed configuration path, never from the configuration.
$ProgramDataDir = Split-Path -Parent ([System.IO.Path]::GetFullPath($ConfigPath))
$AdminDir = Join-Path $ProgramDataDir 'admin'
$SystemDir = Join-Path $AdminDir 'system'
$StatusPath = Join-Path $SystemDir 'status.json'
$JarsPath = Join-Path $SystemDir 'jars.json'
$JournalPath = Join-Path $SystemDir 'journal.jsonl'
$AlertsPath = Join-Path $SystemDir 'alerts.jsonl'
# The inputs of the last successful run (the fast path); ApplySystem's own file.
$SyncStatePath = Join-Path $SystemDir 'sync-state.json'
$PolicyPath = Join-Path $AdminDir 'policy.json'
$FirewallStatePath = Join-Path $ProgramDataDir 'install\firewall-state.json'
$ZamaneDir = Join-Path $ProgramDataDir 'zamane'
$StagingDir = Join-Path $ZamaneDir 'web'
$ApprovedDir = Join-Path $ZamaneDir 'approved'

$script:JarCounts = $null
$report = [ordered]@{
    dry_run = [bool]$DryRun; fast_path = $false; exit_code = 0; code = $null
    steps = (New-Object System.Collections.Generic.List[object]); problems = (New-Object System.Collections.Generic.List[string])
    firewall = $null; time = $null; jars = [ordered]@{ promote = @(); staged = @() }
    status = $null; journal = (New-Object System.Collections.Generic.List[object])
    events = (New-Object System.Collections.Generic.List[object]); alerts = (New-Object System.Collections.Generic.List[object])
}

function Save-Report {
    if ($ReportPath) { Write-ArciUtf8File -Path $ReportPath -Text (([pscustomobject]$report) | ConvertTo-Json -Depth 8) }
}

function Exit-Sync([int]$Code) {
    $report.exit_code = $Code
    Save-Report
    exit $Code
}

function Add-SyncStep([string]$Step, [string]$State, [string]$Code) {
    # A [string] parameter turns $null into '': the report says null for no code.
    $report.steps.Add([pscustomobject][ordered]@{ step = $Step; state = $State; code = $(if ($Code) { $Code } else { $null }) })
    $text = $(if ($Code) { '{0}: {1} ({2})' -f $Step, $State, $Code } else { '{0}: {1}' -f $Step, $State })
    Write-ArciInfo $text
}

function Send-SyncEvent([int]$Id, [string]$Level, [string]$Message) {
    $report.events.Add([pscustomobject][ordered]@{ id = $Id; level = $Level; message = $Message })
    if (-not $DryRun) { [void](Write-ArciEvent -Id $Id -Level $Level -Message $Message) }
}

function Write-SyncFile([string]$Path, [string]$Text) {
    <# Atomically: a temporary file in the same folder, then moved over the old one. #>
    $temp = Join-Path (Split-Path -Parent $Path) ('.{0}.tmp' -f (Split-Path -Leaf $Path))
    Write-ArciUtf8File -Path $temp -Text $Text
    Move-Item -LiteralPath $temp -Destination $Path -Force
}

function Add-SyncLine([string]$Path, $Document) {
    <# One JSON line appended (journal.jsonl, alerts.jsonl), on a line of its own. #>
    $line = ($Document | ConvertTo-Json -Depth 6 -Compress)
    if ($DryRun) { return }
    $prefix = ''
    if ((Test-Path -LiteralPath $Path) -and (Get-Item -LiteralPath $Path).Length -gt 0) {
        $bytes = [System.IO.File]::ReadAllBytes($Path)
        if ($bytes[$bytes.Length - 1] -ne 10) { $prefix = "`n" }
    }
    [System.IO.File]::AppendAllText($Path, $prefix + $line + "`n", (New-Object System.Text.UTF8Encoding -ArgumentList $false))
}

function Add-SyncJournal([string]$Change, $Block) {
    # A journal line (contract.SystemJournalLine), imported once as the audit action admin.system.
    $line = [ordered]@{ v = 1; line = [guid]::NewGuid().ToString('N'); at = (Get-ArciSiteTimestamp); change = $Change }
    $line[$Change] = $Block
    $report.journal.Add([pscustomobject]$line)
    Add-SyncLine $JournalPath ([pscustomobject]$line)
}

function Add-SyncAlert([string]$Rule, [string]$Severity, [string]$Message, [hashtable]$Details) {
    # The line format of arcilogger_core.alerts.raise_alert; the worker imports it (alerts.import).
    $alert = [pscustomobject][ordered]@{ details = [pscustomobject]$Details; message = $Message; rule = $Rule; severity = $Severity; time = (Get-ArciSiteTimestamp) }
    $report.alerts.Add($alert)
    Add-SyncLine $AlertsPath $alert
}

function Format-SyncTime($Value) {
    <# A time read back from JSON, as ISO 8601 text with its offset (PowerShell 7 turns such a text into a DateTime). #>
    if ($null -eq $Value) { return $null }
    if ($Value -is [datetime]) { return $Value.ToString('o', [System.Globalization.CultureInfo]::InvariantCulture) }
    [string]$Value
}

function Read-SyncJson([string]$Path) {
    <# A JSON file of admin\system, or $null when it is missing or damaged. #>
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    try { return ([System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8) | ConvertFrom-Json) }
    catch { return $null }
}

function Get-StagedClients {
    <# The staged clients: zamane\web\<sha256>.jar by name (the fast path compares them). #>
    if (-not (Test-Path -LiteralPath $StagingDir -PathType Container)) { return , @() }
    , @(Get-ChildItem -LiteralPath $StagingDir -File -Force | Where-Object { $_.Name -cmatch '^[0-9a-f]{64}\.jar$' } | ForEach-Object { $_.Name } | Sort-Object)
}

function Read-SyncJars {
    <# The entries of admin\system\jars.json (staged, approved), their times as text; empty lists without the file. #>
    $jars = Read-SyncJson $JarsPath
    $staged = New-Object System.Collections.Generic.List[object]
    $approved = New-Object System.Collections.Generic.List[object]
    foreach ($entry in @(Get-ArciJsonField -Object $jars -Name 'staged')) {
        if ($null -eq $entry) { continue }
        $staged.Add([pscustomobject][ordered]@{ sha256 = [string]$entry.sha256; first_seen = (Format-SyncTime $entry.first_seen) })
    }
    foreach ($entry in @(Get-ArciJsonField -Object $jars -Name 'approved')) {
        if ($null -eq $entry) { continue }
        $approved.Add([pscustomobject][ordered]@{
                sha256 = [string]$entry.sha256; path = [string]$entry.path
                approved_at = (Format-SyncTime $entry.approved_at); approved_by = [string]$entry.approved_by
            })
    }
    [pscustomobject]@{ staged = $staged; approved = $approved }
}

function Test-ReparsePoint([string]$Path) {
    <# True when $Path or a folder up to zamane\ is a reparse point (a junction or link could point a copy anywhere). #>
    $current = $Path
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $attributes = (Get-Item -LiteralPath $current -Force).Attributes
            if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { return $true }
        }
        if ([string]::Equals($current.TrimEnd([char[]]@([char]92, [char]47)), $ZamaneDir.TrimEnd([char[]]@([char]92, [char]47)), [System.StringComparison]::OrdinalIgnoreCase)) { break }
        $parent = Split-Path -Parent $current
        if (-not $parent -or $parent -eq $current) { break }
        $current = $parent
    }
    $false
}

# --- 1. policy -----------------------------------------------------------------------------------

$policy = Read-ArciAdminPolicy -Path $PolicyPath
if (-not $policy.Valid -or -not $policy.Enabled) {
    Add-SyncStep 'policy' 'skipped' $null
    Write-ArciInfo ('Tarayıcı yönetimi kapalı, eşitlenecek bir şey yok [browser administration off, nothing to sync]: {0}' -f $(if ($policy.Problem) { $policy.Problem } else { 'enabled = false' }))
    Exit-Sync 0
}

# --- 2. plan -------------------------------------------------------------------------------------

$previous = Read-SyncJson $StatusPath
$planText = $null
$planError = $null
if ($PlanPath) {
    try { $planText = [System.IO.File]::ReadAllText($PlanPath, [System.Text.Encoding]::UTF8) }
    catch { $planError = $_.Exception.Message }
}
else {
    try {
        $result = Invoke-ArciProcess -FilePath $Python -ArgumentList @('-I', '-m', 'arcilogger_core.admin', 'system-plan', '--config', $ConfigPath) -AllowFailure -TimeoutSeconds 300
        if ($result.ExitCode -eq 0) { $planText = $result.StdOut }
        else { $planError = ('çıkış kodu {0} [exit code]: {1}' -f $result.ExitCode, ($result.StdErr.Trim() -split '\r?\n')[-1]) }
    }
    catch { $planError = $_.Exception.Message }
}
$plan = $null
if (-not $planError) {
    try { $plan = $planText | ConvertFrom-Json }
    catch { $planError = ('JSON değil [not JSON]: {0}' -f $_.Exception.Message) }
}
if (-not $planError -and ($null -eq $plan -or (Get-ArciJsonField -Object $plan -Name 'schema') -cne 'arcilogger.system-plan/1')) {
    $planError = 'schema arcilogger.system-plan/1 değil [is not arcilogger.system-plan/1]'
}

function Write-SyncStatus([bool]$Ok, [string]$Code, $Firewall, $Time, [string]$AppliedSha, [string]$AckSha) {
    <# admin\system\status.json (contract.SystemStatus): what is applied now, for the API and the next plan. #>
    $jars = Read-SyncJson $JarsPath
    $staged = 0
    $promoted = 0
    if ($null -ne $script:JarCounts) {
        # After the jar step: what jars.json holds now (in a dry run: would hold).
        $staged = $script:JarCounts.staged
        $promoted = $script:JarCounts.promoted
    }
    elseif ($null -ne $jars) {
        $staged = @(Get-ArciJsonField -Object $jars -Name 'staged' | Where-Object { $_ }).Count
        $promoted = @(Get-ArciJsonField -Object $jars -Name 'approved' | Where-Object { $_ }).Count
    }
    $status = [pscustomobject][ordered]@{
        schema                = 'arcilogger.admin-system/1'
        at                    = (Get-ArciSiteTimestamp)
        ok                    = $Ok
        code                  = $(if ($Code) { $Code } else { $null })
        applied_config_sha256 = $(if ($AppliedSha) { $AppliedSha } else { $null })
        collector_ack_sha256  = $(if ($AckSha) { $AckSha } else { $null })
        firewall              = $Firewall
        time                  = $Time
        jars                  = [pscustomobject][ordered]@{ staged = $staged; promoted = $promoted }
    }
    $report.status = $status
    if (-not $DryRun) { Write-SyncFile $StatusPath (($status | ConvertTo-Json -Depth 5) + "`n") }
}

# What status.json said last (kept for every block a failed or skipped step leaves as it was).
$lastFirewall = Get-ArciJsonField -Object $previous -Name 'firewall'
if ($null -eq $lastFirewall) { $lastFirewall = [pscustomobject][ordered]@{ managed = $false; strict_outbound = $false; syslog_addresses = @(); outbound_hosts = @() } }
$lastTimeBlock = Get-ArciJsonField -Object $previous -Name 'time'
$lastTime = [pscustomobject][ordered]@{
    peers = [string](Get-ArciJsonField -Object $lastTimeBlock -Name 'peers')
    changed_at = (Format-SyncTime (Get-ArciJsonField -Object $lastTimeBlock -Name 'changed_at'))
}

if ($planError) {
    Add-SyncStep 'plan' 'failed' 'system_plan_failed'
    $report.code = 'system_plan_failed'
    $report.problems.Add($planError)
    Write-SyncStatus $false 'system_plan_failed' $lastFirewall $lastTime ([string](Get-ArciJsonField -Object $previous -Name 'applied_config_sha256')) ([string](Get-ArciJsonField -Object $previous -Name 'collector_ack_sha256'))
    Send-SyncEvent 4106 'Error' ('Sistem eşitlemesi: plan alınamadı [system sync: no plan]: {0}' -f $planError)
    Add-SyncAlert 'R20-admin' 'high' 'Sistem eşitlemesi başarısız: plan alınamadı [system sync failed: no plan]' @{ code = 'system_plan_failed' }
    if (-not $DryRun) { Remove-Item -LiteralPath $SyncStatePath -Force -ErrorAction SilentlyContinue }
    Exit-Sync 1
}
Add-SyncStep 'plan' 'done' $null
$configSha = [string](Get-ArciJsonField -Object $plan -Name 'config_sha256')
$ackSha = [string](Get-ArciJsonField -Object $plan -Name 'collector_ack_sha256')
$staged = Get-StagedClients
$inputs = [pscustomobject][ordered]@{ config_sha256 = $configSha; collector_ack_sha256 = $ackSha; policy_sha256 = $policy.Sha256; staged = @($staged) }
$last = Read-SyncJson $SyncStatePath
if ($null -ne $last -and $null -ne $previous -and ($last | ConvertTo-Json -Depth 3 -Compress) -ceq ($inputs | ConvertTo-Json -Depth 3 -Compress)) {
    $report.fast_path = $true
    Write-ArciInfo 'Değişiklik yok [nothing changed since the last run].'
    # status.json is written again all the same (what it said, a new "at"): ArciAdmin's start
    # (Event 4101) waits for it to change to know that this task runs (agent.json system_task).
    Write-SyncStatus ([bool](Get-ArciJsonField -Object $previous -Name 'ok')) ([string](Get-ArciJsonField -Object $previous -Name 'code')) $lastFirewall $lastTime ([string](Get-ArciJsonField -Object $previous -Name 'applied_config_sha256')) ([string](Get-ArciJsonField -Object $previous -Name 'collector_ack_sha256'))
    Exit-Sync 0
}

# --- 3. check every value before any change -------------------------------------------------------

$firewallPlan = Get-ArciJsonField -Object $plan -Name 'firewall'
$timePlan = Get-ArciJsonField -Object $plan -Name 'time'
$jarPlan = Get-ArciJsonField -Object $plan -Name 'jars'
$senders = @(Get-ArciJsonField -Object $firewallPlan -Name 'syslog_addresses' | Where-Object { $null -ne $_ } | ForEach-Object { [string]$_ })
$outbound = @(Get-ArciJsonField -Object $firewallPlan -Name 'outbound' | Where-Object { $null -ne $_ })
$promotions = @(Get-ArciJsonField -Object $jarPlan -Name 'promote' | Where-Object { $null -ne $_ })
$problems = $report.problems
foreach ($value in $senders) {
    $why = Get-ArciSyslogSenderProblem -Value $value -SourceNetwork @($policy.SourceNetworks) -MinPrefixV4 $policy.MinPrefixV4
    if ($why) { $problems.Add(('{0}: {1}' -f $value, $why)) }
}
foreach ($entry in $outbound) {
    $why = Get-ArciOutboundProblem -Rule ([string](Get-ArciJsonField -Object $entry -Name 'rule')) -HostName ([string](Get-ArciJsonField -Object $entry -Name 'host')) -Port (Get-ArciJsonField -Object $entry -Name 'port')
    if ($why) { $problems.Add($why) }
}
foreach ($name in @('primary', 'fallback')) {
    $value = Get-ArciJsonField -Object $timePlan -Name $name
    if ($null -ne $value -and -not (Test-ArciHostName ([string]$value))) { $problems.Add(('time.{0}: {1}' -f $name, $value)) }
}
foreach ($entry in $promotions) {
    $sha = [string](Get-ArciJsonField -Object $entry -Name 'sha256')
    $expected = Join-Path $StagingDir ('{0}.jar' -f $sha)
    $given = [string](Get-ArciJsonField -Object $entry -Name 'staged')
    if ($sha -cnotmatch '^[0-9a-f]{64}$' -or (Get-ArciJsonField -Object $entry -Name 'reason') -cne 'known') { $problems.Add(('jars.promote: {0}' -f $sha)) }
    elseif (-not [string]::Equals($given.Replace('/', '\'), $expected.Replace('/', '\'), [System.StringComparison]::OrdinalIgnoreCase)) {
        $problems.Add(('jars.promote {0}: bu kurulumun zamane\web klasöründe değil [not in this installation''s zamane\web] ({1})' -f $sha, $given))
    }
}
if ($problems.Count -gt 0) {
    Add-SyncStep 'check' 'failed' 'system_plan_invalid'
    $report.code = 'system_plan_invalid'
    Write-SyncStatus $false 'system_plan_invalid' $lastFirewall $lastTime ([string](Get-ArciJsonField -Object $previous -Name 'applied_config_sha256')) $ackSha
    Send-SyncEvent 4106 'Error' ('Sistem eşitlemesi: plan reddedildi, hiçbir şey değişmedi [system sync: plan refused, nothing changed]: {0}' -f ($problems -join '; '))
    Add-SyncAlert 'R20-admin' 'high' 'Sistem eşitlemesi: plan reddedildi [system sync: plan refused]' @{ code = 'system_plan_invalid' }
    if (-not $DryRun) { Remove-Item -LiteralPath $SyncStatePath -Force -ErrorAction SilentlyContinue }
    Exit-Sync 1
}
Add-SyncStep 'check' 'done' $null

$failed = $false
$warningCode = $null
# What system-plan left out (warning codes): reported, never applied. A source outside the
# policy that the syslog rules hold now stays open (Set-ArciFirewall.ps1 -KeepSourceAddress).
$planProblems = @(Get-ArciJsonField -Object $plan -Name 'problems' | Where-Object { $null -ne $_ } | ForEach-Object {
        [pscustomobject][ordered]@{ code = [string](Get-ArciJsonField -Object $_ -Name 'code'); value = [string](Get-ArciJsonField -Object $_ -Name 'value') }
    })
$outsidePolicy = @($planProblems | Where-Object { $_.code -ceq 'source_outside_policy' } | ForEach-Object { $_.value })
$report['plan_problems'] = $planProblems
$kept = @()

# --- 4. firewall -------------------------------------------------------------------------------------

$managed = [bool](Get-ArciJsonField -Object $firewallPlan -Name 'managed') -and (Test-Path -LiteralPath $FirewallStatePath -PathType Leaf)
$firewallState = $lastFirewall
if ($managed) {
    $arguments = @{ SyncFromConfig = $true; SourceAddress = $senders; Outbound = $outbound; StateFile = $FirewallStatePath; PolicyPath = $PolicyPath }
    if ($outsidePolicy.Count -gt 0) { $arguments['KeepSourceAddress'] = $outsidePolicy }
    $firewallReport = Join-Path ([System.IO.Path]::GetTempPath()) ('arcilogger-sync-firewall-{0}.json' -f [guid]::NewGuid().ToString('N'))
    $arguments['PlanPath'] = $firewallReport
    if ($DryRun) { $arguments['DryRun'] = $true }
    $code = $null
    try {
        # Its exit code (0, 2 refused, 3 not managed) is the global LASTEXITCODE.
        $global:LASTEXITCODE = -1
        & (Join-Path $PSScriptRoot 'Set-ArciFirewall.ps1') @arguments | Out-Host
        $code = $global:LASTEXITCODE
    }
    catch {
        $report.problems.Add(('firewall: {0}' -f $_.Exception.Message))
        $code = 1
    }
    if (Test-Path -LiteralPath $firewallReport) {
        $report.firewall = [System.IO.File]::ReadAllText($firewallReport, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
        Remove-Item -LiteralPath $firewallReport -Force
    }
    if ($code -eq 0) {
        $hosts = @($outbound | ForEach-Object { [string](Get-ArciJsonField -Object $_ -Name 'host') } | Select-Object -Unique)
        $strict = $false
        if ($null -ne $report.firewall) {
            $strict = [bool]$report.firewall.strict_outbound
            $kept = @(Get-ArciJsonField -Object $report.firewall -Name 'kept' | Where-Object { $_ } | ForEach-Object { [string]$_ })
        }
        if (-not $strict) { $hosts = @() }
        # What the syslog rules hold now: the plan's senders and the kept ones.
        $held = @($senders) + @($kept)
        $firewallState = [pscustomobject][ordered]@{ managed = $true; strict_outbound = $strict; syslog_addresses = $held; outbound_hosts = $hosts }
        $changed = ((@($lastFirewall.syslog_addresses) -join ',') -ne ($held -join ',')) -or ((@($lastFirewall.outbound_hosts) -join ',') -ne ($hosts -join ','))
        if ($changed) { Add-SyncJournal 'firewall' ([pscustomobject][ordered]@{ syslog_addresses_count = $held.Count; outbound_rules = @($outbound | ForEach-Object { [string](Get-ArciJsonField -Object $_ -Name 'rule') } | Select-Object -Unique).Count }) }
        Add-SyncStep 'system.firewall' 'done' $null
    }
    elseif ($code -eq 3) {
        $managed = $false
        $warningCode = 'firewall_not_managed'
        Add-SyncStep 'system.firewall' 'skipped' 'firewall_not_managed'
    }
    else {
        $failed = $true
        Add-SyncStep 'system.firewall' 'failed' 'system_sync_failed'
    }
}
else {
    $warningCode = 'firewall_not_managed'
    $firewallState = [pscustomobject][ordered]@{ managed = $false; strict_outbound = $false; syslog_addresses = @(); outbound_hosts = @() }
    Add-SyncStep 'system.firewall' 'skipped' 'firewall_not_managed'
}

# --- 5. Windows Time ---------------------------------------------------------------------------------

$primary = [string](Get-ArciJsonField -Object $timePlan -Name 'primary')
$fallback = [string](Get-ArciJsonField -Object $timePlan -Name 'fallback')
$peers = $null
try { $peers = Get-ArciTimePeerList -Primary $primary -Fallback $fallback } catch { $peers = $null }
$timeState = $lastTime
$report.time = [pscustomobject][ordered]@{ changed = [bool](Get-ArciJsonField -Object $timePlan -Name 'changed'); primary = $primary; fallback = $fallback; peers = $peers }
if ([bool](Get-ArciJsonField -Object $timePlan -Name 'changed') -and $peers) {
    try {
        if (-not $DryRun) { & (Join-Path $PSScriptRoot 'Set-ArciTime.ps1') -Primary $primary -Fallback $fallback | Out-Host }
        $timeState = [pscustomobject][ordered]@{ peers = $peers; changed_at = (Get-ArciSiteTimestamp) }
        Add-SyncJournal 'time' ([pscustomobject][ordered]@{ changed = $true; peers = $peers })
        Add-SyncStep 'system.time' 'done' $null
    }
    catch {
        $failed = $true
        $report.problems.Add(('time: {0}' -f $_.Exception.Message))
        Add-SyncStep 'system.time' 'failed' 'system_sync_failed'
    }
}
else {
    if (-not $lastTime.peers -and $peers) { $timeState = [pscustomobject][ordered]@{ peers = $peers; changed_at = $null } }
    Add-SyncStep 'system.time' 'skipped' $null
}

# --- 6. Zamane clients -------------------------------------------------------------------------------

# The entries of both writers (this script and the console's approve-jar) stay as they are.
$jars = Read-SyncJars
$stagedEntries = $jars.staged
$approvedEntries = $jars.approved
$known = @($stagedEntries | ForEach-Object { [string]$_.sha256 }) + @($approvedEntries | ForEach-Object { [string]$_.sha256 })
$jarsChanged = $false
$jarFailed = $false
$promoted = New-Object System.Collections.Generic.List[object]
foreach ($entry in $promotions) {
    $sha = [string]$entry.sha256
    $source = Join-Path $StagingDir ('{0}.jar' -f $sha)
    $target = Join-Path $ApprovedDir ('{0}.jar' -f $sha)
    $item = [ordered]@{ sha256 = $sha; staged = $source; target = $target; result = 'promote' }
    if (@($approvedEntries | Where-Object { [string]$_.sha256 -eq $sha }).Count -gt 0) {
        $item.result = 'approved already'
        $report.jars.promote += [pscustomobject]$item
        continue
    }
    if (-not (Test-Path -LiteralPath $source -PathType Leaf) -or (Test-ReparsePoint $source) -or (Test-ReparsePoint $ApprovedDir)) {
        $item.result = 'refused: missing or a reparse point'
        $report.problems.Add(('{0}: bekleyen dosya yok ya da bir yeniden ayrıştırma noktası [staged file missing or a reparse point]' -f $source))
        $report.jars.promote += [pscustomobject]$item
        $jarFailed = $true
        continue
    }
    if ((Get-ArciFileSha256 -Path $source) -ne $sha) {
        $item.result = 'refused: hash'
        $report.problems.Add(('{0}: SHA-256 dosya adıyla aynı değil [does not match its name]' -f $source))
        $report.jars.promote += [pscustomobject]$item
        $jarFailed = $true
        continue
    }
    if (-not $DryRun) {
        $why = $null
        try { $why = Copy-ArciApprovedJar -Source $source -Target $target -Sha256 $sha }
        catch { $why = $_.Exception.Message }
        if ($why) {
            $item.result = 'failed'
            $report.problems.Add($why)
            $report.jars.promote += [pscustomobject]$item
            $jarFailed = $true
            continue
        }
    }
    $approvedEntries.Add([pscustomobject][ordered]@{ sha256 = $sha; path = $target; approved_at = (Get-ArciSiteTimestamp); approved_by = 'known' })
    $jarsChanged = $true
    $promoted.Add($sha)
    $report.jars.promote += [pscustomobject]$item
}
$mismatched = New-Object System.Collections.Generic.List[string]
foreach ($name in @($staged)) {
    $sha = $name.Substring(0, 64)
    if ($known -contains $sha -or $promoted.Contains($sha)) { continue }
    $file = Join-Path $StagingDir $name
    if ((Test-ReparsePoint $file) -or (Get-ArciFileSha256 -Path $file) -ne $sha) {
        # Not the file its name promises (or a link): never recorded, never run; reported once
        # per change of the staged files (the fast path compares their names).
        $mismatched.Add($sha)
        $report.problems.Add(('{0}: kaydedilmedi, SHA-256 adıyla aynı değil ya da bir bağlantı [not recorded: its SHA-256 differs from its name, or a link]' -f $file))
        continue
    }
    $stagedEntries.Add([pscustomobject][ordered]@{ sha256 = $sha; first_seen = (Get-ArciSiteTimestamp) })
    $jarsChanged = $true
    $report.jars.staged += [pscustomobject][ordered]@{ sha256 = $sha }
    $after = [DateTime]::UtcNow.AddHours($policy.JarHoldHours).ToString('yyyy-MM-ddTHH:mm:ssZ')
    Send-SyncEvent 4104 'Warning' ('Zamane istemcisi bekletiliyor [Zamane client staged]: {0}; en erken {1} sonra konsolda approve-jar ile onaylanır [can be approved with approve-jar on the console from {1}]' -f $sha, $after)
}
$document = Merge-ArciJarsDocument -Written $null -Staged $stagedEntries.ToArray() -Approved $approvedEntries.ToArray()
if ($jarsChanged -and -not $DryRun) {
    # Read again right before the write: approve-jar may have written since step 6 began.
    $document = Merge-ArciJarsDocument -Written (Read-SyncJars) -Staged $stagedEntries.ToArray() -Approved $approvedEntries.ToArray()
    Write-SyncFile $JarsPath (($document | ConvertTo-Json -Depth 5) + "`n")
}
$script:JarCounts = [pscustomobject]@{ staged = @($document.staged).Count; promoted = @($document.approved).Count }
foreach ($sha in $promoted) {
    Add-SyncJournal 'jar' ([pscustomobject][ordered]@{ sha256 = $sha; approved_by = 'known' })
    Send-SyncEvent 4107 'Warning' ('Zamane istemcisi onaylandı (bilinen sürüm) [Zamane client promoted, a known release]: {0}' -f $sha)
    Add-SyncAlert 'R20-config-change' 'high' 'Zamane istemcisi onaylandı [Zamane client promoted]' @{ section = 'tsa'; paths = @('tsa.zamane_jar') }
}
if ($jarFailed) {
    $failed = $true
    Add-SyncStep 'jar.promotion' 'failed' 'system_sync_failed'
}
elseif ($promoted.Count -gt 0 -or $report.jars.staged.Count -gt 0) { Add-SyncStep 'jar.promotion' 'done' $null }
else { Add-SyncStep 'jar.promotion' 'skipped' $null }

# --- 7. status, journal, events ---------------------------------------------------------------------

$applied = $configSha
if ($failed) { $applied = [string](Get-ArciJsonField -Object $previous -Name 'applied_config_sha256') }
# One code in status.json, the weightiest first. A successful run raises an R20-admin alert per
# warning of its own (a failed one raises system_sync_failed alone and is tried again); the fast
# path keeps it from repeating until the inputs change.
$warnings = New-Object System.Collections.Generic.List[string]
$warningAlerts = New-Object System.Collections.Generic.List[object]
if ($managed -and $outsidePolicy.Count -gt 0) {
    $warnings.Add('source_outside_policy')
    $what = @($outsidePolicy | ForEach-Object { $(if ($kept -contains $_) { '{0} (açık bırakıldı [kept open])' -f $_ } else { '{0} (açılmadı [not opened])' -f $_ }) })
    $warningAlerts.Add(@('source_outside_policy', ('Sistem eşitlemesi: log kaynakları yönetim politikasının ağlarının dışında [system sync: log sources outside the policy networks]: {0}' -f ($what -join ', '))))
}
if ($mismatched.Count -gt 0) {
    $warnings.Add('payload_mismatch')
    $warningAlerts.Add(@('payload_mismatch', ('Sistem eşitlemesi: bekleyen Zamane istemcisi adındaki SHA-256 ile uyuşmuyor, kaydedilmedi [system sync: a staged Zamane client does not match the SHA-256 of its name, not recorded]: {0}' -f ($mismatched -join ', '))))
}
foreach ($problem in $planProblems) {
    if ($problem.code -cne 'source_outside_policy' -and -not $warnings.Contains($problem.code)) { $warnings.Add($problem.code) }
}
if ($warningCode) { $warnings.Add($warningCode) }
$code = $(if ($failed) { 'system_sync_failed' } elseif ($warnings.Count -gt 0) { $warnings[0] } else { $null })
$report.code = $code
Write-SyncStatus (-not $failed) $code $firewallState $timeState $applied $ackSha
if (-not $failed) {
    foreach ($alert in $warningAlerts) { Add-SyncAlert 'R20-admin' 'high' $alert[1] @{ code = $alert[0] } }
}
if ($failed) {
    Send-SyncEvent 4106 'Error' ('Sistem eşitlemesi başarısız, önceki kurallar geçerli [system sync failed, the previous rules stay]: {0}' -f ($report.problems -join '; '))
    Add-SyncAlert 'R20-admin' 'high' 'Sistem eşitlemesi başarısız [system sync failed]' @{ code = 'system_sync_failed' }
    if (-not $DryRun) { Remove-Item -LiteralPath $SyncStatePath -Force -ErrorAction SilentlyContinue }
    Exit-Sync 1
}
Send-SyncEvent 4106 'Information' ('Sistem eşitlendi [system in step with the configuration]: {0} syslog göndericisi [senders]' -f $senders.Count)
if (-not $DryRun) { Write-SyncFile $SyncStatePath (($inputs | ConvertTo-Json -Depth 3) + "`n") }
Exit-Sync 0
