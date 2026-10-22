<#
.SYNOPSIS
    ARCILOGGER Lisans Yöneticisi paketini (tek zip) oluşturur [builds the Licence Manager zip].

.DESCRIPTION
    The owner's licence tool as one zip that needs no installation: a private Python with the
    arcilogger-core wheel and its pinned dependencies, the issuer (arcilogger_license_issuer.py),
    the window (arcilogger_license_gui.py, license_manager_model.py), two launchers, BENIOKU.txt
    and the owner guide. It is never part of the product bundle and never given to customers.

    Runs on a Windows machine WITH internet access (a GitHub windows-latest runner or the owner's
    PC while it is online), the same way as deploy\windows\installer\Build-ArciBundle.ps1:

      1. Downloads the python-build-standalone component of
         deploy\windows\installer\components.json and checks its SHA-256 (a component without a
         verified checksum is refused unless -ComponentSha256 gives one).
      2. Builds the arcilogger-core wheel (uv build --package arcilogger-core --wheel; the licence
         test helpers are not in it) and downloads the win_amd64 wheels uv export pins for its
         dependencies (--only-binary=:all:, --require-hashes).
      3. Installs them into that Python with pip --no-index and copies the three tool modules
         into its site-packages.
      4. Writes "ARCILOGGER Lisans Yöneticisi.cmd" (pythonw -I -m arcilogger_license_gui),
         "Lisans Aracı (komut satırı).cmd" (python -I -m arcilogger_license_issuer) and
         BENIOKU.txt, copies README.md and the screenshots into kilavuz\.
      5. Zips everything to <OutDir>\ARCILOGGER-Lisans-Yoneticisi-<version>.zip with a .sha256
         file next to it (sha256sum format).

    Nothing downloaded here is committed to the repository. The functions above the build are
    pure (no file or network access) and are tested by dot-sourcing this script.

.PARAMETER OutDir
    Folder for the zip and its .sha256 (default: <repository>\dist).

.PARAMETER ComponentSha256
    Checksum checked on the vendor site for the python component, e.g. @{ python = '<64 hex>' };
    replaces the one of components.json (as in Build-ArciBundle.ps1).

.PARAMETER ComponentUrl
    Download URL for the python component, e.g. @{ python = 'https://...' }.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\license-issuer\Build-LicenseManager.ps1
