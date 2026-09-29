$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
& '.\.venv\Scripts\python.exe' -m mpt_factory dashboard --port 8600
exit $LASTEXITCODE
