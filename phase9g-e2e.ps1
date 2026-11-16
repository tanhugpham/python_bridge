# Phase 9G E2E: drive the same /host/action endpoints the page posts, one command per step.
# Writes a timeline to phase9g-out/e2e.txt and never prints a key.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$out = Join-Path $root "phase9g-out"
New-Item -ItemType Directory -Force $out | Out-Null
$log = Join-Path $out "e2e.txt"
$base = "http://127.0.0.1:8787"
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$headers = @{ Host = "127.0.0.1:8787"; Origin = "http://127.0.0.1:8787" }
$boot = Invoke-RestMethod "$base/host/session" -WebSession $session -Headers $headers
$headers["X-CSRF-Token"] = $boot.csrf

function Note([string]$text) {
    $line = (Get-Date -Format "HH:mm:ss.fff") + " " + $text
    Add-Content -Path $log -Value $line -Encoding UTF8
    Write-Host $line
}

function Send([string]$action, $extra) {
    $body = @{ action = $action }
    if ($extra) { foreach ($key in $extra.Keys) { $body[$key] = $extra[$key] } }
    $json = $body | ConvertTo-Json -Depth 6 -Compress
    # PowerShell 5.1 encodes a string body as ISO-8859-1, which breaks Vietnamese text
    # (HTTP 400 "There was an error parsing the body"). Send explicit UTF-8 bytes instead.
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    try {
        $resp = Invoke-RestMethod "$base/host/action" -Method Post -WebSession $session -Headers $headers -ContentType "application/json; charset=utf-8" -Body $bytes
        return $resp
    } catch {
        $detail = $_.ErrorDetails.Message
        return @{ ok = $false; error = "$($_.Exception.Message) $detail" }
    }
}

function Step([string]$label, [string]$action, $extra) {
    $resp = Send $action $extra
    $status = $resp.bridgeStatus
    $seq = $resp.sequence
    $eventId = $resp.eventId
    $viewer = $resp.viewer
    $likes = if ($viewer) { $viewer.likes } else { "" }
    $unlocked = if ($viewer) { $viewer.fishingUnlocked } else { "" }
    Note ("{0} => bridgeStatus={1} seq={2} eventId={3} likes={4} unlocked={5} appliedOnRoblox={6}" -f $label, $status, $seq, $eventId, $likes, $unlocked, $resp.appliedOnRoblox)
    return $resp
}

$viewer = "9g_check2"
$null = Step "add_viewer $viewer" "add_viewer" @{ username = $viewer }
$null = Step "like 500" "like" @{ username = $viewer; count = 500; eventId = "9g2-like-unlock" }
Start-Sleep -Milliseconds 1200
$null = Step "comment /câu" "comment" @{ username = $viewer; text = "/câu"; eventId = "9g2-cau" }
Start-Sleep -Milliseconds 1200
$null = Step "comment /giật" "comment" @{ username = $viewer; text = "/giật"; eventId = "9g2-giat" }
Start-Sleep -Seconds 9
$null = Step "comment /vitri" "comment" @{ username = $viewer; text = "/vitri"; eventId = "9g2-vitri" }
Start-Sleep -Milliseconds 2500
$null = Step "gift 20 (Rose/steal)" "gift" @{ username = $viewer; purpose = "steal"; giftId = "GIFT_STEAL"; giftName = "Rose"; coinValue = 20; repeatCount = 1; eventId = "9g2-gift20" }
Start-Sleep -Milliseconds 600
$null = Step "gift 30 (Galaxy/camera)" "gift" @{ username = $viewer; purpose = "camera"; giftId = "GIFT_CAMERA"; giftName = "Galaxy"; coinValue = 30; repeatCount = 1; eventId = "9g2-gift30" }
Start-Sleep -Milliseconds 600
$tiers = @(
    @{ tier = 1; id = "BAIT_TIER_1"; name = "Mồi Giun Đỏ"; coins = 10 },
    @{ tier = 2; id = "BAIT_TIER_2"; name = "Mồi Tôm Bạc"; coins = 30 },
    @{ tier = 3; id = "BAIT_TIER_3"; name = "Mồi Cá Vàng"; coins = 50 },
    @{ tier = 4; id = "BAIT_TIER_4"; name = "Mồi Ngọc Biển"; coins = 100 },
    @{ tier = 5; id = "BAIT_TIER_5"; name = "Mồi Rồng Thần"; coins = 200 }
)
foreach ($row in $tiers) {
    $null = Step ("bait tier " + $row.tier) "gift" @{
        username = $viewer; purpose = "bait"; giftId = $row.id; giftName = $row.name;
        coinValue = $row.coins; repeatCount = 1; eventId = ("9g2-bait" + $row.tier)
    }
    Start-Sleep -Milliseconds 900
}
$null = Step "comment dedup #1" "comment" @{ username = $viewer; text = "xin chào"; eventId = "9g2-dup-1" }
$null = Step "comment dedup #2 (same eventId)" "comment" @{ username = $viewer; text = "xin chào"; eventId = "9g2-dup-1" }
Start-Sleep -Seconds 2
$board = Invoke-RestMethod "$base/host/dashboard" -WebSession $session -Headers $headers
Note ("dashboard mirror=" + ($board.mirror | ConvertTo-Json -Compress))
$events = Invoke-RestMethod "$base/host/events?limit=100" -WebSession $session -Headers $headers
$mine = @($events.events | Where-Object { $_.eventId -like "9g2-*" })
Note ("bridge log rows for 9g_* = " + $mine.Count)
foreach ($row in $mine) {
    $text = ("{0} {1} {2} {3}" -f $row.eventId, $row.eventType, $row.bridgeStatus, $row.summary)
    Add-Content -Path $log -Value ("  " + $text) -Encoding UTF8
}
Write-Output ("log written: " + $log)
