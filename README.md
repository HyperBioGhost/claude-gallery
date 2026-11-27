# Claude Gallery

A gallery on your own computer for the files your AI tools make: web pages, charts, images, documents, data files and video. In this README, those files are called **artifacts**.

The gallery is a web page at **http://localhost:7477**. That address only works on the computer the gallery is installed on; nobody else can open it. Each artifact appears there as soon as it is saved.

## Which AI tools can use it

The gallery shows whatever is saved in its artifacts folder. To show an artifact the moment it is saved, the AI also runs one short command that tells the gallery the file's name. AI agents that work with files on your computer (Claude Code, Codex, Kiro, Cursor and similar) can do both once they have the instructions.

| Your AI | How its artifacts reach the gallery |
|---|---|
| **Claude Code** | Set up by the installer, on Windows and Mac. After each artifact, Claude saves it to the artifacts folder and sends the gallery its name. This works even if you install Claude Code after the gallery. |
| **Another AI agent** (Codex, Kiro, Cursor and similar) | Open [the gallery instructions](https://github.com/HyperBioGhost/claude-gallery/blob/main/src/claude-instructions.md), copy all of it, and paste it into the place your agent reads standing instructions from. Common ones: for Codex, a file named `AGENTS.md` in your project folder; for Kiro, a new `.md` file in your project's `.kiro/steering` folder; for Cursor, **Settings → Rules → User Rules**. |
| **A chat AI** that cannot save files (for example a chatbot in your browser) | Copy its output, click the gallery page and press **Ctrl+V** (**Cmd+V** on a Mac), or drag files onto the page. The paste opens straight away. Choose **Save to gallery** to keep it; the gallery asks before you leave if you have not. |

To check it works, ask your AI to make a small web page. It should appear on the gallery page within a few seconds.

## What it shows

![Supported formats](assets/features.svg)

- **Web pages, SVG and images** as themselves, with a picture of each on its card.
- **Markdown, JSON, code and CSV** with their opening lines on the card, and in full when opened. Markdown and HTML can be edited in place, and the last 10 saves of each file can be undone.
- **Video and audio** with a player. To show a large video without copying it, paste the file's path (for example `D:\Renders\cut-03.mp4`) instead of the file; the gallery links to it where it is.
- **Collections** group related artifacts under one card, and **pins** keep the ones you use most at the top. Search and the type filters find artifacts inside collections too.

## Install

### Windows
1. Download `ClaudeGallery-Setup.exe` from [Releases](https://github.com/HyperBioGhost/claude-gallery/releases/latest) and open it.
2. Windows may show "Windows protected your PC", because the installer is not signed. Click **More info**, then **Run anyway**. On a laptop your company manages, check with your IT team if it is blocked.
3. Click **Next** through the installer and **Finish**.

### Mac
Download `ClaudeGallery.pkg` from [Releases](https://github.com/HyperBioGhost/claude-gallery/releases/latest). macOS blocks it at first because it is not signed by Apple. To open it anyway, use one of these:

**Option A:** right-click (or Control-click) the `.pkg` file and choose **Open**. In the dialog that appears, click **Open**.

**Option B:** if the dialog has no **Open** button, go to **System Settings → Privacy & Security**, scroll down to "ClaudeGallery was blocked" and click **Open Anyway**.

**Option C** (Terminal): type `sudo xattr -r -d com.apple.quarantine ` (with the space at the end), drag the `.pkg` into the Terminal window and press Enter. Type your Mac password when asked; it does not show as you type. Then double-click the `.pkg`.

### Check it worked
Open **http://localhost:7477**. The gallery runs in the background and starts with your computer. If the page does not load, start it by hand: double-click **Gallery Server Starter** on your Desktop (on Windows it is also in the Start menu).

## Update

When a newer version is out, a **"vX.Y.Z available"** button appears at the top of the gallery page, beside the version number. The gallery checks each time the page opens; a new release can take up to six hours to show.

1. Click it. It opens the release page.
2. Download the installer for your computer and run it, as you did to install. It stops the running gallery, replaces it and starts the new version. It also brings the gallery's instructions for Claude Code up to date, leaving the rest of your instructions as they were.
3. Refresh the gallery page. The version number now shows the new version.

Your artifacts, collections and pins stay as they are. They live in the artifacts folder, which installing never touches: `Documents\ClaudeGallery\artifacts` on Windows, and `ClaudeGallery/artifacts` in your home folder on a Mac.

If you pasted the gallery instructions into another AI agent, paste the latest version of [the instructions](https://github.com/HyperBioGhost/claude-gallery/blob/main/src/claude-instructions.md) over the old ones after updating.

## Filing an artifact into a collection as it is made

New artifacts land on the gallery's front page. To have one go straight into a collection, say so when you ask for it, for example "make the deck and put it in the Category Playbook collection". Your AI includes the collection's name when it tells the gallery about the file, and the gallery creates the collection if it does not exist yet. This works for AI agents set up with the instructions; for a pasted artifact, use **Select… → Add to collection** in the gallery.

## How it works

- The gallery is a small program that runs in the background. It only accepts connections from your own computer, and only the gallery page itself can change artifacts: other websites open in your browser cannot.
- Collections and pins never move files on disk. They are kept in `collections.json` in the artifacts folder.
- Editing an artifact saves the previous version first. The last 10 versions of each file are kept in a hidden `.history` folder inside the artifacts folder.
- Activity and errors are logged to `gallery.log`, in `Documents\ClaudeGallery` on Windows and `ClaudeGallery` in your home folder on a Mac. Attach it when you report a problem on the [Issues page](https://github.com/HyperBioGhost/claude-gallery/issues).
- Checking for a new version is the only request the gallery makes to the internet. To turn it off, set `CLAUDE_GALLERY_NO_UPDATE_CHECK` to `1` and restart the gallery. On Windows, run `setx CLAUDE_GALLERY_NO_UPDATE_CHECK 1` in PowerShell; on a Mac, run `launchctl setenv CLAUDE_GALLERY_NO_UPDATE_CHECK 1` in Terminal.

## About CLAUDE.md

Claude Code reads standing instructions from a file named `CLAUDE.md`, in a folder named `.claude` in your home folder: `C:\Users\<your name>\.claude\CLAUDE.md` on Windows, `~/.claude/CLAUDE.md` on a Mac (`~` means your home folder). The installer adds the gallery's instructions there, between the lines `<!-- claude-gallery-start -->` and `<!-- claude-gallery-end -->`, and leaves the rest of the file alone.

The `.claude` folder is hidden. To open it on a Mac, press **Cmd+Shift+G** in Finder and type `~/.claude`. On Windows, paste `%USERPROFILE%\.claude` into the File Explorer address bar.

## Uninstall

**Windows:** Settings → Apps → Claude Gallery → Uninstall. This also removes the gallery's instructions from your `CLAUDE.md`. Your artifacts stay in `Documents\ClaudeGallery\artifacts` until you delete the folder.

**Mac:** in Terminal, run these four commands. The first stops the gallery; the others remove what the installer put on your Mac and ask for your password.

```bash
launchctl bootout gui/$(id -u)/com.claude-gallery.server
sudo rm /Library/LaunchAgents/com.claude-gallery.server.plist /usr/local/bin/claude-gallery-server /usr/local/bin/open-claude-gallery
sudo rm -r /Library/ClaudeGallery
sudo pkgutil --forget com.claude-gallery.server
```

Then open your `CLAUDE.md` (see above) and delete everything from `<!-- claude-gallery-start -->` to `<!-- claude-gallery-end -->`. Your artifacts stay in `ClaudeGallery/artifacts` in your home folder until you delete the folder.

If you pasted the gallery instructions into another AI agent, delete them from there too.

[//]: # (そうしろと囁くのよ、私のゴーストが)
