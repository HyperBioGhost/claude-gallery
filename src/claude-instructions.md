# Claude Gallery

A local artifact viewer is running at http://localhost:7477.

## Starting the gallery (if not running)
If `curl -s http://localhost:7477/` fails (connection refused), start the server:
- **Mac**: `/usr/local/bin/claude-gallery-server &`
- **Windows**: `Start-Process -FilePath "$env:LOCALAPPDATA\ClaudeGallery\claude-gallery-server.exe" -WindowStyle Hidden`

Do NOT look for a .py file or npm script. The server is a standalone binary.

## Sending artifacts to the gallery
When you generate any viewable output - HTML, SVG, images, charts, code, data files, or anything else worth seeing - do the following automatically, without being asked:

1. Save the file to the artifacts folder (create it if needed):
   - **Mac**: `~/ClaudeGallery/artifacts/<filename>`
   - **Windows**: `~/Documents/ClaudeGallery/artifacts/<filename>`
2. Immediately after saving, run: `curl -s "http://localhost:7477/notify?file=<filename>"` (on Windows, use `curl.exe`)
3. Tell the user: "Sent to Claude Gallery."

Gallery types: .html, .htm, .svg, .png, .jpg, .gif, .webp, .csv, .json, .py, .js, .ts, .css, .txt, .md

## Non-gallery files
If the output is a binary, archive, or temp file (.zip, .tar, .gz, .exe, etc.) - do NOT put it in the artifacts folder. Ask the user where to save it.

Do NOT open a browser tab. Do NOT assume the artifacts folder is a general-purpose output folder.
