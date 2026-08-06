#ifndef MyAppVersion
#define MyAppVersion "0.1.0"
#endif

#define MyAppName "AEROCENTRAL Ground Control Station"
#define MyAppPublisher "AEROCENTRAL"
#define MyAppExeName "AEROCENTRAL.exe"

[Setup]
AppId={{7C64D0F4-5C48-4E1E-B11B-1BA895E4F2F7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\AEROCENTRAL
DefaultGroupName={#MyAppName}
OutputDir=..\..\dist\installer
OutputBaseFilename=AEROCENTRALSetup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "Create desktop shortcut"; GroupDescription: "Additional icons:"; Flags: checkedonce

[Files]
Source: "..\..\dist\AEROCENTRAL\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
