<#
    ARCILOGGER kurulum yardımcıları [shared helpers for the Windows installer and scripts].

    Used by Install-Arcilogger.ps1, Build-ArciBundle.ps1, Set-ArciVaultAcl.ps1,
    Set-ArciFirewall.ps1, Set-ArciTime.ps1 and Set-ArciPower.ps1. Written for Windows PowerShell
    5.1 (the PowerShell that ships with Windows Server 2022/2025 and Windows 11); it also loads in
    PowerShell 7 so that the dry-run logic can be tested on any CI runner.

    Why these helpers exist:
    - Accounts are always given as SIDs, never as names: names are localised ("Yöneticiler" on a
      Turkish Windows) and a virtual service account name only resolves once its service exists.
    - Native programs that receive secrets are started through System.Diagnostics.Process so that
      the secret travels on stdin or in the child's own environment block, never on a command line,
      in this process's environment or in a log.
#>

Set-StrictMode -Version 2.0

# Windows PowerShell 5.1 writes arrays held in variables as {"value": [...], "Count": n} in
# ConvertTo-Json because of an extended type definition; the plans and manifests need plain arrays.
if ($PSVersionTable.PSVersion.Major -lt 6) { Remove-TypeData -TypeName System.Array -ErrorAction SilentlyContinue }

# --- constants ------------------------------------------------------------------------------------

$script:ArciServiceNames = @('ArciCollector', 'ArciIngest', 'ArciApi', 'ArciWorker', 'ArciWeb', 'ArciAdmin')

$script:WellKnownSid = @{
    System         = 'S-1-5-18'
    LocalService   = 'S-1-5-19'
    Administrators = 'S-1-5-32-544'
    Everyone       = 'S-1-1-0'
    Users          = 'S-1-5-32-545'
    # The placeholder of an inheritable entry: whoever creates a file below gets it (its owner).
    CreatorOwner   = 'S-1-3-0'
}

# The Windows Application log source of the browser administration (ArciAdmin and ApplySystem,
# arcilogger_core.admin.contract.EVENT_SOURCE).
$script:ArciEventSource = 'ARCILOGGER-Admin'

function Get-ArciServiceNames {
    <#
    The ARCILOGGER Windows services in start order: the collector first (it receives the logs),
    ArciAdmin last. ArciAdmin writes the configuration the others load (ADR-0014): it is stopped
    before them, so no change is applied while they stop or are replaced, and started after them.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param()
    , $script:ArciServiceNames
}

function Get-ArciWellKnownSid {
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)][ValidateSet('System', 'LocalService', 'Administrators', 'Everyone', 'Users', 'CreatorOwner')][string]$Name)
    $script:WellKnownSid[$Name]
}

# --- messages -------------------------------------------------------------------------------------

function Write-ArciStep {
    <# One line per step: Turkish first, short English in brackets. #>
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ('==> ' + $Message)
}

function Write-ArciInfo {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ('    ' + $Message)
}

function Write-ArciWarn {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Warning $Message
}

# --- platform -------------------------------------------------------------------------------------

function Test-ArciWindows {
    <# True on Windows (works in Windows PowerShell 5.1, where $IsWindows does not exist). #>
    [CmdletBinding()]
    [OutputType([bool])]
    param()
    [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
}

function Test-ArciAdministrator {
    [CmdletBinding()]
    [OutputType([bool])]
    param()
    if (-not (Test-ArciWindows)) { return $false }
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal -ArgumentList $identity
    $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-ArciInteractiveConsole {
    <#
    .SYNOPSIS
        True when a person sits at this console: someone can answer a question and nothing else
        reads what is printed.
    .DESCRIPTION
        False under "powershell -NonInteractive" (Read-Host fails there), in CI (CI,
        GITHUB_ACTIONS, TF_BUILD), without an interactive desktop (a service, a scheduled task)
        and when stdin or stdout is redirected (a file, a pipe, a CI log). The installer prints
        the one-time setup token and asks for the optional Kamu SM credentials only then: printed
        to a redirected stdout, the token would end up in whatever captures it.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([AllowEmptyCollection()][string[]]$CommandLine)
    if (-not $PSBoundParameters.ContainsKey('CommandLine')) { $CommandLine = [Environment]::GetCommandLineArgs() }
    if (-not [Environment]::UserInteractive) { return $false }
    # powershell.exe and pwsh accept -NonInteractive and its abbreviations (-noni, -non).
    foreach ($argument in @($CommandLine)) {
        if ([string]$argument -match '^[-/]non(i|$)') { return $false }
    }
    foreach ($name in @('CI', 'GITHUB_ACTIONS', 'TF_BUILD')) {
        if ([Environment]::GetEnvironmentVariable($name)) { return $false }
    }
    try {
        if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) { return $false }
    }
    catch { return $false }
    $true
}

function Get-ArciWindowsPowerShellModulePath {
    <#
    .SYNOPSIS
        The module path without the module folders of PowerShell 7, for Windows PowerShell 5.1.
    .DESCRIPTION
        PowerShell 7 removes its own module folders when it starts powershell.exe itself, but a
        Windows PowerShell started by another program that pwsh started (cmd.exe, Python, a setup
        .exe launched from a pwsh window, a CI step that runs under pwsh) inherits PowerShell 7's
        PSModulePath unchanged (about_PSModulePath, "Starting Windows PowerShell from PowerShell
        7"). Windows PowerShell 5.1 then finds the PowerShell 7 copies of its own modules first and
        cannot load them: "The 'ConvertTo-SecureString' command was found in the module
        'Microsoft.PowerShell.Security', but the module could not be loaded" (also Get-Acl and the
        other commands of that module). This function leaves out PowerShell 7's user and all-users
        module folders (-ExcludeFolder, by default Documents\PowerShell\Modules and Program
        Files\PowerShell\Modules) and the Modules folder of every PowerShell 7 installation (the
        folder next to pwsh.dll). Empty entries go too.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [AllowEmptyString()][string]$ModulePath = $env:PSModulePath,
        [AllowEmptyCollection()][string[]]$ExcludeFolder
    )
    # Computed here, not as parameter defaults: an empty "Documents" folder (service accounts)
    # must not stop the script with a parameter binding error.
    if (-not $PSBoundParameters.ContainsKey('ExcludeFolder')) {
        $ExcludeFolder = @()
        $documents = [Environment]::GetFolderPath('MyDocuments')
        if ($documents) { $ExcludeFolder += [System.IO.Path]::Combine($documents, 'PowerShell', 'Modules') }
        if ($env:ProgramFiles) { $ExcludeFolder += [System.IO.Path]::Combine($env:ProgramFiles, 'PowerShell', 'Modules') }
    }
    $separators = [char[]]@([char]92, [char]47)
    $excluded = @($ExcludeFolder | Where-Object { $_ } | ForEach-Object { $_.Trim().TrimEnd($separators) })
    $kept = New-Object System.Collections.Generic.List[string]
    if (-not $ModulePath) { return '' }
    foreach ($entry in $ModulePath.Split([System.IO.Path]::PathSeparator)) {
        $folder = $entry.Trim().TrimEnd($separators)
        if (-not $folder) { continue }
        $skip = $false
        foreach ($exclude in $excluded) {
            if ([string]::Equals($folder, $exclude, [System.StringComparison]::OrdinalIgnoreCase)) { $skip = $true }
        }
        if (-not $skip) {
            try {
                $parent = [System.IO.Path]::GetDirectoryName($folder)
                if ($parent -and [System.IO.File]::Exists([System.IO.Path]::Combine($parent, 'pwsh.dll'))) { $skip = $true }
            }
            catch { $skip = $false }   # not a valid path: Windows PowerShell ignores it as well
        }
        if (-not $skip) { $kept.Add($entry.Trim()) }
    }
    $kept.ToArray() -join [string][System.IO.Path]::PathSeparator
}

function Initialize-ArciModulePath {
    <#
    .SYNOPSIS
        In Windows PowerShell 5.1: take the PowerShell 7 module folders out of $env:PSModulePath,
        for this process and for every powershell.exe it starts (Get-ArciWindowsPowerShellModulePath).
        Every ARCILOGGER script calls it right after importing this module, so the installer also
        works when a program started from PowerShell 7 started it (tests check the call in every
        script). Nothing to do in PowerShell 7.
    #>
    [CmdletBinding()]
    param()
    if ($PSVersionTable.PSVersion.Major -ge 6) { return }
    $clean = Get-ArciWindowsPowerShellModulePath -ModulePath $env:PSModulePath
    $own = Join-Path $PSHOME 'Modules'
    $present = @($clean.Split([System.IO.Path]::PathSeparator) | Where-Object { $_ -and ($_.TrimEnd([char[]]@([char]92, [char]47)) -ieq $own) })
    if ($present.Count -eq 0) { $clean = (@($clean, $own) | Where-Object { $_ }) -join [string][System.IO.Path]::PathSeparator }
    if ($clean -ne $env:PSModulePath) {
        Write-Verbose ("PSModulePath (PowerShell 7 folders removed): {0}" -f $clean)
        # Through .NET, not $env: - an assignment through the environment provider could be
        # subject to -WhatIf (Install-Arcilogger.ps1 -WhatIf) and then not happen.
        [Environment]::SetEnvironmentVariable('PSModulePath', $clean, [EnvironmentVariableTarget]::Process)
    }
}

# --- identities -----------------------------------------------------------------------------------

function Get-ArciServiceSid {
    <#
    .SYNOPSIS
        SID of the virtual account "NT SERVICE\<name>" (the per-service SID).
    .DESCRIPTION
        Windows derives it from SHA-1 of the upper-case service name in UTF-16LE: the 20 bytes are
        read as five little-endian 32-bit integers after S-1-5-80. Computing it ourselves lets the
        installer write ACLs before a service exists and keeps the ACL code independent of the
        display language. Golden value: TrustedInstaller ->
        S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)][string]$ServiceName)
    $bytes = [System.Text.Encoding]::Unicode.GetBytes($ServiceName.ToUpperInvariant())
    $sha1 = [System.Security.Cryptography.SHA1]::Create()
    try { $hash = $sha1.ComputeHash($bytes) } finally { $sha1.Dispose() }
    # Windows (x64, ARM64) is little-endian; BitConverter reads the host byte order.
    if (-not [System.BitConverter]::IsLittleEndian) { throw 'big-endian host is not supported' }
    $parts = New-Object System.Collections.Generic.List[string]
    for ($i = 0; $i -lt 20; $i += 4) {
        $parts.Add([System.BitConverter]::ToUInt32($hash, $i).ToString([System.Globalization.CultureInfo]::InvariantCulture))
    }
    'S-1-5-80-' + ($parts -join '-')
}

# --- secrets --------------------------------------------------------------------------------------

function New-ArciRandomPassword {
    <#
    .SYNOPSIS
        Random password of letters and digits from the OS CSPRNG.
    .DESCRIPTION
        Only [A-Za-z0-9]: the value is passed on stdin to arcilogger-core and must survive any
        console code page. Rejection sampling (bytes >= 248 are discarded) avoids modulo bias;
        40 characters give about 238 bits.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([ValidateRange(16, 128)][int]$Length = 40)
    $alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $builder = New-Object System.Text.StringBuilder
    $buffer = New-Object byte[] 64
    try {
        while ($builder.Length -lt $Length) {
            $rng.GetBytes($buffer)
            foreach ($b in $buffer) {
                if ($b -lt 248 -and $builder.Length -lt $Length) { [void]$builder.Append($alphabet[$b % 62]) }
            }
        }
    }
    finally { $rng.Dispose() }
    $builder.ToString()
}

function ConvertTo-ArciScramVerifier {
    <#
    .SYNOPSIS
        PostgreSQL SCRAM-SHA-256 verifier for a password (RFC 5802 / RFC 7677).
    .DESCRIPTION
        "CREATE ROLE ... PASSWORD '<verifier>'" stores the verifier as is, so the plain password
        never reaches the database server, its statement log or its error log.
        Format: SCRAM-SHA-256$<iterations>:<salt>$<StoredKey>:<ServerKey> (Base64).
        The password must be ASCII letters/digits (SASLprep is then the identity).
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)][string]$Password,
        [byte[]]$Salt,
        [ValidateRange(4096, 1000000)][int]$Iterations = 4096
    )
    if ($Password -notmatch '^[A-Za-z0-9]+$') { throw 'SCRAM: yalnızca harf ve rakam [letters and digits only]' }
    if (-not $Salt) {
        $Salt = New-Object byte[] 16
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($Salt) } finally { $rng.Dispose() }
    }
    $passwordBytes = [System.Text.Encoding]::UTF8.GetBytes($Password)
    $kdf = [System.Security.Cryptography.Rfc2898DeriveBytes]::new([byte[]]$passwordBytes, [byte[]]$Salt, $Iterations, [System.Security.Cryptography.HashAlgorithmName]::SHA256)
    try { $salted = $kdf.GetBytes(32) } finally { $kdf.Dispose() }
    $hmac = [System.Security.Cryptography.HMACSHA256]::new([byte[]]$salted)
    try {
        $clientKey = $hmac.ComputeHash([System.Text.Encoding]::ASCII.GetBytes('Client Key'))
        $serverKey = $hmac.ComputeHash([System.Text.Encoding]::ASCII.GetBytes('Server Key'))
    }
    finally { $hmac.Dispose() }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $storedKey = $sha.ComputeHash($clientKey) } finally { $sha.Dispose() }
    'SCRAM-SHA-256${0}:{1}${2}:{3}' -f $Iterations, [System.Convert]::ToBase64String($Salt),
    [System.Convert]::ToBase64String($storedKey), [System.Convert]::ToBase64String($serverKey)
}

# --- files and checksums --------------------------------------------------------------------------

function Rename-ArciFolder {
    # Right after files were written into a folder or a program ran from it, a virus scanner or
    # the search indexer can keep a file open for a few seconds, and Windows refuses to rename the
    # folder ("Access to the path is denied"). The rename is tried again until TimeoutSeconds have
    # passed; then the last error is thrown. A missing folder or a bad name fails at once.
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [Parameter(Mandatory = $true)][string]$NewName,
        [int]$TimeoutSeconds = 60,
        [int]$DelayMilliseconds = 1000
    )
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $waiting = $false
    while ($true) {
        try {
            Rename-Item -LiteralPath $LiteralPath -NewName $NewName -ErrorAction Stop
            return
        }
        catch [System.IO.IOException], [System.UnauthorizedAccessException] {
            if ([DateTime]::UtcNow -ge $deadline) { throw }
            if (-not $waiting) {
                Write-ArciInfo ('Klasör kullanımda, bekleniyor [folder in use, waiting]: {0}' -f $LiteralPath)
                $waiting = $true
            }
            Start-Sleep -Milliseconds $DelayMilliseconds
        }
    }
}

function Get-ArciFileSha256 {
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = [System.IO.File]::OpenRead($Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash($stream)
    }
    finally {
        $sha.Dispose()
        $stream.Dispose()
    }
    ([System.BitConverter]::ToString($hash) -replace '-', '').ToLowerInvariant()
}

function Write-ArciUtf8File {
    <# UTF-8 without BOM and with the given text unchanged (Windows PowerShell 5.1 writes UTF-16 by default). #>
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)
    $parent = [System.IO.Path]::GetDirectoryName($Path)
    if ($parent -and -not [System.IO.Directory]::Exists($parent)) { [void][System.IO.Directory]::CreateDirectory($parent) }
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding -ArgumentList $false))
}

function Read-ArciSha256Sums {
    <#
    .SYNOPSIS
        Parse a SHA256SUMS file ("<64 hex>  <relative/path>", the format of sha256sum).
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)
    $entries = New-Object System.Collections.Generic.List[object]
    $lineNumber = 0
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $lineNumber++
        if ($line.Trim().Length -eq 0) { continue }
        if ($line -notmatch '^(?<hash>[0-9a-f]{64}) [ *](?<path>.+)$') {
            throw ("SHA256SUMS satır {0} okunamadı [unreadable line]: {1}" -f $lineNumber, $line)
        }
        $relative = $Matches['path']
        if ($relative.StartsWith('/') -or $relative -match '^[A-Za-z]:' -or ($relative -split '[\\/]') -contains '..') {
            throw ("SHA256SUMS güvensiz yol [unsafe path]: {0}" -f $relative)
        }
        $entries.Add([pscustomobject]@{ Path = $relative; Sha256 = $Matches['hash'] })
    }
    , $entries.ToArray()
}

function Test-ArciBundle {
    <#
    .SYNOPSIS
        Verify every file listed in <bundle>\SHA256SUMS. Returns an object with Ok, Errors,
        Warnings, Files and Fingerprint (the SHA-256 of SHA256SUMS itself, printed by the build so
        the operator can compare it after copying the bundle).
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$BundleRoot)
    $errors = New-Object System.Collections.Generic.List[string]
    $warnings = New-Object System.Collections.Generic.List[string]
    $sumsPath = Join-Path $BundleRoot 'SHA256SUMS'
    if (-not (Test-Path -LiteralPath $sumsPath -PathType Leaf)) {
        $errors.Add("SHA256SUMS yok [missing]: $sumsPath")
        return [pscustomobject]@{ Ok = $false; Errors = $errors.ToArray(); Warnings = @(); Files = 0; Fingerprint = $null }
    }
    $entries = Read-ArciSha256Sums -Path $sumsPath
    $listed = @{}
    foreach ($entry in $entries) {
        $listed[$entry.Path.Replace('\', '/')] = $true
        $file = Join-Path $BundleRoot ($entry.Path.Replace('/', [string][System.IO.Path]::DirectorySeparatorChar))
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            $errors.Add("eksik dosya [missing file]: $($entry.Path)")
            continue
        }
        $actual = Get-ArciFileSha256 -Path $file
        if ($actual -ne $entry.Sha256) {
            $errors.Add("özet uyuşmuyor [checksum mismatch]: $($entry.Path)")
        }
    }
    $root = (Resolve-Path -LiteralPath $BundleRoot).ProviderPath.TrimEnd([char[]]@([char]92, [char]47))
    foreach ($file in Get-ChildItem -LiteralPath $root -Recurse -File -Force) {
        $relative = $file.FullName.Substring($root.Length + 1).Replace('\', '/')
        if ($relative -eq 'SHA256SUMS') { continue }
        if (-not $listed.ContainsKey($relative)) { $warnings.Add("listede olmayan dosya [unlisted file]: $relative") }
    }
    [pscustomobject]@{
        Ok          = ($errors.Count -eq 0 -and $entries.Count -gt 0)
        Errors      = $errors.ToArray()
        Warnings    = $warnings.ToArray()
        Files       = $entries.Count
        Fingerprint = (Get-ArciFileSha256 -Path $sumsPath)
    }
}

function Resolve-ArciComponentSource {
    <#
    .SYNOPSIS
        Download URL and required SHA-256 of a components.json entry. Throws unless both are known:
        a checksum is either recorded in components.json (computed from a downloaded file) or
        supplied by the operator after checking it on the vendor's site (-Sha256Override).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Component,
        [hashtable]$UrlOverride = @{},
        [hashtable]$Sha256Override = @{}
    )
    $name = [string]$Component.name
    $url = [string]$Component.url
    $sha256 = [string]$Component.sha256
    $source = 'components.json'
    if ($UrlOverride.ContainsKey($name)) { $url = [string]$UrlOverride[$name] }
    if ($Sha256Override.ContainsKey($name)) {
        $sha256 = ([string]$Sha256Override[$name]).Trim().ToLowerInvariant()
        $source = 'operator-supplied (-ComponentSha256)'
    }
    if (-not $sha256) {
        throw ("{0}: doğrulanmış SHA-256 yok [no verified checksum]. Üretici sitesinde doğrulayıp -ComponentSha256 @{{ {0} = '<64 hex>' }} verin." -f $name)
    }
    if ($sha256 -notmatch '^[0-9a-f]{64}$') { throw ("{0}: geçersiz SHA-256 [invalid]: {1}" -f $name, $sha256) }
    if (-not $url) { throw ("{0}: indirme adresi yok [no download URL]; -ComponentUrl verin." -f $name) }
    if ($url -notmatch '^https://') { throw ("{0}: yalnızca https [https only]: {1}" -f $name, $url) }
    $fileName = [string]$Component.file
    if (-not $fileName) { $fileName = [System.IO.Path]::GetFileName(([System.Uri]$url).AbsolutePath) }
    [pscustomobject]@{ Name = $name; Url = $url; Sha256 = $sha256; Source = $source; FileName = $fileName }
}

function Expand-ArciTemplate {
    <#
    .SYNOPSIS
        Replace {{KEY}} placeholders; with -Xml the values are XML-escaped. Fails if any
        placeholder is left, so a new placeholder can never reach a service definition unfilled.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][hashtable]$Values,
        [switch]$Xml
    )
    foreach ($key in $Values.Keys) {
        $value = [string]$Values[$key]
        if ($Xml) { $value = [System.Security.SecurityElement]::Escape($value) }
        $Text = $Text.Replace('{{' + $key + '}}', $value)
    }
    $left = [regex]::Match($Text, '\{\{[A-Z0-9_]+\}\}')
    if ($left.Success) { throw ("Doldurulmamış yer tutucu [unfilled placeholder]: {0}" -f $left.Value) }
    $Text
}

# --- native programs ------------------------------------------------------------------------------

function ConvertTo-ArciArgumentString {
    <#
    .SYNOPSIS
        Quote an argument list for CreateProcess the way the Microsoft C runtime parses it back
        (CommandLineToArgvW rules: backslashes are literal unless they precede a double quote).
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([AllowEmptyCollection()][string[]]$ArgumentList = @())
    $quoted = foreach ($argument in $ArgumentList) {
        if ($null -eq $argument) { $argument = '' }
        if ($argument.Length -gt 0 -and $argument -notmatch '[\s"]') { $argument; continue }
        $builder = New-Object System.Text.StringBuilder
        [void]$builder.Append('"')
        $backslashes = 0
        $backslash = [char]92
        $quote = [char]34
        foreach ($ch in $argument.ToCharArray()) {
            if ($ch -eq $backslash) { $backslashes++; continue }
            if ($ch -eq $quote) {
                [void]$builder.Append($backslash, 2 * $backslashes + 1)
                [void]$builder.Append($quote)
            }
            else {
                if ($backslashes -gt 0) { [void]$builder.Append($backslash, $backslashes) }
                [void]$builder.Append($ch)
            }
            $backslashes = 0
        }
        if ($backslashes -gt 0) { [void]$builder.Append($backslash, 2 * $backslashes) }
        [void]$builder.Append('"')
        $builder.ToString()
    }
    (@($quoted) -join ' ')
}

