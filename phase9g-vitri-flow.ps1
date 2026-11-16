param([string]$Viewer = "9g_v3", [string]$Prefix = "9g3")
# Phase 9G: fresh viewer -> 500 likes -> /câu -> wait for a rig -> /vitri. UTF-8 bodies.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$base = "http://127.0.0.1:8787"
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$headers = @{ Host = "127.0.0.1:8787"; Origin = "http://127.0.0.1:8787" }
$headers["X-CSRF-Token"] = (Invoke-RestMethod "$base/host/session" -WebSession $session -Headers $headers).csrf

function Send($body) {
    $json = $body | ConvertTo-Json -Depth 6 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    try {
        $resp = Invoke-RestMethod "$base/host/action" -Method Post -WebSession $session -Headers $headers -ContentType "application/json; charset=utf-8" -Body $bytes
        return $resp
    } catch {
        return @{ ok = $false; error = "$($_.Exception.Message) $($_.ErrorDetails.Message)" }
    }
}

function Show([string]$label, $resp) {
    Write-Host ("{0} {1} -> ok={2} bridgeStatus={3} seq={4} eventId={5}" -f (Get-Date -Format "HH:mm:ss.fff"), $label, $resp.ok, $resp.bridgeStatus, $resp.sequence, $resp.eventId)
}

Show "add_viewer $Viewer" (Send @{ action = "add_viewer"; username = $Viewer })
Start-Sleep -Milliseconds 800
Show "like 500" (Send @{ action = "like"; username = $Viewer; count = 500; eventId = "$Prefix-like" })
Start-Sleep -Milliseconds 800
Show "comment /câu" (Send @{ action = "comment"; username = $Viewer; text = "/câu"; eventId = "$Prefix-cau" })
Start-Sleep -Seconds 4
Show "comment /vitri" (Send @{ action = "comment"; username = $Viewer; text = "/vitri"; eventId = "$Prefix-vitri" })
