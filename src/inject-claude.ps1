$claudeDir = Join-Path $env:USERPROFILE '.claude'
$claudeMd  = Join-Path $claudeDir 'CLAUDE.md'
$docsBase  = [Environment]::GetFolderPath('MyDocuments')
$artifacts = Join-Path $docsBase 'ClaudeGallery\artifacts'

New-Item -Path $claudeDir -ItemType Directory -Force | Out-Null
New-Item -Path $artifacts -ItemType Directory -Force | Out-Null

$instructions = @"


<!-- claude-gallery-start -->
# Claude Gallery

A local artifact viewer is running at http://localhost:7477.

## Starting the gallery (if not running)
If ``curl.exe -s http://localhost:7477/`` fails (connection refused), start it:
``Start-Process -FilePath "`$env:LOCALAPPDATA\ClaudeGallery\claude-gallery-server.exe" -WindowStyle Hidden``
The server runs silently in the background. Do NOT look for a .py file or npm script.

## Sending artifacts to the gallery
When you generate any viewable output - HTML, SVG, images, charts, code, data files, or anything else worth seeing - do the following automatically, without being asked:

1. Save the file to ~/Documents/ClaudeGallery/artifacts/<filename> (create the folder if it doesn't exist)
2. Immediately after saving, run: curl.exe -s "http://localhost:7477/notify?file=<filename>"
3. Tell the user: "Sent to Claude Gallery."

Gallery types: .html .htm .svg .png .jpg .gif .webp .csv .json .py .js .ts .css .txt .md

## Non-gallery files
If the output is a binary, archive, or temp file (.zip, .tar, .gz, .exe, etc.) - do NOT put it in the artifacts folder. Ask the user where to save it.

Do NOT open a browser tab. Do NOT assume the artifacts folder is a general-purpose output folder.
<!-- claude-gallery-end -->
"@

$existing = if (Test-Path $claudeMd) { Get-Content $claudeMd -Raw -Encoding UTF8 } else { '' }
if ($existing -notmatch 'claude-gallery-start') {
    Add-Content -Path $claudeMd -Value $instructions -Encoding UTF8
}