function Invoke-ArciProcess {
    <#
    .SYNOPSIS
        Run a program and capture its output. Secrets go through -StdinText or -Environment (the
        child's environment only) and are replaced by *** in the returned and printed text.
    .DESCRIPTION
        -TimeoutSeconds (0: wait as long as it takes): a program still running after that many
        seconds is stopped and the call throws, also with -AllowFailure. Its output is not
        waited for then (a child process of it may still hold the pipes open).
    .OUTPUTS
        [pscustomobject] with ExitCode, StdOut, StdErr.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [AllowEmptyCollection()][string[]]$ArgumentList = @(),
        [string]$StdinText,
        [hashtable]$Environment = @{},
        [AllowEmptyCollection()][string[]]$Redact = @(),
        [string]$WorkingDirectory,
        [switch]$AllowFailure,
        [ValidateRange(0, 86400)][int]$TimeoutSeconds = 0
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.Arguments = ConvertTo-ArciArgumentString -ArgumentList $ArgumentList
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true
    $utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
    $psi.StandardOutputEncoding = $utf8
    $psi.StandardErrorEncoding = $utf8
    if ($WorkingDirectory) { $psi.WorkingDirectory = $WorkingDirectory }
    # Python children print UTF-8 regardless of the console code page.
    $psi.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    foreach ($key in $Environment.Keys) { $psi.EnvironmentVariables[$key] = [string]$Environment[$key] }
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    $sendStdin = $PSBoundParameters.ContainsKey('StdinText') -and $null -ne $StdinText
    # .NET Framework (Windows PowerShell 5.1) creates the child's stdin writer with
    # Console.InputEncoding and writes that encoding's preamble at once: on a UTF-8 console (code
    # page 65001) the child would read a byte order mark in front of the secret, which then no
    # longer matches. Use the same code page without a preamble while the child starts.
    $savedInputEncoding = $null
    if ($sendStdin -and $PSVersionTable.PSVersion.Major -lt 6) {
        try {
            $current = [Console]::InputEncoding
            if ($current.GetPreamble().Length -gt 0) {
                $savedInputEncoding = $current
                [Console]::InputEncoding = $utf8
            }
        }
        catch { $savedInputEncoding = $null }
    }
    try { [void]$process.Start() }
    finally {
        if ($null -ne $savedInputEncoding) {
            try { [Console]::InputEncoding = $savedInputEncoding } catch { Write-Verbose 'console input encoding not restored' }
        }
    }
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    if ($sendStdin) {
        $stdinBytes = $utf8.GetBytes($StdinText)
        $process.StandardInput.BaseStream.Write($stdinBytes, 0, $stdinBytes.Length)
        $process.StandardInput.BaseStream.Flush()
    }
    $process.StandardInput.Close()
    if ($TimeoutSeconds -gt 0) {
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            try { $process.Kill() } catch { Write-Verbose 'process already ended' }
            $process.Dispose()
            throw ("{0}: {1} saniyede bitmedi, durduruldu [did not finish within {1} s, stopped]" -f [System.IO.Path]::GetFileName($FilePath), $TimeoutSeconds)
        }
    }
    $process.WaitForExit()
    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result
    foreach ($secret in $Redact) {
        if ($secret) {
            $stdout = $stdout.Replace($secret, '***')
            $stderr = $stderr.Replace($secret, '***')
        }
    }
    $result = [pscustomobject]@{ ExitCode = $process.ExitCode; StdOut = $stdout; StdErr = $stderr }
    $process.Dispose()
    if ($result.ExitCode -ne 0 -and -not $AllowFailure) {
        $name = [System.IO.Path]::GetFileName($FilePath)
        throw ("{0} çıkış kodu {1} [exit code]: {2}{3}" -f $name, $result.ExitCode, $result.StdErr.Trim(), $(if ($result.StdOut.Trim()) { ' | ' + $result.StdOut.Trim() } else { '' }))
    }
    $result
}

function Invoke-ArciExe {
    <#
    .SYNOPSIS
        Run a program attached to the console (its progress stays visible) and fail on a non-zero
        exit code. Never use it for arguments that contain secrets.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$FilePath, [AllowEmptyCollection()][string[]]$ArgumentList = @())
    & $FilePath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw ("{0} çıkış kodu {1} [exit code]" -f [System.IO.Path]::GetFileName($FilePath), $LASTEXITCODE)
    }
}

# --- configuration --------------------------------------------------------------------------------

function Get-ArciConfiguredDataDir {
    <#
    .SYNOPSIS
        data_dir of an arcilogger.toml (with Windows separators on Windows), or $null when the file
        or the key is missing.
    .DESCRIPTION
        A top-level string key, so a narrow pattern is enough where no Python may be installed yet
        (the installer before its program files, Set-ArciVaultAcl.ps1); everything else is read
        with tomllib (Read-ArciTomlFile).
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$ConfigPath)
    if (-not (Test-Path -LiteralPath $ConfigPath)) { return $null }
    $text = [System.IO.File]::ReadAllText($ConfigPath)
    $head = ($text -split '(?m)^\s*\[')[0]
    $match = [regex]::Match($head, '(?m)^\s*data_dir\s*=\s*["''](?<v>[^"'']+)["'']')
    if (-not $match.Success) { return $null }
    $value = $match.Groups['v'].Value
    if (Test-ArciWindows) { $value = $value.Replace('/', '\') }
    $value
}

function Read-ArciDataRootRecord {
    <#
    .SYNOPSIS
        The data root the installer recorded (data-root.txt next to arcilogger.toml, Day 0 notes
        D0-2 and WP2-1), read as the core reads it (config.recorded_data_root): Exists, Value
        and Problem.
    .DESCRIPTION
        Value is the record's one line, an absolute path (UTF-8; a BOM and a final newline are
        accepted). Problem says why a record that exists cannot be used: unreadable, not UTF-8,
        not one line with an absolute path, or an owner other than SYSTEM, Administrators or
        TrustedInstaller (only administrators write the record; an owner can change its rights,
        and ArciAdmin may create files in that folder). -Owner: the owner's SID; by default read
        from the file on Windows and not checked elsewhere. A missing record is no problem (an
        installation from before the browser administration).
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path, [string]$Owner)
    $record = [pscustomobject]@{ Path = $Path; Exists = $false; Value = $null; Problem = $null }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $record }
    $record.Exists = $true
    try {
        $bytes = [System.IO.File]::ReadAllBytes($Path)
        if (-not $Owner -and (Test-ArciWindows)) {
            $security = Get-ArciAccessControl -Item (Get-Item -LiteralPath $Path -Force) -Sections 'Owner'
            $Owner = $security.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
        }
    }
    catch {
        $record.Problem = 'okunamadı [unreadable]: {0}' -f $_.Exception.Message
        return $record
    }
    if ($Owner -and $script:TrustedWriterSids -notcontains $Owner) {
        $record.Problem = 'sahibi yöneticiler ya da SYSTEM değil [not owned by Administrators or SYSTEM]: {0}' -f $Owner
        return $record
    }
    try { $text = (New-Object System.Text.UTF8Encoding -ArgumentList $false, $true).GetString($bytes) }
    catch {
        $record.Problem = 'UTF-8 değil [not UTF-8]'
        return $record
    }
    $lines = @(($text.TrimStart([char]0xFEFF).Trim() -split '\r\n|\r|\n') | Where-Object { $_ -ne '' })
    $absolute = $(if (Test-ArciWindows) { '^([A-Za-z]:[\\/]|[\\/]{2}[^\\/]+[\\/][^\\/]+)' } else { '^/' })
    if ($lines.Count -ne 1 -or $lines[0].Trim() -notmatch $absolute) {
        $record.Problem = 'tek satırda mutlak bir yol değil [not one line with an absolute path]'
        return $record
    }
    $record.Value = $lines[0].Trim()
    $record
}

function Resolve-ArciDataRoot {
    <#
    .SYNOPSIS
        The data root of an installer run and where it comes from (Source: parameter, record,
        config or auto), or the Problem that stops the run before any change. Pure.
    .DESCRIPTION
        The record (Read-ArciDataRootRecord) anchors data_dir: ArciAdmin can write
        arcilogger.toml, and every service and task refuses a data_dir other than the recorded
        one (Day 0 note D0-2). A run therefore never takes a changed data_dir from the
        configuration and never moves the record on its own:
        - -Given (-DataRoot) is a deliberate choice: it must equal the configuration's data_dir
          when there is one, and it replaces the record (Replaces: the root recorded before, or
          why the record could not be used);
        - otherwise the record: one that cannot be used, or a data_dir that differs from it,
          stops the run (put data_dir back, or move deliberately with -DataRoot);
        - without a record, the configuration's data_dir (an installation from before the
          browser administration, whose configuration only administrators could change), unless
          -AdminInstalled: then the record was removed, and only -DataRoot names the data root;
        - otherwise Path is $null and Source 'auto': the caller chooses a drive.
        The caller passes full paths; case and a trailing or forward separator do not count.
    #>
    [CmdletBinding()]
    param(
        [AllowNull()]$Record,
        [AllowNull()][AllowEmptyString()][string]$Configured,
        [AllowNull()][AllowEmptyString()][string]$Given,
        [switch]$AdminInstalled
    )
    $separators = [char[]]@([char]92, [char]47)
    $same = { param([string]$A, [string]$B) $A.Replace('/', '\').TrimEnd($separators) -ieq $B.Replace('/', '\').TrimEnd($separators) }
    $result = [pscustomobject]@{ Path = $null; Source = 'auto'; Problem = $null; Replaces = $null }
    $exists = $null -ne $Record -and [bool]$Record.Exists
    $recordProblem = $(if ($exists) { [string]$Record.Problem } else { '' })
    $recorded = $(if ($exists -and -not $recordProblem) { [string]$Record.Value } else { '' })
    $where = $(if ($null -ne $Record) { [string]$Record.Path } else { 'data-root.txt' })
    if ($Given) {
        if ($Configured -and -not (& $same $Given $Configured)) {
            $result.Problem = '-DataRoot ({0}) arcilogger.toml içindeki data_dir ({1}) ile aynı değil [differs from data_dir in arcilogger.toml].' -f $Given, $Configured
            return $result
        }
        $result.Path = $Given
        $result.Source = 'parameter'
        if ($recordProblem) { $result.Replaces = '({0})' -f $recordProblem }
        elseif ($recorded -and -not (& $same $Given $recorded)) { $result.Replaces = $recorded }
        return $result
    }
    if ($recordProblem) {
        $result.Problem = '{0} kullanılamıyor [cannot be used] ({1}); yönetici olarak düzeltin ya da kurulumun veri klasörünü -DataRoot ile verin [fix it as an administrator or give the installation''s data folder with -DataRoot].' -f $where, $recordProblem
        return $result
    }
    if ($recorded) {
        if ($Configured -and -not (& $same $Configured $recorded)) {
            $result.Problem = 'arcilogger.toml içindeki data_dir ({0}) kayıtlı veri klasörü {1} ({2}) ile aynı değil; hizmetler ve görevler bu yapılandırmayı reddeder. data_dir''i {1} olarak düzeltin; veri klasörünü bilerek taşıdıysanız kurulumu -DataRoot "{0}" ile çalıştırın [data_dir differs from the recorded data root, which the services and tasks refuse: set data_dir back to {1}, or run the installer with -DataRoot "{0}" to move it deliberately].' -f $Configured, $recorded, $where
            return $result
        }
        $result.Path = $recorded
        $result.Source = 'record'
        return $result
    }
    if ($Configured) {
        if ($AdminInstalled) {
            $result.Problem = '{0} yok ama tarayıcıdan yönetim kurulu; kurulumun veri klasörünü -DataRoot ile verin (arcilogger.toml: data_dir = {1}) [the data root record is missing although the browser administration is installed: give the installation''s data folder with -DataRoot].' -f $where, $Configured
            return $result
        }
        $result.Path = $Configured
        $result.Source = 'config'
    }
    $result
}

function Read-ArciTomlFile {
    <#
    .SYNOPSIS
        Read arcilogger.toml with Python's tomllib (the same parser ARCILOGGER uses) and return it
        as objects. PowerShell has no TOML parser; a regular expression would get arrays and
        tables wrong.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Python, [Parameter(Mandatory = $true)][string]$ConfigPath)
    $code = 'import json,sys,tomllib; print(json.dumps(tomllib.load(open(sys.argv[1],''rb'')),default=str))'
    $result = Invoke-ArciProcess -FilePath $Python -ArgumentList @('-I', '-c', $code, $ConfigPath)
    $result.StdOut | ConvertFrom-Json
}

function Get-ArciConfigValue {
    <# Value at a dotted path such as "clock.ntp_server", or -Default when any part is missing. #>
    [CmdletBinding()]
    param([AllowNull()]$Config, [Parameter(Mandatory = $true)][string]$Path, $Default = $null)
    $node = $Config
    foreach ($part in $Path.Split('.')) {
        if ($null -eq $node) { return $Default }
        # Indexed lookup: "Properties.Name" of an empty table ([api] without keys) is an error
        # under StrictMode.
        $property = $node.PSObject.Properties[$part]
        if ($null -eq $property) { return $Default }
        $node = $property.Value
    }
    if ($null -eq $node) { return $Default }
    $node
}

# --- setup values in arcilogger.toml --------------------------------------------------------------
#
# The installer writes the organisation name (site_name) and the local time source ([clock]
# ntp_server) into arcilogger.toml: into the new file it makes from the example, and into an
# existing one when -SiteName or -NtpServer is given. PowerShell has no TOML parser and Python may
# not be installed yet, so these helpers know one shape only, a one-line string key (key = "..."
# or key = '...', a comment after it is kept), and refuse every other one (a dotted key, an inline
# table, a multi-line string, a key given twice): the operator then edits the file by hand.

function Get-ArciTomlRegion {
    <# Where the keys of -Section are: Found, Start and Length of its body ('' = the top level, before the first table). #>
    param([AllowEmptyString()][string]$Text, [AllowEmptyString()][string]$Section)
    $firstTable = [regex]::Match($Text, '(?m)^[ \t]*\[')
    $topLength = $(if ($firstTable.Success) { $firstTable.Index } else { $Text.Length })
    if (-not $Section) { return [pscustomobject]@{ Found = $true; Start = 0; Length = $topLength } }
    $name = [regex]::Escape($Section)
    $top = $Text.Substring(0, $topLength)
    if ([regex]::IsMatch($top, ('(?m)^[ \t]*["'']?{0}["'']?[ \t]*[.=]' -f $name)) -or [regex]::IsMatch($Text, ('(?m)^[ \t]*\[\[[ \t]*{0}[ \t]*\]\]' -f $name))) {
        throw ("[{0}] nokta ile, satır içi tablo ya da tablo dizisi olarak yazılmış; dosyayı elle düzenleyin [written as dotted keys, an inline table or an array of tables: edit the file by hand]" -f $Section)
    }
    $tables = [regex]::Matches($Text, ('(?ms)^[ \t]*\[[ \t]*{0}[ \t]*\][ \t]*(?:#[^\r\n]*)?\r?$(?<body>.*?)(?=^[ \t]*\[|\z)' -f $name))
    if ($tables.Count -gt 1) { throw ("[{0}] iki kez var [the table appears twice]" -f $Section) }
    if ($tables.Count -eq 0) { return [pscustomobject]@{ Found = $false; Start = $Text.Length; Length = 0 } }
    $body = $tables[0].Groups['body']
    [pscustomobject]@{ Found = $true; Start = $body.Index; Length = $body.Length }
}

function ConvertFrom-ArciTomlEscape([string]$Raw) {
    <# The text of a TOML basic string between its quotes, escape sequences resolved. #>
    $builder = New-Object System.Text.StringBuilder
    $i = 0
    while ($i -lt $Raw.Length) {
        $character = $Raw[$i]
        if ($character -ne [char]92) {
            [void]$builder.Append($character)
            $i++
            continue
        }
        $code = [string]$Raw[$i + 1]
        switch -CaseSensitive ($code) {
            'b' { [void]$builder.Append([char]8); $i += 2 }
            't' { [void]$builder.Append([char]9); $i += 2 }
            'n' { [void]$builder.Append([char]10); $i += 2 }
            'f' { [void]$builder.Append([char]12); $i += 2 }
            'r' { [void]$builder.Append([char]13); $i += 2 }
            '"' { [void]$builder.Append([char]34); $i += 2 }
            '\' { [void]$builder.Append([char]92); $i += 2 }
            'u' { [void]$builder.Append([char]::ConvertFromUtf32([Convert]::ToInt32($Raw.Substring($i + 2, 4), 16))); $i += 6 }
            'U' { [void]$builder.Append([char]::ConvertFromUtf32([Convert]::ToInt32($Raw.Substring($i + 2, 8), 16))); $i += 10 }
            default { throw ("geçersiz kaçış dizisi [invalid escape sequence]: \{0}" -f $code) }
        }
    }
    $builder.ToString()
}

function Find-ArciTomlKey {
    <# The one-line string key -Key of -Section: Region, Found, Value and where the value (with its quotes) is in -Text. #>
    param([AllowEmptyString()][string]$Text, [AllowEmptyString()][string]$Section, [string]$Key)
    $region = Get-ArciTomlRegion -Text $Text -Section $Section
    $result = [pscustomobject]@{ Region = $region; Found = $false; Value = $null; ValueIndex = -1; ValueLength = 0 }
    if (-not $region.Found) { return $result }
    $body = $Text.Substring($region.Start, $region.Length)
    $name = [regex]::Escape($Key)
    $label = $(if ($Section) { '[{0}] {1}' -f $Section, $Key } else { $Key })
    $lines = [regex]::Matches($body, ('(?m)^[ \t]*["'']?{0}["'']?[ \t]*=' -f $name))
    if ($lines.Count -eq 0) { return $result }
    if ($lines.Count -gt 1) { throw ("{0} iki kez var [the key appears twice]" -f $label) }
    $pattern = '(?m)^[ \t]*{0}[ \t]*=[ \t]*(?<value>"(?<basic>(?:[^"\\\r\n]|\\.)*)"|''(?<literal>[^''\r\n]*)'')[ \t]*(?:#[^\r\n]*)?(?=\r?$)' -f $name
    $simple = [regex]::Match($body, $pattern)
    if (-not $simple.Success) {
        throw ("{0} tek satırlık bir metin değil; dosyayı elle düzenleyin [not a one-line string: edit the file by hand]" -f $label)
    }
    $result.Found = $true
    if ($simple.Groups['basic'].Success) { $result.Value = ConvertFrom-ArciTomlEscape $simple.Groups['basic'].Value }
    else { $result.Value = $simple.Groups['literal'].Value }
    $result.ValueIndex = $region.Start + $simple.Groups['value'].Index
    $result.ValueLength = $simple.Groups['value'].Length
    $result
}

function ConvertTo-ArciTomlString {
    <#
    .SYNOPSIS
        -Value as a TOML basic string ("..."): backslash, quote and control characters escaped,
        everything else (Turkish letters too) as it is; arcilogger.toml is UTF-8.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([AllowEmptyString()][string]$Value)
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    foreach ($character in $Value.ToCharArray()) {
        $code = [int]$character
        if ($code -eq 34) { [void]$builder.Append('\"') }
        elseif ($code -eq 92) { [void]$builder.Append('\\') }
        elseif ($code -lt 32 -or $code -eq 127) { [void]$builder.Append(('\u{0:X4}' -f $code)) }
        else { [void]$builder.Append($character) }
    }
    [void]$builder.Append('"')
    $builder.ToString()
}

function Get-ArciTomlString {
    <#
    .SYNOPSIS
        The value of a one-line string key of arcilogger.toml given as text, read without Python:
        $null when the key is not there. -Section '' (the default) is the top level (site_name).
    .DESCRIPTION
        Throws when the key or its table is written in a form these helpers do not handle (see
        above): guessing would let the installer report or overwrite the wrong value.
    #>
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Text, [AllowEmptyString()][string]$Section = '', [Parameter(Mandatory = $true)][string]$Key)
    (Find-ArciTomlKey -Text $Text -Section $Section -Key $Key).Value
}

function Set-ArciTomlString {
    <#
    .SYNOPSIS
        -Text with the one-line string key -Key of -Section set to -Value, everything else as it
        was (comments, order, line endings).
    .DESCRIPTION
        An existing value is replaced in place (a comment after it stays). A missing key goes to
        the top of its table: right below the table header, or for the top level below the
        comment lines that start the file. A missing table is added at the end. Throws like
        Get-ArciTomlString.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [AllowEmptyString()][string]$Text,
        [AllowEmptyString()][string]$Section = '',
        [Parameter(Mandatory = $true)][string]$Key,
        [AllowEmptyString()][string]$Value
    )
    $literal = ConvertTo-ArciTomlString -Value $Value
    $found = Find-ArciTomlKey -Text $Text -Section $Section -Key $Key
    if ($found.Found) {
        return $Text.Substring(0, $found.ValueIndex) + $literal + $Text.Substring($found.ValueIndex + $found.ValueLength)
    }
    $newline = $(if ($Text.Contains("`r`n")) { "`r`n" } else { "`n" })
    $line = '{0} = {1}' -f $Key, $literal
    $region = $found.Region
    if (-not $region.Found) {
        $head = $Text
        if ($head -and -not $head.EndsWith("`n")) { $head += $newline }
        return $head + $newline + ('[{0}]' -f $Section) + $newline + $line + $newline
    }
    if (-not $Section) {
        $at = [regex]::Match($Text, '\A(?:[ \t]*(?:#[^\r\n]*)?\r?\n)*').Length
        return $Text.Substring(0, $at) + $line + $newline + $Text.Substring($at)
    }
    # The body starts at the end of the header line.
    $header = [regex]::Match($Text.Substring($region.Start, $region.Length), '\A[^\n]*\n')
    if (-not $header.Success) { return $Text + $newline + $line + $newline }
    $at = $region.Start + $header.Length
    $Text.Substring(0, $at) + $line + $newline + $Text.Substring($at)
}

