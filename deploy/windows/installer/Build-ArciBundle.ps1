<#
.SYNOPSIS
    ARCILOGGER çevrimdışı kurulum paketini oluşturur [builds the offline installation bundle].

.DESCRIPTION
    Runs on a Windows machine WITH internet access (the admin PC or a CI runner), never on
    the ARCILOGGER server. Needs: uv, Node.js 22 with npm 11.21 or newer, Windows tar.exe; optional
    git (commit id in the manifest) and Inno Setup 6 (single setup .exe).

      1. Downloads the third-party components listed in components.json and checks their SHA-256
         (and Caddy's vendor SHA-512); a mismatch stops the build. A component without a verified
         checksum is refused unless -ComponentSha256 gives one checked on the vendor's site.
         Their licence texts (tag-pinned, licence_sha256 in components.json) go into licenses\
         and are installed next to the programs. TimescaleDB (optional, partly under the
         Timescale License) is only added with -IncludeTimescaleDB.
      2. Builds the ARCILOGGER wheels (uv build) and downloads every dependency wheel pinned in
         uv.lock for Windows x64 / CPython 3.12, hash-checked, with the Python that is shipped.
      3. Builds the web interface (npm ci, npm run build; that build fails if it would contain a
         source map or the developer UI kit, web/vite.config.ts).
      4. Assembles dist\ARCILOGGER-<version>\ with bundle-manifest.json (every file with SHA-256)
         and SHA256SUMS, and prints the bundle fingerprint (SHA-256 of SHA256SUMS): compare it on
         the server before installing.
      5. Compiles installer\ARCILOGGER.iss with ISCC.exe when Inno Setup is available.

    Nothing downloaded here is committed to the repository.

.PARAMETER ComponentSha256
    Checksums the operator verified on a vendor site for components whose sha256 is null in
    components.json, e.g. @{ postgresql = '<64 hex>' }. Recorded in the manifest as operator-supplied.

.PARAMETER ComponentUrl
    Download URL for components without one in components.json, e.g. @{ postgresql = 'https://...' }.

.PARAMETER IncludeTimescaleDB
    Also put the TimescaleDB Windows build into operator-installers\. Off by default: its -tsl
    library is under the Timescale License (not OSI-approved), see docs/adr/0007.

.PARAMETER RequireInno
    Stop when Inno Setup's ISCC.exe is not found instead of building the folder bundle only (the
    release job of .github/workflows/installer.yml, which must publish the setup .exe).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\windows\installer\Build-ArciBundle.ps1
.EXAMPLE
    .\Build-ArciBundle.ps1 -SkipOperatorInstallers -NoInno
#>
[CmdletBinding()]
param(
    # Default: the repository this script is in (set below - Windows PowerShell 5.1 leaves
    # $PSScriptRoot empty while it evaluates parameter defaults of "powershell -File" scripts).
    [string]$RepoRoot,
    [string]$OutputRoot,
    [string]$CacheDir,
    [hashtable]$ComponentSha256 = @{},
    [hashtable]$ComponentUrl = @{},
    [switch]$SkipOperatorInstallers,
    [switch]$IncludeTimescaleDB,
    [switch]$NoInno,
    [switch]$RequireInno,
    [string]$IsccPath,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
Import-Module (Join-Path $PSScriptRoot 'ArciInstall.psm1') -Force
Initialize-ArciModulePath
if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path }
if ($NoInno -and $RequireInno) { throw '-NoInno ve -RequireInno birlikte kullanılamaz [use only one].' }

if (-not (Test-ArciWindows)) {
    throw 'Paket Windows üzerinde oluşturulur [build on Windows]: bağımlılık seçimi hedef Python ile yapılır.'
}
# Windows PowerShell 5.1 does not offer TLS 1.2 by default on every system.
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor [System.Net.SecurityProtocolType]::Tls12

