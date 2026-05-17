# Claude Gallery — Windows Smoke Tests
# Run after installation to verify the server is working correctly.
# Usage: powershell -ExecutionPolicy Bypass -File tests\smoke.ps1
# Exit code 0 = all tests passed, 1 = one or more failed.

param([int]$Port = 7477, [int]$StartupTimeout = 20)

$base    = "http://localhost:$Port"
$passed  = 0
$failed  = 0
$results = @()

function Pass($name) {
    Write-Host "  PASS  $name" -ForegroundColor Green
    $script:passed++
    $script:results += @{ name=$name; ok=$true }
}
function Fail($name, $reason) {
    Write-Host "  FAIL  $name — $reason" -ForegroundColor Red
    $script:failed++
    $script:results += @{ name=$name; ok=$false; reason=$reason }
}

Write-Host ""
Write-Host "Claude Gallery Smoke Tests" -ForegroundColor Cyan
Write-Host "==========================" -ForegroundColor Cyan
Write-Host ""

# ── 1. Wait for server to start ───────────────────────────────────────
Write-Host "Waiting for server on port $Port..."
$deadline = (Get-Date).AddSeconds($StartupTimeout)
$up = $false
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -Uri $base -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $up = $true; break }
    } catch {}
    Start-Sleep -Milliseconds 500
}
if (-not $up) {
    Write-Host "  FATAL  Server did not respond within ${StartupTimeout}s" -ForegroundColor Red
    exit 1
}
Write-Host "  Server is up." -ForegroundColor Green
Write-Host ""

# ── 2. GET / returns 200 and contains expected content ────────────────
try {
    $r = Invoke-WebRequest -Uri $base -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -eq 200 -and $r.Content -match "Claude Gallery") {
        Pass "GET / returns 200 with gallery HTML"
    } else {
        Fail "GET / returns 200 with gallery HTML" "status=$($r.StatusCode), body missing 'Claude Gallery'"
    }
} catch { Fail "GET / returns 200 with gallery HTML" $_.Exception.Message }

# ── 3. GET /list returns valid JSON ───────────────────────────────────
try {
    $r = Invoke-WebRequest -Uri "$base/list" -UseBasicParsing -TimeoutSec 5
    $json = $r.Content | ConvertFrom-Json
    if ($r.StatusCode -eq 200 -and $json.PSObject.Properties.Name -contains "files") {
        Pass "GET /list returns valid JSON with 'files' key"
    } else {
        Fail "GET /list returns valid JSON with 'files' key" "missing 'files' key"
    }
} catch { Fail "GET /list returns valid JSON with 'files' key" $_.Exception.Message }

# ── 4. GET /notify?file=smoke-test.txt returns 200 ───────────────────
try {
    $r = Invoke-WebRequest -Uri "$base/notify?file=smoke-test.txt" -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -eq 200) {
        Pass "GET /notify?file=smoke-test.txt returns 200"
    } else {
        Fail "GET /notify?file=smoke-test.txt returns 200" "status=$($r.StatusCode)"
    }
} catch { Fail "GET /notify?file=smoke-test.txt returns 200" $_.Exception.Message }

# ── 5. GET /files/nonexistent returns 404 ────────────────────────────
try {
    $r = Invoke-WebRequest -Uri "$base/files/does-not-exist-smoke.txt" -UseBasicParsing -TimeoutSec 5 -ErrorAction SilentlyContinue
    if ($r.StatusCode -eq 404) {
        Pass "GET /files/nonexistent returns 404"
    } else {
        Fail "GET /files/nonexistent returns 404" "status=$($r.StatusCode)"
    }
} catch [System.Net.WebException] {
    # WebException is thrown for 4xx/5xx in older PS versions
    $code = [int]$_.Exception.Response.StatusCode
    if ($code -eq 404) { Pass "GET /files/nonexistent returns 404" }
    else { Fail "GET /files/nonexistent returns 404" "status=$code" }
} catch { Fail "GET /files/nonexistent returns 404" $_.Exception.Message }

# ── 6. CLAUDE.md was injected ────────────────────────────────────────
$claudeMd = Join-Path $env:USERPROFILE ".claude\CLAUDE.md"
if (Test-Path $claudeMd) {
    $content = Get-Content $claudeMd -Raw
    if ($content -match "claude-gallery-start") {
        Pass "CLAUDE.md contains injected gallery instructions"
    } else {
        Fail "CLAUDE.md contains injected gallery instructions" "marker not found in $claudeMd"
    }
} else {
    Fail "CLAUDE.md contains injected gallery instructions" "$claudeMd does not exist"
}

# ── Summary ───────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Results: $passed passed, $failed failed" -ForegroundColor $(if ($failed -eq 0) { "Green" } else { "Red" })
Write-Host ""

if ($failed -gt 0) { exit 1 } else { exit 0 }
