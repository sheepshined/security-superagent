$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    throw "Missing .venv. Run scripts\bootstrap.ps1 first."
}

$env:PYTHONPATH = Join-Path $projectRoot "backend"
& ".\.venv\Scripts\python.exe" ".\scripts\check_deepseek.py"

