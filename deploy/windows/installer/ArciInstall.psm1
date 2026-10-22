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

$script:ArciServiceNames = @('ArciCollector', 'ArciIngest', 'ArciApi', 'ArciWorker', 'ArciWeb')

$script:WellKnownSid = @{
    System         = 'S-1-5-18'
    LocalService   = 'S-1-5-19'
    Administrators = 'S-1-5-32-544'
    Everyone       = 'S-1-1-0'
    Users          = 'S-1-5-32-545'
}

function Get-ArciServiceNames {
    <# The ARCILOGGER Windows services in start order (collector first: it receives the logs). #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param()
    , $script:ArciServiceNames
}

function Get-ArciWellKnownSid {
    [CmdletBinding()]
    [OutputType([string])]
    param([Parameter(Mandatory = $true)][ValidateSet('System', 'LocalService', 'Administrators', 'Everyone', 'Users')][string]$Name)
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
    <# Sender addresses and networks from the [[sources]] tables (for the firewall). #>
    [CmdletBinding()]
    [OutputType([string[]])]
    param([Parameter(Mandatory = $true)]$Config)
    $addresses = New-Object System.Collections.Generic.List[string]
    if ($Config.PSObject.Properties.Name -contains 'sources') {
        foreach ($source in @($Config.sources)) {
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
        ("#[[sources]]", as in the example configuration) is not one. An address written in
        another form (a dotted key, an inline table, a multi-line string) is not seen: Tables is
        then larger than the number of Addresses.
    #>
    [CmdletBinding()]
    param([AllowEmptyString()][string]$Text)
    $tables = [regex]::Matches($Text, '(?ms)^[ \t]*\[\[[ \t]*sources[ \t]*\]\][ \t]*(?:#[^\r\n]*)?\r?$(?<body>.*?)(?=^[ \t]*\[|\z)')
    $addresses = New-Object System.Collections.Generic.List[string]
    foreach ($table in $tables) {
        $address = [regex]::Match($table.Groups['body'].Value, '(?m)^[ \t]*address[ \t]*=[ \t]*(?:"(?<v>[^"\r\n]*)"|''(?<v>[^''\r\n]*)'')[ \t]*(?:#[^\r\n]*)?\r?$')
        # An empty address is skipped like Get-ArciSourceAddresses does.
        if ($address.Success -and $address.Groups['v'].Value) { $addresses.Add($address.Groups['v'].Value) }
    }
    [pscustomobject]@{ Tables = $tables.Count; Addresses = $addresses.ToArray() }
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
    if ($null -ne $zone) { $local = [System.TimeZoneInfo]::ConvertTimeFromUtc($utc, $zone) }
    else { $local = $utc.AddHours(3) }
    $local.ToString('yyyy-MM-dd', [System.Globalization.CultureInfo]::InvariantCulture)
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
        [switch]$FoldersOnly
    )
    [pscustomobject]@{
        Sid = $Sid; Who = $Who; Rights = $Rights; Inherit = (-not $ThisFolderOnly)
        FoldersOnly = [bool]($FoldersOnly -and -not $ThisFolderOnly)
    }
}

function Get-ArciAclPlan {
    <#
    .SYNOPSIS
        Who may do what in the ARCILOGGER folders (least privilege, docs/adr/0007).
    .DESCRIPTION
        Returns one entry per path: Path, Kind (Directory/File), Protect (inheritance from the
        parent disabled), Aces and Audit. Pure data, no system calls: the dry run prints it and the
        tests check it on any platform.

        - Collector (the only network-facing Python service): reads and writes the spool but has no
          Delete right there, so it cannot delete or rename raw evidence (it can write into a
          file it opens; the nightly seal is what proves the content). It has no access to the
          audit spool, to the sealing/replica/verify/clock state, to the vault or to secret.key.
        - Scheduled tasks (LOCAL SERVICE, see deploy/windows/tasks): seal (read + delete closed
          spool days, write and lock the vault), retention (delete expired vault days), replica,
          verify and clock check; only they write state\seal, state\replica, state\verify and
          state\clock (core seal.py trusts state\seal\<day>.json when it decides a day is sealed).
          The database backup (Backup task, ADR-0009) writes and prunes backups\scheduled, which
          no service can read; backups and backups\db stay administrators-only.
          LOCAL SERVICE is a shared Windows account: other LocalService services hold these
          rights too (known limitation, owner decision: docs/adr/0007 section 8).
        - API: appends to the audit spool (spool\_audit, no delete) and writes exports; reads
          the spool (raw lines of days not sealed yet: GET /raw, the 5651 report, the sealing
          calendar; read only - no write, no delete), the vault and the state.
        - Ingest: reads the spool and the vault (catching up on days sealed while the database
          was down, ADR-0002), writes its own state.
        - Worker: reads the vault and the job state; may only list the spool and its source
          folders (inherited by folders, not by files), so it finds the oldest spool day by file
          name for the licence decision like the API (licensing contract 3.4) without reading
          raw logs.
        - Web (Caddy): only its own data folder (internal CA private key) and the logs.
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
    $collector = Get-ArciServiceSid 'ArciCollector'
    $ingest = Get-ArciServiceSid 'ArciIngest'
    $api = Get-ArciServiceSid 'ArciApi'
    $worker = Get-ArciServiceSid 'ArciWorker'
    $web = Get-ArciServiceSid 'ArciWeb'
    $read = 'ReadAndExecute'
    $full = 'FullControl'
    $modify = 'Modify'
    $appendOnly = 'ReadAndExecute, Write'
    $listOnly = 'ListDirectory, ReadAttributes, Traverse'

    function New-Entry([string]$Path, [string]$Kind, [bool]$Protect, [object[]]$Aces, [bool]$Audit = $false) {
        [pscustomobject]@{ Path = $Path; Kind = $Kind; Protect = $Protect; Aces = @($Aces); Audit = $Audit }
    }
    $owners = @(
        (New-ArciAce -Sid $system -Who 'SYSTEM' -Rights $full),
        (New-ArciAce -Sid $admins -Who 'Administrators' -Rights $full)
    )
    $p = [System.IO.Path]::DirectorySeparatorChar
    $entries = @(
        # Program files: read for every ARCILOGGER account (inherits the Program Files defaults too).
        (New-Entry $InstallDir 'Directory' $false @(
                (New-ArciAce -Sid $collector -Who 'NT SERVICE\ArciCollector' -Rights $read),
                (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights $read),
                (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read),
                (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $read),
                (New-ArciAce -Sid $web -Who 'NT SERVICE\ArciWeb' -Rights $read),
                (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $read))),
        # Configuration, Kamu SM roots, Zamane jar: read-only for the services and tasks.
        (New-Entry $ProgramDataDir 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $collector -Who 'NT SERVICE\ArciCollector' -Rights $read),
                    (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights $read),
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read),
                    (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $read),
                    (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights $read)))),
        # The encryption key of the secret store: not for the collector (it never needs a secret).
        (New-Entry ($ProgramDataDir + $p + 'secret.key') 'File' $true ($owners + @(
                    (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights 'Read'),
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights 'Read'),
                    (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights 'Read'),
                    (New-ArciAce -Sid $localService -Who 'LOCAL SERVICE (tasks)' -Rights 'Read')))),
        # Caddy's data folder holds the private key of the internal CA: Caddy and administrators only.
        (New-Entry ($ProgramDataDir + $p + 'caddy') 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $web -Who 'NT SERVICE\ArciWeb' -Rights $modify)))),
        # Data root: list only (this folder), everything else is granted per sub-folder.
        (New-Entry $DataRoot 'Directory' $true ($owners + @(
                    (New-ArciAce -Sid $collector -Who 'NT SERVICE\ArciCollector' -Rights $read -ThisFolderOnly),
                    (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights $read -ThisFolderOnly),
                    (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read -ThisFolderOnly),
                    (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $read -ThisFolderOnly),
                    (New-ArciAce -Sid $web -Who 'NT SERVICE\ArciWeb' -Rights $read -ThisFolderOnly),
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
        # offsets live in the database).
        (New-Entry ($DataRoot + $p + 'state') 'Directory' $false @(
                (New-ArciAce -Sid $collector -Who 'NT SERVICE\ArciCollector' -Rights $modify),
                (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights $modify),
                (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $modify),
                (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $read),
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
        (New-Entry ($DataRoot + $p + 'exports') 'Directory' $false @(
                (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $modify),
                (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $modify))),
        (New-Entry ($DataRoot + $p + 'logs') 'Directory' $false @(
                (New-ArciAce -Sid $collector -Who 'NT SERVICE\ArciCollector' -Rights $modify),
                (New-ArciAce -Sid $ingest -Who 'NT SERVICE\ArciIngest' -Rights $modify),
                (New-ArciAce -Sid $api -Who 'NT SERVICE\ArciApi' -Rights $modify),
                (New-ArciAce -Sid $worker -Who 'NT SERVICE\ArciWorker' -Rights $modify),
                (New-ArciAce -Sid $web -Who 'NT SERVICE\ArciWeb' -Rights $modify),
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
    , $entries
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
        if ($Entry.Kind -eq 'Directory' -and $ace.Inherit -and $ace.FoldersOnly) {
            $inherit = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit
        }
        elseif ($Entry.Kind -eq 'Directory' -and $ace.Inherit) {
            $inherit = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
        }
        else {
            $inherit = [System.Security.AccessControl.InheritanceFlags]::None
        }
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule -ArgumentList $sid, $rights, $inherit, ([System.Security.AccessControl.PropagationFlags]::None), ([System.Security.AccessControl.AccessControlType]::Allow)
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
    'Invoke-ArciExe', 'Get-ArciConfiguredDataDir', 'Read-ArciTomlFile', 'Get-ArciConfigValue', 'Get-ArciApiEndpoint', 'Test-ArciProxyTrust', 'Get-ArciSourceAddresses', 'Get-ArciSourceTables',
    'ConvertTo-ArciTomlString', 'Get-ArciTomlString', 'Set-ArciTomlString', 'Get-ArciSiteNameProblem', 'Get-ArciTimeServerProblem',
    'Get-ArciTimePeerList', 'Read-ArciAdminAddress',
    'ConvertTo-ArciNetwork', 'Test-ArciAddressSpec', 'Test-ArciAddressInNetwork', 'Get-ArciForeignRdpSession',
    'Get-ArciNetworkFacts', 'Get-ArciLocalAddresses', 'Select-ArciServerAddress', 'Get-ArciCollectorBind', 'Test-ArciBindAddress',
    'Get-ArciInstallMarkerName', 'Get-ArciProgramFolderState',
    'ConvertTo-ArciIsoDate', 'Get-ArciJsonField', 'Read-ArciLicenseFile', 'Read-ArciLicenseStatus', 'Test-ArciMaintenanceCovers',
    'Get-ArciProductReleaseDate', 'Get-ArciSiteToday', 'Get-ArciOldestVaultDay', 'Get-ArciDaysBetween', 'Get-ArciWheelTestHelpers',
    'New-ArciAce', 'Get-ArciAclPlan',
    'Get-ArciAccessControl', 'Set-ArciAccessControl', 'Set-ArciAclEntry', 'Set-ArciVaultAudit',
    'Get-ArciDriveFacts', 'Get-ArciDiskBusType', 'Select-ArciDataRoot', 'Get-ArciDataDriveChecks',
    'Get-ArciOsSupport', 'Get-ArciPowerSchemeGuid', 'ConvertFrom-ArciPowercfgQuery',
    'Test-ArciHttpsHealth'
)
