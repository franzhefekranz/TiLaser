; Inno-Setup-Skript für TiLaser
; Voraussetzung: PyInstaller-Build in dist\TiLaser (packaging\build_windows.ps1)
; Aufruf:  iscc /DMyAppVersion=0.1.0 packaging\windows\tilaser.iss

#define MyAppName "TiLaser"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
#define MyAppPublisher "franzhefekranz"
#define MyAppURL "https://github.com/franzhefekranz/TiLaser"
#define MyAppExeName "TiLaser.exe"

[Setup]
AppId={{6D9B705C-25AE-4C19-8A81-9547F2409BA4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist\release
OutputBaseFilename=TiLaser-{#MyAppVersion}-Setup
SetupIconFile=..\icon\tilaser.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Installation für alle Benutzer (Admin) oder nur für den aktuellen Benutzer wählbar
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes
CloseApplications=yes

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
german.AssocTask=TiLaser-Projektdateien (.tlp) mit TiLaser öffnen
english.AssocTask=Open TiLaser project files (.tlp) with TiLaser
german.ProjectFile=TiLaser-Projekt
english.ProjectFile=TiLaser project

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "fileassoc"; Description: "{cm:AssocTask}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\..\dist\TiLaser\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\..\beispiele\*"; DestDir: "{app}\beispiele"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.tlp\OpenWithProgids"; ValueType: string; ValueName: "TiLaser.Project"; ValueData: ""; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\TiLaser.Project"; ValueType: string; ValueName: ""; ValueData: "{cm:ProjectFile}"; Flags: uninsdeletekey; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\TiLaser.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\TiLaser.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: fileassoc

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
