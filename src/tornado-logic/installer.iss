; Inno Setup 6 - installer di Tornado Logic (compilato da build.bat se Inno Setup e' installato)
#define AppName "Tornado Logic"
#define AppExe "TornadoLogic.exe"
#define AppVer "0.1"
[Setup]
AppId={{C99F77D7-4E0C-4C0E-A370-351FB3A05201}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=Tornado Laboratori
DefaultDirName={autopf}\TornadoLogic
DefaultGroupName={#AppName}
OutputDir=dist\installer
OutputBaseFilename=TornadoLogic-Setup-{#AppVer}
SetupIconFile=assets\TornadoLogic.ico
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
Source: "dist\TornadoLogic\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "tools\install_deps.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "installa_dipendenze.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "docs\Manuale_TornadoLogic.pdf"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Manuale"; Filename: "{app}\Manuale_TornadoLogic.pdf"; Check: FileExists(ExpandConstant('{app}\Manuale_TornadoLogic.pdf'))
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\tools\install_deps.ps1"" -SkipInno"; Verb: runas; Description: "Installa sigrok-cli, Visual C++ e driver USB (collega prima l'analizzatore)"; Flags: postinstall shellexec skipifsilent
Filename: "{app}\{#AppExe}"; Description: "Avvia {#AppName}"; Flags: nowait postinstall skipifsilent
