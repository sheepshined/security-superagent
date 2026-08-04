$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    throw "Missing .venv. Run scripts\bootstrap.ps1 first."
}

& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app `
    --app-dir backend `
    --host 127.0.0.1 `
    --port 8000 `
    --reload