function Get-ArciSiteNameProblem {
    <#
    .SYNOPSIS
        Why -Value cannot be the organisation name (site_name), "Türkçe [English]", or $null when
        it can: one line of text after trimming, not empty, at most 200 characters, no control or
        format characters (the rules of arcilogger_core.config).
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([AllowEmptyString()][string]$Value)
    $text = $Value.Trim()
    if (-not $text) { return 'boş [empty]' }
    if ($text.Length -gt 200) { return ('{0} karakter, en çok 200 [characters, at most 200]' -f $text.Length) }
    foreach ($character in $text.ToCharArray()) {
        $category = [string][System.Globalization.CharUnicodeInfo]::GetUnicodeCategory($character)
        if (@('Control', 'Format', 'LineSeparator', 'ParagraphSeparator') -contains $category) {
            return ('denetim karakteri var (U+{0:X4}) [contains a control character]' -f [int]$character)
        }
    }
    $null
}

function Get-ArciTimeServerProblem {
    <#
    .SYNOPSIS
        Why -Value cannot be a time server (clock.ntp_server / fallback_server), "Türkçe
        [English]", or $null when it can: an IPv4 address (four decimal octets), an IPv6 address
        or a host name. Empty is allowed here (no local time source); the caller decides.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([AllowEmptyString()][string]$Value)
    $text = $Value.Trim()
    if (-not $text) { return $null }
    if ($text -match '\s') { return 'boşluk var [contains a space]' }
    if ($text -match '^[0-9.]+$') {
        if ($text -match $script:Ipv4Pattern) { return $null }
        return 'geçersiz IPv4 adresi [invalid IPv4 address]'
    }
    if ($text.Contains(':')) {
        if (-not $text.Contains('/') -and $null -ne (ConvertTo-ArciNetwork -Value $text)) { return $null }
        return 'geçersiz IPv6 adresi [invalid IPv6 address]'
    }
    $label = '[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
    if ($text.Length -le 253 -and $text -match ('^' + $label + '(?:\.' + $label + ')*\.?$')) { return $null }
    'ne IP adresi ne ana bilgisayar adı [neither an IP address nor a host name]'
}

function Get-ArciTimePeerList {
    <#
    .SYNOPSIS
        The w32tm /manualpeerlist value for Set-ArciTime.ps1: the local time source in client
        mode (0x8) with the fallback used only as a fallback (0xA = 0x8 + 0x2); without a local
        source the fallback is the only source, in client mode.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([AllowEmptyString()][string]$Primary, [AllowEmptyString()][string]$Fallback)
    $local = ([string]$Primary).Trim()
    $public = ([string]$Fallback).Trim()
    if ($local -and $public -and $local -ne $public) { return ('{0},0x8 {1},0xA' -f $local, $public) }
    if ($local) { return ('{0},0x8' -f $local) }
    if ($public) { return ('{0},0x8' -f $public) }
    throw 'Saat sunucusu yok [no time server]: [clock] ntp_server ve fallback_server boş [both are empty].'
}

function Read-ArciAdminAddress {
    <#
    .SYNOPSIS
        Ask at the console for the admin PC's address(es): the firewall opens 443 (web) and 3389
        (remote desktop) only for them. Addresses or CIDR networks, several separated by commas or
        spaces; asked again until every value is valid (Test-ArciAddressSpec), at most -Attempts
        times. Returns the values, or nothing. The caller asks only when a person sits at the
        console (Test-ArciInteractiveConsole).
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param([int]$Attempts = 3)
    $prompt = 'Yönetici PC''sinin IP adresi ya da ağı; 443 (web) ve 3389 (uzak masaüstü) yalnızca ona açılır, örnek 10.0.0.10 veya 10.0.0.0/24 [admin PC address or network: 443 and 3389 are opened only for it, e.g. 10.0.0.10 or 10.0.0.0/24]'
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        $answer = $null
        try { $answer = [string](Read-Host $prompt) }
        catch { return }
        $values = @(([string]$answer).Split([char[]]@(',', ';', ' ', "`t"), [System.StringSplitOptions]::RemoveEmptyEntries))
        $invalid = @($values | Where-Object { -not (Test-ArciAddressSpec $_) })
        if ($values.Count -gt 0 -and $invalid.Count -eq 0) { return $values }
        if ($values.Count -eq 0) { Write-Host '    Adres gerekli [an address is required].' }
        else { Write-Host ('    Geçersiz [invalid]: {0}' -f ($invalid -join ', ')) }
    }
}

function Get-ArciApiEndpoint {
    <#
    .SYNOPSIS
        Where ArciApi is reached on this server, from [api] host/port of arcilogger.toml (default
        127.0.0.1:8000, the defaults of arcilogger_core.config.ApiConfig). Caddy's upstream, the
        installer's health check and its preflight port probe all follow it, so changing the
        port in arcilogger.toml (and running the installer again) never leaves Caddy on the old one.
    .DESCRIPTION
        A wildcard address is reached through the loopback address of its family (0.0.0.0 ->
        127.0.0.1, :: -> ::1); an IPv6 literal is written in brackets. Returns HostName, Port,
        Address ("host:port", the form Caddy's reverse_proxy and an http:// URL take),
        ForwardedAllowIps ([api] forwarded_allow_ips, default 127.0.0.1) and ProxyTrust
        (Test-ArciProxyTrust: whether the API believes the client address Caddy forwards).
    #>
    [CmdletBinding()]
    param([AllowNull()]$Config)
    $allowed = [string](Get-ArciConfigValue -Config $Config -Path 'api.forwarded_allow_ips' -Default '127.0.0.1')
    $hostName = ([string](Get-ArciConfigValue -Config $Config -Path 'api.host' -Default '127.0.0.1')).Trim()
    $portValue = Get-ArciConfigValue -Config $Config -Path 'api.port' -Default 8000
    $port = 0
    if (-not [int]::TryParse([string]$portValue, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw ("[api] port geçersiz [invalid]: {0}" -f $portValue)
    }
    $hostName = $hostName.TrimStart([char]'[').TrimEnd([char]']')
    if (-not $hostName -or $hostName -eq '0.0.0.0') { $hostName = '127.0.0.1' }
    elseif ($hostName -eq '::') { $hostName = '::1' }
    if ($hostName -notmatch '^[A-Za-z0-9.:_-]+$') { throw ("[api] host geçersiz [invalid]: {0}" -f $hostName) }
    $authority = if ($hostName.Contains(':')) { '[' + $hostName + ']' } else { $hostName }
    [pscustomobject]@{
        HostName = $hostName; Port = $port; Address = ('{0}:{1}' -f $authority, $port)
        ForwardedAllowIps = $allowed; ProxyTrust = (Test-ArciProxyTrust -HostName $hostName -Allowed $allowed)
    }
}

function Test-ArciProxyTrust {
    <#
    .SYNOPSIS
        Whether ArciApi believes the client address Caddy forwards (X-Forwarded-For). Returns
        'trusted', 'untrusted' or 'unknown'.
    .DESCRIPTION
        Caddy reaches the API at -HostName ([api] host after Get-ArciApiEndpoint) and, on the same
        server, connects from that very address (127.0.0.1 for 127.0.0.1, ::1 for ::1). uvicorn
        trusts X-Forwarded-For only from [api] forwarded_allow_ips (-Allowed): "*", or addresses
        and CIDR networks separated by commas. When that list does not cover the address, every
        request counts as coming from Caddy's address: the audit log, the sessions and the per-IP
        login limits then record the server itself instead of the user's PC.

        The rules are uvicorn's (uvicorn.middleware.proxy_headers._TrustedHosts, checked against it
        in tests/test_installer_scripts.py): only the whole value "*" trusts everyone; a network
        with host bits set ("127.0.0.1/8") or a malformed entry never matches an address; IPv4 and
        IPv6 are different addresses (127.0.0.1 does not cover ::1). 'unknown': -HostName is a
        name such as "localhost", resolved by Caddy at run time, so the address cannot be known.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)][string]$HostName, [AllowNull()][AllowEmptyString()][string]$Allowed)
    if ($Allowed -ceq '*') { return 'trusted' }
    $client = $null
    if (-not $HostName.Contains('/')) { $client = ConvertTo-ArciNetwork -Value $HostName }
    if ($null -eq $client) { return 'unknown' }
    foreach ($item in ([string]$Allowed).Split(',')) {
        $entry = $item.Trim()
        $network = ConvertTo-ArciNetwork -Value $entry
        if ($null -eq $network -or $network.Bytes.Length -ne $client.Bytes.Length) { continue }
        $covered = $true
        for ($i = 0; $i -lt $client.Bytes.Length; $i++) {
            $bits = [Math]::Max(0, [Math]::Min(8, $network.Prefix - 8 * $i))
            $mask = (0xFF -shl (8 - $bits)) -band 0xFF
            # Host bits set: Python's ip_network (strict) refuses it, uvicorn keeps it as text.
            if (([int]$network.Bytes[$i] -band (-bnot $mask) -band 0xFF) -ne 0) { $covered = $false; break }
            if (([int]$client.Bytes[$i] -band $mask) -ne ([int]$network.Bytes[$i] -band $mask)) { $covered = $false; break }
        }
        if ($covered) { return 'trusted' }
    }
    'untrusted'
}

function Get-ArciSourceAddresses {
    <#
    Sender addresses and networks from the [[sources]] tables (for the firewall). A source with
    enabled = false is left out: its address gets no 514 opening (a message that still arrives
    from it is recorded as before, ADR-0014).
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param([Parameter(Mandatory = $true)]$Config)
    $addresses = New-Object System.Collections.Generic.List[string]
    if ($Config.PSObject.Properties.Name -contains 'sources') {
        foreach ($source in @($Config.sources)) {
            if ($source.PSObject.Properties.Name -contains 'enabled' -and $source.enabled -eq $false) { continue }
            if ($source.PSObject.Properties.Name -contains 'address' -and $source.address) { $addresses.Add([string]$source.address) }
        }
    }
    $addresses.ToArray()
}

function Get-ArciSourceTables {
    <#
    .SYNOPSIS
        The [[sources]] tables of arcilogger.toml given as text, read without Python: how many
        there are (Tables) and the address of each one that is written as a plain one-line string
        (Addresses, in file order).
    .DESCRIPTION
        A narrow pattern like Get-ArciCollectorBind, for the case where no Python is installed yet
        (a new installation, or one over the configuration a removal kept); with Python the
        installer reads the file with tomllib (Get-ArciSourceAddresses). A commented-out table
        ("#[[sources]]", as in the example configuration) is not one, and neither is a source
        switched off with enabled = false (as Get-ArciSourceAddresses). An address written in
        another form (a dotted key, an inline table, a multi-line string) is not seen: Tables is
        then larger than the number of Addresses.
    #>
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Text)
    $tables = [regex]::Matches($Text, '(?ms)^[ \t]*\[\[[ \t]*sources[ \t]*\]\][ \t]*(?:#[^\r\n]*)?\r?$(?<body>.*?)(?=^[ \t]*\[|\z)')
    $addresses = New-Object System.Collections.Generic.List[string]
    $count = 0
    foreach ($table in $tables) {
        $body = $table.Groups['body'].Value
        if ([regex]::IsMatch($body, '(?m)^[ \t]*enabled[ \t]*=[ \t]*false[ \t]*(?:#[^\r\n]*)?\r?$')) { continue }
        $count++
        $address = [regex]::Match($body, '(?m)^[ \t]*address[ \t]*=[ \t]*(?:"(?<v>[^"\r\n]*)"|''(?<v>[^''\r\n]*)'')[ \t]*(?:#[^\r\n]*)?\r?$')
        # An empty address is skipped like Get-ArciSourceAddresses does.
        if ($address.Success -and $address.Groups['v'].Value) { $addresses.Add($address.Groups['v'].Value) }
    }
    [pscustomobject]@{ Tables = $count; Addresses = $addresses.ToArray() }
}

# --- addresses ------------------------------------------------------------------------------------

# Exactly four decimal octets 0-255 without leading zeros. [IPAddress]::TryParse alone also
# accepts shorthand such as "10.0.3" (read as 10.0.0.3) or "10" (0.0.0.10): a typo would
# silently open the firewall for a different host.
$script:Ipv4Pattern = '^(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])(\.(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])){3}$'

function ConvertTo-ArciNetwork {
    <#
    .SYNOPSIS
        Parse an IPv4/IPv6 address or a network in CIDR form ("10.0.1.0/24") strictly.
        Returns $null for anything else (host names, shorthand, ranges, zone ids, bad prefixes).
    #>
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Value)
    if (-not $Value) { return $null }
    $text = $Value.Trim()
    $address = $text
    $prefixText = $null
    $slash = $text.IndexOf('/')
    if ($slash -ge 0) {
        $address = $text.Substring(0, $slash)
        $prefixText = $text.Substring($slash + 1)
    }
    $parsed = $null
    if ($address -match $script:Ipv4Pattern) {
        $parsed = [System.Net.IPAddress]::Parse($address)
        $max = 32
    }
    elseif ($address.Contains(':') -and $address -match '^[0-9A-Fa-f:.]+$') {
        if (-not [System.Net.IPAddress]::TryParse($address, [ref]$parsed)) { return $null }
        if ($parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetworkV6) { return $null }
        $max = 128
    }
    else {
        return $null
    }
    $prefix = $max
    if ($null -ne $prefixText) {
        if ($prefixText -notmatch '^(0|[1-9][0-9]{0,2})$') { return $null }
        $prefix = [int]$prefixText
        if ($prefix -gt $max) { return $null }
    }
    [pscustomobject]@{ Text = $text; Address = $parsed; Prefix = $prefix; Bytes = $parsed.GetAddressBytes() }
}

function Test-ArciAddressSpec {
    <# True for an address or CIDR network that ConvertTo-ArciNetwork accepts. #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([AllowEmptyString()][string]$Value)
    $null -ne (ConvertTo-ArciNetwork -Value $Value)
}

function Test-ArciAddressInNetwork {
    <# True when the address lies inside the address/network spec (IPv4-mapped IPv6 counts as IPv4). #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory = $true)][string]$Address, [Parameter(Mandatory = $true)][string]$Network)
    $net = ConvertTo-ArciNetwork -Value $Network
    if ($null -eq $net) { return $false }
    $parsed = $null
    if (-not [System.Net.IPAddress]::TryParse($Address, [ref]$parsed)) { return $false }
    if ($parsed.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetworkV6 -and $parsed.IsIPv4MappedToIPv6) {
        $parsed = $parsed.MapToIPv4()
    }
    $bytes = $parsed.GetAddressBytes()
    if ($bytes.Length -ne $net.Bytes.Length) { return $false }
    $bits = $net.Prefix
    for ($i = 0; $i -lt $bytes.Length -and $bits -gt 0; $i++) {
        $take = [Math]::Min(8, $bits)
        $mask = (0xFF -shl (8 - $take)) -band 0xFF
        if (([int]$bytes[$i] -band $mask) -ne ([int]$net.Bytes[$i] -band $mask)) { return $false }
        $bits -= $take
    }
    $true
}

function Get-ArciForeignRdpSession {
    <#
    .SYNOPSIS
        Remote addresses of open remote desktop connections that -AdminAddress does not cover
        (they would be cut off by the firewall rules). Loopback connections are never filtered.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param([Parameter(Mandatory = $true)][string[]]$AdminAddress, [int]$Port = 3389)
    # Writes the addresses one by one: call it as @(Get-ArciForeignRdpSession ...).
    $foreign = New-Object System.Collections.Generic.List[string]
    if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) { return }
    foreach ($session in @(Get-NetTCPConnection -LocalPort $Port -State Established -ErrorAction SilentlyContinue)) {
        if ($null -eq $session) { continue }
        $remote = [string]$session.RemoteAddress
        $parsed = $null
        if ([System.Net.IPAddress]::TryParse($remote, [ref]$parsed) -and [System.Net.IPAddress]::IsLoopback($parsed)) { continue }
        $covered = $false
        foreach ($spec in $AdminAddress) {
            if (Test-ArciAddressInNetwork -Address $remote -Network $spec) { $covered = $true; break }
        }
        if (-not $covered -and -not $foreign.Contains($remote)) { $foreign.Add($remote) }
    }
    $foreign.ToArray()
}

# --- this computer's addresses and the collector's ----------------------------------------------

function Get-ArciNetworkFacts {
    <#
    .SYNOPSIS
        The network interfaces of this computer as plain data: Name, Description, Status (Up,
        Down, ...), Loopback, Addresses (Address, Family InterNetwork/InterNetworkV6, Dhcp) and
        Gateways (the IPv4 default gateways).
    .DESCRIPTION
        Read through .NET's NetworkInterface, which behaves the same in Windows PowerShell 5.1 and
        PowerShell 7 (and on the test runners). Dhcp is $true or $false where Windows reports the
        origin of an address (PrefixOrigin), $null where it cannot be known. Select-ArciServerAddress
        and Get-ArciLocalAddresses work on this data, so the tests can give them any network.
    #>
    [CmdletBinding()]
    param()
    $facts = New-Object System.Collections.Generic.List[object]
    $interfaces = @()
    try { $interfaces = @([System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) }
    catch { Write-Verbose ('network interfaces not readable: {0}' -f $_.Exception.Message) }
    foreach ($interface in $interfaces) {
        $addresses = New-Object System.Collections.Generic.List[object]
        $gateways = New-Object System.Collections.Generic.List[string]
        $properties = $null
        try { $properties = $interface.GetIPProperties() } catch { $properties = $null }
        if ($null -ne $properties) {
            foreach ($unicast in @($properties.UnicastAddresses)) {
                $dhcp = $null
                # PrefixOrigin exists on Windows only (PlatformNotSupportedException elsewhere).
                try { $dhcp = ([string]$unicast.PrefixOrigin -eq 'Dhcp') } catch { $dhcp = $null }
                $text = $unicast.Address.ToString()
                $zone = $text.IndexOf('%')
                if ($zone -ge 0) { $text = $text.Substring(0, $zone) }
                $addresses.Add([pscustomobject]@{ Address = $text; Family = [string]$unicast.Address.AddressFamily; Dhcp = $dhcp })
            }
            foreach ($gateway in @($properties.GatewayAddresses)) {
                $address = $gateway.Address
                if ($address.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and -not $address.Equals([System.Net.IPAddress]::Any)) {
                    $gateways.Add($address.ToString())
                }
            }
        }
        $loopback = $false
        try { $loopback = ($interface.NetworkInterfaceType -eq [System.Net.NetworkInformation.NetworkInterfaceType]::Loopback) } catch { $loopback = $false }
        $facts.Add([pscustomobject]@{
                Name = [string]$interface.Name; Description = [string]$interface.Description
                Status = [string]$interface.OperationalStatus; Loopback = $loopback
                Addresses = $addresses.ToArray(); Gateways = $gateways.ToArray()
            })
    }
    , $facts.ToArray()
}

function Get-ArciLocalAddresses {
    <#
    .SYNOPSIS
        The addresses a program can listen on here: those of every interface that is up (or whose
        state the system does not report, such as the loopback interface of Linux) and of the
        loopback interfaces. A disconnected adapter's address cannot be bound.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param([AllowEmptyCollection()][object[]]$Interfaces)
    if (-not $PSBoundParameters.ContainsKey('Interfaces')) { $Interfaces = Get-ArciNetworkFacts }
    $found = New-Object System.Collections.Generic.List[string]
    foreach ($interface in @($Interfaces)) {
        if ($null -eq $interface) { continue }
        if (-not $interface.Loopback -and $interface.Status -ne 'Up' -and $interface.Status -ne 'Unknown') { continue }
        foreach ($address in @($interface.Addresses)) {
            if ($null -ne $address -and $address.Address -and -not $found.Contains([string]$address.Address)) { $found.Add([string]$address.Address) }
        }
    }
    , $found.ToArray()
}

function Select-ArciServerAddress {
    <#
    .SYNOPSIS
        The server address to use when -ServerAddress was neither given nor remembered: the one
        IPv4 address of the one interface that is up and has an IPv4 default gateway.
    .DESCRIPTION
        Nothing is guessed. No such interface, several of them (a second network card, a VPN or a
        virtual switch with its own gateway) or several IPv4 addresses on it give no Address; the
        installer then stops and asks for -ServerAddress. Link-local addresses (169.254.x.x) do
        not count. Returns Address ($null when not decided), Dhcp ($true, $false or $null when
        unknown), Interface, Gateway and Candidates (one line per interface with a default
        gateway, for the message).
    #>
    [CmdletBinding()]
    param([AllowEmptyCollection()][object[]]$Interfaces)
    if (-not $PSBoundParameters.ContainsKey('Interfaces')) { $Interfaces = Get-ArciNetworkFacts }
    $candidates = @(@($Interfaces) | Where-Object { $null -ne $_ -and -not $_.Loopback -and $_.Status -eq 'Up' -and @($_.Gateways).Count -gt 0 })
    $lines = New-Object System.Collections.Generic.List[string]
    $result = [pscustomobject]@{ Address = $null; Dhcp = $null; Interface = $null; Gateway = $null; Candidates = @() }
    foreach ($candidate in $candidates) {
        $ipv4 = @(@($candidate.Addresses) | Where-Object { $null -ne $_ -and $_.Family -eq 'InterNetwork' -and -not ([string]$_.Address).StartsWith('169.254.') })
        $shown = '-'
        if ($ipv4.Count -gt 0) { $shown = (@($ipv4 | ForEach-Object { $_.Address }) -join ', ') }
        $lines.Add(('{0}: {1} (ağ geçidi [gateway] {2})' -f $candidate.Name, $shown, (@($candidate.Gateways) -join ', ')))
        if ($candidates.Count -eq 1 -and $ipv4.Count -eq 1) {
            $result.Address = [string]$ipv4[0].Address
            $result.Dhcp = $ipv4[0].Dhcp
            $result.Interface = [string]$candidate.Name
            $result.Gateway = [string]@($candidate.Gateways)[0]
        }
    }
    $result.Candidates = $lines.ToArray()
    $result
}

function Get-ArciCollectorBind {
    <#
    .SYNOPSIS
        [collector] bind of arcilogger.toml given as text, or $null when the key is not there (the
        collector then listens on 0.0.0.0: arcilogger_core.config.CollectorConfig).
    .DESCRIPTION
        A narrow pattern for a plain string key, for the case where no Python is installed yet (a
        new installation over an existing configuration); with Python the installer reads the file
        with tomllib. A dotted key or an inline table is not seen here.
    #>
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Text)
    $section = [regex]::Match($Text, '(?ms)^[ \t]*\[collector\][ \t]*(?:#[^\r\n]*)?\r?$(?<body>.*?)(?=^[ \t]*\[|\z)')
    if (-not $section.Success) { return $null }
    $bind = [regex]::Match($section.Groups['body'].Value, '(?m)^[ \t]*bind[ \t]*=[ \t]*(?:"(?<v>[^"]*)"|''(?<v>[^'']*)'')')
    if (-not $bind.Success) { return $null }
    $bind.Groups['v'].Value
}

