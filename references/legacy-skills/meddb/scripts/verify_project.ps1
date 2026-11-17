[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = "F:\database\meddb-agent"
$Python = Join-Path $Root ".conda\python.exe"
$MedDB = Join-Path $Root "bin\meddb.cmd"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Project Python 3.10 was not found: $Python"
}
if (-not (Test-Path -LiteralPath $MedDB)) {
    throw "meddb launcher was not found: $MedDB"
}

& $Python -m unittest discover -s (Join-Path $Root "tests") -v
if ($LASTEXITCODE -ne 0) {
    throw "Unit tests failed"
}

& $MedDB catalog metadata
if ($LASTEXITCODE -ne 0) {
    throw "Catalog metadata check failed"
}

$HadProfile = Test-Path Env:MEDDB_CODEX_PROFILE
$PreviousProfile = $env:MEDDB_CODEX_PROFILE
$HadSafe = Test-Path Env:MEDDB_CODEX_SAFE
$PreviousSafe = $env:MEDDB_CODEX_SAFE
try {
    $env:MEDDB_CODEX_PROFILE = "no-mcp"
    $env:MEDDB_CODEX_SAFE = "1"
    & $MedDB mcp-status
    if ($LASTEXITCODE -ne 0) {
        throw "No-MCP profile audit failed"
    }
}
finally {
    if ($HadProfile) { $env:MEDDB_CODEX_PROFILE = $PreviousProfile }
    else { Remove-Item Env:MEDDB_CODEX_PROFILE -ErrorAction SilentlyContinue }
    if ($HadSafe) { $env:MEDDB_CODEX_SAFE = $PreviousSafe }
    else { Remove-Item Env:MEDDB_CODEX_SAFE -ErrorAction SilentlyContinue }
}

& $Python (Join-Path $Root "scripts\validate_templates.py")
if ($LASTEXITCODE -ne 0) {
    throw "Template EXPLAIN validation failed"
}

Write-Output "meddb maintenance verification passed"
