param([int]$Port = 8000, [switch]$RequireGpu, [switch]$Telegram)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create the project virtual environment first; see SETUP.md.' }
$launchArgs = @('ui.py', '--config', 'config/default.json', '--source', 'idle', '--judge-mode', '--video-plays', '1', '--port', "$Port")
if ($RequireGpu) { $launchArgs += '--require-gpu' }
if (-not $Telegram) { $launchArgs += '--no-telegram' }
Push-Location $projectRoot
try { & $pythonPath @launchArgs; exit $LASTEXITCODE }
finally { Pop-Location }