function Test-ArciBindAddress {
    <#
    .SYNOPSIS
        Whether the collector can listen on -Bind on this computer.
    .DESCRIPTION
        Kind is 'wildcard' (0.0.0.0 or ::: every address), 'loopback' (127.0.0.0/8 or ::1: this
        computer only, as in the CI end-to-end test), 'local' (one of -LocalAddress), 'foreign' (an
        address this computer does not have: the collector would stop at once with an OSError,
        be restarted in a loop and receive nothing) or 'invalid' (a host name or no address at
        all). Status is 'ok' for the first three and 'fail' otherwise.
    #>
    [CmdletBinding()]
    param([AllowNull()][AllowEmptyString()][string]$Bind, [AllowEmptyCollection()][string[]]$LocalAddress = @())
    $value = ([string]$Bind).Trim().TrimStart([char]'[').TrimEnd([char]']')
    $kind = 'invalid'
    $parsed = $null
    if ($value -eq '0.0.0.0' -or $value -eq '::') { $kind = 'wildcard' }
    elseif (-not $value.Contains('/') -and (Test-ArciAddressSpec $value) -and [System.Net.IPAddress]::TryParse($value, [ref]$parsed)) {
        if ([System.Net.IPAddress]::IsLoopback($parsed)) { $kind = 'loopback' }
        else {
            $kind = 'foreign'
            foreach ($local in @($LocalAddress)) {
                $candidate = $null
                if ($local -and [System.Net.IPAddress]::TryParse([string]$local, [ref]$candidate) -and $candidate.Equals($parsed)) {
                    $kind = 'local'
                    break
                }
            }
        }
    }
    $status = 'fail'
    if (@('wildcard', 'loopback', 'local') -contains $kind) { $status = 'ok' }
    [pscustomobject]@{ Bind = $value; Kind = $kind; Status = $status }
}

# --- program folders ------------------------------------------------------------------------------

$script:VersionFileName = 'VERSION.json'
$script:InstallMarkerName = 'INSTALL-IN-PROGRESS.json'

function Get-ArciInstallMarkerName {
    [CmdletBinding()]
    [OutputType([string])]
    param()
    $script:InstallMarkerName
}

function Get-ArciProgramFolderState {
    <#
    .SYNOPSIS
        What a program folder holds: 'installed' (VERSION.json written by this installer),
        'partial' (an installation that stopped half-way: INSTALL-IN-PROGRESS.json), 'empty',
        'missing' or 'foreign' (anything else - never deleted or overwritten).
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        if (Test-Path -LiteralPath $Path) { return 'foreign' }
        return 'missing'
    }
    foreach ($pair in @(@($script:VersionFileName, 'installed'), @($script:InstallMarkerName, 'partial'))) {
        $file = Join-Path $Path $pair[0]
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { continue }
        $document = $null
        try { $document = [System.IO.File]::ReadAllText($file, [System.Text.Encoding]::UTF8) | ConvertFrom-Json } catch { $document = $null }
        if ($null -ne $document -and $document.PSObject.Properties.Name -contains 'product' -and $document.product -eq 'ARCILOGGER') {
            return $pair[1]
        }
    }
    if (@(Get-ChildItem -LiteralPath $Path -Force).Count -eq 0) { return 'empty' }
    'foreign'
}

# --- licence (advisory checks without Python) -----------------------------------------------------
#
# The installer reads a licence file (-LicenseFile) and the status file the product writes
# (<data_dir>\state\license-status.json) without Python and without any trust decision: no
# signature is checked here. Its maintenance rule is advisory; the product re-verifies the stored
# licence at run time, where a version released after maintenance_until has its paid modules off
# (state version_not_covered) whatever the installer did.

$script:LicenseFileMaxBytes = 65536
# The licence id of the format arcilogger.license/1 (date of issue + 8 Crockford base32 characters).
$script:LicenseIdPattern = '^ARCI-L-[0-9]{8}-[0-9A-HJKMNP-TV-Z]{8}$'
$script:LicenseStatusSchema = 'arcilogger.license-status/1'
# Lower bounds before this day are ignored at run time (PRODUCT_EPOCH of arcilogger_core.license).
$script:ProductEpochDay = '2026-01-01'
# Test helpers that must never ship in the bundle's wheels (docs/adr/0013, phase 2 hooks).
$script:ArciTestOnlyFiles = @('arcilogger_core/license/testing.py', 'arcilogger_db/pytest_plugin.py')

function ConvertTo-ArciIsoDate {
    <# -Value when it is a string 'YYYY-MM-DD' (ASCII digits) naming a real calendar day, else $null. #>
    [CmdletBinding()]
    [OutputType([string])]
    param([AllowNull()]$Value)
    if ($Value -isnot [string] -or $Value -cnotmatch '^[0-9]{4}-[0-9]{2}-[0-9]{2}$') { return $null }
    $parsed = [datetime]::MinValue
    $styles = [System.Globalization.DateTimeStyles]::None
    if (-not [datetime]::TryParseExact($Value, 'yyyy-MM-dd', [System.Globalization.CultureInfo]::InvariantCulture, $styles, [ref]$parsed)) { return $null }
    $Value
}

function Get-ArciJsonField {
    <# The value of the property -Name of a ConvertFrom-Json object, $null when it is missing (StrictMode-safe). #>
    param([AllowNull()]$Object, [Parameter(Mandatory = $true)][string]$Name)
    if ($null -eq $Object -or $Object -isnot [System.Management.Automation.PSCustomObject]) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    $property.Value
}

function Read-ArciJsonObject {
    <#
    A JSON object read from -Path: at most -MaxBytes bytes, UTF-8 (a leading byte order mark is
    ignored, invalid UTF-8 refused). Throws with the reason (Turkish, English in brackets).
    #>
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][int]$MaxBytes)
    $item = Get-Item -LiteralPath $Path -Force
    $size = $item.Length
    if ($size -gt $MaxBytes) {
        throw ("dosya çok büyük: {0} bayt, en çok {1} [file too large: {0} bytes, at most {1}]" -f $size, $MaxBytes)
    }
    $bytes = [System.IO.File]::ReadAllBytes($item.FullName)
    $offset = 0
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) { $offset = 3 }
    $strict = New-Object System.Text.UTF8Encoding -ArgumentList $false, $true
    try { $text = $strict.GetString($bytes, $offset, $bytes.Length - $offset) }
    catch { throw 'UTF-8 değil [not UTF-8]' }
    if (-not $text.Trim()) { throw 'dosya boş [file empty]' }
    try { $document = $text | ConvertFrom-Json }
    catch { throw ("JSON değil [not JSON]: {0}" -f $_.Exception.Message) }
    if ($document -isnot [System.Management.Automation.PSCustomObject]) { throw 'JSON nesnesi değil [not a JSON object]' }
    $document
}

function Read-ArciLicenseFile {
    <#
    .SYNOPSIS
        The fields of a licence file (.lic) the installer's preflight needs: LicenseId,
        MaintenanceUntil ('YYYY-MM-DD'), ValidUntil ('YYYY-MM-DD' or $null), Size.
    .DESCRIPTION
        Read without Python and without trusting it: the signature is checked only by
        "arcilogger-db license install" and again by the product at run time. Throws with the
        reason when the file is missing, larger than 65,536 bytes, not UTF-8 JSON (a byte order
        mark is tolerated) or without payload.license_id, payload.maintenance_until (YYYY-MM-DD)
        and payload.valid_until (null or YYYY-MM-DD).
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw ("dosya yok [file not found]: {0}" -f $Path) }
    $document = Read-ArciJsonObject -Path $Path -MaxBytes $script:LicenseFileMaxBytes
    $payload = Get-ArciJsonField -Object $document -Name 'payload'
    if ($payload -isnot [System.Management.Automation.PSCustomObject]) { throw 'payload nesnesi yok [no payload object]' }
    $id = Get-ArciJsonField -Object $payload -Name 'license_id'
    if ($id -isnot [string] -or $id -cnotmatch $script:LicenseIdPattern) {
        throw 'payload.license_id yok ya da geçersiz [missing or invalid]'
    }
    $until = ConvertTo-ArciIsoDate (Get-ArciJsonField -Object $payload -Name 'maintenance_until')
    if (-not $until) { throw 'payload.maintenance_until YYYY-MM-DD biçiminde bir tarih değil [not a YYYY-MM-DD date]' }
    if ($null -eq $payload.PSObject.Properties['valid_until']) { throw 'payload.valid_until yok [missing]' }
    $valid = $null
    if ($null -ne $payload.valid_until) {
        $valid = ConvertTo-ArciIsoDate $payload.valid_until
        if (-not $valid) { throw 'payload.valid_until null ya da YYYY-MM-DD biçiminde bir tarih değil [neither null nor a YYYY-MM-DD date]' }
    }
    [pscustomobject]@{
        LicenseId        = $id
        MaintenanceUntil = $until
        ValidUntil       = $valid
        Size             = (Get-Item -LiteralPath $Path -Force).Length
    }
}

function Read-ArciLicenseStatus {
    <#
    .SYNOPSIS
        The licence status file the product writes (<data_dir>\state\license-status.json, schema
        arcilogger.license-status/1): Status, MaintenanceUntil, LicenseId, Licensee, Version,
        ReleaseDate. $null when the file does not exist; throws when it is damaged.
    .DESCRIPTION
        Never trusted by the product and editable by the customer: the installer's maintenance
        rule built on it is advisory. In the states licensed and version_not_covered
        maintenance_until must be a date.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $document = Read-ArciJsonObject -Path $Path -MaxBytes 1048576
    if ((Get-ArciJsonField -Object $document -Name 'schema') -cne $script:LicenseStatusSchema) {
        throw ("schema {0} değil [is not {0}]" -f $script:LicenseStatusSchema)
    }
    $status = Get-ArciJsonField -Object $document -Name 'status'
    if ($status -isnot [string] -or $status -cnotmatch '^[a-z][a-z_]{0,39}$') { throw 'status yok ya da geçersiz [missing or invalid]' }
    $until = $null
    $rawUntil = Get-ArciJsonField -Object $document -Name 'maintenance_until'
    if ($null -ne $rawUntil) {
        $until = ConvertTo-ArciIsoDate $rawUntil
        if (-not $until) { throw 'maintenance_until YYYY-MM-DD biçiminde bir tarih değil [not a YYYY-MM-DD date]' }
    }
    if (@('licensed', 'version_not_covered') -ccontains $status -and -not $until) {
        throw ("{0} durumunda maintenance_until yok [missing in state {0}]" -f $status)
    }
    $text = {
        param($Name)
        $value = Get-ArciJsonField -Object $document -Name $Name
        if ($value -is [string]) { $value } else { $null }
    }
    [pscustomobject]@{
        Status           = $status
        MaintenanceUntil = $until
        LicenseId        = (& $text 'license_id')
        Licensee         = (& $text 'licensee')
        Version          = (& $text 'version')
        ReleaseDate      = (ConvertTo-ArciIsoDate (Get-ArciJsonField -Object $document -Name 'release_date'))
    }
}

function Test-ArciMaintenanceCovers {
    <#
    .SYNOPSIS
        Whether a version released on -ReleaseDate is covered by maintenance until -MaintenanceUntil
        (inclusive; both 'YYYY-MM-DD'): [pscustomobject]@{ Covered; Text }. Text is the
        installer's message: the refusal (Turkish line, English line) when not covered.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ReleaseDate,
        [Parameter(Mandatory = $true)][string]$MaintenanceUntil,
        [string]$Version = '?'
    )
    $release = ConvertTo-ArciIsoDate $ReleaseDate
    $until = ConvertTo-ArciIsoDate $MaintenanceUntil
    if (-not $release) { throw ("Yayın tarihi YYYY-MM-DD değil [release date is not YYYY-MM-DD]: {0}" -f $ReleaseDate) }
    if (-not $until) { throw ("Bakım tarihi YYYY-MM-DD değil [maintenance date is not YYYY-MM-DD]: {0}" -f $MaintenanceUntil) }
    # Same format, so the ordinal comparison is the date comparison.
    $covered = [string]::CompareOrdinal($release, $until) -le 0
    if ($covered) {
        $text = 'Bakım süresi {0} tarihine kadar; bu sürüm ({1}, yayın tarihi {2}) kapsanıyor [maintenance until {0} covers this version ({1}, released {2})]' -f $until, $Version, $release
    }
    else {
        $text = @(
            ('Bu sürüm ({0}, yayın tarihi {1}) lisansınızın bakım süresinden ({2} tarihine kadar) sonra yayımlandı; hiçbir şey değiştirilmedi. Bakımı yenileyip yeni lisans dosyasını -LicenseFile ile verin ya da yalnızca 5651 Temel ile devam etmek için -AllowUncoveredUpgrade kullanın.' -f $Version, $release, $until),
            ('[This version ({0}, released {1}) was released after your licence''s maintenance period (until {2}); nothing was changed. Renew maintenance and pass the new licence file with -LicenseFile, or use -AllowUncoveredUpgrade to continue with 5651 Temel only.]' -f $Version, $release, $until)
        ) -join [Environment]::NewLine
    }
    [pscustomobject]@{ Covered = $covered; Text = $text }
}

function Get-ArciProductReleaseDate {
    <#
    .SYNOPSIS
        The release date of the product, RELEASE_DATE of core\src\arcilogger_core\__init__.py
        (-InitText, its content): the line RELEASE_DATE: Final = "YYYY-MM-DD". Throws when the
        line is missing or names no real day. Used by Build-ArciBundle.ps1 for the bundle manifest.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$InitText)
    $match = [regex]::Match($InitText, '(?m)^RELEASE_DATE: Final = "(?<d>\d{4}-\d{2}-\d{2})"')
    if (-not $match.Success) { throw 'RELEASE_DATE okunamadı [not found]: RELEASE_DATE: Final = "YYYY-MM-DD" satırı yok [line missing].' }
    $value = ConvertTo-ArciIsoDate $match.Groups['d'].Value
    if (-not $value) { throw ("RELEASE_DATE geçerli bir tarih değil [not a valid date]: {0}" -f $match.Groups['d'].Value) }
    $value
}

function Get-ArciSiteToday {
    <#
    Today at the site (Europe/Istanbul; on Windows "Turkey Standard Time") as 'YYYY-MM-DD'.
    -UtcNow for tests. Without either time zone: UTC+03:00 (Türkiye's offset all year since 2016).
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([datetime]$UtcNow = [DateTime]::UtcNow)
    (ConvertTo-ArciSiteTime -UtcNow $UtcNow).Local.ToString('yyyy-MM-dd', [System.Globalization.CultureInfo]::InvariantCulture)
}

function ConvertTo-ArciSiteTime([datetime]$UtcNow) {
    <# -UtcNow at the site: Local (the wall clock) and Offset (from UTC), as Get-ArciSiteToday explains. #>
    if ($UtcNow.Kind -eq [DateTimeKind]::Local) { $utc = $UtcNow.ToUniversalTime() }
    else { $utc = [DateTime]::SpecifyKind($UtcNow, [DateTimeKind]::Utc) }
    $zone = $null
    foreach ($id in @('Turkey Standard Time', 'Europe/Istanbul')) {
        try {
            $zone = [System.TimeZoneInfo]::FindSystemTimeZoneById($id)
            break
        }
        catch { $zone = $null }
    }
    if ($null -ne $zone) { return [pscustomobject]@{ Local = [System.TimeZoneInfo]::ConvertTimeFromUtc($utc, $zone); Offset = $zone.GetUtcOffset($utc) } }
    [pscustomobject]@{ Local = $utc.AddHours(3); Offset = [TimeSpan]::FromHours(3) }
}

function Get-ArciSiteTimestamp {
    <#
    Now at the site as ISO 8601 with its offset ('2026-10-25T10:00:00+03:00'), the form of the
    state files of ARCILOGGER (arcilogger_core.timeutil.isoformat_site). -UtcNow for tests.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([datetime]$UtcNow = [DateTime]::UtcNow)
    $site = ConvertTo-ArciSiteTime -UtcNow $UtcNow
    $sign = $(if ($site.Offset -lt [TimeSpan]::Zero) { '-' } else { '+' })
    '{0}{1}{2:hh\:mm}' -f $site.Local.ToString('yyyy-MM-ddTHH:mm:ss', [System.Globalization.CultureInfo]::InvariantCulture), $sign, $site.Offset.Duration()
}

function Get-ArciOldestVaultDay {
    <#
    .SYNOPSIS
        The oldest sealed day of the vault by its path name, as 'YYYY-MM-DD', or $null.
    .DESCRIPTION
        The files vault\YYYY\MM\DD\manifest-<day>.json (the sealer's own glob; the day is taken
        from the file name). No manifest or token is read. Days before 2026-01-01 are ignored, as
        the product ignores lower bounds before its epoch.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)][string]$VaultPath)
    if (-not (Test-Path -LiteralPath $VaultPath -PathType Container)) { return $null }
    $folders = {
        param([string]$Parent, [string]$Pattern)
        @(Get-ChildItem -LiteralPath $Parent -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -cmatch $Pattern })
    }
    $oldest = $null
    foreach ($year in (& $folders $VaultPath '^[0-9]{4}$')) {
        foreach ($month in (& $folders $year.FullName '^[0-9]{2}$')) {
            foreach ($day in (& $folders $month.FullName '^[0-9]{2}$')) {
                foreach ($file in @(Get-ChildItem -LiteralPath $day.FullName -File -Force -Filter 'manifest-*.json' -ErrorAction SilentlyContinue)) {
                    $match = [regex]::Match($file.Name, '^manifest-(?<d>[0-9]{4}-[0-9]{2}-[0-9]{2})\.json$')
                    if (-not $match.Success) { continue }
                    $value = ConvertTo-ArciIsoDate $match.Groups['d'].Value
                    if (-not $value -or [string]::CompareOrdinal($value, $script:ProductEpochDay) -lt 0) { continue }
                    if ($null -eq $oldest -or [string]::CompareOrdinal($value, $oldest) -lt 0) { $oldest = $value }
                }
            }
        }
    }
    $oldest
}

function Get-ArciDaysBetween {
    <# Whole days from -From to -To (both 'YYYY-MM-DD'; negative when -To is earlier). #>
    [CmdletBinding()]
    [OutputType([int])]
    param([Parameter(Mandatory = $true)][string]$From, [Parameter(Mandatory = $true)][string]$To)
    $culture = [System.Globalization.CultureInfo]::InvariantCulture
    $start = [datetime]::ParseExact($From, 'yyyy-MM-dd', $culture)
    $end = [datetime]::ParseExact($To, 'yyyy-MM-dd', $culture)
    [int]($end - $start).TotalDays
}

function Get-ArciWheelTestHelpers {
    <#
    .SYNOPSIS
        Why the ARCILOGGER wheels in -WheelDirectory must not be shipped, one string per problem
        (none: an empty list): the arcilogger_core or arcilogger_db wheel is missing, or a wheel
        contains a test helper (arcilogger_core/license/testing.py, arcilogger_db/pytest_plugin.py).
    .DESCRIPTION
        The wheels' own exclude rules keep them out (core/pyproject.toml, db/pyproject.toml);
        Build-ArciBundle.ps1 checks the built files, so a changed build rule cannot ship them.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$WheelDirectory)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $problems = New-Object System.Collections.Generic.List[string]
    foreach ($pattern in @('arcilogger_core-*.whl', 'arcilogger_db-*.whl')) {
        if (@(Get-ChildItem -LiteralPath $WheelDirectory -Filter $pattern -File -ErrorAction SilentlyContinue).Count -eq 0) {
            $problems.Add(("wheel yok [missing]: {0}" -f $pattern))
        }
    }
    foreach ($wheel in @(Get-ChildItem -LiteralPath $WheelDirectory -Filter '*.whl' -File -ErrorAction SilentlyContinue)) {
        $zip = [System.IO.Compression.ZipFile]::OpenRead($wheel.FullName)
        try {
            foreach ($entry in $zip.Entries) {
                $name = $entry.FullName.Replace('\', '/')
                if ($script:ArciTestOnlyFiles -contains $name) { $problems.Add(("{0}: {1}" -f $wheel.Name, $name)) }
            }
        }
        finally { $zip.Dispose() }
    }
    , $problems.ToArray()
}

# --- NTFS rights ----------------------------------------------------------------------------------

function New-ArciAce {
    param(
        [Parameter(Mandatory = $true)][string]$Sid,
        [Parameter(Mandatory = $true)][string]$Rights,
        [string]$Who,
        [switch]$ThisFolderOnly,
        # Inherited by sub-folders only, never by files (for a list right: on a file the same bit
        # is ReadData, so a list-only account must not pass it on to files).
        [switch]$FoldersOnly,
        # Inherited by files only, never by sub-folders (CREATOR OWNER Delete: a file's creator
        # may delete it, never a folder).
        [switch]$FilesOnly,
        # Not for the folder itself, only for what is created below it.
        [switch]$InheritOnly,
        # Only one level down: the files directly in the folder, not those of its sub-folders.
        [switch]$OneLevel
    )
    $inherit = -not $ThisFolderOnly
    [pscustomobject]@{
        Sid = $Sid; Who = $Who; Rights = $Rights; Inherit = $inherit
        FoldersOnly = [bool]($FoldersOnly -and $inherit)
        FilesOnly = [bool]($FilesOnly -and -not $FoldersOnly -and $inherit)
        InheritOnly = [bool]($InheritOnly -and $inherit)
        OneLevel = [bool]($OneLevel -and $inherit)
    }
}