if (-not $OutputRoot) { $OutputRoot = Join-Path $RepoRoot 'dist' }
if (-not $CacheDir) { $CacheDir = Join-Path $OutputRoot 'cache' }
$Windows = Join-Path $RepoRoot 'deploy\windows'
$ComponentsFile = Join-Path $PSScriptRoot 'components.json'

function Assert-Tool([string]$Name, [string]$Hint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) { throw ("{0} bulunamadı [not found]. {1}" -f $Name, $Hint) }
}

function Get-ProductVersion {
    $init = [System.IO.File]::ReadAllText((Join-Path $RepoRoot 'core\src\arcilogger_core\__init__.py'))
    $match = [regex]::Match($init, '(?m)^__version__\s*=\s*"(?<v>[^"]+)"')
    if (-not $match.Success) { throw '__version__ okunamadı [not found].' }
    $match.Groups['v'].Value
}

function Get-ProductReleaseDate {
    <#
    RELEASE_DATE of core\src\arcilogger_core\__init__.py, bumped with __version__ at each release:
    the line RELEASE_DATE: Final = "YYYY-MM-DD", read with
    (?m)^RELEASE_DATE: Final = "(?<d>\d{4}-\d{2}-\d{2})" (Get-ArciProductReleaseDate); missing:
    the build stops. It goes into bundle-manifest.json as release_date, the day a licence's
    maintenance must cover; built_at (this machine's clock) stays informational, so rebuilding a
    version later never changes which licences cover it.
    #>
    $init = [System.IO.File]::ReadAllText((Join-Path $RepoRoot 'core\src\arcilogger_core\__init__.py'))
    Get-ArciProductReleaseDate -InitText $init
}

function Get-VerifiedComponent($Component) {
    <# Download (or reuse from the cache) and verify one component; returns the local file. #>
    $resolved = Resolve-ArciComponentSource -Component $Component -UrlOverride $ComponentUrl -Sha256Override $ComponentSha256
    $name = $resolved.Name
    $url = $resolved.Url
    $sha256 = $resolved.Sha256
    $source = $resolved.Source
    $fileName = $resolved.FileName
    $target = Join-Path $CacheDir $fileName
    if ((Test-Path -LiteralPath $target) -and (Get-ArciFileSha256 -Path $target) -eq $sha256) {
        Write-ArciInfo ("önbellekten [cached]: {0}" -f $fileName)
    }
    else {
        $partial = $target + '.part'
        Write-ArciInfo ("indiriliyor [downloading]: {0}" -f $url)
        $previous = $ProgressPreference
        $ProgressPreference = 'SilentlyContinue'   # the progress bar makes Invoke-WebRequest very slow in 5.1
        try { Invoke-WebRequest -Uri $url -OutFile $partial -UseBasicParsing } finally { $ProgressPreference = $previous }
        $actual = Get-ArciFileSha256 -Path $partial
        if ($actual -ne $sha256) {
            Remove-Item -LiteralPath $partial -Force
            throw ("{0}: SHA-256 UYUŞMUYOR [MISMATCH] beklenen [expected] {1}, gelen [actual] {2}. Dosya kullanılmadı." -f $name, $sha256, $actual)
        }
        Move-Item -LiteralPath $partial -Destination $target -Force
    }
    if ($Component.PSObject.Properties.Name -contains 'vendor_sha512' -and $Component.vendor_sha512) {
        $sha512 = (Get-FileHash -LiteralPath $target -Algorithm SHA512).Hash.ToLowerInvariant()
        if ($sha512 -ne $Component.vendor_sha512) { throw ("{0}: üreticinin SHA-512 değeri tutmuyor [vendor SHA-512 mismatch]" -f $name) }
    }
    [pscustomobject]@{ Name = $name; File = $target; FileName = $fileName; Sha256 = $sha256; Source = $source; Url = $url }
}

function Get-VerifiedLicence($Component) {
    <#
    The licence text of a component that goes into the bundle, downloaded from its tag-pinned
    licence_url and checked against licence_sha256. Bundled programs must have one: MIT and
    Apache-2.0 require the licence text to travel with every copy.
    #>
    $name = [string]$Component.name
    $hasSum = $Component.PSObject.Properties.Name -contains 'licence_sha256' -and $Component.licence_sha256
    if (-not $hasSum) {
        if ($Component.role -eq 'bundled') { throw ("{0}: licence_sha256 yok [no verified licence text]" -f $name) }
        return $null
    }
    $url = [string]$Component.licence_url
    if ($url -notmatch '^https://') { throw ("{0}: lisans adresi https değil [licence URL must be https]: {1}" -f $name, $url) }
    $fileName = '{0}-{1}' -f $name, [System.IO.Path]::GetFileName(([System.Uri]$url).AbsolutePath)
    if ([System.IO.Path]::GetExtension($fileName) -eq '') { $fileName += '.txt' }
    $target = Join-Path $CacheDir ('licence-' + $fileName)
    if (-not ((Test-Path -LiteralPath $target) -and (Get-ArciFileSha256 -Path $target) -eq $Component.licence_sha256)) {
        $partial = $target + '.part'
        $previous = $ProgressPreference
        $ProgressPreference = 'SilentlyContinue'
        try { Invoke-WebRequest -Uri $url -OutFile $partial -UseBasicParsing } finally { $ProgressPreference = $previous }
        $actual = Get-ArciFileSha256 -Path $partial
        if ($actual -ne $Component.licence_sha256) {
            Remove-Item -LiteralPath $partial -Force
            throw ("{0}: lisans metni SHA-256 UYUŞMUYOR [licence text MISMATCH] beklenen {1}, gelen {2}" -f $name, $Component.licence_sha256, $actual)
        }
        Move-Item -LiteralPath $partial -Destination $target -Force
    }
    [pscustomobject]@{ File = $target; FileName = $fileName }
}

function Copy-Tree([string]$Source, [string]$Destination, [string]$Filter = '*') {
    [void](New-Item -ItemType Directory -Path $Destination -Force)
    Get-ChildItem -LiteralPath $Source -Filter $Filter -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Destination $_.Name) -Force
    }
}

