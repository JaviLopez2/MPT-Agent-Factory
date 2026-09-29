param([string]$Python = 'py')
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (!(Test-Path '.venv\Scripts\python.exe')) {
    if ($Python -eq 'py') { & py -3.11 -m venv .venv } else { & $Python -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python 3.11+ virtual environment' }
}
& '.\.venv\Scripts\python.exe' -m pip install -e '.[dev]'
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
if (!(Test-Path 'factory.toml')) { Copy-Item 'factory.example.toml' 'factory.toml' }
& '.\.venv\Scripts\python.exe' -m mpt_factory init
if ($LASTEXITCODE -ne 0) { throw 'Factory initialization failed' }
Write-Host 'Ready. Run doctor, then start supervisor and dashboard in separate terminals.'
