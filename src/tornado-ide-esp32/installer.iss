; Inno Setup 6 - installer di Tornado IDE ESP32 (compilato da build.bat se Inno Setup e' installato)
#define AppName "Tornado IDE ESP32"
#define AppExe "TornadoESPIDE.exe"
#define AppVer "0.2"
[Setup]
AppId={{7B3E5A12-9C4D-4F6A-8E21-5D0A93C7B4E8}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=Tornado Laboratori
DefaultDirName={autopf}\TornadoESPIDE
DefaultGroupName={#AppName}
OutputDir=dist\installer
OutputBaseFilename=TornadoESPIDE-Setup-{#AppVer}
SetupIconFile=assets\TornadoESPIDE.ico
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
Source: "dist\TornadoESPIDE\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "tools\install_deps.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "installa_dipendenze.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "docs\Manuale_TornadoESPIDE.pdf"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Manuale"; Filename: "{app}\Manuale_TornadoESPIDE.pdf"; Check: FileExists(ExpandConstant('{app}\Manuale_TornadoESPIDE.pdf'))
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
[Run]
Filename: "{app}\{#AppExe}"; Description: "Avvia {#AppName}"; Flags: nowait postinstall skipifsilent
