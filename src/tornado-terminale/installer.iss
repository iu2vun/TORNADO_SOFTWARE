; Inno Setup 6 - installer di Tornado Terminale (compilato da build.bat se Inno Setup e' installato)
#define AppName "Tornado Terminale"
#define AppExe "TornadoTerminale.exe"
#define AppVer "0.1.0"
[Setup]
AppId={{A1D84E27-5C93-4B6F-8E02-47F5C9A3D1B6}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=Tornado Laboratori
DefaultDirName={autopf}\TornadoTerminale
DefaultGroupName={#AppName}
OutputDir=dist\installer
OutputBaseFilename=TornadoTerminale-Setup-{#AppVer}
SetupIconFile=assets\TornadoTerminale.ico
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
Source: "dist\TornadoTerminale\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "tools\install_deps.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "installa_dipendenze.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "docs\Manuale_TornadoTerminale.pdf"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Manuale"; Filename: "{app}\Manuale_TornadoTerminale.pdf"; Check: FileExists(ExpandConstant('{app}\Manuale_TornadoTerminale.pdf'))
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
[Run]
Filename: "{app}\{#AppExe}"; Description: "Avvia {#AppName}"; Flags: nowait postinstall skipifsilent
