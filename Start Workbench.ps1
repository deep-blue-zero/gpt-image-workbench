param([switch]$Demo, [switch]$NoBrowser, [int]$Port = 0)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Run Install.ps1 first. See README.md for the exact installation command.'
}
$launchArgs = @('-m', 'app.main')
if ($Port -gt 0) { $launchArgs += @('--port', $Port) }
if ($Demo) { $launchArgs += '--demo' }
if ($NoBrowser) { $launchArgs += '--no-browser' }
& $pythonPath @launchArgs
if ($LASTEXITCODE -ne 0) { throw 'Workbench exited with an error. Check the message above and data/workbench.jsonl.' }
