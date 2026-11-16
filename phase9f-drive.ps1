# Phase 9F mock driver. Sends the same event shapes the HTML host controller posts
# to /host/action, but straight to the authenticated ingest API.
# Reads BRIDGE_INGEST_KEY from .env and never prints it.
param(
    [string]$Stage = "likes",
    [int]$LikeTotal = 500,
    [string]$Viewer = "u9f_minh",
    [string]$Nick = "MinhFishing9F",
    [string]$Fan = "u9f_lan",
    [string]$FanNick = "LanFan9F",
    [string]$Text = "/vitri",
    [string]$Stamp = "",
    [int]$Tier = 3
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$keys = @{}
foreach ($line in Get-Content (Join-Path $root ".env")) {
    if ($line -match '^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$') { $keys[$matches[1]] = $matches[2].Trim() }
}
$ingest = $keys["BRIDGE_INGEST_KEY"]
if (-not $ingest) { throw "BRIDGE_INGEST_KEY missing" }
$base = "https://python-bridge-6v9w.onrender.com"
$headers = @{ "X-Bridge-Key" = $ingest; "Accept" = "application/json" }
if (-not $Stamp) { $Stamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
$script:seq = 0

function New-Id([string]$tag) {
    $script:seq += 1
    return ("9f" + $tag + "-" + $Stamp + "-" + $script:seq)
}

function New-Event([string]$type, [string]$viewerId, [string]$nick, $payload, [string]$eventId) {
    return [ordered]@{
        eventId   = $eventId
        type      = $type
        viewerId  = $viewerId
        nickname  = $nick
        timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        payload   = $payload
    }
}

function Send-Batch($events) {
    $body = @{ events = @($events) } | ConvertTo-Json -Depth 8 -Compress
    $resp = Invoke-RestMethod -Uri "$base/v1/events" -Method Post -Headers $headers -ContentType "application/json" -Body $body -TimeoutSec 60
    $statuses = @($resp.results | ForEach-Object { $_.status })
    $tally = ($statuses | Group-Object | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join ","
    Write-Output ("batch {0} -> {1}" -f $events.Count, $tally)
    return $resp
}

function Send-One([string]$type, [string]$viewerId, [string]$nick, $payload, [string]$tag) {
    $event = New-Event $type $viewerId $nick $payload (New-Id $tag)
    $resp = Send-Batch @($event)
    return @{ eventId = $event.eventId; status = $resp.results[0].status; sequence = $resp.results[0].sequence }
}

function Get-Health {
    return Invoke-RestMethod -Uri "$base/v1/health" -Method Get -TimeoutSec 60
}

switch ($Stage) {
    "health" {
        $h = Get-Health
        Write-Output ("health sessionId={0} depth={1}" -f $h.sessionId, $h.depth)
    }
    "likes" {
        Write-Output ("likes: viewer={0} total={1}" -f $Viewer, $LikeTotal)
        $sent = 0
        while ($sent -lt $LikeTotal) {
            $n = [Math]::Min(20, $LikeTotal - $sent)
            $batch = @()
            for ($i = 0; $i -lt $n; $i++) {
                $batch += New-Event "like" $Viewer $Nick @{ count = 1 } (New-Id "like")
            }
            Send-Batch $batch | Out-Null
            $sent += $n
            Start-Sleep -Milliseconds 400
        }
        $h = Get-Health
        Write-Output ("likes done sent={0} sessionId={1} depth={2}" -f $sent, $h.sessionId, $h.depth)
    }
    "actions" {
        $r1 = Send-One "comment" $Viewer $Nick @{ text = "/cau" } "cau"
        Write-Output ("/cau bridge={0} seq={1} id={2}" -f $r1.status, $r1.sequence, $r1.eventId)
        Start-Sleep -Seconds 3
        $r2 = Send-One "comment" $Viewer $Nick @{ text = "/giat" } "giat"
        Write-Output ("/giat bridge={0} seq={1} id={2}" -f $r2.status, $r2.sequence, $r2.eventId)
        Start-Sleep -Seconds 3
        $r3 = Send-One "comment" $Viewer $Nick @{ text = "/vitri" } "vitri"
        Write-Output ("/vitri bridge={0} seq={1} id={2}" -f $r3.status, $r3.sequence, $r3.eventId)
    }
    "say" {
        $r = Send-One "comment" $Viewer $Nick @{ text = $Text } "say"
        Write-Output ("say '{0}' bridge={1} seq={2} id={3}" -f $Text, $r.status, $r.sequence, $r.eventId)
    }
    "steal" {
        $r = Send-One "gift" $Fan $FanNick @{ giftId = "GIFT_STEAL"; giftName = "Rose"; coinValue = 20; repeatCount = 1; amount = 1 } "steal"
        Write-Output ("gift20 bridge={0} seq={1} id={2}" -f $r.status, $r.sequence, $r.eventId)
    }
    "camera" {
        $r = Send-One "gift" $Fan $FanNick @{ giftId = "GIFT_CAMERA"; giftName = "Galaxy"; coinValue = 30; repeatCount = 1; amount = 1 } "cam"
        Write-Output ("gift30 bridge={0} seq={1} id={2}" -f $r.status, $r.sequence, $r.eventId)
    }
    "baits" {
        $tiers = @(
            @{ id = "BAIT_TIER_1"; coins = 10 },
            @{ id = "BAIT_TIER_2"; coins = 30 },
            @{ id = "BAIT_TIER_3"; coins = 50 },
            @{ id = "BAIT_TIER_4"; coins = 100 },
            @{ id = "BAIT_TIER_5"; coins = 200 }
        )
        foreach ($tier in $tiers) {
            $r = Send-One "gift" $Fan $FanNick @{ giftId = $tier.id; giftName = $tier.id; coinValue = $tier.coins; repeatCount = 1; amount = 1 } ("bait" + $tier.coins)
            Write-Output ("{0} ({1} xu) bridge={2} seq={3}" -f $tier.id, $tier.coins, $r.status, $r.sequence)
            Start-Sleep -Milliseconds 600
        }
    }
    "baitone" {
        $coins = @{ 1 = 10; 2 = 30; 3 = 50; 4 = 100; 5 = 200 }
        if (-not $coins.ContainsKey($Tier)) { throw "tier must be 1-5" }
        $giftId = "BAIT_TIER_" + $Tier
        $r = Send-One "gift" $Fan $FanNick @{ giftId = $giftId; giftName = $giftId; coinValue = $coins[$Tier]; repeatCount = 1; amount = 1 } ("bait" + $coins[$Tier])
        Write-Output ("{0} ({1} xu) bridge={2} seq={3}" -f $giftId, $coins[$Tier], $r.status, $r.sequence)
    }
    "dupgift" {
        $id = "9fdupgift-" + $Stamp
        $p = @{ giftId = "GIFT_STEAL"; giftName = "Rose"; coinValue = 20; repeatCount = 1; amount = 1 }
        $resp = Send-Batch @((New-Event "gift" $Fan $FanNick $p $id))
        Write-Output ("dupgift first -> {0} seq={1}" -f $resp.results[0].status, $resp.results[0].sequence)
        Start-Sleep -Milliseconds 500
        $resp2 = Send-Batch @((New-Event "gift" $Fan $FanNick $p $id))
        Write-Output ("dupgift second -> {0} seq={1}" -f $resp2.results[0].status, $resp2.results[0].sequence)
    }
    "dup" {
        $id = "9fdup-" + $Stamp
        $p = @{ count = 1 }
        $resp = Send-Batch @((New-Event "like" $Fan $FanNick $p $id))
        Write-Output ("dup first -> {0} seq={1}" -f $resp.results[0].status, $resp.results[0].sequence)
        Start-Sleep -Milliseconds 400
        $resp2 = Send-Batch @((New-Event "like" $Fan $FanNick $p $id))
        Write-Output ("dup second -> {0} seq={1}" -f $resp2.results[0].status, $resp2.results[0].sequence)
    }
    default { throw "unknown stage $Stage" }
}

$h = Get-Health
Write-Output ("health sessionId={0} depth={1}" -f $h.sessionId, $h.depth)
