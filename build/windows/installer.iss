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
SetupIconFile=..\..\assets\icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
FinishedHeadingLabel=Claude Gallery is successfully installed.
FinishedLabel=Open Kiro or Claude Code and start generating%n— your artifacts will be shown in the gallery.

[Files]
Source: "dist\{#AppExeName}";          DestDir: "{app}";                  Flags: ignoreversion
Source: "..\..\src\gallery.html";      DestDir: "{userdocs}\ClaudeGallery"; Flags: ignoreversion
Source: "..\..\src\inject-claude.ps1"; DestDir: "{app}";                  Flags: ignoreversion
Source: "..\..\src\remove-claude.ps1"; DestDir: "{app}";                  Flags: ignoreversion
Source: "..\..\src\register-task.ps1"; DestDir: "{app}";                  Flags: ignoreversion
Source: "..\..\src\unregister-task.ps1"; DestDir: "{app}";                Flags: ignoreversion

[Dirs]
Name: "{userdocs}\ClaudeGallery\artifacts"

[Icons]
Name: "{userdesktop}\Claude Gallery"; Filename: "{app}\{#AppExeName}"; Comment: "Start Claude Gallery server and open http://localhost:7477"
Name: "{userstartmenu}\Claude Gallery"; Filename: "{app}\{#AppExeName}"; Comment: "Start Claude Gallery server"

[Tasks]
Name: "startupentry"; Description: "Start Claude Gallery automatically when Windows starts"; GroupDescription: "Startup:"; Flags: checked

[Run]
; skipifsilent: CI runs /VERYSILENT and handles these steps manually.
; Real user installs (non-silent) get all three behaviours.
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\inject-claude.ps1"""; Flags: runhidden nowait skipifsilent
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\register-task.ps1"""; Flags: runhidden nowait skipifsilent; Tasks: startupentry
Filename: "{app}\{#AppExeName}"; Flags: nowait runhidden skipifsilent

[UninstallRun]
Filename: "taskkill"; Parameters: "/F /IM {#AppExeName}"; Flags: runhidden
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\remove-claude.ps1"""; Flags: runhidden waituntilterminated
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -File ""{app}\unregister-task.ps1"""; Flags: runhidden waituntilterminated