function Get-ArciAclPlan {
    <#
    .SYNOPSIS
        Who may do what in the ARCILOGGER folders (least privilege, docs/adr/0007, docs/adr/0014).
    .DESCRIPTION
        Returns one entry per path: Path, Kind (Directory/File), Protect (inheritance from the
        parent disabled), Aces and Audit. Pure data, no system calls: the dry run prints it and the
        tests check it on any platform. Parents come before their children (Set-ArciVaultAcl.ps1
        creates a missing folder in this order). "Read" of a folder is ReadAndExecute (list and
        traverse), of a file Read. The entries go down the pipeline one by one, so
        "Get-ArciAclPlan | Where-Object { $_.Path -eq ... }" selects entries: written as one array,
        $_ would be the whole plan, $_.Path every path, and Set-ArciAclEntry would get them all.

        - Collector (the only network-facing Python service): reads and writes the spool but has no
          Delete right there, so it cannot delete or rename raw evidence (it can write into a
          file it opens; the nightly seal is what proves the content). It has no access to the
          audit spool, to the sealing/replica/verify/clock state, to the vault or to secret.key.
        - Scheduled tasks (LOCAL SERVICE, see deploy/windows/tasks): seal (read + delete closed
          spool days, write and lock the vault), retention (delete expired vault days), replica,
          verify and clock check; only they write state\seal, state\replica, state\verify and
          state\clock (core seal.py trusts state\seal\<day>.json when it decides a day is sealed).
          The database backup (Backup task, ADR-0009) writes and prunes backups\scheduled, which
          no service can read; backups and backups\db stay administrators-only. The tasks take
          their signed requests from state\requests (ADR-0014) and read admin\keys\keys.json,
          admin\policy.json and admin\system\jars.json.
          LOCAL SERVICE is a shared Windows account: other LocalService services hold these
          rights too (known limitation, owner decision: docs/adr/0007 section 8).
        - API: appends to the audit spool (spool\_audit, no delete) and writes exports; reads
          the spool (raw lines of days not sealed yet: GET /raw, the 5651 report, the sealing
          calendar; read only - no write, no delete), the vault and the state. For the browser
          administration it writes signed requests into admin\inbox (create and write, no
          delete except its own files) and reads the agent's status and journal; it never gets
          a write right on the configuration, secret.key, secrets.json or zamane\.
        - ArciAdmin (the administration agent, ADR-0014; no listener, no administrator rights):
          the only writer of arcilogger.toml and secrets.json (explicit file entries, which
          ReplaceFileW keeps), with CreateFiles in the configuration folder for its temporary
          files and CREATOR OWNER Delete on the files of that folder only (it can delete what it
          created, never secret.key or another file an administrator placed). In kamusm-roots
          and smtp-ca it may only add files. It stages uploaded Zamane clients in zamane\web,
          which no task can read (a staged client never runs); zamane\approved is written by
          SYSTEM (ApplySystem) and administrators only. It writes its own admin\ folders,
          state\requests (task requests) and its logs, and reads the state of the services.
          No access to the spool, the vault, the exports, the backups or the evidence-job state.
          "arcilogger-core secret set" replaces secrets.json with a new file that has only the
          inherited entries: whoever stores a secret on the console applies these entries again
          (the installer does it after its own, Set-ArciVaultAcl.ps1 at any time).
        - ApplySystem (SYSTEM) and administrators: full control everywhere (the owners entry of
          every protected path); admin\policy.json and data-root.txt are written by
          administrators only.
        - Ingest: reads the spool and the vault (catching up on days sealed while the database
          was down, ADR-0002), writes its own state.
        - Worker: reads the vault and the job state; may only list the spool and its source
          folders (inherited by folders, not by files), so it finds the oldest spool day by file
          name for the licence decision like the API (licensing contract 3.4) without reading
          raw logs. It reads the agent's status, journal and the system journal (audit import).
        - Web (Caddy): only its own data folder (internal CA private key) and the logs.
        - install\ (the installer's own state, firewall-state.json which SYSTEM reads) is
          protected: administrators, SYSTEM and ArciAdmin read only.
        - The PostgreSQL data folder is never touched (its installer sets its own rights).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$DataRoot,
        [Parameter(Mandatory = $true)][string]$ProgramDataDir,
        [Parameter(Mandatory = $true)][string]$InstallDir
    )
    $system = $script:WellKnownSid.System
    $admins = $script:WellKnownSid.Administrators
    $localService = $script:WellKnownSid.LocalService
    $creatorOwner = $script:WellKnownSid.CreatorOwner
    $collector = Get-ArciServiceSid 'ArciCollector'
    $ingest = Get-ArciServiceSid 'ArciIngest'
    $api = Get-ArciServiceSid 'ArciApi'
    $worker = Get-ArciServiceSid 'ArciWorker'
    $web = Get-ArciServiceSid 'ArciWeb'
    $agent = Get-ArciServiceSid 'ArciAdmin'
    $read = 'ReadAndExecute'
    $full = 'FullControl'
    $modify = 'Modify'
    $appendOnly = 'ReadAndExecute, Write'
    $listOnly = 'ListDirectory, ReadAttributes, Traverse'
    # The API's right on the inbox: create a request file and write it, read it back, list the
    # folder (quotas); deleting is left to CREATOR OWNER (its own temporary files).
    $inboxWriter = 'CreateFiles, WriteData, ReadData, ReadAttributes, ListDirectory'
    # The agent's right on state\requests: read, create a task request and write it.
    $requestWriter = 'ReadAndExecute, CreateFiles, WriteData'

    function New-Entry([string]$Path, [string]$Kind, [bool]$Protect, [object[]]$Aces, [bool]$Audit = $false) {
        [pscustomobject]@{ Path = $Path; Kind = $Kind; Protect = $Protect; Aces = @($Aces); Audit = $Audit }
    }
    $owners = @(
        (New-ArciAce -Sid $system -Who 'SYSTEM' -Rights $full),
        (New-ArciAce -Sid $admins -Who 'Administrators' -Rights $full)
    )
    $p = [System.IO.Path]::DirectorySeparatorChar
    $adminDir = $ProgramDataDir + $p + 'admin'
    $keysDir = $adminDir + $p + 'keys'
    # Add-only (kamusm-roots, smtp-ca): create a file, delete only one created by the agent.
    $addOnly = @(
        (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights 'CreateFiles' -ThisFolderOnly),
        (New-ArciAce -Sid $creatorOwner -Who 'CREATOR OWNER (files ArciAdmin added)' -Rights 'Delete' -FilesOnly -InheritOnly)
    )
    $entries = @(
        # Program files: read for every ARCILOGGER account (inherits the Program Files defaults too).
        (New-Entry $InstallDir 'Directory' $false @(
                (New-ArciAce -Sid $collector -Who 'NT SERVICE\ArciCollector' -Rights $read),
                (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights $read),
                (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read),
                (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $read),
                (New-ArciAce -Sid $web -Who 'NT SERVICE\ArciWeb' -Rights $read),
                (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $read),
                (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $read))),
        # Configuration, Kamu SM roots, Zamane jar: read-only for the services and tasks. ArciAdmin
        # may create files here (the temporary files of an apply, this folder only) and delete
        # the files it created (CREATOR OWNER, files of this folder only, never a sub-folder).
        (New-Entry $ProgramDataDir 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $collector -Who 'NT SERVICE\ArciCollector' -Rights $read),
                    (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights $read),
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read),
                    (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $read),
                    (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $read),
                    (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights 'CreateFiles' -ThisFolderOnly),
                    (New-ArciAce -Sid $creatorOwner -Who 'CREATOR OWNER (files ArciAdmin created here)' -Rights 'Delete' -FilesOnly -InheritOnly -OneLevel),
                    (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $read)))),
        # The two files the browser administration changes: an explicit entry for ArciAdmin on top
        # of the inherited read rights (ReplaceFileW keeps it, contract 2.2).
        (New-Entry ($ProgramDataDir + $p + 'arcilogger.toml') 'File' $false @(
                (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $modify))),
        (New-Entry ($ProgramDataDir + $p + 'secrets.json') 'File' $false @(
                (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $modify))),
        # The data root every service and task checks data_dir against (Day 0 notes D0-2, WP2-1):
        # protected, written by the installer only; ArciAdmin may create files in this folder,
        # so the installer re-creates the record (owner: Administrators) on every run.
        (New-Entry ($ProgramDataDir + $p + 'data-root.txt') 'File' $true ($owners + @(
                    (New-ArciAce -Sid $collector -Who 'NT SERVICE\ArciCollector' -Rights 'Read'),
                    (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights 'Read'),
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights 'Read'),
                    (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights 'Read'),
                    (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights 'Read'),
                    (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights 'Read')))),
        # The encryption key of the secret store: not for the collector (it never needs a secret).
        # Nobody but administrators may write or delete it.
        (New-Entry ($ProgramDataDir + $p + 'secret.key') 'File' $true ($owners + @(
                    (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights 'Read'),
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights 'Read'),
                    (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights 'Read'),
                    (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights 'Read'),
                    (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights 'Read')))),
        # Kamu SM roots and SMTP CA files: ArciAdmin adds a file (read inherited), it never changes
        # or deletes one an administrator placed.
        (New-Entry ($ProgramDataDir + $p + 'kamusm-roots') 'Directory' $false $addOnly),
        (New-Entry ($ProgramDataDir + $p + 'smtp-ca') 'Directory' $false $addOnly),
        # Uploaded Zamane clients wait here for their approval: no task may read them (a staged
        # client never runs); ApplySystem (SYSTEM) only hashes and copies them.
        (New-Entry ($ProgramDataDir + $p + 'zamane' + $p + 'web') 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $modify),
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read)))),
        # Approved clients: written by SYSTEM (ApplySystem promotes a known release, approve-jar)
        # and administrators only; run by the tasks and the API's report stamps.
        (New-Entry ($ProgramDataDir + $p + 'zamane' + $p + 'approved') 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $read),
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read),
                    (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $read)))),
        # Caddy's data folder holds the private key of the internal CA: Caddy and administrators only.
        (New-Entry ($ProgramDataDir + $p + 'caddy') 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $web -Who 'NT SERVICE\ArciWeb' -Rights $modify)))),
        # The installer's own state (firewall-state.json, which ApplySystem reads as SYSTEM).
        (New-Entry ($ProgramDataDir + $p + 'install') 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $read)))),
        # The browser administration (ADR-0014): protected; the readers may list the folder itself,
        # every sub-folder below grants its own rights.
        (New-Entry $adminDir 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $listOnly -ThisFolderOnly),
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $listOnly -ThisFolderOnly),
                    (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $listOnly -ThisFolderOnly),
                    (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $listOnly -ThisFolderOnly)))),
        # The guard rails: read by the agent, the API, the worker (whether to expect the agent's
        # heartbeat), the tasks and SYSTEM; written by the installer (administrators) only.
        (New-Entry ($adminDir + $p + 'policy.json') 'File' $false @(
                (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights 'Read'),
                (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights 'Read'),
                (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights 'Read'),
                (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights 'Read'))),
        # The channel's keys: protected before "keys init" creates them (Install-Arcilogger.ps1);
        # each private key is readable by its one service, the public keys by every reader.
        (New-Entry $keysDir 'Directory' $true $owners),
        (New-Entry ($keysDir + $p + 'agent-ed25519.key') 'File' $true ($owners + @(
                    (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights 'Read')))),
        (New-Entry ($keysDir + $p + 'api-ed25519.key') 'File' $true ($owners + @(
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights 'Read')))),
        (New-Entry ($keysDir + $p + 'keys.json') 'File' $true ($owners + @(
                    (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights 'Read'),
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights 'Read'),
                    (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights 'Read')))),
        # The inbox (and inbox\uploads below it): the API creates and writes requests and uploads,
        # the agent claims them.
        (New-Entry ($adminDir + $p + 'inbox') 'Directory' $false @(
                (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $modify),
                (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $inboxWriter),
                (New-ArciAce -Sid $creatorOwner -Who 'CREATOR OWNER (the API''s own files)' -Rights 'Delete' -InheritOnly))),
        # The agent's own folders (SYSTEM reads the versions through its full right).
        (New-Entry ($adminDir + $p + 'work') 'Directory' $false @(
                (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $modify))),
        (New-Entry ($adminDir + $p + 'pending') 'Directory' $false @(
                (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $modify))),
        (New-Entry ($adminDir + $p + 'state') 'Directory' $false @(
                (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $modify))),
        (New-Entry ($adminDir + $p + 'versions') 'Directory' $false @(
                (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $modify))),
        # Heartbeat, request status, alerts and the journal: the API shows them, the worker imports them.
        (New-Entry ($adminDir + $p + 'status') 'Directory' $false @(
                (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $modify),
                (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read),
                (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $read))),
        (New-Entry ($adminDir + $p + 'journal') 'Directory' $false @(
                (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $modify),
                (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read),
                (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $read))),
        # ApplySystem's state (SYSTEM writes it through its full right): the tasks read jars.json.
        (New-Entry ($adminDir + $p + 'system') 'Directory' $false @(
                (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $read),
                (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read),
                (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $read),
                (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $read))),
        # Data root: list only (this folder), everything else is granted per sub-folder.
        (New-Entry $DataRoot 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $collector -Who 'NT SERVICE\ArciCollector' -Rights $read -ThisFolderOnly),
                    (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights $read -ThisFolderOnly),
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read -ThisFolderOnly),
                    (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $read -ThisFolderOnly),
                    (New-ArciAce -Sid $web -Who 'NT SERVICE\ArciWeb' -Rights $read -ThisFolderOnly),
                    (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $read -ThisFolderOnly),
                    (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $read -ThisFolderOnly)))),
        # Raw spool. The API reads the days that are not sealed yet (raw line view, the 5651
        # report and the sealing calendar; MVP-CONTRACTS 6.1): read only, inherited by the source
        # folders and day files the collector creates. spool\_audit below is protected, so this
        # entry does not reach it. The worker evaluates the licence like the API and needs the
        # oldest spool day by its file name (licensing contract 3.4, the trial's spool lower
        # bound): it may list the spool and the source folders, never read a file.
        (New-Entry ($DataRoot + $p + 'spool') 'Directory' $false @(
                (New-ArciAce -Sid $collector -Who 'NT SERVICE\ArciCollector' -Rights $appendOnly),
                (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights $read),
                (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read),
                (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $listOnly -FoldersOnly),
                (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $modify))),
        # The ARCILOGGER audit log of the day (sealed at night like the raw logs): protected, so the
        # collector's rights on the spool do not reach it. The API appends (no delete); sealing
        # reads and then removes the day; ingest may list it while it walks the spool.
        (New-Entry ($DataRoot + $p + 'spool' + $p + '_audit') 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $appendOnly),
                    (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $modify),
                    (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights $read)))),
        (New-Entry ($DataRoot + $p + 'vault') 'Directory' $false @(
                (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $modify),
                (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read),
                (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $read),
                (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights $read)) $true),
        # Service status and alerts: every Python service keeps its own files here (ingest
        # offsets live in the database). ArciAdmin reads them (which configuration each service
        # loaded, the collector's acknowledgement for the firewall); it reaches none of the
        # protected folders below.
        (New-Entry ($DataRoot + $p + 'state') 'Directory' $false @(
                (New-ArciAce -Sid $collector -Who 'NT SERVICE\ArciCollector' -Rights $modify),
                (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights $modify),
                (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $modify),
                (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read),
                (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $read),
                (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $modify))),
        # Evidence-job state (protected): written only by the scheduled tasks. A service that could
        # write state\seal\<day>.json with status "sealed" could stop that day from ever being sealed.
        (New-Entry ($DataRoot + $p + 'state' + $p + 'seal') 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $modify),
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read),
                    (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $read),
                    (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights $read)))),
        (New-Entry ($DataRoot + $p + 'state' + $p + 'replica') 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $modify),
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read),
                    (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $read)))),
        (New-Entry ($DataRoot + $p + 'state' + $p + 'verify') 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $modify),
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read),
                    (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $read)))),
        (New-Entry ($DataRoot + $p + 'state' + $p + 'clock') 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $modify),
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read),
                    (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $read)))),
        # Signed task requests (seal now, credit query, TSA and replica test; ADR-0014): ArciAdmin
        # writes them and deletes only its own, the tasks take, answer and delete them. No service
        # but the agent can put a request here; the signature is the authority.
        (New-Entry ($DataRoot + $p + 'state' + $p + 'requests') 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $requestWriter),
                    (New-ArciAce -Sid $creatorOwner -Who 'CREATOR OWNER (ArciAdmin''s own requests)' -Rights 'Delete' -InheritOnly),
                    (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $modify)))),
        (New-Entry ($DataRoot + $p + 'exports') 'Directory' $false @(
                (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $modify),
                (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $modify))),
        (New-Entry ($DataRoot + $p + 'logs') 'Directory' $false @(
                (New-ArciAce -Sid $collector -Who 'NT SERVICE\ArciCollector' -Rights $modify),
                (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights $modify),
                (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $modify),
                (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $modify),
                (New-ArciAce -Sid $web -Who 'NT SERVICE\ArciWeb' -Rights $modify),
                (New-ArciAce -Sid $agent -Who 'NT SERVICE\ArciAdmin' -Rights $modify),
                (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $modify))),
        # Database dumps: administrators only (inherited from the data root), so backups\db - the
        # installer's dumps before an upgrade - is too.
        (New-Entry ($DataRoot + $p + 'backups') 'Directory' $false @()),
        # Scheduled dumps (Backup task, ADR-0009): protected; only the task writes and prunes them.
        # No service may read them: they hold password hashes, TOTP secrets encrypted with
        # secret.key and personal data.
        (New-Entry ($DataRoot + $p + 'backups' + $p + 'scheduled') 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $modify))))
    )
    $entries
}

function Get-ArciAceFlags {
    <#
    .SYNOPSIS
        The Windows inheritance and propagation flags of a planned entry (New-ArciAce) on a path
        of -Kind, as text ('ContainerInherit, ObjectInherit', 'None', ...). Pure; Set-ArciAclEntry
        applies them.
    .DESCRIPTION
        On a file nothing is inherited. On a folder: FoldersOnly is ContainerInherit, FilesOnly
        ObjectInherit, else both; InheritOnly leaves the folder itself out; OneLevel
        (NoPropagateInherit) stops an entry at the folder's own children: an entry inherited by
        files only would otherwise reach the files of every sub-folder too.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Kind, [Parameter(Mandatory = $true)]$Ace)
    if ($Kind -ne 'Directory' -or -not $Ace.Inherit) { return [pscustomobject]@{ Inheritance = 'None'; Propagation = 'None' } }
    $inheritance = 'ContainerInherit, ObjectInherit'
    if ($Ace.FoldersOnly) { $inheritance = 'ContainerInherit' }
    elseif ($Ace.PSObject.Properties.Name -contains 'FilesOnly' -and $Ace.FilesOnly) { $inheritance = 'ObjectInherit' }
    $propagation = @()
    if ($Ace.PSObject.Properties.Name -contains 'InheritOnly' -and $Ace.InheritOnly) { $propagation += 'InheritOnly' }
    if ($Ace.PSObject.Properties.Name -contains 'OneLevel' -and $Ace.OneLevel) { $propagation += 'NoPropagateInherit' }
    [pscustomobject]@{ Inheritance = $inheritance; Propagation = $(if ($propagation.Count -gt 0) { $propagation -join ', ' } else { 'None' }) }
}

function Get-ArciAccessControl {
    param([Parameter(Mandatory = $true)][System.IO.FileSystemInfo]$Item, [string]$Sections = 'Access')
    if ($Item.PSObject.Methods.Name -contains 'GetAccessControl') { return $Item.GetAccessControl($Sections) }
    if ($Item -is [System.IO.DirectoryInfo]) {
        return [System.IO.FileSystemAclExtensions]::GetAccessControl([System.IO.DirectoryInfo]$Item, [System.Security.AccessControl.AccessControlSections]$Sections)
    }
    [System.IO.FileSystemAclExtensions]::GetAccessControl([System.IO.FileInfo]$Item, [System.Security.AccessControl.AccessControlSections]$Sections)
}

function Set-ArciAccessControl {
    param([Parameter(Mandatory = $true)][System.IO.FileSystemInfo]$Item, [Parameter(Mandatory = $true)]$Security)
    # Only the changed sections are written (never the owner), which avoids Set-Acl's
    # "not allowed to be the owner" failure on folders created by another account.
    if ($Item.PSObject.Methods.Name -contains 'SetAccessControl') { $Item.SetAccessControl($Security); return }
    if ($Item -is [System.IO.DirectoryInfo]) {
        [System.IO.FileSystemAclExtensions]::SetAccessControl([System.IO.DirectoryInfo]$Item, [System.Security.AccessControl.DirectorySecurity]$Security)
        return
    }
    [System.IO.FileSystemAclExtensions]::SetAccessControl([System.IO.FileInfo]$Item, [System.Security.AccessControl.FileSecurity]$Security)
}

function Set-ArciAclEntry {
    <#
    .SYNOPSIS
        Make the explicit ACL of one path exactly the planned one. Explicit entries on children
        (for example the deny entries that arcilogger_core.immutability puts on sealed files) are
        not touched; inherited entries are recomputed by Windows.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Entry)
    $item = Get-Item -LiteralPath $Entry.Path -Force
    $security = Get-ArciAccessControl -Item $item
    $security.SetAccessRuleProtection([bool]$Entry.Protect, $false)
    $explicit = $security.GetAccessRules($true, $false, [System.Security.Principal.SecurityIdentifier])
    foreach ($rule in @($explicit)) { [void]$security.RemoveAccessRuleSpecific($rule) }
    foreach ($ace in $Entry.Aces) {
        $sid = New-Object System.Security.Principal.SecurityIdentifier -ArgumentList $ace.Sid
        $rights = [System.Security.AccessControl.FileSystemRights]$ace.Rights
        $flags = Get-ArciAceFlags -Kind $Entry.Kind -Ace $ace
        $inherit = [System.Security.AccessControl.InheritanceFlags]$flags.Inheritance
        $propagation = [System.Security.AccessControl.PropagationFlags]$flags.Propagation
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule -ArgumentList $sid, $rights, $inherit, $propagation, ([System.Security.AccessControl.AccessControlType]::Allow)
        $security.AddAccessRule($rule)
    }
    Set-ArciAccessControl -Item $item -Security $security
}

function Set-ArciVaultAudit {
    <#
    .SYNOPSIS
        Audit (SACL) every successful and failed write, delete, permission or owner change in the
        vault for Everyone (ADR-0003); the events go to the Windows Security log once the
        "File System" audit subcategory is enabled.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$VaultPath)
    $item = Get-Item -LiteralPath $VaultPath -Force
    $security = Get-ArciAccessControl -Item $item -Sections 'Audit'
    $everyone = New-Object System.Security.Principal.SecurityIdentifier -ArgumentList $script:WellKnownSid.Everyone
    $rights = [System.Security.AccessControl.FileSystemRights]'Write, Delete, DeleteSubdirectoriesAndFiles, ChangePermissions, TakeOwnership'
    $inherit = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    $flags = [System.Security.AccessControl.AuditFlags]'Success, Failure'
    $rule = New-Object System.Security.AccessControl.FileSystemAuditRule -ArgumentList $everyone, $rights, $inherit, ([System.Security.AccessControl.PropagationFlags]::None), $flags
    $security.SetAuditRule($rule)
    Set-ArciAccessControl -Item $item -Security $security
}

