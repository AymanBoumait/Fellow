; Inno Setup Script for Fellow — Offline All-in-One Windows Installer
; Bundles Fellow + OllamaSetup.exe into installer_output\FellowSetup.exe
; Build with: .\build_installer.ps1 (or ISCC.exe installer.iss after preparing dist\Fellow and installer_assets\OllamaSetup.exe)

#define MyAppName "Fellow"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Ayman Boumait"
#define MyAppURL "https://github.com/AymanBoumait/Fellow"
#define MyAppExeName "Fellow.exe"

[Setup]
AppId={{8D4F69C2-4B7A-4719-9A63-2F6F5EFE1100}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer_output
OutputBaseFilename=FellowSetup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=no
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\Fellow\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\Fellow\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "THIRD_PARTY_LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "installer_assets\OllamaSetup.exe"; DestDir: "{tmp}"; Flags: ignoreversion nocompression deleteafterinstall; Check: IsOllamaMissing

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\OllamaSetup.exe"; Parameters: "/VERYSILENT /NORESTART /SUPPRESSMSGBOXES"; StatusMsg: "Installing Ollama local AI runtime (this may take a minute)..."; Flags: runhidden waituntilterminated; Check: IsOllamaMissing
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
function IsOllamaMissing: Boolean;
begin
  Result := True;

  if FileExists(ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe')) then
  begin
    Result := False;
    Exit;
  end;

  if FileExists(ExpandConstant('{autopf}\Ollama\ollama.exe')) then
  begin
    Result := False;
    Exit;
  end;

  if IsWin64 and FileExists(ExpandConstant('{commonpf64}\Ollama\ollama.exe')) then
  begin
    Result := False;
    Exit;
  end;

  if FileExists(ExpandConstant('{commonpf32}\Ollama\ollama.exe')) then
  begin
    Result := False;
    Exit;
  end;

  if FileSearch('ollama.exe', GetEnv('PATH')) <> '' then
  begin
    Result := False;
    Exit;
  end;
end;
