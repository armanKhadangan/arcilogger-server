# ARCILOGGER evidence package verifier - wrapper for Windows PowerShell 5.1 and PowerShell 7.
#
#   powershell -ExecutionPolicy Bypass -File .\verify.ps1 [package folder or zip] [options]
#
# Without a package argument the folder of this script is verified. The verifier itself is
# verify.py: one file, Python 3.9+ standard library only, run isolated (-I -S) so that no
# installed package and no PYTHON* environment variable can change what it does.
# This file is ASCII only on purpose: PowerShell 5.1 reads scripts without a BOM as ANSI.

$verifier = Join-Path $PSScriptRoot 'verify.py'
$candidates = @(
    @{ Name = 'py'; Prefix = @('-3') },
    @{ Name = 'python'; Prefix = @() },
    @{ Name = 'python3'; Prefix = @() }
)
foreach ($candidate in $candidates) {
    $command = Get-Command $candidate.Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $command) { continue }
    # The Microsoft Store placeholder "python.exe" exists even without Python: check it runs.
    $prefix = $candidate.Prefix
    & $command.Source @prefix -I -S -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>$null
    if ($LASTEXITCODE -ne 0) { continue }
    & $command.Source @prefix -I -S $verifier @args
    exit $LASTEXITCODE
}
Write-Host 'Python 3.9 veya ustu bulunamadi [Python 3.9 or newer not found]: https://www.python.org/downloads/'
exit 2