# --- browser administration (ADR-0014) -----------------------------------------------------------
#
# The installer prepares what the administration agent ArciAdmin and the SYSTEM task
# \ARCILOGGER\ApplySystem trust: admin\policy.json (the guard rails, written by administrators
# only), the record of the programs the tasks may run (policy.trusted_executables and
# zamane\zamane-jar.sha256, arcilogger_core.execpolicy) and the Application log source. Nothing
# here depends on the licence, and nothing of the 5651 path depends on the browser administration:
# -DisableBrowserAdministration switches the agent off, never sealing.

$script:AdminPolicySchema = 'arcilogger.admin-policy/1'
$script:DefaultSourceNetworks = @('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
$script:DefaultExecutableRoots = @('C:\Program Files', 'C:\Program Files (x86)')
# Who may write a program the installer records as trusted: SYSTEM, Administrators,
# TrustedInstaller. CREATOR OWNER and OWNER RIGHTS only stand for the owner, which is checked
# itself.
$script:TrustedWriterSids = @('S-1-5-18', 'S-1-5-32-544', 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464')
$script:OwnerPlaceholderSids = @('S-1-3-0', 'S-1-3-4')
# FileSystemRights that let someone change a recorded program (Get-ArciUnsafeWriters), by
# where the entry is. On the file: WriteData, AppendData, WriteExtendedAttributes,
# WriteAttributes, Delete, ChangePermissions, TakeOwnership, GENERIC_ALL, GENERIC_WRITE. On its
# folder (a file or a library planted next to it): CreateFiles, CreateDirectories,
# DeleteSubdirectoriesAndFiles, Delete, ChangePermissions, TakeOwnership, GENERIC_ALL,
# GENERIC_WRITE. On a folder above it (renamed away and created anew): Delete,
# DeleteSubdirectoriesAndFiles, ChangePermissions, TakeOwnership, GENERIC_ALL; creating a
# sub-folder there (the AppendData of C:\ for Authenticated Users) changes nothing on the path.
$script:FileWriteMask = [int64](0x2 -bor 0x4 -bor 0x10 -bor 0x100 -bor 0x10000 -bor 0x40000 -bor 0x80000 -bor 0x10000000 -bor 0x40000000)
$script:FolderWriteMask = [int64](0x2 -bor 0x4 -bor 0x40 -bor 0x10000 -bor 0x40000 -bor 0x80000 -bor 0x10000000 -bor 0x40000000)
$script:AncestorWriteMask = [int64](0x40 -bor 0x10000 -bor 0x40000 -bor 0x80000 -bor 0x10000000)
# What the system plan may name (arcilogger_core.admin.contract: OutboundRule, HostName).
$script:OutboundRulePattern = '^ARCILOGGER Out (NTP|Kamu SM|SMTP( [2-9])?|Replica)$'
$script:HostNamePattern = '^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$'

function Get-ArciDefaultSourceNetworks {
    <# The default -SourceNetwork of the installer (RFC 1918), as policy.json states it. #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param()
    , $script:DefaultSourceNetworks
}

function ConvertTo-ArciSourceNetwork {
    <#
    .SYNOPSIS
        -Value as a source network of policy.json: Network (canonical CIDR text such as
        "172.16.0.0/12"; an address alone is its /32) and Problem ("Türkçe [English]", $null
        when it is valid).
    .DESCRIPTION
        An IPv4 network without host bits (Python's ipaddress.IPv4Network(strict=True), which
        arcilogger_core.admin.contract.AdminPolicy applies): "172.16.0.1/12" is refused, not
        silently widened to the network it lies in.
    #>
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Value)
    $network = ConvertTo-ArciNetwork -Value $Value
    if ($null -eq $network -or $network.Bytes.Length -ne 4) {
        return [pscustomobject]@{ Network = $null; Problem = 'IPv4 ağı değil, örnek 172.16.0.0/12 [not an IPv4 network, e.g. 172.16.0.0/12]' }
    }
    for ($i = 0; $i -lt 4; $i++) {
        $bits = [Math]::Max(0, [Math]::Min(8, $network.Prefix - 8 * $i))
        $mask = (0xFF -shl (8 - $bits)) -band 0xFF
        if (([int]$network.Bytes[$i] -band (-bnot $mask) -band 0xFF) -ne 0) {
            return [pscustomobject]@{ Network = $null; Problem = ('ağ adresi değil, ağın içinde bir adres [a host address, not the network address]: /{0}' -f $network.Prefix) }
        }
    }
    [pscustomobject]@{ Network = ('{0}/{1}' -f $network.Address, $network.Prefix); Problem = $null }
}

function New-ArciAdminPolicy {
    <#
    .SYNOPSIS
        The text of admin\policy.json (contract 2.4.1): the guard rails of the browser
        administration. Pure; Install-Arcilogger.ps1 writes it (administrators only).
    .DESCRIPTION
        arcilogger_core.admin.contract.AdminPolicy reads it strictly: no unknown key, integers
        only, every range checked (min_prefix_v4 8-32, max_sources 1-5000, hold_hours 0-168,
        jar_hold_hours 1-168, tsa_test_per_day 0-20). A missing or invalid file switches the
        browser administration off (fail closed), never a 5651 function. -Enabled:$false is
        -DisableBrowserAdministration: the agent refuses every request and the web interface
        names the console path instead.
        -TrustedExecutable: the programs the installer recorded outside the default trusted
        locations (path, sha256 and, for a tsa.java with more than one element, argv).
        -WrittenAt: an ISO 8601 time with offset (default: now at the site).
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [bool]$Enabled = $true,
        [string[]]$SourceNetwork = $script:DefaultSourceNetworks,
        [ValidateRange(8, 32)][int]$MinPrefixV4 = 16,
        [ValidateRange(1, 5000)][int]$MaxSources = 500,
        [ValidateRange(0, 168)][int]$HoldHours = 0,
        [ValidateRange(1, 168)][int]$JarHoldHours = 24,
        [string[]]$ExecutableRoot = $script:DefaultExecutableRoots,
        [AllowEmptyCollection()][object[]]$TrustedExecutable = @(),
        [ValidateRange(0, 20)][int]$TsaTestPerDay = 3,
        [string]$WrittenAt,
        [string]$WrittenBy = 'installer'
    )
    if (-not $WrittenAt) { $WrittenAt = Get-ArciSiteTimestamp }
    $networks = New-Object System.Collections.Generic.List[string]
    foreach ($value in @($SourceNetwork)) {
        $checked = ConvertTo-ArciSourceNetwork -Value $value
        if ($checked.Problem) { throw ("-SourceNetwork '{0}': {1}" -f $value, $checked.Problem) }
        if (-not $networks.Contains($checked.Network)) { $networks.Add($checked.Network) }
    }
    $trusted = New-Object System.Collections.Generic.List[object]
    foreach ($entry in @($TrustedExecutable)) {
        if ($null -eq $entry) { continue }
        $sha256 = ([string]$entry.sha256).ToLowerInvariant()
        if ($sha256 -notmatch '^[0-9a-f]{64}$') { throw ("SHA-256 geçersiz [invalid]: {0}" -f $entry.path) }
        $item = [ordered]@{ path = [string]$entry.path; sha256 = $sha256 }
        $argv = @()
        if ($entry.PSObject.Properties.Name -contains 'argv') { $argv = @($entry.argv | Where-Object { $null -ne $_ } | ForEach-Object { [string]$_ }) }
        if ($argv.Count -gt 0) { $item['argv'] = $argv }
        $trusted.Add([pscustomobject]$item)
    }
    $document = [pscustomobject][ordered]@{
        schema              = $script:AdminPolicySchema
        enabled             = $Enabled
        source_networks     = $networks.ToArray()
        min_prefix_v4       = $MinPrefixV4
        max_sources         = $MaxSources
        hold_hours          = $HoldHours
        jar_hold_hours      = $JarHoldHours
        executable_roots    = @($ExecutableRoot | ForEach-Object { [string]$_ })
        trusted_executables = $trusted.ToArray()
        tsa_test_per_day    = $TsaTestPerDay
        written_at          = $WrittenAt
        written_by          = $WrittenBy
    }
    ($document | ConvertTo-Json -Depth 5) + "`n"
}

function Read-ArciAdminPolicy {
    <#
    .SYNOPSIS
        admin\policy.json as arcilogger_core.admin.contract.load_policy reads it. Returns
        Exists, Valid, Problem, Sha256 (of the file's bytes, $null without a file) and the
        values; a missing or invalid file gives the values of FALLBACK_POLICY: Enabled $false
        (fail closed) with the default networks and executable roots, nothing trusted.
    .DESCRIPTION
        The checks of AdminPolicy (contract 2.4.1): UTF-8 without a byte order mark, one JSON
        object with only the known keys and its schema stated, booleans and integers of their
        type (no floats) in range, IPv4 source networks without host bits, texts of 1-1000
        characters, trusted executables with a path, a 64-hex SHA-256 and an optional argv.
        ApplySystem (Sync-ArciSystem.ps1, Set-ArciFirewall.ps1 -SyncFromConfig) reads it so;
        the installer keeps what an administrator wrote by hand (the keys without a parameter).
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)
    $policy = [pscustomobject]@{
        Exists = $false; Valid = $false; Problem = $null; Sha256 = $null
        Enabled = $false; SourceNetworks = $script:DefaultSourceNetworks; MinPrefixV4 = 16; MaxSources = 500
        HoldHours = 0; JarHoldHours = 24; ExecutableRoots = $script:DefaultExecutableRoots; TrustedExecutables = @()
        TsaTestPerDay = 3; WrittenAt = $null; WrittenBy = $null
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        $policy.Problem = 'policy.json yok [missing]'
        return $policy
    }
    $policy.Exists = $true
    $fail = {
        param([string]$Reason)
        $policy.Problem = $Reason
        $policy
    }
    try { $bytes = [System.IO.File]::ReadAllBytes($Path) }
    catch { return (& $fail ('okunamadı [unreadable]: {0}' -f $_.Exception.Message)) }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $policy.Sha256 = ([System.BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
    if ($bytes.Length -gt 1048576) { return (& $fail 'çok büyük [too large]') }
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) { return (& $fail 'bayt sırası işareti var [has a byte order mark]') }
    $strict = New-Object System.Text.UTF8Encoding -ArgumentList $false, $true
    try { $text = $strict.GetString($bytes) }
    catch { return (& $fail 'UTF-8 değil [not UTF-8]') }
    try { $document = $text | ConvertFrom-Json }
    catch { return (& $fail ('JSON değil [not JSON]: {0}' -f $_.Exception.Message)) }
    if ($document -isnot [System.Management.Automation.PSCustomObject]) { return (& $fail 'JSON nesnesi değil [not a JSON object]') }
    $known = @('schema', 'enabled', 'source_networks', 'min_prefix_v4', 'max_sources', 'hold_hours', 'jar_hold_hours', 'executable_roots', 'trusted_executables', 'tsa_test_per_day', 'written_at', 'written_by')
    $names = @($document.PSObject.Properties | ForEach-Object { $_.Name })
    $unknown = @($names | Where-Object { $known -cnotcontains $_ })
    if ($unknown.Count -gt 0) { return (& $fail ('bilinmeyen anahtar [unknown key]: {0}' -f ($unknown -join ', '))) }
    foreach ($required in @('schema', 'enabled', 'written_at', 'written_by')) {
        if ($names -cnotcontains $required) { return (& $fail ('{0} yok [missing]' -f $required)) }
    }
    if ($document.schema -isnot [string] -or $document.schema -cne $script:AdminPolicySchema) { return (& $fail ('schema {0} değil [is not {0}]' -f $script:AdminPolicySchema)) }
    if ($document.enabled -isnot [bool]) { return (& $fail 'enabled true ya da false değil [is not true or false]') }
    $isText = { param($Value) ($Value -is [string]) -and $Value.Length -ge 1 -and $Value.Length -le 1000 }
    $integer = {
        param([string]$Name, [int]$Minimum, [int]$Maximum, [int]$Default)
        if ($names -cnotcontains $Name) { return $Default }
        $value = $document.$Name
        if (-not ($value -is [int] -or $value -is [long]) -or $value -lt $Minimum -or $value -gt $Maximum) {
            throw ('{0} {1}-{2} arasında bir tam sayı değil [is not an integer from {1} to {2}]' -f $Name, $Minimum, $Maximum)
        }
        [int]$value
    }
    try {
        $minPrefix = & $integer 'min_prefix_v4' 8 32 16
        $maxSources = & $integer 'max_sources' 1 5000 500
        $holdHours = & $integer 'hold_hours' 0 168 0
        $jarHoldHours = & $integer 'jar_hold_hours' 1 168 24
        $tsaTests = & $integer 'tsa_test_per_day' 0 20 3
    }
    catch { return (& $fail $_.Exception.Message) }
    $networks = $script:DefaultSourceNetworks
    if ($names -ccontains 'source_networks') {
        if ($document.source_networks -isnot [System.Array]) { return (& $fail 'source_networks bir liste değil [is not a list]') }
        $list = New-Object System.Collections.Generic.List[string]
        foreach ($value in @($document.source_networks)) {
            $checked = ConvertTo-ArciSourceNetwork -Value ([string]$value)
            if ($value -isnot [string] -or $checked.Problem) { return (& $fail ('source_networks: {0}' -f $value)) }
            $list.Add($checked.Network)
        }
        $networks = $list.ToArray()
    }
    $roots = $script:DefaultExecutableRoots
    if ($names -ccontains 'executable_roots') {
        if ($document.executable_roots -isnot [System.Array]) { return (& $fail 'executable_roots bir liste değil [is not a list]') }
        foreach ($value in @($document.executable_roots)) { if (-not (& $isText $value)) { return (& $fail ('executable_roots: {0}' -f $value)) } }
        $roots = @($document.executable_roots)
    }
    $trusted = @()
    if ($names -ccontains 'trusted_executables') {
        if ($document.trusted_executables -isnot [System.Array]) { return (& $fail 'trusted_executables bir liste değil [is not a list]') }
        foreach ($entry in @($document.trusted_executables)) {
            if ($entry -isnot [System.Management.Automation.PSCustomObject]) { return (& $fail 'trusted_executables: nesne değil [not an object]') }
            $keys = @($entry.PSObject.Properties | ForEach-Object { $_.Name })
            if (@($keys | Where-Object { @('path', 'sha256', 'argv') -cnotcontains $_ }).Count -gt 0 -or $keys -cnotcontains 'path' -or $keys -cnotcontains 'sha256') {
                return (& $fail 'trusted_executables: path ve sha256 gerekli, başka anahtar olmaz [path and sha256 required, no other key but argv]')
            }
            if (-not (& $isText $entry.path) -or $entry.sha256 -isnot [string] -or $entry.sha256 -cnotmatch '^[0-9a-f]{64}$') {
                return (& $fail ('trusted_executables: {0}' -f $entry.path))
            }
            if ($keys -ccontains 'argv' -and $null -ne $entry.argv) {
                $argv = @($entry.argv)
                if ($entry.argv -isnot [System.Array] -or $argv.Count -eq 0 -or @($argv | Where-Object { -not (& $isText $_) }).Count -gt 0) {
                    return (& $fail ('trusted_executables argv: {0}' -f $entry.path))
                }
            }
        }
        $trusted = @($document.trusted_executables)
    }
    # ConvertFrom-Json of PowerShell 7 turns an ISO 8601 text into a DateTime: one without an
    # offset is of the kind Unspecified.
    if (-not ((& $isText $document.written_at) -or $document.written_at -is [datetime])) { return (& $fail 'written_at bir zaman değil [is not a time]') }
    if (($document.written_at -is [datetime] -and $document.written_at.Kind -eq [System.DateTimeKind]::Unspecified) -or
        ($document.written_at -is [string] -and $document.written_at -notmatch '^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$')) {
        return (& $fail 'written_at saat farkı olan bir ISO 8601 zamanı değil [is not an ISO 8601 time with an offset]')
    }
    if (-not (& $isText $document.written_by)) { return (& $fail 'written_by bir metin değil [is not a text]') }
    $policy.Valid = $true
    $policy.Enabled = [bool]$document.enabled
    $policy.SourceNetworks = $networks
    $policy.MinPrefixV4 = $minPrefix
    $policy.MaxSources = $maxSources
    $policy.HoldHours = $holdHours
    $policy.JarHoldHours = $jarHoldHours
    $policy.ExecutableRoots = $roots
    $policy.TrustedExecutables = $trusted
    $policy.TsaTestPerDay = $tsaTests
    $policy.WrittenAt = $document.written_at
    $policy.WrittenBy = [string]$document.written_by
    $policy
}

function Get-ArciSyslogSenderProblem {
    <#
    .SYNOPSIS
        Why -Value may not be a syslog sender of ApplySystem's firewall step, "Türkçe [English]",
        or $null when it may: an IPv4 address or network in canonical text (no host bits),
        inside one of -SourceNetwork and not wider than /-MinPrefixV4 (admin\policy.json,
        contract 2.8.3 step 3; the same rule as system-plan, checked again before any change).
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [AllowEmptyString()][string]$Value,
        [AllowEmptyCollection()][string[]]$SourceNetwork = @(),
        [ValidateRange(0, 32)][int]$MinPrefixV4 = 16
    )
    $network = ConvertTo-ArciNetwork -Value $Value
    if ($null -eq $network -or $network.Bytes.Length -ne 4 -or $network.Text -ne $Value) { return 'IPv4 adresi ya da ağı değil [not an IPv4 address or network]' }
    if ($Value.Contains('/') -and (ConvertTo-ArciSourceNetwork -Value $Value).Network -ne $Value) { return 'ağ adresi değil, ağın içinde bir adres [a host address, not the network address]' }
    if ($network.Prefix -lt $MinPrefixV4) { return ('/{0}, izin verilen en geniş ağ /{1} [wider than the widest network allowed]' -f $network.Prefix, $MinPrefixV4) }
    foreach ($allowed in @($SourceNetwork)) {
        $outer = ConvertTo-ArciNetwork -Value $allowed
        if ($null -ne $outer -and $outer.Prefix -le $network.Prefix -and (Test-ArciAddressInNetwork -Address ([string]$network.Address) -Network $allowed)) { return $null }
    }
    'izin verilen ağların dışında [outside the allowed networks]: {0}' -f (@($SourceNetwork) -join ', ')
}

function ConvertTo-ArciSyslogSender {
    <#
    .SYNOPSIS
        The canonical text of a [[sources]] address as system-plan hands it to the firewall
        (arcilogger_core.admin.systemplan): the host bits of a network cleared, a /32 written as
        the address alone; $null for anything that is not an IPv4 address or network.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([AllowEmptyString()][string]$Value)
    $network = ConvertTo-ArciNetwork -Value $Value
    if ($null -eq $network -or $network.Bytes.Length -ne 4) { return $null }
    if (-not $network.Text.Contains('/') -or $network.Prefix -eq 32) { return [string]$network.Address }
    $bytes = [byte[]]$network.Bytes.Clone()
    for ($i = 0; $i -lt 4; $i++) {
        $bits = [Math]::Max(0, [Math]::Min(8, $network.Prefix - 8 * $i))
        $bytes[$i] = [byte]([int]$bytes[$i] -band ((0xFF -shl (8 - $bits)) -band 0xFF))
    }
    '{0}/{1}' -f (New-Object System.Net.IPAddress -ArgumentList (, $bytes)), $network.Prefix
}

function Get-ArciOutboundProblem {
    <#
    .SYNOPSIS
        Why an outbound entry of the system plan (Rule, HostName, Port) may not be applied in
        strict outbound mode, or $null: the rule is one ApplySystem updates (ARCILOGGER Out NTP,
        Out Kamu SM, Out SMTP, Out SMTP 2-9, Out Replica), the host a plain host name or IPv4
        address, the port 1-65535 (contract 2.8.3 step 3).
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([AllowEmptyString()][string]$Rule, [AllowEmptyString()][string]$HostName, [AllowNull()]$Port)
    if ($Rule -notmatch $script:OutboundRulePattern) { return ('kural bu adımda güncellenmez [rule not updated by this step]: {0}' -f $Rule) }
    if (-not (Test-ArciHostName $HostName)) { return ('ana bilgisayar adı geçersiz [invalid host name]: {0}' -f $HostName) }
    $number = 0
    if (-not [int]::TryParse([string]$Port, [ref]$number) -or $number -lt 1 -or $number -gt 65535) { return ('port geçersiz [invalid port]: {0}' -f $Port) }
    $null
}

function Test-ArciHostName {
    <# True for a host name or IPv4 address as the system plan may name it (letters, digits, dots and hyphens; 1-253 characters). #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([AllowNull()][AllowEmptyString()][string]$Value)
    [bool]($Value -and $Value -match $script:HostNamePattern)
}

function Get-ArciEventLogApi {
    <# The calls of System.Diagnostics.EventLog the event source helpers make, as script blocks (the tests give their own with -EventLog). #>
    [CmdletBinding()]
    [OutputType([hashtable])]
    param()
    @{
        Exists = { param([string]$Source) [System.Diagnostics.EventLog]::SourceExists($Source) }
        LogOf  = { param([string]$Source) [System.Diagnostics.EventLog]::LogNameFromSourceName($Source, '.') }
        Create = { param([string]$Source, [string]$LogName) [System.Diagnostics.EventLog]::CreateEventSource($Source, $LogName) }
        Delete = { param([string]$Source) [System.Diagnostics.EventLog]::DeleteEventSource($Source) }
        Write  = {
            param([string]$Source, [string]$Message, [string]$Level, [int]$Id)
            [System.Diagnostics.EventLog]::WriteEntry($Source, $Message, [System.Diagnostics.EventLogEntryType]$Level, $Id)
        }
    }
}

function Write-ArciEvent {
    <#
    .SYNOPSIS
        Write one entry of the source ARCILOGGER-Admin into the Application log (ApplySystem:
        4104, 4106, 4107; contract 2.14). -Level: Information, Warning or Error. Never fails the
        caller: an entry that cannot be written (the source is not registered) only returns
        $false; an event only wakes a task or tells a person, it is no authority.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param(
        [Parameter(Mandatory = $true)][int]$Id,
        [ValidateSet('Information', 'Warning', 'Error')][string]$Level = 'Information',
        [Parameter(Mandatory = $true)][string]$Message,
        [string]$Source = $script:ArciEventSource,
        [hashtable]$EventLog
    )
    if (-not $EventLog) { $EventLog = Get-ArciEventLogApi }
    try {
        & $EventLog['Write'] $Source $Message $Level $Id
        $true
    }
    catch {
        Write-Verbose ('event {0} not written: {1}' -f $Id, $_.Exception.Message)
        $false
    }
}