# --- prerequisites ------------------------------------------------------------------------------------

Write-ArciStep 'Ön koşullar [prerequisites]'
Assert-Tool 'uv' 'https://docs.astral.sh/uv/ adresindeki Windows kurulumunu izleyin.'
Assert-Tool 'npm' 'Node.js 22 kurun; ardından: npm install --global npm@11.21.0'
$tar = Join-Path $env:SystemRoot 'System32\tar.exe'
if (-not (Test-Path -LiteralPath $tar)) { throw ("tar.exe yok [missing]: {0}" -f $tar) }
$npmVersion = [version]((& npm --version).Trim())
if ($npmVersion -lt [version]'11.21.0') { throw ("npm {0} çok eski; npm install --global npm@11.21.0 [npm 11.21+ required, see ADR-0004]" -f $npmVersion) }
$uvVersion = (& uv --version).Trim()
$nodeVersion = (& node --version).Trim()

$version = Get-ProductVersion
$releaseDate = Get-ProductReleaseDate
$stage = Join-Path $OutputRoot ("ARCILOGGER-{0}" -f $version)
$work = Join-Path $OutputRoot ("work-{0}" -f $version)
Write-ArciInfo ("Sürüm [version] {0}, yayın tarihi [release date] {1} -> {2}" -f $version, $releaseDate, $stage)
foreach ($folder in @($stage, $work)) {
    if (Test-Path -LiteralPath $folder) {
        if (-not $Force -and $folder -eq $stage) { throw ("{0} zaten var; -Force ile yeniden oluşturun [exists, use -Force]." -f $folder) }
        Remove-Item -LiteralPath $folder -Recurse -Force
    }
    [void](New-Item -ItemType Directory -Path $folder -Force)
}
[void](New-Item -ItemType Directory -Path $CacheDir -Force)

