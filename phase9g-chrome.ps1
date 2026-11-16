$ErrorActionPreference = "Stop"
# Phase 9G: headless Chrome for the host-controller page (real browser, no manual click).
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
if (-not (Test-Path $chrome)) { throw "Chrome not found" }
$profile = Join-Path $env:TEMP "fl-phase9g-chrome"
New-Item -ItemType Directory -Force $profile | Out-Null
$out = Join-Path $root "phase9g-out"
New-Item -ItemType Directory -Force $out | Out-Null

$alive = $false
try {
    $null = Invoke-RestMethod "http://127.0.0.1:9333/json/version" -TimeoutSec 2
    $alive = $true
} catch {
    $alive = $false
}
if ($alive) {
    Write-Output "chrome already listening on 9222"
    return
}
$args = @(
    "--headless=new",
    "--remote-debugging-port=9333",
    "--user-data-dir=$profile",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--window-size=1280,900",
    "about:blank"
)
$proc = Start-Process -FilePath $chrome -ArgumentList $args -PassThru -WindowStyle Hidden
Set-Content -Path (Join-Path $out "chrome.pid") -Value $proc.Id -Encoding ascii
Start-Sleep -Seconds 3
$version = Invoke-RestMethod "http://127.0.0.1:9333/json/version" -TimeoutSec 10
Write-Output ("chrome pid=" + $proc.Id + " browser=" + $version.Browser)
