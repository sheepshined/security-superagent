$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$python = Get-Command python -ErrorAction Stop
$version = & $python.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($version -notin @("3.11", "3.12")) {
    throw "Python 3.11 or 3.12 is required. Detected: $version"
}

if (-not (Test-Path -LiteralPath ".venv")) {
    & $python.Source -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -e ".[dev]"

Push-Location -LiteralPath "frontend"
try {
    npm install
}
finally {
    Pop-Location
}

Write-Host "Bootstrap complete. Copy .env.example to .env and configure local secrets."