function Register-ArciEventSource {
    <#
    .SYNOPSIS
        Register the Application log source ARCILOGGER-Admin, with which ArciAdmin and
        ApplySystem write their events (contract 2.14; an event only wakes a task, the signed
        request is the authority). Idempotent: 'present' when it is registered for the
        Application log already, 'created', or 'moved' when a source of that name belonged to
        another log.
    .DESCRIPTION
        Needs an administrator (a source is a registry key below
        HKLM\SYSTEM\CurrentControlSet\Services\EventLog). Works in Windows PowerShell 5.1 and
        PowerShell 7 (System.Diagnostics.EventLog). -EventLog: the calls to make
        (Get-ArciEventLogApi by default).
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param([string]$Source = $script:ArciEventSource, [string]$LogName = 'Application', [hashtable]$EventLog)
    if (-not $EventLog) { $EventLog = Get-ArciEventLogApi }
    if (& $EventLog['Exists'] $Source) {
        if ([string](& $EventLog['LogOf'] $Source) -eq $LogName) { return 'present' }
        & $EventLog['Delete'] $Source
        & $EventLog['Create'] $Source $LogName
        return 'moved'
    }
    & $EventLog['Create'] $Source $LogName
    'created'
}

function Unregister-ArciEventSource {
    <# Remove the Application log source ARCILOGGER-Admin (-Uninstall): 'removed', or 'absent' when there is none. The events already written stay in the log. #>
    [CmdletBinding()]
    [OutputType([string])]
    param([string]$Source = $script:ArciEventSource, [hashtable]$EventLog)
    if (-not $EventLog) { $EventLog = Get-ArciEventLogApi }
    if (-not (& $EventLog['Exists'] $Source)) { return 'absent' }
    & $EventLog['Delete'] $Source
    'removed'
}

function Test-ArciProgramConfig {
    <#
    .SYNOPSIS
        Whether the program whose Python is -Python loads -ConfigPath: $null when it does,
        otherwise the reason (one line).
    .DESCRIPTION
        Run before a rollback starts the previous version's services (contract 2.16): a version
        from before the browser administration refuses a key it does not know (its Config has
        extra="forbid"), such as enabled = false in a [[sources]] table, and its services and
        tasks would not start. The program is never trusted with more than this one read.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [ValidateRange(1, 3600)][int]$TimeoutSeconds = 120
    )
    $code = @(
        'import pathlib, sys',
        'try:',
        '    from arcilogger_core.config import load_config',
        '    load_config(pathlib.Path(sys.argv[1]))',
        'except Exception as exc:',
        '    sys.exit(" ".join(str(exc).split())[:400] or type(exc).__name__)'
    ) -join "`n"
    try { $result = Invoke-ArciProcess -FilePath $Python -ArgumentList @('-I', '-c', $code, $ConfigPath) -AllowFailure -TimeoutSeconds $TimeoutSeconds }
    catch { return ('{0} çalıştırılamadı [could not be run]: {1}' -f $Python, $_.Exception.Message) }
    if ($result.ExitCode -eq 0) { return $null }
    $lines = @(($result.StdErr + "`n" + $result.StdOut) -split '\r?\n' | Where-Object { $_.Trim() })
    if ($lines.Count -eq 0) { return ('çıkış kodu {0} [exit code]' -f $result.ExitCode) }
    $lines[-1].Trim()
}

function Test-ArciPathBelow([string]$Path, [string]$Folder) {
    <#
    True when -Path is -Folder or lies below it (case-insensitive, either separator). "." and
    ".." segments are resolved first, as Windows resolves them when the path is opened:
    zamane\..\admin\work\x.bin is not below zamane\. A ".." above the root is never below.
    #>
    if (-not $Path -or -not $Folder) { return $false }
    $normal = {
        param([string]$Text)
        $segments = New-Object System.Collections.Generic.List[string]
        foreach ($segment in $Text.Replace('/', '\').Split('\')) {
            if ($segment -eq '.') { continue }
            if ($segment -eq '..') {
                # The first segment is the drive (or empty before a leading separator): never popped.
                if ($segments.Count -le 1) { return $null }
                $segments.RemoveAt($segments.Count - 1)
                continue
            }
            $segments.Add($segment)
        }
        ($segments -join '\').TrimEnd([char]92)
    }
    $p = & $normal $Path
    $f = & $normal $Folder
    if ($null -eq $p -or $null -eq $f) { return $false }
    ($p -ieq $f) -or $p.StartsWith($f + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-ArciExecutableRecords {
    <#
    .SYNOPSIS
        The configured programs the installer must record in policy.trusted_executables so
        that the tasks keep running them after an upgrade (arcilogger_core.execpolicy, contract
        2.16): one entry (What, Path, Argv) each. Pure: -Config as Read-ArciTomlFile returns it.
    .DESCRIPTION
        tsa.java when it has more than one element (the whole command is recorded as Argv: an
        extra JVM argument runs only as a recorded command) or names an absolute path outside
        -ExecutableRoot; backup.pg_dump and backup.pg_restore when set to a path outside it. A
        bare java / java.exe without arguments needs no record (execpolicy finds it through
        the system PATH, which only administrators set), nor does a program below
        -ExecutableRoot. The Zamane jar is never recorded here: it runs only from zamane\
        (Get-ArciZamaneJarPlan).
    #>
    [CmdletBinding()]
    param([AllowNull()]$Config, [string[]]$ExecutableRoot = $script:DefaultExecutableRoots)
    $records = New-Object System.Collections.Generic.List[object]
    $outside = {
        param([string]$Path)
        foreach ($root in @($ExecutableRoot)) { if (Test-ArciPathBelow $Path $root) { return $false } }
        $true
    }
    $java = @(Get-ArciConfigValue -Config $Config -Path 'tsa.java' -Default @('java') | ForEach-Object { [string]$_ })
    if ($java.Count -gt 0) {
        $program = $java[0]
        $absolute = $program.Contains('\') -or $program.Contains('/')
        if ($java.Count -gt 1 -or ($absolute -and (& $outside $program))) {
            $argv = $null
            if ($java.Count -gt 1) { $argv = $java }
            $records.Add([pscustomobject]@{ What = 'tsa.java'; Path = $program; Argv = $argv })
        }
    }
    foreach ($key in @('backup.pg_dump', 'backup.pg_restore')) {
        $value = [string](Get-ArciConfigValue -Config $Config -Path $key -Default '')
        if ($value -and (& $outside $value)) { $records.Add([pscustomobject]@{ What = $key; Path = $value; Argv = $null }) }
    }
    , $records.ToArray()
}

function Get-ArciUnsafeWriters {
    <#
    .SYNOPSIS
        Who other than SYSTEM, Administrators and TrustedInstaller could change a program, plant
        a file next to it or replace a folder above it: one "<path>: <SID>" per finding (an empty
        list: nobody). Pure: -Facts is what Get-ArciPathAclFacts read, the program first, then
        its folder, then each folder above it (Path, Owner, Rules with Sid, Rights as a number,
        Allow, InheritOnly, ObjectInherit).
    .DESCRIPTION
        A recorded program must stay the file the installer hashed: execpolicy trusts a SHA-256
        only at its recorded path, and whoever can write the folder could replace the file or
        put a library next to it (Day 0 note D0-3: the installer records only paths in folders
        that only administrators and SYSTEM can write). What counts depends on the place (see
        $script:FileWriteMask): a write, delete, permission or owner right on the file; the
        right to create or delete files in its folder, to delete or re-permission that folder,
        and an inherit-only entry of that folder that files inherit (it reaches the program's
        libraries); a delete, permission or owner right on a folder above it. An inherit-only
        entry of a folder above is left out: what it passes on shows as an inherited entry on
        the folders of the path, which are checked themselves (C:\ grants Authenticated Users
        Modify that way, for new folders that do not block it, and only AppendData itself). An
        owner outside the trusted accounts counts everywhere (an owner can change the rights).
        CREATOR OWNER and OWNER RIGHTS stand for the owner and are covered by that check.
    #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param([AllowNull()][AllowEmptyCollection()][object[]]$Facts)
    $found = New-Object System.Collections.Generic.List[string]
    $level = -1
    foreach ($fact in @($Facts)) {
        if ($null -eq $fact) { continue }
        $level++
        $owner = [string]$fact.Owner
        if ($owner -and $script:TrustedWriterSids -notcontains $owner) {
            $found.Add(('{0}: {1} (sahip [owner])' -f $fact.Path, $owner))
        }
        foreach ($rule in @($fact.Rules)) {
            if ($null -eq $rule -or -not $rule.Allow) { continue }
            $sid = [string]$rule.Sid
            if ($script:TrustedWriterSids -contains $sid -or $script:OwnerPlaceholderSids -contains $sid) { continue }
            $inheritOnly = ($rule.PSObject.Properties.Name -contains 'InheritOnly') -and [bool]$rule.InheritOnly
            $toFiles = ($rule.PSObject.Properties.Name -contains 'ObjectInherit') -and [bool]$rule.ObjectInherit
            $mask = [int64]0
            if ($level -eq 0) { $mask = $script:FileWriteMask }
            elseif ($level -eq 1) { $mask = $(if (-not $inheritOnly) { $script:FolderWriteMask } elseif ($toFiles) { $script:FileWriteMask } else { [int64]0 }) }
            elseif (-not $inheritOnly) { $mask = $script:AncestorWriteMask }
            if (([int64]$rule.Rights -band $mask) -eq 0) { continue }
            $text = '{0}: {1}' -f $fact.Path, $sid
            if (-not $found.Contains($text)) { $found.Add($text) }
        }
    }
    , $found.ToArray()
}

function Get-ArciPathAclFacts {
    <#
    .SYNOPSIS
        The owner and the access entries of -Path and of every folder above it (the path first,
        each with its inheritance flags), as plain data for Get-ArciUnsafeWriters; $null where
        Windows rights cannot be read (not Windows).
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-ArciWindows)) { return $null }
    $facts = New-Object System.Collections.Generic.List[object]
    $sidType = [System.Security.Principal.SecurityIdentifier]
    $current = [System.IO.Path]::GetFullPath($Path)
    while ($current) {
        $item = Get-Item -LiteralPath $current -Force
        $security = Get-ArciAccessControl -Item $item -Sections 'Access, Owner'
        $rules = New-Object System.Collections.Generic.List[object]
        foreach ($rule in @($security.GetAccessRules($true, $true, $sidType))) {
            $rules.Add([pscustomobject]@{
                    Sid = $rule.IdentityReference.Value; Rights = [int64][int]$rule.FileSystemRights
                    Allow = ($rule.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow)
                    InheritOnly = (($rule.PropagationFlags -band [System.Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0)
                    ObjectInherit = (($rule.InheritanceFlags -band [System.Security.AccessControl.InheritanceFlags]::ObjectInherit) -ne 0)
                })
        }
        $facts.Add([pscustomobject]@{ Path = $current; Owner = $security.GetOwner($sidType).Value; Rules = $rules.ToArray() })
        $parent = [System.IO.Path]::GetDirectoryName($current)
        if (-not $parent -or $parent -eq $current) { break }
        $current = $parent
    }
    , $facts.ToArray()
}

function Read-ArciInstallerHashes {
    <# The SHA-256 values of zamane\zamane-jar.sha256 ("<sha256>  <file>" lines; "#" lines are comments), as arcilogger_core.execpolicy reads them. #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param([Parameter(Mandatory = $true)][string]$Path)
    $hashes = New-Object System.Collections.Generic.List[string]
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return , $hashes.ToArray() }
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $fields = @($line.Trim().TrimStart([char]0xFEFF) -split '\s+' | Where-Object { $_ })
        if ($fields.Count -gt 0 -and -not $fields[0].StartsWith('#')) { $hashes.Add($fields[0].ToLowerInvariant()) }
    }
    , $hashes.ToArray()
}

function Get-ArciZamaneJarPlan {
    <#
    .SYNOPSIS
        What an installation run does with the configured Zamane client (tsa.zamane_jar) so
        that sealing goes on under execpolicy (contract 2.16, Day 0 note D0-14). Pure.
    .DESCRIPTION
        A client runs only from zamane\ (not from its staging folder web\) and only when its
        SHA-256 is known, approved or recorded by the installer. Returns Action and Reason:
        - 'none': nothing to do (no client configured, the file is missing - the health check
          says so -, or it is in zamane\ and its SHA-256 is recorded or approved already);
        - 'record': in zamane\ but not recorded: its line is appended to zamane-jar.sha256
          (only administrators and SYSTEM write zamane\ outside web\);
        - 'copy': outside zamane\ (configured by hand before this version): copied into
          zamane\ under its file name (Target), its line appended, tsa.zamane_jar pointed at the
          copy; the original stays where it is. A copy that is there already with the same
          SHA-256 is used as it is (TargetSha256);
        - 'refuse': a staged client (web\), or a different file of the same name in zamane\.
        -Sha256: the client's SHA-256 ($null: the file is missing). -Recorded: the hashes of
        zamane-jar.sha256 and of the approved clients (jars.json). -TargetSha256: the hash of
        the file zamane\<name> when one exists.
    #>
    [CmdletBinding()]
    param(
        [AllowEmptyString()][string]$Jar,
        [AllowEmptyString()][string]$Sha256,
        [Parameter(Mandatory = $true)][string]$ZamaneDir,
        [AllowEmptyCollection()][string[]]$Recorded = @(),
        [AllowEmptyString()][string]$TargetSha256
    )
    $plan = [pscustomobject]@{ Action = 'none'; Jar = $Jar; Sha256 = $Sha256; Target = $null; Reason = $null }
    if (-not $Jar -or -not $Sha256) { return $plan }
    $separators = [char[]]@([char]92, [char]47)
    $name = $Jar.Replace('/', '\').TrimEnd($separators).Split('\')[-1]
    # Path.Combine, not Join-Path: no drive of -ZamaneDir needs to exist (pure).
    if (Test-ArciPathBelow $Jar ([System.IO.Path]::Combine($ZamaneDir, 'web'))) {
        $plan.Action = 'refuse'
        $plan.Reason = 'tsa.zamane_jar bekleyen (yüklenmiş, onaylanmamış) bir istemciyi gösteriyor; yüklenen istemci approve-jar ile onaylanınca zamane\approved içinden çalışır [tsa.zamane_jar names a staged client: it runs from zamane\approved once approved with approve-jar]'
        return $plan
    }
    if (Test-ArciPathBelow $Jar $ZamaneDir) {
        if (@($Recorded) -notcontains $Sha256.ToLowerInvariant()) {
            $plan.Action = 'record'
            $plan.Reason = 'zamane\ içindeki istemcinin SHA-256 değeri kayıtlı değil, kaydediliyor [the client in zamane\ is not recorded yet: recording its SHA-256]'
        }
        return $plan
    }
    $plan.Target = [System.IO.Path]::Combine($ZamaneDir, $name)
    if ($TargetSha256 -and $TargetSha256.ToLowerInvariant() -ne $Sha256.ToLowerInvariant()) {
        $plan.Action = 'refuse'
        $plan.Reason = ('zamane\{0} başka bir dosya; üzerine yazılmaz. tsa.zamane_jar ayarını ya da dosyayı elle düzeltin [zamane\{0} is a different file and is not overwritten: fix tsa.zamane_jar or the file by hand]' -f $name)
        return $plan
    }
    $plan.Action = 'copy'
    $plan.Reason = ('Zamane istemcisi zamane\ dışında; görevler yalnızca zamane\ içinden çalıştırır: {0} klasörüne kopyalanıyor, tsa.zamane_jar kopyayı gösterecek, özgün dosya yerinde kalır [the client is outside zamane\, which is where the tasks run it from: copied there, tsa.zamane_jar will name the copy, the original stays]' -f $ZamaneDir)
    $plan
}

function Copy-ArciApprovedJar {
    <#
    .SYNOPSIS
        Promote a staged Zamane client (ApplySystem, contract 2.8.3 step 6): -Source copied to
        -Target (zamane\approved\<sha256>.jar) and the copy's SHA-256 compared with -Sha256.
        $null when the copy is in place, otherwise why not ("Türkçe [English]").
    .DESCRIPTION
        The copy is written under a temporary name in the target folder, hashed and only then
        renamed to -Target, so no other content ever carries that name; a copy that does not
        match is deleted (the staged file may have changed after the caller hashed it: ArciAdmin
        writes zamane\web). A -Target that holds the right content already is kept; one with
        other content is replaced (a file named after a hash it does not have is never run).
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][string]$Sha256
    )
    $expected = $Sha256.ToLowerInvariant()
    if ((Test-Path -LiteralPath $Target -PathType Leaf) -and (Get-ArciFileSha256 -Path $Target) -eq $expected) { return $null }
    $folder = Split-Path -Parent $Target
    if (-not (Test-Path -LiteralPath $folder)) { [void](New-Item -ItemType Directory -Path $folder -Force) }
    $temp = Join-Path $folder ('.{0}.tmp' -f (Split-Path -Leaf $Target))
    Copy-Item -LiteralPath $Source -Destination $temp -Force
    $actual = Get-ArciFileSha256 -Path $temp
    if ($actual -ne $expected) {
        Remove-Item -LiteralPath $temp -Force
        return ('kopya tutmuyor, silindi [the copy does not match, deleted]: {0} (SHA-256 {1})' -f $Target, $actual)
    }
    Move-Item -LiteralPath $temp -Destination $Target -Force
    $null
}

function Merge-ArciJarsDocument {
    <#
    .SYNOPSIS
        admin\system\jars.json as ApplySystem writes it: its own -Staged and -Approved entries
        merged with -Written, the document as it is on disk right before the write. Pure.
    .DESCRIPTION
        The file has two writers, ApplySystem and the console's approve-jar (Day 0 note D0-16),
        and neither may drop the other's entries: every entry of either side stays, one per
        SHA-256 and list; where both sides list a SHA-256 the entry on disk wins (an approval
        approve-jar recorded meanwhile keeps its approved_by). -Written $null: no file.
    #>
    [CmdletBinding()]
    param(
        [AllowNull()]$Written,
        [AllowEmptyCollection()][object[]]$Staged = @(),
        [AllowEmptyCollection()][object[]]$Approved = @()
    )
    $merge = {
        param([object[]]$Disk, [object[]]$Ours)
        $list = New-Object System.Collections.Generic.List[object]
        $seen = New-Object System.Collections.Generic.HashSet[string]
        foreach ($entry in @($Disk) + @($Ours)) {
            if ($null -eq $entry -or -not $seen.Add([string]$entry.sha256)) { continue }
            $list.Add($entry)
        }
        , $list.ToArray()
    }
    $diskStaged = @(Get-ArciJsonField -Object $Written -Name 'staged' | Where-Object { $null -ne $_ })
    $diskApproved = @(Get-ArciJsonField -Object $Written -Name 'approved' | Where-Object { $null -ne $_ })
    [pscustomobject][ordered]@{
        schema   = 'arcilogger.admin-jars/1'
        staged   = (& $merge $diskStaged @($Staged))
        approved = (& $merge $diskApproved @($Approved))
    }
}

# --- data drive -----------------------------------------------------------------------------------

function Get-ArciDriveFacts {
    <#
    .SYNOPSIS
        The drive whose root is -Root ("D:\") as plain data: Root, DriveType (Fixed, Removable,
        CDRom, Network, Ram, ...), IsReady, DriveFormat and FreeBytes; $null when the system lists
        no such drive. An empty DVD drive is listed but not ready.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Root)
    $separators = [char[]]@([char]92, [char]47)
    $wanted = $Root.TrimEnd($separators)
    $drives = @()
    try { $drives = @([System.IO.DriveInfo]::GetDrives()) } catch { $drives = @() }
    foreach ($drive in $drives) {
        if (-not [string]::Equals(([string]$drive.Name).TrimEnd($separators), $wanted, [System.StringComparison]::OrdinalIgnoreCase)) { continue }
        $ready = $false
        try { $ready = [bool]$drive.IsReady } catch { $ready = $false }
        $format = $null
        $free = $null
        if ($ready) {
            try {
                $format = [string]$drive.DriveFormat
                $free = [int64]$drive.AvailableFreeSpace
            }
            catch { $format = $null; $free = $null }
        }
        return [pscustomobject]@{ Root = [string]$drive.Name; DriveType = [string]$drive.DriveType; IsReady = $ready; DriveFormat = $format; FreeBytes = $free }
    }
    $null
}

function Get-ArciDiskBusType {
    <#
    .SYNOPSIS
        The bus of the disk behind a drive letter (USB, SATA, NVMe, SAS, RAID, ...) from the Storage
        module (Get-Partition, Get-Disk), or $null where Windows does not tell: no Storage module,
        no partition behind the letter, a disk type it does not report.
    #>
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Root)
    $letter = [regex]::Match([string]$Root, '^(?<l>[A-Za-z]):')
    if (-not $letter.Success) { return $null }
    if (-not (Get-Command Get-Partition -ErrorAction SilentlyContinue) -or -not (Get-Command Get-Disk -ErrorAction SilentlyContinue)) { return $null }
    try {
        $partition = @(Get-Partition -DriveLetter $letter.Groups['l'].Value -ErrorAction Stop)[0]
        $bus = [string]($partition | Get-Disk -ErrorAction Stop).BusType
        if ($bus) { return $bus }
    }
    catch { Write-Verbose ('disk bus type not readable: {0}' -f $_.Exception.Message) }
    $null
}

function Select-ArciDataRoot {
    <#
    .SYNOPSIS
        The data root of a new installation when neither -DataRoot nor an existing arcilogger.toml
        names one.
    .DESCRIPTION
        D:\ARCILOGGER when D: is a ready, fixed NTFS drive (the data disk of docs/KURULUM.md
        section 1), otherwise <system drive>\ARCILOGGER: on a desktop with one disk D: is often
        missing or the DVD drive. Returns Path, Fallback ($true: the evidence then shares the
        disk of Windows, which the preflight warns about) and Reason (why D: was not taken).
    #>
    [CmdletBinding()]
    param([AllowNull()]$DataDrive, [AllowEmptyString()][string]$SystemDrive = 'C:')
    if (-not $SystemDrive) { $SystemDrive = 'C:' }
    $reason = $null
    if ($null -eq $DataDrive) { $reason = 'D: yok [no drive D:]' }
    elseif ($DataDrive.DriveType -ne 'Fixed') { $reason = ('D: sabit disk değil [not a fixed disk]: {0}' -f $DataDrive.DriveType) }
    elseif (-not $DataDrive.IsReady) { $reason = 'D: hazır değil [not ready]' }
    elseif ($DataDrive.DriveFormat -ne 'NTFS') { $reason = ('D: NTFS değil [not NTFS]: {0}' -f $DataDrive.DriveFormat) }
    if ($null -eq $reason) { return [pscustomobject]@{ Path = 'D:\ARCILOGGER'; Fallback = $false; Reason = $null } }
    [pscustomobject]@{ Path = ($SystemDrive.TrimEnd([char[]]@([char]92, [char]47)) + '\ARCILOGGER'); Fallback = $true; Reason = $reason }
}

