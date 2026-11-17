[CmdletBinding()]
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$MeddbArgs
)

$ErrorActionPreference = "Stop"
throw "The MedDB Codex query integration is temporarily disabled pending redesign"
