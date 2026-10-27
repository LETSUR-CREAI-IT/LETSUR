param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$alertRoot = $PSScriptRoot
$alertPython = (Get-Command $Python -ErrorAction Stop).Source
$alertState = & $alertPython (Join-Path $alertRoot 'operations.py') status | ConvertFrom-Json
if ($alertState.listener_running) {
    Write-Output 'Listener already connected.'
    exit 0
}
$alertArgs = '"' + (Join-Path $alertRoot 'operations.py') + '" listen'
$alertProcess = Start-Process -FilePath $alertPython -ArgumentList $alertArgs -WorkingDirectory $alertRoot -WindowStyle Hidden -PassThru
Write-Output ('Listener launch requested. PID=' + $alertProcess.Id)
$alertWait = [Diagnostics.Stopwatch]::StartNew()
while ($alertWait.Elapsed.TotalSeconds -lt 30) {
    Start-Sleep -Milliseconds 500
    $alertState = & $alertPython (Join-Path $alertRoot 'operations.py') status | ConvertFrom-Json
    if ($alertState.listener_running) { Write-Output 'Listener connected.'; exit 0 }
}
throw 'Listener did not connect within 30 seconds; inspect output/live/listener.log.'
