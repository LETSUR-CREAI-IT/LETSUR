param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$alertPython = (Get-Command $Python -ErrorAction Stop).Source
& (Join-Path $PSScriptRoot 'start-listener.ps1') -Python $alertPython
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $alertPython (Join-Path $PSScriptRoot 'operations.py') watch
exit $LASTEXITCODE
