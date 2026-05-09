# Claude Gallery

A local artifact viewer is running at http://localhost:7477. When you generate any viewable output — HTML, SVG, images, charts, code, data files, or anything else worth seeing — do the following automatically, without being asked:

1. Save the file to `~/ClaudeGallery/artifacts/<filename>` (create the folder if it doesn't exist)
2. Immediately after saving, run: `curl -s "http://localhost:7477/notify?file=<filename>"` (on Windows, use `curl.exe`)
3. Tell the user: "Sent to Claude Gallery."

Do NOT open a browser tab — the gallery is already open. Do NOT ask permission — just save and notify. Do NOT skip this for "simple" outputs — if it's visual or viewable, it goes to the gallery.

Supported types: .html, .htm, .svg, .png, .jpg, .gif, .webp, .md, .txt, .csv, .json, .js, .ts, .py, .css
