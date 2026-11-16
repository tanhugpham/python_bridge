param([string]$BodyFile)
# Phase 9G: post one /host/action JSON file as UTF-8 bytes (PS 5.1 mangles Vietnamese strings).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$base = "http://127.0.0.1:8787"
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$headers = @{ Host = "127.0.0.1:8787"; Origin = "http://127.0.0.1:8787" }
$headers["X-CSRF-Token"] = (Invoke-RestMethod "$base/host/session" -WebSession $session -Headers $headers).csrf
$json = [System.IO.File]::ReadAllText($BodyFile, [System.Text.Encoding]::UTF8)
$bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
try {
    $resp = Invoke-RestMethod "$base/host/action" -Method Post -WebSession $session -Headers $headers -ContentType "application/json; charset=utf-8" -Body $bytes
    ($resp | ConvertTo-Json -Depth 4 -Compress)
} catch {
    "ERR " + $_.Exception.Message + " " + $_.ErrorDetails.Message
}
