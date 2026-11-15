; Inno Setup 6 - installer di Tornado Shell (compilato da build.bat se Inno Setup e' installato)
#define AppName "Tornado Shell"
#define AppExe "TornadoShell.exe"
#define AppVer "0.1.0"
[Setup]
AppId={{3F6A2C41-8B7D-4E19-A5C3-9D2E71B04F68}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=Tornado Laboratori
DefaultDirName={autopf}\TornadoShell
DefaultGroupName={#AppName}
OutputDir=dist\installer
OutputBaseFilename=TornadoShell-Setup-{#AppVer}
SetupIconFile=assets\TornadoShell.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
[Tasks]
Name: "desktopicon"; Description: "Icona sul desktop"; GroupDescription: "Icone:"
[Files]
Source: "dist\TornadoShell\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "tools\install_deps.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "installa_dipendenze.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "docs\Manuale_TornadoShell.pdf"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Manuale"; Filename: "{app}\Manuale_TornadoShell.pdf"; Check: FileExists(ExpandConstant('{app}\Manuale_TornadoShell.pdf'))
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
[Run]
Filename: "{app}\{#AppExe}"; Description: "Avvia {#AppName}"; Flags: nowait postinstall skipifsilent
