[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$RunLabel = ("skill-v4-exposed-dev-four-" + (Get-Date -Format 'yyyyMMddTHHmmss')),
    [ValidateRange(1, 2)][int]$Workers = 2
)
$ErrorActionPreference = 'Stop'
$env:MEDDB_CODEX_PROFILE = 'no-mcp'
$env:MEDDB_CODEX_SAFE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$pythonExe = 'F:\database\meddb-agent\.conda\python.exe'
$nativeCodex = Get-ChildItem 'C:\Users\lenovo\AppData\Roaming\npm\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\*\bin\codex.exe' | Select-Object -First 1 -ExpandProperty FullName
if (-not $nativeCodex) { throw 'Native Codex executable not found' }
$runRoot = "F:\database\benchmark\runs\clinsql\$RunLabel"
if (Test-Path -LiteralPath $runRoot) { throw 'Run directory already exists; never overwrite prior results' }
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
    '--report', "F:\database\benchmark\artifacts\$RunLabel.json",
    '--development-profile', 'C:\Users\lenovo\.codex\skills\mimic-postgres-cli\references\clinsql-development-profile.md',
    '--development-manifest', 'F:\database\benchmark\configs\clinsql_v4_exposed_development_20261027.json',
    '--pairing-mode', 'shared', '--model', 'gpt-5.6-terra', '--reasoning-effort', 'medium',
    '--workers', "$Workers", '--generation-timeout-seconds', '360', '--query-timeout-seconds', '45',
    '--repair-attempts', '1', '--dictionary-grounding',
    '--task-keys', 'validation/Medication_Management/easy/010',
    'validation/Disease_Diagnosis_and_Outcomes/easy/002',
    'validation/Laboratory_Results_Analysis/easy/004',
    'validation/Patient_Demographics_and_Admissions/easy/043'
)
& $pythonExe @runnerArgs
exit $LASTEXITCODE
