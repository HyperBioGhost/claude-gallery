# Claude Gallery

**A gallery on your own computer for the files your AI tools make.** When an AI agent writes a web page, chart, image, document, data file or video, it appears in a browser gallery at **http://localhost:7477** as soon as it is saved.

## Works with any AI that writes files

The gallery watches one folder. Any AI agent that can save a file and run one command can send it artifacts: Claude Code, Codex, Kiro, Cursor and others.

| Your AI | How its work reaches the gallery |
|---|---|
| **Claude Code** | Set up by the installer. Claude saves each artifact to the gallery folder and tells the gallery about it, without being asked. |
| **Another agent** that can save files and run commands | Copy [`src/claude-instructions.md`](src/claude-instructions.md) into the file your agent reads its standing instructions from (for example `AGENTS.md` for Codex, a steering file for Kiro, a rules file for Cursor). From then on it sends its artifacts the same way. |
| **A chat AI** that cannot save files | Copy its output and press **Ctrl+V** anywhere in the gallery, or drag files onto it. A paste opens straight away; choose **Save to gallery** to keep it. |

## What it shows

![Supported formats](assets/features.svg)

- **Web pages, SVG and images** as themselves, with a picture of each on its card.
- **Markdown, JSON, code and CSV** with their opening lines on the card, and in full when opened. Markdown and HTML can be edited in place; every save can be undone.
- **Video and audio** with a player. A large file can stay where your project keeps it: the gallery links to it instead of copying it.
- **Collections** group related artifacts under one card, and **pins** keep the ones you use most at the top. Search and the type filters find artifacts inside collections too.

## Install

### Windows
Download `ClaudeGallery-Setup.exe` from [Releases](https://github.com/HyperBioGhost/claude-gallery/releases/latest) and run it. Next → Next → Finish.

### Mac
Download `ClaudeGallery.pkg` from [Releases](https://github.com/HyperBioGhost/claude-gallery/releases/latest).

macOS blocks the installer because it is not signed by Apple. To open it anyway:

**Option A:** right-click (or Control-click) the `.pkg` file and choose **Open**. The dialog now has an **Open** button. Click it.

**Option B:** if Option A shows no install button, go to **System Settings → Privacy & Security**, scroll down to "ClaudeGallery was blocked" and click **Open Anyway**.

**Option C** (Terminal): type `sudo xattr -r -d com.apple.quarantine `, drag the `.pkg` into Terminal, press Enter, then double-click the `.pkg`.

After installing, the gallery runs in the background and starts with your computer. Open it any time at **http://localhost:7477**.

## Update

When a newer version is out, the gallery shows **"vX.Y.Z available"** at the right of the Import / Select row, beside the version number. It checks each time the page opens; a new release can take up to six hours to show.

1. Click it. It opens the release page.
2. Download the installer for your computer and run it over the one you have. It stops the running gallery, replaces it and starts the new version. On Claude Code it also refreshes the gallery's instructions in your `CLAUDE.md`, leaving the rest of the file as it was.
3. Refresh the gallery tab. The version number now shows the new version.

Your artifacts, collections and pins stay as they are: they live in the artifacts folder, which installing never touches. That folder is `Documents\ClaudeGallery\artifacts` on Windows and `~/ClaudeGallery/artifacts` on a Mac.

## Telling your AI to file something in a collection

New artifacts land on the gallery's homepage. To have one go straight into a collection, say so when you ask for it: "make the deck and put it in the Category Playbook collection". The agent adds the collection's name when it tells the gallery about the file, and the gallery creates the collection if it does not exist yet.

## How it works

- A small local server runs on port 7477, using about 15 MB of memory and no CPU when idle. It only accepts connections from your own computer.
- Only the gallery page itself can change artifacts: the server refuses requests from other websites open in your browser.
- Files never move on disk. Collections and pins are kept in `collections.json` in the artifacts folder.
- The page is told about new artifacts the moment they are saved; it never polls.
- Editing an artifact saves the previous version first, so a save can be undone. The last 10 versions of each file are kept in `artifacts/.history/`.
- Activity and errors are logged to `ClaudeGallery/gallery.log`. Attach it to a bug report.
- Checking for a new version is the only request the app makes to the internet. Set `CLAUDE_GALLERY_NO_UPDATE_CHECK=1` to turn it off.

## Uninstall

**Windows:** Settings → Apps → Claude Gallery → Uninstall. This also removes the gallery's instructions from your `CLAUDE.md`.

**Mac:** in Terminal, run:

```bash
launchctl bootout gui/$(id -u)/com.claude-gallery.server
sudo rm /Library/LaunchAgents/com.claude-gallery.server.plist /usr/local/bin/claude-gallery-server /usr/local/bin/open-claude-gallery
sudo rm -r /Library/ClaudeGallery
sudo pkgutil --forget com.claude-gallery.server
```

Then open `~/.claude/CLAUDE.md` and delete the section between `<!-- claude-gallery-start -->` and `<!-- claude-gallery-end -->`. Your artifacts stay in `~/ClaudeGallery/artifacts` until you delete the folder.

[//]: # (そうしろと囁くのよ、私のゴーストが)
