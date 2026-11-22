; Inno Setup Script for Fellow — Offline Study & Research Companion
; Compile with Inno Setup 6 (ISCC.exe installer.iss) after running build_exe.ps1

#define MyAppName "Fellow"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Ayman Boumait"
#define MyAppURL "https://github.com/AymanBoumait"
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
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer_output
OutputBaseFilename=FellowSetup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\Fellow\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\Fellow\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
