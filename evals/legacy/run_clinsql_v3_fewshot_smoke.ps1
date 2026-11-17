[CmdletBinding()]
param([ValidateSet('fewshot', 'zero-shot')][string]$Track = 'fewshot')
$ErrorActionPreference = 'Stop'
$env:MEDDB_CODEX_PROFILE = 'no-mcp'
$env:MEDDB_CODEX_SAFE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$pythonExe = 'F:\database\meddb-agent\.conda\python.exe'
$nativeCodex = Get-ChildItem 'C:\Users\lenovo\AppData\Roaming\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\*\bin\codex.exe' | Select-Object -First 1 -ExpandProperty FullName
if (-not $nativeCodex) { throw 'Native Codex executable not found' }
$runRoot = "F:\database\benchmark\runs\clinsql\skill-v3-$Track-smoke-two-20261027"
if (Test-Path -LiteralPath $runRoot) { throw 'Run directory already exists' }
$runnerArgs = @(
    'F:\database\benchmark\tools\run_clinsql_model_generation.py',
    '--validation-root', 'F:\database\vendor\ClinSQL-main\data\splits\validation',
    '--meddb-root', 'F:\database\meddb-agent', '--codex', $nativeCodex,
    '--codex-home', 'C:\Users\lenovo\.codex-mimic-benchmark',
    '--output-schema', 'F:\database\benchmark\configs\clinsql_generated_sql_v3.schema.json',
    '--schema-context', 'F:\database\vendor\ClinSQL-main\model_inference\mimic4_schema.txt',
    '--skill-entry', 'C:\Users\lenovo\.codex\skills\mimic-postgres-cli\SKILL.md',
    '--skill-protocol', 'C:\Users\lenovo\.codex\skills\mimic-postgres-cli\references\benchmark-protocol.md',
    '--static-validator', 'C:\Users\lenovo\.codex\skills\mimic-postgres-cli\scripts\validate_generated_sql.py',
    '--gold-helper', 'F:\database\benchmark\tools\run_clinsql_validation_pilot.py',
    '--official-config', 'F:\database\benchmark\configs\meddb-clinsql-official-validation.json',
    '--local-config', 'F:\database\benchmark\configs\meddb-clinsql-local-validation.json',
    '--run-root', $runRoot,
    '--report', "F:\database\benchmark\artifacts\clinsql_skill_v3_$($Track.Replace('-', '_'))_smoke_two_20261027.json",
    '--pairing-mode', 'shared', '--model', 'gpt-5.6-terra', '--reasoning-effort', 'medium',
    '--workers', '2',
    '--generation-timeout-seconds', '360', '--query-timeout-seconds', '45',
    '--repair-attempts', '1', '--dictionary-grounding',
    '--task-keys', 'validation/Patient_Demographics_and_Admissions/easy/043',
    'validation/Medication_Management/easy/010'
)
if ($Track -eq 'fewshot') {
    $runnerArgs += @('--development-examples', 'F:\database\benchmark\configs\clinsql_declared_development_examples_20261027.json')
}
& $pythonExe @runnerArgs
exit $LASTEXITCODE
