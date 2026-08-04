$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath (Join-Path $projectRoot "frontend")

if (-not (Test-Path -LiteralPath "node_modules")) {
    throw "Missing frontend dependencies. Run scripts\bootstrap.ps1 first."
}

npm run dev