#>
[CmdletBinding()]
param(
    # Default: the repository this script is in (set in the build - Windows PowerShell 5.1 leaves
    # $PSScriptRoot empty while it evaluates parameter defaults of "powershell -File" scripts).
    [string]$RepoRoot,
    [string]$OutDir,
    [string]$CacheDir,
    [hashtable]$ComponentSha256 = @{},
    [hashtable]$ComponentUrl = @{},
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

# --- pure helpers -------------------------------------------------------------------------------

$script:LmToolFiles = @('arcilogger_license_issuer.py', 'arcilogger_license_gui.py', 'license_manager_model.py')
# The names the owner guide uses (Turkish letters). ZipFile.CreateFromDirectory stores a non-ASCII
# entry name as UTF-8 with the zip's language encoding flag (bit 11), which Windows Explorer's
# "Extract All" and Expand-Archive both read; the CI unzip checks that both launchers are there.
$script:LmGuiLauncherName = 'ARCILOGGER Lisans Yöneticisi.cmd'
$script:LmCliLauncherName = 'Lisans Aracı (komut satırı).cmd'

function Get-LmProductVersion {
    <# __version__ of core\src\arcilogger_core\__init__.py (the product version). #>
    param([Parameter(Mandatory = $true)][string]$InitText)
    $match = [regex]::Match($InitText, '(?m)^__version__\s*=\s*"(?<v>[0-9A-Za-z.+-]+)"')
    if (-not $match.Success) { throw '__version__ okunamadı [not found].' }
    $match.Groups['v'].Value
}

function Get-LmPackageName {
    <# The zip's name without extension (also its top folder). #>
    param([Parameter(Mandatory = $true)][string]$Version)
    if ($Version -notmatch '^[0-9A-Za-z][0-9A-Za-z.+-]*$') { throw ("geçersiz sürüm [invalid version]: {0}" -f $Version) }
    'ARCILOGGER-Lisans-Yoneticisi-{0}' -f $Version
}

function Get-LmGuiLauncher {
    <# The window, without a console (pythonw), isolated (-I: no PYTHON* variable, no user site). #>
    ("@echo off`r`n" +
        "rem ARCILOGGER Lisans Yoneticisi (offline): the window, without a console`r`n" +
        "start `"`" `"%~dp0python\pythonw.exe`" -I -m arcilogger_license_gui`r`n")
}

function Get-LmCliLauncher {
    <# The command line issuer with every argument passed on; its exit code is the batch's. #>
    ("@echo off`r`n" +
        "rem ARCILOGGER licence issuer, command line: every argument is passed on`r`n" +
        "`"%~dp0python\python.exe`" -I -m arcilogger_license_issuer %*`r`n")
}

function Format-LmSha256Line {
    <# "<64 hex>  <file name>" + LF, the format of sha256sum. #>
    param([Parameter(Mandatory = $true)][string]$Hash, [Parameter(Mandatory = $true)][string]$FileName)
    $lower = $Hash.ToLowerInvariant()
    if ($lower -notmatch '^[0-9a-f]{64}$') { throw ("geçersiz SHA-256 [invalid]: {0}" -f $Hash) }
    ('{0}  {1}' -f $lower, $FileName) + "`n"
}

function Get-LmReadme {
    <# BENIOKU.txt: a short start guide, Turkish then English (CRLF). #>
    param([Parameter(Mandatory = $true)][string]$Version)
    $lines = @(
        "ARCILOGGER Lisans Yöneticisi $Version",
        '',
        'TÜRKÇE',
        '- Yalnızca hak sahibinin, tercihen internete bağlı olmayan Windows bilgisayarı içindir.',
        '  Müşteriye verilmez; ağ bağlantısı kurmaz.',
        '- Kurulum gerekmez: zip dosyasını bir klasöre açın (örneğin E:\arcilogger-lisans\program)',
        "  ve içindeki $(Get-LmPackageName -Version $Version) klasörüne girin (.cmd dosyalarını",
        '  görene kadar; Windows "Tümünü ayıkla" aynı adla bir klasör daha ekleyebilir).',
        "- Başlatmak için: `"$($script:LmGuiLauncherName)`" dosyasına çift tıklayın.",
        "- Komut satırı aracı: `"$($script:LmCliLauncherName)`" --help",
        '- İlk iş: Ayarlar sekmesinde özel anahtarı, kayıt defterini ve lisans klasörünü seçin',
        '  (hepsi depo dışında, çevrimdışı bir yerde). Anahtar yoksa Anahtar üret sekmesini kullanın.',
        '- Özel anahtarı iki ayrı çevrimdışı ortama yedekleyin; asla e-postayla göndermeyin.',
        '  Geliştiriciye yalnızca keys.py satırını gönderin.',
        '- Ayrıntılı kılavuz (ekran görüntüleriyle): kilavuz\README.md',
        '- Öz sınama: bu klasörde Dosya Gezgini adres çubuğuna cmd yazıp Enter, sonra:',
        '  python\python.exe -I -m arcilogger_license_gui --self-test',
        '',
        'ENGLISH',
        "- For the rights holder's own, preferably offline, Windows PC only. Never given to",
        '  customers; it makes no network connection.',
        '- No installation: unzip into a folder (e.g. E:\arcilogger-lisans\program) and open its',
        "  $(Get-LmPackageName -Version $Version) folder (until you see the .cmd files; Explorer's",
        '  "Extract All" may add one more folder of the same name).',
        "- Start: double-click `"$($script:LmGuiLauncherName)`".",
        "- Command line tool: `"$($script:LmCliLauncherName)`" --help",
        '- First: in Ayarlar (Settings) choose the private key, the registry and the licence folder',
        '  (all offline, outside any repository). Without a key, use Anahtar üret (Generate key).',
        '- Back up the private key on two separate offline media; never e-mail it. Send only the',
        '  keys.py line to the developer.',
        '- Full guide with screenshots: kilavuz\README.md',
        '- Self-test: type cmd into the File Explorer address bar of this folder, Enter, then:',
        '  python\python.exe -I -m arcilogger_license_gui --self-test',
        '',
        'python\ is python-build-standalone (CPython, PSF licence; notices in python\LICENSE.txt).'
    )
    ($lines -join "`r`n") + "`r`n"
}

# --- build ----------------------------------------------------------------------------------------

function Get-LmVerifiedPython {
    <# The python component of components.json, downloaded (or reused from the cache) and checked. #>
    param([Parameter(Mandatory = $true)][string]$ComponentsFile, [Parameter(Mandatory = $true)][string]$Cache)
    $catalog = [System.IO.File]::ReadAllText($ComponentsFile, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
    $component = $catalog.components | Where-Object { $_.name -eq 'python' } | Select-Object -First 1
    if (-not $component) { throw 'components.json: python bileşeni yok [no python component].' }
    $resolved = Resolve-ArciComponentSource -Component $component -UrlOverride $ComponentUrl -Sha256Override $ComponentSha256
    $target = Join-Path $Cache $resolved.FileName
    if ((Test-Path -LiteralPath $target) -and (Get-ArciFileSha256 -Path $target) -eq $resolved.Sha256) {
        Write-ArciInfo ("önbellekten [cached]: {0}" -f $resolved.FileName)
    }
    else {
        $partial = $target + '.part'
        Write-ArciInfo ("indiriliyor [downloading]: {0}" -f $resolved.Url)
        $previous = $ProgressPreference
        $ProgressPreference = 'SilentlyContinue'
        try { Invoke-WebRequest -Uri $resolved.Url -OutFile $partial -UseBasicParsing } finally { $ProgressPreference = $previous }
        $actual = Get-ArciFileSha256 -Path $partial
        if ($actual -ne $resolved.Sha256) {
            Remove-Item -LiteralPath $partial -Force
            throw ("python: SHA-256 UYUŞMUYOR [MISMATCH] beklenen [expected] {0}, gelen [actual] {1}. Dosya kullanılmadı." -f $resolved.Sha256, $actual)
        }
        Move-Item -LiteralPath $partial -Destination $target -Force
    }
    [pscustomobject]@{ File = $target; Sha256 = $resolved.Sha256; Version = [string]$component.version; Source = $resolved.Source }
}

function Invoke-LmBuild {
    Import-Module (Join-Path $PSScriptRoot '..\..\deploy\windows\installer\ArciInstall.psm1') -Force
    Initialize-ArciModulePath
    if (-not $RepoRoot) { $script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path }
    if (-not (Test-ArciWindows)) {
        throw 'Paket Windows üzerinde oluşturulur [build on Windows]: tekerlekler Windows Python ile seçilir.'
    }
    # Windows PowerShell 5.1 does not offer TLS 1.2 by default on every system.
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor [System.Net.SecurityProtocolType]::Tls12
    if (-not (Get-Command 'uv' -ErrorAction SilentlyContinue)) { throw 'uv bulunamadı [not found]: https://docs.astral.sh/uv/' }
    $tar = Join-Path $env:SystemRoot 'System32\tar.exe'
    if (-not (Test-Path -LiteralPath $tar)) { throw ("tar.exe yok [missing]: {0}" -f $tar) }

    $out = if ($OutDir) { $OutDir } else { Join-Path $RepoRoot 'dist' }
    [void](New-Item -ItemType Directory -Path $out -Force)
    $out = (Resolve-Path -LiteralPath $out).ProviderPath
    $cache = if ($CacheDir) { $CacheDir } else { Join-Path $out 'cache' }
    [void](New-Item -ItemType Directory -Path $cache -Force)

    $version = Get-LmProductVersion -InitText ([System.IO.File]::ReadAllText((Join-Path $RepoRoot 'core\src\arcilogger_core\__init__.py')))
    $name = Get-LmPackageName -Version $version
    $work = Join-Path $out ("work-{0}" -f $name)
    $stage = Join-Path $work $name
    $zip = Join-Path $out ($name + '.zip')
    $sumFile = $zip + '.sha256'
    if ((Test-Path -LiteralPath $zip) -and -not $Force) { throw ("{0} zaten var; -Force ile yeniden oluşturun [exists, use -Force]." -f $zip) }
    foreach ($path in @($zip, $sumFile)) { if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force } }
    if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
    [void](New-Item -ItemType Directory -Path $stage -Force)
    Write-ArciInfo ("Sürüm [version] {0} -> {1}" -f $version, $zip)

    Write-ArciStep 'Python (components.json)'
    $python = Get-LmVerifiedPython -ComponentsFile (Join-Path $RepoRoot 'deploy\windows\installer\components.json') -Cache $cache
    Write-ArciInfo ("python {0}: {1} ({2})" -f $python.Version, $python.Sha256, $python.Source)
    Invoke-ArciExe -FilePath $tar -ArgumentList @('-xzf', $python.File, '-C', $stage)
    $exe = Join-Path $stage 'python\python.exe'
    foreach ($file in @($exe, (Join-Path $stage 'python\pythonw.exe'))) {
        if (-not (Test-Path -LiteralPath $file)) { throw ("Python dosyası yok [missing]: {0}" -f $file) }
    }
    $pythonVersion = (Invoke-ArciProcess -FilePath $exe -ArgumentList @('-I', '-c', 'import platform; print(platform.python_version())')).StdOut.Trim()
    if ($pythonVersion -notmatch '^3\.12\.') { throw ("Python 3.12 bekleniyordu [expected]: {0}" -f $pythonVersion) }
    $tk = (Invoke-ArciProcess -FilePath $exe -ArgumentList @('-I', '-c', 'import tkinter; print(tkinter.TkVersion)')).StdOut.Trim()
    Write-ArciInfo ("Python {0}, Tk {1}" -f $pythonVersion, $tk)

    Write-ArciStep 'arcilogger-core ve bağımlılıkları [and its dependencies]'
    $appWheels = Join-Path $work 'wheels\app'
    $depWheels = Join-Path $work 'wheels\deps'
    $requirements = Join-Path $work 'requirements.txt'
    [void](New-Item -ItemType Directory -Path $appWheels -Force)
    [void](New-Item -ItemType Directory -Path $depWheels -Force)
    Push-Location $RepoRoot
    try {
        Invoke-ArciExe -FilePath 'uv' -ArgumentList @('build', '--package', 'arcilogger-core', '--wheel', '--out-dir', $appWheels)
        # Only the core wheel is built here: the db wheel's absence is not a problem.
        $shipped = @(Get-ArciWheelTestHelpers -WheelDirectory $appWheels | Where-Object { $_ -notlike '*arcilogger_db-*' })
        if ($shipped.Count -gt 0) { throw ('Test yardımcıları pakette olmamalı [test helpers must not be shipped]: ' + ($shipped -join '; ')) }
        Invoke-ArciExe -FilePath 'uv' -ArgumentList @('export', '--frozen', '--no-dev', '--package', 'arcilogger-core', '--no-emit-workspace', '--no-header', '--quiet', '--format', 'requirements-txt', '--output-file', $requirements)
    }
    finally { Pop-Location }
    $pip = @('-I', '-m', 'pip', '--isolated', '--no-input', '--disable-pip-version-check')
    Invoke-ArciExe -FilePath $exe -ArgumentList ($pip + @(
            'download', '--only-binary=:all:', '--platform', 'win_amd64', '--python-version', '3.12',
            '--implementation', 'cp', '--abi', 'cp312', '--require-hashes', '--no-deps', '--dest', $depWheels, '-r', $requirements))
    Invoke-ArciExe -FilePath $exe -ArgumentList ($pip + @('install', '--no-index', '--find-links', $depWheels, '--require-hashes', '--no-deps', '--no-warn-script-location', '-r', $requirements))
    $core = @(Get-ChildItem -LiteralPath $appWheels -Filter 'arcilogger_core-*.whl')
    if ($core.Count -ne 1) { throw ("arcilogger-core tekerleği bulunamadı [wheel not found] in {0}" -f $appWheels) }
    Invoke-ArciExe -FilePath $exe -ArgumentList ($pip + @('install', '--no-index', '--no-deps', '--no-warn-script-location', $core[0].FullName))
    Invoke-ArciExe -FilePath $exe -ArgumentList ($pip + @('check'))

    Write-ArciStep 'Araç ve pencere [tool and window]'
    $site = (Invoke-ArciProcess -FilePath $exe -ArgumentList @('-I', '-c', "import sysconfig; print(sysconfig.get_paths()['purelib'])")).StdOut.Trim()
    if (-not (Test-Path -LiteralPath $site)) { throw ("site-packages yok [missing]: {0}" -f $site) }
    foreach ($file in $script:LmToolFiles) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination (Join-Path $site $file) -Force
    }
    Invoke-ArciExe -FilePath $exe -ArgumentList @('-I', '-m', 'arcilogger_license_issuer', '--help')
    $ascii = New-Object System.Text.ASCIIEncoding
    [System.IO.File]::WriteAllText((Join-Path $stage $script:LmGuiLauncherName), (Get-LmGuiLauncher), $ascii)
    [System.IO.File]::WriteAllText((Join-Path $stage $script:LmCliLauncherName), (Get-LmCliLauncher), $ascii)
    [System.IO.File]::WriteAllText((Join-Path $stage 'BENIOKU.txt'), (Get-LmReadme -Version $version), (New-Object System.Text.UTF8Encoding -ArgumentList $true))
    $guide = Join-Path $stage 'kilavuz'
    [void](New-Item -ItemType Directory -Path (Join-Path $guide 'screenshots') -Force)
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination (Join-Path $guide 'README.md') -Force
    Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'screenshots') -Filter '*.png' | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $guide ('screenshots\' + $_.Name)) -Force
    }

    Write-ArciStep 'Zip'
    Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory($stage, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $true)
    $hash = Get-ArciFileSha256 -Path $zip
    [System.IO.File]::WriteAllText($sumFile, (Format-LmSha256Line -Hash $hash -FileName ([System.IO.Path]::GetFileName($zip))), $ascii)
    Remove-Item -LiteralPath $work -Recurse -Force

    Write-Host ''
    Write-Host ("ARCILOGGER Lisans Yöneticisi {0} hazır [ready]: {1}" -f $version, $zip) -ForegroundColor Green
    Write-Host ("  SHA-256: {0}" -f $hash)
    Write-Host ("  Python {0} (python-build-standalone {1}), Tk {2}" -f $pythonVersion, $python.Version, $tk)
}

# Dot-sourced (the tests): only the functions above are defined.
if ($MyInvocation.InvocationName -ne '.') { Invoke-LmBuild }