function Get-ArciDataDriveChecks {
    <#
    .SYNOPSIS
        Preflight checks of the drive that holds the data root, from plain facts
        (Get-ArciDriveFacts, Get-ArciDiskBusType). Returns Id, Status and Text per check.
    .DESCRIPTION
        - drive: the drive exists, is ready and is a fixed disk. A removable drive (USB stick,
          memory card), an optical drive, a network drive or path and a RAM disk are refused.
        - ntfs: the vault's deny entries and its auditing need NTFS.
        - space: at least -MinFreeGB free (default 100): the raw logs and the vault are kept for
          at least two years (730 days) and only grow. Less stops a new installation and warns
          otherwise.
        - disk-bus: a disk on USB gets a warning (it can be unplugged, or switched off to save
          power); 'unknown' when Windows does not tell.
        - system-disk: data on the drive of Windows gets a warning: one disk failure loses the
          evidence, the database and the secret store together.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Root,
        [AllowNull()]$Drive,
        [AllowNull()][AllowEmptyString()][string]$BusType,
        [AllowNull()][AllowEmptyString()][string]$SystemDrive,
        [int]$MinFreeGB = 100,
        [string]$Mode = 'install'
    )
    $checks = New-Object System.Collections.Generic.List[object]
    $add = { param([string]$Id, [string]$Status, [string]$Text) $checks.Add([pscustomobject]@{ Id = $Id; Status = $Status; Text = $Text }) }
    $fix = 'sabit bir NTFS disk seçin (-DataRoot) [choose a fixed NTFS disk]'
    if ($Root.StartsWith('\\') -or ($null -ne $Drive -and $Drive.DriveType -eq 'Network')) {
        & $add 'drive' 'fail' ('{0} bir ağ sürücüsü ya da paylaşımı [network drive or share]: ham loglar ve kasa yerel bir diskte durur; {1}' -f $Root, $fix)
        return , $checks.ToArray()
    }
    if ($null -eq $Drive -or $Drive.DriveType -eq 'NoRootDirectory') {
        & $add 'drive' 'fail' ('Veri sürücüsü yok [data drive missing]: {0}; {1}' -f $Root, $fix)
        return , $checks.ToArray()
    }
    switch ($Drive.DriveType) {
        'Fixed' { }
        'Removable' { & $add 'drive' 'fail' ('{0} çıkarılabilir bir sürücü (USB bellek, hafıza kartı) [removable drive]: kanıt deposu olamaz; {1}' -f $Root, $fix) }
        'CDRom' { & $add 'drive' 'fail' ('{0} bir CD/DVD sürücüsü [optical drive]; {1}' -f $Root, $fix) }
        'Ram' { & $add 'drive' 'fail' ('{0} bir RAM diski [RAM disk]: içeriği yeniden başlatmada silinir; {1}' -f $Root, $fix) }
        default { & $add 'drive' 'fail' ('{0} sürücü türü desteklenmiyor [unsupported drive type]: {1}; {2}' -f $Root, $Drive.DriveType, $fix) }
    }
    if ($checks.Count -gt 0) { return , $checks.ToArray() }
    if (-not $Drive.IsReady) {
        & $add 'drive' 'fail' ('{0} hazır değil [drive not ready]; {1}' -f $Root, $fix)
        return , $checks.ToArray()
    }
    if ($Drive.DriveFormat -eq 'NTFS') { & $add 'ntfs' 'ok' ('{0} NTFS' -f $Root) }
    else { & $add 'ntfs' 'fail' ('{0} NTFS değil [not NTFS]: {1}; kasanın izinleri ve denetimi NTFS ister [the vault needs NTFS]' -f $Root, $Drive.DriveFormat) }
    $freeGb = [math]::Floor([double]$Drive.FreeBytes / 1GB)
    $why = 'ham loglar ve kasa en az iki yıl (730 gün) saklanır ve büyür [raw logs and the vault are kept for at least two years (730 days) and grow]; daha büyük bir disk seçin (-DataRoot) ya da boyutlandırmayı doğruladıysanız -MinFreeGB verin (docs\KURULUM.md bölüm 0)'
    if ($freeGb -ge $MinFreeGB) { & $add 'space' 'ok' ('{0} boş alan [free] {1} GB' -f $Root, $freeGb) }
    elseif ($Mode -eq 'install') { & $add 'space' 'fail' ('{0} boş alan {1} GB < {2} GB [not enough free space]: {3}' -f $Root, $freeGb, $MinFreeGB, $why) }
    else { & $add 'space' 'warn' ('{0} boş alan {1} GB < {2} GB [low free space]: {3}' -f $Root, $freeGb, $MinFreeGB, $why) }
    if (-not $BusType) { & $add 'disk-bus' 'unknown' ('{0} diskinin veri yolu denetlenemedi [disk bus type not checked]' -f $Root) }
    elseif ($BusType -eq 'USB') {
        & $add 'disk-bus' 'warn' ('{0} USB ile bağlı bir diskte [USB-attached disk]: bağlantı koparsa ya da Windows diski güç tasarrufu için kapatırsa loglar yazılamaz; kanıt deposu için bilgisayarın içindeki bir disk önerilir [an internal disk is recommended for the evidence store]' -f $Root)
    }
    else { & $add 'disk-bus' 'ok' ('{0} disk veri yolu [disk bus] {1}' -f $Root, $BusType) }
    $separators = [char[]]@([char]92, [char]47)
    if ($SystemDrive -and [string]::Equals($Root.TrimEnd($separators), $SystemDrive.TrimEnd($separators), [System.StringComparison]::OrdinalIgnoreCase)) {
        & $add 'system-disk' 'warn' ('Veri klasörü Windows ile aynı diskte [evidence shares the system disk] ({0}): tek bir disk arızası ham logları, kasayı, veritabanını ve gizli depoyu birlikte kaybettirir; bir ağ paylaşımına replikayı ([replica]) ve düzenli yedeği kurun ya da ayrı bir veri diski ekleyip -DataRoot ile kurun [one disk failure loses evidence, database and secret store together: set up the replica on a network share ([replica]) and backups, or add a data disk] (docs\KURULUM.md, "Masaüstü Windows 11 Pro")' -f $Root)
    }
    , $checks.ToArray()
}

# --- operating system ----------------------------------------------------------------------------

# EditionID (HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion) of the client editions, by family.
$script:ArciEditionFamily = @{
    Core = 'Home'; CoreN = 'Home'; CoreSingleLanguage = 'Home'; CoreCountrySpecific = 'Home'
    Professional = 'Pro'; ProfessionalN = 'Pro'; ProfessionalWorkstation = 'Pro'; ProfessionalWorkstationN = 'Pro'
    ProfessionalEducation = 'Pro'; ProfessionalEducationN = 'Pro'
    Enterprise = 'Enterprise'; EnterpriseN = 'Enterprise'; EnterpriseS = 'Enterprise'; EnterpriseSN = 'Enterprise'
    IoTEnterprise = 'Enterprise'; IoTEnterpriseS = 'Enterprise'
    Education = 'Education'; EducationN = 'Education'
}

# The lowest Windows 11 build that is still serviced, per edition family: Microsoft's lifecycle
# pages as read for this release (docs/adr/0007, amendment 2026-10-19): Windows 11 24H2 (26100)
# Home/Pro had its last update on 2026-10-13, 25H2 (26200) is serviced until 2027-10-12. Enterprise
# and Education use the Pro cut-off here although Microsoft may service their builds longer (not
# checked for this table: their lifecycle pages per version). Check the table with every release;
# older builds only warn ("check Microsoft's support dates").
$script:ArciClientServicedBuild = @{ Pro = 26200; Enterprise = 26200; Education = 26200 }

# Windows Server builds ARCILOGGER supports.
$script:ArciServerBuilds = @{ 20348 = 'Windows Server 2022'; 26100 = 'Windows Server 2025' }

function Get-ArciOsSupport {
    <#
    .SYNOPSIS
        Whether ARCILOGGER supports this Windows, from plain facts (pure, testable anywhere).
        Returns checks with Id ('os', 'os-arch', 'os-licence'), Status (ok, warn, fail, info,
        unknown) and Text.
    .DESCRIPTION
        - Windows Server 2022 (build 20348) and 2025 (26100): ok. Other server builds: warning.
        - Windows 11 Pro, Enterprise, Education: ok from the build in -ServicedBuild (default
          $script:ArciClientServicedBuild: 26200 = 25H2), a warning for older Windows 11 builds
          (22000 and later: possibly out of servicing, check Microsoft's support dates).
        - Windows 11 Home and Windows 10 (client builds below 22000, every edition incl. LTSC) stop
          the installation unless -AllowUnsupportedWindows (then a warning); an unknown edition
          warns. The text states the product policy only, no Microsoft support date (those differ
          by edition and channel).
        - ARM64: warning (the x64 programs would run under emulation, untested); other
          architectures than x64 and ARM64 fail.
        - Any client Windows: an information line about the Windows client licence (verify with a
          Microsoft partner; no conclusion is drawn here).
    #>
    [CmdletBinding()]
    param(
        [int]$ProductType,
        [int]$Build,
        [AllowNull()][AllowEmptyString()][string]$EditionId,
        [AllowNull()][AllowEmptyString()][string]$Architecture,
        [AllowNull()][AllowEmptyString()][string]$Caption,
        [switch]$AllowUnsupportedWindows,
        [hashtable]$ServicedBuild
    )
    if (-not $PSBoundParameters.ContainsKey('ServicedBuild')) { $ServicedBuild = $script:ArciClientServicedBuild }
    $checks = New-Object System.Collections.Generic.List[object]
    $add = { param([string]$Id, [string]$Status, [string]$Text) $checks.Add([pscustomobject]@{ Id = $Id; Status = $Status; Text = $Text }) }
    $name = $Caption
    if (-not $name) { $name = 'Windows' }
    $override = ' Yine de denemek için [to try anyway]: -AllowUnsupportedWindows.'
    $unsupported = 'fail'
    if ($AllowUnsupportedWindows) {
        $unsupported = 'warn'
        $override = ' -AllowUnsupportedWindows ile sürüyor [continuing with -AllowUnsupportedWindows].'
    }
    if ($ProductType -ne 1) {
        if ($script:ArciServerBuilds.ContainsKey($Build)) { & $add 'os' 'ok' ('{0} (build {1})' -f $name, $Build) }
        else { & $add 'os' 'warn' ('Desteklenen sürüm değil [not a supported version]: {0} (build {1}); önerilen Windows Server 2022/2025 ya da Windows 11 Pro/Enterprise/Education' -f $name, $Build) }
    }
    elseif ($Build -lt 22000) {
        & $add 'os' $unsupported ('Windows 10 desteklenmiyor [Windows 10 is not supported]: {0} (build {1}). Windows 11 Pro, Enterprise, Education ya da Windows Server 2022/2025 kullanın [use Windows 11 Pro, Enterprise, Education or Windows Server 2022/2025].{2}' -f $name, $Build, $override)
    }
    else {
        $family = $null
        if ($EditionId -and $script:ArciEditionFamily.ContainsKey($EditionId)) { $family = $script:ArciEditionFamily[$EditionId] }
        if ($family -eq 'Home') {
            & $add 'os' $unsupported ('Windows 11 Home desteklenmiyor [Windows 11 Home is not supported]: {0} (build {1}). Home sürümünde Yerel Güvenlik İlkesi ve uzak masaüstü sunucusu yoktur; Windows 11 Pro, Enterprise ya da Education kullanın.{2}' -f $name, $Build, $override)
        }
        elseif ($null -eq $family) {
            & $add 'os' 'warn' ('Bilinmeyen Windows sürümü [unknown edition] ''{0}'': {1} (build {2}); desteklenenler Windows 11 Pro, Enterprise, Education' -f $EditionId, $name, $Build)
        }
        else {
            $minimum = 0
            if ($ServicedBuild.ContainsKey($family)) { $minimum = [int]$ServicedBuild[$family] }
            if ($Build -ge $minimum) { & $add 'os' 'ok' ('{0} (build {1}, {2})' -f $name, $Build, $EditionId) }
            else {
                & $add 'os' 'warn' ('{0} (build {1}): bu Windows 11 sürümü güncelleme almıyor olabilir [may be out of servicing]; Microsoft''un destek tarihlerini kontrol edin ve build {2} ya da sonrasına yükseltin [check Microsoft''s support dates]' -f $name, $Build, $minimum)
            }
        }
    }
    switch ($Architecture) {
        'x64' { & $add 'os-arch' 'ok' 'x64' }
        'ARM64' { & $add 'os-arch' 'warn' 'ARM64: ARCILOGGER''ın x64 programları (Python, Caddy, PostgreSQL) öykünmeyle çalışır; bu yapılandırma denenmedi [x64 programs run under emulation, untested]' }
        { -not $_ } { & $add 'os-arch' 'unknown' 'İşlemci mimarisi okunamadı [architecture not checked]' }
        default { & $add 'os-arch' 'fail' ('x64 Windows gerekli [x64 Windows required]: {0}' -f $Architecture) }
    }
    if ($ProductType -eq 1) {
        & $add 'os-licence' 'info' 'Windows istemci lisansı [client licence]: bu bilgisayarı sunucu olarak kullanmadan önce Windows lisans koşullarını bir Microsoft iş ortağıyla doğrulayın [verify the Windows licence terms with a Microsoft partner].'
    }
    , $checks.ToArray()
}

# --- power settings (Set-ArciPower.ps1) ------------------------------------------------------------

function Get-ArciPowerSchemeGuid {
    <# The GUID in "powercfg /getactivescheme" output (the label around it is localised), or $null. #>
    [CmdletBinding()]
    [OutputType([string])]
    param([AllowNull()][AllowEmptyString()][string]$Text)
    $match = [regex]::Match([string]$Text, '[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}')
    if (-not $match.Success) { return $null }
    $match.Value.ToLowerInvariant()
}

function ConvertFrom-ArciPowercfgQuery {
    <#
    .SYNOPSIS
        The current AC and DC values of one setting from "powercfg /query <scheme> <subgroup>
        <setting>", or $null when the output is not the expected shape.
    .DESCRIPTION
        The labels are localised; the numbers are not. A range setting such as STANDBYIDLE prints
        exactly five hexadecimal values in this order: minimum, maximum and increment of the
        possible settings, then the current AC index and the current DC index. Any other count is
        not guessed at.
    #>
    [CmdletBinding()]
    param([AllowNull()][AllowEmptyString()][string]$Text)
    $values = @([regex]::Matches([string]$Text, '\b0x(?<hex>[0-9A-Fa-f]{8})\b') | ForEach-Object { [Convert]::ToInt64($_.Groups['hex'].Value, 16) })
    if ($values.Count -ne 5) { return $null }
    [pscustomobject]@{ Ac = $values[3]; Dc = $values[4] }
}

# --- health ---------------------------------------------------------------------------------------

function Test-ArciHttpsHealth {
    <#
    .SYNOPSIS
        GET https://<HostName>:<Port><Path> through Caddy and accept the server certificate only if
        its chain ends at Caddy's own root certificate (pinned by thumbprint) - no blanket
        "trust any certificate". Returns the HTTP status code and body.
    #>
    [CmdletBinding()]
    param(
        [string]$HostName = 'localhost',
        [int]$Port = 443,
        [string]$Path = '/api/v1/health',
        [Parameter(Mandatory = $true)][string]$RootCertificatePath,
        [int]$TimeoutMilliseconds = 10000
    )
    $root = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList $RootCertificatePath
    $rootThumbprint = $root.Thumbprint
    $callback = {
        param($requestSender, $certificate, $chain, $sslPolicyErrors)
        if ($null -eq $certificate) { return $false }
        $leaf = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList $certificate
        $check = New-Object System.Security.Cryptography.X509Certificates.X509Chain
        $check.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
        $check.ChainPolicy.VerificationFlags = [System.Security.Cryptography.X509Certificates.X509VerificationFlags]::AllowUnknownCertificateAuthority
        [void]$check.ChainPolicy.ExtraStore.Add($root)
        if ($null -ne $chain) {
            foreach ($element in $chain.ChainElements) { [void]$check.ChainPolicy.ExtraStore.Add($element.Certificate) }
        }
        if (-not $check.Build($leaf)) { return $false }
        $top = $check.ChainElements[$check.ChainElements.Count - 1].Certificate
        return ($top.Thumbprint -eq $rootThumbprint)
    }.GetNewClosure()
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $connect = $client.ConnectAsync('127.0.0.1', $Port)
        if (-not $connect.Wait($TimeoutMilliseconds)) { throw "bağlantı zaman aşımı [connect timeout] 127.0.0.1:$Port" }
        $stream = $client.GetStream()
        $stream.ReadTimeout = $TimeoutMilliseconds
        $stream.WriteTimeout = $TimeoutMilliseconds
        $ssl = New-Object System.Net.Security.SslStream -ArgumentList $stream, $false, ([System.Net.Security.RemoteCertificateValidationCallback]$callback)
        # TLS 1.2 named explicitly: without it .NET Framework (Windows PowerShell 5.1) may offer
        # only its legacy protocols, which Caddy refuses ("protocols tls1.2 tls1.3").
        $ssl.AuthenticateAsClient($HostName, $null, [System.Security.Authentication.SslProtocols]::Tls12, $false)
        $request = "GET $Path HTTP/1.1`r`nHost: $HostName`r`nAccept: application/json`r`nConnection: close`r`n`r`n"
        $bytes = [System.Text.Encoding]::ASCII.GetBytes($request)
        $ssl.Write($bytes, 0, $bytes.Length)
        $ssl.Flush()
        $reader = New-Object System.IO.StreamReader -ArgumentList $ssl, ([System.Text.Encoding]::UTF8)
        $response = $reader.ReadToEnd()
        $status = 0
        if ($response -match '^HTTP/1\.[01] (?<code>\d{3})') { $status = [int]$Matches['code'] }
        $body = ''
        $split = $response.IndexOf("`r`n`r`n")
        if ($split -ge 0) { $body = $response.Substring($split + 4) }
        [pscustomobject]@{ Status = $status; Body = $body; Response = $response }
    }
    finally {
        $client.Close()
    }
}

Export-ModuleMember -Function @(
    'Get-ArciServiceNames', 'Get-ArciWellKnownSid', 'Write-ArciStep', 'Write-ArciInfo', 'Write-ArciWarn',
    'Test-ArciWindows', 'Test-ArciAdministrator', 'Test-ArciInteractiveConsole', 'Get-ArciWindowsPowerShellModulePath', 'Initialize-ArciModulePath',
    'Get-ArciServiceSid', 'New-ArciRandomPassword', 'Rename-ArciFolder',
    'ConvertTo-ArciScramVerifier', 'Get-ArciFileSha256', 'Write-ArciUtf8File', 'Read-ArciSha256Sums',
    'Test-ArciBundle', 'Resolve-ArciComponentSource', 'Expand-ArciTemplate', 'ConvertTo-ArciArgumentString', 'Invoke-ArciProcess',
    'Invoke-ArciExe', 'Get-ArciConfiguredDataDir', 'Read-ArciDataRootRecord', 'Resolve-ArciDataRoot', 'Read-ArciTomlFile', 'Get-ArciConfigValue', 'Get-ArciApiEndpoint', 'Test-ArciProxyTrust', 'Get-ArciSourceAddresses', 'Get-ArciSourceTables',
    'ConvertTo-ArciTomlString', 'Get-ArciTomlString', 'Set-ArciTomlString', 'Get-ArciSiteNameProblem', 'Get-ArciTimeServerProblem',
    'Get-ArciTimePeerList', 'Read-ArciAdminAddress',
    'ConvertTo-ArciNetwork', 'Test-ArciAddressSpec', 'Test-ArciAddressInNetwork', 'Get-ArciForeignRdpSession',
    'Get-ArciNetworkFacts', 'Get-ArciLocalAddresses', 'Select-ArciServerAddress', 'Get-ArciCollectorBind', 'Test-ArciBindAddress',
    'Get-ArciInstallMarkerName', 'Get-ArciProgramFolderState',
    'ConvertTo-ArciIsoDate', 'Get-ArciJsonField', 'Read-ArciLicenseFile', 'Read-ArciLicenseStatus', 'Test-ArciMaintenanceCovers',
    'Get-ArciProductReleaseDate', 'Get-ArciSiteToday', 'Get-ArciSiteTimestamp', 'Get-ArciOldestVaultDay', 'Get-ArciDaysBetween', 'Get-ArciWheelTestHelpers',
    'New-ArciAce', 'Get-ArciAclPlan', 'Get-ArciAceFlags',
    'Get-ArciAccessControl', 'Set-ArciAccessControl', 'Set-ArciAclEntry', 'Set-ArciVaultAudit',
    'Get-ArciDefaultSourceNetworks', 'ConvertTo-ArciSourceNetwork', 'New-ArciAdminPolicy', 'Read-ArciAdminPolicy',
    'ConvertTo-ArciSyslogSender', 'Get-ArciSyslogSenderProblem', 'Get-ArciOutboundProblem', 'Test-ArciHostName',
    'Get-ArciEventLogApi', 'Register-ArciEventSource', 'Unregister-ArciEventSource', 'Write-ArciEvent', 'Test-ArciProgramConfig', 'Test-ArciPathBelow',
    'Get-ArciExecutableRecords', 'Get-ArciUnsafeWriters', 'Get-ArciPathAclFacts', 'Read-ArciInstallerHashes', 'Get-ArciZamaneJarPlan', 'Copy-ArciApprovedJar', 'Merge-ArciJarsDocument',
    'Get-ArciDriveFacts', 'Get-ArciDiskBusType', 'Select-ArciDataRoot', 'Get-ArciDataDriveChecks',
    'Get-ArciOsSupport', 'Get-ArciPowerSchemeGuid', 'ConvertFrom-ArciPowercfgQuery',
    'Test-ArciHttpsHealth'
)