# --- third-party components -----------------------------------------------------------------------------

Write-ArciStep 'Üçüncü taraf bileşenler [third-party components]'
$catalog = [System.IO.File]::ReadAllText($ComponentsFile, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
$manifestComponents = New-Object System.Collections.Generic.List[object]
$skipped = New-Object System.Collections.Generic.List[string]
$verified = @{}
$licences = @{}
[void](New-Item -ItemType Directory -Path (Join-Path $stage 'licenses') -Force)
foreach ($component in $catalog.components) {
    if ($component.name -eq 'timescaledb' -and -not $IncludeTimescaleDB) {
        $skipped.Add('timescaledb: -IncludeTimescaleDB verilmedi (lisans kararı bekleniyor) [not included, licence decision pending]')
        continue
    }
    if ($component.role -eq 'operator') {
        if ($SkipOperatorInstallers) { $skipped.Add(("{0}: -SkipOperatorInstallers" -f $component.name)); continue }
        $hasUrl = $component.url -or $ComponentUrl.ContainsKey($component.name)
        $hasSum = $component.sha256 -or $ComponentSha256.ContainsKey($component.name)
        if (-not $hasUrl -or -not $hasSum) {
            $skipped.Add(("{0}: doğrulanmış indirme yok, operatör kurar [no verified download, operator installs it]" -f $component.name))
            continue
        }
    }
    $result = Get-VerifiedComponent $component
    $verified[$component.name] = $result
    $folder = if ($component.role -eq 'bundled') { 'thirdparty' } else { 'operator-installers' }
    $relative = '{0}/{1}/{2}' -f $folder, $component.name, $result.FileName
    $destination = Join-Path $stage ($relative.Replace('/', '\'))
    [void](New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force)
    Copy-Item -LiteralPath $result.File -Destination $destination -Force
    $licence = Get-VerifiedLicence $component
    $licencePath = $null
    if ($licence) {
        $licencePath = 'licenses/' + $licence.FileName
        Copy-Item -LiteralPath $licence.File -Destination (Join-Path $stage ($licencePath.Replace('/', '\'))) -Force
        $licences[$component.name] = $licencePath
    }
    $manifestComponents.Add([pscustomobject]@{
            name          = $component.name
            version       = $component.version
            role          = $component.role
            licence       = $component.licence
            url           = $result.Url
            sha256        = $result.Sha256
            sha256_source = $result.Source
            path          = $relative
            licence_file  = $licencePath
        })
    Write-ArciInfo ("{0} {1}: {2}" -f $component.name, $component.version, $result.Sha256)
}
foreach ($required in @('python', 'winsw', 'caddy')) {
    if (-not $verified.ContainsKey($required)) { throw ("Gerekli bileşen eksik [required component missing]: {0}" -f $required) }
}
foreach ($line in $skipped) { Write-ArciWarn $line }

# --- Python wheels -------------------------------------------------------------------------------------------

Write-ArciStep 'Python paketleri [wheels]'
Invoke-ArciExe -FilePath $tar -ArgumentList @('-xzf', $verified['python'].File, '-C', $work)
$buildPython = Join-Path $work 'python\python.exe'
$pythonVersion = (Invoke-ArciProcess -FilePath $buildPython -ArgumentList @('-I', '-c', 'import platform; print(platform.python_version())')).StdOut.Trim()
if ($pythonVersion -notmatch '^3\.12\.') { throw ("Python 3.12 bekleniyordu [expected]: {0}" -f $pythonVersion) }

$wheels = Join-Path $stage 'wheels'
$appWheels = Join-Path $wheels 'arcilogger'
$depWheels = Join-Path $wheels 'deps'
$requirements = Join-Path $wheels 'requirements.txt'
[void](New-Item -ItemType Directory -Path $appWheels -Force)
[void](New-Item -ItemType Directory -Path $depWheels -Force)
Push-Location $RepoRoot
try {
    Invoke-ArciExe -FilePath 'uv' -ArgumentList @('build', '--all-packages', '--wheel', '--out-dir', $appWheels)
    # The licence test helpers (arcilogger_core/license/testing.py with its status override,
    # arcilogger_db/pytest_plugin.py) never ship: the wheels' exclude rules keep them out, and
    # the built files are checked here so that a changed build rule cannot ship them.
    $shipped = Get-ArciWheelTestHelpers -WheelDirectory $appWheels
    if ($shipped.Count -gt 0) { throw ('Test yardımcıları paketlenmemeli [test helpers must not be in the bundle wheels]: ' + ($shipped -join '; ')) }
    # Exactly the versions and hashes of uv.lock, without the workspace packages and dev tools.
    Invoke-ArciExe -FilePath 'uv' -ArgumentList @('export', '--frozen', '--no-dev', '--all-packages', '--no-emit-workspace', '--no-header', '--quiet', '--format', 'requirements-txt', '--output-file', $requirements)
}
finally { Pop-Location }
# Run by the shipped Windows Python, so environment markers are evaluated for the server.
Invoke-ArciExe -FilePath $buildPython -ArgumentList @(
    '-I', '-m', 'pip', '--isolated', '--no-input', '--disable-pip-version-check', 'download',
    '--only-binary=:all:', '--platform', 'win_amd64', '--python-version', '3.12', '--implementation', 'cp', '--abi', 'cp312',
    '--require-hashes', '--no-deps', '--dest', $depWheels, '-r', $requirements)

# --- web interface -------------------------------------------------------------------------------------------

Write-ArciStep 'Web arayüzü [web interface]'
Push-Location (Join-Path $RepoRoot 'web')
try {
    Invoke-ArciExe -FilePath 'npm' -ArgumentList @('ci')
    Invoke-ArciExe -FilePath 'npm' -ArgumentList @('run', 'build')
}
finally { Pop-Location }
$webDist = Join-Path $RepoRoot 'web\dist'
if (-not (Test-Path -LiteralPath (Join-Path $webDist 'index.html'))) { throw 'web\dist\index.html yok [missing].' }
Copy-Item -LiteralPath $webDist -Destination (Join-Path $stage 'web') -Recurse -Force

# --- scripts, definitions, documents -------------------------------------------------------------------------

Write-ArciStep 'Betikler ve tanımlar [scripts and definitions]'
Copy-Tree (Join-Path $Windows 'installer') (Join-Path $stage 'installer') '*.ps*1'
Copy-Item -LiteralPath $ComponentsFile -Destination (Join-Path $stage 'components.json') -Force
Copy-Tree (Join-Path $Windows 'scripts') (Join-Path $stage 'scripts') '*.ps1'
Copy-Tree (Join-Path $Windows 'tasks') (Join-Path $stage 'tasks') '*.xml'
Copy-Tree (Join-Path $Windows 'services') (Join-Path $stage 'services') '*.xml'
Copy-Tree (Join-Path $Windows 'caddy') (Join-Path $stage 'caddy') 'Caddyfile'
[void](New-Item -ItemType Directory -Path (Join-Path $stage 'config') -Force)
Copy-Item -LiteralPath (Join-Path $Windows 'arcilogger.example.toml') -Destination (Join-Path $stage 'config\arcilogger.example.toml') -Force
# Every Markdown document of docs\ with its subfolders (adr\, legal\): the operator documents link
# to each other (and to the ADRs and legal drafts), so the bundle carries the whole set and every
# link works offline (tests/test_docs_consistency.py).
$docsSource = (Resolve-Path -LiteralPath (Join-Path $RepoRoot 'docs')).ProviderPath.TrimEnd('\')
$docsStage = Join-Path $stage 'docs'
[void](New-Item -ItemType Directory -Path $docsStage -Force)
Get-ChildItem -LiteralPath $docsSource -Recurse -File -Filter '*.md' | Sort-Object FullName | ForEach-Object {
    $target = Join-Path $docsStage $_.FullName.Substring($docsSource.Length + 1)
    [void](New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force)
    Copy-Item -LiteralPath $_.FullName -Destination $target -Force
}
foreach ($doc in @('KURULUM.md', 'INSTALL.md', 'ILK-ADIMLAR.md', 'FIRST-STEPS.md', 'DEVICE-SETUP.md', 'kamusm.md', 'DENETCI-KILAVUZU.md', 'LISANS.md', 'LICENSING.md')) {
    if (-not (Test-Path -LiteralPath (Join-Path $docsStage $doc))) { throw ("docs\{0} yok [missing]." -f $doc) }
}

$notices = New-Object System.Text.StringBuilder
[void]$notices.AppendLine('ARCILOGGER - third-party components / üçüncü taraf bileşenler')
[void]$notices.AppendLine('')
foreach ($component in $catalog.components) {
    [void]$notices.AppendLine(("{0} {1} ({2})" -f $component.name, $component.version, $component.role))
    [void]$notices.AppendLine(("  licence: {0}" -f $component.licence))
    [void]$notices.AppendLine(("  licence text: {0}" -f $component.licence_url))
    if ($licences.ContainsKey($component.name)) {
        [void]$notices.AppendLine(("  licence file in this bundle / installed: {0}" -f $licences[$component.name]))
    }
    [void]$notices.AppendLine(("  source: {0}" -f $component.download_page))
    [void]$notices.AppendLine('')
}
Write-ArciUtf8File -Path (Join-Path $stage 'THIRD-PARTY-NOTICES.txt') -Text $notices.ToString().Replace("`r`n", "`n")

$readme = @(
    "ARCILOGGER $version - çevrimdışı kurulum paketi / offline installation bundle",
    '',
    'TR: Paketi sunucuya kopyalayın ve yönetici PowerShell penceresinde çalıştırın:',
    '    powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1 -DryRun',
    '    powershell -ExecutionPolicy Bypass -File .\installer\Install-Arcilogger.ps1',
    '    Ayrıntılar: docs\KURULUM.md',
    'EN: Copy the bundle to the server and run the same commands as Administrator; see docs\INSTALL.md.',
    '',
    'Paket parmak izi / bundle fingerprint = SHA-256 of SHA256SUMS (printed by the build).'
) -join "`n"
Write-ArciUtf8File -Path (Join-Path $stage 'README.txt') -Text ($readme + "`n")

# --- manifest and checksums --------------------------------------------------------------------------------

Write-ArciStep 'Bildirim ve özetler [manifest and checksums]'
$gitCommit = $null
$gitDirty = $null
if (Get-Command git -ErrorAction SilentlyContinue) {
    $commit = Invoke-ArciProcess -FilePath 'git' -ArgumentList @('-C', $RepoRoot, 'rev-parse', 'HEAD') -AllowFailure
    if ($commit.ExitCode -eq 0) { $gitCommit = $commit.StdOut.Trim() }
    $status = Invoke-ArciProcess -FilePath 'git' -ArgumentList @('-C', $RepoRoot, 'status', '--porcelain') -AllowFailure
    if ($status.ExitCode -eq 0) { $gitDirty = [bool]$status.StdOut.Trim() }
}
$stageRoot = (Resolve-Path -LiteralPath $stage).ProviderPath.TrimEnd('\')
function Get-StageFiles {
    Get-ChildItem -LiteralPath $stageRoot -Recurse -File | Sort-Object FullName | ForEach-Object {
        $relative = $_.FullName.Substring($stageRoot.Length + 1).Replace('\', '/')
        [pscustomobject]@{ path = $relative; sha256 = (Get-ArciFileSha256 -Path $_.FullName); size = $_.Length }
    }
}
$files = @(Get-StageFiles | Where-Object { $_.path -ne 'bundle-manifest.json' -and $_.path -ne 'SHA256SUMS' })
$manifest = [pscustomobject]@{
    product      = 'ARCILOGGER'
    version      = $version
    # The licence's maintenance must cover this day (the installer's preflight "license").
    release_date = $releaseDate
    built_at     = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    git_commit   = $gitCommit
    git_dirty    = $gitDirty
    builder      = [pscustomobject]@{ uv = $uvVersion; node = $nodeVersion; npm = $npmVersion.ToString(); powershell = $PSVersionTable.PSVersion.ToString() }
    python       = [pscustomobject]@{ version = $pythonVersion; platform = 'win_amd64'; abi = 'cp312' }
    components   = $manifestComponents.ToArray()
    skipped      = $skipped.ToArray()
    files        = $files
}
Write-ArciUtf8File -Path (Join-Path $stage 'bundle-manifest.json') -Text (($manifest | ConvertTo-Json -Depth 6).Replace("`r`n", "`n") + "`n")
$sums = @(Get-StageFiles | Where-Object { $_.path -ne 'SHA256SUMS' } | Sort-Object path | ForEach-Object { '{0}  {1}' -f $_.sha256, $_.path })
Write-ArciUtf8File -Path (Join-Path $stage 'SHA256SUMS') -Text (($sums -join "`n") + "`n")
$check = Test-ArciBundle -BundleRoot $stage
if (-not $check.Ok) { throw ('Paket kendi denetiminden geçmedi [bundle self-check failed]: ' + ($check.Errors -join '; ')) }
$fingerprintFile = Join-Path $OutputRoot ("ARCILOGGER-{0}.fingerprint.txt" -f $version)
Write-ArciUtf8File -Path $fingerprintFile -Text ("{0}  ARCILOGGER-{1}/SHA256SUMS`n" -f $check.Fingerprint, $version)
Remove-Item -LiteralPath $work -Recurse -Force

# --- Inno Setup -----------------------------------------------------------------------------------------------

$setupExe = $null
if (-not $NoInno) {
    $candidates = @($IsccPath, (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'), (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')) | Where-Object { $_ }
    $iscc = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($iscc) {
        Write-ArciStep ("Inno Setup: {0}" -f $iscc)
        Invoke-ArciExe -FilePath $iscc -ArgumentList @(("/DBundleDir=" + $stageRoot), ("/DAppVersion=" + $version), ("/O" + $OutputRoot), (Join-Path $PSScriptRoot 'ARCILOGGER.iss'))
        $setupExe = Join-Path $OutputRoot ("ARCILOGGER-Setup-{0}.exe" -f $version)
        if ($RequireInno -and -not (Test-Path -LiteralPath $setupExe)) { throw ("Kurulum dosyası oluşmadı [setup .exe missing]: {0}" -f $setupExe) }
    }
    elseif ($RequireInno) {
        throw ('ISCC.exe bulunamadı [Inno Setup not found] (-RequireInno): {0}' -f ($candidates -join ', '))
    }
    else {
        Write-ArciWarn 'ISCC.exe bulunamadı; yalnızca klasör paketi oluşturuldu [Inno Setup not found, folder bundle only].'
    }
}

Write-Host ''
Write-Host ("ARCILOGGER {0} paketi hazır [bundle ready]: {1}" -f $version, $stage) -ForegroundColor Green
Write-Host ("  Yayın tarihi [release date]: {0}" -f $releaseDate)
Write-Host ("  Dosya [files]: {0}" -f $check.Files)
Write-Host ("  Paket parmak izi [bundle fingerprint] (SHA-256 of SHA256SUMS): {0}" -f $check.Fingerprint)
Write-Host '  Sunucuda kurulumdan önce aynı değeri görmelisiniz [the installer prints the same value].'
if ($setupExe -and (Test-Path -LiteralPath $setupExe)) {
    Write-Host ("  Kurulum dosyası [setup]: {0}" -f $setupExe)
    Write-Host ("  SHA-256: {0}" -f (Get-ArciFileSha256 -Path $setupExe))
}
