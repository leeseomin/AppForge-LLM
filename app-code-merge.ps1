Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptPath = Join-Path $PSScriptRoot "app-code-merge.py"
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    & $venvPython $scriptPath @args
    exit $LASTEXITCODE
}

$pyLauncher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($null -ne $pyLauncher) {
    & $pyLauncher.Source -3.11 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
    if ($LASTEXITCODE -eq 0) {
        & $pyLauncher.Source -3.11 $scriptPath @args
        exit $LASTEXITCODE
    }
}

$python = Get-Command python.exe, python3.exe, python -CommandType Application `
    -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $python) {
    Write-Error "Python 3.11 or newer is required."
    exit 1
}

& $python.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "Python 3.11 or newer is required."
    exit 1
}

& $python.Source $scriptPath @args
exit $LASTEXITCODE
