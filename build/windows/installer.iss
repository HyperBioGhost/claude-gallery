; Claude Gallery — Windows Installer
; Requires Inno Setup 6+ (https://jrsoftware.org/isinfo.php)

#define AppName "Claude Gallery"
#define AppVersion "1.0.0"
#define AppPublisher "Claude Gallery"
#define AppURL "https://github.com/HyperBioGhost/claude-gallery"
#define AppExeName "claude-gallery-server.exe"
#define ServicePort "7477"

[Setup]
AppId={{A3F2E1D0-4B5C-6D7E-8F9A-0B1C2D3E4F5A}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
DefaultDirName={localappdata}\ClaudeGallery
DefaultGroupName={#AppName}
AllowNoIcons=yes
OutputDir=dist
OutputBaseFilename=ClaudeGallery-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\{#AppExeName}";          DestDir: "{app}";                  Flags: ignoreversion
Source: "..\..\src\gallery.html";      DestDir: "{userdocs}\ClaudeGallery"; Flags: ignoreversion
Source: "..\..\src\inject-claude.ps1"; DestDir: "{app}";                  Flags: ignoreversion
Source: "..\..\src\remove-claude.ps1"; DestDir: "{app}";                  Flags: ignoreversion

[Dirs]
Name: "{userdocs}\ClaudeGallery\artifacts"

[Tasks]
Name: "startupentry"; Description: "Start Claude Gallery automatically when Windows starts"; GroupDescription: "Startup:"

[Run]
; Inject CLAUDE.md instructions
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\inject-claude.ps1"""; Flags: runhidden waituntilterminated
; Register auto-start
Filename: "reg.exe"; Parameters: "add ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v ClaudeGallery /t REG_SZ /d """"""{app}\{#AppExeName}"""""" /f"; Flags: runhidden waituntilterminated; Tasks: startupentry
; Start server now
Filename: "{app}\{#AppExeName}"; Flags: nowait postinstall runhidden; Description: "Start Claude Gallery now"
; Open gallery in browser
Filename: "http://localhost:{#ServicePort}"; Flags: shellexec postinstall; Description: "Open Claude Gallery in browser"

[UninstallRun]
Filename: "taskkill"; Parameters: "/F /IM {#AppExeName}"; Flags: runhidden
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\remove-claude.ps1"""; Flags: runhidden waituntilterminated
Filename: "reg.exe"; Parameters: "delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v ClaudeGallery /f"; Flags: runhidden
