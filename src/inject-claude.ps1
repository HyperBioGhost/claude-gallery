$claudeDir = Join-Path $env:USERPROFILE '.claude'
$claudeMd  = Join-Path $claudeDir 'CLAUDE.md'

New-Item -Path $claudeDir -ItemType Directory -Force | Out-Null
New-Item -Path (Join-Path $env:USERPROFILE 'ClaudeGallery\artifacts') -ItemType Directory -Force | Out-Null

$instructions = @"


<!-- claude-gallery-start -->
# Claude Gallery

A local artifact viewer is running at http://localhost:7477. When you generate any viewable output - HTML, SVG, images, charts, code, data files, or anything else worth seeing - do the following automatically, without being asked:

1. Save the file to ~/ClaudeGallery/artifacts/<filename> (create the folder if it doesn't exist)
2. Immediately after saving, run: curl.exe -s "http://localhost:7477/notify?file=<filename>"
3. Tell the user: "Sent to Claude Gallery."

Do NOT open a browser tab. Do NOT ask permission. Do NOT skip this for simple outputs.
Supported types: .html .htm .svg .png .jpg .gif .webp .md .txt .csv .json .js .ts .py .css
<!-- claude-gallery-end -->
"@

$existing = if (Test-Path $claudeMd) { Get-Content $claudeMd -Raw -Encoding UTF8 } else { '' }
if ($existing -notmatch 'claude-gallery-start') {
    Add-Content -Path $claudeMd -Value $instructions -Encoding UTF8
}
