$claudeMd = Join-Path $env:USERPROFILE '.claude\CLAUDE.md'
if (-not (Test-Path $claudeMd)) { exit 0 }

$content = Get-Content $claudeMd -Raw -Encoding UTF8
$content = $content -replace '\r?\n*<!-- claude-gallery-start -->[\s\S]*?<!-- claude-gallery-end -->', ''
Set-Content -Path $claudeMd -Value $content.TrimEnd() -Encoding UTF8
