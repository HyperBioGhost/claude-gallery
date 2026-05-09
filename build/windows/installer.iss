; Claude Gallery — Windows Installer
; Requires Inno Setup 6+ (https://jrsoftware.org/isinfo.php)
; Build: compile this .iss file with Inno Setup Compiler

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
; Bundled server executable (built with PyInstaller)
Source: "dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; Gallery HTML
Source: "..\..\src\gallery.html"; DestDir: "{userdocs}\ClaudeGallery"; Flags: ignoreversion

[Dirs]
Name: "{userdocs}\ClaudeGallery\artifacts"

[Tasks]
Name: "startupentry"; Description: "Start Claude Gallery automatically when Windows starts"; GroupDescription: "Startup:"

[Run]
; Start server immediately after install
Filename: "{app}\{#AppExeName}"; Flags: nowait postinstall runhidden; Description: "Start Claude Gallery now"
; Open the gallery in browser
Filename: "http://localhost:{#ServicePort}"; Flags: shellexec postinstall; Description: "Open Claude Gallery in browser"; Check: WizardIsTaskSelected('startupentry')

[UninstallRun]
; Stop server on uninstall
Filename: "taskkill"; Parameters: "/F /IM {#AppExeName}"; Flags: runhidden

[Code]
const
  ClaudeInstructions =
    '<!-- claude-gallery-start -->' + #13#10 +
    '# Claude Gallery' + #13#10 +
    '' + #13#10 +
    'A local artifact viewer is running at http://localhost:7477. When you generate any viewable output — HTML, SVG, images, charts, code, data files, or anything else worth seeing — do the following automatically, without being asked:' + #13#10 +
    '' + #13#10 +
    '1. Save the file to `~/ClaudeGallery/artifacts/<filename>` (create the folder if it doesn''t exist)' + #13#10 +
    '2. Immediately after saving, run: `curl.exe -s "http://localhost:7477/notify?file=<filename>"`' + #13#10 +
    '3. Tell the user: "Sent to Claude Gallery."' + #13#10 +
    '' + #13#10 +
    'Do NOT open a browser tab. Do NOT ask permission. Do NOT skip this for "simple" outputs.' + #13#10 +
    'Supported types: .html, .htm, .svg, .png, .jpg, .gif, .webp, .md, .txt, .csv, .json, .js, .ts, .py, .css' + #13#10 +
    '<!-- claude-gallery-end -->';

function GetClaudeMdPath: String;
begin
  Result := ExpandConstant('{%USERPROFILE}') + '\.claude\CLAUDE.md';
end;

procedure InjectClaudeInstructions;
var
  ClaudeMdPath, Existing, Updated: String;
begin
  ClaudeMdPath := GetClaudeMdPath;

  // Create .claude dir if needed
  ForceDirectories(ExtractFileDir(ClaudeMdPath));

  // Read existing content (empty string if file doesn't exist)
  Existing := '';
  if FileExists(ClaudeMdPath) then
    if not LoadStringFromFile(ClaudeMdPath, Existing) then
      Existing := '';

  // Only inject if not already present
  if Pos('claude-gallery-start', Existing) = 0 then
  begin
    if Existing <> '' then
      Updated := Existing + #13#10 + #13#10 + ClaudeInstructions
    else
      Updated := ClaudeInstructions;
    SaveStringToFile(ClaudeMdPath, Updated, False);
  end;
end;

procedure RemoveClaudeInstructions;
var
  ClaudeMdPath, Content: String;
  StartPos, EndPos: Integer;
  StartTag, EndTag: String;
begin
  ClaudeMdPath := GetClaudeMdPath;
  if not FileExists(ClaudeMdPath) then Exit;

  if not LoadStringFromFile(ClaudeMdPath, Content) then Exit;
  StartTag := '<!-- claude-gallery-start -->';
  EndTag   := '<!-- claude-gallery-end -->';
  StartPos := Pos(StartTag, Content);
  EndPos   := Pos(EndTag, Content);
  if (StartPos > 0) and (EndPos > 0) then
  begin
    // Remove the block including surrounding newlines
    Delete(Content, StartPos - 2, (EndPos + Length(EndTag)) - (StartPos - 2) + 2);
    SaveStringToFile(ClaudeMdPath, Content, False);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    InjectClaudeInstructions;
    // Register startup task if selected
    if WizardIsTaskSelected('startupentry') then
      RegWriteStringValue(
        HKCU,
        'Software\Microsoft\Windows\CurrentVersion\Run',
        'ClaudeGallery',
        ExpandConstant('"{app}\{#AppExeName}"')
      );
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RemoveClaudeInstructions;
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'ClaudeGallery');
  end;
end;
