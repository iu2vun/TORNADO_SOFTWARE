; Inno Setup 6 - installer di Tornado Visualizer (compilato da build.bat se Inno Setup e' installato)
#define AppName "Tornado Visualizer"
#define AppExe "TornadoVisualizer.exe"
#define AppVer "0.1.0"
[Setup]
AppId={{8EF6C610-57F6-43E0-8A48-0FABC01B3D05}
AppName={#AppName}
AppVersion={#AppVer}
AppPublisher=Tornado Laboratori
DefaultDirName={autopf}\TornadoVisualizer
DefaultGroupName={#AppName}
OutputDir=dist\installer
OutputBaseFilename=TornadoVisualizer-Setup-{#AppVer}
SetupIconFile=assets\TornadoVisualizer.ico
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
Source: "dist\TornadoVisualizer\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "tools\install_deps.ps1"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "installa_dipendenze.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "docs\Manuale_TornadoVisualizer.pdf"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Manuale"; Filename: "{app}\Manuale_TornadoVisualizer.pdf"; Check: FileExists(ExpandConstant('{app}\Manuale_TornadoVisualizer.pdf'))
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon
[Run]
Filename: "{app}\{#AppExe}"; Description: "Avvia {#AppName}"; Flags: nowait postinstall skipifsilent
