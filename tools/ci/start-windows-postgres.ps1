<#
.SYNOPSIS
    Starts the PostgreSQL preinstalled on the GitHub windows-latest runner image for the Python
    jobs of .github/workflows/ci.yml and passes its DSN to the later steps (ARCILOGGER_TEST_PG).

.DESCRIPTION
    The runner image documents PGBIN/PGROOT/PGDATA and a disabled postgresql service.
    These facts cannot be checked from the repository: print what is there and fail
    with a clear message if PostgreSQL cannot be started.

    One script for the three Windows jobs (core gate, other tests, throughput guards), called
    with "shell: pwsh". The installer workflow starts the same server in its own step: its
    end-to-end job passes other variables on.
#>

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false  # exit codes are checked below
Write-Host "PGBIN=$env:PGBIN PGROOT=$env:PGROOT PGDATA=$env:PGDATA PGUSER=$env:PGUSER"
$service = Get-Service -Name 'postgresql*' -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $service -or -not $env:PGBIN) {
    Get-ChildItem 'C:\Program Files\PostgreSQL' -ErrorAction SilentlyContinue | Format-Table -AutoSize | Out-String | Write-Host
    Write-Host '::error::No preinstalled PostgreSQL service/PGBIN found on this runner image; database tests cannot run.'
    exit 1
}
Write-Host "Service $($service.Name): status=$($service.Status) start=$($service.StartType)"
Set-Service -Name $service.Name -StartupType Manual
Start-Service -Name $service.Name
$ready = $false
for ($i = 0; $i -lt 30 -and -not $ready; $i++) {
    & (Join-Path $env:PGBIN 'pg_isready.exe') -h 127.0.0.1 -p 5432
    $ready = ($LASTEXITCODE -eq 0)
    if (-not $ready) { Start-Sleep -Seconds 2 }
}
if (-not $ready) {
    Get-ChildItem (Join-Path $env:PGDATA 'log') -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1 | Get-Content -Tail 50 | Write-Host
    Write-Host '::error::PostgreSQL did not become ready on 127.0.0.1:5432.'
    exit 1
}
$user = if ($env:PGUSER) { $env:PGUSER } else { 'postgres' }
$password = if ($env:PGPASSWORD) { $env:PGPASSWORD } else { 'root' }
$env:PGPASSWORD = $password
& (Join-Path $env:PGBIN 'psql.exe') -h 127.0.0.1 -U $user -d postgres -w -c 'SELECT version();' -c 'SHOW server_encoding;'
if ($LASTEXITCODE -ne 0) {
    Write-Host "::error::Cannot log in to PostgreSQL as $user (PGUSER/PGPASSWORD of the runner image)."
    exit 1
}
"ARCILOGGER_TEST_PG=postgresql://${user}:${password}@127.0.0.1:5432/postgres" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
